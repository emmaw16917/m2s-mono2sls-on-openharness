"""s5_report: with AWS judging on, every kept change must carry its AWS verdict."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from coord.checks import s5_report  # noqa: E402


def _git(ws: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(ws), *args], check=True, capture_output=True, text=True)


@pytest.fixture()
def ws(tmp_path):
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "storage" / "stage_reports").mkdir(parents=True)
    (tmp_path / "output").mkdir()
    (tmp_path / "output" / "base.txt").write_text("b0\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-m", "b0")
    _git(tmp_path, "tag", "B0")
    return tmp_path


def _spawn(ws: Path, head: str, aws: bool = True) -> None:
    (ws / "coord").mkdir(exist_ok=True)
    event = {"type": "spawn", "task": "O", "attempt": 1, "aws": aws, "head": head}
    (ws / "coord" / "events.jsonl").write_text(json.dumps(event) + "\n", encoding="utf-8")


def _report(ws: Path, change: dict, reported_with: str | None = None) -> None:
    (ws / "storage" / "stage_reports" / "O.json").write_text("{}", encoding="utf-8")
    report = {"changes": [change], **({"judged_with": reported_with} if reported_with else {})}
    (ws / "output" / "perf_report.json").write_text(json.dumps(report), encoding="utf-8")
    _git(ws, "add", "-A")
    _git(ws, "commit", "-m", "one kept change")


def test_without_aws_a_kept_change_needs_only_its_commit(ws, monkeypatch):
    monkeypatch.delenv("M2S_AWS", raising=False)
    _report(ws, {"id": "C1", "verdict": "kept"})
    result = s5_report(ws)
    assert result.ok and "commits since B0" in result.summary


def test_with_aws_a_kept_change_without_an_aws_verdict_fails(ws, monkeypatch):
    monkeypatch.setenv("M2S_AWS", "1")
    _report(ws, {"id": "C1", "verdict": "kept"}, reported_with="aws")
    result = s5_report(ws)
    assert not result.ok and "evidence.aws" in result.relay and "C1" in result.relay


def test_with_aws_an_aws_verdict_that_disagrees_fails(ws, monkeypatch):
    monkeypatch.setenv("M2S_AWS", "1")
    _report(ws, {"id": "C1", "verdict": "kept", "evidence": {"aws": {"verdict": "revert"}}})
    assert not s5_report(ws).ok


def test_with_aws_a_kept_change_with_its_verdict_passes(ws, monkeypatch):
    monkeypatch.setenv("M2S_AWS", "1")
    _report(ws, {"id": "C1", "verdict": "kept", "evidence": {"aws": {"verdict": "kept"}}})
    result = s5_report(ws)
    assert result.ok and "1 with an AWS verdict" in result.summary


def test_an_o_only_rerun_of_an_old_report_still_passes(ws, monkeypatch):
    """The changes kept before AWS mode have no AWS evidence; re-running O on
    that workspace must not demand it retroactively."""
    monkeypatch.setenv("M2S_AWS", "1")
    _report(ws, {"id": "C1", "verdict": "kept", "evidence": {"rie": {"verdict": "kept"}}})
    assert s5_report(ws).ok


def test_changes_kept_before_this_o_attempt_are_exempt(ws, monkeypatch):
    """An O-only rerun over airline-m4 has an old kept commit; the spawn event
    records the HEAD it started from, so that commit is not re-judged."""
    monkeypatch.setenv("M2S_AWS", "1")
    _spawn(ws, "HEAD")
    _report(ws, {"id": "C1", "verdict": "kept", "commit": "B0", "evidence": {"rie": {"verdict": "kept"}}},
            reported_with="aws")
    assert s5_report(ws).ok


def test_a_change_kept_in_this_aws_attempt_needs_its_verdict(ws, monkeypatch):
    monkeypatch.setenv("M2S_AWS", "1")
    _spawn(ws, "B0")
    _report(ws, {"id": "C1", "verdict": "kept", "commit": "HEAD"}, reported_with="aws")
    assert not s5_report(ws).ok
