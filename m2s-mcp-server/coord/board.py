"""Thin wrapper around the `br` CLI for one task board (PIPELINE_V3_PLAN.md §4.1).

Only the dispatcher writes a board. Every call passes `--json` (for commands
that print data) and `--lock-timeout`, and runs with RUST_LOG=error so br's
dependency logs stay out of stdout.
"""
from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from coord.policy import DISPATCHER_ACTOR, DONE_GATE, POLICY_YAML
from coord.toolchain import exe_path

LOCK_TIMEOUT_MS = 15000


class BrError(RuntimeError):
    def __init__(self, args: list[str], result: "BrResult"):
        super().__init__(f"br {' '.join(args)} failed (rc={result.rc}): {result.stderr.strip() or result.stdout.strip()}")
        self.result = result


@dataclass(frozen=True)
class BrResult:
    rc: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.rc == 0

    def json(self) -> Any:
        return json.loads(self.stdout)


def issues_of(payload: Any) -> list[dict[str, Any]]:
    """Normalize br's JSON shapes (a list, {"issues": [...]}, or one issue) to a list."""
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        if isinstance(payload.get("issues"), list):
            return issues_of(payload["issues"])
        if "id" in payload:
            return [payload]
    return []


class Br:
    """Run br against the board in ``workdir`` (the directory holding `.beads/`)."""

    def __init__(self, workdir: Path, exe: Path | None = None, actor: str = DISPATCHER_ACTOR):
        self.workdir = Path(workdir)
        self.exe = Path(exe) if exe else exe_path("br")
        self.actor = actor

    def run(self, *args: str, check: bool = True, actor: str | None = None) -> BrResult:
        argv = [str(self.exe), *args, "--lock-timeout", str(LOCK_TIMEOUT_MS), "--actor", actor or self.actor]
        proc = subprocess.run(
            argv,
            cwd=self.workdir,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env={**os.environ, "RUST_LOG": "error"},
        )
        result = BrResult(proc.returncode, proc.stdout, proc.stderr)
        if check and not result.ok:
            raise BrError(list(args), result)
        return result

    def init(self, policy: str = POLICY_YAML) -> None:
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.run("init", "-q")
        (self.workdir / ".beads" / "policy.yaml").write_text(policy, encoding="utf-8")

    def create(self, title: str) -> str:
        """Create an issue and return its id."""
        return self.run("q", title).stdout.strip()

    def ready_ids(self) -> list[str]:
        return [issue["id"] for issue in issues_of(self.run("ready", "--json").json())]

    def status(self, issue_id: str) -> str:
        issues = issues_of(self.run("show", issue_id, "--json").json())
        return str(issues[0]["status"]) if issues else ""


class TaskBoard:
    """Task-level view of one run's board: tasks are addressed by their template
    id (the issue title), and the description holds the immutable TaskSpec JSON."""

    def __init__(self, coord_dir: Path, br: Br | None = None):
        self.dir = Path(coord_dir) / "beads"
        self.br = br or Br(self.dir)
        self._ids: dict[str, str] = {}

    # --- setup ---

    def exists(self) -> bool:
        return (self.dir / ".beads" / "policy.yaml").is_file()

    def init(self) -> None:
        self.br.init()

    def add_task(self, spec: dict[str, Any], deps: list[str] | tuple[str, ...] = ()) -> str:
        labels = [f"kind:{spec['id']}", f"runner:{spec.get('runner', 'agent')}"]
        if spec.get("role"):
            labels.append(f"role:{spec['role']}")
        args = ["create", "--title", spec["id"], "--description", json.dumps(spec, ensure_ascii=False),
                "--labels", ",".join(labels), "--silent"]
        if deps:
            # "blocks:X" = the new issue waits for X (checked against `dep add`, 2026-10-30)
            args += ["--deps", ",".join(f"blocks:{self.issue_id(dep)}" for dep in deps)]
        issue_id = self.br.run(*args).stdout.strip()
        self._ids[spec["id"]] = issue_id
        return issue_id

    def add_dep(self, task_id: str, depends_on: str) -> None:
        self.br.run("dep", "add", self.issue_id(task_id), self.issue_id(depends_on))

    # --- lookup ---

    def _refresh(self) -> list[dict[str, Any]]:
        issues = issues_of(self.br.run("list", "--all", "--json", "--limit", "10000").json())
        self._ids = {str(i["title"]): str(i["id"]) for i in issues}
        return issues

    def issue_id(self, task_id: str) -> str:
        if task_id not in self._ids:
            self._refresh()
        return self._ids[task_id]

    def tasks(self) -> dict[str, dict[str, Any]]:
        """task id -> {"status", "issue", "spec"} for every task on the board."""
        out = {}
        for issue in self._refresh():
            try:
                spec = json.loads(issue.get("description") or "{}")
            except json.JSONDecodeError:
                spec = {}
            out[str(issue["title"])] = {"status": issue["status"], "issue": issue["id"], "spec": spec}
        return out

    def status(self, task_id: str) -> str:
        return self.br.status(self.issue_id(task_id))

    def _edge_titles(self, task_id: str, field: str) -> list[str]:
        payload = self.br.run("show", self.issue_id(task_id), "--json").json()
        issues = issues_of(payload)
        if not issues:
            return []
        return [str(edge.get("title")) for edge in issues[0].get(field) or [] if edge.get("title")]

    def dependencies(self, task_id: str) -> list[str]:
        """Task ids that task_id directly depends on (the board knows the
        children `_wire_deps` added for split families)."""
        return self._edge_titles(task_id, "dependencies")

    def dependents(self, task_id: str) -> list[str]:
        """Task ids that directly depend on task_id."""
        return self._edge_titles(task_id, "dependents")

    def ready(self) -> list[str]:
        if not self._ids:
            self._refresh()
        by_issue = {issue: task for task, issue in self._ids.items()}
        ready = []
        for issue in issues_of(self.br.run("ready", "--json", "--limit", "1000").json()):
            if issue["id"] not in by_issue:
                self._refresh()
                by_issue = {i: t for t, i in self._ids.items()}
            ready.append(by_issue.get(issue["id"], str(issue.get("title"))))
        return ready

    # --- transitions (only the dispatcher calls these) ---

    def claim(self, task_id: str, worker: str) -> None:
        """Assign the task to this attempt's worker; br refuses to re-claim an
        issue still assigned to the previous attempt, so clear that first."""
        issue = self.issue_id(task_id)
        first = self.br.run("update", issue, "--claim", actor=worker, check=False)
        if first.ok:
            return
        if "already assigned" not in first.stderr:
            raise BrError(["update", issue, "--claim"], first)
        self.br.run("update", issue, "--assignee", "")
        self.br.run("update", issue, "--claim", actor=worker)

    def set_status(self, task_id: str, status: str, comment: str | None = None) -> None:
        args = ["update", self.issue_id(task_id), "-s", status]
        if comment:
            args += ["--transition-comment", comment]
        self.br.run(*args)

    def report_gate(self, task_id: str, passed: bool) -> None:
        self.br.run(
            "gate", "report", self.issue_id(task_id), "--gate", DONE_GATE, "--provider", DISPATCHER_ACTOR,
            "--status", "pass" if passed else "fail", "--to", "closed",
        )

    def close(self, task_id: str, comment: str | None = None) -> None:
        args = ["close", self.issue_id(task_id)]
        if comment:
            args += ["--transition-comment", comment]
        self.br.run(*args)

    def reopen(self, task_id: str) -> None:
        """closed -> stale -> open, or rework/needs_human -> ... -> open."""
        status = self.status(task_id)
        if status == "closed":
            self.set_status(task_id, "stale")
            status = "stale"
        if status in ("stale", "needs_human"):
            self.set_status(task_id, "open", comment="dispatcher: reopened" if status == "needs_human" else None)
        elif status == "rework":
            pass  # rework is already in the ready group
        elif status == "in_progress":
            self.set_status(task_id, "open")

    def comment(self, task_id: str, text: str) -> None:
        self.br.run("comments", "add", self.issue_id(task_id), text)
