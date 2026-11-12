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
    # v3 M2/W2.7: "package" tasks are not created at setup; they are expanded
    # after `expand_after` closes (one task per work package). v3 M4/W4.2 adds
    # "domain": expand after A1 by the blueprint's impl_packages (function sets
    # and directories) instead of the monolith-side work packages. Scope may use
    # {pkg} / {pkg_dir}; deps may use "<family>:*" for every child of a family,
    # and "<family>:shared" for its shared child.
    split: str = "none"              # none | package | domain
    expand_after: tuple[str, ...] = ()
    split_shared: bool = False       # create "<id>:shared" next to the package children
    shared_scope: tuple[str, ...] = ()  # scope of that shared child (falls back to scope)
    shared_check: tuple[str, ...] = ()  # checks of that shared child (falls back to check)
    # Dynamic children carry package/endpoints/functions; checks read them from
    # to_json(). Unknown template keys are preserved here too.
    extra: dict[str, Any] = field(default_factory=dict, compare=False, hash=False, repr=False)

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id, "runner": self.runner, "role": self.role, "action": self.action, "line": self.line,
            "deps": list(self.deps), "scope": list(self.scope), "check": list(self.check),
            "on_stale": self.on_stale, "max_turns": self.max_turns, "timeout_min": self.timeout_min,
            "mode": self.mode, "deny_read": list(self.deny_read), "start": self.start,
            "split": self.split, "expand_after": list(self.expand_after), "split_shared": self.split_shared,
            "shared_scope": list(self.shared_scope), "shared_check": list(self.shared_check),
            **self.extra,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> TaskSpec:
        known = {"id", "runner", "role", "action", "line", "deps", "scope", "check", "on_stale",
                 "max_turns", "timeout_min", "mode", "deny_read", "start", "split", "expand_after",
                 "split_shared", "shared_scope", "shared_check", "extra"}
        return cls(
            id=data["id"], runner=data.get("runner", "agent"), role=data.get("role"), action=data.get("action"),
            line=data.get("line"), deps=tuple(data.get("deps") or ()), scope=tuple(data.get("scope") or ()),
            check=tuple(data.get("check") or ()), on_stale=data.get("on_stale", "recheck"),
            max_turns=int(data.get("max_turns", 200)), timeout_min=float(data.get("timeout_min", 120)),
            mode=data.get("mode"), deny_read=tuple(data.get("deny_read") or ()), start=data.get("start", "spawn"),
            split=data.get("split", "none"), expand_after=tuple(data.get("expand_after") or ()),
            split_shared=bool(data.get("split_shared", False)),
            shared_scope=tuple(data.get("shared_scope") or ()),
            shared_check=tuple(data.get("shared_check") or ()),
            extra={k: v for k, v in data.items() if k not in known},
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
                if dep.endswith(":*") or dep.endswith(":shared"):
                    continue  # a split family, not a task id
                if dep not in found:
                    found.add(dep)
                    frontier.append(dep)
        return [task.id for task in self.tasks if task.id in found]


def load_template(name: str = "v1", path: Path | None = None) -> Template:
    source = path or TEMPLATES_DIR / f"{name}.yaml"
    data = yaml.safe_load(source.read_text(encoding="utf-8"))
    tasks: list[TaskSpec] = []
    known: set[str] = set()
    for raw in data["tasks"]:
        spec = TaskSpec.from_json(raw)
        if spec.id in known:
            raise ValueError(f"{source}: duplicate task id {spec.id}")
        if spec.split not in ("none", "package", "domain"):
            raise ValueError(f"{source}: {spec.id} has unknown split {spec.split!r}")
        if spec.runner == "agent" and not spec.role:
            raise ValueError(f"{source}: agent task {spec.id} has no role")
        if spec.runner == "dispatcher" and not spec.action:
            raise ValueError(f"{source}: dispatcher task {spec.id} has no action")
        known.add(spec.id)
        tasks.append(spec)
    # Dependencies may name a static task or a split family ("S0:*"). A split
    # task's own id is never a task on the board, so it must be referenced as
    # a family.
    splits = {spec.id: spec for spec in tasks}
    for spec in tasks:
        for dep in spec.deps:
            if dep.endswith(":*"):
                family = dep[:-2]
                if family not in splits or splits[family].split not in ("package", "domain"):
                    raise ValueError(f"{source}: {spec.id} depends on {dep}, but {family} is not a split task")
            elif dep.endswith(":shared") and dep[:-7] in splits and splits[dep[:-7]].split in ("package", "domain"):
                continue  # the shared child of a split family, created at expansion
            elif dep not in known:
                raise ValueError(f"{source}: {spec.id} depends on {dep}, which does not exist")
            elif splits[dep].split in ("package", "domain"):
                raise ValueError(f"{source}: {spec.id} depends on split task {dep} directly; use {dep}:*")
        for trigger in spec.expand_after:
            if trigger not in known:
                raise ValueError(f"{source}: {spec.id} expands after unknown task {trigger}")
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


def _literal_prefix(pattern: str) -> str:
    p = pattern.replace("\\", "/")
    for i, ch in enumerate(p):
        if ch in "*?":
            return p[:i].rstrip("/")
    return p.rstrip("/")


def scopes_conflict(a: tuple[str, ...] | list[str], b: tuple[str, ...] | list[str]) -> bool:
    """Conservative overlap test for two scopes (v3 M2/W2.8).

    Two patterns can name the same file when one literal prefix contains the
    other; a pattern that starts with a wildcard can name anything. This is
    used to keep two workers from editing the same file at once; a false
    positive only serializes them.
    """
    for pa in a:
        for pb in b:
            x, y = _literal_prefix(pa), _literal_prefix(pb)
            if not x or not y:
                return True
            if x == y or x.startswith(y + "/") or y.startswith(x + "/"):
                return True
    return False
