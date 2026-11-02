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
# Contract testing (contract 2, PIPELINE_REDESIGN.md section 3.6): what a case
# may assert about SDK calls -- side effects only, never how often or in what
# order the implementation reads.
READ_OPS = ("GetItem", "BatchGetItem", "Query", "Scan")
WRITE_OPS = ("PutItem", "UpdateItem", "DeleteItem", "BatchWriteItem", "TransactWriteItems", "SendMessage", "SendMessageBatch")
LOCAL_DISCLAIMER = ("运行时检查在本机 Node 中完成，未在 Lambda 运行时中验证"
                    "（依赖来自 sam build 产物，但 Windows/Node 小版本与 Lambda 不同）")
LOCAL_DISCLAIMER_PY = ("运行时检查在本机 Python（{version}）中完成，未在 Lambda 运行时中验证"
                       "（Lambda 是 Python 3.12，boto3 版本也不同）；Python 函数的覆盖率在这种模式下测不了")


def local_disclaimer(model: "Model") -> str:
    """The no-Docker disclaimer, naming the runtimes actually used locally."""
    runtimes = {str(f.get("runtime", "")) for f in model.functions.values()}
    parts = []
    if not runtimes or any(r.startswith("nodejs") or not r for r in runtimes):
        parts.append(LOCAL_DISCLAIMER)
    if any(r.startswith("python") for r in runtimes):
        parts.append(LOCAL_DISCLAIMER_PY.format(version=sys.version.split()[0]))
    return "；".join(parts)


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
        meta = self.blueprint.get("metadata", {}) or {}
        meta = meta if isinstance(meta, dict) else {}
        # 1: original rules; 2: contract testing (PIPELINE_REDESIGN.md section 3.6).
        self.contract = meta.get("verification_contract", 1)
        self.trace_rulings = {r.get("issue_id") for r in meta.get("spec_resolutions") or []
                              if isinstance(r, dict) and r.get("class") == "implementation-trace"}
        # Attributes an accepted data-model ruling adds only to feed a new
        # index (e.g. a constant GSI partition key): H1 ignores them, everything
        # else of the writes is still compared.
        # Functions an accepted perf spec relaxation (S5 -> S1) rules on: for
        # them H1 compares reads by table even in a contract-1 workspace -- the
        # ruling is what allows a different read operation (Scan -> Query).
        # Functions whose result order an accepted ruling leaves unspecified
        # (result_order: "unspecified"): H1 (b) compares their response arrays
        # as multisets, e.g. after a Scan became a Query on another index.
        self.unordered_functions = {f for r in meta.get("spec_resolutions") or []
                                    if isinstance(r, dict) and r.get("result_order") == "unspecified"
                                    and r.get("ruling", "accepted") == "accepted"
                                    for f in r.get("applies_to") or []}
        self.relaxed_read_functions = {f for r in meta.get("spec_resolutions") or []
                                       if isinstance(r, dict) and r.get("class") == "implementation-trace"
                                       and str(r.get("origin", "")).startswith("perf-request") and r.get("ruling", "accepted") == "accepted"
                                       for f in r.get("applies_to") or []}
        # Accepted bounded_staleness rulings (PERF_OPTIMIZATION.md 10.4 (b)):
        # these functions may answer from a module-level cache for up to
        # ttl_seconds, so (1) every case runs in a fresh execution environment
        # (the cache must not carry one case's fixtures into the next) and
        # (2) H1 (a) ignores their reads of the cached tables (a hit elides them).
        self.cached_tables: dict[str, set[str]] = {}
        for r in meta.get("spec_resolutions") or []:
            bs = r.get("bounded_staleness") if isinstance(r, dict) and r.get("ruling", "accepted") == "accepted" else None
            if isinstance(bs, dict):
                for f in r.get("applies_to") or []:
                    self.cached_tables.setdefault(f, set()).update(str(t) for t in bs.get("tables") or [])
        self.index_attributes = {a.get("name") for r in meta.get("spec_resolutions") or []
                                 if isinstance(r, dict) and r.get("ruling", "accepted") == "accepted"
                                 for a in ((r.get("data_model_change") or {}).get("new_attributes") or [])
                                 if isinstance(a, dict) and a.get("name")}
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
        # Layer logical id -> ContentUri (relative to output/), to map changed
        # layer files to the functions that use the layer.
        self.layers = {lid: str((r.get("Properties") or {}).get("ContentUri", "")).strip().strip("./").rstrip("/")
                       for lid, r in res.items()
                       if isinstance(r, dict) and r.get("Type") in ("AWS::Serverless::LayerVersion", "AWS::Lambda::LayerVersion")}
        glob_env = ((glob_fn.get("Environment", {}) or {}).get("Variables", {}) or {})
        glob_api_auth = (((self.template.get("Globals", {}) or {}).get("Api", {}) or {}).get("Auth", {}) or {})
        glob_http_auth = (((self.template.get("Globals", {}) or {}).get("HttpApi", {}) or {}).get("Auth", {}) or {})
        for lid, r in res.items():
            if not isinstance(r, dict) or r.get("Type") != "AWS::Serverless::Function":
                continue
            p = r.get("Properties", {}) or {}
            code_uri = str(p.get("CodeUri", "")).rstrip("/")
            name = Path(code_uri).name if code_uri else lid
            env = {**glob_env, **((p.get("Environment", {}) or {}).get("Variables", {}) or {})}
            layers = []
            for ly in (p.get("Layers") if p.get("Layers") is not None else glob_fn.get("Layers")) or []:
                ref = ly.get("Ref") if isinstance(ly, dict) else ly
                if isinstance(ref, str) and (res.get(ref) or {}).get("Type") in ("AWS::Serverless::LayerVersion", "AWS::Lambda::LayerVersion"):
                    layers.append(ref)  # logical id; sam build puts it in build/<id>/
            fn = {"name": name, "logical": lid, "handler": p.get("Handler") or glob_fn.get("Handler"),
                  "code_uri": code_uri.strip().strip("./").rstrip("/"),
                  "runtime": str(p.get("Runtime") or glob_fn.get("Runtime") or ""),
                  "layers": layers,
                  "memory": p.get("MemorySize") or glob_fn.get("MemorySize") or 128,
                  # Lambda's default is x86_64; Architectures is a one-element list
                  "architecture": str((p.get("Architectures") or glob_fn.get("Architectures") or ["x86_64"])[0]),
                  "env": {k: self._resolve(v) for k, v in env.items()}, "api": [], "sqs": []}
            for ev in (p.get("Events", {}) or {}).values():
                if not isinstance(ev, dict):
                    continue
                ep = ev.get("Properties", {}) or {}
                if ev.get("Type") in ("Api", "HttpApi"):
                    # HttpApi events carry PayloadFormatVersion '1.0' (gate AT2):
                    # the same event as REST, plus "version": "1.0".
                    fn["api"].append({"path": ep.get("Path"), "method": str(ep.get("Method", "")).upper(),
                                      "authorized": self._api_authorized(ep, glob_api_auth if ev["Type"] == "Api" else glob_http_auth),
                                      "http_api": ev["Type"] == "HttpApi"})
                elif ev.get("Type") == "SQS":
                    fn["sqs"].append(str(ep.get("Queue", "")).split(".")[0])
                    # how the event source mapping delivers (run_judge's SQS delivery)
                    fn.setdefault("sqs_events", []).append({
                        "queue": fn["sqs"][-1], "batch_size": int(ep.get("BatchSize") or 10),
                        "report_failures": "ReportBatchItemFailures" in (ep.get("FunctionResponseTypes") or [])})
            self.functions[name] = fn
        # maxReceiveCount of each queue's redrive policy (None: no DLQ)
        self.queue_max_receive = {
            lid: (((r.get("Properties") or {}).get("RedrivePolicy") or {}).get("maxReceiveCount"))
            for lid, r in res.items() if isinstance(r, dict) and r.get("Type") == "AWS::SQS::Queue"}
        self._index_gateway_responses(res)
        self.http_api_cors_any = any(
            "*" in ((((r.get("Properties") or {}).get("CorsConfiguration") or {}) if isinstance((r.get("Properties") or {}).get("CorsConfiguration"), dict) else {}).get("AllowOrigins") or [])
            for r in res.values() if isinstance(r, dict) and r.get("Type") == "AWS::Serverless::HttpApi")

    def _index_gateway_responses(self, res: dict) -> None:
        """GatewayResponses of the Api (and Globals.Api): the headers API
        Gateway puts on the answers it generates itself, e.g. the 401 of an
        authorizer. SAM writes header values as quoted literals ("'*'") or
        !Sub "'${Param}'"."""
        self.gateway_responses: dict[str, dict[str, Any]] = {}
        glob = (((self.template.get("Globals", {}) or {}).get("Api", {}) or {}).get("GatewayResponses") or {})
        apis = [((r.get("Properties") or {}).get("GatewayResponses") or {}) for r in res.values()
                if isinstance(r, dict) and r.get("Type") == "AWS::Serverless::Api"]
        for src in [glob, *apis]:
            for rtype, spec in (src or {}).items():
                spec = spec or {}
                headers = ((spec.get("ResponseParameters") or {}).get("Headers") or {})
                status = self._resolve(spec["StatusCode"]).strip("'") if spec.get("StatusCode") is not None else ""
                self.gateway_responses[str(rtype)] = {
                    "status": int(status) if status.isdigit() else None,
                    "headers": {str(k): self._resolve(v).strip().strip("'") for k, v in headers.items()},
                }

    def _api_authorized(self, ev_props: dict, glob_auth: dict) -> bool:
        ev_auth = (ev_props.get("Auth") or {}).get("Authorizer")
        if ev_auth is not None:
            return str(ev_auth).upper() != "NONE"
        api_id = ev_props.get("RestApiId") or ev_props.get("ApiId")
        api = (self.template.get("Resources", {}) or {}).get(str(api_id)) if api_id else None
        api_auth = ((api or {}).get("Properties", {}) or {}).get("Auth", {}) or glob_auth
        default = api_auth.get("DefaultAuthorizer")
        return bool(default) and str(default).upper() != "NONE"

    def _resolve(self, v: Any) -> str:
        """The CFN loader drops tags, so !Ref X arrives as "X", !GetAtt X.Y as
        "X.Y" and !Sub keeps its ${...} placeholders."""
        if isinstance(v, (int, float, bool)):
            return str(v)
        if isinstance(v, dict) and len(v) == 1 and next(iter(v)) in ("Ref", "Fn::Sub", "Fn::GetAtt"):
            key, v = next(iter(v.items()))  # long form: {Ref: X} / {Fn::Sub: ...} / {Fn::GetAtt: [X, Y]}
            if key == "Fn::GetAtt" and isinstance(v, list):
                v = ".".join(map(str, v))
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
    allowed = {"id", "function", "kind", "title", "spec_refs", "request", "sqs", "fixtures", "faults", "expect", "note", "repeat", "_file"}
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
    if "repeat" in case and not (isinstance(case["repeat"], int) and not isinstance(case["repeat"], bool) and 2 <= case["repeat"] <= 5):
        errs.append(f"{where}: repeat must be an integer 2..5 (the same request that many times in one execution environment)")
    if "faults" in case:
        errs += _check_rules(model, where, case["faults"], with_template)
    errs += _check_expect(model, where, case.get("expect"), trigger, with_template)
    if isinstance(case.get("expect"), dict) and "alternatives" in case["expect"] and not _si_refs(case):
        errs.append(f"{where}: expect.alternatives is only allowed in a case whose spec_refs cite a "
                    "blueprint:metadata.spec_resolutions[SI-x] ruling that allows more than one outcome")
    return errs


def _si_refs(case: dict[str, Any]) -> list[str]:
    """Issue ids of the spec_resolutions a case cites."""
    return [m.group(1) for r in case.get("spec_refs") or []
            if (m := re.match(r"^blueprint:metadata\.spec_resolutions\[([^\]]+)\]", str(r)))]


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
    if "alternatives" in exp:
        # Any one complete expectation passes (an SI ruling allows several
        # outcomes, e.g. for a failure only an implementation step can cause).
        alts = exp["alternatives"]
        if set(exp) - {"alternatives", "trace"}:
            errs.append(f"{where}: expect with alternatives may only have alternatives / trace at the top level")
        if not isinstance(alts, list) or len(alts) < 2:
            return errs + [f"{where}: expect.alternatives must be a list of at least two expectations"]
        for i, a in enumerate(alts):
            if isinstance(a, dict) and "alternatives" in a:
                errs.append(f"{where}: expect.alternatives[{i}] cannot nest alternatives")
            else:
                errs += _check_expect(model, f"{where}: alternatives[{i}]", a, trigger, with_template)
        return errs
    allowed = {"status", "body", "error", "batch_item_failures", "tables_after", "sdk_calls", "messages", "headers", "trace"}
    if set(exp) - allowed:
        errs.append(f"{where}: expect has unknown keys {sorted(set(exp) - allowed)} (allowed: {sorted(allowed)})")
    if not ({"status", "error", "batch_item_failures"} & set(exp)):
        errs.append(f"{where}: expect needs at least one of status / error / batch_item_failures")
    if "status" in exp and not isinstance(exp["status"], int):
        errs.append(f"{where}: expect.status must be an integer")
    if "headers" in exp:
        h = exp["headers"]
        if not isinstance(h, dict) or not h or set(h) - {"equals", "contains", "absent", "count"}:
            errs.append(f"{where}: expect.headers must use equals {{name: value}} / contains {{name: substring or [substrings]}} / absent [names] / count {{name: n}}")
        else:
            if "equals" in h and not (isinstance(h["equals"], dict) and all(isinstance(v, str) for v in h["equals"].values())):
                errs.append(f"{where}: expect.headers.equals must map header names to strings")
            if "contains" in h and not (isinstance(h["contains"], dict) and all(
                    isinstance(v, str) or (isinstance(v, list) and v and all(isinstance(x, str) for x in v)) for v in h["contains"].values())):
                errs.append(f"{where}: expect.headers.contains must map header names to a substring or a non-empty list of substrings")
            if "count" in h and not (isinstance(h["count"], dict) and all(
                    isinstance(v, int) and not isinstance(v, bool) and v >= 0 for v in h["count"].values())):
                errs.append(f"{where}: expect.headers.count must map header names to non-negative integers")
            if "absent" in h and not (isinstance(h["absent"], list) and all(isinstance(v, str) for v in h["absent"])):
                errs.append(f"{where}: expect.headers.absent must be a list of header names")
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
            if model.contract >= 2:
                # Reads are how the implementation works, not what it does:
                # assert the side effects; record anything else in expect.trace.
                if "total" in calls:
                    errs.append(f"{where}: expect.sdk_calls.total is not allowed under verification_contract 2 "
                                "(a call count is implementation trace; put it in expect.trace)")
                for c in calls.get("includes") or []:
                    if isinstance(c, dict) and c.get("op") not in WRITE_OPS:
                        errs.append(f"{where}: expect.sdk_calls.includes may only name write / message operations "
                                    f"under verification_contract 2, got {c.get('op') or 'any op on ' + str(c.get('table'))} "
                                    "(assert the effect in tables_after / messages, or record it in expect.trace)")
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
        if model.contract >= 2 and isinstance(m, dict) and m.get("op") in READ_OPS:
            errs.append(f"{where}: rules[{i}].match.op {m['op']!r} names a read operation; under verification_contract 2 a read "
                        "fault targets the read of a table, not the operation the implementation happens to use: "
                        "match by table / item (op omitted, or \"@read\")")
        if model.contract >= 2 and nth != "all" and isinstance(m, dict) and "item" not in m:
            errs.append(f"{where}: rules[{i}] selects matching call(s) {nth} by position; under verification_contract 2 a rule "
                        "with nth other than 'all' needs match.item (nth then counts operations on that item)")
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
    """Match a template/blueprint path ('/api/books/{id}', ':id' or the Flask
    '/books/<id>' / '/books/<int:id>') against a concrete path; returns path
    parameters or None."""
    regex = re.sub(r"\\\{(\w+)(?:\\\+)?\\\}|:(\w+)|<(?:\w+:)?(\w+)>", lambda m: f"(?P<{m.group(1) or m.group(2) or m.group(3)}>[^/]+)", re.escape(pattern))
    m = re.fullmatch(regex, path.split("?")[0])
    return m.groupdict() if m else None


def find_route(model: Model, method: str, path: str) -> tuple[str, dict[str, Any]] | None:
    """(function, Api event) serving method + path on the target, as API
    Gateway picks it: a static route wins over a parameterized one."""
    hits = [(name, r) for name, fn in model.functions.items() for r in fn["api"]
            if r["method"] in (method.upper(), "ANY") and _path_match(r["path"], path) is not None]
    if not hits:
        return None
    return min(hits, key=lambda h: (len(re.findall(r"[{<:]", h[1]["path"])), h[1]["method"] == "ANY"))


def _claim(v: Any, http_api: bool) -> str:
    """A claim value as the gateway hands it to the function: every claim is
    a string; a list claim (cognito:groups) is 'a,b' on a REST Cognito
    authorizer and '[a b]' on an HTTP API JWT authorizer (AWS probe
    2026-10-10, results-e2e/jwt-probe). Handlers must parse both."""
    if isinstance(v, list):
        return "[" + " ".join(map(str, v)) + "]" if http_api else ",".join(map(str, v))
    return str(v)


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
        if r.get("http_api"):
            # HTTP API: no GatewayResponses; a CorsConfiguration that allows
            # '*' still puts Access-Control-Allow-Origin: * on the 401 when
            # the request has an Origin (AWS probe 2026-10-10).
            has_origin = any(k.lower() == "origin" for k in (req.get("headers") or {}))
            cors = {"Access-Control-Allow-Origin": "*"} if has_origin and model.http_api_cors_any else {}
            return None, {"statusCode": 401, "headers": {"Content-Type": "application/json", **cors},
                          "body": json.dumps({"message": "Unauthorized"})}
        gw = getattr(model, "gateway_responses", {})
        spec = gw.get("UNAUTHORIZED") or gw.get("DEFAULT_4XX") or {}
        return None, {"statusCode": spec.get("status") or 401,
                      "headers": {"Content-Type": "application/json", **(spec.get("headers") or {})},
                      "body": json.dumps({"message": "Unauthorized"})}
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
        claims.update({k: _claim(v, bool(r.get("http_api"))) for k, v in ident.items() if k not in ("sub",)})
        rc["authorizer"] = {"claims": claims}
    version = {"version": "1.0"} if r.get("http_api") else {}
    return {**version, "resource": r["path"], "path": path, "httpMethod": method, "headers": headers or None,
            "multiValueHeaders": {k: [v] for k, v in headers.items()} or None,
            "queryStringParameters": single or None, "multiValueQueryStringParameters": multi or None,
            "pathParameters": path_params or None, "stageVariables": None, "requestContext": rc,
            "body": body, "isBase64Encoded": False}, None


# ---------------------------------------------------------------------------
# Build + run
# ---------------------------------------------------------------------------

# sam build of a python3.12 function needs a local Python 3.12 on PATH; the
# MCP server runs in a 3.11 conda env, the 3.12 one sits next to it.
PY312_DIR = Path(os.environ["M2S_PY312_DIR"]) if os.environ.get("M2S_PY312_DIR") else Path(sys.executable).resolve().parent.parent / "m2s-py312"


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
    # Layers are part of the build too: leaving them out reused a stale build
    # after a layer changed.
    sources = [q for d in ("lambdas", "layers") if (out / d).is_dir() for q in (out / d).rglob("*")
               if q.is_file() and "node_modules" not in q.parts and "__pycache__" not in q.parts]
    for p in sorted([out / "template.yaml", *sources]):
        h.update(str(p.relative_to(out)).encode())
        h.update(p.read_bytes())
    digest = h.hexdigest()[:12]
    root = TMP_ROOT / re.sub(r"[^A-Za-z0-9_-]", "_", ws.name) / digest
    build_dir = root / "build"
    if (root / "BUILD_OK").exists():
        return build_dir
    root.mkdir(parents=True, exist_ok=True)
    env = _env()
    if (PY312_DIR / "python.exe").exists():
        env["PATH"] = os.pathsep.join([str(PY312_DIR), str(PY312_DIR / "Scripts"), env.get("PATH", "")])
    proc = _run([_find_sam(), "build", "--template-file", str(out / "template.yaml"), "--build-dir", str(build_dir)],
                          cwd=str(root), capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=900)
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


PY_RIE_IMAGE = "amazon/aws-lambda-python:3.12"
COVERAGE_PY = "coverage==7.16.2"
PY_VENDOR = VERIFY_TOOLS / "py" / "vendor"


def ensure_py_vendor() -> Path:
    """coverage.py for the Lambda Python (cp312, manylinux x86_64, with its C
    tracer), unpacked once into verify-tools/py/vendor and copied into RIE
    containers as /opt/m2s-cov."""
    if (PY_VENDOR / "coverage" / "__init__.py").exists():
        return PY_VENDOR
    import zipfile
    dl = TMP_ROOT / "py-wheels"
    dl.mkdir(parents=True, exist_ok=True)
    p = _run([sys.executable, "-m", "pip", "download", COVERAGE_PY, "--no-deps", "--only-binary=:all:", "--python-version", "3.12",
              "--platform", "manylinux2014_x86_64", "--implementation", "cp", "-d", str(dl)],
             capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
    wheels = sorted(dl.glob("coverage-*.whl"))
    if p.returncode != 0 or not wheels:
        raise VerifyError(f"could not download {COVERAGE_PY} for Python coverage: {(p.stdout + p.stderr)[-800:]}")
    PY_VENDOR.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(wheels[-1]) as z:
        z.extractall(PY_VENDOR)
    return PY_VENDOR


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
        functions.append({"name": name, "dir": str(build_dir / fn["logical"]), "handler": fn["handler"], "env": fn["env"], "memory": fn["memory"],
                          "runtime": fn.get("runtime", ""), "layers": [str(build_dir / ly) for ly in fn.get("layers", [])],
                          "isolate_cases": bool(model.cached_tables.get(name))})
    has_py = any(f["runtime"].startswith("python") for f in functions)
    py = {"rie_image_python": PY_RIE_IMAGE, "python_exe": sys.executable}
    if has_py and coverage and mode == "rie":
        py["py_vendor"] = str(ensure_py_vendor())
    job = {"mode": mode, "coverage": coverage, "out_dir": str(out_dir), "functions": functions, **ddb_backend(), **py,
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


def _fill_fixture(items: Any, now_ms: int) -> Any:
    """Time-relative fixture data: "{now_ms // 300000 + 1}" and the like
    (integer expressions over now_ms / now_s, the syntax of perf benchmark
    templates), so cases that seed data around the current time -- time
    buckets, TTLs -- stay valid whenever they run. Other braces stay as they are."""
    import perf  # perf imports verify; a top-level import would be circular
    return perf._fill(items, 0, now_ms)


def _prepare_runs(model: Model, cases: list[dict[str, Any]], faults_for: dict[str, list[dict]] | None = None,
                  pinned: dict[str, list[dict]] | None = None) -> list[dict[str, Any]]:
    runs = []
    now_ms = int(time.time() * 1000)
    for c in cases:
        event, gw = build_event(model, c)
        fixtures = {model.table_id(t): _fill_fixture(items, now_ms) for t, items in (c.get("fixtures") or {}).items()}
        case_faults = [_resolve_rule(model, r) for r in c.get("faults") or []]
        run = {"id": c["id"], "run_key": c["id"], "function": c["function"], "event": event, "gateway_response": gw, "fixtures": fixtures,
               "faults": case_faults, "case_faults": case_faults}
        if c.get("repeat"):
            run["repeat"] = c["repeat"]
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


def _response_headers(result: Any) -> dict[str, list[str]]:
    """lower-cased header name -> values, merged the way API Gateway merges
    `headers` and `multiValueHeaders`: every multi value is kept, and a
    `headers` pair that also appears in `multiValueHeaders` is dropped.
    Names differing only in case are separate response headers, so they
    all count."""
    multi: dict[str, list[str]] = {}
    single: dict[str, list[str]] = {}
    if not isinstance(result, dict):
        return {}
    for name, values in (result.get("multiValueHeaders") or {}).items():
        for value in values if isinstance(values, list) else [values]:
            multi.setdefault(str(name).lower(), []).append(str(value))
    for name, value in (result.get("headers") or {}).items():
        single.setdefault(str(name).lower(), []).append(str(value))
    out: dict[str, list[str]] = {}
    for name in [*single, *(n for n in multi if n not in single)]:
        out[name] = [v for v in single.get(name, []) if v not in multi.get(name, [])] + multi.get(name, [])
    return out


def judge(model: Model, case: dict[str, Any], raw: dict[str, Any]) -> list[str]:
    """Mismatches between one case's expectations and its raw observation."""
    exp = case.get("expect") or {}
    if raw.get("infra_error"):
        return [f"infra error (not a verdict on the code): {raw['infra_error'][:500]}"]
    if "alternatives" in exp:
        outs = [judge(model, {**case, "expect": alt}, raw) for alt in exp["alternatives"]]
        if any(not o for o in outs):
            return []
        return ["no alternative matched: " + " | ".join(f"[{i}] " + "; ".join(o) for i, o in enumerate(outs))]
    mism: list[str] = []
    result, error = raw.get("result"), raw.get("error")
    # repeat: the same request again in the same execution environment (a
    # cache hit) must answer exactly like the first time; the data did not change.
    reps = raw.get("repeats") or []
    for k, rr in enumerate(reps[1:], 2):
        if normalize(rr.get("result")) != normalize(reps[0].get("result")) or (rr.get("error") or {}).get("type") != (reps[0].get("error") or {}).get("type"):
            mism.append(f"repeat: invocation {k} answered differently from invocation 1: {json.dumps(rr.get('result') or rr.get('error'))[:300]}")
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
    if "headers" in exp:
        got, spec = _response_headers(result), exp["headers"]
        for name, want in (spec.get("equals") or {}).items():
            values = got.get(name.lower(), [])
            if want not in values:
                mism.append(f"header {name}: expected {want!r}, got {values if values else 'absent'}")
        for name, parts in (spec.get("contains") or {}).items():
            # a list means one value that contains every substring (e.g. a
            # cookie's name and its attributes, whatever its random value)
            parts = [parts] if isinstance(parts, str) else parts
            values = got.get(name.lower(), [])
            if not any(all(p in v for p in parts) for v in values):
                mism.append(f"header {name}: expected a value containing {parts if len(parts) > 1 else parts[0]!r}, got {values if values else 'absent'}")
        for name, n in (spec.get("count") or {}).items():
            values = got.get(name.lower(), [])
            if len(values) != n:
                mism.append(f"header {name}: expected {n} value(s), got {len(values)}: {values}")
        for name in spec.get("absent") or []:
            if got.get(name.lower()):
                mism.append(f"header {name}: expected absent, got {got[name.lower()]}")
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


def not_applicable(model: Model, case: dict[str, Any], raw: dict[str, Any]) -> str | None:
    """A case whose fault rules never fired, because the operation they
    target no longer exists, does not judge the code -- but only when the
    case cites a spec ruling that classed that step as implementation trace
    (otherwise deleting the step would be a way around the test)."""
    fired = raw.get("rules_fired")
    if not case.get("faults") or fired is None or any(fired) or raw.get("infra_error"):
        return None
    rulings = sorted(set(_si_refs(case)) & model.trace_rulings)
    if not rulings:
        return None
    return (f"none of its fault rules fired (the targeted operation does not occur) and it cites "
            f"implementation-trace ruling(s) {rulings}")


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


def _strip_attrs(c: dict[str, Any], attrs: set[str]) -> dict[str, Any]:
    """A call record without the given attributes: dropped from Item, and
    from SET clauses of an UpdateExpression (with the values only they used)."""
    c = dict(c)
    if isinstance(c.get("item"), dict):
        c["item"] = {k: v for k, v in c["item"].items() if k not in attrs}
    upd = c.get("update")
    if isinstance(upd, str):
        names = c.get("names") or {}
        dropped: set[str] = set()

        def keep(clause: str) -> bool:
            m = re.match(r"\s*([#\w.]+)\s*=\s*(.+?)\s*$", clause)
            if not m:
                return True
            name = names.get(m.group(1), m.group(1))
            if name in attrs:
                dropped.update(re.findall(r":\w+", m.group(2)))
                return False
            return True

        def rewrite(match: re.Match) -> str:
            clauses = [x for x in re.split(r",(?![^()]*\))", match.group(2)) if keep(x)]
            return f"{match.group(1)} {', '.join(x.strip() for x in clauses)} " if clauses else ""
        new = re.sub(r"(SET)\s+(.*?)(?=\s+(?:ADD|REMOVE|DELETE)\b|$)", rewrite, upd, flags=re.I).strip()
        c["update"] = re.sub(r"\s+", " ", new)
        if isinstance(c.get("values"), dict):
            used = set(re.findall(r":\w+", c["update"]))
            c["values"] = {k: v for k, v in c["values"].items() if k not in dropped or k in used}
    if isinstance(c.get("transact"), list):
        c["transact"] = [_strip_attrs(t, attrs) for t in c["transact"]]
    return c


def _sort_arrays(v: Any) -> Any:
    """Every array, at any depth, in a canonical order (by its JSON form)."""
    if isinstance(v, dict):
        return {k: _sort_arrays(x) for k, x in v.items()}
    if isinstance(v, list):
        return sorted((_sort_arrays(x) for x in v), key=lambda x: json.dumps(x, sort_keys=True, default=str))
    return v


def _unordered_result(result: Any) -> Any:
    """A handler result with its arrays order-free: an API Gateway response's
    JSON body, or the returned value itself."""
    if isinstance(result, dict) and isinstance(result.get("body"), str):
        try:
            body = json.loads(result["body"])
        except ValueError:
            return result
        return {**result, "body": json.dumps(_sort_arrays(body), sort_keys=True)}
    return _sort_arrays(result)


def _run_contract(model: "Model", raw: dict[str, Any]) -> int:
    """The H1 read rules for one run: contract 2 everywhere in a contract-2
    workspace, and for the functions an accepted perf relaxation rules on."""
    return 2 if model.contract >= 2 or raw.get("function") in model.relaxed_read_functions else model.contract


def _observation(raw: dict[str, Any], contract: int = 1, ignore_attrs: set[str] | None = None,
                 unordered: bool = False, cached_tables: set[str] | None = None) -> dict[str, Any]:
    """What H1 compares: (a) the multiset of calls that took effect -- faulted
    attempts and calls the backend rejected changed nothing, so retries do
    not count twice; (b) the externally visible outcome. Under contract 2 a
    read is compared only as "table T was read": Scan vs Query vs GetItem,
    the index and the key condition are how the implementation reads, and
    what it read shows in the outcome (b)."""
    effective = [c for c in raw.get("calls") or [] if not c.get("fault") and not c.get("backend_error")]
    if cached_tables:
        effective = [c for c in effective if not (c.get("op") in READ_OPS and c.get("table") in cached_tables)]
    if ignore_attrs:
        effective = [_strip_attrs(c, ignore_attrs) for c in effective]

    def enc(c: dict) -> str:
        if contract >= 2 and c.get("op") in READ_OPS:
            return json.dumps({"op": "@read", "table": c.get("table")}, sort_keys=True)
        return json.dumps(normalize({k: c.get(k) for k in ("op", "table", "queue", "index", "key", "item", "update", "values", "transact")}), sort_keys=True)
    encoded = [("@read" if contract >= 2 and c.get("op") in READ_OPS else c.get("op"), enc(c)) for c in effective]
    # "操作集合": repeating an idempotent operation (a read, the same Put,
    # a Delete) changes nothing, so those compare as a set; UpdateItem (ADD),
    # messages and multi-item writes keep every occurrence.
    idem = sorted({e for op, e in encoded if op in IDEMPOTENT_OPS or op == "@read"})
    other = sorted(e for op, e in encoded if op not in IDEMPOTENT_OPS and op != "@read")
    calls = sorted(idem + other)
    result = raw.get("result")
    if unordered:
        result = _unordered_result(result)
    outcome = {"error": (raw.get("error") or {}).get("type"), "result": normalize(result),
               "messages": sorted(json.dumps(normalize(m), sort_keys=True) for m in raw.get("messages") or []),
               "tables_after": {t: sorted((normalize({k: v for k, v in i.items() if not ignore_attrs or k not in ignore_attrs})
                                           for i in items), key=lambda x: json.dumps(x, sort_keys=True))
                                for t, items in sorted((raw.get("tables_after") or {}).items())}}
    return {"calls": [json.loads(c) for c in calls], "outcome": outcome,
            "attempts": len(raw.get("calls") or []), "calls_after_return": sum(1 for c in raw.get("calls") or [] if c.get("after_return")),
            "conflicts": sum(1 for c in raw.get("calls") or [] if c.get("conflict")),
            "faults_applied": _pins(raw, contract)}


def _pins(raw: dict[str, Any], contract: int = 1) -> list[dict[str, Any]]:
    """The faults a run actually applied, as rules pinned to the concrete
    operation (op, table, identity, n-th attempt on it) instead of "the n-th
    matching call" -- so the same operations fail in a version whose call
    order differs. Under contract 2 a faulted read is pinned to "the n-th
    read of that table", so it still fires when the other version reads the
    table with another operation (Scan -> Query on an index)."""
    pins, reads = [], {}
    for c in raw.get("calls") or []:
        table = c.get("table") or c.get("queue")
        if contract >= 2 and c.get("op") in READ_OPS:
            reads[table] = reads.get(table, 0) + 1
            if c.get("fault"):
                pins.append({"match": {"op": "@read", "table": table}, "nth": reads[table], "kind": c["fault"]})
        elif c.get("fault"):
            pins.append({"match": {"op": c["op"], "table": table, "identity": c.get("identity", "")},
                         "nth": c.get("occurrence", 1), "kind": c["fault"]})
    return pins


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
    # GM3: headers the monolith adds to every response (app-wide middleware,
    # analysis_report.global_middleware) must be asserted on every API function
    contract_headers = gates._contract_headers(ws)
    if contract_headers:
        asserted: dict[str, set[str]] = {}
        for c in all_cases:
            h = ((c.get("expect") or {}).get("headers") or {}) if isinstance(c.get("expect"), dict) else {}
            names = {n.lower() for part in ("equals", "contains", "count") for n in (h.get(part) or {})}
            asserted.setdefault(c.get("function"), set()).update(names)
        for name, fn in model.bp_functions.items():
            if fn.get("trigger_type") != "api":
                continue
            missing = [h for h in contract_headers if h.lower() not in asserted.get(name, set())]
            if missing:
                schema_errors.append(f"GM3: no case of {name} asserts app-wide response header(s) {missing} (the monolith adds them "
                                     "to every response via global middleware) -- add expect.headers to at least one case")
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
        na = not_applicable(model, c, r) if mism else None
        if na:
            mism = []
        entry = {"id": c["id"], "function": c["function"], "kind": c.get("kind"), "passed": not mism, "mismatches": mism,
                 "status": (r.get("result") or {}).get("statusCode") if isinstance(r.get("result"), dict) else None,
                 "error": r.get("error"), "gateway": bool(r.get("gateway")),
                 "sdk_calls": [f"{x.get('op')}:{x.get('table') or x.get('queue') or ''}{'!' + x['fault'] if x.get('fault') else '!conflict' if x.get('conflict') else ''}" for x in r.get("calls") or []]}
        if na:
            entry["not_applicable"] = na
        if mism:
            entry["triage_hint"] = _triage_hint(r)
            fres = raw["functions"].get(c["function"], {})
            entry["logs_tail"] = (fres.get("logs_tail") or "")[-1500:]
        results.append(entry)
    failed = [r for r in results if not r["passed"]]
    disclaimers = [d for d in (local_disclaimer(model) if mode == "local" else "", DYNALITE_DISCLAIMER if raw.get("backend") == "dynalite" else "") if d]
    summary = {"status": "PASS" if not failed else "FAIL", "mode": mode, "backend": raw.get("backend"), "total": len(results),
               "passed": len(results) - len(failed), "failed": len(failed), "failures": failed,
               "cases_with_conflicts": [r["id"] for r in results if any(x.endswith("!conflict") for x in r["sdk_calls"])],
               "not_applicable": [{"id": r["id"], "reason": r["not_applicable"]} for r in results if r.get("not_applicable")],
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


CODE_SUFFIXES = (".js", ".mjs", ".cjs", ".ts", ".py")


def _changed_lines(ws: Path, ref: str, model: "Model") -> tuple[dict[str, dict[str, set[int]]], list[str]]:
    """({function: {file key: {new line numbers}}}, [unmapped files]) for
    every changed line of a code file under output/lambdas and output/layers
    (added or modified; untracked files count as fully changed).

    Files are mapped through the template: a function's CodeUri, or a
    layer's ContentUri (then the change counts for every function using the
    layer, keyed "layer:<LayerId>:<path>"). The directory layout does not
    matter. A changed code file that belongs to no function is returned as
    unmapped -- never silently skipped."""
    def git(*args: str) -> str:
        p = _run(["git", "-C", str(ws), *args], capture_output=True, text=True, encoding="utf-8", errors="replace")
        if p.returncode != 0:
            raise VerifyError(f"git {' '.join(args)} failed: {p.stderr.strip()}")
        return p.stdout
    out: dict[str, dict[str, set[int]]] = {}
    unmapped: list[str] = []

    def owners(path: str) -> list[tuple[str, str]]:
        rel = path[len("output/"):] if path.startswith("output/") else path
        for name, fn in model.functions.items():
            cu = fn.get("code_uri") or ""
            if cu and rel.startswith(cu + "/"):
                return [(name, rel[len(cu) + 1:])]
        hits = []
        for lid, cu in getattr(model, "layers", {}).items():
            if cu and rel.startswith(cu + "/"):
                hits += [(name, f"layer:{lid}:{rel[len(cu) + 1:]}") for name, fn in model.functions.items() if lid in fn.get("layers", [])]
        return hits

    def add(path: str, lines: set[int]) -> None:
        if not path.endswith(CODE_SUFFIXES):
            return
        who = owners(path)
        if not who:
            unmapped.append(path)
        for name, key in who:
            out.setdefault(name, {}).setdefault(key, set()).update(lines)

    cur = None
    for line in git("diff", "-U0", ref, "--", "output/lambdas", "output/layers").splitlines():
        if line.startswith("+++ "):
            cur = line[6:] if line.startswith("+++ b/") else None
        elif line.startswith("@@") and cur:
            m = re.match(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", line)
            if m:
                start, n = int(m.group(1)), int(m.group(2) or 1)
                add(cur, set(range(start, start + n)))
    for path in git("ls-files", "--others", "--exclude-standard", "--", "output/lambdas", "output/layers").splitlines():
        n = len((ws / path).read_text(encoding="utf-8", errors="ignore").splitlines())
        add(path, set(range(1, n + 1)))
    return out, sorted(set(unmapped))


def _changed_coverage(final_json: Path, logical: str, changed: dict[str, set[int]]) -> dict[str, Any]:
    data = _read_json(final_json)
    covered, uncovered = [], []
    seen: set[str] = set()

    def key_of(fpath: str) -> str | None:
        norm = fpath.replace("\\", "/")
        for key in changed:
            if key.startswith("layer:"):
                _, lid, rel = key.split(":", 2)
                # build/<LayerId>/[python/|nodejs/]<rel>
                if f"/{lid}/" in norm and norm.endswith("/" + rel):
                    return key
        rel = norm.split(f"/{logical}/", 1)[-1]
        return rel if rel in changed else None

    for fpath, fc in data.items():
        key = key_of(fpath)
        if not key:
            continue
        seen.add(key)
        rel = key.split(":", 2)[2] if key.startswith("layer:") else key
        lines = changed[key]
        src = Path(fpath).read_text(encoding="utf-8", errors="ignore").splitlines() if Path(fpath).exists() else []
        for ln in sorted(lines):
            hits = [fc["s"][sid] for sid, loc in fc["statementMap"].items() if loc["start"]["line"] <= ln <= loc["end"]["line"]]
            if not hits:
                continue  # not executable (comment, brace, blank)
            (covered if any(h > 0 for h in hits) else uncovered).append(
                {"file": rel, "line": ln, "code": src[ln - 1].strip()[:160] if 0 < ln <= len(src) else ""})
    for key in sorted(set(changed) - seen):
        # No test case loaded the file at all, so none of its changed lines ran.
        rel = key.split(":", 2)[2] if key.startswith("layer:") else key
        uncovered.append({"file": rel, "line": None, "code": f"file not loaded by any test case ({len(changed[key])} changed lines)"})
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
        per_fn[name] = _summarize_coverage(Path(cov["report_dir"]) / "coverage-final.json", Path(model.functions[name]["logical"]),
                                           _project_files(ws, model, name))
        (out / f"{name}.json").write_text(json.dumps(per_fn[name], indent=2, ensure_ascii=False), encoding="utf-8")
    changed_report = None
    if changed_since:
        changed, unmapped = _changed_lines(ws, changed_since, model)
        changed_report = {}
        if unmapped:
            changed_report["(unmapped)"] = {"error": "changed code files that belong to no function's CodeUri or any layer's ContentUri: "
                                                     + ", ".join(unmapped)}
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
               "disclaimer": local_disclaimer(model) if mode == "local" else ""}
    if changed_report is not None:
        summary["changed_lines"] = {"since": changed_since, "functions": changed_report,
                                    "all_covered": all(not r.get("uncovered") and not r.get("error") for r in changed_report.values())}
    (out / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    return summary


def _project_files(ws: Path, model: Model, name: str) -> dict[str, set[str]] | None:
    """The function's own source files, as paths relative to its CodeUri
    ("" key) and to each of its layers' ContentUri (layer id keys). Coverage
    counts only these: what sam build bundles next to them (pip packages,
    node_modules) is not code under test. None when the CodeUri is unknown."""
    fn = model.functions.get(name) or {}
    out_dir = ws / "output"
    roots = {"": fn.get("code_uri") or ""} | {lid: getattr(model, "layers", {}).get(lid, "") for lid in fn.get("layers", [])}
    if not roots[""] or not (out_dir / roots[""]).is_dir():
        return None
    own: dict[str, set[str]] = {}
    for key, cu in roots.items():
        base = out_dir / cu if cu else None
        if base is None or not base.is_dir():
            own[key] = set()
            continue
        own[key] = {q.relative_to(base).as_posix() for q in base.rglob("*")
                    if q.is_file() and q.suffix in (".py", ".js", ".mjs", ".cjs", ".ts")
                    and not {"node_modules", "__pycache__", ".aws-sam"} & set(q.relative_to(base).parts)}
    return own


def _own_file(norm: str, rel: str, own: dict[str, set[str]]) -> str | None:
    """Display path of a covered file if it is project source, else None."""
    if rel in own.get("", set()):
        return rel
    for lid, rels in own.items():
        if lid and f"/{lid}/" in norm:
            for r in rels:
                if norm.endswith("/" + r) or norm.endswith("/" + r.split("/", 1)[-1]):
                    return f"layer:{lid}:{r}"
    return None


def _summarize_coverage(final_json: Path, logical: Path, own: dict[str, set[str]] | None = None) -> dict[str, Any]:
    data = _read_json(final_json)
    files = {}
    uncovered = []
    excluded = 0
    tot = {"statements": [0, 0], "branches": [0, 0], "functions": [0, 0]}
    for fpath, fc in data.items():
        norm = fpath.replace("\\", "/")
        rel = norm.split(f"/{logical.name}/", 1)[-1]
        if rel.startswith(".m2s-cov-flush") or "node_modules" in rel:
            continue
        if own is not None:
            shown = _own_file(norm, rel, own)
            if shown is None:
                excluded += 1  # bundled dependency, not code under test
                continue
            rel = shown
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
            "files": files, "excluded_files": excluded, "uncovered": uncovered}


def _is_read(c: dict[str, Any]) -> bool:
    return c.get("op") in READ_OPS or c.get("op") == "@read"


def _status(outcome: dict[str, Any]) -> int | None:
    r = outcome.get("result")
    return r.get("statusCode") if isinstance(r, dict) and isinstance(r.get("statusCode"), int) else None


def speculative_extra_reads(before: dict[str, Any], now: dict[str, Any]) -> tuple[list[dict], str | None]:
    """R1b (PERF_OPTIMIZATION.md 10.4 (a)): reads moved ahead of an
    error-propagation early return run on the failure path too. H1 (a) then
    accepts the candidate's calls when (1) the calls other than reads are the
    same multiset, (2) every baseline read is still made, (3) only reads are
    extra, and (4) the baseline answered with an error that is not an
    authorization denial: 401/403 mean an authz guard, whose reads must stay
    behind it (unauthenticated callers would read, and pay for, data).
    Returns (extra reads, None) or ([], reason it is not acceptable)."""
    enc = lambda c: json.dumps(c, sort_keys=True)
    b_other = sorted(enc(c) for c in before["calls"] if not _is_read(c))
    n_other = sorted(enc(c) for c in now["calls"] if not _is_read(c))
    if b_other != n_other:
        return [], "the writes / messages differ"
    b_reads = [enc(c) for c in before["calls"] if _is_read(c)]
    n_reads = [enc(c) for c in now["calls"] if _is_read(c)]
    rest = list(n_reads)
    for r in b_reads:
        if r not in rest:
            return [], "a baseline read is missing"
        rest.remove(r)
    status = _status(before["outcome"])
    failed = before["outcome"].get("error") is not None or (status is not None and status >= 400)
    if not failed:
        return [], "extra reads on a run the baseline answered successfully"
    if status in (401, 403):
        return [], f"extra reads behind an authorization denial ({status}): authz guards keep their reads sequential"
    return [json.loads(r) for r in rest], None


def run_equivalence(workspace: str, mode: str = "record", label: str = "v0", functions: list[str] | None = None,
                    runtime: str = "auto", baseline_ref: str | None = "v0",
                    speculative_reads: list[str] | None = None) -> dict[str, Any]:
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
    stored baseline file and the original n-th-call rules.

    speculative_reads: functions changed under rule R1b. For their runs H1 (a)
    tolerates extra reads on error-propagation failure paths (see
    speculative_extra_reads); they are listed in the result's
    speculative_reads with their count, the extra RRU the change costs."""
    if mode not in ("record", "diff"):
        raise VerifyError("mode must be 'record' or 'diff'")
    ws = Path(workspace)
    model = Model(ws)
    cases = load_cases(ws)
    by_id = {c["id"]: c for c in cases if "id" in c}
    faults = [f for f in load_faults(ws) if not functions or f.get("function") in functions]
    errs = [e for f in faults for e in check_fault(model, f, set(by_id), with_template=True)]
    if not faults and mode == "record":
        errs.append("no fault scenarios under verification/faults/*.json")
    if mode == "diff" and not faults and not any(not functions or c.get("function") in functions for c in cases):
        # diff also compares every plain case, so scenarios are not required
        # -- but there must be something to compare.
        errs.append("nothing to compare: no fault scenarios and no test cases for these functions")
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
        scen_runs = _prepare_runs(m, needed, scen, pinned) if scen else []
        raw = _run_controller(m, build, scen_runs, coverage=False, mode=runtime, label=f"equiv-{tag}") if scen_runs else {"cases": []}
        plain_runs = _prepare_runs(m, plain)
        if pinned is not None:  # case-level faults pinned to the same operations too
            for r in plain_runs:
                r["faults"] = pinned.get(r["run_key"], [])
        extra = _run_controller(m, build, plain_runs, coverage=False, mode=runtime, label=f"equiv-{tag}-nofault")
        rs = raw["cases"] + extra["cases"]
        return {r["run_key"]: r for r in rs}, {r["run_key"]: r["infra_error"] for r in rs if r.get("infra_error")}

    path = _vdir(ws) / f"fault_baseline_{label}.json"
    if mode == "record":
        # A functions-filtered record updates only those functions in an
        # existing baseline; it used to overwrite the whole file with just
        # their observations.
        old = json.loads(path.read_text(encoding="utf-8")) if functions and path.exists() else None
        if old and old.get("runtime") != runtime:
            return {"status": "FAIL", "error": f"{path.name} was recorded with runtime {old.get('runtime')!r}; a functions-filtered "
                    f"record with runtime {runtime!r} cannot be merged into it -- record all functions instead"}
        raws, infra = run_version(model, ws, None, "record")
        obs = {k: _observation(r, _run_contract(model, r), model.index_attributes, r.get("function") in model.unordered_functions,
                              {model.table_id(t) for t in model.cached_tables.get(r.get("function"), ())}) for k, r in raws.items()}
        scenarios = {k: {"description": v.get("description", ""), "function": v["function"], "cases": v["cases"], "rules": v["rules"]} for k, v in scen.items()}
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        doc = {"label": label, "runtime": runtime, "recorded_at": now, "scenarios": scenarios, "observations": obs, "infra_errors": infra,
               "note": "v1 records fault behaviour only; whether it is correct is judged in v2 together with monolith diffing"}
        if old:
            def fn_of(key: str) -> str | None:
                if "::" in key:
                    return (old.get("scenarios", {}).get(key.split("::", 1)[0]) or scenarios.get(key.split("::", 1)[0]) or {}).get("function")
                return (by_id.get(key) or {}).get("function")
            keep = lambda k: fn_of(k) not in functions
            doc = {**old, "recorded_at": now,
                   "scenarios": {**{k: v for k, v in old.get("scenarios", {}).items() if v.get("function") not in functions}, **scenarios},
                   "observations": {**{k: v for k, v in old.get("observations", {}).items() if keep(k)}, **obs},
                   "infra_errors": {**{k: v for k, v in (old.get("infra_errors") or {}).items() if keep(k)}, **infra},
                   "partial_updates": [*old.get("partial_updates", []), {"at": now, "functions": sorted(functions)}]}
        elif functions:
            doc["partial"] = {"functions": sorted(functions), "note": "no earlier baseline: only these functions are recorded"}
        path.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
        return {"status": "RECORDED" if not infra else "RECORDED_WITH_INFRA_ERRORS", "runtime": runtime, "path": str(path.relative_to(ws)),
                "runs": len(obs), "total_runs_in_baseline": len(doc["observations"]),
                "merged_into_existing": bool(old), "infra_errors": infra, "disclaimer": local_disclaimer(model) if runtime == "local" else ""}

    if baseline_ref and has_own_repo(ws):
        base_root = export_ref(ws, baseline_ref)
        base_model = Model(base_root)
        base_raws, base_infra = run_version(base_model, base_root, None, "base")
        base = {k: _observation(r, _run_contract(model, r), model.index_attributes, r.get("function") in model.unordered_functions,
                              {model.table_id(t) for t in model.cached_tables.get(r.get("function"), ())}) for k, r in base_raws.items()}
        pinned = {k: v["faults_applied"] for k, v in base.items()}
        # Stability check: the baseline run again with the same pinned faults;
        # runs whose two baseline observations differ are measurement noise,
        # not evidence against the change.
        again_raws, again_infra = run_version(base_model, base_root, pinned, "base2")
        again = {k: _observation(r, _run_contract(model, r), model.index_attributes, r.get("function") in model.unordered_functions,
                              {model.table_id(t) for t in model.cached_tables.get(r.get("function"), ())}) for k, r in again_raws.items()}
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
    obs = {k: _observation(r, _run_contract(model, r), model.index_attributes, r.get("function") in model.unordered_functions,
                              {model.table_id(t) for t in model.cached_tables.get(r.get("function"), ())}) for k, r in now_raws.items()}
    diffs, spec_reads = [], []
    spec_fns = set(speculative_reads or [])
    for key, now in obs.items():
        before = base.get(key)
        if key in unstable:
            continue
        if before is None:
            diffs.append({"run": key, "h1": "n/a", "detail": "not in baseline (new scenario or case)"})
            continue
        if now["calls"] != before["calls"]:
            extra, why = speculative_extra_reads(before, now) if now_raws[key].get("function") in spec_fns else ([], None)
            if extra:
                spec_reads.append({"run": key, "extra_reads": extra, "baseline_status": _status(before["outcome"])})
            else:
                diffs.append({"run": key, "h1": "a", "detail": "different multiset of calls that took effect" + (f" (R1b not applicable: {why})" if why else ""),
                              "baseline": before["calls"], "now": now["calls"]})
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
            **({"speculative_reads": spec_reads, "extra_reads_total": sum(len(x["extra_reads"]) for x in spec_reads)} if spec_fns else {}),
            "note": ("runs listed in unstable_in_baseline gave different results in two runs of the baseline itself; "
                     "they are excluded from the verdict, so the change is NOT verified on them") if unstable else ""}
