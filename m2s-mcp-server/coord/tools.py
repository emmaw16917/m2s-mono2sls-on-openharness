"""Worker-side coordination tools, served by m2s-server (PIPELINE_V3_PLAN.md §4.6).

A worker started by the dispatcher gets these environment variables on its
m2s-server process (it cannot change them):
    M2S_COORD_DIR   WS/coord
    M2S_TASK_ID     e.g. "S2"
    M2S_ATTEMPT     1, 2, ...
    M2S_ROLE        agent role, or "handler" for the exception handler
Outside a dispatcher run (plain v1) the tools say so and do nothing.

Only the standard library is used: m2s-server runs in its own conda env.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from coord import rulings

HANDLER_ROLE = "handler"
HANDLER_ACTIONS = ("retry", "reroute", "escalate", "ok")


class NotDispatched(RuntimeError):
    pass


def _env() -> tuple[Path, str, int, str]:
    coord = os.environ.get("M2S_COORD_DIR")
    task = os.environ.get("M2S_TASK_ID")
    if not coord or not task:
        raise NotDispatched("this session was not started by the v3 dispatcher (M2S_COORD_DIR / M2S_TASK_ID unset)")
    return Path(coord), task, int(os.environ.get("M2S_ATTEMPT") or 1), os.environ.get("M2S_ROLE") or ""


def write_json_atomic(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def submission_path(coord_dir: Path, task_id: str, attempt: int) -> Path:
    return Path(coord_dir) / "submissions" / f"{task_id}-a{attempt}.json"


def run_dir(coord_dir: Path, task_id: str, attempt: int) -> Path:
    return Path(coord_dir) / "run" / f"{task_id}-a{attempt}"


def _submit(status: str, **fields: Any) -> dict[str, Any]:
    coord, task, attempt, role = _env()
    record = {"task": task, "attempt": attempt, "role": role, "status": status,
              "time": time.strftime("%Y-%m-%dT%H:%M:%S%z"), **fields}
    write_json_atomic(submission_path(coord, task, attempt), record)
    return {"ok": True, "task": task, "attempt": attempt, "status": status,
            "next": "Submission recorded. End the session now; the dispatcher runs the checks."}


def submit_task(summary: str, notes: str = "") -> dict[str, Any]:
    return _submit("done", summary=summary, notes=notes)


def report_blocked(reason: str, evidence: str = "", suspected_module: str = "") -> dict[str, Any]:
    return _submit("blocked", reason=reason, evidence=evidence, suspected_module=suspected_module)


def get_task_context() -> dict[str, Any]:
    coord, task, attempt, role = _env()
    prompt = run_dir(coord, task, attempt) / "prompt.md"
    return {"task": task, "attempt": attempt, "role": role,
            "context": prompt.read_text(encoding="utf-8") if prompt.is_file() else ""}


def handler_decide(case: str, action: str, target: str = "", feedback: str = "", rationale: str = "") -> dict[str, Any]:
    coord, task, attempt, role = _env()
    if role != HANDLER_ROLE:
        return {"ok": False, "error": "only the exception handler may call handler_decide"}
    if action not in HANDLER_ACTIONS:
        return {"ok": False, "error": f"action must be one of {HANDLER_ACTIONS}"}
    if action == "reroute" and not target:
        return {"ok": False, "error": "reroute needs a target task id"}
    record = {"case": case, "action": action, "target": target, "feedback": feedback, "rationale": rationale,
              "time": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    write_json_atomic(Path(coord) / "cases" / f"{case}.decision.json", record)
    return {"ok": True, **record, "next": "Decision recorded. End the session now."}


def record_ruling(question: str, conclusion: str, category: str, basis: str, raised_by: str = "",
                  options: list[str] | None = None, relaxation: str = "", affected_cases: list[str] | None = None,
                  layers: list[str] | None = None, affected_tasks: list[str] | None = None,
                  deviates_from_monolith: bool = False) -> dict[str, Any]:
    coord, task, attempt, role = _env()
    raw = {"question": question, "conclusion": conclusion, "category": category, "basis": basis,
           "raised_by": raised_by, "options": options or [], "relaxation": relaxation or None,
           "affects": {"cases": affected_cases or [], "layers": layers or [], "tasks": affected_tasks or []},
           "deviates_from_monolith": deviates_from_monolith}
    try:
        ruling = rulings.propose(coord, task, attempt, role, raw)
    except rulings.RulingError as exc:
        return {"ok": False, "error": str(exc)}
    when = "after a person confirms it (it departs from the monolith)" if ruling["status"] == "proposed" else ""
    return {"ok": True, "id": ruling["id"], "status": ruling["status"],
            "next": f"Recorded. It takes effect when this task passes its checks{' and ' + when if when else ''}. "
                    "Cite the id in your stage report."}


def safe(fn, *args: Any, **kwargs: Any) -> dict[str, Any]:
    """Wrap a tool call for MCP: never raise, report NotDispatched as an error."""
    try:
        return fn(*args, **kwargs)
    except NotDispatched as exc:
        return {"ok": False, "error": str(exc)}
