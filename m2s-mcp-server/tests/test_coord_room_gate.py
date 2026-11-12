"""v3 M4/W4.4: J3 disagreements go through a spec room before the ruling gate.

The gate task (RULE_J3_1) waits for the room's turns; its recheck passes when
the architect's `record_ruling` covers every difference, so no architect worker
is spent. Without rooms the gate falls back to the v2 N1 behaviour (a worker).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(not os.environ.get("M2S_RUN_SLOW"), reason="slow; set M2S_RUN_SLOW=1")

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from coord_fakes import make_dispatcher  # noqa: E402

from coord import rooms  # noqa: E402
from coord.checks import CHECKS, CheckResult  # noqa: E402
from coord.dispatcher import EXIT_DONE  # noqa: E402
from coord.template import load_template  # noqa: E402

JUDGE = load_template(path=Path(__file__).resolve().parent / "fixtures" / "coord_judge.yaml")


def diff(case: str) -> dict:
    return {"id": f"{case}/s1/status", "case": case, "step": "s1", "check": "status", "predicted": 201,
            "actual": 200, "fact_refs": [f"POST /api/{case}#responses[0]"],
            "request": {"method": "POST", "path": f"/api/{case}"}}


class Calls:
    def __init__(self, ws: Path, rounds: list[list[dict]]):
        self.ws, self.rounds, self.reconcile = ws, rounds, 0

    def run_monolith(self, spec) -> CheckResult:
        return CheckResult("run_monolith", True, "fake run_monolith PASS", detail={"status": "PASS"})

    def reconcile_spec(self, spec) -> CheckResult:
        diffs = self.rounds[min(self.reconcile, len(self.rounds) - 1)]
        self.reconcile += 1
        jd = self.ws / "judge"
        jd.mkdir(parents=True, exist_ok=True)
        (jd / "reconcile.json").write_text(json.dumps({"differences": diffs, "resolved_by_rulings": []}), encoding="utf-8")
        (jd / "reconcile_classes.json").unlink(missing_ok=True)
        return CheckResult("reconcile_spec", True, f"fake: {len(diffs)} differences")

    def freeze_judge(self, spec) -> CheckResult:
        return CheckResult("freeze_judge", True, "fake freeze")


def turn(room: str, role_body: str, **extra) -> dict:
    return {"write": {f"storage/stage_reports/ROOM-{room}-{extra.pop('turn')}.json": {"ok": True}},
            "room_post": [{"room_id": room, "kind": "spec", "body": role_body}],
            "submit": "done", **extra}


def test_a_j3_disagreement_is_settled_in_a_spec_room(tmp_path, monkeypatch):
    monkeypatch.setenv("M2S_ROOMS", "1")
    monkeypatch.setenv("M2S_LOCK_DIR", str(tmp_path / "locks"))
    # one round instead of two keeps the test at four turns
    real_open = rooms.open_spec_room

    def open_one_round(*args, **kwargs):
        state = real_open(*args, **kwargs)
        state["max_rounds"] = 1
        return state

    monkeypatch.setattr(rooms, "open_spec_room", open_one_round)

    rule = diff("c4")
    ruling = {"question": "c4 answers 200 where the facts say 201", "conclusion": "keep the monolith's 200",
              "category": "contract", "basis": "routes/c4.js", "affected_cases": ["c4"]}
    room = "spec-j3-r1"
    script = {
        "J3": [{"write": {"judge/reconcile_classes.json": {rule["id"]: {"class": "needs_ruling", "reason": "ambiguous"}},
                          "storage/stage_reports/J3.json": {"ok": True}}, "submit": "done"}],
        f"ROOM-{room}-0-1": [turn(room, "c4 answers 200; the facts say 201, which side is right?", turn="0-1")],
        f"ROOM-{room}-1-1": [turn(room, "the monolith's 200 is the contract", turn="1-1", rulings=[ruling])],
        f"ROOM-{room}-2-1": [turn(room, "the fact entry needs a note", turn="2-1")],
        f"ROOM-{room}-3-1": [turn(room, "agreed: keep 200", turn="3-1")],
    }
    calls = Calls(tmp_path / "ws", rounds=[[rule], []])
    d = make_dispatcher(tmp_path, script, template=JUDGE,
                        actions={"run_monolith": calls.run_monolith, "reconcile_spec": calls.reconcile_spec,
                                 "freeze_judge": calls.freeze_judge})
    d.checks["reconcile_classified"] = CHECKS["reconcile_classified"]
    d.checks["room_turn"] = CHECKS["room_turn"]
    report = d.ws / "storage" / "stage_reports" / "F2.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps({"ok": True}), encoding="utf-8")

    assert d.run() == EXIT_DONE

    # the room ran four turns and the gate spent no worker
    turn_spawns = [t for t in d.board.tasks() if str(t).startswith(f"ROOM-{room}-")]
    assert len(turn_spawns) == 4
    assert d.events.count("spawn", "RULE_J3_1") == 0
    assert d.events.of_task("RULE_J3_1", "recheck")
    assert d.board.status("RULE_J3_1") == "closed" and d.board.status("J6") == "closed"

    saved = json.loads((d.ws / "storage" / "rulings.json").read_text(encoding="utf-8"))
    assert [r["affects"]["cases"] for r in saved["rulings"]] == [["c4"]]
    assert d.events.last("RULE_J3_1", "recheck")
    assert d.events.of_task("J3", "reconcile_round")[0]["counts"]["needs_ruling"] == 1
