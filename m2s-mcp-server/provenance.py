"""Provenance checks and annotation merging for the spec fields that
spec_extract.py adds to analysis_report.json (PIPELINE_REDESIGN.md §2.1).

Two entry points, both exposed as MCP tools by server.py:

- check_provenance(workspace): the stage-1 gate. Every `source` entry
  ({file, line, symbol}) anywhere in the spec fields must point at a real
  line of the monolith, and that line (or one within PROVENANCE_WINDOW lines
  of it) must literally contain `symbol`. This is aimed at the typical LLM
  failure of inventing a plausible-looking file:line. It also checks the
  schema and that every endpoint is complete (request, responses, auth and
  at least one business rule).

- annotate_entry_point(workspace, method, path, field, value_json): how
  m2s-spec-annotator writes. It replaces one spec field of one endpoint,
  after validating the schema and running the provenance check on just
  that value, so a bad citation is rejected at write time with the exact
  reason instead of surfacing only in the final gate. Items that claim
  origin "script" must be byte-identical to a script item already there,
  so the annotator cannot relabel its own guesses as script output.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

PROVENANCE_WINDOW = 2
SPEC_FIELDS = ("handler_location", "auth", "request", "responses", "data_access", "business_rules")
REQUEST_LOCATIONS = ("path_params", "query", "body", "headers")
AUTH_CLASSES = ("required", "optional", "none", "unknown")
PERF_INTENTS = ("cache", "batch", "prefetch")
GUARD_KINDS = ("authz", "error_propagation")
RESPONSE_KINDS = ("success", "error")
RULE_KINDS = ("validation", "authorization", "data_scope", "side_effect", "error_handling", "computation", "other")
# Contract testing (PIPELINE_REDESIGN.md section 3.6): whether a rule is an
# externally observable effect or just how the monolith happens to do it.
RULE_CLASSES = ("contract", "implementation-trace")
ORIGINS = ("script", "llm")


class SpecError(ValueError):
    pass


# ---------------------------------------------------------------------------
# Source (file:line:symbol) checking
# ---------------------------------------------------------------------------

class _Files:
    def __init__(self, monolith_root: Path):
        self.root = monolith_root.resolve()
        self.cache: dict[str, list[str] | None] = {}

    def lines(self, rel: str) -> list[str] | None:
        if rel not in self.cache:
            path = (self.root.parent / rel).resolve()
            try:
                path.relative_to(self.root)
            except ValueError:
                self.cache[rel] = None
                return None
            self.cache[rel] = path.read_text(encoding="utf-8", errors="ignore").splitlines() if path.is_file() else None
        return self.cache[rel]


def _check_source(files: _Files, src: Any) -> str | None:
    if not isinstance(src, dict) or set(src) != {"file", "line", "symbol"}:
        return f"source must be exactly {{file, line, symbol}}, got {src!r}"
    file, line, symbol = src["file"], src["line"], src["symbol"]
    if not isinstance(file, str) or not isinstance(line, int) or isinstance(line, bool):
        return f"source file must be a string and line an integer, got {src!r}"
    if not isinstance(symbol, str) or not symbol.strip() or len(symbol) > 120:
        return f"source symbol must be a non-empty string of at most 120 chars, got {symbol!r}"
    lines = files.lines(file)
    if lines is None:
        return f"{file}: file does not exist inside the monolith (paths look like '{files.root.name}/routes/x.js')"
    if not 1 <= line <= len(lines):
        return f"{file}:{line}: line out of range (file has {len(lines)} lines)"
    lo, hi = max(1, line - PROVENANCE_WINDOW), min(len(lines), line + PROVENANCE_WINDOW)
    if not any(symbol in lines[i - 1] for i in range(lo, hi + 1)):
        return (f"{file}:{line}: symbol {symbol!r} does not appear on lines {lo}-{hi} "
                f"(line {line} is: {lines[line - 1].strip()!r})")
    return None


def _check_sources(files: _Files, sources: Any, where: str, errors: list[str]) -> None:
    if not isinstance(sources, list) or not sources:
        errors.append(f"{where}: 'source' must be a non-empty list")
        return
    for i, s in enumerate(sources):
        err = _check_source(files, s)
        if err:
            errors.append(f"{where}.source[{i}]: {err}")


# ---------------------------------------------------------------------------
# Schema validation (collects errors instead of raising)
# ---------------------------------------------------------------------------

def _keys_ok(obj: dict, required: set[str], optional: set[str], where: str, errors: list[str]) -> bool:
    missing = required - set(obj)
    extra = set(obj) - required - optional
    if missing:
        errors.append(f"{where}: missing keys {sorted(missing)}")
    if extra:
        errors.append(f"{where}: unknown keys {sorted(extra)} (allowed: {sorted(required | optional)})")
    return not missing


def _origin_ok(obj: dict, where: str, errors: list[str]) -> None:
    if obj.get("origin") not in ORIGINS:
        errors.append(f"{where}: 'origin' must be one of {ORIGINS}")


def _validate_request_field(files: _Files, f: Any, where: str, errors: list[str]) -> None:
    if not isinstance(f, dict):
        errors.append(f"{where}: must be an object")
        return
    if not _keys_ok(f, {"name", "type", "required", "source", "origin"}, {"note", "fields"}, where, errors):
        return
    if not isinstance(f["name"], str) or not f["name"]:
        errors.append(f"{where}: 'name' must be a non-empty string")
    if not isinstance(f["type"], str) or not f["type"]:
        errors.append(f"{where}: 'type' must be a string ('unknown' if undeterminable)")
    if f["required"] not in (True, False, "unknown"):
        errors.append(f"{where}: 'required' must be true, false or \"unknown\"")
    _origin_ok(f, where, errors)
    _check_sources(files, f["source"], where, errors)
    # Element/member fields of an array or object field (e.g. books[].bookId).
    for i, sub in enumerate(f.get("fields") or []):
        _validate_request_field(files, sub, f"{where}.fields[{i}]", errors)


def _validate_field(files: _Files, field: str, value: Any, where: str, errors: list[str],
                    shared_ids: set[str] | None = None) -> None:
    if field == "handler_location":
        if not isinstance(value, dict) or not _keys_ok(value, {"file", "start_line", "end_line"}, set(), where, errors):
            if not isinstance(value, dict):
                errors.append(f"{where}: must be an object {{file, start_line, end_line}}")
            return
        lines = files.lines(value["file"]) if isinstance(value["file"], str) else None
        s, e = value["start_line"], value["end_line"]
        if lines is None:
            errors.append(f"{where}: file {value['file']!r} does not exist inside the monolith")
        elif not (isinstance(s, int) and isinstance(e, int) and 1 <= s <= e <= len(lines)):
            errors.append(f"{where}: need 1 <= start_line <= end_line <= {len(lines)}, got {s}..{e}")
        return

    if field == "auth":
        if not isinstance(value, dict):
            errors.append(f"{where}: must be an object")
            return
        if not _keys_ok(value, {"classification", "middleware", "source", "origin"}, {"note"}, where, errors):
            return
        if value["classification"] not in AUTH_CLASSES:
            errors.append(f"{where}: 'classification' must be one of {AUTH_CLASSES}")
        if not isinstance(value["middleware"], list) or not all(isinstance(m, str) for m in value["middleware"]):
            errors.append(f"{where}: 'middleware' must be a list of strings")
        _origin_ok(value, where, errors)
        _check_sources(files, value["source"], where, errors)
        return

    if field == "request":
        if not isinstance(value, dict):
            errors.append(f"{where}: must be an object with keys {REQUEST_LOCATIONS}")
            return
        if not _keys_ok(value, set(REQUEST_LOCATIONS), set(), where, errors):
            return
        for loc in REQUEST_LOCATIONS:
            if not isinstance(value[loc], list):
                errors.append(f"{where}.{loc}: must be a list")
                continue
            for i, f in enumerate(value[loc]):
                _validate_request_field(files, f, f"{where}.{loc}[{i}]", errors)
        return

    if not isinstance(value, list):
        errors.append(f"{where}: must be a list")
        return

    for i, item in enumerate(value):
        iw = f"{where}[{i}]"
        if not isinstance(item, dict):
            errors.append(f"{iw}: must be an object")
            continue
        if field == "responses":
            if not _keys_ok(item, {"status", "kind", "body", "source", "origin"},
                            {"when", "in_catch", "via", "note", "trace_arm", "guard_kind"}, iw, errors):
                continue
            st = item["status"]
            if not ((isinstance(st, int) and not isinstance(st, bool) and 100 <= st <= 599) or st == "unknown"):
                errors.append(f"{iw}: 'status' must be an HTTP status integer or \"unknown\"")
            # A status held in a variable is "unknown", and then so is whether
            # the response is a success or an error.
            if "trace_arm" in item and not isinstance(item["trace_arm"], bool):
                errors.append(f"{iw}: 'trace_arm' must be a boolean (true: only an implementation step of the monolith produces this response)")
            # guard_kind: an error response that returns before later data
            # access. authz keeps those accesses behind it; error_propagation
            # lets rule R1b issue the later reads speculatively
            # (PERF_OPTIMIZATION.md 10.4 (a)).
            if "guard_kind" in item and item["guard_kind"] not in GUARD_KINDS:
                errors.append(f"{iw}: 'guard_kind' must be one of {GUARD_KINDS}")
            if item["kind"] not in RESPONSE_KINDS and not (item["kind"] == "unknown" and st == "unknown"):
                errors.append(f"{iw}: 'kind' must be one of {RESPONSE_KINDS} (or \"unknown\" together with status \"unknown\")")
            body = item["body"]
            if not isinstance(body, dict) or not _keys_ok(body, {"fields", "expression"}, {"description"}, f"{iw}.body", errors):
                if not isinstance(body, dict):
                    errors.append(f"{iw}.body: must be an object {{fields, expression, description?}}")
            elif body["fields"] is not None and not (isinstance(body["fields"], list) and all(isinstance(x, str) for x in body["fields"])):
                errors.append(f"{iw}.body.fields: must be null or a list of strings")
        elif field == "data_access":
            if not _keys_ok(item, {"operation", "table", "index", "repeated", "source", "origin"},
                            {"via", "note"}, iw, errors):
                continue
            if not isinstance(item["table"], dict):
                errors.append(f"{iw}.table: must be an object")
            if not isinstance(item["repeated"], bool):
                errors.append(f"{iw}: 'repeated' must be a boolean")
        elif field == "business_rules":
            rule = item.get("rule")
            is_ref = isinstance(rule, str) and rule.strip().lower().startswith("ref:")
            required = {"rule", "source", "origin"} if is_ref else {"rule", "kind", "source", "origin"}
            optional = {"id", "note", "class", "perf_intent"} | ({"kind"} if is_ref else set())
            if not _keys_ok(item, required, optional, iw, errors):
                continue
            if not isinstance(rule, str) or not rule.strip():
                errors.append(f"{iw}: 'rule' must be a non-empty string")
            if is_ref:
                ref = rule.split(":", 1)[1].strip()
                if shared_ids is None:
                    errors.append(f"{iw}: shared reference {ref!r} outside a fact base")
                elif ref not in shared_ids:
                    errors.append(f"{iw}: shared reference {ref!r} is not an id in analysis_report.shared_rules")
                if "kind" in item and item["kind"] not in RULE_KINDS:
                    errors.append(f"{iw}: 'kind' must be one of {RULE_KINDS}")
            elif item.get("kind") not in RULE_KINDS:
                errors.append(f"{iw}: 'kind' must be one of {RULE_KINDS}")
            if "class" in item and item["class"] not in RULE_CLASSES:
                errors.append(f"{iw}: 'class' must be one of {RULE_CLASSES}")
            # perf_intent: the rule describes something the monolith does for
            # speed (cache, batching, prefetch); the architect turns it into a
            # perf hint instead of dropping it (PERF_OPTIMIZATION.md 10.4 (c)).
            if "perf_intent" in item and item["perf_intent"] not in PERF_INTENTS:
                errors.append(f"{iw}: 'perf_intent' must be one of {PERF_INTENTS}")
        else:
            errors.append(f"{where}: unknown spec field {field!r}")
            return
        _origin_ok(item, iw, errors)
        _check_sources(files, item.get("source"), iw, errors)


def _script_items(field: str, value: Any) -> list[str]:
    """Canonical JSON of every origin=script item in a field value."""
    if field in ("handler_location",) or value is None:
        return []
    if field == "auth":
        items = [value]
    elif field == "request":
        items = [f for loc in REQUEST_LOCATIONS for f in (value.get(loc) or [])] if isinstance(value, dict) else []
    else:
        items = value if isinstance(value, list) else []
    return [json.dumps(i, sort_keys=True, ensure_ascii=False) for i in items
            if isinstance(i, dict) and i.get("origin") == "script"]


def _count_unknown(ep: dict[str, Any]) -> int:
    n = 0
    req = ep.get("request") or {}
    for loc in REQUEST_LOCATIONS:
        for f in req.get(loc) or []:
            n += (f.get("type") == "unknown") + (f.get("required") == "unknown")
    for r in ep.get("responses") or []:
        n += (r.get("status") == "unknown") + ((r.get("body") or {}).get("fields") is None and not (r.get("body") or {}).get("description"))
    if (ep.get("auth") or {}).get("classification") == "unknown":
        n += 1
    return n


# ---------------------------------------------------------------------------
# Report I/O
# ---------------------------------------------------------------------------

def _report_path(workspace: str | Path) -> Path:
    return Path(workspace) / "storage" / "analysis_report.json"


def _load(workspace: str | Path) -> tuple[dict[str, Any], _Files]:
    path = _report_path(workspace)
    if not path.exists():
        raise SpecError(f"{path} not found -- run analyze_project first")
    report = json.loads(path.read_text(encoding="utf-8"))
    root = (report.get("spec_meta") or {}).get("monolith_root")
    if not root:
        raise SpecError("analysis_report.json has no spec_meta.monolith_root -- rerun analyze_project")
    return report, _Files(Path(root))


def _save(workspace: str | Path, report: dict[str, Any]) -> None:
    path = _report_path(workspace)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _find_ep(report: dict[str, Any], method: str, path: str) -> dict[str, Any]:
    for ep in report.get("entry_points", []):
        if (ep.get("method") or "").upper() == method.upper() and ep.get("path") == path:
            return ep
    known = [f"{ep.get('method')} {ep.get('path')}" for ep in report.get("entry_points", [])]
    raise SpecError(f"no entry point {method.upper()} {path}; known: {known}")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

SHARED_ID_RE = re.compile(r"^shared/[a-z0-9][a-z0-9._-]*$")


def _validate_shared_entry(files: _Files, entry: Any, where: str, errors: list[str]) -> None:
    """One analysis_report.shared_rules entry (v3 M2/W2.5).

    Shared code is annotated once by F2:shared; endpoints cite it with a
    business_rules item {"rule": "ref: shared/<id>", ...}.
    """
    if not isinstance(entry, dict):
        errors.append(f"{where}: must be an object")
        return
    required = {"id", "file", "function", "rule", "kind", "source", "origin"}
    if not _keys_ok(entry, required, {"used_by", "note", "class"}, where, errors):
        return
    if not isinstance(entry["id"], str) or not SHARED_ID_RE.match(entry["id"]):
        errors.append(f"{where}: 'id' must look like shared/<slug>")
    for key in ("file", "function", "rule"):
        if not isinstance(entry[key], str) or not entry[key].strip():
            errors.append(f"{where}: '{key}' must be a non-empty string")
    if isinstance(entry.get("rule"), str) and entry["rule"].strip().lower().startswith("ref:"):
        errors.append(f"{where}: a shared entry's rule cannot itself be a ref")
    if entry.get("kind") not in RULE_KINDS:
        errors.append(f"{where}: 'kind' must be one of {RULE_KINDS}")
    if "class" in entry and entry["class"] not in RULE_CLASSES:
        errors.append(f"{where}: 'class' must be one of {RULE_CLASSES}")
    used_by = entry.get("used_by")
    if used_by is not None and (not isinstance(used_by, list) or not all(isinstance(x, str) for x in used_by)):
        errors.append(f"{where}: 'used_by' must be a list of endpoint strings")
    _origin_ok(entry, where, errors)
    _check_sources(files, entry.get("source"), where, errors)


def _shared_ids(files: _Files, entries: Any, errors: list[str]) -> set[str]:
    ids: set[str] = set()
    if entries is None:
        return ids
    if not isinstance(entries, list):
        errors.append("shared_rules: must be a list")
        return ids
    for i, entry in enumerate(entries):
        where = f"shared_rules[{i}]"
        _validate_shared_entry(files, entry, where, errors)
        if isinstance(entry, dict) and isinstance(entry.get("id"), str):
            if entry["id"] in ids:
                errors.append(f"{where}: duplicate shared id {entry['id']!r}")
            ids.add(entry["id"])
    return ids


def annotate_shared_entry(workspace: str | Path, entry_json: str) -> dict[str, Any]:
    """F2:shared: write (replace by id, or append) one shared_rules entry."""
    try:
        entry = json.loads(entry_json)
    except json.JSONDecodeError as exc:
        raise SpecError(f"entry_json is not valid JSON: {exc}") from exc
    report, files = _load(workspace)
    errors: list[str] = []
    _validate_shared_entry(files, entry, "shared entry", errors)
    if errors:
        return {"ok": False, "errors": errors}
    entries = report.setdefault("shared_rules", [])
    if not isinstance(entries, list):
        return {"ok": False, "errors": ["analysis_report.shared_rules is not a list"]}
    replaced = False
    for i, old in enumerate(entries):
        if isinstance(old, dict) and old.get("id") == entry["id"]:
            entries[i] = entry
            replaced = True
            break
    if not replaced:
        entries.append(entry)
    _save(workspace, report)
    return {"ok": True, "id": entry["id"], "shared_entries": len(entries), "replaced": replaced}


GM_KINDS = ("cors", "security_headers", "response_header_hook")


def _validate_global_middleware(files: Any, items: Any, errors: list[str], incomplete: list[str]) -> None:
    """report.global_middleware (P0 + S0): app-wide middleware that changes
    every response. headers null means S0 has not determined them yet."""
    if items is None:
        return
    if not isinstance(items, list):
        errors.append("global_middleware: must be a list")
        return
    for i, it in enumerate(items):
        where = f"global_middleware[{i}]"
        if not isinstance(it, dict):
            errors.append(f"{where}: must be an object")
            continue
        if it.get("kind") not in GM_KINDS:
            errors.append(f"{where}: kind must be one of {GM_KINDS}")
        h = it.get("headers")
        if h is None:
            incomplete.append(f"{where} ({it.get('call')}): headers not determined -- read the middleware's options and "
                              "fill the headers every response gets (annotate_global_middleware)")
        elif not isinstance(h, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in h.items()):
            errors.append(f"{where}: headers must map header names to string values (use a description such as "
                          "\"<request Origin if in CORS_ORIGINS>\" when the value depends on the request)")
        _origin_ok(it, where, errors)
        _check_sources(files, it.get("source"), where, errors)


def annotate_global_middleware(workspace: str | Path, index: int, headers_json: str, note: str = "",
                               source_json: str = "") -> dict[str, Any]:
    """S0: set the headers one app-wide middleware item adds to every response."""
    try:
        headers = json.loads(headers_json)
        extra_sources = json.loads(source_json) if source_json else []
    except json.JSONDecodeError as exc:
        raise SpecError(f"headers_json / source_json is not valid JSON: {exc}") from exc
    report, files = _load(workspace)
    items = report.get("global_middleware") or []
    if not isinstance(index, int) or not 0 <= index < len(items):
        raise SpecError(f"index must be 0..{len(items) - 1}")
    item = dict(items[index])
    item["headers"] = headers
    item["origin"] = "llm"
    if note:
        item["note"] = note
    if extra_sources:
        item["source"] = list(item.get("source") or []) + list(extra_sources)
    errors: list[str] = []
    _validate_global_middleware(files, [item], errors, [])
    errors = [e.replace("global_middleware[0]", f"global_middleware[{index}]") for e in errors]
    if errors:
        return {"ok": False, "errors": errors}
    items[index] = item
    report["global_middleware"] = items
    _save(workspace, report)
    return {"ok": True, "index": index, "headers": headers}


def annotate_entry_point(workspace: str | Path, method: str, path: str, field: str, value_json: str) -> dict[str, Any]:
    if field not in SPEC_FIELDS:
        raise SpecError(f"field must be one of {SPEC_FIELDS}, got {field!r}")
    try:
        value = json.loads(value_json)
    except json.JSONDecodeError as exc:
        raise SpecError(f"value_json is not valid JSON: {exc}") from exc

    report, files = _load(workspace)
    ep = _find_ep(report, method, path)
    where = f"{method.upper()} {path}.{field}"

    errors: list[str] = []
    shared_ids = _shared_ids(files, report.get("shared_rules"), errors)
    _validate_field(files, field, value, where, errors, shared_ids)
    existing = set(_script_items(field, ep.get(field)))
    for canon in _script_items(field, value):
        if canon not in existing:
            errors.append(
                f"{where}: an item claims origin \"script\" but is not identical to any script-extracted item "
                f"already in this field; items you add or change must use origin \"llm\": {canon[:200]}"
            )
    if errors:
        return {"ok": False, "errors": errors}

    ep[field] = value
    status = ep.setdefault("spec_status", {"script": "unknown", "annotated_fields": []})
    if field not in status.setdefault("annotated_fields", []):
        status["annotated_fields"].append(field)
    _save(workspace, report)
    return {"ok": True, "entry_point": f"{method.upper()} {path}", "field": field,
            "annotated_fields": status["annotated_fields"], "unknown_items": _count_unknown(ep)}


def check_provenance(workspace: str | Path, endpoint: str | None = None) -> dict[str, Any]:
    try:
        report, files = _load(workspace)
    except (SpecError, json.JSONDecodeError) as exc:
        return {"status": "FAIL", "errors": [str(exc)], "incomplete": [], "checked_sources": 0}

    errors: list[str] = []
    incomplete: list[str] = []
    per_endpoint: list[dict[str, Any]] = []
    shared_ids = _shared_ids(files, report.get("shared_rules"), errors)

    selected = [ep for ep in report.get("entry_points", [])
                if not endpoint or f"{(ep.get('method') or '').upper()} {ep.get('path')}".lower() == endpoint.strip().lower()]
    if endpoint and not selected:
        known = [f"{ep.get('method')} {ep.get('path')}" for ep in report.get("entry_points", [])]
        return {"status": "FAIL", "errors": [f"no entry point {endpoint!r}; known: {known}"],
                "incomplete": [], "checked_sources": 0}

    for ep in selected:
        name = f"{ep.get('method')} {ep.get('path')}"
        for field in SPEC_FIELDS:
            if ep.get(field) is None:
                incomplete.append(f"{name}: '{field}' is missing")
                continue
            _validate_field(files, field, ep[field], f"{name}.{field}", errors, shared_ids)
        if not ep.get("responses"):
            incomplete.append(f"{name}: no responses")
        elif not any(r.get("kind") == "success" for r in ep["responses"] if isinstance(r, dict)):
            incomplete.append(f"{name}: no success response")
        if not ep.get("business_rules"):
            incomplete.append(f"{name}: no business_rules (annotator has not covered this endpoint)")
        per_endpoint.append({
            "entry_point": name,
            "annotated_fields": (ep.get("spec_status") or {}).get("annotated_fields", []),
            "business_rules": len(ep.get("business_rules") or []),
            "unknown_items": _count_unknown(ep),
        })

    # A contract-2 spec classifies every rule; a report with some classes but
    # not all is an annotator that stopped halfway.
    rules = [r for ep in selected for r in ep.get("business_rules") or [] if isinstance(r, dict)]
    if any("class" in r for r in rules):
        for ep in selected:
            for j, r in enumerate(ep.get("business_rules") or []):
                if isinstance(r, dict) and "class" not in r:
                    incomplete.append(f"{ep.get('method')} {ep.get('path')}.business_rules[{j}]: no 'class' (contract / implementation-trace)")

    _validate_global_middleware(files, report.get("global_middleware"), errors, incomplete)

    for i, t in enumerate((report.get("dynamodb_info") or {}).get("table_schemas", []) or []):
        where = f"dynamodb_info.table_schemas[{i}]"
        _check_sources(files, t.get("source"), where, errors)
        for j, g in enumerate(t.get("gsis") or []):
            _check_sources(files, g.get("source"), f"{where}.gsis[{j}]", errors)

    checked = json.dumps(report.get("entry_points", [])).count('"symbol"') + \
        json.dumps((report.get("dynamodb_info") or {}).get("table_schemas", [])).count('"symbol"')
    return {
        "status": "PASS" if not errors and not incomplete else "FAIL",
        "errors": errors,
        "incomplete": incomplete,
        "checked_sources": checked,
        "unknown_items": sum(e["unknown_items"] for e in per_endpoint),
        "endpoints": per_endpoint,
    }
