"""Signals in the monolith's code that the gap scan needs (PIPELINE_V2_PLAN.md §4.11,
N4.1) and route-level extraction does not record: behaviour a long-running
process gets for free and a serverless target has to handle explicitly.

scan() returns report["runtime_signals"]:

    scans                  {kind: "scan", paginates: bool}   a DynamoDB Scan, and whether
                           LastEvaluatedKey / ExclusiveStartKey is handled near it
    process_state          module_variable | module_collection | global | global_statement |
                           cache | session    state that lives as long as the process
    time_and_order         now | timer | sort
    files_and_connections  fs_write | upload | websocket | streaming
    background             background        work started and not waited for (threads, tasks);
                           unawaited JS calls are in the analyzer's call_graph already
    self_issued_tokens     token_signing     the application signs its own tokens

Every item is {kind, source: [{file, line, symbol}], origin: "script"} like the other
script-extracted facts. JS/TS is matched by pattern, Python by its AST; a miss is left
for G2, which reads the code. Only new fields: nothing else in the report changes.
Helper scripts (scripts/, tests) are not the running application and are left out.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

import spec_extract
import spec_extract_py

CATEGORIES = ("scans", "process_state", "time_and_order", "files_and_connections", "background", "self_issued_tokens")
_NOT_THE_APP = {"scripts", "script", "tests", "test", "__tests__", "migrations", "seed", "seeds"}
PAGINATION_WINDOW = 40  # lines around a JS scan, within its statement, where handling pagination counts

_PAGINATES_RE = re.compile(r"LastEvaluatedKey|ExclusiveStartKey")
_JS_PATTERNS: list[tuple[str, str, re.Pattern[str]]] = [
    ("process_state", "module_variable", re.compile(r"^(?:let|var)\s+[A-Za-z_$][\w$]*(?!.*\brequire\()", re.M)),
    ("process_state", "module_collection",
     re.compile(r"^const\s+[A-Za-z_$][\w$]*\s*=\s*(?:\{\s*\}|\[\s*\]|new\s+(?:Map|Set|WeakMap)\b)", re.M)),
    ("process_state", "global", re.compile(r"\bglobal\.[A-Za-z_$][\w$]*\s*=[^=]")),
    ("process_state", "cache", re.compile(r"require\(\s*['\"](?:node-cache|lru-cache|memory-cache)['\"]\s*\)")),
    ("process_state", "session", re.compile(r"require\(\s*['\"](?:express-session|cookie-session)['\"]\s*\)|\breq\.session\b")),
    ("time_and_order", "now", re.compile(r"\bDate\.now\(\s*\)|\bnew\s+Date\(\s*\)")),
    ("time_and_order", "timer", re.compile(r"\bset(?:Timeout|Interval)\(|\bcron\.schedule\(|require\(\s*['\"](?:node-cron|agenda|bull)['\"]\s*\)")),
    ("time_and_order", "sort", re.compile(r"\.sort\(")),
    ("files_and_connections", "fs_write",
     re.compile(r"\bfs(?:\.promises)?\.(?:writeFile|appendFile|createWriteStream|mkdir|unlink|rename|rm)\w*\(")),
    ("files_and_connections", "upload", re.compile(r"require\(\s*['\"](?:multer|formidable|busboy)['\"]\s*\)")),
    ("files_and_connections", "websocket", re.compile(r"require\(\s*['\"](?:ws|socket\.io)['\"]\s*\)|\bnew\s+WebSocket\w*\(")),
    ("files_and_connections", "streaming", re.compile(r"text/event-stream|\bres\.write\(")),
    ("background", "background", re.compile(r"\bsetImmediate\(|\bprocess\.nextTick\(|\bnew\s+Worker\(")),
    ("self_issued_tokens", "token_signing", re.compile(r"\bjwt\.sign\(|\bjsonwebtoken\b\s*\)?\.sign\(")),
]
_JS_SCAN_RE = re.compile(r"\b[A-Za-z_$][\w$]*\s*\.\s*scan\s*\(|\bnew\s+ScanCommand\(", re.I)


def _is_app_file(rel: str) -> bool:
    return not any(p.lower() in _NOT_THE_APP for p in rel.split("/")[1:-1])


def _item(kind: str, rel: str, line: int, symbol: str, **extra: Any) -> dict[str, Any]:
    return {"kind": kind, **extra, "source": [{"file": rel, "line": int(line), "symbol": symbol.strip()[:120]}], "origin": "script"}


def _top_level_span(lines: list[str], index: int) -> tuple[int, int]:
    """[first, last) of the top-level statement around a line of formatted code, so that a
    paginated scan in the next route does not count for this one."""
    def starts(i: int) -> bool:
        return bool(lines[i]) and lines[i][0] not in " \t})]"

    first = index
    while first > 0 and not starts(first):
        first -= 1
    last = index + 1
    while last < len(lines) and not starts(last):
        last += 1
    return first, last


def _scan_js(root: Path, out: dict[str, list[dict[str, Any]]]) -> None:
    project = spec_extract._Project(root)
    for rel, src in project.sources.items():
        if not _is_app_file(rel):
            continue
        blank = spec_extract._blank_comments(src)
        lines = blank.split("\n")
        for m in _JS_SCAN_RE.finditer(blank):
            if not re.search(r"doc|dynamo|ddb|client|table|new\s+ScanCommand", m.group(0), re.I):
                continue
            line = spec_extract._line_of(blank, m.start())
            first, last = _top_level_span(lines, line - 1)
            near = "\n".join(lines[max(first, line - 1 - PAGINATION_WINDOW):min(last, line + PAGINATION_WINDOW)])
            out["scans"].append(_item("scan", rel, line, m.group(0), paginates=bool(_PAGINATES_RE.search(near))))
        for category, kind, pattern in _JS_PATTERNS:
            for m in pattern.finditer(blank):
                out[category].append(_item(kind, rel, spec_extract._line_of(blank, m.start()), m.group(0)))


def _dotted(node: ast.AST) -> str:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


_PY_EMPTY_CALLS = {"dict", "list", "set", "defaultdict", "OrderedDict", "deque", "Counter", "TTLCache", "LRUCache"}
_PY_NOW = {"datetime.now", "datetime.utcnow", "datetime.today", "date.today", "time.time",
           "datetime.datetime.now", "datetime.datetime.utcnow", "datetime.date.today"}
_PY_BACKGROUND = {"Thread", "threading.Thread", "Timer", "threading.Timer", "asyncio.create_task", "create_task",
                  "executor.submit", "ThreadPoolExecutor", "ProcessPoolExecutor"}
_PY_IMPORT_KIND = {"flask_socketio": ("files_and_connections", "websocket"), "websockets": ("files_and_connections", "websocket"),
                   "apscheduler": ("time_and_order", "timer"), "schedule": ("time_and_order", "timer"),
                   "sched": ("time_and_order", "timer"), "celery": ("background", "background"),
                   "cachetools": ("process_state", "cache"), "flask_caching": ("process_state", "cache"),
                   "flask_session": ("process_state", "session")}


def _empty_collection(node: ast.AST | None) -> bool:
    if isinstance(node, (ast.Dict, ast.List, ast.Set)):
        return not (getattr(node, "keys", None) or getattr(node, "elts", None))
    return isinstance(node, ast.Call) and _dotted(node.func).split(".")[-1] in _PY_EMPTY_CALLS


def _scan_py_tree(rel: str, tree: ast.Module, src: str, out: dict[str, list[dict[str, Any]]]) -> None:
    lines = src.splitlines()

    def text(node: ast.AST) -> str:
        return lines[node.lineno - 1] if 0 < node.lineno <= len(lines) else ""

    def add(category: str, kind: str, node: ast.AST, **extra: Any) -> None:
        out[category].append(_item(kind, rel, node.lineno, text(node), **extra))

    def state_targets(body: list[ast.stmt]) -> None:
        for stmt in body:
            if isinstance(stmt, (ast.Assign, ast.AnnAssign)) and _empty_collection(stmt.value):
                add("process_state", "module_collection", stmt)

    state_targets(tree.body)
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            state_targets(node.body)
    functions = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    for node in ast.walk(tree):
        if isinstance(node, ast.Global):
            add("process_state", "global_statement", node)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
            for name in names:
                hit = _PY_IMPORT_KIND.get(name.split(".")[0])
                if hit:
                    add(hit[0], hit[1], node)
            if isinstance(node, ast.ImportFrom) and node.module == "flask" and any(a.name == "session" for a in node.names):
                add("process_state", "session", node)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for deco in node.decorator_list:
                name = _dotted(deco.func if isinstance(deco, ast.Call) else deco).split(".")[-1]
                if name in ("lru_cache", "cache", "cached", "memoize"):
                    add("process_state", "cache", deco)
        elif isinstance(node, ast.Attribute) and _dotted(node) == "request.files":
            add("files_and_connections", "upload", node)
        elif isinstance(node, ast.Call):
            name = _dotted(node.func)
            short = name.split(".")[-1]
            if short == "scan" and isinstance(node.func, ast.Attribute):
                enclosing = next((f for f in functions if f.lineno <= node.lineno <= (f.end_lineno or f.lineno)), None)
                span = "\n".join(lines[enclosing.lineno - 1:enclosing.end_lineno]) if enclosing else text(node)
                add("scans", "scan", node, paginates=bool(_PAGINATES_RE.search(span)))
            elif name in _PY_NOW:
                add("time_and_order", "now", node)
            elif short == "sort" and isinstance(node.func, ast.Attribute) or name == "sorted":
                add("time_and_order", "sort", node)
            elif name in _PY_BACKGROUND or short in ("delay", "apply_async") or (short == "submit" and isinstance(node.func, ast.Attribute)):
                add("background", "background", node)
            elif name in ("jwt.encode", "create_access_token", "create_refresh_token"):
                add("self_issued_tokens", "token_signing", node)
            elif name == "open" and _opens_for_writing(node):
                add("files_and_connections", "fs_write", node)
            elif name in ("os.makedirs", "os.mkdir", "os.remove", "os.rename", "shutil.copy", "shutil.move", "shutil.rmtree"):
                add("files_and_connections", "fs_write", node)
            elif short == "stream_with_context":
                add("files_and_connections", "streaming", node)


def _opens_for_writing(call: ast.Call) -> bool:
    mode = call.args[1] if len(call.args) > 1 else next((k.value for k in call.keywords if k.arg == "mode"), None)
    return isinstance(mode, ast.Constant) and isinstance(mode.value, str) and bool(set(mode.value) & set("wax+"))


def scan(monolith_root: Path | str) -> dict[str, list[dict[str, Any]]]:
    root = Path(monolith_root)
    out: dict[str, list[dict[str, Any]]] = {c: [] for c in CATEGORIES}
    _scan_js(root, out)
    project = spec_extract_py.PyProject(root)
    for rel, tree in project.trees.items():
        if _is_app_file(rel):
            _scan_py_tree(rel, tree, project.sources[rel], out)
    return out
