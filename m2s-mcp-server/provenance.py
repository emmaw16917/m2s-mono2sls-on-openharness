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
from pathlib import Path
from typing import Any

PROVENANCE_WINDOW = 2
SPEC_FIELDS = ("handler_location", "auth", "request", "responses", "data_access", "business_rules")
REQUEST_LOCATIONS = ("path_params", "query", "body", "headers")
AUTH_CLASSES = ("required", "optional", "none", "unknown")
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


def _validate_field(files: _Files, field: str, value: Any, where: str, errors: list[str]) -> None:
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
                            {"when", "in_catch", "via", "note", "trace_arm"}, iw, errors):
                continue
            st = item["status"]
            if not ((isinstance(st, int) and not isinstance(st, bool) and 100 <= st <= 599) or st == "unknown"):
                errors.append(f"{iw}: 'status' must be an HTTP status integer or \"unknown\"")
            # A status held in a variable is "unknown", and then so is whether
            # the response is a success or an error.
            if "trace_arm" in item and not isinstance(item["trace_arm"], bool):
                errors.append(f"{iw}: 'trace_arm' must be a boolean (true: only an implementation step of the monolith produces this response)")
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
            if not _keys_ok(item, {"rule", "kind", "source", "origin"}, {"id", "note", "class"}, iw, errors):
                continue
            if not isinstance(item["rule"], str) or not item["rule"].strip():
                errors.append(f"{iw}: 'rule' must be a non-empty string")
            if item["kind"] not in RULE_KINDS:
                errors.append(f"{iw}: 'kind' must be one of {RULE_KINDS}")
            if "class" in item and item["class"] not in RULE_CLASSES:
                errors.append(f"{iw}: 'class' must be one of {RULE_CLASSES}")
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
    _validate_field(files, field, value, where, errors)
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


def check_provenance(workspace: str | Path) -> dict[str, Any]:
    try:
        report, files = _load(workspace)
    except (SpecError, json.JSONDecodeError) as exc:
        return {"status": "FAIL", "errors": [str(exc)], "incomplete": [], "checked_sources": 0}

    errors: list[str] = []
    incomplete: list[str] = []
    per_endpoint: list[dict[str, Any]] = []

    for ep in report.get("entry_points", []):
        name = f"{ep.get('method')} {ep.get('path')}"
        for field in SPEC_FIELDS:
            if ep.get(field) is None:
                incomplete.append(f"{name}: '{field}' is missing")
                continue
            _validate_field(files, field, ep[field], f"{name}.{field}", errors)
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
    rules = [r for ep in report.get("entry_points", []) for r in ep.get("business_rules") or [] if isinstance(r, dict)]
    if any("class" in r for r in rules):
        for ep in report.get("entry_points", []):
            for j, r in enumerate(ep.get("business_rules") or []):
                if isinstance(r, dict) and "class" not in r:
                    incomplete.append(f"{ep.get('method')} {ep.get('path')}.business_rules[{j}]: no 'class' (contract / implementation-trace)")

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
