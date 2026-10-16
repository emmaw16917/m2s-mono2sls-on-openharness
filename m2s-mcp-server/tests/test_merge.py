"""Tests for m2s_merge.py's deterministic blueprint/template merging."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import m2s_merge  # noqa: E402


def test_merge_blueprint_key_sets_new_key(tmp_path):
    result = m2s_merge.merge_blueprint_key(str(tmp_path), "metadata", json.dumps({"application_name": "TODO"}))
    assert result["action"] == "set"
    merged = json.loads((tmp_path / "storage" / "blueprint.json").read_text(encoding="utf-8"))
    assert merged == {"metadata": {"application_name": "TODO"}}


def test_merge_blueprint_key_appends_arrays(tmp_path):
    m2s_merge.merge_blueprint_key(str(tmp_path), "lambda_functions", json.dumps([{"name": "fn1"}]))
    result = m2s_merge.merge_blueprint_key(str(tmp_path), "lambda_functions", json.dumps([{"name": "fn2"}]))
    assert result["action"] == "appended to"
    merged = json.loads((tmp_path / "storage" / "blueprint.json").read_text(encoding="utf-8"))
    assert merged["lambda_functions"] == [{"name": "fn1"}, {"name": "fn2"}]


def test_merge_blueprint_key_rejects_duplicate_name_append(tmp_path):
    m2s_merge.merge_blueprint_key(str(tmp_path), "lambda_functions", json.dumps([{"name": "todo-list-items"}]))
    try:
        m2s_merge.merge_blueprint_key(str(tmp_path), "lambda_functions", json.dumps([{"name": "todo-list-items"}]))
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "todo-list-items" in str(exc)
    # nothing should have been appended
    merged = json.loads((tmp_path / "storage" / "blueprint.json").read_text(encoding="utf-8"))
    assert merged["lambda_functions"] == [{"name": "todo-list-items"}]


def test_merge_blueprint_key_allows_distinct_names_after_append(tmp_path):
    m2s_merge.merge_blueprint_key(str(tmp_path), "lambda_functions", json.dumps([{"name": "fn1"}]))
    m2s_merge.merge_blueprint_key(str(tmp_path), "lambda_functions", json.dumps([{"name": "fn2"}]))
    merged = json.loads((tmp_path / "storage" / "blueprint.json").read_text(encoding="utf-8"))
    assert [f["name"] for f in merged["lambda_functions"]] == ["fn1", "fn2"]


def test_merge_blueprint_key_replace_mode_bypasses_duplicate_check(tmp_path):
    m2s_merge.merge_blueprint_key(str(tmp_path), "lambda_functions", json.dumps([{"name": "fn1", "auth": "none"}]))
    result = m2s_merge.merge_blueprint_key(
        str(tmp_path), "lambda_functions", json.dumps([{"name": "fn1", "auth": "required"}]), mode="replace"
    )
    assert result["action"] == "replaced"
    merged = json.loads((tmp_path / "storage" / "blueprint.json").read_text(encoding="utf-8"))
    assert merged["lambda_functions"] == [{"name": "fn1", "auth": "required"}]


def test_merge_blueprint_key_replace_mode_on_fresh_key(tmp_path):
    result = m2s_merge.merge_blueprint_key(str(tmp_path), "s3_buckets", json.dumps([]), mode="replace")
    assert result["action"] == "replaced"
    merged = json.loads((tmp_path / "storage" / "blueprint.json").read_text(encoding="utf-8"))
    assert merged["s3_buckets"] == []


def test_merge_blueprint_key_rejects_invalid_mode(tmp_path):
    try:
        m2s_merge.merge_blueprint_key(str(tmp_path), "metadata", "{}", mode="overwrite")
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "mode" in str(exc)


def test_merge_blueprint_key_overwrites_non_array(tmp_path):
    m2s_merge.merge_blueprint_key(str(tmp_path), "cognito", json.dumps({"enabled": False}))
    m2s_merge.merge_blueprint_key(str(tmp_path), "cognito", json.dumps({"enabled": True}))
    merged = json.loads((tmp_path / "storage" / "blueprint.json").read_text(encoding="utf-8"))
    assert merged["cognito"] == {"enabled": True}


def test_merge_blueprint_key_rejects_invented_key(tmp_path):
    try:
        m2s_merge.merge_blueprint_key(str(tmp_path), "auth_info", json.dumps({"used": True}))
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "not a valid blueprint.json top-level key" in str(exc)
    # nothing should have been written
    assert not (tmp_path / "storage" / "blueprint.json").exists()


def test_merge_blueprint_key_rejects_invalid_json_value(tmp_path):
    try:
        m2s_merge.merge_blueprint_key(str(tmp_path), "metadata", "{not valid json")
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "not valid JSON" in str(exc)


def test_merge_blueprint_key_reports_missing_keys(tmp_path):
    result = m2s_merge.merge_blueprint_key(str(tmp_path), "metadata", json.dumps({"application_name": "TODO"}))
    assert "metadata" not in result["missing_keys"]
    assert "lambda_functions" in result["missing_keys"]
    assert len(result["missing_keys"]) == len(m2s_merge.BLUEPRINT_KEYS) - 1


def test_merge_blueprint_key_builds_full_valid_blueprint(tmp_path):
    """End-to-end: build up the real todo blueprint one key at a time and
    confirm gates.py's B0 check passes, the same way the architect agent
    will build it via the MCP tool."""
    import gates

    storage = tmp_path / "storage"
    storage.mkdir(parents=True)
    (storage / "analysis_report.json").write_text(
        json.dumps({"entry_points": [{"method": "GET", "path": "/item"}]}), encoding="utf-8"
    )

    sections = {
        "metadata": {
            "application_name": "TODO", "architecture_pattern": "Basic Serverless Architecture",
            "has_authentication": True, "has_database": True, "has_file_storage": False,
            "has_async_flows": False, "notes": "fixture",
        },
        "dynamodb_tables": [{"logical_name": "TodoItems", "purpose": "x", "partition_key": {"name": "userId", "type": "S"}, "sort_key": {"name": "id", "type": "S"}, "gsi": []}],
        "s3_buckets": [], "sqs_queues": [], "eventbridge_rules": [], "lambda_invoke_permissions": [],
        "cognito": {"enabled": True, "user_pool_attributes": ["email"], "password_policy": {}},
        "api_gateway": {"type": "REST"},
        "lambda_functions": [{"name": "todo-list-items", "trigger_type": "api", "runtime": "nodejs22.x", "handler": "handler.lambda_handler", "auth": "required", "source_files": [], "entry_points": ["GET /item"], "environment_variables": {}}],
        "dropped_functions": [],
    }
    for key, value in sections.items():
        m2s_merge.merge_blueprint_key(str(tmp_path), key, json.dumps(value))
    (storage / "DECISIONS.md").write_text("## D-1\n- todo-list-items: one Lambda per endpoint\n", encoding="utf-8")

    findings = gates.check_b0(tmp_path)
    assert findings == []


def test_merge_blueprint_unions_sections(tmp_path):
    sections = tmp_path / "storage" / "blueprint_sections"
    sections.mkdir(parents=True)
    (sections / "01_metadata.json").write_text(json.dumps({"metadata": {"a": 1}, "cognito": {"enabled": True}}), encoding="utf-8")
    (sections / "02_functions.json").write_text(json.dumps({"lambda_functions": [{"name": "fn"}], "dropped_functions": []}), encoding="utf-8")

    result = m2s_merge.merge_blueprint(str(tmp_path))
    assert result["warnings"] == []
    merged = json.loads((tmp_path / "storage" / "blueprint.json").read_text(encoding="utf-8"))
    assert merged["metadata"] == {"a": 1}
    assert merged["lambda_functions"] == [{"name": "fn"}]


def test_merge_blueprint_reports_key_collision(tmp_path):
    sections = tmp_path / "storage" / "blueprint_sections"
    sections.mkdir(parents=True)
    (sections / "01_a.json").write_text(json.dumps({"metadata": {"from": "a"}}), encoding="utf-8")
    (sections / "02_b.json").write_text(json.dumps({"metadata": {"from": "b"}}), encoding="utf-8")

    result = m2s_merge.merge_blueprint(str(tmp_path))
    assert len(result["warnings"]) == 1
    merged = json.loads((tmp_path / "storage" / "blueprint.json").read_text(encoding="utf-8"))
    assert merged["metadata"] == {"from": "b"}  # later (sorted) section wins


def test_merge_blueprint_missing_sections_dir_raises(tmp_path):
    try:
        m2s_merge.merge_blueprint(str(tmp_path))
        assert False, "expected FileNotFoundError"
    except FileNotFoundError:
        pass


def test_merge_template_concatenates_parts(tmp_path):
    output = tmp_path / "output"
    output.mkdir(parents=True)
    (output / "template.part1.yaml").write_text("AWSTemplateFormatVersion: '2010-09-09'\nResources:\n  Table:\n    Type: AWS::DynamoDB::Table\n", encoding="utf-8")
    (output / "template.part2.yaml").write_text("  MyFunction:\n    Type: AWS::Serverless::Function\n\nOutputs: {}\n", encoding="utf-8")

    result = m2s_merge.merge_template(str(tmp_path))
    merged = (output / "template.yaml").read_text(encoding="utf-8")
    assert "AWSTemplateFormatVersion" in merged
    assert "MyFunction" in merged
    assert merged.index("Table") < merged.index("MyFunction")
    assert result["ok"] if "ok" in result else True


def test_merge_template_missing_part_raises(tmp_path):
    output = tmp_path / "output"
    output.mkdir(parents=True)
    (output / "template.part1.yaml").write_text("x: 1\n", encoding="utf-8")
    try:
        m2s_merge.merge_template(str(tmp_path))
        assert False, "expected FileNotFoundError"
    except FileNotFoundError:
        pass


def test_merge_matches_real_m2s_reference(tmp_path):
    """Split the REAL M2S blueprint.json and template.yaml into sections/parts
    the way the plan's agents are meant to, then confirm the merge reproduces
    output that gates.py still judges as PASS -- a real round-trip check, not
    just synthetic fixtures."""
    m2s_root = Path(r"D:\Emma\MyLab\Mono2SLs\Mono2Sls-artifacts\M2S")
    real_blueprint = json.loads((m2s_root / "storage" / "blueprint.json").read_text(encoding="utf-8"))
    real_template = (m2s_root / "output" / "template.yaml").read_text(encoding="utf-8")

    # --- split blueprint into two sections ---
    sections = tmp_path / "storage" / "blueprint_sections"
    sections.mkdir(parents=True)
    part_a_keys = {"metadata", "cognito", "api_gateway", "dynamodb_tables", "s3_buckets", "sqs_queues", "eventbridge_rules", "lambda_invoke_permissions"}
    part_a = {k: v for k, v in real_blueprint.items() if k in part_a_keys}
    part_b = {k: v for k, v in real_blueprint.items() if k not in part_a_keys}
    (sections / "01_infra.json").write_text(json.dumps(part_a), encoding="utf-8")
    (sections / "02_functions.json").write_text(json.dumps(part_b), encoding="utf-8")

    m2s_merge.merge_blueprint(str(tmp_path))
    merged_blueprint = json.loads((tmp_path / "storage" / "blueprint.json").read_text(encoding="utf-8"))
    assert merged_blueprint == real_blueprint

    # --- split template at the real metadata marker line ---
    split_marker = "# --- END METADATA ---\n"
    first_end = real_template.index(split_marker) + len(split_marker)
    part1_text, part2_text = real_template[:first_end], real_template[first_end:]

    (tmp_path / "output").mkdir(parents=True, exist_ok=True)
    (tmp_path / "output" / "template.part1.yaml").write_text(part1_text, encoding="utf-8")
    (tmp_path / "output" / "template.part2.yaml").write_text(part2_text, encoding="utf-8")

    m2s_merge.merge_template(str(tmp_path))
    merged_template_path = tmp_path / "output" / "template.yaml"

    import gates

    (tmp_path / "storage" / "analysis_report.json").write_text(
        (m2s_root / "storage" / "analysis_report.json").read_text(encoding="utf-8"), encoding="utf-8"
    )
    import shutil

    shutil.copytree(m2s_root / "output" / "lambdas", tmp_path / "output" / "lambdas")

    result = gates.run_gate("template", str(tmp_path))
    assert result["status"] == "PASS", result["mechanical_failures"]
