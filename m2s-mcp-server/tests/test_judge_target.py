"""Tests for run_judge's parts (PIPELINE_V2_PLAN.md §4.9, N2.2-N2.3): the step
comparison (symbols, declared headers, R layer), routing and identities on
the target, fault rules through the adapter map, and judge-run.js itself on a
small synthetic API (local Node + dynalite, no sam, no Docker)."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import judge  # noqa: E402
import verify  # noqa: E402

DECLARED = {"content-type", "access-control-allow-origin", "access-control-allow-credentials"}


def step(status=200, body=None, headers=None, kind="json"):
    return {"status": status, "headers": headers or {"content-type": "application/json; charset=utf-8",
                                                     "access-control-allow-origin": "*"},
            "body_kind": kind, "body": body}


# ---------------------------------------------------------------- comparison

def test_identity_and_id_symbols_bind_across_steps():
    t1 = step(body={"orderId": "<uuid#1>", "customerId": "<id:u1>"})
    t2 = step(body=[{"orderId": "<uuid#1>", "at": "<time#2>"}])
    b = {"<id:u1>": "sub-1"}
    assert judge.compare_target_step(t1, step(body={"orderId": "o-9", "customerId": "sub-1"}), b, "a", DECLARED, set()) == []
    assert judge.compare_target_step(t2, step(body=[{"orderId": "o-9", "at": 123}]), b, "b", DECLARED, set()) == []
    bad = judge.compare_target_step(t2, step(body=[{"orderId": "o-8", "at": 1}]), b, "b", DECLARED, set())
    assert bad and bad[0]["path"] == "steps.b.body[0].orderId"
    other = judge.compare_target_step(t1, step(body={"orderId": "o-1", "customerId": "sub-2"}), {"<id:u1>": "sub-1"},
                                      "a", DECLARED, set())
    assert other[0]["path"] == "steps.a.body.customerId"


def test_declared_headers_only_and_content_type_by_media_type():
    t = step(body={}, headers={"content-type": "application/json; charset=utf-8", "access-control-allow-origin": "*",
                               "x-request-id": "<uuid#1>", "vary": "Origin"})
    a = step(body={}, headers={"content-type": "application/json", "access-control-allow-origin": "*",
                               "x-amzn-requestid": "r"})
    assert judge.compare_target_step(t, a, {}, "s", DECLARED, set()) == []
    a2 = step(body={}, headers={"content-type": "text/html", "access-control-allow-credentials": "true"})
    why = {m["path"].rsplit(".", 1)[1]: m["why"] for m in judge.compare_target_step(t, a2, {}, "s", DECLARED, set())}
    assert why == {"access-control-allow-credentials": "header the monolith does not send",
                   "access-control-allow-origin": "header missing", "content-type": "different media type"}


def test_unordered_lists_from_the_masks_match_as_multisets():
    t = step(body=[{"id": "<uuid#1>", "n": 1}, {"id": "<uuid#2>", "n": 2}])
    a = step(body=[{"id": "y", "n": 2}, {"id": "x", "n": 1}])
    assert judge.compare_target_step(t, a, {}, "s", DECLARED, set())
    assert judge.compare_target_step(t, a, {}, "s", DECLARED, {"steps.s.body"}) == []


FACTS = {"entry_points": [
    {"method": "GET", "path": "/api/items", "responses": [{"status": 200}], "data_access": [{"operation": "scan", "table": {"default_name": "Items"}}]},
    {"method": "POST", "path": "/api/items", "responses": [{"status": 201}], "data_access": [{"operation": "put", "table": {"default_name": "Items"}}, {"operation": "put", "table": {"default_name": "Log"}}]},
    {"method": "POST", "path": "/api/login", "responses": [{"status": 200}], "data_access": []}],
    "dynamodb_info": {"table_schemas": [
        {"table": {"default_name": "Items"}, "partition_key": {"name": "id", "type": "S"}},
        {"table": {"default_name": "Log"}, "partition_key": {"name": "id", "type": "S"}},
        {"table": {"default_name": "Users"}, "partition_key": {"name": "userId", "type": "S"}}]},
    "global_middleware": [{"kind": "cors", "headers": {"Access-Control-Allow-Origin": "*"}}]}
AMAP = {"tables": [{"monolith": "Items", "target": "ItemsTable"}, {"monolith": "Log", "target": "LogTable"},
                   {"monolith": "Users", "target": None, "replaced_by": "cognito", "reason": "x"}],
        "identities": {"claims": {"sub": "${id}", "email": "${email}"}},
        "dropped_endpoints": [{"endpoint": "POST /api/login", "reason": "Cognito"}]}


@pytest.fixture()
def facts(tmp_path):
    (tmp_path / "storage").mkdir()
    (tmp_path / "storage" / "analysis_report.json").write_text(json.dumps(FACTS), encoding="utf-8")
    return judge.Facts(tmp_path)


def unit_ctx(steps, bindings=None):
    return {"bindings": bindings or {}, "rule_problems": [],
            "routes": [{"name": n, "route": {"kind": k, "function": "f", "endpoint": "GET /api/items"}} for n, k in steps]}


def test_r_layer_order_ruling_and_migration_change(facts):
    template = {"steps": {"list": step(body=[{"id": "a"}, {"id": "b"}])}, "tables_after": {}}
    rec = {"steps": [{"name": "list", "route": "invoke", "response": step(body=[{"id": "b"}, {"id": "a"}])}],
           "tables_after": {}}
    ctx = unit_ctx([("list", "invoke")])
    assert judge.judge_unit(facts, template, rec, ctx, AMAP, [], [], DECLARED)["verdict"] == "fail"
    order = [{"id": "R-1", "relaxation": "order", "category": "contract"}]
    res = judge.judge_unit(facts, template, rec, ctx, AMAP, [], order, DECLARED)
    assert res["verdict"] == "pass" and res["steps"]["list"]["rulings"] == ["R-1"]
    moved = [{"id": "R-2", "category": "migration-change"}]
    assert judge.judge_unit(facts, template, rec, ctx, AMAP, [], moved, DECLARED)["verdict"] == "migration-change"


def test_dropped_steps_and_write_only_tables(facts):
    template = {"steps": {"login": step(), "add": step(201, {"ok": True})},
                "tables_after": {"Items": [{"id": "a"}], "Log": [{"id": "<uuid#1>", "at": "<time#2>"}], "Users": [{"userId": "<id:u1>"}]}}
    rec = {"steps": [{"name": "login", "route": "dropped", "reason": "Cognito"},
                     {"name": "add", "route": "invoke", "response": step(201, {"ok": True})}],
           "tables_after": {"ItemsTable": [{"id": "a"}], "LogTable": [{"id": "l-1", "at": 5}]}}
    ctx = unit_ctx([("login", "dropped"), ("add", "invoke")])
    res = judge.judge_unit(facts, template, rec, ctx, AMAP, [], [], DECLARED)
    assert res["verdict"] == "pass" and res["steps"]["login"]["verdict"] == "dropped"
    # Log is written but no endpoint reads it: its contents are compared (V2P-03)
    rec["tables_after"]["LogTable"] = []
    res = judge.judge_unit(facts, template, rec, ctx, AMAP, [], [], DECLARED)
    assert res["verdict"] == "fail" and res["tables"]["mismatches"][0]["path"] == "tables_after.Log"
    # Items is read back by an endpoint: a difference there is only reported
    rec["tables_after"] = {"ItemsTable": [{"id": "b"}], "LogTable": [{"id": "l-1", "at": 5}]}
    res = judge.judge_unit(facts, template, rec, ctx, AMAP, [], [], DECLARED)
    assert res["verdict"] == "pass" and res["table_differences"]


TEMPLATE_YAML = """
AWSTemplateFormatVersion: '2010-09-09'
Transform: AWS::Serverless-2016-10-31
Globals:
  Api:
    GatewayResponses:
      DEFAULT_4XX:
        ResponseParameters:
          Headers:
            Access-Control-Allow-Origin: "'*'"
Resources:
  ItemsTable:
    Type: AWS::DynamoDB::Table
    Properties:
      AttributeDefinitions: [{AttributeName: id, AttributeType: S}]
      KeySchema: [{AttributeName: id, KeyType: HASH}]
  Api:
    Type: AWS::Serverless::Api
    Properties: {StageName: Prod, Auth: {DefaultAuthorizer: Cognito}}
  ListItems:
    Type: AWS::Serverless::Function
    Properties:
      CodeUri: lambdas/items-list/
      Handler: index.handler
      Runtime: nodejs22.x
      Events:
        A: {Type: Api, Properties: {RestApiId: !Ref Api, Path: /api/items, Method: get}}
        B: {Type: Api, Properties: {RestApiId: !Ref Api, Path: /api/items/new, Method: get}}
  GetItem:
    Type: AWS::Serverless::Function
    Properties:
      CodeUri: lambdas/items-get/
      Handler: index.handler
      Runtime: nodejs22.x
      Events:
        A: {Type: Api, Properties: {RestApiId: !Ref Api, Path: "/api/items/{id}", Method: get}}
"""


def test_routes_dropped_blocked_and_static_first(facts, tmp_path):
    (tmp_path / "output").mkdir()
    (tmp_path / "output" / "template.yaml").write_text(TEMPLATE_YAML, encoding="utf-8")
    model = verify.Model(tmp_path)
    case = {"steps": [{"name": "login", "method": "POST", "path": "/api/login", "save": {"tok": "body.token"}},
                      {"name": "use", "method": "GET", "path": "/api/items", "headers": {"Authorization": "${tok}"}},
                      {"name": "new", "method": "GET", "path": "/api/items/new"},
                      {"name": "one", "method": "GET", "path": "/api/items/${u1.id}"},
                      {"name": "post", "method": "POST", "path": "/api/items", "body": {}}]}
    r = {s["name"]: s["route"] for s in judge._route_steps(model, facts, case, judge._dropped_endpoints(AMAP))}
    assert r["login"]["kind"] == "dropped" and r["use"]["kind"] == "blocked"
    assert (r["new"]["function"], r["new"]["resource"]) == ("items-list", "/api/items/new")
    assert (r["one"]["function"], r["one"]["resource"]) == ("items-get", "/api/items/{id}")
    assert r["post"]["kind"] == "no_route"
    gw = judge._gateway_answers(model)
    assert gw["unauthorized"]["statusCode"] == 401 and gw["unauthorized"]["headers"]["Access-Control-Allow-Origin"] == "*"
    assert gw["no_route"]["statusCode"] == 403 and "Missing Authentication Token" in gw["no_route"]["body"]


def test_unit_identities_and_fault_rules_through_the_map(tmp_path):
    identities = {"u1": {"monolith": {"steps": [{"method": "POST", "path": "/api/register", "body": {"email": "u1@x.io"},
                                                 "save": {"id": "body.id", "token": "body.token"}}],
                                      "header": {"Authorization": "Bearer ${token}"}}}}
    vars_, auth, bindings, seeds = judge._unit_identities(AMAP, identities, ["u1"], "case-1")
    sub = vars_["u1.id"]
    assert auth["u1"] == {"header": {"Authorization": "Bearer m2s-token-u1"}, "claims": {"sub": sub, "email": "u1@x.io"}}
    assert bindings["<id:u1>"] == sub and vars_["u1.token"] == "m2s-token-u1" and seeds == []
    (tmp_path / "output").mkdir()
    (tmp_path / "output" / "template.yaml").write_text(TEMPLATE_YAML, encoding="utf-8")
    amap = {**AMAP, "tables": [{"monolith": "Items", "target": "ItemsTable", "fields": {"pk": {"from": "id"}}}, *AMAP["tables"][1:]]}
    rules, problems = judge._rules_to_target(amap, [{"match": {"table": "Items", "item": {"id": "a"}, "op": "PutItem"}, "kind": "error", "nth": 1},
                                                    {"match": {"table": "Users"}, "kind": "throttle"}], verify.Model(tmp_path))
    assert rules == [{"match": {"table": "ItemsTable", "item": {"pk": "a"}, "op": "PutItem"}, "nth": 1, "kind": "error"}]
    assert problems and "Users has no target table" in problems[0]


# ---------------------------------------------------------------- judge-run.js

HANDLERS = {
    "put": """
const { DynamoDBClient, PutItemCommand } = require('@aws-sdk/client-dynamodb');
const http = require('http');
const ddb = new DynamoDBClient({});
function send(body) {
  const u = new URL(process.env.AWS_ENDPOINT_URL_SQS);
  const data = JSON.stringify({ QueueUrl: process.env.QUEUE_URL, MessageBody: body });
  return new Promise((res, rej) => { const r = http.request({ host: u.hostname, port: u.port, method: 'POST', path: '/',
    headers: { 'x-amz-target': 'AmazonSQS.SendMessage', 'content-type': 'application/x-amz-json-1.0', 'content-length': Buffer.byteLength(data) } },
    (x) => { x.resume(); x.on('end', res); }); r.on('error', rej); r.end(data); });
}
exports.handler = async (event) => {
  const b = JSON.parse(event.body);
  const owner = event.requestContext.authorizer.claims.sub;
  await ddb.send(new PutItemCommand({ TableName: process.env.TABLE, Item: { id: { S: b.id }, owner: { S: owner } } }));
  await send(JSON.stringify({ id: b.id }));
  return { statusCode: 201, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ id: b.id, owner }) };
};""",
    "get": """
const { DynamoDBClient, GetItemCommand } = require('@aws-sdk/client-dynamodb');
const ddb = new DynamoDBClient({});
exports.handler = async (event) => {
  const r = await ddb.send(new GetItemCommand({ TableName: process.env.TABLE, Key: { id: { S: event.pathParameters.id } } }));
  const q = event.queryStringParameters || {};
  if (!r.Item) return { statusCode: 404, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ error: 'nf', q }) };
  return { statusCode: 200, multiValueHeaders: { 'Content-Type': ['application/json'] }, body: JSON.stringify({ id: r.Item.id.S, count: r.Item.count ? Number(r.Item.count.N) : 0 }) };
};""",
    "consumer": """
const { DynamoDBClient, UpdateItemCommand } = require('@aws-sdk/client-dynamodb');
const ddb = new DynamoDBClient({});
let calls = 0;
exports.handler = async (event) => {
  calls += 1;
  if (calls === 1) throw new Error('first delivery fails');
  for (const r of event.Records) {
    const id = JSON.parse(r.body).id;
    await ddb.send(new UpdateItemCommand({ TableName: process.env.TABLE, Key: { id: { S: id } },
      UpdateExpression: 'ADD #c :one', ExpressionAttributeNames: { '#c': 'count' }, ExpressionAttributeValues: { ':one': { N: '1' } } }));
  }
};""",
}


@pytest.mark.skipif(not shutil.which("node"), reason="needs node")
def test_judge_run_routes_steps_shares_tables_and_delivers_messages(tmp_path):
    node_modules = str(verify.VERIFY_TOOLS / "node_modules")
    fns = {}
    for name, src in HANDLERS.items():
        d = tmp_path / "fn" / name
        d.mkdir(parents=True)
        (d / "index.js").write_text(src, encoding="utf-8")
        env = {"TABLE": "Items", "NODE_PATH": node_modules, "QUEUE_URL": "https://sqs.us-east-1.amazonaws.com/000000000000/Q"}
        fns[name] = {"name": name, "dir": str(d), "handler": "index.handler", "env": env, "memory": 128, "runtime": "nodejs22.x",
                     "layers": [], "sqs_events": [{"queue": "Q", "batch_size": 10, "report_failures": False}] if name == "consumer" else []}
    put = {"kind": "invoke", "function": "put", "resource": "/items", "authorized": True, "http_api": False}
    get = {"kind": "invoke", "function": "get", "resource": "/items/{id}", "authorized": True, "http_api": False}
    gw = {"unauthorized": {"statusCode": 401, "headers": {"Content-Type": "application/json"}, "body": '{"message":"Unauthorized"}'},
          "no_route": {"statusCode": 403, "headers": {"Content-Type": "application/json"}, "body": '{"message":"Missing Authentication Token"}'},
          "error_502": {"statusCode": 502, "headers": {}, "body": "{}"}, "timeout_504": {"statusCode": 504, "headers": {}, "body": "{}"}}
    unit = {"id": "u", "identity": "u1", "fixtures": {"Items": [{"id": "seed"}]}, "vars": {"u1.id": "sub-1"},
            "auth": {"u1": {"header": {"Authorization": "Bearer m2s-token-u1"}, "claims": {"sub": "sub-1"}}},
            "rules": [],
            "steps": [{"name": "add", "method": "POST", "path": "/items", "body": {"id": "a"}, "save": {"made": "body.id"}, "route": put},
                      {"name": "read", "method": "GET", "path": "/items/${made}?x=1&x=2", "route": get},
                      {"name": "seeded", "method": "GET", "path": "/items/seed", "route": get},
                      {"name": "anon", "identity": None, "method": "GET", "path": "/items/a", "route": get},
                      {"name": "forged", "method": "GET", "path": "/items/a", "headers": {"Authorization": "Bearer other"}, "route": get},
                      {"name": "nowhere", "method": "DELETE", "path": "/items", "route": {"kind": "no_route"}},
                      {"name": "gone", "method": "POST", "path": "/login", "route": {"kind": "dropped", "reason": "Cognito"}}]}
    job = {"mode": "local", "backend": "dynalite", "out_dir": str(tmp_path / "out"), "functions": fns,
           "tables": [{"name": "Items", "create": {"TableName": "Items", "BillingMode": "PAY_PER_REQUEST",
                                                   "KeySchema": [{"AttributeName": "id", "KeyType": "HASH"}],
                                                   "AttributeDefinitions": [{"AttributeName": "id", "AttributeType": "S"}]}}],
           "queues": {"Q": {"max_receive": 3}}, "gateway": gw, "units": [unit]}
    (tmp_path / "job.json").write_text(json.dumps(job), encoding="utf-8")
    p = subprocess.run(["node", str(verify.VERIFY_TOOLS / "judge-run.js"), str(tmp_path / "job.json")], capture_output=True,
                       text=True, encoding="utf-8", timeout=180, env=verify._env())
    assert p.returncode == 0, p.stderr[-2000:]
    raw = json.loads((tmp_path / "out" / "judge_raw.json").read_text(encoding="utf-8"))
    u = raw["units"][0]
    assert "infra_error" not in u, u
    s = {x["name"]: x for x in u["steps"]}
    assert s["add"]["response"] == {"status": 201, "headers": {"content-type": "application/json"},
                                    "body": {"id": "a", "owner": "sub-1"}, "body_kind": "json"}
    # the message of "add" failed once, was delivered again (receive count 2)
    assert [d["receive_counts"] for d in s["add"]["deliveries"]] == [[1], [2]]
    assert s["read"]["response"]["body"] == {"id": "a", "count": 1}
    assert s["seeded"]["response"]["status"] == 200
    assert s["anon"]["gateway"] == "unauthorized" and s["anon"]["response"]["status"] == 401
    assert s["forged"]["authenticated_as"] is None and s["forged"]["response"]["status"] == 401
    assert s["nowhere"]["response"]["status"] == 403 and s["gone"]["route"] == "dropped"
    assert sorted(i["id"] for i in u["tables_after"]["Items"]) == ["a", "seed"]


# ---------------------------------------------------------------- A2 gate, template

def test_gate_blueprint_checks_the_adapter_map_only_in_a_v2_workspace(tmp_path):
    import gates
    (tmp_path / "storage").mkdir()
    (tmp_path / "storage" / "analysis_report.json").write_text(json.dumps(FACTS), encoding="utf-8")
    assert gates.check_adapter_map(tmp_path) == []  # v1: no judge, no map
    (tmp_path / "judge" / "cases").mkdir(parents=True)
    found = gates.check_adapter_map(tmp_path)
    assert found and found[0]["check_id"] == "AM" and "not found" in found[0]["description"]
    (tmp_path / "storage" / "adapter_map.json").write_text(json.dumps(AMAP), encoding="utf-8")
    assert gates.check_adapter_map(tmp_path) == []


def test_the_v2_adapter_template_and_its_actions(tmp_path):
    from coord.dispatcher import Dispatcher, closed_to
    from coord.template import load_template
    t = load_template("v2-adapter")
    assert [s.id for s in t.tasks] == ["AM", "V1", "V2"] and t.get("AM").mode == "adapter"
    assert (t.get("V1").action, t.get("V2").action) == ("check_adapter", "run_judge")
    dirs, cmds = closed_to(t.get("AM"), tmp_path, None)
    assert dirs == (str(tmp_path / "output" / "lambdas"),) and "*output/lambdas*" in cmds
    d = Dispatcher.__new__(Dispatcher)
    d.actions = {}
    assert hasattr(Dispatcher, "_act_check_adapter") and hasattr(Dispatcher, "_act_run_judge")


def test_check_adapter_counts_status_and_body_of_data_steps_only():
    st = lambda m, t: {"monolith": m, "target": t}
    cases = {
        "list": {"verdict": "fail", "steps": {
            "ok": {"verdict": "pass", "status": st(200, 200)},
            "hdr": {"verdict": "fail", "status": st(200, 200), "mismatches": [
                {"path": "steps.hdr.headers.access-control-allow-credentials", "why": "header missing"}]},
            "anon": {"verdict": "fail", "status": st(401, 401), "answered_by": "API Gateway (unauthorized)",
                     "mismatches": [{"path": "steps.anon.body", "why": "keys differ"}]},
            "dup": {"verdict": "fail", "status": st(500, 200), "mismatches": [{"path": "steps.dup.status", "why": "differs"}]}}},
        "one": {"verdict": "fail", "steps": {
            "hit": {"verdict": "fail", "status": st(200, 200), "mismatches": [
                {"path": "steps.hit.body", "why": "keys differ", "missing": ["name"], "extra": ["title"]},
                {"path": "steps.hit.headers.x", "why": "header missing"}]}}}}
    failed, other, n = judge.adapter_failures(cases)
    assert n == 3
    assert list(failed) == ["one"] and [m["path"] for m in failed["one"]["hit"]] == ["steps.hit.body"]
    assert other == {"list": ["anon", "dup", "hdr"]}
