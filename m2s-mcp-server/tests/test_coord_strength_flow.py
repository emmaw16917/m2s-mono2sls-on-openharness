"""The J4/J5 flow with scripted fake workers (PIPELINE_V2_PLAN.md §4.10, N3.2-N3.3):
a rule J4 finds in the code goes to F2 as a spec issue (not only into a case),
new cases make J2 run the monolith again, and mutants classified as gaps go back
to J4 for a second round."""

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

import judge  # noqa: E402
from coord.checks import CHECKS, CheckResult  # noqa: E402
from coord.dispatcher import EXIT_DONE  # noqa: E402
from coord.template import load_template  # noqa: E402

STRENGTH = load_template(path=Path(__file__).resolve().parent / "fixtures" / "coord_judge_strength.yaml")
ISSUE = {"id": "J4-1", "endpoint": "POST /api/x", "kind": "missing", "rule": "a quantity string is stored as it is",
         "evidence": [{"file": "mono/routes/x.js", "line": 12}], "cases": ["wb-x-string"]}
GAP = "negate:routes/x.js:3"


class Calls:
    """Fake J2 / RC / J5 / J6 actions. run_monolith records the inputs it ran (as the
    real one does in oracle_expected.json); run_mutants returns survivors per round."""

    def __init__(self, ws: Path, survivors: list[list[str]]):
        self.ws, self.survivors = ws, survivors
        self.run = self.mutants = self.freeze = 0

    def run_monolith(self, spec) -> CheckResult:
        self.run += 1
        judge._write(self.ws / "judge" / "oracle_expected.json", {"meta": {"inputs_sha": judge.inputs_sha(self.ws)}, "cases": {}})
        return CheckResult("run_monolith", True, "fake run_monolith")

    def reconcile_spec(self, spec) -> CheckResult:
        judge._write(self.ws / "judge" / "reconcile.json", {"differences": [], "resolved_by_rulings": []})
        return CheckResult("reconcile_spec", True, "fake: 0 differences")

    def run_mutants(self, spec) -> CheckResult:
        left = self.survivors[min(self.mutants, len(self.survivors) - 1)]
        self.mutants += 1
        rows = [{"id": m, "operator": m.split(":")[0], "outcome": "survived", "file": "routes/x.js", "line": 3,
                 "before": "if (a) {", "after": "if (!(a)) {"} for m in left]
        rows.append({"id": "serial", "operator": "serial", "reverse": True, "outcome": "survived"})
        judge._write(self.ws / "judge" / "judge_validation.json", {"summary": {"reverse_ok": True}, "mutants": rows})
        return CheckResult("run_mutants", True, f"fake: {len(left)} survivors")

    def freeze_judge(self, spec) -> CheckResult:
        self.freeze += 1
        return CheckResult("freeze_judge", True, "fake freeze")


def strength_dispatcher(tmp_path: Path, script: dict, survivors: list[list[str]]):
    calls = Calls(tmp_path / "ws", survivors)
    d = make_dispatcher(tmp_path, script, template=STRENGTH,
                        actions={"run_monolith": calls.run_monolith, "reconcile_spec": calls.reconcile_spec,
                                 "run_mutants": calls.run_mutants, "freeze_judge": calls.freeze_judge})
    d.checks["reconcile_classified"] = CHECKS["reconcile_classified"]
    d.checks["mutant_classes"] = CHECKS["mutant_classes"]  # the real check: it reads judge/
    report = d.ws / "storage" / "stage_reports" / "F2.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps({"ok": True}), encoding="utf-8")
    return d, calls


def _spawns(d, task: str) -> int:
    return d.events.count("spawn", task)


def _prompt(d, task: str, attempt: int) -> str:
    return (d.coord / "run" / f"{task}-a{attempt}" / "prompt.md").read_text(encoding="utf-8")


def j4(files: dict) -> dict:
    return {"write": {**files, "storage/stage_reports/J4.json": {"ok": True}}, "submit": "done"}


def test_a_rule_j4_finds_in_the_code_goes_to_f2_and_the_monolith_runs_again(tmp_path: Path):
    script = {"J1": [ok_report("J1")],
              "J4": [j4({"judge/cases/post-api-x/wb-x-string.json": {"spec_issues": ["J4-1"]},
                         "judge/spec_issues.json": [ISSUE]})],
              "F2": [ok_report("F2")]}
    d, calls = strength_dispatcher(tmp_path, script, survivors=[[]])

    assert d.run() == EXIT_DONE

    assert _spawns(d, "F2") == 1  # passed its recheck at the start, then the spec issue
    f2 = _prompt(d, "F2", 1)
    assert "J4-1" in f2 and "annotate_entry_point" in f2 and "a quantity string is stored as it is" in f2
    assert calls.run == 2  # F2's correction made J2 run the monolith again
    assert _spawns(d, "J4") == 1  # stale after F2: its recheck passed, no second worker
    assert [e["ids"] for e in d.events.all() if e["type"] == "spec_issues_routed"] == [["J4-1"]]
    assert calls.mutants == 1 and calls.freeze == 1 and _spawns(d, "J5C") == 0  # no survivors to classify


def test_gaps_go_back_to_j4_and_its_new_cases_run_on_the_monolith(tmp_path: Path):
    classes = {"write": {"judge/mutant_classes.json": {GAP: {"class": "gap", "reason": "no case reaches a falsy a",
                                                             "suggest": "send a=0"}},
                         "storage/stage_reports/J5C.json": {"ok": True}}, "submit": "done"}
    script = {"J1": [ok_report("J1")],
              "J4": [j4({}), j4({"judge/cases/post-api-x/wb-x-zero.json": {"steps": []}})],
              "J5C": [classes]}
    d, calls = strength_dispatcher(tmp_path, script, survivors=[[GAP], []])

    assert d.run() == EXIT_DONE

    assert _spawns(d, "J4") == 2 and _spawns(d, "J5C") == 1
    second = _prompt(d, "J4", 2)
    assert GAP in second and "gap" in second and "send a=0" in second
    assert calls.run == 2 and calls.mutants == 2 and calls.freeze == 1  # the new case ran on the monolith
    rounds = [e for e in d.events.all() if e["type"] == "strength_round"]
    assert len(rounds) == 1 and rounds[0]["ids"] == [GAP]


def test_after_the_rounds_the_gaps_left_are_recorded(tmp_path: Path):
    classes = {"write": {"judge/mutant_classes.json": {GAP: {"class": "gap", "reason": "r"}},
                         "storage/stage_reports/J5C.json": {"ok": True}}, "submit": "done"}
    script = {"J1": [ok_report("J1")], "J4": [j4({})], "J5C": [classes]}
    d, calls = strength_dispatcher(tmp_path, script, survivors=[[GAP]])

    assert d.run() == EXIT_DONE

    assert len([e for e in d.events.all() if e["type"] == "strength_round"]) == 2
    assert [e["ids"] for e in d.events.all() if e["type"] == "strength_gaps_left"] == [[GAP]]
    assert calls.freeze == 1
