"""Deterministic merge CLI for the M2S migration pipeline.

Exists specifically to prevent the historical crash this plan's B0 gate
also guards against: an agent splitting output across numbered sibling
files (blueprint2.json, blueprint3.json, ...) with no merge step, leaving
blueprint.json holding only whatever fragment it wrote last.

Two subcommands:

  python m2s_merge.py blueprint <workspace>
      Deep-merges <workspace>/storage/blueprint_sections/*.json (sorted by
      filename) into <workspace>/storage/blueprint.json. Pure JSON dict
      union -- no YAML/CFN-tag parsing involved, so there's no lossy
      round-trip risk.

  python m2s_merge.py template <workspace>
      Concatenates <workspace>/output/template.part1.yaml and
      template.part2.yaml into <workspace>/output/template.yaml.
      Deliberately a plain text splice, NOT a YAML parse+reserialize:
      gates.py's _CFNLoader (ported from M2S's file_tools.py) resolves
      CFN intrinsic tags (!Ref, !GetAtt, !Sub, ...) down to their bare
      value for comparison purposes, which is lossy -- re-serializing
      that would silently turn "!Ref TodoItems" into the plain string
      "TodoItems" and produce an invalid template. Text concatenation
      has no such risk.

      Split convention (mirrors the real M2S output/template.yaml, which
      already carries these exact marker comments):
        part1.yaml: AWSTemplateFormatVersion .. Globals .. Parameters ..
                    shared resources (Cognito/DynamoDB/API/etc.) ..
                    ending after the "# --- SHARED_RESOURCES_METADATA ---
                    ... # --- END METADATA ---" block.
        part2.yaml: the AWS::Serverless::Function resources ..
                    "# --- FUNCTIONS_METADATA --- ... # --- END METADATA
                    ---" block .. Outputs.
      Both files are written by the sam-engineer agent(s); this command
      does not validate their content, only concatenates them (cfn-lint
      via mcp__m2s-server__validate_sam is the semantic validator).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def merge_blueprint(workspace: str) -> dict[str, Any]:
    ws = Path(workspace).resolve()
    sections_dir = ws / "storage" / "blueprint_sections"
    output_path = ws / "storage" / "blueprint.json"

    if not sections_dir.exists():
        raise FileNotFoundError(f"no blueprint_sections directory: {sections_dir}")

    section_files = sorted(sections_dir.glob("*.json"))
    if not section_files:
        raise FileNotFoundError(f"no *.json files found under {sections_dir}")

    merged: dict[str, Any] = {}
    key_origin: dict[str, str] = {}
    warnings: list[str] = []

    for path in section_files:
        section = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(section, dict):
            raise ValueError(f"{path}: top-level JSON must be an object, got {type(section).__name__}")
        for key, value in section.items():
            if key in merged:
                warnings.append(
                    f"key '{key}' defined in both {key_origin[key]} and {path.name}; "
                    f"{path.name} (later, sorted-by-name) wins"
                )
            merged[key] = value
            key_origin[key] = path.name

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(merged, indent=2, ensure_ascii=False), encoding="utf-8")

    return {
        "output_path": str(output_path),
        "sections_merged": [p.name for p in section_files],
        "keys": sorted(merged.keys()),
        "warnings": warnings,
    }


#: The only legal top-level keys in blueprint.json. Mirrors M2S's own
#: schema (storage/blueprint.json) -- rejecting anything else here is an
#: improvement over M2S's WriteFileTool.json_key, which accepts an
#: arbitrary key name. This is a direct fix for a real failure mode: the
#: architect agent twice invented its own top-level keys (auth_info,
#: nodejs_dependencies, async_patterns, ...) when asked to produce a
#: complete blueprint.json in one shot. Rejecting bad keys immediately,
#: in the same tool-call turn, is cheaper than catching it via a later
#: run_gate(phase="blueprint") call after the agent has already exited.
BLUEPRINT_KEYS: frozenset[str] = frozenset(
    {
        "metadata",
        "lambda_functions",
        "dynamodb_tables",
        "s3_buckets",
        "sqs_queues",
        "eventbridge_rules",
        "lambda_invoke_permissions",
        "cognito",
        "api_gateway",
        "dropped_functions",
    }
)


def merge_blueprint_key(workspace: str, key: str, value_json: str) -> dict[str, Any]:
    """Merge one section into storage/blueprint.json, one top-level key at a time.

    Ported from M2S's WriteFileTool(json_key=...) (src/tools/file_tools.py:
    _run_json_merge): the agent supplies a small JSON fragment for a single
    key instead of producing one giant, internally-consistent document in
    one shot. If the key already holds a list and the new value is also a
    list, they're concatenated (so lambda_functions can be built up a few
    functions at a time); otherwise the key is set/overwritten. The file is
    created as {} if it doesn't exist yet, and is re-parsed after writing to
    guarantee it's never left invalid.
    """
    if key not in BLUEPRINT_KEYS:
        raise ValueError(
            f"'{key}' is not a valid blueprint.json top-level key. "
            f"Valid keys are: {sorted(BLUEPRINT_KEYS)}. "
            "Do not invent new keys -- if none of these fit what you're "
            "trying to record, put it in 'metadata.notes' instead."
        )

    try:
        parsed_value = json.loads(value_json)
    except json.JSONDecodeError as exc:
        raise ValueError(f"value_json is not valid JSON: {exc}") from exc

    ws = Path(workspace).resolve()
    target = ws / "storage" / "blueprint.json"
    target.parent.mkdir(parents=True, exist_ok=True)

    if target.exists():
        raw = target.read_text(encoding="utf-8")
        data = json.loads(raw) if raw.strip() else {}
        if not isinstance(data, dict):
            data = {}
    else:
        data = {}

    if key in data and isinstance(data[key], list) and isinstance(parsed_value, list):
        # Guard against accidental duplicate submission: arrays concatenate
        # rather than replace, so resubmitting an item you already sent
        # (e.g. after renaming it) silently doubles it up instead of fixing
        # it. Caught once in practice: the architect re-submitted its whole
        # lambda_functions list after renaming one function, producing 12
        # entries (6 real + 6 near-duplicates) instead of 6. Only checked
        # for dict items carrying a "name" field (lambda_functions,
        # dynamodb_tables, s3_buckets, sqs_queues all use one).
        existing_names = {
            item.get("name") for item in data[key] if isinstance(item, dict) and item.get("name")
        }
        new_names = {item.get("name") for item in parsed_value if isinstance(item, dict) and item.get("name")}
        collision = existing_names & new_names
        if collision:
            raise ValueError(
                f"'{key}' already contains an item named {sorted(collision)}. "
                "merge_blueprint_key APPENDS to arrays, it does not replace items -- "
                "resubmitting the same name (e.g. after renaming or editing it) would "
                "create a duplicate, not a correction. If you need to change an item "
                "you already submitted, you must include the FULL corrected array "
                "including all previously-submitted items you want to keep, and the "
                "workspace's blueprint.json needs to be reset for this key first "
                "(ask the orchestrator, or start this key over from a clean run)."
            )
        data[key].extend(parsed_value)
        action = "appended to"
    else:
        data[key] = parsed_value
        action = "set"

    target.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    # Re-read and re-parse to guarantee the file on disk is valid JSON,
    # exactly mirroring M2S's own post-write verification.
    written = target.read_text(encoding="utf-8")
    reparsed = json.loads(written)

    return {
        "action": action,
        "key": key,
        "output_path": str(target),
        "current_keys": sorted(reparsed.keys()),
        "missing_keys": sorted(BLUEPRINT_KEYS - reparsed.keys()),
    }


def merge_template(workspace: str) -> dict[str, Any]:
    ws = Path(workspace).resolve()
    output_dir = ws / "output"
    part1_path = output_dir / "template.part1.yaml"
    part2_path = output_dir / "template.part2.yaml"
    output_path = output_dir / "template.yaml"

    for part_path in (part1_path, part2_path):
        if not part_path.exists():
            raise FileNotFoundError(f"missing template part: {part_path}")

    part1 = part1_path.read_text(encoding="utf-8")
    part2 = part2_path.read_text(encoding="utf-8")

    merged = part1.rstrip("\n") + "\n\n" + part2.lstrip("\n")
    if not merged.endswith("\n"):
        merged += "\n"

    output_path.write_text(merged, encoding="utf-8")

    return {
        "output_path": str(output_path),
        "part1_path": str(part1_path),
        "part2_path": str(part2_path),
        "part1_lines": part1.count("\n"),
        "part2_lines": part2.count("\n"),
    }


def main(argv: list[str]) -> int:
    if len(argv) != 3 or argv[1] not in {"blueprint", "template"}:
        print("usage: python m2s_merge.py {blueprint|template} <workspace>", file=sys.stderr)
        return 2

    mode, workspace = argv[1], argv[2]
    try:
        result = merge_blueprint(workspace) if mode == "blueprint" else merge_template(workspace)
    except (FileNotFoundError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1

    print(json.dumps({"ok": True, **result}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
