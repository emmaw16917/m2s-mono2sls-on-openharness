"""Verification-line tools (PIPELINE_REDESIGN.md §3, §4.3): run_validate,
run_coverage, run_equivalence.

Test assets live in the workspace under verification/ and are written by the
m2s-verifier agent:

    verification/test_plan.json
    verification/events/<function>/<case>.json   one test case per file
    verification/faults/<scenario>.json          fault-injection scenarios

A test case is written at the spec level (V1 writes them before any code
exists): an HTTP request or SQS records, table fixtures keyed by the
blueprint's table names, and expectations on the response, the tables
afterwards, the SDK calls made and the messages sent. This module turns cases
into Lambda events (emulating API Gateway: path parameters, the Cognito
authorizer's 401 for anonymous requests, claims), runs them through
verify-tools/controller.js (dynalite + recording/fault proxy; handlers in RIE
containers, or local Node without Docker), and judges the raw observations.

Execution always starts from a `sam build` of output/template.yaml, so
packaging problems (Runtime.ImportModuleError) show up here too.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import gates

HERE = Path(__file__).resolve().parent
VERIFY_TOOLS = HERE / "verify-tools"
# Short root: sam build output under a long workspace path hits Windows
# MAX_PATH inside node_modules (seen before, see PERF_OPTIMIZATION_DESIGN A.2).
TMP_ROOT = Path(os.environ.get("M2S_VERIFY_TMP", r"D:\tmp\m2sv"))
DOCKER_BIN = r"C:\Program Files\Docker\Docker\resources\bin"
REGION, ACCOUNT, STACK = "us-east-1", "000000000000", "m2sv"

CASE_KINDS = ("happy", "error", "boundary", "whitebox")
FAULT_KINDS = ("throttle", "error", "validation", "conditional", "network", "tx_conflict")
LOCAL_DISCLAIMER = ("运行时检查在本机 Node 中完成，未在 Lambda 运行时中验证"
                    "（依赖来自 sam build 产物，但 Windows/Node 小版本与 Lambda 不同）")


class VerifyError(ValueError):
    pass


# ---------------------------------------------------------------------------
# Workspace model: template + blueprint
# ---------------------------------------------------------------------------

def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


class Model:
    """What the tools need to know about the implementation, read from
    output/template.yaml (resolved the way CloudFormation would, well enough
    for env vars) plus the blueprint's names, which is what test cases use."""

    def __init__(self, ws: Path, need_template: bool = True):
        self.ws = ws
        bp_path = ws / "storage" / "blueprint.json"
        self.blueprint = _read_json(bp_path) if bp_path.exists() else {}
        an_path = ws / "storage" / "analysis_report.json"
        self.analysis = _read_json(an_path) if an_path.exists() else {}
        self.bp_functions = {f.get("name"): f for f in self.blueprint.get("lambda_functions", []) or [] if isinstance(f, dict)}
        self.bp_tables = [t.get("logical_name") or t.get("name") for t in self.blueprint.get("dynamodb_tables", []) or [] if isinstance(t, dict)]
        self.bp_queues = [q.get("logical_name") or q.get("name") for q in self.blueprint.get("sqs_queues", []) or [] if isinstance(q, dict)]
        self.template: dict[str, Any] = {}
        self.tables: dict[str, dict[str, Any]] = {}
        self.queues: set[str] = set()
        self.functions: dict[str, dict[str, Any]] = {}
        tpath = ws / "output" / "template.yaml"
        if need_template:
            if not tpath.exists():
                raise VerifyError(f"{tpath} not found -- the implementation stage has not produced a template yet")
            self.template = gates.load_template(tpath)
            self._index_template()

    # -- template ----------------------------------------------------------
    def _index_template(self) -> None:
        res = self.template.get("Resources", {}) or {}
        self.params = {k: (v or {}).get("Default") for k, v in (self.template.get("Parameters", {}) or {}).items()}
        for lid, r in res.items():
            if not isinstance(r, dict):
                continue
            t, p = r.get("Type"), r.get("Properties", {}) or {}
            if t == "AWS::DynamoDB::Table":
                create = {"TableName": lid, "KeySchema": p.get("KeySchema"), "AttributeDefinitions": p.get("AttributeDefinitions"),
                          "BillingMode": "PAY_PER_REQUEST"}
                gsis = [{"IndexName": g["IndexName"], "KeySchema": g["KeySchema"], "Projection": g.get("Projection") or {"ProjectionType": "ALL"}}
                        for g in p.get("GlobalSecondaryIndexes", []) or []]
                if gsis:
                    create["GlobalSecondaryIndexes"] = gsis
                lsis = [{"IndexName": g["IndexName"], "KeySchema": g["KeySchema"], "Projection": g.get("Projection") or {"ProjectionType": "ALL"}}
                        for g in p.get("LocalSecondaryIndexes", []) or []]
                if lsis:
                    create["LocalSecondaryIndexes"] = lsis
                self.tables[lid] = {"name": lid, "create": create}
            elif t == "AWS::Serverless::SimpleTable":
                pk = p.get("PrimaryKey") or {"Name": "id", "Type": "String"}
                typ = {"String": "S", "Number": "N", "Binary": "B"}.get(pk.get("Type"), "S")
                self.tables[lid] = {"name": lid, "create": {"TableName": lid, "BillingMode": "PAY_PER_REQUEST",
                                                            "KeySchema": [{"AttributeName": pk["Name"], "KeyType": "HASH"}],
                                                            "AttributeDefinitions": [{"AttributeName": pk["Name"], "AttributeType": typ}]}}
            elif t == "AWS::SQS::Queue":
                self.queues.add(lid)
        glob_fn = ((self.template.get("Globals", {}) or {}).get("Function", {}) or {})
        glob_env = ((glob_fn.get("Environment", {}) or {}).get("Variables", {}) or {})
        glob_api_auth = (((self.template.get("Globals", {}) or {}).get("Api", {}) or {}).get("Auth", {}) or {})
        for lid, r in res.items():
            if not isinstance(r, dict) or r.get("Type") != "AWS::Serverless::Function":
                continue
            p = r.get("Properties", {}) or {}
            code_uri = str(p.get("CodeUri", "")).rstrip("/")
            name = Path(code_uri).name if code_uri else lid
            env = {**glob_env, **((p.get("Environment", {}) or {}).get("Variables", {}) or {})}
            fn = {"name": name, "logical": lid, "handler": p.get("Handler") or glob_fn.get("Handler"),
                  "memory": p.get("MemorySize") or glob_fn.get("MemorySize") or 128,
                  "env": {k: self._resolve(v) for k, v in env.items()}, "api": [], "sqs": []}
            for ev in (p.get("Events", {}) or {}).values():
                if not isinstance(ev, dict):
                    continue
                ep = ev.get("Properties", {}) or {}
                if ev.get("Type") == "Api":
                    fn["api"].append({"path": ep.get("Path"), "method": str(ep.get("Method", "")).upper(),
                                      "authorized": self._api_authorized(ep, glob_api_auth)})
                elif ev.get("Type") == "SQS":
                    fn["sqs"].append(str(ep.get("Queue", "")).split(".")[0])
            self.functions[name] = fn

    def _api_authorized(self, ev_props: dict, glob_auth: dict) -> bool:
        ev_auth = (ev_props.get("Auth") or {}).get("Authorizer")
        if ev_auth is not None:
            return str(ev_auth).upper() != "NONE"
        api_id = ev_props.get("RestApiId")
        api = (self.template.get("Resources", {}) or {}).get(str(api_id)) if api_id else None
        api_auth = ((api or {}).get("Properties", {}) or {}).get("Auth", {}) or glob_auth
        default = api_auth.get("DefaultAuthorizer")
        return bool(default) and str(default).upper() != "NONE"

    def _resolve(self, v: Any) -> str:
        """The CFN loader drops tags, so !Ref X arrives as "X", !GetAtt X.Y as
        "X.Y" and !Sub keeps its ${...} placeholders."""
        if isinstance(v, (int, float, bool)):
            return str(v)
        if isinstance(v, list):  # !Sub [str, {vars}]
            v = v[0] if v else ""
        if not isinstance(v, str):
            return json.dumps(v)

        def one(ref: str) -> str:
            base, _, attr = ref.partition(".")
            pseudo = {"AWS::Region": REGION, "AWS::AccountId": ACCOUNT, "AWS::StackName": STACK, "AWS::Partition": "aws", "AWS::URLSuffix": "amazonaws.com"}
            if ref in pseudo:
                return pseudo[ref]
            if base in self.tables:
                return f"arn:aws:dynamodb:{REGION}:{ACCOUNT}:table/{base}" if attr in ("Arn", "StreamArn") else base
            if base in self.queues:
                if attr == "Arn":
                    return f"arn:aws:sqs:{REGION}:{ACCOUNT}:{base}"
                if attr == "QueueName":
                    return base
                return f"https://sqs.{REGION}.amazonaws.com/{ACCOUNT}/{base}"
            if ref in getattr(self, "params", {}) and self.params[ref] is not None:
                return str(self.params[ref])
            return ref

        if "${" in v:
            return re.sub(r"\$\{([^}]+)\}", lambda m: one(m.group(1)), v)
        return one(v)

    # -- names used in test cases -------------------------------------------
    def table_id(self, alias: str) -> str:
        return self._alias(alias, list(self.tables), "table")

    def queue_id(self, alias: str) -> str:
        return self._alias(alias, sorted(self.queues), "queue")

    @staticmethod
    def _alias(alias: str, ids: list[str], what: str) -> str:
        if alias in ids:
            return alias
        norm = lambda s: re.sub(r"[^a-z0-9]", "", s.lower())
        for suffix in ("", "table", "queue"):
            for i in ids:
                if norm(i) == norm(alias) + suffix:
                    return i
        raise VerifyError(f"unknown {what} {alias!r}; known: {ids}")


# ---------------------------------------------------------------------------
# Test assets
# ---------------------------------------------------------------------------

def _vdir(ws: Path) -> Path:
    return ws / "verification"


def load_cases(ws: Path) -> list[dict[str, Any]]:
    out = []
    for p in sorted((_vdir(ws) / "events").glob("*/*.json")):
        try:
            case = _read_json(p)
        except json.JSONDecodeError as exc:
            case = {"_load_error": f"invalid JSON: {exc}"}
        if isinstance(case, dict):
            case["_file"] = str(p.relative_to(ws)).replace("\\", "/")
            case.setdefault("id", p.stem)
            case.setdefault("function", p.parent.name)
        out.append(case)
    return out


def load_faults(ws: Path) -> list[dict[str, Any]]:
    out = []
    for p in sorted((_vdir(ws) / "faults").glob("*.json")):
        try:
            f = _read_json(p)
        except json.JSONDecodeError as exc:
            f = {"_load_error": f"invalid JSON: {exc}"}
        if isinstance(f, dict):
            f["_file"] = str(p.relative_to(ws)).replace("\\", "/")
            f.setdefault("id", p.stem)
        out.append(f)
    return out


_SPEC_REF_RE = re.compile(r"^(GET|POST|PUT|DELETE|PATCH) (\S+)#(request|responses|business_rules|auth|data_access)(?:\[(\d+)\])?(?:\.\S+)?$")


def _check_spec_ref(model: Model, ref: str) -> str | None:
    res = re.match(r"^blueprint:metadata\.spec_resolutions\[([^\]]+)\]", ref)
    if res:
        ids = [r.get("issue_id") for r in (model.blueprint.get("metadata", {}) or {}).get("spec_resolutions", []) or [] if isinstance(r, dict)]
        return None if res.group(1) in ids else f"spec_ref {ref!r}: blueprint metadata.spec_resolutions has no issue_id {res.group(1)!r} (has {ids})"
    if ref.startswith("blueprint:"):
        return None
    m = _SPEC_REF_RE.match(ref)
    if not m:
        return (f"spec_ref {ref!r} must look like 'POST /api/orders#business_rules[2]' "
                f"(field one of request/responses/business_rules/auth/data_access) or start with 'blueprint:'")
    method, path, field, idx = m.groups()
    ep = next((e for e in model.analysis.get("entry_points", []) if e.get("method") == method and e.get("path") == path), None)
    if ep is None:
        return f"spec_ref {ref!r}: no entry point {method} {path} in analysis_report.json"
    if idx is not None:
        val = ep.get(field)
        if not isinstance(val, list) or int(idx) >= len(val):
            return f"spec_ref {ref!r}: {field} has {len(val) if isinstance(val, list) else 0} items"
    return None


def check_case(model: Model, case: dict[str, Any], with_template: bool) -> list[str]:
    """Schema and name checks for one case; no execution."""
    where = case.get("_file", case.get("id", "?"))
    if "_load_error" in case:
        return [f"{where}: {case['_load_error']}"]
    errs: list[str] = []
    allowed = {"id", "function", "kind", "title", "spec_refs", "request", "sqs", "fixtures", "faults", "expect", "note", "_file"}
    extra = set(case) - allowed
    if extra:
        errs.append(f"{where}: unknown keys {sorted(extra)} (allowed: {sorted(allowed - {'_file'})})")
    fn_name = case.get("function")
    if fn_name not in model.bp_functions:
        errs.append(f"{where}: function {fn_name!r} is not a blueprint lambda_functions name ({sorted(model.bp_functions)})")
        return errs
    bp_fn = model.bp_functions[fn_name]
    if case.get("kind") not in CASE_KINDS:
        errs.append(f"{where}: kind must be one of {CASE_KINDS}")
    refs = case.get("spec_refs")
    if not isinstance(refs, list) or not refs:
        errs.append(f"{where}: spec_refs must be a non-empty list")
    else:
        errs += [f"{where}: {e}" for r in refs if (e := _check_spec_ref(model, str(r)))]
    trigger = bp_fn.get("trigger_type")
    if trigger == "api":
        req = case.get("request")
        if not isinstance(req, dict) or not req.get("method") or not req.get("path"):
            errs.append(f"{where}: api functions need request {{method, path, query?, headers?, body?, identity?}}")
        else:
            bad = set(req) - {"method", "path", "query", "headers", "body", "identity"}
            if bad:
                errs.append(f"{where}: request has unknown keys {sorted(bad)}")
            ident = req.get("identity")
            if ident is not None and (not isinstance(ident, dict) or not ident.get("sub")):
                errs.append(f"{where}: request.identity must be null (anonymous) or an object with at least 'sub'")
            eps = [e.split(" ", 1) for e in bp_fn.get("entry_points") or [] if isinstance(e, str) and " " in e]
            if not any(m.upper() == str(req["method"]).upper() and _path_match(p, str(req["path"])) is not None for m, p in eps):
                errs.append(f"{where}: {req['method']} {req['path']} does not match any entry point of {fn_name} {bp_fn.get('entry_points')}")
    elif trigger == "sqs":
        sqs = case.get("sqs")
        if not isinstance(sqs, dict) or not isinstance(sqs.get("records"), list) or not sqs["records"]:
            errs.append(f"{where}: sqs functions need sqs {{records: [{{body, message_id?}}, ...]}}")
    fixtures = case.get("fixtures", {})
    if not isinstance(fixtures, dict):
        errs.append(f"{where}: fixtures must be an object {{Table: [items]}}")
    else:
        for t, items in fixtures.items():
            errs += _name_err(model, where, "fixtures", t, "table", with_template)
            if not isinstance(items, list) or not all(isinstance(i, dict) for i in items):
                errs.append(f"{where}: fixtures.{t} must be a list of items")
    if "faults" in case:
        errs += _check_rules(model, where, case["faults"], with_template)
    errs += _check_expect(model, where, case.get("expect"), trigger, with_template)
    return errs


def _name_err(model: Model, where: str, ctx: str, name: str, what: str, with_template: bool) -> list[str]:
    try:
        if with_template:
            (model.table_id if what == "table" else model.queue_id)(name)
        else:
            known = model.bp_tables if what == "table" else model.bp_queues
            if name not in known:
                raise VerifyError(f"unknown {what} {name!r}; blueprint names: {known}")
    except VerifyError as exc:
        return [f"{where}: {ctx}: {exc}"]
    return []


def _check_expect(model: Model, where: str, exp: Any, trigger: str | None, with_template: bool) -> list[str]:
    if not isinstance(exp, dict):
        return [f"{where}: expect must be an object"]
    errs = []
    allowed = {"status", "body", "error", "batch_item_failures", "tables_after", "sdk_calls", "messages"}
    if set(exp) - allowed:
        errs.append(f"{where}: expect has unknown keys {sorted(set(exp) - allowed)} (allowed: {sorted(allowed)})")
    if not ({"status", "error", "batch_item_failures"} & set(exp)):
        errs.append(f"{where}: expect needs at least one of status / error / batch_item_failures")
    if "status" in exp and not isinstance(exp["status"], int):
        errs.append(f"{where}: expect.status must be an integer")
    if "body" in exp:
        b = exp["body"]
        if not isinstance(b, dict) or not b or set(b) - {"equals", "subset", "contains", "not_contains", "has_fields", "types", "length"}:
            errs.append(f"{where}: expect.body must use equals/subset/contains/not_contains/has_fields/types/length")
    if "error" in exp and not isinstance(exp["error"], dict):
        errs.append(f"{where}: expect.error must be an object ({{}} or {{type}})")
    if "batch_item_failures" in exp and not isinstance(exp["batch_item_failures"], list):
        errs.append(f"{where}: expect.batch_item_failures must be a list of message ids")
    for t, spec in (exp.get("tables_after") or {}).items():
        errs += _name_err(model, where, "expect.tables_after", t, "table", with_template)
        if not isinstance(spec, dict) or set(spec) - {"contains", "not_contains", "count"}:
            errs.append(f"{where}: expect.tables_after.{t} must use contains/not_contains/count")
    calls = exp.get("sdk_calls")
    if calls is not None:
        if not isinstance(calls, dict) or set(calls) - {"includes", "excludes", "total"}:
            errs.append(f"{where}: expect.sdk_calls must use includes/excludes/total")
        else:
            for c in (calls.get("includes") or []) + (calls.get("excludes") or []):
                if not isinstance(c, dict) or not (c.get("op") or c.get("table")) or set(c) - {"op", "table", "count"}:
                    errs.append(f"{where}: expect.sdk_calls items need 'op' and/or 'table' (plus optional 'count')")
                elif c.get("table"):
                    errs += _name_err(model, where, "expect.sdk_calls", c["table"], "table", with_template)
    for q, spec in (exp.get("messages") or {}).items():
        errs += _name_err(model, where, "expect.messages", q, "queue", with_template)
        if not isinstance(spec, dict) or set(spec) - {"count", "contains"}:
            errs.append(f"{where}: expect.messages.{q} must use count/contains")
    return errs


def check_fault(model: Model, fault: dict[str, Any], case_ids: set[str], with_template: bool) -> list[str]:
    where = fault.get("_file", fault.get("id", "?"))
    if "_load_error" in fault:
        return [f"{where}: {fault['_load_error']}"]
    errs = []
    if set(fault) - {"id", "description", "function", "cases", "rules", "_file"}:
        errs.append(f"{where}: unknown keys {sorted(set(fault) - {'id', 'description', 'function', 'cases', 'rules', '_file'})}")
    if fault.get("function") not in model.bp_functions:
        errs.append(f"{where}: function {fault.get('function')!r} is not a blueprint lambda name")
    cases = fault.get("cases")
    if not isinstance(cases, list) or not cases:
        errs.append(f"{where}: cases must be a non-empty list of case ids")
    else:
        errs += [f"{where}: unknown case id {c!r}" for c in cases if c not in case_ids]
    rules = fault.get("rules")
    if not isinstance(rules, list) or not rules:
        errs.append(f"{where}: rules must be a non-empty list")
        return errs
    return errs + _check_rules(model, where, rules, with_template)


def _check_rules(model: Model, where: str, rules: Any, with_template: bool) -> list[str]:
    """Fault rules, used by fault scenarios and by a test case's own `faults`."""
    errs: list[str] = []
    if not isinstance(rules, list) or not rules:
        return [f"{where}: faults/rules must be a non-empty list"]
    for i, r in enumerate(rules):
        if not isinstance(r, dict) or r.get("kind") not in FAULT_KINDS:
            errs.append(f"{where}: rules[{i}].kind must be one of {FAULT_KINDS}")
            continue
        nth = r.get("nth", "all")
        if not (nth == "all" or (isinstance(nth, int) and nth >= 1) or (isinstance(nth, list) and all(isinstance(x, int) for x in nth))):
            errs.append(f"{where}: rules[{i}].nth must be 'all', a positive integer or a list of them")
        m = r.get("match") or {}
        if not isinstance(m, dict) or set(m) - {"op", "table", "queue", "item"}:
            errs.append(f"{where}: rules[{i}].match may only have op / table / queue / item")
        elif "item" in m and (not isinstance(m["item"], dict) or not m["item"]):
            errs.append(f"{where}: rules[{i}].match.item must be a non-empty object of attribute values, e.g. {{\"pk\": \"...\"}}")
        else:
            if m.get("table"):
                errs += _name_err(model, where, f"rules[{i}].match", m["table"], "table", with_template)
            if m.get("queue"):
                errs += _name_err(model, where, f"rules[{i}].match", m["queue"], "queue", with_template)
    return errs


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------

def _path_match(pattern: str, path: str) -> dict[str, str] | None:
    """Match a template/blueprint path ('/api/books/{id}' or ':id') against a
    concrete path; returns path parameters or None."""
    regex = re.sub(r"\\\{(\w+)(?:\\\+)?\\\}|:(\w+)",lambda m: f"(?P<{m.group(1) or m.group(2)}>[^/]+)", re.escape(pattern))
    m = re.fullmatch(regex, path.split("?")[0])
    return m.groupdict() if m else None


def build_event(model: Model, case: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """(lambda event, gateway_response). gateway_response is set when API
    Gateway itself would answer (anonymous request to an authorized route)."""
    fn = model.functions.get(case["function"])
    if fn is None:
        raise VerifyError(f"function {case['function']!r} has no AWS::Serverless::Function in template.yaml (CodeUri basename)")
    if "sqs" in case:
        records = []
        for i, r in enumerate(case["sqs"]["records"]):
            body = r.get("body")
            body = body if isinstance(body, str) else json.dumps(body)
            mid = r.get("message_id") or f"msg-{i + 1}"
            queue = fn["sqs"][0] if fn["sqs"] else "Queue"
            records.append({"messageId": mid, "receiptHandle": f"rh-{mid}", "body": body,
                            "attributes": {"ApproximateReceiveCount": str(r.get("receive_count", 1)), "SentTimestamp": "1700000000000",
                                           "SenderId": "AIDAVERIFY", "ApproximateFirstReceiveTimestamp": "1700000000000"},
                            "messageAttributes": {}, "md5OfBody": hashlib.md5(body.encode()).hexdigest(),
                            "eventSource": "aws:sqs", "eventSourceARN": f"arn:aws:sqs:{REGION}:{ACCOUNT}:{queue}", "awsRegion": REGION})
        return {"Records": records}, None

    req = case["request"]
    method, path = str(req["method"]).upper(), str(req["path"])
    route = next(((r, pp) for r in fn["api"] if r["method"] in (method, "ANY") and (pp := _path_match(r["path"], path)) is not None), None)
    if route is None:
        raise VerifyError(f"{method} {path} matches no Api event of {case['function']} in template.yaml: {[(r['method'], r['path']) for r in fn['api']]}")
    r, path_params = route
    ident = req.get("identity")
    if r["authorized"] and not ident:
        return None, {"statusCode": 401, "headers": {"Content-Type": "application/json"}, "body": json.dumps({"message": "Unauthorized"})}
    query = req.get("query") or {}
    single = {k: (v[-1] if isinstance(v, list) else str(v)) for k, v in query.items()}
    multi = {k: ([str(x) for x in v] if isinstance(v, list) else [str(v)]) for k, v in query.items()}
    headers = {k: str(v) for k, v in (req.get("headers") or {}).items()}
    body = req.get("body")
    if body is not None and not isinstance(body, str):
        body = json.dumps(body)
        headers.setdefault("Content-Type", "application/json")
    rc: dict[str, Any] = {"resourcePath": r["path"], "httpMethod": method, "path": path, "stage": "Prod",
                          "requestId": "req-" + case["id"], "identity": {"sourceIp": "127.0.0.1"}, "accountId": ACCOUNT}
    if r["authorized"] and ident:
        claims = {"sub": ident["sub"], "cognito:username": ident.get("username", ident["sub"])}
        claims.update({k: str(v) for k, v in ident.items() if k not in ("sub",)})
        rc["authorizer"] = {"claims": claims}
    return {"resource": r["path"], "path": path, "httpMethod": method, "headers": headers or None,
            "multiValueHeaders": {k: [v] for k, v in headers.items()} or None,
            "queryStringParameters": single or None, "multiValueQueryStringParameters": multi or None,
            "pathParameters": path_params or None, "stageVariables": None, "requestContext": rc,
            "body": body, "isBase64Encoded": False}, None


# ---------------------------------------------------------------------------
# Build + run
# ---------------------------------------------------------------------------

def _env() -> dict[str, str]:
    env = dict(os.environ)
    if Path(DOCKER_BIN).exists() and DOCKER_BIN not in env.get("PATH", ""):
        env["PATH"] = env.get("PATH", "") + os.pathsep + DOCKER_BIN
    env["NO_PROXY"] = env["no_proxy"] = "localhost,127.0.0.1,::1,host.docker.internal"
    env["SAM_CLI_TELEMETRY"] = "0"
    return env


def _run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
    """subprocess.run with stdin closed. Inside the MCP stdio server a child
    that inherits the server's stdin pipe hangs at interpreter startup on
    Windows (seen with cfn-lint, see wrap_m2s.validate_sam, and again with
    `sam build` from run_validate: a 9.6MB python.exe that never progressed)."""
    kwargs.setdefault("stdin", subprocess.DEVNULL)
    if sys.platform == "win32":
        kwargs.setdefault("creationflags", subprocess.CREATE_NO_WINDOW)
    return subprocess.run(cmd, **kwargs)


def docker_available() -> bool:
    exe = shutil.which("docker", path=_env()["PATH"])
    if not exe:
        return False
    try:
        return _run([exe, "info", "--format", "{{.ServerVersion}}"], capture_output=True, timeout=20, env=_env()).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _find_sam() -> str:
    for cand in (os.environ.get("M2S_SAM"), shutil.which("sam"),
                 r"C:\Users\36029\miniconda3\envs\mono2esls\Scripts\sam.exe"):
        if cand and Path(cand).exists():
            return cand
    raise VerifyError("sam CLI not found (set M2S_SAM)")


def sam_build(ws: Path) -> Path:
    """sam build output/template.yaml into a short tmp dir, cached by a hash
    of the template and every file under output/lambdas."""
    out = ws / "output"
    h = hashlib.sha256()
    for p in sorted([out / "template.yaml", *[q for q in (out / "lambdas").rglob("*") if q.is_file() and "node_modules" not in q.parts]]):
        h.update(str(p.relative_to(out)).encode())
        h.update(p.read_bytes())
    digest = h.hexdigest()[:12]
    root = TMP_ROOT / re.sub(r"[^A-Za-z0-9_-]", "_", ws.name) / digest
    build_dir = root / "build"
    if (root / "BUILD_OK").exists():
        return build_dir
    root.mkdir(parents=True, exist_ok=True)
    proc = _run([_find_sam(), "build", "--template-file", str(out / "template.yaml"), "--build-dir", str(build_dir)],
                          cwd=str(root), capture_output=True, text=True, encoding="utf-8", errors="replace", env=_env(), timeout=900)
    (root / "sam_build.log").write_text(proc.stdout + "\n" + proc.stderr, encoding="utf-8")
    if proc.returncode != 0:
        raise VerifyError(f"sam build failed (exit {proc.returncode}), a packaging problem; log tail:\n{(proc.stdout + proc.stderr)[-3000:]}")
    (root / "BUILD_OK").write_text(digest, encoding="utf-8")
    return build_dir


def _git(ws: Path, *args: str) -> str:
    p = _run(["git", "-C", str(ws), *args], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if p.returncode != 0:
        raise VerifyError(f"git {' '.join(args)} failed: {p.stderr.strip()}")
    return p.stdout.strip()


def has_own_repo(ws: Path) -> bool:
    return (ws / ".git").is_dir()


def export_ref(ws: Path, ref: str) -> Path:
    """Materialize <ref>:output of the workspace repo into
    TMP_ROOT/<ws>/versions/<sha>/output and return that root."""
    import tarfile
    if not has_own_repo(ws):
        raise VerifyError(f"{ws} has no .git of its own; the v0 snapshot (SKILL.md §6) must exist first")
    sha = _git(ws, "rev-parse", "--short=12", ref)
    root = TMP_ROOT / re.sub(r"[^A-Za-z0-9_-]", "_", ws.name) / "versions" / sha
    if not (root / "output" / "template.yaml").exists():
        root.mkdir(parents=True, exist_ok=True)
        tar = root / "src.tar"
        p = _run(["git", "-C", str(ws), "archive", "--format=tar", "-o", str(tar), ref, "output"], capture_output=True, text=True)
        if p.returncode != 0:
            raise VerifyError(f"git archive {ref} failed: {p.stderr}")
        with tarfile.open(tar) as t:
            t.extractall(root)
        tar.unlink()
    return root


DDB_LOCAL_JAR = "/usr/lib/localstack/dynamodb-local/latest"
DYNALITE_DISCLAIMER = ("DynamoDB 后端是 dynalite（第三方模拟器，没有 Docker 时的降级方案）："
                       "不支持 TransactWriteItems/TransactGetItems，用到事务的用例在这种模式下无法验证")


def _image_present(image: str) -> bool:
    return _run(["docker", "image", "inspect", image], capture_output=True, env=_env()).returncode == 0


def ddb_backend() -> dict[str, Any]:
    """AWS DynamoDB Local in a container when Docker is available (K14):
    the amazon/dynamodb-local image if present, otherwise the official
    DynamoDBLocal.jar that localstack/localstack:3.8 bundles (with its Java).
    Falls back to dynalite without Docker. M2S_DDB_BACKEND=dynalite forces it."""
    if os.environ.get("M2S_DDB_BACKEND") == "dynalite" or not docker_available():
        return {"backend": "dynalite"}
    for image in ("amazon/dynamodb-local:latest", "public.ecr.aws/aws-dynamodb-local/aws-dynamodb-local:latest"):
        if _image_present(image):
            return {"backend": "ddblocal", "ddb_local": {"image": image, "entrypoint": None,
                                                        "args": ["-jar", "DynamoDBLocal.jar", "-inMemory", "-sharedDb"]}}
    if _image_present("localstack/localstack:3.8"):
        return {"backend": "ddblocal", "ddb_local": {"image": "localstack/localstack:3.8", "entrypoint": "java",
                                                    "args": [f"-Djava.library.path={DDB_LOCAL_JAR}/DynamoDBLocal_lib", "-jar",
                                                             f"{DDB_LOCAL_JAR}/DynamoDBLocal.jar", "-inMemory", "-sharedDb", "-port", "8000"]}}
    return {"backend": "dynalite", "note": "no DynamoDB Local image available locally"}


def _run_controller(model: Model, build_dir: Path, runs: list[dict[str, Any]], coverage: bool, mode: str, label: str) -> dict[str, Any]:
    out_dir = TMP_ROOT / re.sub(r"[^A-Za-z0-9_-]", "_", model.ws.name) / "runs" / f"{time.strftime('%Y%m%d-%H%M%S')}-{label}"
    out_dir.mkdir(parents=True, exist_ok=True)
    fn_names = sorted({r["function"] for r in runs})
    functions = []
    for name in fn_names:
        fn = model.functions[name]
        functions.append({"name": name, "dir": str(build_dir / fn["logical"]), "handler": fn["handler"], "env": fn["env"], "memory": fn["memory"]})
    job = {"mode": mode, "coverage": coverage, "out_dir": str(out_dir), "functions": functions, **ddb_backend(),
           "tables": [{"name": t["name"], "create": t["create"]} for t in model.tables.values()], "cases": runs}
    job_path = out_dir / "job.json"
    job_path.write_text(json.dumps(job, indent=1), encoding="utf-8")
    node = shutil.which("node", path=_env()["PATH"]) or "node"
    proc = _run([node, str(VERIFY_TOOLS / "controller.js"), str(job_path)], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=_env(), timeout=3600)
    raw_path = out_dir / "raw_results.json"
    if proc.returncode != 0 or not raw_path.exists():
        raise VerifyError(f"controller failed (exit {proc.returncode}): {proc.stderr[-3000:]}")
    raw = _read_json(raw_path)
    raw["out_dir"] = str(out_dir)
    return raw


def _prepare_runs(model: Model, cases: list[dict[str, Any]], faults_for: dict[str, list[dict]] | None = None,
                  pinned: dict[str, list[dict]] | None = None) -> list[dict[str, Any]]:
    runs = []
    for c in cases:
        event, gw = build_event(model, c)
        fixtures = {model.table_id(t): items for t, items in (c.get("fixtures") or {}).items()}
        case_faults = [_resolve_rule(model, r) for r in c.get("faults") or []]
        run = {"id": c["id"], "run_key": c["id"], "function": c["function"], "event": event, "gateway_response": gw, "fixtures": fixtures,
               "faults": case_faults, "case_faults": case_faults}
        runs.append(run)
    if faults_for:
        by_id = {r["id"]: r for r in runs}
        runs = []
        for scen_id, scen in faults_for.items():
            for cid in scen["cases"]:
                base = dict(by_id[cid])
                base["run_key"] = f"{scen_id}::{cid}"
                if pinned is not None:
                    base["faults"] = pinned.get(base["run_key"], [])
                else:
                    base["faults"] = base["case_faults"] + [_resolve_rule(model, r) for r in scen["rules"]]
                runs.append(base)
    return runs


def _resolve_rule(model: Model, rule: dict[str, Any]) -> dict[str, Any]:
    m = dict(rule.get("match") or {})
    if m.get("table"):
        m["table"] = model.table_id(m["table"])
    if m.get("queue"):
        m["table"] = model.queue_id(m.pop("queue"))
    return {"match": m, "nth": rule.get("nth", "all"), "kind": rule["kind"]}


# ---------------------------------------------------------------------------
# Judging
# ---------------------------------------------------------------------------

IDEMPOTENT_OPS = {"GetItem", "BatchGetItem", "Query", "Scan", "PutItem", "DeleteItem"}
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


def _parse_body(result: Any) -> Any:
    if isinstance(result, dict) and isinstance(result.get("body"), str):
        try:
            return json.loads(result["body"])
        except json.JSONDecodeError:
            return result["body"]
    return None


def _subset(exp: Any, act: Any) -> bool:
    if isinstance(exp, dict):
        return isinstance(act, dict) and all(k in act and _subset(v, act[k]) for k, v in exp.items())
    if isinstance(exp, list):
        return isinstance(act, list) and len(exp) == len(act) and all(_subset(e, a) for e, a in zip(exp, act))
    if isinstance(exp, (int, float)) and isinstance(act, (int, float)) and not isinstance(exp, bool):
        return exp == act
    return exp == act


def _type_of(v: Any) -> str:
    return {dict: "object", list: "array", str: "string", bool: "boolean", type(None): "null"}.get(type(v), "number" if isinstance(v, (int, float)) else "unknown")


def _get(obj: Any, dotted: str) -> tuple[bool, Any]:
    cur = obj
    for part in dotted.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            return False, None
    return True, cur


def judge(model: Model, case: dict[str, Any], raw: dict[str, Any]) -> list[str]:
    """Mismatches between one case's expectations and its raw observation."""
    exp = case.get("expect") or {}
    if raw.get("infra_error"):
        return [f"infra error (not a verdict on the code): {raw['infra_error'][:500]}"]
    mism: list[str] = []
    result, error = raw.get("result"), raw.get("error")
    if "error" in exp:
        if not error:
            mism.append(f"expected the handler to throw{(' ' + exp['error']['type']) if exp['error'].get('type') else ''}, it returned {json.dumps(result)[:300]}")
        elif exp["error"].get("type") and exp["error"]["type"] != error.get("type"):
            mism.append(f"expected error type {exp['error']['type']}, got {error.get('type')}: {error.get('message')}")
    elif error:
        mism.append(f"handler threw {error.get('type')}: {error.get('message')}")
        return mism
    if "status" in exp:
        got = result.get("statusCode") if isinstance(result, dict) else None
        if got != exp["status"]:
            mism.append(f"status: expected {exp['status']}, got {got} (body {str(_parse_body(result))[:300]})")
    if "body" in exp:
        body, spec = _parse_body(result), exp["body"]
        if "equals" in spec and body != spec["equals"]:
            mism.append(f"body: expected equals {json.dumps(spec['equals'])[:300]}, got {json.dumps(body)[:300]}")
        if "subset" in spec and not _subset(spec["subset"], body):
            mism.append(f"body: expected subset {json.dumps(spec['subset'])[:300]}, got {json.dumps(body)[:300]}")
        for item in spec.get("contains") or []:
            if not (isinstance(body, list) and any(_subset(item, b) for b in body)):
                mism.append(f"body: expected an element matching {json.dumps(item)[:200]}, got {json.dumps(body)[:300]}")
        for item in spec.get("not_contains") or []:
            if isinstance(body, list) and any(_subset(item, b) for b in body):
                mism.append(f"body: expected no element matching {json.dumps(item)[:200]}")
        for f in spec.get("has_fields") or []:
            if not _get(body, f)[0]:
                mism.append(f"body: missing field {f!r} in {json.dumps(body)[:300]}")
        for f, t in (spec.get("types") or {}).items():
            ok, v = _get(body, f)
            if not ok or _type_of(v) != t:
                mism.append(f"body: field {f!r} expected type {t}, got {_type_of(v) if ok else 'missing'}")
        if "length" in spec and (not isinstance(body, list) or len(body) != spec["length"]):
            mism.append(f"body: expected an array of length {spec['length']}, got {len(body) if isinstance(body, list) else _type_of(body)}")
    if "batch_item_failures" in exp:
        got = None
        if isinstance(result, dict) and isinstance(result.get("batchItemFailures"), list):
            got = sorted(str(x.get("itemIdentifier")) for x in result["batchItemFailures"])
        want = sorted(str(x) for x in exp["batch_item_failures"])
        if (got or []) != want:
            mism.append(f"batchItemFailures: expected {want}, got {got}")
    tables = raw.get("tables_after") or {}
    for alias, spec in (exp.get("tables_after") or {}).items():
        items = tables.get(model.table_id(alias), [])
        for it in spec.get("contains") or []:
            if not any(_subset(it, x) for x in items):
                mism.append(f"table {alias}: expected an item matching {json.dumps(it)[:200]}; has {json.dumps(items)[:300]}")
        for it in spec.get("not_contains") or []:
            if any(_subset(it, x) for x in items):
                mism.append(f"table {alias}: expected no item matching {json.dumps(it)[:200]}")
        if "count" in spec and len(items) != spec["count"]:
            mism.append(f"table {alias}: expected {spec['count']} items, has {len(items)}")
    calls = raw.get("calls") or []
    cspec = exp.get("sdk_calls") or {}

    def matching(c: dict) -> list[dict]:
        tid = model.table_id(c["table"]) if c.get("table") else None
        return [x for x in calls if (not c.get("op") or x.get("op") == c["op"]) and (tid is None or x.get("table") == tid or x.get("queue") == tid)]

    def label(c: dict) -> str:
        return f"{c.get('op') or 'any call'}{' on ' + c['table'] if c.get('table') else ''}"

    for c in cspec.get("includes") or []:
        n = len(matching(c))
        if ("count" in c and n != c["count"]) or ("count" not in c and n == 0):
            mism.append(f"sdk_calls: expected {c.get('count', '>=1')} x {label(c)}, got {n}")
    for c in cspec.get("excludes") or []:
        if matching(c):
            mism.append(f"sdk_calls: expected no {label(c)}, got {len(matching(c))}")
    if "total" in cspec and len(calls) != cspec["total"]:
        mism.append(f"sdk_calls: expected {cspec['total']} calls in total, got {len(calls)}: {[x.get('op') for x in calls]}")
    msgs = raw.get("messages") or []
    for alias, spec in (exp.get("messages") or {}).items():
        qid = model.queue_id(alias)
        bodies = []
        for m in msgs:
            if m.get("queue") == qid:
                try:
                    bodies.append(json.loads(m["body"]))
                except (json.JSONDecodeError, TypeError):
                    bodies.append(m.get("body"))
        if "count" in spec and len(bodies) != spec["count"]:
            mism.append(f"messages {alias}: expected {spec['count']}, got {len(bodies)}")
        for it in spec.get("contains") or []:
            if not any(_subset(it, b) for b in bodies):
                mism.append(f"messages {alias}: expected a message matching {json.dumps(it)[:200]}; got {json.dumps(bodies)[:300]}")
    return mism


def _triage_hint(raw: dict[str, Any]) -> str | None:
    """Mechanical first-pass classification (PIPELINE_REDESIGN.md §3.4)."""
    text = json.dumps({k: raw.get(k) for k in ("error", "infra_error", "result")})
    if raw.get("infra_error"):
        return "measurement_tool"
    if "Runtime.ImportModuleError" in text or "Cannot find module" in text:
        return "packaging"
    if any(c.get("error", "").startswith("unsupported protocol") or "not mocked" in str(c.get("backend_error", "")) for c in raw.get("calls") or []):
        return "measurement_tool"
    if any("UnknownOperation" in str(c.get("backend_error", "")) for c in raw.get("calls") or []):
        return "measurement_tool"  # e.g. transactions on the dynalite fallback
    if "node_modules" in text:
        return "mock_shape_suspect"
    return None


# ---------------------------------------------------------------------------
# Normalization for baselines (UUIDs, timestamps)
# ---------------------------------------------------------------------------

def normalize(v: Any, now_ms: float | None = None) -> Any:
    now_ms = now_ms or time.time() * 1000
    if isinstance(v, dict):
        return {k: normalize(x, now_ms) for k, x in sorted(v.items())}
    if isinstance(v, list):
        return [normalize(x, now_ms) for x in v]
    if isinstance(v, str):
        if _UUID_RE.match(v):
            return "<uuid>"
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}T[\d:.]+Z?", v):
            return "<iso-time>"
        if v.startswith("{") or v.startswith("["):
            try:
                return {"<json>": normalize(json.loads(v), now_ms)}
            except json.JSONDecodeError:
                return v
        return v
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        # Timestamps near "now": epoch milliseconds (Date.now()) and epoch
        # seconds (DynamoDB TTL attributes such as expiresAt = now/1000 + 7d).
        # The window is wide because TTLs are set days or months ahead.
        window_s = 400 * 86400
        if abs(v - now_ms) < window_s * 1000:
            return "<epoch-ms>"
        if abs(v - now_ms / 1000) < window_s:
            return "<epoch-s>"
    return v


def _observation(raw: dict[str, Any]) -> dict[str, Any]:
    """What H1 compares: (a) the multiset of calls that took effect -- faulted
    attempts and calls the backend rejected changed nothing, so retries do
    not count twice; (b) the externally visible outcome."""
    effective = [c for c in raw.get("calls") or [] if not c.get("fault") and not c.get("backend_error")]
    encoded = [(c.get("op"), json.dumps(normalize({k: c.get(k) for k in ("op", "table", "queue", "index", "key", "item", "update", "values", "transact")}), sort_keys=True))
               for c in effective]
    # "操作集合": repeating an idempotent operation (a read, the same Put,
    # a Delete) changes nothing, so those compare as a set; UpdateItem (ADD),
    # messages and multi-item writes keep every occurrence.
    idem = sorted({e for op, e in encoded if op in IDEMPOTENT_OPS})
    other = sorted(e for op, e in encoded if op not in IDEMPOTENT_OPS)
    calls = sorted(idem + other)
    result = raw.get("result")
    outcome = {"error": (raw.get("error") or {}).get("type"), "result": normalize(result),
               "messages": sorted(json.dumps(normalize(m), sort_keys=True) for m in raw.get("messages") or []),
               "tables_after": {t: sorted((normalize(i) for i in items), key=lambda x: json.dumps(x, sort_keys=True))
                                for t, items in sorted((raw.get("tables_after") or {}).items())}}
    return {"calls": [json.loads(c) for c in calls], "outcome": outcome,
            "attempts": len(raw.get("calls") or []), "calls_after_return": sum(1 for c in raw.get("calls") or [] if c.get("after_return")),
            "conflicts": sum(1 for c in raw.get("calls") or [] if c.get("conflict")),
            "faults_applied": _pins(raw)}


def _pins(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """The faults a run actually applied, as rules pinned to the concrete
    operation (op, table, identity, n-th attempt on it) instead of "the n-th
    matching call" -- so the same operations fail in a version whose call
    order differs."""
    return [{"match": {"op": c["op"], "table": c.get("table") or c.get("queue"), "identity": c.get("identity", "")},
             "nth": c.get("occurrence", 1), "kind": c["fault"]}
            for c in raw.get("calls") or [] if c.get("fault")]


# ---------------------------------------------------------------------------
# Public tools
# ---------------------------------------------------------------------------

def _mode(requested: str) -> str:
    if requested in ("rie", "local"):
        return requested
    return "rie" if docker_available() else "local"


def _select(cases: list[dict], functions: list[str] | None, case_ids: list[str] | None) -> list[dict]:
    out = [c for c in cases if not functions or c.get("function") in functions]
    return [c for c in out if not case_ids or c.get("id") in case_ids]


def _code_state(ws: Path, ref: str | None) -> dict[str, Any]:
    """Which code a verdict describes: a git ref, or the working tree with
    any uncommitted changes under output/ listed (e.g. an S5 candidate)."""
    if not has_own_repo(ws):
        return {"code": "working tree", "repo": False}
    if ref:
        return {"code": ref, "commit": _git(ws, "rev-parse", "--short", ref)}
    p = _run(["git", "-C", str(ws), "status", "--porcelain", "--", "output"], capture_output=True, text=True, encoding="utf-8", errors="replace")
    return {"code": "working tree", "head": _git(ws, "rev-parse", "--short", "HEAD"),
            "uncommitted_output_changes": sorted(l[3:] for l in p.stdout.splitlines() if l.strip())}


def run_validate(workspace: str, functions: list[str] | None = None, cases: list[str] | None = None,
                 dry_run: bool = False, mode: str = "auto", ref: str | None = None) -> dict[str, Any]:
    """ref: run the working tree's test assets against the code of this git
    ref of the workspace repo (e.g. "v0" after the tests were revised)
    instead of the working tree's code."""
    ws = Path(workspace)
    model = Model(ws, need_template=not dry_run)
    all_cases = load_cases(ws)
    if not all_cases:
        raise VerifyError("no test cases under verification/events/<function>/*.json")
    ids = [c.get("id") for c in all_cases]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    schema_errors = [f"duplicate case id {d!r}" for d in dupes]
    for c in all_cases:
        schema_errors += check_case(model, c, with_template=not dry_run)
    faults = load_faults(ws)
    for f in faults:
        schema_errors += check_fault(model, f, set(ids), with_template=not dry_run)
    selected = _select(all_cases, functions, cases)
    if dry_run or schema_errors:
        uncovered_fns = sorted(set(model.bp_functions) - {c.get("function") for c in all_cases})
        return {"status": "PASS" if not schema_errors else "FAIL", "dry_run": True, "schema_errors": schema_errors,
                "cases": len(all_cases), "fault_scenarios": len(faults), "functions_without_cases": uncovered_fns,
                "note": "" if dry_run else "schema errors must be fixed before cases can run"}
    mode = _mode(mode)
    if ref:
        code_root = export_ref(ws, ref)
        run_model = Model(code_root)
    else:
        code_root, run_model = ws, model
    build_dir = sam_build(code_root)
    raw = _run_controller(run_model, build_dir, _prepare_runs(model, selected), coverage=False, mode=mode, label=f"validate-{ref}" if ref else "validate")
    by_key = {r["run_key"]: r for r in raw["cases"]}
    results = []
    for c in selected:
        r = by_key.get(c["id"], {"infra_error": "case was not run"})
        mism = judge(model, c, r)
        entry = {"id": c["id"], "function": c["function"], "kind": c.get("kind"), "passed": not mism, "mismatches": mism,
                 "status": (r.get("result") or {}).get("statusCode") if isinstance(r.get("result"), dict) else None,
                 "error": r.get("error"), "gateway": bool(r.get("gateway")),
                 "sdk_calls": [f"{x.get('op')}:{x.get('table') or x.get('queue') or ''}{'!' + x['fault'] if x.get('fault') else '!conflict' if x.get('conflict') else ''}" for x in r.get("calls") or []]}
        if mism:
            entry["triage_hint"] = _triage_hint(r)
            fres = raw["functions"].get(c["function"], {})
            entry["logs_tail"] = (fres.get("logs_tail") or "")[-1500:]
        results.append(entry)
    failed = [r for r in results if not r["passed"]]
    disclaimers = [d for d in (LOCAL_DISCLAIMER if mode == "local" else "", DYNALITE_DISCLAIMER if raw.get("backend") == "dynalite" else "") if d]
    summary = {"status": "PASS" if not failed else "FAIL", "mode": mode, "backend": raw.get("backend"), "total": len(results),
               "passed": len(results) - len(failed), "failed": len(failed), "failures": failed,
               "cases_with_conflicts": [r["id"] for r in results if any(x.endswith("!conflict") for x in r["sdk_calls"])],
               "disclaimer": "；".join(disclaimers), "run_dir": raw["out_dir"], "code_state": _code_state(ws, ref)}
    out = _vdir(ws) / "results"
    out.mkdir(parents=True, exist_ok=True)
    (out / "validate_latest.json").write_text(json.dumps({**summary, "results": results}, indent=2, ensure_ascii=False), encoding="utf-8")
    if not functions and not cases:
        verdict = {"stage": "A", "status": summary["status"], "mode": mode, "backend": summary["backend"], "total": summary["total"], "passed": summary["passed"],
                   "failed": [{"id": f["id"], "function": f["function"], "mismatches": f["mismatches"], "triage_hint": f.get("triage_hint")} for f in failed],
                   "disclaimer": summary["disclaimer"], "code_state": summary["code_state"], "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S")}
        (_vdir(ws) / "verdict_A.json").write_text(json.dumps(verdict, indent=2, ensure_ascii=False), encoding="utf-8")
        summary["verdict_path"] = "verification/verdict_A.json"
    return summary


def _changed_lines(ws: Path, ref: str) -> dict[str, dict[str, set[int]]]:
    """{function: {path-inside-function-dir: {new line numbers}}} for every
    line under output/lambdas that differs from <ref> (added or modified;
    untracked files count as fully changed)."""
    def git(*args: str) -> str:
        p = _run(["git", "-C", str(ws), *args], capture_output=True, text=True, encoding="utf-8", errors="replace")
        if p.returncode != 0:
            raise VerifyError(f"git {' '.join(args)} failed: {p.stderr.strip()}")
        return p.stdout
    out: dict[str, dict[str, set[int]]] = {}

    def add(path: str, lines: set[int]) -> None:
        parts = path.split("/")
        if len(parts) < 5 or parts[:2] != ["output", "lambdas"]:
            return
        out.setdefault(parts[3], {}).setdefault("/".join(parts[4:]), set()).update(lines)

    cur = None
    for line in git("diff", "-U0", ref, "--", "output/lambdas").splitlines():
        if line.startswith("+++ "):
            cur = line[6:] if line.startswith("+++ b/") else None
        elif line.startswith("@@") and cur:
            m = re.match(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", line)
            if m:
                start, n = int(m.group(1)), int(m.group(2) or 1)
                add(cur, set(range(start, start + n)))
    for path in git("ls-files", "--others", "--exclude-standard", "--", "output/lambdas").splitlines():
        n = len((ws / path).read_text(encoding="utf-8", errors="ignore").splitlines())
        add(path, set(range(1, n + 1)))
    return out


def _changed_coverage(final_json: Path, logical: str, changed: dict[str, set[int]]) -> dict[str, Any]:
    data = _read_json(final_json)
    covered, uncovered = [], []
    for fpath, fc in data.items():
        rel = fpath.replace("\\", "/").split(f"/{logical}/", 1)[-1]
        lines = changed.get(rel)
        if not lines:
            continue
        src = Path(fpath).read_text(encoding="utf-8", errors="ignore").splitlines() if Path(fpath).exists() else []
        for ln in sorted(lines):
            hits = [fc["s"][sid] for sid, loc in fc["statementMap"].items() if loc["start"]["line"] <= ln <= loc["end"]["line"]]
            if not hits:
                continue  # not executable (comment, brace, blank)
            (covered if any(h > 0 for h in hits) else uncovered).append(
                {"file": rel, "line": ln, "code": src[ln - 1].strip()[:160] if 0 < ln <= len(src) else ""})
    return {"changed_executable_lines": len(covered) + len(uncovered), "covered": len(covered), "uncovered": uncovered}


def run_coverage(workspace: str, functions: list[str] | None = None, mode: str = "auto",
                 changed_since: str | None = None) -> dict[str, Any]:
    ws = Path(workspace)
    model = Model(ws)
    selected = _select(load_cases(ws), functions, None)
    errs = [e for c in selected for e in check_case(model, c, with_template=True)]
    if errs:
        return {"status": "FAIL", "schema_errors": errs}
    mode = _mode(mode)
    build_dir = sam_build(ws)
    raw = _run_controller(model, build_dir, _prepare_runs(model, selected), coverage=True, mode=mode, label="coverage")
    out = _vdir(ws) / "coverage"
    out.mkdir(parents=True, exist_ok=True)
    per_fn = {}
    for name, fres in raw["functions"].items():
        cov = fres.get("coverage") or {}
        if cov.get("error") or not cov.get("report_dir"):
            per_fn[name] = {"error": cov.get("error", "no coverage collected")}
            continue
        per_fn[name] = _summarize_coverage(Path(cov["report_dir"]) / "coverage-final.json", Path(model.functions[name]["logical"]))
        (out / f"{name}.json").write_text(json.dumps(per_fn[name], indent=2, ensure_ascii=False), encoding="utf-8")
    changed_report = None
    if changed_since:
        changed = _changed_lines(ws, changed_since)
        changed_report = {}
        for name, files in changed.items():
            cov = (raw["functions"].get(name) or {}).get("coverage") or {}
            if name not in model.functions:
                continue
            if not cov.get("report_dir"):
                changed_report[name] = {"error": "function was not run (no test cases selected for it)"}
                continue
            changed_report[name] = _changed_coverage(Path(cov["report_dir"]) / "coverage-final.json", model.functions[name]["logical"], files)
    summary = {"mode": mode, "functions": {n: {k: v for k, v in s.items() if k != "uncovered"} | {"uncovered_count": len(s.get("uncovered", []))}
                                           for n, s in per_fn.items()},
               "details": "verification/coverage/<function>.json lists every uncovered branch/statement with its line",
               "disclaimer": LOCAL_DISCLAIMER if mode == "local" else ""}
    if changed_report is not None:
        summary["changed_lines"] = {"since": changed_since, "functions": changed_report,
                                    "all_covered": all(not r.get("uncovered") and not r.get("error") for r in changed_report.values())}
    (out / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    return summary


def _summarize_coverage(final_json: Path, logical: Path) -> dict[str, Any]:
    data = _read_json(final_json)
    files = {}
    uncovered = []
    tot = {"statements": [0, 0], "branches": [0, 0], "functions": [0, 0]}
    for fpath, fc in data.items():
        rel = fpath.replace("\\", "/").split(f"/{logical.name}/", 1)[-1]
        if rel.startswith(".m2s-cov-flush") or "node_modules" in rel:
            continue
        s_hit = sum(1 for v in fc["s"].values() if v > 0)
        b_all = [c for arr in fc["b"].values() for c in arr]
        b_hit = sum(1 for c in b_all if c > 0)
        f_hit = sum(1 for v in fc["f"].values() if v > 0)
        files[rel] = {"statements": f"{s_hit}/{len(fc['s'])}", "branches": f"{b_hit}/{len(b_all)}", "functions": f"{f_hit}/{len(fc['f'])}"}
        for k, (h, n) in (("statements", (s_hit, len(fc["s"]))), ("branches", (b_hit, len(b_all))), ("functions", (f_hit, len(fc["f"])))):
            tot[k][0] += h
            tot[k][1] += n
        src_lines = Path(fpath).read_text(encoding="utf-8", errors="ignore").splitlines() if Path(fpath).exists() else []
        snippet = lambda ln: src_lines[ln - 1].strip()[:160] if 0 < ln <= len(src_lines) else ""
        for bid, counts in fc["b"].items():
            bm = fc["branchMap"][bid]
            for i, c in enumerate(counts):
                if c == 0:
                    loc = (bm.get("locations") or [bm["loc"]])[i] if i < len(bm.get("locations") or []) else bm["loc"]
                    ln = loc["start"]["line"]
                    uncovered.append({"file": rel, "line": ln, "kind": "branch", "branch_type": bm.get("type"), "arm": i, "code": snippet(ln)})
        for sid, c in fc["s"].items():
            if c == 0:
                ln = fc["statementMap"][sid]["start"]["line"]
                uncovered.append({"file": rel, "line": ln, "kind": "statement", "code": snippet(ln)})
    pct = lambda h, n: round(100.0 * h / n, 1) if n else 100.0
    uncovered.sort(key=lambda u: (u["file"], u["line"], u["kind"]))
    return {"statements_pct": pct(*tot["statements"]), "branches_pct": pct(*tot["branches"]), "functions_pct": pct(*tot["functions"]),
            "files": files, "uncovered": uncovered}


def run_equivalence(workspace: str, mode: str = "record", label: str = "v0", functions: list[str] | None = None,
                    runtime: str = "auto", baseline_ref: str | None = "v0") -> dict[str, Any]:
    """record: run every fault scenario on the working tree and store the
    observations as verification/fault_baseline_<label>.json.

    diff: H1 check of the working tree against baseline_ref (a git ref of the
    workspace repo, default v0). The baseline version is rebuilt and run
    first; the faults it actually applied are then pinned to the same
    concrete operations in the working-tree run, so a change of call order
    (e.g. parallelization) does not move a fault onto a different item.
    H1 (a): same multiset of calls that took effect; (b): same externally
    visible outcome (return value / thrown error / batchItemFailures /
    messages / table state); (c): no emulated transaction conflicts where
    the baseline had none. Without a workspace repo it falls back to the
    stored baseline file and the original n-th-call rules."""
    if mode not in ("record", "diff"):
        raise VerifyError("mode must be 'record' or 'diff'")
    ws = Path(workspace)
    model = Model(ws)
    cases = load_cases(ws)
    by_id = {c["id"]: c for c in cases if "id" in c}
    faults = [f for f in load_faults(ws) if not functions or f.get("function") in functions]
    errs = [e for f in faults for e in check_fault(model, f, set(by_id), with_template=True)]
    if not faults:
        errs.append("no fault scenarios under verification/faults/*.json")
    if errs:
        return {"status": "FAIL", "schema_errors": errs}
    scen = {f["id"]: f for f in faults}
    needed = list({cid: by_id[cid] for f in faults for cid in f["cases"]}.values())
    # Without scenario faults every test case of the functions under check
    # runs too (not only the scenarios' cases): a change of concurrency shows
    # on the ordinary cases, e.g. two messages of one batch touching one item.
    plain = [c for c in cases if "id" in c and (not functions or c.get("function") in functions)]
    runtime = _mode(runtime)

    def run_version(m: Model, root: Path, pinned: dict | None, tag: str) -> tuple[dict, dict]:
        build = sam_build(root)
        raw = _run_controller(m, build, _prepare_runs(m, needed, scen, pinned), coverage=False, mode=runtime, label=f"equiv-{tag}")
        plain_runs = _prepare_runs(m, plain)
        if pinned is not None:  # case-level faults pinned to the same operations too
            for r in plain_runs:
                r["faults"] = pinned.get(r["run_key"], [])
        extra = _run_controller(m, build, plain_runs, coverage=False, mode=runtime, label=f"equiv-{tag}-nofault")
        rs = raw["cases"] + extra["cases"]
        return {r["run_key"]: r for r in rs}, {r["run_key"]: r["infra_error"] for r in rs if r.get("infra_error")}

    path = _vdir(ws) / f"fault_baseline_{label}.json"
    if mode == "record":
        raws, infra = run_version(model, ws, None, "record")
        obs = {k: _observation(r) for k, r in raws.items()}
        doc = {"label": label, "runtime": runtime, "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
               "scenarios": {k: {"description": v.get("description", ""), "function": v["function"], "cases": v["cases"], "rules": v["rules"]} for k, v in scen.items()},
               "observations": obs, "infra_errors": infra,
               "note": "v1 records fault behaviour only; whether it is correct is judged in v2 together with monolith diffing"}
        path.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
        return {"status": "RECORDED" if not infra else "RECORDED_WITH_INFRA_ERRORS", "runtime": runtime, "path": str(path.relative_to(ws)),
                "runs": len(obs), "infra_errors": infra, "disclaimer": LOCAL_DISCLAIMER if runtime == "local" else ""}

    if baseline_ref and has_own_repo(ws):
        base_root = export_ref(ws, baseline_ref)
        base_model = Model(base_root)
        base_raws, base_infra = run_version(base_model, base_root, None, "base")
        base = {k: _observation(r) for k, r in base_raws.items()}
        pinned = {k: v["faults_applied"] for k, v in base.items()}
        # Stability check: the baseline run again with the same pinned faults;
        # runs whose two baseline observations differ are measurement noise,
        # not evidence against the change.
        again_raws, again_infra = run_version(base_model, base_root, pinned, "base2")
        again = {k: _observation(r) for k, r in again_raws.items()}
        unstable = sorted(k for k in base if k in again and (base[k]["calls"] != again[k]["calls"] or base[k]["outcome"] != again[k]["outcome"]))
        base_infra = {**base_infra, **{f"rerun:{k}": v for k, v in again_infra.items()}}
        now_raws, infra = run_version(model, ws, pinned, "cand")
        baseline_desc = f"{baseline_ref} ({_git(ws, 'rev-parse', '--short', baseline_ref)}), re-run now; its faults pinned to the same operations in the working tree"
        infra = {**{f"baseline:{k}": v for k, v in base_infra.items()}, **infra}
    else:
        unstable = []
        if not path.exists():
            raise VerifyError(f"{path} not found and no workspace repo to rebuild {baseline_ref} from")
        base = _read_json(path)["observations"]
        now_raws, infra = run_version(model, ws, None, "cand")
        baseline_desc = f"{path.relative_to(ws)} (stored; n-th-call fault rules, order-sensitive)"
    obs = {k: _observation(r) for k, r in now_raws.items()}
    diffs = []
    for key, now in obs.items():
        before = base.get(key)
        if key in unstable:
            continue
        if before is None:
            diffs.append({"run": key, "h1": "n/a", "detail": "not in baseline (new scenario or case)"})
            continue
        if now["calls"] != before["calls"]:
            diffs.append({"run": key, "h1": "a", "detail": "different multiset of calls that took effect", "baseline": before["calls"], "now": now["calls"]})
        if now["outcome"] != before["outcome"]:
            diffs.append({"run": key, "h1": "b", "detail": "different externally visible outcome", "baseline": before["outcome"], "now": now["outcome"]})
        if now.get("conflicts", 0) and not before.get("conflicts", 0):
            # H1 (c): the proxy rejected concurrent requests on one item the
            # way real DynamoDB does. Even when retries hide it here, whether
            # they succeed in production depends on timing.
            diffs.append({"run": key, "h1": "c", "detail": f"introduces transaction conflicts: {now['conflicts']} emulated TransactionConflict "
                          "rejection(s), none in the baseline (concurrent requests touching the same item)"})
    h1 = [d for d in diffs if d["h1"] in ("a", "b", "c")]
    return {"status": "EQUIVALENT" if not h1 and not infra else "DIFFERENT" if h1 else "INFRA_ERROR", "runtime": runtime,
            "baseline": baseline_desc, "runs": len(obs), "differences": diffs, "infra_errors": infra,
            "unstable_in_baseline": unstable,
            "note": ("runs listed in unstable_in_baseline gave different results in two runs of the baseline itself; "
                     "they are excluded from the verdict, so the change is NOT verified on them") if unstable else ""}
