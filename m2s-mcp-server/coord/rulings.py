"""Ruling record (PIPELINE_V2_PLAN.md §4.6, design §4.12).

A ruling settles a question the facts alone do not: whether to copy a defect of
the monolith, whether a behaviour may be relaxed (order, freshness, several
acceptable results), what a migration changes on purpose.

How a ruling gets in:
- architect or exception handler: the `record_ruling` tool writes a proposal to
  WS/coord/rulings/<id>.json (workers cannot reach coord/). The dispatcher
  accepts the proposals of an attempt only when that attempt passes its checks;
  a failed attempt's proposals are dropped (the next attempt records again).
- a person: `dispatcher resolve ... --ruling-file R.json`.

A ruling that departs from the monolith (`deviates_from_monolith`) takes effect
only after a person confirms it (V2-20); until then it is `proposed`.

WS/storage/rulings.json is the dispatcher's: rebuilt from the event log, entries
in the order they were accepted, never edited. A confirmation or rejection is a
new entry that names the ruling it settles, so the file is append-only;
`effective()` folds them into each ruling's current status.

Only the standard library is used: m2s-server imports this module.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

WORKSPACE_FILE = Path("storage") / "rulings.json"
PROPOSALS_DIR = "rulings"  # under WS/coord
RULING_ROLES = ("m2s-architect", "handler")
CATEGORIES = ("contract", "implementation-trace", "migration-change", "spec-gap")
RELAXATIONS = ("order", "freshness", "alternatives")
LAYERS = ("P", "R", "T")
CONFIRM = "confirm"
REJECT = "reject"


class RulingError(ValueError):
    pass


def _strings(value: Any, name: str) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)) or not all(isinstance(v, str) and v.strip() for v in value):
        raise RulingError(f"{name} must be a list of non-empty strings")
    return [v.strip() for v in value]


def normalize(raw: dict[str, Any]) -> dict[str, Any]:
    """Check a ruling's fields and return it in canonical form (without id, decider, time)."""
    if not isinstance(raw, dict):
        raise RulingError("a ruling is a JSON object")
    out: dict[str, Any] = {}
    for key in ("question", "conclusion", "basis"):
        value = raw.get(key)
        if not isinstance(value, str) or not value.strip():
            raise RulingError(f"{key} is required")
        out[key] = value.strip()
    category = raw.get("category")
    if category not in CATEGORIES:
        raise RulingError(f"category must be one of {CATEGORIES}")
    out["category"] = category
    relaxation = raw.get("relaxation") or None
    if relaxation is not None and relaxation not in RELAXATIONS:
        raise RulingError(f"relaxation must be one of {RELAXATIONS}")
    affects = raw.get("affects") or {}
    if not isinstance(affects, dict):
        raise RulingError("affects must be an object {cases, layers, tasks}")
    layers = _strings(affects.get("layers"), "affects.layers")
    if any(layer not in LAYERS for layer in layers):
        raise RulingError(f"affects.layers must be among {LAYERS}")
    if relaxation and "R" not in layers:
        layers.append("R")  # a relaxation is what the R layer compares by
    out.update(
        raised_by=str(raw.get("raised_by") or "").strip(),
        options=_strings(raw.get("options"), "options"),
        relaxation=relaxation,
        affects={"cases": _strings(affects.get("cases"), "affects.cases"), "layers": layers,
                 "tasks": _strings(affects.get("tasks"), "affects.tasks")},
        deviates_from_monolith=bool(raw.get("deviates_from_monolith", False)),
    )
    return out


def _write_json_atomic(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def propose(coord: Path, task: str, attempt: int, role: str, raw: dict[str, Any]) -> dict[str, Any]:
    """Write one proposal of a worker's attempt; ids are R-<task>-a<attempt>-<n>."""
    if role not in RULING_ROLES:
        raise RulingError(f"only {RULING_ROLES} may record rulings (this session is {role or 'unknown'})")
    ruling = normalize(raw)
    folder = Path(coord) / PROPOSALS_DIR
    prefix = f"R-{task}-a{attempt}-"
    n = 1 + sum(1 for p in folder.glob(f"{prefix}*.json")) if folder.is_dir() else 1
    ruling = {"id": f"{prefix}{n}", "decider": role, "task": task, "attempt": attempt,
              "time": time.strftime("%Y-%m-%dT%H:%M:%S%z"), **ruling,
              "status": "proposed" if ruling["deviates_from_monolith"] else "decided"}
    if not ruling["raised_by"]:
        ruling["raised_by"] = task
    _write_json_atomic(folder / f"{ruling['id']}.json", ruling)
    return ruling


def proposals(coord: Path, task: str, attempt: int) -> list[dict[str, Any]]:
    folder = Path(coord) / PROPOSALS_DIR
    found = []
    for path in sorted(folder.glob(f"R-{task}-a{attempt}-*.json"), key=lambda p: int(p.stem.rsplit("-", 1)[1])):
        try:
            found.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError, ValueError):
            continue
    return found


def human_ruling(raw: dict[str, Any], seq: int) -> dict[str, Any]:
    ruling = normalize(raw)
    return {"id": f"R-human-{seq}", "decider": "human", "task": None, "attempt": None,
            "time": time.strftime("%Y-%m-%dT%H:%M:%S%z"), **ruling,
            "raised_by": ruling["raised_by"] or "human", "status": "decided"}


def settlement(ruling_id: str, decision: str, comment: str, seq: int) -> dict[str, Any]:
    """A person's confirmation or rejection of a proposed ruling: a new entry."""
    if decision not in (CONFIRM, REJECT):
        raise RulingError(f"decision must be {CONFIRM} or {REJECT}")
    return {"id": f"R-human-{seq}", "decider": "human", "settles": ruling_id, "decision": decision,
            "comment": comment, "time": time.strftime("%Y-%m-%dT%H:%M:%S%z")}


def effective(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The rulings with their current status: decided, proposed, confirmed or rejected."""
    rulings = [dict(e) for e in entries if "settles" not in e]
    by_id = {r["id"]: r for r in rulings}
    for entry in entries:
        target = by_id.get(entry.get("settles"))
        if target is not None:
            target["status"] = "confirmed" if entry["decision"] == CONFIRM else "rejected"
            target["settled_by"] = entry["id"]
    return rulings


def in_force(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rulings that the judge and the tasks must follow."""
    return [r for r in effective(entries) if r["status"] in ("decided", "confirmed")]


def write_workspace_file(ws: Path, entries: list[dict[str, Any]]) -> bool:
    """Write WS/storage/rulings.json from the accepted entries; True when it changed."""
    path = Path(ws) / WORKSPACE_FILE
    text = json.dumps({"entries": entries, "rulings": effective(entries)}, ensure_ascii=False, indent=2) + "\n"
    try:
        if path.read_text(encoding="utf-8") == text:
            return False
    except OSError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return True
