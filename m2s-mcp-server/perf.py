"""Optimization-line tools (PERF_OPTIMIZATION_DESIGN.md §2, §3, §5, §7, §8):
run_proxy_metrics, run_rie_verdict, update_ledger.

Benchmarks are written by m2s-perf-optimizer under WS/perf/benchmarks/
<function>/<id>.json, in the verification case format (request or sqs +
fixtures) plus:
    "data_volume": "<the data-volume assumption, in words>"   (required)
    "generate": {"<Table>": {"count": N, "template": {... "{i}" ...}}}
        placeholders are integer expressions over i, now_ms, now_s, e.g.
        "{now_ms // 300000 - 432 + i}" for time buckets around the run
The same benchmark is used before and after a change. Measurements use
verify-tools/perf-shim.js: the SDK's HTTP send is replaced by a fixed
latency plus a response computed from the benchmark's table data (§2.1).

Versions: the baseline is a git ref of the workspace repo (default HEAD =
v0 plus the changes kept so far), exported with `git archive`; the
candidate is the working tree. Both go through `sam build`.
"""

from __future__ import annotations

import functools
import json
import math
import os
import re
import shutil
import statistics
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Any

import verify
from coord import locks as coord_locks
from verify import VerifyError, Model, _run, _env, _git, export_ref, sam_build, TMP_ROOT, VERIFY_TOOLS, PY_RIE_IMAGE, PY312_DIR


def _rie_locked(fn):
    @functools.wraps(fn)
    def wrapper(workspace, *args, **kwargs):
        with coord_locks.hold_count("docker-rie", owner="run_rie_verdict"):
            return fn(workspace, *args, **kwargs)
    return wrapper

REPO_PERF = Path(__file__).resolve().parent / "perf-rules"
LEDGER = REPO_PERF / "ledger.json"
PRICES = REPO_PERF / "prices.json"
RIE_IMAGE = "public.ecr.aws/lambda/nodejs:22-x86_64"
PY_TOOLS = VERIFY_TOOLS / "py"
# Files of the Python shim, copied flat into /opt/m2s-shim of an RIE container;
# AWS_LAMBDA_EXEC_WRAPPER=/opt/m2s-shim/wrapper starts the runtime through m2s_boot.py.
PY_SHIM_FILES = [PY_TOOLS / "m2s_boot.py", PY_TOOLS / "m2s_perf_shim.py", PY_TOOLS / "m2s_tx_conflicts.py"]
PY_WRAPPER = "\n".join(['#!/bin/sh', 'py="$1"; shift', 'exec "$py" /opt/m2s-shim/m2s_boot.py "$@"', ''])


def _is_python(fn: dict[str, Any]) -> bool:
    return str(fn.get("runtime", "")).startswith("python")


def _layer_dirs(build_dir: Path, fn: dict[str, Any]) -> list[Path]:
    return [build_dir / ly for ly in fn.get("layers", [])]
ROUNDS = 5          # alternating rounds; gives >= 5 init samples and 5 warm medians (§3.3)
WARM_INVOKES = 30
SCREEN_LATENCY = 10
MULTI_LATENCIES = [0, 5, 15]
# Real DynamoDB round trips are several ms, so 0 ms only shows the fixed
# overhead of a change (e.g. a thread pool) with nothing to overlap. When
# every realistic latency is clearly better, 0 ms is reported as a reference
# and does not decide (user ruling 2026-09-30).
REALISTIC_LATENCIES = [5, 10, 15]
# Per-operation round trips calibrated on real AWS; sizes a change's effect
# (network_ms_cal), never decides a verdict.
LATENCY_PROFILE = Path(__file__).resolve().parent / "perf-rules" / "latency-profile.json"
# "CPU in a reasonable range" for that rule: candidate warm CPU median at
# most this many times the baseline median.
CPU_RATIO_LIMIT = 2.0


# ---------------------------------------------------------------------------
# Benchmarks
# ---------------------------------------------------------------------------

def load_benchmarks(ws: Path, ids: list[str] | None = None, functions: list[str] | None = None) -> list[dict[str, Any]]:
    out = []
    seen: dict[str, str] = {}
    for p in sorted((ws / "perf" / "benchmarks").glob("*/*.json")):
        b = json.loads(p.read_text(encoding="utf-8"))
        b.setdefault("id", p.stem)
        b.setdefault("function", p.parent.name)
        b["_file"] = str(p.relative_to(ws)).replace("\\", "/")
        if b["id"] in seen:
            # run_proxy_metrics and the RIE/AWS selectors key results by id:
            # a duplicate would silently overwrite the other benchmark.
            raise VerifyError(f"duplicate benchmark id {b['id']!r}: {seen[b['id']]} and {b['_file']}; "
                              "benchmark ids are global, not per function")
        seen[b["id"]] = b["_file"]
        if (ids and b["id"] not in ids) or (functions and b["function"] not in functions):
            continue
        out.append(b)
    if not out:
        raise VerifyError("no matching benchmarks under perf/benchmarks/<function>/*.json")
    return out


_EXPR_RE = re.compile(r"\{([^{}]+)\}")


def _eval_int(expr: str, names: dict[str, int]) -> int | None:
    """Integer arithmetic over i / now_ms / now_s (+ - * // %), or None if
    `expr` is anything else -- so ordinary braces in text stay untouched."""
    import ast as _ast
    try:
        tree = _ast.parse(expr.strip(), mode="eval")
    except SyntaxError:
        return None

    def ev(n: Any) -> int:
        if isinstance(n, _ast.Expression):
            return ev(n.body)
        if isinstance(n, _ast.Constant) and isinstance(n.value, int) and not isinstance(n.value, bool):
            return n.value
        if isinstance(n, _ast.Name) and n.id in names:
            return names[n.id]
        if isinstance(n, _ast.UnaryOp) and isinstance(n.op, _ast.USub):
            return -ev(n.operand)
        ops = {_ast.Add: lambda a, b: a + b, _ast.Sub: lambda a, b: a - b, _ast.Mult: lambda a, b: a * b,
               _ast.FloorDiv: lambda a, b: a // b, _ast.Mod: lambda a, b: a % b}
        if isinstance(n, _ast.BinOp) and type(n.op) in ops:
            return ops[type(n.op)](ev(n.left), ev(n.right))
        raise ValueError(expr)

    try:
        return ev(tree)
    except (ValueError, ZeroDivisionError):
        return None


def _fill(tpl: Any, i: int, now_ms: int | None = None) -> Any:
    """Expand a generate template for item i. Placeholders are integer
    expressions over i, now_ms and now_s ("{i}", "{i % 7}",
    "{now_ms // 300000 - 432 + i}"): time-relative data, such as buckets
    around the current time, keeps a benchmark valid whenever it runs. A
    placeholder that is the whole string becomes a number."""
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    names = {"i": i, "now_ms": now_ms, "now_s": now_ms // 1000}
    if isinstance(tpl, str):
        full = _EXPR_RE.fullmatch(tpl)
        if full:
            value = _eval_int(full.group(1), names)
            if value is not None:
                return value
        return _EXPR_RE.sub(lambda m: str(v) if (v := _eval_int(m.group(1), names)) is not None else m.group(0), tpl)
    if isinstance(tpl, dict):
        return {k: _fill(v, i, now_ms) for k, v in tpl.items()}
    if isinstance(tpl, list):
        return [_fill(v, i, now_ms) for v in tpl]
    return tpl


def check_benchmark(model: Model, b: dict[str, Any]) -> list[str]:
    where = b.get("_file", b.get("id"))
    errs = []
    if not str(b.get("data_volume", "")).strip():
        errs.append(f"{where}: 'data_volume' (the data-volume assumption, in words) is required")
    as_case = {k: v for k, v in b.items() if k in ("id", "function", "request", "sqs", "fixtures")}
    as_case.update({"kind": "happy", "spec_refs": ["blueprint:perf-benchmark"], "expect": {"status": 200}})
    errs += [e for e in verify.check_case(model, as_case, with_template=True) if "expect" not in e]
    for t, g in (b.get("generate") or {}).items():
        errs += verify._name_err(model, where, "generate", t, "table", True)
        if not isinstance(g, dict) or not isinstance(g.get("count"), int) or not isinstance(g.get("template"), dict):
            errs.append(f"{where}: generate.{t} must be {{count: int, template: {{...}}}}")
    return errs


def _fixture_file(model: Model, b: dict[str, Any], out: Path) -> Path:
    tables: dict[str, Any] = {}
    for lid, t in model.tables.items():
        c = t["create"]
        key = [k["AttributeName"] for k in sorted(c["KeySchema"], key=lambda k: k["KeyType"] != "HASH")]
        idx = {g["IndexName"]: [k["AttributeName"] for k in sorted(g["KeySchema"], key=lambda k: k["KeyType"] != "HASH")]
               for g in c.get("GlobalSecondaryIndexes", []) + c.get("LocalSecondaryIndexes", [])}
        tables[lid] = {"key": key, "indexes": idx, "items": []}
    for alias, items in (b.get("fixtures") or {}).items():
        tables[model.table_id(alias)]["items"].extend(items)
    for alias, g in (b.get("generate") or {}).items():
        now_ms = int(time.time() * 1000)  # one "now" for the whole table: buckets cannot straddle a boundary
        tables[model.table_id(alias)]["items"].extend(_fill(g["template"], i, now_ms) for i in range(g["count"]))
    out.write_text(json.dumps({"tables": tables}), encoding="utf-8")
    return out


def _event(model: Model, b: dict[str, Any]) -> dict[str, Any]:
    event, gw = verify.build_event(model, {**b, "id": b["id"]})
    if gw is not None:
        raise VerifyError(f"benchmark {b['id']}: the request would be answered by API Gateway (anonymous request to an authorized route); add request.identity")
    return event


# ---------------------------------------------------------------------------
# Versions
# ---------------------------------------------------------------------------

def working_tree_changed(ws: Path, ref: str) -> list[str]:
    if not verify.has_own_repo(ws):
        raise VerifyError(f"{ws} has no .git of its own; the v0 snapshot (SKILL.md §6) must exist before optimization")
    # Not _git(): it strips the whole output, which eats the leading status
    # column of the first porcelain line (" M output/x" -> "utput/x").
    p = _run(["git", "-C", str(ws), "status", "--porcelain", "--", "output"], capture_output=True, text=True, encoding="utf-8", errors="replace")
    diff = _git(ws, "diff", "--name-only", ref, "--", "output")
    return sorted({l[3:] for l in p.stdout.splitlines() if l.strip()} | set(diff.splitlines()))


# ---------------------------------------------------------------------------
# Proxy metrics (local Node, perf-harness.js)
# ---------------------------------------------------------------------------

def _harness(model: Model, build_dir: Path, b: dict[str, Any], work: Path) -> dict[str, Any]:
    fn = model.functions[b["function"]]
    work.mkdir(parents=True, exist_ok=True)
    fx = _fixture_file(model, b, work / f"{b['id']}.fixtures.json")
    job = {"fn_dir": str(build_dir / fn["logical"]), "handler": fn["handler"], "env": fn["env"], "event": _event(model, b),
           "fixtures_path": str(fx), "depth_latency_ms": SCREEN_LATENCY, "cpu_warmup": 30, "cpu_reps": 300,
           "latency_profile": json.loads(LATENCY_PROFILE.read_text(encoding="utf-8")) if LATENCY_PROFILE.exists() else None,
           "layer_dirs": [str(d / "python") for d in _layer_dirs(build_dir, fn)]}
    jp = work / f"{b['id']}.harness.json"
    jp.write_text(json.dumps(job), encoding="utf-8")
    if _is_python(fn):
        # Python 3.12 with Lambda's boto3/botocore (conda env m2s-py312).
        py = PY312_DIR / "python.exe"
        if not py.exists():
            raise VerifyError(f"{py} not found: proxy metrics of Python functions need a local Python 3.12 with boto3")
        cmd = [str(py), str(PY_TOOLS / "m2s_perf_harness.py"), str(jp)]
    else:
        cmd = [shutil.which("node", path=_env()["PATH"]) or "node", str(VERIFY_TOOLS / "perf-harness.js"), str(jp)]
    p = _run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", env=_env(), timeout=900)
    line = next((l for l in p.stdout.splitlines() if l.startswith("@@M2S@@")), None)
    if not line:
        raise VerifyError(f"perf-harness produced no result for {b['id']}: {p.stderr[-2000:]}")
    return json.loads(line[7:])


def _calibrated_estimate(proxies: dict[str, Any]) -> dict[str, Any] | None:
    """Expected warm network time per invocation on real AWS (same region),
    from latency-profile.json. Sizing only: the RIE runs still decide."""
    b, c = (proxies.get(k, {}).get("network_ms_cal") for k in ("base", "cand"))
    if not b or not c:
        return None
    return {"base_ms": b["ms"], "cand_ms": c["ms"], "delta_ms": round(c["ms"] - b["ms"], 2),
            "uncalibrated_ops": sorted(set(b["uncalibrated_ops"]) | set(c["uncalibrated_ops"])),
            "note": "critical-path DynamoDB round trips only (no CPU, no API Gateway/Lambda invoke ~16-21 ms per request); "
                    f"evidence grade {_profile_grade()}, see perf-rules/latency-profile.json"}


def _profile_grade() -> str:
    try:
        return json.loads(LATENCY_PROFILE.read_text(encoding="utf-8")).get("grade", "E")
    except (OSError, ValueError):
        return "?"


def _valid(m: dict[str, Any]) -> str | None:
    """§3.1 item 1: the event must reach the real code path."""
    if m.get("error"):
        return f"handler threw {m['error'].get('type')}: {m['error'].get('message')}"
    if m.get("status") is not None and not 200 <= int(m["status"]) < 300:
        return f"status {m['status']} (benchmarks must exercise a success path)"
    if not m.get("calls"):
        return "no SDK calls were made -- the event does not reach the handler's data path"
    return None


def run_proxy_metrics(workspace: str, functions: list[str] | None = None, benchmarks: list[str] | None = None, ref: str | None = None) -> dict[str, Any]:
    ws = Path(workspace)
    model = Model(ws)
    bs = load_benchmarks(ws, benchmarks, functions)
    errs = [e for b in bs for e in check_benchmark(model, b)]
    if errs:
        return {"status": "FAIL", "schema_errors": errs}
    root = export_ref(ws, ref) if ref else ws
    build = sam_build(root)
    work = TMP_ROOT / re.sub(r"[^A-Za-z0-9_-]", "_", ws.name) / "perf" / f"{time.strftime('%Y%m%d-%H%M%S')}-proxy"
    res = {}
    for b in bs:
        m = _harness(model, build, b, work)
        m["valid"] = _valid(m) is None
        m["invalid_reason"] = _valid(m)
        m["cost_usd"] = cost(m, None, model.functions[b["function"]]["memory"], model.functions[b["function"]].get("architecture", "x86_64"))
        m["data_volume"] = b["data_volume"]
        res[b["id"]] = m
    return {"status": "OK", "version": ref or "working tree", "benchmarks": res,
            "note": "calls/depth at 10ms fixed latency; network_ms_cal = the same call chains weighted by the per-operation "
                    "round trips of perf-rules/latency-profile.json (real-AWS calibrated, sizing only); warm_cpu = wall time per invocation at 0 latency (median of 300, "
                    "local Node, or local Python 3.12 with Lambda's boto3 for Python functions); "
                    "capacity units estimated from request/response sizes; no 1MB pagination or FilterExpression in the shim"}


# ---------------------------------------------------------------------------
# Cost (§4)
# ---------------------------------------------------------------------------

def _prices() -> dict[str, Any]:
    return json.loads(PRICES.read_text(encoding="utf-8"))


def cost(m: dict[str, Any], duration_ms: float | None, memory_mb: int, arch: str = "x86_64") -> dict[str, Any]:
    p = _prices()
    gb_second = p["lambda_gb_second_arm64"] if arch == "arm64" else p["lambda_gb_second"]
    units = (m.get("rru", 0) * p["dynamodb_read_request_unit"] + m.get("wru", 0) * p["dynamodb_write_request_unit"]
             + m.get("sqs_requests", 0) * p["sqs_request"])
    out = {"units_usd": units, "lambda_request_usd": p["lambda_request"]}
    if duration_ms is not None:
        out["duration_usd"] = duration_ms / 1000.0 * memory_mb / 1024.0 * gb_second
        out["total_usd"] = units + out["duration_usd"] + p["lambda_request"]
    return out


# ---------------------------------------------------------------------------
# RIE measurement
# ---------------------------------------------------------------------------

_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _invoke(port: int, event: dict[str, Any], timeout: float = 120) -> Any:
    req = urllib.request.Request(f"http://127.0.0.1:{port}/2015-03-31/functions/function/invocations",
                                 data=json.dumps(event).encode(), headers={"Content-Type": "application/json"}, method="POST")
    with _opener.open(req, timeout=timeout) as r:
        body = r.read().decode("utf-8", errors="replace")
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return body


def _docker(*args: str, check: bool = True) -> str:
    p = _run(["docker", *args], capture_output=True, text=True, encoding="utf-8", errors="replace", env=_env())
    if check and p.returncode != 0:
        raise VerifyError(f"docker {args[0]} failed: {p.stderr.strip()[:500]}")
    return p.stdout + p.stderr


def _copy_py_shim(container: str) -> None:
    """docker cp creates only the last path component, so stage the shim
    files in one directory and copy that as /opt/m2s-shim."""
    stage = TMP_ROOT / "py-shim"
    shutil.rmtree(stage, ignore_errors=True)
    stage.mkdir(parents=True, exist_ok=True)
    for f in PY_SHIM_FILES:
        shutil.copyfile(f, stage / f.name)
    # Written here with LF line endings (a CRLF shebang does not run); docker
    # cp from Windows gives files mode 0755, so it is executable.
    (stage / "wrapper").write_bytes(PY_WRAPPER.encode())
    _docker("cp", str(stage) + "/.", f"{container}:/opt/m2s-shim")


def rie_round(model: Model, build_dir: Path, b: dict[str, Any], fixtures: Path, latency: int, label: str, port: int) -> dict[str, Any]:
    """One fresh container: 1 cold + WARM_INVOKES warm invocations; returns
    init (rapid INIT REPORT), warm REPORT durations and the shim's max RSS."""
    fn = model.functions[b["function"]]
    name = f"m2sp-{label}"
    _docker("rm", "-f", name, check=False)
    py = _is_python(fn)
    # Lambda sets both region variables; botocore reads AWS_DEFAULT_REGION.
    env = {**fn["env"], "AWS_REGION": "us-east-1", "AWS_DEFAULT_REGION": "us-east-1", "AWS_ACCESS_KEY_ID": "AKIDPERF", "AWS_SECRET_ACCESS_KEY": "perf",
           "AWS_LAMBDA_FUNCTION_MEMORY_SIZE": str(fn["memory"]),
           "SHIM_FIXTURES": "/var/task/.m2s-fixtures.json", "SHIM_LATENCY_MS": str(latency), "SHIM_LOG": "1", "SHIM_FN_DIR": "/var/task"}
    # Node: the shim is preloaded with --require. Python: an exec wrapper, not
    # PYTHONPATH -- setting PYTHONPATH makes the Lambda bootstrap skip the
    # layer paths (/opt/python...).
    env.update({"AWS_LAMBDA_EXEC_WRAPPER": "/opt/m2s-shim/wrapper"} if py else {"NODE_OPTIONS": "--require /var/task/.m2s-perf-shim.js"})
    cpus = round(int(fn["memory"]) / 1769, 3)
    env_args = [a for k, v in env.items() for a in ("-e", f"{k}={v}")]
    _docker("create", "--name", name, "--cpus", str(cpus), "-p", f"127.0.0.1:{port}:8080", *env_args, PY_RIE_IMAGE if py else RIE_IMAGE, fn["handler"])
    try:
        _docker("cp", str(build_dir / fn["logical"]) + "/.", f"{name}:/var/task")
        for layer in _layer_dirs(build_dir, fn):
            _docker("cp", str(layer) + "/.", f"{name}:/opt")
        if py:
            _copy_py_shim(name)
        else:
            _docker("cp", str(VERIFY_TOOLS / "perf-shim.js"), f"{name}:/var/task/.m2s-perf-shim.js")
            _docker("cp", str(VERIFY_TOOLS / "tx-conflicts.js"), f"{name}:/var/task/.m2s-tx-conflicts.js")
        _docker("cp", str(fixtures), f"{name}:/var/task/.m2s-fixtures.json")
        _docker("start", name)
        time.sleep(1.0)
        event = _event(model, b)
        first = _invoke(port, event)
        for _ in range(WARM_INVOKES):
            _invoke(port, event)
        logs = _docker("logs", name)
    finally:
        _docker("rm", "-f", name, check=False)
    init = re.search(r"INIT REPORT\(durationMs: ([\d.]+)", logs)
    durs = [float(x) for x in re.findall(r"REPORT RequestId: \S+\s+Duration: ([\d.]+) ms", logs)]
    rss = [int(x) for x in re.findall(r'"max_rss_mb":(\d+)', logs)]
    conflicts = sum(1 for l in logs.splitlines() if "[m2s-shim] " in l and '"conflict":true' in l)
    err = first if isinstance(first, dict) and "errorType" in first else None
    return {"init_ms": float(init.group(1)) if init else None, "cold_ms": durs[0] if durs else None,
            "warm_ms": durs[1:], "max_rss_mb": max(rss) if rss else None, "error": err, "conflicts": conflicts,
            "status": first.get("statusCode") if isinstance(first, dict) else None, "cpus": cpus}


def _measure_pair(model: Model, builds: dict[str, Path], b: dict[str, Any], fixtures: Path, latency: int) -> dict[str, Any]:
    """ROUNDS alternating rounds (order flips each round to cancel drift)."""
    res = {v: {"warm_round_medians": [], "init_ms": [], "warm_all": [], "max_rss_mb": 0, "errors": [], "conflicts": 0} for v in builds}
    for r in range(ROUNDS):
        order = list(builds) if r % 2 == 0 else list(reversed(list(builds)))
        for i, v in enumerate(order):
            rr = rie_round(model, builds[v], b, fixtures, latency, f"{b['function']}-{v}", 9300 + i)
            if rr["error"]:
                res[v]["errors"].append(rr["error"])
            if rr["warm_ms"]:
                res[v]["warm_round_medians"].append(statistics.median(rr["warm_ms"]))
                res[v]["warm_all"].extend(rr["warm_ms"])
            if rr["init_ms"] is not None:
                res[v]["init_ms"].append(rr["init_ms"])
            res[v]["max_rss_mb"] = max(res[v]["max_rss_mb"], rr["max_rss_mb"] or 0)
            res[v]["conflicts"] += rr["conflicts"]
            res[v]["cpus"] = rr["cpus"]
    for v in res:
        w = sorted(res[v].pop("warm_all"))
        res[v]["warm_p50"] = statistics.median(w) if w else None
        res[v]["warm_p90"] = w[int(0.9 * (len(w) - 1))] if w else None
    return res


def _direction(base: list[float], cand: list[float]) -> str:
    """§3.3: clearly better/worse only when the two ranges do not overlap."""
    if not base or not cand:
        return "unknown"
    if max(cand) < min(base):
        return "better"
    if min(cand) > max(base):
        return "worse"
    return "no_diff"


def _proxy_direction(base: dict, cand: dict) -> dict[str, str]:
    d = {}
    for k in ("calls", "depth", "rru", "wru", "sqs_requests"):
        d[k] = "better" if cand[k] < base[k] else "worse" if cand[k] > base[k] else "same"
    bc, cc = base["warm_cpu_ms"], cand["warm_cpu_ms"]
    d["warm_cpu"] = "worse" if cc["median"] > bc["p90"] else "better" if cc["p90"] < bc["median"] else "same"
    return d


def native_binaries(build_dir: Path, logical: str) -> list[str]:
    """Compiled extension modules in one function's build (Python .so/.pyd,
    Node .node). Their presence means an architecture change needs wheels /
    prebuilds for the new architecture -- the local x86_64 RIE cannot tell."""
    root = build_dir / logical
    return sorted(f.relative_to(root).as_posix() for f in root.rglob("*") if f.suffix in (".so", ".pyd", ".node"))[:20] if root.exists() else []


def _judge(ws: Path, model: Model, builds: dict[str, Path], b: dict[str, Any], baseline: str, changed: list[str],
           latencies: list[int] | None, archs: dict[str, str] | None = None) -> dict[str, Any]:
    """One benchmark, base vs cand builds: proxies, conflict veto, RIE rounds
    and the §3.2 decision. `veto` names a hard reason (conflicts / rss)."""
    benchmark = b["id"]
    work = TMP_ROOT / re.sub(r"[^A-Za-z0-9_-]", "_", ws.name) / "perf" / f"{time.strftime('%Y%m%d-%H%M%S')}-verdict-{benchmark}"
    work.mkdir(parents=True, exist_ok=True)
    fx = _fixture_file(model, b, work / "fixtures.json")
    mem = int(model.functions[b["function"]]["memory"])
    # Architecture per version: the RIE rounds always run the local x86_64
    # images, so an x86_64 -> arm64 change shows up only in the duration price
    # (real-AWS A/B: arm64 as fast or faster, PERF_OPTIMIZATION_DESIGN.md A.4).
    archs = archs or {"base": model.functions[b["function"]].get("architecture", "x86_64"),
                      "cand": model.functions[b["function"]].get("architecture", "x86_64")}

    proxies = {v: _harness(model, builds[v], b, work / v) for v in builds}
    invalid = {v: _valid(m) for v, m in proxies.items() if _valid(m)}
    if invalid:
        return {"status": "INVALID", "verdict": "revert", "reason": "benchmark does not reach the real code path (§3.1-1)", "invalid": invalid, "proxies": proxies}
    # Conflict veto (§6.2): concurrent writes to one item that real DynamoDB
    # would reject with TransactionConflict. Checked before the RIE rounds --
    # the change is reverted whatever it measures.
    if proxies["cand"].get("conflicts", 0) > proxies["base"].get("conflicts", 0):
        return {"status": "OK", "benchmark": benchmark, "function": b["function"], "verdict": "revert", "veto": "conflicts",
                "conflicts": {"proxy_10ms": {"base": proxies["base"].get("conflicts", 0), "cand": proxies["cand"]["conflicts"]}},
                "reason": (f"introduces transaction conflicts: {proxies['cand']['conflicts']} emulated TransactionConflict "
                           f"rejection(s) vs {proxies['base'].get('conflicts', 0)} in the baseline at {SCREEN_LATENCY}ms latency "
                           "(concurrent requests touching the same item; DynamoDB Local would hide this)"),
                "proxies": {"base": proxies["base"], "cand": proxies["cand"]}}
    pdir = _proxy_direction(proxies["base"], proxies["cand"])
    proxy_worse = any(v == "worse" for v in pdir.values())
    proxy_better = any(v == "better" for v in pdir.values())

    lat_list = latencies or [SCREEN_LATENCY]
    rie: dict[str, Any] = {}
    for L in lat_list:
        rie[f"{L}ms"] = _measure_pair(model, builds, b, fx, L)
    screen = rie.get(f"{SCREEN_LATENCY}ms") or rie[f"{lat_list[0]}ms"]
    warm_dir = {k: _direction(v["base"]["warm_round_medians"], v["cand"]["warm_round_medians"]) for k, v in rie.items()}
    need_multi = latencies is None and (proxy_worse or warm_dir[f"{SCREEN_LATENCY}ms"] == "worse")
    if need_multi:
        for L in MULTI_LATENCIES:
            rie[f"{L}ms"] = _measure_pair(model, builds, b, fx, L)
            warm_dir[f"{L}ms"] = _direction(rie[f"{L}ms"]["base"]["warm_round_medians"], rie[f"{L}ms"]["cand"]["warm_round_medians"])

    # Latency dimension (§3.2 steps 1-2). Realistic-latency rule first: all of
    # 5/10/15 ms clearly better and CPU within CPU_RATIO_LIMIT -> better,
    # whatever 0 ms shows (kept as a reference).
    realistic = [f"{L}ms" for L in REALISTIC_LATENCIES]
    cpu_b, cpu_c = proxies["base"]["warm_cpu_ms"]["median"], proxies["cand"]["warm_cpu_ms"]["median"]
    cpu_ratio = round(cpu_c / cpu_b, 2) if cpu_b else None
    cpu_ok = cpu_ratio is not None and cpu_ratio <= CPU_RATIO_LIMIT
    realistic_rule = all(warm_dir.get(k) == "better" for k in realistic) and cpu_ok
    dirs = list(warm_dir.values())
    if realistic_rule:
        latency_dim = "better"
    elif any(d == "better" for d in dirs) and not any(d == "worse" for d in dirs):
        latency_dim = "better"
    elif any(d == "better" for d in dirs) and any(d == "worse" for d in dirs):
        latency_dim = "depends_on_rtt"
    elif any(d == "worse" for d in dirs):
        latency_dim = "worse"
    else:
        latency_dim = "no_diff"
    # Init dimension: all init samples of every latency run (≥ ROUNDS each)
    init_b = [x for v in rie.values() for x in v["base"]["init_ms"]]
    init_c = [x for v in rie.values() for x in v["cand"]["init_ms"]]
    init_dim = _direction(init_b, init_c)
    # Cost dimension: request units are deterministic; duration cost uses the
    # screen latency's warm round medians (a range per version).
    cost_b = [cost(proxies["base"], d, mem, archs["base"])["total_usd"] for d in screen["base"]["warm_round_medians"]]
    cost_c = [cost(proxies["cand"], d, mem, archs["cand"])["total_usd"] for d in screen["cand"]["warm_round_medians"]]
    cost_dim = _direction(cost_b, cost_c)
    # RSS guard (§3.1-4)
    rss = max(v["cand"]["max_rss_mb"] for v in rie.values())
    rss_veto = rss > 0.9 * mem
    # Conflicts that only show in the RIE rounds (other latencies / warm runs)
    rie_conflicts = {k: {"base": v["base"]["conflicts"], "cand": v["cand"]["conflicts"]} for k, v in rie.items()}
    conflict_veto = [k for k, v in rie_conflicts.items() if v["cand"] > v["base"]]

    dims = {"latency": latency_dim, "init": init_dim, "cost": cost_dim}
    if conflict_veto:
        verdict, why = "revert", f"introduces transaction conflicts in the RIE rounds at {', '.join(conflict_veto)}: {rie_conflicts}"
    elif rss_veto:
        verdict, why = "revert", f"RSS guard: candidate max RSS {rss}MB > 90% of MemorySize {mem}MB"
    elif latency_dim == "depends_on_rtt":
        verdict, why = "pending_human", "latency verdict flips with the simulated round-trip time"
    elif any(d == "worse" for d in dims.values()) and any(d == "better" for d in dims.values()):
        verdict, why = "pending_human", "trade-off: some dimensions better, some worse"
    elif any(d == "better" for d in dims.values()) and not any(d in ("worse", "unknown") for d in dims.values()):
        verdict, why = "kept", "no dimension worse and at least one clearly better"
    elif latency_dim == "no_diff" and proxy_better and not proxy_worse and not any(d == "worse" for d in dims.values()):
        verdict, why = "kept_inconclusive", "RIE shows no significant difference, proxies improve and nothing got worse"
    else:
        verdict, why = "revert", "no dimension clearly better"

    def brief(x: dict) -> dict:
        return {k: (round(v, 2) if isinstance(v, float) else [round(i, 2) for i in v] if isinstance(v, list) and v and isinstance(v[0], float) else v)
                for k, v in x.items() if k != "errors"} | ({"errors": x["errors"][:2]} if x.get("errors") else {})

    result = {
        "status": "OK", "benchmark": benchmark, "function": b["function"], "data_volume": b["data_volume"],
        "baseline": f"{baseline} ({_git(ws, 'rev-parse', '--short', baseline)})", "changed_files": changed,
        "verdict": verdict, "reason": why, "veto": "conflicts" if conflict_veto else "rss" if rss_veto else None,
        "dimensions": dims, "warm_by_latency": warm_dir,
        "realistic_latency_rule": {"applied": realistic_rule, "latencies": realistic,
                                   "reference_0ms": warm_dir.get("0ms"), "warm_cpu_ratio": cpu_ratio,
                                   "cpu_ratio_limit": CPU_RATIO_LIMIT, "cost_latency": f"{SCREEN_LATENCY}ms" if f"{SCREEN_LATENCY}ms" in rie else f"{lat_list[0]}ms"},
        "proxies": {"base": proxies["base"], "cand": proxies["cand"], "direction": pdir},
        "calibrated_estimate": _calibrated_estimate(proxies),
        "rie": {k: {"base": brief(v["base"]), "cand": brief(v["cand"])} for k, v in rie.items()},
        "init_ms": {"base": init_b, "cand": init_c},
        "cost_usd_per_invocation": {"base": cost_b, "cand": cost_c},
        "rss_guard": {"candidate_max_rss_mb": rss, "memory_mb": mem, "veto": rss_veto},
        "architecture": {**archs, **({"note": "RIE rounds ran x86_64 for both versions; the architecture enters only through the "
                                              "duration price (lambda_gb_second vs lambda_gb_second_arm64)"} if archs["base"] != archs["cand"] else {})},
        "conflicts": {"proxy_10ms": {"base": proxies["base"].get("conflicts", 0), "cand": proxies["cand"].get("conflicts", 0)}, "rie": rie_conflicts},
        "method": {"rounds": ROUNDS, "warm_invokes_per_round": WARM_INVOKES, "cpus": screen["base"].get("cpus"),
                   "clearly_different": "ranges of per-round warm medians (or init samples, or cost) do not overlap",
                   "latency_model": "fixed per-call latency (PERF_OPTIMIZATION_DESIGN §2.1 limits apply: e.g. parallel calls are unbounded)"},
    }
    return result


CONFIG_ONLY_KEYS = ("Architectures",)


def _without_config(t: dict[str, Any]) -> dict[str, Any]:
    """The template with the tier-A configuration keys removed from
    Globals.Function and from every function's Properties."""
    t = json.loads(json.dumps(t))
    for k in CONFIG_ONLY_KEYS:
        ((t.get("Globals") or {}).get("Function") or {}).pop(k, None)
        for res in (t.get("Resources") or {}).values():
            if isinstance(res, dict) and res.get("Type") == "AWS::Serverless::Function":
                (res.get("Properties") or {}).pop(k, None)
    return t


def run_config_verdict(workspace: str, baseline: str = "HEAD") -> dict[str, Any]:
    """Tier-A verdict (PERF_OPTIMIZATION_DESIGN.md §3.4) for a change
    that only sets Architectures in output/template.yaml. No code changed and
    the local RIE runs x86_64 either way, so validation, equivalence and RIE
    rounds cannot see any difference and are not run. Checked instead: only
    the template changed, and only in Architectures; the template gate;
    sam build of the candidate; compiled modules in any function's build
    (pending_human: the local x86_64 run cannot tell whether they exist for
    arm64). Cost: the duration price per architecture (prices.json)."""
    ws = Path(workspace)
    changed = working_tree_changed(ws, baseline)
    if changed != ["output/template.yaml"]:
        return {"status": "NOT_CONFIG_ONLY", "verdict": "revert",
                "reason": f"tier A covers a template-only change; changed: {changed} -- use the tier-B checks and run_rie_verdict"}
    base_t = verify.gates.load_template(export_ref(ws, baseline) / "output" / "template.yaml")
    cand_t = verify.gates.load_template(ws / "output" / "template.yaml")
    if _without_config(base_t) != _without_config(cand_t):
        return {"status": "NOT_CONFIG_ONLY", "verdict": "revert",
                "reason": f"template differs outside {CONFIG_ONLY_KEYS}; judge it as a code/template change"}
    gate = verify.gates.run_gate("template", str(ws))
    if gate.get("status") != "PASS":
        return {"status": "OK", "verdict": "revert", "reason": "gate(template) failed", "gate": gate}
    try:
        build = sam_build(ws)
    except VerifyError as exc:
        return {"status": "OK", "verdict": "revert", "reason": f"sam build failed: {str(exc)[:500]}"}
    model = Model(ws)
    base_model = Model(export_ref(ws, baseline))
    p = _prices()
    archs, native = {}, {}
    for name, fn in model.functions.items():
        b, c = base_model.functions.get(name, {}).get("architecture", "x86_64"), fn.get("architecture", "x86_64")
        archs[name] = {"base": b, "cand": c}
        # A module tagged for the target architecture (sam build fetched the
        # right wheel, e.g. *-aarch64-linux-gnu.so) is fine; flag the ones
        # tagged for another architecture and the untagged ones (.node, abi3).
        tag = "aarch64" if c == "arm64" else "x86_64"
        found = [f for f in native_binaries(build, fn["logical"]) if tag not in f]
        if found:
            native[name] = found
    price = {a: p["lambda_gb_second_arm64"] if a == "arm64" else p["lambda_gb_second"] for a in ("x86_64", "arm64")}
    moved = {k: v for k, v in archs.items() if v["base"] != v["cand"]}
    ratio = {k: round(price[v["cand"]] / price[v["base"]], 3) for k, v in moved.items()}
    worse = [k for k, r in ratio.items() if r > 1]
    if native:
        verdict, why = "pending_human", f"compiled modules in {sorted(native)}; confirm they exist for the new architecture"
    elif worse:
        verdict, why = "revert", f"duration price goes up for {worse}"
    elif moved:
        verdict, why = "kept", "template-only architecture change; gate and build pass; duration price lower, latency not measurable locally"
    else:
        verdict, why = "revert", "no function changes architecture"
    result = {"status": "OK", "tier": "A", "verdict": verdict, "reason": why, "changed_files": changed,
              "baseline": f"{baseline} ({_git(ws, 'rev-parse', '--short', baseline)})",
              "architectures": moved, "duration_price_ratio": ratio, "native_binaries": native,
              "dimensions": {"latency": "not_measured_locally", "init": "not_measured_locally", "cost": "better" if moved and not worse else "no_diff"},
              "evidence_for_speed": "real-AWS A/B, PERF_OPTIMIZATION_DESIGN.md appendix A.4 (arm64 as fast or faster)"}
    out = ws / "perf" / "verdicts"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"config-{time.strftime('%Y%m%d-%H%M%S')}.json"
    path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    result["saved_to"] = str(path.relative_to(ws)).replace("\\", "/")
    return result


@_rie_locked
def run_rie_verdict(workspace: str, benchmark: str, baseline: str = "HEAD", latencies: list[int] | None = None,
                    guard_benchmarks: list[str] | None = None) -> dict[str, Any]:
    """Judge the working tree against <baseline> on one benchmark (§3.2).
    Only the latency / init / cost dimensions are judged here; the veto
    checks (gate(final), stage-B regression/equivalence/coverage) are
    separate tools and must pass first."""
    ws = Path(workspace)
    model = Model(ws)
    b = load_benchmarks(ws, [benchmark])[0]
    errs = check_benchmark(model, b)
    if errs:
        return {"status": "FAIL", "schema_errors": errs}
    changed = working_tree_changed(ws, baseline)
    if not changed:
        return {"status": "FAIL", "error": f"working tree output/ is identical to {baseline}; nothing to judge"}
    if not verify.docker_available():
        return {"status": "NO_DOCKER", "error": "RIE verdict needs Docker; judge with run_proxy_metrics only and mark the change low-confidence (§3.5)"}
    base_root = export_ref(ws, baseline)
    builds = {"base": sam_build(base_root), "cand": sam_build(ws)}
    fn = b["function"]
    archs = {"base": Model(base_root).functions.get(fn, {}).get("architecture", "x86_64"),
             "cand": model.functions[fn].get("architecture", "x86_64")}
    result = _judge(ws, model, builds, b, baseline, changed, latencies, archs)
    if archs["base"] != archs["cand"] and result.get("status") == "OK":
        native = native_binaries(builds["cand"], model.functions[fn]["logical"])
        result["native_binaries"] = native
        if native and result.get("verdict") in ("kept", "kept_inconclusive"):
            result["verdict"], result["reason"] = "pending_human", (
                f"architecture changed to {archs['cand']} but the build carries compiled modules {native[:5]}; "
                "confirm they were built for that architecture (the local x86_64 RIE cannot)")
    # Guard benchmarks (e.g. the hot-key case of a concurrency change): the
    # change must not make them worse in any dimension, nor add conflicts.
    if guard_benchmarks and result.get("verdict") != "revert":
        guards = {}
        for gid in guard_benchmarks:
            g = load_benchmarks(ws, [gid])[0]
            gerr = check_benchmark(model, g)
            if gerr:
                return {"status": "FAIL", "schema_errors": gerr}
            gr = _judge(ws, model, builds, g, baseline, changed, latencies, archs)
            worse = [k for k, v in (gr.get("dimensions") or {}).items() if v in ("worse", "depends_on_rtt")]
            bad = gr.get("status") == "INVALID" or bool(gr.get("veto")) or bool(worse)
            guards[gid] = {"ok": not bad, "verdict": gr.get("verdict"), "reason": gr.get("reason"), "dimensions": gr.get("dimensions"),
                           "warm_by_latency": gr.get("warm_by_latency"), "conflicts": gr.get("conflicts"), "data_volume": g.get("data_volume")}
        result["guards"] = guards
        failed = [k for k, v in guards.items() if not v["ok"]]
        if failed and result.get("verdict") != "revert":
            result["verdict"] = "revert"
            result["reason"] = f"guard benchmark(s) {', '.join(failed)} got worse or gained conflicts: " + "; ".join(str(guards[k]["reason"]) for k in failed)
    if result.get("status") != "OK":
        return result
    out = ws / "perf" / "verdicts"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{benchmark}-{time.strftime('%Y%m%d-%H%M%S')}.json"
    path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    result["saved_to"] = str(path.relative_to(ws)).replace("\\", "/")
    return result


# ---------------------------------------------------------------------------
# AWS verdict (PERF_OPTIMIZATION_DESIGN.md §2.2, O1M/O2; on by default)
# ---------------------------------------------------------------------------

def run_aws_verdict(workspace: str, benchmark: str, baseline: str = "HEAD", load_cmd: str | None = None,
                    repeats: int = 3, budget_usd: float = 0.0, proxy_better: bool = False,
                    seed_cmd: str | None = None, backend: Any = None) -> dict[str, Any]:
    """Judge one O candidate on real AWS. Primary dimensions: Lambda warm
    Duration and marginal cost (Lambda + requests + DynamoDB + SQS); guards:
    end-to-end p50/p95 and cold start. Correctness is the local checks' job;
    this tool only measures. AWS is the default; M2S_AWS=0 disables it.
    A load command is required."""
    ws = Path(workspace).resolve()
    from coord import aws as aws_mod

    if not aws_mod.enabled():
        return {"status": "DISABLED", "note": "M2S_AWS=0 disables the default AWS measurement"}
    if not load_cmd:
        return {"status": "ERROR", "error": "load_cmd is required (reads M2S_AWS_TARGET, writes {'samples': [ms]} to $M2S_AWS_OUT)"}
    model = Model(ws)
    b = load_benchmarks(ws, [benchmark])[0]
    errs = check_benchmark(model, b)
    if errs:
        return {"status": "FAIL", "schema_errors": errs}
    seed_cmd = seed_cmd or os.environ.get("M2S_AWS_SEED_CMD") or None
    try:
        res = aws_mod.measure_aws(ws, candidate="candidate", ref=None, baseline_ref=baseline, baseline_label="head",
                                  load_cmd=load_cmd, repeats=repeats, seed_cmd=seed_cmd,
                                  load_env={"M2S_AWS_WS": str(ws), "M2S_AWS_BENCHMARK": str(b.get("_file") or "")},
                                  backend=backend)
    except Exception as exc:  # noqa: BLE001 -- a measurement failure must come back as data
        return {"status": "ERROR", "error": f"{type(exc).__name__}: {exc}"}
    if res.get("status") not in ("PASS", "INCONCLUSIVE"):
        return {"status": res.get("status", "ERROR"), "error": res.get("note") or res.get("error"),
                "measured": res}
    fn = b["function"]
    if fn not in model.functions:
        return {"status": "FAIL", "error": f"benchmark function {fn!r} is not in output/template.yaml"}
    mem = int(model.functions[fn]["memory"])
    arch = model.functions[fn].get("architecture", "x86_64")
    p = _prices()
    gb = p["lambda_gb_second_arm64"] if arch == "arm64" else p["lambda_gb_second"]
    api = res.get("api_type", "rest")
    api_price = p["api_gateway_http_request"] if api == "http" else p["api_gateway_rest_request"]

    is_sqs = bool(b.get("sqs"))

    def side_cost(side: str) -> dict[str, float]:
        s = res["sides"][side]
        inv, req = s.get("invocations", 0), s.get("requests", 0)
        # Billed ms (REPORT, includes init) is the cost basis; older records only have Duration.
        lam = float(s.get("billed_ms") or s.get("duration_sum_ms", 0.0)) / 1000.0 * mem / 1024.0 * gb
        req_fee = inv * p["lambda_request"]
        ddb = s.get("rru", 0) * p["dynamodb_read_request_unit"] + s.get("wru", 0) * p["dynamodb_write_request_unit"]
        sqs = s.get("sqs_requests", 0) * p["sqs_request"]
        marginal = lam + req_fee + ddb + sqs
        gw = req * api_price
        # an SQS pass sends batches, so the loader's client samples are not the
        # unit cost; one consumer invocation (one batch of messages) is.
        divisor = inv if is_sqs else req
        return {"marginal_usd": round(marginal, 9), "total_usd": round(marginal + gw, 9),
                "marginal_per_request": round(marginal / divisor, 9) if divisor else 0.0,
                "total_per_request": round((marginal + gw) / divisor, 9) if divisor else 0.0,
                "per": "invocation" if is_sqs else "request",
                "api_gateway_usd": round(gw, 9), "requests": req, "invocations": inv,
                "lambda_usd": round(lam, 9), "dynamodb_usd": round(ddb, 9), "sqs_usd": round(sqs, 9)}

    cost = {"b0": side_cost("b0"), "candidate": side_cost("candidate")}
    threshold = float(res.get("noise_threshold", 0.10))

    def direction(base: float, cand: float) -> str:
        if not base:
            return "unknown"
        delta = (cand - base) / base
        return "better" if delta < -threshold else "worse" if delta > threshold else "no_diff"

    base_s, cand_s = res["sides"]["b0"], res["sides"]["candidate"]
    dims = {"latency": direction(base_s["duration_mean_ms"], cand_s["duration_mean_ms"]),
            "cost": direction(cost["b0"]["marginal_per_request"], cost["candidate"]["marginal_per_request"])}
    v = res.get("verdict") or {}
    # For an SQS benchmark the client samples are enqueue latency, not an
    # end-to-end request: p50/p95 do not apply (the consumer's Lambda Duration
    # is the primary metric).
    p50_guard = "not_applicable" if is_sqs else direction((v.get("base") or {}).get("p50", 0), (v.get("candidate") or {}).get("p50", 0))
    p95_guard = "not_applicable" if is_sqs else direction((v.get("base") or {}).get("p95", 0), (v.get("candidate") or {}).get("p95", 0))

    def cold_start_guard(base: dict[str, Any], cand: dict[str, Any]) -> str:
        """Compare cold-start rates with a two-proportion z-test (a couple of
        extra cold starts in ~150 invocations is scaling noise, not a
        regression); when both sides have cold starts, Init Duration must also
        not be clearly worse."""
        b_inv, c_inv = int(base.get("invocations", 0)), int(cand.get("invocations", 0))
        b_cold, c_cold = int(base.get("init_count", 0)), int(cand.get("init_count", 0))
        if not b_inv or not c_inv or (not b_cold and not c_cold):
            return "unknown"
        pooled = (b_cold + c_cold) / (b_inv + c_inv)
        se = math.sqrt(pooled * (1 - pooled) * (1 / b_inv + 1 / c_inv))
        if se == 0:
            return "no_diff"
        z = ((c_cold / c_inv) - (b_cold / b_inv)) / se
        if z > 1.96:
            return "worse"
        if z < -1.96:
            return "better"
        if b_cold and c_cold:
            d = direction(base.get("init_mean_ms", 0), cand.get("init_mean_ms", 0))
            return d if d in ("worse", "better") else "no_diff"
        return "no_diff"

    cold_guard = cold_start_guard(base_s, cand_s)

    def warm_samples(side: str) -> int:
        s = res["sides"][side]
        return int(s["warm_count"]) if "warm_count" in s else int(s.get("invocations", 0))

    small_aws = any(warm_samples(side) < 10 for side in ("b0", "candidate"))
    guards = {"p50": p50_guard, "p95": p95_guard, "cold_start": cold_guard,
              "small_samples": bool(v.get("small_samples")) or small_aws}
    uncovered = res.get("uncovered") or []
    if uncovered:
        verdict, reason = "INCONCLUSIVE", f"the load never reached {uncovered}; fix the load before judging"
    elif guards["small_samples"]:
        verdict, reason = "INCONCLUSIVE", "fewer than 10 warm samples on one side"
    elif any(d == "worse" for d in dims.values()) and any(d == "better" for d in dims.values()):
        verdict, reason = "pending_human", "trade-off: one primary dimension better, one worse"
    elif any(d == "worse" for d in dims.values()):
        verdict, reason = "revert", "a primary dimension got clearly worse"
    elif any(d == "worse" for d in (guards["p50"], guards["p95"], guards["cold_start"])):
        verdict, reason = "pending_human", "a guard (p50/p95/cold start) got clearly worse"
    elif any(d == "better" for d in dims.values()):
        verdict, reason = "kept", "no primary dimension worse and at least one clearly better"
    elif proxy_better:
        verdict, reason = "kept_inconclusive", "AWS shows no significant difference; proxies improve and nothing got worse"
    else:
        verdict, reason = "revert", "no primary dimension clearly better"
    if budget_usd and (cost["b0"]["total_usd"] + cost["candidate"]["total_usd"]) > budget_usd:
        verdict, reason = "pending_human", (f"measurement cost estimate {cost['b0']['total_usd'] + cost['candidate']['total_usd']:.4f} "
                                            f"over the budget {budget_usd}; the decision needs a person")
    result = {"status": "OK", "benchmark": benchmark, "function": fn, "baseline": baseline,
              "verdict": verdict, "reason": reason, "dimensions": dims, "guards": guards,
              "cost": cost, "sides": res["sides"], "uncovered": uncovered, "noise_threshold": threshold,
              "api_type": api, "budget_usd": budget_usd, "measured": res,
              "method": {"repeats": repeats, "primary": "Lambda warm Duration + marginal cost per request",
                         "guards": "end-to-end p50/p95 + cold start", "cost": "marginal judged; total incl. API Gateway reported"}}
    out = ws / "perf" / "verdicts"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{benchmark}-aws-{time.strftime('%Y%m%d-%H%M%S')}.json"
    path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    result["saved_to"] = str(path.relative_to(ws)).replace("\\", "/")
    return result


# ---------------------------------------------------------------------------
# Ledger (§8.3)
# ---------------------------------------------------------------------------

OUTCOMES = ("kept", "kept_inconclusive", "reverted", "pending_human", "blocked_by_test", "blocked_by_coverage",
            # S5 -> S1 spec relaxation (CONTRACT_TESTING_PLAN.md section 7): waiting
            # for the architect's ruling / the architect rejected it.
            "pending_spec", "spec_rejected")
SPEC_COUNTS = ("requested", "accepted", "rejected", "pending", "kept")
# Per rule, besides the final outcomes: changes kept only after the one fix
# attempt (§3.6), how many fix attempts were made, and how many changes went
# through the verification line's test review before their final verdict.
COUNTERS = ("kept_after_fix", "fix_attempted", "reviewed")


def _ledger_app_name(ws: Path) -> str:
    """The benchmark app a workspace migrated: the monolith's directory name
    (analysis_report spec_meta.monolith_root), so every app has one stable
    key. The blueprint's application_name is the architect's free text
    ("presso (coffee order management)" for coffee, "Bookstore" vs
    "bookstore" across runs) and only a fallback."""
    ar = ws / "storage" / "analysis_report.json"
    if ar.exists():
        root = (json.loads(ar.read_text(encoding="utf-8")).get("spec_meta") or {}).get("monolith_root")
        if root:
            return Path(root).name.strip().lower()
    bp = ws / "storage" / "blueprint.json"
    meta = (json.loads(bp.read_text(encoding="utf-8")).get("metadata") or {}) if bp.exists() else {}
    return str(meta.get("application_name") or ws.name).strip().lower()


def update_ledger(workspace: str) -> dict[str, Any]:
    """Fold WS/output/perf_report.json into perf-rules/ledger.json (per app,
    per rule). Re-running for the same workspace replaces its earlier entry."""
    ws = Path(workspace)
    rp = ws / "output" / "perf_report.json"
    if not rp.exists():
        raise VerifyError("output/perf_report.json not found")
    report = json.loads(rp.read_text(encoding="utf-8"))
    app = _ledger_app_name(ws)
    scan = report.get("rule_scan") or {}
    if not isinstance(scan, dict) or not scan:
        raise VerifyError("perf_report.json needs rule_scan: {R1: {hits, locations}, R2: ..., R3: ...} (hits may be 0)")
    blank = {**{o: 0 for o in OUTCOMES}, **{k: 0 for k in COUNTERS}}
    per_rule: dict[str, dict[str, int]] = {r: {"hits": int((v or {}).get("hits", 0)), **blank} for r, v in scan.items()}
    candidates = []
    spec = {k: 0 for k in SPEC_COUNTS}
    for c in report.get("changes") or []:
        src = str(c.get("source", ""))
        verdict = c.get("verdict")
        verdict = "reverted" if verdict == "revert" else verdict
        sr = c.get("spec_relaxation")
        if isinstance(sr, dict):
            # one per request: the re-judged change carries the same block
            spec["requested"] += 1
            ruling = sr.get("ruling", "pending")
            spec[{"accepted": "accepted", "rejected": "rejected"}.get(ruling, "pending")] += 1
            spec["kept"] += 1 if ruling == "accepted" and verdict in ("kept", "kept_inconclusive") else 0
        if src.startswith("rule:"):
            r = src.split(":", 1)[1]
            per_rule.setdefault(r, {"hits": 0, **blank})
            if verdict in OUTCOMES:
                per_rule[r][verdict] += 1
            fixes = int(c.get("fix_attempts") or 0)
            per_rule[r]["fix_attempted"] += 1 if fixes else 0
            per_rule[r]["kept_after_fix"] += 1 if fixes and verdict in ("kept", "kept_inconclusive") else 0
            per_rule[r]["reviewed"] += 1 if c.get("review") else 0
        elif src == "analysis" and verdict in ("kept", "kept_inconclusive"):
            candidates.append({"app": app, "change": c.get("id"), "request": c.get("request"), "functions": c.get("functions"),
                               "note": "data-driven finding judged effective; promotion to a rule needs human review"})
    ledger = json.loads(LEDGER.read_text(encoding="utf-8")) if LEDGER.exists() else {"version": 1, "runs": [], "candidate_rules": []}
    key = str(ws.resolve())
    ledger["runs"] = [r for r in ledger["runs"] if r.get("workspace") != key]
    ledger["runs"].append({"app": app, "workspace": key, "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                           "confidence": report.get("confidence"), "rules": per_rule, "spec_relaxations": spec})
    ledger["candidate_rules"] = [c for c in ledger.get("candidate_rules", []) if c.get("app") != app] + candidates
    totals: dict[str, dict[str, int]] = {}
    for run in ledger["runs"]:
        for r, v in run["rules"].items():
            t = totals.setdefault(r, {"hits": 0, **{o: 0 for o in OUTCOMES}, **{k: 0 for k in COUNTERS}, "apps": 0})
            for k in ("hits", *OUTCOMES, *COUNTERS):
                t[k] += v.get(k, 0)
            t["apps"] += 1
    ledger["totals"] = totals
    ledger["totals_spec_relaxations"] = {k: sum((run.get("spec_relaxations") or {}).get(k, 0) for run in ledger["runs"]) for k in SPEC_COUNTS}
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    LEDGER.write_text(json.dumps(ledger, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"status": "OK", "app": app, "rules": per_rule, "spec_relaxations": spec, "candidates": len(candidates), "ledger": str(LEDGER)}
