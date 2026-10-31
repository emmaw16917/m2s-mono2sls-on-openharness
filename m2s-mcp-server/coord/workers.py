"""Starting and watching worker processes (PIPELINE_V3_PLAN.md §4.4).

One `oh -p` process per task attempt, configured entirely through files in
WS/coord/run/<task>-a<n>/:
    prompt.md      the task prompt (also returned by get_task_context)
    settings.json  --settings overlay: the role's system prompt and the
                   per-worker permission rules
    mcp.json       --mcp-config: m2s-server with this worker's environment
    stream.jsonl   stdout (--output-format stream-json)
    stderr.log
The role prompt goes through settings.json rather than --system-prompt because
agent definitions can exceed the Windows command-line limit (32K chars).
"""
from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from coord.checks import REPO
from coord.tools import run_dir as task_run_dir

OH_EXE = Path(sys.executable).parent / "Scripts" / "oh.exe"
AGENTS_DIR = Path.home() / ".openharness" / "agents"
SETTINGS_FILE = Path.home() / ".openharness" / "settings.json"
INLINE_PROMPT_LIMIT = 20000


@dataclass
class WorkerJob:
    task_id: str
    attempt: int
    role: str                 # agent name, or "handler"
    ws: Path
    coord_dir: Path
    system_prompt: str
    prompt: str
    scope: tuple[str, ...]
    max_turns: int
    timeout_s: float
    read_coord: bool = False  # only the exception handler may read WS/coord
    deny_read: tuple[str, ...] = ()  # directories closed to the worker (other runs' workspaces)

    @property
    def run_dir(self) -> Path:
        return task_run_dir(self.coord_dir, self.task_id, self.attempt)

    @property
    def worker_name(self) -> str:
        short = self.role.removeprefix("m2s-")
        return f"{short}-{self.task_id}-a{self.attempt}"

    def env(self) -> dict[str, str]:
        return {
            "M2S_WORKSPACE": str(self.ws),
            "M2S_COORD_DIR": str(self.coord_dir),
            "M2S_TASK_ID": self.task_id,
            "M2S_ATTEMPT": str(self.attempt),
            "M2S_ROLE": self.role,
            "PYTHONUTF8": "1",
        }


@dataclass
class WorkerHandle:
    task_id: str
    attempt: int
    worker: str
    pid: int
    started: float
    timeout_s: float
    run_dir: Path
    proc: subprocess.Popen | None = None
    kind: str = "task"        # task | handler
    meta: dict[str, Any] = field(default_factory=dict)

    def poll(self) -> int | None:
        """Exit code, or None while running. Adopted handles (no Popen) report 0 when gone."""
        if self.proc is not None:
            return self.proc.poll()
        return None if pid_alive(self.pid) else 0

    def timed_out(self, now: float | None = None) -> bool:
        return (now or time.time()) - self.started > self.timeout_s

    def kill(self) -> None:
        kill_tree(self.pid)
        if self.proc is not None:
            try:
                self.proc.wait(15)
            except subprocess.TimeoutExpired:
                pass


class Launcher(Protocol):
    def start(self, job: WorkerJob) -> WorkerHandle: ...


# --- process helpers --------------------------------------------------------------------


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        # os.kill(pid, 0) would *terminate* the process on Windows.
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            ok = ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
            return bool(ok) and code.value == STILL_ACTIVE
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def kill_tree(pid: int) -> None:
    """Kill a worker and everything it started (m2s-server, subagents)."""
    if not pid_alive(pid):
        return
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)], capture_output=True)
    else:
        import signal

        try:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
        except OSError:
            pass


def _popen(argv: list[str], job: WorkerJob, extra_env: dict[str, str], cwd: Path) -> WorkerHandle:
    job.run_dir.mkdir(parents=True, exist_ok=True)
    out = (job.run_dir / "stream.jsonl").open("w", encoding="utf-8")
    err = (job.run_dir / "stderr.log").open("w", encoding="utf-8")
    kwargs: dict[str, Any] = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    try:
        proc = subprocess.Popen(argv, stdout=out, stderr=err, stdin=subprocess.DEVNULL, cwd=cwd,
                                env={**os.environ, **extra_env}, **kwargs)
    finally:
        out.close()
        err.close()
    return WorkerHandle(job.task_id, job.attempt, job.worker_name, proc.pid, time.time(), job.timeout_s,
                        job.run_dir, proc=proc, kind="handler" if job.role == "handler" else "task")


# --- worker configuration -------------------------------------------------------------------


def role_prompt(role: str, agents_dir: Path = AGENTS_DIR) -> str:
    """Body of ~/.openharness/agents/<role>.md without its frontmatter."""
    text = (agents_dir / f"{role}.md").read_text(encoding="utf-8")
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            text = text[end + 4:]
    return text.strip()


def base_m2s_server(settings_file: Path = SETTINGS_FILE) -> dict[str, Any]:
    settings = json.loads(settings_file.read_text(encoding="utf-8"))
    server = (settings.get("mcp_servers") or {}).get("m2s-server")
    if not isinstance(server, dict):
        raise RuntimeError(f"{settings_file} has no mcp_servers.m2s-server entry to start workers with")
    return server


def worker_settings(job: WorkerJob, other_servers: list[str]) -> dict[str, Any]:
    ws = str(job.ws)
    home = str(Path.home())
    tmp = tempfile.gettempdir()
    path_rules = [
        {"pattern": f"{REPO}/m2s-mcp-server/*", "allow": False, "access": "write"},
        {"pattern": f"{REPO}/.openharness/*", "allow": False, "access": "write"},
        {"pattern": f"{home}/.openharness/*", "allow": False, "access": "write"},
    ]
    path_rules += [{"pattern": f"{d}/*", "allow": False} for d in job.deny_read]
    denied_commands: list[str] = []
    if not job.read_coord:
        path_rules.insert(0, {"pattern": f"{ws}/coord/*", "allow": False})
        denied_commands = ["*/coord/*", "*\\coord\\*"]
    writable = [f"{ws}/{pattern.replace('**', '*')}" for pattern in job.scope] + [f"{tmp}/*"]
    return {
        "system_prompt": job.system_prompt,
        "mcp_servers": {name: None for name in other_servers},
        "permission": {
            "mode": "full_auto",
            "path_rules": path_rules,
            "denied_commands": denied_commands,
            "writable_paths": writable,
        },
    }


class OhLauncher:
    """Start real `oh -p` workers."""

    def __init__(self, oh_exe: Path = OH_EXE, cwd: Path = REPO, settings_file: Path = SETTINGS_FILE):
        self.oh_exe = oh_exe
        self.cwd = cwd  # repo root: project skills (the m2s KBs) live in .openharness/skills there
        self.settings_file = settings_file

    def start(self, job: WorkerJob) -> WorkerHandle:
        job.run_dir.mkdir(parents=True, exist_ok=True)
        settings = json.loads(self.settings_file.read_text(encoding="utf-8"))
        others = [name for name in (settings.get("mcp_servers") or {}) if name != "m2s-server"]
        server = dict(base_m2s_server(self.settings_file))
        server["env"] = {**(server.get("env") or {}), **job.env()}

        (job.run_dir / "prompt.md").write_text(job.prompt, encoding="utf-8")
        (job.run_dir / "settings.json").write_text(json.dumps(worker_settings(job, others), ensure_ascii=False, indent=1), encoding="utf-8")
        (job.run_dir / "mcp.json").write_text(json.dumps({"mcpServers": {"m2s-server": server}}, ensure_ascii=False, indent=1), encoding="utf-8")

        prompt = job.prompt
        if len(prompt) > INLINE_PROMPT_LIMIT:
            prompt = ("完整的任务说明太长，没有放在这里。先调用 mcp__m2s-server__get_task_context 取回完整说明，"
                      f"然后按说明完成任务。任务编号 {job.task_id}，第 {job.attempt} 次尝试。")
        argv = [
            str(self.oh_exe), "-p", prompt,
            "--settings", str(job.run_dir / "settings.json"),
            "--mcp-config", str(job.run_dir / "mcp.json"),
            "--permission-mode", "full_auto",
            "--max-turns", str(job.max_turns),
            "--output-format", "stream-json",
            "--cwd", str(self.cwd),
        ]
        return _popen(argv, job, {"PYTHONUTF8": "1"}, self.cwd)


class CommandLauncher:
    """Start an arbitrary command per job (tests: tests/fake_worker.py).

    The command gets the job's environment (M2S_COORD_DIR, M2S_TASK_ID, ...)
    and the run directory as its last argument; prompt.md is written first.
    """

    def __init__(self, argv: list[str], cwd: Path | None = None):
        self.argv = argv
        self.cwd = cwd

    def start(self, job: WorkerJob) -> WorkerHandle:
        job.run_dir.mkdir(parents=True, exist_ok=True)
        (job.run_dir / "prompt.md").write_text(job.prompt, encoding="utf-8")
        return _popen([*self.argv, str(job.run_dir)], job, job.env(), self.cwd or job.ws)
