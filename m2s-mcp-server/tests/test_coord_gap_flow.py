"""The gap line and V4's routes with scripted fake workers (PIPELINE_V2_PLAN.md §4.11, §4.13,
N4.3, N4.6): an undecided gap stops A2, and every route of §4.13 goes where it belongs."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(not os.environ.get("M2S_RUN_SLOW"), reason="slow; set M2S_RUN_SLOW=1")

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from coord_fakes import make_dispatcher, ok_report  # noqa: E402

from coord.checks import CheckResult  # noqa: E402
from coord.dispatcher import EXIT_DONE, EXIT_HUMAN  # noqa: E402
from coord.template import load_template  # noqa: E402

GAPS = load_template(path=Path(__file__).resolve().parent / "fixtures" / "coord_gaps.yaml")


class Target:
    """Fake V1 / V2 actions: each call answers the next scripted result, then PASS."""

    def __init__(self, adapter: list[dict] | None = None, judge: list[dict] | None = None):
        self.adapter, self.judge = adapter or [], judge or []
        self.adapter_calls = self.judge_calls = 0

    def check_adapter(self, spec) -> CheckResult:
        detail = self.adapter[self.adapter_calls] if self.adapter_calls < len(self.adapter) else {"status": "PASS"}
        self.adapter_calls += 1
        return CheckResult("check_adapter", detail["status"] == "PASS", f"fake {detail['status']}", detail=detail)

    def run_judge(self, spec) -> CheckResult:
        detail = self.judge[self.judge_calls] if self.judge_calls < len(self.judge) else {"status": "PASS"}
        self.judge_calls += 1
        return CheckResult("run_judge", detail["status"] == "PASS", f"fake {detail['status']}", detail=detail)


def gaps_dispatcher(tmp_path: Path, script: dict, target: Target | None = None, handler: bool = False, tag=None):
    target = target or Target()
    d = make_dispatcher(tmp_path, script, template=GAPS, handler=handler,
                        actions={"gap_scan": lambda spec: CheckResult("gap_scan", True, "fake gap_scan"),
                                 "check_adapter": target.check_adapter, "run_judge": target.run_judge,
                                 "tag_b0": tag or (lambda spec: CheckResult("tag_b0", True, "fake tag"))})
    return d, target


def _spawns(d, task: str) -> int:
    return d.events.count("spawn", task)


def _prompt(d, task: str, attempt: int) -> str:
    return (d.coord / "run" / f"{task}-a{attempt}" / "prompt.md").read_text(encoding="utf-8")


def v3_report(*routing: dict, ok: bool = False) -> dict:
    return {"write": {"storage/stage_reports/V3.json": {"ok": ok, "routing": list(routing)}}, "submit": "blocked" if not ok else "done"}


ROUTE = {"function": "orders-create", "spec_refs": ["blueprint:x"], "expected_per_spec": "只加一次", "observed": "加了两次",
         "evidence_cases": ["delta-secret-case-name"]}


def test_every_task_of_the_line_passes_and_b0_is_tagged(tmp_path: Path):
    d, target = gaps_dispatcher(tmp_path, {})

    assert d.run() == EXIT_DONE

    assert [t.id for t in GAPS.tasks if d.board.status(t.id) != "closed"] == []
    assert (target.adapter_calls, target.judge_calls) == (1, 1)


@pytest.mark.parametrize("to,target,others", [("code", "I1", ["A1", "D1", "I2"]), ("template", "I2", ["A1", "D1", "I1"]),
                                              ("blueprint", "A1", ["D1", "I1", "I2"]), ("assets", "D1", ["A1", "I1", "I2"])])
def test_v3_routes(tmp_path: Path, to: str, target: str, others: list[str]):
    script = {"V3": [v3_report({"to": to, **ROUTE}), ok_report("V3")]}
    d, _ = gaps_dispatcher(tmp_path, script)

    assert d.run() == EXIT_DONE

    assert _spawns(d, target) == 2 and _spawns(d, "V3") == 2
    assert all(_spawns(d, o) == 1 for o in others)
    feedback = _prompt(d, target, 2)
    assert "加了两次" in feedback
    # the implementation line gets the behaviour, not the tests; D1 gets the evidence too
    assert ("delta-secret-case-name" in feedback) == (target == "D1")


def test_v3_undetermined_opens_a_case_and_the_handler_decides(tmp_path: Path):
    script = {"V3": [v3_report({"to": "undetermined", **ROUTE}), ok_report("V3")],
              "handler": [{"action": "reroute", "target": "I1", "feedback": "orders-create 要只加一次"}]}
    d, _ = gaps_dispatcher(tmp_path, script, handler=True)

    assert d.run() == EXIT_DONE

    assert d.events.count("handler_spawn", "V3") == 1
    assert _spawns(d, "I1") == 2 and "orders-create 要只加一次" in _prompt(d, "I1", 2)


def test_v3_without_a_handler_goes_to_a_person(tmp_path: Path):
    d, _ = gaps_dispatcher(tmp_path, {"V3": [v3_report({"to": "undetermined", **ROUTE})]})

    assert d.run() == EXIT_HUMAN

    assert d.board.status("V3") == "needs_human"


def test_v3_routes_stop_at_the_rework_budget(tmp_path: Path):
    d, _ = gaps_dispatcher(tmp_path, {"V3": [v3_report({"to": "code", **ROUTE})]})

    assert d.run() == EXIT_HUMAN

    assert _spawns(d, "I1") == 1 + GAPS.max_rework_rounds and d.board.status("V3") == "needs_human"


def test_v1_static_map_error_goes_to_the_architect(tmp_path: Path):
    target = Target(adapter=[{"status": "ERROR", "error": "the adapter map does not pass its check: X", "map_error": True}])
    d, target = gaps_dispatcher(tmp_path, {}, target)

    assert d.run() == EXIT_DONE

    assert _spawns(d, "A1") == 2 and "the adapter map does not pass its check: X" in _prompt(d, "A1", 2)
    assert target.adapter_calls == 2


def test_v1_read_difference_opens_a_case(tmp_path: Path):
    target = Target(adapter=[{"status": "FAIL", "failed": ["c1"], "failures": {"c1": {"s1": [{"path": "steps.s1.body"}]}}}])
    script = {"handler": [{"action": "reroute", "target": "I1", "feedback": "GET /api/books 读错了字段"}]}
    d, target = gaps_dispatcher(tmp_path, script, target, handler=True)

    assert d.run() == EXIT_DONE

    case = d.events.of_task("V1", "handler_spawn")[0]
    assert "steps.s1.body" in Path(case["case_file"]).read_text(encoding="utf-8")
    assert _spawns(d, "I1") == 2 and _spawns(d, "A1") == 1 and target.adapter_calls == 2


@pytest.mark.parametrize("failure", [
    {"case": "c1", "error": "Error: function orders-create could not be started: boom"},
    {"case": "c1", "steps": {"s1": {"function": "orders-create", "verdict": "fail",
                                    "lambda_error": {"type": "Runtime.ImportModuleError", "message": "Cannot find module 'uuid'"}}}},
])
def test_v2_function_that_cannot_start_goes_to_packaging(tmp_path: Path, failure: dict):
    target = Target(judge=[{"status": "FAIL", "failures": [failure, {"case": "c2", "steps": {"s1": {"verdict": "fail"}}}]}])
    d, target = gaps_dispatcher(tmp_path, {}, target)

    assert d.run() == EXIT_DONE

    assert _spawns(d, "I2") == 2 and _spawns(d, "I1") == 1
    assert "函数启动不了" in _prompt(d, "I2", 2)
    assert d.events.count("handler_spawn", "V2") == 0 and target.judge_calls == 2


def test_v2_build_failure_goes_to_packaging(tmp_path: Path):
    target = Target(judge=[{"status": "ERROR", "error": "sam build failed (exit 1), a packaging problem"}])
    d, _ = gaps_dispatcher(tmp_path, {}, target)

    assert d.run() == EXIT_DONE

    assert _spawns(d, "I2") == 2


@pytest.mark.parametrize("to", ["I1", "I2", "A1"])
def test_v2_other_failures_open_a_case_that_cannot_change_the_judge(tmp_path: Path, to: str):
    target = Target(judge=[{"status": "FAIL", "failures": [{"case": "c1", "steps": {"s1": {"verdict": "fail", "mismatches": [
        {"path": "steps.s1.body.total", "expected": 3, "actual": 2}]}}}], "failures_total": 1}])
    script = {"handler": [{"action": "reroute", "target": to, "feedback": "total 少算了一项"}]}
    d, target = gaps_dispatcher(tmp_path, script, target, handler=True)
    (d.ws / "judge").mkdir()
    (d.ws / "judge" / "judge_manifest.json").write_text("{}", encoding="utf-8")
    jobs = []
    start = d.launcher.start
    d.launcher.start = lambda job: (jobs.append(job), start(job))[1]

    assert d.run() == EXIT_DONE

    case = d.events.of_task("V2", "handler_spawn")[0]
    text = Path(case["case_file"]).read_text(encoding="utf-8")
    assert "steps.s1.body.total" in text and "分诊不能改裁判" in text
    assert _spawns(d, to) == 2 and "total 少算了一项" in _prompt(d, to, 2)
    handler = next(j for j in jobs if j.role == "handler")
    assert handler.deny_write == (str(d.ws / "judge"),)


def _b0_workspace(d, judge_ok: str = "PASS", delta_ok: str = "PASS") -> None:
    v = d.ws / "verification"
    v.mkdir(exist_ok=True)
    (v / "verdict_judge.json").write_text(json.dumps({"status": judge_ok}), encoding="utf-8")
    (v / "verdict_delta.json").write_text(json.dumps({"status": delta_ok}), encoding="utf-8")
    for name in ("output", "storage", "judge"):
        (d.ws / name).mkdir(exist_ok=True)
        (d.ws / name / "x.txt").write_text(name, encoding="utf-8")


def _real_tag_dispatcher(tmp_path: Path, changes):
    import judge

    holder = {}
    judge_changes = judge.judge_changes
    judge.judge_changes = lambda ws: changes

    def tag(spec):
        return holder["d"]._act_tag_b0(spec)

    d, _ = gaps_dispatcher(tmp_path, {}, tag=tag)
    holder["d"] = d
    return d, lambda: setattr(judge, "judge_changes", judge_changes)


def test_tag_b0_when_everything_passes(tmp_path: Path):
    d, restore = _real_tag_dispatcher(tmp_path, {"changed": [], "added": [], "removed": []})
    try:
        _b0_workspace(d)
        assert d.run() == EXIT_DONE
    finally:
        restore()
    tags = subprocess.run(["git", "-C", str(d.ws), "tag", "--list"], capture_output=True, text=True).stdout.split()
    assert tags == ["B0"] and d.events.count("tag_b0", "B0") == 1
    files = subprocess.run(["git", "-C", str(d.ws), "ls-tree", "-r", "--name-only", "B0"], capture_output=True, text=True).stdout
    assert "judge/x.txt" in files and "verification/verdict_delta.json" in files and "coord/" not in files


@pytest.mark.parametrize("judge_ok,delta_ok,changes", [
    ("FAIL", "PASS", {"changed": []}), ("PASS", "FAIL", {"changed": []}), ("PASS", "PASS", {"changed": ["judge/cases/a.json"]}),
    ("PASS", "PASS", None)])
def test_no_b0_unless_everything_passes(tmp_path: Path, judge_ok: str, delta_ok: str, changes):
    d, restore = _real_tag_dispatcher(tmp_path, changes)
    try:
        _b0_workspace(d, judge_ok, delta_ok)
        assert d.run() == EXIT_HUMAN
    finally:
        restore()
    assert d.board.status("B0") == "needs_human"
    assert not (d.ws / ".git").exists()
