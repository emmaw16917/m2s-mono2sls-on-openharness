"""Unit tests for the per-package gap files (v3 M4/W4.2: G2 split + G2M merge)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import gaps  # noqa: E402


def _workspace(tmp_path: Path) -> Path:
    storage = tmp_path / "storage"
    storage.mkdir()
    (storage / "analysis_report.json").write_text(json.dumps({
        "entry_points": [{"method": "GET", "path": "/a"}, {"method": "GET", "path": "/b"}]}), encoding="utf-8")
    (storage / "work_packages.json").write_text(json.dumps({"packages": [
        {"id": "p1", "endpoints": [{"method": "GET", "path": "/a"}]},
        {"id": "p2", "endpoints": [{"method": "GET", "path": "/b"}]}]}), encoding="utf-8")
    inv = {"version": 1, "gaps": [
        {"id": "GAP-1", "key": "k1", "category": "time", "summary": "a", "evidence": [{"file": "m/a.py", "line": 1, "symbol": "x"}],
         "endpoints": ["GET /a"], "source": "G1", "status": "open"},
        {"id": "GAP-2", "key": "k2", "category": "time", "summary": "b", "evidence": [{"file": "m/b.py", "line": 1, "symbol": "y"}],
         "endpoints": ["GET /b"], "source": "G1", "status": "open"},
        {"id": "GAP-3", "key": "k3", "category": "process_state", "summary": "s", "evidence": [{"file": "m/c.py", "line": 1, "symbol": "z"}],
         "endpoints": [], "source": "G1", "status": "open"}]}
    (storage / "gap_inventory.json").write_text(json.dumps(inv), encoding="utf-8")
    (storage / "gaps").mkdir()
    return tmp_path


def _write(ws: Path, package: str, entries: list[dict]) -> None:
    (ws / "storage" / "gaps" / f"{package}.json").write_text(json.dumps({"gaps": entries}), encoding="utf-8")


def test_gap_package_owns_its_endpoints_and_covers_the_inventory(tmp_path):
    ws = _workspace(tmp_path)
    _write(ws, "p1", [{"id": "GAP-1", "key": "k1", "category": "time", "summary": "a",
                       "evidence": [{"file": "m/a.py", "line": 1, "symbol": "x"}],
                       "endpoints": ["GET /a"], "source": "G1", "status": "open"}])
    assert gaps.check_package(ws, "p1", ["GET /a"])["status"] == "PASS"
    # a gap of the other package is not this package's to write
    _write(ws, "p1", [{"id": "GAP-2", "key": "k2", "category": "time", "summary": "b",
                       "evidence": [{"file": "m/b.py", "line": 1, "symbol": "y"}],
                       "endpoints": ["GET /b"], "source": "G1", "status": "open"}])
    errs = gaps.check_package(ws, "p1", ["GET /a"])["errors"]
    assert any("not in this package" in e for e in errs) and any("GAP-1" in e and "missing" in e for e in errs)
    # a package gap without endpoints belongs to the shared file
    _write(ws, "p1", [{"id": "GAP-1", "key": "k1", "category": "time", "summary": "a",
                       "evidence": [{"file": "m/a.py", "line": 1, "symbol": "x"}],
                       "endpoints": [], "source": "G1", "status": "open"}])
    assert any("needs endpoints" in e for e in gaps.check_package(ws, "p1", ["GET /a"])["errors"])


def test_gap_package_shared_takes_what_no_package_owns(tmp_path):
    ws = _workspace(tmp_path)
    _write(ws, "shared", [{"id": "GAP-3", "key": "k3", "category": "process_state", "summary": "s",
                           "evidence": [{"file": "m/c.py", "line": 1, "symbol": "z"}],
                           "endpoints": [], "source": "G1", "status": "open"}])
    assert gaps.check_package(ws, "shared")["status"] == "PASS"
    _write(ws, "shared", [{"id": "GAP-1", "key": "k1", "category": "time", "summary": "a",
                           "evidence": [{"file": "m/a.py", "line": 1, "symbol": "x"}],
                           "endpoints": ["GET /a"], "source": "G1", "status": "open"}])
    assert any("belong to package" in e for e in gaps.check_package(ws, "shared")["errors"])


def test_gap_merge_keeps_ids_and_decisions_and_assigns_new_ids(tmp_path):
    ws = _workspace(tmp_path)
    inv = json.loads((ws / "storage" / "gap_inventory.json").read_text(encoding="utf-8"))
    inv["gaps"][0]["status"] = "decided"
    inv["gaps"][0]["decision"] = {"choice": "keep", "why": "w", "test": "none"}
    (ws / "storage" / "gap_inventory.json").write_text(json.dumps(inv), encoding="utf-8")
    _write(ws, "p1", [{"id": "GAP-1", "key": "k1", "category": "time", "summary": "a2",
                       "evidence": [{"file": "m/a.py", "line": 1, "symbol": "x"}],
                       "endpoints": ["GET /a"], "source": "G1", "status": "open"}])
    _write(ws, "p2", [{"id": "GAP-2", "key": "k2", "category": "time", "summary": "b",
                       "evidence": [{"file": "m/b.py", "line": 1, "symbol": "y"}],
                       "endpoints": ["GET /b"], "source": "G1", "status": "open"}])
    _write(ws, "shared", [
        {"id": "GAP-3", "key": "k3", "category": "process_state", "summary": "s",
         "evidence": [{"file": "m/c.py", "line": 1, "symbol": "z"}], "endpoints": [], "source": "G1", "status": "open"},
        {"key": "g2:new:1", "category": "new", "candidate_name": "连接池", "summary": "n",
         "evidence": [{"file": "m/c.py", "line": 1, "symbol": "z"}], "endpoints": [], "source": "G2", "status": "open"}])
    result = gaps.merge_packages(ws)
    assert result["status"] == "PASS" and result["added"] == 1 and result["updated"] == 3
    merged = json.loads((ws / "storage" / "gap_inventory.json").read_text(encoding="utf-8"))["gaps"]
    by_key = {g["key"]: g for g in merged}
    assert by_key["k1"]["id"] == "GAP-1" and by_key["k1"]["status"] == "decided" and by_key["k1"]["summary"] == "a2"
    assert by_key["g2:new:1"]["id"] == "GAP-4"
    assert len(merged) == 4 and [g["id"] for g in merged] == ["GAP-1", "GAP-2", "GAP-3", "GAP-4"]
