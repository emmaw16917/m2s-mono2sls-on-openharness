"""Recompute the serverless cost/data figures of an e2e_ec2.py run that was
collected before the window fix (2026-10-09), from the saved raw Lambda logs
plus a fresh CloudWatch query (DynamoDB metrics outlive the tables for 15 days).

    python e2e_ec2_recompute.py [--out-dir .../results-e2e/acmeair-ec2]

What the old collection got wrong:
  1. Lambda REPORT window [started-5 s, ended+120 s]: the passes are ~4 s
     apart, so pass 1 also counted every pass-2 invocation.
  2. DynamoDB window [started-60 s, ended+300 s] per pass: both passes summed
     overlapping minutes, and the minute holding the loader's 10k-customer
     write (and the harness's count Scans) fell inside.
  3. Only the table dimension was read; GSI capacity (the flight-query index
     reads, every write's index copy) is published separately and was missing.

Here: REPORT lines are assigned to the pass whose [start, next start) holds
them, deduplicated by RequestId, and the harness's own functions (count
checks, loader) are left out. DynamoDB is summed over the whole minutes of
both passes, table + GSI, and the parts that are not workload are taken out
by what they are: the loader's writes (the four loaded tables, their GSIs and
numCustomers customer writes) and the count Scans (reads on base tables the
workload only touches by key). The workload's customer GetItems are added
back as 0.5 RRU each (eventually consistent, item < 4 KB), which the opt run's
clean pass-2 minute confirms (125 RRU for 200 Login + 50 View Profile).
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
from pathlib import Path

import boto3

from e2e_aws import API_GW_REST_PER_REQUEST, PRICES, REGION, TESTS

RX = re.compile(r"REPORT RequestId: (\S+)\s+Duration: ([\d.]+) ms\s+Billed Duration: (\d+) ms\s+Memory Size: (\d+) MB"
                r"\s+Max Memory Used: (\d+) MB(?:\s+Init Duration: ([\d.]+) ms)?")
HARNESS_FN = re.compile(r"^(ConfigCount|Loader)")
LOADED = ("AirportCodeMappingTable", "FlightSegmentTable", "FlightTable")


def lambda_per_pass(raw: Path, passes: list[dict]) -> list[dict]:
    bounds = [(p["started"], passes[i + 1]["started"] if i + 1 < len(passes) else p["ended"] + 60) for i, p in enumerate(passes)]
    out = [{"functions": {}, "harness_invocations": 0} for _ in passes]
    seen = set()
    for f in sorted(raw.glob("pass*/logs/*Function.log")):
        fn = f.stem
        for line in f.read_text(encoding="utf-8").splitlines():
            parts = line.split("\t", 2)
            if len(parts) < 3 or not parts[2].startswith("REPORT"):
                continue
            m = RX.search(parts[2])
            if not m or m[1] in seen:
                continue
            seen.add(m[1])
            ts = int(parts[0]) / 1000
            for i, (a, b) in enumerate(bounds):
                if a <= ts < b:
                    if HARNESS_FN.match(fn):
                        out[i]["harness_invocations"] += 1
                    else:
                        out[i]["functions"].setdefault(fn, []).append(
                            {"duration": float(m[2]), "billed": int(m[3]), "memory": int(m[4]), "init": float(m[6]) if m[6] else None})
    res = []
    for o in out:
        fns, gb_s, n = {}, 0.0, 0
        for fn, reps in sorted(o["functions"].items()):
            warm = [r["duration"] for r in reps if r["init"] is None]
            cold = [r["init"] for r in reps if r["init"] is not None]
            fns[fn] = {"invocations": len(reps), "cold": len(cold),
                       "warm_p50_ms": round(statistics.median(warm), 1) if warm else None,
                       "warm_mean_ms": round(statistics.mean(warm), 2) if warm else None,
                       "init_p50_ms": round(statistics.median(cold), 1) if cold else None}
            gb_s += sum(r["billed"] / 1000 * r["memory"] / 1024 for r in reps)
            n += len(reps)
        res.append({"functions": fns, "invocations": n, "lambda_gb_seconds": round(gb_s, 3),
                    "harness_invocations": o["harness_invocations"]})
    return res


def dynamodb(resources: dict, passes: list[dict], customers: int, lam: list[dict]) -> dict:
    cw = boto3.client("cloudwatch", region_name=REGION)
    m0, m1 = int(passes[0]["started"] // 60 * 60), int(passes[-1]["ended"] // 60 * 60) + 60
    raw = {}
    for logical, r in resources.items():
        if r["type"] != "AWS::DynamoDB::Table":
            continue
        base = [{"Name": "TableName", "Value": r["physical"]}]
        gsis = sorted({d["Value"] for mt in cw.list_metrics(Namespace="AWS/DynamoDB", Dimensions=base)["Metrics"]
                       for d in mt["Dimensions"] if d["Name"] == "GlobalSecondaryIndexName"})
        for target, dims in [(logical, base)] + [(f"{logical}/{g}", base + [{"Name": "GlobalSecondaryIndexName", "Value": g}]) for g in gsis]:
            raw[target] = {k: sum(p["Sum"] for p in cw.get_metric_statistics(
                Namespace="AWS/DynamoDB", MetricName=f"Consumed{k}CapacityUnits", Dimensions=dims,
                StartTime=m0, EndTime=m1, Period=60, Statistics=["Sum"])["Datapoints"]) for k in ("Write", "Read")}
    # loader minute inside the window? its signature is the loaded tables' writes
    loader_in = any(raw.get(t, {}).get("Write", 0) > 0 for t in LOADED)
    wru = rru = 0.0
    parts = {}
    for target, v in raw.items():
        table = target.split("/")[0]
        w, rd = v["Write"], v["Read"]
        if table in LOADED:
            w = 0.0                                   # loader only; the workload never writes here
            rd = rd if "/" in target else 0.0         # base-table reads are count Scans; GSI reads are flight queries
        elif table == "CustomerTable":
            if loader_in:
                w -= customers                        # loader: one ~1 KB customer = 1 WRU
            gets = sum(p["functions"].get(fn, {}).get("invocations", 0) for p in lam
                       for fn in ("LoginCreateSessionFunction", "CustomerGetFunction"))
            rd = 0.5 * gets                           # count Scans share this table's minutes
        elif table == "BookingTable" and "/" not in target:
            rd = 0.0                                  # countBookings Scan; the workload reads bookings via the GSI
        parts[target] = {"wru": w, "rru": rd, "raw_wru": v["Write"], "raw_rru": v["Read"]}
        wru += w
        rru += rd
    return {"minutes": [m0, m1], "loader_in_window": loader_in, "wru": wru, "rru": rru, "per_target": parts}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out-dir", type=Path, default=TESTS / "results-e2e" / "acmeair-ec2")
    ap.add_argument("--customers", type=int, default=10000)
    a = ap.parse_args()
    results = json.loads((a.out_dir / "results.json").read_text(encoding="utf-8"))
    out, md = {}, ["# acmeair same-region run: corrected serverless cost and data", "",
                   "Recomputed by `m2s-mcp-server/e2e_ec2_recompute.py` (see its docstring for the three collection bugs). "
                   "End-to-end latency and the warm per-function Duration table in report.md were not affected.", ""]
    rows = []
    for v in ("v0", "opt"):
        if v not in results or results[v].get("error"):
            continue
        r = results[v]
        stack = json.loads((a.out_dir / f"{v}-aws" / "stack.json").read_text(encoding="utf-8"))
        lam = lambda_per_pass(a.out_dir / f"{v}-aws", r["passes"])
        ddb = dynamodb(stack["resources"], r["passes"], a.customers, lam)
        samples = sum(p["samples"] for p in r["passes"])
        inv = sum(p["invocations"] for p in lam)
        gb_s = sum(p["lambda_gb_seconds"] for p in lam)
        gb = PRICES["lambda_gb_second_arm64"] if r.get("architecture") == "arm64" else PRICES["lambda_gb_second"]
        cost = {"lambda_duration": gb_s * gb, "lambda_requests": inv * PRICES["lambda_request"],
                "dynamodb": ddb["rru"] * PRICES["dynamodb_read_request_unit"] + ddb["wru"] * PRICES["dynamodb_write_request_unit"],
                "api_gateway": samples * API_GW_REST_PER_REQUEST}
        cost["total"] = sum(cost.values())
        out[v] = {"lambda_per_pass": lam, "dynamodb": ddb, "samples": samples, "invocations": inv,
                  "lambda_gb_seconds": round(gb_s, 3), "cost_usd": {k: round(x, 8) for k, x in cost.items()},
                  "old": {"invocations": r["metrics"]["invocations"], "lambda_gb_seconds": r["metrics"]["lambda_gb_seconds"],
                          "rru": r["metrics"]["rru"], "wru": r["metrics"]["wru"], "cost_usd": r["cost_usd"]["total"]}}
        rows.append((v, out[v]))
    md += ["## Cost, both passes (workload only)", "",
           "| variant | JMeter requests | Lambda invocations | Lambda GB-s | RRU (incl. GSI) | WRU (incl. GSI) | Lambda $ | DynamoDB $ | API GW $ | total $ | per 1k requests |",
           "|---|---|---|---|---|---|---|---|---|---|---|"]
    for v, x in rows:
        c = x["cost_usd"]
        md.append(f"| {v} | {x['samples']} | {x['invocations']} | {x['lambda_gb_seconds']} | {x['dynamodb']['rru']:.1f} | {x['dynamodb']['wru']:.0f} | "
                  f"{c['lambda_duration'] + c['lambda_requests']:.6f} | {c['dynamodb']:.6f} | {c['api_gateway']:.6f} | {c['total']:.6f} | "
                  f"{c['total'] / x['samples'] * 1000:.6f} |")
    md += ["", "Old (buggy) figures for comparison:", "", "| variant | invocations | GB-s | RRU | WRU | total $ |", "|---|---|---|---|---|---|"]
    md += [f"| {v} | {x['old']['invocations']} | {x['old']['lambda_gb_seconds']} | {x['old']['rru']} | {x['old']['wru']} | {x['old']['cost_usd']:.6f} |" for v, x in rows]
    md += ["", "## Lambda per pass (workload functions only)", "",
           "| variant | pass | invocations | cold | GB-s | harness invocations left out |", "|---|---|---|---|---|---|"]
    for v, x in rows:
        for i, p in enumerate(x["lambda_per_pass"], 1):
            md.append(f"| {v} | {i} | {p['invocations']} | {sum(f['cold'] for f in p['functions'].values())} | {p['lambda_gb_seconds']} | {p['harness_invocations']} |")
    md += ["", "## DynamoDB by table / GSI (both passes)", "", "| variant | target | WRU kept | RRU kept | raw WRU | raw RRU |", "|---|---|---|---|---|---|"]
    for v, x in rows:
        for t, p in sorted(x["dynamodb"]["per_target"].items()):
            if p["raw_wru"] or p["raw_rru"]:
                md.append(f"| {v} | {t} | {p['wru']:.0f} | {p['rru']:.1f} | {p['raw_wru']:.0f} | {p['raw_rru']:.1f} |")
    (a.out_dir / "corrected.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    (a.out_dir / "corrected.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n".join(md))


if __name__ == "__main__":
    main()
