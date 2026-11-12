"""The v3 dispatcher (PIPELINE_V3_PLAN.md §4.3): deterministic, restartable.

    python -m coord.dispatcher run     --ws WS --monolith DIR [--max-workers 1]
    python -m coord.dispatcher status  --ws WS
    python -m coord.dispatcher resolve --ws WS --task S2 (--accept | --reopen | --reject) --comment "..." [--feedback "..."]
    python -m coord.dispatcher rework  --ws WS --task S3c --comment "..." [--feedback "..."] [--recheck]

resolve and rework take --open-issue TEXT (repeatable): a question the person leaves
open on purpose; it goes into the final report. resolve also takes --ruling-file R.json
(repeatable): a ruling the person makes (coord/rulings.py). A ruling gate
(HUMAN_R-...) is resolved with --accept (confirm the ruling) or --reject.

Loop: take ready tasks from the board in template order, run dispatcher
actions itself, start one worker per agent task, and when a worker exits,
check the attempt (write scope, tools unchanged, submission, done checks).
A pass closes the task; a failure feeds the check output into the next
attempt; when the rules run out, an exception handler (LLM) decides once,
and after that a person does.

All state lives on the board (task status) and in events.jsonl (attempts,
feedback, rounds), so the process can be killed and restarted at any point.
The dispatcher never calls an LLM itself.

v1 rules ported from .openharness/skills/m2s-migrate/SKILL.md:
- V1 open spec_issues -> S1 rules on them -> V1 reruns (at most 2 rounds);
- V2 failure -> rework the stages its routing names (code S2, template S3c,
  spec S1), then V2 reruns; "undetermined" goes to the exception handler;
- a worker that reports an upstream stage as the cause sends the rework there;
- after V2 passes: tag v0 in the workspace repo, then S5, then the final check
  (which reverts S5's changes once if they break it).
"""
from __future__ import annotations

import argparse
import inspect
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

from coord import facts as facts_mod
from coord import harness, notices, rooms, rulings
from coord.board import TaskBoard
from coord.checks import CHECKS, CheckResult, ToolGuard, _clip, _read_json
from coord.events import EventLog
from coord.handler import Case, Casebook
from coord.locks import LockHandle, Locks
from coord.policy import DISPATCHER_ACTOR
from coord.prompts import HANDLER_SYSTEM_PROMPT, handler_prompt, worker_prompt
from coord.snapshot import Shadow
from coord.template import Template, TaskSpec, in_scope, load_template, scopes_conflict
from coord.tools import submission_path
from coord.workers import Launcher, OhLauncher, WorkerHandle, WorkerJob, role_prompt

# Changes no task is blamed for: dispatcher state, the v1 orchestrator's TODO, caches.
ALWAYS_ALLOWED = ("coord/**", "storage/TODO.md", "**/node_modules/**", "**/__pycache__/**")
# Workspace files only the dispatcher writes; rebuilt from the event log after every worker.
DISPATCHER_FILES = (harness.WORKSPACE_FILE.as_posix(), rulings.WORKSPACE_FILE.as_posix(),
                    facts_mod.WORKSPACE_FILE.as_posix())
# Who fixes what a verification failure points at, by role: v1 S2 / S3c / S1, v2 I1 / I2 / A1.
ROUTE_ROLES = {"code": "m2s-code-developer", "template": "m2s-sam-engineer", "spec": "m2s-architect", "blueprint": "m2s-architect"}
RELAY_FIELDS = ("function", "spec_refs", "expected_per_spec", "observed")
# run_judge: a function that cannot start is a packaging problem, whatever else failed.
_START_FAILURE = re.compile(r"could not be started|Cannot find module|ImportModuleError|ModuleNotFoundError|"
                            r"No module named|ERR_MODULE_NOT_FOUND|sam build failed")
JUDGE_TRIAGE_RULES = ("裁判已经冻结，分诊不能改裁判（judge/），也不能放宽它的比较。可以退回的任务：代码的问题给 I1，打包和模板的问题给 I2，"
                      "蓝图和适配映射的问题给 A1；单体的这个行为其实是迁移带来的变化、需要裁定的，交给人。"
                      "feedback 写失败的函数、事实库或规则的出处、期望和实际；裁判用例对实现线可见，可以直接引用。")
MAX_CRASHES = 3
# A worker whose session ended on a model-API failure (OpenHarness gave up after its own
# retries) did not fail the task: it does not use an attempt, and the next spawn waits.
INFRA_BACKOFF_S = (300, 600, 1200, 1800)
MAX_INFRA_FAILURES = 8  # per round, about 2.5 hours of waiting before a person is asked
MAX_SPAWNS_PER_TASK = 12  # backstop against a rule loop; normal limits are far lower
MAX_ACTION_RUNS = 20  # the same for the dispatcher's own actions: a loop over them spawns nothing and would spin unseen
HANDLER_TIMEOUT_S = 20 * 60
HANDLER_MAX_TURNS = 60

EXIT_DONE, EXIT_HUMAN, EXIT_STALLED, EXIT_ERROR, EXIT_STOPPED = 0, 3, 4, 1, 5
HUMAN_KINDS = {"human_accept": "接受", "human_reopen": "重做", "human_rework": "退回", "human_reject": "否决"}


_TRANSIENT_API = re.compile(r"\b(429|5\d\d|overloaded|rate.?limit(ed)?|time[sd]? ?out)\b", re.I)


def stream_end_error(run_dir: Path) -> str | None:
    """The message of the worker stream's last event when that event is an error, else None."""
    try:
        with (run_dir / "stream.jsonl").open("rb") as fh:
            fh.seek(0, 2)
            fh.seek(max(0, fh.tell() - 8192))
            tail = fh.read().decode("utf-8", errors="replace")
    except OSError:
        return None
    for line in reversed(tail.splitlines()):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except ValueError:
            return None
        return str(event.get("message") or "") if isinstance(event, dict) and event.get("type") == "error" else None
    return None


def is_infra_error(message: str | None) -> bool:
    """OpenHarness ends a session with 'Network error: ...' or 'API error: ...' once its retries run out."""
    if not message or is_content_refusal(message):
        return False
    return message.startswith("Network error:") or (message.startswith("API error:") and bool(_TRANSIENT_API.search(message)))


def is_content_refusal(message: str | None) -> bool:
    """The model service refused the input itself (DashScope content moderation,
    seen on 2026-11-01 when J1 sessions had read 90-180K characters)."""
    return bool(message) and "data_inspection_failed" in message


def other_run_dirs(ws: Path, monolith: Path | None) -> tuple[str, ...]:
    """Workspaces of other runs, closed to this run's workers: the siblings of WS, and
    siblings of WS's parent named like it (runs-prev-*). Never the monolith or its parents."""
    found: list[Path] = []
    parent = ws.parent
    if parent.is_dir():
        found += [d for d in parent.iterdir() if d.is_dir() and d != ws]
    if parent.parent.is_dir():
        found += [d for d in parent.parent.iterdir()
                  if d.is_dir() and d != parent and d.name.startswith(parent.name)]
    if monolith is not None:
        found = [d for d in found if d != monolith and d not in monolith.parents]
    return tuple(str(d) for d in sorted(found))


def closed_to(spec: TaskSpec, ws: Path, monolith: Path | None) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Directories a task may not read (TaskSpec.deny_read) and the shell
    command patterns that would name them (V2P-06).

    "monolith" closes the monolith's source, and its parent directory too
    unless that is an ancestor of WS: a grep started at the parent would read
    the monolith's files (the checker sees only the root it is given). A bash
    command naming the closed directory is refused; reads that do not name it
    (a recursive grep from an ancestor) remain the known gap of W0.5."""
    dirs: list[str] = []
    cmds: list[str] = []
    entries: list[str] = []
    for entry in spec.deny_read:
        if entry == "judge_outputs":  # what the monolith did and the reconciliation (judge.MONOLITH_OUTPUTS)
            import judge
            entries += [f"judge/{name}" for name in judge.MONOLITH_OUTPUTS]
        else:
            entries.append(entry)
    for entry in entries:
        if entry == "monolith":
            if monolith is None:
                continue
            parent = monolith.parent
            if parent != ws and parent not in ws.parents and parent.parent != parent:
                dirs += [str(monolith), str(parent)]
                cmds.append(f"*{parent.name}*")
                continue
            target = monolith
        else:
            target = ws / entry.replace("\\", "/").removesuffix("/**").removesuffix("/*")
            if target.parent == ws:  # a command run in the workspace names it by its relative path
                cmds += [f"*{target.name}/*", f"*{target.name}\\*"]
        dirs.append(str(target))
        tail = f"{target.parent.name}/{target.name}"
        cmds += [f"*{tail}*", "*" + tail.replace("/", "\\") + "*"]
    return tuple(dict.fromkeys(dirs)), tuple(dict.fromkeys(cmds))


def _git(ws: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(ws), *args], capture_output=True, text=True, encoding="utf-8", errors="replace")


class Dispatcher:
    def __init__(
        self,
        ws: Path | str,
        monolith: Path | str | None = None,
        template: Template | str = "v1",
        launcher: Launcher | None = None,
        checks: dict[str, Callable[[Path], CheckResult]] | None = None,
        actions: dict[str, Callable[[TaskSpec], CheckResult]] | None = None,
        max_workers: int = 1,
        poll_s: float = 5.0,
        handler: bool = True,
        phase_review: bool = True,
        role_prompts: Callable[[str], str] = role_prompt,
        guard: ToolGuard | None = None,
        stamp: Callable[[], dict[str, Any]] = harness.stamp,
        log: Callable[[str], None] = print,
        stop_after: str | None = None,
        locks: Locks | None = None,
    ):
        self.ws = Path(ws).resolve()
        self.coord = self.ws / "coord"
        self.monolith = Path(monolith).resolve() if monolith else None
        self.template = load_template(template) if isinstance(template, str) else template
        # --stop-after: start only this task and what it depends on (plus the human gates the
        # flows add); stop once it is closed. A later run without the flag carries on.
        self.stop_after = stop_after
        try:
            self.stop_set = set(self.template.upstream(stop_after)) if stop_after else None
        except KeyError:
            raise ValueError(f"--stop-after: {stop_after} is not a task of template {self.template.name}") from None
        self.coord.mkdir(parents=True, exist_ok=True)
        self.events = EventLog(self.coord / "events.jsonl")
        self.board = TaskBoard(self.coord)
        self.shadow = Shadow(self.ws)
        self.launcher = launcher or OhLauncher()
        self.checks = checks if checks is not None else CHECKS
        self.actions = {"analyze_project": self._act_analyze, "tag_v0": self._act_tag_v0,
                        "final": self._act_final, "human": self._act_human,
                        "run_monolith": self._act_run_monolith, "reconcile_spec": self._act_reconcile,
                        "freeze_judge": self._act_freeze, "check_adapter": self._act_check_adapter,
                        "run_judge": self._act_run_judge, "run_mutants": self._act_run_mutants,
                        "gap_scan": self._act_gap_scan, "tag_b0": self._act_tag_b0, "final_v2": self._act_final_v2,
                        "archive_run": self._act_archive, **(actions or {})}
        self.max_workers = max_workers
        self.poll_s = poll_s
        self.handler_enabled = handler
        self.phase_review = phase_review
        self.role_prompts = role_prompts
        self.guard = guard or ToolGuard()
        self.stamp = stamp
        self.log = log
        self.cases = Casebook(self.coord, self.events)
        self.running: dict[str, WorkerHandle] = {}
        self.waiting = False  # a ready task waits out an infra backoff
        self.foreign_runs = other_run_dirs(self.ws, self.monolith)
        # Machine-wide resources shared with other workspaces (v3 M2/W2.8).
        self.locks = locks or Locks()
        self.worker_slots = int(os.environ.get("M2S_WORKER_SLOTS", "3"))
        self.worker_locks: dict[str, LockHandle] = {}
        self.mail_env: dict[str, str] = {}  # M3: set in setup when M2S_MAIL is enabled
        self._scope_wait_logged: set[str] = set()

    # ------------------------------------------------------------------ setup / resume

    def _ensure_mail(self) -> None:
        if self.mail_env or os.environ.get("M2S_MAIL", "").lower() not in ("1", "true", "yes"):
            return
        from coord import mail

        try:
            server = mail.ensure_server()
            mail.ensure_project(server, self.ws)
            self.mail_env = mail.mail_env(server, self.ws)
            self.events.append("mail_start", url=server.url)
            self.log(f"mail: {server.url}")
        except Exception as exc:  # messaging must never stop the pipeline
            self.events.append("mail_error", error=f"{type(exc).__name__}: {exc}")
            self.log(f"mail unavailable: {exc}")

    def setup(self) -> None:
        self._ensure_mail()
        if not self.board.exists():
            if self.monolith is None:
                raise ValueError("a new run needs --monolith")
            self.board.init()
            for spec in self.template.tasks:
                if spec.split != "none":
                    continue  # expanded after its trigger closes (v3 M2/W2.7)
                deps: list[str] = []
                for dep in spec.deps:
                    family = dep[:-2] if dep.endswith(":*") else (dep[:-7] if dep.endswith(":shared") else None)
                    if family and family in {t.id for t in self.template.tasks}:
                        # wait for the trigger; _wire_deps adds the children
                        trigger = self.template.get(family).expand_after
                        deps.extend(t for t in trigger if t not in deps)
                    elif not dep.endswith(":*"):
                        deps.append(dep)
                self.board.add_task(spec.to_json(), deps)
            stamp = self.stamp()
            self.events.append("harness", stamp=stamp)
            self.events.append("run_start", ws=str(self.ws), monolith=str(self.monolith),
                               template=self.template.name, max_workers=self.max_workers, stop_after=self.stop_after)
            self.events.append("tool_baseline", baseline=self.guard.capture())
            self._write_dispatcher_files()
            self.shadow.init()
            self.log(f"new run: {len([t for t in self.template.tasks if t.split == 'none'])} tasks on the board")
            return
        start = next((e for e in self.events.all() if e["type"] == "run_start"), None)
        if self.monolith is None and start:
            self.monolith = Path(start["monolith"])
        baseline = next((e for e in self.events.all() if e["type"] == "tool_baseline"), None)
        if baseline:
            self.guard.load(baseline["baseline"])
        else:
            self.guard.capture()
        self.events.append("run_resume", stop_after=self.stop_after)
        self.events.append("harness", stamp=self.stamp(), on="resume")  # the tools may have changed between segments
        self._write_dispatcher_files()
        self._reconcile()

    def _write_dispatcher_files(self) -> list[str]:
        """DISPATCHER_FILES are rebuilt from the event log, so a worker that changes one
        changes nothing (see _evaluate). Returns the files that had to be written."""
        events = self.events.all()
        changed = []
        if harness.write_workspace_record(self.ws, [e["stamp"] for e in events if e["type"] == "harness"]):
            changed.append(harness.WORKSPACE_FILE.as_posix())
        entries = [e["entry"] for e in events if e["type"] == "ruling"]
        if (entries or (self.ws / rulings.WORKSPACE_FILE).exists()) and rulings.write_workspace_file(self.ws, entries):
            changed.append(rulings.WORKSPACE_FILE.as_posix())
        if self._write_facts_file():
            changed.append(facts_mod.WORKSPACE_FILE.as_posix())
        return changed

    def _facts_state(self) -> dict[str, Any] | None:
        return next((e for e in reversed(self.events.all()) if e["type"] == "facts"), None)

    def _write_facts_file(self) -> bool:
        last = self._facts_state()
        return facts_mod.write(self.ws, last) if last else False

    def _judge_version(self) -> int | None:
        event = next((e for e in reversed(self.events.all()) if e["type"] == "judge_frozen"), None)
        try:
            return int(event["version"]) if event and event.get("version") is not None else None
        except (TypeError, ValueError):
            return None

    def _record_facts_version(self, task_id: str) -> None:
        """v3 M4/W4.1: every fact-base change gets a version and an artifact
        notice. The sha dedupes: a recheck of an unchanged fact base is a no-op.
        Tasks record the version they start from in their spawn event."""
        spec = self._spec(task_id)
        if spec.role != "m2s-spec-annotator":
            return
        import hashlib

        path = self.ws / "storage" / "analysis_report.json"
        try:
            sha = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            return
        last = self._facts_state()
        if last and last.get("sha") == sha:
            return
        version = int(last.get("version") or 0) + 1 if last else 1
        self.events.append("facts", task=task_id, version=version, sha=sha)
        self._write_facts_file()
        self.log(f"facts v{version} ({sha[:12]}) after {task_id}")
        try:
            notices.post_notice(self.ws, "m2s-server", "artifact",
                                {"facts_version": version, "sha": sha, "by": task_id},
                                subject=f"facts v{version}", task=task_id)
        except Exception as exc:  # messaging must never stop the pipeline
            self.events.append("notice_error", task=task_id, error=f"{type(exc).__name__}: {exc}")

    def _ruling_entries(self) -> list[dict[str, Any]]:
        return [e["entry"] for e in self.events.all() if e["type"] == "ruling"]

    def _reconcile(self) -> None:
        """Bring board and processes back in line after a dispatcher restart."""
        for task_id, info in self.board.tasks().items():
            status = info["status"]
            if status in ("in_progress", "checking"):
                # A rework that happened under an older dispatcher may not have
                # cancelled this task; its worker is working on the old upstream.
                upstream = sorted(set(self._upstream_open(task_id)) | set(self._upstream_reopened(task_id)))
                if upstream:
                    self._cancel_for_upstream(task_id, upstream)
                    continue
            spawn = self.events.last(task_id, "spawn")
            exited = self.events.last(task_id, "worker_exit")
            live_spawn = spawn and (not exited or exited["seq"] < spawn["seq"])
            if status == "in_progress" and live_spawn:
                handle = WorkerHandle(task_id, spawn["attempt"], spawn["worker"], spawn["pid"], spawn["started"],
                                      spawn["timeout_s"], Path(spawn["run_dir"]))
                if handle.poll() is None:
                    self.running[task_id] = handle
                    lock = self.locks.acquire_count("workers", self.worker_slots, task_id)
                    if lock:
                        self.worker_locks[task_id] = lock
                    self.events.append("adopt", task=task_id, pid=spawn["pid"])
                    self.log(f"{task_id}: worker {spawn['worker']} still running, adopted")
                else:
                    self._on_exit(handle, timed_out=False, rc=None)
            elif status == "in_progress":
                self.board.set_status(task_id, "open")
                self.events.append("status", task=task_id, to="open", reason="reconcile: no live worker")
            elif status == "checking" and spawn and not self._open_case_for(task_id):
                self.events.append("recheck_after_restart", task=task_id)
                sub = _read_json(submission_path(self.coord, task_id, spawn["attempt"]))
                self._evaluate(self._spec(task_id), spawn["attempt"], sub, spawn["snapshot"], timed_out=False)
            elif status == "checking" and not spawn and not self._open_case_for(task_id):
                self.board.report_gate(task_id, False)
                self.board.set_status(task_id, "rework")
        decided = {e.get("case") for e in self.events.all() if e["type"] == "case_decision"}
        for event in self.events.all():
            if event["type"] == "handler_spawn" and event["case"] not in decided:
                case = Case(event["case"], event["kind"], event["task"], Path(event["case_file"]))
                handle = WorkerHandle(event["case"], 1, "handler", event["pid"], event["started"], HANDLER_TIMEOUT_S,
                                      Path(event["run_dir"]), kind="handler", meta={"case": case})
                if handle.poll() is None:
                    self.running[f"handler:{case.case_id}"] = handle
                else:
                    self._on_handler_exit(handle, timed_out=False)
        self._ensure_expansions()

    def _open_case_for(self, task_id: str) -> bool:
        decided = {e.get("case") for e in self.events.all() if e["type"] == "case_decision"}
        return any(e["type"] == "handler_spawn" and e["task"] == task_id and e["case"] not in decided
                   for e in self.events.all())

    # ------------------------------------------------------------------ main loop

    def run(self, max_cycles: int | None = None) -> int:
        self.setup()
        cycles = 0
        while True:
            self._collect()
            started = self._start_ready()
            if not self.running and not started and not self.waiting:
                outcome = self._outcome()
                if outcome is not None:
                    return self._finish(outcome)
            cycles += 1
            if max_cycles is not None and cycles >= max_cycles:
                return -1
            if (self.running or self.waiting) and not started:
                time.sleep(self.poll_s)

    def _spec(self, task_id: str) -> TaskSpec:
        try:
            return self.template.get(task_id)
        except KeyError:
            return TaskSpec.from_json(self.board.tasks()[task_id]["spec"])

    def _paused(self) -> bool:
        return any(h.kind == "handler" and h.meta["case"].kind == "review" for h in self.running.values())

    def _in_stop_set(self, task_id: str) -> bool:
        if self.stop_set is None or task_id in self.stop_set:
            return True
        template_ids = {t.id for t in self.template.tasks}
        if task_id in template_ids:
            return False
        # A dynamic child (S0-identity, S2-shared, ...) belongs to its family:
        # --stop-after P0 must not run it. Ids the flows add (HUMAN_*) stay allowed.
        family = task_id.split("-", 1)[0]
        if family in template_ids:
            return family in self.stop_set
        return True  # human gates added by the flows

    def _busy_workers(self) -> int:
        return sum(1 for h in self.running.values() if h.kind == "task")

    def _backing_off(self, task_id: str) -> bool:
        last = self.events.last(task_id, "worker_exit")
        return bool(last and last.get("retry_after") and last["retry_after"] > time.time())

    def _start_ready(self) -> int:
        self.waiting = False
        self._ensure_room_turns()
        if self._paused() or self._busy_workers() >= self.max_workers:
            return 0
        started = 0
        ready = sorted((t for t in self.board.ready() if t not in self.running and self._in_stop_set(t)),
                       key=lambda t: (self.template.order(t), 0 if self._spec(t).extra.get("package") == "shared" else 1, t))
        held = [t for t in ready if self._backing_off(t)]
        self.waiting = bool(held)
        ready = [t for t in ready if t not in held]
        running_specs = [self._spec(t) for t, h in self.running.items() if h.kind == "task"]
        chosen: list[TaskSpec] = []
        for task_id in ready:
            if self._busy_workers() >= self.max_workers:
                break
            spec = self._spec(task_id)
            # W2.8: two tasks whose scopes can name the same file never run together.
            if spec.runner == "agent" and any(scopes_conflict(spec.scope, other.scope)
                                              for other in [*running_specs, *chosen]):
                if task_id not in self._scope_wait_logged:
                    self.log(f"{task_id}: waits, its scope overlaps a running task")
                    self._scope_wait_logged.add(task_id)
                self.waiting = True
                continue
            lock = None
            if spec.runner == "agent":
                lock = self.locks.acquire_count("workers", self.worker_slots, task_id)
                if lock is None:
                    self.waiting = True
                    break
            self._start(task_id)
            if lock is not None:
                if task_id in self.running:
                    self.worker_locks[task_id] = lock
                else:
                    lock.release()  # recheck: no worker was spent
            started += 1
            self._scope_wait_logged.discard(task_id)
            chosen.append(spec)
            if self._paused():
                break
        return started

    def _start(self, task_id: str) -> None:
        spec = self._spec(task_id)
        infra = sum(1 for e in self.events.of_task(task_id, "worker_exit") if e.get("infra"))
        if self.events.count("spawn", task_id) - infra >= MAX_SPAWNS_PER_TASK:
            self._escalate(task_id, f"{task_id} 已经启动过 {MAX_SPAWNS_PER_TASK} 次 worker，调度器不再自动重试")
            return
        if spec.runner == "dispatcher" and self.events.count("action_start", task_id) >= MAX_ACTION_RUNS:
            self._escalate(task_id, f"{task_id} 已经运行过 {MAX_ACTION_RUNS} 次，流转规则可能在循环，调度器不再自动重试")
            return
        reopen = self.events.reopen_mode(task_id)
        first_start = reopen is None and not self.events.of_task(task_id, "spawn", "recheck")
        if spec.runner == "agent" and (reopen == "recheck" or (first_start and spec.start == "recheck")):
            self._recheck(spec)
        elif spec.runner == "dispatcher":  # actions are cheap and deterministic: a stale one runs again
            self._run_action(spec)
        else:
            self._spawn(spec)

    def _collect(self) -> None:
        for key, handle in list(self.running.items()):
            if self.running.get(key) is not handle:
                # A rework handled earlier in this pass cancelled it: _make_stale
                # already killed it and took it off self.running.
                continue
            rc = handle.poll()
            timed_out = rc is None and handle.timed_out()
            if rc is None and not timed_out:
                continue
            if timed_out:
                handle.kill()
            self.running.pop(key, None)
            lock = self.worker_locks.pop(key, None)
            if lock:
                lock.release()
            if handle.kind == "handler":
                self._on_handler_exit(handle, timed_out)
            else:
                self._on_exit(handle, timed_out, rc)

    # ------------------------------------------------------------------ starting tasks

    def _spawn(self, spec: TaskSpec) -> None:
        # Attempt numbers run across rounds so run dirs and submissions never collide;
        # max_attempts is counted per round (events.attempts).
        attempt = self.events.count("spawn", spec.id) + 1
        feedback = [{"source": f.get("source"), "text": f.get("text", "")} for f in self.events.pending_feedback(spec.id)]
        closed, closed_cmds = closed_to(spec, self.ws, self.monolith)
        job = WorkerJob(
            task_id=spec.id, attempt=attempt, role=spec.role, ws=self.ws, coord_dir=self.coord,
            system_prompt=self.role_prompts(spec.role),
            prompt=worker_prompt(spec, self.ws, self.monolith, attempt, feedback),
            scope=spec.scope, max_turns=spec.max_turns, timeout_s=spec.timeout_min * 60,
            deny_read=self.foreign_runs + closed, deny_commands=closed_cmds,
            deny_write=(str(self.ws / "judge"),) if self._judge_frozen() else (), mode=spec.mode or "",
            pipeline=self.template.name, extra_env=self.mail_env,
        )
        self.board.claim(spec.id, job.worker_name)
        snapshot = self.shadow.snapshot(f"before {spec.id} a{attempt}")
        handle = self.launcher.start(job)
        self.running[spec.id] = handle
        self.events.append("spawn", task=spec.id, attempt=attempt, worker=job.worker_name, pid=handle.pid,
                           started=handle.started, timeout_s=job.timeout_s, run_dir=str(handle.run_dir),
                           snapshot=snapshot, feedback_items=len(feedback),
                           facts_version=(self._facts_state() or {}).get("version"), judge_version=self._judge_version())
        self.log(f"{spec.id}: started {job.worker_name} (pid {handle.pid})")

    def _recheck(self, spec: TaskSpec) -> None:
        """A closed task went stale; rerun its checks before spending a worker on it."""
        self.board.claim(spec.id, DISPATCHER_ACTOR)
        self.board.set_status(spec.id, "checking")
        self.events.append("recheck", task=spec.id)
        results = self._done_checks(spec)
        self.events.append("checks", task=spec.id, mode="recheck", results=[r.to_event() for r in results])
        if all(r.ok for r in results):
            self.log(f"{spec.id}: recheck passed")
            self._pass(spec.id)
        else:
            self.log(f"{spec.id}: recheck failed, a worker will redo it")
            self.board.report_gate(spec.id, False)
            self._feedback(spec.id, "recheck", self._relay(spec, results))
            self.board.set_status(spec.id, "rework")

    def _run_action(self, spec: TaskSpec) -> None:
        self.board.claim(spec.id, DISPATCHER_ACTOR)
        self.events.append("action_start", task=spec.id, action=spec.action)
        try:
            result = self.actions[spec.action](spec)
        except Exception as exc:  # an action bug must not kill the run
            result = CheckResult(spec.action or spec.id, False, f"{type(exc).__name__}: {exc}")
        self.board.set_status(spec.id, "checking")
        results = [result]
        if result.ok:
            results += self._done_checks(spec)
        self.events.append("checks", task=spec.id, mode="action", results=[r.to_event() for r in results])
        if all(r.ok for r in results):
            self.log(f"{spec.id}: {result.summary}")
            self._pass(spec.id)
        elif result.detail.get("escalate"):
            self._escalate(spec.id, result.summary)
        elif not self._flow_action_failed(spec, result):
            self.board.report_gate(spec.id, False)
            self._open_case("exhausted", spec.id, f"调度器执行 {spec.action} 没有通过",
                            {"检查结果": self._results_text(results)})

    # ------------------------------------------------------------------ evaluating attempts

    def _on_exit(self, handle: WorkerHandle, timed_out: bool, rc: int | None) -> None:
        task_id, attempt = handle.task_id, handle.attempt
        sub = _read_json(submission_path(self.coord, task_id, attempt))
        api_error = stream_end_error(handle.run_dir) if sub is None and not timed_out else None
        infra = is_infra_error(api_error)
        # rc None / -1: the exit code is unknown (the worker died while the dispatcher was down)
        crashed = sub is None and not timed_out and (infra or rc != 0)
        extra: dict[str, Any] = {}
        wait = 0
        if infra:
            n = self.events.crashes(task_id, infra=True) + 1
            wait = INFRA_BACKOFF_S[min(n, len(INFRA_BACKOFF_S)) - 1]
            extra = {"infra": True, "api_error": (api_error or "")[:300], "retry_after": time.time() + wait}
        self.events.append("worker_exit", task=task_id, attempt=attempt, rc=rc, timed_out=timed_out,
                           submitted=sub is not None, crashed=crashed, duration_s=round(time.time() - handle.started),
                           **extra)
        spawn = self.events.last(task_id, "spawn")
        if infra:
            n = self.events.crashes(task_id, infra=True)
            if n >= MAX_INFRA_FAILURES:
                self.board.set_status(task_id, "checking")
                self._escalate(task_id, f"连续 {n} 次因为模型 API 不可用而中断（最后一次：{(api_error or '')[:200]}），见 {handle.run_dir}")
            else:
                self.log(f"{task_id}: model API unreachable ({(api_error or '')[:80]}); next try in {wait // 60} min")
                self.board.set_status(task_id, "open")
            return
        if crashed:
            self.log(f"{task_id}: worker crashed (rc={rc})")
            if self.events.crashes(task_id, infra=False) >= MAX_CRASHES:
                self.board.set_status(task_id, "checking")
                self._escalate(task_id, f"worker 连续崩溃 {MAX_CRASHES} 次（最后一次 rc={rc}），见 {handle.run_dir}")
            else:
                self.board.set_status(task_id, "open")
            return
        self.board.set_status(task_id, "checking")
        self._evaluate(self._spec(task_id), attempt, sub, spawn["snapshot"], timed_out)

    def _write_scope_outside(self, spec: TaskSpec, changes: list[tuple[str, str]]) -> list[tuple[str, str]]:
        """Paths this task changed outside its scope.

        With parallel work packages, a task's snapshot window also contains the
        files another task legitimately wrote. Those paths belong to the other
        task's scope, so they are neither this task's violation nor ours to
        revert (the M2 acmeair run lost S2 output to a concurrent V1 check)."""
        other_scopes = [self._spec(t).scope for t in self.board.tasks() if t != spec.id]
        return [(status, path) for status, path in changes
                if not in_scope(path, spec.scope) and not in_scope(path, ALWAYS_ALLOWED)
                and not any(in_scope(path, scope) for scope in other_scopes)]

    def _evaluate(self, spec: TaskSpec, attempt: int, sub: dict[str, Any] | None, snapshot: str, timed_out: bool) -> None:
        results: list[CheckResult] = []

        changes = self.shadow.changes(snapshot)
        # Files the dispatcher owns are rebuilt rather than reverted: the dispatcher itself may
        # have written one while this worker ran (a resume adopts live workers).
        changes = [(s, p) for s, p in changes if p not in DISPATCHER_FILES]
        for path in self._write_dispatcher_files():
            self.events.append("dispatcher_file_rewritten", task=spec.id, attempt=attempt, path=path)
        outside = self._write_scope_outside(spec, changes)
        if outside:
            self.shadow.restore(snapshot, outside)
            shown = [p for _, p in outside if not (spec.line == "impl" and p.startswith("verification/"))]
            results.append(CheckResult("write_scope", False, f"{len(outside)} out-of-scope changes reverted",
                                       detail={"paths": [p for _, p in outside]},
                                       relay=f"上一次尝试改了任务范围之外的文件，已被撤销：{shown}。只改这些范围内的文件：{list(spec.scope)}。"))
        else:
            results.append(CheckResult("write_scope", True, f"{len(changes)} changes, all in scope"))

        tools = self.guard.violations()
        if tools["repo_new"]:
            self.guard.revert(tools["repo_new"])
        tools_bad = tools["repo_new"] + tools["repo_changed"] + [f"~/.openharness/agents/{a}" for a in tools["agents"]]
        results.append(CheckResult("tools_unchanged", not tools_bad,
                                   "pipeline tools untouched" if not tools_bad else f"tools changed: {tools_bad}",
                                   relay="" if not tools_bad else f"上一次尝试修改了流水线自己的工具（{tools_bad}），已撤销。不要修改工具。"))
        if self._judge_frozen():
            # v2 §4.7: nobody changes the frozen judge, whatever the task's scope says.
            touched = [(s, p) for s, p in changes if p.startswith("judge/")]
            if touched:
                self.shadow.restore(snapshot, touched)
            results.append(CheckResult("judge_unchanged", not touched,
                                       "frozen judge untouched" if not touched else f"{len(touched)} judge files changed, reverted",
                                       detail={"paths": [p for _, p in touched]},
                                       relay="" if not touched else f"裁判已经冻结，上一次尝试改了 judge/ 下的文件，已被撤销："
                                       f"{[p for _, p in touched]}。冻结之后不能改裁判。"))

        if timed_out:
            results.append(CheckResult("finished_in_time", False, f"killed after {spec.timeout_min} min",
                                       relay=f"上一次尝试超过了 {spec.timeout_min} 分钟的时间上限，被调度器结束。抓紧完成，并且一定要调用 submit_task。"))
        elif sub is None and self._content_refusal(spec.id):
            # The model service refused the session's input (DashScope content moderation):
            # not a transient outage -- the same context would be refused again.
            results.append(CheckResult("submitted", False, "the model service refused the session's input (data_inspection_failed)",
                                       relay="上一次尝试被模型服务的内容审核中断了（data_inspection_failed：它拒绝了会话里累积的输入）。"
                                       "这次少往会话里读东西：只读完成任务需要的内容，按需分段读，不要整个打印大文件，"
                                       "不要读流水线工具的源码；完成后一定要调用 submit_task。"))
        elif sub is None:
            results.append(CheckResult("submitted", False, "ended without submit_task",
                                       relay="上一次尝试没有调用 submit_task 就结束了（可能只回复了文字，或者用完了轮数）。这次完成后一定要调用 submit_task。"))
        blocked = sub is not None and sub.get("status") == "blocked"
        if blocked:
            results.append(CheckResult("not_blocked", False, f"worker reported blocked: {sub.get('reason', '')[:200]}",
                                       relay=f"上一次尝试报告做不下去：{sub.get('reason', '')}\n证据：{sub.get('evidence', '')}"))

        # Run the done checks whenever the workspace is trustworthy (in scope, tools intact,
        # not cut off mid-write), even after a blocked or missing submission: their verdict
        # drives the rework rules (e.g. V2 reports blocked together with its routing).
        if results[0].ok and not tools_bad and not timed_out:
            results += self._done_checks(spec)
            results += self._ruling_checks(spec, attempt)
        self.events.append("checks", task=spec.id, attempt=attempt, results=[r.to_event() for r in results])

        if tools["repo_changed"] or tools["agents"]:
            self._escalate(spec.id, f"worker 修改了无法自动恢复的工具文件：{tools['repo_changed'] + tools['agents']}")
            return
        if all(r.ok for r in results):
            self.log(f"{spec.id}: attempt {attempt} passed")
            self._accept_rulings(spec.id, attempt)
            self._pass(spec.id)
            return
        self.log(f"{spec.id}: attempt {attempt} failed: {[r.summary for r in results if not r.ok]}")
        if blocked and self._flow_blocked(spec, sub):
            return
        if self._flow_failed(spec, results):
            return
        self._fail(spec, results)

    def _done_checks(self, spec: TaskSpec) -> list[CheckResult]:
        results = []
        for name in spec.check:
            try:
                check = self.checks[name]
                takes_spec = len(inspect.signature(check).parameters) >= 2
                results.append(check(self.ws, spec.to_json()) if takes_spec else check(self.ws))
            except Exception as exc:
                results.append(CheckResult(name, False, f"check crashed: {type(exc).__name__}: {exc}",
                                           relay=f"检查 {name} 运行出错：{exc}"))
        results += self._flow_extra_checks(spec)
        return results

    def _relay(self, spec: TaskSpec, results: list[CheckResult]) -> str:
        parts = [r.relay for r in results if not r.ok and r.relay]
        report = self.ws / "storage" / "stage_reports" / f"{spec.id}.json"
        if report.is_file() and spec.runner == "agent":
            parts.append("上一次的工作报告全文：\n" + _clip(report.read_text(encoding="utf-8", errors="replace")))
        return "\n\n".join(parts) or "上一次尝试没有通过检查。"

    def _pass(self, task_id: str) -> None:
        self.board.report_gate(task_id, True)
        self.board.close(task_id)
        self.events.append("status", task=task_id, to="closed")
        self._record_facts_version(task_id)
        self._flow_after_close(task_id)

    def _fail(self, spec: TaskSpec, results: list[CheckResult]) -> None:
        self.board.report_gate(spec.id, False)
        self._feedback(spec.id, "checks", self._relay(spec, results))
        if self.events.attempts(spec.id) >= self.template.max_attempts:
            self._open_case("exhausted", spec.id, f"{spec.id} 用完了 {self.template.max_attempts} 次尝试",
                            {"最后一次的检查结果": self._results_text(results),
                             "反馈记录": self._feedback_text(spec.id)})
        else:
            self.board.set_status(spec.id, "rework")

    # ------------------------------------------------------------------ rework, escalation, cases

    def _feedback(self, task_id: str, source: str, text: str) -> None:
        self.events.append("feedback", task=task_id, source=source, text=text)

    def _rework_budget_ok(self, target: str) -> bool:
        # spec_issues and reconcile rounds have their own limits
        used = sum(1 for e in self.events.of_task(target, "round_start")
                   if e.get("cause") == "rework" and e.get("reason") not in ("spec_issues", "human", "reconcile", "spec_issues_j4", "strength"))
        return used < self.template.max_rework_rounds

    def _request_rework(self, target: str, feedback: str, reason: str, by: str, mode: str = "rerun") -> None:
        """Reopen an earlier task with feedback; closed tasks downstream of it go stale."""
        self.events.append("round_start", task=target, cause="rework", reason=reason, mode=mode, by=by)
        self._feedback(target, by, feedback)
        self.board.reopen(target)
        self._make_stale([target], by=target)
        self.log(f"{target}: reworked ({reason}, requested by {by})")

    def _downstream(self, tasks: list[str]) -> list[str]:
        """Every board task downstream of one of `tasks`, directly or transitively,
        in breadth-first order (v3 M3). The board has the dependency edges for the
        expanded children ("S2:*" becomes S2-ops, S2-shared, ...); the template
        cannot see them, and its own downstream edges name split families (V1,
        S2) that are not tasks on the board."""
        seen = set(tasks)
        out: list[str] = []
        frontier = list(tasks)
        while frontier:
            current = frontier.pop(0)
            try:
                edges = self.board.dependents(current)
            except KeyError:
                edges = []  # not on the board (e.g. a split family)
            for task in dict.fromkeys(edges):
                if task not in seen:
                    seen.add(task)
                    out.append(task)
                    frontier.append(task)
        return out

    def _upstream_open(self, task_id: str) -> list[str]:
        """Upstream tasks that are not closed right now (v3 M3). While the
        dispatcher runs this cannot happen: a rework marks running downstream
        tasks cancelled. It can after a restart, when the rework happened under
        an older dispatcher; _reconcile then sends the task back."""
        statuses = {t: info["status"] for t, info in self.board.tasks().items()}
        seen = {task_id}
        frontier = [task_id]
        found: list[str] = []
        while frontier:
            current = frontier.pop(0)
            try:
                deps = self.board.dependencies(current)
            except KeyError:
                continue
            for dep in deps:
                if dep in seen:
                    continue
                seen.add(dep)
                frontier.append(dep)
                if statuses.get(dep, "closed") != "closed":
                    found.append(dep)
        return found

    def _upstream_reopened(self, task_id: str) -> list[str]:
        """Upstream tasks reopened after task_id last started (v3 M3).

        The status check in _upstream_open misses the case where the upstream
        was reopened and closed again while the dispatcher was down: the state
        is consistent again, but the downstream task still runs on the previous
        upstream. The event log keeps the evidence -- a round_start for an
        upstream with a later sequence than this task's spawn.
        """
        spawn = self.events.last(task_id, "spawn")
        if not spawn:
            return []
        reopened = {event["task"] for event in self.events.all()
                    if event["type"] == "round_start" and event.get("seq", 0) > spawn["seq"]}
        if not reopened:
            return []
        seen = {task_id}
        frontier = [task_id]
        found: list[str] = []
        while frontier:
            current = frontier.pop(0)
            try:
                deps = self.board.dependencies(current)
            except KeyError:
                continue
            for dep in deps:
                if dep in seen:
                    continue
                seen.add(dep)
                frontier.append(dep)
                if dep in reopened:
                    found.append(dep)
        return found

    def _cancel_for_upstream(self, task_id: str, upstream: list[str]) -> None:
        """Send a running task back because an upstream task was reopened under it."""
        handle = self.running.pop(task_id, None)
        if handle is not None:
            handle.kill()
            lock = self.worker_locks.pop(task_id, None)
            if lock:
                lock.release()
        status = self.board.status(task_id)
        if status == "checking":
            self.board.set_status(task_id, "rework")
        else:
            self.board.set_status(task_id, "open")
        self.events.append("cancelled", task=task_id, by="reconcile", upstream=upstream)
        self.events.append("round_start", task=task_id, cause="cancelled", mode="rerun", by="reconcile")
        self.log(f"{task_id}: upstream reopened {upstream}; cancelled and will rerun")

    def _make_stale(self, tasks: list[str], by: str) -> list[str]:
        """The given tasks, everything downstream of them, and closed tasks
        downstream of those go stale and follow their on_stale rule (recheck or
        rerun). A downstream task that is already running is cancelled first:
        its work was done against the previous upstream."""
        board_ids = set(self.board.tasks())
        orders = [t for t in dict.fromkeys([*tasks, *self._downstream(tasks)]) if t in board_ids]
        hit: list[str] = []
        for t in orders:
            handle = self.running.pop(t, None)
            if handle is not None:
                handle.kill()
                lock = self.worker_locks.pop(t, None)
                if lock:
                    lock.release()
                self.events.append("cancelled", task=t, by=by)
                self.events.append("round_start", task=t, cause="cancelled", mode="rerun", by=by)
                status = self.board.status(t)
                if status == "checking":
                    self.board.set_status(t, "rework")
                elif status in ("in_progress", "stale"):
                    self.board.set_status(t, "open")
                # rework / open / needs_human already wait for a worker
                hit.append(t)
            elif self.board.status(t) == "closed":
                self.board.set_status(t, "stale")
                self.board.set_status(t, "open")
                self.events.append("round_start", task=t, cause="stale", mode=self._spec(t).on_stale, by=by)
                hit.append(t)
        return hit

    # ------------------------------------------------------------------ rulings (v2 §4.6)

    def _ruling_checks(self, spec: TaskSpec, attempt: int) -> list[CheckResult]:
        found = rulings.proposals(self.coord, spec.id, attempt)
        if not found:
            return []
        known = {t.id for t in self.template.tasks}
        unknown = sorted({t for r in found for t in r["affects"]["tasks"] if t not in known})
        return [CheckResult("rulings", not unknown,
                            f"{len(found)} rulings recorded" if not unknown else f"rulings name unknown tasks {unknown}",
                            relay="" if not unknown else
                            f"record_ruling 的 affected_tasks 里有不存在的任务 {unknown}；任务只能是 {sorted(known)} 之一。")]

    def _accept_rulings(self, task_id: str, attempt: int) -> None:
        """The proposals of a passing attempt (or of a handler that decided) become rulings."""
        found = rulings.proposals(self.coord, task_id, attempt)
        if not found:
            return
        for ruling in found:
            self.events.append("ruling", task=task_id, entry=ruling)
        self._write_dispatcher_files()
        for ruling in found:
            if ruling["status"] == "proposed":
                self._ruling_gate(ruling)
            else:
                self._apply_ruling(ruling, by=task_id)
        self.log(f"{task_id}: {len(found)} rulings accepted: {[r['id'] for r in found]}")

    def _ruling_gate(self, ruling: dict[str, Any]) -> None:
        known = {t.id for t in self.template.tasks}
        # A ruling task the dispatcher created (J3) also holds the tasks that wait for it.
        held = next((e.get("blocks") or [] for e in reversed(self.events.all())
                     if e["type"] == "ruling_task" and e.get("task") == ruling.get("task")), [])
        waiting = [t for t in dict.fromkeys([*ruling["affects"]["tasks"], *held])
                   if t in known and self.board.status(t) in ("open", "rework")]
        question = (f"裁定 {ruling['id']} 不照搬单体的行为，要人确认（V2-20）。问题：{ruling['question']}；"
                    f"结论：{ruling['conclusion']}；依据：{ruling['basis']}；影响的任务：{ruling['affects']['tasks']}，"
                    f"用例：{ruling['affects']['cases']}。确认用 resolve --accept，否决（照搬单体）用 resolve --reject。")
        self._human_gate(f"HUMAN_{ruling['id']}", question, waiting)

    def _apply_ruling(self, ruling: dict[str, Any], by: str) -> list[str]:
        """A ruling in force: the tasks it names get it as feedback; closed ones go stale."""
        known = {t.id for t in self.template.tasks}
        board_ids = set(self.board.tasks())
        targets: list[str] = []
        for task in ruling["affects"]["tasks"]:
            if task not in known or task == ruling.get("task"):
                continue
            if self.template.get(task).split == "package":
                # A ruling may name the family ("S2"); its work lives in the
                # expanded children, which are the tasks that can rework.
                targets += sorted(t for t in board_ids if t.startswith(task + "-"))
            elif task in board_ids:
                targets.append(task)
        text = (f"新的裁定 {ruling['id']}（{ruling['category']}）影响这个任务。问题：{ruling['question']}；"
                f"结论：{ruling['conclusion']}；依据：{ruling['basis']}。见 storage/rulings.json。")
        for task in targets:
            self._feedback(task, f"ruling {ruling['id']}", text)
        stale = self._make_stale(targets, by=ruling["id"])
        self.events.append("ruling_applied", task=by, ruling=ruling["id"], stale=stale)
        return stale

    def _wait_for_upstream(self, task_id: str, reason: str) -> None:
        """The current task failed because of an upstream stage; retry it fresh once that is fixed."""
        self.board.report_gate(task_id, False)
        self.events.append("round_start", task=task_id, cause="upstream_rework", reason=reason, mode="rerun")
        self.board.set_status(task_id, "rework")

    def _escalate(self, task_id: str, reason: str) -> None:
        status = self.board.status(task_id)
        if status == "stale":
            self.board.set_status(task_id, "open")
            status = "open"
        if status == "open":
            self.board.claim(task_id, DISPATCHER_ACTOR)
            status = "in_progress"
        if status == "in_progress":
            self.board.set_status(task_id, "checking")
        self.board.set_status(task_id, "needs_human")
        self.board.comment(task_id, f"交给人：{reason}")
        self.events.append("escalate", task=task_id, reason=reason)
        self.log(f"{task_id}: needs a person: {reason}")

    def _human_gate(self, gate_id: str, question: str, blocks: list[str]) -> None:
        """A dispatcher task that waits in needs_human until a person resolves it. A gate
        that a person closed earlier (e.g. a phase review before a rework moved v0) is
        asked again; one that is still waiting keeps its first question."""
        if gate_id in self.board.tasks():
            if self.board.status(gate_id) == "closed":
                self.board.reopen(gate_id)
                self.events.append("round_start", task=gate_id, cause="asked_again", mode="rerun")
                self._escalate(gate_id, question)
            return
        spec = TaskSpec(id=gate_id, runner="dispatcher", action="human")
        self.board.add_task(spec.to_json())
        board_ids = set(self.board.tasks())
        for task in blocks:
            if task not in board_ids:
                continue  # a split family or something not created yet
            self.board.add_dep(task, gate_id)
        self._escalate(gate_id, question)

    def _case_in_round(self, task_id: str) -> bool:
        """At most one exception case per task per round; the next exception goes to a person.
        A retry granted by the handler continues the same round, so the handler cannot
        keep a failing task alive forever."""
        events = self.events.of_task(task_id, "case_open", "round_start")
        last_round = max((i for i, e in enumerate(events)
                          if e["type"] == "round_start" and e.get("cause") != "handler_retry"), default=-1)
        return any(e["type"] == "case_open" and e.get("kind") != "review" for e in events[last_round + 1:])

    def _open_case(self, kind: str, task_id: str, title: str, sections: dict[str, str]) -> None:
        if not self.handler_enabled or (kind != "review" and self._case_in_round(task_id)):
            if kind != "review":
                self._escalate(task_id, title)
            return
        sections = {**sections, "任务定义": json.dumps(self._spec(task_id).to_json(), ensure_ascii=False, indent=1),
                    "这个任务的事件": self._task_events_text(task_id)}
        case = self.cases.open(kind, task_id, title, sections)
        job = WorkerJob(task_id=case.case_id, attempt=1, role="handler", ws=self.ws, coord_dir=self.coord,
                        system_prompt=HANDLER_SYSTEM_PROMPT,
                        prompt=handler_prompt(case.case_id, case.path, self.cases.journal, self.ws, kind),
                        scope=(), max_turns=HANDLER_MAX_TURNS, timeout_s=HANDLER_TIMEOUT_S, read_coord=True,
                        deny_read=self.foreign_runs, deny_write=(str(self.ws / "judge"),) if self._judge_frozen() else (),
                        pipeline=self.template.name, extra_env=self.mail_env)
        handle = self.launcher.start(job)
        handle.kind = "handler"
        handle.meta = {"case": case}
        self.running[f"handler:{case.case_id}"] = handle
        self.events.append("handler_spawn", task=task_id, case=case.case_id, kind=kind, case_file=str(case.path),
                           pid=handle.pid, started=handle.started, run_dir=str(handle.run_dir))
        self.log(f"{task_id}: exception handler started for {case.case_id}")

    def _on_handler_exit(self, handle: WorkerHandle, timed_out: bool) -> None:
        case: Case = handle.meta["case"]
        decision = case.decision()
        task_id = case.task_id
        action = (decision or {}).get("action")
        if decision is not None and not timed_out:
            self._accept_rulings(case.case_id, 1)
        if case.kind == "review":
            if action == "escalate":
                self._human_gate(f"HUMAN_{task_id}", f"阶段复查要求人确认：{decision.get('rationale', '')}", self._next_tasks(task_id))
                applied = "交给人确认后再继续"
            elif action == "reroute" and decision.get("target") in {t.id for t in self.template.tasks} \
                    and task_id in self.template.downstream(decision["target"]) \
                    and self._rework_budget_ok(decision["target"]):
                # A review finds a blocking defect in an earlier stage: send that
                # stage back. _request_rework makes the reviewed task's closed
                # downstream stale -> open, so it reruns once the target is fixed.
                self._request_rework(decision["target"], decision.get("feedback", ""), reason="handler", by=case.case_id)
                applied = f"退回 {decision['target']}"
            else:
                applied = "继续" if action == "ok" else f"处理者给出 {action!r}，复查只接受 ok/escalate/reroute，按继续处理"
            self.cases.record(case, decision, applied)
            return
        if decision is None or timed_out:
            self._escalate(task_id, f"异常处理者没有给出决定（{case.case_id}）")
            applied = "交给人"
        elif action == "retry":
            self.events.append("round_start", task=task_id, cause="handler_retry", reason=case.case_id, mode="rerun")
            self._feedback(task_id, f"exception handler {case.case_id}", decision.get("feedback", ""))
            self.board.set_status(task_id, "rework")
            applied = "同一任务再试一轮"
        elif action == "reroute" and decision.get("target") in {t.id for t in self.template.tasks} \
                and task_id in self.template.downstream(decision["target"]) \
                and self._rework_budget_ok(decision["target"]):
            self._request_rework(decision["target"], decision.get("feedback", ""), reason="handler", by=case.case_id)
            self._wait_for_upstream(task_id, case.case_id)
            applied = f"退回 {decision['target']}"
        else:
            self._escalate(task_id, decision.get("rationale") or f"异常处理者决定 {action}")
            applied = "交给人"
        self.cases.record(case, decision, applied)

    def _next_tasks(self, task_id: str) -> list[str]:
        """The tasks that wait for task_id. A split family (I1) is not a board
        task: its expanded children are what a gate or ruling task can block."""
        board_ids = set(self.board.tasks())
        out: list[str] = []
        for spec in self.template.tasks:
            if task_id not in spec.deps:
                continue
            if spec.split in ("package", "domain"):
                out += sorted(t for t in board_ids if t.startswith(spec.id + "-") and t != spec.id)
            elif spec.id in board_ids:
                out.append(spec.id)
        return out

    # ------------------------------------------------------------------ v1 rules

    def _active_spec_round(self) -> dict[str, Any] | None:
        rounds = [e for e in self.events.all() if e["type"] in ("spec_round", "spec_round_done")]
        return rounds[-1] if rounds and rounds[-1]["type"] == "spec_round" else None

    def _flow_extra_checks(self, spec: TaskSpec) -> list[CheckResult]:
        if spec.mode == "ruling":
            return [self._ruling_task_check(spec)]
        if spec.id != "S1":
            return []
        active = self._active_spec_round()
        if not active:
            return []
        blueprint = _read_json(self.ws / "storage" / "blueprint.json") or {}
        resolved = {str(r.get("issue_id")) for r in (blueprint.get("metadata") or {}).get("spec_resolutions") or []
                    if isinstance(r, dict)}
        missing = [i for i in active["issues"] if i not in resolved]
        return [CheckResult("spec_resolutions", not missing,
                            "every open spec issue has a ruling" if not missing else f"no ruling for {missing}",
                            relay="" if not missing else f"这些 spec_issues 在 blueprint.json 的 metadata.spec_resolutions 里还没有裁定：{missing}")]

    def _flow_after_close(self, task_id: str) -> None:
        self._ensure_expansions()
        if task_id.startswith("ROOM-"):
            self._room_after_close(task_id)
            return
        spec = self._spec(task_id)
        if spec.mode in ("J3", "J4", "J5") and self._judge_settled():
            return  # the judge is frozen: what it found was settled before it froze
        if spec.mode == "J3":
            self._after_j3(task_id)
        elif spec.mode == "J4":
            self._after_j4(task_id)
        elif spec.mode == "J5":
            self._after_j5(task_id)
        elif task_id == "V1":
            self._after_v1()
        elif task_id == "TAG_V0" and self.phase_review:
            self._open_case("review", "TAG_V0", "v0 已打 tag，进入 S5 前的阶段复查",
                            {"运行摘要": self._summary_text()})
        elif spec.action == "tag_b0" and self.phase_review:
            self._open_case("review", task_id, "B0 已打 tag，进入优化阶段前的阶段复查", {"运行摘要": self._summary_text()})
        elif spec.action == "freeze_judge" and self.phase_review and self.events.last(task_id, "judge_frozen") \
                and not self.events.last(task_id, "judge_frozen").get("unchanged"):
            self._open_case("review", task_id, "裁判已冻结，进入设计线之前的阶段复查", {"运行摘要": self._summary_text()})

    # ------------------------------------------------------------------ work packages (v3 M2/W2.7)

    def _ensure_expansions(self) -> None:
        """Create the children of every split family whose trigger has closed."""
        tasks = self.board.tasks()
        closed = {tid for tid, info in tasks.items() if info["status"] == "closed"}
        for spec in self.template.tasks:
            if spec.split not in ("package", "domain") or not spec.expand_after:
                continue
            if not any(trigger in closed for trigger in spec.expand_after):
                continue
            if any(tid.startswith(spec.id + "-") and tid != spec.id for tid in tasks):
                continue
            if spec.split == "domain":
                self._expand_domain(spec)
            else:
                self._expand_family(spec)

    def _expand_family(self, spec: TaskSpec) -> None:
        from coord import packages as packages_mod

        if not self.events.count("work_packages"):
            result = packages_mod.write(self.ws)
            self.events.append("work_packages", packages=[p["id"] for p in result["packages"]],
                               sha=(result.get("source") or {}).get("analysis_report_sha256"))
            self.log(f"work packages: {[p['id'] for p in result['packages']]}")
        plan = _read_json(self.ws / "storage" / "work_packages.json") or {"packages": []}
        blueprint = _read_json(self.ws / "storage" / "blueprint.json") or {}
        functions = blueprint.get("lambda_functions") or []
        created: list[str] = []
        for package in plan.get("packages") or []:
            pid = str(package.get("id"))
            pkg_dir = re.sub(r"[^a-z0-9_]+", "_", pid.lower())
            endpoints = [f"{e.get('method')} {e.get('path')}" for e in package.get("endpoints") or []]
            fns = [f.get("name") for f in functions
                   if set(f.get("entry_points") or []) & set(endpoints)]
            child_id = f"{spec.id}-{pid}"
            deps = self._resolve_deps(spec, child_id, same_package=pid)
            data = spec.to_json()
            data.update({"id": child_id, "split": "none", "expand_after": [], "split_shared": False,
                         "deps": deps, "package": pid, "endpoints": endpoints, "functions": fns,
                         "scope": [s.replace("{pkg}", pid).replace("{pkg_dir}", pkg_dir) for s in spec.scope]})
            self.board.add_task(data, deps)
            created.append(child_id)
            self.events.append("expand", family=spec.id, task=child_id, package=pid, functions=fns)
        if spec.split_shared:
            child_id = f"{spec.id}-shared"
            deps = self._resolve_deps(spec, child_id, same_package=None)
            if spec.extra.get("shared_after_children"):
                deps += [c for c in created if c not in deps]
            data = spec.to_json()
            data.update({"id": child_id, "split": "none", "expand_after": [], "split_shared": False,
                         "deps": deps, "package": "shared", "endpoints": [], "functions": [],
                         "check": list(spec.shared_check or spec.check),
                         "scope": [s.replace("{pkg}", "shared").replace("{pkg_dir}", "shared")
                                   for s in (spec.shared_scope or spec.scope)]})
            self.board.add_task(data, deps)
            created.append(child_id)
            self.events.append("expand", family=spec.id, task=child_id, package="shared", functions=[])
        self._wire_deps(spec.id, created)
        self.log(f"{spec.id}: expanded to {created}")

    def _expand_domain(self, spec: TaskSpec) -> None:
        """v3 M4/W4.2: expand by the blueprint's domains (rule 8). A1 fills
        work_packages.impl_packages as {package: {functions, dir}}; when it did not
        (or a package is missing), the same mapping is computed from the blueprint's
        lambda_functions and the package's endpoints, so the split never depends on
        a worker remembering to write it."""
        plan = _read_json(self.ws / "storage" / "work_packages.json") or {}
        impl = plan.get("impl_packages") or {}
        if not isinstance(impl, dict) or not impl:
            blueprint = _read_json(self.ws / "storage" / "blueprint.json") or {}
            functions = [f for f in blueprint.get("lambda_functions") or [] if isinstance(f, dict)]
            impl = {}
            for package in plan.get("packages") or []:
                pid = str(package.get("id"))
                endpoints = {f"{e.get('method')} {e.get('path')}" for e in package.get("endpoints") or []}
                fns = [f.get("name") for f in functions if set(f.get("entry_points") or []) & endpoints]
                impl[pid] = {"functions": fns, "dir": pid}
            if impl:
                self.events.append("impl_packages_fallback", task=spec.id, packages=list(impl))
                self.log(f"{spec.id}: impl_packages missing; computed from the blueprint and the work packages")
        if not isinstance(impl, dict) or not impl:
            self._escalate(spec.id, f"{spec.id}: storage/work_packages.json has no impl_packages "
                                    "and the blueprint has no lambda_functions to compute them from")
            return
        created: list[str] = []
        for pid, info in impl.items():
            pid = str(pid)
            if isinstance(info, dict):
                functions = [str(f) for f in (info.get("functions") or [])]
                pkg_dir = str(info.get("dir") or "")
            else:
                functions = [str(f) for f in (info or [])]
                pkg_dir = ""
            pkg_dir = re.sub(r"[^a-z0-9_]+", "_", (pkg_dir or pid).lower())
            child_id = f"{spec.id}-{pid}"
            deps = self._resolve_deps(spec, child_id, same_package=pid)
            app_name = self.monolith.name if self.monolith is not None else ""
            scope: list[str] = []
            for pattern in spec.scope:
                if "{fn}" in pattern:
                    scope += [pattern.replace("{fn}", fn).replace("{app}", app_name) for fn in functions]
                else:
                    scope.append(pattern.replace("{pkg}", pid).replace("{pkg_dir}", pkg_dir)
                                 .replace("{app}", app_name))
            data = spec.to_json()
            data.update({"id": child_id, "split": "none", "expand_after": [], "split_shared": False,
                         "deps": deps, "package": pid, "endpoints": [], "functions": functions,
                         "scope": scope})
            self.board.add_task(data, deps)
            created.append(child_id)
            self.events.append("expand", family=spec.id, task=child_id, package=pid, functions=functions)
        if spec.split_shared:
            child_id = f"{spec.id}-shared"
            deps = self._resolve_deps(spec, child_id, same_package=None)
            if spec.extra.get("shared_after_children"):
                deps += [c for c in created if c not in deps]
            data = spec.to_json()
            data.update({"id": child_id, "split": "none", "expand_after": [], "split_shared": False,
                         "deps": deps, "package": "shared", "endpoints": [], "functions": [],
                         "check": list(spec.shared_check or spec.check),
                         "scope": [s.replace("{pkg}", "shared").replace("{pkg_dir}", "shared")
                                   for s in (spec.shared_scope or spec.scope)]})
            self.board.add_task(data, deps)
            created.append(child_id)
            self.events.append("expand", family=spec.id, task=child_id, package="shared", functions=[])
        self._wire_deps(spec.id, created)
        self.log(f"{spec.id}: expanded by domain to {created}")

    def _resolve_deps(self, spec: TaskSpec, child_id: str, same_package: str | None) -> list[str]:
        """Resolve "<family>:*" to the same package's child when there is one,
        else to every child; leave ordinary ids alone."""
        tasks = self.board.tasks()
        out: list[str] = []
        for dep in spec.deps:
            if dep.endswith(":*"):
                family = dep[:-2]
                children = sorted(t for t in tasks if t.startswith(family + "-") and t != family)
                chosen = [f"{family}-{same_package}"] if same_package and f"{family}-{same_package}" in children else children
            else:
                chosen = [dep]
            for child in chosen:
                if child and child != child_id and child not in out:
                    out.append(child)
        return out

    def _wire_deps(self, family: str, children: list[str]) -> None:
        """Add the expanded children as dependencies of every task that names
        "<family>:*" (all children) or "<family>:shared" (just the shared one).
        Runs once per family (recorded as deps_wired)."""
        if any(e.get("type") == "deps_wired" and e.get("family") == family for e in self.events.all()):
            return
        shared = f"{family}-shared"
        tasks = self.board.tasks()
        for tid, info in tasks.items():
            deps = (info.get("spec") or {}).get("deps") or []
            if f"{family}:*" in deps:
                wanted = children
            elif f"{family}:shared" in deps and shared in children:
                wanted = [shared]
            else:
                continue
            for child in wanted:
                try:
                    self.board.add_dep(tid, child)
                except Exception:
                    pass  # an already-present dependency
        self.events.append("deps_wired", family=family, children=children)

    # ------------------------------------------------------------------ rooms (v3 M3)

    def _rooms_enabled(self) -> bool:
        return bool(self.mail_env) or os.environ.get("M2S_ROOMS", "").lower() in ("1", "true", "yes")

    def _ensure_room_turns(self) -> None:
        """Start the next turn of every open room; open rooms requested by notices."""
        if not self._rooms_enabled():
            return
        room_dir = rooms.rooms_dir(self.coord)
        if room_dir.is_dir():
            for path in sorted(room_dir.glob("*.json")):
                state = rooms.load(self.coord, path.stem)
                if not state or state.get("status") != "open":
                    continue
                room_id = state["id"]
                busy = [t for t, info in self.board.tasks().items()
                        if t.startswith(f"ROOM-{room_id}-") and info["status"] != "closed"]
                if busy:
                    continue
                role = rooms.next_role(state)
                if not role:
                    self._room_conclude(state)
                    continue
                turn = int(state.get("turn") or 0)
                task_id = f"ROOM-{room_id}-{turn}-{state.get('round', 1)}"
                scope = [f"storage/stage_reports/{task_id}.json"]
                if state.get("kind") == "spec":
                    if self.template.name.startswith("v1"):
                        # v1: the architect writes the ruling into the blueprint
                        scope += ["storage/blueprint.json", "storage/DECISIONS.md", "storage/analysis_report.json"]
                    else:
                        # v2 (M4): the ruling goes through record_ruling; the
                        # annotator may correct the fact base. A1 owns the blueprint.
                        scope += ["storage/analysis_report.json"]
                spec = TaskSpec(id=task_id, role=role, mode="room", check=("room_turn",),
                                scope=tuple(scope), max_turns=80, timeout_min=60,
                                extra={"room_id": room_id, "room_kind": state["kind"], "room_role": role})
                self.board.add_task(spec.to_json(), ())
                gate = state.get("ruling_gate")
                if gate:
                    # J3's downstream waits for the last room turn; adding each
                    # new turn as a dependency keeps the gate from starting early.
                    try:
                        self.board.add_dep(gate, task_id)
                    except Exception:
                        pass
                self.events.append("room_turn", room=room_id, task=task_id, role=role)
                self.log(f"room {room_id}: turn {turn} -> {role}")
        # a raise_spec_issue notice opens a spec room once
        for entry in notices.read_notices(self.ws, "dispatcher", ["spec-issue"]):
            body = entry.get("body") or {}
            if not isinstance(body, dict) or body.get("status") != "open":
                continue
            room_id = body.get("room_id")
            if room_id and not rooms.load(self.coord, room_id):
                rooms.open_spec_room(self.coord, room_id, entry.get("sender_role") or "m2s-verifier",
                                     str(body.get("topic") or entry.get("subject") or ""),
                                     proposer_task=str(entry.get("task") or ""))
                self.events.append("room_open", room=room_id, kind="spec")
                self.log(f"room {room_id}: opened (spec issue)")

    def _room_after_close(self, task_id: str) -> None:
        spec = self._spec(task_id)
        room_id = spec.extra.get("room_id")
        if not room_id:
            return
        state = rooms.load(self.coord, room_id)
        if not state or state.get("status") != "open":
            return
        rooms.advance(self.coord, state)
        if state.get("status") == "concluding":
            self._room_conclude(state)

    def _room_conclude(self, state: dict[str, Any]) -> None:
        room_id = state["id"]
        entries = rooms.messages(self.ws, room_id)
        self.events.append("room_end", room=room_id, kind=state.get("kind"), messages=len(entries))
        state["status"] = "closed"
        rooms.save(self.coord, state)
        proposer = state.get("proposer_task") or ""
        if state.get("kind") == "triage":
            module = rooms.triage_consensus(entries)
            board_ids = set(self.board.tasks())
            template_ids = {t.id for t in self.template.tasks}
            target = None
            if module and (module in template_ids or module in board_ids):
                target = module
            elif module:
                role = ROUTE_ROLES.get(module) or (module if module in ROUTE_ROLES.values() else None)
                target = next((t.id for t in reversed(self.template.tasks) if t.role == role), None)
            if target in template_ids and self.template.get(target).split == "package":
                # The room named a module whose work is split; the discussion
                # did not name one package, so every package is reopened.
                children = sorted(t for t in board_ids if t.startswith(target + "-"))
                for child in children:
                    self._request_rework(child, f"分诊讨论室的结论是 {module}；按讨论串修正。",
                                         reason="triage", by=room_id)
                if children:
                    self.log(f"room {room_id}: consensus {module} -> {children}")
                else:
                    self._open_case("triage", proposer or "V2", f"分诊讨论室 {room_id} 的结论 {module} 没有对应的任务",
                                    {"讨论串": json.dumps(entries, ensure_ascii=False, indent=1)})
            elif target:
                self._request_rework(target, f"分诊讨论室的结论是 {module}；按讨论串修正。", reason="triage", by=room_id)
                self.log(f"room {room_id}: consensus {module} -> {target}")
            else:
                self._open_case("triage", proposer or "V2", f"分诊讨论室 {room_id} 没有达成一致",
                                {"讨论串": json.dumps(entries, ensure_ascii=False, indent=1)})
            return
        if state.get("ruling_gate"):
            # v2 (M4/W4.4): a J3 disagreement room. Its rulings were accepted as
            # the turns passed (record_ruling); the gate task's recheck now checks
            # that they cover every difference and releases the blocked tasks.
            self.log(f"room {room_id}: closed; the ruling gate {state['ruling_gate']} checks coverage")
            return
        # spec room: a ruling in blueprint.metadata.spec_resolutions settles it
        blueprint = _read_json(self.ws / "storage" / "blueprint.json") or {}
        rulings_written = (blueprint.get("metadata") or {}).get("spec_resolutions") or []
        if rulings_written:
            if proposer:
                try:
                    self._request_rework(proposer, f"规格讨论室 {room_id} 已给出裁定，按裁定重做。", reason="spec_room", by=room_id)
                except Exception:
                    pass
            self.log(f"room {room_id}: settled by a ruling")
        else:
            self._open_case("triage", proposer or "V1", f"规格讨论室 {room_id} 没有写出裁定",
                            {"讨论串": json.dumps(entries, ensure_ascii=False, indent=1)})

    def _after_v1(self) -> None:
        if self._active_spec_round():
            self.events.append("spec_round_done", task="V1")
        plan = _read_json(self.ws / "verification" / "test_plan.json") or {}
        open_issues = [i for i in plan.get("spec_issues") or [] if isinstance(i, dict) and i.get("status") == "open"]
        if not open_issues:
            return
        ids = [str(i.get("id")) for i in open_issues]
        done_rounds = self.events.count("spec_round")
        if done_rounds < self.template.max_spec_issue_rounds:
            self.events.append("spec_round", task="V1", round=done_rounds + 1, issues=ids)
            text = ("验证线（V1）报了下面这些 open 的 spec_issues。请逐条裁定，写进 blueprint.json 的 "
                    "metadata.spec_resolutions，每条带 issue_id（例如 {\"issue_id\": \"SI-1\", \"decision\": ..., "
                    "\"applies_to\": [...], \"rationale\": ...}）：\n" + json.dumps(open_issues, ensure_ascii=False, indent=1))
            self._request_rework("S1", text, reason="spec_issues", by="V1")
            self.events.append("round_start", task="V1", cause="spec_issues", mode="rerun")
            self._feedback("V1", "V1 回炉", "V1 回炉：architect 已裁定 spec_issues（见 blueprint.json 的 "
                           "metadata.spec_resolutions）。按裁定补用例，并把已裁定的 issue 的 status 改为 resolved。")
            self.log(f"V1: {len(ids)} open spec issues -> S1 rules on them (round {done_rounds + 1})")
            return
        blocking = [i for i in open_issues if i.get("severity") == "blocking"]
        if blocking:
            self._human_gate("HUMAN_SPEC_ISSUES", f"{len(blocking)} 条 blocking 的 spec_issues 经过 "
                             f"{done_rounds} 轮仍未解决：{[i.get('id') for i in blocking]}", ["S2"])
        else:
            self.events.append("spec_issues_carried", task="V1", issues=ids)
            self.log(f"V1: {len(ids)} non-blocking spec issues stay open; they go into the final report")

    def _route_target(self, kind: Any, task_id: str, function: str | None = None) -> str | None:
        """The nearest earlier task that fixes what a verification failure points at:
        code, template, spec / blueprint by role; assets (the delta tests) is D1.
        v3 M2: with work packages the role resolves to a split family, which is
        not a task; the routing entry's function picks its package child."""
        earlier = [t for t in self.template.tasks if t.id in self.template.upstream(task_id) and t.id != task_id]
        if kind == "assets":
            found = [t.id for t in earlier if t.mode == "D1"]
        else:
            found = [t.id for t in earlier if kind in ROUTE_ROLES and t.role == ROUTE_ROLES[kind]]
        if found:
            return found[-1]
        role = ROUTE_ROLES.get(kind) if isinstance(kind, str) else None
        if role and function:
            return self._package_child(role, function)
        return None

    def _package_child(self, role: str, function: str) -> str | None:
        """The expanded child of the package family for `role` whose functions
        include `function` (v3 M2/W2.7)."""
        families = [t.id for t in self.template.tasks if t.role == role and t.split == "package"]
        if not families:
            return None
        for tid, info in self.board.tasks().items():
            functions = (info.get("spec") or {}).get("functions") or []
            if function in functions and any(tid.startswith(f + "-") for f in families):
                return tid
        return None

    def _flow_failed(self, spec: TaskSpec, results: list[CheckResult]) -> bool:
        """The verifier's acceptance failed (v1 V2, v2 V3): its report's routing says
        whose it is. Implementation-line tasks get the behaviour, not the tests."""
        verdict = "verdict_delta" if spec.mode == "V3" else "validate_full" if spec.id == "V2" else None
        if not verdict or not any(r.name == verdict and not r.ok for r in results):
            return False
        report = _read_json(self.ws / "storage" / "stage_reports" / f"{spec.id}.json") or {}
        routing = [r for r in report.get("routing") or [] if isinstance(r, dict)]
        if not routing:
            return False
        if any(self._route_target(r.get("to"), spec.id, r.get("function")) is None for r in routing):
            self.board.report_gate(spec.id, False)
            if self._rooms_enabled():
                unknown = [str(r.get("to")) for r in routing]
                suspect = sorted({ROUTE_ROLES[t] for t in unknown if t in ROUTE_ROLES})
                room_id = f"triage-{spec.id.lower()}-{self.events.count('room_open') + 1}"
                rooms.open_triage_room(self.coord, room_id, f"{spec.id} 有无法归因的失败",
                                       suspect or sorted({t.role for t in self.template.tasks if t.role}),
                                       proposer_task=spec.id)
                self.events.append("room_open", room=room_id, kind="triage", by=spec.id)
                self.log(f"room {room_id}: opened (triage of {spec.id})")
                self._wait_for_upstream(spec.id, room_id)
                return True
            self._open_case("triage", spec.id, f"{spec.id} 的分诊里有判断不了归因的失败",
                            {f"{spec.id} 的 routing": json.dumps(routing, ensure_ascii=False, indent=1),
                             "检查结果": self._results_text(results)})
            return True
        targets: dict[str, list[dict[str, Any]]] = {}
        for entry in routing:
            targets.setdefault(self._route_target(entry["to"], spec.id, entry.get("function")), []).append(entry)
        over = [t for t in targets if not self._rework_budget_ok(t)]
        if over:
            self.board.report_gate(spec.id, False)
            self._escalate(spec.id, f"{spec.id} 要求再次退回 {over}，但这些阶段的回炉次数已用完")
            return True
        for target in sorted(targets, key=self.template.order):
            if self._spec(target).mode == "D1":
                self._request_rework(target, "V3 判断下面这些增量测试本身写错了。按依据改用例，不要为了通过而放宽预期：\n"
                                     + json.dumps(targets[target], ensure_ascii=False, indent=1), reason="verification", by=spec.id)
                continue
            relay = [{k: e.get(k) for k in RELAY_FIELDS if k in e} for e in targets[target]]
            self._request_rework(target, "验收发现下面这些行为和规格不符，请修正：\n" + json.dumps(relay, ensure_ascii=False, indent=1),
                                 reason="verification", by=spec.id)
        self._wait_for_upstream(spec.id, "verification")
        return True

    def _flow_blocked(self, spec: TaskSpec, sub: dict[str, Any]) -> bool:
        raw = str(sub.get("suspected_module") or "").strip()
        ids = {t.id.upper(): t.id for t in self.template.tasks}
        roles = {t.role: t.id for t in self.template.tasks if t.role}
        target = ids.get(raw.upper()) or roles.get(raw) or self._route_target(raw.lower(), spec.id)
        if not target or target == spec.id or spec.id not in self.template.downstream(target):
            return False
        if spec.line == "verify" and self._spec(target).line == "impl":
            return False  # evidence from the verification line may quote tests; let the handler phrase it
        if not self._rework_budget_ok(target):
            return False
        text = f"{spec.id} 报告做不下去，认为问题出在 {target}：\n{sub.get('reason', '')}\n证据：\n{sub.get('evidence', '')}"
        self._request_rework(target, text, reason="blocked", by=spec.id)
        self._wait_for_upstream(spec.id, "blocked")
        return True

    # ------------------------------------------------------------------ v2 judge line (PIPELINE_V2_PLAN.md §4.5)

    def _task_by(self, *, mode: str | None = None, action: str | None = None, role: str | None = None,
                 upstream_of: str | None = None) -> str | None:
        pool = set(self.template.upstream(upstream_of)) if upstream_of else {t.id for t in self.template.tasks}
        return next((t.id for t in self.template.tasks if t.id in pool and (mode is None or t.mode == mode)
                     and (action is None or t.action == action) and (role is None or t.role == role)), None)

    def _flow_action_failed(self, spec: TaskSpec, result: CheckResult) -> bool:
        """run_monolith FAIL is about the cases (a request or an identity preparation
        failed, a value that cannot be masked): J1 gets the details and J2 waits.
        check_adapter and run_judge follow §4.13."""
        if spec.action == "check_adapter":
            return self._adapter_failed(spec, result)
        if spec.action == "run_judge":
            return self._judge_failed(spec, result)
        if spec.action != "run_monolith" or result.detail.get("status") != "FAIL":
            return False
        j1 = self._task_by(mode="J1", upstream_of=spec.id)
        if not j1 or not self._rework_budget_ok(j1):
            return False
        detail = {k: result.detail[k] for k in ("error", "errors", "problems", "unstable") if result.detail.get(k)}
        self._request_rework(j1, "run_monolith（在单体上运行裁判用例）没有通过，按下面的问题修改用例或身份：\n"
                             + _clip(json.dumps(detail, ensure_ascii=False, indent=1)), reason="monolith_run", by=spec.id)
        self._wait_for_upstream(spec.id, "monolith_run")
        return True

    def _adapter_failed(self, spec: TaskSpec, result: CheckResult) -> bool:
        """V1 (§4.13). A map that fails its static check is the architect's. A read that
        differs from the monolith may be the map or the code: an exception case."""
        detail = result.detail
        a1 = self._route_target("blueprint", spec.id)
        if detail.get("map_error") and a1 and self._rework_budget_ok(a1):
            self._request_rework(a1, "适配映射 storage/adapter_map.json 没有通过检查，请修正：\n" + str(detail.get("error")),
                                 reason="adapter_map", by=spec.id)
            self._wait_for_upstream(spec.id, "adapter_map")
            return True
        self.board.report_gate(spec.id, False)
        self._open_case("triage", spec.id, "check_adapter 读回的结果和单体不一致",
                        {"check_adapter 的结果": _clip(json.dumps(detail, ensure_ascii=False, indent=1)),
                         "怎么判断": "映射写错了（表、键、属性、身份）退回 A1；读接口的代码不对退回 I1；分不清就交给人。" + JUDGE_TRIAGE_RULES})
        return True

    def _judge_failed(self, spec: TaskSpec, result: CheckResult) -> bool:
        """V2 (§4.13). A function that cannot start goes to I2 at once, and the rest of the
        failures are looked at when it can. Everything else is an exception case; SDK
        decode errors too, since they may be the map or the code."""
        detail = result.detail
        dumped = json.dumps({"failures": detail.get("failures"), "error": detail.get("error")}, ensure_ascii=False)
        signals = sorted({m.group(0)[:300] for m in re.finditer(rf"[^\"]*(?:{_START_FAILURE.pattern})[^\"]*", dumped)})
        i2 = self._route_target("template", spec.id)
        if signals and i2 and self._rework_budget_ok(i2):
            self.events.append("judge_start_failures", task=spec.id, to=i2, signals=signals[:20])
            self._request_rework(i2, "目标版本的函数启动不了（打包或模板的问题），请修正：\n" + "\n".join(signals[:20]),
                                 reason="packaging", by=spec.id)
            self._wait_for_upstream(spec.id, "packaging")
            return True
        unfinished = sorted({json.dumps(c, ensure_ascii=False, sort_keys=True) for f in detail.get("failures") or []
                             for c in (f.get("unawaited") or {}).get("calls") or []})
        i1 = self._route_target("code", spec.id)
        if unfinished and i1 and self._rework_budget_ok(i1):
            self.events.append("judge_unawaited", task=spec.id, to=i1, calls=unfinished[:20])
            self._request_rework(i1, "函数返回时这些写操作或消息还没完成（代码没有等它们）。真实的 Lambda 在函数返回后会冻结，"
                                 "它们可能永远完成不了。在返回之前等它们完成，失败时的处理按原来的语义：\n" + "\n".join(unfinished[:20]),
                                 reason="unawaited", by=spec.id)
            self._wait_for_upstream(spec.id, "unawaited")
            return True
        self.board.report_gate(spec.id, False)
        self._open_case("triage", spec.id, "裁判在目标版本上没有通过",
                        {"run_judge 的结果": _clip(json.dumps(detail, ensure_ascii=False, indent=1)),
                         "完整判定": "verification/verdict_judge.json", "规则": JUDGE_TRIAGE_RULES})
        return True

    def _after_j3(self, j3: str) -> None:
        """Route the classified differences of a reconciliation (one round each time):
        fact_error -> F2 corrects the facts and J1 updates its predictions;
        case_error -> J1; mask_gap -> J2 again (with judge/mask_rules.json);
        needs_ruling -> an architect ruling task the next tasks wait for.
        White-box cases (wb-*) are J4's: their case errors and prediction updates go
        to J4, and differences only in them have J4's own budget (max_strength_rounds),
        so J1's rounds do not use up what J4's cases need.
        After the rounds, what is left goes to a person."""
        import judge

        diffs, classes, _ = judge.reconcile_classes(self.ws)
        if not diffs:
            return
        groups = {k: [{**d, **(classes.get(d["id"]) or {})} for d in diffs if (classes.get(d["id"]) or {}).get("class") == k]
                  for k in judge.RECONCILE_CLASSES}
        blocks = self._next_tasks(j3)
        j4 = self._task_by(mode="J4")

        def white_box(d: dict[str, Any]) -> bool:
            return bool(j4) and str(d.get("case") or "").startswith("wb-")

        only_j4 = all(white_box(d) for d in diffs)
        done = sum(1 for e in self.events.all() if e["type"] == "reconcile_round" and bool(e.get("white_box")) == only_j4)
        if done >= (self.template.max_strength_rounds if only_j4 else self.template.max_reconcile_rounds):
            self._human_gate("HUMAN_RECONCILE", f"规格对账经过 {done} 轮，仍有 {len(diffs)} 条差异（见 judge/reconcile.json 和 "
                             f"judge/reconcile_classes.json）：" + json.dumps({k: [d["id"] for d in v] for k, v in groups.items() if v},
                                                                               ensure_ascii=False), blocks)
            return
        rnd = done + 1
        self.events.append("reconcile_round", task=j3, round=rnd, white_box=only_j4, counts={k: len(v) for k, v in groups.items()},
                           ids={k: [d["id"] for d in v] for k, v in groups.items() if v})
        j1 = self._task_by(mode="J1", upstream_of=j3)
        j2 = self._task_by(action="run_monolith", upstream_of=j3)
        f2 = self._task_by(role="m2s-spec-annotator", upstream_of=j1) if j1 else None
        fact_targets = {f2: groups["fact_error"]} if f2 else self._fact_targets(groups["fact_error"])
        keys = ("id", "case", "step", "request", "check", "predicted", "actual", "fact_refs", "reason", "fact_ref", "fix")

        def show(items: list[dict[str, Any]]) -> str:
            return _clip(json.dumps([{k: d[k] for k in keys if d.get(k) is not None} for d in items], ensure_ascii=False, indent=1))

        unrouted: list[dict[str, Any]] = []
        if groups["fact_error"] and fact_targets:
            for target, items in fact_targets.items():
                self._request_rework(target, "规格对账（J3）发现事实库和单体的实际行为不一致（判为事实错误）。按单体代码核实，用 "
                                     "annotate_entry_point 修正下面这些事实（每条都要有出处），不要改动无关的事实；改完 "
                                     "check_provenance 必须 PASS。`predicted` 是按事实库推出的预期，`actual` 是单体的实际输出：\n"
                                     + show(items), reason="reconcile", by=j3)
        else:
            unrouted += groups["fact_error"]

        def parts(fact: list[dict[str, Any]], case: list[dict[str, Any]]) -> list[str]:
            out = []
            if fact and fact_targets:
                out.append("事实库已按下面这些差异修正。只更新这些用例的 predicted，使它符合修正后的事实库（重新读事实库），"
                           "不改请求：\n" + show(fact))
            if case:
                out.append("规格对账（J3）判定下面这些用例写错了，按 reason 修改：\n" + show(case))
            return out

        def send(task: str, text: str) -> None:
            if self.board.status(task) == "closed":
                self._request_rework(task, text, reason="reconcile", by=j3)
            else:  # already stale: F2 was reworked above, or it waits behind this J3
                self.events.append("round_start", task=task, cause="rework", reason="reconcile", mode="rerun", by=j3)
                self._feedback(task, j3, text)

        j1_parts = parts([d for d in groups["fact_error"] if not white_box(d)],
                         [d for d in groups["case_error"] if not white_box(d)])
        if j1_parts and j1:
            send(j1, "\n\n".join(j1_parts))
        elif any(not white_box(d) for d in groups["case_error"]):
            unrouted += [d for d in groups["case_error"] if not white_box(d)]
        j4_parts = parts([d for d in groups["fact_error"] if white_box(d)],
                         [d for d in groups["case_error"] if white_box(d)])
        if j4_parts:
            send(j4, "\n\n".join(j4_parts))  # J4's changed cases make J2 run again (_after_j4)
        if groups["mask_gap"] and not (j1_parts and j1):  # a J1 rework reruns J2 anyway
            if j2:
                self._make_stale([j2], by=j3)
            else:
                unrouted += groups["mask_gap"]
        if groups["needs_ruling"]:
            self._ruling_task(j3, rnd, groups["needs_ruling"], blocks, show(groups["needs_ruling"]))
        if unrouted:
            self._human_gate("HUMAN_RECONCILE", "规格对账的这些差异在模板里没有可以退回的任务：" + show(unrouted), blocks)
        self.log(f"{j3}: reconcile round {rnd}: " + ", ".join(f"{k} {len(v)}" for k, v in groups.items() if v))

    def _fact_targets(self, items: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
        """The annotator task(s) that own these fact corrections (v3 M4/W4.2).
        In a split template the family (e.g. F2) is not a board task, so each
        difference is routed to the package child that owns its endpoint; a
        difference that maps to no package goes to the shared child."""
        families = [t.id for t in self.template.tasks
                    if t.role == "m2s-spec-annotator" and t.split in ("package", "domain")]
        if not families:
            return {}
        family = families[-1]
        tasks = self.board.tasks()
        children = sorted(t for t in tasks if t.startswith(family + "-") and t != family)
        if not children:
            return {}
        plan = _read_json(self.ws / "storage" / "work_packages.json") or {}
        owner: dict[str, str] = {}
        for package in plan.get("packages") or []:
            child = f"{family}-{package.get('id')}"
            if child not in children:
                continue
            for e in package.get("endpoints") or []:
                owner[f"{e.get('method')} {e.get('path')}"] = child
        shared = f"{family}-shared"
        out: dict[str, list[dict[str, Any]]] = {}
        for d in items:
            endpoint = str(d.get("endpoint") or "").strip()
            if not endpoint:
                request = d.get("request") or {}
                endpoint = f"{request.get('method', '')} {request.get('path', '')}".strip()
            target = owner.get(endpoint)
            if not target:
                for ref in d.get("fact_refs") or []:
                    target = owner.get(str(ref).split("#")[0].strip())
                    if target:
                        break
            if target is None and shared in children:
                target = shared
            if target:
                out.setdefault(target, []).append(d)
        return out

    def _after_j4(self, j4: str) -> None:
        """J4 added white-box cases (§4.10): rules the code has and the facts lack
        (judge/spec_issues.json, status open) go to F2, which puts them into the fact
        base; new or changed cases make J2 run the monolith again (and what follows).
        J4 itself rechecks when it goes stale, so this does not loop."""
        import judge

        jd = judge.judge_dir(self.ws)
        issues = judge._read(jd / "spec_issues.json") if (jd / "spec_issues.json").is_file() else []
        routed = {i for e in self.events.all() if e["type"] == "spec_issues_routed" for i in e.get("ids") or []}
        fresh = [i for i in issues if isinstance(i, dict) and i.get("status", "open") == "open" and i.get("id") not in routed] \
            if isinstance(issues, list) else []
        j2 = self._task_by(action="run_monolith", upstream_of=j4)
        f2 = self._task_by(role="m2s-spec-annotator", upstream_of=j4)
        targets = {f2: fresh} if f2 else self._fact_targets(fresh)
        if fresh:
            if targets:
                self.events.append("spec_issues_routed", task=j4, to=sorted(targets), ids=[i["id"] for i in fresh])
                for target, items in targets.items():
                    self._request_rework(target, "白盒补测（J4）在单体代码里发现了事实库没有、或者写错了的规则（judge/spec_issues.json）。"
                                         "按代码核实，用 annotate_entry_point 补进或修正事实库（每条都带出处），不要改动无关的事实；"
                                         "改完 check_provenance 必须 PASS：\n" + _clip(json.dumps(items, ensure_ascii=False, indent=1)),
                                         reason="spec_issues_j4", by=j4)
                self.log(f"{j4}: {len(fresh)} spec issues -> {sorted(targets)}")
                return  # F2's rework makes everything after it stale, J2 included
            self._human_gate("HUMAN_SPEC_ISSUES_J4", f"J4 报了 {len(fresh)} 条规格问题，模板里没有 annotator 任务可以退回："
                             + json.dumps([i.get("id") for i in fresh], ensure_ascii=False), self._next_tasks(j4))
            return
        oracle = judge._read(jd / "oracle_expected.json") if (jd / "oracle_expected.json").is_file() else {}
        if j2 and (oracle.get("meta") or {}).get("inputs_sha") != judge.inputs_sha(self.ws):
            self.events.append("strength_cases", task=j4)
            self._make_stale([j2], by=j4)
            self.log(f"{j4}: new or changed cases -> {j2} runs the monolith again")

    def _after_j5(self, j5c: str) -> None:
        """Mutants the J5 classification calls gaps go back to J4 (max_strength_rounds);
        after that the judge freezes with the score as it is."""
        import mutants

        found = mutants.gaps(self.ws)
        if not found:
            return
        j4 = self._task_by(mode="J4", upstream_of=j5c)
        done = self.events.count("strength_round")
        if not j4 or done >= self.template.max_strength_rounds:
            self.events.append("strength_gaps_left", task=j5c, ids=[g["id"] for g in found])
            self.log(f"{j5c}: {len(found)} gaps left after {done} rounds; the score is frozen as it is")
            return
        self.events.append("strength_round", task=j5c, round=done + 1, ids=[g["id"] for g in found])
        self._request_rework(j4, "变异检验（J5）有这些变异体没被现有用例抓住，分类判为 gap（改动会改变外部行为）。"
                             "为每一个补一个能看出差别的用例（`suggest` 是分类时的建议）：\n"
                             + _clip(json.dumps(found, ensure_ascii=False, indent=1)), reason="strength", by=j5c)
        self.log(f"{j5c}: strength round {done + 1}: {len(found)} gaps -> {j4}")

    def _ruling_task(self, j3: str, rnd: int, items: list[dict[str, Any]], blocks: list[str], shown: str) -> None:
        """Differences that need a ruling. With rooms on (v3 M4/W4.4) they go to a
        spec discussion room: each new turn task is added as a dependency of the
        gate, whose start=recheck passes once the architect's rulings cover every
        difference. Without rooms, the gate spawns an architect worker directly
        (the v2 N1 behaviour)."""
        task_id = f"RULE_{j3}_{rnd}"
        room_id = None
        if self._rooms_enabled():
            room_id = f"spec-{j3.lower()}-r{rnd}"
            state = rooms.open_spec_room(self.coord, room_id, "m2s-verifier",
                                         "规格对账（J3）有争议的差异需要裁定：\n" + shown, proposer_task=j3)
            state["ruling_gate"] = task_id
            rooms.save(self.coord, state)
            self.events.append("room_open", room=room_id, kind="spec", by=j3)
            self.log(f"room {room_id}: opened (J3 differences)")
        spec = TaskSpec(id=task_id, role="m2s-architect", mode="ruling",
                        start="recheck" if room_id else "spawn",
                        scope=(f"storage/stage_reports/{task_id}.json",), max_turns=120, timeout_min=60)
        self.board.add_task(spec.to_json())
        for task in blocks:
            self.board.add_dep(task, task_id)
        self.events.append("ruling_task", task=task_id, by=j3, round=rnd, blocks=blocks, room=room_id,
                           differences=[{"id": d["id"], "case": d["case"]} for d in items])
        self._feedback(task_id, j3, "规格对账（J3）发现下面这些地方需要裁定（单体本身的缺陷，或者可能可以放宽的行为）。"
                       "逐条用 mcp__m2s-server__record_ruling 裁定，affected_cases 写差异所在的用例：\n" + shown)

    def _ruling_task_check(self, spec: TaskSpec) -> CheckResult:
        made = next((e for e in reversed(self.events.all()) if e["type"] == "ruling_task" and e.get("task") == spec.id), None)
        if not made:
            return CheckResult("rulings_cover", True, "no differences to rule on")
        spawn = self.events.last(spec.id, "spawn")
        found = rulings.proposals(self.coord, spec.id, spawn["attempt"]) if spawn else []
        standing = [r for r in rulings.effective(self._ruling_entries()) if r["status"] in ("decided", "confirmed", "proposed")]
        covered = {c.split("#")[0] for r in [*found, *standing] for c in r["affects"]["cases"]}
        missing = [d["id"] for d in made["differences"] if d["case"] not in covered]
        return CheckResult("rulings_cover", not missing,
                           "every difference has a ruling" if not missing else f"no ruling names the case of {missing}",
                           relay="" if not missing else f"这些差异还没有裁定（record_ruling 的 affected_cases 里要有它们的用例）：{missing}")

    def _act_run_monolith(self, spec: TaskSpec) -> CheckResult:
        import judge
        from coord import locks as coord_locks

        if self._judge_settled():
            return CheckResult("run_monolith", True, "the judge is frozen and unchanged: not run again")
        try:
            # v3 M4/W4.3: the monolith run starts containers too; share the one
            # machine-wide slot with run_validate / run_rie_verdict.
            with coord_locks.hold_count("docker-rie", owner="run_monolith"):
                r = judge.run_monolith(self.ws)
        except judge.JudgeError as exc:
            return CheckResult("run_monolith", False, f"run_monolith: {exc}", detail={"status": "ERROR", "error": str(exc)})
        cov = (r.get("coverage") or {}).get("lines")
        return CheckResult("run_monolith", r.get("status") == "PASS",
                           f"run_monolith {r.get('status')}: {r.get('cases')} cases, {r.get('fault_runs')} fault runs, "
                           f"{r.get('unstable_total', 0)} unstable, {len(r.get('problems') or [])} problems"
                           + (f", coverage {cov}" if cov else ""), detail=r)

    def _content_refusal(self, task_id: str) -> bool:
        spawn = self.events.last(task_id, "spawn")
        return bool(spawn) and is_content_refusal(stream_end_error(Path(spawn["run_dir"])))

    def _judge_frozen(self) -> bool:
        return (self.ws / "judge" / "judge_manifest.json").is_file()

    def _judge_settled(self) -> bool:
        """Frozen with the hashes of its manifest: nothing of the judge line runs again."""
        import judge

        if not self._judge_frozen():
            return False
        changes = judge.judge_changes(self.ws)
        return changes is not None and not any(changes.values())

    def _act_freeze(self, spec: TaskSpec) -> CheckResult:
        import judge

        r = judge.freeze_judge(self.ws)
        if r["status"] != "PASS":
            return CheckResult("freeze_judge", False, f"freeze_judge: {r.get('error')}: {r.get('problems', '')}", detail=r)
        self.events.append("judge_frozen", task=spec.id, version=r["version"], unchanged=bool(r.get("unchanged")))
        return CheckResult("freeze_judge", True, f"judge v{r['version']} frozen" + (" (unchanged)" if r.get("unchanged") else
                           f": {r.get('cases')} cases, {r.get('files')} files, coverage {(r.get('coverage') or {}).get('lines')}"),
                           detail=r)

    def _act_reconcile(self, spec: TaskSpec) -> CheckResult:
        import judge

        if self._judge_settled():
            return CheckResult("reconcile_spec", True, "the judge is frozen and unchanged: not reconciled again")
        r = judge.reconcile(self.ws)
        return CheckResult("reconcile_spec", True, f"{r['differences']} differences, "
                           f"{r['resolved_by_rulings']} settled by rulings, {r['steps']} steps", detail=r)

    def _act_gap_scan(self, spec: TaskSpec) -> CheckResult:
        """G1 (v2 §4.11): the gap inventory from the fact base's signals."""
        import gaps

        try:
            r = gaps.gap_scan(self.ws)
        except gaps.GapError as exc:
            return CheckResult("gap_scan", False, f"gap_scan: {exc}", detail={"status": "ERROR", "error": str(exc)})
        return CheckResult("gap_scan", True, f"gap_scan: {r['gaps']} gaps ({r['added']} new): {r['by_category']}", detail=r)

    def _act_run_mutants(self, spec: TaskSpec) -> CheckResult:
        """J5 (v2 §4.10): mutants of the monolith against the judge. The reverse check
        (parallel -> serial) must not be caught: if it is, the judge compares an
        implementation trace and a person looks at it."""
        import judge
        import mutants

        if self._judge_settled():
            return CheckResult("run_mutants", True, "the judge is frozen and unchanged: its score stands")
        try:
            r = mutants.run_mutants(self.ws)
        except judge.JudgeError as exc:
            return CheckResult("run_mutants", False, f"run_mutants: {exc}", detail={"status": "ERROR", "error": str(exc)})
        s = r["summary"]
        return CheckResult("run_mutants", r["status"] == "PASS",
                           f"run_mutants: score {s.get('score')} ({s.get('killed')} killed of {s.get('mutants')}; "
                           f"{s.get('survived')} survived, {s.get('not_covered')} not covered, {s.get('trace_only')} trace only, "
                           f"{s.get('invalid')} invalid); reverse {s.get('reverse')}", detail=r)

    def _act_check_adapter(self, spec: TaskSpec) -> CheckResult:
        """V1 (v2 §4.8): the target's reads over the judge's fixtures, written
        through the adapter map, answer what the monolith answered."""
        import judge

        r = judge.check_adapter(self.ws)
        if r["status"] == "ERROR":
            return CheckResult("check_adapter", False, f"check_adapter: {r.get('error')}", detail=r)
        return CheckResult("check_adapter", r["status"] == "PASS",
                           f"check_adapter {r['status']}: {r.get('data_steps')} data steps in {r.get('cases')} cases"
                           + (f", failed {r['failed']}" if r.get("failed") else ""), detail=r,
                           relay="" if r["status"] == "PASS" else
                           "check_adapter（按适配映射写入裁判数据后读回）和单体不一致：\n" + json.dumps(r.get("failures"), ensure_ascii=False)[:4000])

    def _act_run_judge(self, spec: TaskSpec) -> CheckResult:
        """V2 (v2 §4.9): the frozen judge on the target; verification/verdict_judge.json."""
        import judge

        r = judge.run_judge(self.ws)
        if r["status"] == "ERROR":
            return CheckResult("run_judge", False, f"run_judge: {r.get('error')}", detail=r)
        return CheckResult("run_judge", r["status"] == "PASS",
                           f"run_judge {r['status']}: cases {r['summary']['cases']}, fault runs {r['summary']['fault_runs']}",
                           detail=r)

    # ------------------------------------------------------------------ dispatcher actions

    def _act_analyze(self, spec: TaskSpec) -> CheckResult:
        import wrap_m2s

        report = _read_json(self.ws / "storage" / "analysis_report.json")
        if isinstance(report, dict) and (report.get("spec_meta") or {}).get("monolith_root"):
            # analyzing again would throw the annotations away
            return CheckResult("analyze_project", True, "storage/analysis_report.json is already there: kept")
        wrap_m2s.analyze(str(self.monolith), str(self.ws))
        return CheckResult("analyze_project", True, f"analyzed {self.monolith.name}")

    def _act_human(self, spec: TaskSpec) -> CheckResult:
        return CheckResult("human", False, "waiting for a person", detail={"escalate": True})

    def _acceptance(self) -> list[CheckResult]:
        results = [self.checks["gate_final"](self.ws), self.checks["consistency_pass"](self.ws)]
        verdict = _read_json(self.ws / "verification" / "verdict_A.json") or {}
        results.append(CheckResult("verdict_A", verdict.get("status") == "PASS", f"verdict_A {verdict.get('status')!r}"))
        return results

    def _commit_and_tag(self, tag: str, message: str, paths: tuple[str, ...]) -> tuple[str, bool]:
        """Commit `paths` in the workspace's own repo and tag it: (short sha, tag moved)."""
        lock = self.locks.acquire(f"ws-git:{self.ws.name}", "dispatcher", ttl_s=180, wait_s=60)
        try:
            if not (self.ws / ".git").is_dir():
                _git(self.ws, "init", "-q")
            self._write_dispatcher_files()  # the tag carries the harness and rulings it was built with
            exclude = self.ws / ".git" / "info" / "exclude"
            exclude.parent.mkdir(parents=True, exist_ok=True)
            if "coord/" not in (exclude.read_text(encoding="utf-8") if exclude.exists() else ""):
                with exclude.open("a", encoding="utf-8") as fh:
                    fh.write("\ncoord/\n")
            _git(self.ws, "add", "-A", *paths)
            _git(self.ws, "-c", "user.name=m2s-pipeline", "-c", "user.email=m2s@local", "commit", "-q", "-m", message)
            moved = bool(_git(self.ws, "tag", "--list", tag).stdout.strip())
            _git(self.ws, "tag", "-f", tag)
            return _git(self.ws, "rev-parse", "--short", tag).stdout.strip(), moved
        finally:
            if lock:
                lock.release()

    def _act_tag_v0(self, spec: TaskSpec) -> CheckResult:
        results = self._acceptance()
        if not all(r.ok for r in results):
            return CheckResult("tag_v0", False, "acceptance failed: " + "; ".join(r.summary for r in results if not r.ok))
        sha, moved = self._commit_and_tag("v0", "v0: stage A accepted", ("output", "verification", "storage"))
        self.events.append("tag_v0", task=spec.id, commit=sha, moved=moved)
        return CheckResult("tag_v0", bool(sha), f"tagged v0 at {sha}" + (" (moved)" if moved else ""))

    def _act_tag_b0(self, spec: TaskSpec) -> CheckResult:
        """B0 (v2 §4.13): the judge and the delta tests both pass on this code."""
        import gaps
        import judge

        problems = []
        for name in ("verdict_judge", "verdict_delta"):
            status = (_read_json(self.ws / "verification" / f"{name}.json") or {}).get("status")
            if status != "PASS":
                problems.append(f"{name} {status!r}")
        changes = judge.judge_changes(self.ws)
        if changes is None or any(changes.values()):
            problems.append("the judge is not frozen or has changed" + (f": {changes}" if changes else ""))
        problems += [f"the judge does not verify {p}" for p in
                     gaps.judge_case_problems(self.ws, _read_json(self.ws / "verification" / "verdict_judge.json") or {})]
        if problems:
            return CheckResult("tag_b0", False, "acceptance failed: " + "; ".join(problems))
        sha, moved = self._commit_and_tag("B0", "B0: judge and delta tests pass", ("output", "verification", "storage", "judge"))
        self.events.append("tag_b0", task=spec.id, commit=sha, moved=moved)
        return CheckResult("tag_b0", bool(sha), f"tagged B0 at {sha}" + (" (moved)" if moved else ""))

    def _act_final(self, spec: TaskSpec) -> CheckResult:
        s5 = _read_json(self.ws / "storage" / "stage_reports" / "S5.json") or {}
        perf = _read_json(self.ws / "output" / "perf_report.json") or {}
        pending_spec = [c.get("id") for c in perf.get("changes") or [] if isinstance(c, dict) and c.get("verdict") == "pending_spec"]
        if s5.get("blocked") or pending_spec:
            return CheckResult("final", False, "S5 left changes that need the test review (VR) or a spec-relaxation ruling; "
                               f"M1 hands these to a person (blocked={s5.get('blocked')}, pending_spec={pending_spec})",
                               detail={"escalate": True})
        results = [self.checks["gate_final"](self.ws), self.checks["validate_full"](self.ws)]
        if all(r.ok for r in results):
            return CheckResult("final", True, "gate(final) PASS, full regression PASS")
        head, v0 = _git(self.ws, "rev-parse", "HEAD").stdout.strip(), _git(self.ws, "rev-parse", "v0").stdout.strip()
        if head and v0 and head != v0 and not self.events.count("s5_reverted"):
            _git(self.ws, "checkout", "v0", "--", "output")
            _git(self.ws, "-c", "user.name=m2s-pipeline", "-c", "user.email=m2s@local", "commit", "-q", "-am", "revert S5: stage 6 failed")
            self.events.append("s5_reverted", task=spec.id, failed=[r.summary for r in results if not r.ok])
            results = [self.checks["gate_final"](self.ws), self.checks["validate_full"](self.ws)]
            if all(r.ok for r in results):
                return CheckResult("final", True, "S5's changes broke the final check and were reverted; v0 delivered")
        return CheckResult("final", False, "final check failed: " + "; ".join(r.summary for r in results if not r.ok),
                           detail={"escalate": True})

    def _act_final_v2(self, spec: TaskSpec) -> CheckResult:
        """C1 (v2 §4.15): gate(final), the whole judge and all delta tests on the code to deliver.
        If the optimizer's changes broke them, back to B0 once. Then output/final_report.md."""
        from coord import closing

        report = _read_json(self.ws / "storage" / "stage_reports" / "O.json") or {}
        perf = _read_json(self.ws / "output" / "perf_report.json") or {}
        pending = [c.get("id") for c in perf.get("changes") or [] if isinstance(c, dict) and c.get("verdict") == "pending_spec"]
        if report.get("blocked") or pending:
            return CheckResult("final", False, f"the optimizer left changes a person has to look at (blocked={report.get('blocked')}, "
                               f"pending_spec={pending})", detail={"escalate": True})

        def checks() -> list[CheckResult]:
            return [self.checks[name](self.ws) for name in ("gate_final", "judge_full", "delta_full")]

        results = checks()
        reverted = False
        if not all(r.ok for r in results):
            head, b0 = _git(self.ws, "rev-parse", "HEAD").stdout.strip(), _git(self.ws, "rev-parse", "B0").stdout.strip()
            if head and b0 and head != b0 and not self.events.count("o_reverted"):
                _git(self.ws, "checkout", "B0", "--", "output")
                _git(self.ws, "-c", "user.name=m2s-pipeline", "-c", "user.email=m2s@local", "commit", "-q", "-am", "revert O: the final check failed")
                self.events.append("o_reverted", task=spec.id, failed=[r.summary for r in results if not r.ok])
                reverted = True
                results = checks()
        if not all(r.ok for r in results):
            return CheckResult("final", False, "final check failed: " + "; ".join(r.summary for r in results if not r.ok),
                               detail={"escalate": True})
        self._write_dispatcher_files()
        open_items = [*self._flagged_issues(),
                      *[f"- {e['task']}：{e.get('reason')}" for e in self.events.all() if e["type"] == "escalate" and self.board.status(e["task"]) != "closed"],
                      *[f"- 留到最后的未决问题：{e['text']}" for e in self.events.all() if e["type"] == "open_issue"],
                      *[f"- 裁判强度：冻结时还有没补上的变异体 {e.get('ids')}" for e in self.events.all() if e["type"] == "strength_gaps_left"]]
        text = closing.final_report(self.ws, results=results, rulings=rulings.effective(self._ruling_entries()),
                                    open_items=open_items, reverted=reverted)
        (self.ws / "output").mkdir(exist_ok=True)
        (self.ws / "output" / "final_report.md").write_text(text, encoding="utf-8")
        if (self.ws / ".git").is_dir():
            _git(self.ws, "add", "-A", "output/final_report.md", "verification", "storage")
            _git(self.ws, "-c", "user.name=m2s-pipeline", "-c", "user.email=m2s@local", "commit", "-q", "-m", "C1: final report")
        return CheckResult("final", True, "gate(final), the judge and the delta tests pass; output/final_report.md written"
                           + ("; the optimizer's changes were reverted" if reverted else ""))

    def _act_archive(self, spec: TaskSpec) -> CheckResult:
        """C2 (v2 §4.15): the run's evidence goes to the evidence store, append-only."""
        from coord import closing

        self._write_dispatcher_files()
        self.events.append("archive_start", task=spec.id)
        (self.coord / "report.md").write_text(self.report("archiving"), encoding="utf-8")
        r = closing.archive_run(self.ws, self.monolith)
        if r["status"] != "PASS":
            return CheckResult("archive_run", False, f"archive_run: {r.get('error')}: {r.get('files')}", detail={**r, "escalate": True})
        self.events.append("archived", task=spec.id, path=r["path"], missing=r["missing"])
        return CheckResult("archive_run", True, f"archived to {r['path']}" + (f"; not there: {r['missing']}" if r["missing"] else ""), detail=r)

    # ------------------------------------------------------------------ outcome and report

    def _outcome(self) -> str | None:
        statuses = {t: info["status"] for t, info in self.board.tasks().items()}
        if all(s == "closed" for s in statuses.values()):
            return "done"
        if any(s == "needs_human" for s in statuses.values()):
            return "needs_human"
        if self.stop_after and statuses.get(self.stop_after) == "closed":
            return "stopped"
        return "stalled"

    def _finish(self, outcome: str) -> int:
        self.events.append("run_end", outcome=outcome, stop_after=self.stop_after)
        report = self.coord / "report.md"
        report.write_text(self.report(outcome), encoding="utf-8")
        shown = f"stopped after {self.stop_after}" if outcome == "stopped" else outcome
        self.log(f"run {shown}; report: {report}")
        return {"done": EXIT_DONE, "needs_human": EXIT_HUMAN, "stopped": EXIT_STOPPED}.get(outcome, EXIT_STALLED)

    def _task_events_text(self, task_id: str) -> str:
        lines = []
        for e in self.events.of_task(task_id):
            shown = {k: v for k, v in e.items() if k not in ("seq", "task", "text")}
            lines.append(f"- {json.dumps(shown, ensure_ascii=False)[:400]}")
        return "\n".join(lines[-40:])

    def _feedback_text(self, task_id: str) -> str:
        return "\n\n".join(f"[{e.get('source')}] {_clip(e.get('text', ''), 3000)}" for e in self.events.of_task(task_id, "feedback")[-4:])

    @staticmethod
    def _results_text(results: list[CheckResult]) -> str:
        return "\n".join(f"- {r.name}: {'通过' if r.ok else '未通过'} — {r.summary}\n{r.relay}" for r in results)

    def _summary_text(self) -> str:
        rows = []
        for task_id, info in self.board.tasks().items():
            rows.append(f"- {task_id}: {info['status']}，启动 worker {self.events.count('spawn', task_id)} 次")
        carried = self.events.last("V1", "spec_issues_carried")
        if carried:
            rows.append(f"- 留到最终报告的 non_blocking spec_issues：{carried['issues']}")
        rows += [f"- 人决定留到最终报告的未决问题：{e['text']}" for e in self.events.all() if e["type"] == "open_issue"]
        rows += [f"- 人的决定（{e['task']}，{HUMAN_KINDS[e['type']]}）：{e.get('comment', '')}" for e in self._human_events()]
        return "\n".join(rows)

    def _human_events(self) -> list[dict[str, Any]]:
        return [e for e in self.events.all() if e["type"] in HUMAN_KINDS]

    def report(self, outcome: str) -> str:
        tasks = self.board.tasks()
        shown = f"stopped（`--stop-after {self.stop_after}`，后面的任务未启动）" if outcome == "stopped" else outcome
        lines = [f"# 运行报告：{self.ws.name}", "", f"- 结果：**{shown}**", f"- 单体：{self.monolith}",
                 f"- 模板：{self.template.name}", ""]
        seen: list[str] = []
        for event in self.events.all():  # one line per distinct harness version (the tools may change between segments)
            if event["type"] != "harness":
                continue
            stamp = event.get("stamp") or {}
            text = (f"m2s {str(stamp.get('m2s', {}).get('commit'))[:10]}"
                    f"（未提交改动 {len(stamp.get('m2s', {}).get('dirty', []))} 处），OpenHarness "
                    f"{str(stamp.get('openharness', {}).get('commit'))[:10]}，模型 {stamp.get('models', {}).get('model')}")
            if text not in seen:
                seen.append(text)
                lines.append(f"- harness（{event['ts'][:16]} 起）：{text}" if "ts" in event else f"- harness：{text}")
        if seen:
            lines.append("")
        lines += ["| 任务 | 状态 | 启动次数 | 崩溃 | API 中断 | 用时（分钟） | 最后一次检查 |", "|---|---|---|---|---|---|---|"]
        for task_id, info in sorted(tasks.items(), key=lambda kv: self.template.order(kv[0])):
            exits = self.events.of_task(task_id, "worker_exit")
            minutes = round(sum(e.get("duration_s") or 0 for e in exits) / 60, 1)
            last = self.events.last(task_id, "checks")
            last_text = "；".join(f"{r['name']}{'✓' if r['ok'] else '✗'}" for r in (last or {}).get("results", []))
            lines.append(f"| {task_id} | {info['status']} | {self.events.count('spawn', task_id)} | "
                         f"{sum(1 for e in exits if e.get('crashed') and not e.get('infra'))} | "
                         f"{sum(1 for e in exits if e.get('infra'))} | {minutes} | {last_text} |")
        cases = [e for e in self.events.all() if e["type"] == "case_decision"]
        if cases:
            lines += ["", "## 异常处理", ""] + [f"- {e['case']}：{(e.get('decision') or {}).get('action')} → {e['applied']}" for e in cases]
        escalations = [e for e in self.events.all() if e["type"] == "escalate"]
        if escalations:
            lines += ["", "## 交给人", ""] + [f"- {e['task']}：{e['reason']}" for e in escalations]
        rounds = [e for e in self.events.all() if e["type"] == "reconcile_round"]
        frozen = [e for e in self.events.all() if e["type"] == "judge_frozen" and not e.get("unchanged")]
        if rounds or frozen:
            lines += ["", "## 裁判线", ""]
            lines += [f"- 规格对账第 {e['round']} 轮：" + "，".join(f"{k} {v}" for k, v in e["counts"].items() if v)
                      for e in rounds]
            lines += [f"- 裁判 v{e['version']} 已冻结（{e.get('ts', '')[:16]}），见 judge/judge_manifest.json" for e in frozen]
        ruled = rulings.effective(self._ruling_entries())
        if ruled:
            lines += ["", "## 裁定", ""] + [f"- {r['id']}（{r['decider']}，{r['status']}，{r['category']}）：{_clip(r['question'], 200)} → "
                                             f"{_clip(r['conclusion'], 200)}" for r in ruled]
        human = self._human_events()
        if human:
            lines += ["", "## 人的决定", ""] + [f"- {e.get('ts', '')[:16]} {e['task']}：{HUMAN_KINDS[e['type']]}。{e.get('comment', '')}"
                                                for e in human]
        carried = self.events.last("V1", "spec_issues_carried")
        if carried:
            lines += ["", "## 仍然 open 的 non_blocking spec_issues", "", f"- {carried['issues']}"]
        issues = [e for e in self.events.all() if e["type"] == "open_issue"]
        if issues:
            lines += ["", "## 人决定暂不处理的未决问题", ""] + [f"- {e['text']}（{e['task']}）" for e in issues]
        if ruled:
            from coord import closing

            counts = closing.ruling_counts(ruled)
            lines += ["", f"裁定共 {counts['total']} 条，其中人工裁定或确认 {counts['human']} 条，还在等人 {counts['waiting']} 条。"]
        inventory = self._gap_lines()
        if inventory:
            lines += ["", "## 缺口清单", ""] + inventory
        flagged = self._flagged_issues()
        if flagged:
            lines += ["", "## 工作报告里要人看的内容（调度器没有等它们）", ""] + flagged
        return "\n".join(lines) + "\n"

    def _gap_lines(self) -> list[str]:
        import gaps

        data = gaps.load(self.ws) or {}
        entries = [g for g in data.get("gaps") or [] if isinstance(g, dict)]
        if not entries:
            return []
        tests: dict[str, int] = {}
        for g in entries:
            test = (g.get("decision") or {}).get("test") or "未决定"
            tests[test] = tests.get(test, 0) + 1
        lines = [f"- {len(entries)} 条：" + "，".join(f"{k} {v}" for k, v in sorted(tests.items()))]
        lines += [f"- 新类别候选 {g.get('id')}（{g.get('candidate_name')}）：{_clip(str(g.get('summary')), 200)}"
                  for g in entries if g.get("category") == "new"]
        return lines

    def _flagged_issues(self) -> list[str]:
        """Issues a worker marked needs_human in its report. The dispatcher acts on rulings and
        on report_blocked, not on these, so at least the report has to show them."""
        out = []
        for task_id in self.board.tasks():
            report = _read_json(self.ws / "storage" / "stage_reports" / f"{task_id}.json")
            for issue in (report.get("issues") or []) if isinstance(report, dict) else []:
                if isinstance(issue, dict) and "needs_human" in (issue.get("type"), issue.get("kind")):
                    text = issue.get("text") or issue.get("detail") or json.dumps(issue, ensure_ascii=False)
                    out.append(f"- {task_id}：{_clip(str(text), 600)}")
        return out


# ---------------------------------------------------------------------- CLI


def _load_run(ws: Path) -> Dispatcher | None:
    events = EventLog(ws / "coord" / "events.jsonl")
    start = next((e for e in events.all() if e["type"] == "run_start"), None)
    if start is None:
        print(f"{ws} has no run", file=sys.stderr)
        return None
    return Dispatcher(ws, template=start.get("template", "v1"), log=print)


def _human_rulings(d: Dispatcher, files: list[str], seq: int) -> list[dict[str, Any]] | None:
    """Read and check --ruling-file arguments before anything is changed; ids follow seq."""
    out = []
    for name in files:
        try:
            raw = json.loads(Path(name).read_text(encoding="utf-8"))
            for item in raw if isinstance(raw, list) else [raw]:
                seq += 1
                out.append(rulings.human_ruling(item, seq))
        except (OSError, json.JSONDecodeError, rulings.RulingError) as exc:
            print(f"--ruling-file {name}: {exc}", file=sys.stderr)
            return None
    known = {t.id for t in d.template.tasks}
    unknown = sorted({t for r in out for t in r["affects"]["tasks"] if t not in known})
    if unknown:
        print(f"--ruling-file names unknown tasks {unknown}", file=sys.stderr)
        return None
    return out


def _resolve(args: argparse.Namespace) -> int:
    ws = Path(args.ws).resolve()
    coord = ws / "coord"
    board, events = TaskBoard(coord), EventLog(coord / "events.jsonl")
    if board.status(args.task) != "needs_human":
        print(f"{args.task} is {board.status(args.task)}, not needs_human", file=sys.stderr)
        return EXIT_ERROR
    entries = [e["entry"] for e in events.all() if e["type"] == "ruling"]
    gated = next((r for r in rulings.effective(entries) if f"HUMAN_{r['id']}" == args.task), None)
    if gated is None and args.reject:
        print(f"{args.task} is not a ruling gate; --reject is only for HUMAN_R-... gates", file=sys.stderr)
        return EXIT_ERROR
    if gated is not None and args.reopen:
        print(f"{args.task} is a ruling gate: use --accept (confirm) or --reject", file=sys.stderr)
        return EXIT_ERROR
    d = _load_run(ws) if args.ruling_file or gated is not None else None
    if (args.ruling_file or gated is not None) and d is None:
        return EXIT_ERROR
    seq = sum(1 for e in entries if e.get("decider") == "human") + (gated is not None)  # the settlement comes first
    new_rulings = _human_rulings(d, args.ruling_file, seq) if args.ruling_file else []
    if new_rulings is None:
        return EXIT_ERROR
    if gated is not None:
        settle = rulings.settlement(gated["id"], rulings.CONFIRM if args.accept else rulings.REJECT, args.comment, seq)
        board.close(args.task, comment=f"human: {args.comment}")
        events.append("ruling", task=args.task, entry=settle)
        events.append("human_accept" if args.accept else "human_reject", task=args.task, comment=args.comment)
        d._write_dispatcher_files()
        if args.accept:
            d._apply_ruling(gated, by="human")
    elif args.accept:
        board.close(args.task, comment=f"human: {args.comment}")
        events.append("human_accept", task=args.task, comment=args.comment)
    else:
        board.set_status(args.task, "open", comment=f"human: {args.comment}")
        events.append("round_start", task=args.task, cause="human", mode="rerun")
        if args.feedback:
            events.append("feedback", task=args.task, source="human", text=args.feedback)
        events.append("human_reopen", task=args.task, comment=args.comment)
    for ruling in new_rulings:
        events.append("ruling", task="human", entry=ruling)
    if new_rulings:
        d._write_dispatcher_files()
        for ruling in new_rulings:
            d._apply_ruling(ruling, by="human")
    if gated is not None:
        decision = f"{'确认' if args.accept else '否决'}裁定 {gated['id']}"
    else:
        decision = "接受" if args.accept else "重做"
    if new_rulings:
        decision += "；新的裁定 " + "、".join(r["id"] for r in new_rulings)
    _record_human(coord, events, args.task, decision, args.comment, args.open_issue)
    done = "accepted" if args.accept else "rejected" if args.reject else "reopened"
    print(f"{args.task}: {done}; run the dispatcher again to continue")
    return EXIT_DONE


def _rework(args: argparse.Namespace) -> int:
    """A person sends a closed task back (e.g. after adding a check that it now fails).
    Closed tasks downstream go stale and follow their on_stale rule, as for any rework."""
    ws = Path(args.ws).resolve()
    events = EventLog(ws / "coord" / "events.jsonl")
    dispatcher = _load_run(ws)
    if dispatcher is None:
        return EXIT_ERROR
    if args.task not in {t.id for t in dispatcher.template.tasks}:
        print(f"{args.task} is not a task of template {dispatcher.template.name}", file=sys.stderr)
        return EXIT_ERROR
    status = dispatcher.board.status(args.task)
    if status != "closed":
        print(f"{args.task} is {status}; only a closed task can be sent back (use resolve for needs_human)", file=sys.stderr)
        return EXIT_ERROR
    mode = "recheck" if args.recheck else "rerun"
    dispatcher._request_rework(args.task, args.feedback or args.comment, reason="human", by="human", mode=mode)
    events.append("human_rework", task=args.task, comment=args.comment, mode=mode)
    _record_human(ws / "coord", events, args.task, f"退回（{mode}）", args.comment, args.open_issue)
    reopened = [t for t in dispatcher._downstream([args.task]) if dispatcher.board.status(t) == "open"]
    print(f"{args.task}: sent back ({mode}); open downstream: {reopened}; run the dispatcher again to continue")
    return EXIT_DONE


def _record_human(coord: Path, events: EventLog, task: str, decision: str, comment: str, open_issues: list[str]) -> None:
    """The journal is what the exception handler reads; a person's decisions belong there too,
    so a later phase review does not ask the same question again."""
    for text in open_issues:
        events.append("open_issue", task=task, text=text)
    journal = Casebook(coord, events).journal
    if not journal.exists():
        journal.write_text("# 异常处理记录\n\n本次运行中异常处理者和人做过的判断，按时间排列。\n\n", encoding="utf-8")
    entry = f"## 人的决定（任务 {task}，{time.strftime('%Y-%m-%d %H:%M')}）\n\n- 决定：{decision}\n- 说明：{comment}\n"
    entry += "".join(f"- 留到最终报告的未决问题：{t}\n" for t in open_issues) + "\n"
    with journal.open("a", encoding="utf-8") as fh:
        fh.write(entry)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m coord.dispatcher")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_run = sub.add_parser("run", help="start or resume a run")
    p_run.add_argument("--ws", required=True)
    p_run.add_argument("--monolith")
    p_run.add_argument("--template", default="v1")
    p_run.add_argument("--max-workers", type=int, default=1)
    p_run.add_argument("--poll", type=float, default=5.0)
    p_run.add_argument("--no-handler", action="store_true", help="hand exceptions straight to a person")
    p_run.add_argument("--no-phase-review", action="store_true")
    p_run.add_argument("--stop-after", metavar="TASK",
                       help="run only TASK and its upstream tasks, then stop (exit 5); run again without it to continue")
    p_status = sub.add_parser("status", help="show task status")
    p_status.add_argument("--ws", required=True)
    p_resolve = sub.add_parser("resolve", help="resolve a needs_human task")
    p_resolve.add_argument("--ws", required=True)
    p_resolve.add_argument("--task", required=True)
    group = p_resolve.add_mutually_exclusive_group(required=True)
    group.add_argument("--accept", action="store_true", help="close the task as it is")
    group.add_argument("--reopen", action="store_true", help="run the task again")
    group.add_argument("--reject", action="store_true", help="ruling gates only: the ruling does not take effect")
    p_resolve.add_argument("--comment", required=True)
    p_resolve.add_argument("--feedback", default="")
    p_resolve.add_argument("--ruling-file", action="append", default=[], metavar="R.json",
                           help="a ruling the person makes: one object or a list (coord/rulings.py fields)")
    p_resolve.add_argument("--open-issue", action="append", default=[], metavar="TEXT",
                           help="a question left open on purpose; listed in the final report (repeatable)")
    p_rework = sub.add_parser("rework", help="send a closed task back (downstream tasks go stale)")
    p_rework.add_argument("--ws", required=True)
    p_rework.add_argument("--task", required=True)
    p_rework.add_argument("--comment", required=True)
    p_rework.add_argument("--feedback", default="", help="text for the worker (default: the comment)")
    p_rework.add_argument("--recheck", action="store_true",
                          help="rerun the task's checks first; start a worker only if they fail (e.g. after adding a check)")
    p_rework.add_argument("--open-issue", action="append", default=[], metavar="TEXT")
    args = parser.parse_args(argv)

    if args.cmd == "resolve":
        return _resolve(args)
    if args.cmd == "rework":
        return _rework(args)
    if args.cmd == "status":
        board = TaskBoard(Path(args.ws).resolve() / "coord")
        for task_id, info in board.tasks().items():
            print(f"{task_id:20} {info['status']}")
        return EXIT_DONE
    try:
        dispatcher = Dispatcher(args.ws, args.monolith, args.template, max_workers=args.max_workers, poll_s=args.poll,
                                handler=not args.no_handler, phase_review=not args.no_phase_review,
                                stop_after=args.stop_after)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_ERROR
    return dispatcher.run()


if __name__ == "__main__":
    sys.exit(main())
