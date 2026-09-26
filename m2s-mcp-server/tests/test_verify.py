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
