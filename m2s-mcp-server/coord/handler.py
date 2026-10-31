"""Exception handler cases and the run journal (PIPELINE_V3_PLAN.md §4.8).

The dispatcher opens a case when its own rules cannot decide: a task ran out
of attempts, a verification failure could not be attributed, or a phase
review is due. A case is a Markdown dossier in WS/coord/cases/; an `oh -p`
handler reads it together with journal.md (every earlier decision of this
run, so similar problems get consistent answers) and records one decision
through handler_decide. The dispatcher applies the decision and appends it to
the journal.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from coord.events import EventLog


@dataclass
class Case:
    case_id: str
    kind: str          # exhausted | triage | blocked | review
    task_id: str
    path: Path

    @property
    def decision_path(self) -> Path:
        return self.path.with_name(f"{self.case_id}.decision.json")

    def decision(self) -> dict[str, Any] | None:
        try:
            return json.loads(self.decision_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None


class Casebook:
    def __init__(self, coord_dir: Path, events: EventLog):
        self.dir = Path(coord_dir) / "cases"
        self.journal = Path(coord_dir) / "journal.md"
        self.events = events

    def open(self, kind: str, task_id: str, title: str, sections: dict[str, str]) -> Case:
        self.dir.mkdir(parents=True, exist_ok=True)
        number = len(list(self.dir.glob("C*.md"))) + 1
        case_id = f"C{number:03d}-{task_id}-{kind}"
        lines = [f"# {case_id}：{title}", "", f"- 任务：{task_id}", f"- 类型：{kind}",
                 f"- 时间：{time.strftime('%Y-%m-%d %H:%M:%S')}", ""]
        for heading, body in sections.items():
            lines += [f"## {heading}", "", body.strip() or "（无）", ""]
        path = self.dir / f"{case_id}.md"
        path.write_text("\n".join(lines), encoding="utf-8")
        self.events.append("case_open", task=task_id, case=case_id, kind=kind, title=title)
        return Case(case_id, kind, task_id, path)

    def record(self, case: Case, decision: dict[str, Any] | None, applied: str) -> None:
        if not self.journal.exists():
            self.journal.write_text("# 异常处理记录\n\n本次运行中异常处理者做过的判断，按时间排列。\n\n", encoding="utf-8")
        d = decision or {}
        entry = (
            f"## {case.case_id}（{case.kind}，任务 {case.task_id}）\n\n"
            f"- 决定：{d.get('action', '无（处理者没有给出决定）')}"
            + (f"，目标 {d['target']}" if d.get("target") else "") + "\n"
            f"- 理由：{d.get('rationale', '').strip() or '（无）'}\n"
            f"- 给 worker 的反馈：{d.get('feedback', '').strip() or '（无）'}\n"
            f"- 调度器的处理：{applied}\n\n"
        )
        with self.journal.open("a", encoding="utf-8") as fh:
            fh.write(entry)
        self.events.append("case_decision", task=case.task_id, case=case.case_id, decision=d, applied=applied)
