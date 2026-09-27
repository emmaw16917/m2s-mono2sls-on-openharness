"""Optimization-line tools (PERF_OPTIMIZATION_DESIGN.md §2-§4, §8-§9):
run_proxy_metrics, run_rie_verdict, update_ledger.

Benchmarks are written by m2s-perf-optimizer under WS/perf/benchmarks/
<function>/<id>.json, in the verification case format (request or sqs +
fixtures) plus:
    "data_volume": "<the data-volume assumption, in words>"   (required)
    "generate": {"<Table>": {"count": N, "template": {... "{i}" ...}}}
The same benchmark is used before and after a change. Measurements use
verify-tools/perf-shim.js: the SDK's HTTP send is replaced by a fixed
latency plus a response computed from the benchmark's table data (§2.1).

Versions: the baseline is a git ref of the workspace repo (default HEAD =
v0 plus the changes kept so far), exported with `git archive`; the
candidate is the working tree. Both go through `sam build`.
"""

from __future__ import annotations

import json
import re
import shutil
import statistics
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Any

import verify
from verify import VerifyError, Model, _run, _env, _git, export_ref, sam_build, TMP_ROOT, VERIFY_TOOLS

REPO_PERF = Path(__file__).resolve().parent / "perf-rules"
LEDGER = REPO_PERF / "ledger.json"
PRICES = REPO_PERF / "prices.json"
RIE_IMAGE = "public.ecr.aws/lambda/nodejs:22-x86_64"
ROUNDS = 5          # alternating rounds; gives >= 5 init samples and 5 warm medians (§3.3)
WARM_INVOKES = 30
SCREEN_LATENCY = 10
MULTI_LATENCIES = [0, 5, 15]


# ---------------------------------------------------------------------------
# Benchmarks
# ---------------------------------------------------------------------------

def load_benchmarks(ws: Path, ids: list[str] | None = None, functions: list[str] | None = None) -> list[dict[str, Any]]:
    out = []
    for p in sorted((ws / "perf" / "benchmarks").glob("*/*.json")):
        b = json.loads(p.read_text(encoding="utf-8"))
        b.setdefault("id", p.stem)
        b.setdefault("function", p.parent.name)
        b["_file"] = str(p.relative_to(ws)).replace("\\", "/")
        if (ids and b["id"] not in ids) or (functions and b["function"] not in functions):
            continue
        out.append(b)
    if not out:
        raise VerifyError("no matching benchmarks under perf/benchmarks/<function>/*.json")
    return out


def _fill(tpl: Any, i: int) -> Any:
    if isinstance(tpl, str):
        full = re.fullmatch(r"\{i(?:\s*%\s*(\d+))?\}", tpl)
        if full:  # a bare "{i}" / "{i % 7}" becomes a number
            return i % int(full.group(1)) if full.group(1) else i
        return re.sub(r"\{i(?:\s*%\s*(\d+))?\}", lambda m: str(i % int(m.group(1)) if m.group(1) else i), tpl)
    if isinstance(tpl, dict):
        return {k: _fill(v, i) for k, v in tpl.items()}
    if isinstance(tpl, list):
        return [_fill(v, i) for v in tpl]
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
        tables[model.table_id(alias)]["items"].extend(_fill(g["template"], i) for i in range(g["count"]))
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
           "fixtures_path": str(fx), "depth_latency_ms": SCREEN_LATENCY, "cpu_warmup": 30, "cpu_reps": 300}
    jp = work / f"{b['id']}.harness.json"
    jp.write_text(json.dumps(job), encoding="utf-8")
    node = shutil.which("node", path=_env()["PATH"]) or "node"
    p = _run([node, str(VERIFY_TOOLS / "perf-harness.js"), str(jp)], capture_output=True, text=True, encoding="utf-8", errors="replace", env=_env(), timeout=900)
    line = next((l for l in p.stdout.splitlines() if l.startswith("@@M2S@@")), None)
    if not line:
        raise VerifyError(f"perf-harness produced no result for {b['id']}: {p.stderr[-2000:]}")
    return json.loads(line[7:])


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
        m["cost_usd"] = cost(m, None, model.functions[b["function"]]["memory"])
        m["data_volume"] = b["data_volume"]
        res[b["id"]] = m
    return {"status": "OK", "version": ref or "working tree", "benchmarks": res,
            "note": "calls/depth at 10ms fixed latency; warm_cpu = wall time per invocation at 0 latency (median of 300, local Node); "
                    "capacity units estimated from request/response sizes; no 1MB pagination or FilterExpression in the shim"}


# ---------------------------------------------------------------------------
# Cost (§4)
# ---------------------------------------------------------------------------

def _prices() -> dict[str, Any]:
    return json.loads(PRICES.read_text(encoding="utf-8"))


def cost(m: dict[str, Any], duration_ms: float | None, memory_mb: int) -> dict[str, Any]:
    p = _prices()
    units = (m.get("rru", 0) * p["dynamodb_read_request_unit"] + m.get("wru", 0) * p["dynamodb_write_request_unit"]
             + m.get("sqs_requests", 0) * p["sqs_request"])
    out = {"units_usd": units, "lambda_request_usd": p["lambda_request"]}
    if duration_ms is not None:
        out["duration_usd"] = duration_ms / 1000.0 * memory_mb / 1024.0 * p["lambda_gb_second"]
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


def rie_round(model: Model, build_dir: Path, b: dict[str, Any], fixtures: Path, latency: int, label: str, port: int) -> dict[str, Any]:
    """One fresh container: 1 cold + WARM_INVOKES warm invocations; returns
    init (rapid INIT REPORT), warm REPORT durations and the shim's max RSS."""
    fn = model.functions[b["function"]]
    name = f"m2sp-{label}"
    _docker("rm", "-f", name, check=False)
    env = {**fn["env"], "AWS_REGION": "us-east-1", "AWS_ACCESS_KEY_ID": "AKIDPERF", "AWS_SECRET_ACCESS_KEY": "perf",
           "AWS_LAMBDA_FUNCTION_MEMORY_SIZE": str(fn["memory"]), "NODE_OPTIONS": "--require /var/task/.m2s-perf-shim.js",
           "SHIM_FIXTURES": "/var/task/.m2s-fixtures.json", "SHIM_LATENCY_MS": str(latency), "SHIM_LOG": "1", "SHIM_FN_DIR": "/var/task"}
    cpus = round(int(fn["memory"]) / 1769, 3)
    env_args = [a for k, v in env.items() for a in ("-e", f"{k}={v}")]
    _docker("create", "--name", name, "--cpus", str(cpus), "-p", f"127.0.0.1:{port}:8080", *env_args, RIE_IMAGE, fn["handler"])
    try:
        _docker("cp", str(build_dir / fn["logical"]) + "/.", f"{name}:/var/task")
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


def _judge(ws: Path, model: Model, builds: dict[str, Path], b: dict[str, Any], baseline: str, changed: list[str],
           latencies: list[int] | None) -> dict[str, Any]:
    """One benchmark, base vs cand builds: proxies, conflict veto, RIE rounds
    and the §3.2 decision. `veto` names a hard reason (conflicts / rss)."""
    benchmark = b["id"]
    work = TMP_ROOT / re.sub(r"[^A-Za-z0-9_-]", "_", ws.name) / "perf" / f"{time.strftime('%Y%m%d-%H%M%S')}-verdict-{benchmark}"
    work.mkdir(parents=True, exist_ok=True)
    fx = _fixture_file(model, b, work / "fixtures.json")
    mem = int(model.functions[b["function"]]["memory"])

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
    screen = rie[f"{lat_list[0]}ms"]
    warm_dir = {k: _direction(v["base"]["warm_round_medians"], v["cand"]["warm_round_medians"]) for k, v in rie.items()}
    need_multi = latencies is None and (proxy_worse or warm_dir[f"{SCREEN_LATENCY}ms"] == "worse")
    if need_multi:
        for L in MULTI_LATENCIES:
            rie[f"{L}ms"] = _measure_pair(model, builds, b, fx, L)
            warm_dir[f"{L}ms"] = _direction(rie[f"{L}ms"]["base"]["warm_round_medians"], rie[f"{L}ms"]["cand"]["warm_round_medians"])

    # Latency dimension (§3.2 steps 1-2)
    dirs = list(warm_dir.values())
    if any(d == "better" for d in dirs) and not any(d == "worse" for d in dirs):
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
    cost_b = [cost(proxies["base"], d, mem)["total_usd"] for d in screen["base"]["warm_round_medians"]]
    cost_c = [cost(proxies["cand"], d, mem)["total_usd"] for d in screen["cand"]["warm_round_medians"]]
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
        "proxies": {"base": proxies["base"], "cand": proxies["cand"], "direction": pdir},
        "rie": {k: {"base": brief(v["base"]), "cand": brief(v["cand"])} for k, v in rie.items()},
        "init_ms": {"base": init_b, "cand": init_c},
        "cost_usd_per_invocation": {"base": cost_b, "cand": cost_c},
        "rss_guard": {"candidate_max_rss_mb": rss, "memory_mb": mem, "veto": rss_veto},
        "conflicts": {"proxy_10ms": {"base": proxies["base"].get("conflicts", 0), "cand": proxies["cand"].get("conflicts", 0)}, "rie": rie_conflicts},
        "method": {"rounds": ROUNDS, "warm_invokes_per_round": WARM_INVOKES, "cpus": screen["base"].get("cpus"),
                   "clearly_different": "ranges of per-round warm medians (or init samples, or cost) do not overlap",
                   "latency_model": "fixed per-call latency (PERF_OPTIMIZATION_DESIGN §2.1 limits apply: e.g. parallel calls are unbounded)"},
    }
    return result


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
    builds = {"base": sam_build(export_ref(ws, baseline)), "cand": sam_build(ws)}
    result = _judge(ws, model, builds, b, baseline, changed, latencies)
    # Guard benchmarks (e.g. the hot-key case of a concurrency change): the
    # change must not make them worse in any dimension, nor add conflicts.
    if guard_benchmarks and result.get("verdict") != "revert":
        guards = {}
        for gid in guard_benchmarks:
            g = load_benchmarks(ws, [gid])[0]
            gerr = check_benchmark(model, g)
            if gerr:
                return {"status": "FAIL", "schema_errors": gerr}
            gr = _judge(ws, model, builds, g, baseline, changed, latencies)
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
# Ledger (§8.3)
# ---------------------------------------------------------------------------

OUTCOMES = ("kept", "kept_inconclusive", "reverted", "pending_human")


def update_ledger(workspace: str) -> dict[str, Any]:
    """Fold WS/output/perf_report.json into perf-rules/ledger.json (per app,
    per rule). Re-running for the same workspace replaces its earlier entry."""
    ws = Path(workspace)
    rp = ws / "output" / "perf_report.json"
    if not rp.exists():
        raise VerifyError("output/perf_report.json not found")
    report = json.loads(rp.read_text(encoding="utf-8"))
    bp = json.loads((ws / "storage" / "blueprint.json").read_text(encoding="utf-8"))
    # Lower-cased: architects capitalize application_name inconsistently
    # ("bookstore" vs "Bookstore"), which split one app into two in the ledger.
    app = str((bp.get("metadata") or {}).get("application_name") or ws.name).strip().lower()
    scan = report.get("rule_scan") or {}
    if not isinstance(scan, dict) or not scan:
        raise VerifyError("perf_report.json needs rule_scan: {R1: {hits, locations}, R2: ..., R3: ...} (hits may be 0)")
    per_rule: dict[str, dict[str, int]] = {r: {"hits": int((v or {}).get("hits", 0)), **{o: 0 for o in OUTCOMES}} for r, v in scan.items()}
    candidates = []
    for c in report.get("changes") or []:
        src = str(c.get("source", ""))
        verdict = c.get("verdict")
        verdict = "reverted" if verdict == "revert" else verdict
        if src.startswith("rule:"):
            r = src.split(":", 1)[1]
            per_rule.setdefault(r, {"hits": 0, **{o: 0 for o in OUTCOMES}})
            if verdict in OUTCOMES:
                per_rule[r][verdict] += 1
        elif src == "analysis" and verdict in ("kept", "kept_inconclusive"):
            candidates.append({"app": app, "change": c.get("id"), "request": c.get("request"), "functions": c.get("functions"),
                               "note": "data-driven finding judged effective; promotion to a rule needs human review"})
    ledger = json.loads(LEDGER.read_text(encoding="utf-8")) if LEDGER.exists() else {"version": 1, "runs": [], "candidate_rules": []}
    key = str(ws.resolve())
    ledger["runs"] = [r for r in ledger["runs"] if r.get("workspace") != key]
    ledger["runs"].append({"app": app, "workspace": key, "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                           "confidence": report.get("confidence"), "rules": per_rule})
    ledger["candidate_rules"] = [c for c in ledger.get("candidate_rules", []) if c.get("app") != app] + candidates
    totals: dict[str, dict[str, int]] = {}
    for run in ledger["runs"]:
        for r, v in run["rules"].items():
            t = totals.setdefault(r, {"hits": 0, **{o: 0 for o in OUTCOMES}, "apps": 0})
            for k in ("hits", *OUTCOMES):
                t[k] += v.get(k, 0)
            t["apps"] += 1
    ledger["totals"] = totals
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    LEDGER.write_text(json.dumps(ledger, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"status": "OK", "app": app, "rules": per_rule, "candidates": len(candidates), "ledger": str(LEDGER)}
