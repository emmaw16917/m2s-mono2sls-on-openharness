"""Tests for oh_chain.finished: completion is decided by gate(final), not commit messages."""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import oh_chain  # noqa: E402


def _ws(tmp_path, changes):
    (tmp_path / "output").mkdir()
    (tmp_path / "output" / "perf_report.json").write_text(json.dumps({"changes": changes}), encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty",
                    "-m", "stage 6 (not accepted): gate(final) FAIL"], check=True)
    return tmp_path


def test_a_failed_gate_is_not_finished_whatever_the_commit_says(tmp_path, monkeypatch):
    ws = _ws(tmp_path, [])
    monkeypatch.setattr(oh_chain.gates, "run_gate", lambda phase, w: {"status": "FAIL", "checks": [{"check_id": "SR2", "severity": "ERROR"}]})
    done, why = oh_chain.finished(ws, time.time() - 60)
    assert not done and "SR2" in why


def test_gate_pass_with_new_pending_human_is_not_finished(tmp_path, monkeypatch):
    ws = _ws(tmp_path, [{"id": "C3", "verdict": "pending_human"}])
    monkeypatch.setattr(oh_chain.gates, "run_gate", lambda phase, w: {"status": "PASS", "checks": []})
    assert oh_chain.finished(ws, time.time() - 60)[0] is False
    # a pending_human left over from an earlier round (no commits since) does not hold the chain
    assert oh_chain.finished(ws, time.time() + 60)[0] is True


def test_gate_pass_is_finished(tmp_path, monkeypatch):
    ws = _ws(tmp_path, [{"id": "C1", "verdict": "kept"}])
    monkeypatch.setattr(oh_chain.gates, "run_gate", lambda phase, w: {"status": "PASS", "checks": []})
    assert oh_chain.finished(ws, time.time() - 60) == (True, "gate(final) PASS")
