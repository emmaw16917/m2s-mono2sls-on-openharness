"""P0 spec extraction: adds per-endpoint spec fields to analysis_report.json.

Runs after M2S's static analyzer (see wrap_m2s.analyze) and adds, for every
Express entry point:

    handler_location   {file, start_line, end_line}
    auth               endpoint-level classification from the middleware chain
    request            path_params / query / body / headers fields
    responses          status codes + body shape, one item per res.* call site
    data_access        DynamoDB operations (op, table, index, repeated)
    business_rules     [] -- filled by the m2s-spec-annotator agent (LLM)
    spec_status        which parts came from the script / the annotator

plus `dynamodb_info.table_schemas` (key schema + GSIs parsed from the
createTable calls in schema files) and a top-level `spec_meta`.

Every extracted item carries `source` (a list of {file, line, symbol}) and
`origin` ("script" here, "llm" for annotator items), so check_provenance can
mechanically confirm the cited line really mentions the cited symbol.

This is deliberately a pattern extractor, not a JS parser: anything it cannot
recognize is recorded as "unknown" (never guessed) and left for the annotator.
Only JS/TS (Express) is handled; Python entry points get
spec_status.script = "unsupported" and are left entirely to the annotator.
See PIPELINE_REDESIGN.md §2.1.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

SPEC_SCHEMA_VERSION = 1

_JS_EXTS = {".js", ".ts", ".jsx", ".tsx"}
_IDENT = r"[A-Za-z_$][\w$]*"

# Middleware names that gate a route behind authentication. Matched
# case-insensitively as substrings; "optional" wins over these.
_AUTH_MW_RE = re.compile(r"auth|token|jwt|login|session|protect|guard|require|verify", re.I)
_OPTIONAL_MW_RE = re.compile(r"optional", re.I)

_DDB_OPS = (
    "get|put|query|scan|update|delete|batchGet|batchWrite|transactGet|transactWrite|"
    "getItem|putItem|updateItem|deleteItem|batchGetItem|batchWriteItem|transactGetItems|transactWriteItems"
)
# Receiver must look like a DynamoDB client so router.get / map.get are not matched.
_DDB_CALL_RE = re.compile(rf"\b({_IDENT})\s*\.\s*({_DDB_OPS})\s*\(")
_DDB_RECEIVER_RE = re.compile(r"doc|dynamo|ddb|client|table", re.I)


# ---------------------------------------------------------------------------
# Low-level JS text helpers
# ---------------------------------------------------------------------------

def _match_close(text: str, open_idx: int) -> int:
    """Index of the bracket closing text[open_idx] (one of ({[), skipping
    string literals and comments. Returns -1 if unbalanced."""
    pairs = {"(": ")", "[": "]", "{": "}"}
    stack: list[str] = []
    i, n = open_idx, len(text)
    while i < n:
        c = text[i]
        if c in "'\"`":
            j = i + 1
            while j < n and text[j] != c:
                j += 2 if text[j] == "\\" else 1
            i = j + 1
            continue
        if text.startswith("//", i):
            nl = text.find("\n", i)
            i = n if nl == -1 else nl
            continue
        if text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end == -1 else end + 2
            continue
        if c in pairs:
            stack.append(pairs[c])
        elif c in ")]}":
            if not stack or stack.pop() != c:
                return -1
            if not stack:
                return i
        i += 1
    return -1


def _split_top_level(args: str) -> list[tuple[str, int]]:
    """Split a call's argument text on top-level commas. Returns
    (arg_text_stripped, offset_of_arg_start_within_args)."""
    out: list[tuple[str, int]] = []
    depth, start, i, n = 0, 0, 0, len(args)
    while i < n:
        c = args[i]
        if c in "'\"`":
            j = i + 1
            while j < n and args[j] != c:
                j += 2 if args[j] == "\\" else 1
            i = j + 1
            continue
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif c == "," and depth == 0:
            seg = args[start:i]
            out.append((seg.strip(), start + len(seg) - len(seg.lstrip())))
            start = i + 1
        i += 1
    seg = args[start:]
    if seg.strip():
        out.append((seg.strip(), start + len(seg) - len(seg.lstrip())))
    return out


def _line_of(text: str, idx: int, base_line: int = 1) -> int:
    return base_line + text.count("\n", 0, idx)


def _object_keys(literal: str) -> list[str]:
    """Top-level keys of a JS object literal ("{ a: 1, b, ...c }")."""
    body = literal.strip()
    if not (body.startswith("{") and body.endswith("}")):
        return []
    keys: list[str] = []
    for part, _ in _split_top_level(body[1:-1]):
        if not part or part.startswith("..."):
            continue
        m = re.match(rf"^(?:['\"]([^'\"]+)['\"]|({_IDENT}))\s*(?::|$|\()", part)
        if m:
            keys.append(m.group(1) or m.group(2))
    return keys


def _src(file: str, line: int, symbol: str) -> dict[str, Any]:
    return {"file": file, "line": line, "symbol": symbol}


def _dedupe(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen, out = set(), []
    for s in sources:
        key = (s["file"], s["line"], s["symbol"])
        if key not in seen:
            seen.add(key)
            out.append(s)
    return out


# ---------------------------------------------------------------------------
# Project-level indexes
# ---------------------------------------------------------------------------

class _Project:
    """Sources of every JS file, keyed by app-prefixed rel path
    ("bookstore/routes/cart.js", matching analysis_report's own keys)."""

    def __init__(self, monolith_root: Path):
        self.root = monolith_root
        self.app = monolith_root.name
        self.sources: dict[str, str] = {}
        for path in sorted(monolith_root.rglob("*")):
            if path.suffix.lower() not in _JS_EXTS or not path.is_file():
                continue
            rel_parts = path.relative_to(monolith_root).parts
            if any(p in {"node_modules", ".git", "dist", "build", "coverage"} or p.startswith(".") for p in rel_parts):
                continue
            rel = f"{self.app}/{path.relative_to(monolith_root).as_posix()}"
            self.sources[rel] = path.read_text(encoding="utf-8", errors="ignore")
        self.config_tables = self._index_config_tables()

    def resolve_require(self, from_rel: str, spec: str) -> str | None:
        if not spec.startswith("."):
            return None
        base = (self.root.parent / from_rel).parent / spec
        for cand in [base] + [base.with_suffix(e) for e in (".js", ".ts")] + [base / "index.js", base / "index.ts"]:
            try:
                cand = cand.resolve()
            except OSError:
                continue
            if cand.is_file():
                try:
                    return f"{self.app}/{cand.relative_to(self.root.resolve()).as_posix()}"
                except ValueError:
                    return None
        return None

    def _index_config_tables(self) -> dict[str, dict[str, Any]]:
        """configKey -> {config_key, env_var, default_name, source} for
        `key: ENV_CONST` properties whose ENV_CONST is
        `process.env.X || 'default'`."""
        env_consts: dict[str, tuple[str, str | None, str, int]] = {}
        for rel, src in self.sources.items():
            for m in re.finditer(
                rf"(?:const|let|var)\s+({_IDENT})\s*=\s*process\.env\.({_IDENT})(?:\s*\|\|\s*['\"]([^'\"]*)['\"])?",
                src,
            ):
                env_consts[m.group(1)] = (m.group(2), m.group(3), rel, _line_of(src, m.start()))
        out: dict[str, dict[str, Any]] = {}
        for rel, src in self.sources.items():
            for m in re.finditer(rf"\b({_IDENT})\s*:\s*({_IDENT})\s*[,}}\n]", src):
                key, const = m.group(1), m.group(2)
                if const not in env_consts or "table" not in key.lower():
                    continue
                env_var, default, def_rel, def_line = env_consts[const]
                out[key] = {
                    "config_key": key,
                    "env_var": env_var,
                    "default_name": default,
                    "source": [_src(rel, _line_of(src, m.start()), key), _src(def_rel, def_line, env_var)],
                }
        # Inline `key: process.env.X || 'default'` in a config object.
        for rel, src in self.sources.items():
            for m in re.finditer(
                rf"\b({_IDENT})\s*:\s*process\.env\.({_IDENT})\s*\|\|\s*['\"]([^'\"]*)['\"]", src
            ):
                if "table" in m.group(1).lower() and m.group(1) not in out:
                    line = _line_of(src, m.start())
                    out[m.group(1)] = {
                        "config_key": m.group(1),
                        "env_var": m.group(2),
                        "default_name": m.group(3),
                        "source": [_src(rel, line, m.group(1))],
                    }
        return out

    def resolve_table(self, rel: str, expr: str) -> dict[str, Any]:
        """Resolve a TableName expression to its config key / env var."""
        expr = expr.strip()
        lit = re.fullmatch(r"['\"]([^'\"]+)['\"]", expr)
        if lit:
            return {"expr": expr, "config_key": None, "env_var": None, "default_name": lit.group(1)}
        src = self.sources.get(rel, "")
        if re.fullmatch(_IDENT, expr):
            # const booksTable = dynamodbConfig.booksTable;
            m = re.search(rf"(?:const|let|var)\s+{re.escape(expr)}\s*=\s*([\w$.]+)\s*;?", src)
            if m:
                expr_key = m.group(1).rsplit(".", 1)[-1]
            else:
                expr_key = expr
        else:
            expr_key = expr.rsplit(".", 1)[-1]
        info = self.config_tables.get(expr_key)
        if info:
            return {"expr": expr, "config_key": info["config_key"], "env_var": info["env_var"],
                    "default_name": info["default_name"]}
        return {"expr": expr, "config_key": "unknown", "env_var": "unknown", "default_name": "unknown"}


# ---------------------------------------------------------------------------
# Routes, mounts, middleware
# ---------------------------------------------------------------------------

_ROUTE_RE = re.compile(r"\b(?:router|app)\s*\.\s*(get|post|put|delete|patch)\s*\(\s*(['\"])([^'\"]+)\2", re.I)
_USE_RE = re.compile(r"\b(?:router|app)\s*\.\s*use\s*\(")


def _requires(src: str) -> dict[str, str]:
    """local name -> require spec, for both `const x = require()` and
    `const { a, b: c } = require()` forms."""
    out: dict[str, str] = {}
    for m in re.finditer(rf"(?:const|let|var)\s+({_IDENT})\s*=\s*require\(\s*['\"]([^'\"]+)['\"]\s*\)", src):
        out[m.group(1)] = m.group(2)
    for m in re.finditer(r"(?:const|let|var)\s*\{([^}]+)\}\s*=\s*require\(\s*['\"]([^'\"]+)['\"]\s*\)", src):
        for part in m.group(1).split(","):
            name = part.split(":")[-1].strip()
            if re.fullmatch(_IDENT, name):
                out[name] = m.group(2)
    return out


def _mounts(project: _Project) -> dict[str, list[dict[str, Any]]]:
    """router file -> [{prefix, middleware:[{name, source}], source}] from
    `app.use('/prefix', mw..., routerVar)`."""
    out: dict[str, list[dict[str, Any]]] = {}
    for rel, src in project.sources.items():
        reqs = _requires(src)
        for m in _USE_RE.finditer(src):
            open_idx = m.end() - 1
            close = _match_close(src, open_idx)
            if close == -1:
                continue
            args = _split_top_level(src[open_idx + 1:close])
            if len(args) < 2 or not re.fullmatch(r"['\"][^'\"]*['\"]", args[0][0]):
                continue
            router_var = args[-1][0]
            if router_var not in reqs:
                continue
            target = project.resolve_require(rel, reqs[router_var])
            if target is None:
                continue
            line = _line_of(src, m.start())
            mws = []
            for text, off in args[1:-1]:
                mws.append({"name": text, "source": _src(rel, _line_of(src, open_idx + 1 + off), text)})
            out.setdefault(target, []).append(
                {"prefix": args[0][0][1:-1], "middleware": mws, "source": _src(rel, line, router_var)}
            )
    return out


def _error_handler(project: _Project) -> dict[str, Any] | None:
    """The app-level `app.use((err, req, res, next) => ...)` handler's
    response: status expression, body keys, source."""
    for rel, src in project.sources.items():
        for m in re.finditer(r"\bapp\s*\.\s*use\s*\(\s*(?:async\s*)?(?:function\s*\w*\s*)?\(\s*err\b", src):
            open_idx = src.find("(", m.start())
            close = _match_close(src, open_idx)
            if close == -1:
                continue
            body = src[m.start():close]
            base = _line_of(src, m.start())
            r = re.search(r"res\s*\.\s*status\s*\(([^)]*\|\|\s*(\d{3})[^)]*)\)\s*\.\s*json\s*\(", body)
            if not r:
                continue
            json_open = m.start() + r.end() - 1
            json_close = _match_close(src, json_open)
            keys = _object_keys(src[json_open + 1:json_close]) if json_close != -1 else []
            return {
                "status": int(r.group(2)),
                "status_expr": r.group(1).strip(),
                "fields": keys,
                "source": _src(rel, _line_of(body, r.start(), base), r.group(2)),
            }
    return None


def _classify_auth(middleware: list[dict[str, Any]]) -> str:
    kinds = set()
    for mw in middleware:
        name = mw["name"]
        if _OPTIONAL_MW_RE.search(name):
            kinds.add("optional")
        elif _AUTH_MW_RE.search(name):
            kinds.add("required")
        else:
            kinds.add("other")
    if "required" in kinds:
        return "required"
    if "optional" in kinds:
        return "optional"
    if "other" in kinds:
        return "unknown"
    return "none"


# ---------------------------------------------------------------------------
# Per-handler extraction
# ---------------------------------------------------------------------------

class _Handler:
    def __init__(self, rel: str, src: str, start_idx: int, end_idx: int):
        self.rel = rel
        self.src = src
        self.start = start_idx
        self.end = end_idx
        self.text = src[start_idx:end_idx + 1]
        self.base_line = _line_of(src, start_idx)

    def line(self, local_idx: int) -> int:
        return _line_of(self.text, local_idx, self.base_line)

    def regions(self, pattern: str) -> list[tuple[int, int]]:
        """(start, end) local index ranges of bracket bodies following each
        match of `pattern` (pattern must end right before an opening bracket)."""
        out = []
        for m in re.finditer(pattern, self.text):
            open_idx = m.end()
            while open_idx < len(self.text) and self.text[open_idx] not in "({":
                open_idx += 1
            if open_idx >= len(self.text):
                continue
            close = _match_close(self.text, open_idx)
            if close == -1:
                continue
            # for (...) { body } -- extend over the loop body block too.
            if self.text[open_idx] == "(" and m.group(0).lstrip().startswith("for"):
                brace = self.text.find("{", close)
                if brace != -1:
                    body_close = _match_close(self.text, brace)
                    if body_close != -1:
                        close = body_close
            out.append((m.start(), close))
        return out


def _extract_request(h: _Handler, path: str, route_line: int) -> dict[str, list[dict[str, Any]]]:
    fields: dict[str, dict[str, dict[str, Any]]] = {"path_params": {}, "query": {}, "body": {}, "headers": {}}
    loc_map = {"params": "path_params", "query": "query", "body": "body", "headers": "headers"}

    for pm in re.finditer(r":(\w+)", path):
        fields["path_params"][pm.group(1)] = {
            "name": pm.group(1), "type": "string", "required": True,
            "source": [_src(h.rel, route_line, pm.group(1))], "origin": "script",
        }

    def add(loc: str, name: str, idx: int, default: bool = False) -> None:
        bucket = fields[loc_map[loc]]
        if name in bucket:
            return
        item = {"name": name, "type": "unknown", "required": "unknown",
                "source": [_src(h.rel, h.line(idx), name)], "origin": "script"}
        if default:
            item["required"] = False
        bucket[name] = item

    for m in re.finditer(r"(?:const|let|var)\s*\{([^}]*)\}\s*=\s*req\s*\.\s*(body|query|params)\b", h.text):
        for part, _ in _split_top_level(m.group(1)):
            key = part.split(":")[0].split("=")[0].strip()
            if re.fullmatch(_IDENT, key):
                add(m.group(2), key, m.start(), default="=" in part)
    for m in re.finditer(rf"req\s*\.\s*(body|query|params|headers)\s*\.\s*({_IDENT})", h.text):
        add(m.group(1), m.group(2), m.start())
    for m in re.finditer(r"req\s*\.\s*(body|query|params|headers)\s*\[\s*['\"]([^'\"]+)['\"]\s*\]", h.text):
        add(m.group(1), m.group(2), m.start())

    # Local variable name -> request field item, for `const x = req.query.x`
    # aliases and destructured names (the common case: same name).
    by_name: dict[str, dict[str, Any]] = {}
    for loc in ("body", "query", "headers"):
        by_name.update(fields[loc])
    for m in re.finditer(rf"(?:const|let|var)\s+({_IDENT})\s*=\s*req\s*\.\s*(body|query|headers)\s*\.\s*({_IDENT})", h.text):
        item = fields[loc_map[m.group(2)]].get(m.group(3))
        if item:
            by_name[m.group(1)] = item

    # if (...) guards: `!x` inside a condition whose branch answers 4xx makes x
    # required; a bare `if (x)` whose branch does not answer 4xx makes x optional.
    for m in re.finditer(r"\bif\s*\(", h.text):
        open_idx = m.end() - 1
        close = _match_close(h.text, open_idx)
        if close == -1:
            continue
        cond = h.text[open_idx + 1:close]
        after = h.text[close + 1:]
        brace = re.match(r"\s*\{", after)
        if brace:
            b_open = close + 1 + brace.end() - 1
            b_close = _match_close(h.text, b_open)
            branch = h.text[b_open:b_close] if b_close != -1 else ""
        else:
            branch = after.split(";", 1)[0]
        rejects = re.search(r"status\s*\(\s*4\d\d\s*\)", branch) is not None
        cond_line = h.line(m.start())
        for name, item in by_name.items():
            if rejects and re.search(rf"!\s*{re.escape(name)}\b(?!\s*\.)", cond):
                if item["required"] is not True:
                    item["required"] = True
                    item["source"].append(_src(h.rel, cond_line, name))
            elif not rejects and re.fullmatch(rf"\s*{re.escape(name)}\s*", cond):
                if item["required"] == "unknown":
                    item["required"] = False
                    item["source"].append(_src(h.rel, cond_line, name))
            if re.search(rf"Array\s*\.\s*isArray\s*\(\s*{re.escape(name)}\s*\)", cond) and item["type"] == "unknown":
                item["type"] = "array"
                item["source"].append(_src(h.rel, cond_line, name))
            tm = re.search(rf"typeof\s+{re.escape(name)}\s*[!=]==?\s*['\"](\w+)['\"]", cond)
            if tm and item["type"] == "unknown":
                item["type"] = tm.group(1)
                item["source"].append(_src(h.rel, cond_line, name))

    for bucket in fields.values():
        for item in bucket.values():
            item["source"] = _dedupe(item["source"])
    return {k: list(v.values()) for k, v in fields.items()}


def _extract_responses(h: _Handler, error_handler: dict[str, Any] | None) -> list[dict[str, Any]]:
    catch_regions = h.regions(r"\bcatch\s*\(\s*\w*\s*\)\s*")
    out: list[dict[str, Any]] = []
    pattern = re.compile(
        r"\bres\s*\.\s*(?:status\s*\(\s*(\d{3})\s*\)\s*\.\s*)?(json|send)\s*\(|\bres\s*\.\s*sendStatus\s*\(\s*(\d{3})\s*\)"
    )
    for m in pattern.finditer(h.text):
        if m.group(3):
            status, body = int(m.group(3)), {"fields": None, "expression": None}
            symbol = m.group(3)
        else:
            status = int(m.group(1)) if m.group(1) else 200
            symbol = m.group(1) or f"res.{m.group(2)}"
            open_idx = m.end() - 1
            close = _match_close(h.text, open_idx)
            arg = h.text[open_idx + 1:close].strip() if close != -1 else ""
            if arg.startswith("{"):
                body = {"fields": _object_keys(arg), "expression": None}
            else:
                body = {"fields": None, "expression": arg or None}
        item = {
            "status": status,
            "kind": "success" if 200 <= status < 300 else "error",
            "body": body,
            "source": [_src(h.rel, h.line(m.start()), symbol)],
            "origin": "script",
        }
        if any(s <= m.start() <= e for s, e in catch_regions):
            item["in_catch"] = True
        out.append(item)

    nm = re.search(r"\bnext\s*\(\s*(\w+)\s*\)", h.text)
    if nm and error_handler:
        out.append({
            "status": error_handler["status"],
            "kind": "error",
            "body": {"fields": error_handler["fields"], "expression": None},
            "via": f"error middleware (status = {error_handler['status_expr']})",
            "source": [_src(h.rel, h.line(nm.start()), "next"), error_handler["source"]],
            "origin": "script",
        })
    return out


def _function_ranges(src: str) -> dict[str, tuple[int, int]]:
    """name -> (start_idx, end_idx) of top-level-ish function definitions."""
    out: dict[str, tuple[int, int]] = {}
    pat = re.compile(
        rf"(?:async\s+)?function\s+({_IDENT})\s*\(|(?:const|let|var)\s+({_IDENT})\s*=\s*(?:async\s*)?(?:function\b[^(]*\(|\([^)]*\)\s*=>|{_IDENT}\s*=>)"
    )
    for m in pat.finditer(src):
        name = m.group(1) or m.group(2)
        brace = src.find("{", m.end())
        if brace == -1:
            continue
        close = _match_close(src, brace)
        if close != -1:
            out.setdefault(name, (m.start(), close))
    return out


def _extract_data_access(project: _Project, h: _Handler, via: str | None = None,
                         depth: int = 0, seen: set | None = None) -> list[dict[str, Any]]:
    seen = seen if seen is not None else set()
    repeat_regions = h.regions(r"\.\s*(?:map|forEach|flatMap)\s*(?=\()") + h.regions(r"\bfor\s*(?=\()")
    out: list[dict[str, Any]] = []
    for m in _DDB_CALL_RE.finditer(h.text):
        receiver, op = m.group(1), m.group(2)
        if not _DDB_RECEIVER_RE.search(receiver):
            continue
        open_idx = m.end() - 1
        close = _match_close(h.text, open_idx)
        arg = h.text[open_idx + 1:close].strip() if close != -1 else ""
        obj = None
        if arg.startswith("{"):
            obj = arg
        elif re.fullmatch(_IDENT, arg):
            # nearest preceding `const arg = { ... }` in this handler
            defs = list(re.finditer(rf"(?:const|let|var)\s+{re.escape(arg)}\s*=\s*\{{", h.text[:m.start()]))
            if defs:
                o_open = defs[-1].end() - 1
                o_close = _match_close(h.text, o_open)
                if o_close != -1:
                    obj = h.text[o_open:o_close + 1]
        table = {"expr": "unknown", "config_key": "unknown", "env_var": "unknown", "default_name": "unknown"}
        index = None
        if obj:
            tm = re.search(r"\bTableName\s*:\s*([^,\n}]+)", obj)
            if tm:
                table = project.resolve_table(h.rel, tm.group(1))
            im = re.search(r"\bIndexName\s*:\s*['\"]([^'\"]+)['\"]", obj)
            if im:
                index = im.group(1)
        item = {
            "operation": op,
            "table": table,
            "index": index,
            "repeated": any(s <= m.start() <= e for s, e in repeat_regions),
            "source": [_src(h.rel, h.line(m.start()), f"{receiver}.{op}")],
            "origin": "script",
        }
        if via:
            item["via"] = via
        out.append(item)

    if depth >= 2:
        return out
    # Follow calls into other project-local modules (and, inside those, into
    # same-file helpers) so e.g. POST /orders -> updateBestSellers ->
    # updateBestSeller -> docClient.update shows up with its call path.
    reqs = {k: v for k, v in _requires(h.src).items() if v.startswith(".")}
    local_funcs = _function_ranges(h.src) if depth > 0 else {}
    for m in re.finditer(rf"(?<![.\w$])({_IDENT})\s*\(", h.text):
        name = m.group(1)
        target_rel, target_name = None, None
        if name in reqs:
            target_rel = project.resolve_require(h.rel, reqs[name])
            target_name = name
        elif name in local_funcs:
            target_rel, target_name = h.rel, name
        if not target_rel or (target_rel, target_name) in seen:
            continue
        if _DDB_RECEIVER_RE.search(name):
            continue
        tsrc = project.sources.get(target_rel)
        rng = _function_ranges(tsrc).get(target_name) if tsrc else None
        if not rng:
            continue
        seen.add((target_rel, target_name))
        callee = _Handler(target_rel, tsrc, rng[0], rng[1])
        chain = f"{via} -> {target_name}" if via else target_name
        inner = _extract_data_access(project, callee, chain, depth + 1, seen)
        if any(s <= m.start() <= e for s, e in repeat_regions):
            for it in inner:
                it["repeated"] = True
        for it in inner:
            it["source"].insert(0, _src(h.rel, h.line(m.start()), name))
        out.extend(inner)
    return out


# ---------------------------------------------------------------------------
# DynamoDB table schemas (createTable params)
# ---------------------------------------------------------------------------

def _js_literal_to_json(literal: str) -> Any:
    """Best-effort conversion of a simple JS object literal (as found in
    createTable params) to Python. Bare identifiers become "@ident"."""
    s = re.sub(r"//[^\n]*", "", literal)
    s = re.sub(r"'([^'\\]*)'", lambda m: json.dumps(m.group(1)), s)
    s = re.sub(rf"([{{,]\s*)({_IDENT})\s*:", r'\1"\2":', s)
    s = re.sub(rf"(:\s*)([A-Za-z_$][\w$.]*)(\s*[,}}\]])",
               lambda m: m.group(1) + (m.group(2) if m.group(2) in ("true", "false", "null") else json.dumps("@" + m.group(2))) + m.group(3), s)
    s = re.sub(r",(\s*[}\]])", r"\1", s)
    return json.loads(s)


def _extract_table_schemas(project: _Project, schema_files: list[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for rel in schema_files:
        src = project.sources.get(rel)
        if not src:
            continue
        for m in re.finditer(r"\bKeySchema\s*:", src):
            # Walk back to the enclosing object literal's opening brace.
            depth, i = 0, m.start()
            while i > 0:
                i -= 1
                if src[i] == "}":
                    depth += 1
                elif src[i] == "{":
                    if depth == 0:
                        break
                    depth -= 1
            close = _match_close(src, i)
            if close == -1:
                continue
            literal = src[i:close + 1]
            if "TableName" not in literal:
                continue  # a GSI's KeySchema; handled via its parent
            try:
                obj = _js_literal_to_json(literal)
            except (json.JSONDecodeError, ValueError):
                continue
            name_val = obj.get("TableName", "")
            name_expr = name_val[1:] if isinstance(name_val, str) and name_val.startswith("@") else json.dumps(name_val)
            attr_types = {a.get("AttributeName"): a.get("AttributeType") for a in obj.get("AttributeDefinitions", [])}

            def keys(ks: list) -> dict[str, Any]:
                d: dict[str, Any] = {"partition_key": None, "sort_key": None}
                for k in ks:
                    slot = "partition_key" if k.get("KeyType") == "HASH" else "sort_key"
                    d[slot] = {"name": k.get("AttributeName"), "type": attr_types.get(k.get("AttributeName"), "unknown")}
                return d

            table_line = _line_of(src, i + literal.find("TableName"))
            key_line = _line_of(src, m.start())
            first_key = (obj.get("KeySchema") or [{}])[0].get("AttributeName", "KeySchema")
            item = {
                "table": project.resolve_table(rel, name_expr),
                **keys(obj.get("KeySchema", [])),
                "gsis": [],
                "source": [_src(rel, table_line, "TableName"), _src(rel, key_line + 1, first_key)],
                "origin": "script",
            }
            for g in obj.get("GlobalSecondaryIndexes", []) or []:
                g_line = _line_of(src, src.find(g.get("IndexName", "\0"), i))
                item["gsis"].append({"index_name": g.get("IndexName"), **keys(g.get("KeySchema", [])),
                                     "projection": (g.get("Projection") or {}).get("ProjectionType"),
                                     "source": [_src(rel, g_line, g.get("IndexName"))]})
            out.append(item)
    return out


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _empty_spec(ep: dict[str, Any], reason: str) -> None:
    ep.setdefault("handler_location", None)
    ep.setdefault("auth", None)
    ep.setdefault("request", {"path_params": [], "query": [], "body": [], "headers": []})
    ep.setdefault("responses", [])
    ep.setdefault("data_access", [])
    ep.setdefault("business_rules", [])
    ep["spec_status"] = {"script": reason, "annotated_fields": []}


def enrich_report(report: dict[str, Any], monolith_root: Path) -> dict[str, Any]:
    """Add spec fields to `report` in place (see module docstring). Returns
    a summary {entry_points, script_extracted, unsupported, unknown_items}."""
    project = _Project(monolith_root)
    mounts = _mounts(project)
    error_handler = _error_handler(project)

    # (file, METHOD, full path) -> route call location, computed the same way
    # the analyzer builds its prefixed paths.
    routes: dict[tuple[str, str, str], dict[str, Any]] = {}
    for rel, src in project.sources.items():
        mount_list = mounts.get(rel) or [{"prefix": "", "middleware": [], "source": None}]
        for m in _ROUTE_RE.finditer(src):
            open_idx = src.find("(", m.start())
            close = _match_close(src, open_idx)
            if close == -1:
                continue
            args = _split_top_level(src[open_idx + 1:close])
            handler_text, handler_off = args[-1]
            h_start = open_idx + 1 + handler_off
            route_line = _line_of(src, m.start())
            route_mw = [{"name": t, "source": _src(rel, _line_of(src, open_idx + 1 + o), t)} for t, o in args[1:-1]]
            for mount in mount_list:
                raw = m.group(3)
                prefix = mount["prefix"]
                full = prefix.rstrip("/") + "/" + raw.lstrip("/") if raw not in ("", "/") else (prefix or "/")
                routes[(rel, m.group(1).upper(), full)] = {
                    "handler": _Handler(rel, src, h_start, open_idx + 1 + handler_off + len(handler_text) - 1),
                    "raw_path": raw,
                    "route_line": route_line,
                    "middleware": mount["middleware"] + route_mw,
                    "mount_source": mount["source"],
                }

    unknown = 0
    extracted = unsupported = 0
    for ep in report.get("entry_points", []):
        key = (ep.get("file"), (ep.get("method") or "").upper(), ep.get("path"))
        route = routes.get(key)
        if route is None:
            _empty_spec(ep, "unsupported" if not str(ep.get("file", "")).endswith(tuple(_JS_EXTS)) else "not_found")
            unsupported += 1
            continue
        h: _Handler = route["handler"]
        mws = route["middleware"]
        auth_source = [mw["source"] for mw in mws] or [_src(h.rel, route["route_line"], route["raw_path"])]
        if route["mount_source"]:
            auth_source.append(route["mount_source"])
        ep["handler_location"] = {"file": h.rel, "start_line": h.line(0), "end_line": h.line(len(h.text) - 1)}
        ep["auth"] = {
            "classification": _classify_auth(mws),
            "middleware": [mw["name"] for mw in mws],
            "source": auth_source,
            "origin": "script",
        }
        ep["request"] = _extract_request(h, route["raw_path"], route["route_line"])
        ep["responses"] = _extract_responses(h, error_handler)
        ep["data_access"] = _extract_data_access(project, h)
        ep["business_rules"] = []
        ep["spec_status"] = {"script": "extracted", "annotated_fields": []}
        extracted += 1
        for loc in ep["request"].values():
            unknown += sum(1 for f in loc if f["type"] == "unknown") + sum(1 for f in loc if f["required"] == "unknown")
        unknown += sum(1 for r in ep["responses"] if r["body"]["fields"] is None)
        unknown += sum(1 for d in ep["data_access"] if d["table"]["config_key"] == "unknown")

    ddb = report.get("dynamodb_info")
    if isinstance(ddb, dict):
        ddb["table_schemas"] = _extract_table_schemas(project, ddb.get("schema_files", []))

    report["spec_meta"] = {
        "schema_version": SPEC_SCHEMA_VERSION,
        "monolith_root": str(monolith_root.resolve()),
    }
    return {
        "entry_points": len(report.get("entry_points", [])),
        "script_extracted": extracted,
        "unsupported": unsupported,
        "unknown_items": unknown,
    }
