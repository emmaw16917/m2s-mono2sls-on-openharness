"""One-off real-AWS calibration of perf-rules/latency-profile.json.

    python e2e_calibrate.py [--sweeps 5] [--n 100] [--write] [--keep]

Deploys e2e-tools/calibrate (a DynamoDB table with a GSI, an SQS queue and an
arm64 / 512 MB / nodejs22.x Lambda: the pipeline's defaults), invokes the
Lambda --sweeps times (each sweep times every operation --n times after a
warm-up, with the SDK the runtime bundles), deletes the stack, and writes the
medians to Mono2Sls-artifacts/tests/results-e2e/calibration/. --write also
replaces ops_ms in perf-rules/latency-profile.json (evidence grade M: measured
directly, not derived).

Why this touches AWS although the pipeline is local-first: the pipeline never
calls AWS. Its verdicts come from the local RIE runs at fixed latencies; the
latency profile is only a static input that sizes the expected real-AWS effect
of a change (perf.py _calibrated_estimate), like perf-rules/prices.json. This
script refreshes that input by hand, once per region / runtime / SDK change;
without it the profile keeps its last values (or the derived E-grade ones).
"""
from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import time
from pathlib import Path

import boto3

from e2e_aws import REGION, TESTS

HERE = Path(__file__).resolve().parent
SRC = HERE / "e2e-tools" / "calibrate"
PROFILE = HERE / "perf-rules" / "latency-profile.json"
STACK = "m2s-latency-calibration"


def sam(*args: str) -> None:
    subprocess.run(["sam", *args], cwd=SRC, check=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--sweeps", type=int, default=5)
    ap.add_argument("--n", type=int, default=100)
    ap.add_argument("--write", action="store_true", help="update perf-rules/latency-profile.json")
    ap.add_argument("--keep", action="store_true", help="do not delete the stack")
    a = ap.parse_args()
    out_dir = TESTS / "results-e2e" / "calibration"
    out_dir.mkdir(parents=True, exist_ok=True)
    sam("deploy", "--stack-name", STACK, "--region", REGION, "--resolve-s3", "--no-confirm-changeset", "--capabilities", "CAPABILITY_IAM")
    try:
        cf = boto3.client("cloudformation", region_name=REGION)
        fn = {o["OutputKey"]: o["OutputValue"] for o in cf.describe_stacks(StackName=STACK)["Stacks"][0]["Outputs"]}["FunctionName"]
        lam = boto3.client("lambda", region_name=REGION, config=boto3.session.Config(read_timeout=310))
        sweeps = []
        for i in range(a.sweeps):
            r = lam.invoke(FunctionName=fn, Payload=json.dumps({"n": a.n, "warm": 10, "seed": i == 0}).encode())
            body = json.loads(r["Payload"].read())
            if "ops" not in body:
                raise RuntimeError(f"sweep {i}: {body}")
            sweeps.append(body)
            print(f"sweep {i + 1}/{a.sweeps}:", {k: v["p50"] for k, v in body["ops"].items()})
    finally:
        if not a.keep:
            sam("delete", "--stack-name", STACK, "--region", REGION, "--no-prompts")
    ops = sorted(sweeps[0]["ops"])
    med = {op: {k: round(statistics.median(s["ops"][op][k] for s in sweeps), 2) for k in ("p10", "p50", "p90", "mean")} for op in ops}
    stamp = time.strftime("%Y-%m-%d")
    res = {"measured": stamp, "region": REGION, "runtime": sweeps[0]["node"], "arch": sweeps[0]["arch"], "memory_mb": sweeps[0]["memory"],
           "sweeps": a.sweeps, "n_per_op_per_sweep": a.n, "medians_over_sweeps": med, "raw": sweeps}
    path = out_dir / f"calibration-{stamp}.json"
    path.write_text(json.dumps(res, indent=2), encoding="utf-8")
    print(f"wrote {path}")
    if a.write:
        old = json.loads(PROFILE.read_text(encoding="utf-8"))
        prof = {
            "_note": ("Per-operation round trip (ms, p50) for perf.py's calibrated critical-path estimate (network_ms_cal). Sizing only, "
                      "never a verdict: verdicts stay on the local fixed-latency RIE runs. Evidence grade M: measured by e2e_calibrate.py "
                      "inside an arm64 512 MB nodejs22.x Lambda (runtime-bundled SDK v3, keep-alive) against a table and queue in the same "
                      "region, ~1 KB items, Query/Scan/BatchGet return 10-20 items. The pipeline itself never calls AWS; refresh this file by "
                      "hand when the region, runtime or SDK changes."),
            "source": str(path.relative_to(HERE.parent)).replace("\\", "/"),
            "grade": "M",
            "region_scope": "client Lambda and table in the same region",
            "ops_ms": {op.replace("QueryGSI", "Query@GSI"): med[op]["p50"] for op in ops},
            "ops_p90_ms": {op.replace("QueryGSI", "Query@GSI"): med[op]["p90"] for op in ops},
            "default_ms": 10,
            "uncalibrated": [],
            "previous": {"grade": old.get("grade", "E"), "source": old.get("source"), "ops_ms": old.get("ops_ms")},
        }
        PROFILE.write_text(json.dumps(prof, indent=2) + "\n", encoding="utf-8")
        print(f"updated {PROFILE}")


if __name__ == "__main__":
    main()
