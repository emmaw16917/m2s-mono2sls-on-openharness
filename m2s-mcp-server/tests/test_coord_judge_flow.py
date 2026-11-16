"""The J3 flow with scripted fake workers (PIPELINE_V2_PLAN.md §4.5, N1.5): the
four classes of differences, the round limit, and run_monolith sending J1 back."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(not os.environ.get("M2S_RUN_SLOW"), reason="slow; set M2S_RUN_SLOW=1")

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from coord_fakes import make_dispatcher, ok_report  # noqa: E402

from coord.checks import CHECKS, CheckResult  # noqa: E402
from coord.dispatcher import EXIT_DONE, EXIT_HUMAN  # noqa: E402
from coord.template import load_template  # noqa: E402

JUDGE = load_template(path=Path(__file__).resolve().parent / "fixtures" / "coord_judge.yaml")


def diff(case: str, check: str = "status", predicted=201, actual=200) -> dict:
    return {"id": f"{case}/s1/{check}", "case": case, "step": "s1", "check": check, "predicted": predicted,
            "actual": actual, "fact_refs": [f"POST /api/{case}#responses[0]"], "request": {"method": "POST", "path": f"/api/{case}"}}


def classify(classes: dict[str, str], mask_rules: bool = False) -> dict:
    files = {"judge/reconcile_classes.json": {i: {"class": c, "reason": f"evidence for {c}"} for i, c in classes.items()},
             "storage/stage_reports/J3.json": {"ok": True}}
    if mask_rules:
        files["judge/mask_rules.json"] = [{"path": "steps.*.body.date", "reason": "a date, same in both passes", "class": "time"}]
    return {"write": files, "submit": "done"}


class Calls:
    """Fake run_monolith / reconcile_spec / freeze_judge actions that count their calls."""

    def __init__(self, ws: Path, rounds: list[list[dict]], monolith: list[dict] | None = None):
        self.ws, self.rounds, self.monolith = ws, rounds, monolith or [{"status": "PASS"}]
        self.reconcile = self.run = self.freeze = 0

    def run_monolith(self, spec) -> CheckResult:
        detail = self.monolith[min(self.run, len(self.monolith) - 1)]
        self.run += 1
        return CheckResult("run_monolith", detail["status"] == "PASS", f"fake run_monolith {detail['status']}", detail=detail)

    def reconcile_spec(self, spec) -> CheckResult:
        diffs = self.rounds[min(self.reconcile, len(self.rounds) - 1)]
        self.reconcile += 1
        jd = self.ws / "judge"
        jd.mkdir(parents=True, exist_ok=True)
        (jd / "reconcile.json").write_text(json.dumps({"differences": diffs, "resolved_by_rulings": []}), encoding="utf-8")
        (jd / "reconcile_classes.json").unlink(missing_ok=True)
        return CheckResult("reconcile_spec", True, f"fake: {len(diffs)} differences")

    def freeze_judge(self, spec) -> CheckResult:
        self.freeze += 1
        return CheckResult("freeze_judge", True, "fake freeze")


def judge_dispatcher(tmp_path: Path, script: dict, rounds: list[list[dict]], monolith: list[dict] | None = None):
    calls = Calls(tmp_path / "ws", rounds, monolith)
    d = make_dispatcher(tmp_path, script, template=JUDGE,
                        actions={"run_monolith": calls.run_monolith, "reconcile_spec": calls.reconcile_spec,
                                 "freeze_judge": calls.freeze_judge})
    d.checks["reconcile_classified"] = CHECKS["reconcile_classified"]  # the real check: it reads judge/
    report = d.ws / "storage" / "stage_reports" / "F2.json"  # the fact base copied from an earlier run passes F3
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps({"ok": True}), encoding="utf-8")
    return d, calls


def _spawns(d, task: str) -> int:
    return d.events.count("spawn", task)


def _prompt(d, task: str, attempt: int) -> str:
    return (d.coord / "run" / f"{task}-a{attempt}" / "prompt.md").read_text(encoding="utf-8")


def test_each_class_goes_where_it_belongs(tmp_path: Path):
    fact, case, mask, rule = diff("c1"), diff("c2", "body.equals", {"a": 1}, {"a": 2}), diff("c3", "body.subset"), diff("c4")
    ruling = {"question": "c4 answers 200 where the facts say 201", "conclusion": "keep the monolith's 200",
              "category": "contract", "basis": "routes/c4.js", "affected_cases": ["c4"]}
    script = {"J3": [classify({fact["id"]: "fact_error", case["id"]: "case_error", mask["id"]: "mask_gap",
                               rule["id"]: "needs_ruling"}, mask_rules=True)],
              "RULE_J3_1": [{**ok_report("RULE_J3_1"), "rulings": [ruling]}]}
    d, calls = judge_dispatcher(tmp_path, script, rounds=[[fact, case, mask, rule], []])

    assert d.run() == EXIT_DONE

    assert _spawns(d, "F2") == 1  # the copied fact base passed at the start; one correction
    assert _spawns(d, "J1") == 2 and _spawns(d, "J3") == 1 and _spawns(d, "RULE_J3_1") == 1
    assert (calls.run, calls.reconcile, calls.freeze) == (2, 2, 1)
    assert fact["id"] in _prompt(d, "F2", 1) and "annotate_entry_point" in _prompt(d, "F2", 1)
    j1 = _prompt(d, "J1", 2)
    assert fact["id"] in j1 and case["id"] in j1 and "只更新这些用例的 predicted" in j1
    assert rule["id"] in _prompt(d, "RULE_J3_1", 1) and "MODE: ruling" in _prompt(d, "RULE_J3_1", 1)
    rounds = d.events.of_task("J3", "reconcile_round")
    assert len(rounds) == 1 and rounds[0]["counts"] == {"fact_error": 1, "needs_ruling": 1, "case_error": 1, "mask_gap": 1}
    saved = json.loads((d.ws / "storage" / "rulings.json").read_text(encoding="utf-8"))
    assert [r["affects"]["cases"] for r in saved["rulings"]] == [["c4"]]
    # J6 waited for the ruling task
    order = [e["task"] for e in d.events.all() if e["type"] in ("spawn", "action_start")]
    assert order.index("RULE_J3_1") < order.index("J6")


def test_a_mask_gap_alone_reruns_the_monolith(tmp_path: Path):
    mask = diff("c3", "body.subset")
    d, calls = judge_dispatcher(tmp_path, {"J3": [classify({mask["id"]: "mask_gap"}, mask_rules=True)]},
                                rounds=[[mask], []])

    assert d.run() == EXIT_DONE

    assert _spawns(d, "J1") == 1 and _spawns(d, "F2") == 0 and (calls.run, calls.reconcile) == (2, 2)


def test_a_classification_without_mask_rules_fails_its_check(tmp_path: Path):
    mask = diff("c3", "body.subset")
    script = {"J3": [classify({mask["id"]: "mask_gap"}), classify({mask["id"]: "mask_gap"}, mask_rules=True)]}
    d, calls = judge_dispatcher(tmp_path, script, rounds=[[mask], []])

    assert d.run() == EXIT_DONE

    assert _spawns(d, "J3") == 2 and "judge/mask_rules.json" in _prompt(d, "J3", 2)


def test_after_two_rounds_a_person_decides(tmp_path: Path):
    case = diff("c2", "body.equals", {"a": 1}, {"a": 2})
    d, calls = judge_dispatcher(tmp_path, {"J3": [classify({case["id"]: "case_error"})]}, rounds=[[case]])

    assert d.run() == EXIT_HUMAN

    assert d.board.status("HUMAN_RECONCILE") == "needs_human"
    assert _spawns(d, "J1") == 3 and _spawns(d, "J3") == 3 and calls.reconcile == 3
    assert len(d.events.of_task("J3", "reconcile_round")) == 2 and calls.freeze == 0


def test_run_monolith_failing_on_the_cases_sends_j1_back(tmp_path: Path):
    problem = {"status": "FAIL", "problems": [{"unit": "c9", "why": "preparing identity u1 failed", "step": {"status": 400}}]}
    d, calls = judge_dispatcher(tmp_path, {}, rounds=[[]], monolith=[problem, {"status": "PASS"}])

    assert d.run() == EXIT_DONE

    assert _spawns(d, "J1") == 2 and calls.run == 2
    assert "preparing identity u1 failed" in _prompt(d, "J1", 2)
    assert _spawns(d, "J3") == 0  # nothing to classify: the recheck closed it


def test_after_the_freeze_nobody_changes_the_judge(tmp_path: Path):
    """N1.6: a task after J6 edits a case (its scope would allow it): the attempt
    fails, the edit is undone, and the next attempt is told why."""
    from dataclasses import replace

    import judge
    from coord.template import TaskSpec

    later = TaskSpec(id="A1", role="m2s-architect", deps=("J6",), scope=("judge/**", "storage/stage_reports/A1.json"))
    template = replace(JUDGE, tasks=JUDGE.tasks + (later,))
    ws = tmp_path / "ws"
    case = ws / "judge" / "cases" / "post-api-x" / "c1.json"
    case.parent.mkdir(parents=True)
    case.write_text('{"original": true}', encoding="utf-8")
    calls = Calls(ws, [[]])

    def freeze(spec) -> CheckResult:  # the manifest the real freeze writes, without its preconditions
        judge._write(ws / "judge" / judge.MANIFEST, {"version": 1, "files": judge.judge_files(ws)})
        return CheckResult("freeze_judge", True, "fake freeze")

    tamper = {"write": {"judge/cases/post-api-x/c1.json": {"tampered": True}, "storage/stage_reports/A1.json": {"ok": True}},
              "submit": "done"}
    d = make_dispatcher(tmp_path, {"A1": [tamper, ok_report("A1")]}, template=template,
                        actions={"run_monolith": calls.run_monolith, "reconcile_spec": calls.reconcile_spec,
                                 "freeze_judge": freeze})
    d.checks["reconcile_classified"] = CHECKS["reconcile_classified"]
    report = ws / "storage" / "stage_reports" / "F2.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps({"ok": True}), encoding="utf-8")

    assert d.run() == EXIT_DONE

    assert _spawns(d, "A1") == 2
    assert json.loads(case.read_text(encoding="utf-8")) == {"original": True}
    assert "裁判已经冻结" in _prompt(d, "A1", 2)
    first = next(e for e in d.events.of_task("A1", "checks"))
    assert {r["name"]: r["ok"] for r in first["results"]}["judge_unchanged"] is False
