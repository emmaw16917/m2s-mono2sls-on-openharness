"""M2S MCP server: exposes deterministic Mono2Sls tools (static analysis,
SAM template validation, consistency gates) to OpenHarness agents over stdio.

Registered in OpenHarness as `m2s-server`, so tool names resolve to:
    mcp__m2s-server__analyze_project
    mcp__m2s-server__validate_sam
    mcp__m2s-server__run_gate
    mcp__m2s-server__merge_blueprint_key

Workspace resolution: all relative paths are resolved against $M2S_WORKSPACE
(default: ./runs/todo-oh relative to this file's parent dir) so tool output
never pollutes M2S's own reference `storage/`/`output/` directories.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP

import wrap_m2s
import m2s_merge

# Only import gates lazily / guard for P0: gates.py is a P1 deliverable.
try:
    import gates as _gates
except ImportError:
    _gates = None

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MONOLITH_ROOT = REPO_ROOT / "Mono2Sls-artifacts" / "mono_benchmark" / "todo"
# Matches the registered mcp_servers.m2s-server config: cwd=M2S,
# M2S_WORKSPACE=runs/todo-oh (relative to that cwd) -> M2S/runs/todo-oh.
# Kept separate from M2S/storage and M2S/output so it never overwrites the
# reference pipeline artifacts already sitting there.
DEFAULT_WORKSPACE = wrap_m2s.M2S_ROOT / "runs" / "todo-oh"


def _workspace() -> Path:
    ws = os.environ.get("M2S_WORKSPACE")
    path = Path(ws).resolve() if ws else DEFAULT_WORKSPACE
    path.mkdir(parents=True, exist_ok=True)
    return path


mcp = FastMCP("m2s-server")


@mcp.tool()
def analyze_project(monolith_root: str | None = None) -> str:
    """Run static analysis over the monolith and write
    analysis_report.json + symbol_table.json into the workspace.

    Args:
        monolith_root: absolute path to the monolith source root. Defaults
            to Mono2Sls-artifacts/mono_benchmark/todo if omitted.

    Returns: JSON string with report_path, symbol_table_path,
        entry_point_count, entry_points, config_info, dynamodb_info.
    """
    root = monolith_root or str(DEFAULT_MONOLITH_ROOT)
    result = wrap_m2s.analyze(root, str(_workspace()))
    return json.dumps(result, ensure_ascii=False)


@mcp.tool()
def validate_sam(template_path: str) -> str:
    """Run cfn-lint against a SAM template.

    Args:
        template_path: absolute path to the template.yaml to validate.

    Returns: JSON string {ok, errors, warnings, raw}.
    """
    result = wrap_m2s.validate_sam(template_path)
    return json.dumps(result, ensure_ascii=False)


@mcp.tool()
def merge_blueprint_key(key: str, value_json: str, mode: str = "append") -> str:
    """Merge one section into storage/blueprint.json, one top-level key at a time.

    Build the blueprint incrementally instead of writing one giant JSON
    document in a single shot: call this once per top-level key (e.g. once
    for "metadata", once for "cognito", once or more for "lambda_functions"
    if you want to add functions in a few batches). The file is validated as
    JSON on every call, so a malformed value_json is rejected immediately
    instead of surfacing later in run_gate.

    Args:
        key: one of metadata, lambda_functions, dynamodb_tables, s3_buckets,
            sqs_queues, eventbridge_rules, lambda_invoke_permissions,
            cognito, api_gateway, dropped_functions. Any other key is
            rejected -- do not invent new top-level keys.
        value_json: a JSON string holding the value for that key (an object
            for metadata/cognito/api_gateway, an array for the rest).
        mode: "append" (default) -- for array values, concatenates onto
            whatever is already there, and rejects the call if an item with
            the same "name" already exists (use this for genuinely NEW
            items). "replace" -- overwrites the key wholesale regardless of
            type, no duplicate check. Use "replace" when you're
            self-correcting something you already submitted (e.g. after
            run_gate flagged a problem with one function you already wrote):
            pass the FULL corrected array, not just the changed item.

    Returns: JSON string {action, key, output_path, current_keys, missing_keys}.
    """
    try:
        result = m2s_merge.merge_blueprint_key(str(_workspace()), key, value_json, mode=mode)
    except ValueError as exc:
        return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)
    return json.dumps({"ok": True, **result}, ensure_ascii=False)


@mcp.tool()
def run_gate(phase: str) -> str:
    """Run the deterministic consistency gate for a migration phase.

    Args:
        phase: one of "blueprint", "code", "template", "final".

    Returns: JSON string {phase, status, mechanical_failures, checks,
        suspects, stage_report}.
    """
    if _gates is None:
        return json.dumps(
            {
                "phase": phase,
                "status": "not_implemented",
                "mechanical_failures": [],
                "checks": [],
                "suspects": {},
                "stage_report": {"path": "", "exists": False},
                "note": "gates.py not yet implemented (P1 deliverable)",
            },
            ensure_ascii=False,
        )
    result = _gates.run_gate(phase, str(_workspace()))
    return json.dumps(result, ensure_ascii=False)


if __name__ == "__main__":
    mcp.run(transport="stdio")
