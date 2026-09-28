"""Proxy metrics for one Python handler + one benchmark event, in a local
Python 3.12 with Lambda's boto3 version -- the twin of perf-harness.js.

    python m2s_perf_harness.py <job.json>

job: {fn_dir, handler, env, event, fixtures_path, layer_dirs, depth_latency_ms,
cpu_warmup, cpu_reps}. Prints one line "@@M2S@@<json>" with the same fields
as perf-harness.js:
  valid run (latency = depth_latency_ms, the second invocation -- warm):
  status/error, calls, sequential
  depth, conflicts, capacity units; warm run (latency 0): median/p90 wall
  time per invocation over cpu_reps runs after cpu_warmup runs.
"""

import copy
import importlib
import json
import os
import sys
import time


def out(obj):
    sys.stdout.write("@@M2S@@" + json.dumps(obj, default=str) + "\n")
    sys.stdout.flush()


def depth_of(calls):
    """Longest chain of calls where each starts after the previous one ended."""
    cs = sorted(calls, key=lambda c: c["start"])
    dp = [1] * len(cs)
    for i in range(len(cs)):
        for j in range(i):
            if cs[j]["end"] <= cs[i]["start"] + 0.5:
                dp[i] = max(dp[i], dp[j] + 1)
    return max(dp) if dp else 0


class Context:
    aws_request_id = "perf"
    function_name = "perf"
    memory_limit_in_mb = "512"
    invoked_function_arn = "arn:aws:lambda:us-east-1:000000000000:function:perf"

    def get_remaining_time_in_millis(self):
        return 30000


def main():
    with open(sys.argv[1], encoding="utf-8") as fh:
        job = json.load(fh)
    os.environ.update({k: str(v) for k, v in (job.get("env") or {}).items()})
    os.environ.update({"SHIM_FIXTURES": job["fixtures_path"], "SHIM_LATENCY_MS": "0",
                       "AWS_ACCESS_KEY_ID": "AKIDPERF", "AWS_SECRET_ACCESS_KEY": "perf",
                       "AWS_REGION": "us-east-1", "AWS_DEFAULT_REGION": "us-east-1"})
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import m2s_perf_shim as shim
    if not shim.patched:
        return out({"error": {"type": "M2SPerf", "message": "botocore not importable; the perf shim patched nothing"}})
    sys.path[:0] = [job["fn_dir"], *job.get("layer_dirs", [])]
    mod, _, fn = job["handler"].rpartition(".")
    try:
        handler = getattr(importlib.import_module(mod.replace("/", ".")), fn)
    except Exception as e:  # noqa: BLE001
        return out({"error": {"type": "Runtime.ImportModuleError", "message": f"{type(e).__name__}: {e}"}})

    def invoke():
        try:
            return {"result": handler(copy.deepcopy(job["event"]), Context())}
        except Exception as e:  # noqa: BLE001
            return {"error": {"type": type(e).__name__, "message": str(e)}}

    shim.state["latency"] = job.get("depth_latency_ms") or 10
    # The first invocation pays one-time work (clients, thread pool, per-thread
    # resources) that staggers its calls; calls and depth describe a warm
    # invocation, the second one. A first invocation that fails is reported.
    first = invoke()
    if not first.get("error"):
        shim.reset()
        first = invoke()
    calls = shim.calls()
    result = first.get("result")
    res = {
        "status": result.get("statusCode") if isinstance(result, dict) else None,
        "batch_item_failures": len(result["batchItemFailures"]) if isinstance(result, dict) and isinstance(result.get("batchItemFailures"), list) else None,
        "error": first.get("error"),
        "calls": len(calls),
        "call_ops": [f"{c['op']}:{c.get('table') or ''}" for c in calls],
        "depth": depth_of(calls),
        "conflicts": sum(1 for c in calls if c.get("conflict")),
        "rru": sum(c.get("rru", 0) for c in calls), "wru": sum(c.get("wru", 0) for c in calls),
        "sqs_requests": sum(c.get("sqs_requests", 0) for c in calls),
    }
    shim.state["latency"] = 0
    for _ in range(job.get("cpu_warmup") or 30):
        invoke()
    ts = []
    for _ in range(job.get("cpu_reps") or 300):
        t0 = time.perf_counter()
        invoke()
        ts.append((time.perf_counter() - t0) * 1000)
    ts.sort()
    res["warm_cpu_ms"] = {"median": round(ts[len(ts) // 2], 3), "p90": round(ts[int(len(ts) * 0.9)], 3), "n": len(ts)}
    res["max_rss_mb"] = shim.state["max_rss"]
    out(res)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001
        import traceback
        out({"error": {"type": "M2SPerfHarness", "message": traceback.format_exc()[-2000:]}})
