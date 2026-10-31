"""coord.board.TaskBoard against the pinned br and the v3 policy (W1.1)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from coord.board import BrError, TaskBoard  # noqa: E402
from coord.toolchain import ToolchainError, exe_path  # noqa: E402

try:
    exe_path("br")
except ToolchainError:
    pytest.skip("br is not installed (python -m coord.toolchain install)", allow_module_level=True)


@pytest.fixture(scope="module")
def board(tmp_path_factory) -> TaskBoard:
    b = TaskBoard(tmp_path_factory.mktemp("coord"))
    b.init()
    b.add_task({"id": "S1", "runner": "agent", "role": "m2s-architect"})
    b.add_task({"id": "V1", "runner": "agent", "role": "m2s-verifier"}, deps=["S1"])
    return b


def test_dependencies_and_specs(board: TaskBoard):
    assert board.ready() == ["S1"]
    tasks = board.tasks()
    assert tasks["V1"]["spec"]["role"] == "m2s-verifier" and tasks["S1"]["status"] == "open"


def test_close_needs_a_passing_gate(board: TaskBoard):
    board.claim("S1", "architect-S1-a1")
    with pytest.raises(BrError):
        board.close("S1")  # in_progress -> closed is not a transition
    board.set_status("S1", "checking")
    with pytest.raises(BrError, match="done_check"):
        board.close("S1")
    board.report_gate("S1", False)
    with pytest.raises(BrError):
        board.close("S1")
    board.set_status("S1", "rework")
    assert board.ready() == ["S1"]

    board.claim("S1", "architect-S1-a2")  # re-claim by the next attempt's worker
    board.set_status("S1", "checking")
    board.report_gate("S1", True)
    board.close("S1")
    assert board.status("S1") == "closed" and board.ready() == ["V1"]


def test_reopen_reblocks_downstream(board: TaskBoard):
    board.reopen("S1")
    assert board.status("S1") == "open"
    assert board.ready() == ["S1"]  # V1 waits again


def test_needs_human_exits_need_a_comment(board: TaskBoard):
    board.claim("S1", "architect-S1-a3")
    board.set_status("S1", "checking")
    board.set_status("S1", "needs_human")
    with pytest.raises(BrError):
        board.set_status("S1", "open")
    board.set_status("S1", "open", comment="human: retry with the new rule")
    assert board.status("S1") == "open"
    with pytest.raises(BrError):
        board.set_status("S1", "closed")  # terminal states only through close
