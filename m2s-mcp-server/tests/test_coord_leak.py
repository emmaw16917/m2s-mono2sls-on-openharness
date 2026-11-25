"""v3 M3 (W3.6): visibility matrix — no canary leaks through notices or rooms."""
from __future__ import annotations

import socket
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from coord import mail, notices  # noqa: E402

IMPL = ["m2s-architect", "m2s-architect-v1", "m2s-code-developer", "m2s-sam-engineer", "m2s-validator", "m2s-perf-optimizer"]
VERIFY = ["m2s-verifier", "m2s-verifier-v1", "m2s-spec-annotator"]


def _board(tmp_path: Path) -> None:
    notices.post_notice(tmp_path, "dispatcher", "artifact", "blueprint v2", mirror=False)
    notices.post_notice(tmp_path, "m2s-server", "ruling",
                        {"question": "q", "conclusion": "c", "basis": "b", "discussion": "CANARY"}, mirror=False)
    notices.post_notice(tmp_path, "dispatcher", "task", "CANARY task", mirror=False)
    notices.post_notice(tmp_path, "m2s-verifier", "spec-issue", "CANARY spec", mirror=False)


def test_no_canary_reaches_the_implementation_line(tmp_path):
    _board(tmp_path)
    for role in IMPL:
        seen = notices.read_notices(tmp_path, role)
        text = repr(seen)
        assert "CANARY task" not in text
        if role in ("m2s-architect", "m2s-architect-v1"):
            assert "CANARY spec" in text  # the architect is a spec-room participant
        else:
            assert "CANARY spec" not in text
        # a ruling is conclusion + basis only, never the discussion
        ruling = next(e for e in seen if e["topic"] == "ruling")
        assert "CANARY" not in repr(ruling)
        assert ruling["body"]["conclusion"] == "c"


def test_verifier_sees_spec_issues_and_full_rulings(tmp_path):
    _board(tmp_path)
    for role in ("m2s-verifier", "m2s-verifier-v1"):
        seen = notices.read_notices(tmp_path, role)
        topics = {e["topic"] for e in seen}
        assert {"artifact", "ruling", "spec-issue"} <= topics, role
        assert "task" not in topics, role


def test_room_canary_never_reaches_a_non_participant(tmp_path):
    notices.post_room(tmp_path, "m2s-verifier", "spec-x", "spec", "CANARY expected body", mirror=False)
    with pytest.raises(notices.RelayError):
        notices.read_room(tmp_path, "m2s-code-developer", "spec-x", "spec")
    with pytest.raises(notices.RelayError):
        notices.post_room(tmp_path, "m2s-code-developer", "spec-x", "spec", "CANARY", mirror=False)


def test_mail_server_is_reused(tmp_path):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    root = tmp_path / "am"
    first = mail.ensure_server(root, port)
    try:
        assert first.port == port
        second = mail.ensure_server(root, port)
        assert second.process is None  # the running server was reused, not started twice
        assert first.token == second.token
    finally:
        first.stop()
