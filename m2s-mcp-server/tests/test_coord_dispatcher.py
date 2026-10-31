"""coord.dispatcher with scripted fake workers (no LLM): the v1 flow and its rules."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

# Each scenario drives a real br board (~0.3 s per call), ~5 minutes in total.
pytestmark = pytest.mark.skipif(not os.environ.get("M2S_RUN_SLOW"), reason="slow; set M2S_RUN_SLOW=1")

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from coord_fakes import FakeGuard, fail_report, make_dispatcher, ok_report  # noqa: E402

from coord.dispatcher import EXIT_DONE, EXIT_HUMAN, main  # noqa: E402
from coord.template import load_template  # noqa: E402

MINI = load_template(path=Path(__file__).resolve().parent / "fixtures" / "coord_mini.yaml")

AGENT_TASKS = ["S0", "S1", "V1", "S2", "S3a", "S3b", "S3c", "S4", "V2", "S5"]


def _spawns(d, task: str) -> int:
    return d.events.count("spawn", task)


def _prompt(d, task: str, attempt: int) -> str:
    return (d.coord / "run" / f"{task}-a{attempt}" / "prompt.md").read_text(encoding="utf-8")


def test_happy_path_runs_every_task_once(tmp_path: Path):
    d = make_dispatcher(tmp_path, {})

    assert d.run() == EXIT_DONE

    statuses = {t: i["status"] for t, i in d.board.tasks().items()}
    assert set(statuses.values()) == {"closed"}
    for task in AGENT_TASKS:
        assert _spawns(d, task) == 1, task
    order = [e["task"] for e in d.events.all() if e["type"] == "spawn"]
    assert order == AGENT_TASKS
    report = (d.coord / "report.md").read_text(encoding="utf-8")
    assert "**done**" in report
    assert "STAGE_ID: S2" in _prompt(d, "S2", 1)


def test_failed_check_feeds_the_next_attempt(tmp_path: Path):
    d = make_dispatcher(tmp_path, {"S2": [fail_report("S2"), ok_report("S2")]}, template=MINI)

    assert d.run() == EXIT_DONE

    assert _spawns(d, "S2") == 2
    second = _prompt(d, "S2", 2)
    assert "fake gate_code failed for S2" in second
    assert "上一次的工作报告全文" in second
    assert "第 2 次尝试" in second


def test_missing_submission_counts_as_a_failed_attempt(tmp_path: Path):
    d = make_dispatcher(tmp_path, {"S1": [{"write": {"storage/stage_reports/S1.json": {"ok": True}}, "submit": None},
                                          ok_report("S1")]}, template=MINI)

    assert d.run() == EXIT_DONE

    assert _spawns(d, "S1") == 2
    assert "没有调用 submit_task" in _prompt(d, "S1", 2)


def test_out_of_scope_writes_are_reverted(tmp_path: Path):
    blueprint = {"write": {"storage/blueprint.json": {"by": "S1"}, "storage/stage_reports/S1.json": {"ok": True}},
                 "submit": "done"}
    trespass = {"write": {"storage/blueprint.json": {"by": "S2"}, "storage/stage_reports/S2.json": {"ok": True}},
                "submit": "done"}
    d = make_dispatcher(tmp_path, {"S1": [blueprint], "S2": [trespass, ok_report("S2")]}, template=MINI)

    assert d.run() == EXIT_DONE

    assert json.loads((d.ws / "storage" / "blueprint.json").read_text(encoding="utf-8")) == {"by": "S1"}
    assert _spawns(d, "S2") == 2
    assert "storage/blueprint.json" in _prompt(d, "S2", 2)


def test_crashes_do_not_use_up_attempts(tmp_path: Path):
    crash = {"submit": None, "exit": 3}
    d = make_dispatcher(tmp_path, {"S1": [crash, crash, ok_report("S1")]}, template=MINI)

    assert d.run() == EXIT_DONE

    assert _spawns(d, "S1") == 3
    assert d.events.attempts("S1") == 1


def test_exhausted_attempts_go_to_a_person_and_resume(tmp_path: Path):
    d = make_dispatcher(tmp_path, {"S2": [fail_report("S2")] * 3 + [ok_report("S2")]}, template=MINI)

    assert d.run() == EXIT_HUMAN
    assert d.board.status("S2") == "needs_human"
    assert _spawns(d, "S2") == 3

    assert main(["resolve", "--ws", str(d.ws), "--task", "S2", "--reopen", "--comment", "try again",
                 "--feedback", "part1 must end with the marker"]) == EXIT_DONE
    resumed = make_dispatcher(tmp_path, {"S2": [ok_report("S2")]}, template=MINI)
    assert resumed.run() == EXIT_DONE
    assert "part1 must end with the marker" in _prompt(resumed, "S2", 4)


def test_exception_handler_can_grant_a_retry(tmp_path: Path):
    script = {"S2": [fail_report("S2")] * 3 + [ok_report("S2")],
              "handler": [{"action": "retry", "feedback": "handler says: export the handler function"}]}
    d = make_dispatcher(tmp_path, script, handler=True, template=MINI)

    assert d.run() == EXIT_DONE

    assert _spawns(d, "S2") == 4
    assert "handler says: export the handler function" in _prompt(d, "S2", 4)
    journal = (d.coord / "journal.md").read_text(encoding="utf-8")
    assert "retry" in journal and "S2" in journal


def test_v1_spec_issues_go_back_to_the_architect(tmp_path: Path):
    issue = {"id": "SI-1", "status": "open", "severity": "non_blocking", "where": "POST /orders", "problem": "ordering"}
    v1_open = {"write": {"verification/test_plan.json": {"spec_issues": [issue]},
                         "storage/stage_reports/V1.json": {"ok": True}}, "submit": "done"}
    v1_done = {"write": {"verification/test_plan.json": {"spec_issues": [{**issue, "status": "resolved"}]},
                         "storage/stage_reports/V1.json": {"ok": True}}, "submit": "done"}
    s1_first = {"write": {"storage/blueprint.json": {"metadata": {}}, "storage/stage_reports/S1.json": {"ok": True}},
                "submit": "done"}
    s1_rules = {"write": {"storage/blueprint.json": {"metadata": {"spec_resolutions": [{"issue_id": "SI-1"}]}},
                          "storage/stage_reports/S1.json": {"ok": True}}, "submit": "done"}
    d = make_dispatcher(tmp_path, {"S1": [s1_first, s1_rules], "V1": [v1_open, v1_done]})

    assert d.run() == EXIT_DONE

    assert _spawns(d, "S1") == 2 and _spawns(d, "V1") == 2
    assert "SI-1" in _prompt(d, "S1", 2)
    assert "V1 回炉" in _prompt(d, "V1", 2)
    order = [e["task"] for e in d.events.all() if e["type"] == "spawn"]
    assert order[:5] == ["S0", "S1", "V1", "S1", "V1"]


def test_v2_routing_reworks_code_and_rechecks_the_rest(tmp_path: Path):
    routing = [{"to": "code", "function": "orders-create", "spec_refs": ["POST /api/orders#business_rules[5]"],
                "expected_per_spec": "only ordered books leave the cart", "observed": "the whole cart is emptied",
                "evidence_cases": ["orders-create-happy-two-books"]}]
    v2_fail = {"write": {"storage/stage_reports/V2.json": {"ok": False, "routing": routing}}, "submit": "blocked",
               "reason": "verdict FAIL"}
    d = make_dispatcher(tmp_path, {"V2": [v2_fail, ok_report("V2")]})

    assert d.run() == EXIT_DONE

    assert _spawns(d, "S2") == 2 and _spawns(d, "V2") == 2
    for task in ("S3a", "S3b", "S3c", "S4"):
        assert _spawns(d, task) == 1, f"{task} should be rechecked, not redone"
    s2 = _prompt(d, "S2", 2)
    assert "only ordered books leave the cart" in s2
    assert "orders-create-happy-two-books" not in s2  # test evidence never reaches the implementation line


def test_worker_can_send_rework_upstream(tmp_path: Path):
    blocked = {"write": {"storage/stage_reports/S3c.json": {"ok": False}}, "submit": "blocked",
               "reason": "C3 fails because handler.js exports nothing", "suspected": "S2"}
    d = make_dispatcher(tmp_path, {"S3c": [blocked, ok_report("S3c")]})

    assert d.run() == EXIT_DONE

    assert _spawns(d, "S2") == 2 and _spawns(d, "S3c") == 2
    assert _spawns(d, "S3a") == 1 and _spawns(d, "S3b") == 1
    assert "handler.js exports nothing" in _prompt(d, "S2", 2)


def test_tool_edits_are_escalated(tmp_path: Path):
    guard = FakeGuard()
    d = make_dispatcher(tmp_path, {}, guard=guard, template=MINI)
    original = d._evaluate

    def evaluate(spec, *args, **kwargs):
        if spec.id == "S1":
            guard.next = {"repo_new": [], "repo_changed": ["m2s-mcp-server/gates.py"], "agents": []}
        return original(spec, *args, **kwargs)

    d._evaluate = evaluate
    assert d.run() == EXIT_HUMAN
    assert d.board.status("S1") == "needs_human"


def test_timeout_kills_the_worker(tmp_path: Path):
    d = make_dispatcher(tmp_path, {"S4": [{"sleep": 30, "submit": None}, ok_report("S4")]}, template=MINI, timeouts={"S4": 0.02})

    assert d.run() == EXIT_DONE

    exits = d.events.of_task("S4", "worker_exit")
    assert exits[0]["timed_out"] is True
    assert "时间上限" in _prompt(d, "S4", 2)


def test_dispatcher_restart_adopts_a_running_worker(tmp_path: Path):
    script = {"S1": [{"sleep": 3, **ok_report("S1")}]}
    d = make_dispatcher(tmp_path, script, template=MINI)
    while "S1" not in d.running:
        d.run(max_cycles=1)
    pid = d.running["S1"].pid

    restarted = make_dispatcher(tmp_path, script, template=MINI)
    assert restarted.run() == EXIT_DONE
    assert restarted.events.last("S1", "adopt")["pid"] == pid
    assert _spawns(restarted, "S1") == 1


def test_dispatcher_restart_after_worker_finished(tmp_path: Path):
    d = make_dispatcher(tmp_path, {}, template=MINI)
    while "S1" not in d.running:
        d.run(max_cycles=1)
    d.running["S1"].proc.wait(30)  # the worker submits while the dispatcher is "dead"

    restarted = make_dispatcher(tmp_path, {}, template=MINI)
    assert restarted.run() == EXIT_DONE
    assert _spawns(restarted, "S1") == 1
