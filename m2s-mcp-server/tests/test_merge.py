"""Tests for m2s_merge.py's deterministic blueprint/template merging."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import m2s_merge  # noqa: E402


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
