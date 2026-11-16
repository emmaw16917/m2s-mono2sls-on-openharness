"""coord.rulings and the record_ruling tool (PIPELINE_V2_PLAN.md §4.6, N0.2)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from coord import rulings, tools  # noqa: E402

GOOD = {"question": "Q", "conclusion": "C", "category": "contract", "basis": "B"}


@pytest.mark.parametrize("patch, message", [
    ({"question": ""}, "question is required"),
    ({"basis": None}, "basis is required"),
    ({"category": "nice-to-have"}, "category must be one of"),
    ({"relaxation": "sometimes"}, "relaxation must be one of"),
    ({"affects": {"layers": ["X"]}}, "affects.layers"),
    ({"affects": {"cases": "c1"}, "options": [""]}, "options must be"),
    ({"affects": ["c1"]}, "affects must be an object"),
])
def test_normalize_rejects_bad_fields(patch, message):
    with pytest.raises(rulings.RulingError, match=message):
        rulings.normalize({**GOOD, **patch})


def test_normalize_fills_defaults_and_relaxation_implies_r_layer():
    out = rulings.normalize({**GOOD, "relaxation": "order", "affects": {"cases": "books-list", "layers": ["P"]}})
    assert out["affects"] == {"cases": ["books-list"], "layers": ["P", "R"], "tasks": []}
    assert out["deviates_from_monolith"] is False and out["options"] == []


def test_propose_checks_the_role_and_numbers_per_attempt(tmp_path: Path):
    with pytest.raises(rulings.RulingError, match="only"):
        rulings.propose(tmp_path, "S2", 1, "m2s-code-developer", GOOD)
    first = rulings.propose(tmp_path, "S1", 2, "m2s-architect", GOOD)
    second = rulings.propose(tmp_path, "S1", 2, "m2s-architect", {**GOOD, "deviates_from_monolith": True})
    other = rulings.propose(tmp_path, "C001-J3-x", 1, "handler", GOOD)

    assert (first["id"], second["id"], other["id"]) == ("R-S1-a2-1", "R-S1-a2-2", "R-C001-J3-x-a1-1")
    assert (first["status"], second["status"]) == ("decided", "proposed")
    assert first["raised_by"] == "S1" and first["decider"] == "m2s-architect"
    assert [r["id"] for r in rulings.proposals(tmp_path, "S1", 2)] == ["R-S1-a2-1", "R-S1-a2-2"]
    assert rulings.proposals(tmp_path, "S1", 1) == []


def test_settlements_are_new_entries_folded_into_status(tmp_path: Path):
    decided = rulings.propose(tmp_path, "S1", 1, "m2s-architect", GOOD)
    keep = rulings.propose(tmp_path, "S1", 1, "m2s-architect", {**GOOD, "deviates_from_monolith": True})
    drop = rulings.propose(tmp_path, "S1", 1, "m2s-architect", {**GOOD, "deviates_from_monolith": True})
    waiting = rulings.propose(tmp_path, "S1", 1, "m2s-architect", {**GOOD, "deviates_from_monolith": True})
    entries = [decided, keep, drop, waiting,
               rulings.settlement(keep["id"], rulings.CONFIRM, "ok", 1),
               rulings.settlement(drop["id"], rulings.REJECT, "copy the monolith", 2)]

    status = {r["id"]: r["status"] for r in rulings.effective(entries)}
    assert status == {decided["id"]: "decided", keep["id"]: "confirmed", drop["id"]: "rejected", waiting["id"]: "proposed"}
    assert [r["id"] for r in rulings.in_force(entries)] == [decided["id"], keep["id"]]
    with pytest.raises(rulings.RulingError):
        rulings.settlement(keep["id"], "maybe", "", 3)

    assert rulings.write_workspace_file(tmp_path, entries)
    assert not rulings.write_workspace_file(tmp_path, entries)
    data = json.loads((tmp_path / "storage" / "rulings.json").read_text(encoding="utf-8"))
    assert len(data["entries"]) == 6 and len(data["rulings"]) == 4


def test_human_ruling(tmp_path: Path):
    ruling = rulings.human_ruling({**GOOD, "affects": {"tasks": ["S2"]}}, 3)
    assert ruling["id"] == "R-human-3" and ruling["decider"] == "human" and ruling["status"] == "decided"


def test_record_ruling_tool(tmp_path: Path, monkeypatch):
    assert tools.safe(tools.record_ruling, "Q", "C", "contract", "B")["ok"] is False  # not dispatched
    monkeypatch.setenv("M2S_COORD_DIR", str(tmp_path))
    monkeypatch.setenv("M2S_TASK_ID", "S1")
    monkeypatch.setenv("M2S_ATTEMPT", "1")
    monkeypatch.setenv("M2S_ROLE", "m2s-architect")

    result = tools.record_ruling("Q", "C", "contract", "B", relaxation="order", affected_cases=["books-list"],
                                 deviates_from_monolith=True)
    assert result["ok"] and result["id"] == "R-S1-a1-1" and result["status"] == "proposed"
    assert "a person confirms" in result["next"]
    assert tools.record_ruling("Q", "C", "bogus", "B")["ok"] is False

    monkeypatch.setenv("M2S_ROLE", "m2s-verifier")
    assert "only" in tools.record_ruling("Q", "C", "contract", "B")["error"]
    assert len(rulings.proposals(tmp_path, "S1", 1)) == 1
