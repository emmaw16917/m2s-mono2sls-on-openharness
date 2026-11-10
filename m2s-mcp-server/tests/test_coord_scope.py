"""v3 M2 (W2.6): scoped checks.

check_provenance can check one endpoint; run_gate(code) can be scoped to a
work package or an explicit function list, so a package task is not blocked by
another package's unfinished directory.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import gates  # noqa: E402
import provenance  # noqa: E402

REF = {"file": "mono/server.js", "line": 1, "symbol": "authMiddleware"}
ENDPOINT = {
    "method": "GET", "path": "/things/:id",
    "handler_location": {"file": "mono/server.js", "start_line": 1, "end_line": 3},
    "auth": {"classification": "none", "middleware": [], "source": [REF], "origin": "script"},
    "request": {"path_params": [{"name": "id", "type": "string", "required": True, "source": [REF], "origin": "script"}],
                "query": [], "body": [], "headers": []},
    "responses": [{"status": 200, "kind": "success", "body": {"fields": ["id"], "expression": "res.send"},
                   "source": [REF], "origin": "script"}],
    "data_access": [],
    "business_rules": [{"rule": "rule", "kind": "other", "source": [REF], "origin": "llm"}],
}


def _provenance_ws(tmp_path: Path) -> Path:
    mono = tmp_path / "mono"
    mono.mkdir()
    (mono / "server.js").write_text("function authMiddleware(req, res, next) {\n  next();\n}\n", encoding="utf-8")
    second = json.loads(json.dumps(ENDPOINT))
    second["path"] = "/things/:id/other"
    second["business_rules"] = []
    ws = tmp_path / "ws"
    (ws / "storage").mkdir(parents=True)
    (ws / "storage" / "analysis_report.json").write_text(json.dumps({
        "spec_meta": {"monolith_root": str(mono)},
        "entry_points": [ENDPOINT, second],
    }), encoding="utf-8")
    return ws


def test_check_provenance_scoped_to_endpoint(tmp_path):
    ws = _provenance_ws(tmp_path)
    full = provenance.check_provenance(ws)
    assert full["status"] == "FAIL"
    assert any("/things/:id/other" in item for item in full["incomplete"])
    scoped = provenance.check_provenance(ws, "GET /things/:id")
    assert scoped["status"] == "PASS", scoped
    assert [e["entry_point"] for e in scoped["endpoints"]] == ["GET /things/:id"]


def test_check_provenance_unknown_endpoint(tmp_path):
    result = provenance.check_provenance(_provenance_ws(tmp_path), "GET /nope")
    assert result["status"] == "FAIL"
    assert "no entry point" in result["errors"][0]


def _gate_ws(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    (ws / "storage").mkdir(parents=True)
    (ws / "storage" / "blueprint.json").write_text(json.dumps({
        "lambda_functions": [{"name": "a"}, {"name": "b"}],
    }), encoding="utf-8")
    (ws / "output" / "lambdas" / "domain" / "a").mkdir(parents=True)
    (ws / "output" / "lambdas" / "domain" / "a" / "handler.py").write_text("", encoding="utf-8")
    return ws


def _c1(result):
    return [f for f in result["checks"] if f["check_id"] == "C1"]


def test_gate_code_scoped_by_functions(tmp_path):
    ws = _gate_ws(tmp_path)
    full = gates.run_gate("code", str(ws))
    assert any("'b'" in f["description"] for f in _c1(full))
    scoped = gates.run_gate("code", str(ws), functions=["a"])
    assert _c1(scoped) == [], _c1(scoped)


def test_gate_code_scoped_by_package(tmp_path):
    ws = _gate_ws(tmp_path)
    (ws / "storage" / "work_packages.json").write_text(json.dumps({
        "impl_packages": {"pkg-a": {"functions": ["a"]}},
    }), encoding="utf-8")
    scoped = gates.run_gate("code", str(ws), package="pkg-a")
    assert _c1(scoped) == [], _c1(scoped)
    missing = gates.run_gate("code", str(ws), package="nope")
    assert missing["status"] == "FAIL"
    assert any(f["check_id"] == "SCOPE" for f in missing["checks"])
