"""Dispatcher-side room state (PIPELINE_V3_PLAN.md §4.7, v3 M3).

Workers are one-shot processes, so a room is a sequence of short turns the
dispatcher starts. State lives in WS/coord/rooms/<id>.json (the dispatcher owns
coord/); the messages live in coord/mail/rooms/<id>.jsonl, written by the
m2s-server proxy. Turn tasks are ordinary board tasks named ROOM-<id>-<n>.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

LIMITS = {"spec": {"rounds": 2, "participants": ["m2s-architect", "m2s-spec-annotator"]},
          "triage": {"rounds": 1, "participants": ["m2s-verifier"]}}


def rooms_dir(coord: Path | str) -> Path:
    return Path(coord) / "rooms"


def state_path(coord: Path | str, room_id: str) -> Path:
    return rooms_dir(coord) / f"{room_id}.json"


def load(coord: Path | str, room_id: str) -> dict[str, Any] | None:
    try:
        return json.loads(state_path(coord, room_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def save(coord: Path | str, state: dict[str, Any]) -> None:
    path = state_path(coord, state["id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def open_spec_room(coord: Path | str, room_id: str, proposer_role: str, theme: str,
                   proposer_task: str = "", extra_participants: list[str] | None = None) -> dict[str, Any]:
    """proposer -> architect -> annotator -> proposer reply, at most 2 rounds."""
    participants = [proposer_role, *LIMITS["spec"]["participants"], proposer_role]
    if extra_participants:
        participants = [p for p in participants if p in extra_participants or p == proposer_role]
    state = {"id": room_id, "kind": "spec", "theme": theme, "status": "open",
             "proposer_role": proposer_role, "proposer_task": proposer_task,
             "participants": participants, "turn": 0, "round": 1, "max_rounds": LIMITS["spec"]["rounds"],
             "created": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    save(coord, state)
    return state


def open_triage_room(coord: Path | str, room_id: str, theme: str, suspect_roles: list[str],
                     proposer_task: str = "") -> dict[str, Any]:
    """verifier states the failure; each suspect module attributes it; verifier replies."""
    participants = ["m2s-verifier", *[r for r in suspect_roles if r != "m2s-verifier"], "m2s-verifier"]
    state = {"id": room_id, "kind": "triage", "theme": theme, "status": "open",
             "proposer_role": "m2s-verifier", "proposer_task": proposer_task,
             "participants": participants, "turn": 0, "round": 1,
             "max_rounds": LIMITS["triage"]["rounds"],
             "created": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    save(coord, state)
    return state


def next_role(state: dict[str, Any]) -> str | None:
    turn = int(state.get("turn") or 0)
    participants = state.get("participants") or []
    if turn < len(participants):
        return participants[turn]
    return None


def advance(coord: Path | str, state: dict[str, Any]) -> dict[str, Any]:
    state["turn"] = int(state.get("turn") or 0) + 1
    if next_role(state) is None:
        if int(state.get("round") or 1) < int(state.get("max_rounds") or 1):
            state["round"] = int(state.get("round") or 1) + 1
            state["turn"] = 0
        else:
            state["status"] = "concluding"
    save(coord, state)
    return state


def messages(ws: Path | str, room_id: str) -> list[dict[str, Any]]:
    path = Path(ws).resolve() / "coord" / "mail" / "rooms" / f"{room_id}.jsonl"
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


def triage_consensus(messages_list: list[dict[str, Any]]) -> str | None:
    """The module every attribution names, or None when they disagree."""
    votes = []
    for message in messages_list:
        body = message.get("body")
        if isinstance(body, str):
            try:
                body = json.loads(body)
            except ValueError:
                continue
        if isinstance(body, dict) and body.get("module"):
            votes.append(str(body["module"]))
    return votes[0] if votes and len(set(votes)) == 1 else None
