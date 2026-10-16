"""P0 spec extraction for Python (Flask) entry points -- the ast-based twin of
the Express extractor in spec_extract.py, producing the same fields with the
same shapes (handler_location, auth, request, responses, data_access,
business_rules = [], spec_status) and dynamodb_info.table_schemas entries.

Every item carries `source` [{file, line, symbol}] where `symbol` is text on
(or within two lines of) the cited line, so check_provenance can confirm it,
and `origin: "script"`. Anything not recognized is recorded as "unknown",
never guessed, and left to the m2s-spec-annotator.

Recognized (as found in the benchmark's Flask apps):
- routes: @app.route / @bp.route (+ Blueprint url_prefix, via the
  analyzer's own entry points and their handler_function), Flask 2 shortcuts
  @bp.get/post/...; path params <name> / <int:name>;
- auth: the handler's other decorators (login_required, jwt_required(),
  role decorators) and the module's @bp.before_request hooks;
- request: X = request.get_json() / request.json, then X.get('k'[, default]),
  X['k'], 'k' in X; request.args.get('k'[, default][, type=int]);
  request.headers.get('K'); request.cookies.get('k'); required-ness from an
  `if` whose branch answers 4xx (return ..., 4xx / abort(4xx));
- responses: return jsonify({...}|k=v), status / return {...}, status /
  make_response(...) / abort(status); in_catch inside except;
- data_access: boto3 calls (get_item, put_item, update_item, delete_item,
  query, scan, batch_get_item, batch_write_item, transact_*, and
  batch_writer() puts/deletes), followed through project-local functions,
  classes and methods up to depth 2; tables resolved through Table(...)
  arguments, helper functions returning Table(...), module constants,
  os.environ / os.getenv and app.config[...] keys defined in config classes.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

_AUTH_RE = re.compile(r"auth|token|jwt|login|session|protect|guard|require|verify", re.I)
_OPTIONAL_RE = re.compile(r"optional", re.I)
_ROLE_RE = re.compile(r"admin|role|_required$|permission", re.I)
_DDB_METHODS = {"get_item", "put_item", "update_item", "delete_item", "query", "scan", "batch_get_item",
                "batch_write_item", "transact_write_items", "transact_get_items"}
_SKIP_DIRS = {"venv", ".venv", "env", "__pycache__", ".git", "node_modules", "tests", "test", "site-packages"}
_ROUTE_ATTRS = {"route", "get", "post", "put", "delete", "patch"}


def _src(file: str, line: int, symbol: str) -> dict[str, Any]:
    return {"file": file, "line": int(line), "symbol": str(symbol)}


def _dedupe(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen, out = set(), []
    for s in sources:
        k = (s["file"], s["line"], s["symbol"])
        if k not in seen:
            seen.add(k)
            out.append(s)
    return out


def _unparse(node: ast.AST, limit: int = 160) -> str:
    try:
        text = ast.unparse(node)
    except Exception:  # noqa: BLE001
        return "?"
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _name_of(node: ast.AST) -> str | None:
    """Decorator / callee name: `x`, `a.b` or the func of a call."""
    if isinstance(node, ast.Call):
        node = node.func
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _name_of(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return None


def _const_str(node: ast.AST | None) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


class PyProject:
    """Sources and ASTs of every .py file, keyed by app-prefixed rel path
    ("shopping-cart/models.py", matching analysis_report's own keys)."""

    def __init__(self, monolith_root: Path):
        self.root = monolith_root
        self.app = monolith_root.name
        self.sources: dict[str, str] = {}
        self.trees: dict[str, ast.Module] = {}
        self.modules: dict[str, str] = {}  # dotted module name -> rel
        for path in sorted(monolith_root.rglob("*.py")):
            parts = path.relative_to(monolith_root).parts
            if any(p in _SKIP_DIRS or p.startswith(".") for p in parts):
                continue
            rel = f"{self.app}/{path.relative_to(monolith_root).as_posix()}"
            src = path.read_text(encoding="utf-8", errors="ignore")
            try:
                tree = ast.parse(src)
            except SyntaxError:
                continue
            self.sources[rel], self.trees[rel] = src, tree
            dotted = ".".join(parts[:-1] + ((path.stem,) if path.stem != "__init__" else ()))
            if dotted:
                self.modules[dotted] = rel
        self.defs = {rel: self._index_defs(tree) for rel, tree in self.trees.items()}
        self.config_index = self._index_config()

    # -- symbols ---------------------------------------------------------------
    @staticmethod
    def _index_defs(tree: ast.Module) -> dict[str, ast.AST]:
        out: dict[str, ast.AST] = {}
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                out[node.name] = node
            if isinstance(node, ast.ClassDef):
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        out[f"{node.name}.{item.name}"] = item
        return out

    def imports(self, rel: str) -> dict[str, tuple[str, str | None]]:
        """local name -> (target rel, symbol or None for a module)."""
        out: dict[str, tuple[str, str | None]] = {}
        pkg = rel.split("/", 1)[1].rsplit("/", 1)[0].replace("/", ".") if "/" in rel.split("/", 1)[1] else ""
        for node in ast.walk(self.trees[rel]):  # function-local imports count too
            if isinstance(node, ast.ImportFrom):
                base = node.module or ""
                if node.level:
                    parent = pkg.split(".") if pkg else []
                    parent = parent[: len(parent) - (node.level - 1)] if node.level > 1 else parent
                    base = ".".join([p for p in parent if p] + ([node.module] if node.module else []))
                for alias in node.names:
                    local = alias.asname or alias.name
                    sub = f"{base}.{alias.name}" if base else alias.name
                    if sub in self.modules:  # from pkg import module
                        out[local] = (self.modules[sub], None)
                    elif base in self.modules:
                        target = self.modules[base]
                        # re-exported from a package __init__: follow one hop
                        if alias.name not in self.defs.get(target, {}):
                            hop = self.imports(target).get(alias.name) if target != rel else None
                            if hop:
                                out[local] = hop
                                continue
                        out[local] = (target, alias.name)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in self.modules:
                        out[alias.asname or alias.name] = (self.modules[alias.name], None)
        return out

    # -- configuration -----------------------------------------------------------
    def _index_config(self) -> dict[str, dict[str, Any]]:
        """NAME -> {env_var, default_name, source} for module- or class-level
        `NAME = os.environ.get('X'[, 'd'])` (also `... or 'd'`, os.getenv,
        os.environ['X'])."""
        out: dict[str, dict[str, Any]] = {}
        for rel, tree in self.trees.items():
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Assign) and len(node.targets) == 1):
                    continue
                target = node.targets[0]
                # NAME = ... (module / class level) or self.name = ... (in __init__)
                name = target.id if isinstance(target, ast.Name) else target.attr if isinstance(target, ast.Attribute) \
                    and isinstance(target.value, ast.Name) and target.value.id == "self" else None
                env = self.env_of(node.value) if name else None
                if env:
                    out.setdefault(name, {**env, "source": [_src(rel, node.lineno, name)]})
        return out

    @staticmethod
    def _default(node: ast.AST | None) -> str | None:
        """A default table name: a string, or an f-string kept as its
        pattern (f'Airline-Flight-{stage}' -> 'Airline-Flight-{stage}')."""
        if node is None:
            return None
        if isinstance(node, ast.JoinedStr):
            return "".join(v.value if isinstance(v, ast.Constant) else "{" + _unparse(v.value) + "}"
                           for v in node.values if isinstance(v, (ast.Constant, ast.FormattedValue)))
        return _const_str(node)

    @staticmethod
    def env_of(node: ast.AST) -> dict[str, Any] | None:
        default = None
        if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or) and len(node.values) == 2:
            default = PyProject._default(node.values[1])
            node = node.values[0]
        if isinstance(node, ast.Call) and _name_of(node.func) in ("os.environ.get", "os.getenv", "environ.get", "getenv"):
            var = _const_str(node.args[0]) if node.args else None
            if var:
                return {"env_var": var, "default_name": PyProject._default(node.args[1]) if len(node.args) > 1 else default}
        if isinstance(node, ast.Subscript) and _name_of(node.value) in ("os.environ", "environ"):
            var = _const_str(node.slice)
            if var:
                return {"env_var": var, "default_name": default}
        return None


class _Fn:
    """One function body under analysis: its module, node and parameters."""

    def __init__(self, project: PyProject, rel: str, node: ast.AST, cls: str | None = None):
        self.p, self.rel, self.node, self.cls = project, rel, node, cls
        self.params = [a.arg for a in getattr(node, "args", ast.arguments(args=[], posonlyargs=[], kwonlyargs=[])).args] if hasattr(node, "args") else []

    def walk(self):
        """Nodes of the body, not descending into nested defs."""
        stack = list(getattr(self.node, "body", []))
        while stack:
            n = stack.pop(0)
            yield n
            for child in ast.iter_child_nodes(n):
                if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                    stack.append(child)

    def assignments(self, name: str) -> list[ast.AST]:
        out = []
        for n in self.walk():
            if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in n.targets):
                out.append(n.value)
            elif isinstance(n, ast.With):
                for item in n.items:
                    if isinstance(item.optional_vars, ast.Name) and item.optional_vars.id == name:
                        out.append(item.context_expr)
        return out


def _parents(root: ast.AST) -> dict[ast.AST, ast.AST]:
    return {child: parent for parent in ast.walk(root) for child in ast.iter_child_nodes(parent)}


# -- tables ---------------------------------------------------------------------
_UNKNOWN_TABLE = {"expr": "unknown", "config_key": "unknown", "env_var": "unknown", "default_name": "unknown"}


def _table_from_value(p: PyProject, rel: str, node: ast.AST, bound: dict[str, ast.AST], depth: int = 0) -> dict[str, Any]:
    """Resolve an expression naming a table (the Table(...) argument)."""
    if depth > 4:
        return dict(_UNKNOWN_TABLE, expr=_unparse(node))
    expr = _unparse(node)
    lit = _const_str(node)
    if lit is not None:
        info = p.config_index.get(lit)
        if info and lit.isupper():  # get_table('USERS_TABLE') -> config key
            return {"expr": expr, "config_key": lit, "env_var": info["env_var"], "default_name": info["default_name"]}
        return {"expr": expr, "config_key": None, "env_var": None, "default_name": lit}
    env = PyProject.env_of(node)
    if env:
        return {"expr": expr, "config_key": None, **env}
    if isinstance(node, ast.JoinedStr):  # f'Airline-Flight-{stage}' -> its pattern
        return {"expr": expr, "config_key": None, "env_var": None, "default_name": PyProject._default(node)}
    if isinstance(node, ast.Subscript) and "config" in (_name_of(node.value) or ""):
        key_node = node.slice
        if isinstance(key_node, ast.Name) and key_node.id in bound:
            key_node = bound[key_node.id]
        key = _const_str(key_node)
        if key:
            info = p.config_index.get(key, {})
            return {"expr": expr, "config_key": key, "env_var": info.get("env_var", "unknown"), "default_name": info.get("default_name", "unknown")}
    if isinstance(node, ast.Attribute) and (node.attr in p.config_index or (_name_of(node.value) or "").lower().endswith("config")):
        # Config.X / conf.X / settings.X where X is a known config constant
        info = p.config_index.get(node.attr, {})
        return {"expr": expr, "config_key": node.attr, "env_var": info.get("env_var", "unknown"), "default_name": info.get("default_name", "unknown")}
    if isinstance(node, ast.Name):
        if node.id in bound:
            return _table_from_value(p, rel, bound[node.id], {}, depth + 1)
        info = p.config_index.get(node.id)
        if info:
            return {"expr": expr, "config_key": node.id, "env_var": info["env_var"], "default_name": info["default_name"]}
        for n in p.trees[rel].body:  # module constant
            if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == node.id for t in n.targets):
                return _table_from_value(p, rel, n.value, {}, depth + 1)
    return dict(_UNKNOWN_TABLE, expr=expr)


def _table_of_receiver(fn: _Fn, recv: ast.AST, depth: int = 0) -> dict[str, Any]:
    """Which table a boto3 receiver (a Table object, a batch writer) is."""
    p = fn.p
    if depth > 4:
        return dict(_UNKNOWN_TABLE)
    if isinstance(recv, ast.Call):
        return _table_of_call(fn, recv, {}, depth)
    if isinstance(recv, ast.Name):
        vals = fn.assignments(recv.id)
        if vals:
            return _table_of_receiver(fn, vals[-1], depth + 1)
        for n in p.trees[fn.rel].body:
            if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == recv.id for t in n.targets):
                return _table_of_receiver(fn, n.value, depth + 1)
    if isinstance(recv, ast.Attribute) and isinstance(recv.value, ast.Name) and recv.value.id in ("self", "cls") and fn.cls:
        # self.table set in the class (e.g. in __init__ or as a class attribute)
        cls_node = p.defs[fn.rel].get(fn.cls)
        for n in ast.walk(cls_node) if cls_node else []:
            if isinstance(n, ast.Assign) and any(_unparse(t) in (f"self.{recv.attr}", recv.attr) for t in n.targets):
                return _table_of_receiver(_Fn(p, fn.rel, cls_node, fn.cls), n.value, depth + 1)
    return dict(_UNKNOWN_TABLE, expr=_unparse(recv))


def _known(info: dict[str, Any]) -> bool:
    return info.get("config_key") not in (None, "unknown") or info.get("default_name") not in (None, "unknown") \
        or info.get("env_var") not in (None, "unknown")


def _table_of_call(fn: _Fn, call: ast.Call, bound: dict[str, ast.AST], depth: int) -> dict[str, Any]:
    """The table a call returns: X.Table(arg), X.batch_writer(), or a
    project-local helper whose return value is one of those (recursively,
    with its parameters bound to the call's arguments -- get_users_table()
    -> get_table('TABLE_USERS_NAME') -> Table(current_app.config[key]))."""
    p = fn.p
    if depth > 5:
        return dict(_UNKNOWN_TABLE, expr=_unparse(call))
    callee = call.func
    if isinstance(callee, ast.Attribute) and callee.attr == "Table" and call.args:
        arg = call.args[0]
        if isinstance(arg, ast.Name) and arg.id not in bound:
            vals = fn.assignments(arg.id)
            if vals:
                return _table_from_value(p, fn.rel, vals[-1], bound)
        return _table_from_value(p, fn.rel, arg, bound)
    if isinstance(callee, ast.Attribute) and callee.attr == "batch_writer":
        return _table_of_receiver(fn, callee.value, depth + 1)
    target = _resolve_call(fn, call)
    if not target:
        return dict(_UNKNOWN_TABLE, expr=_unparse(call))
    t_rel, t_node, cls = target
    params = [a.arg for a in t_node.args.args]
    is_static = any(_name_of(d) == "staticmethod" for d in t_node.decorator_list)
    if params and params[0] in ("self", "cls") and not is_static:
        params = params[1:]
    new_bound: dict[str, ast.AST] = {}
    for name, value in zip(params, call.args):
        new_bound[name] = bound.get(value.id, value) if isinstance(value, ast.Name) else value
    for kw in call.keywords:
        if kw.arg:
            new_bound[kw.arg] = bound.get(kw.value.id, kw.value) if isinstance(kw.value, ast.Name) else kw.value
    tfn = _Fn(p, t_rel, t_node, cls)
    for n in tfn.walk():
        if not isinstance(n, ast.Return) or n.value is None:
            continue
        value = n.value
        if isinstance(value, ast.Name):
            vals = tfn.assignments(value.id)
            value = vals[-1] if vals else value
        if isinstance(value, ast.Call):
            info = _table_of_call(tfn, value, new_bound, depth + 1)
            if _known(info):
                return info
    return dict(_UNKNOWN_TABLE, expr=_unparse(call))


def _resolve_call(fn: _Fn, call: ast.Call) -> tuple[str, ast.AST, str | None] | None:
    """Project-local target (rel, def node, class) of a call, if any."""
    p = fn.p
    imports = p.imports(fn.rel)
    func = call.func
    if isinstance(func, ast.Name):
        if func.id in p.defs[fn.rel] and isinstance(p.defs[fn.rel][func.id], (ast.FunctionDef, ast.AsyncFunctionDef)):
            return fn.rel, p.defs[fn.rel][func.id], None
        if func.id in imports:
            t_rel, sym = imports[func.id]
            node = p.defs.get(t_rel, {}).get(sym or "")
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                return t_rel, node, None
        return None
    if isinstance(func, ast.Attribute):
        base = func.value
        if isinstance(base, ast.Name):
            if base.id in ("self", "cls") and fn.cls:
                node = p.defs[fn.rel].get(f"{fn.cls}.{func.attr}")
                return (fn.rel, node, fn.cls) if node else None
            # Class.method / module.function / obj.method where obj = Class(...)
            owner_rel, owner_sym = None, None
            if base.id in p.defs[fn.rel] and isinstance(p.defs[fn.rel][base.id], ast.ClassDef):
                owner_rel, owner_sym = fn.rel, base.id
            elif base.id in imports:
                owner_rel, owner_sym = imports[base.id]
            else:
                for v in reversed(fn.assignments(base.id)):
                    if isinstance(v, ast.Call):
                        cname = _name_of(v.func)
                        if cname in p.defs[fn.rel] and isinstance(p.defs[fn.rel][cname], ast.ClassDef):
                            owner_rel, owner_sym = fn.rel, cname
                        elif cname in imports:
                            owner_rel, owner_sym = imports[cname]
                        break
            if owner_rel and owner_sym and not isinstance(p.defs.get(owner_rel, {}).get(owner_sym), ast.ClassDef):
                # a module-level instance: storage = DataStorage() -> DataStorage
                cls = _instance_class(p, owner_rel, owner_sym)
                if cls:
                    owner_rel, owner_sym = cls
            if owner_rel:
                key = f"{owner_sym}.{func.attr}" if owner_sym else func.attr
                node = p.defs.get(owner_rel, {}).get(key)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    return owner_rel, node, owner_sym
    return None


def _instance_class(p: PyProject, rel: str, name: str) -> tuple[str, str] | None:
    """`name = SomeClass(...)` at module level of `rel` -> (class rel, class)."""
    for n in p.trees[rel].body:
        if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in n.targets) \
                and isinstance(n.value, ast.Call):
            cname = _name_of(n.value.func)
            if cname in p.defs[rel] and isinstance(p.defs[rel][cname], ast.ClassDef):
                return rel, cname
            imported = p.imports(rel).get(cname or "")
            if imported and imported[1] and isinstance(p.defs.get(imported[0], {}).get(imported[1]), ast.ClassDef):
                return imported
    return None


def _data_access(fn: _Fn, via: str | None = None, depth: int = 0, seen: set | None = None) -> list[dict[str, Any]]:
    p = fn.p
    seen = seen if seen is not None else set()
    parents = _parents(fn.node)

    def repeated(node: ast.AST) -> bool:
        cur = parents.get(node)
        while cur is not None and cur is not fn.node:
            if isinstance(cur, (ast.For, ast.AsyncFor, ast.While, ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
                return True
            cur = parents.get(cur)
        return False

    out: list[dict[str, Any]] = []
    for n in fn.walk():
        if not isinstance(n, ast.Call) or not isinstance(n.func, ast.Attribute):
            continue
        op, recv = n.func.attr, n.func.value
        if op in _DDB_METHODS:
            table = _table_of_receiver(fn, recv)
            batch = isinstance(recv, ast.Name) and any(isinstance(v, ast.Call) and isinstance(v.func, ast.Attribute)
                                                       and v.func.attr == "batch_writer" for v in fn.assignments(recv.id))
            index = next((_const_str(k.value) for k in n.keywords if k.arg == "IndexName"), None)
            item = {"operation": f"batch_writer.{op}" if batch else op, "table": table, "index": index,
                    "repeated": repeated(n), "source": [_src(fn.rel, n.lineno, op)], "origin": "script"}
            if via:
                item["via"] = via
            out.append(item)
    if depth >= 2:
        return out
    for n in fn.walk():
        if not isinstance(n, ast.Call):
            continue
        target = _resolve_call(fn, n)
        if not target or (target[0], target[1].name, target[2]) in seen:
            continue
        seen.add((target[0], target[1].name, target[2]))
        name = f"{target[2]}.{target[1].name}" if target[2] else target[1].name
        chain = f"{via} -> {name}" if via else name
        inner = _data_access(_Fn(p, target[0], target[1], target[2]), chain, depth + 1, seen)
        call_name = _name_of(n.func) or name
        for it in inner:
            if repeated(n):
                it["repeated"] = True
            it["source"].insert(0, _src(fn.rel, n.lineno, call_name.rsplit(".", 1)[-1]))
        out.extend(inner)
    return out


# -- request / responses -----------------------------------------------------------
def _status_of(node: ast.AST | None) -> int | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, int) else None


def _body_shape(node: ast.AST) -> dict[str, Any]:
    if isinstance(node, ast.Call) and _name_of(node.func) in ("jsonify", "flask.jsonify"):
        if node.args and isinstance(node.args[0], ast.Dict):
            node = node.args[0]
        elif node.keywords and not node.args:
            return {"fields": [k.arg for k in node.keywords if k.arg], "expression": None}
        elif node.args:
            return {"fields": None, "expression": _unparse(node.args[0])}
    if isinstance(node, ast.Call) and _name_of(node.func) == "make_response" and node.args:
        return _body_shape(node.args[0])
    if isinstance(node, ast.Dict):
        keys = [_const_str(k) for k in node.keys]
        if all(keys):
            return {"fields": keys, "expression": None}
    return {"fields": None, "expression": _unparse(node)}


def _responses(fn: _Fn) -> list[dict[str, Any]]:
    parents = _parents(fn.node)

    def in_except(node: ast.AST) -> bool:
        cur = parents.get(node)
        while cur is not None and cur is not fn.node:
            if isinstance(cur, ast.ExceptHandler):
                return True
            cur = parents.get(cur)
        return False

    out = []
    for n in fn.walk():
        status, body, line, symbol = None, None, None, None
        if isinstance(n, ast.Return) and n.value is not None:
            value = n.value
            if isinstance(value, ast.Tuple) and len(value.elts) >= 2:
                status = _status_of(value.elts[1])
                body = _body_shape(value.elts[0])
                line, symbol = (value.elts[1].lineno, str(status)) if status is not None else (n.lineno, "return")
            else:
                if isinstance(value, ast.Call) and _name_of(value.func) == "make_response" and len(value.args) >= 2:
                    status = _status_of(value.args[1])
                status = status if status is not None else 200
                body = _body_shape(value)
                symbol = _name_of(value.func).rsplit(".", 1)[-1] if isinstance(value, ast.Call) and _name_of(value.func) else "return"
                line = value.lineno
        elif isinstance(n, ast.Call) and _name_of(n.func) in ("abort", "flask.abort") and n.args:
            status = _status_of(n.args[0])
            body = {"fields": None, "expression": None}
            line, symbol = n.lineno, "abort"
        else:
            continue
        item = {"status": status if status is not None else "unknown",
                "kind": "unknown" if status is None else ("success" if 200 <= status < 300 else "error"),
                "body": body, "source": [_src(fn.rel, line, symbol)], "origin": "script"}
        if in_except(n):
            item["in_catch"] = True
        out.append(item)
    return out


def _rejects(stmts: list[ast.stmt]) -> bool:
    """Does this branch answer 4xx (return ..., 4xx / abort(4xx))?"""
    for n in ast.walk(ast.Module(body=stmts, type_ignores=[])):
        if isinstance(n, ast.Return) and isinstance(n.value, ast.Tuple) and len(n.value.elts) >= 2:
            s = _status_of(n.value.elts[1])
            if s is not None and 400 <= s < 500:
                return True
        if isinstance(n, ast.Call) and _name_of(n.func) in ("abort", "flask.abort") and n.args:
            s = _status_of(n.args[0])
            if s is not None and 400 <= s < 500:
                return True
    return False


def _request(fn: _Fn, path: str, route_line: int) -> dict[str, list[dict[str, Any]]]:
    rel = fn.rel
    fields: dict[str, dict[str, dict[str, Any]]] = {"path_params": {}, "query": {}, "body": {}, "headers": {}}
    for m in re.finditer(r"<(?:(\w+):)?(\w+)>", path):
        conv = m.group(1) or "string"
        fields["path_params"][m.group(2)] = {"name": m.group(2), "type": {"int": "integer", "float": "number"}.get(conv, "string"),
                                             "required": True, "source": [_src(rel, route_line, m.group(2))], "origin": "script"}
    # Variables holding the JSON body / form: X = request.get_json() / request.json [or {}]
    body_vars = set()
    for n in fn.walk():
        if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name):
            v = n.value.values[0] if isinstance(n.value, ast.BoolOp) else n.value
            text = _unparse(v)
            if re.fullmatch(r"request\.(get_json\(.*\)|json|form|values)", text):
                body_vars.add(n.targets[0].id)

    local_alias: dict[str, dict[str, Any]] = {}  # username = data.get('username')

    def add(loc: str, name: str, line: int, *, required: Any = "unknown", type_: str = "unknown") -> dict[str, Any]:
        bucket = fields[loc]
        item = bucket.get(name)
        if item is None:
            item = bucket[name] = {"name": name, "type": type_, "required": required,
                                   "source": [_src(rel, line, name)], "origin": "script"}
        return item

    for n in fn.walk():
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "get" and n.args:
            key = _const_str(n.args[0])
            owner = _unparse(n.func.value)
            if not key:
                continue
            has_default = len(n.args) > 1 or any(k.arg == "default" for k in n.keywords)
            if owner in body_vars or owner in ("request.json", "request.form"):
                add("body", key, n.lineno, required=False if has_default else "unknown")
            elif owner == "request.args":
                t = next((_unparse(k.value) for k in n.keywords if k.arg == "type"), None)
                add("query", key, n.lineno, required=False if has_default else "unknown",
                    type_={"int": "integer", "float": "number", "str": "string"}.get(t or "", "unknown"))
            elif owner == "request.headers":
                add("headers", key, n.lineno)
            elif owner == "request.cookies":
                add("headers", f"Cookie:{key}", n.lineno)["source"][-1]["symbol"] = key
        elif isinstance(n, ast.Subscript) and _unparse(n.value) in body_vars | {"request.json", "request.form"}:
            key = _const_str(n.slice)
            if key:
                add("body", key, n.lineno)
        elif isinstance(n, ast.Subscript) and _unparse(n.value) == "request.args":
            key = _const_str(n.slice)
            if key:
                add("query", key, n.lineno)
    for n in fn.walk():  # local names for body/query fields
        if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name):
            v = n.value
            if isinstance(v, ast.Call) and isinstance(v.func, ast.Attribute) and v.func.attr == "get" and v.args:
                key, owner = _const_str(v.args[0]), _unparse(v.func.value)
                bucket = "body" if owner in body_vars or owner in ("request.json", "request.form") else "query" if owner == "request.args" else None
                if key and bucket and key in fields[bucket]:
                    local_alias[n.targets[0].id] = fields[bucket][key]
            elif isinstance(v, ast.Subscript) and _unparse(v.value) in body_vars:
                key = _const_str(v.slice)
                if key and key in fields["body"]:
                    local_alias[n.targets[0].id] = fields["body"][key]
    # Required-ness from `if <negative test>: ... return ..., 4xx`
    for n in fn.walk():
        if not isinstance(n, ast.If) or not _rejects(n.body):
            continue
        for t in ast.walk(n.test):
            item, symbol = None, None
            if isinstance(t, ast.UnaryOp) and isinstance(t.op, ast.Not):
                operand = t.operand
                if isinstance(operand, ast.Name) and operand.id in local_alias:
                    item, symbol = local_alias[operand.id], operand.id
                elif isinstance(operand, ast.Call) and isinstance(operand.func, ast.Attribute) and operand.func.attr == "get" \
                        and operand.args and _unparse(operand.func.value) in body_vars:
                    key = _const_str(operand.args[0])
                    item, symbol = fields["body"].get(key or ""), key
            elif isinstance(t, ast.Compare) and len(t.ops) == 1 and isinstance(t.ops[0], ast.NotIn) \
                    and _unparse(t.comparators[0]) in body_vars:
                key = _const_str(t.left)
                if key:
                    item, symbol = add("body", key, t.lineno), key
            elif isinstance(t, ast.Compare) and len(t.ops) == 1 and isinstance(t.ops[0], ast.Is) \
                    and isinstance(t.left, ast.Name) and t.left.id in local_alias and _unparse(t.comparators[0]) == "None":
                item, symbol = local_alias[t.left.id], t.left.id
            if item is not None and item["required"] is not True:
                item["required"] = True
                item["source"].append(_src(rel, t.lineno, symbol))
    for bucket in fields.values():
        for item in bucket.values():
            item["source"] = _dedupe(item["source"])
    return {k: list(v.values()) for k, v in fields.items()}


def _classify(names: list[str]) -> str:
    kinds = set()
    for name in names:
        if _OPTIONAL_RE.search(name):
            kinds.add("optional")
        elif _AUTH_RE.search(name) or _ROLE_RE.search(name):
            kinds.add("required")
        else:
            kinds.add("other")
    if "required" in kinds:
        return "required"
    if "optional" in kinds:
        return "optional"
    return "unknown" if "other" in kinds else "none"


def _route_decorator(fn_node: ast.AST) -> ast.Call | None:
    for dec in fn_node.decorator_list:
        if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute) and dec.func.attr in _ROUTE_ATTRS:
            return dec
    return None


def _before_request_hooks(p: PyProject, rel: str) -> list[dict[str, Any]]:
    out = []
    for node in p.trees[rel].body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in node.decorator_list:
                if (_name_of(dec) or "").endswith("before_request"):
                    out.append({"name": node.name, "source": _src(rel, node.lineno, node.name)})
    return out


def extract_entry_point(p: PyProject, ep: dict[str, Any]) -> bool:
    """Fill the spec fields of one Python entry point; False if its handler
    was not found."""
    rel = ep.get("file")
    node = p.defs.get(rel, {}).get(ep.get("handler_function") or "")
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return False
    route = _route_decorator(node)
    route_line = route.lineno if route else node.lineno
    raw_path = _const_str(route.args[0]) if route is not None and route.args else ep.get("path", "")
    fn = _Fn(p, rel, node)
    start = min([d.lineno for d in node.decorator_list] + [node.lineno])
    ep["handler_location"] = {"file": rel, "start_line": start, "end_line": node.end_lineno}
    mws = [{"name": _name_of(d) or _unparse(d), "source": _src(rel, d.lineno, (_name_of(d) or "").rsplit(".", 1)[-1])}
           for d in node.decorator_list if d is not route]
    mws += _before_request_hooks(p, rel)
    ep["auth"] = {"classification": _classify([m["name"] for m in mws]), "middleware": [m["name"] for m in mws],
                  "source": [m["source"] for m in mws] or [_src(rel, route_line, (raw_path or "/").strip("/").split("/")[0] or "route")],
                  "origin": "script"}
    ep["request"] = _request(fn, raw_path or "", route_line)
    ep["responses"] = _responses(fn)
    ep["data_access"] = _data_access(fn)
    ep["business_rules"] = []
    ep["spec_status"] = {"script": "extracted", "annotated_fields": []}
    return True


def _schema_table_name(p: PyProject, rel: str, tree: ast.Module, call: ast.Call, value: ast.AST) -> dict[str, Any]:
    """TableName of a create_table call. When it is a parameter of the
    enclosing function (create_users_table(client, table_name)), resolve the
    argument its call site in the module passes (conf.TABLE_USERS_NAME)."""
    info = _table_from_value(p, rel, value, {})
    if _known(info) or not isinstance(value, ast.Name):
        return info
    parents = _parents(tree)
    enclosing = parents.get(call)
    while enclosing is not None and not isinstance(enclosing, (ast.FunctionDef, ast.AsyncFunctionDef)):
        enclosing = parents.get(enclosing)
    if enclosing is None:
        return info
    local = _Fn(p, rel, enclosing)
    vals = local.assignments(value.id)
    if vals:
        info = _table_from_value(p, rel, vals[-1], {})
        if _known(info):
            return info
    params = [a.arg for a in enclosing.args.args]
    if value.id not in params:
        return info
    index = params.index(value.id)
    # [(conf.TABLE_USERS_NAME, create_users_table), ...] then create_fn(client, table_name)
    for t in ast.walk(tree):
        if isinstance(t, ast.Tuple) and any(isinstance(e, ast.Name) and e.id == enclosing.name for e in t.elts):
            for e in t.elts:
                if not (isinstance(e, ast.Name) and e.id == enclosing.name):
                    resolved = _table_from_value(p, rel, e, {})
                    if _known(resolved):
                        return resolved
    for site in ast.walk(tree):
        if isinstance(site, ast.Call) and _name_of(site.func) == enclosing.name:
            arg = site.args[index] if index < len(site.args) else next((k.value for k in site.keywords if k.arg == value.id), None)
            if arg is not None:
                resolved = _table_from_value(p, rel, arg, {})
                if _known(resolved):
                    return resolved
    return info


def table_schemas(p: PyProject, schema_files: list[str]) -> list[dict[str, Any]]:
    """Table definitions in schema files: create_table(TableName=...,
    KeySchema=[...], ...) calls, and dict literals with TableName and
    KeySchema (tables = [{...}, ...]; create_table(**config))."""
    out = []
    for rel in schema_files:
        tree = p.trees.get(rel)
        if tree is None:
            continue
        for n in ast.walk(tree):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "create_table":
                kw = {k.arg: k.value for k in n.keywords if k.arg}
            elif isinstance(n, ast.Dict) and all(k is not None for k in n.keys):
                kw = {_const_str(k): v for k, v in zip(n.keys, n.values) if _const_str(k)}
            else:
                continue
            if "TableName" not in kw or "KeySchema" not in kw:
                continue
            try:
                key_schema = ast.literal_eval(kw["KeySchema"])
                attrs = {a["AttributeName"]: a["AttributeType"] for a in ast.literal_eval(kw.get("AttributeDefinitions", ast.List(elts=[])))}
            except (ValueError, TypeError, KeyError):
                continue

            def keys(ks: list) -> dict[str, Any]:
                d: dict[str, Any] = {"partition_key": None, "sort_key": None}
                for k in ks:
                    d["partition_key" if k.get("KeyType") == "HASH" else "sort_key"] = {"name": k.get("AttributeName"), "type": attrs.get(k.get("AttributeName"), "unknown")}
                return d

            item = {"table": _schema_table_name(p, rel, tree, n, kw["TableName"]), **keys(key_schema), "gsis": [],
                    "source": [_src(rel, kw["TableName"].lineno, "TableName"), _src(rel, kw["KeySchema"].lineno, "KeySchema")],
                    "origin": "script"}
            try:
                for g in ast.literal_eval(kw["GlobalSecondaryIndexes"]) if "GlobalSecondaryIndexes" in kw else []:
                    item["gsis"].append({"index_name": g.get("IndexName"), **keys(g.get("KeySchema", [])),
                                         "projection": (g.get("Projection") or {}).get("ProjectionType"),
                                         "source": [_src(rel, kw["GlobalSecondaryIndexes"].lineno, "GlobalSecondaryIndexes")]})
            except (ValueError, TypeError):
                pass
            out.append(item)
    return out
