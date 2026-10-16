"""The monolith's own data store when it is not DynamoDB (P0).

The pipeline's target is always DynamoDB. A MongoDB monolith therefore needs
its data model converted, and the conversion is only as good as the list of
ways the code reads and writes each collection: every access pattern must end
up as a GetItem / Query on the table or one of its GSIs (or an explicitly
justified Scan). detect() produces that list from the code, so S1 designs the
tables from it and gate DM1 can check every pattern is covered.

    report["source_data_store"] = {
      "kind": "mongodb", "driver": "mongodb" | "mongoose",
      "source": [{file, line, symbol}],               # the require site(s)
      "collections": [{"name", "source"}],
      "access_patterns": [{
         "id": "AP1", "collection": "booking" | null, "operation": "find" | "findOne" | "insert" | "update" |
                                   "replace" | "delete" | "count" | "aggregate" | "distinct",
         "filter_fields": [{"name", "match": "eq" | "range" | "in" | "other"}] | null,   # null = not determined
         "filter_raw": "<the filter as written>", "sort": "<raw>" | null,
         "via": "<wrapper method>" | null, "note": str | null,
         "source": [{file, line, symbol}], "origin": "script"}],
      "indexes": [{"collection", "keys": [field, ...], "source"}],   # ensureIndex / createIndex
      "unresolved": ["<what the script could not determine>"]}

Generic data-access wrappers (`dataaccess.findBy(collection, condition)` over
`collection.find(condition)`) are resolved one level: the wrapper's
definition says which operation runs, its call sites say which collection and
which filter. Anything not determined is null and listed in `unresolved` for
the spec annotator. Pattern matcher, not a parser (same approach as
spec_extract.py).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from spec_extract import _line_of, _match_close, _split_top_level

SKIP_DIRS = {"node_modules", ".git", "test", "tests", "dist", "build", "public", "coverage"}
_IDENT = r"[A-Za-z_$][\w$]*"

# driver method -> normalized operation
OPS = {
    "find": "find", "findOne": "findOne", "findById": "findOne",
    "insertOne": "insert", "insertMany": "insert", "insert": "insert", "save": "insert", "create": "insert",
    "updateOne": "update", "updateMany": "update", "update": "update", "findOneAndUpdate": "update",
    "findByIdAndUpdate": "update", "replaceOne": "replace", "findOneAndReplace": "replace",
    "deleteOne": "delete", "deleteMany": "delete", "remove": "delete", "findOneAndDelete": "delete",
    "findByIdAndDelete": "delete", "findOneAndRemove": "delete",
    "count": "count", "countDocuments": "count", "estimatedDocumentCount": "count",
    "aggregate": "aggregate", "distinct": "distinct",
}
_OP_RE = re.compile(rf"\b({_IDENT})\s*\.\s*({'|'.join(sorted(OPS, key=len, reverse=True))})\s*\(")
_CHAINED_RE = re.compile(rf"\.\s*collection\s*\(\s*([^()]*?)\s*\)\s*\.\s*({'|'.join(sorted(OPS, key=len, reverse=True))})\s*\(")
_FUNC_RES = [
    re.compile(rf"(?:module\.exports|module|exports|this)\s*\.\s*({_IDENT})\s*=\s*(?:async\s+)?function\s*(?:{_IDENT})?\s*\(([^)]*)\)"),
    re.compile(rf"(?:async\s+)?function\s+({_IDENT})\s*\(([^)]*)\)"),
    re.compile(rf"(?:const|let|var)\s+({_IDENT})\s*=\s*(?:async\s*)?(?:function\s*(?:{_IDENT})?\s*)?\(([^)]*)\)\s*(?:=>)?"),
    # implicit global: countItems = function (dbName, callback) {
    re.compile(rf"^\s*({_IDENT})\s*=\s*(?:async\s+)?function\s*(?:{_IDENT})?\s*\(([^)]*)\)", re.M),
    re.compile(rf"^\s*({_IDENT})\s*:\s*(?:async\s+)?function\s*(?:{_IDENT})?\s*\(([^)]*)\)", re.M),
]


_MONGO_REQUIRE_RE = re.compile(r"require\(\s*['\"](mongodb|mongoose)['\"]\s*\)|from\s+['\"](mongodb|mongoose)['\"]")


def _blank_comments(src: str) -> str:
    """Comments replaced by spaces (newlines kept), so offsets and line
    numbers stay valid and commented-out code is never matched."""
    out, i, n = list(src), 0, len(src)
    while i < n:
        c = src[i]
        if c in "'\"`":
            j = i + 1
            while j < n and src[j] != c:
                j += 2 if src[j] == "\\" else 1
            i = j + 1
        elif src.startswith("//", i):
            j = src.find("\n", i)
            j = n if j == -1 else j
            out[i:j] = " " * (j - i)
            i = j
        elif src.startswith("/*", i):
            j = src.find("*/", i + 2)
            j = n if j == -1 else j + 2
            out[i:j] = [ch if ch == "\n" else " " for ch in src[i:j]]
            i = j
        else:
            i += 1
    return "".join(out)


def _files(root: Path):
    for p in sorted(root.rglob("*")):
        if p.suffix in (".js", ".mjs", ".cjs", ".ts") and p.is_file() and not (set(p.relative_to(root).parts) & SKIP_DIRS):
            yield p


def _functions(src: str) -> list[tuple[str, list[str], int, int]]:
    """(name, params, body_start, body_end) for function definitions."""
    out = []
    for rx in _FUNC_RES:
        for m in rx.finditer(src):
            brace = src.find("{", m.end())
            if brace == -1 or src[m.end():brace].strip() not in ("", "=>"):
                continue
            close = _match_close(src, brace)
            if close != -1:
                params = [p.strip().split("=")[0].strip() for p in m.group(2).split(",") if p.strip()]
                out.append((m.group(1), params, brace, close))
    return out


def _enclosing(funcs, idx: int):
    best = None
    for f in funcs:
        if f[2] <= idx <= f[3] and (best is None or f[2] > best[2]):
            best = f
    return best


def _call_args(src: str, open_idx: int) -> list[str]:
    close = _match_close(src, open_idx)
    return [a for a, _ in _split_top_level(src[open_idx + 1:close])] if close != -1 else []


def _literal(src: str, arg: str, before: int) -> str:
    """An identifier argument -> the object literal most recently assigned to
    it before `before` (`var searchCriteria = {...}`); otherwise arg itself."""
    arg = arg.strip()
    if not re.fullmatch(_IDENT, arg):
        return arg
    defs = list(re.finditer(rf"(?<![\w$.]){re.escape(arg)}\s*=\s*\{{", src[:before]))
    if not defs:
        return arg
    o = defs[-1].end() - 1
    c = _match_close(src, o)
    return src[o:c + 1] if c != -1 else arg


def _filter_fields(arg: str) -> list[dict[str, str]] | None:
    """Top-level fields of an object-literal filter; None when not a literal
    or when it uses a top-level operator ($or, $and, $where ...)."""
    arg = arg.strip()
    if not arg.startswith("{"):
        return None
    inner = arg[1:-1].strip()
    if not inner:
        return []
    fields = []
    for part, _ in _split_top_level(inner):
        if not part:
            continue
        if ":" not in part:  # shorthand {username}
            name, value = part.strip(), ""
        else:
            name, value = part.split(":", 1)
        name = name.strip().strip("'\"`")
        if name.startswith("$") or name.startswith("..."):
            return None
        value = value.strip()
        match = "eq"
        if value.startswith("{"):
            ops = set(re.findall(r"\$(\w+)", value))
            match = "range" if ops and ops <= {"gt", "gte", "lt", "lte"} else "in" if ops == {"in"} else "other" if ops else "eq"
        fields.append({"name": name, "match": match})
    return fields


class _Project:
    def __init__(self, root: Path):
        self.root = root
        self.app = root.name
        self.raw: dict[str, str] = {}
        self.src: dict[str, str] = {}
        for p in _files(root):
            rel = p.relative_to(root).as_posix()
            self.raw[rel] = p.read_text(encoding="utf-8", errors="replace")
            self.src[rel] = _blank_comments(self.raw[rel])

    def ref(self, rel: str, idx: int, symbol: str) -> dict[str, Any]:
        return {"file": f"{self.app}/{rel}", "line": _line_of(self.src[rel], idx), "symbol": symbol}

    def name_of(self, expr: str) -> str | None:
        """A collection name expression -> its string value, if it is a
        literal or a uniquely assigned constant (`bookingName: "booking"`)."""
        expr = expr.strip()
        m = re.fullmatch(r"['\"`]([^'\"`$]+)['\"`]", expr)
        if m:
            return m.group(1)
        m = re.search(rf"({_IDENT})\s*$", expr)
        if not m:
            return None
        ident = m.group(1)
        rx = rf"\b{re.escape(ident)}\s*[:=]\s*['\"]([^'\"]+)['\"]"
        values = {v for s in self.src.values() for v in re.findall(rx, s)}
        if len(values) > 1:
            # several data-access implementations (mongo / cloudant / cassandra)
            # name the same constant differently: the MongoDB module's wins
            values = {v for s in self.src.values() if _MONGO_REQUIRE_RE.search(s) for v in re.findall(rx, s)}
        return values.pop() if len(values) == 1 else None


def detect(monolith_root: str | Path) -> dict[str, Any] | None:
    root = Path(monolith_root).resolve()
    pj = _Project(root)
    driver, req_src = None, []
    for rel, s in pj.src.items():
        for m in _MONGO_REQUIRE_RE.finditer(s):
            lib = m.group(1) or m.group(2)
            driver = "mongoose" if lib == "mongoose" or driver == "mongoose" else "mongodb"
            req_src.append(pj.ref(rel, m.start(), lib))
    if not driver:
        return None

    collections: dict[str, list[dict]] = {}
    patterns: list[dict[str, Any]] = []
    unresolved: list[str] = []
    wrappers: dict[str, dict[str, Any]] = {}  # method name -> {op, coll_param, filter_param, filter_fields, def_src}

    def add_collection(name: str | None, ref: dict) -> None:
        if name:
            collections.setdefault(name, []).append(ref)

    for rel, s in pj.src.items():
        funcs = _functions(s)
        # mongoose models: const Booking = mongoose.model('Booking', schema)
        models = {m.group(1): m.group(2) for m in re.finditer(
            rf"(?:const|let|var)\s+({_IDENT})\s*=\s*\w+\.model\(\s*['\"]([^'\"]+)['\"]", s)}
        sites = []
        for m in _CHAINED_RE.finditer(s):
            sites.append((m.start(2), m.group(2), m.group(1), m.end() - 1))
        for m in _OP_RE.finditer(s):
            recv, op = m.group(1), m.group(2)
            fn = _enclosing(funcs, m.start())
            scope = s[fn[2]:fn[3]] if fn else s
            coll_expr = None
            if recv in models:
                coll_expr = repr(models[recv])
            else:
                b = re.search(rf"(?:const|let|var)?\s*{re.escape(recv)}\s*=\s*(?:await\s+)?\w+\s*\.\s*collection\s*\(\s*([^(),]+)", scope) \
                    or re.search(rf"\.\s*collection\s*\(\s*([^(),]+?)\s*,\s*(?:async\s*)?(?:function\s*)?\(\s*\w+\s*,\s*{re.escape(recv)}\s*\)", scope)
                if not b:
                    continue  # not a collection (arrays, routers, caches ...)
                coll_expr = b.group(1)
            sites.append((m.start(2), op, coll_expr, m.end() - 1))

        for pos, op, coll_expr, open_idx in sites:
            args = _call_args(s, open_idx)
            filt = args[0] if args and OPS[op] not in ("insert", "aggregate") else ""
            fn = _enclosing(funcs, pos)
            params = fn[1] if fn else []
            coll_is_param = coll_expr.strip() in params
            filt_is_param = filt.strip() in params
            ffields = _filter_fields(filt) if filt and not filt_is_param else ([] if not filt and OPS[op] != "aggregate" else None)
            stmt_end = s.find(";", open_idx)
            stmt = s[open_idx: stmt_end if stmt_end != -1 else open_idx + 300]
            sm = re.search(r"\.\s*sort\s*\(([^)]*)\)", stmt)
            ref = pj.ref(rel, pos, op)
            if fn and (coll_is_param or filt_is_param or any(p and re.search(rf"\b{re.escape(p)}\b", filt) for p in params)):
                wrappers[fn[0]] = {
                    "op": OPS[op], "raw_op": op, "def_src": ref, "defs": [ref], "raw_wrapper": fn[0],
                    "coll_param": params.index(coll_expr.strip()) if coll_is_param else None,
                    "coll_name": None if coll_is_param else pj.name_of(coll_expr),
                    "filter_param": params.index(filt.strip()) if filt_is_param else None,
                    "filter_fields": ffields, "filter_raw": filt, "sort": sm.group(1).strip() if sm else None,
                }
                continue
            name = pj.name_of(coll_expr)
            add_collection(name, ref)
            if name is None:
                unresolved.append(f"{ref['file']}:{ref['line']} {op}: collection expression {coll_expr!r} not resolved")
            if ffields is None and OPS[op] not in ("insert",):
                unresolved.append(f"{ref['file']}:{ref['line']} {op}: filter {filt[:80]!r} not a plain object literal")
            patterns.append({"collection": name, "operation": OPS[op], "filter_fields": ffields, "filter_raw": filt[:200],
                             "sort": sm.group(1).strip() if sm else None, "via": None, "note": None,
                             "source": [ref], "origin": "script"})

    # Call sites of the wrappers: <anything>.findBy(collection, condition, cb),
    # or a bare countItems(...) in the wrapper's own file. A call site that
    # only forwards its own parameters is itself a wrapper (countItems(dbName)
    # -> dataaccess.count(dbName, {})): resolved again, until none are left.
    funcs_of = {rel: _functions(s) for rel, s in pj.src.items()}
    work = list(wrappers.items())
    done: set[tuple[str, str]] = set()  # (wrapper name, defining file)
    while work:
        wname, w = work.pop(0)
        def_file = w["def_src"]["file"].split("/", 1)[1]
        if (wname, def_file) in done:
            continue
        done.add((wname, def_file))
        for rel, s in pj.src.items():
            rx = rf"(?:\.\s*|(?<![\w$.]))(?<!function ){re.escape(wname)}\s*\(" if rel == def_file else rf"\.\s*{re.escape(wname)}\s*\("
            for m in re.finditer(rx, s):
                ref = pj.ref(rel, m.start(), wname)
                if rel == def_file and any(abs(ref["line"] - d["line"]) < 2 for d in w["defs"]):
                    continue
                args = _call_args(s, m.end() - 1)
                coll_arg = args[w["coll_param"]] if w["coll_param"] is not None and len(args) > w["coll_param"] else None
                filt_arg = args[w["filter_param"]] if w["filter_param"] is not None and len(args) > w["filter_param"] else None
                fn = _enclosing(funcs_of[rel], m.start())
                params = fn[1] if fn else []
                fwd_coll = coll_arg is not None and coll_arg.strip() in params
                fwd_filt = filt_arg is not None and filt_arg.strip() in params
                if fn and (fwd_coll or fwd_filt) and fn[0] in wrappers:
                    continue  # same-named forwarder: its callers are matched under this name already
                if fn and (fwd_coll or fwd_filt):
                    work.append((fn[0], {**w, "defs": w["defs"] + [ref], "def_src": ref,
                                         "coll_param": params.index(coll_arg.strip()) if fwd_coll else None,
                                         "coll_name": w["coll_name"] if coll_arg is None else (None if fwd_coll else pj.name_of(coll_arg)),
                                         "filter_param": params.index(filt_arg.strip()) if fwd_filt else None,
                                         "filter_fields": w["filter_fields"] if filt_arg is None else (None if fwd_filt else _filter_fields(_literal(s, filt_arg, m.start()))),
                                         "filter_raw": w["filter_raw"] if filt_arg is None else filt_arg}))
                    continue
                name = w["coll_name"] if coll_arg is None else pj.name_of(coll_arg)
                note = None
                if filt_arg is not None:
                    raw = _literal(s, filt_arg, m.start())
                    fields = _filter_fields(raw)
                else:
                    raw, fields = w["filter_raw"], w["filter_fields"]
                    # the call site passes a filter the wrapper does not fully use
                    passed = [_filter_fields(a) for a in args if a.strip().startswith("{")]
                    if passed and passed[0] is not None and fields is not None:
                        extra = sorted({f["name"] for f in passed[0]} - {f["name"] for f in fields})
                        if extra:
                            note = (f"call site passes {sorted(f['name'] for f in passed[0])} but {w['raw_wrapper']} filters only on "
                                    f"{[f['name'] for f in fields]}: {extra} are ignored by the monolith")
                add_collection(name, ref)
                if name is None:
                    unresolved.append(f"{ref['file']}:{ref['line']} {wname}: collection argument {coll_arg!r} not resolved")
                if fields is None:
                    unresolved.append(f"{ref['file']}:{ref['line']} {wname}: filter {raw[:80]!r} not a plain object literal")
                patterns.append({"collection": name, "operation": w["op"], "filter_fields": fields, "filter_raw": raw[:200],
                                 "sort": w["sort"], "via": " -> ".join([wname] + ([w["raw_wrapper"]] if wname != w["raw_wrapper"] else [])),
                                 "note": note, "source": [ref] + w["defs"][::-1], "origin": "script"})

    # indexes the monolith declares: db.ensureIndex(coll, {a:1, b:1}) or
    # coll.createIndex({a:1}) -- the access paths it already relies on
    indexes = []
    for rel, s in pj.src.items():
        for m in re.finditer(rf"\b({_IDENT})\s*\.\s*(ensureIndex|createIndex)\s*\(", s):
            args = _call_args(s, m.end() - 1)
            if not args:
                continue
            if args[0].strip().startswith("{"):
                coll, keys = None, args[0]
                fn = _enclosing(_functions(s), m.start())
                b = re.search(rf"{re.escape(m.group(1))}\s*=\s*\w+\s*\.\s*collection\s*\(\s*([^(),]+)", s[fn[2]:fn[3]] if fn else s)
                coll = pj.name_of(b.group(1)) if b else None
            elif len(args) > 1:
                coll, keys = pj.name_of(args[0]), args[1]
            else:
                continue
            fields = _filter_fields(keys)
            if fields is not None:
                indexes.append({"collection": coll, "keys": [f["name"] for f in fields],
                                "source": [pj.ref(rel, m.start(2), m.group(2))], "origin": "script"})

    for i, p in enumerate(patterns, 1):
        p["id"] = f"AP{i}"
    return {
        "kind": "mongodb", "driver": driver, "source": req_src,
        "collections": [{"name": n, "source": refs[:3]} for n, refs in sorted(collections.items())],
        "access_patterns": [{"id": p.pop("id"), **p} for p in patterns],
        "indexes": indexes,
        "unresolved": unresolved,
    }
