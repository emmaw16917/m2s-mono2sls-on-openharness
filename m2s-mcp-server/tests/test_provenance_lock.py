"""F2 parallel annotation (2026-11-24): one fact base, many writers.

v2-split lets the F2 package children run together; the annotate tools
serialize their read-modify-write of analysis_report.json with the workspace
lock storage/.analysis_report.lock. These tests cover the lost-update race
(two real processes), stale-lock reclaim, lock timeout and release on error.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import provenance  # noqa: E402
from coord.checks import check_provenance_pkg  # noqa: E402

MONO = "function auth(req, res, next) {\n  next();\n}\n"
REF = {"file": "mono/server.js", "line": 1, "symbol": "auth"}


def _endpoint(path: str, rule: str) -> dict:
    return {
        "method": "GET", "path": path,
        "handler_location": {"file": "mono/server.js", "start_line": 1, "end_line": 3},
        "auth": {"classification": "none", "middleware": [], "source": [REF], "origin": "script"},
        "request": {"path_params": [{"name": "id", "type": "string", "required": True,
                                     "source": [REF], "origin": "script"}],
                    "query": [], "body": [], "headers": []},
        "responses": [{"status": 200, "kind": "success", "body": {"fields": ["id"], "expression": "res.send"},
                       "source": [REF], "origin": "script"}],
        "data_access": [],
        "business_rules": [{"rule": rule, "kind": "other", "source": [REF], "origin": "llm"}],
    }


def _rules(rule: str) -> list[dict]:
    return [{"rule": rule, "kind": "other", "source": [REF], "origin": "llm"}]


def _workspace(tmp_path: Path) -> Path:
    mono = tmp_path / "mono"
    mono.mkdir()
    (mono / "server.js").write_text(MONO, encoding="utf-8")
    ws = tmp_path / "ws"
    (ws / "storage").mkdir(parents=True)
    report = {"spec_meta": {"monolith_root": str(mono)},
              "entry_points": [_endpoint("/things/one", "rule one"),
                               _endpoint("/things/two", "rule two")]}
    (ws / "storage" / "analysis_report.json").write_text(json.dumps(report), encoding="utf-8")
    return ws


def _lock_path(ws: Path) -> Path:
    return ws / "storage" / ".analysis_report.lock"


def _report(ws: Path) -> dict:
    return json.loads((ws / "storage" / "analysis_report.json").read_text(encoding="utf-8"))


def _write_lock(ws: Path, pid: int, expires: float, owner: str = "test") -> Path:
    path = _lock_path(ws)
    path.write_text(json.dumps({"pid": pid, "owner": owner, "task": "annotate", "expires": expires}),
                    encoding="utf-8")
    return path


# One annotate call in its own process, with a deliberate pause between the
# load and the save so two processes overlap even on a fast machine.
ANNOTATE_PROCESS = r"""
import json, sys, time
sys.path.insert(0, sys.argv[1])
import provenance
original = provenance._validate_field
def slow(*args, **kwargs):
    time.sleep(0.4)
    return original(*args, **kwargs)
provenance._validate_field = slow
cfg = json.loads(sys.argv[2])
print(json.dumps(provenance.annotate_entry_point(
    cfg["ws"], "GET", cfg["path"], "business_rules", json.dumps(cfg["rules"]))))
"""


def _start_annotator(ws: Path, path: str, rule: str) -> subprocess.Popen:
    cfg = json.dumps({"ws": str(ws), "path": path, "rules": _rules(rule)})
    return subprocess.Popen(
        [sys.executable, "-c", ANNOTATE_PROCESS, str(Path(__file__).resolve().parent.parent), cfg],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
    )


def test_two_annotators_on_different_endpoints_both_land(tmp_path):
    """The loss this prevents: process A and B each load the report, then A
    saves and B overwrites A's field with its older copy."""
    ws = _workspace(tmp_path)
    first = _start_annotator(ws, "/things/one", "one changed")
    second = _start_annotator(ws, "/things/two", "two changed")
    out1, err1 = first.communicate(timeout=120)
    out2, err2 = second.communicate(timeout=120)
    assert first.returncode == 0, err1
    assert second.returncode == 0, err2
    assert json.loads(out1.strip().splitlines()[-1])["ok"] is True
    assert json.loads(out2.strip().splitlines()[-1])["ok"] is True

    rules = {ep["path"]: ep["business_rules"][0]["rule"] for ep in _report(ws)["entry_points"]}
    assert rules == {"/things/one": "one changed", "/things/two": "two changed"}
    assert not _lock_path(ws).exists()
    assert not list((ws / "storage").glob("*.tmp"))

    # The checks the dispatcher runs on F2 children still pass afterwards.
    package = check_provenance_pkg(ws, {"endpoints": ["GET /things/one", "GET /things/two"]})
    assert package.ok, package.relay
    full = provenance.check_provenance(ws)
    assert full["status"] == "PASS", full["errors"]


def test_dead_holder_lock_is_reclaimed(tmp_path):
    ws = _workspace(tmp_path)
    _write_lock(ws, pid=999999, expires=time.time() + 3600)
    result = provenance.annotate_entry_point(ws, "GET", "/things/one", "business_rules",
                                             json.dumps(_rules("after crash")))
    assert result["ok"] is True, result
    assert not _lock_path(ws).exists()


def test_expired_lock_is_reclaimed(tmp_path):
    ws = _workspace(tmp_path)
    _write_lock(ws, pid=os.getpid(), expires=1)
    result = provenance.annotate_entry_point(ws, "GET", "/things/one", "business_rules",
                                             json.dumps(_rules("after ttl")))
    assert result["ok"] is True, result


def test_lock_timeout_raises_and_writes_nothing(tmp_path, monkeypatch):
    ws = _workspace(tmp_path)
    lock = _write_lock(ws, pid=os.getpid(), expires=time.time() + 3600, owner="someone-else")
    before = (ws / "storage" / "analysis_report.json").read_bytes()
    monkeypatch.setattr(provenance, "LOCK_WAIT_S", 0.15)
    with pytest.raises(provenance.SpecError, match="timed out"):
        provenance.annotate_entry_point(ws, "GET", "/things/one", "business_rules",
                                        json.dumps(_rules("never written")))
    assert (ws / "storage" / "analysis_report.json").read_bytes() == before
    assert lock.exists()  # not ours: the release must leave it alone


def test_empty_lock_is_not_stolen_while_its_writer_fills_it(tmp_path, monkeypatch):
    """A lock is created with O_EXCL and then filled; a waiter must not treat
    the empty instant as debris (that would give two holders)."""
    ws = _workspace(tmp_path)
    lock = _lock_path(ws)
    lock.write_text("", encoding="utf-8")
    monkeypatch.setattr(provenance, "LOCK_WAIT_S", 0.2)
    with pytest.raises(provenance.SpecError, match="timed out"):
        provenance.annotate_entry_point(ws, "GET", "/things/one", "business_rules", json.dumps(_rules("x")))
    assert lock.exists()


def test_old_garbage_lock_is_reclaimed(tmp_path):
    ws = _workspace(tmp_path)
    lock = _lock_path(ws)
    lock.write_text("not json", encoding="utf-8")
    os.utime(lock, (time.time() - 5, time.time() - 5))
    result = provenance.annotate_entry_point(ws, "GET", "/things/one", "business_rules",
                                             json.dumps(_rules("after debris")))
    assert result["ok"] is True, result
    assert not lock.exists()


def test_lock_is_released_when_validation_rejects(tmp_path):
    ws = _workspace(tmp_path)
    result = provenance.annotate_entry_point(ws, "GET", "/things/one", "business_rules",
                                             json.dumps([{"rule": "missing kind/source/origin"}]))
    assert result["ok"] is False
    assert not _lock_path(ws).exists()


def test_lock_is_released_when_the_save_fails(tmp_path, monkeypatch):
    ws = _workspace(tmp_path)

    def boom(*_args, **_kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(provenance, "_save", boom)
    with pytest.raises(RuntimeError, match="disk full"):
        provenance.annotate_entry_point(ws, "GET", "/things/one", "business_rules", json.dumps(_rules("x")))
    assert not _lock_path(ws).exists()

    monkeypatch.undo()
    result = provenance.annotate_entry_point(ws, "GET", "/things/one", "business_rules",
                                             json.dumps(_rules("after the failure")))
    assert result["ok"] is True, result
