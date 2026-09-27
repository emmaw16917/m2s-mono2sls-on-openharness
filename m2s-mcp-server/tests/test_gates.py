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


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
