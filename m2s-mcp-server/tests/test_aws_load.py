"""perf-tools/aws_load.py: benchmark substitution, table mapping, request build.
The AWS calls themselves are not exercised here (no credentials in unit tests)."""
from __future__ import annotations

import sys
from pathlib import Path

M2S = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(M2S))
sys.path.insert(0, str(M2S / "perf-tools"))

import aws_load  # noqa: E402


def test_subst_replaces_the_identity_placeholder_everywhere():
    data = {"customerId": "u-perf-1", "nested": [{"id": "u-perf-1"}, "keep"], "n": 3}
    assert aws_load._subst(data, "u-perf-1", "real-sub") == {
        "customerId": "real-sub", "nested": [{"id": "real-sub"}, "keep"], "n": 3}


def test_table_of_maps_logical_ids_and_blueprint_names():
    resources = {
        "CartTable": {"type": "AWS::DynamoDB::Table", "physical": "staging-cart"},
        "OrdersTable": {"type": "AWS::DynamoDB::Table", "physical": "staging-orders"},
        "BestsellerUpdateQueue": {"type": "AWS::SQS::Queue", "physical": "q"},
    }
    assert aws_load._table_of(resources, "CartTable") == "staging-cart"
    assert aws_load._table_of(resources, "Cart") == "staging-cart"
    assert aws_load._table_of(resources, "Missing") == ""


def test_request_builds_the_url_body_and_auth_header():
    bench = {"request": {"method": "post", "path": "/api/orders", "identity": {"sub": "u-perf-1"},
                         "body": {"customerId": "u-perf-1", "books": [{"bookId": "b1"}]}}}
    req = aws_load._request(bench, "https://stack.example/staging/", "Bearer token", "u-perf-1", "real-sub")
    assert req["method"] == "POST"
    assert req["url"] == "https://stack.example/staging/api/orders"
    assert req["body"]["customerId"] == "real-sub"
    assert req["headers"]["Authorization"] == "Bearer token"
    assert req["headers"]["Content-Type"] == "application/json"


def test_output_finds_app_specific_cognito_keys():
    assert aws_load._output({"CognitoUserPoolId": "p", "CognitoUserPoolClientId": "c"},
                            "CognitoUserPoolId", "UserPoolId") == "p"
    assert aws_load._output({"UserPoolId": "p2"}, "CognitoUserPoolId", "UserPoolId") == "p2"
    assert aws_load._output({"UserPoolClientId": "c2"}, "UserPoolClientId") == "c2"


def test_load_dispatches_sqs_benchmarks(tmp_path: Path, monkeypatch):
    called = {}

    def fake_sqs(bench, resources):
        called["resources"] = resources
        return {"samples": [1.0], "statuses": {"sent": 10}, "mode": "sqs"}

    monkeypatch.setattr(aws_load, "_load_sqs", fake_sqs)
    result = aws_load._load(tmp_path, {"sqs": {"records": []}}, "https://x", {},
                            {"Q": {"type": "AWS::SQS::Queue", "physical": "q"}})
    assert result["mode"] == "sqs" and called["resources"]["Q"]["physical"] == "q"


def test_app_name_follows_the_workspace(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("M2S_AWS_APP", raising=False)
    assert aws_load._app_name(tmp_path / "bookstore-o-aws") == "bookstore"
    assert aws_load._app_name(tmp_path / "airline-o-aws") == "airline"
    monkeypatch.setenv("M2S_AWS_APP", "airline")
    assert aws_load._app_name(tmp_path / "bookstore-o-aws") == "airline"
