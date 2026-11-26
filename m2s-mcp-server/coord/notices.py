"""Notice board and discussion rooms (PIPELINE_V3_PLAN.md §4.7, v3 M3).

Workers never talk to agent_mail directly. They call m2s-server, which appends
one JSON line to the workspace's own board/room files and mirrors it to
agent_mail (for the Web UI). The files are the source of truth: the dispatcher
reads them to decide turns and to write rulings.

Visibility is enforced here, before a message is stored or returned:
- artifact / ruling: every role; the implementation line sees a ruling's
  conclusion and basis, never the discussion around it;
- task: dispatcher and exception handler only;
- spec-issue: verifier, architect and spec annotator;
- rooms: their participant roles only.

Relay format: a message an implementation-line role can see may mention a
failed function, a source location, the expectation and the actual result. It
may not contain paths or text from the verification assets (delta tests, the
V1 test plan, verdict files); such a post is refused.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

from coord.locks import Locks

IMPL_ROLES = {"m2s-architect", "m2s-architect-v1", "m2s-code-developer", "m2s-code-developer-v1",
              "m2s-sam-engineer", "m2s-sam-engineer-v1", "m2s-validator",
              "m2s-perf-triage", "m2s-perf-optimizer", "m2s-perf-optimizer-v1"}
# v1 runs its own verifier role (m2s-verifier-v1); both count as the verify line.
VERIFY_ROLES = {"m2s-verifier", "m2s-verifier-v1", "m2s-spec-annotator"}
HANDLER_ROLE = "handler"
ALL_ROLES = IMPL_ROLES | VERIFY_ROLES | {HANDLER_ROLE, "dispatcher"}

TOPIC_ROLES: dict[str, set[str] | None] = {
    "artifact": None,  # everyone may read
    "ruling": None,    # everyone, filtered for the implementation line
    "task": {"dispatcher", HANDLER_ROLE},
    "spec-issue": {"m2s-verifier", "m2s-verifier-v1", "m2s-architect", "m2s-architect-v1", "m2s-spec-annotator", "dispatcher"},
}
# Who may write a topic. Readers are TOPIC_ROLES; code-developer may raise an
# issue but does not read the spec room/board topic (§4.6 tool table).
TOPIC_POST_ROLES: dict[str, set[str] | None] = {
    "artifact": {"dispatcher", "m2s-server"},
    "ruling": {"dispatcher", "m2s-server"},
    "task": {"dispatcher", HANDLER_ROLE},
    "spec-issue": {"m2s-verifier", "m2s-verifier-v1", "m2s-architect", "m2s-architect-v1", "m2s-spec-annotator", "m2s-code-developer", "m2s-code-developer-v1"},
}
TOPICS = tuple(TOPIC_ROLES)

ROOM_ROLES: dict[str, set[str]] = {
    "spec": {"m2s-verifier", "m2s-verifier-v1", "m2s-architect", "m2s-architect-v1", "m2s-spec-annotator"},
    "triage": {"m2s-verifier", "m2s-verifier-v1", "m2s-architect", "m2s-architect-v1", "m2s-code-developer", "m2s-code-developer-v1", "m2s-sam-engineer", "m2s-sam-engineer-v1"},
}

# What the implementation line must not see from the verification side.
RELAY_FORBIDDEN = re.compile(
    r"verification[\\/]|verification\s*/\s*delta|test_plan\.json|fault_baseline|"
    r"verdict_[A-Za-z0-9_]+\.json|verification[\\/]events|verification[\\/]faults",
    re.I,
)


class RelayError(ValueError):
    pass


def check_relay(text: str) -> None:
    """Refuse a message that carries verification assets into a visible place."""
    if RELAY_FORBIDDEN.search(text or ""):
        raise RelayError(
            "message contains verification asset paths or text; an implementation-line role can read this place. "
            "Describe the failed function, its source location, the expected and the actual result instead."
        )


def _mail_dir(ws: Path | str) -> Path:
    return Path(ws).resolve() / "coord" / "mail"


def _append(path: Path, entry: dict[str, Any], ws: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = Locks().acquire(f"mail:{Path(ws).resolve().name}", "m2s-server", ttl_s=30, wait_s=10)
    try:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    finally:
        if lock:
            lock.release()


def _read(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict):
            out.append(item)
    return out


def _visible(topic: str, role: str) -> bool:
    allowed = TOPIC_ROLES.get(topic)
    return allowed is None or role in allowed


def _filter_for_role(entry: dict[str, Any], role: str) -> dict[str, Any]:
    """Rulings are conclusion + basis for the implementation line; no discussion."""
    if entry.get("topic") == "ruling" and role in IMPL_ROLES:
        body = entry.get("body") or {}
        return {**entry, "body": {k: body.get(k) for k in ("question", "conclusion", "basis") if k in body}}
    return entry


def post_notice(ws: Path | str, role: str, topic: str, body: Any, *, subject: str = "",
                task: str = "", mirror: bool = True) -> dict[str, Any]:
    if topic not in TOPIC_ROLES:
        raise RelayError(f"topic must be one of {TOPICS}")
    allowed = TOPIC_POST_ROLES.get(topic)
    if allowed is not None and role not in allowed:
        raise RelayError(f"role {role!r} may not post on topic {topic!r}")
    text = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)
    if topic != "task":  # task notices never reach the implementation line
        check_relay(text)
    ws = Path(ws).resolve()
    entry = {"id": f"N-{int(time.time() * 1000)}", "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
             "topic": topic, "sender_role": role, "task": task, "subject": subject, "body": body}
    _append(_mail_dir(ws) / "notices.jsonl", entry, ws)
    if mirror:
        mirror_message(ws, f"notice-{topic}", subject or topic, text, role)
    return {"ok": True, "id": entry["id"]}


def read_notices(ws: Path | str, role: str, topics: list[str] | None = None,
                 limit: int = 100) -> list[dict[str, Any]]:
    wanted = set(topics or TOPICS)
    out = []
    for entry in _read(_mail_dir(ws) / "notices.jsonl"):
        topic = str(entry.get("topic"))
        if topic not in wanted or not _visible(topic, role):
            continue
        out.append(_filter_for_role(entry, role))
    return out[-limit:]


# ---------------------------------------------------------------------------
# Rooms
# ---------------------------------------------------------------------------

def room_path(ws: Path | str, room_id: str) -> Path:
    return _mail_dir(ws) / "rooms" / f"{room_id}.jsonl"


def _room_state(ws: Path | str, room_id: str) -> dict[str, Any] | None:
    try:
        return json.loads((Path(ws).resolve() / "coord" / "rooms" / f"{room_id}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def room_allowed(kind: str, role: str, state: dict[str, Any] | None = None) -> bool:
    """A room's own participant list wins: the role that raised a spec issue may
    speak in its room even when that role is not a regular reader of the topic."""
    if isinstance(state, dict) and role in (state.get("participants") or []):
        return True
    return role in ROOM_ROLES.get(kind, set())


def post_room(ws: Path | str, role: str, room_id: str, kind: str, body: Any, *,
              task: str = "", mirror: bool = True) -> dict[str, Any]:
    state = _room_state(ws, room_id)
    if not room_allowed(kind, role, state):
        raise RelayError(f"role {role!r} is not a participant of a {kind} room")
    # §4.7: turns are dispatcher-driven; only the participant whose turn it is may post.
    if isinstance(state, dict) and state.get("status") == "open":
            participants = state.get("participants") or []
            turn = int(state.get("turn") or 0)
            expected = participants[turn] if turn < len(participants) else None
            if expected and role != expected:
                raise RelayError(f"not your turn in {room_id}: waiting for {expected}")
    text = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)
    check_relay(text)
    ws = Path(ws).resolve()
    entries = _read(room_path(ws, room_id))
    if role in IMPL_ROLES and any(e.get("sender_role") in VERIFY_ROLES for e in entries):
        check_relay(text)  # same rule, kept explicit for readers
    entry = {"seq": len(entries) + 1, "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "room": room_id,
             "kind": kind, "sender_role": role, "task": task, "body": body}
    _append(room_path(ws, room_id), entry, ws)
    if mirror:
        mirror_message(ws, f"room-{room_id}", f"{room_id} #{entry['seq']}", f"[{role}] {text}", role)
    return {"ok": True, "seq": entry["seq"], "messages": len(entries) + 1}


def read_room(ws: Path | str, role: str, room_id: str, kind: str) -> list[dict[str, Any]]:
    if not room_allowed(kind, role, _room_state(ws, room_id)):
        raise RelayError(f"role {role!r} is not a participant of a {kind} room")
    return _read(room_path(ws, room_id))


# ---------------------------------------------------------------------------
# Mirroring to agent_mail (Web UI); never fatal
# ---------------------------------------------------------------------------

def mirror_message(ws: Path | str, thread_id: str, subject: str, body: str, role: str) -> bool:
    url = os.environ.get("M2S_MAIL_URL")
    token = os.environ.get("M2S_MAIL_TOKEN")
    if not url or not token:
        return False
    try:
        from coord import mail as mail_mod

        root = Path(os.environ.get("M2S_MAIL_ROOT") or ("D:/m2s-am" if os.name == "nt" else "/tmp/m2s-am"))
        port = int(os.environ.get("M2S_MAIL_PORT") or "8765")
        server = mail_mod.AgentMailServer(root, port, token)
        project = str(Path(ws).resolve())
        mail_mod.call_sync(server, "ensure_project", human_key=project)
        agent = mail_mod.call_sync(server, "register_agent", project_key=project, program="m2s",
                                   model="none", task_description="m2s-server")
        name = agent.get("name") if isinstance(agent, dict) else None
        if not name:
            return False
        mail_mod.call_sync(server, "send_message", project_key=project, sender_name=name,
                           sender_token=(agent or {}).get("registration_token"), to=[name],
                           subject=subject[:200], body_md=body[:20000], thread_id=thread_id,
                           auto_contact_if_blocked=False)
        return True
    except Exception as exc:
        _record_mirror_error(ws, exc)
        return False


def _record_mirror_error(ws: Path | str, exc: BaseException) -> None:
    """A failed mirror must be visible: the Web UI silently missing a thread is
    an acceptance failure, not a no-op (v3 M3/W3.7)."""
    try:
        path = _mail_dir(ws) / "mirror_errors.jsonl"
        entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                 "error": f"{type(exc).__name__}: {exc}"[:2000]}
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass
