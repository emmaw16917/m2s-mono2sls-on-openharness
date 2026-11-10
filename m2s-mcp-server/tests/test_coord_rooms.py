"""v3 M3 (W3.3): a spec room runs its turns and a ruling sends the task back."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from coord_fakes import fake_checks, make_dispatcher  # noqa: E402

from coord import rooms  # noqa: E402
from coord.checks import room_turn  # noqa: E402
from coord.dispatcher import EXIT_DONE  # noqa: E402
from coord.template import load_template  # noqa: E402

TEMPLATE = """\
name: v1-split
tasks:
  - id: P0
    runner: dispatcher
    action: analyze_project
    scope: [storage/analysis_report.json]
    check: [ok]
  - id: S1
    role: m2s-architect
    deps: [P0]
    scope: [storage/blueprint.json, storage/stage_reports/S1.json]
    check: [ok]
  - id: S2
    role: m2s-code-developer
    deps: [S1]
    scope: [output/lambdas/**, storage/stage_reports/S2.json]
    check: [ok]
"""


def test_spec_room_turns_and_ruling(tmp_path, monkeypatch):
    monkeypatch.setenv("M2S_ROOMS", "1")
    monkeypatch.setenv("M2S_LOCK_DIR", str(tmp_path / "locks"))
    tpl = tmp_path / "template.yaml"
    tpl.write_text(TEMPLATE, encoding="utf-8")
    template = load_template(path=tpl)
    script = {
        "ROOM-spec-test-0-1": [{"room_post": [{"room_id": "spec-test", "kind": "spec", "body": "which 401 body?"}]}],
        "ROOM-spec-test-1-1": [{
            "room_post": [{"room_id": "spec-test", "kind": "spec", "body": "the gateway"}],
            "rulings": [{"question": "who answers 401?", "conclusion": "the gateway",
                         "category": "migration-change", "basis": "R-ROOM", "raised_by": "R-ROOM"}],
        }],
        "ROOM-spec-test-2-1": [{"room_post": [{"room_id": "spec-test", "kind": "spec", "body": "facts unchanged"}]}],
        "ROOM-spec-test-3-1": [{"room_post": [{"room_id": "spec-test", "kind": "spec", "body": "agreed"}]}],
    }
    checks = fake_checks(template)
    checks["room_turn"] = room_turn
    d = make_dispatcher(tmp_path, script, template=template, checks=checks)
    d.setup()
    (d.ws / "storage").mkdir(parents=True, exist_ok=True)
    (d.ws / "storage" / "blueprint.json").write_text(json.dumps({"metadata": {}}), encoding="utf-8")

    state = rooms.open_spec_room(d.coord, "spec-test", "m2s-verifier", "who answers 401?", proposer_task="S1")
    state["max_rounds"] = 1
    rooms.save(d.coord, state)

    assert d.run() == EXIT_DONE
    [entry] = json.loads((d.ws / "storage" / "blueprint.json").read_text(encoding="utf-8"))["metadata"]["spec_resolutions"]
    assert entry["issue_id"] == "R-ROOM"
    assert rooms.load(d.coord, "spec-test")["status"] == "closed"
    assert any(e["type"] == "room_end" and e.get("room") == "spec-test" for e in d.events.all())
