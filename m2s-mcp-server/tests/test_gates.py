"""Tests for gates.py's deterministic consistency checks.

Run with: pytest m2s-mcp-server/tests/test_gates.py -v
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import gates  # noqa: E402

_real_check_tools_unmodified = gates.check_tools_unmodified


@pytest.fixture(autouse=True)
def _tools_check_off(monkeypatch):
    """gate(final)'s TOOLS check looks at the real repo's git state; unit
    tests of the other checks must not depend on it (tested on its own)."""
    monkeypatch.setattr(gates, "check_tools_unmodified", lambda *a, **k: [])

FIXTURES = Path(__file__).resolve().parent / "fixtures"

GOOD_BLUEPRINT = {
    "metadata": {
        "application_name": "TODO",
        "architecture_pattern": "Basic Serverless Architecture",
        "has_authentication": True,
        "has_database": True,
        "has_file_storage": False,
        "has_async_flows": False,
        "notes": "fixture",
    },
    "lambda_functions": [
        {
            "name": "todo-list-items",
            "trigger_type": "api",
            "runtime": "nodejs22.x",
            "handler": "handler.lambda_handler",
            "auth": "required",
            "source_files": ["todo/routes/todo.js"],
            "entry_points": ["GET /item"],
            "environment_variables": {"TODO_TABLE": "${TodoItemsTable}"},
        },
        {
            "name": "todo-create-item",
            "trigger_type": "api",
            "runtime": "nodejs22.x",
            "handler": "handler.lambda_handler",
            "auth": "required",
            "source_files": ["todo/routes/todo.js"],
            "entry_points": ["POST /item"],
            "environment_variables": {"TODO_TABLE": "${TodoItemsTable}"},
        },
    ],
    "lambda_invoke_permissions": [],
    "sqs_queues": [],
    "eventbridge_rules": [],
    "dynamodb_tables": [
        {
            "logical_name": "TodoItems",
            "purpose": "fixture",
            "partition_key": {"name": "userId", "type": "S"},
            "sort_key": {"name": "id", "type": "S"},
            "gsi": [],
        }
    ],
    "s3_buckets": [],
    "cognito": {
        "enabled": True,
        "user_pool_attributes": ["email"],
        "password_policy": {
            "minimum_length": 8,
            "require_lowercase": True,
            "require_uppercase": True,
            "require_numbers": True,
            "require_symbols": False,
            "temporary_password_validity_days": 7,
        },
    },
    "api_gateway": {
        "type": "REST",
        "api_type": "rest",
        "clients": "internet",
        "api_type_reason": "cognito_authorizer",
        "cors": {"allow_origins": ["*"], "allow_methods": ["GET", "POST"]},
        "authorizer": "Cognito",
        "authorizer_type": "COGNITO_USER_POOLS",
        "user_pool_arn": "${CognitoUserPool.Arn}",
        "token_validation": "Automatic at API Gateway layer",
        "identity_source": "Authorization header (Bearer token)",
    },
    "dropped_functions": [
        {"item": "POST /login", "type": "endpoint", "reason": "Cognito"},
    ],
}

GOOD_TEMPLATE_YAML = """\
AWSTemplateFormatVersion: '2010-09-09'
Transform: AWS::Serverless-2016-10-31
Globals:
  Function:
    Runtime: nodejs22.x
    Environment:
      Variables:
        ENVIRONMENT: !Ref Environment
Parameters:
  Environment:
    Type: String
    Default: dev
Resources:
  TodoListItemsFunction:
    Type: AWS::Serverless::Function
    Properties:
      CodeUri: lambdas/todo/todo-list-items/
      Handler: handler.handler
      Policies:
        - DynamoDBCrudPolicy:
            TableName: !Ref TodoItems
      Environment:
        Variables:
          TODO_TABLE: !Ref TodoItems
      Events:
        Api:
          Type: Api
          Properties:
            RestApiId: !Ref MyApi
            Path: /item
            Method: GET
  TodoCreateItemFunction:
    Type: AWS::Serverless::Function
    Properties:
      CodeUri: lambdas/todo/todo-create-item/
      Handler: handler.handler
      Policies:
        - DynamoDBCrudPolicy:
            TableName: !Ref TodoItems
      Environment:
        Variables:
          TODO_TABLE: !Ref TodoItems
      Events:
        Api:
          Type: Api
          Properties:
            RestApiId: !Ref MyApi
            Path: /item
            Method: POST
  TodoItems:
    Type: AWS::DynamoDB::Table
    Properties:
      AttributeDefinitions:
        - AttributeName: userId
          AttributeType: S
      KeySchema:
        - AttributeName: userId
          KeyType: HASH
  UserPool:
    Type: AWS::Cognito::UserPool
    Properties:
      Policies:
        PasswordPolicy:
          MinimumLength: 8
          RequireLowercase: true
          RequireUppercase: true
          RequireNumbers: true
          RequireSymbols: false
          TemporaryPasswordValidityDays: 7
  UserPoolClient:
    Type: AWS::Cognito::UserPoolClient
    Properties:
      UserPoolId: !Ref UserPool
"""

GOOD_HANDLER_JS = """\
const { DynamoDBClient } = require('@aws-sdk/client-dynamodb');
const { DynamoDBDocumentClient, PutCommand } = require('@aws-sdk/lib-dynamodb');

exports.handler = async function (event, context) {
  const table = process.env.TODO_TABLE;
  return { statusCode: 200, body: JSON.stringify({ table }) };
};
"""

GOOD_PACKAGE_JSON = {
    "name": "fixture",
    "dependencies": {
        "@aws-sdk/client-dynamodb": "^3.617.0",
        "@aws-sdk/lib-dynamodb": "^3.617.0",
    },
}


def _write_good_workspace(root: Path) -> Path:
    storage = root / "storage"
    output = root / "output"
    storage.mkdir(parents=True)
    (storage / "blueprint.json").write_text(json.dumps(GOOD_BLUEPRINT), encoding="utf-8")
    (storage / "DECISIONS.md").write_text(
        "## D-1 one Lambda per endpoint\n- todo-list-items, todo-create-item: auth required (spec)\n", encoding="utf-8")
    (storage / "analysis_report.json").write_text(
        json.dumps({"entry_points": [{"method": "GET", "path": "/item"}, {"method": "POST", "path": "/item"}]}),
        encoding="utf-8",
    )
    (output / "template.yaml").parent.mkdir(parents=True, exist_ok=True)
    (output / "template.yaml").write_text(GOOD_TEMPLATE_YAML, encoding="utf-8")
    for name in ("todo-list-items", "todo-create-item"):
        lambda_dir = output / "lambdas" / "todo" / name
        lambda_dir.mkdir(parents=True)
        (lambda_dir / "handler.js").write_text(GOOD_HANDLER_JS, encoding="utf-8")
        (lambda_dir / "package.json").write_text(json.dumps(GOOD_PACKAGE_JSON), encoding="utf-8")
    return root


# ---------------------------------------------------------------------------
# B0
# ---------------------------------------------------------------------------


def test_b0_good_blueprint_passes(tmp_path):
    _write_good_workspace(tmp_path)
    findings = gates.check_b0(tmp_path)
    assert findings == []


def test_normalize_path_folds_flask_angle_brackets():
    """P0 keeps the framework's own route spelling. Express (":id" / "{id}")
    was already folded; Flask "<id>" / "<int:id>" must fold to the same
    canonical form, because the blueprint can only carry the deployable
    API Gateway shape "/{id}"."""
    assert gates._normalize_path("/item/<id>") == "/item/{param}"
    assert gates._normalize_path("/item/<int:id>") == "/item/{param}"
    assert gates._normalize_path("/item/{id}") == gates._normalize_path("/item/:id")


def test_b0_flask_spec_path_covered_by_curly_blueprint(tmp_path):
    """A Flask monolith: analysis_report says GET /item/<id>, the blueprint
    must say GET /item/{id}. That is correct, not uncovered."""
    _write_good_workspace(tmp_path)
    bp_path = tmp_path / "storage" / "blueprint.json"
    bp = json.loads(bp_path.read_text(encoding="utf-8"))
    bp["lambda_functions"][0]["entry_points"] = ["GET /item/{id}"]
    bp_path.write_text(json.dumps(bp), encoding="utf-8")
    (tmp_path / "storage" / "analysis_report.json").write_text(json.dumps({"entry_points": [
        {"method": "GET", "path": "/item/<id>", "auth": {"classification": "required"}},
        {"method": "POST", "path": "/item"},
    ]}), encoding="utf-8")
    assert gates.check_b0(tmp_path) == []


def test_b0_requires_decisions_md_mentioning_every_function(tmp_path):
    _write_good_workspace(tmp_path)
    (tmp_path / "storage" / "DECISIONS.md").write_text("## D-1\n- todo-list-items only\n", encoding="utf-8")
    msgs = [f["description"] for f in gates.check_b0(tmp_path) if f["severity"] == "ERROR"]
    assert any("todo-create-item" in m and "DECISIONS.md" in m for m in msgs)
    (tmp_path / "storage" / "DECISIONS.md").unlink()
    msgs = [f["description"] for f in gates.check_b0(tmp_path) if f["severity"] == "ERROR"]
    assert any("missing or empty" in m for m in msgs)


def test_b0_auth_must_not_drop_spec_required(tmp_path):
    """Spec says the endpoint needs auth, blueprint makes it public: security regression."""
    _write_good_workspace(tmp_path)
    (tmp_path / "storage" / "analysis_report.json").write_text(json.dumps({"entry_points": [
        {"method": "GET", "path": "/item", "auth": {"classification": "required"}},
        {"method": "POST", "path": "/item", "auth": {"classification": "required"}}]}), encoding="utf-8")
    bp = json.loads(json.dumps(GOOD_BLUEPRINT))
    bp["lambda_functions"][0]["auth"] = "none"
    (tmp_path / "storage" / "blueprint.json").write_text(json.dumps(bp), encoding="utf-8")
    errs = [f for f in gates.check_b0(tmp_path) if f["severity"] == "ERROR"]
    assert len(errs) == 1 and "requires authentication" in errs[0]["description"]


def test_b0_warns_on_consumer_without_idempotency(tmp_path):
    _write_good_workspace(tmp_path)
    bp = json.loads(json.dumps(GOOD_BLUEPRINT))
    bp["lambda_functions"].append({"name": "todo-consumer", "trigger_type": "sqs", "runtime": "nodejs22.x", "handler": "handler.handler"})
    (tmp_path / "storage" / "blueprint.json").write_text(json.dumps(bp), encoding="utf-8")
    (tmp_path / "storage" / "DECISIONS.md").write_text("todo-list-items todo-create-item todo-consumer\n", encoding="utf-8")
    warns = [f for f in gates.check_b0(tmp_path) if f["severity"] == "WARNING"]
    assert any("idempotency" in f["description"] for f in warns)
    (tmp_path / "storage" / "DECISIONS.md").write_text("todo-list-items todo-create-item todo-consumer: 按 orderId 幂等\n", encoding="utf-8")
    assert not [f for f in gates.check_b0(tmp_path) if "idempotency" in f["description"]]


def test_b0_residue_fixture_reports_findings():
    """The real 'blueprint2/3/4.json left behind, blueprint.json only has
    {cognito, metadata}' crash scenario from the migration plan's incident log."""
    workspace = FIXTURES / "bad_blueprint_residue"
    findings = gates.check_b0(workspace)
    check_ids_and_resources = {(f["check_id"], Path(f["affected_resource"]).name) for f in findings}
    assert ("B0", "blueprint2.json") in check_ids_and_resources
    assert ("B0", "blueprint3.json") in check_ids_and_resources
    # blueprint.json itself is missing lambda_functions/dynamodb_tables/etc.
    assert any(f["check_id"] == "B0" and "lambda_functions" in f["description"] for f in findings)


def test_b0_missing_blueprint():
    findings = gates.check_b0(FIXTURES / "does-not-exist")
    assert len(findings) == 1
    assert findings[0]["severity"] == "ERROR"


def test_b0_invalid_auth_value(tmp_path):
    blueprint = json.loads(json.dumps(GOOD_BLUEPRINT))
    blueprint["lambda_functions"][0]["auth"] = "sometimes"
    _write_good_workspace(tmp_path)
    (tmp_path / "storage" / "blueprint.json").write_text(json.dumps(blueprint), encoding="utf-8")
    findings = gates.check_b0(tmp_path)
    assert any("auth" in f["description"] for f in findings)


def test_b0_double_coverage_two_functions_same_endpoint(tmp_path):
    """Real incident: architect resubmitted its whole lambda_functions list
    via merge_blueprint_key after renaming one function, so 'GET /item' ended
    up claimed by two different lambda names."""
    blueprint = json.loads(json.dumps(GOOD_BLUEPRINT))
    duplicate_fn = json.loads(json.dumps(blueprint["lambda_functions"][0]))
    duplicate_fn["name"] = "todo-get-items"  # same entry_points, different name
    blueprint["lambda_functions"].append(duplicate_fn)
    _write_good_workspace(tmp_path)
    (tmp_path / "storage" / "blueprint.json").write_text(json.dumps(blueprint), encoding="utf-8")
    findings = gates.check_b0(tmp_path)
    assert any("claimed by more than one place" in f["description"] for f in findings)


def test_b0_double_coverage_function_and_dropped(tmp_path):
    blueprint = json.loads(json.dumps(GOOD_BLUEPRINT))
    blueprint["dropped_functions"].append(
        {"item": blueprint["lambda_functions"][0]["entry_points"][0], "type": "endpoint", "reason": "duplicate"}
    )
    _write_good_workspace(tmp_path)
    (tmp_path / "storage" / "blueprint.json").write_text(json.dumps(blueprint), encoding="utf-8")
    findings = gates.check_b0(tmp_path)
    assert any("claimed by more than one place" in f["description"] for f in findings)


# ---------------------------------------------------------------------------
# C1
# ---------------------------------------------------------------------------


def test_c1_good_workspace_passes(tmp_path):
    _write_good_workspace(tmp_path)
    assert gates.check_c1_code(tmp_path) == []
    template = gates.load_template(tmp_path / "output" / "template.yaml")
    assert gates.check_c1_template(tmp_path, template) == []


def test_c1_missing_handler_dir(tmp_path):
    _write_good_workspace(tmp_path)
    import shutil

    shutil.rmtree(tmp_path / "output" / "lambdas" / "todo" / "todo-create-item")
    findings = gates.check_c1_code(tmp_path)
    assert any(f["affected_resource"] == "todo-create-item" for f in findings)


def test_c1_orphan_directory(tmp_path):
    _write_good_workspace(tmp_path)
    orphan = tmp_path / "output" / "lambdas" / "todo" / "todo-orphan"
    orphan.mkdir()
    findings = gates.check_c1_code(tmp_path)
    assert any(f["affected_resource"] == "todo-orphan" for f in findings)


def test_c1_template_bad_codeuri(tmp_path):
    _write_good_workspace(tmp_path)
    template = gates.load_template(tmp_path / "output" / "template.yaml")
    template["Resources"]["TodoListItemsFunction"]["Properties"]["CodeUri"] = "lambdas/todo/does-not-exist/"
    findings = gates.check_c1_template(tmp_path, template)
    assert any(f["affected_resource"] == "TodoListItemsFunction" for f in findings)


# ---------------------------------------------------------------------------
# C2
# ---------------------------------------------------------------------------


def test_c2_good_workspace_passes(tmp_path):
    _write_good_workspace(tmp_path)
    template = gates.load_template(tmp_path / "output" / "template.yaml")
    assert gates.check_c2(tmp_path, template) == []


def test_c2_missing_trailing_slash(tmp_path):
    _write_good_workspace(tmp_path)
    template = gates.load_template(tmp_path / "output" / "template.yaml")
    template["Resources"]["TodoListItemsFunction"]["Properties"]["CodeUri"] = "lambdas/todo/todo-list-items"
    findings = gates.check_c2(tmp_path, template)
    assert any("must end with" in f["description"] for f in findings)


# ---------------------------------------------------------------------------
# C3
# ---------------------------------------------------------------------------


def test_c3_good_workspace_passes(tmp_path):
    _write_good_workspace(tmp_path)
    template = gates.load_template(tmp_path / "output" / "template.yaml")
    assert gates.check_c3(tmp_path, template) == []


def test_c3_wrong_export_name(tmp_path):
    _write_good_workspace(tmp_path)
    handler_path = tmp_path / "output" / "lambdas" / "todo" / "todo-list-items" / "handler.js"
    handler_path.write_text(GOOD_HANDLER_JS.replace("exports.handler", "exports.notHandler"), encoding="utf-8")
    template = gates.load_template(tmp_path / "output" / "template.yaml")
    findings = gates.check_c3(tmp_path, template)
    assert any(f["affected_resource"] == "TodoListItemsFunction" for f in findings)


def test_c3_stale_globals_handler_overridden_everywhere_is_flagged(tmp_path):
    """Real run7 finding: Globals.Function.Handler was the blueprint's
    placeholder 'index.handler' (no such file on disk); every function
    overrode it, so nothing broke, but any function added later would
    inherit a handler that resolves nowhere."""
    _write_good_workspace(tmp_path)
    template = gates.load_template(tmp_path / "output" / "template.yaml")
    template.setdefault("Globals", {}).setdefault("Function", {})["Handler"] = "index.handler"
    findings = gates.check_c3(tmp_path, template)
    assert any(f["severity"] == "ERROR" and f["affected_resource"] == "Globals.Function.Handler" for f in findings)


def test_c3_valid_globals_handler_is_not_flagged(tmp_path):
    _write_good_workspace(tmp_path)
    template = gates.load_template(tmp_path / "output" / "template.yaml")
    template.setdefault("Globals", {}).setdefault("Function", {})["Handler"] = "handler.handler"
    assert gates.check_c3(tmp_path, template) == []


def test_c3_function_inheriting_globals_handler_is_resolved_against_disk(tmp_path):
    _write_good_workspace(tmp_path)
    template = gates.load_template(tmp_path / "output" / "template.yaml")
    template.setdefault("Globals", {}).setdefault("Function", {})["Handler"] = "handler.handler"
    del template["Resources"]["TodoListItemsFunction"]["Properties"]["Handler"]
    assert gates.check_c3(tmp_path, template) == []

    template["Globals"]["Function"]["Handler"] = "index.handler"
    findings = gates.check_c3(tmp_path, template)
    assert any(f["affected_resource"] == "TodoListItemsFunction" for f in findings)
    assert not any(f["affected_resource"] == "Globals.Function.Handler" for f in findings)


# ---------------------------------------------------------------------------
# C7
# ---------------------------------------------------------------------------


def test_c7_good_workspace_passes(tmp_path):
    _write_good_workspace(tmp_path)
    template = gates.load_template(tmp_path / "output" / "template.yaml")
    assert gates.check_c7(tmp_path, template) == []


def test_c7_path_mismatch(tmp_path):
    _write_good_workspace(tmp_path)
    template = gates.load_template(tmp_path / "output" / "template.yaml")
    template["Resources"]["TodoListItemsFunction"]["Properties"]["Events"]["Api"]["Properties"]["Path"] = "/wrong"
    findings = gates.check_c7(tmp_path, template)
    assert any("GET /item" in f["description"] for f in findings)
    assert any("/wrong" in f["description"] for f in findings)


def test_c7_param_style_normalization_does_not_false_positive(tmp_path):
    _write_good_workspace(tmp_path)
    blueprint = json.loads((tmp_path / "storage" / "blueprint.json").read_text(encoding="utf-8"))
    blueprint["lambda_functions"][0]["entry_points"] = ["GET /item/:id"]
    (tmp_path / "storage" / "blueprint.json").write_text(json.dumps(blueprint), encoding="utf-8")
    template = gates.load_template(tmp_path / "output" / "template.yaml")
    template["Resources"]["TodoListItemsFunction"]["Properties"]["Events"]["Api"]["Properties"]["Path"] = "/item/{id}"
    findings = gates.check_c7(tmp_path, template)
    assert findings == []


# ---------------------------------------------------------------------------
# C7b
# ---------------------------------------------------------------------------


def _write_blueprint_only(tmp_path, blueprint: dict) -> None:
    storage = tmp_path / "storage"
    storage.mkdir(parents=True, exist_ok=True)
    (storage / "blueprint.json").write_text(json.dumps(blueprint), encoding="utf-8")


def _public_endpoint_blueprint(auth: str) -> dict:
    bp = json.loads(json.dumps(GOOD_BLUEPRINT))
    bp["lambda_functions"] = [
        {
            "name": "todo-public",
            "trigger_type": "api",
            "runtime": "nodejs22.x",
            "handler": "handler.lambda_handler",
            "auth": auth,
            "source_files": [],
            "entry_points": ["GET /public"],
            "environment_variables": {},
        }
    ]
    return bp


def _api_event_template(auth_none: bool) -> dict:
    properties: dict = {"RestApiId": "MyApi", "Path": "/public", "Method": "GET"}
    if auth_none:
        properties["Auth"] = {"Authorizer": "NONE"}
    return {
        "Resources": {
            "PublicFunction": {
                "Type": "AWS::Serverless::Function",
                "Properties": {
                    "CodeUri": "lambdas/todo/todo-public/",
                    "Handler": "handler.handler",
                    "Events": {"Api": {"Type": "Api", "Properties": properties}},
                },
            }
        }
    }


def test_c7b_public_endpoint_with_override_passes(tmp_path):
    _write_blueprint_only(tmp_path, _public_endpoint_blueprint("none"))
    findings = gates.check_c7b(tmp_path, _api_event_template(auth_none=True))
    assert findings == []


def test_c7b_public_endpoint_missing_override_fails(tmp_path):
    _write_blueprint_only(tmp_path, _public_endpoint_blueprint("none"))
    findings = gates.check_c7b(tmp_path, _api_event_template(auth_none=False))
    assert len(findings) == 1
    assert findings[0]["check_id"] == "C7b"
    assert "missing" in findings[0]["description"]


def test_c7b_required_endpoint_with_bad_override_fails(tmp_path):
    _write_blueprint_only(tmp_path, _public_endpoint_blueprint("required"))
    findings = gates.check_c7b(tmp_path, _api_event_template(auth_none=True))
    assert len(findings) == 1
    assert findings[0]["check_id"] == "C7b"
    assert "bypass" in findings[0]["description"]


def test_c7b_required_endpoint_without_override_passes(tmp_path):
    _write_blueprint_only(tmp_path, _public_endpoint_blueprint("required"))
    findings = gates.check_c7b(tmp_path, _api_event_template(auth_none=False))
    assert findings == []


# ---------------------------------------------------------------------------
# C8 - SQS resource consistency
# ---------------------------------------------------------------------------


def _sqs_blueprint(*, with_queue: bool = True) -> dict:
    bp = json.loads(json.dumps(GOOD_BLUEPRINT))
    bp["sqs_queues"] = [{"logical_name": "WorkQueue", "purpose": "fixture"}] if with_queue else []
    bp["lambda_functions"] = [
        {
            "name": "todo-producer",
            "trigger_type": "api",
            "runtime": "nodejs22.x",
            "handler": "handler.lambda_handler",
            "auth": "required",
            "source_files": [],
            "entry_points": ["POST /produce"],
            "environment_variables": {},
            "publishes_to": ["sqs"],
        },
        {
            "name": "todo-consumer",
            "trigger_type": "sqs",
            "runtime": "nodejs22.x",
            "handler": "handler.lambda_handler",
            "auth": "required",
            "source_files": [],
            "entry_points": [],
            "environment_variables": {},
        },
    ]
    return bp


def _sqs_template(*, with_queue_resource=True, with_policy=True, with_queue_url_env=True, with_sqs_event=True) -> dict:
    resources: dict = {}
    if with_queue_resource:
        resources["WorkQueue"] = {"Type": "AWS::SQS::Queue", "Properties": {}}
    producer_props: dict = {"CodeUri": "lambdas/todo/todo-producer/", "Handler": "handler.handler"}
    if with_policy:
        producer_props["Policies"] = [{"SQSSendMessagePolicy": {"QueueName": "WorkQueue"}}]
    if with_queue_url_env:
        producer_props["Environment"] = {"Variables": {"QUEUE_URL": "url"}}
    resources["ProducerFunction"] = {"Type": "AWS::Serverless::Function", "Properties": producer_props}

    consumer_props: dict = {"CodeUri": "lambdas/todo/todo-consumer/", "Handler": "handler.handler"}
    if with_sqs_event:
        consumer_props["Events"] = {"QueueTrigger": {"Type": "SQS", "Properties": {"Queue": "WorkQueue"}}}
    resources["ConsumerFunction"] = {"Type": "AWS::Serverless::Function", "Properties": consumer_props}
    return {"Resources": resources}


def test_c8_good_workspace_passes(tmp_path):
    _write_blueprint_only(tmp_path, _sqs_blueprint())
    findings = gates.check_c8(tmp_path, _sqs_template())
    assert findings == []


def test_c8_missing_queue_resource(tmp_path):
    _write_blueprint_only(tmp_path, _sqs_blueprint())
    findings = gates.check_c8(tmp_path, _sqs_template(with_queue_resource=False))
    assert any("WorkQueue" in f["affected_resource"] for f in findings)


def test_c8_missing_send_policy(tmp_path):
    _write_blueprint_only(tmp_path, _sqs_blueprint())
    findings = gates.check_c8(tmp_path, _sqs_template(with_policy=False))
    assert any("SQSSendMessagePolicy" in f["description"] for f in findings)


def test_c8_missing_queue_url_env(tmp_path):
    _write_blueprint_only(tmp_path, _sqs_blueprint())
    findings = gates.check_c8(tmp_path, _sqs_template(with_queue_url_env=False))
    assert any("QUEUE_URL" in f["description"] for f in findings)


def test_c8_missing_sqs_event_source(tmp_path):
    _write_blueprint_only(tmp_path, _sqs_blueprint())
    findings = gates.check_c8(tmp_path, _sqs_template(with_sqs_event=False))
    assert any("SQS Event source mapping" in f["description"] for f in findings)


def test_c8_no_async_flows_passes(tmp_path):
    _write_good_workspace(tmp_path)
    template = gates.load_template(tmp_path / "output" / "template.yaml")
    assert gates.check_c8(tmp_path, template) == []


# ---------------------------------------------------------------------------
# C9 - EventBridge resource consistency
# ---------------------------------------------------------------------------


def _eventbridge_blueprint() -> dict:
    bp = json.loads(json.dumps(GOOD_BLUEPRINT))
    bp["eventbridge_rules"] = [{"logical_name": "OrderRule", "purpose": "fixture"}]
    bp["lambda_functions"] = [
        {
            "name": "todo-emitter",
            "trigger_type": "api",
            "runtime": "nodejs22.x",
            "handler": "handler.lambda_handler",
            "auth": "required",
            "source_files": [],
            "entry_points": ["POST /emit"],
            "environment_variables": {},
            "publishes_to": ["eventbridge"],
        },
        {
            "name": "todo-target",
            "trigger_type": "eventbridge",
            "runtime": "nodejs22.x",
            "handler": "handler.lambda_handler",
            "auth": "required",
            "source_files": [],
            "entry_points": [],
            "environment_variables": {},
        },
    ]
    return bp


def _eventbridge_template(*, with_rule=True, with_targets=True, with_permission=True, with_put_events_policy=True) -> dict:
    resources: dict = {}
    if with_rule:
        resources["OrderRule"] = {
            "Type": "AWS::Events::Rule",
            "Properties": {"Targets": [{"Arn": {"Fn::GetAtt": ["TargetFunction", "Arn"]}}] if with_targets else []},
        }
    if with_permission:
        resources["EventBridgeInvokePermission"] = {
            "Type": "AWS::Lambda::Permission",
            "Properties": {"Principal": "events.amazonaws.com", "FunctionName": {"Ref": "TargetFunction"}},
        }
    emitter_props: dict = {"CodeUri": "lambdas/todo/todo-emitter/", "Handler": "handler.handler"}
    if with_put_events_policy:
        emitter_props["Policies"] = ["EventBridgePutEventsPolicy"]
    resources["EmitterFunction"] = {"Type": "AWS::Serverless::Function", "Properties": emitter_props}
    resources["TargetFunction"] = {
        "Type": "AWS::Serverless::Function",
        "Properties": {"CodeUri": "lambdas/todo/todo-target/", "Handler": "handler.handler"},
    }
    return {"Resources": resources}


def test_c9_good_workspace_passes(tmp_path):
    _write_blueprint_only(tmp_path, _eventbridge_blueprint())
    findings = gates.check_c9(tmp_path, _eventbridge_template())
    assert findings == []


def test_c9_missing_rule_resource(tmp_path):
    _write_blueprint_only(tmp_path, _eventbridge_blueprint())
    findings = gates.check_c9(tmp_path, _eventbridge_template(with_rule=False))
    assert any("OrderRule" in f["affected_resource"] for f in findings)


def test_c9_rule_without_targets(tmp_path):
    _write_blueprint_only(tmp_path, _eventbridge_blueprint())
    findings = gates.check_c9(tmp_path, _eventbridge_template(with_targets=False))
    assert any("no Targets" in f["description"] for f in findings)


def test_c9_missing_invoke_permission(tmp_path):
    _write_blueprint_only(tmp_path, _eventbridge_blueprint())
    findings = gates.check_c9(tmp_path, _eventbridge_template(with_permission=False))
    assert any("Lambda::Permission" in f["description"] for f in findings)


def test_c9_missing_put_events_policy(tmp_path):
    _write_blueprint_only(tmp_path, _eventbridge_blueprint())
    findings = gates.check_c9(tmp_path, _eventbridge_template(with_put_events_policy=False))
    assert any("EventBridgePutEventsPolicy" in f["description"] for f in findings)


def test_c9_no_async_flows_passes(tmp_path):
    _write_good_workspace(tmp_path)
    template = gates.load_template(tmp_path / "output" / "template.yaml")
    assert gates.check_c9(tmp_path, template) == []


# ---------------------------------------------------------------------------
# C10
# ---------------------------------------------------------------------------


def test_c10_good_workspace_passes(tmp_path):
    _write_good_workspace(tmp_path)
    assert gates.check_c10(tmp_path) == []


def test_c10_requirements_txt_only_builtins(tmp_path):
    _write_good_workspace(tmp_path)
    lambda_dir = tmp_path / "output" / "lambdas" / "todo" / "todo-list-items"
    (lambda_dir / "requirements.txt").write_text("boto3==1.34.0\nbotocore==1.34.0\n", encoding="utf-8")
    findings = gates.check_c10(tmp_path)
    assert any("boto3" in f["description"] for f in findings)


# ---------------------------------------------------------------------------
# C11
# ---------------------------------------------------------------------------


def test_c11_good_workspace_passes(tmp_path):
    _write_good_workspace(tmp_path)
    template = gates.load_template(tmp_path / "output" / "template.yaml")
    assert gates.check_c11(tmp_path, template) == []


def test_c11_esm_syntax_detected(tmp_path):
    _write_good_workspace(tmp_path)
    handler_path = tmp_path / "output" / "lambdas" / "todo" / "todo-list-items" / "handler.js"
    handler_path.write_text(
        "import { DynamoDBClient } from '@aws-sdk/client-dynamodb';\nexport default async function () {}\n",
        encoding="utf-8",
    )
    template = gates.load_template(tmp_path / "output" / "template.yaml")
    findings = gates.check_c11(tmp_path, template)
    assert any(f["check_id"] == "C11" for f in findings)


def test_c11_type_module_in_package_json(tmp_path):
    _write_good_workspace(tmp_path)
    pkg_path = tmp_path / "output" / "lambdas" / "todo" / "todo-list-items" / "package.json"
    pkg = json.loads(pkg_path.read_text(encoding="utf-8"))
    pkg["type"] = "module"
    pkg_path.write_text(json.dumps(pkg), encoding="utf-8")
    template = gates.load_template(tmp_path / "output" / "template.yaml")
    findings = gates.check_c11(tmp_path, template)
    assert any("type" in f["description"] for f in findings)


# ---------------------------------------------------------------------------
# C12
# ---------------------------------------------------------------------------


def test_c12_good_workspace_passes(tmp_path):
    _write_good_workspace(tmp_path)
    template = gates.load_template(tmp_path / "output" / "template.yaml")
    assert gates.check_c12(tmp_path, template) == []


def test_c12_aws_sdk_v2_is_error(tmp_path):
    _write_good_workspace(tmp_path)
    handler_path = tmp_path / "output" / "lambdas" / "todo" / "todo-list-items" / "handler.js"
    handler_path.write_text("const AWS = require('aws-sdk');\nexports.handler = async () => {};\n", encoding="utf-8")
    template = gates.load_template(tmp_path / "output" / "template.yaml")
    findings = gates.check_c12(tmp_path, template)
    assert any("aws-sdk" in f["description"] for f in findings)


def test_c12_undeclared_third_party_package(tmp_path):
    _write_good_workspace(tmp_path)
    handler_path = tmp_path / "output" / "lambdas" / "todo" / "todo-list-items" / "handler.js"
    handler_path.write_text(
        GOOD_HANDLER_JS + "\nconst { v1 } = require('uuid');\n",
        encoding="utf-8",
    )
    template = gates.load_template(tmp_path / "output" / "template.yaml")
    findings = gates.check_c12(tmp_path, template)
    assert any("uuid" in f["description"] for f in findings)


def test_c12_ignores_non_node_runtime(tmp_path):
    _write_good_workspace(tmp_path)
    template_text = GOOD_TEMPLATE_YAML.replace("Runtime: nodejs22.x", "Runtime: python3.12")
    (tmp_path / "output" / "template.yaml").write_text(template_text, encoding="utf-8")
    handler_path = tmp_path / "output" / "lambdas" / "todo" / "todo-list-items" / "handler.js"
    handler_path.write_text("const AWS = require('aws-sdk');\n", encoding="utf-8")
    template = gates.load_template(tmp_path / "output" / "template.yaml")
    assert gates.check_c12(tmp_path, template) == []


# ---------------------------------------------------------------------------
# run_gate phase dispatch
# ---------------------------------------------------------------------------


def test_run_gate_blueprint_pass(tmp_path):
    _write_good_workspace(tmp_path)
    result = gates.run_gate("blueprint", str(tmp_path))
    assert result["status"] == "PASS"
    assert result["mechanical_failures"] == []


def test_run_gate_blueprint_severe_on_residue():
    result = gates.run_gate("blueprint", str(FIXTURES / "bad_blueprint_residue"))
    assert result["status"] == "FAIL"
    assert result["mechanical_failures"]


def test_run_gate_template_pass_and_suspects_present(tmp_path):
    _write_good_workspace(tmp_path)
    result = gates.run_gate("template", str(tmp_path))
    assert result["status"] == "PASS"
    assert set(result["suspects"].keys()) == {"C4", "C5", "C6"}


def test_run_gate_template_severe_when_many_mechanical_failures(tmp_path):
    _write_good_workspace(tmp_path)
    import shutil

    # Blow away both lambda dirs -> C1 double failure + C3 failures etc.
    shutil.rmtree(tmp_path / "output" / "lambdas")
    result = gates.run_gate("template", str(tmp_path))
    assert result["status"] == "FAIL"
    assert len(result["mechanical_failures"]) > 2


def test_run_gate_unknown_phase(tmp_path):
    _write_good_workspace(tmp_path)
    result = gates.run_gate("nonsense", str(tmp_path))
    assert result["status"] == "error"
    assert "error" in result


def test_run_gate_final_reports_missing_consistency_report(tmp_path):
    _write_good_workspace(tmp_path)
    result = gates.run_gate("final", str(tmp_path))
    assert result["status"] == "FAIL"
    assert any(f["check_id"] == "REPORT" for f in result["mechanical_failures"])


def test_run_gate_final_passes_with_report(tmp_path):
    _write_good_workspace(tmp_path)
    (tmp_path / "output" / "consistency_report.json").write_text(
        json.dumps({"status": "PASS", "total_checks": 12, "findings": [], "fixes_applied": 0, "sam_validation": "PASS"}),
        encoding="utf-8",
    )
    result = gates.run_gate("final", str(tmp_path))
    assert result["status"] == "PASS"


@pytest.mark.parametrize("status", ["IN_PROGRESS", "FAIL"])
def test_run_gate_final_rejects_unfinished_or_failed_report(tmp_path, status):
    """Real incident (bookstore-v1): S4 wrote a provisional report with status
    IN_PROGRESS and gate(final) still returned PASS."""
    _write_good_workspace(tmp_path)
    (tmp_path / "output" / "consistency_report.json").write_text(json.dumps({"status": status}), encoding="utf-8")
    result = gates.run_gate("final", str(tmp_path))
    assert result["status"] == "FAIL"
    assert any(f["check_id"] == "REPORT" and status in f["description"] for f in result["mechanical_failures"])


# ---------------------------------------------------------------------------
# C14 - Cognito settings of blueprint.cognito are in the template
# ---------------------------------------------------------------------------


def _c14(tmp_path, cognito_patch=None, pool_props=None, client_props=None):
    _write_good_workspace(tmp_path)
    blueprint = json.loads(json.dumps(GOOD_BLUEPRINT))
    blueprint["cognito"].update(cognito_patch or {})
    (tmp_path / "storage" / "blueprint.json").write_text(json.dumps(blueprint), encoding="utf-8")
    template = gates.load_template(tmp_path / "output" / "template.yaml")
    if pool_props is not None:
        template["Resources"]["UserPool"]["Properties"] = pool_props
    if client_props is not None:
        template["Resources"]["UserPoolClient"]["Properties"] = {"UserPoolId": "x", **client_props}
    return gates.check_c14(tmp_path, template)


def test_c14_good_workspace_passes(tmp_path):
    assert _c14(tmp_path) == []


def test_c14_refresh_token_left_at_cognito_default(tmp_path):
    """bookstore-v3a: blueprint 1h / 1h / 7d, template without token settings."""
    tokens = {"id_token_validity": "1h", "access_token_validity": "1h", "refresh_token_validity": "7d"}
    found = _c14(tmp_path, {"token_validity": tokens})
    assert [f["check_id"] for f in found] == ["C14"]  # 1h matches Cognito's default; 7d does not (30 days)
    assert found[0]["severity"] == "ERROR"
    assert "RefreshTokenValidity: 7" in found[0]["description"] and "RefreshToken: days" in found[0]["description"]
    (tmp_path / "storage" / "blueprint.json").write_text(json.dumps(
        {**GOOD_BLUEPRINT, "cognito": {**GOOD_BLUEPRINT["cognito"], "token_validity": tokens}}), encoding="utf-8")
    assert gates.run_gate("template", str(tmp_path))["status"] == "FAIL"  # one C14 error is enough


@pytest.mark.parametrize("client", [
    {"RefreshTokenValidity": 7, "TokenValidityUnits": {"RefreshToken": "days"}},
    {"RefreshTokenValidity": 7},  # CloudFormation's default unit for refresh tokens is days
    {"RefreshTokenValidity": 168, "TokenValidityUnits": {"RefreshToken": "hours"}},
])
def test_c14_refresh_token_set(tmp_path, client):
    assert _c14(tmp_path, {"token_validity": {"refresh_token_validity": "7d"}}, client_props=client) == []


def test_c14_access_token_unit_matters(tmp_path):
    found = _c14(tmp_path, {"token_validity": {"access_token_validity": "1h"}},
                 client_props={"AccessTokenValidity": 1, "TokenValidityUnits": {"AccessToken": "days"}})
    assert len(found) == 1 and "AccessTokenValidity: 1 with TokenValidityUnits.AccessToken: hours" in found[0]["description"]


def test_c14_unreadable_duration_and_intrinsic_value_only_warn(tmp_path):
    found = _c14(tmp_path / "a", {"token_validity": {"refresh_token_validity": "a week"}})
    assert [f["severity"] for f in found] == ["WARNING"]
    found = _c14(tmp_path / "b", {"token_validity": {"refresh_token_validity": "7d"}},
                 client_props={"RefreshTokenValidity": {"Ref": "RefreshDays"}})
    assert [f["severity"] for f in found] == ["WARNING"]


def test_c14_password_policy_mismatch_and_omission(tmp_path):
    policy = {"MinimumLength": 8, "RequireLowercase": True, "RequireUppercase": True, "RequireNumbers": True,
              "RequireSymbols": True}
    found = _c14(tmp_path, pool_props={"Policies": {"PasswordPolicy": policy}})
    descriptions = " | ".join(f["description"] for f in found)
    assert len(found) == 2, descriptions  # RequireSymbols differs; TemporaryPasswordValidityDays is missing
    assert "require_symbols" in descriptions and "no TemporaryPasswordValidityDays" in descriptions


def test_c14_no_password_policy_means_cognito_defaults(tmp_path):
    found = _c14(tmp_path / "a", pool_props={})
    assert len(found) == 1 and "RequireSymbols = True" in found[0]["description"]  # default policy requires symbols
    assert _c14(tmp_path / "b", {"password_policy": {"min_length": 8}}, pool_props={}) == []


def test_c14_zero_temporary_password_days_may_be_left_out(tmp_path):
    """CloudFormation only accepts 1-365 days; 0 means the app has no temporary passwords."""
    policy = {"min_length": 6, "require_uppercase": False, "require_lowercase": False, "require_numbers": False,
              "require_symbols": False, "temporary_password_validity_days": 0}
    pool = {"Policies": {"PasswordPolicy": {"MinimumLength": 6, "RequireUppercase": False, "RequireLowercase": False,
                                            "RequireNumbers": False, "RequireSymbols": False}}}
    assert _c14(tmp_path, {"password_policy": policy}, pool_props=pool) == []


def test_c14_username_attribute_and_missing_pool(tmp_path):
    found = _c14(tmp_path, {"username_attribute": "email"})
    assert len(found) == 1 and "UsernameAttributes" in found[0]["description"]
    _write_good_workspace(tmp_path / "nopool")
    template = gates.load_template(tmp_path / "nopool" / "output" / "template.yaml")
    del template["Resources"]["UserPool"], template["Resources"]["UserPoolClient"]
    assert [f["severity"] for f in gates.check_c14(tmp_path / "nopool", template)] == ["ERROR"]


def test_c14_cognito_disabled_is_not_checked(tmp_path):
    assert _c14(tmp_path, {"enabled": False}, pool_props={}) == []



if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))


def _layer_ws(tmp_path, files, content_uri, runtime, build_method):
    for rel in files:
        (tmp_path / "output" / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / "output" / rel).write_text("x = 1\n", encoding="utf-8")
    res = {"Type": "AWS::Serverless::LayerVersion", "Properties": {"ContentUri": content_uri, "CompatibleRuntimes": [runtime]}}
    if build_method:
        res["Metadata"] = {"BuildMethod": build_method}
    return {"Resources": {"SharedLayer": res}}


def test_c13_layer_layout(tmp_path):
    """sam build with BuildMethod nests ContentUri under python/ / nodejs/
    itself; M2S's documented Python layout (code in layers/shared/python/ +
    BuildMethod) produced /opt/python/python/ and an ImportModuleError."""
    cases = [
        (["layers/shared/python/shared_utils.py"], "layers/shared/", "python3.12", "python3.12", True),   # double nesting
        (["layers/shared/shared_utils.py"], "layers/shared/", "python3.12", "python3.12", False),          # correct
        (["layers/shared/python/shared_utils.py"], "layers/shared/", "python3.12", None, False),           # plain copy, correct
        (["layers/shared/shared_utils.py"], "layers/shared/", "python3.12", None, True),                   # plain copy to /opt/
        (["layers/shared/nodejs/shared.js"], "layers/shared/", "nodejs22.x", "nodejs22.x", True),          # nodejs/nodejs
        (["layers/shared/nodejs/shared.js"], "layers/shared/nodejs/", "nodejs22.x", "nodejs22.x", False),  # M2S's Node layout
    ]
    for i, (files, uri, runtime, bm, bad) in enumerate(cases):
        ws = tmp_path / str(i)
        template = _layer_ws(ws, files, uri, runtime, bm)
        found = gates.check_c13(ws, template)
        assert bool(found) == bad, (i, found)
        assert all(f["check_id"] == "C13" and f["severity"] == "ERROR" for f in found)


def test_tools_check_flags_modified_pipeline_files(tmp_path):
    """TOOLS (gate final): an agent editing gates.py, adding a file to the
    tools, or a stale ~/.openharness agent copy fails the gate; the ledger
    that S5 writes does not."""
    import subprocess
    repo, home = tmp_path / "repo", tmp_path / "home_agents"
    (repo / "m2s-mcp-server" / "agents").mkdir(parents=True)
    (repo / "m2s-mcp-server" / "perf-rules").mkdir()
    home.mkdir()
    (repo / "m2s-mcp-server" / "gates.py").write_text("x = 1\n", encoding="utf-8")
    (repo / "m2s-mcp-server" / "perf-rules" / "ledger.json").write_text("{}\n", encoding="utf-8")
    (repo / "m2s-mcp-server" / "agents" / "m2s-architect.md").write_text("prompt\n", encoding="utf-8")
    (home / "m2s-architect.md").write_bytes((repo / "m2s-mcp-server" / "agents" / "m2s-architect.md").read_bytes())
    git = lambda *a: subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)
    git("init", "-q")
    git("add", "-A")
    git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "tools")
    assert _real_check_tools_unmodified(repo, home) == []
    (repo / "m2s-mcp-server" / "perf-rules" / "ledger.json").write_text('{"runs": []}\n', encoding="utf-8")
    assert _real_check_tools_unmodified(repo, home) == []  # S5's ledger is exempt
    (repo / "m2s-mcp-server" / "gates.py").write_text("x = 2  # 'fixed' by an agent\n", encoding="utf-8")
    (repo / "m2s-mcp-server" / "helper.py").write_text("y = 1\n", encoding="utf-8")
    (home / "m2s-architect.md").write_text("edited\n", encoding="utf-8")
    found = _real_check_tools_unmodified(repo, home)
    assert {f["check_id"] for f in found} == {"TOOLS"} and all(f["severity"] == "ERROR" for f in found)
    text = " | ".join(f["description"] for f in found)
    assert "gates.py" in text and "helper.py" in text and "m2s-architect.md" in text


# -- contract testing (PIPELINE_REDESIGN.md section 3.6) -------------------

def _contract_ws(tmp_path, meta, rules, responses):
    (tmp_path / "storage").mkdir()
    (tmp_path / "storage" / "blueprint.json").write_text(json.dumps({"metadata": meta}), encoding="utf-8")
    report = {"entry_points": [{"method": "POST", "path": "/x", "business_rules": rules, "responses": responses}]}
    (tmp_path / "storage" / "analysis_report.json").write_text(json.dumps(report), encoding="utf-8")
    return tmp_path


def _ids(findings):
    return sorted(f["check_id"] for f in findings)


def test_contract_unclassified_spec_needs_nothing(tmp_path):
    ws = _contract_ws(tmp_path, {}, [{"rule": "r"}], [{"status": 200}])
    assert gates.check_contract(ws) == []


def test_contract_classified_spec_needs_the_switch_and_rulings(tmp_path):
    rules = [{"rule": "r", "class": "implementation-trace"}]
    resp = [{"status": 200}, {"status": 400, "trace_arm": True}]
    ws = _contract_ws(tmp_path, {}, rules, resp)
    assert _ids(gates.check_contract(ws)) == ["CT1", "CT3"]
    si = {"issue_id": "SI-1", "class": "implementation-trace", "decision": "void"}
    (ws / "storage" / "blueprint.json").write_text(json.dumps({"metadata": {"verification_contract": 2, "spec_resolutions": [si]}}), encoding="utf-8")
    assert _ids(gates.check_contract(ws)) == ["CT2", "CT3"]
    si["targets"] = ["POST /x#responses[1]"]
    (ws / "storage" / "blueprint.json").write_text(json.dumps({"metadata": {"verification_contract": 2, "spec_resolutions": [si]}}), encoding="utf-8")
    assert gates.check_contract(ws) == []


def test_spec_relaxations_must_be_finished(tmp_path):
    (tmp_path / "storage").mkdir()
    (tmp_path / "output").mkdir()
    si = {"issue_id": "SI-20", "class": "implementation-trace", "origin": "perf-request REQ-1", "targets": ["POST /x#responses[1]"]}
    (tmp_path / "storage" / "blueprint.json").write_text(json.dumps({"metadata": {"spec_resolutions": [si]}}), encoding="utf-8")
    rp = tmp_path / "output" / "perf_report.json"
    rp.write_text(json.dumps({"changes": [{"id": "C1", "request": "perf/requests/REQ-1.json", "verdict": "pending_spec"}]}), encoding="utf-8")
    assert _ids(gates.check_spec_relaxations(tmp_path)) == ["SR1", "SR2"]
    rp.write_text(json.dumps({"changes": [{"id": "C1", "request": "perf/requests/REQ-1.json", "verdict": "reverted"}]}), encoding="utf-8")
    assert gates.check_spec_relaxations(tmp_path) == []
    si["ruling"] = "rejected"
    (tmp_path / "storage" / "blueprint.json").write_text(json.dumps({"metadata": {"spec_resolutions": [si]}}), encoding="utf-8")
    rp.write_text(json.dumps({"changes": []}), encoding="utf-8")
    assert gates.check_spec_relaxations(tmp_path) == []


def test_si_class_vocabulary(tmp_path):
    ws = _contract_ws(tmp_path, {}, [{"rule": "r"}], [{"status": 200}])
    def meta(m):
        (ws / "storage" / "blueprint.json").write_text(json.dumps({"metadata": m}), encoding="utf-8")
    meta({"spec_resolutions": [{"issue_id": "SI-1", "class": "auth-migration"}]})
    assert _ids(gates.check_contract(ws)) == ["CT4"]
    meta({"spec_resolutions": [{"issue_id": "SI-1"}]})  # contract 1: class optional
    assert gates.check_contract(ws) == []
    meta({"verification_contract": 2, "spec_resolutions": [{"issue_id": "SI-1"}, {"issue_id": "SI-2", "class": "spec-gap"}]})
    assert _ids(gates.check_contract(ws)) == ["CT4"]


def test_data_model_change_shape(tmp_path):
    ws = _contract_ws(tmp_path, {}, [{"rule": "r"}], [{"status": 200}])
    si = {"issue_id": "SI-1", "class": "implementation-trace", "targets": ["POST /x#responses[0]"],
          "data_model_change": {"table": "Books", "index": {"name": "all-by-sales"}}}
    (ws / "storage" / "blueprint.json").write_text(json.dumps({"metadata": {"spec_resolutions": [si]}}), encoding="utf-8")
    assert _ids(gates.check_contract(ws)) == ["CT5"]
    si["data_model_change"] = {"table": "Books", "index": {"name": "all-by-sales", "keys": [{"attribute": "gsiPk", "type": "HASH"}]},
                               "new_attributes": [{"name": "gsiPk", "value": "ALL", "backfill": "set gsiPk='ALL' on every existing row"}]}
    (ws / "storage" / "blueprint.json").write_text(json.dumps({"metadata": {"spec_resolutions": [si]}}), encoding="utf-8")
    assert gates.check_contract(ws) == []


def test_result_order_values(tmp_path):
    ws = _contract_ws(tmp_path, {}, [{"rule": "r"}], [{"status": 200}])
    def si(**kw):
        (ws / "storage" / "blueprint.json").write_text(json.dumps({"metadata": {"spec_resolutions": [{"issue_id": "SI-1", **kw}]}}),
                                                       encoding="utf-8")
    si(result_order="any")
    assert _ids(gates.check_contract(ws)) == ["CT6"]
    si(result_order="unspecified")
    assert _ids(gates.check_contract(ws)) == ["CT6"]
    si(result_order="unspecified", applies_to=["f"])
    assert gates.check_contract(ws) == []


# -- app-wide response headers (global middleware) ---------------------------

def _gm_ws(tmp_path, handler_src, gateway_headers=None, dropped=False):
    (tmp_path / "storage").mkdir()
    report = {"entry_points": [], "global_middleware": [
        {"kind": "cors", "framework": "flask-cors", "headers": {"Access-Control-Allow-Origin": "*"}, "source": [{"file": "a/app.py", "line": 24}]}]}
    (tmp_path / "storage" / "analysis_report.json").write_text(json.dumps(report), encoding="utf-8")
    meta = {"spec_resolutions": [{"issue_id": "SI-1", "class": "migration-change", "targets": ["global_middleware[0]"]}]} if dropped else {}
    bp = {"metadata": meta, "lambda_functions": [{"name": "flights-search", "trigger_type": "api"}, {"name": "consumer", "trigger_type": "sqs"}]}
    (tmp_path / "storage" / "blueprint.json").write_text(json.dumps(bp), encoding="utf-8")
    for name, src in (("flights-search", handler_src), ("consumer", "def lambda_handler(e, c): return {}")):
        d = tmp_path / "output" / "lambdas" / "flights" / name
        d.mkdir(parents=True)
        (d / "handler.py").write_text(src, encoding="utf-8")
    template = {"Globals": {"Api": {"Cors": {"AllowOrigin": "'*'"}}}, "Resources": {}}
    if gateway_headers:
        template["Globals"]["Api"]["GatewayResponses"] = {r: {"ResponseParameters": {"Headers": gateway_headers}} for r in ("DEFAULT_4XX", "DEFAULT_5XX")}
    return tmp_path, template


def test_gm_flags_the_airline_gap(tmp_path):
    # airline: a Cors block (preflight only) and handlers returning only Content-Type
    ws, template = _gm_ws(tmp_path, 'def json_response(s, p): return {"headers": {"Content-Type": "application/json"}}')
    assert _ids(gates.check_gm_code(ws)) == ["GM1"]
    assert _ids(gates.check_gm_template(ws, template)) == ["GM2", "GM2"]


def test_gm_passes_when_lambda_and_gateway_set_the_header(tmp_path):
    ws, template = _gm_ws(tmp_path, 'H = {"Content-Type": "application/json", "Access-Control-Allow-Origin": "*"}',
                          gateway_headers={"Access-Control-Allow-Origin": "'*'"})
    assert gates.check_gm_code(ws) == [] and gates.check_gm_template(ws, template) == []


def test_gm_respects_a_migration_change_ruling(tmp_path):
    ws, template = _gm_ws(tmp_path, "def f(): pass", dropped=True)
    assert gates.check_gm_code(ws) == [] and gates.check_gm_template(ws, template) == []


def _at_ws(tmp_path, api, cognito=False, auth="none", headers=None):
    (tmp_path / "storage").mkdir(exist_ok=True)
    bp = {"lambda_functions": [{"name": "f", "trigger_type": "api", "auth": auth, "entry_points": ["GET /x"]}],
          "cognito": {"enabled": cognito}, "api_gateway": api, "metadata": {}}
    (tmp_path / "storage" / "blueprint.json").write_text(json.dumps(bp), encoding="utf-8")
    gm = [{"kind": "cors", "headers": headers}] if headers else []
    (tmp_path / "storage" / "analysis_report.json").write_text(json.dumps({"global_middleware": gm}), encoding="utf-8")
    return tmp_path


def _sev(findings):
    return sorted((f["check_id"], f["severity"]) for f in findings)


def test_at1_http_when_nothing_rest_only(tmp_path):
    ws = _at_ws(tmp_path, {"api_type": "http", "clients": "same_region", "api_type_reason": "none"})
    assert gates.check_api_type(ws) == []
    ws = _at_ws(tmp_path, {"api_type": "rest", "clients": "same_region", "api_type_reason": "habit"})
    assert _sev(gates.check_api_type(ws)) == [("AT1", "WARNING")]          # R6
    assert _sev(gates.check_api_type(_at_ws(tmp_path, {}))) == [("AT1", "ERROR")] * 3


def test_at1_rest_only_needs(tmp_path):
    # Cognito and a plain 'ACAO: *' work on HTTP API (JWT authorizer, CorsConfiguration): AWS probe 2026-10-10
    for kw in ({"auth": "required"}, {"cognito": True}, {"headers": {"Access-Control-Allow-Origin": "*"}}):
        ws = _at_ws(tmp_path, {"api_type": "http", "clients": "internet", "api_type_reason": "x"}, **kw)
        assert gates.check_api_type(ws) == [], kw
    ws = _at_ws(tmp_path, {"api_type": "http", "clients": "internet", "api_type_reason": "x"},
                headers={"Access-Control-Allow-Origin": "<echo Origin>", "Vary": "Origin"})
    assert _sev(gates.check_api_type(ws)) == [("AT1", "ERROR")]
    ws = _at_ws(tmp_path, {"api_type": "rest", "clients": "internet", "api_type_reason": "x"},
                headers={"X-Frame-Options": "DENY"})
    assert gates.check_api_type(ws) == []
    ws = _at_ws(tmp_path, {"api_type": "http", "clients": "internet", "api_type_reason": "x", "rest_features": ["waf", "bogus"]})
    assert _sev(gates.check_api_type(ws)) == [("AT1", "ERROR")] * 2


def _at_template(kind, payload="'1.0'", endpoint=None):
    api = {"Type": "AWS::Serverless::HttpApi" if kind == "http" else "AWS::Serverless::Api", "Properties": {"StageName": "Prod"}}
    if endpoint:
        api["Properties"]["EndpointConfiguration"] = {"Type": endpoint}
    ev = ({"Type": "HttpApi", "Properties": {"ApiId": "A", "Path": "/x", "Method": "get", **({"PayloadFormatVersion": "1.0"} if payload else {})}}
          if kind == "http" else {"Type": "Api", "Properties": {"RestApiId": "A", "Path": "/x", "Method": "get"}})
    return {"Resources": {"A": api, "F": {"Type": "AWS::Serverless::Function", "Properties": {"Events": {"E": ev}}}}}


def test_at2_template_matches_api_type(tmp_path):
    ws = _at_ws(tmp_path, {"api_type": "http", "clients": "same_region", "api_type_reason": "none"})
    assert gates.check_api_type_template(ws, _at_template("http")) == []
    assert _sev(gates.check_api_type_template(ws, _at_template("http", payload=None))) == [("AT2", "ERROR")]
    assert _sev(gates.check_api_type_template(ws, _at_template("rest"))) == [("AT2", "ERROR")] * 2
    ws = _at_ws(tmp_path, {"api_type": "rest", "clients": "same_region", "api_type_reason": "waf", "rest_features": ["waf"]})
    assert _sev(gates.check_api_type_template(ws, _at_template("rest"))) == [("AT2", "ERROR")]   # EDGE by default
    assert gates.check_api_type_template(ws, _at_template("rest", endpoint="REGIONAL")) == []
    ws = _at_ws(tmp_path, {"type": "REST"})                                    # before AT1: not checked
    assert gates.check_api_type_template(ws, _at_template("rest")) == []
    assert gates.check_c7(ws, _at_template("http")) == []


def test_at2_http_with_gateway_auth_needs_jwt_authorizer(tmp_path):
    ws = _at_ws(tmp_path, {"api_type": "http", "clients": "internet", "api_type_reason": "none"},
                cognito=True, auth="required")
    t = _at_template("http")
    assert _sev(gates.check_api_type_template(ws, t)) == [("AT2", "ERROR")]
    t["Resources"]["A"]["Properties"]["Auth"] = {"DefaultAuthorizer": "J", "Authorizers": {"J": {"JwtConfiguration": {"issuer": "i", "audience": ["a"]}}}}
    assert gates.check_api_type_template(ws, t) == []


def _ct_ws(tmp_path, resolutions=None, hints=None):
    (tmp_path / "storage").mkdir(exist_ok=True)
    meta = {"verification_contract": 2, "spec_resolutions": resolutions or []}
    if hints is not None:
        meta["perf_hints"] = hints
    bp = {"metadata": meta, "dynamodb_tables": [{"logical_name": "Flight"}],
          "lambda_functions": [{"name": "flights-query", "trigger_type": "api"}]}
    (tmp_path / "storage" / "blueprint.json").write_text(json.dumps(bp), encoding="utf-8")
    (tmp_path / "storage" / "analysis_report.json").write_text(json.dumps({"entry_points": []}), encoding="utf-8")
    return tmp_path


BS_OK = {"issue_id": "SI-9", "class": "contract", "applies_to": ["flights-query"],
         "bounded_staleness": {"tables": ["Flight"], "ttl_seconds": 600, "evidence": "routes/index.js:459 ttl-lru-cache, flightDataCacheTTL"}}


def test_ct7_bounded_staleness(tmp_path):
    assert gates.check_contract(_ct_ws(tmp_path, [BS_OK])) == []
    bad = {**BS_OK, "class": "implementation-trace", "targets": ["x"], "applies_to": ["nope"],
           "bounded_staleness": {"tables": ["Nope"], "ttl_seconds": 0, "evidence": ""}}
    ids = [f["description"] for f in gates.check_contract(_ct_ws(tmp_path, [bad])) if f["check_id"] == "CT7"]
    assert len(ids) == 5


def test_ph1_perf_hints(tmp_path):
    ok = [{"kind": "cache", "source": "acmeair/routes/index.js:435", "applies_to": ["flights-query"], "ttl_seconds": 600}]
    assert gates.check_contract(_ct_ws(tmp_path, hints=ok)) == []
    bad = [{"kind": "memo", "source": "somewhere", "applies_to": ["x"]}, "str"]
    assert [f["check_id"] for f in gates.check_contract(_ct_ws(tmp_path, hints=bad))] == ["PH1"] * 4


def test_at2_cognito_disabled_allows_in_function_auth(tmp_path):
    ws = _at_ws(tmp_path, {"api_type": "http", "clients": "internet", "api_type_reason": "none"},
                cognito=False, auth="required")
    assert gates.check_api_type_template(ws, _at_template("http")) == []


def _c7b_template_with_api(default_authorizer: bool) -> dict:
    api_props = {"StageName": "Prod"}
    if default_authorizer:
        api_props["Auth"] = {"DefaultAuthorizer": "J", "Authorizers": {"J": {"JwtConfiguration": {"issuer": "i", "audience": ["a"]}}}}
    return {"Resources": {
        "Api": {"Type": "AWS::Serverless::HttpApi", "Properties": api_props},
        "PublicFunction": {"Type": "AWS::Serverless::Function", "Properties": {"Events": {
            "E": {"Type": "HttpApi", "Properties": {"ApiId": "Api", "Path": "/public", "Method": "GET"}}}}},
    }}


def test_c7b_skipped_without_default_authorizer(tmp_path):
    _write_blueprint_only(tmp_path, _public_endpoint_blueprint("none"))
    assert gates.check_c7b(tmp_path, _c7b_template_with_api(default_authorizer=False)) == []


def test_c7b_still_requires_override_with_default_authorizer(tmp_path):
    _write_blueprint_only(tmp_path, _public_endpoint_blueprint("none"))
    findings = gates.check_c7b(tmp_path, _c7b_template_with_api(default_authorizer=True))
    assert [f["check_id"] for f in findings] == ["C7b"]
