"""v3: a phase review may send an earlier stage back, and a rework cancels
running downstream work.

bookstore-m2's TAG_V0 review found a template packaging defect; the review
decision (reroute to S3c) used to be logged as "continue" and S5 started on the
broken template. These tests pin both behaviours.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from coord_fakes import make_dispatcher  # noqa: E402

from coord.checks import CheckResult  # noqa: E402
from coord.handler import Case  # noqa: E402
from coord.template import load_template  # noqa: E402

TEMPLATE = """\
name: review-test
tasks:
  - id: A
    role: fake
    scope: [storage/stage_reports/A.json]
    check: [ok]
  - id: TAG_V0
    runner: dispatcher
    action: tag_v0
    deps: [A]
    scope: []
    check: []
  - id: B
    role: fake
    deps: [TAG_V0]
    scope: [storage/stage_reports/B.json]
    check: [ok]
"""


class DummyHandle:
    def __init__(self) -> None:
        self.killed = False
        self.kind = "task"

    def kill(self) -> None:
        self.killed = True


def _ok(ws: Path, spec: dict) -> CheckResult:
    return CheckResult("ok", True, "ok")


def _dispatcher(tmp_path: Path):
    path = tmp_path / "template.yaml"
    path.write_text(TEMPLATE, encoding="utf-8")
    template = load_template(path=path)
    d = make_dispatcher(tmp_path, {}, template=template, checks={"ok": _ok})
    d.setup()
    return d


def _close(d, task: str) -> None:
    d.board.claim(task, "test")
    d.board.set_status(task, "checking")
    d.board.report_gate(task, True)
    d.board.close(task)


def test_review_reroute_reopens_the_target(tmp_path):
    d = _dispatcher(tmp_path)
    for task in ("A", "TAG_V0", "B"):
        _close(d, task)
    case_dir = d.coord / "cases"
    case_dir.mkdir(parents=True, exist_ok=True)
    decision = case_dir / "C001-review.decision.json"
    decision.write_text(json.dumps({"action": "reroute", "target": "A", "feedback": "fix the template"}), encoding="utf-8")
    case = Case("C001-review", "review", "TAG_V0", decision)

    d._on_handler_exit(SimpleNamespace(meta={"case": case}), timed_out=False)

    assert d.board.status("A") == "open"       # sent back and ready to rerun
    assert d.board.status("TAG_V0") == "open"  # it reruns after the target
    assert d.board.status("B") == "open"       # closed downstream went stale


def test_rework_cancels_a_running_downstream_task(tmp_path):
    d = _dispatcher(tmp_path)
    _close(d, "A")
    _close(d, "TAG_V0")
    d.board.claim("B", "test")
    handle = DummyHandle()
    d.running["B"] = handle

    d._make_stale(["A"], by="test")

    assert handle.killed is True
    assert "B" not in d.running
    assert d.board.status("B") == "open"
