"""The gap inventory (PIPELINE_V2_PLAN.md §4.11, N4.2-N4.3): behaviour a long-running
monolith gets for free and a serverless target must handle explicitly, listed per
application so that nothing is left to chance.

    G1 gap_scan            mechanical signals of the fact base -> storage/gap_inventory.json
    G2 (annotator)         adds what the signals cannot see (source "G2"); a gap of no known
                           category is category "new" with candidate_name
    A1 (architect)         decides every gap: status "decided" and a decision; also in DECISIONS.md
    A2 gate(blueprint)     no gap left undecided (check AG)
    D1 (verifier)          writes delta tests for the gaps whose decision says test: "delta"

    {"version": 1, "gaps": [
      {"id": "GAP-1", "key": "fire_and_forget:bookstore/routes/orders.js:POST__api_orders",
       "category": "fire_and_forget", "summary": "...", "evidence": [{"file", "line", "symbol"}],
       "endpoints": ["POST /api/orders"], "rulebook_rule": "...", "default": "...",
       "source": "G1", "status": "open",
       "decision": {"choice": "what the target does", "why": "...", "test": "delta" | "judge" | "none",
                    "judge_cases": ["ids of the judge cases that verify it, for test: judge"]}}]}

Rerunning gap_scan keeps ids and decisions: an entry is identified by its key. A signal
that is gone is marked no_longer_detected, never deleted; G2's entries are left alone.
gap_scan reads the signals from the fact base (report["runtime_signals"]) or, for a fact
base made before they existed, from the monolith's code. It never changes the fact base:
an annotated report must not be analyzed again.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import runtime_signals

INVENTORY = ("storage", "gap_inventory.json")
TESTS = ("delta", "judge", "none")
ENTRY_KEYS = {"id", "key", "category", "candidate_name", "summary", "evidence", "endpoints", "rulebook_rule", "default",
              "source", "status", "decision", "no_longer_detected", "note"}

# The rulebook's gap categories: what the rules already say about each, cited by name.
CATEGORIES: dict[str, dict[str, str | None]] = {
    "multi_write": {
        "title": "一个请求里有多次写操作，没有事务",
        "rule": None, "default": None},
    "process_state": {
        "title": "进程内状态：模块级变量、内存缓存、session",
        "rule": "m2s-basic-serverless-kb: Store user data in global state between invocations -> Keep state in DynamoDB / S3",
        "default": "状态放进 DynamoDB 或 S3，不留在函数实例里"},
    "fire_and_forget": {
        "title": "即发即忘的副作用：不等待结果的调用",
        "rule": "m2s-async-patterns-kb: REQUIRED correctness rule: consumers must be idempotent under redelivery",
        "default": "改成消息（SQS），消费者幂等"},
    "order_and_consistency": {
        "title": "顺序和一致性：依赖返回顺序的排序、刚写入就读取",
        "rule": None, "default": None},
    "time": {
        "title": "时间相关：当前时间、时区、定时任务",
        "rule": None, "default": None},
    "global_middleware": {
        "title": "全局中间件：CORS、统一错误处理、全局响应头",
        "rule": "gate GM1-GM3: every function's response carries the headers; GatewayResponses for what API Gateway answers",
        "default": "每个函数的响应带上这些头，网关自己的响应用 GatewayResponses"},
    "auth_semantics": {
        "title": "鉴权语义：应用自己签发和校验令牌",
        "rule": "m2s-basic-serverless-kb: Cognito User Pool (if auth exists in monolith)",
        "default": "Cognito + API Gateway 授权器，认证端点删除"},
    "files_and_connections": {
        "title": "文件系统和长连接：写本地文件、上传、WebSocket、流式响应",
        "rule": None, "default": None},
    "monolith_defect": {
        "title": "单体自身的缺陷：例如 Scan 不翻页（超过 1MB 时结果被静默截断）",
        "rule": "PIPELINE_REDESIGN_v2.md Q10: 默认照搬单体", "default": "默认照搬单体，要偏离须人确认"},
}
_SIGNAL_CATEGORY = {"now": "time", "timer": "time", "sort": "order_and_consistency"}
WRITE_RE = re.compile(r"put|update|delete|write", re.I)


class GapError(ValueError):
    pass


def inventory_path(ws: Path | str) -> Path:
    return Path(ws).joinpath(*INVENTORY)


def load(ws: Path | str) -> dict[str, Any] | None:
    p = inventory_path(ws)
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {"_load_error": f"invalid JSON: {exc}"}
    return data if isinstance(data, dict) else {"_load_error": "must be a JSON object {version, gaps}"}


def _report(ws: Path) -> dict[str, Any]:
    p = ws / "storage" / "analysis_report.json"
    if not p.is_file():
        raise GapError("storage/analysis_report.json not found: the gap scan reads the fact base (F1, F2 first)")
    return json.loads(p.read_text(encoding="utf-8"))


def _name(ep: dict[str, Any]) -> str:
    return f"{ep.get('method')} {ep.get('path')}"


def _sources_of(ep: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for part in ("responses", "business_rules", "data_access"):
        for x in ep.get(part) or []:
            if isinstance(x, dict):
                out += [s for s in x.get("source") or [] if isinstance(s, dict)]
    out += [s for s in (ep.get("auth") or {}).get("source") or [] if isinstance(s, dict)]
    return out


def _endpoints_at(report: dict[str, Any], file: str, line: int) -> list[str]:
    """Endpoints a line of code belongs to: the handler that holds it, the facts that cite it,
    else the endpoints whose facts cite that file."""
    points = [e for e in report.get("entry_points") or [] if isinstance(e, dict)]
    exact = []
    for ep in points:
        loc = ep.get("handler_location") or {}
        if loc.get("file") == file and isinstance(loc.get("start_line"), int) and isinstance(loc.get("end_line"), int) \
                and loc["start_line"] <= line <= loc["end_line"]:
            exact.append(_name(ep))
        elif any(s.get("file") == file and s.get("line") == line for s in _sources_of(ep)):
            exact.append(_name(ep))
    if exact:
        return sorted(set(exact))
    return sorted({_name(ep) for ep in points if any(s.get("file") == file for s in _sources_of(ep))})


def _entry(key: str, category: str, summary: str, evidence: list[dict[str, Any]], endpoints: list[str]) -> dict[str, Any]:
    c = CATEGORIES[category]
    return {"key": key, "category": category, "summary": summary, "evidence": evidence, "endpoints": sorted(set(endpoints)),
            "rulebook_rule": c["rule"], "default": c["default"], "source": "G1", "status": "open"}


def _from_multi_write(report: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for ep in report.get("entry_points") or []:
        writes = [d for d in ep.get("data_access") or [] if isinstance(d, dict) and WRITE_RE.search(str(d.get("operation") or ""))]
        transact = any("transact" in str(d.get("operation") or "").lower() for d in writes)
        repeated = any(d.get("repeated") for d in writes)
        if transact or not (len(writes) >= 2 or repeated):
            continue
        ops = ", ".join(f"{d.get('operation')} {(d.get('table') or {}).get('default_name')}" + (" (循环)" if d.get("repeated") else "")
                        for d in writes)
        evidence = [s for d in writes for s in (d.get("source") or [])[:1]]
        out.append(_entry(f"multi_write:{_name(ep)}", "multi_write", f"{_name(ep)} 一次请求写 {len(writes)} 次，没有事务：{ops}",
                          evidence, [_name(ep)]))
    return out


def _from_call_graph(report: dict[str, Any], root_is_python: bool) -> list[dict[str, Any]]:
    """JS: a call neither awaited nor used. Python's call graph says is_awaited False for
    every sync call, so it says nothing there: Python uses the background signal."""
    if root_is_python:
        return []
    handlers = {d.get("handler_function"): d.get("entry_point") or {} for d in report.get("entry_point_dependencies") or []
                if isinstance(d, dict)}
    out = []
    for c in report.get("call_graph") or []:
        if not isinstance(c, dict) or c.get("is_awaited") is not False or c.get("return_value_used") is not False:
            continue
        caller, file, line = c.get("caller_function"), c.get("caller_file"), c.get("caller_line")
        ep = handlers.get(caller)
        endpoints = [f"{ep.get('method')} {ep.get('path')}"] if ep else _endpoints_at(report, str(file), int(line or 0))
        evidence = [{"file": file, "line": line, "symbol": str(c.get("callee_symbol"))}]
        out.append(_entry(f"fire_and_forget:{file}:{caller}:{c.get('callee_symbol')}", "fire_and_forget",
                          f"{caller} 调用 {c.get('callee_symbol')}（{file}:{line}），既不等待也不用返回值", evidence, endpoints))
    return out


def _from_signals(report: dict[str, Any], signals: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = {}

    def add(category: str, kind: str, item: dict[str, Any]) -> None:
        src = item["source"][0]
        grouped.setdefault((category, kind, src["file"]), []).append(src)

    for item in signals.get("scans") or []:
        if not item.get("paginates"):
            add("monolith_defect", "scan_no_pagination", item)
    for item in signals.get("process_state") or []:
        add("process_state", item["kind"], item)
    for item in signals.get("time_and_order") or []:
        add(_SIGNAL_CATEGORY[item["kind"]], item["kind"], item)
    for item in signals.get("files_and_connections") or []:
        add("files_and_connections", item["kind"], item)
    for item in signals.get("background") or []:
        add("fire_and_forget", item["kind"], item)
    out = []
    labels = {"scan_no_pagination": "Scan 没有处理 LastEvaluatedKey（不翻页）", "now": "用到当前时间", "timer": "定时器或定时任务",
              "sort": "排序", "module_variable": "模块级可变变量", "module_collection": "模块级集合（进程内的数据）",
              "global": "全局变量", "global_statement": "函数里改全局变量", "cache": "内存缓存", "session": "session",
              "fs_write": "写本地文件", "upload": "文件上传", "websocket": "WebSocket", "streaming": "流式响应",
              "background": "启动后不等待的后台工作"}
    for (category, kind, file), sources in sorted(grouped.items()):
        lines = ", ".join(str(s["line"]) for s in sources)
        endpoints = [e for s in sources for e in _endpoints_at(report, file, s["line"])]
        out.append(_entry(f"{category}:{kind}:{file}", category, f"{labels.get(kind, kind)}：{file} 第 {lines} 行", sources, endpoints))
    return out


def _from_global_middleware(report: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for i, g in enumerate(report.get("global_middleware") or []):
        if not isinstance(g, dict):
            continue
        headers = ", ".join(sorted(g.get("headers") or {}))
        out.append(_entry(f"global_middleware:{g.get('kind')}:{i}", "global_middleware",
                          f"全局中间件 {g.get('framework') or g.get('kind')}（global_middleware[{i}]）" + (f"：{headers}" if headers else ""),
                          list(g.get("source") or [])[:3], [_name(e) for e in report.get("entry_points") or []]))
    return out


def _from_auth(report: dict[str, Any], signals: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    tokens = signals.get("self_issued_tokens") or []
    guarded = [e for e in report.get("entry_points") or []
               if isinstance(e, dict) and (e.get("auth") or {}).get("classification") in ("required", "optional")]
    if not tokens and not guarded:
        return []
    evidence = [i["source"][0] for i in tokens] or [s for e in guarded[:1] for s in ((e.get("auth") or {}).get("source") or [])[:1]]
    what = "应用自己签发和校验令牌" if tokens else "应用自己校验身份"
    return [_entry("auth_semantics:app", "auth_semantics", f"{what}，{len(guarded)} 个端点要求身份", evidence, [_name(e) for e in guarded])]


def gap_scan(ws: Path | str) -> dict[str, Any]:
    """G1: write storage/gap_inventory.json from the fact base's signals."""
    ws = Path(ws)
    report = _report(ws)
    root = Path((report.get("spec_meta") or {}).get("monolith_root") or "")
    signals = report.get("runtime_signals")
    if not isinstance(signals, dict):
        if not root.is_dir():
            raise GapError(f"the fact base has no runtime_signals and the monolith is not at {root}")
        signals = runtime_signals.scan(root)
    python = not (root / "package.json").is_file() and any(root.glob("*.py")) if root.is_dir() else False
    found = (_from_multi_write(report) + _from_call_graph(report, python) + _from_signals(report, signals)
             + _from_global_middleware(report) + _from_auth(report, signals))
    old = load(ws)
    if old is not None and "_load_error" in old:
        raise GapError(f"storage/gap_inventory.json: {old['_load_error']}")
    previous = list((old or {}).get("gaps") or [])
    by_key = {g.get("key"): g for g in previous if isinstance(g, dict)}
    used = [int(m.group(1)) for g in previous if isinstance(g, dict) and (m := re.fullmatch(r"GAP-(\d+)", str(g.get("id"))))]
    next_id = max(used, default=0) + 1
    out, added = [], 0
    keys = set()
    for entry in found:
        keys.add(entry["key"])
        prior = by_key.get(entry["key"])
        if prior:  # a person's or the architect's words stay; the evidence follows the code
            merged = {**prior, "evidence": entry["evidence"], "endpoints": entry["endpoints"], "summary": entry["summary"]}
            merged.pop("no_longer_detected", None)
            out.append(merged)
        else:
            out.append({"id": f"GAP-{next_id}", **entry})
            next_id += 1
            added += 1
    for g in previous:
        if isinstance(g, dict) and g.get("key") not in keys:
            out.append(g if g.get("source") != "G1" else {**g, "no_longer_detected": True})
    data = {"version": 1, "categories": {k: v["title"] for k, v in CATEGORIES.items()}, "gaps": out}
    path = inventory_path(ws)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    counts: dict[str, int] = {}
    for g in out:
        counts[g["category"]] = counts.get(g["category"], 0) + 1
    return {"status": "PASS", "gaps": len(out), "added": added, "by_category": dict(sorted(counts.items())),
            "open": sum(1 for g in out if g.get("status") != "decided"), "output": "/".join(INVENTORY)}


def _evidence_errors(where: str, evidence: Any, root: Path | None) -> list[str]:
    if not isinstance(evidence, list) or not evidence:
        return [f"{where}: evidence must be a non-empty list of {{file, line, symbol}}"]
    errs = []
    for e in evidence:
        if not isinstance(e, dict) or not isinstance(e.get("file"), str) or not isinstance(e.get("line"), int) or not e.get("symbol"):
            errs.append(f"{where}: each evidence is {{file, line, symbol}}, got {e!r}")
            continue
        if root is None:
            continue
        path = root.parent / e["file"]
        if not path.is_file():
            errs.append(f"{where}: {e['file']} is not a file of the monolith (paths start with its directory name)")
            continue
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        near = "\n".join(lines[max(0, e["line"] - 3):e["line"] + 2])
        if str(e["symbol"]).strip() not in near:
            errs.append(f"{where}: {e['file']}:{e['line']} does not mention {e['symbol']!r}")
    return errs


def _judge_case_ids(ws: Path) -> set[str] | None:
    """Ids of the judge's cases; None in a workspace without a judge."""
    root = ws / "judge" / "cases"
    if not root.is_dir():
        return None
    ids: set[str] = set()
    for path in root.glob("*/*.json"):
        try:
            ids.add(str(json.loads(path.read_text(encoding="utf-8")).get("id") or path.stem))
        except (OSError, ValueError):
            ids.add(path.stem)
    return ids


def _judge_case_errors(where: str, decision: dict[str, Any], known: set[str] | None) -> list[str]:
    """V2P-23: "the judge verifies it" has to name what does, or it is only a claim."""
    named = decision.get("judge_cases")
    if known is None:
        return [f"{where}: decision.test is judge, but this workspace has no judge"]
    if not isinstance(named, list) or not named or not all(isinstance(c, str) for c in named):
        return [f"{where}: decision.test is judge, so decision.judge_cases must list the judge cases that verify it. "
                "When no case can (for example the data is too small to reach the behaviour), use test: none and say "
                "that it can only be checked by reading the code"]
    return [f"{where}: decision.judge_cases names {c!r}, which is not a judge case" for c in named if c not in known]


def judge_case_problems(ws: Path | str, verdict: dict[str, Any]) -> list[str]:
    """Before B0: the cases each test: judge decision names were really run and did not fail.
    A dropped case proves nothing: at least one of them has to have run."""
    data = load(ws) or {}
    cases = verdict.get("cases") or {}
    out = []
    for g in data.get("gaps") or []:
        d = (g.get("decision") or {}) if isinstance(g, dict) else {}
        if d.get("test") != "judge" or g.get("no_longer_detected"):
            continue
        named = d.get("judge_cases") or []
        verdicts = {c: (cases.get(c) or {}).get("verdict", "not run") for c in named}
        bad = {c: v for c, v in verdicts.items() if v in ("fail", "infra_error", "not run")}
        if bad:
            out.append(f"{g.get('id')}: {bad}")
        elif not any(v in ("pass", "migration-change", "ruled") for v in verdicts.values()):
            out.append(f"{g.get('id')}: none of {named} ran on the target ({verdicts})")
    return out


def check_inventory(ws: Path | str, require_decided: bool = False) -> dict[str, Any]:
    """The inventory's format; with require_decided also that every gap has a decision
    that DECISIONS.md repeats (A2)."""
    ws = Path(ws)
    data = load(ws)
    where = "/".join(INVENTORY)
    if data is None:
        return {"status": "FAIL", "errors": [f"{where} not found: gap_scan (G1) comes first"], "gaps": 0, "open": []}
    if "_load_error" in data:
        return {"status": "FAIL", "errors": [f"{where}: {data['_load_error']}"], "gaps": 0, "open": []}
    gaps = data.get("gaps")
    if not isinstance(gaps, list):
        return {"status": "FAIL", "errors": [f"{where}: gaps must be a list"], "gaps": 0, "open": []}
    report = _report(ws)
    root_s = (report.get("spec_meta") or {}).get("monolith_root")
    root = Path(root_s) if root_s and Path(root_s).is_dir() else None
    endpoints = {_name(e) for e in report.get("entry_points") or [] if isinstance(e, dict)}
    decisions_path = ws / "storage" / "DECISIONS.md"
    decisions = decisions_path.read_text(encoding="utf-8", errors="replace") if decisions_path.is_file() else ""
    errs, seen, undecided = [], set(), []
    judge_cases = _judge_case_ids(ws)
    for i, g in enumerate(gaps):
        w = f"{where}: gaps[{i}]"
        if not isinstance(g, dict):
            errs.append(f"{w}: must be an object")
            continue
        w = f"{where}: {g.get('id', f'gaps[{i}]')}"
        if set(g) - ENTRY_KEYS:
            errs.append(f"{w}: unknown keys {sorted(set(g) - ENTRY_KEYS)}")
        if not re.fullmatch(r"GAP-\d+", str(g.get("id"))) or g.get("id") in seen:
            errs.append(f"{w}: id must be a unique GAP-<n>")
        seen.add(g.get("id"))
        if g.get("category") == "new":
            if not str(g.get("candidate_name") or "").strip():
                errs.append(f"{w}: category new (a new category candidate) needs candidate_name")
        elif g.get("category") not in CATEGORIES:
            errs.append(f"{w}: category must be one of {sorted(CATEGORIES)} or new")
        if not str(g.get("summary") or "").strip():
            errs.append(f"{w}: summary is required")
        if g.get("source") not in ("G1", "G2"):
            errs.append(f"{w}: source is G1 or G2")
        errs += _evidence_errors(w, g.get("evidence"), root) if not g.get("no_longer_detected") else []
        errs += [f"{w}: endpoint {e!r} is not in the fact base" for e in g.get("endpoints") or [] if e not in endpoints]
        status = g.get("status")
        if status not in ("open", "decided"):
            errs.append(f"{w}: status is open or decided")
        if status == "decided":
            d = g.get("decision")
            if not isinstance(d, dict) or not str(d.get("choice") or "").strip() or not str(d.get("why") or "").strip():
                errs.append(f"{w}: a decided gap needs decision {{choice, why, test}}")
            elif d.get("test") not in TESTS:
                errs.append(f"{w}: decision.test must be one of {TESTS}")
            elif d.get("test") == "judge" and _judge_case_errors(w, d, judge_cases):
                errs += _judge_case_errors(w, d, judge_cases)
            elif require_decided and not re.search(rf"{re.escape(str(g.get('id')))}(?!\d)", decisions):
                errs.append(f"{w}: storage/DECISIONS.md does not mention this gap")
        elif not g.get("no_longer_detected"):
            undecided.append(g.get("id"))
    if require_decided and undecided:
        errs.append(f"{where}: {len(undecided)} gaps have no decision: {undecided}")
    return {"status": "PASS" if not errs else "FAIL", "errors": errs[:80], "gaps": len(gaps), "open": undecided}


def delta_gaps(ws: Path | str) -> list[dict[str, Any]]:
    """Decided gaps whose decision says they are tested by delta tests."""
    data = load(ws) or {}
    return [g for g in data.get("gaps") or [] if isinstance(g, dict) and not g.get("no_longer_detected")
            and (g.get("decision") or {}).get("test") == "delta"]
