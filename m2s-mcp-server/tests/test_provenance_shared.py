"""v3 M2 (W2.5): shared-code rules in the fact base.

F2:shared writes analysis_report.shared_rules once; endpoints cite it with
{"rule": "ref: shared/<id>"}. check_provenance validates both sides.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import provenance  # noqa: E402

MONO = "function authMiddleware(req, res, next) {\n  next();\n}\n"
REF = {"file": "mono/server.js", "line": 1, "symbol": "authMiddleware"}
ENDPOINT = "GET /things/:id"


def _workspace(tmp_path: Path, shared: bool = True, rule: str = "ref: shared/auth-middleware") -> Path:
    mono = tmp_path / "mono"
    mono.mkdir(exist_ok=True)
    (mono / "server.js").write_text(MONO, encoding="utf-8")
    report = {
        "spec_meta": {"monolith_root": str(mono)},
        "entry_points": [{
            "method": "GET", "path": "/things/:id",
            "handler_location": {"file": "mono/server.js", "start_line": 1, "end_line": 3},
            "auth": {"classification": "none", "middleware": [], "source": [REF], "origin": "script"},
            "request": {"path_params": [{"name": "id", "type": "string", "required": True, "source": [REF], "origin": "script"}],
                        "query": [], "body": [], "headers": []},
            "responses": [{"status": 200, "kind": "success", "body": {"fields": ["id"], "expression": "res.send"},
                           "source": [REF], "origin": "script"}],
            "data_access": [],
            "business_rules": [{"rule": rule, "source": [REF], "origin": "llm"}],
        }],
        "shared_rules": ([{
            "id": "shared/auth-middleware", "file": "mono/server.js", "function": "authMiddleware",
            "rule": "Every request passes the session check first", "kind": "authorization",
            "source": [REF], "origin": "llm", "used_by": [ENDPOINT],
        }] if shared else []),
    }
    ws = tmp_path / "ws"
    (ws / "storage").mkdir(parents=True, exist_ok=True)
    (ws / "storage" / "analysis_report.json").write_text(json.dumps(report), encoding="utf-8")
    return ws


def test_shared_ref_passes(tmp_path):
    result = provenance.check_provenance(_workspace(tmp_path))
    assert result["status"] == "PASS", result["errors"]
    assert result["errors"] == []


def test_unknown_shared_ref_fails(tmp_path):
    result = provenance.check_provenance(_workspace(tmp_path, rule="ref: shared/missing"))
    assert result["status"] == "FAIL"
    assert any("shared reference 'shared/missing' is not an id" in e for e in result["errors"])


def test_duplicate_shared_ids_fail(tmp_path):
    ws = _workspace(tmp_path)
    path = ws / "storage" / "analysis_report.json"
    report = json.loads(path.read_text(encoding="utf-8"))
    report["shared_rules"].append(dict(report["shared_rules"][0]))
    path.write_text(json.dumps(report), encoding="utf-8")
    result = provenance.check_provenance(ws)
    assert result["status"] == "FAIL"
    assert any("duplicate shared id" in e for e in result["errors"])


def test_annotate_shared_entry_writes_and_replaces(tmp_path):
    ws = _workspace(tmp_path, shared=False, rule="ref: shared/auth-middleware")
    entry = {
        "id": "shared/auth-middleware", "file": "mono/server.js", "function": "authMiddleware",
        "rule": "Every request passes the session check first", "kind": "authorization",
        "source": [REF], "origin": "llm", "used_by": [ENDPOINT],
    }
    first = provenance.annotate_shared_entry(ws, json.dumps(entry))
    assert first["ok"] is True and first["replaced"] is False and first["shared_entries"] == 1
    entry["note"] = "second pass"
    second = provenance.annotate_shared_entry(ws, json.dumps(entry))
    assert second["ok"] is True and second["replaced"] is True and second["shared_entries"] == 1
    assert provenance.check_provenance(ws)["status"] == "PASS"


def test_annotate_entry_point_accepts_refs(tmp_path):
    ws = _workspace(tmp_path)
    rules = [{"rule": "ref: shared/auth-middleware", "source": [REF], "origin": "llm"}]
    result = provenance.annotate_entry_point(ws, "GET", "/things/:id", "business_rules", json.dumps(rules))
    assert result["ok"] is True, result
    bad = [{"rule": "ref: shared/nope", "source": [REF], "origin": "llm"}]
    result = provenance.annotate_entry_point(ws, "GET", "/things/:id", "business_rules", json.dumps(bad))
    assert result["ok"] is False
    assert any("shared/nope" in e for e in result["errors"])
