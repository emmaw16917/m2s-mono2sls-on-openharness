"""Workspace snapshots in a shadow git repository (PIPELINE_V3_PLAN.md §4.5).

The workspace gets its own `.git` only when v0 is tagged, and v1 tools treat
that repository as meaningful (run_validate(ref=...), S5 commits), so the
dispatcher must not create or touch it. Instead it keeps a separate repository
in WS/coord/shadow.git whose work tree is the workspace: a commit before each
worker starts, a diff after it exits, and a checkout to undo out-of-scope
writes. Bash writes are caught too, because the diff does not depend on which
tool wrote a file.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

# Never snapshotted: the dispatcher's own state, the workspace's real repo,
# and bulky build/cache trees.
EXCLUDES = (
    "/coord/",
    "node_modules/",
    "__pycache__/",
    ".aws-sam/",
    ".m2s-cov-flush*",
)


class SnapshotError(RuntimeError):
    pass


class Shadow:
    def __init__(self, workspace: Path):
        self.ws = Path(workspace).resolve()
        self.git_dir = self.ws / "coord" / "shadow.git"

    def _git(self, *args: str, check: bool = True) -> str:
        proc = subprocess.run(
            ["git", f"--git-dir={self.git_dir}", f"--work-tree={self.ws}", *args],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
        if check and proc.returncode != 0:
            raise SnapshotError(f"git {' '.join(args)}: {proc.stderr.strip()}")
        return proc.stdout

    def init(self) -> None:
        if (self.git_dir / "HEAD").is_file():
            return
        self.git_dir.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "-q", "--bare", str(self.git_dir)], check=True, capture_output=True)
        for key, value in (("core.bare", "false"), ("core.autocrlf", "false"), ("core.longpaths", "true"),
                           ("core.quotepath", "false"), ("user.name", "m2s-dispatcher"), ("user.email", "dispatcher@m2s.local")):
            self._git("config", key, value)
        (self.git_dir / "info").mkdir(exist_ok=True)
        (self.git_dir / "info" / "exclude").write_text("\n".join(EXCLUDES) + "\n", encoding="utf-8")

    def snapshot(self, label: str) -> str:
        """Commit the whole workspace and return the commit id."""
        self.init()
        self._git("add", "-A", ".")
        self._git("commit", "-q", "--allow-empty", "--no-verify", "-m", label)
        return self._git("rev-parse", "HEAD").strip()

    def changes(self, since: str) -> list[tuple[str, str]]:
        """(status, workspace-relative path) for everything changed since a snapshot."""
        current = self.snapshot(f"after {since[:12]}")
        out = self._git("diff", "--name-status", "--no-renames", "-z", since, current)
        parts = [p for p in out.split("\0") if p]
        return [(parts[i], parts[i + 1]) for i in range(0, len(parts) - 1, 2)]

    def restore(self, since: str, changes: list[tuple[str, str]]) -> None:
        """Undo the given changes: re-create modified/deleted files, remove added ones."""
        for status, rel in changes:
            target = self.ws / rel
            if status.startswith("A"):
                if target.is_file():
                    target.unlink()
            else:
                self._git("checkout", since, "--", rel)
