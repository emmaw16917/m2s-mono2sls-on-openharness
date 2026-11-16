"""Unit tests for the per-package judge case check (v3 M4/W4.2: J1 split)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import judge  # noqa: E402


def _case(ws: Path, package: str, name: str, case_id: str, function: str) -> None:
    path = ws / "judge" / "cases" / package / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"id": case_id, "function": function, "spec_refs": ["GET /x#responses[0]"]}),
                    encoding="utf-8")


def test_cases_package_needs_one_case_per_function(tmp_path: Path):
    ws = tmp_path / "ws"
    _case(ws, "alpha", "a", "case-a", "a-fn")
    result = judge.check_cases_package(ws, "alpha", ["a-fn", "b-fn"])
    assert result["status"] == "FAIL" and any("b-fn" in e for e in result["errors"])
    _case(ws, "alpha", "b", "case-b", "b-fn")
    assert judge.check_cases_package(ws, "alpha", ["a-fn", "b-fn"])["status"] == "PASS"


def test_cases_package_rejects_a_foreign_function_in_its_dir(tmp_path: Path):
    ws = tmp_path / "ws"
    _case(ws, "alpha", "a", "case-a", "a-fn")
    _case(ws, "alpha", "c", "case-c", "c-fn")
    result = judge.check_cases_package(ws, "alpha", ["a-fn"])
    assert result["status"] == "FAIL" and any("not in this package" in e for e in result["errors"])


def test_cases_package_rejects_duplicate_ids(tmp_path: Path):
    ws = tmp_path / "ws"
    _case(ws, "alpha", "a", "same-id", "a-fn")
    _case(ws, "beta", "b", "same-id", "b-fn")
    result = judge.check_cases_package(ws, "alpha", ["a-fn"])
    assert result["status"] == "FAIL" and any("same-id" in e for e in result["errors"])
