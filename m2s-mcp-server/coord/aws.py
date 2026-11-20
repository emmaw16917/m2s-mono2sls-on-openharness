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
  6. 指标：Lambda 用各函数日志的 REPORT 行（按 RequestId 去重、热 Duration、
     billed ms、init），DynamoDB 容量含 GSI，SQS 请求按发送 + 2×接收估算；
     每个 pass 独占整分钟（minute_gap），负载结束后统一等 90 秒再读；
  7. 费用：按 billed 时长/调用次数、DynamoDB 容量与 SQS 请求粗算，写进结果和
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
# REPORT lines and DynamoDB's per-minute metrics arrive late; one wait after
# the load covers every pass (e2e_aws.collect_metrics(delay=0) per pass).
METRICS_DELAY_S = 90


def enabled() -> bool:
    return os.environ.get(ENV_FLAG, "").lower() in ("1", "true", "yes")


class MeasurementError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# the real AWS side (tests replace this with a fake)
# ---------------------------------------------------------------------------

class Backend(Protocol):
    def deploy(self, ws: Path, ref: str | None, stack: str, log: Path, variant: str = "opt",
               tags: dict[str, str] | None = None) -> None: ...
    def outputs(self, stack: str) -> dict[str, str]: ...
    def stack_resources(self, stack: str) -> dict[str, dict[str, str]]: ...
    def delete(self, stack: str, log: Path) -> None: ...
    def run_load(self, cmd: str, env: dict[str, str], out: Path, timeout_s: int) -> list[float]: ...
    def invocations(self, functions: dict[str, str], start: float, end: float) -> dict[str, int]: ...


class AwsBackend:
    """sam build/deploy + the load command + CloudWatch, through the existing
    e2e helpers (e2e_aws.sh / e2e_aws.stack_info), so the tool shares their
    environment quirks (PY312, sam build, retained resources on delete)."""

    def deploy(self, ws: Path, ref: str | None, stack: str, log: Path, variant: str = "opt",
               tags: dict[str, str] | None = None) -> None:
        import verify
        import e2e_aws

        if ref is None:
            # the candidate is the working tree, not a commit: deploy a copy
            # (sam build writes .aws-sam into the directory it builds)
            src = _worktree_copy(ws)
        else:
            src = verify.export_ref(ws, ref) / "output"
        try:
            if not (src / "template.yaml").is_file():
                raise MeasurementError(f"{ref or 'working tree'}: output/template.yaml does not exist")
            py312 = ({"PATH": os.pathsep.join([str(verify.PY312_DIR), str(verify.PY312_DIR / "Scripts"),
                                               os.environ.get("PATH", "")])}
                     if (verify.PY312_DIR / "python.exe").exists() else {})
            e2e_aws.sh([e2e_aws.SAM, "build"], cwd=src, env=py312, log=log, timeout=30 * 60)
            # distinct Environment values: the two stacks' table names must not collide
            env_tag = e2e_aws.environment_value(src / "template.yaml", variant)
            argv = [e2e_aws.SAM, "deploy", "--stack-name", stack, "--region", REGION, "--resolve-s3",
                    "--no-confirm-changeset", "--no-fail-on-empty-changeset",
                    "--capabilities", "CAPABILITY_IAM", "CAPABILITY_NAMED_IAM", "CAPABILITY_AUTO_EXPAND",
                    "--parameter-overrides", f"Environment={env_tag}"]
            if tags:
                argv += ["--tags", *[f"{k}={v}" for k, v in tags.items()]]
            p = e2e_aws.sh(argv, cwd=src, log=log, timeout=45 * 60, check=False)
            if p.returncode != 0:
                st = e2e_aws.stack_status(stack)
                if st not in ("CREATE_COMPLETE", "UPDATE_COMPLETE"):
                    raise MeasurementError(f"sam deploy of {stack} failed (stack {st}); see {log}")
        finally:
            if ref is None:
                shutil.rmtree(src.parent, ignore_errors=True)  # the temporary working-tree copy

    def outputs(self, stack: str) -> dict[str, str]:
        import e2e_aws

        outputs, _ = e2e_aws.stack_info(stack)
        return outputs

    def stack_resources(self, stack: str) -> dict[str, dict[str, str]]:
        """Logical id -> {type, physical}; the load/seed command maps benchmark
        table names (CartTable, ...) to the deployed tables."""
        import e2e_aws

        _, resources = e2e_aws.stack_info(stack)
        return {logical: {"type": str(r.get("type") or ""), "physical": str(r.get("physical") or "")}
                for logical, r in resources.items() if isinstance(r, dict)
                and r.get("type") and r.get("physical")}

    def delete(self, stack: str, log: Path) -> None:
        import e2e_aws

        e2e_aws.delete(stack, log)

    def stack_exists(self, stack: str) -> bool:
        import e2e_aws

        return e2e_aws.stack_exists(stack)

    def stack_tags(self, stack: str) -> dict[str, str]:
        """The stack's own tags (the rolling baseline records its HEAD here)."""
        import boto3

        cf = boto3.client("cloudformation", region_name=REGION)
        try:
            stack_info = cf.describe_stacks(StackName=stack)["Stacks"][0]
        except Exception:  # noqa: BLE001 -- a missing stack just means "not current"
            return {}
        return {t.get("Key", ""): t.get("Value", "") for t in stack_info.get("Tags") or []}

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

    def pass_metrics(self, functions: dict[str, str], tables: list[str], queues: list[str], start: float, end: float,
                     until: float) -> dict[str, Any]:
        """One pass's Lambda REPORT lines (e2e_aws's collector: deduped by
        request id), DynamoDB capacity including GSIs, and SQS requests."""
        import e2e_aws

        resources = {f"fn:{k}": {"type": "AWS::Lambda::Function", "physical": v} for k, v in functions.items()}
        resources.update({f"table:{i}": {"type": "AWS::DynamoDB::Table", "physical": t}
                          for i, t in enumerate(tables)})
        m = e2e_aws.collect_metrics(resources, start, end, until=until, delay=0)
        lam: dict[str, dict[str, Any]] = {}
        for logical in functions:
            reports = m["functions"].get(f"fn:{logical}") or []
            warm = [r["duration"] for r in reports if r["init"] is None]
            cold = [r["init"] for r in reports if r["init"] is not None]
            lam[logical] = {"invocations": len(reports),
                            # Judgment uses the warm durations; cost uses billed ms
                            # (AWSLambda's REPORT includes Duration + Init Duration).
                            "duration_sum_ms": round(sum(r["duration"] for r in reports), 3),
                            "duration_mean_ms": round(statistics.fmean(warm), 3) if warm else 0.0,
                            "warm_count": len(warm), "warm_duration_sum_ms": round(sum(warm), 3),
                            "billed_ms": sum(int(r["billed"]) for r in reports),
                            "init_count": len(cold),
                            "init_mean_ms": round(statistics.fmean(cold), 3) if cold else 0.0}
        units = {"rru": round(sum(t.get("ConsumedReadCapacityUnits", 0) for t in m["tables"].values()), 3),
                 "wru": round(sum(t.get("ConsumedWriteCapacityUnits", 0) for t in m["tables"].values()), 3),
                 "sqs_requests": self._sqs_requests(queues, start, end)}
        return {"lambda": lam, "units": units, "invocations": {k: v["invocations"] for k, v in lam.items()}}

    def _sqs_requests(self, queues: list[str], start: float, end: float) -> int:
        """Approximate request units: each send, receive and delete is billed;
        a received message is assumed to be deleted once."""
        if not queues:
            return 0
        import boto3

        cw = boto3.client("cloudwatch", region_name=REGION)
        m0, m1 = int(start // 60 * 60), int(end // 60 * 60) + 60

        def total(metric: str, queue: str) -> float:
            # the resource's physical id is the queue URL; CloudWatch wants the name
            name = queue.rstrip("/").rsplit("/", 1)[-1]
            pts = cw.get_metric_statistics(Namespace="AWS/SQS", MetricName=metric,
                                           Dimensions=[{"Name": "QueueName", "Value": name}],
                                           StartTime=m0, EndTime=m1, Period=60, Statistics=["Sum"])["Datapoints"]
            return sum(p.get("Sum", 0) for p in pts)

        sent = sum(total("NumberOfMessagesSent", q) for q in queues)
        received = sum(total("NumberOfMessagesReceived", q) for q in queues)
        return int(round(sent + 2 * received))

    def resource_tables(self, stack: str) -> list[str]:
        import e2e_aws

        _, resources = e2e_aws.stack_info(stack)
        return [str(r.get("physical")) for r in resources.values()
                if r.get("type") == "AWS::DynamoDB::Table" and r.get("physical")]

    def resource_queues(self, stack: str) -> list[str]:
        import e2e_aws

        _, resources = e2e_aws.stack_info(stack)
        return [str(r.get("physical")) for r in resources.values()
                if r.get("type") == "AWS::SQS::Queue" and r.get("physical")]

    def api_type(self, ws: Path, ref: str | None) -> str:
        if ref is None:
            path = ws / "output" / "template.yaml"
        else:
            import verify

            path = verify.export_ref(ws, ref) / "output" / "template.yaml"
        if not path.is_file():
            return "rest"
        import gates

        doc = gates.load_template(path)
        types = {res.get("Type") for res in (doc.get("Resources") or {}).values() if isinstance(res, dict)}
        return "http" if "AWS::Serverless::HttpApi" in types else "rest"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _worktree_copy(ws: Path) -> Path:
    """A buildable copy of the working tree's output/ (ref None = candidate)."""
    import tempfile

    src = ws / "output"
    if not (src / "template.yaml").is_file():
        raise MeasurementError("the working tree has no output/template.yaml")
    dst = Path(tempfile.mkdtemp(prefix="m2s-aws-src-")) / "output"
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns(".aws-sam", "__pycache__", "*.pyc", "node_modules"))
    return dst


def changed_functions(ws: Path, baseline_ref: str, ref: str | None) -> list[str]:
    """Directories under output/lambdas touched between the baseline and the
    candidate (a ref, or None = the working tree)."""
    diff = [baseline_ref] if ref is None else [f"{baseline_ref}..{ref}"]
    p = subprocess.run(["git", "diff", "--name-only", *diff, "--", "output/lambdas"],
                       cwd=ws, capture_output=True, text=True)
    if p.returncode != 0:
        raise MeasurementError(f"git diff {baseline_ref}..{ref or 'worktree'} failed: {p.stderr[-400:]}")
    lines = p.stdout.splitlines()
    if ref is None:
        # a brand-new file is untracked, so git diff does not list it
        untracked = subprocess.run(["git", "ls-files", "--others", "--exclude-standard", "--", "output/lambdas"],
                                   cwd=ws, capture_output=True, text=True)
        if untracked.returncode == 0:
            lines += untracked.stdout.splitlines()
    lazy = Path("output/lambdas")
    dirs: set[str] = set()
    for line in lines:
        path = Path(line)
        try:
            rel = path.relative_to(lazy)
        except ValueError:
            continue
        # output/lambdas/<app>/<function>/...
        if len(rel.parts) >= 2:
            dirs.add(rel.parts[1])
    return sorted(dirs)


def logical_names(ws: Path, ref: str | None, function_dirs: list[str]) -> dict[str, str]:
    """function dir -> Lambda logical id, from the template at `ref` (None = working tree)."""
    import verify

    template = (ws / "output" / "template.yaml") if ref is None else (verify.export_ref(ws, ref) / "output" / "template.yaml")
    if not template.is_file():
        return {}
    import gates

    doc = gates.load_template(template)  # CloudFormation tags (!Sub, !Ref, ...) included
    out = {}
    for logical, res in (doc.get("Resources") or {}).items():
        # SAM templates declare AWS::Serverless::Function; a hand-written
        # CloudFormation one may use AWS::Lambda::Function.
        if not isinstance(res, dict) or res.get("Type") not in ("AWS::Serverless::Function", "AWS::Lambda::Function"):
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


def _commit_sha(ws: Path, ref: str) -> str:
    try:
        p = subprocess.run(["git", "-C", str(ws), "rev-parse", ref], capture_output=True, text=True, timeout=30)
    except OSError:
        return ""
    return p.stdout.strip() if p.returncode == 0 else ""


def _minute_gap() -> None:
    """Start a pass in a minute of its own: DynamoDB capacity metrics only
    exist per minute, so adjacent passes must not share one (e2e_ec2.minute_gap)."""
    time.sleep(62 - time.time() % 60)


# ---------------------------------------------------------------------------
# the measurement
# ---------------------------------------------------------------------------

def measure_aws(ws: Path | str, candidate: str, *, ref: str | None = "HEAD", baseline_ref: str = "B0",
                baseline_label: str = "b0", load_cmd: str | None = None, repeats: int = 2, keep: bool = False,
                seed_cmd: str | None = None, load_env: dict[str, str] | None = None,
                backend: Backend | None = None, out_dir: Path | None = None) -> dict[str, Any]:
    """Measure one candidate against the baseline on AWS (ABAB). Default off:
    without M2S_AWS=1 it returns DISABLED before touching AWS. ref=None deploys
    the working tree (the O candidate before it is committed)."""
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
    base_stack, cand_stack = _stack_name(ws, baseline_label), _stack_name(ws, candidate)
    out_dir = out_dir or (ws / "perf" / "aws" / candidate)
    out_dir.mkdir(parents=True, exist_ok=True)
    log = out_dir / "aws.log"
    started = time.time()
    passes: list[dict[str, Any]] = []
    base_sha = _commit_sha(ws, baseline_ref)
    tags_of = getattr(backend, "stack_tags", None)

    def baseline_is_current(stack: str) -> bool:
        """The rolling baseline follows HEAD: a stack left from an earlier
        HEAD must be redeployed, or the next change is compared against old code."""
        if not _stack_exists(backend, stack):
            return False
        if not callable(tags_of) or not base_sha:
            return True  # no way to tell; the fake backend's behaviour
        try:
            return str((tags_of(stack) or {}).get("M2SBaseline") or "") == base_sha
        except Exception:  # noqa: BLE001 -- an unreadable tag means "not current"
            return False

    try:
        for ref_to_deploy, stack, variant in ((baseline_ref, base_stack, "v0"), (ref, cand_stack, "opt")):
            is_baseline = ref_to_deploy is baseline_ref
            if (baseline_is_current(stack) if is_baseline else False):
                passes.append({"note": f"reusing deployed stack {stack}"})
                continue
            extra = {"tags": {"M2SBaseline": base_sha}} if is_baseline and callable(tags_of) and base_sha else {}
            backend.deploy(ws, ref_to_deploy, stack, log, variant, **extra)
        base_outputs = backend.outputs(base_stack)
        cand_outputs = backend.outputs(cand_stack)
        target = {"b0": _endpoint(base_outputs), "candidate": _endpoint(cand_outputs)}
        if not target["b0"] or not target["candidate"]:
            raise MeasurementError("a stack has no HTTP endpoint output (ApiEndpoint/ApiUrl)")
        order = [("b0", base_stack), ("candidate", cand_stack)]
        pass_of = getattr(backend, "pass_metrics", None)
        tables_of = getattr(backend, "resource_tables", None)
        queues_of = getattr(backend, "resource_queues", None)
        stack_resources = getattr(backend, "stack_resources", None)
        resource_cache: dict[tuple[str, str], list[str]] = {}
        resources_cache: dict[str, dict[str, dict[str, str]]] = {}
        outputs_by_side = {"b0": base_outputs, "candidate": cand_outputs}

        def resources_of(stack: str, kind: str) -> list[str]:
            key = (stack, kind)
            if key not in resource_cache:
                getter = tables_of if kind == "tables" else queues_of
                try:
                    resource_cache[key] = list(getter(stack)) if callable(getter) else []
                except Exception:  # noqa: BLE001 -- capacity is evidence, not the verdict
                    resource_cache[key] = []
            return resource_cache[key]

        def resources_on_stack(stack: str) -> dict[str, dict[str, str]]:
            if stack not in resources_cache:
                if not callable(stack_resources):
                    resources_cache[stack] = {}
                else:
                    try:
                        resources_cache[stack] = stack_resources(stack)
                    except Exception as exc:  # noqa: BLE001 -- seeding/loading depend on it
                        raise MeasurementError(f"cannot read the resources of {stack}: {exc}") from exc
            return resources_cache[stack]

        def stack_env(side: str, stack: str, pass_no: str | None = None) -> dict[str, str]:
            env = {"M2S_AWS_TARGET": target[side], "M2S_AWS_SIDE": side, "M2S_AWS_REGION": REGION,
                   "M2S_AWS_STACK": stack,
                   "M2S_AWS_OUTPUTS": json.dumps(outputs_by_side[side], ensure_ascii=False),
                   "M2S_AWS_RESOURCES": json.dumps(resources_on_stack(stack), ensure_ascii=False),
                   **(load_env or {})}
            if pass_no:
                env["M2S_AWS_PASS"] = pass_no
            return env

        with coord_locks.hold_count("aws-load-client", owner="measure_aws"):
            if seed_cmd:
                # Seed each side once, before its first pass; the pass's minute
                # gap then keeps these writes out of the measured minutes.
                for side, stack in order:
                    proc = subprocess.run(seed_cmd, shell=True, env={**os.environ, **stack_env(side, stack)},
                                          capture_output=True, text=True, timeout=30 * 60)
                    if proc.returncode != 0:
                        raise MeasurementError(f"seed command failed for {side} (exit {proc.returncode}): "
                                               f"{proc.stderr[-800:]}")
                    passes.append({"note": f"seeded {side} via {seed_cmd}"})
            for i in range(max(1, repeats)):
                pair = order if i % 2 == 0 else list(reversed(order))
                for side, stack in pair:
                    out = out_dir / f"pass{i + 1}-{side}.json"
                    if callable(pass_of):
                        _minute_gap()  # a pass's DynamoDB minutes belong to it alone
                    t0 = time.time()
                    samples = backend.run_load(load_cmd, stack_env(side, stack, str(i + 1)), out, 30 * 60)
                    t1 = time.time()
                    passes.append({"pass": i + 1, "side": side, "stack": stack, "samples": samples,
                                   "summary": latency_summary(_warm(samples)),
                                   "start": t0, "end": t1, "window_s": round(t1 - t0, 1)})
        metric_passes = [p for p in passes if "side" in p]
        if callable(pass_of) and metric_passes:
            if METRICS_DELAY_S:
                time.sleep(METRICS_DELAY_S)
            for i, p in enumerate(metric_passes):
                nxt = metric_passes[i + 1]["start"] if i + 1 < len(metric_passes) else None
                until = min(p["end"] + 60, nxt) if nxt else p["end"] + 60
                physical = {k: _physical(backend, p["stack"], v) for k, v in logical.items()}
                m = pass_of(physical, resources_of(p["stack"], "tables"), resources_of(p["stack"], "queues"),
                            p["start"], p["end"], until)
                p["lambda"] = m.get("lambda") or {}
                p["units"] = m.get("units") or {"rru": 0.0, "wru": 0.0}
                p["invocations"] = m.get("invocations") or {}
                p["uncovered"] = sorted(k for k, n in p["invocations"].items() if n <= 0)
        elif metric_passes:
            # a backend without pass_metrics (tests): coverage only
            for p in metric_passes:
                physical = {k: _physical(backend, p["stack"], v) for k, v in logical.items()}
                p["invocations"] = backend.invocations(physical, p["start"] - 5, p["end"] + 60)
                p["lambda"] = {}
                p["units"] = {"rru": 0.0, "wru": 0.0}
                p["uncovered"] = sorted(k for k, n in p["invocations"].items() if n <= 0)
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

    def side_metrics(side: str) -> dict[str, Any]:
        ps = [p for p in passes if p.get("side") == side]

        def lam(key: str) -> float:
            """Sum one CloudWatch field over the changed functions and the passes."""
            return sum(float((entry or {}).get(key, 0) or 0)
                       for p in ps for entry in (p.get("lambda") or {}).values() if isinstance(entry, dict))

        inv = int(lam("invocations"))
        warm_n = int(lam("warm_count"))
        warm_ms = lam("warm_duration_sum_ms")
        dur = lam("duration_sum_ms")
        billed = lam("billed_ms")
        init_n = int(lam("init_count"))
        init_ms = sum(float((entry or {}).get("init_mean_ms", 0) or 0) * int((entry or {}).get("init_count", 0) or 0)
                      for p in ps for entry in (p.get("lambda") or {}).values() if isinstance(entry, dict))
        return {"passes": len(ps), "requests": sum(len(p.get("samples") or []) for p in ps),
                "invocations": inv,
                # the judged Duration is warm only; billed_ms (REPORT) is the cost basis
                "duration_sum_ms": round(dur, 3), "warm_count": warm_n,
                "warm_duration_sum_ms": round(warm_ms, 3),
                "duration_mean_ms": round(warm_ms / warm_n, 3) if warm_n else 0.0,
                "billed_ms": round(billed, 3),
                "init_count": init_n, "init_mean_ms": round(init_ms / init_n, 3) if init_n else 0.0,
                "rru": round(sum((p.get("units") or {}).get("rru", 0) for p in ps), 3),
                "wru": round(sum((p.get("units") or {}).get("wru", 0) for p in ps), 3),
                "sqs_requests": sum(int((p.get("units") or {}).get("sqs_requests", 0) or 0) for p in ps)}

    api_of = getattr(backend, "api_type", None)
    try:
        api = str(api_of(ws, ref)) if callable(api_of) else "rest"
    except Exception:  # noqa: BLE001
        api = "rest"
    result = {"status": "PASS" if verdict["verdict"] in ("better", "no_change", "worse") else "INCONCLUSIVE",
              "candidate": candidate, "ref": ref, "baseline_ref": baseline_ref, "baseline_sha": base_sha,
              "region": REGION,
              "stacks": {"b0": base_stack, "candidate": cand_stack, "candidate_deleted": not keep},
              "functions": logical, "passes": passes, "verdict": verdict, "uncovered": uncovered,
              "sides": {"b0": side_metrics("b0"), "candidate": side_metrics("candidate")},
              "api_type": api, "noise_threshold": threshold,
              "seconds": round(time.time() - started, 1), "log": str(log)}
    _append_cost(ws, {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "candidate": candidate, "region": REGION,
                      "api_type": api, "sides": result["sides"],
                      "invocations": {p.get("side"): sum((p.get("invocations") or {}).values())
                                      for p in passes if p.get("side")},
                      "note": "raw metrics; USD is computed by run_aws_verdict from perf-rules/prices.json"})
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
    # generated templates prefix the key with the app name (BookstoreApiUrl)
    for key, value in outputs.items():
        if value and re.search(r"(ApiUrl|ApiEndpoint|Endpoint|BaseUrl|ApiGatewayUrl|HttpApiUrl)$", key, re.I):
            return str(value).rstrip("/")
    return ""


def _physical(backend: Backend, stack: str, logical: str) -> str:
    method = getattr(backend, "physical", None)
    if callable(method):
        return str(method(stack, logical))
    return logical
