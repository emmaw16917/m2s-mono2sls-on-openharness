"""Append-only event log of one run (PIPELINE_V3_PLAN.md §4.12).

Every dispatcher decision is an event, so a run can be replayed and audited,
and the dispatcher can rebuild its counters (attempts, rework rounds,
pending feedback) after a restart. The board holds task status; this log
holds everything else.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Iterator


class EventLog:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._seq = sum(1 for _ in self._lines())

    def _lines(self) -> Iterator[str]:
        if not self.path.is_file():
            return iter(())
        return (line for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip())

    def append(self, type_: str, **fields: Any) -> dict[str, Any]:
        self._seq += 1
        event = {"seq": self._seq, "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "type": type_, **fields}
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        return event

    def all(self) -> list[dict[str, Any]]:
        events = []
        for line in self._lines():
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # a torn last line after a crash
        return events

    def of_task(self, task_id: str, *types: str) -> list[dict[str, Any]]:
        return [e for e in self.all() if e.get("task") == task_id and (not types or e["type"] in types)]

    def last(self, task_id: str, *types: str) -> dict[str, Any] | None:
        found = self.of_task(task_id, *types)
        return found[-1] if found else None

    # --- counters derived from the log ---

    def _since_round_start(self, task_id: str) -> list[dict[str, Any]]:
        events = self.of_task(task_id)
        start = 0
        for index, event in enumerate(events):
            if event["type"] == "round_start":
                start = index
        return events[start:]

    def spawns(self, task_id: str) -> int:
        """Worker spawns in the current round; the next attempt number is spawns + 1."""
        return sum(1 for e in self._since_round_start(task_id) if e["type"] == "spawn")

    def crashes(self, task_id: str, infra: bool | None = None) -> int:
        """Crashed exits in the current round; infra=True / False keeps only model-API
        (network) failures / only the others."""
        return sum(1 for e in self._since_round_start(task_id) if e["type"] == "worker_exit" and e.get("crashed")
                   and (infra is None or bool(e.get("infra")) == infra))

    def attempts(self, task_id: str) -> int:
        """Attempts that count against max_attempts: spawns minus crashes."""
        return self.spawns(task_id) - self.crashes(task_id)

    def count(self, type_: str, task_id: str | None = None) -> int:
        return sum(1 for e in self.all() if e["type"] == type_ and (task_id is None or e.get("task") == task_id))

    def rework_rounds(self, task_id: str, reason: str | None = None) -> int:
        return sum(
            1 for e in self.of_task(task_id, "round_start")
            if e.get("cause") == "rework" and (reason is None or e.get("reason") == reason)
        )

    def pending_feedback(self, task_id: str) -> list[dict[str, Any]]:
        """Feedback recorded since the task's last spawn."""
        events = self.of_task(task_id, "spawn", "feedback")
        pending: list[dict[str, Any]] = []
        for event in events:
            if event["type"] == "spawn":
                pending = []
            else:
                pending.append(event)
        return pending

    def reopen_mode(self, task_id: str) -> str | None:
        """'recheck' or 'rerun' when the task was reopened and not yet started again."""
        events = self.of_task(task_id, "round_start", "spawn", "recheck")
        if not events or events[-1]["type"] != "round_start":
            return None
        return events[-1].get("mode")
