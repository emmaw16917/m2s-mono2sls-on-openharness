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
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

from coord import harness, rulings
from coord.board import TaskBoard
from coord.checks import CHECKS, CheckResult, ToolGuard, _clip, _read_json
from coord.events import EventLog
from coord.handler import Case, Casebook
from coord.policy import DISPATCHER_ACTOR
from coord.prompts import HANDLER_SYSTEM_PROMPT, handler_prompt, worker_prompt
from coord.snapshot import Shadow
from coord.template import Template, TaskSpec, in_scope, load_template
from coord.tools import submission_path
from coord.workers import Launcher, OhLauncher, WorkerHandle, WorkerJob, role_prompt

# Changes no task is blamed for: dispatcher state, the v1 orchestrator's TODO, caches.
ALWAYS_ALLOWED = ("coord/**", "storage/TODO.md", "**/node_modules/**", "**/__pycache__/**")
# Workspace files only the dispatcher writes; rebuilt from the event log after every worker.
DISPATCHER_FILES = (harness.WORKSPACE_FILE.as_posix(), rulings.WORKSPACE_FILE.as_posix())
ROUTING_TARGETS = {"code": "S2", "template": "S3c", "spec": "S1"}
RELAY_FIELDS = ("function", "spec_refs", "expected_per_spec", "observed")
MAX_CRASHES = 3
# A worker whose session ended on a model-API failure (OpenHarness gave up after its own
# retries) did not fail the task: it does not use an attempt, and the next spawn waits.
INFRA_BACKOFF_S = (300, 600, 1200, 1800)
MAX_INFRA_FAILURES = 8  # per round, about 2.5 hours of waiting before a person is asked
MAX_SPAWNS_PER_TASK = 12  # backstop against a rule loop; normal limits are far lower
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
    if not message:
        return False
    return message.startswith("Network error:") or (message.startswith("API error:") and bool(_TRANSIENT_API.search(message)))


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
    for entry in spec.deny_read:
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
                        "final": self._act_final, "human": self._act_human, **(actions or {})}
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

    # ------------------------------------------------------------------ setup / resume

    def setup(self) -> None:
        if not self.board.exists():
            if self.monolith is None:
                raise ValueError("a new run needs --monolith")
            self.board.init()
            for spec in self.template.tasks:
                self.board.add_task(spec.to_json(), spec.deps)
            stamp = self.stamp()
            self.events.append("harness", stamp=stamp)
            self.events.append("run_start", ws=str(self.ws), monolith=str(self.monolith),
                               template=self.template.name, max_workers=self.max_workers, stop_after=self.stop_after)
            self.events.append("tool_baseline", baseline=self.guard.capture())
            self._write_dispatcher_files()
            self.shadow.init()
            self.log(f"new run: {len(self.template.tasks)} tasks on the board")
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
        return changed

    def _ruling_entries(self) -> list[dict[str, Any]]:
        return [e["entry"] for e in self.events.all() if e["type"] == "ruling"]

    def _reconcile(self) -> None:
        """Bring board and processes back in line after a dispatcher restart."""
        for task_id, info in self.board.tasks().items():
            status = info["status"]
            spawn = self.events.last(task_id, "spawn")
            exited = self.events.last(task_id, "worker_exit")
            live_spawn = spawn and (not exited or exited["seq"] < spawn["seq"])
            if status == "in_progress" and live_spawn:
                handle = WorkerHandle(task_id, spawn["attempt"], spawn["worker"], spawn["pid"], spawn["started"],
                                      spawn["timeout_s"], Path(spawn["run_dir"]))
                if handle.poll() is None:
                    self.running[task_id] = handle
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
        return task_id not in {t.id for t in self.template.tasks}  # human gates added by the flows

    def _busy_workers(self) -> int:
        return sum(1 for h in self.running.values() if h.kind == "task")

    def _backing_off(self, task_id: str) -> bool:
        last = self.events.last(task_id, "worker_exit")
        return bool(last and last.get("retry_after") and last["retry_after"] > time.time())

    def _start_ready(self) -> int:
        self.waiting = False
        if self._paused() or self._busy_workers() >= self.max_workers:
            return 0
        started = 0
        ready = sorted((t for t in self.board.ready() if t not in self.running and self._in_stop_set(t)),
                       key=self.template.order)
        held = [t for t in ready if self._backing_off(t)]
        self.waiting = bool(held)
        ready = [t for t in ready if t not in held]
        for task_id in ready:
            if self._busy_workers() >= self.max_workers:
                break
            self._start(task_id)
            started += 1
            if self._paused():
                break
        return started

    def _start(self, task_id: str) -> None:
        spec = self._spec(task_id)
        infra = sum(1 for e in self.events.of_task(task_id, "worker_exit") if e.get("infra"))
        if self.events.count("spawn", task_id) - infra >= MAX_SPAWNS_PER_TASK:
            self._escalate(task_id, f"{task_id} 已经启动过 {MAX_SPAWNS_PER_TASK} 次 worker，调度器不再自动重试")
            return
        if self.events.reopen_mode(task_id) == "recheck" and spec.runner == "agent":
            self._recheck(spec)
        elif spec.runner == "dispatcher":  # actions are cheap and deterministic: a stale one runs again
            self._run_action(spec)
        else:
            self._spawn(spec)

    def _collect(self) -> None:
        for key, handle in list(self.running.items()):
            rc = handle.poll()
            timed_out = rc is None and handle.timed_out()
            if rc is None and not timed_out:
                continue
            if timed_out:
                handle.kill()
            del self.running[key]
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
        )
        self.board.claim(spec.id, job.worker_name)
        snapshot = self.shadow.snapshot(f"before {spec.id} a{attempt}")
        handle = self.launcher.start(job)
        self.running[spec.id] = handle
        self.events.append("spawn", task=spec.id, attempt=attempt, worker=job.worker_name, pid=handle.pid,
                           started=handle.started, timeout_s=job.timeout_s, run_dir=str(handle.run_dir),
                           snapshot=snapshot, feedback_items=len(feedback))
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
        else:
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

    def _evaluate(self, spec: TaskSpec, attempt: int, sub: dict[str, Any] | None, snapshot: str, timed_out: bool) -> None:
        results: list[CheckResult] = []

        changes = self.shadow.changes(snapshot)
        # Files the dispatcher owns are rebuilt rather than reverted: the dispatcher itself may
        # have written one while this worker ran (a resume adopts live workers).
        changes = [(s, p) for s, p in changes if p not in DISPATCHER_FILES]
        for path in self._write_dispatcher_files():
            self.events.append("dispatcher_file_rewritten", task=spec.id, attempt=attempt, path=path)
        outside = [(s, p) for s, p in changes if not in_scope(p, spec.scope) and not in_scope(p, ALWAYS_ALLOWED)]
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

        if timed_out:
            results.append(CheckResult("finished_in_time", False, f"killed after {spec.timeout_min} min",
                                       relay=f"上一次尝试超过了 {spec.timeout_min} 分钟的时间上限，被调度器结束。抓紧完成，并且一定要调用 submit_task。"))
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
                results.append(self.checks[name](self.ws))
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
        used = sum(1 for e in self.events.of_task(target, "round_start")
                   if e.get("cause") == "rework" and e.get("reason") not in ("spec_issues", "human"))
        return used < self.template.max_rework_rounds

    def _request_rework(self, target: str, feedback: str, reason: str, by: str, mode: str = "rerun") -> None:
        """Reopen an earlier task with feedback; closed tasks downstream of it go stale."""
        self.events.append("round_start", task=target, cause="rework", reason=reason, mode=mode, by=by)
        self._feedback(target, by, feedback)
        self.board.reopen(target)
        self._make_stale(self.template.downstream(target), by=target)
        self.log(f"{target}: reworked ({reason}, requested by {by})")

    def _make_stale(self, tasks: list[str], by: str) -> list[str]:
        """The closed ones among `tasks`, and closed tasks downstream of them, go stale and
        follow their on_stale rule (recheck or rerun)."""
        hit: list[str] = []
        for task in tasks:
            for t in [task, *self.template.downstream(task)]:
                if t not in hit and self.board.status(t) == "closed":
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
        waiting = [t for t in ruling["affects"]["tasks"] if t in known and self.board.status(t) in ("open", "rework")]
        question = (f"裁定 {ruling['id']} 不照搬单体的行为，要人确认（V2-20）。问题：{ruling['question']}；"
                    f"结论：{ruling['conclusion']}；依据：{ruling['basis']}；影响的任务：{ruling['affects']['tasks']}，"
                    f"用例：{ruling['affects']['cases']}。确认用 resolve --accept，否决（照搬单体）用 resolve --reject。")
        self._human_gate(f"HUMAN_{ruling['id']}", question, waiting)

    def _apply_ruling(self, ruling: dict[str, Any], by: str) -> list[str]:
        """A ruling in force: the tasks it names get it as feedback; closed ones go stale."""
        known = {t.id for t in self.template.tasks}
        targets = [t for t in ruling["affects"]["tasks"] if t in known and t != ruling.get("task")]
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
        for task in blocks:
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
                        deny_read=self.foreign_runs)
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
            else:
                applied = "继续" if action == "ok" else f"处理者给出 {action!r}，复查只接受 ok/escalate，按继续处理"
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
        return [t.id for t in self.template.tasks if task_id in t.deps]

    # ------------------------------------------------------------------ v1 rules

    def _active_spec_round(self) -> dict[str, Any] | None:
        rounds = [e for e in self.events.all() if e["type"] in ("spec_round", "spec_round_done")]
        return rounds[-1] if rounds and rounds[-1]["type"] == "spec_round" else None

    def _flow_extra_checks(self, spec: TaskSpec) -> list[CheckResult]:
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
        if task_id == "V1":
            self._after_v1()
        elif task_id == "TAG_V0" and self.phase_review:
            self._open_case("review", "TAG_V0", "v0 已打 tag，进入 S5 前的阶段复查",
                            {"运行摘要": self._summary_text()})

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

    def _flow_failed(self, spec: TaskSpec, results: list[CheckResult]) -> bool:
        if spec.id != "V2" or not any(r.name == "validate_full" and not r.ok for r in results):
            return False
        report = _read_json(self.ws / "storage" / "stage_reports" / "V2.json") or {}
        routing = [r for r in report.get("routing") or [] if isinstance(r, dict)]
        if not routing:
            return False
        if any(r.get("to") not in ROUTING_TARGETS for r in routing):
            self.board.report_gate(spec.id, False)
            self._open_case("triage", spec.id, "V2 的分诊里有判断不了归因的失败",
                            {"V2 的 routing": json.dumps(routing, ensure_ascii=False, indent=1),
                             "检查结果": self._results_text(results)})
            return True
        targets: dict[str, list[dict[str, Any]]] = {}
        for entry in routing:
            targets.setdefault(ROUTING_TARGETS[entry["to"]], []).append(entry)
        over = [t for t in targets if not self._rework_budget_ok(t)]
        if over:
            self.board.report_gate(spec.id, False)
            self._escalate(spec.id, f"V2 要求再次退回 {over}，但这些阶段的回炉次数已用完")
            return True
        for target in sorted(targets, key=self.template.order):
            relay = [{k: e.get(k) for k in RELAY_FIELDS if k in e} for e in targets[target]]
            self._request_rework(target, "验收发现下面这些行为和规格不符，请修正：\n" + json.dumps(relay, ensure_ascii=False, indent=1),
                                 reason="verification", by="V2")
        self._wait_for_upstream(spec.id, "verification")
        return True

    def _flow_blocked(self, spec: TaskSpec, sub: dict[str, Any]) -> bool:
        raw = str(sub.get("suspected_module") or "").strip()
        ids = {t.id.upper(): t.id for t in self.template.tasks}
        roles = {t.role: t.id for t in self.template.tasks if t.role}
        target = ids.get(raw.upper()) or roles.get(raw) or ROUTING_TARGETS.get(raw.lower())
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

    # ------------------------------------------------------------------ dispatcher actions

    def _act_analyze(self, spec: TaskSpec) -> CheckResult:
        import wrap_m2s

        wrap_m2s.analyze(str(self.monolith), str(self.ws))
        return CheckResult("analyze_project", True, f"analyzed {self.monolith.name}")

    def _act_human(self, spec: TaskSpec) -> CheckResult:
        return CheckResult("human", False, "waiting for a person", detail={"escalate": True})

    def _acceptance(self) -> list[CheckResult]:
        results = [self.checks["gate_final"](self.ws), self.checks["consistency_pass"](self.ws)]
        verdict = _read_json(self.ws / "verification" / "verdict_A.json") or {}
        results.append(CheckResult("verdict_A", verdict.get("status") == "PASS", f"verdict_A {verdict.get('status')!r}"))
        return results

    def _act_tag_v0(self, spec: TaskSpec) -> CheckResult:
        results = self._acceptance()
        if not all(r.ok for r in results):
            return CheckResult("tag_v0", False, "acceptance failed: " + "; ".join(r.summary for r in results if not r.ok))
        if not (self.ws / ".git").is_dir():
            _git(self.ws, "init", "-q")
        self._write_dispatcher_files()  # v0 carries the harness and rulings it was built with
        exclude = self.ws / ".git" / "info" / "exclude"
        exclude.parent.mkdir(parents=True, exist_ok=True)
        if "coord/" not in (exclude.read_text(encoding="utf-8") if exclude.exists() else ""):
            with exclude.open("a", encoding="utf-8") as fh:
                fh.write("\ncoord/\n")
        _git(self.ws, "add", "-A", "output", "verification", "storage")
        _git(self.ws, "-c", "user.name=m2s-pipeline", "-c", "user.email=m2s@local", "commit", "-q", "-m", "v0: stage A accepted")
        moved = bool(_git(self.ws, "tag", "--list", "v0").stdout.strip())
        _git(self.ws, "tag", "-f", "v0")
        sha = _git(self.ws, "rev-parse", "--short", "v0").stdout.strip()
        self.events.append("tag_v0", task=spec.id, commit=sha, moved=moved)
        return CheckResult("tag_v0", bool(sha), f"tagged v0 at {sha}" + (" (moved)" if moved else ""))

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
        return "\n".join(lines) + "\n"


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
    reopened = [t for t in dispatcher.template.downstream(args.task) if dispatcher.board.status(t) == "open"]
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
