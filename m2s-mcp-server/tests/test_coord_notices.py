"""v3 M3: notice board, rooms, visibility and relay-format checks (W3.2)."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from coord import notices, tools  # noqa: E402


def test_relay_format_rejects_test_assets():
    with pytest.raises(notices.RelayError):
        notices.check_relay("see verification/events/books/list.json for the expected body")
    with pytest.raises(notices.RelayError):
        notices.check_relay("the delta test says: verification/delta/orders-create.json")
    notices.check_relay("GET /api/books: expected 200, actual 500 (monolith source line 42)")


def test_notice_visibility(tmp_path):
    notices.post_notice(tmp_path, "dispatcher", "artifact", {"artifact": "blueprint", "version": 2}, mirror=False)
    assert [n["topic"] for n in notices.read_notices(tmp_path, "m2s-code-developer")] == ["artifact"]
    assert [n["topic"] for n in notices.read_notices(tmp_path, "m2s-verifier")] == ["artifact"]

    notices.post_notice(tmp_path, "m2s-server", "ruling",
                        {"question": "who answers 401?", "conclusion": "the gateway",
                         "basis": "R-A1", "discussion": "verifier and architect argued"}, mirror=False)
    impl = notices.read_notices(tmp_path, "m2s-code-developer", ["ruling"])[0]
    assert set(impl["body"]) == {"question", "conclusion", "basis"}
    full = notices.read_notices(tmp_path, "m2s-verifier", ["ruling"])[0]
    assert "discussion" in full["body"]

    notices.post_notice(tmp_path, "dispatcher", "task", "S2 failed twice", mirror=False)
    assert notices.read_notices(tmp_path, "m2s-code-developer", ["task"]) == []
    assert notices.read_notices(tmp_path, "handler", ["task"])

    with pytest.raises(notices.RelayError):
        notices.post_notice(tmp_path, "m2s-code-developer", "artifact", "x", mirror=False)


def test_spec_issue_post_and_read_roles(tmp_path):
    posted = notices.post_notice(tmp_path, "m2s-code-developer", "spec-issue", "which field?", mirror=False)
    assert posted["ok"]
    assert notices.read_notices(tmp_path, "m2s-code-developer", ["spec-issue"]) == []
    assert notices.read_notices(tmp_path, "m2s-architect", ["spec-issue"])


def test_rooms_are_for_participants_only(tmp_path):
    notices.post_room(tmp_path, "m2s-architect", "spec-SI-1", "spec", "option A", mirror=False)
    notices.post_room(tmp_path, "m2s-verifier", "spec-SI-1", "spec", "option B", mirror=False)
    messages = notices.read_room(tmp_path, "m2s-spec-annotator", "spec-SI-1", "spec")
    assert [m["sender_role"] for m in messages] == ["m2s-architect", "m2s-verifier"]
    with pytest.raises(notices.RelayError):
        notices.read_room(tmp_path, "m2s-code-developer", "spec-SI-1", "spec")
    with pytest.raises(notices.RelayError):
        notices.post_room(tmp_path, "m2s-verifier", "spec-SI-1", "spec",
                          "see verification/events/x.json", mirror=False)


def test_call_sync_works_from_a_running_event_loop(monkeypatch):
    """m2s-server runs its sync tools on the event loop thread and mirrors from
    there; asyncio.run inside that loop raises, and the mirror failed silently
    (v3 M3/W3.7). The call must get its own loop."""
    import asyncio

    from coord import mail

    async def fake_call(server, tool, **arguments):
        return {"tool": tool, "args": arguments}

    monkeypatch.setattr(mail, "_call", fake_call)

    async def main():
        return mail.call_sync(object(), "ping", x=1)

    assert asyncio.run(main()) == {"tool": "ping", "args": {"x": 1}}


def test_record_ruling_v1_writes_spec_resolutions(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    (ws / "storage").mkdir(parents=True)
    (ws / "coord").mkdir()
    (ws / "storage" / "blueprint.json").write_text(json.dumps({"metadata": {}}), encoding="utf-8")
    monkeypatch.setenv("M2S_WORKSPACE", str(ws))
    monkeypatch.setenv("M2S_COORD_DIR", str(ws / "coord"))
    monkeypatch.setenv("M2S_TASK_ID", "S1")
    monkeypatch.setenv("M2S_ATTEMPT", "1")
    monkeypatch.setenv("M2S_ROLE", "m2s-architect")
    monkeypatch.setenv("M2S_PIPELINE", "v1-split")

    result = tools.record_ruling("who answers 401?", "the gateway", "migration-change",
                                 "R-A1", raised_by="R-A1", affected_tasks=["S2"])
    assert result["ok"] and result["status"] == "decided"
    blueprint = json.loads((ws / "storage" / "blueprint.json").read_text(encoding="utf-8"))
    [entry] = blueprint["metadata"]["spec_resolutions"]
    assert entry["issue_id"] == "R-A1" and entry["decision"] == "the gateway"
    assert notices.read_notices(ws, "m2s-code-developer", ["ruling"])[0]["body"]["conclusion"] == "the gateway"
