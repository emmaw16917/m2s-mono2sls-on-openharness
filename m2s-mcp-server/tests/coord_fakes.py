"""Shared fakes for dispatcher tests: scripted workers, file-based checks, no LLM."""
from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from coord.checks import CheckResult  # noqa: E402
from coord.dispatcher import Dispatcher  # noqa: E402
from coord.template import Template, load_template  # noqa: E402
from coord.workers import CommandLauncher  # noqa: E402

FAKE_WORKER = Path(__file__).resolve().parent / "fake_worker.py"


def fake_checks(template: Template) -> dict[str, Any]:
    """Check X of task T passes when storage/stage_reports/T.json says so:
    {"ok": true} passes every check of T, {"checks": {"X": false}} fails one. A check several
    tasks share answers from the latest of them that has a report; with no report at all it
    passes only if a dispatcher task owns it."""
    owners: dict[str, list[str]] = {}
    for spec in template.tasks:
        for name in spec.check:
            owners.setdefault(name, []).append(spec.id)
    dispatcher_tasks = {spec.id for spec in template.tasks if spec.runner == "dispatcher"}

    def make(name: str):
        def check(ws: Path) -> CheckResult:
            tasks = owners.get(name, [name])
            report = None
            for task in reversed(tasks):
                try:
                    report = json.loads((ws / "storage" / "stage_reports" / f"{task}.json").read_text(encoding="utf-8"))
                    break
                except (OSError, ValueError):
                    continue
            if report is None:
                report = {"ok": True} if dispatcher_tasks & set(tasks) else {}
            ok = bool((report.get("checks") or {}).get(name, report.get("ok", False)))
            return CheckResult(name, ok, f"fake {name} {'PASS' if ok else 'FAIL'}",
                               relay="" if ok else f"fake {name} failed for {tasks[-1]}")
        return check

    return {name: make(name) for name in {*owners, "gate_final", "consistency_pass", "validate_full", "judge_full", "delta_full"}}


class FakeGuard:
    def __init__(self) -> None:
        self.next: dict[str, list[str]] | None = None

    def capture(self) -> dict[str, Any]:
        return {}

    def load(self, baseline: dict[str, Any]) -> None:
        pass

    def violations(self) -> dict[str, list[str]]:
        found, self.next = self.next, None
        return found or {"repo_new": [], "repo_changed": [], "agents": []}

    def revert(self, paths: list[str]) -> None:
        pass


def ok_action(name: str):
    def action(spec) -> CheckResult:
        return CheckResult(name, True, f"fake {name}")
    return action


def make_dispatcher(tmp_path: Path, script: dict[str, Any], *, template: Template | None = None,
                    handler: bool = False, phase_review: bool = False, actions: dict[str, Any] | None = None,
                    guard: FakeGuard | None = None, timeouts: dict[str, float] | None = None,
                    stop_after: str | None = None, checks: dict[str, Any] | None = None) -> Dispatcher:
    template = template or load_template("v1")
    if timeouts:
        template = replace(template, tasks=tuple(replace(t, timeout_min=timeouts.get(t.id, t.timeout_min)) for t in template.tasks))
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    script_path = tmp_path / "script.json"
    script_path.write_text(json.dumps(script), encoding="utf-8")
    return Dispatcher(
        ws, monolith=tmp_path / "monolith", template=template,
        launcher=CommandLauncher([sys.executable, str(FAKE_WORKER), str(script_path)]),
        checks=checks or fake_checks(template),
        actions={"analyze_project": ok_action("analyze_project"), "tag_v0": ok_action("tag_v0"),
                 "final": ok_action("final"), **(actions or {})},
        handler=handler, phase_review=phase_review, role_prompts=lambda role: f"ROLE {role}",
        guard=guard or FakeGuard(), stamp=lambda: {"fake": True}, poll_s=0.05, log=lambda msg: None,
        stop_after=stop_after,
    )


def ok_report(task: str, **extra: Any) -> dict[str, Any]:
    return {"write": {f"storage/stage_reports/{task}.json": {"ok": True, **extra}}, "submit": "done"}


def fail_report(task: str, **extra: Any) -> dict[str, Any]:
    return {"write": {f"storage/stage_reports/{task}.json": {"ok": False, **extra}}, "submit": "done"}
