"""The judge (PIPELINE_V2_PLAN.md §4.1-§4.7): cases written from the fact base
(J1), run on the monolith (J2), reconciled with the fact base (J3), frozen (J6).

This module so far holds the asset format and `check_judge_assets` (N1.1).

Assets, relative to the workspace:

    judge/cases/<endpoint>/<case>.json   one case = a sequence of HTTP steps
    judge/fixtures/<name>.json           {"<monolith table>": [items]}
    judge/faults/<scenario>.json         {description, cases, rules}
    judge/identities.json                user aliases and how the monolith issues them
    judge/run_config.json                optional overrides of how the monolith starts
    judge/uncovered.json                 optional [{ref, reason}]: required coverage
                                         J1 cannot provide, with the reason

<endpoint> is the endpoint's slug: method and path, lower case, every run of
other characters replaced by "-" ("GET /api/books/:id" -> "get-api-books-id").

A case:

    {"id": "cart-add-then-read",                 (default: the file name)
     "endpoint": "POST /api/cart",               the endpoint under test
     "layer": "P",                               P, or R when J1 already expects
                                                 a relaxed comparison (e.g. order)
     "title": "...",
     "fact_refs": ["POST /api/cart#responses[3]", "GET /api/cart#responses[2]"],
     "fixtures": ["books-basic"],                judge/fixtures/<name>.json
     "identity": "u1",                           default for every step; omit or
                                                 null for anonymous
     "steps": [
       {"name": "add", "method": "POST", "path": "/api/cart",
        "body": {"bookId": "book-003", "quantity": 2, "price": 25.0}},
       {"name": "read", "method": "GET", "path": "/api/cart", "read_back": true}],
     "predicted": {"add": {"status": 200},
                   "read": {"status": 200, "body": {"contains": [{"bookId": "book-003"}]}}}}

A step: name, method, path (may carry a query string), query {k: v},
headers {k: v}, and at most one of body (any JSON value, sent as
application/json), form ({k: v}, sent form-encoded) and raw_body (a string
sent as is, with the headers given); identity (an alias, or null for an
anonymous step in a case that has an identity); save {var: "body.a.0.b" |
"headers.<name>" | "status"}; read_back (a GET after a write that reads its
effect back). Strings may use ${var} (saved by an earlier step) and
${alias.var} (saved by that identity's preparation, e.g. ${u1.id}).

`predicted` is what J1 expects from the fact base, one entry per step, in the
v1 expect grammar (status, body, headers). It is only used by J3
reconciliation; the judge's expectation is always the monolith's output.
"""

from __future__ import annotations

import copy
import json
import re
import sys
from pathlib import Path
from typing import Any

import adapter
import verify

JUDGE_DIR = "judge"
# What the monolith did (J2) and the reconciliation (J3): closed to J1, whose
# predictions must come from the fact base alone (coord deny_read "judge_outputs").
MONOLITH_OUTPUTS = ("oracle_expected.json", "fault_baseline_monolith.json", "masks.json", "monolith_calls.json",
                    "coverage_monolith", "reconcile.json", "reconcile_classes.json")
METHODS = ("GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS")
LAYERS = ("P", "R")
FAULT_KINDS = verify.FAULT_KINDS
BODY_OPS = ("equals", "subset", "contains", "not_contains", "has_fields", "types", "length")
HEADER_OPS = ("equals", "contains", "absent", "count")
STEP_KEYS = {"name", "method", "path", "query", "headers", "body", "form", "raw_body", "identity", "save",
             "read_back", "note"}
CASE_KEYS = {"id", "endpoint", "layer", "title", "fact_refs", "fixtures", "identity", "steps", "predicted", "note",
             # J4: the spec issues (judge/spec_issues.json) a white-box case depends on
             "spec_issues"}
RUN_CONFIG_KEYS = {"runtime": str, "entry": str, "args": list, "port_env": str, "env": dict, "ready_path": str,
                   "startup_timeout_s": int, "note": str,
                   # Python monoliths: the interpreter (default: a venv from requirements.txt),
                   # the WSGI app ("module:attr"), Flask's app.debug
                   "python": str, "app": str, "debug": bool,
                   # fact-base table name -> the name the monolith uses (default: placeholders
                   # such as {stage} take the variable's default in the monolith's code)
                   "table_names": dict}
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
VAR_RE = re.compile(r"^[A-Za-z_]\w*$")
ALIAS_RE = re.compile(r"^[a-z][a-z0-9_]*$")
SAVE_PATH_RE = re.compile(r"^(status|body(\.[^.\s]+)*|headers\.[^.\s]+)$")
REF_USE_RE = re.compile(r"\$\{([^}]*)\}")
GLOBAL_REF_RE = re.compile(r"^global_middleware\[(\d+)\](?:\.\S+)?$")
READ_BACK_REF = re.compile(r"^(GET|POST|PUT|DELETE|PATCH) (\S+)#read_back$")
WRITE_OP_RE = re.compile(r"put|update|delete|write", re.I)


class JudgeError(Exception):
    pass


class AdapterMapError(JudgeError):
    """The adapter map is missing or does not pass its static check: the architect's to fix."""


def judge_dir(ws: Path | str) -> Path:
    return Path(ws) / JUDGE_DIR


def endpoint_slug(endpoint: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", endpoint.lower()).strip("-")


def _read(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {"_load_error": f"invalid JSON: {exc}"}


def _rel(ws: Path, p: Path) -> str:
    return str(p.relative_to(ws)).replace("\\", "/")


def load_assets(ws: Path | str) -> dict[str, Any]:
    """Every judge asset as written, with `_file` on cases and faults."""
    ws = Path(ws)
    jd = judge_dir(ws)
    cases = []
    for p in sorted((jd / "cases").glob("*/*.json")):
        c = _read(p)
        if isinstance(c, dict):
            c["_file"] = _rel(ws, p)
            c["_dir"] = p.parent.name
            c.setdefault("id", p.stem)
        else:
            c = {"_file": _rel(ws, p), "_load_error": "a case must be a JSON object"}
        cases.append(c)
    fixtures = {p.stem: _read(p) for p in sorted((jd / "fixtures").glob("*.json"))}
    faults = []
    for p in sorted((jd / "faults").glob("*.json")):
        f = _read(p)
        if isinstance(f, dict):
            f["_file"] = _rel(ws, p)
            f.setdefault("id", p.stem)
        else:
            f = {"_file": _rel(ws, p), "_load_error": "a fault scenario must be a JSON object"}
        faults.append(f)
    opt = {name: (_read(jd / f"{name}.json") if (jd / f"{name}.json").is_file() else None)
           for name in ("identities", "run_config", "uncovered", "spec_issues")}
    return {"cases": cases, "fixtures": fixtures, "faults": faults, **opt}


# ---------------------------------------------------------------------------
# The fact base, as the judge sees it
# ---------------------------------------------------------------------------

class Facts:
    def __init__(self, ws: Path):
        path = ws / "storage" / "analysis_report.json"
        if not path.is_file():
            raise JudgeError(f"{_rel(ws, path)} not found: the judge is written from the fact base (run F1/F2 first)")
        self.report = json.loads(path.read_text(encoding="utf-8"))
        self.entry_points = {f"{e.get('method')} {e.get('path')}": e for e in self.report.get("entry_points") or []}
        self.tables: dict[str, dict[str, Any]] = {}
        for t in (self.report.get("dynamodb_info") or {}).get("table_schemas") or []:
            name = (t.get("table") or {}).get("default_name")
            if not name:
                continue
            keys = {t["partition_key"]["name"]: t["partition_key"].get("type", "S")}
            if t.get("sort_key"):
                keys[t["sort_key"]["name"]] = t["sort_key"].get("type", "S")
            index_keys = {}
            for g in t.get("gsis") or []:
                for k in ("partition_key", "sort_key"):
                    if g.get(k):
                        index_keys[g[k]["name"]] = g[k].get("type", "S")
            self.tables[name] = {"keys": keys, "index_keys": index_keys}
        self.global_middleware = self.report.get("global_middleware") or []
        self.monolith_root = (self.report.get("spec_meta") or {}).get("monolith_root")

    def check_ref(self, ref: str) -> str | None:
        m = GLOBAL_REF_RE.match(ref)
        if m:
            n = len(self.global_middleware)
            return None if int(m.group(1)) < n else f"fact_ref {ref!r}: global_middleware has {n} items"
        m = verify._SPEC_REF_RE.match(ref)
        if not m:
            return (f"fact_ref {ref!r} must look like 'POST /api/orders#responses[2]' (field one of request / "
                    "responses / business_rules / auth / data_access) or 'global_middleware[0]'")
        method, path, field, idx = m.groups()
        ep = self.entry_points.get(f"{method} {path}")
        if ep is None:
            return f"fact_ref {ref!r}: no entry point {method} {path} in the fact base"
        if idx is not None:
            val = ep.get(field)
            if not isinstance(val, list) or int(idx) >= len(val):
                return f"fact_ref {ref!r}: {field} has {len(val) if isinstance(val, list) else 0} items"
        return None

    @staticmethod
    def ref_endpoint(ref: str) -> str | None:
        m = verify._SPEC_REF_RE.match(ref)
        return f"{m.group(1)} {m.group(2)}" if m else None

    def required_responses(self, endpoint: str) -> list[int]:
        """Responses a case must reach without fault injection: everything
        below 500 that is not the outcome of a caught exception."""
        ep = self.entry_points[endpoint]
        return [i for i, r in enumerate(ep.get("responses") or [])
                if isinstance(r, dict) and isinstance(r.get("status"), int) and r["status"] < 500
                and not r.get("in_catch")]

    def optional_responses(self, endpoint: str) -> list[int]:
        req = set(self.required_responses(endpoint))
        return [i for i, _ in enumerate(self.entry_points[endpoint].get("responses") or []) if i not in req]

    def writes(self, endpoint: str) -> bool:
        return any(WRITE_OP_RE.search(str(d.get("operation") or ""))
                   for d in self.entry_points[endpoint].get("data_access") or [] if isinstance(d, dict))


# ---------------------------------------------------------------------------
# check_judge_assets
# ---------------------------------------------------------------------------

def _vars_used(value: Any) -> list[str]:
    if isinstance(value, str):
        return REF_USE_RE.findall(value)
    if isinstance(value, list):
        return [v for x in value for v in _vars_used(x)]
    if isinstance(value, dict):
        return [v for k, x in value.items() for v in _vars_used(k) + _vars_used(x)]
    return []


def _step_path(path: str) -> str:
    """The path of a step for endpoint matching: no query, ${...} as one segment."""
    return REF_USE_RE.sub("x", path.split("?", 1)[0])


def _step_endpoint(step: dict[str, Any], endpoints: list[str]) -> str | None:
    """The endpoint a step calls; a static route wins over a parameterized one."""
    method = str(step.get("method", "")).upper()
    path = _step_path(str(step.get("path", "")))
    hits = [ep for ep in endpoints
            if ep.split(" ", 1)[0] == method and verify._path_match(ep.split(" ", 1)[1], path) is not None]
    return min(hits, key=lambda ep: len(re.findall(r"[:{<]", ep))) if hits else None


def _check_expect(where: str, exp: Any) -> list[str]:
    if not isinstance(exp, dict):
        return [f"{where}: must be an object {{status, body?, headers?}}"]
    errs = []
    if set(exp) - {"status", "body", "headers", "note"}:
        errs.append(f"{where}: unknown keys {sorted(set(exp) - {'status', 'body', 'headers', 'note'})} "
                    "(allowed: status, body, headers, note)")
    if not isinstance(exp.get("status"), int) or isinstance(exp.get("status"), bool):
        errs.append(f"{where}: status (an integer) is required")
    if "body" in exp:
        b = exp["body"]
        if not isinstance(b, dict) or not b or set(b) - set(BODY_OPS):
            errs.append(f"{where}: body must use {'/'.join(BODY_OPS)}")
    if "headers" in exp:
        h = exp["headers"]
        if not isinstance(h, dict) or not h or set(h) - set(HEADER_OPS):
            errs.append(f"{where}: headers must use {'/'.join(HEADER_OPS)}")
    return errs


def _check_save(where: str, save: Any) -> list[str]:
    if not isinstance(save, dict):
        return [f"{where}: save must be an object {{var: \"body.path\" | \"headers.<name>\" | \"status\"}}"]
    errs = []
    for k, v in save.items():
        if not VAR_RE.match(str(k)):
            errs.append(f"{where}: save variable {k!r} must be an identifier")
        if not isinstance(v, str) or not SAVE_PATH_RE.match(v):
            errs.append(f"{where}: save.{k} must be \"status\", \"body\", \"body.<path>\" or \"headers.<name>\", got {v!r}")
    return errs


def _check_request(where: str, step: dict[str, Any], *, allowed: set[str]) -> list[str]:
    errs = []
    extra = set(step) - allowed
    if extra:
        errs.append(f"{where}: unknown keys {sorted(extra)} (allowed: {sorted(allowed)})")
    if str(step.get("method", "")).upper() not in METHODS or not isinstance(step.get("method"), str):
        errs.append(f"{where}: method must be one of {METHODS}")
    if not isinstance(step.get("path"), str) or not step["path"].startswith("/"):
        errs.append(f"{where}: path must be a string starting with '/'")
    for k in ("query", "headers"):
        if k in step and not (isinstance(step[k], dict) and all(isinstance(v, (str, int, float)) and not isinstance(v, bool)
                                                                  for v in step[k].values())):
            errs.append(f"{where}: {k} must map names to strings or numbers")
    bodies = [k for k in ("body", "form", "raw_body") if k in step]
    if len(bodies) > 1:
        errs.append(f"{where}: use at most one of body / form / raw_body, got {bodies}")
    if "form" in step and not (isinstance(step["form"], dict)
                               and all(isinstance(v, (str, int, float)) and not isinstance(v, bool) for v in step["form"].values())):
        errs.append(f"{where}: form must map field names to strings or numbers")
    if "raw_body" in step and not isinstance(step["raw_body"], str):
        errs.append(f"{where}: raw_body must be a string (sent as is)")
    if "save" in step:
        errs += _check_save(where, step["save"])
    return errs


def _identity_vars(identities: dict[str, Any]) -> dict[str, set[str]]:
    out = {}
    for alias, spec in identities.items():
        mono = spec.get("monolith") if isinstance(spec, dict) else None
        if not isinstance(mono, dict):
            continue
        names = {"id"}
        if "sign" in mono:
            names.add("token")
        for s in mono.get("steps") or []:
            if isinstance(s, dict) and isinstance(s.get("save"), dict):
                names.update(s["save"])
        out[alias] = names
    return out


def _check_identities(facts: Facts, identities: Any) -> list[str]:
    where = "judge/identities.json"
    if not isinstance(identities, dict) or "_load_error" in identities:
        return [f"{where}: {identities.get('_load_error') if isinstance(identities, dict) else 'must be an object {alias: {...}}'}"]
    errs = []
    endpoints = list(facts.entry_points)
    for alias, spec in identities.items():
        w = f"{where}: {alias}"
        if not ALIAS_RE.match(alias):
            errs.append(f"{w}: an alias is lower case letters, digits and _ (starting with a letter)")
        if not isinstance(spec, dict) or set(spec) - {"monolith", "target", "note"}:
            errs.append(f"{w}: must be an object with monolith (and later target, from the adapter map)")
            continue
        mono = spec.get("monolith")
        # J1 prepares identities through the monolith's own endpoints (steps); a
        # `sign` entry is something only a person sets up, so the messages J1
        # reads name steps only.
        if not isinstance(mono, dict):
            errs.append(f"{w}: monolith {{steps, header}} is required (steps call the monolith, e.g. register)")
            continue
        if ("steps" in mono) == ("sign" in mono):
            errs.append(f"{w}.monolith: steps is required: preparation requests to the monolith (e.g. register) "
                        "that save the user's id")
            continue
        if set(mono) - {"steps", "sign", "id", "header", "note"}:
            errs.append(f"{w}.monolith: unknown keys {sorted(set(mono) - {'steps', 'sign', 'id', 'header', 'note'})}")
        defined: set[str] = set()
        if "steps" in mono:
            steps = mono["steps"]
            if not isinstance(steps, list) or not steps:
                errs.append(f"{w}.monolith.steps must be a non-empty list of requests")
                steps = []
            for i, s in enumerate(steps):
                ws_ = f"{w}.monolith.steps[{i}]"
                if not isinstance(s, dict):
                    errs.append(f"{ws_}: must be an object")
                    continue
                errs += _check_request(ws_, s, allowed=STEP_KEYS - {"identity", "read_back"})
                for v in _vars_used({k: s.get(k) for k in ("path", "query", "headers", "body", "form", "raw_body")}):
                    if v not in defined:
                        errs.append(f"{ws_}: ${{{v}}} is not saved by an earlier preparation step")
                if isinstance(s.get("save"), dict):
                    defined.update(s["save"])
                if isinstance(s.get("method"), str) and isinstance(s.get("path"), str) \
                        and not _step_endpoint(s, endpoints):
                    errs.append(f"{ws_}: {s['method']} {s['path']} is not an endpoint of the fact base")
            if "id" not in defined:
                errs.append(f"{w}.monolith: a preparation step must save id (the user id the monolith assigns, "
                            "bound to the symbol <id:" + alias + ">)")
            if "id" in mono:
                errs.append(f"{w}.monolith: id comes from save with steps; a literal id is for sign")
        else:
            sign = mono["sign"]
            if not isinstance(sign, dict) or set(sign) - {"alg", "secret", "claims", "expires_in_s"} \
                    or not isinstance(sign.get("secret"), str) or not isinstance(sign.get("claims"), dict):
                errs.append(f"{w}.monolith.sign must be {{secret, claims, alg?: HS256, expires_in_s?}}")
            elif sign.get("alg", "HS256") != "HS256":
                errs.append(f"{w}.monolith.sign.alg: only HS256 is supported")
            if not isinstance(mono.get("id"), str) or not mono.get("id"):
                errs.append(f"{w}.monolith: sign needs id, the user id the token stands for")
            defined = {"id", "token"}
        header = mono.get("header")
        if not isinstance(header, dict) or not header or not all(isinstance(v, str) for v in header.values()):
            errs.append(f"{w}.monolith.header must map header names to templates, e.g. "
                        "{\"Authorization\": \"Bearer ${token}\"}")
        else:
            for v in _vars_used(header):
                if v not in defined:
                    errs.append(f"{w}.monolith.header: ${{{v}}} is not saved by the preparation")
    return errs


def _check_fixture(facts: Facts, name: str, data: Any) -> list[str]:
    where = f"judge/fixtures/{name}.json"
    if not isinstance(data, dict) or "_load_error" in data:
        return [f"{where}: {data.get('_load_error') if isinstance(data, dict) else 'must be an object {table: [items]}'}"]
    errs = []
    for table, items in data.items():
        schema = facts.tables.get(table)
        if schema is None:
            errs.append(f"{where}: {table!r} is not a monolith table (fixtures use the monolith's tables: "
                        f"{sorted(facts.tables)})")
            continue
        if not isinstance(items, list) or not all(isinstance(i, dict) for i in items):
            errs.append(f"{where}: {table} must be a list of items")
            continue
        seen = set()
        for n, item in enumerate(items):
            for k, ty in schema["keys"].items():
                if k not in item:
                    errs.append(f"{where}: {table}[{n}] has no key attribute {k!r}")
                elif not _type_ok(item[k], ty):
                    errs.append(f"{where}: {table}[{n}].{k} must be {'a string' if ty == 'S' else 'a number'} (key type {ty})")
            for k, ty in schema["index_keys"].items():
                if k in item and not _type_ok(item[k], ty):
                    errs.append(f"{where}: {table}[{n}].{k} is an index key of type {ty}; DynamoDB rejects other types")
            key = json.dumps([item.get(k) for k in schema["keys"]], default=str)
            if key in seen:
                errs.append(f"{where}: {table}[{n}] repeats the key of an earlier item")
            seen.add(key)
    return errs


def _type_ok(v: Any, ty: str) -> bool:
    if ty == "S":
        return isinstance(v, str) and v != ""
    if ty == "N":
        return isinstance(v, (int, float)) and not isinstance(v, bool)
    return True


def _check_case(facts: Facts, case: dict[str, Any], fixtures: dict[str, Any], id_vars: dict[str, set[str]]
                ) -> tuple[list[str], set[str]]:
    """Errors of one case, and the endpoints its steps call."""
    where = case.get("_file", case.get("id", "?"))
    if "_load_error" in case:
        return [f"{where}: {case['_load_error']}"], set()
    errs = []
    extra = set(case) - CASE_KEYS - {"_file", "_dir"}
    if extra:
        errs.append(f"{where}: unknown keys {sorted(extra)} (allowed: {sorted(CASE_KEYS)})")
    if not isinstance(case.get("id"), str) or not NAME_RE.match(case["id"]):
        errs.append(f"{where}: id must be letters, digits, '.', '_' or '-'")
    endpoints = list(facts.entry_points)
    ep = case.get("endpoint")
    if ep not in facts.entry_points:
        errs.append(f"{where}: endpoint {ep!r} is not an entry point of the fact base ({len(endpoints)} known, "
                    f"e.g. {endpoints[:3]})")
    elif case.get("_dir") != endpoint_slug(ep):
        errs.append(f"{where}: a case of {ep} belongs in judge/cases/{endpoint_slug(ep)}/")
    if case.get("layer") not in LAYERS:
        errs.append(f"{where}: layer must be P or R")
    refs = case.get("fact_refs")
    if not isinstance(refs, list) or not refs or not all(isinstance(r, str) for r in refs):
        errs.append(f"{where}: fact_refs must be a non-empty list of fact references")
        refs = []
    errs += [f"{where}: {e}" for r in refs if (e := facts.check_ref(r))]
    fx = case.get("fixtures", [])
    if not isinstance(fx, list) or not all(isinstance(f, str) for f in fx):
        errs.append(f"{where}: fixtures must be a list of fixture names (judge/fixtures/<name>.json)")
    else:
        errs += [f"{where}: fixture {f!r} not found (judge/fixtures/{f}.json)" for f in fx if f not in fixtures]
    default_identity = case.get("identity")
    if default_identity is not None and default_identity not in id_vars:
        errs.append(f"{where}: identity {default_identity!r} is not defined in judge/identities.json")
    steps = case.get("steps")
    if not isinstance(steps, list) or not steps:
        return errs + [f"{where}: steps must be a non-empty list"], set()
    defined: set[str] = set()
    names = []
    called: set[str] = set()
    wrote = False
    for i, s in enumerate(steps):
        ws_ = f"{where}: steps[{i}]"
        if not isinstance(s, dict):
            errs.append(f"{ws_}: must be an object")
            continue
        name = s.get("name")
        if not isinstance(name, str) or not NAME_RE.match(name):
            errs.append(f"{ws_}: name is required (letters, digits, '.', '_' or '-'); predicted is keyed by it")
        elif name in names:
            errs.append(f"{ws_}: name {name!r} is used by an earlier step")
        names.append(name)
        errs += _check_request(ws_, s, allowed=STEP_KEYS)
        if "identity" in s and s["identity"] is not None and s["identity"] not in id_vars:
            errs.append(f"{ws_}: identity {s['identity']!r} is not defined in judge/identities.json")
        for v in _vars_used({k: s.get(k) for k in ("path", "query", "headers", "body", "form", "raw_body")}):
            alias, dot, var = v.partition(".")
            if dot:
                if alias not in id_vars:
                    errs.append(f"{ws_}: ${{{v}}}: {alias!r} is not an identity")
                elif var not in id_vars[alias]:
                    errs.append(f"{ws_}: ${{{v}}}: identity {alias} saves {sorted(id_vars[alias])}")
            elif v not in defined:
                errs.append(f"{ws_}: ${{{v}}} is not saved by an earlier step")
        if isinstance(s.get("save"), dict):
            defined.update(s["save"])
        if isinstance(s.get("method"), str) and isinstance(s.get("path"), str):
            hit = _step_endpoint(s, endpoints)
            if hit:
                called.add(hit)
            else:
                errs.append(f"{ws_}: {s['method']} {s['path']} does not match an endpoint of the fact base")
            if s.get("read_back"):
                if s["method"].upper() != "GET":
                    errs.append(f"{ws_}: read_back is for a GET that reads a write's effect back")
                elif not wrote:
                    errs.append(f"{ws_}: read_back needs an earlier step that writes")
            if s["method"].upper() not in ("GET", "HEAD", "OPTIONS"):
                wrote = True
        if "read_back" in s and not isinstance(s["read_back"], bool):
            errs.append(f"{ws_}: read_back must be true or false")
    if ep in facts.entry_points and ep not in called:
        errs.append(f"{where}: no step calls the endpoint under test {ep}")
    for r in refs:
        rep = facts.ref_endpoint(r)
        if rep and rep in facts.entry_points and rep not in called:
            errs.append(f"{where}: fact_ref {r!r} cites {rep}, which no step calls")
    pred = case.get("predicted")
    if not isinstance(pred, dict):
        errs.append(f"{where}: predicted must be an object {{step name: {{status, body?, headers?}}}}")
    else:
        for k in set(pred) - set(names):
            errs.append(f"{where}: predicted.{k} names no step")
        for n in names:
            if isinstance(n, str) and n not in pred:
                errs.append(f"{where}: predicted has no entry for step {n!r} (at least its status)")
            elif isinstance(n, str):
                errs += _check_expect(f"{where}: predicted.{n}", pred[n])
    return errs, called


def _check_fault(facts: Facts, f: dict[str, Any], case_ids: set[str]) -> list[str]:
    where = f.get("_file", f.get("id", "?"))
    if "_load_error" in f:
        return [f"{where}: {f['_load_error']}"]
    errs = []
    if set(f) - {"id", "description", "cases", "rules", "note", "_file"}:
        errs.append(f"{where}: unknown keys {sorted(set(f) - {'id', 'description', 'cases', 'rules', 'note', '_file'})}")
    if not isinstance(f.get("description"), str) or not f["description"]:
        errs.append(f"{where}: description is required")
    cases = f.get("cases")
    if not isinstance(cases, list) or not cases:
        errs.append(f"{where}: cases must be a non-empty list of case ids")
    else:
        errs += [f"{where}: case {c!r} not found" for c in cases if c not in case_ids]
    rules = f.get("rules")
    if not isinstance(rules, list) or not rules:
        return errs + [f"{where}: rules must be a non-empty list"]
    for i, r in enumerate(rules):
        w = f"{where}: rules[{i}]"
        if not isinstance(r, dict) or r.get("kind") not in FAULT_KINDS:
            errs.append(f"{w}.kind must be one of {FAULT_KINDS}")
            continue
        if set(r) - {"match", "nth", "kind"}:
            errs.append(f"{w}: unknown keys {sorted(set(r) - {'match', 'nth', 'kind'})}")
        nth = r.get("nth", "all")
        if not (nth == "all" or (isinstance(nth, int) and not isinstance(nth, bool) and nth >= 1)
                or (isinstance(nth, list) and nth and all(isinstance(x, int) and x >= 1 for x in nth))):
            errs.append(f"{w}.nth must be 'all', a positive integer or a list of them")
        m = r.get("match") or {}
        if not isinstance(m, dict) or set(m) - {"op", "table", "queue", "item"}:
            errs.append(f"{w}.match may only have op / table / queue / item")
            continue
        # The same scenario runs on the monolith and on the target, whose
        # call patterns differ: target the effect, not the call sequence.
        if m.get("op") in verify.READ_OPS:
            errs.append(f"{w}.match.op {m['op']!r} names a read operation; target the read of a table "
                        "(op omitted or \"@read\") -- the target may read it another way")
        if nth != "all" and "item" not in m:
            errs.append(f"{w}: nth other than 'all' needs match.item (nth then counts operations on that item)")
        if "item" in m and (not isinstance(m["item"], dict) or not m["item"]):
            errs.append(f"{w}.match.item must be a non-empty object of attribute values")
        if m.get("table") and m["table"] not in facts.tables:
            errs.append(f"{w}.match.table {m['table']!r} is not a monolith table ({sorted(facts.tables)})")
    return errs


def _check_run_config(facts: Facts, rc: Any) -> list[str]:
    where = "judge/run_config.json"
    if not isinstance(rc, dict) or "_load_error" in rc:
        return [f"{where}: {rc.get('_load_error') if isinstance(rc, dict) else 'must be an object'}"]
    errs = []
    for k, v in rc.items():
        ty = RUN_CONFIG_KEYS.get(k)
        if ty is None:
            errs.append(f"{where}: unknown key {k!r} (allowed: {sorted(RUN_CONFIG_KEYS)})")
        elif not isinstance(v, ty) or (isinstance(v, bool) and ty is not bool):
            errs.append(f"{where}: {k} must be {ty.__name__}")
    for k in ("env", "table_names"):
        if isinstance(rc.get(k), dict) and not all(isinstance(v, str) for v in rc[k].values()):
            errs.append(f"{where}: {k} values must be strings")
    if isinstance(rc.get("table_names"), dict):
        errs += [f"{where}: table_names: {t!r} is not a table of the fact base" for t in rc["table_names"] if t not in facts.tables]
    if rc.get("runtime") not in (None, "node", "python"):
        errs.append(f"{where}: runtime must be node or python")
    if isinstance(rc.get("entry"), str) and facts.monolith_root \
            and not (Path(facts.monolith_root) / rc["entry"]).is_file():
        errs.append(f"{where}: entry {rc['entry']!r} is not a file of the monolith")
    return errs


SPEC_ISSUE_KINDS = ("missing", "wrong")


def _check_spec_issues(facts: Facts, issues: Any) -> tuple[list[str], set[str]]:
    """judge/spec_issues.json (J4): rules the monolith's code has and the fact base
    lacks (missing) or states otherwise (wrong); F2 puts them into the facts."""
    where = "judge/spec_issues.json"
    if not isinstance(issues, list):
        return [f"{where}: must be a list of {{id, endpoint, kind, rule, evidence, status?}}"], set()
    errs, ids = [], set()
    for i, x in enumerate(issues):
        w = f"{where}[{i}]"
        if not isinstance(x, dict):
            errs.append(f"{w}: must be an object")
            continue
        if set(x) - {"id", "endpoint", "kind", "rule", "evidence", "status", "cases", "note"}:
            errs.append(f"{w}: unknown keys {sorted(set(x) - {'id', 'endpoint', 'kind', 'rule', 'evidence', 'status', 'cases', 'note'})}")
        if not isinstance(x.get("id"), str) or not NAME_RE.match(x["id"]) or x["id"] in ids:
            errs.append(f"{w}: id must be a unique name (e.g. \"J4-1\")")
        else:
            ids.add(x["id"])
        if x.get("endpoint") not in facts.entry_points:
            errs.append(f"{w}: endpoint {x.get('endpoint')!r} is not an entry point of the fact base")
        if x.get("kind") not in SPEC_ISSUE_KINDS:
            errs.append(f"{w}: kind must be one of {SPEC_ISSUE_KINDS}")
        if not str(x.get("rule") or "").strip():
            errs.append(f"{w}: rule (what the code does) is required")
        ev = x.get("evidence")
        if not isinstance(ev, list) or not ev or not all(isinstance(e, dict) and isinstance(e.get("file"), str)
                                                         and isinstance(e.get("line"), int) for e in ev):
            errs.append(f"{w}: evidence must be a non-empty list of {{file, line}} in the monolith's code")
        if x.get("status", "open") not in ("open", "routed", "resolved"):
            errs.append(f"{w}: status is open, routed or resolved")
    return errs, ids


def _mutation_summary(jd: Path) -> dict[str, Any] | None:
    """The J5 result frozen into the manifest (judge/judge_validation.json)."""
    v = _read(jd / "judge_validation.json") if (jd / "judge_validation.json").is_file() else None
    if not isinstance(v, dict) or "summary" not in v:
        return None
    s = v["summary"]
    return {k: s.get(k) for k in ("score", "mutants", "killed", "survived", "not_covered", "trace_only", "invalid",
                                  "equivalent", "reverse", "reverse_ok")}


def check_cases_package(ws: Path | str, package: str, functions: list[str]) -> dict[str, Any]:
    """J1:<pkg> (v3 M4/W4.2): one package's cases are well formed and cover its
    functions. Identities, fixtures, faults and the whole-asset coverage are
    J1-shared's check_judge_assets (it runs after every package)."""
    ws = Path(ws)
    root = judge_dir(ws) / "cases"
    wanted = {str(f) for f in functions}
    errs: list[str] = []
    ids: dict[str, str] = {}
    count = 0
    per_fn: dict[str, int] = {}
    for path in sorted(root.rglob("*.json")):
        where = path.relative_to(ws).as_posix()
        try:
            case = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            errs.append(f"{where}: invalid JSON: {exc}")
            continue
        if not isinstance(case, dict):
            errs.append(f"{where}: a case must be a JSON object")
            continue
        cid = case.get("id")
        if isinstance(cid, str) and cid:
            if cid in ids:
                errs.append(f"{where}: id {cid!r} is also used by {ids[cid]}")
            ids.setdefault(cid, where)
        fn = case.get("function")
        mine = path.parent.name == package
        if fn not in wanted:
            if mine:
                errs.append(f"{where}: function {fn!r} is not in this package ({sorted(wanted)})")
            continue
        count += 1
        if not isinstance(cid, str) or not cid:
            errs.append(f"{where}: id is required")
        if not isinstance(case.get("spec_refs"), list) or not case.get("spec_refs"):
            errs.append(f"{where}: spec_refs must be a non-empty list")
        per_fn[fn] = per_fn.get(fn, 0) + 1
    missing = sorted(f for f in wanted if not per_fn.get(f))
    if missing:
        errs.append(f"no case yet for {missing} (each function of the package needs at least one)")
    return {"status": "PASS" if not errs else "FAIL", "errors": errs[:60], "cases": count, "package": package}


def check_judge_assets(ws: Path | str) -> dict[str, Any]:
    """J1's completion check: format, references, and coverage of the fact base."""
    ws = Path(ws)
    facts = Facts(ws)
    a = load_assets(ws)
    errs: list[str] = []
    warnings: list[str] = []
    if not a["cases"]:
        return {"status": "FAIL", "errors": ["judge/cases/ has no cases (judge/cases/<endpoint>/<case>.json)"],
                "warnings": [], "summary": {}}

    identities = a["identities"] if a["identities"] is not None else {}
    if a["identities"] is not None:
        errs += _check_identities(facts, identities)
    id_vars = _identity_vars(identities) if isinstance(identities, dict) else {}
    for name, data in a["fixtures"].items():
        errs += _check_fixture(facts, name, data)

    ids: dict[str, str] = {}
    called_by: dict[str, set[str]] = {}
    for c in a["cases"]:
        e, called = _check_case(facts, c, a["fixtures"], id_vars)
        errs += e
        cid = c.get("id")
        if isinstance(cid, str):
            if cid in ids:
                errs.append(f"{c.get('_file')}: id {cid!r} is also used by {ids[cid]}")
            ids.setdefault(cid, c.get("_file", "?"))
            called_by[cid] = called
    for f in a["faults"]:
        errs += _check_fault(facts, f, set(ids))
    if a["run_config"] is not None:
        errs += _check_run_config(facts, a["run_config"])
    issue_ids = set()
    if a.get("spec_issues") is not None:
        e, issue_ids = _check_spec_issues(facts, a["spec_issues"])
        errs += e
    for c in a["cases"]:
        si = c.get("spec_issues")
        if si is not None and not (isinstance(si, list) and all(isinstance(x, str) for x in si)):
            errs.append(f"{c.get('_file')}: spec_issues must be a list of issue ids (judge/spec_issues.json)")
        elif si:
            errs += [f"{c.get('_file')}: spec issue {x!r} is not in judge/spec_issues.json" for x in si if x not in issue_ids]
    if (judge_dir(ws) / "mask_rules.json").is_file():
        errs += _check_mask_rules(_read(judge_dir(ws) / "mask_rules.json"), set(ids))

    # Exemptions J1 states for coverage it cannot provide.
    exempt: dict[str, str] = {}
    unc = a["uncovered"]
    if unc is not None:
        if not isinstance(unc, list):
            errs.append("judge/uncovered.json must be a list of {ref, reason}")
        else:
            for i, u in enumerate(unc):
                w = f"judge/uncovered.json[{i}]"
                if not isinstance(u, dict) or not isinstance(u.get("ref"), str) or not str(u.get("reason") or "").strip():
                    errs.append(f"{w}: needs ref and a non-empty reason")
                    continue
                ref = u["ref"]
                rb = READ_BACK_REF.match(ref)
                if rb:
                    if f"{rb.group(1)} {rb.group(2)}" not in facts.entry_points:
                        errs.append(f"{w}: no entry point {rb.group(1)} {rb.group(2)}")
                elif (e := facts.check_ref(ref)) or not re.search(r"#responses\[\d+\]$", ref):
                    errs.append(f"{w}: ref must be '<METHOD> <path>#responses[i]' or '<METHOD> <path>#read_back'"
                                + (f" ({e})" if e else ""))
                exempt[ref] = u["reason"]

    # Coverage of the fact base.
    cases = [c for c in a["cases"] if "_load_error" not in c]
    cited = {r for c in cases for r in c.get("fact_refs") or [] if isinstance(r, str)}
    by_endpoint: dict[str, int] = {ep: 0 for ep in facts.entry_points}
    for c in cases:
        if c.get("endpoint") in by_endpoint:
            by_endpoint[c["endpoint"]] += 1
    missing_required, optional_open = [], []
    req_total = req_done = opt_total = opt_done = 0
    rules_total = rules_cited = 0
    for ep, n in by_endpoint.items():
        if n == 0:
            errs.append(f"{ep}: no case (every endpoint needs at least one, in judge/cases/{endpoint_slug(ep)}/)")
        for i in facts.required_responses(ep):
            ref = f"{ep}#responses[{i}]"
            req_total += 1
            if ref in cited:
                req_done += 1
                if ref in exempt:
                    warnings.append(f"judge/uncovered.json lists {ref}, which a case cites")
            elif ref not in exempt:
                missing_required.append(ref)
        for i in facts.optional_responses(ep):
            opt_total += 1
            if f"{ep}#responses[{i}]" in cited:
                opt_done += 1
            else:
                optional_open.append(f"{ep}#responses[{i}]")
        nrules = len(facts.entry_points[ep].get("business_rules") or [])
        rules_total += nrules
        rules_cited += sum(1 for j in range(nrules) if f"{ep}#business_rules[{j}]" in cited)
        if facts.writes(ep):
            has_rb = any(c.get("endpoint") == ep and any(isinstance(s, dict) and s.get("read_back")
                                                         for s in c.get("steps") or []) for c in cases)
            if not has_rb and f"{ep}#read_back" not in exempt:
                errs.append(f"{ep} writes ({', '.join(str(d.get('operation')) for d in facts.entry_points[ep].get('data_access') or [])}) "
                            "but none of its cases reads the effect back (a later GET step with read_back: true), and "
                            f"judge/uncovered.json does not list {ep}#read_back")
    for ref in missing_required:
        r = facts.entry_points[ref.split("#")[0]]["responses"][int(ref.rsplit("[", 1)[1][:-1])]
        errs.append(f"{ref} ({r.get('status')}: {str(r.get('when') or '')[:80]}) is not cited by any case: write a case "
                    "whose steps reach it and cite it in fact_refs, or list it in judge/uncovered.json with the reason")
    referenced_fixtures = {f for c in cases for f in c.get("fixtures") or [] if isinstance(f, str)}
    warnings += [f"judge/fixtures/{n}.json is not used by any case" for n in sorted(set(a["fixtures"]) - referenced_fixtures)]
    in_faults = {c for f in a["faults"] for c in f.get("cases") or [] if isinstance(c, str)}

    summary = {
        "cases": len(a["cases"]),
        "layers": {lay: sum(1 for c in cases if c.get("layer") == lay) for lay in LAYERS},
        "endpoints": by_endpoint,
        "required_responses": f"{req_done}/{req_total} cited" + (f", {len(exempt)} exempted" if exempt else ""),
        "optional_responses": f"{opt_done}/{opt_total} cited (5xx and caught exceptions; reached with fault scenarios)",
        "optional_open": optional_open,
        "business_rules": f"{rules_cited}/{rules_total} cited",
        "read_back_cases": sum(1 for c in cases if any(isinstance(s, dict) and s.get("read_back") for s in c.get("steps") or [])),
        "fixtures": len(a["fixtures"]),
        "fault_scenarios": len(a["faults"]),
        "cases_in_fault_scenarios": len(in_faults),
        "identities": sorted(identities) if isinstance(identities, dict) else [],
        "exempted": exempt,
    }
    return {"status": "PASS" if not errs else "FAIL", "errors": errs, "warnings": warnings, "summary": summary}


# ---------------------------------------------------------------------------
# Symbolic masks (§4.4, V2P-05)
# ---------------------------------------------------------------------------
#
# Values that differ between two passes over the same case are not dropped:
# each becomes a symbol, numbered in order of appearance (<uuid#1>,
# <token#2>, <time#3>, ...), and the same value wherever it appears -- in a
# response, inside a longer string, in a table -- gets the same symbol. An
# identity's values are named after it (<id:u1>, <token:u1>), so "the cart's
# customerId is this user's id" holds on both sides. `unify` checks a later
# observation against such a template: a symbol matches any value, but every
# occurrence of one symbol must be the same value -- for identifiers (uuid,
# hex, other values, an identity's id). Times, tokens and hashes are "free":
# each occurrence only says "a volatile value here", because two of them can
# be equal by coincidence (a refresh issued within the same second as the
# login returns the very same JWT; createdAt == updatedAt).
#
# A stack trace in a response (V2P-18) is the same in every pass, yet it is the
# monolith's own file paths and lines, which no target can repeat: it is masked
# as a free symbol too, and the target only needs a string in that place.

SYMBOL_PAT = r"<(?:[a-z]+#\d+|[A-Za-z_]\w*:[a-z][a-z0-9_]*)>"
SYMBOL_RE = re.compile(SYMBOL_PAT)
WHOLE_SYMBOL_RE = re.compile(rf"^{SYMBOL_PAT}$")
VOLATILE_PATTERNS = (
    ("token", r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"),
    ("hash", r"\$2[aby]?\$\d{2}\$[./A-Za-z0-9]{53}"),
    ("uuid", r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"),
    ("time", r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?"),
    ("hex", r"(?<![0-9A-Za-z])[0-9a-f]{16,}(?![0-9A-Za-z])"),
    ("num", r"(?<!\d)\d{9,}(?!\d)"),
)
TOKEN_RE = re.compile("|".join(f"(?P<{n}>{p})" for n, p in VOLATILE_PATTERNS))
FREE_CLASSES = frozenset({"time", "token", "hash", "num", "trace"})
STACK_RE = re.compile(r"\n\s+at \S|^Traceback \(most recent call last\):\n")


def free_symbol(symbol: str) -> bool:
    """A symbol whose occurrences need not be equal (see above)."""
    m = re.match(r"^<([a-z]+)#\d+>$", symbol)
    if m:
        return m.group(1) in FREE_CLASSES
    m = re.match(r"^<(\w+):[a-z][a-z0-9_]*>$", symbol)
    return bool(m) and m.group(1) != "id"
# Framework headers with no behavioural meaning (V2P-03): never compared.
T_HEADERS = frozenset({"date", "connection", "keep-alive", "transfer-encoding", "content-length", "etag", "x-powered-by"})


def _jkey(v: Any) -> str:
    return json.dumps(v, sort_keys=True, ensure_ascii=False)


def _scalar(v: Any) -> bool:
    return v is None or isinstance(v, (str, int, float, bool))


def _classify(v: Any) -> str:
    if isinstance(v, bool):
        return "v"
    if isinstance(v, (int, float)):
        return "time" if 1e9 <= v < 1e10 or 1e12 <= v < 1e13 else "v"
    if isinstance(v, str):
        m = TOKEN_RE.fullmatch(v)
        return m.lastgroup if m else "v"
    return "v"


class Symbolizer:
    """Builds the template of one unit from two passes (a, b)."""

    def __init__(self, forced: list[tuple[re.Pattern[str], str]] | None = None) -> None:
        # judge/mask_rules.json: paths masked even when both passes agree (J3 mask_gap)
        self.forced = forced or []
        self.pairs: dict[tuple[str, str], str] = {}
        self.by_a: dict[str, str] = {}
        self.by_b: dict[str, str] = {}
        self.n = 0
        self.symbols: dict[str, dict[str, Any]] = {}
        self.positions: list[list[str]] = []
        self.unstable: list[dict[str, Any]] = []
        # Lists the monolith returns in a different order from pass to pass (sorted
        # by a random key, e.g. two orders by their uuid): compared as multisets.
        self.unordered: list[str] = []

    def seed(self, symbol: str, a: Any, b: Any, cls: str = "identity") -> None:
        ka, kb = _jkey(a), _jkey(b)
        self.pairs[(ka, kb)] = symbol
        self.by_a[ka] = symbol
        self.by_b[kb] = symbol
        self.symbols[symbol] = {"class": cls, "first": None, "type": verify._type_of(a)}

    def _pair(self, a: Any, b: Any, path: str, cls: str | None = None) -> str | None:
        ka, kb = _jkey(a), _jkey(b)
        s = self.pairs.get((ka, kb))
        if s:
            return s
        self.n += 1
        cls = cls or _classify(a)
        s = f"<{cls}#{self.n}>"
        self.pairs[(ka, kb)] = s
        # the JSON type the symbol stands for (a number becomes a symbol string)
        self.symbols[s] = {"class": cls, "first": path, "type": verify._type_of(a)}
        if ka in self.by_a or kb in self.by_b:
            # In one pass this value equals another volatile value, in the
            # other it does not (two tokens issued within the same second):
            # the equality is a coincidence, so the symbol is not tied to it.
            self.symbols[s]["note"] = "equals another volatile value in one pass only"
        else:
            self.by_a[ka] = s
            self.by_b[kb] = s
        return s

    def _note(self, path: str, symbol: str) -> str:
        self.positions.append([path, symbol])
        if self.symbols[symbol]["first"] is None:
            self.symbols[symbol]["first"] = path
        return symbol

    def _embed(self, a: str, b: str, path: str) -> str | None:
        """A template string for two strings that differ only in volatile parts."""
        ta, tb = a, b
        for (ka, kb), sym in sorted(self.pairs.items(), key=lambda kv: -len(kv[0][0])):
            va, vb = json.loads(ka), json.loads(kb)
            if isinstance(va, str) and isinstance(vb, str) and len(va) >= 6 and va in ta and vb in tb:
                ta, tb = ta.replace(va, sym), tb.replace(vb, sym)
        if ta == tb:
            return ta if ta != a else None
        ma, mb = list(TOKEN_RE.finditer(ta)), list(TOKEN_RE.finditer(tb))
        if not ma or len(ma) != len(mb):
            return None
        out_a, out_b, ia, ib = [], [], 0, 0
        for x, y in zip(ma, mb):
            out_a.append(ta[ia:x.start()])
            out_b.append(tb[ib:y.start()])
            if x.group() == y.group():
                out_a.append(x.group())
                out_b.append(y.group())
            else:
                sym = self._pair(x.group(), y.group(), path, x.lastgroup)
                if sym is None:
                    return None
                out_a.append(sym)
                out_b.append(sym)
            ia, ib = x.end(), y.end()
        out_a.append(ta[ia:])
        out_b.append(tb[ib:])
        t = "".join(out_a)
        return t if t == "".join(out_b) else None

    def walk(self, a: Any, b: Any, path: str = "") -> Any:
        if isinstance(a, dict) and isinstance(b, dict):
            if set(a) != set(b):
                self.unstable.append({"path": path, "why": "keys differ between passes",
                                      "only_first": sorted(set(a) - set(b)), "only_second": sorted(set(b) - set(a))})
                return a
            return {k: self.walk(a[k], b[k], f"{path}.{k}" if path else k) for k in a}
        if isinstance(a, list) and isinstance(b, list):
            if len(a) != len(b):
                self.unstable.append({"path": path, "why": f"{len(a)} vs {len(b)} items between passes"})
                return a
            before = copy.deepcopy(self.__dict__)
            out = [self.walk(x, y, f"{path}[{i}]") for i, (x, y) in enumerate(zip(a, b))]
            if len(self.unstable) == len(before["unstable"]) or len(a) < 2:
                if _sorted_by_volatile_id(a, out):
                    self.unordered.append(path)
                return out
            ordered = self.__dict__
            self.__dict__ = copy.deepcopy(before)
            perm = self._pairing(a, b, path)
            if perm is not None:
                paired = [self.walk(a[i], b[j], f"{path}[{i}]") for i, j in enumerate(perm)]
                if len(self.unstable) == len(before["unstable"]):
                    self.unordered.append(path)
                    return paired
            self.__dict__ = ordered  # the order really differs in content: report it
            return out
        if _scalar(a) and _scalar(b):
            seeded = self.pairs.get((_jkey(a), _jkey(b)))
            if seeded:
                return self._note(path, seeded)
            forced = next((cls for rx, cls in self.forced if rx.match(path)), None)
            if forced is not None and a is not None and b is not None:
                return self._note(path, self._pair(a, b, path, forced))
            if isinstance(a, str) and isinstance(b, str) and STACK_RE.search(a) and STACK_RE.search(b):
                return self._note(path, self._pair(a, b, path, "trace"))
            if _jkey(a) == _jkey(b):
                return a
            if isinstance(a, str) and isinstance(b, str):
                t = self._embed(a, b, path)
                if t is not None:
                    for sym in SYMBOL_RE.findall(t):
                        self._note(path, sym)
                    return t
            numbers = all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (a, b))
            if type(a) is type(b) or numbers:
                s = self._pair(a, b, path)
                if s:
                    return self._note(path, s)
        self.unstable.append({"path": path, "why": "differs between passes and cannot be masked",
                              "first": a, "second": b})
        return a

    def _pairing(self, a: list[Any], b: list[Any], path: str) -> list[int] | None:
        """For each element of a, an element of b it can be masked against (each used once)."""
        remaining = list(range(len(b)))
        perm = []
        for i, x in enumerate(a):
            found = None
            for j in remaining:
                state = copy.deepcopy(self.__dict__)
                n = len(self.unstable)
                self.walk(x, b[j], f"{path}[{i}]")
                ok = len(self.unstable) == n
                self.__dict__ = state
                if ok:
                    found = j
                    break
            if found is None:
                return None
            perm.append(found)
            remaining.remove(found)
        return perm

    def known(self, side: str) -> dict[str, str]:
        return self.by_a if side == "a" else self.by_b


def _sorted_by_volatile_id(items: list[Any], template: list[Any]) -> bool:
    """The list is ordered by a field whose values are random ids (a DynamoDB
    Query by a uuid sort key): its order is random even when the passes agree."""
    if len(items) < 2 or not all(isinstance(x, dict) for x in items + template):
        return False
    for k in template[0]:
        syms = [t.get(k) for t in template]
        if not all(isinstance(v, str) and WHOLE_SYMBOL_RE.match(v) and not free_symbol(v) for v in syms):
            continue
        vals = [x.get(k) for x in items]
        if all(isinstance(v, str) for v in vals) and len(set(vals)) == len(vals) and                 (vals == sorted(vals) or vals == sorted(vals, reverse=True)):
            return True
    return False


def _same(bound: Any, value: Any) -> bool:
    """One symbol, two occurrences: equal, or the same scalar where one
    occurrence is inside a string (a number bound whole, then seen embedded)."""
    if _jkey(bound) == _jkey(value):
        return True
    if isinstance(bound, (dict, list)) or isinstance(value, (dict, list)):
        return False
    return (isinstance(bound, str) or isinstance(value, str)) and str(bound) == str(value)


TABLE_LIST_RE = re.compile(r"^tables_after\.[^.\[]+$")


def unify(template: Any, actual: Any, bindings: dict[str, Any], path: str = "",
          out: list[dict[str, Any]] | None = None, unordered: set[str] | frozenset[str] = frozenset()
          ) -> list[dict[str, Any]]:
    """Mismatches of `actual` against a template; binds symbols as it goes. Lists
    at `unordered` paths, and the item lists of tables_after, match as multisets."""
    out = [] if out is None else out

    def miss(why: str) -> list[dict[str, Any]]:
        out.append({"path": path, "expected": template, "actual": actual, "why": why})
        return out

    if isinstance(template, str) and SYMBOL_RE.search(template):
        if WHOLE_SYMBOL_RE.match(template):
            if free_symbol(template):
                if template.startswith("<trace#") and not isinstance(actual, str):
                    return miss("expected a string (a stack trace in the monolith)")
                return out if _scalar(actual) else miss("expected a single value")
            if template in bindings:
                return out if _same(bindings[template], actual) else miss(f"{template} is {bindings[template]!r} elsewhere")
            bindings[template] = actual
            return out
        if not isinstance(actual, str):
            return miss("expected a string")
        syms = SYMBOL_RE.findall(template)
        parts = SYMBOL_RE.split(template)
        regex, groups, seen = "", [], {}
        for i, part in enumerate(parts):
            regex += re.escape(part)
            if i < len(syms):
                s = syms[i]
                if free_symbol(s):
                    regex += ".+?"
                elif s in bindings:
                    regex += re.escape(str(bindings[s]))
                elif s in seen:
                    regex += f"(?P={seen[s]})"
                else:
                    seen[s] = f"g{len(seen)}"
                    groups.append(s)
                    regex += f"(?P<{seen[s]}>.+?)"
        m = re.fullmatch(regex, actual, re.S)
        if not m:
            return miss("does not match the template")
        for s in groups:
            bindings[s] = m.group(seen[s])
        return out
    if isinstance(template, dict):
        if not isinstance(actual, dict):
            return miss("expected an object")
        if set(template) != set(actual):
            out.append({"path": path, "why": "keys differ", "missing": sorted(set(template) - set(actual)),
                        "extra": sorted(set(actual) - set(template))})
        for k in template:
            if k in actual:
                unify(template[k], actual[k], bindings, f"{path}.{k}" if path else k, out, unordered)
        return out
    if isinstance(template, list):
        if not isinstance(actual, list) or len(actual) != len(template):
            return miss(f"expected {len(template)} items")
        if path in unordered or TABLE_LIST_RE.match(path):
            snapshot = dict(bindings)
            remaining = list(range(len(actual)))
            for i, t in enumerate(template):
                hit = None
                for j in remaining:
                    trial = dict(bindings)
                    if not unify(t, actual[j], trial, f"{path}[{i}]", [], unordered):
                        hit = (j, trial)
                        break
                if hit is None:
                    break
                bindings.clear()
                bindings.update(hit[1])
                remaining.remove(hit[0])
            else:
                return out
            bindings.clear()
            bindings.update(snapshot)  # no pairing: report the elementwise mismatches
        for i, (t, x) in enumerate(zip(template, actual)):
            unify(t, x, bindings, f"{path}[{i}]", out, unordered)
        return out
    return out if _jkey(template) == _jkey(actual) else miss("differs")


# ---------------------------------------------------------------------------
# run_monolith (J2, §4.3)
# ---------------------------------------------------------------------------

def derive_run_config(facts: Facts) -> dict[str, Any]:
    """How the monolith starts, from its own files; judge/run_config.json overrides."""
    root = Path(facts.monolith_root or "")
    # 90 s: the first start after a while loads a cold node_modules (aws-sdk
    # v2 is thousands of files); one start took over 30 s on 2026-11-01.
    rc: dict[str, Any] = {"runtime": "node", "entry": None, "args": [], "port_env": "PORT", "env": {},
                          "ready_path": "/", "startup_timeout_s": 90}
    pkg = root / "package.json"
    if pkg.is_file():
        data = json.loads(pkg.read_text(encoding="utf-8"))
        m = re.search(r"\bnode\s+(\S+\.[cm]?js)\b", str((data.get("scripts") or {}).get("start") or ""))
        for cand in (data.get("main"), m.group(1) if m else None):
            if cand and (root / cand).is_file():
                rc["entry"] = cand
                break
    elif any(root.glob("*.py")) or (root / "requirements.txt").is_file():
        rc["runtime"] = "python"
        rc.update({"entry": None, "app": _python_app(root), "python": None, "debug": False})
    if rc["runtime"] == "node":
        if rc["entry"] is None:
            rc["entry"] = next((n for n in ("server.js", "app.js", "index.js") if (root / n).is_file()), None)
        names: list[str] = []
        for p in sorted(root.rglob("*.js")):
            if "node_modules" in p.parts:
                continue
            names += re.findall(r"process\.env\.(\w*PORT\w*)", p.read_text(encoding="utf-8", errors="replace"))
        if names:
            rc["port_env"] = "PORT" if "PORT" in names else names[0]
    return rc


def _python_app(root: Path) -> str | None:
    """The WSGI application as "module:attr" (or "module:factory()"): a
    top-level `app = Flask(...)`, else a create_app() factory."""
    for p in sorted(root.glob("*.py")):
        if re.search(r"^(\w+)\s*=\s*Flask\(", p.read_text(encoding="utf-8", errors="replace"), re.M):
            name = re.search(r"^(\w+)\s*=\s*Flask\(", p.read_text(encoding="utf-8", errors="replace"), re.M).group(1)
            return f"{p.stem}:{name}"
    for p in sorted(root.glob("*/__init__.py")) + sorted(root.glob("*.py")):
        if re.search(r"^def create_app\(", p.read_text(encoding="utf-8", errors="replace"), re.M):
            module = p.parent.name if p.name == "__init__.py" else p.stem
            return f"{module}:create_app()"
    return None


# Packages run_monolith adds to a Python monolith's venv: per-case coverage.
MONOLITH_PY_EXTRA = (verify.COVERAGE_PY,)


def python_env(root: Path) -> Path:
    """The monolith's own venv (its requirements.txt, plus coverage.py), made once
    under TMP_ROOT/pyenv/<app>-<hash of requirements.txt>; returns its python."""
    import hashlib
    req = root / "requirements.txt"
    if not req.is_file():
        raise JudgeError(f"{root} has no requirements.txt: set python (an interpreter with the app's packages) "
                         "in judge/run_config.json")
    digest = hashlib.sha256(req.read_bytes() + "\n".join(MONOLITH_PY_EXTRA).encode()).hexdigest()[:10]
    env_dir = verify.TMP_ROOT / "pyenv" / f"{re.sub(r'[^A-Za-z0-9_-]', '_', root.name)}-{digest}"
    py = env_dir / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    if (env_dir / "M2S_OK").is_file() and py.is_file():
        return py
    env_dir.parent.mkdir(parents=True, exist_ok=True)
    for cmd in ([sys.executable, "-m", "venv", "--clear", str(env_dir)],
                [str(py), "-m", "pip", "install", "-q", "--disable-pip-version-check", "-r", str(req), *MONOLITH_PY_EXTRA]):
        p = verify._run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900)
        if p.returncode != 0:
            raise JudgeError(f"could not prepare the monolith's Python environment ({' '.join(cmd[:4])}...): "
                             f"{(p.stdout + p.stderr)[-1500:]}")
    (env_dir / "M2S_OK").write_text(digest, encoding="utf-8")
    return py


def effective_run_config(facts: Facts, overrides: dict[str, Any] | None, prepare: bool = False) -> dict[str, Any]:
    """derive_run_config with judge/run_config.json's overrides. prepare: make
    the Python monolith's venv when run_config names no interpreter."""
    rc = derive_run_config(facts)
    for k, v in (overrides or {}).items():
        if k == "env":
            rc["env"] = {**rc["env"], **v}
        elif k != "note":
            rc[k] = v
    if rc["runtime"] == "python":
        if not rc.get("app"):
            raise JudgeError("cannot tell which WSGI application to serve (no `app = Flask(...)` at the top of a "
                             "module, no create_app()): set app (\"module:attr\") in judge/run_config.json")
        if prepare and not rc.get("python"):
            rc["python"] = str(python_env(Path(facts.monolith_root)))
        return rc
    if rc["runtime"] != "node":
        raise JudgeError(f"run_monolith supports Node and Python monoliths; runtime is {rc['runtime']!r}")
    if not rc.get("entry"):
        raise JudgeError("cannot tell how the monolith starts (no package.json main / start script, no server.js, "
                         "app.js or index.js): set entry in judge/run_config.json")
    return rc


TABLE_VAR_RE = re.compile(r"\{(\w+)\}")


def monolith_table_names(facts: Facts, overrides: dict[str, str] | None = None) -> dict[str, str]:
    """Fact-base table name -> the name the monolith actually uses. A name with
    a placeholder ("Airline-Flight-{stage}") takes the default of the variable
    in the monolith's code (os.environ.get('STAGE', 'dev'), process.env.STAGE ||
    'dev'); judge/run_config.json table_names overrides."""
    root = Path(facts.monolith_root or "")
    defaults: dict[str, str] = {}
    if root.is_dir():
        for p in sorted([*root.rglob("*.py"), *root.rglob("*.js")]):
            if "node_modules" in p.parts or "site-packages" in p.parts:
                continue
            text = p.read_text(encoding="utf-8", errors="replace")
            for var, val in re.findall(r"(?:os\.environ\.get|os\.getenv)\(\s*['\"](\w+)['\"]\s*,\s*['\"]([^'\"]*)['\"]", text):
                defaults.setdefault(var.lower(), val)
            for var, val in re.findall(r"process\.env\.(\w+)\s*\|\|\s*['\"]([^'\"]*)['\"]", text):
                defaults.setdefault(var.lower(), val)
    out = {}
    for name in facts.tables:
        out[name] = TABLE_VAR_RE.sub(lambda m: defaults.get(m.group(1).lower(), m.group(0)), name)
    out.update(overrides or {})
    return out


def _table_create(name: str, schema: dict[str, Any], raw: dict[str, Any]) -> dict[str, Any]:
    keys = [(k, "HASH" if i == 0 else "RANGE") for i, k in enumerate(schema["keys"])]
    attrs = dict(schema["keys"])
    gsis = []
    for g in raw.get("gsis") or []:
        gk = [(g["partition_key"]["name"], "HASH")] + ([(g["sort_key"]["name"], "RANGE")] if g.get("sort_key") else [])
        attrs[g["partition_key"]["name"]] = g["partition_key"].get("type", "S")
        if g.get("sort_key"):
            attrs[g["sort_key"]["name"]] = g["sort_key"].get("type", "S")
        gsis.append({"IndexName": g["index_name"], "KeySchema": [{"AttributeName": k, "KeyType": t} for k, t in gk],
                     "Projection": {"ProjectionType": "ALL"}})
    create = {"TableName": name, "BillingMode": "PAY_PER_REQUEST",
              "KeySchema": [{"AttributeName": k, "KeyType": t} for k, t in keys],
              "AttributeDefinitions": [{"AttributeName": a, "AttributeType": t} for a, t in attrs.items()]}
    if gsis:
        create["GlobalSecondaryIndexes"] = gsis
    return create


def _monolith_tables(facts: Facts, names: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """The monolith's tables to create, under the names it actually uses."""
    out = []
    for t in (facts.report.get("dynamodb_info") or {}).get("table_schemas") or []:
        name = (t.get("table") or {}).get("default_name")
        if name in facts.tables:
            actual = (names or {}).get(name, name)
            out.append({"name": actual, "create": _table_create(actual, facts.tables[name], t)})
    return out


def _rename_tables(value: Any, names: dict[str, str]) -> Any:
    """Table names in fixtures / fault rules / records, through `names`
    (dict keys and "table" fields)."""
    if isinstance(value, list):
        return [_rename_tables(v, names) for v in value]
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            k2 = names.get(k, k)
            if k in ("table", "queue") and isinstance(v, str):
                out[k2] = names.get(v, v)
            elif k == "tables" and isinstance(v, list):
                out[k2] = [names.get(x, x) if isinstance(x, str) else x for x in v]
            else:
                out[k2] = _rename_tables(v, names)
        return out
    return value


def _aliases(case: dict[str, Any]) -> list[str]:
    """The aliases a case uses, in order of first use."""
    out: list[str] = []
    if case.get("identity"):
        out.append(case["identity"])
    for s in case.get("steps") or []:
        if s.get("identity"):
            out.append(s["identity"])
        for v in _vars_used({k: s.get(k) for k in ("path", "query", "headers", "body", "form", "raw_body")}):
            if "." in v:
                out.append(v.split(".", 1)[0])
    return list(dict.fromkeys(out))


def build_units(assets: dict[str, Any]) -> list[dict[str, Any]]:
    cases = sorted((c for c in assets["cases"] if "_load_error" not in c), key=lambda c: c["id"])
    by_id = {c["id"]: c for c in cases}

    def unit(c: dict[str, Any], uid: str, rules: list[dict[str, Any]]) -> dict[str, Any]:
        fixtures: dict[str, list[Any]] = {}
        for name in c.get("fixtures") or []:
            for table, items in assets["fixtures"][name].items():
                fixtures.setdefault(table, []).extend(items)
        return {"id": uid, "case": c["id"], "identity": c.get("identity"), "aliases": _aliases(c),
                "fixtures": fixtures, "steps": c["steps"], "rules": rules}

    units = [unit(c, c["id"], []) for c in cases]
    for f in sorted(assets["faults"], key=lambda f: f["id"]):
        units += [unit(by_id[cid], f"{f['id']}/{cid}", f["rules"]) for cid in f["cases"]]
    return units


def _observed(facts: Facts, r: dict[str, Any], symbols: dict[str, str] | None = None) -> dict[str, Any]:
    """What a unit did, as the judge compares it: per step the status, the
    headers (minus T_HEADERS) and the body; the messages; the tables (items
    in key order, volatile key values ordered by their symbol)."""
    steps = {}
    for s in r["steps"]:
        resp = s["response"]
        if "error" in resp:
            steps[s["name"]] = {"error": resp["error"]}
        else:
            steps[s["name"]] = {"status": resp["status"],
                                "headers": {k: v for k, v in sorted(resp["headers"].items()) if k not in T_HEADERS},
                                "body_kind": resp["body_kind"], "body": resp["body"]}
    known = symbols or {}

    def order(table: str, item: dict[str, Any]) -> str:
        keys = facts.tables.get(table, {}).get("keys") or {}
        return _jkey([known.get(_jkey(item.get(k)), item.get(k)) for k in keys] + [item])

    tables = {t: sorted(items, key=lambda i, t=t: order(t, i)) for t, items in sorted((r.get("tables") or {}).items())}
    # whether each fault rule hit at all: how many calls it hit is the SDK's
    # retrying (an implementation trace), and varies with timing
    out = {"steps": steps, "messages": r.get("messages") or [],
           "rules_fired": [bool(n) for n in r.get("rules_fired") or []], "tables_after": tables}
    if r.get("monolith_exited"):
        out["monolith_exited"] = True
    return out


def _identity_bindings(r: dict[str, Any]) -> dict[str, Any]:
    return {f"<{var}:{alias}>": val for alias, p in (r.get("identities") or {}).items()
            for var, val in (p.get("vars") or {}).items()}


def mask_unit(facts: Facts, runs: list[dict[str, Any]], rules: list[dict[str, Any]] | None = None,
              case_id: str | None = None) -> dict[str, Any]:
    """Template and masks of one unit from its passes; the third and later
    passes are checked against the template. `rules` (judge/mask_rules.json)
    that name no cases, or name `case_id`, force masks on their paths."""
    a, b = runs[0], runs[1]
    sym = Symbolizer([(_glob_re(r["path"]), r.get("class", "v")) for r in rules or []
                      if isinstance(r, dict) and isinstance(r.get("path"), str)
                      and (not r.get("cases") or case_id in r["cases"])])
    ia, ib = _identity_bindings(a), _identity_bindings(b)
    for s in ia:
        if s in ib:
            sym.seed(s, ia[s], ib[s])
            # The same in both passes (a user id the case itself chose, e.g. register
            # with userId): J1 can predict the literal, so reconcile compares with it.
            if _jkey(ia[s]) == _jkey(ib[s]):
                sym.symbols[s]["value"] = ia[s]

    def head(o: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in o.items() if k != "tables_after"}

    oa, ob = _observed(facts, a), _observed(facts, b)
    steps = sym.walk(head(oa), head(ob))
    # Tables last: their items are ordered with the symbols the responses found.
    ta = _observed(facts, a, sym.known("a"))["tables_after"]
    tb = _observed(facts, b, sym.known("b"))["tables_after"]
    template = {**steps, "tables_after": sym.walk(ta, tb, "tables_after")}
    checks = []
    for n, later in enumerate(runs[2:], start=3):
        bindings = _identity_bindings(later)
        o = _observed(facts, later, {_jkey(v): k for k, v in bindings.items()})
        mism = unify(template, o, bindings, unordered=frozenset(sym.unordered))
        checks.append({"pass": n, "mismatches": mism})
    return {"template": template, "symbols": sym.symbols, "positions": sym.positions, "unstable": sym.unstable,
            "unordered": sym.unordered, "checks": checks}


def _coverage_summary(raw: dict[str, Any]) -> tuple[dict[str, Any], dict[str, dict[str, list[int]]]]:
    per_unit: dict[str, dict[str, list[int]]] = {}
    ran: dict[str, set[int]] = {}
    zero: dict[str, set[int]] = {}
    sources = [("(load)", raw.get("load_coverage") or {})] + [(r["id"], r.get("coverage") or {}) for r in raw["passes"][0]]
    for uid, cov in sources:
        if uid != "(load)":
            per_unit[uid] = {f: v["ran"] for f, v in cov.items() if v["ran"]}
        for f, v in cov.items():
            ran.setdefault(f, set()).update(v["ran"])
            zero.setdefault(f, set()).update(v["zero"])
    files = {}
    for f in sorted(set(ran) | set(zero)):
        covered = ran.get(f, set())
        uncovered = sorted(zero.get(f, set()) - covered)
        files[f] = {"covered": len(covered), "lines": len(covered) + len(uncovered), "uncovered": uncovered}
    total = sum(v["lines"] for v in files.values())
    done = sum(v["covered"] for v in files.values())
    return {"lines": f"{done}/{total}", "percent": round(100 * done / total, 1) if total else None,
            "note": "line coverage of the monolith's own files (load + every case and fault run, first pass)",
            "files": files}, per_unit


def _write(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def run_monolith(ws: Path | str, passes: int = 3, coverage: bool = True, timeout_s: int = 3600) -> dict[str, Any]:
    """J2: run every judge case (and every case of every fault scenario) on
    the monolith `passes` times; write the masked expectations."""
    import shutil
    import time
    ws = Path(ws)
    facts = Facts(ws)
    check = check_judge_assets(ws)
    if check["status"] != "PASS":
        return {"status": "FAIL", "error": "the judge assets do not pass check_judge_assets",
                "errors": check["errors"][:40]}
    if passes < 3:
        raise JudgeError("passes must be at least 3: two to find the volatile values, one to check the masks")
    assets = load_assets(ws)
    inputs = inputs_sha(ws)  # freeze_judge refuses an oracle made from other inputs
    rc = effective_run_config(facts, assets["run_config"], prepare=True)
    names = monolith_table_names(facts, rc.get("table_names"))
    actual_of = {k: v for k, v in names.items() if k != v}
    fact_of = {v: k for k, v in actual_of.items()}
    units = build_units(assets)
    run_units = [{**u, "fixtures": _rename_tables(u["fixtures"], actual_of), "rules": _rename_tables(u["rules"], actual_of)}
                 for u in units] if actual_of else units
    identities = {alias: spec["monolith"] for alias, spec in (assets["identities"] or {}).items()}
    out_dir = verify.TMP_ROOT / re.sub(r"[^A-Za-z0-9_-]", "_", ws.name) / "monolith" / time.strftime("%Y%m%d-%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)
    backend = verify.ddb_backend()
    job = {"monolith_root": str(Path(facts.monolith_root)), "run": rc, **backend, "tables": _monolith_tables(facts, names),
           "identities": identities, "units": run_units, "passes": passes, "coverage": coverage, "out_dir": str(out_dir)}
    job_path = out_dir / "job.json"
    job_path.write_text(json.dumps(job, ensure_ascii=False, indent=1), encoding="utf-8")
    node = shutil.which("node", path=verify._env()["PATH"]) or "node"
    t0 = time.time()
    proc = verify._run([node, str(verify.VERIFY_TOOLS / "monolith-run.js"), str(job_path)], capture_output=True,
                       text=True, encoding="utf-8", errors="replace", env=verify._env(), timeout=timeout_s)
    raw_path = out_dir / "monolith_raw.json"
    if proc.returncode != 0 or not raw_path.is_file():
        return {"status": "ERROR", "error": f"monolith-run failed (exit {proc.returncode})",
                "stderr": proc.stderr[-3000:], "out_dir": str(out_dir)}
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    if fact_of:  # records name the tables as the fact base does
        for results in raw["passes"]:
            for r in results:
                r["tables"] = {fact_of.get(t, t): items for t, items in (r.get("tables") or {}).items()}
                r["calls"] = _rename_tables(r.get("calls") or [], fact_of)

    # A request that fails or a monolith that exits is a problem in a case;
    # in a fault run it is the monolith's behaviour under that fault.
    fault_ids = {u["id"] for u in units if u["rules"]}
    problems: list[dict[str, Any]] = []
    for p, results in enumerate(raw["passes"], start=1):
        for r in results:
            if r.get("monolith_exited") and r["id"] not in fault_ids:
                problems.append({"unit": r["id"], "pass": p, "why": "the monolith exited during this case"})
            for alias, prep in (r.get("identities") or {}).items():
                for s in prep.get("steps") or []:
                    if s.get("error") or not isinstance(s.get("status"), int) or s["status"] >= 400:
                        problems.append({"unit": r["id"], "pass": p, "why": f"preparing identity {alias} failed",
                                         "step": s})
                if (prep.get("vars") or {}).get("id") in (None, ""):
                    problems.append({"unit": r["id"], "pass": p, "why": f"identity {alias} has no id"})
            for s in r["steps"]:
                if "error" in s["response"] and r["id"] not in fault_ids:
                    problems.append({"unit": r["id"], "pass": p, "why": "request failed", "step": s["name"],
                                     "error": s["response"]["error"]})
    runs_of: dict[str, list[dict[str, Any]]] = {}
    for results in raw["passes"]:
        for r in results:
            runs_of.setdefault(r["id"], []).append(r)
    rules = load_mask_rules(ws)
    case_of = {u["id"]: u["case"] for u in units}
    masked = {uid: mask_unit(facts, rs, rules, case_of.get(uid)) for uid, rs in runs_of.items()}
    unstable = []
    for uid, m in masked.items():
        unstable += [{"unit": uid, "between": "passes 1 and 2", **u} for u in m["unstable"]]
        for c in m["checks"]:
            unstable += [{"unit": uid, "between": f"pass {c['pass']} and the masks", **x} for x in c["mismatches"]]

    meta = {"backend": raw["backend"], "node": raw["node"], "passes": passes, "run_config": rc, "inputs_sha": inputs,
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "monolith_root": job["monolith_root"]}
    jd = judge_dir(ws)
    case_units = [u for u in units if not u["rules"]]
    fault_units = [u for u in units if u["rules"]]
    _write(jd / "oracle_expected.json", {"meta": meta, "cases": {u["id"]: masked[u["id"]]["template"] for u in case_units}})
    scenarios: dict[str, dict[str, Any]] = {}
    for u in fault_units:
        scenarios.setdefault(u["id"].split("/", 1)[0], {})[u["case"]] = masked[u["id"]]["template"]
    _write(jd / "fault_baseline_monolith.json", {"meta": meta, "scenarios": scenarios})
    _write(jd / "masks.json", {"meta": {"backend": raw["backend"], "passes": passes},
                               "units": {uid: {"symbols": m["symbols"], "positions": m["positions"],
                                               "unordered": m["unordered"], "unstable": m["unstable"]}
                                         for uid, m in masked.items()}})
    _write(jd / "monolith_calls.json", {"note": "SDK calls of the first pass (T layer: for reading, never compared)",
                                        "units": {r["id"]: r["calls"] for r in raw["passes"][0]}})
    cov_note = None
    if coverage:
        summary, per_unit = _coverage_summary(raw)
        cov_dir = jd / "coverage_monolith"
        if cov_dir.is_dir():
            shutil.rmtree(cov_dir)
        _write(cov_dir / "summary.json", summary)
        for uid, files in per_unit.items():
            _write(cov_dir / "units" / f"{uid.replace('/', '__')}.json", files)
        cov_note = {"lines": summary["lines"], "percent": summary["percent"]}
    status = "PASS" if not problems and not unstable else "FAIL"
    return {
        "status": status, "backend": raw["backend"], "passes": passes, "cases": len(case_units),
        "fault_runs": len(fault_units), "seconds": round(time.time() - t0, 1),
        "responses_5xx": sum(1 for u in case_units for s in masked[u["id"]]["template"]["steps"].values()
                             if isinstance(s.get("status"), int) and s["status"] >= 500),
        "symbols": sum(len(m["symbols"]) for m in masked.values()),
        "problems": problems[:30], "unstable": unstable[:30], "unstable_total": len(unstable),
        "coverage": cov_note,
        "outputs": ["judge/oracle_expected.json", "judge/fault_baseline_monolith.json", "judge/masks.json",
                    "judge/monolith_calls.json"] + (["judge/coverage_monolith/"] if coverage else []),
        "raw": str(raw_path),
        "next": None if status == "PASS" else
        ("fix the cases or identities whose requests failed" if problems else
         "values that change from run to run and cannot be masked: see unstable"),
    }


# ---------------------------------------------------------------------------
# J3: reconciling the fact base with the monolith (§4.5)
# ---------------------------------------------------------------------------
#
# reconcile() compares each step's `predicted` (what J1 read in the fact base)
# with what the monolith did (oracle_expected.json, masked) and writes the
# differences to judge/reconcile.json. A verifier in J3 mode classifies each
# one in judge/reconcile_classes.json:
#   fact_error    the monolith does this and the fact base says otherwise -> F2 fixes the facts
#   needs_ruling  a defect of the monolith, or behaviour that may be relaxed -> an architect rules
#   case_error    J1 wrote the case wrong (the request, or a prediction the facts do not support)
#   mask_gap      a volatile value the masks missed -> a rule in judge/mask_rules.json, J2 again
# The dispatcher routes the classes (coord/dispatcher.py, J3 flow).

RECONCILE_CLASSES = ("fact_error", "needs_ruling", "case_error", "mask_gap")


def _glob_re(pattern: str) -> re.Pattern[str]:
    """A mask-rule path: "*" matches anything (dots and brackets included), the rest is literal."""
    return re.compile("^" + ".*".join(re.escape(p) for p in pattern.split("*")) + "$")


def load_mask_rules(ws: Path | str) -> list[dict[str, Any]]:
    path = judge_dir(ws) / "mask_rules.json"
    data = _read(path) if path.is_file() else []
    return data if isinstance(data, list) else []


def _check_mask_rules(rules: Any, case_ids: set[str]) -> list[str]:
    where = "judge/mask_rules.json"
    if not isinstance(rules, list):
        return [f"{where}: must be a list of {{path, reason, cases?, class?}}"]
    errs = []
    for i, r in enumerate(rules):
        w = f"{where}[{i}]"
        if not isinstance(r, dict) or set(r) - {"path", "reason", "cases", "class"}:
            errs.append(f"{w}: only path, reason, cases, class")
            continue
        if not isinstance(r.get("path"), str) or not r["path"].strip():
            errs.append(f"{w}: path is required, e.g. \"steps.*.body.date\" (\"*\" matches anything)")
        if not str(r.get("reason") or "").strip():
            errs.append(f"{w}: reason is required")
        if "cases" in r and not (isinstance(r["cases"], list) and all(c in case_ids for c in r["cases"])):
            errs.append(f"{w}: cases must list existing case ids")
        if "class" in r and r["class"] not in {n for n, _ in VOLATILE_PATTERNS} | {"v"}:
            errs.append(f"{w}: class must be one of {[n for n, _ in VOLATILE_PATTERNS] + ['v']}")
    return errs


def _type_name(v: Any) -> str:
    return verify._type_of(v)


def _header_values(headers: dict[str, Any], name: str) -> list[str]:
    v = (headers or {}).get(name.lower())
    if v is None:
        return []
    return [str(x) for x in v] if isinstance(v, list) else [str(v)]


def compare_step(predicted: dict[str, Any], actual: dict[str, Any], symbol_types: dict[str, str] | None = None
                 ) -> list[dict[str, Any]]:
    """Where one step's prediction (v1 expect grammar) does not hold on the
    monolith's masked output. A whole-symbol value has the type of the value
    it stands for."""
    types = symbol_types or {}
    if "error" in actual:
        return [{"check": "request", "predicted": predicted.get("status"), "actual": actual["error"]}]
    out = []

    def diff(check: str, want: Any, got: Any) -> None:
        out.append({"check": check, "predicted": want, "actual": got})

    if predicted.get("status") != actual.get("status"):
        diff("status", predicted.get("status"), actual.get("status"))
    h = predicted.get("headers") or {}
    got_h = actual.get("headers") or {}
    for name, want in (h.get("equals") or {}).items():
        if want not in _header_values(got_h, name):
            diff(f"headers.equals.{name.lower()}", want, _header_values(got_h, name) or None)
    for name, parts in (h.get("contains") or {}).items():
        parts = [parts] if isinstance(parts, str) else parts
        if not any(all(p in v for p in parts) for v in _header_values(got_h, name)):
            diff(f"headers.contains.{name.lower()}", parts, _header_values(got_h, name) or None)
    for name in h.get("absent") or []:
        if _header_values(got_h, name):
            diff(f"headers.absent.{name.lower()}", None, _header_values(got_h, name))
    for name, n in (h.get("count") or {}).items():
        if len(_header_values(got_h, name)) != n:
            diff(f"headers.count.{name.lower()}", n, len(_header_values(got_h, name)))
    b = predicted.get("body") or {}
    body = actual.get("body")
    if "equals" in b and _jkey(b["equals"]) != _jkey(body):
        diff("body.equals", b["equals"], body)
    if "subset" in b and not verify._subset(b["subset"], body):
        diff("body.subset", b["subset"], body)
    for i, item in enumerate(b.get("contains") or []):
        if not (isinstance(body, list) and any(verify._subset(item, x) for x in body)):
            diff(f"body.contains[{i}]", item, body)
    for i, item in enumerate(b.get("not_contains") or []):
        if isinstance(body, list) and any(verify._subset(item, x) for x in body):
            diff(f"body.not_contains[{i}]", item, body)
    for f in b.get("has_fields") or []:
        if not verify._get(body, f)[0]:
            diff(f"body.has_fields.{f}", f, body)
    for f, t in (b.get("types") or {}).items():
        ok, v = verify._get(body, f)
        got = (types.get(v) or _type_name(v)) if ok and isinstance(v, str) and WHOLE_SYMBOL_RE.match(v) else \
            (_type_name(v) if ok else "missing")
        if got != t:
            diff(f"body.types.{f}", t, got)
    if "length" in b and (not isinstance(body, list) or len(body) != b["length"]):
        diff("body.length", b["length"], len(body) if isinstance(body, list) else _type_name(body))
    return out


def _with_values(v: Any, values: dict[str, Any]) -> Any:
    """A masked observation with the symbols whose value is known put back: an
    identity id that was the same in every pass (masks.json `value`). The oracle
    keeps the symbol (the target's id differs); only the reconciliation with J1's
    literal predictions needs the value."""
    if not values:
        return v
    if isinstance(v, str):
        if v in values:
            return values[v]
        return SYMBOL_RE.sub(lambda m: str(values[m.group()]) if m.group() in values else m.group(), v)
    if isinstance(v, list):
        return [_with_values(x, values) for x in v]
    if isinstance(v, dict):
        return {k: _with_values(x, values) for k, x in v.items()}
    return v


def _rulings_by_case(ws: Path) -> dict[str, list[dict[str, Any]]]:
    """Rulings in force or waiting for a person, by the cases they name."""
    data = _read(ws / "storage" / "rulings.json") if (ws / "storage" / "rulings.json").is_file() else {}
    out: dict[str, list[dict[str, Any]]] = {}
    for r in (data.get("rulings") or []) if isinstance(data, dict) else []:
        if r.get("status") in ("decided", "confirmed", "proposed"):
            for c in {str(c).split("#")[0] for c in (r.get("affects") or {}).get("cases") or []}:
                out.setdefault(c, []).append({"id": r["id"], "status": r["status"]})
    return out


def reconcile(ws: Path | str) -> dict[str, Any]:
    """reconcile_spec (J3 step 1): differences between the predictions and the
    monolith, to judge/reconcile.json. Clears judge/reconcile_classes.json: the
    classification is made afresh for every reconciliation."""
    ws = Path(ws)
    jd = judge_dir(ws)
    oracle_path = jd / "oracle_expected.json"
    if not oracle_path.is_file():
        raise JudgeError("judge/oracle_expected.json not found: run_monolith (J2) comes first")
    oracle = _read(oracle_path)
    masks = (_read(jd / "masks.json") if (jd / "masks.json").is_file() else {}) or {}
    assets = load_assets(ws)
    ruled = _rulings_by_case(ws)
    differences, resolved, steps = [], [], 0
    for case in sorted((c for c in assets["cases"] if "_load_error" not in c), key=lambda c: c["id"]):
        cid = case["id"]
        observed = (oracle.get("cases") or {}).get(cid)
        if observed is None:
            raise JudgeError(f"case {cid} is not in judge/oracle_expected.json: the cases changed after run_monolith")
        symbols = ((masks.get("units") or {}).get(cid) or {}).get("symbols", {})
        types = {s: info.get("type") for s, info in symbols.items()}
        values = {s: info["value"] for s, info in symbols.items() if "value" in info}
        for step in case["steps"]:
            name = step["name"]
            steps += 1
            # Both sides: a prediction may name the symbol itself or the literal.
            for d in compare_step(_with_values((case.get("predicted") or {}).get(name) or {}, values),
                                  _with_values(observed["steps"].get(name) or {}, values), types):
                d = {"id": f"{cid}/{name}/{d['check']}", "case": cid, "step": name,
                     "request": {k: step[k] for k in ("method", "path") if k in step}, **d,
                     "fact_refs": case.get("fact_refs") or [], "file": case.get("_file")}
                if cid in ruled:
                    resolved.append({**d, "rulings": ruled[cid]})
                else:
                    differences.append(d)
    out = {"meta": {"cases": len(assets["cases"]), "steps": steps,
                    "oracle_generated_at": (oracle.get("meta") or {}).get("generated_at")},
           "differences": differences, "resolved_by_rulings": resolved}
    _write(jd / "reconcile.json", out)
    classes = jd / "reconcile_classes.json"
    if classes.is_file():
        classes.unlink()
    return {"status": "PASS" if not differences else "DIFFERENCES", "differences": len(differences),
            "resolved_by_rulings": len(resolved), "steps": steps,
            "by_check": {k: sum(1 for d in differences if d["check"].split(".")[0].split("[")[0] == k)
                         for k in ("status", "headers", "body", "request")}}


def reconcile_classes(ws: Path | str) -> tuple[list[dict[str, Any]], dict[str, Any], list[str]]:
    """(differences, classes, errors): the J3 classification and what is wrong with it."""
    jd = judge_dir(ws)
    rec = _read(jd / "reconcile.json") if (jd / "reconcile.json").is_file() else None
    if not isinstance(rec, dict):
        return [], {}, ["judge/reconcile.json not found (reconcile_spec has not run)"]
    diffs = rec.get("differences") or []
    path = jd / "reconcile_classes.json"
    classes = _read(path) if path.is_file() else {}
    errs = []
    if not isinstance(classes, dict) or "_load_error" in classes:
        return diffs, {}, [f"judge/reconcile_classes.json: {classes.get('_load_error') if isinstance(classes, dict) else 'must be an object {difference id: {class, reason}}'}"]
    ids = {d["id"] for d in diffs}
    for d in diffs:
        c = classes.get(d["id"])
        if not isinstance(c, dict):
            errs.append(f"{d['id']}: not classified")
        elif c.get("class") not in RECONCILE_CLASSES:
            errs.append(f"{d['id']}: class must be one of {RECONCILE_CLASSES}")
        elif not str(c.get("reason") or "").strip():
            errs.append(f"{d['id']}: reason is required (the evidence for the class)")
    errs += [f"{k}: no such difference in judge/reconcile.json" for k in classes if k not in ids]
    if any(isinstance(classes.get(d["id"]), dict) and classes[d["id"]].get("class") == "mask_gap" for d in diffs):
        rules = _read(jd / "mask_rules.json") if (jd / "mask_rules.json").is_file() else None
        if not rules:
            errs.append("mask_gap differences need a rule in judge/mask_rules.json ({path, reason, cases?, class?})")
        else:
            errs += _check_mask_rules(rules, {c.get("id") for c in load_assets(ws)["cases"]})
    return diffs, classes, errs


def check_reconcile_classified(ws: Path | str) -> dict[str, Any]:
    diffs, classes, errs = reconcile_classes(ws)
    counts = {k: sum(1 for d in diffs if (classes.get(d["id"]) or {}).get("class") == k) for k in RECONCILE_CLASSES}
    return {"status": "PASS" if not errs else "FAIL", "differences": len(diffs), "classes": counts, "errors": errs[:60]}


# ---------------------------------------------------------------------------
# J6: freezing the judge (§4.7)
# ---------------------------------------------------------------------------
#
# freeze_judge writes judge/judge_manifest.json: the judge's version, the hash
# of every file under judge/, and its size and strength. From then on the
# judge does not change: the dispatcher reverts any attempt that touches
# judge/ (and closes judge/ to workers' writes), gate(final) checks the hashes.
# Changing a frozen judge needs a person (and a new version).

MANIFEST = "judge_manifest.json"
# The case-side inputs of run_monolith: the oracle is stale when any of these changed.
INPUT_GLOBS = ("cases/*/*.json", "fixtures/*.json", "faults/*.json", "identities.json", "run_config.json",
               "mask_rules.json")


def _sha_file(path: Path) -> str:
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest()


def judge_files(ws: Path | str) -> dict[str, str]:
    """judge/<rel> -> sha256 for every file under judge/ but the manifest."""
    jd = judge_dir(ws)
    if not jd.is_dir():
        return {}
    return {f"{JUDGE_DIR}/{p.relative_to(jd).as_posix()}": _sha_file(p) for p in sorted(jd.rglob("*"))
            if p.is_file() and p.relative_to(jd).as_posix() != MANIFEST and "__pycache__" not in p.parts}


def inputs_sha(ws: Path | str) -> str:
    """One hash over run_monolith's case-side inputs (recorded in the oracle's meta)."""
    import hashlib
    jd = judge_dir(ws)
    h = hashlib.sha256()
    for pattern in INPUT_GLOBS:
        for p in sorted(jd.glob(pattern)):
            h.update(p.relative_to(jd).as_posix().encode() + b"\0" + p.read_bytes() + b"\0")
    return h.hexdigest()


def manifest(ws: Path | str) -> dict[str, Any] | None:
    path = judge_dir(ws) / MANIFEST
    data = _read(path) if path.is_file() else None
    return data if isinstance(data, dict) and "files" in data else None


def judge_changes(ws: Path | str) -> dict[str, list[str]] | None:
    """What differs from the frozen judge (None when the judge is not frozen)."""
    m = manifest(ws)
    if m is None:
        return None
    now, then = judge_files(ws), m["files"]
    return {"changed": sorted(p for p in now if p in then and now[p] != then[p]),
            "added": sorted(p for p in now if p not in then),
            "removed": sorted(p for p in then if p not in now)}


def _pending_rulings(ws: Path) -> list[str]:
    data = _read(ws / "storage" / "rulings.json") if (ws / "storage" / "rulings.json").is_file() else {}
    return [r["id"] for r in (data.get("rulings") or []) if isinstance(data, dict) and r.get("status") == "proposed"]


def freeze_judge(ws: Path | str, refreeze: bool = False) -> dict[str, Any]:
    """J6. Refuses while the judge is not ready: assets failing their check, an
    oracle older than the cases, a difference with the fact base that no ruling
    settles, a ruling still waiting for a person. Freezing again an unchanged
    judge is a no-op; a changed frozen judge needs refreeze=True (a person)."""
    import time
    ws = Path(ws)
    jd = judge_dir(ws)
    prev = manifest(ws)
    if prev is not None:
        changes = judge_changes(ws) or {}
        if not any(changes.values()):
            return {"status": "PASS", "version": prev["version"], "unchanged": True}
        if not refreeze:
            return {"status": "FAIL", "error": "the judge is frozen and has changed since; only a person can freeze "
                    f"a new version (refreeze): {changes}"}
    problems: list[str] = []
    check = check_judge_assets(ws)
    if check["status"] != "PASS":
        problems.append(f"check_judge_assets FAIL ({len(check['errors'])} errors)")
    oracle = _read(jd / "oracle_expected.json") if (jd / "oracle_expected.json").is_file() else None
    if not isinstance(oracle, dict):
        problems.append("judge/oracle_expected.json missing: run_monolith first")
    elif (oracle.get("meta") or {}).get("inputs_sha") != inputs_sha(ws):
        problems.append("the cases changed after run_monolith: run it again")
    rec = None
    if not problems:
        rec = reconcile(ws)
        if rec["differences"]:
            problems.append(f"{rec['differences']} differences with the fact base that no ruling settles (judge/reconcile.json)")
    pending = _pending_rulings(ws)
    if pending:
        problems.append(f"rulings waiting for a person: {pending}")
    if problems:
        return {"status": "FAIL", "error": "the judge cannot be frozen yet", "problems": problems}
    cov = _read(jd / "coverage_monolith" / "summary.json") if (jd / "coverage_monolith" / "summary.json").is_file() else {}
    rulings_data = _read(ws / "storage" / "rulings.json") if (ws / "storage" / "rulings.json").is_file() else {}
    s = check["summary"]
    m = {
        "version": (prev["version"] + 1) if prev else 1,
        "frozen_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "backend": (oracle.get("meta") or {}).get("backend"),
        "facts_sha": _sha_file(ws / "storage" / "analysis_report.json"),
        "cases": s["cases"], "layers": s["layers"], "fault_scenarios": s["fault_scenarios"],
        "required_responses": s["required_responses"], "business_rules": s["business_rules"],
        "exempted": s["exempted"],
        "coverage": {k: cov.get(k) for k in ("lines", "percent")} if isinstance(cov, dict) and cov else None,
        "mutation_score": _mutation_summary(jd),
        "reconcile": {"differences": 0, "settled_by_rulings": rec["resolved_by_rulings"] if rec else 0},
        "rulings": [r["id"] for r in (rulings_data.get("rulings") or []) if r.get("status") in ("decided", "confirmed")]
        if isinstance(rulings_data, dict) else [],
        "files": judge_files(ws),
    }
    _write(jd / MANIFEST, m)
    return {"status": "PASS", "version": m["version"], "cases": m["cases"], "files": len(m["files"]),
            "coverage": m["coverage"], "layers": m["layers"]}


# ---------------------------------------------------------------------------
# What J1 reads: the fact base without its citations
# ---------------------------------------------------------------------------

def fact_digest(ws: Path | str, endpoint: str | None = None) -> dict[str, Any]:
    """The fact base as J1 needs it (judge_facts tool): per endpoint the auth
    classification, the request fields, every response (index, status, kind,
    when, body fields) and every business rule -- without the source
    citations (code snippets J1 does not need) -- plus the tables and the
    global middleware. Reading this instead of dumping analysis_report.json
    keeps a J1 session small."""
    facts = Facts(Path(ws))
    eps = {}
    for name, ep in facts.entry_points.items():
        if endpoint and name != endpoint:
            continue
        req = ep.get("request") or {}
        eps[name] = {
            "auth": (ep.get("auth") or {}).get("classification"),
            "request": {part: [{k: f.get(k) for k in ("name", "type", "required", "note") if f.get(k) not in (None, "")}
                               for f in req.get(part) or [] if isinstance(f, dict)]
                        for part in ("path_params", "query", "body", "headers") if req.get(part)},
            "responses": [{"ref": f"{name}#responses[{i}]", "status": r.get("status"), "kind": r.get("kind"),
                           "when": r.get("when"), "in_catch": bool(r.get("in_catch")),
                           "body": {k: (r.get("body") or {}).get(k) for k in ("fields", "description")
                                    if (r.get("body") or {}).get(k)},
                           **({"note": r["note"]} if r.get("note") else {})}
                          for i, r in enumerate(ep.get("responses") or []) if isinstance(r, dict)],
            "data_access": [{"operation": d.get("operation"), "table": (d.get("table") or {}).get("default_name"),
                             "index": d.get("index")} for d in ep.get("data_access") or [] if isinstance(d, dict)],
            "business_rules": [{"ref": f"{name}#business_rules[{i}]", "id": b.get("id"), "rule": b.get("rule"),
                                "kind": b.get("kind")} for i, b in enumerate(ep.get("business_rules") or [])
                               if isinstance(b, dict)],
        }
    out: dict[str, Any] = {"endpoints": eps}
    if not endpoint:
        out["tables"] = {t: {"keys": s["keys"], "index_keys": s["index_keys"]} for t, s in facts.tables.items()}
        out["global_middleware"] = [{"ref": f"global_middleware[{i}]", "kind": g.get("kind"), "headers": g.get("headers"),
                                     "applies_to": g.get("applies_to")} for i, g in enumerate(facts.global_middleware)
                                    if isinstance(g, dict)]
    return out


# ---------------------------------------------------------------------------
# run_judge (V2, §4.9) and check_adapter (V1, §4.8)
# ---------------------------------------------------------------------------
#
# The frozen judge runs on the target: every case (and every case of every
# fault scenario) on the whole API simulated by verify-tools/judge-run.js,
# with the fixtures written through the adapter map and each identity's
# claims from its identity mapping. Each step is compared with what the
# monolith did (oracle_expected.json / fault_baseline_monolith.json):
#   P layer   status, the declared response headers (global middleware headers,
#             content-type by media type, headers the fact base names) and the
#             body, symbol by symbol (unify); lists the masks mark unordered as
#             multisets; tables only when no endpoint reads them (V2P-03);
#   R layer   a decided ruling on the case: "order" compares every list as a
#             multiset; "alternatives" leaves the step to the ruling ("ruled");
#   migration a decided migration-change ruling on the case, or an endpoint the
#             adapter map lists as dropped (its steps are not run).
# Output: verification/verdict_judge.json.

VERDICT_FILE = ("verification", "verdict_judge.json")
CHECK_ADAPTER_FILE = ("verification", "check_adapter.json")
DECLARED_ALWAYS = frozenset({"content-type"})
ID_NAMESPACE = __import__("uuid").UUID("6f1d9a52-5f0e-4c43-9a57-6d2e1c0b7a11")


def _declared_headers(facts: Facts) -> set[str]:
    """Response headers the P layer compares (V2P-03)."""
    names = {str(h).lower() for g in facts.global_middleware if isinstance(g, dict) for h in (g.get("headers") or {})}
    for ep in facts.entry_points.values():
        for r in ep.get("responses") or []:
            if isinstance(r, dict) and isinstance(r.get("headers"), dict):
                names |= {str(h).lower() for h in r["headers"]}
    return names | set(DECLARED_ALWAYS)


def _read_tables(facts: Facts) -> set[str]:
    """Monolith tables some endpoint reads (their writes are checked by read-back steps)."""
    out = set()
    for ep in facts.entry_points.values():
        for d in ep.get("data_access") or []:
            if isinstance(d, dict) and not WRITE_OP_RE.search(str(d.get("operation") or "")):
                out.add((d.get("table") or {}).get("default_name"))
    return out


def _media_type(v: Any) -> str:
    v = v[0] if isinstance(v, list) and v else v
    return str(v).split(";", 1)[0].strip().lower()


def blueprint_target_tables(ws: Path) -> dict[str, list[str]] | None:
    """{logical table: [key attributes]} from the blueprint (A1 / A2 side)."""
    bp = ws / "storage" / "blueprint.json"
    if not bp.is_file():
        return None
    out = {}
    for t in json.loads(bp.read_text(encoding="utf-8")).get("dynamodb_tables") or []:
        if not isinstance(t, dict) or not (t.get("logical_name") or t.get("name")):
            continue
        keys = [k.get("name") for k in (t.get("partition_key"), t.get("sort_key")) if isinstance(k, dict) and k.get("name")]
        out[t.get("logical_name") or t.get("name")] = keys
    return out


def _model_target_tables(model: "verify.Model") -> dict[str, list[str]]:
    return {t: [k["AttributeName"] for k in spec["create"].get("KeySchema") or []] for t, spec in model.tables.items()}


def check_adapter_map(ws: Path | str, target_tables: dict[str, list[str]] | None = None) -> dict[str, Any]:
    """Static check of storage/adapter_map.json against the fact base, the judge
    and the target's tables (A2 and the first half of V1)."""
    ws = Path(ws)
    facts = Facts(ws)
    a = load_assets(ws) if judge_dir(ws).is_dir() else {"fixtures": {}, "identities": None}
    bp_path = ws / "storage" / "blueprint.json"
    bp = json.loads(bp_path.read_text(encoding="utf-8")) if bp_path.is_file() else {}
    auth_replaced = bool((bp.get("cognito") or {}).get("enabled")) if isinstance(bp.get("cognito"), dict) else False
    errs = adapter.check_adapter_map(
        adapter.load_map(ws), monolith_tables=facts.tables, endpoints=list(facts.entry_points),
        target_tables=target_tables if target_tables is not None else blueprint_target_tables(ws),
        identities=a.get("identities") or {}, fixtures=a.get("fixtures") or {}, auth_replaced=auth_replaced)
    return {"status": "PASS" if not errs else "FAIL", "errors": errs[:60]}


def _gateway_answers(model: "verify.Model") -> dict[str, dict[str, Any]]:
    """What API Gateway answers by itself: the authorizer's 401, an unknown route,
    a failed or timed-out Lambda (REST: GatewayResponses; HTTP API: fixed)."""
    http_only = model.functions and all(r.get("http_api") for fn in model.functions.values() for r in fn["api"])
    gw = getattr(model, "gateway_responses", {}) or {}

    def rest(kind: str, family: str, status: int, message: str) -> dict[str, Any]:
        spec = gw.get(kind) or gw.get(family) or {}
        return {"statusCode": spec.get("status") or status,
                "headers": {"Content-Type": "application/json", **(spec.get("headers") or {})},
                "body": json.dumps({"message": message})}

    if http_only:
        def http(status: int, message: str) -> dict[str, Any]:
            return {"statusCode": status, "headers": {"Content-Type": "application/json"}, "body": json.dumps({"message": message})}
        return {"unauthorized": http(401, "Unauthorized"), "no_route": http(404, "Not Found"),
                "error_502": http(500, "Internal Server Error"), "timeout_504": http(503, "Service Unavailable")}
    return {"unauthorized": rest("UNAUTHORIZED", "DEFAULT_4XX", 401, "Unauthorized"),
            "no_route": rest("MISSING_AUTHENTICATION_TOKEN", "DEFAULT_4XX", 403, "Missing Authentication Token"),
            "error_502": rest("INTEGRATION_FAILURE", "DEFAULT_5XX", 502, "Internal server error"),
            "timeout_504": rest("INTEGRATION_TIMEOUT", "DEFAULT_5XX", 504, "Endpoint request timed out")}


def _dropped_endpoints(amap: dict[str, Any]) -> dict[str, str]:
    return {d["endpoint"]: str(d.get("reason") or "") + (f" (replaced by {d['replaced_by']})" if d.get("replaced_by") else "")
            for d in amap.get("dropped_endpoints") or [] if isinstance(d, dict) and d.get("endpoint")}


def _route_steps(model: "verify.Model", facts: Facts, case: dict[str, Any], dropped: dict[str, str]) -> list[dict[str, Any]]:
    """Each step with where it goes on the target."""
    out = []
    lost: set[str] = set()  # variables saved by steps that do not run
    endpoints = list(facts.entry_points)
    for s in case["steps"]:
        step = {k: v for k, v in s.items() if k not in ("note", "read_back")}
        used = [v for v in _vars_used({k: s.get(k) for k in ("path", "query", "headers", "body", "form", "raw_body")})
                if "." not in v]
        ep = _step_endpoint(s, endpoints)
        if ep in dropped:
            step["route"] = {"kind": "dropped", "reason": f"{ep} is not served by the target: {dropped[ep]}"}
            lost |= set(s.get("save") or {})
        elif any(v in lost for v in used):
            step["route"] = {"kind": "blocked",
                             "reason": f"uses ${{{next(v for v in used if v in lost)}}}, saved by a step that does not run"}
            lost |= set(s.get("save") or {})
        else:
            hit = verify.find_route(model, s["method"], _step_path(s["path"]))
            if hit is None:
                step["route"] = {"kind": "no_route", "endpoint": ep}
            else:
                fn, r = hit
                step["route"] = {"kind": "invoke", "function": fn, "resource": r["path"], "authorized": r["authorized"],
                                 "http_api": bool(r.get("http_api")), "endpoint": ep}
        out.append(step)
    return out


def _rules_to_target(amap: dict[str, Any], rules: list[dict[str, Any]], model: "verify.Model"
                     ) -> tuple[list[dict[str, Any]], list[str]]:
    """Fault rules (monolith tables and attributes) on the target's tables."""
    out, problems = [], []
    for r in rules:
        m = dict(r.get("match") or {})
        if m.get("table"):
            entry = adapter._table_entry(amap, m["table"]) or {}
            if not entry.get("target"):
                problems.append(f"{m['table']} has no target table ({entry.get('replaced_by') or 'not in the adapter map'})")
                continue
            if isinstance(m.get("item"), dict):
                renames = {spec["from"]: t for t, spec in (entry.get("fields") or {}).items()
                           if isinstance(spec, dict) and set(spec) <= {"from", "as", "note"}}
                m["item"] = {renames.get(k, k): v for k, v in m["item"].items()}
            m["table"] = entry["target"]
        if m.get("queue"):
            m["table"] = model.queue_id(m.pop("queue"))
        out.append({"match": m, "nth": r.get("nth", "all"), "kind": r["kind"]})
    return out, problems


def _rulings_in_force(ws: Path) -> dict[str, list[dict[str, Any]]]:
    """Rulings by case. An entry "case#step" reaches only that step (_steps); a bare "case"
    reaches the whole case, and every difference in it, so name steps where that matters."""
    data = _read(ws / "storage" / "rulings.json") if (ws / "storage" / "rulings.json").is_file() else {}
    out: dict[str, list[dict[str, Any]]] = {}
    for r in (data.get("rulings") or []) if isinstance(data, dict) else []:
        if r.get("status") not in ("decided", "confirmed"):
            continue
        scope: dict[str, set[str] | None] = {}
        for entry in (r.get("affects") or {}).get("cases") or []:
            case, _, step = str(entry).partition("#")
            if not step:
                scope[case] = None
            elif scope.get(case, set()) is not None:
                scope.setdefault(case, set()).add(step)
        for case, steps in scope.items():
            out.setdefault(case, []).append({**r, "_steps": steps})
    return out


def _all_list_paths(v: Any, path: str, out: set[str]) -> set[str]:
    if isinstance(v, list):
        out.add(path)
        for i, x in enumerate(v):
            _all_list_paths(x, f"{path}[{i}]", out)
    elif isinstance(v, dict):
        for k, x in v.items():
            _all_list_paths(x, f"{path}.{k}" if path else k, out)
    return out


def compare_target_step(template: dict[str, Any], actual: dict[str, Any], bindings: dict[str, Any], name: str,
                 declared: set[str], unordered: set[str] | frozenset[str]) -> list[dict[str, Any]]:
    """Mismatches of one target step against the monolith's (masked) step."""
    out: list[dict[str, Any]] = []
    where = f"steps.{name}"
    if "error" in template:
        return [{"path": where, "why": f"the monolith's request failed ({template['error']}); the target answered",
                 "actual": actual.get("status")}]
    unify(template.get("status"), actual.get("status"), bindings, f"{where}.status", out)
    th = {k: v for k, v in (template.get("headers") or {}).items() if k in declared}
    ah = {k: v for k, v in (actual.get("headers") or {}).items() if k in declared}
    for h in sorted(set(th) | set(ah)):
        if h not in ah:
            out.append({"path": f"{where}.headers.{h}", "expected": th[h], "actual": None, "why": "header missing"})
        elif h not in th:
            out.append({"path": f"{where}.headers.{h}", "expected": None, "actual": ah[h], "why": "header the monolith does not send"})
        elif h == "content-type":
            if _media_type(th[h]) != _media_type(ah[h]):
                out.append({"path": f"{where}.headers.{h}", "expected": th[h], "actual": ah[h], "why": "different media type"})
        else:
            unify(th[h], ah[h], bindings, f"{where}.headers.{h}", out)
    if template.get("body_kind") != actual.get("body_kind"):
        out.append({"path": f"{where}.body_kind", "expected": template.get("body_kind"), "actual": actual.get("body_kind"),
                    "why": "differs"})
    unify(template.get("body"), actual.get("body"), bindings, f"{where}.body", out, unordered)
    return out


def _unit_identities(amap: dict[str, Any], identities: dict[str, Any], aliases: list[str], unit_id: str,
                     identity_values: dict[str, str] | None = None
                     ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    """(vars, auth, bindings, seeds) of a unit's aliases on the target.

    identity_values: alias -> the monolith's own id literal (masks.json `<id:alias>`,
    or the register body's userId). check_adapter uses it as the target id so both
    sides name the same user; without it (a case where the id never showed up) the
    old per-unit uuid5 stands in.
    """
    import uuid
    identity_values = identity_values or {}
    vars_: dict[str, Any] = {}
    auth: dict[str, Any] = {}
    bindings: dict[str, Any] = {}
    seeds: list[dict[str, Any]] = []
    for alias in aliases:
        mono = (identities.get(alias) or {}).get("monolith") or {}
        literal = identity_values.get(alias)
        if not literal:
            for step in mono.get("steps") or []:
                body = step.get("body") if isinstance(step, dict) else None
                if isinstance(body, dict) and isinstance(body.get("userId"), str) and body["userId"]:
                    literal = body["userId"]
                    break
        target_id = str(literal or uuid.uuid5(ID_NAMESPACE, f"{unit_id}/{alias}"))
        ti = adapter.target_identity(amap, alias, mono, target_id)
        own = {"id": ti["user_id"]}
        for s in mono.get("steps") or []:
            for var in (s.get("save") or {}):
                own.setdefault(var, f"m2s-{var}-{alias}")  # a token stand-in: the gateway does the checking
        if "sign" in mono:
            own.setdefault("token", f"m2s-token-{alias}")
        header = {k: adapter.render(v, own) for k, v in (mono.get("header") or {}).items()}
        auth[alias] = {"header": header, "claims": ti["claims"]}
        vars_.update({f"{alias}.{k}": v for k, v in own.items()})
        bindings.update({f"<{k}:{alias}>": v for k, v in own.items()})
        seeds += ti["seed"]
    return vars_, auth, bindings, seeds


def _plan_units(ws: Path, facts: Facts, assets: dict[str, Any], amap: dict[str, Any], model: "verify.Model",
                units: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """The job's units and, per unit, what the comparison needs."""
    dropped = _dropped_endpoints(amap)
    by_case = {c["id"]: c for c in assets["cases"] if "_load_error" not in c}
    identities = assets["identities"] or {}
    masks_path = judge_dir(ws) / "masks.json"
    masks = (_read(masks_path) if masks_path.is_file() else {}) or {}
    identity_values: dict[str, str] = {}
    for unit in (masks.get("units") or {}).values():
        for symbol, info in (unit.get("symbols") or {}).items():
            match = re.fullmatch(r"<id:([^>]+)>", str(symbol))
            if match and isinstance(info, dict) and isinstance(info.get("value"), str):
                identity_values.setdefault(match.group(1), info["value"])
    job_units, ctx = [], {}
    for u in units:
        case = by_case[u["case"]]
        vars_, auth, bindings, seeds = _unit_identities(amap, identities, u["aliases"], u["id"], identity_values)
        fixtures = adapter.fixtures_to_target(amap, u["fixtures"])
        for s in seeds:
            fixtures.setdefault(s["table"], []).append(s["item"])
        rules, problems = _rules_to_target(amap, u["rules"], model)
        steps = _route_steps(model, facts, case, dropped)
        job_units.append({"id": u["id"], "fixtures": fixtures, "identity": u["identity"], "vars": vars_, "auth": auth,
                          "steps": steps, "rules": rules})
        ctx[u["id"]] = {"case": case, "bindings": bindings, "rule_problems": problems, "routes": steps}
    return job_units, ctx


def _verdict_of(steps: dict[str, dict[str, Any]]) -> str:
    vs = [s["verdict"] for s in steps.values()]
    if all(v in ("dropped", "blocked") for v in vs):
        return "dropped"
    for v in ("fail", "migration-change", "ruled"):
        if v in vs:
            return v
    return "pass"


def judge_unit(facts: Facts, template: dict[str, Any], rec: dict[str, Any], ctx: dict[str, Any], amap: dict[str, Any],
               unordered: list[str], rulings: list[dict[str, Any]], declared: set[str]) -> dict[str, Any]:
    """The verdict of one unit: per step pass / fail / dropped / blocked /
    migration-change / ruled, the table differences, the faults that fired."""
    if rec.get("infra_error"):
        return {"verdict": "infra_error", "error": rec["infra_error"][-2000:]}
    bindings = dict(ctx["bindings"])
    steps: dict[str, dict[str, Any]] = {}
    route_of = {s["name"]: s["route"] for s in ctx["routes"]}
    for s in rec["steps"]:
        name = s["name"]
        mine = [r for r in rulings if r.get("_steps") is None or name in r["_steps"]]
        order = [r for r in mine if r.get("relaxation") == "order"]
        alternatives = [r for r in mine if r.get("relaxation") == "alternatives"]
        migration = [r for r in mine if r.get("category") == "migration-change"]
        trace = [r for r in mine if r.get("category") == "implementation-trace"]
        unordered_paths = set(unordered)
        if order:
            unordered_paths |= _all_list_paths((template.get("steps") or {}).get(name) or {}, f"steps.{name}", set())
        route = route_of.get(name) or {}
        entry: dict[str, Any] = {"function": route.get("function"), "endpoint": route.get("endpoint")}
        if s["route"] in ("dropped", "blocked"):
            steps[name] = {**entry, "verdict": s["route"], "reason": s.get("reason")}
            continue
        tmpl = (template.get("steps") or {}).get(name)
        if tmpl is None:
            steps[name] = {**entry, "verdict": "fail", "mismatches": [{"path": f"steps.{name}", "why": "not in the monolith's record"}]}
            continue
        mism = compare_target_step(tmpl, s["response"], bindings, name, declared, unordered_paths)
        entry["status"] = {"monolith": tmpl.get("status"), "target": (s.get("response") or {}).get("status")}
        if s.get("gateway"):
            entry["answered_by"] = f"API Gateway ({s['gateway']})"
        if s.get("lambda_error"):
            entry["lambda_error"] = s["lambda_error"]
        if not mism:
            steps[name] = {**entry, "verdict": "pass", **({"rulings": [r["id"] for r in order]} if order else {})}
        elif migration:
            steps[name] = {**entry, "verdict": "migration-change", "rulings": [r["id"] for r in migration], "mismatches": mism}
        elif alternatives or trace:
            steps[name] = {**entry, "verdict": "ruled", "rulings": [r["id"] for r in alternatives + trace], "mismatches": mism}
        else:
            steps[name] = {**entry, "verdict": "fail", "mismatches": mism}
    # tables: in the monolith's structure, replaced tables and dropped fields left out
    actual_tables = adapter.tables_to_monolith(amap, rec.get("tables_after") or {})
    want_tables = {}
    for t, items in (template.get("tables_after") or {}).items():
        if t in actual_tables:
            drop = adapter.dropped_fields(amap, t)
            want_tables[t] = [{k: v for k, v in i.items() if k not in drop} if isinstance(i, dict) else i for i in items]
    table_diffs = unify(want_tables, {t: actual_tables[t] for t in want_tables}, dict(bindings), "tables_after", [])
    write_only = set(facts.tables) - _read_tables(facts)
    p_tables = [d for d in table_diffs if d["path"].split(".")[1].split("[")[0] in write_only] if write_only else []
    out: dict[str, Any] = {"steps": steps}
    if p_tables:
        out["tables"] = {"verdict": "fail", "mismatches": p_tables, "why": "a table no endpoint reads (V2P-03)"}
    if table_diffs:
        out["table_differences"] = table_diffs[:20]
    want_fired = template.get("rules_fired") or []
    got_fired = [bool(n) for n in rec.get("rules_fired") or []]
    if want_fired:
        out["rules_fired"] = {"monolith": want_fired, "target": got_fired}
    if ctx["rule_problems"]:
        out["rule_problems"] = ctx["rule_problems"]
    unfinished = verify.unfinished_writes(rec)
    if unfinished:
        out["unawaited"] = {"verdict": "fail", "calls": unfinished, "why": verify.unfinished_text(unfinished)}
    out["verdict"] = "fail" if p_tables or unfinished else _verdict_of(steps)
    return out


def _judge_job(ws: Path, ref: str | None, mode: str, unit_filter: Any = None, label: str = "judge",
               model: "verify.Model | None" = None, coverage: bool = False) -> tuple[dict[str, Any], dict[str, Any]]:
    """Plan and run the judge on the target: (raw results, context). With `model` (the self
    test) the code is what that model built; otherwise the template's, through sam build."""
    import shutil
    import time
    facts = Facts(ws)
    m = manifest(ws)
    if m is None:
        raise JudgeError("the judge is not frozen (judge/judge_manifest.json): run_judge uses a frozen judge only")
    changes = judge_changes(ws) or {}
    if any(changes.values()):
        raise JudgeError(f"the judge differs from its manifest (frozen judge changed): {changes}")
    amap = adapter.load_map(ws)
    if amap is None or "_load_error" in amap:
        raise AdapterMapError("storage/adapter_map.json " + ("not found: the architect writes it (A1)" if amap is None
                                                             else amap["_load_error"]))
    code_root = verify.export_ref(ws, ref) if ref else ws
    given_model = model is not None
    model = model or verify.Model(code_root)
    static = check_adapter_map(ws, _model_target_tables(model))
    if static["status"] != "PASS":
        raise AdapterMapError("the adapter map does not pass its check: " + "; ".join(static["errors"][:8]))
    oracle = _read(judge_dir(ws) / "oracle_expected.json")
    backend = verify.ddb_backend()
    if (oracle.get("meta") or {}).get("backend") != backend["backend"]:
        raise JudgeError(f"the judge was built on {(oracle.get('meta') or {}).get('backend')}; this machine would run "
                         f"{backend['backend']} -- both sides must use the same backend (V2P-11)")
    assets = load_assets(ws)
    units = build_units(assets)
    if unit_filter is not None:
        units = [u for u in units if unit_filter(u)]
    job_units, ctx = _plan_units(ws, facts, assets, amap, model, units)
    build_dir = model.build_dir if given_model else verify.sam_build(code_root)
    mode = verify._mode(mode)
    functions = {}
    for name, fn in model.functions.items():
        functions[name] = {"name": name, "dir": str(build_dir / fn["logical"]), "handler": fn["handler"], "env": fn["env"],
                           "memory": fn["memory"], "runtime": fn.get("runtime", ""),
                           "layers": [str(build_dir / ly) for ly in fn.get("layers", [])],
                           "sqs_events": fn.get("sqs_events") or []}
    out_dir = verify.TMP_ROOT / re.sub(r"[^A-Za-z0-9_-]", "_", ws.name) / "judge" / f"{time.strftime('%Y%m%d-%H%M%S')}-{label}"
    out_dir.mkdir(parents=True, exist_ok=True)
    job = {"mode": mode, "coverage": coverage, **backend, "out_dir": str(out_dir), "functions": functions,
           "tables": [{"name": t["name"], "create": t["create"]} for t in model.tables.values()],
           "queues": {q: {"max_receive": n} for q, n in model.queue_max_receive.items()},
           "gateway": _gateway_answers(model), "units": job_units, "hold_writes_ms": verify.HOLD_WRITES_MS,
           "rie_image_python": verify.PY_RIE_IMAGE, "python_exe": sys.executable}
    (out_dir / "job.json").write_text(json.dumps(job, ensure_ascii=False, indent=1), encoding="utf-8")
    node = shutil.which("node", path=verify._env()["PATH"]) or "node"
    t0 = time.time()
    env = verify._env()
    if given_model:  # local Node: what the Lambda runtime provides, the SDK
        env["NODE_PATH"] = str(verify.VERIFY_TOOLS / "node_modules")
    proc = verify._run([node, str(verify.VERIFY_TOOLS / "judge-run.js"), str(out_dir / "job.json")], capture_output=True,
                       text=True, encoding="utf-8", errors="replace", env=env, timeout=7200)
    raw_path = out_dir / "judge_raw.json"
    if proc.returncode != 0 or not raw_path.is_file():
        raise JudgeError(f"judge-run failed (exit {proc.returncode}): {proc.stderr[-3000:]}")
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    raw["seconds"] = round(time.time() - t0, 1)
    raw["out_dir"] = str(out_dir)
    return raw, {"facts": facts, "amap": amap, "model": model, "ctx": ctx, "units": units, "manifest": m,
                 "oracle": oracle, "assets": assets, "mode": mode}


def _judge_results(ws: Path, raw: dict[str, Any], info: dict[str, Any]) -> dict[str, Any]:
    facts, amap = info["facts"], info["amap"]
    jd = judge_dir(ws)
    masks = (_read(jd / "masks.json") or {}).get("units") or {}
    faults = (_read(jd / "fault_baseline_monolith.json") or {}).get("scenarios") or {}
    rulings = _rulings_in_force(ws)
    declared = _declared_headers(facts)
    by_id = {r["id"]: r for r in raw["units"]}
    cases: dict[str, Any] = {}
    fault_runs: dict[str, dict[str, Any]] = {}
    for u in info["units"]:
        rec = by_id.get(u["id"], {"infra_error": "not run"})
        c = info["ctx"][u["id"]]["case"]
        if u["rules"]:
            scen = u["id"].split("/", 1)[0]
            template = (faults.get(scen) or {}).get(u["case"])
        else:
            template = (info["oracle"].get("cases") or {}).get(u["id"])
        if template is None:
            res = {"verdict": "fail", "error": "no monolith record for this unit"}
        else:
            res = judge_unit(facts, template, rec, info["ctx"][u["id"]], amap,
                             ((masks.get(u["id"]) or {}).get("unordered") or []), rulings.get(u["case"], []), declared)
        res["endpoint"] = c.get("endpoint")
        res["fact_refs"] = c.get("fact_refs") or []
        if u["rules"]:
            fault_runs.setdefault(u["id"].split("/", 1)[0], {})[u["case"]] = res
        else:
            cases[u["id"]] = res

    def count(results: list[dict[str, Any]]) -> dict[str, int]:
        out: dict[str, int] = {}
        for r in results:
            out[r["verdict"]] = out.get(r["verdict"], 0) + 1
        return dict(sorted(out.items()))

    all_fault = [r for s in fault_runs.values() for r in s.values()]
    fns = raw.get("functions") or {}
    return {"cases": cases, "faults": fault_runs, "summary": {"cases": count(list(cases.values())),
                                                               "fault_runs": count(all_fault)},
            "functions": {n: {k: v for k, v in f.items() if k != "logs_tail"} for n, f in fns.items()},
            "logs": {n: f.get("logs_tail", "")[-1500:] for n, f in fns.items() if f.get("logs_tail")}}


def _meta(ws: Path, raw: dict[str, Any], info: dict[str, Any], ref: str | None) -> dict[str, Any]:
    import hashlib
    import time
    return {"judge_version": info["manifest"]["version"], "judge_frozen_at": info["manifest"].get("frozen_at"),
            "code": verify._code_state(ws, ref), "mode": info["mode"], "backend": raw.get("backend"),
            "adapter_map_sha": hashlib.sha256(adapter.map_path(ws).read_bytes()).hexdigest(),
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "seconds": raw.get("seconds"),
            "run_dir": raw.get("out_dir"),
            **({"disclaimer": verify.local_disclaimer(info["model"])} if info["mode"] == "local" else {})}


def _reaching(model: "verify.Model", functions: list[str]) -> set[str]:
    """Functions whose steps reach these: a consumer has no route of its own, it runs when
    its producer's message is delivered. Without a declared producer, any function may be one."""
    wanted = set(functions)
    for name in functions:
        for queue in (model.functions.get(name) or {}).get("sqs") or []:
            producers = {n for n, f in model.bp_functions.items() if queue in str(f.get("publishes_to") or "")}
            wanted |= producers or set(model.functions)
    return wanted


def _cases_of_functions(ws: Path, model: "verify.Model", functions: list[str]) -> list[str]:
    """The judge cases that reach one of these functions."""
    facts = Facts(ws)
    amap = adapter.load_map(ws) or {}
    dropped = _dropped_endpoints(amap)
    wanted = _reaching(model, functions)
    found = []
    for case in load_assets(ws)["cases"]:
        if "_load_error" in case:
            continue
        routed = {s["route"].get("function") for s in _route_steps(model, facts, case, dropped)}
        if routed & wanted:
            found.append(case["id"])
    return found


def run_judge(ws: Path | str, ref: str | None = None, mode: str = "auto", cases: list[str] | None = None,
              functions: list[str] | None = None) -> dict[str, Any]:
    """V2: the frozen judge on the target (the working tree, or git `ref` of the workspace
    repo). functions: only the cases that reach these functions (O2 on a candidate). Writes
    verification/verdict_judge.json when all cases run."""
    ws = Path(ws).resolve()
    if functions:
        try:
            cases = _cases_of_functions(ws, verify.Model(verify.export_ref(ws, ref) if ref else ws), functions)
        except (JudgeError, verify.VerifyError, adapter.AdapterError) as exc:
            return {"status": "ERROR", "error": str(exc)}
        if not cases:
            return {"status": "PASS", "summary": {"cases": {}, "fault_runs": {}}, "failures": [], "failures_total": 0,
                    "verdict": None, "note": f"no judge case reaches {functions}"}
    flt = (lambda u: u["case"] in cases) if cases else None
    try:
        raw, info = _judge_job(ws, ref, mode, flt, label=f"judge-{ref}" if ref else "judge")
    except (JudgeError, verify.VerifyError, adapter.AdapterError) as exc:
        return {"status": "ERROR", "error": str(exc)}
    res = _judge_results(ws, raw, info)
    meta = _meta(ws, raw, info, ref)
    bad = [k for k, v in res["summary"]["cases"].items() if k in ("fail", "infra_error")] + \
          [k for k, v in res["summary"]["fault_runs"].items() if k in ("fail", "infra_error")]
    status = "PASS" if not bad else "FAIL"
    doc = {"meta": meta, "status": status, **res}
    if not cases:
        _write(ws.joinpath(*VERDICT_FILE), doc)
    failures = [{"case": cid, "steps": {n: s for n, s in r.get("steps", {}).items() if s["verdict"] == "fail"},
                 **{k: r[k] for k in ("tables", "unawaited", "error") if r.get(k)}}
                for cid, r in res["cases"].items() if r["verdict"] in ("fail", "infra_error")]
    return {"status": status, "summary": res["summary"], "mode": info["mode"], "backend": raw.get("backend"),
            "verdict": "/".join(VERDICT_FILE) if not cases else None, "failures": failures[:15],
            "failures_total": len(failures), "seconds": raw.get("seconds"), "run_dir": raw.get("out_dir")}


def run_judge_self_test(ws: Path | str, functions: list[str] | None = None) -> dict[str, Any]:
    """I1 (N5.2): the judge's cases on the code written so far, before any template exists:
    routes and environment from the blueprint, on this machine (no Lambda container). Only the
    cases whose every step reaches a function with code. Advisory, nothing is written: V2 is
    the judge's verdict, on the built template."""
    ws = Path(ws).resolve()
    try:
        model = verify.BlueprintModel(ws)
        unknown = sorted(set(functions or []) - set(model.bp_functions))
        if unknown:
            return {"status": "ERROR", "error": f"not in the blueprint: {unknown}"}
        wanted = _reaching(model, functions or sorted(model.functions))
        facts = Facts(ws)
        dropped = _dropped_endpoints(adapter.load_map(ws) or {})
        picked = []
        for case in load_assets(ws)["cases"]:
            if "_load_error" in case:
                continue
            routes = [s["route"] for s in _route_steps(model, facts, case, dropped)]
            reached = {r.get("function") for r in routes if r["kind"] == "invoke"}
            if reached & wanted and not any(r["kind"] == "no_route" for r in routes):
                picked.append(case["id"])
        if not picked:
            return {"status": "ERROR", "error": f"no judge case can run: none reaches {sorted(wanted)} with code for all its steps",
                    "functions_with_code": sorted(model.functions)}
        raw, info = _judge_job(ws, None, "local", lambda u: u["case"] in picked, label="selftest", model=model)
    except (JudgeError, verify.VerifyError, adapter.AdapterError) as exc:
        return {"status": "ERROR", "error": str(exc)}
    res = _judge_results(ws, raw, info)
    failures = []
    for cid, r in res["cases"].items():
        steps = {n: s for n, s in (r.get("steps") or {}).items() if s["verdict"] == "fail" and not s.get("answered_by")}
        extra = {k: r[k] for k in ("tables", "unawaited", "error") if r.get(k)}
        if steps or extra:
            failures.append({"case": cid, "steps": steps, **extra})
    return {"status": "PASS" if not failures else "FAIL", "mode": "local", "cases_run": len(picked), "summary": res["summary"],
            "functions_with_code": sorted(model.functions), "failures": failures[:12], "failures_total": len(failures),
            "note": "advisory, on this machine, without a Lambda container: answers the gateway gives itself are not "
                    "counted. Nothing was written; V2 runs the judge on the built template."}


def changed_lines_covered(ws: Path | str, functions: list[str], changed_since: str, assets: str | None = None) -> dict[str, Any]:
    """O2 (N5.3): were the lines changed since `changed_since` executed, by the judge's cases
    or the delta tests? A line one of them executed is covered. Functions with no delta test
    are covered by the judge alone, which is most read paths."""
    ws = Path(ws).resolve()
    assets = assets or verify.DELTA_ASSETS
    try:
        model = verify.Model(ws)
        changed, unmapped = verify._changed_lines(ws, changed_since, model)
        cases = _cases_of_functions(ws, model, functions)
        raw, _ = _judge_job(ws, None, "auto", lambda u: u["case"] in cases, label="judge-coverage", coverage=True)
    except (JudgeError, verify.VerifyError, adapter.AdapterError) as exc:
        return {"status": "ERROR", "error": str(exc)}
    delta = verify.run_coverage(str(ws), functions, changed_since=changed_since, assets=assets)
    delta_fns = ((delta.get("changed_lines") or {}).get("functions") or {}) if isinstance(delta, dict) else {}
    report: dict[str, Any] = {}
    if unmapped:
        report["(unmapped)"] = {"error": "changed code files that belong to no function: " + ", ".join(unmapped)}
    for name, files in changed.items():
        if name not in model.functions or (functions and name not in functions):
            continue
        cov = ((raw.get("functions") or {}).get(name) or {}).get("coverage") or {}
        sides = [delta_fns[name]] if not (delta_fns.get(name) or {}).get("error") and name in delta_fns else []
        if cov.get("report_dir"):
            sides.append(verify._changed_coverage(Path(cov["report_dir"]) / "coverage-final.json", model.functions[name]["logical"], files))
        report[name] = verify.merge_changed_coverage(sides) if sides else {"error": "no test reached this function"}
    return {"status": "PASS", "since": changed_since, "functions": report,
            "all_covered": all(not r.get("uncovered") and not r.get("error") for r in report.values()),
            "sources": {"judge_cases": len(cases), "delta": "see verification/coverage/"}}


def _read_only_case(facts: Facts, case: dict[str, Any]) -> bool:
    endpoints = list(facts.entry_points)
    eps = [_step_endpoint(s, endpoints) for s in case.get("steps") or []]
    return bool(case.get("fixtures")) and all(ep and not facts.writes(ep) for ep in eps)


def _data_step(s: dict[str, Any]) -> bool:
    """A step that read data: the monolith answered 2xx/3xx and a function (not
    the gateway) answered on the target. Errors, validation and auth are not
    about the data mapping; run_judge judges them."""
    st = (s.get("status") or {}).get("monolith")
    return isinstance(st, int) and st < 400 and not s.get("answered_by")


def adapter_failures(cases: dict[str, Any]) -> tuple[dict[str, Any], dict[str, list[str]], int]:
    """(failed, other_differences, data steps) of check_adapter's case results:
    a failure is a status or body mismatch on a data-reading step; header
    mismatches and the other steps' differences are not about the mapping."""
    failed: dict[str, Any] = {}
    other: dict[str, list[str]] = {}
    data_steps = 0
    for cid, r in cases.items():
        if r["verdict"] == "infra_error":
            failed[cid] = {"error": r.get("error")}
            continue
        steps = r.get("steps") or {}
        data_steps += sum(1 for s in steps.values() if _data_step(s))
        bad, rest = {}, []
        for n, s in steps.items():
            if s["verdict"] != "fail":
                continue
            data = [m for m in s.get("mismatches") or [] if not str(m.get("path", "")).startswith(f"steps.{n}.headers.")]
            if _data_step(s) and data:
                bad[n] = data
            else:
                rest.append(n)
        if bad:
            failed[cid] = bad
        if rest:
            other[cid] = sorted(rest)
    return failed, other, data_steps


def check_adapter(ws: Path | str, ref: str | None = None, mode: str = "auto") -> dict[str, Any]:
    """V1: the adapter map is right when the target's read endpoints, on the
    judge's fixtures written through the map, answer what the monolith answered
    on the same data -- status and body of the data-reading steps (2xx on the
    monolith, answered by a function) of the judge's read-only cases that seed
    fixtures. Plus the static check (every table, keys produced, fixtures
    round-trip). Everything else (headers, errors, validation, auth) is listed
    under other_differences and left to run_judge."""
    ws = Path(ws).resolve()
    try:
        facts = Facts(ws)
        assets = load_assets(ws)
        picked = {c["id"] for c in assets["cases"] if "_load_error" not in c and _read_only_case(facts, c)}
        raw, info = _judge_job(ws, ref, mode, lambda u: not u["rules"] and u["case"] in picked, label="adapter")
    except (JudgeError, verify.VerifyError, adapter.AdapterError) as exc:
        return {"status": "ERROR", "error": str(exc), "map_error": isinstance(exc, (AdapterMapError, adapter.AdapterError))}
    res = _judge_results(ws, raw, info)
    failed, other, data_steps = adapter_failures(res["cases"])
    status = "PASS" if data_steps and not failed else "FAIL"
    doc = {"meta": _meta(ws, raw, info, ref), "status": status, "cases": sorted(picked), "data_steps": data_steps,
           "note": "data-reading steps of read-only judge cases over fixtures: a failure means the adapter map, or "
                   "the read itself, differs from the monolith on the same data; differences of other steps "
                   "(headers, errors, validation, auth) are listed under other_differences and left to run_judge",
           "failed": failed, "other_differences": other, "results": res["cases"]}
    _write(ws.joinpath(*CHECK_ADAPTER_FILE), doc)
    return {"status": status, "cases": len(picked), "data_steps": data_steps, "failed": sorted(failed),
            "failures": dict(list(failed.items())[:10]), "other_differences": other,
            "result": "/".join(CHECK_ADAPTER_FILE),
            **({"error": "no data-reading step in the judge's read-only cases over fixtures"} if not data_steps else {})}
