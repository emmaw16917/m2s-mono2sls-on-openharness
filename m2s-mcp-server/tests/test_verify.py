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
