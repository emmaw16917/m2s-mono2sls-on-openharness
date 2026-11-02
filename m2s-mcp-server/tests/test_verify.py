"""Tests for verify.py (run_validate / run_coverage / run_equivalence).

Unit tests use a small synthetic template. The integration test runs the
real engine (sam build + dynalite + local Node) against bookstore-oh's
generated code with the cases in fixtures/verify_bookstore; it needs sam,
npm access and the (gitignored) runs/bookstore-oh workspace, so it only runs
with M2S_VERIFY_INTEGRATION=1.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import verify  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
TEMPLATE = """
AWSTemplateFormatVersion: '2010-09-09'
Transform: AWS::Serverless-2016-10-31
Parameters:
  Environment:
    Type: String
    Default: dev
Globals:
  Function:
    Runtime: nodejs22.x
    Environment:
      Variables:
        STAGE: !Ref Environment
Resources:
  BooksTable:
    Type: AWS::DynamoDB::Table
    Properties:
      TableName: !Sub ${Environment}-books
      BillingMode: PAY_PER_REQUEST
      AttributeDefinitions: [{AttributeName: id, AttributeType: S}]
      KeySchema: [{AttributeName: id, KeyType: HASH}]
  Queue:
    Type: AWS::SQS::Queue
  Api:
    Type: AWS::Serverless::Api
    Properties:
      StageName: Prod
      Auth:
        DefaultAuthorizer: CognitoAuth
  GetBook:
    Type: AWS::Serverless::Function
    Properties:
      CodeUri: lambdas/books-get/
      Handler: handler.handler
      Environment:
        Variables:
          BOOKS_TABLE: !Ref BooksTable
          QUEUE_URL: !Ref Queue
          QUEUE_ARN: !GetAtt Queue.Arn
          NAME: !Sub "${AWS::StackName}-x"
      Events:
        Get:
          Type: Api
          Properties: {RestApiId: !Ref Api, Path: "/api/books/{id}", Method: get}
        Public:
          Type: Api
          Properties: {RestApiId: !Ref Api, Path: /api/public, Method: get, Auth: {Authorizer: NONE}}
  Consumer:
    Type: AWS::Serverless::Function
    Properties:
      CodeUri: lambdas/consumer/
      Handler: handler.handler
      Events:
        Q:
          Type: SQS
          Properties: {Queue: !GetAtt Queue.Arn}
"""
BLUEPRINT = {
    "lambda_functions": [
        {"name": "books-get", "trigger_type": "api", "entry_points": ["GET /api/books/{id}", "GET /api/public"]},
        {"name": "consumer", "trigger_type": "sqs"},
    ],
    "dynamodb_tables": [{"logical_name": "Books"}],
    "sqs_queues": [{"logical_name": "Queue"}],
}
ANALYSIS = {"entry_points": [{"method": "GET", "path": "/api/books/:id", "responses": [{"status": 200}, {"status": 404}],
                              "business_rules": [{"rule": "x"}]}]}


@pytest.fixture()
def ws(tmp_path):
    (tmp_path / "output").mkdir()
    (tmp_path / "output" / "template.yaml").write_text(TEMPLATE, encoding="utf-8")
    (tmp_path / "storage").mkdir()
    (tmp_path / "storage" / "blueprint.json").write_text(json.dumps(BLUEPRINT), encoding="utf-8")
    (tmp_path / "storage" / "analysis_report.json").write_text(json.dumps(ANALYSIS), encoding="utf-8")
    return tmp_path


def _case(**kw):
    base = {"id": "c1", "function": "books-get", "kind": "happy", "spec_refs": ["GET /api/books/:id#responses[0]"],
            "request": {"method": "GET", "path": "/api/books/b1", "identity": {"sub": "u1"}},
            "fixtures": {"Books": [{"id": "b1"}]}, "expect": {"status": 200}}
    base.update(kw)
    return base


def test_path_match():
    assert verify._path_match("/api/books/{id}", "/api/books/b1") == {"id": "b1"}
    assert verify._path_match("/api/cart/:bookId", "/api/cart/x") == {"bookId": "x"}
    assert verify._path_match("/api/cart", "/api/cart/x") is None
    assert verify._path_match("/p/{proxy+}", "/p/a") == {"proxy": "a"}


def test_model_resolves_env_and_auth(ws):
    m = verify.Model(ws)
    env = m.functions["books-get"]["env"]
    assert env["BOOKS_TABLE"] == "BooksTable"
    assert env["QUEUE_URL"] == "https://sqs.us-east-1.amazonaws.com/000000000000/Queue"
    assert env["QUEUE_ARN"] == "arn:aws:sqs:us-east-1:000000000000:Queue"
    assert env["NAME"] == "m2sv-x" and env["STAGE"] == "dev"
    assert [r["authorized"] for r in m.functions["books-get"]["api"]] == [True, False]
    assert m.table_id("Books") == "BooksTable" and m.queue_id("Queue") == "Queue"
    with pytest.raises(verify.VerifyError):
        m.table_id("Carts")


def test_build_event_api_and_gateway_401(ws):
    m = verify.Model(ws)
    ev, gw = verify.build_event(m, _case(request={"method": "GET", "path": "/api/books/b1", "query": {"q": ["a", "b"]},
                                                  "identity": {"sub": "u1", "email": "e@x"}}))
    assert gw is None and ev["pathParameters"] == {"id": "b1"}
    assert ev["queryStringParameters"] == {"q": "b"} and ev["multiValueQueryStringParameters"] == {"q": ["a", "b"]}
    assert ev["requestContext"]["authorizer"]["claims"]["sub"] == "u1"
    ev, gw = verify.build_event(m, _case(request={"method": "GET", "path": "/api/books/b1", "identity": None}))
    assert ev is None and gw["statusCode"] == 401
    ev, gw = verify.build_event(m, _case(request={"method": "GET", "path": "/api/public", "identity": None}))
    assert gw is None and "authorizer" not in ev["requestContext"]


def test_gateway_401_carries_gateway_response_headers(ws):
    tpl = TEMPLATE.replace("""Parameters:
  Environment:""", """Parameters:
  AllowedOrigin:
    Type: String
    Default: http://localhost:3000
  Environment:""").replace("""      Auth:
        DefaultAuthorizer: CognitoAuth""", """      Auth:
        DefaultAuthorizer: CognitoAuth
      GatewayResponses:
        UNAUTHORIZED:
          ResponseParameters:
            Headers:
              Access-Control-Allow-Origin: !Sub "'${AllowedOrigin}'"
              Access-Control-Allow-Credentials: "'true'"
        DEFAULT_4XX:
          ResponseParameters:
            Headers:
              Access-Control-Allow-Origin: {"Fn::Sub": "'${AllowedOrigin}'"}""")
    (ws / "output" / "template.yaml").write_text(tpl, encoding="utf-8")
    m = verify.Model(ws)
    assert m.gateway_responses["DEFAULT_4XX"]["headers"] == {"Access-Control-Allow-Origin": "http://localhost:3000"}
    _, gw = verify.build_event(m, _case(request={"method": "GET", "path": "/api/books/b1", "identity": None}))
    assert gw["statusCode"] == 401
    assert gw["headers"] == {"Content-Type": "application/json", "Access-Control-Allow-Origin": "http://localhost:3000",
                             "Access-Control-Allow-Credentials": "true"}
    # without UNAUTHORIZED the authorizer's 401 falls back to DEFAULT_4XX
    del m.gateway_responses["UNAUTHORIZED"]
    _, gw = verify.build_event(m, _case(request={"method": "GET", "path": "/api/books/b1", "identity": None}))
    assert gw["headers"]["Access-Control-Allow-Origin"] == "http://localhost:3000"
    assert "Access-Control-Allow-Credentials" not in gw["headers"]


def test_judge_headers(ws):
    m = verify.Model(ws)
    raw = {"result": {"statusCode": 200, "body": "{}",
                      "headers": {"access-control-allow-origin": "http://localhost:3000", "Content-Type": "application/json"},
                      "multiValueHeaders": {"Set-Cookie": ["cartId=c1; Path=/; HttpOnly", "x=1"]}}, "calls": []}
    good = {"headers": {"equals": {"Access-Control-Allow-Origin": "http://localhost:3000"},
                        "contains": {"set-cookie": "cartId=c1"}, "absent": ["Access-Control-Allow-Credentials"]}}
    assert verify.judge(m, _case(expect=good), raw) == []
    bad = {"headers": {"equals": {"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Credentials": "true"},
                       "contains": {"Set-Cookie": "cartId=c2"}, "absent": ["content-type"]}}
    mism = verify.judge(m, _case(expect=bad), raw)
    assert len(mism) == 4
    assert any("Access-Control-Allow-Credentials" in x and "absent" in x for x in mism)
    # a header sent in both maps is not counted twice
    both = {"result": {"statusCode": 200, "headers": {"Set-Cookie": "a=1"}, "multiValueHeaders": {"set-cookie": ["a=1", "b=2"]}}}
    assert verify._response_headers(both["result"]) == {"set-cookie": ["a=1", "b=2"]}


def test_judge_headers_contains_all_and_count(ws):
    m = verify.Model(ws)
    raw = {"result": {"statusCode": 200, "body": "{}",
                      "headers": {"Access-Control-Allow-Origin": "http://a", "access-control-allow-origin": "http://a"},
                      "multiValueHeaders": {"Set-Cookie": ["cartId=9f3; Path=/; HttpOnly", "cartId=9f3; Path=/; HttpOnly"]}}}
    # every substring in the list must be in the same value
    assert verify.judge(m, _case(expect={"headers": {"contains": {"Set-Cookie": ["cartId=", "HttpOnly", "Path=/"]}}}), raw) == []
    assert verify.judge(m, _case(expect={"headers": {"contains": {"Set-Cookie": ["cartId=", "Secure"]}}}), raw)
    # duplicates are counted: a case-variant name and a repeated multi value are both real duplicates
    assert verify.judge(m, _case(expect={"headers": {"count": {"Access-Control-Allow-Origin": 2, "set-cookie": 2, "X-None": 0}}}), raw) == []
    mism = verify.judge(m, _case(expect={"headers": {"count": {"Access-Control-Allow-Origin": 1}}}), raw)
    assert len(mism) == 1 and "expected 1 value(s), got 2" in mism[0]
    # a headers pair repeated in multiValueHeaders is not a second header
    assert verify._response_headers({"headers": {"X-A": "1"}, "multiValueHeaders": {"x-a": ["1"]}}) == {"x-a": ["1"]}


def test_check_case_headers_schema(ws):
    m = verify.Model(ws)
    assert verify.check_case(m, _case(expect={"status": 200, "headers": {"equals": {"X-A": "1"}, "absent": ["X-B"]}}), with_template=True) == []
    assert verify.check_case(m, _case(expect={"status": 200, "headers": {"contains": {"X-A": ["a", "b"]}, "count": {"X-A": 1}}}), with_template=True) == []
    for bad in ({}, {"exact": {"X-A": "1"}}, {"equals": {"X-A": 1}}, {"absent": "X-B"}, ["X-A"],
                {"contains": {"X-A": []}}, {"contains": {"X-A": ["a", 1]}}, {"count": {"X-A": -1}}, {"count": {"X-A": True}}):
        errs = verify.check_case(m, _case(expect={"status": 200, "headers": bad}), with_template=True)
        assert any("expect.headers" in e for e in errs), bad


def test_build_event_sqs(ws):
    m = verify.Model(ws)
    ev, _ = verify.build_event(m, {"id": "s", "function": "consumer", "sqs": {"records": [{"message_id": "m1", "body": {"a": 1}}]}})
    rec = ev["Records"][0]
    assert rec["messageId"] == "m1" and json.loads(rec["body"]) == {"a": 1} and rec["eventSourceARN"].endswith(":Queue")


def test_check_case_errors(ws):
    m = verify.Model(ws)
    assert verify.check_case(m, _case(), with_template=True) == []
    errs = verify.check_case(m, _case(spec_refs=["GET /api/books/:id#responses[5]"], extra=1,
                                      fixtures={"Carts": []}, expect={}), with_template=True)
    text = "\n".join(errs)
    assert "unknown keys" in text and "responses has 2 items" in text and "unknown table 'Carts'" in text
    assert "at least one of status" in text
    # V1 (no template yet) checks names against the blueprint
    assert verify.check_case(verify.Model(ws, need_template=False), _case(), with_template=False) == []


def test_spec_ref_to_architect_resolution(ws):
    bp = dict(BLUEPRINT, metadata={"spec_resolutions": [{"issue_id": "SI-2", "decision": "d"}]})
    (ws / "storage" / "blueprint.json").write_text(json.dumps(bp), encoding="utf-8")
    m = verify.Model(ws)
    assert verify.check_case(m, _case(spec_refs=["blueprint:metadata.spec_resolutions[SI-2]"]), with_template=True) == []
    errs = verify.check_case(m, _case(spec_refs=["blueprint:metadata.spec_resolutions[SI-9]"]), with_template=True)
    assert errs and "no issue_id 'SI-9'" in errs[0]


def test_case_level_faults_are_checked(ws):
    m = verify.Model(ws)
    ok = _case(faults=[{"match": {"op": "TransactWriteItems", "table": "Books"}, "nth": 1, "kind": "tx_conflict"}])
    assert verify.check_case(m, ok, with_template=True) == []
    bad = _case(faults=[{"match": {"op": "GetItem", "table": "Nope"}, "kind": "explode"}])
    text = " | ".join(verify.check_case(m, bad, with_template=True))
    assert "kind must be one of" in text
    runs = verify._prepare_runs(m, [ok])
    assert runs[0]["faults"] == [{"match": {"op": "TransactWriteItems", "table": "BooksTable"}, "nth": 1, "kind": "tx_conflict"}]


def test_fault_match_item_targets_an_operation_by_content(ws):
    m = verify.Model(ws)
    ok = _case(faults=[{"match": {"op": "TransactWriteItems", "item": {"pk": "c#order-1#1"}}, "nth": 1, "kind": "validation"}])
    assert verify.check_case(m, ok, with_template=True) == []
    bad = _case(faults=[{"match": {"item": "c#order-1"}, "kind": "validation"}])
    assert "match.item must be" in " | ".join(verify.check_case(m, bad, with_template=True))


def test_judge(ws):
    m = verify.Model(ws)
    raw = {"result": {"statusCode": 200, "body": json.dumps({"id": "b1", "tags": ["x"]})},
           "calls": [{"op": "GetItem", "table": "BooksTable"}], "messages": [{"queue": "Queue", "body": json.dumps({"n": 1})}],
           "tables_after": {"BooksTable": [{"id": "b1"}]}}
    good = {"status": 200, "body": {"subset": {"id": "b1"}, "types": {"tags": "array"}, "has_fields": ["tags.0"]},
            "tables_after": {"Books": {"count": 1, "contains": [{"id": "b1"}]}},
            "sdk_calls": {"includes": [{"op": "GetItem", "table": "Books", "count": 1}, {"table": "Books"}], "excludes": [{"op": "PutItem"}], "total": 1},
            "messages": {"Queue": {"count": 1, "contains": [{"n": 1}]}}}
    assert verify.judge(m, _case(expect=good), raw) == []
    bad = {"status": 404, "body": {"equals": {"id": "b2"}}, "tables_after": {"Books": {"count": 0}},
           "sdk_calls": {"excludes": [{"table": "Books"}]}, "messages": {"Queue": {"count": 2}}}
    assert len(verify.judge(m, _case(expect=bad), raw)) == 5
    thrown = {"error": {"type": "Error", "message": "boom"}, "calls": []}
    assert verify.judge(m, _case(expect={"error": {}}), thrown) == []
    assert "handler threw" in verify.judge(m, _case(expect={"status": 200}), thrown)[0]
    sqs = {"result": {"batchItemFailures": [{"itemIdentifier": "m2"}]}}
    assert verify.judge(m, _case(expect={"batch_item_failures": ["m2"]}), sqs) == []
    assert verify.judge(m, _case(expect={"batch_item_failures": []}), sqs)


def test_observation_normalizes_nondeterminism():
    raw = {"calls": [{"op": "PutItem", "table": "T", "item": {"id": "123e4567-e89b-12d3-a456-426614174000"}}],
           "result": {"statusCode": 200, "body": json.dumps({"orderId": "123e4567-e89b-12d3-a456-426614174000"})},
           "tables_after": {"T": [{"id": "9b2f0c4e-1111-4222-8333-444455556666"}, {"id": "0a2f0c4e-1111-4222-8333-444455556666"}]}}
    o = verify._observation(raw)
    assert o["calls"][0]["item"]["id"] == "<uuid>"
    assert o["outcome"]["result"]["body"] == {"<json>": {"orderId": "<uuid>"}}
    assert verify._observation(raw) == o


def test_normalize_epoch_seconds_and_future_ms():
    import time
    now_s = time.time()
    assert verify.normalize(int(now_s) + 7 * 86400) == "<epoch-s>"          # TTL 7 days ahead
    assert verify.normalize(int(now_s * 1000) + 30 * 86400 * 1000) == "<epoch-ms>"
    assert verify.normalize(42) == 42 and verify.normalize(1.5) == 1.5


def test_observation_effective_calls():
    """H1 (a): faulted attempts don't count; repeated idempotent operations
    collapse (a retried Delete), repeated non-idempotent ones don't (ADD)."""
    d = {"op": "DeleteItem", "table": "T", "key": {"id": "a"}}
    u = {"op": "UpdateItem", "table": "T", "key": {"id": "a"}, "update": "ADD n :q", "values": {":q": 1}}
    once = verify._observation({"calls": [d, u]})
    retried = verify._observation({"calls": [dict(d, fault="network", identity="x", occurrence=1), d, d, u]})
    assert once["calls"] == retried["calls"]
    assert retried["faults_applied"] == [{"match": {"op": "DeleteItem", "table": "T", "identity": "x"}, "nth": 1, "kind": "network"}]
    double_add = verify._observation({"calls": [d, u, u]})
    assert double_add["calls"] != once["calls"]


def test_observation_counts_emulated_conflicts_without_pinning_them():
    """An emulated TransactionConflict changed nothing (not an effective call)
    and is not an injected fault (never pinned); H1 (c) compares the count."""
    t = {"op": "TransactWriteItems", "transact": [{"kind": "Update", "table": "T", "key": {"id": "a"}}]}
    rejected = dict(t, conflict="emulated", backend_error="com.amazonaws.dynamodb.v20120810#TransactionCanceledException")
    clean = verify._observation({"calls": [t]})
    retried = verify._observation({"calls": [rejected, t]})
    assert clean["calls"] == retried["calls"]
    assert (clean["conflicts"], retried["conflicts"]) == (0, 1)
    assert retried["faults_applied"] == []


@pytest.mark.skipif(os.environ.get("M2S_VERIFY_INTEGRATION") != "1", reason="set M2S_VERIFY_INTEGRATION=1 (needs sam, npm, runs/bookstore-oh)")
def test_integration_bookstore_local(tmp_path):
    src = REPO / "Mono2Sls-artifacts" / "M2S" / "runs" / "bookstore-oh"
    if not (src / "output" / "template.yaml").exists():
        pytest.skip("runs/bookstore-oh not present")
    ws = tmp_path / "vtest"
    shutil.copytree(src / "output", ws / "output", ignore=shutil.ignore_patterns(".aws-sam", "node_modules"))
    (ws / "storage").mkdir()
    shutil.copy(src / "storage" / "blueprint.json", ws / "storage")
    shutil.copy(REPO / "Mono2Sls-artifacts" / "M2S" / "runs" / "bookstore-v1" / "storage" / "analysis_report.json", ws / "storage")
    shutil.copytree(Path(__file__).parent / "fixtures" / "verify_bookstore", ws / "verification")
    r = verify.run_validate(str(ws), mode="local")
    assert r["status"] == "PASS", r
    rec = verify.run_equivalence(str(ws), mode="record", runtime="local")
    assert rec["status"] == "RECORDED", rec
    diff = verify.run_equivalence(str(ws), mode="diff", runtime="local")
    assert diff["status"] == "EQUIVALENT", diff


LAYOUT_TEMPLATE = """
AWSTemplateFormatVersion: '2010-09-09'
Transform: AWS::Serverless-2016-10-31
Resources:
  Shared:
    Type: AWS::Serverless::LayerVersion
    Properties: {ContentUri: layers/shared/, CompatibleRuntimes: [python3.12]}
  Nested:
    Type: AWS::Serverless::Function
    Properties: {CodeUri: lambdas/books/books-get/, Handler: handler.handler, Runtime: nodejs22.x}
  Flat:
    Type: AWS::Serverless::Function
    Properties: {CodeUri: lambdas/items-get/, Handler: handler.lambda_handler, Runtime: python3.12, Layers: [!Ref Shared]}
"""


def test_changed_lines_follow_code_uri_and_layers(tmp_path):
    """Changed files are mapped through CodeUri / ContentUri, whatever the
    directory depth; a layer change counts for every function using it; a
    code file outside every function is reported, not skipped."""
    import subprocess
    out = tmp_path / "output"
    files = {"template.yaml": LAYOUT_TEMPLATE, "lambdas/books/books-get/handler.js": "a\n", "lambdas/items-get/handler.py": "a\n",
             "layers/shared/shared_utils.py": "a\n", "lambdas/orphan/x.py": "a\n"}
    for rel, text in files.items():
        (out / rel).parent.mkdir(parents=True, exist_ok=True)
        (out / rel).write_text(text, encoding="utf-8")
    git = lambda *a: subprocess.run(["git", "-C", str(tmp_path), *a], check=True, capture_output=True)
    git("init", "-q")
    git("add", "-A")
    git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "v0")
    for rel in ("lambdas/books/books-get/handler.js", "lambdas/items-get/handler.py", "layers/shared/shared_utils.py", "lambdas/orphan/x.py"):
        (out / rel).write_text("a\nb\n", encoding="utf-8")
    (out / "lambdas/items-get/requirements.txt").write_text("requests\n", encoding="utf-8")  # not code
    model = verify.Model(tmp_path)
    changed, unmapped = verify._changed_lines(tmp_path, "HEAD", model)
    assert changed == {"books-get": {"handler.js": {2}}, "items-get": {"handler.py": {2}, "layer:Shared:shared_utils.py": {2}}}
    assert unmapped == ["output/lambdas/orphan/x.py"]


def test_changed_coverage_reports_files_no_case_loaded(tmp_path):
    final = tmp_path / "coverage-final.json"
    final.write_text(json.dumps({"/b/Flat/handler.py": {"statementMap": {"0": {"start": {"line": 2}, "end": {"line": 2}}}, "s": {"0": 1}},
                                 "/b/Shared/python/shared_utils.py": {"statementMap": {"0": {"start": {"line": 2}, "end": {"line": 2}}}, "s": {"0": 0}}}),
                     encoding="utf-8")
    r = verify._changed_coverage(final, "Flat", {"handler.py": {2}, "layer:Shared:shared_utils.py": {2}, "helpers.py": {1, 2}})
    assert r["covered"] == 1
    assert [(u["file"], u["line"]) for u in r["uncovered"]] == [("shared_utils.py", 2), ("helpers.py", None)]


@pytest.mark.skipif(os.environ.get("M2S_VERIFY_INTEGRATION") != "1", reason="set M2S_VERIFY_INTEGRATION=1 (needs sam, Docker, amazon/aws-lambda-python:3.12)")
def test_integration_python_rie(tmp_path):
    """A python3.12 function with a shared layer, in the Lambda Python image
    against DynamoDB Local: cases, coverage (coverage.py) and a fault run."""
    ws = tmp_path / "pyws"
    shutil.copytree(Path(__file__).parent / "fixtures" / "verify_python", ws)
    r = verify.run_validate(str(ws), mode="rie")
    assert r["status"] == "PASS", r
    c = verify.run_coverage(str(ws), mode="rie")
    fn = c["functions"]["items-get"]
    assert fn["uncovered_count"] == 2 and fn["functions_pct"] == 100.0  # the 400 branch: its branch arm + its statement
    e = verify.run_equivalence(str(ws), mode="record", label="t")
    assert e["status"] == "RECORDED" and e["runs"] == 7  # 2 fault-scenario runs + 5 plain cases


@pytest.mark.skipif(os.environ.get("M2S_VERIFY_INTEGRATION") != "1", reason="set M2S_VERIFY_INTEGRATION=1 (needs sam, Docker, amazon/aws-lambda-python:3.12)")
def test_integration_python_consumer_pattern_a(tmp_path):
    """The Python Pattern A consumer (the m2s-async-patterns-kb snippet's
    logic) against DynamoDB Local: duplicates, merged keys, injected conflicts
    pinned with match.item, a 100-item message in two chunks, redelivery
    after a partial commit."""
    ws = tmp_path / "pyc"
    shutil.copytree(Path(__file__).parent / "fixtures" / "verify_python_consumer", ws)
    r = verify.run_validate(str(ws), mode="rie")
    assert r["status"] == "PASS" and r["total"] == 8, r
    assert r["cases_with_conflicts"] == []


def test_filtered_record_merges_into_the_existing_baseline(ws, monkeypatch):
    cases = [{"id": "a1", "function": "books-get"}, {"id": "c1", "function": "consumer"}]
    faults = [{"id": "fa", "function": "books-get", "cases": ["a1"], "rules": []},
              {"id": "fc", "function": "consumer", "cases": ["c1"], "rules": []}]
    monkeypatch.setattr(verify, "load_cases", lambda ws, assets=None: cases)
    monkeypatch.setattr(verify, "load_faults", lambda ws, assets=None: faults)
    monkeypatch.setattr(verify, "check_fault", lambda *a, **k: [])
    monkeypatch.setattr(verify, "sam_build", lambda root: root)
    monkeypatch.setattr(verify, "_mode", lambda r: "local")
    tag = {"v": "first"}

    def prepare(m, cs, scen=None, pinned=None):
        if scen:
            return [{"run_key": f"{sid}::{cid}"} for sid, f in scen.items() for cid in f["cases"]]
        return [{"run_key": c["id"]} for c in cs]
    monkeypatch.setattr(verify, "_prepare_runs", prepare)
    monkeypatch.setattr(verify, "_run_controller", lambda m, b, runs, **k: {"cases": [
        {"run_key": r["run_key"], "result": {"statusCode": 200, "body": tag["v"]}, "calls": []} for r in runs]})

    (ws / "verification").mkdir(exist_ok=True)
    full = verify.run_equivalence(str(ws), mode="record", runtime="local")
    assert full["runs"] == 4 and not full["merged_into_existing"]
    tag["v"] = "second"
    part = verify.run_equivalence(str(ws), mode="record", runtime="local", functions=["consumer"])
    assert part["runs"] == 2 and part["merged_into_existing"] and part["total_runs_in_baseline"] == 4
    doc = json.loads((ws / "verification" / "fault_baseline_v0.json").read_text(encoding="utf-8"))
    bodies = {k: v["outcome"]["result"]["body"] for k, v in doc["observations"].items()}
    assert bodies == {"fa::a1": "first", "a1": "first", "fc::c1": "second", "c1": "second"}
    assert set(doc["scenarios"]) == {"fa", "fc"} and doc["partial_updates"][0]["functions"] == ["consumer"]
    # a runtime mismatch is refused rather than mixed into one baseline
    monkeypatch.setattr(verify, "_mode", lambda r: "rie")
    assert verify.run_equivalence(str(ws), mode="record", functions=["consumer"])["status"] == "FAIL"


def test_coverage_counts_only_project_source(ws, tmp_path):
    """Packages that sam build bundles next to the handler (pip / npm) are
    not code under test; a shared layer's own files still count."""
    src = ws / "output" / "lambdas" / "books-get"
    (src / "lib").mkdir(parents=True)
    (src / "handler.py").write_text("x = 1\n", encoding="utf-8")
    (src / "lib" / "util.py").write_text("y = 2\n", encoding="utf-8")
    (ws / "output" / "layers" / "shared").mkdir(parents=True)
    (ws / "output" / "layers" / "shared" / "common.py").write_text("z = 3\n", encoding="utf-8")
    m = verify.Model(ws)
    m.layers = {"SharedLayer": "layers/shared"}
    m.functions["books-get"]["layers"] = ["SharedLayer"]
    own = verify._project_files(ws, m, "books-get")
    assert own == {"": {"handler.py", "lib/util.py"}, "SharedLayer": {"common.py"}}

    def fc(hit):  # one statement, one 2-arm branch, one function
        return {"s": {"0": hit}, "b": {"0": [hit, 0]}, "f": {"0": hit},
                "statementMap": {"0": {"start": {"line": 1}, "end": {"line": 1}}},
                "branchMap": {"0": {"type": "if", "loc": {"start": {"line": 1}}, "locations": [{"start": {"line": 1}}, {"start": {"line": 1}}]}}}
    b = "/build/GetBook/"
    final = tmp_path / "coverage-final.json"
    final.write_text(json.dumps({b + "handler.py": fc(1), b + "lib/util.py": fc(1), "/build/SharedLayer/python/common.py": fc(1),
                                 b + "stripe/api.py": fc(0), b + "urllib3/pool.py": fc(0)}), encoding="utf-8")
    r = verify._summarize_coverage(final, Path("GetBook"), own)
    assert set(r["files"]) == {"handler.py", "lib/util.py", "layer:SharedLayer:common.py"}
    assert r["excluded_files"] == 2 and r["statements_pct"] == 100.0 and r["branches_pct"] == 50.0
    assert all(not u["file"].startswith(("stripe", "urllib3")) for u in r["uncovered"])
    # without the source map the old behaviour stays (everything but node_modules)
    assert len(verify._summarize_coverage(final, Path("GetBook"))["files"]) == 5


# -- contract testing (contract 2, PIPELINE_REDESIGN.md section 3.6) --------

def _contract_ws(ws, contract=2, rulings=()):
    meta = {"spec_resolutions": [{"issue_id": i, "decision": "d", "class": "implementation-trace"} for i in rulings]}
    if contract:
        meta["verification_contract"] = contract
    (ws / "storage" / "blueprint.json").write_text(json.dumps(dict(BLUEPRINT, metadata=meta)), encoding="utf-8")
    return verify.Model(ws)


def test_contract2_sdk_calls_only_side_effects(ws):
    m = _contract_ws(ws)
    ok = _case(expect={"status": 200, "sdk_calls": {"includes": [{"op": "PutItem", "table": "Books", "count": 1}],
                                                    "excludes": [{"op": "GetItem", "table": "Books"}]},
                       "trace": {"reads": "GetItem x2"}})
    assert verify.check_case(m, ok, with_template=True) == []
    text = " | ".join(verify.check_case(m, _case(expect={"status": 200, "sdk_calls": {
        "includes": [{"op": "GetItem", "table": "Books"}, {"table": "Books"}], "total": 3}}), with_template=True))
    assert "total is not allowed" in text and "got GetItem" in text and "any op on Books" in text
    # contract 1 (no switch) keeps the original rules
    m1 = _contract_ws(ws, contract=None)
    assert verify.check_case(m1, _case(expect={"status": 200, "sdk_calls": {"includes": [{"op": "GetItem"}], "total": 1}}),
                             with_template=True) == []


def test_contract2_positional_faults_need_an_item(ws):
    m = _contract_ws(ws)
    assert verify.check_case(m, _case(faults=[{"match": {"op": "GetItem", "table": "Books"}, "nth": 2, "kind": "error"}]),
                             with_template=True)
    for rule in ({"match": {"op": "PutItem", "table": "Books"}, "nth": "all", "kind": "error"},
                 {"match": {"op": "PutItem", "item": {"id": "b1"}}, "nth": 2, "kind": "error"},
                 {"match": {"item": {"id": "b1"}}, "nth": 2, "kind": "error"}):
        assert verify.check_case(m, _case(faults=[rule]), with_template=True) == []
    m1 = _contract_ws(ws, contract=None)
    assert verify.check_case(m1, _case(faults=[{"match": {"op": "GetItem"}, "nth": 2, "kind": "error"}]), with_template=True) == []


def test_alternatives(ws):
    m = _contract_ws(ws, contract=None, rulings=["SI-1"])
    alt = {"alternatives": [{"status": 400, "body": {"subset": {"type": "ValueError"}}}, {"status": 200}]}
    case = _case(expect=alt, spec_refs=["blueprint:metadata.spec_resolutions[SI-1]"])
    assert verify.check_case(m, case, with_template=True) == []
    assert "only allowed in a case whose spec_refs cite" in " | ".join(verify.check_case(m, _case(expect=alt), with_template=True))
    assert "at least two" in " | ".join(verify.check_case(m, _case(expect={"alternatives": [{"status": 200}]},
                                                                   spec_refs=case["spec_refs"]), with_template=True))
    assert verify.judge(m, case, {"result": {"statusCode": 200}}) == []
    assert verify.judge(m, case, {"result": {"statusCode": 400, "body": json.dumps({"type": "ValueError"})}}) == []
    out = verify.judge(m, case, {"result": {"statusCode": 500}})
    assert len(out) == 1 and out[0].startswith("no alternative matched") and "[0]" in out[0] and "[1]" in out[0]


def test_not_applicable_needs_an_implementation_trace_ruling(ws):
    m = _contract_ws(ws, contract=None, rulings=["SI-1"])
    fault = [{"match": {"op": "GetItem", "item": {"id": "b1"}}, "nth": 2, "kind": "error"}]
    cited = _case(faults=fault, expect={"status": 400}, spec_refs=["blueprint:metadata.spec_resolutions[SI-1]"])
    unfired = {"result": {"statusCode": 200}, "rules_fired": [0]}
    assert "SI-1" in verify.not_applicable(m, cited, unfired)
    assert verify.not_applicable(m, cited, {"result": {"statusCode": 200}, "rules_fired": [1]}) is None
    assert verify.not_applicable(m, _case(faults=fault, expect={"status": 400}), unfired) is None
    other = _contract_ws(ws, contract=None, rulings=[])
    bp = json.loads((ws / "storage" / "blueprint.json").read_text(encoding="utf-8"))
    bp["metadata"]["spec_resolutions"] = [{"issue_id": "SI-1", "decision": "d"}]  # a ruling, but not implementation-trace
    (ws / "storage" / "blueprint.json").write_text(json.dumps(bp), encoding="utf-8")
    assert verify.not_applicable(verify.Model(ws), cited, unfired) is None
    assert other.trace_rulings == set()


def test_fixtures_accept_time_relative_placeholders(ws, monkeypatch):
    m = verify.Model(ws)
    monkeypatch.setattr(verify.time, "time", lambda: 1_790_534_500.0)
    case = _case(fixtures={"Books": [{"id": "b-{now_ms // 300000}", "ts": "{now_ms // 300000 * 300000}", "note": "{not an expr}"}]})
    item = verify._prepare_runs(m, [case])[0]["fixtures"]["BooksTable"][0]
    assert item == {"id": "b-5968448", "ts": 1790534400000, "note": "{not an expr}"}


def test_contract2_read_faults_name_no_operation(ws):
    m = _contract_ws(ws)
    errs = " | ".join(verify.check_case(m, _case(faults=[{"match": {"op": "Scan", "table": "Books"}, "nth": "all", "kind": "error"}]),
                                        with_template=True))
    assert "names a read operation" in errs
    for rule in ({"match": {"table": "Books"}, "nth": "all", "kind": "error"},
                 {"match": {"op": "@read", "table": "Books"}, "nth": "all", "kind": "error"},
                 {"match": {"op": "PutItem", "table": "Books"}, "nth": "all", "kind": "error"}):
        assert verify.check_case(m, _case(faults=[rule]), with_template=True) == []
    m1 = _contract_ws(ws, contract=None)
    assert verify.check_case(m1, _case(faults=[{"match": {"op": "Scan"}, "nth": "all", "kind": "error"}]), with_template=True) == []


def test_contract2_observation_compares_reads_by_table():
    scan = {"calls": [{"op": "Scan", "table": "T", "values": {":c": "a"}}, {"op": "PutItem", "table": "U", "item": {"id": "1"}}],
            "result": {"statusCode": 200}}
    query = {"calls": [{"op": "Query", "table": "T", "index": "by-c", "key_condition": "#c = :c", "values": {":c": "a"}},
                       {"op": "PutItem", "table": "U", "item": {"id": "1"}}], "result": {"statusCode": 200}}
    assert verify._observation(scan, 2)["calls"] == verify._observation(query, 2)["calls"]
    assert verify._observation(scan, 1)["calls"] != verify._observation(query, 1)["calls"]


def test_contract2_pins_read_faults_by_table():
    raw = {"calls": [{"op": "GetItem", "table": "T", "identity": "k1"}, {"op": "Scan", "table": "T", "fault": "error", "identity": "s"},
                     {"op": "PutItem", "table": "U", "fault": "throttle", "identity": "p", "occurrence": 1}]}
    assert verify._pins(raw, 2) == [{"match": {"op": "@read", "table": "T"}, "nth": 2, "kind": "error"},
                                    {"match": {"op": "PutItem", "table": "U", "identity": "p"}, "nth": 1, "kind": "throttle"}]
    assert verify._pins(raw, 1)[0]["match"]["op"] == "Scan"


def test_index_attributes_are_ignored_by_h1(ws):
    bp = dict(BLUEPRINT, metadata={"spec_resolutions": [{"issue_id": "SI-1", "ruling": "accepted",
                                                         "data_model_change": {"new_attributes": [{"name": "gsiPk", "backfill": "x"}]}}]})
    (ws / "storage" / "blueprint.json").write_text(json.dumps(bp), encoding="utf-8")
    m = verify.Model(ws)
    assert m.index_attributes == {"gsiPk"}
    before = {"calls": [{"op": "UpdateItem", "table": "T", "key": {"id": "1"}, "update": "ADD quantity :q", "values": {":q": 1}}],
              "tables_after": {"T": [{"id": "1", "quantity": 1}]}}
    after = {"calls": [{"op": "UpdateItem", "table": "T", "key": {"id": "1"}, "update": "SET #g = :g ADD quantity :q",
                        "values": {":g": "ALL", ":q": 1}, "names": {"#g": "gsiPk"}}],
             "tables_after": {"T": [{"id": "1", "quantity": 1, "gsiPk": "ALL"}]}}
    a, b = verify._observation(before, 1, m.index_attributes), verify._observation(after, 1, m.index_attributes)
    assert a["calls"] == b["calls"] and a["outcome"] == b["outcome"]
    assert verify._observation(before, 1)["calls"] != verify._observation(after, 1)["calls"]
    other = {**after, "calls": [{**after["calls"][0], "values": {":g": "ALL", ":q": 2}}]}
    assert verify._observation(other, 1, m.index_attributes)["calls"] != a["calls"]  # the real write is still compared


def test_relaxed_read_functions_use_table_level_reads(ws):
    bp = dict(BLUEPRINT, metadata={"spec_resolutions": [{"issue_id": "SI-9", "class": "implementation-trace", "origin": "perf-request REQ-4",
                                                         "ruling": "accepted", "applies_to": ["books-get"], "targets": ["x"]}]})
    (ws / "storage" / "blueprint.json").write_text(json.dumps(bp), encoding="utf-8")
    m = verify.Model(ws)
    assert m.contract == 1 and m.relaxed_read_functions == {"books-get"}
    assert verify._run_contract(m, {"function": "books-get"}) == 2
    assert verify._run_contract(m, {"function": "consumer"}) == 1


def test_unordered_result_compares_arrays_as_multisets(ws):
    bp = dict(BLUEPRINT, metadata={"spec_resolutions": [{"issue_id": "SI-2", "ruling": "accepted", "result_order": "unspecified",
                                                         "applies_to": ["books-get"]}]})
    (ws / "storage" / "blueprint.json").write_text(json.dumps(bp), encoding="utf-8")
    m = verify.Model(ws)
    assert m.unordered_functions == {"books-get"}
    a = {"result": {"statusCode": 200, "body": json.dumps({"products": [{"id": "p1"}, {"id": "p2"}]})}}
    b = {"result": {"statusCode": 200, "body": json.dumps({"products": [{"id": "p2"}, {"id": "p1"}]})}}
    c = {"result": {"statusCode": 200, "body": json.dumps({"products": [{"id": "p2"}, {"id": "p3"}]})}}
    assert verify._observation(a, unordered=True)["outcome"] == verify._observation(b, unordered=True)["outcome"]
    assert verify._observation(a, unordered=True)["outcome"] != verify._observation(c, unordered=True)["outcome"]
    assert verify._observation(a)["outcome"] != verify._observation(b)["outcome"]  # default: order is part of the contract


def test_http_api_payload_1_event(ws):
    tpl = TEMPLATE.replace("""  Api:
    Type: AWS::Serverless::Api
    Properties:
      StageName: Prod
      Auth:
        DefaultAuthorizer: CognitoAuth
""", """  Api:
    Type: AWS::Serverless::HttpApi
""").replace("Type: Api\n", "Type: HttpApi\n").replace("RestApiId: !Ref Api,", "ApiId: !Ref Api, PayloadFormatVersion: '1.0',")
    (ws / "output" / "template.yaml").write_text(tpl, encoding="utf-8")
    m = verify.Model(ws)
    assert [r["http_api"] for r in m.functions["books-get"]["api"]] == [True, True]
    ev, gw = verify.build_event(m, _case(request={"method": "GET", "path": "/api/public", "identity": None}))
    assert gw is None and ev["version"] == "1.0" and ev["pathParameters"] is None and ev["httpMethod"] == "GET"


def _obs(calls, status=None, error=None):
    return {"calls": calls, "outcome": {"error": error, "result": {"statusCode": status} if status else None}}


R_SEG = {"op": "Query", "table": "Seg", "key": "BOS-JFK"}
R_SEG2 = {"op": "Query", "table": "Seg", "key": "JFK-BOS"}
W = {"op": "PutItem", "table": "Booking", "item": {"id": "b"}}


def test_r1b_extra_reads_accepted_on_error_propagation_path():
    extra, why = verify.speculative_extra_reads(_obs([R_SEG], 500), _obs([R_SEG, R_SEG2], 500))
    assert why is None and extra == [R_SEG2]
    extra, why = verify.speculative_extra_reads(_obs([R_SEG], error="Error"), _obs([R_SEG2, R_SEG], error="Error"))
    assert extra == [R_SEG2]


def test_r1b_rejects_authz_success_writes_and_missing_reads():
    assert "authorization" in verify.speculative_extra_reads(_obs([R_SEG], 403), _obs([R_SEG, R_SEG2], 403))[1]
    assert "authorization" in verify.speculative_extra_reads(_obs([R_SEG], 401), _obs([R_SEG, R_SEG2], 401))[1]
    assert "successfully" in verify.speculative_extra_reads(_obs([R_SEG], 200), _obs([R_SEG, R_SEG2], 200))[1]
    assert "writes" in verify.speculative_extra_reads(_obs([R_SEG], 500), _obs([R_SEG, W], 500))[1]
    assert "missing" in verify.speculative_extra_reads(_obs([R_SEG, R_SEG2], 500), _obs([R_SEG2], 500))[1]


def test_http_api_jwt_claims_and_401(ws):
    tpl = TEMPLATE.replace("""    Type: AWS::Serverless::Api
    Properties:
      StageName: Prod
      Auth:
        DefaultAuthorizer: CognitoAuth
""", """    Type: AWS::Serverless::HttpApi
    Properties:
      CorsConfiguration: {AllowOrigins: ['*']}
      Auth:
        DefaultAuthorizer: Jwt
        Authorizers: {Jwt: {JwtConfiguration: {issuer: i, audience: [a]}}}
""").replace("Type: Api\n", "Type: HttpApi\n").replace("RestApiId: !Ref Api,", "ApiId: !Ref Api, PayloadFormatVersion: '1.0',")
    (ws / "output" / "template.yaml").write_text(tpl, encoding="utf-8")
    m = verify.Model(ws)
    ev, gw = verify.build_event(m, _case(request={"method": "GET", "path": "/api/books/b1", "identity": {"sub": "u1", "cognito:groups": ["staff", "admins"]}}))
    assert ev["requestContext"]["authorizer"]["claims"]["cognito:groups"] == "[staff admins]"
    ev, gw = verify.build_event(m, _case(request={"method": "GET", "path": "/api/books/b1", "identity": None, "headers": {"Origin": "https://x"}}))
    assert gw["statusCode"] == 401 and gw["headers"]["Access-Control-Allow-Origin"] == "*"
    ev, gw = verify.build_event(m, _case(request={"method": "GET", "path": "/api/books/b1", "identity": None}))
    assert "Access-Control-Allow-Origin" not in gw["headers"]


def test_rest_list_claim_is_comma_joined(ws):
    m = verify.Model(ws)
    ev, _ = verify.build_event(m, _case(request={"method": "GET", "path": "/api/books/b1", "identity": {"sub": "u1", "cognito:groups": ["staff", "admins"]}}))
    assert ev["requestContext"]["authorizer"]["claims"]["cognito:groups"] == "staff,admins"


def test_observation_drops_reads_of_cached_tables():
    raw = {"calls": [{"op": "Query", "table": "FlightTable"}, {"op": "GetItem", "table": "Other"}, {"op": "PutItem", "table": "FlightTable", "item": {}}],
           "result": {"statusCode": 200}}
    ops = [c["op"] for c in verify._observation(raw, 1, None, False, {"FlightTable"})["calls"]]
    assert sorted(ops) == ["GetItem", "PutItem"]


def test_repeat_must_answer_alike(ws):
    case = _case(repeat=2)
    assert verify.check_case(verify.Model(ws), case, with_template=True) == []
    assert verify.check_case(verify.Model(ws), _case(repeat=9), with_template=True)
    same = {"result": {"statusCode": 200, "body": "[1]"}}
    raw = {**same, "calls": [], "repeats": [same, same]}
    assert verify.judge(verify.Model(ws), _case(expect={"status": 200}), raw) == []
    raw["repeats"] = [same, {"result": {"statusCode": 200, "body": "[2]"}}]
    assert any("repeat" in m for m in verify.judge(verify.Model(ws), _case(expect={"status": 200}), raw))


def test_model_cached_tables(ws):
    bp = json.loads((ws / "storage" / "blueprint.json").read_text(encoding="utf-8"))
    bp["metadata"] = {"spec_resolutions": [{"issue_id": "SI-1", "class": "contract", "applies_to": ["books-get"],
                                            "bounded_staleness": {"tables": ["Books"], "ttl_seconds": 60, "evidence": "x:1"}}]}
    (ws / "storage" / "blueprint.json").write_text(json.dumps(bp), encoding="utf-8")
    assert verify.Model(ws).cached_tables == {"books-get": {"Books"}}


CACHE_TEMPLATE = """
AWSTemplateFormatVersion: '2010-09-09'
Transform: AWS::Serverless-2016-10-31
Resources:
  Items:
    Type: AWS::DynamoDB::Table
    Properties:
      BillingMode: PAY_PER_REQUEST
      AttributeDefinitions: [{AttributeName: id, AttributeType: S}]
      KeySchema: [{AttributeName: id, KeyType: HASH}]
  ItemsGet:
    Type: AWS::Serverless::Function
    Properties:
      CodeUri: lambdas/items-get/
      Handler: handler.handler
      Runtime: nodejs22.x
      Environment: {Variables: {TABLE: !Ref Items}}
      Events:
        Get: {Type: Api, Properties: {Path: "/items/{id}", Method: get}}
"""
CACHE_HANDLER = """
const { DynamoDBClient, GetItemCommand } = require('@aws-sdk/client-dynamodb');
const ddb = new DynamoDBClient({});
const cache = new Map();               // module-level: lives as long as the execution environment
exports.handler = async (event) => {
  const id = event.pathParameters.id;
  let hit = cache.get(id);
  if (!hit) {
    const r = await ddb.send(new GetItemCommand({ TableName: process.env.TABLE, Key: { id: { S: id } } }));
    hit = r.Item ? r.Item.name.S : null;
    cache.set(id, hit);
  }
  return hit ? { statusCode: 200, body: JSON.stringify({ name: hit }) } : { statusCode: 404, body: '{}' };
};
"""


def test_integration_cached_function_isolated_per_case(tmp_path):
    """Two cases seed the same key with different data: without a fresh
    execution environment per case, the second would see the first's cache."""
    sdk = REPO / "m2s-mcp-server" / "verify-tools" / "node_modules" / "@aws-sdk" / "client-dynamodb"
    if not sdk.exists():
        pytest.skip("verify-tools node_modules not installed")
    ws = tmp_path / "cachews"
    fn = ws / "output" / "lambdas" / "items-get"
    fn.mkdir(parents=True)
    (fn / "handler.js").write_text(CACHE_HANDLER, encoding="utf-8")
    (fn / "package.json").write_text(json.dumps({"name": "items-get", "version": "1.0.0", "dependencies": {"@aws-sdk/client-dynamodb": "^3.600.0"}}), encoding="utf-8")
    (ws / "output" / "template.yaml").write_text(CACHE_TEMPLATE, encoding="utf-8")
    (ws / "storage").mkdir()
    bp = {"lambda_functions": [{"name": "items-get", "trigger_type": "api", "entry_points": ["GET /items/{id}"]}],
          "dynamodb_tables": [{"logical_name": "Items"}],
          "metadata": {"spec_resolutions": [{"issue_id": "SI-1", "class": "contract", "applies_to": ["items-get"],
                                             "bounded_staleness": {"tables": ["Items"], "ttl_seconds": 60, "evidence": "app.js:1"}}]}}
    (ws / "storage" / "blueprint.json").write_text(json.dumps(bp), encoding="utf-8")
    (ws / "storage" / "analysis_report.json").write_text(json.dumps({"entry_points": [{"method": "GET", "path": "/items/:id",
                                                                       "responses": [{"status": 200}], "business_rules": [{"rule": "x"}]}]}), encoding="utf-8")
    ev = ws / "verification" / "events" / "items-get"
    ev.mkdir(parents=True)
    for cid, name, extra in (("a", "first", {}), ("b", "second", {"repeat": 3})):
        (ev / f"{cid}.json").write_text(json.dumps({"kind": "happy", "spec_refs": ["GET /items/:id#responses[0]"], **extra,
                                                    "request": {"method": "GET", "path": "/items/k1"}, "fixtures": {"Items": [{"id": "k1", "name": name}]},
                                                    "expect": {"status": 200, "body": {"subset": {"name": name}}}}), encoding="utf-8")
    r = verify.run_validate(str(ws), mode="local")
    assert r["status"] == "PASS", r
