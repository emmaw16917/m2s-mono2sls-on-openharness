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

from coord.policy import DISPATCHER_ACTOR, POLICY_YAML
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
