"""Pipeline templates: which tasks exist, their dependencies, scope and checks."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"


@dataclass(frozen=True)
class TaskSpec:
    id: str
    runner: str = "agent"            # agent | dispatcher
    role: str | None = None          # agent definition name for runner=agent
    action: str | None = None        # dispatcher action for runner=dispatcher
    line: str | None = None          # impl | verify | None
    deps: tuple[str, ...] = ()
    scope: tuple[str, ...] = ()
    check: tuple[str, ...] = ()
    on_stale: str = "recheck"        # recheck | rerun
    max_turns: int = 200
    timeout_min: float = 120
    mode: str | None = None          # role mode for the prompt and the role doc (v2: J1, J3, ...)
    # Closed to the worker for reading: "monolith" (the monolith's source), or
    # workspace-relative globs such as "output/lambdas/**" (v2 V2P-06).
    deny_read: tuple[str, ...] = ()
    # "recheck": the first start runs the checks and spends a worker only if they
    # fail (a fact base copied from an earlier run; a J3 with nothing to classify).
    start: str = "spawn"

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id, "runner": self.runner, "role": self.role, "action": self.action, "line": self.line,
            "deps": list(self.deps), "scope": list(self.scope), "check": list(self.check),
            "on_stale": self.on_stale, "max_turns": self.max_turns, "timeout_min": self.timeout_min,
            "mode": self.mode, "deny_read": list(self.deny_read), "start": self.start,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> TaskSpec:
        return cls(
            id=data["id"], runner=data.get("runner", "agent"), role=data.get("role"), action=data.get("action"),
            line=data.get("line"), deps=tuple(data.get("deps") or ()), scope=tuple(data.get("scope") or ()),
            check=tuple(data.get("check") or ()), on_stale=data.get("on_stale", "recheck"),
            max_turns=int(data.get("max_turns", 200)), timeout_min=float(data.get("timeout_min", 120)),
            mode=data.get("mode"), deny_read=tuple(data.get("deny_read") or ()), start=data.get("start", "spawn"),
        )


@dataclass(frozen=True)
class Template:
    name: str
    tasks: tuple[TaskSpec, ...]
    max_attempts: int = 3
    max_rework_rounds: int = 2
    max_spec_issue_rounds: int = 2
    max_reconcile_rounds: int = 2  # v2 J3: fact-base <-> monolith reconciliation rounds
    max_strength_rounds: int = 2  # v2 J4/J5: white-box cases for the mutants the judge missed
    extra: dict[str, Any] = field(default_factory=dict)

    def get(self, task_id: str) -> TaskSpec:
        for task in self.tasks:
            if task.id == task_id:
                return task
        raise KeyError(task_id)

    def order(self, task_id: str) -> int:
        """Position in the template; the dispatcher starts ready tasks in this order."""
        for index, task in enumerate(self.tasks):
            if task.id == task_id:
                return index
        return len(self.tasks)

    def downstream(self, task_id: str) -> list[str]:
        """Every task that depends on task_id, directly or transitively, in template order."""
        found: set[str] = set()
        frontier = [task_id]
        while frontier:
            current = frontier.pop()
            for task in self.tasks:
                if current in task.deps and task.id not in found:
                    found.add(task.id)
                    frontier.append(task.id)
        return [task.id for task in self.tasks if task.id in found]

    def upstream(self, task_id: str) -> list[str]:
        """task_id and every task it depends on, directly or transitively, in template order."""
        found = {task_id}
        frontier = [task_id]
        while frontier:
            for dep in self.get(frontier.pop()).deps:
                if dep not in found:
                    found.add(dep)
                    frontier.append(dep)
        return [task.id for task in self.tasks if task.id in found]


def load_template(name: str = "v1", path: Path | None = None) -> Template:
    source = path or TEMPLATES_DIR / f"{name}.yaml"
    data = yaml.safe_load(source.read_text(encoding="utf-8"))
    tasks = []
    known: set[str] = set()
    for raw in data["tasks"]:
        spec = TaskSpec.from_json(raw)
        if spec.id in known:
            raise ValueError(f"{source}: duplicate task id {spec.id}")
        missing = [dep for dep in spec.deps if dep not in known]
        if missing:
            raise ValueError(f"{source}: {spec.id} depends on {missing}, which must be listed earlier")
        if spec.runner == "agent" and not spec.role:
            raise ValueError(f"{source}: agent task {spec.id} has no role")
        if spec.runner == "dispatcher" and not spec.action:
            raise ValueError(f"{source}: dispatcher task {spec.id} has no action")
        known.add(spec.id)
        tasks.append(spec)
    return Template(
        name=data.get("name", name),
        tasks=tuple(tasks),
        max_attempts=int(data.get("max_attempts", 3)),
        max_rework_rounds=int(data.get("max_rework_rounds", 2)),
        max_spec_issue_rounds=int(data.get("max_spec_issue_rounds", 2)),
        max_reconcile_rounds=int(data.get("max_reconcile_rounds", 2)),
        max_strength_rounds=int(data.get("max_strength_rounds", 2)),
    )


def glob_to_regex(pattern: str) -> re.Pattern[str]:
    """Workspace glob: "**" matches across directories, "*" within one."""
    out = []
    i = 0
    while i < len(pattern):
        if pattern.startswith("**", i):
            out.append(".*")
            i += 2
            if i < len(pattern) and pattern[i] == "/":
                i += 1
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("^" + "".join(out) + "$", re.IGNORECASE)


def in_scope(rel_path: str, patterns: tuple[str, ...] | list[str]) -> bool:
    rel = rel_path.replace("\\", "/")
    return any(glob_to_regex(p).match(rel) for p in patterns)
