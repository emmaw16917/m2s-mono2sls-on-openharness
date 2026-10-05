"""Acme Air: the official JMeter workload (acmeair-driver) against the
monolith (local, MongoDB in Docker) and the migrated v0 / optimized
serverless versions (deployed to AWS, torn down afterwards).

    python e2e_jmeter.py [--only monolith|v0|opt] [--threads 10] [--loops 20]
                         [--customers 10000] [--workspace acmeair-a1] [--keep]

Outputs under Mono2Sls-artifacts/tests/results-e2e/acmeair/:
  report.md, report.json                       comparison
  <variant>.jtl, <variant>.jmeter.log          JMeter raw results
  <variant>.log, <variant>-aws/                deploy log, stack + raw CloudWatch data

The workload is the driver's AcmeAir.jmx as shipped with the monolith
(document/workload/jmeter), unchanged except that the request protocol comes
from a property (the AWS variants are https on 443). JMeter runs in the
justb4/jmeter:5.5 image with the driver jar and json-simple (which JMeter 5
no longer bundles) in lib/ext. The serverless stage name goes into
CONTEXT_ROOT, since the script builds paths as ${CONTEXT_ROOT}/rest/api/...
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from urllib.parse import urlparse

import e2e_aws
from e2e_aws import MONO, RUNS, TESTS, sh

APP = "acmeair"
MONO_SRC = MONO / "acmeair"
JM_SRC = MONO_SRC / "document" / "workload" / "jmeter"
WORK = Path(os.environ.get("M2S_E2E_WORK", r"D:\tmp\m2s-e2e"))
DOCKER = shutil.which("docker") or r"C:\Program Files\Docker\Docker\resources\bin\docker.exe"
JMETER_IMAGE = "justb4/jmeter:5.5"
MONGO_IMAGE = "mongo:4.4"  # the monolith's mongodb 2.0 driver needs the legacy wire protocol (removed in 5.1)
# vendored (Apache-2.0, 24 KB): JMeter 5 no longer ships it and the driver needs it
JSON_SIMPLE = Path(__file__).resolve().parent / "e2e-tools" / "json-simple-1.1.1.jar"
MONO_PORT = 9080
NO_PROXY = {"NO_PROXY": "localhost,127.0.0.1,.amazonaws.com", "no_proxy": "localhost,127.0.0.1,.amazonaws.com"}


def _docker(args: list[str], log: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    env = {**os.environ, "MSYS_NO_PATHCONV": "1"}
    return sh([DOCKER, *args], env=env, check=check, log=log)


def _http(url: str, timeout: float = 600) -> str:
    import requests
    r = requests.get(url, timeout=timeout, proxies={"http": None, "https": None})
    r.raise_for_status()
    return r.text


def jmeter_dir(variant: str) -> Path:
    """A run directory with the workload files; protocol from -JPROTOCOL."""
    d = WORK / f"jm-{variant}"
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True)
    jmx = (JM_SRC / "AcmeAir.jmx").read_text(encoding="utf-8")
    jmx = jmx.replace('<stringProp name="HTTPSampler.protocol"></stringProp>',
                      '<stringProp name="HTTPSampler.protocol">${__P(PROTOCOL,http)}</stringProp>')
    (d / "AcmeAir.jmx").write_text(jmx, encoding="utf-8")
    for f in ("Airports.csv", "Airports2.csv"):
        shutil.copy(JM_SRC / f, d / f)
    shutil.copy(JM_SRC / "lib" / "acmeair-driver-1.0-SNAPSHOT.jar", d)
    shutil.copy(JSON_SIMPLE, d)
    return d


def run_jmeter(variant: str, host: str, port: int, context_root: str, protocol: str, threads: int, loops: int,
               out_dir: Path) -> dict:
    d = jmeter_dir(variant)
    (d / "hosts.csv").write_text(f"{host},{port},{context_root}\n", encoding="utf-8")
    started = time.time()
    # TZ=UTC: the driver formats dates as "EEE MMM dd 00:00:00 z yyyy" in the
    # JVM's zone; the image defaults to CEST, which JavaScript's Date cannot
    # parse (every flight query then finds nothing and nothing is booked)
    _docker(["run", "--rm", "-e", "TZ=UTC", "-v", f"{d}:/test", "-w", "/test", "--entrypoint", "sh", JMETER_IMAGE, "-c",
             "cp /test/*.jar $JMETER_HOME/lib/ext/ && jmeter -n -t AcmeAir.jmx "
             f"-JNUM_THREAD={threads} -JLOOP_COUNT={loops} -JPROTOCOL={protocol} -DusePureIDs=true "
             "-l /test/result.jtl -j /test/jmeter.log"], log=out_dir / f"{variant}.log")
    ended = time.time()
    shutil.copy(d / "result.jtl", out_dir / f"{variant}.jtl")
    shutil.copy(d / "jmeter.log", out_dir / f"{variant}.jmeter.log")
    return {"started": started, "ended": ended, **summarize_jtl(out_dir / f"{variant}.jtl")}


def _pct(values: list[int], p: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    return float(s[min(len(s) - 1, int(round(p / 100 * (len(s) - 1))))])


def summarize_jtl(path: Path) -> dict:
    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    labels: dict[str, dict] = {}
    for r in rows:
        x = labels.setdefault(r["label"], {"n": 0, "errors": 0, "elapsed": [], "codes": {}})
        x["n"] += 1
        x["errors"] += r["success"] != "true"
        x["elapsed"].append(int(r["elapsed"]))
        x["codes"][r["responseCode"]] = x["codes"].get(r["responseCode"], 0) + 1
    t0 = min(int(r["timeStamp"]) for r in rows) if rows else 0
    t1 = max(int(r["timeStamp"]) + int(r["elapsed"]) for r in rows) if rows else 0
    out = {"samples": len(rows), "errors": sum(x["errors"] for x in labels.values()),
           "throughput_per_s": round(len(rows) / ((t1 - t0) / 1000), 2) if t1 > t0 else None,
           "all": {"p50": _pct([int(r["elapsed"]) for r in rows], 50), "p90": _pct([int(r["elapsed"]) for r in rows], 90),
                   "p99": _pct([int(r["elapsed"]) for r in rows], 99)},
           "labels": {}}
    for k, x in labels.items():
        out["labels"][k] = {"n": x["n"], "errors": x["errors"], "codes": x["codes"],
                            "p50": _pct(x["elapsed"], 50), "p90": _pct(x["elapsed"], 90), "p99": _pct(x["elapsed"], 99)}
    return out


def run_monolith(out_dir: Path, threads: int, loops: int, customers: int) -> dict:
    log = out_dir / "monolith.log"
    log.write_text("", encoding="utf-8")
    src = WORK / "acmeair-mono"
    if not (src / "node_modules").exists():
        if src.exists():
            shutil.rmtree(src)
        shutil.copytree(MONO_SRC, src)
        sh([shutil.which("npm") or "npm", "install", "--no-audit", "--no-fund", "--omit=optional"], cwd=src, log=log)
    _docker(["rm", "-f", "acmeair-mongo"], check=False)
    _docker(["run", "-d", "--name", "acmeair-mongo", "-p", "27017:27017", MONGO_IMAGE], log=log)
    time.sleep(5)
    proc = subprocess.Popen([shutil.which("node") or "node", "app.js"], cwd=src,
                            env={**os.environ, **NO_PROXY, "VCAP_APP_PORT": str(MONO_PORT)},
                            stdout=(out_dir / "monolith.app.log").open("w"), stderr=subprocess.STDOUT)
    try:
        base = f"http://127.0.0.1:{MONO_PORT}/rest/api"
        for _ in range(30):
            try:
                _http(f"{base}/checkstatus", 5)
                break
            except Exception:  # noqa: BLE001
                time.sleep(2)
        loaded = _http(f"{base}/loader/load?numCustomers={customers}")
        counts = {k: _http(f"{base}/config/count{k}") for k in ("Customers", "Flights", "FlightSegments", "Airports")}
        res = run_jmeter("monolith", "host.docker.internal", MONO_PORT, "", "http", threads, loops, out_dir)
        res["counts_after"] = {k: _http(f"{base}/config/count{k}") for k in ("Bookings", "Sessions")}
        return {"variant": "monolith", "loader": loaded.strip(), "counts": counts, **res}
    finally:
        proc.terminate()
        _docker(["rm", "-f", "acmeair-mongo"], check=False)


def run_serverless(variant: str, ref: str, workspace: str, out_dir: Path, threads: int, loops: int, customers: int,
                   keep: bool) -> dict:
    log = out_dir / f"{variant}.log"
    log.write_text("", encoding="utf-8")
    src = e2e_aws.export_variant(RUNS / workspace, ref, e2e_aws.WORK / f"{APP}-{variant}")
    stack = f"m2s-e2e-{APP}-{variant}"
    try:
        stack, outputs, resources = e2e_aws.deploy(APP, variant, src, log)
        api = next((v for k, v in outputs.items() if re.search(r"api.*(url|endpoint)", k, re.I)), None)
        if not api:
            raise RuntimeError(f"no API URL in stack outputs {sorted(outputs)}")
        u = urlparse(api.rstrip("/"))
        stage = u.path.rstrip("/")
        # the stage URL may already include /rest/api if the template made it the base path
        if stage.endswith("/rest/api"):
            stage = stage[: -len("/rest/api")]
        base = f"{u.scheme}://{u.netloc}{stage}/rest/api"
        loaded = _http(f"{base}/loader/load?numCustomers={customers}", 900)
        counts = {k: _http(f"{base}/config/count{k}") for k in ("Customers", "Flights", "FlightSegments", "Airports")}
        res = run_jmeter(variant, u.netloc, 443, stage, "https", threads, loops, out_dir)
        res["counts_after"] = {k: _http(f"{base}/config/count{k}") for k in ("Bookings", "Sessions")}
        raw_dir = out_dir / f"{variant}-aws"
        raw_dir.mkdir(exist_ok=True)
        (raw_dir / "stack.json").write_text(json.dumps({"stack": stack, "outputs": outputs, "resources": resources}, indent=2),
                                            encoding="utf-8")
        metrics = e2e_aws.collect_metrics(resources, res["started"], res["ended"], raw_dir)
        (out_dir / f"{variant}.metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
        summary = e2e_aws.summarize_metrics(metrics)
        arch = "arm64" if re.search(r"Architectures:\s*\n\s*-\s*arm64", (src / "template.yaml").read_text(encoding="utf-8")) else "x86_64"
        return {"variant": variant, "ref": ref, "commit": sh(["git", "-C", str(RUNS / workspace), "rev-parse", "--short", ref]).stdout.strip(),
                "architecture": arch, "loader": loaded.strip()[:200], "counts": counts, **res, "metrics": summary,
                "cost_usd": e2e_aws.cost(summary, arch)}
    finally:
        if not keep and e2e_aws.stack_exists(stack):
            e2e_aws.delete(stack, log)


def report(results: dict[str, dict], out_dir: Path, threads: int, loops: int) -> None:
    (out_dir / "report.json").write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    vs = [v for v in ("monolith", "v0", "opt") if v in results]
    md = [f"# acmeair: official JMeter workload, monolith vs v0 vs optimized", "",
          f"acmeair-driver AcmeAir.jmx, {threads} threads x {loops} loops. Monolith local (Node + MongoDB 4.4 in Docker, "
          "no network hop); serverless variants on AWS us-east-1 (includes the client-to-us-east-1 round trip).", "",
          "| variant | commit | arch | samples | errors | req/s | p50 ms | p90 ms | p99 ms | invocations | Lambda GB-s | RRU | WRU | est. cost USD |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for v in vs:
        r = results[v]
        if r.get("error"):
            md.append(f"| {v} | - | - | error: {r['error'][:120]} |" + " |" * 10)
            continue
        m = r.get("metrics") or {}
        md.append(f"| {v} | {r.get('commit', '-')} | {r.get('architecture', '-')} | {r['samples']} | {r['errors']} | "
                  f"{r['throughput_per_s']} | {r['all']['p50']} | {r['all']['p90']} | {r['all']['p99']} | "
                  f"{m.get('invocations', '-')} | {m.get('lambda_gb_seconds', '-')} | {m.get('rru', '-')} | {m.get('wru', '-')} | "
                  f"{(r.get('cost_usd') or {}).get('total', '-')} |")
    labels = sorted({k for v in vs for k in (results[v].get("labels") or {})})
    md += ["", "## Per request (p50 / p90 ms, errors/samples, status codes)", "",
           "| request | " + " | ".join(vs) + " |", "|---|" + "---|" * len(vs)]
    for k in labels:
        cells = []
        for v in vs:
            x = (results[v].get("labels") or {}).get(k)
            cells.append(f"{x['p50']} / {x['p90']} ({x['errors']}/{x['n']}) {x['codes']}" if x else "-")
        md.append(f"| {k} | " + " | ".join(cells) + " |")
    md += ["", "## Data", ""]
    for v in vs:
        r = results[v]
        md.append(f"- {v}: loader `{r.get('loader')}`; counts {r.get('counts')}; after the run {r.get('counts_after')}")
    (out_dir / "report.md").write_text("\n".join(md) + "\n", encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--only", choices=["monolith", "v0", "opt"], action="append")
    ap.add_argument("--workspace", default="acmeair-a1")
    ap.add_argument("--v0-ref", default="v0")
    ap.add_argument("--threads", type=int, default=10)
    ap.add_argument("--loops", type=int, default=20)
    ap.add_argument("--customers", type=int, default=10000)
    ap.add_argument("--out", default=APP)
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args()
    out_dir = TESTS / "results-e2e" / args.out
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "results.json"
    results = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    for v in args.only or ["monolith", "v0", "opt"]:
        print(f"== {APP} {v}", flush=True)
        try:
            results[v] = run_monolith(out_dir, args.threads, args.loops, args.customers) if v == "monolith" else \
                run_serverless(v, args.v0_ref if v == "v0" else "HEAD", args.workspace, out_dir, args.threads, args.loops,
                               args.customers, args.keep)
        except Exception as exc:  # noqa: BLE001 -- keep going; the report shows it
            results[v] = {"variant": v, "error": str(exc)[:2000]}
        path.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
        print(json.dumps({k: results[v].get(k) for k in ("samples", "errors", "all", "error", "cost_usd")}), flush=True)
    report({v: results[v] for v in ("monolith", "v0", "opt") if v in results}, out_dir, args.threads, args.loops)


if __name__ == "__main__":
    main()
