"""W4.7: same-region AWS measurement of one optimization candidate
(PIPELINE_V3_PLAN.md §4.13).

    M2S_AWS=1 才启用；默认关闭时没有任何 AWS 调用（measure_aws 直接返回 DISABLED）。

一次测量：
  1. 两个栈：B0 与候选各一个（m2s-<ws>-b0 / m2s-<ws>-<candidate>），B0 的栈
     由 O 阶段保留，候选的栈测完即拆（keep=True 时保留，调试用）；
  2. B0/候选交替（ABAB）：每个 pass 用同一个负载命令测一侧；
  3. 每次测量都取 aws-load-client 锁（与别的候选/别的运行串行）；
  4. CloudWatch 里改动函数的调用次数覆盖检查：没走到记 uncovered，不参与判定；
  5. 判定用改动的函数的调用次数与客户端样本（去掉每个 pass 的头一个样本=冷启动，
     样本 <10 的标出来）；差值小于噪声阈值判 no_change。阈值可由
     calibrate_noise() 用 B0 对 B0 的测量校准，写 perf/aws_noise.json；
  6. 费用：按 lambda 调用次数/时长与 DynamoDB 容量粗算，写进结果和
     perf/aws_costs.json。

真实运行要花 AWS 费用：先在 M2S_AWS=1 下由人确认，再调用本工具。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import statistics
import subprocess
import time
from pathlib import Path
from typing import Any, Protocol

from coord import locks as coord_locks

ENV_FLAG = "M2S_AWS"
NOISE_FILE = ("perf", "aws_noise.json")
COST_FILE = ("perf", "aws_costs.json")
DEFAULT_THRESHOLD = 0.10
MIN_SAMPLES = 10
REGION = "us-east-1"


def enabled() -> bool:
    return os.environ.get(ENV_FLAG, "").lower() in ("1", "true", "yes")


class MeasurementError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# the real AWS side (tests replace this with a fake)
# ---------------------------------------------------------------------------

class Backend(Protocol):
    def deploy(self, ws: Path, ref: str, stack: str, log: Path, variant: str = "opt") -> None: ...
    def outputs(self, stack: str) -> dict[str, str]: ...
    def delete(self, stack: str, log: Path) -> None: ...
    def run_load(self, cmd: str, env: dict[str, str], out: Path, timeout_s: int) -> list[float]: ...
    def invocations(self, functions: dict[str, str], start: float, end: float) -> dict[str, int]: ...


class AwsBackend:
    """sam build/deploy + the load command + CloudWatch, through the existing
    e2e helpers (e2e_aws.sh / e2e_aws.stack_info), so the tool shares their
    environment quirks (PY312, sam build, retained resources on delete)."""

    def deploy(self, ws: Path, ref: str, stack: str, log: Path, variant: str = "opt") -> None:
        import verify
        import e2e_aws

        src = verify.export_ref(ws, ref) / "output"
        if not (src / "template.yaml").is_file():
            raise MeasurementError(f"{ref}: output/template.yaml does not exist")
        py312 = ({"PATH": os.pathsep.join([str(verify.PY312_DIR), str(verify.PY312_DIR / "Scripts"),
                                           os.environ.get("PATH", "")])}
                 if (verify.PY312_DIR / "python.exe").exists() else {})
        e2e_aws.sh([e2e_aws.SAM, "build"], cwd=src, env=py312, log=log, timeout=30 * 60)
        # distinct Environment values: the two stacks' table names must not collide
        env_tag = e2e_aws.environment_value(src / "template.yaml", variant)
        p = e2e_aws.sh([e2e_aws.SAM, "deploy", "--stack-name", stack, "--region", REGION, "--resolve-s3",
                        "--no-confirm-changeset", "--no-fail-on-empty-changeset",
                        "--capabilities", "CAPABILITY_IAM", "CAPABILITY_NAMED_IAM", "CAPABILITY_AUTO_EXPAND",
                        "--parameter-overrides", f"Environment={env_tag}"],
                       cwd=src, log=log, timeout=45 * 60, check=False)
        if p.returncode != 0:
            st = e2e_aws.stack_status(stack)
            if st not in ("CREATE_COMPLETE", "UPDATE_COMPLETE"):
                raise MeasurementError(f"sam deploy of {stack} failed (stack {st}); see {log}")

    def outputs(self, stack: str) -> dict[str, str]:
        import e2e_aws

        outputs, _ = e2e_aws.stack_info(stack)
        return outputs

    def delete(self, stack: str, log: Path) -> None:
        import e2e_aws

        e2e_aws.delete(stack, log)

    def stack_exists(self, stack: str) -> bool:
        import e2e_aws

        return e2e_aws.stack_exists(stack)

    def physical(self, stack: str, logical: str) -> str:
        import e2e_aws

        _, resources = e2e_aws.stack_info(stack)
        return str((resources.get(logical) or {}).get("physical") or logical)

    def run_load(self, cmd: str, env: dict[str, str], out: Path, timeout_s: int) -> list[float]:
        full = {**os.environ, **env, "M2S_AWS_OUT": str(out)}
        p = subprocess.run(cmd, shell=True, env=full, capture_output=True, text=True, timeout=timeout_s)
        if p.returncode != 0:
            raise MeasurementError(f"load command failed (exit {p.returncode}): {p.stderr[-800:]}")
        try:
            data = json.loads(out.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise MeasurementError(f"load command did not write {{'samples': [ms]}} to {out}: {exc}")
        samples = data.get("samples") if isinstance(data, dict) else None
        if not isinstance(samples, list) or not samples:
            raise MeasurementError(f"{out}: samples must be a non-empty list of milliseconds")
        return [float(s) for s in samples]

    def invocations(self, functions: dict[str, str], start: float, end: float) -> dict[str, int]:
        import boto3

        cw = boto3.client("cloudwatch", region_name=REGION)
        out = {}
        for logical, physical in functions.items():
            total = 0
            for page in cw.get_paginator("get_metric_statistics").paginate(
                    Namespace="AWS/Lambda", MetricName="Invocations", Dimensions=[{"Name": "FunctionName", "Value": physical}],
                    StartTime=start, EndTime=end, Period=60, Statistics=["Sum"]):
                total += int(sum(dp.get("Sum", 0) for dp in page.get("Datapoints", [])))
            out[logical] = total
        return out


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def changed_functions(ws: Path, baseline_ref: str, ref: str) -> list[str]:
    """Directories under output/lambdas touched between the two refs."""
    p = subprocess.run(["git", "diff", "--name-only", f"{baseline_ref}..{ref}", "--", "output/lambdas"],
                       cwd=ws, capture_output=True, text=True)
    if p.returncode != 0:
        raise MeasurementError(f"git diff {baseline_ref}..{ref} failed: {p.stderr[-400:]}")
    lazy = Path("output/lambdas")
    dirs: set[str] = set()
    for line in p.stdout.splitlines():
        path = Path(line)
        try:
            rel = path.relative_to(lazy)
        except ValueError:
            continue
        # output/lambdas/<app>/<function>/...
        if len(rel.parts) >= 2:
            dirs.add(rel.parts[1])
    return sorted(dirs)


def logical_names(ws: Path, ref: str, function_dirs: list[str]) -> dict[str, str]:
    """function dir -> Lambda logical id, from the template at `ref`."""
    import verify

    template = (verify.export_ref(ws, ref) / "output" / "template.yaml")
    if not template.is_file():
        return {}
    import yaml

    doc = yaml.safe_load(template.read_text(encoding="utf-8")) or {}
    out = {}
    for logical, res in (doc.get("Resources") or {}).items():
        if not isinstance(res, dict) or res.get("Type") != "AWS::Lambda::Function":
            continue
        code = str((res.get("Properties") or {}).get("CodeUri") or "")
        for d in function_dirs:
            if d in Path(code).parts:
                out[d] = logical
    return out


def noise_threshold(ws: Path) -> float:
    data = _read_json(ws.joinpath(*NOISE_FILE)) or {}
    value = data.get("relative_threshold")
    return float(value) if isinstance(value, (int, float)) and value > 0 else DEFAULT_THRESHOLD


def _warm(samples: list[float]) -> list[float]:
    """Drop the first sample of a pass: it includes the cold start."""
    return samples[1:] if len(samples) > 1 else samples


def latency_summary(samples: list[float]) -> dict[str, float]:
    ordered = sorted(samples)

    def pct(p: float) -> float:
        if not ordered:
            return 0.0
        idx = min(len(ordered) - 1, max(0, round(p * (len(ordered) - 1))))
        return round(ordered[idx], 3)

    return {"n": len(ordered), "p50": pct(0.50), "p95": pct(0.95),
            "mean": round(statistics.fmean(ordered), 3) if ordered else 0.0}


def compare(base: list[float], cand: list[float], threshold: float) -> dict[str, Any]:
    b, c = latency_summary(base), latency_summary(cand)
    small = b["n"] < MIN_SAMPLES or c["n"] < MIN_SAMPLES
    if not b["p50"] or not c["p50"]:
        return {"verdict": "inconclusive", "base": b, "candidate": c, "small_samples": True}
    delta = (c["p50"] - b["p50"]) / b["p50"]
    verdict = "no_change" if abs(delta) < threshold else ("better" if delta < 0 else "worse")
    return {"verdict": verdict, "base": b, "candidate": c, "relative_delta": round(delta, 4),
            "threshold": threshold, "small_samples": small}


def _append_cost(ws: Path, entry: dict[str, Any]) -> None:
    path = ws.joinpath(*COST_FILE)
    data = _read_json(path) or {"measurements": []}
    measurements = data.get("measurements") if isinstance(data, dict) else None
    if not isinstance(measurements, list):
        measurements = []
    measurements.append(entry)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"measurements": measurements}, ensure_ascii=False, indent=1), encoding="utf-8")


def _stack_name(ws: Path, label: str) -> str:
    return f"m2s-{re.sub(r'[^A-Za-z0-9-]', '-', ws.name).lower()[:24]}-{label}".lower()[:60]


# ---------------------------------------------------------------------------
# the measurement
# ---------------------------------------------------------------------------

def measure_aws(ws: Path | str, candidate: str, *, ref: str = "HEAD", baseline_ref: str = "B0",
                load_cmd: str | None = None, repeats: int = 2, keep: bool = False,
                backend: Backend | None = None, out_dir: Path | None = None) -> dict[str, Any]:
    """Measure one candidate against B0 on AWS (ABAB). Default off: without
    M2S_AWS=1 it returns DISABLED before touching AWS."""
    ws = Path(ws).resolve()
    if not enabled():
        return {"status": "DISABLED", "note": f"{ENV_FLAG}=1 is required; a real run needs a person to approve the cost"}
    if not load_cmd:
        return {"status": "ERROR", "error": "load_cmd is required: a command that reads M2S_AWS_TARGET and writes "
                                            "{'samples': [ms]} to $M2S_AWS_OUT"}
    try:
        dirs = changed_functions(ws, baseline_ref, ref)
    except MeasurementError as exc:
        return {"status": "ERROR", "error": str(exc)}
    if not dirs:
        return {"status": "NO_CHANGES", "note": f"no output/lambdas change between {baseline_ref} and {ref}"}
    logical = logical_names(ws, ref, dirs)
    if len(logical) < len(dirs):
        missing = sorted(set(dirs) - set(logical))
        return {"status": "ERROR", "error": f"no Lambda logical id for {missing} in output/template.yaml"}
    backend = backend or AwsBackend()
    base_stack, cand_stack = _stack_name(ws, "b0"), _stack_name(ws, candidate)
    out_dir = out_dir or (ws / "perf" / "aws" / candidate)
    out_dir.mkdir(parents=True, exist_ok=True)
    log = out_dir / "aws.log"
    started = time.time()
    passes: list[dict[str, Any]] = []
    try:
        for ref_to_deploy, stack, variant in ((baseline_ref, base_stack, "v0"), (ref, cand_stack, "opt")):
            if _stack_exists(backend, stack):
                passes.append({"note": f"reusing deployed stack {stack}"})
            else:
                backend.deploy(ws, ref_to_deploy, stack, log, variant)
        base_outputs = backend.outputs(base_stack)
        cand_outputs = backend.outputs(cand_stack)
        target = {"b0": _endpoint(base_outputs), "candidate": _endpoint(cand_outputs)}
        if not target["b0"] or not target["candidate"]:
            raise MeasurementError("a stack has no HTTP endpoint output (ApiEndpoint/ApiUrl)")
        order = [("b0", base_stack), ("candidate", cand_stack)]
        with coord_locks.hold_count("aws-load-client", owner="measure_aws"):
            for i in range(max(1, repeats)):
                pair = order if i % 2 == 0 else list(reversed(order))
                for side, stack in pair:
                    out = out_dir / f"pass{i + 1}-{side}.json"
                    t0 = time.time()
                    samples = backend.run_load(load_cmd, {"M2S_AWS_TARGET": target[side], "M2S_AWS_SIDE": side,
                                                          "M2S_AWS_PASS": str(i + 1)}, out, 30 * 60)
                    t1 = time.time()
                    counts = backend.invocations({k: _physical(backend, stack, v) for k, v in logical.items()}, t0 - 5, t1 + 60)
                    uncovered = sorted(k for k, n in counts.items() if n <= 0)
                    passes.append({"pass": i + 1, "side": side, "stack": stack, "samples": samples,
                                   "summary": latency_summary(_warm(samples)), "invocations": counts,
                                   "uncovered": uncovered, "window_s": round(t1 - t0, 1)})
    finally:
        if not keep:
            try:
                backend.delete(cand_stack, log)
            except Exception as exc:  # noqa: BLE001 -- teardown must not hide the measurement
                passes.append({"note": f"candidate stack {cand_stack} was not deleted: {exc}"})
    base_samples = [s for p in passes if p.get("side") == "b0" for s in _warm(p.get("samples") or [])]
    cand_samples = [s for p in passes if p.get("side") == "candidate" for s in _warm(p.get("samples") or [])]
    threshold = noise_threshold(ws)
    verdict = compare(base_samples, cand_samples, threshold)
    uncovered = sorted({f for p in passes if p.get("side") == "candidate" for f in p.get("uncovered") or []})
    if uncovered:
        verdict["verdict"] = "uncovered"
    result = {"status": "PASS" if verdict["verdict"] in ("better", "no_change", "worse") else "INCONCLUSIVE",
              "candidate": candidate, "ref": ref, "baseline_ref": baseline_ref, "region": REGION,
              "stacks": {"b0": base_stack, "candidate": cand_stack,
                         "candidate_deleted": not keep},
              "functions": logical, "passes": passes, "verdict": verdict, "uncovered": uncovered,
              "noise_threshold": threshold, "seconds": round(time.time() - started, 1), "log": str(log)}
    _append_cost(ws, {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "candidate": candidate, "region": REGION,
                      "invocations": {p.get("side"): sum((p.get("invocations") or {}).values())
                                      for p in passes if p.get("side")},
                      "note": "estimate; Lambda/DynamoDB prices from perf-rules/prices.json where available"})
    (out_dir / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return result


def calibrate_noise(ws: Path | str, *, load_cmd: str, repeats: int = 3, backend: Backend | None = None) -> dict[str, Any]:
    """B0 against B0: the spread is the noise floor; write perf/aws_noise.json."""
    ws = Path(ws).resolve()
    if not enabled():
        return {"status": "DISABLED", "note": f"{ENV_FLAG}=1 is required"}
    backend = backend or AwsBackend()
    stack = _stack_name(ws, "b0")
    if not _stack_exists(backend, stack):
        backend.deploy(ws, "B0", stack, ws / "perf" / "aws" / "calibrate.log", "v0")
    endpoint = _endpoint(backend.outputs(stack))
    if not endpoint:
        return {"status": "ERROR", "error": f"stack {stack} has no HTTP endpoint output"}
    runs = []
    with coord_locks.hold_count("aws-load-client", owner="calibrate_noise"):
        for i in range(max(2, repeats)):
            out = ws / "perf" / "aws" / f"calibrate-{i + 1}.json"
            out.parent.mkdir(parents=True, exist_ok=True)
            runs.append(backend.run_load(load_cmd, {"M2S_AWS_TARGET": endpoint, "M2S_AWS_SIDE": "b0",
                                                    "M2S_AWS_PASS": str(i + 1)}, out, 30 * 60))
    p50s = [latency_summary(_warm(r))["p50"] for r in runs]
    spread = (max(p50s) - min(p50s)) / statistics.fmean(p50s) if statistics.fmean(p50s) else 1.0
    data = {"relative_threshold": round(max(0.05, spread * 1.5), 4), "b0_runs": p50s,
            "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    path = ws.joinpath(*NOISE_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"status": "PASS", **data, "file": str(path)}


def _stack_exists(backend: Backend, stack: str) -> bool:
    method = getattr(backend, "stack_exists", None)
    if callable(method):
        return bool(method(stack))
    return False


def _endpoint(outputs: dict[str, str]) -> str:
    for key in ("ApiEndpoint", "ApiUrl", "Endpoint", "ApiGatewayUrl", "BaseUrl", "HttpApiUrl"):
        if outputs.get(key):
            return outputs[key].rstrip("/")
    return ""


def _physical(backend: Backend, stack: str, logical: str) -> str:
    method = getattr(backend, "physical", None)
    if callable(method):
        return str(method(stack, logical))
    return logical
