"""Tools added for N5: unfinished writes (V2P-22), decisions that name their judge cases
(V2P-23), rulings that reach one step, the blueprint model of I1's self test (N5.2) and the
merged coverage of changed lines (N5.3)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import gaps  # noqa: E402
import gates  # noqa: E402
import judge  # noqa: E402
import verify  # noqa: E402

# --- V2P-22 -----------------------------------------------------------------------


def test_unfinished_writes():
    calls = [{"op": "PutItem", "table": "T"}, {"op": "SendMessage", "queue": "Q", "unfinished_at_return": True, "function": "f"},
             {"op": "UpdateItem", "table": "T", "after_return": True}, {"op": "GetItem", "table": "T", "unfinished_at_return": True}]
    found = verify.unfinished_writes({"calls": calls, "rules_fired": [0]})
    # reads are left out: an abandoned speculative read (R1b) changes nothing
    assert found == [{"op": "SendMessage", "queue": "Q", "function": "f"}, {"op": "UpdateItem", "table": "T"}]
    # a fault that fired: abandoning a sibling call is how Promise.all fails
    assert verify.unfinished_writes({"calls": calls, "rules_fired": [1]}) == []


def test_judge_reports_them_and_the_expectations_too():
    model = verify.Model.__new__(verify.Model)
    model.contract = 1
    raw = {"result": {"statusCode": 500}, "calls": [{"op": "SendMessage", "queue": "Q", "unfinished_at_return": True}], "rules_fired": []}
    mism = verify.judge(model, {"id": "c", "expect": {"status": 200}}, raw)
    assert any("still unfinished when the function returned: SendMessage Q" in m for m in mism)
    assert any("500" in m for m in mism)
    assert verify.judge(model, {"id": "c", "expect": {"status": 500}}, {**raw, "calls": []}) == []


class _Facts:
    tables: dict = {}
    writes = staticmethod(lambda ep: [])
    entry_points: dict = {}


def _ctx() -> dict:
    return {"bindings": {}, "routes": [{"name": "s1", "route": {"function": "f"}}, {"name": "s2", "route": {"function": "f"}}], "rule_problems": []}


def _template() -> dict:
    return {"steps": {"s1": {"status": 401, "headers": {}, "body_kind": "json", "body": {"error": "x"}},
                      "s2": {"status": 200, "headers": {}, "body_kind": "json", "body": {"total": 3}}}}


def _rec(**extra) -> dict:
    return {"steps": [{"name": "s1", "route": "invoke", "response": {"status": 401, "headers": {}, "body_kind": "json", "body": {"message": "x"}}},
                      {"name": "s2", "route": "invoke", "response": {"status": 200, "headers": {}, "body_kind": "json", "body": {"total": 2}}}],
            "calls": [], "tables_after": {}, **extra}


def _unit(rulings: list[dict], **extra) -> dict:
    return judge.judge_unit(_Facts(), _template(), _rec(**extra), _ctx(), {"tables": []}, [], rulings, set())


def test_an_unawaited_write_fails_the_unit():
    r = _unit([{"category": "migration-change", "id": "R", "_steps": None}], calls=[{"op": "PutItem", "table": "T", "after_return": True}])
    assert r["verdict"] == "fail" and r["unawaited"]["calls"] == [{"op": "PutItem", "table": "T"}]


# --- rulings that reach one step -----------------------------------------------------


def test_a_ruling_on_a_step_leaves_the_other_steps_to_the_judge():
    """The reason for "case#step": a ruling on the whole case would hide s2's real difference."""
    whole = _unit([{"category": "migration-change", "id": "R-1", "_steps": None}])
    assert whole["verdict"] == "migration-change" and whole["steps"]["s2"]["verdict"] == "migration-change"
    one = _unit([{"category": "migration-change", "id": "R-1", "_steps": {"s1"}}])
    assert one["steps"]["s1"]["verdict"] == "migration-change" and one["steps"]["s2"]["verdict"] == "fail"
    assert one["verdict"] == "fail"


def test_rulings_in_force_read_steps(tmp_path: Path):
    (tmp_path / "storage").mkdir()
    entries = [{"id": "R-1", "status": "decided", "affects": {"cases": ["a#s1", "a#s2", "b"]}},
               {"id": "R-2", "status": "proposed", "affects": {"cases": ["c"]}},
               {"id": "R-3", "status": "confirmed", "affects": {"cases": ["d#s1", "d"]}}]
    (tmp_path / "storage" / "rulings.json").write_text(json.dumps({"rulings": entries}), encoding="utf-8")
    found = judge._rulings_in_force(tmp_path)
    assert found["a"][0]["_steps"] == {"s1", "s2"} and found["b"][0]["_steps"] is None
    assert "c" not in found  # waiting for a person: not in force
    assert found["d"][0]["_steps"] is None  # the whole case wins
    assert judge._rulings_by_case(tmp_path)["a"] == [{"id": "R-1", "status": "decided"}]


# --- V2P-23 -----------------------------------------------------------------------


def _inventory_ws(tmp_path: Path, decision: dict, judge_cases: list[str] | None = ("c1",)) -> Path:
    ws = tmp_path / "ws"
    (ws / "storage").mkdir(parents=True)
    (ws / "storage" / "analysis_report.json").write_text(json.dumps({"entry_points": []}), encoding="utf-8")
    (ws / "storage" / "DECISIONS.md").write_text("## GAP-1", encoding="utf-8")
    (ws / "storage" / "gap_inventory.json").write_text(json.dumps({"version": 1, "gaps": [
        {"id": "GAP-1", "category": "time", "summary": "s", "source": "G1", "status": "decided",
         "evidence": [{"file": "a.js", "line": 1, "symbol": "x"}], "decision": {"choice": "c", "why": "w", **decision}}]}), encoding="utf-8")
    if judge_cases is not None:
        for c in judge_cases:
            p = ws / "judge" / "cases" / "get-x" / f"{c}.json"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps({"id": c}), encoding="utf-8")
    return ws


@pytest.mark.parametrize("decision,cases,message", [
    ({"test": "judge"}, ("c1",), "must list the judge cases"),
    ({"test": "judge", "judge_cases": []}, ("c1",), "must list the judge cases"),
    ({"test": "judge", "judge_cases": ["c9"]}, ("c1",), "'c9', which is not a judge case"),
    ({"test": "judge", "judge_cases": ["c1"]}, None, "has no judge"),
])
def test_a_judge_decision_has_to_name_cases_that_exist(tmp_path: Path, decision, cases, message):
    r = gaps.check_inventory(_inventory_ws(tmp_path, decision, cases), require_decided=True)
    assert r["status"] == "FAIL" and any(message in e for e in r["errors"]), r["errors"]


@pytest.mark.parametrize("decision", [{"test": "judge", "judge_cases": ["c1"]}, {"test": "none"}, {"test": "delta"}])
def test_decisions_that_pass(tmp_path: Path, decision):
    r = gaps.check_inventory(_inventory_ws(tmp_path, decision), require_decided=True)
    assert r["status"] == "PASS", r["errors"]


# --- I1's self test: the model from the blueprint --------------------------------------

BLUEPRINT = {
    "metadata": {"verification_contract": 2},
    "api_gateway": {"api_type": "rest"},
    "dynamodb_tables": [
        {"logical_name": "BooksTable", "partition_key": {"name": "id", "type": "S"},
         "gsi": [{"index_name": "category-index", "partition_key": {"name": "category", "type": "S"}, "projection": "ALL"}]},
        {"logical_name": "CartTable", "partition_key": {"name": "customerId", "type": "S"}, "sort_key": {"name": "bookId", "type": "S"}}],
    "sqs_queues": [{"logical_name": "UpdateQueue", "dlq_logical_name": "UpdateDLQ", "max_receive_count": 3, "batch_size": 10}],
    "lambda_functions": [
        {"name": "book-get", "trigger_type": "api", "runtime": "nodejs22.x", "handler": "index.handler", "auth": "required",
         "entry_points": ["GET /api/books/{id}"], "environment_variables": {"BOOKS_TABLE": "${BooksTable}", "QUEUE_URL": "${UpdateQueue}", "STAGE": "dev"}},
        {"name": "update", "trigger_type": "sqs", "sqs_source": "UpdateQueue", "runtime": "python3.12", "handler": "index.handler", "auth": "none",
         "trigger_config": {"batch_size": 5, "report_batch_item_failures": True}, "environment_variables": {}},
        {"name": "not-written-yet", "trigger_type": "api", "runtime": "nodejs22.x", "handler": "index.handler", "auth": "none", "entry_points": ["GET /x"]}],
}


def _blueprint_ws(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    (ws / "storage").mkdir(parents=True)
    (ws / "storage" / "blueprint.json").write_text(json.dumps(BLUEPRINT), encoding="utf-8")
    (ws / "storage" / "analysis_report.json").write_text(json.dumps({"entry_points": []}), encoding="utf-8")
    for rel, text in {"output/lambdas/books/book-get/index.js": "exports.handler = async () => ({statusCode: 200});",
                      "output/lambdas/updates/update/handler.py": "def lambda_handler(event, context):\n    return {}\n",
                      "output/layers/shared/nodejs/shared.js": "module.exports = {};"}.items():
        path = ws / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return ws


def test_blueprint_model_serves_what_has_code(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(verify, "TMP_ROOT", tmp_path / "tmp")
    m = verify.BlueprintModel(_blueprint_ws(tmp_path))
    assert sorted(m.functions) == ["book-get", "update"]  # not-written-yet has no code
    fn = m.functions["book-get"]
    assert fn["api"] == [{"path": "/api/books/{id}", "method": "GET", "authorized": True, "http_api": False}]
    assert fn["env"]["BOOKS_TABLE"] == "BooksTable" and fn["env"]["STAGE"] == "dev"
    assert fn["env"]["QUEUE_URL"].endswith("/UpdateQueue") and fn["handler"] == "index.handler" and fn["layers"] == ["SharedLayer"]
    assert m.functions["update"]["handler"] == "handler.lambda_handler"  # the file decides, not the placeholder
    assert m.functions["update"]["sqs_events"] == [{"queue": "UpdateQueue", "batch_size": 5, "report_failures": True}]
    create = m.tables["CartTable"]["create"]
    assert create["KeySchema"] == [{"AttributeName": "customerId", "KeyType": "HASH"}, {"AttributeName": "bookId", "KeyType": "RANGE"}]
    books = m.tables["BooksTable"]["create"]
    assert books["GlobalSecondaryIndexes"][0]["IndexName"] == "category-index"
    assert {a["AttributeName"] for a in books["AttributeDefinitions"]} == {"id", "category"}
    assert m.queues == {"UpdateQueue", "UpdateDLQ"} and m.queue_max_receive["UpdateQueue"] == 3
    assert verify.find_route(m, "GET", "/api/books/b1")[0] == "book-get" and verify.find_route(m, "GET", "/x") is None
    assert (m.build_dir / "book-get" / "index.js").is_file() and (m.build_dir / "SharedLayer" / "nodejs" / "shared.js").is_file()


def test_blueprint_model_reuses_a_build_until_the_code_changes(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(verify, "TMP_ROOT", tmp_path / "tmp")
    ws = _blueprint_ws(tmp_path)
    first = verify.BlueprintModel(ws).build_dir
    assert verify.BlueprintModel(ws).build_dir == first
    (ws / "output" / "lambdas" / "books" / "book-get" / "index.js").write_text("exports.handler = async () => null;", encoding="utf-8")
    assert verify.BlueprintModel(ws).build_dir != first


# --- N5.3: a changed line either suite executed is covered -------------------------------


def _side(total: int, *uncovered: tuple[str, int | None]) -> dict:
    return {"changed_executable_lines": total, "covered": total - len(uncovered),
            "uncovered": [{"file": f, "line": ln, "code": "x"} for f, ln in uncovered]}


def test_merge_changed_coverage():
    both = verify.merge_changed_coverage([_side(3, ("a.js", 1), ("a.js", 2)), _side(3, ("a.js", 2), ("a.js", 3))])
    assert [u["line"] for u in both["uncovered"]] == [2] and both["covered"] == 2
    # a suite that never loaded the file says nothing about its lines
    loaded = verify.merge_changed_coverage([_side(2, ("a.js", None)), _side(2, ("a.js", 5))])
    assert [u["line"] for u in loaded["uncovered"]] == [5]
    neither = verify.merge_changed_coverage([_side(2, ("a.js", None)), _side(2, ("a.js", None))])
    assert [u["line"] for u in neither["uncovered"]] == [None]
    assert verify.merge_changed_coverage([_side(2)])["uncovered"] == []


# --- gate TOOLS --------------------------------------------------------------------


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "m2s-mcp-server" / "agents").mkdir(parents=True)
    (repo / "m2s-mcp-server" / "agents" / "m2s-a.md").write_text("role", encoding="utf-8")
    (repo / "m2s-mcp-server" / "gates.py").write_text("x", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "x"], check=True)
    return repo


def test_tools_check_exempts_the_evidence_store(tmp_path: Path):
    repo = _repo(tmp_path)
    (repo / "m2s-mcp-server" / "evidence" / "app" / "run").mkdir(parents=True)
    (repo / "m2s-mcp-server" / "evidence" / "app" / "run" / "x.json").write_text("{}", encoding="utf-8")
    assert gates.check_tools_unmodified(repo, tmp_path / "none") == []
    (repo / "m2s-mcp-server" / "new.py").write_text("x", encoding="utf-8")
    assert [f["check_id"] for f in gates.check_tools_unmodified(repo, tmp_path / "none")] == ["TOOLS"]


def test_tools_check_on_a_worktree_ignores_the_deployed_role_docs(tmp_path: Path):
    repo = _repo(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    (home / "m2s-a.md").write_text("an older role", encoding="utf-8")
    assert [f["check_id"] for f in gates.check_tools_unmodified(repo, home)] == ["TOOLS"]  # the main checkout: they must match
    worktree = tmp_path / "wt"
    subprocess.run(["git", "-C", str(repo), "worktree", "add", "-q", str(worktree), "HEAD"], check=True)
    assert gates.check_tools_unmodified(worktree, home) == []


def test_tool_guard_ignores_the_evidence_store(tmp_path: Path):
    from coord.checks import ToolGuard

    repo = _repo(tmp_path)
    guard = ToolGuard(repo=repo, agents_dir=tmp_path / "none")
    guard.capture()
    (repo / "m2s-mcp-server" / "evidence" / "app").mkdir(parents=True)
    (repo / "m2s-mcp-server" / "evidence" / "app" / "x.json").write_text("{}", encoding="utf-8")
    assert guard.violations() == {"repo_new": [], "repo_changed": [], "agents": []}


# --- what the optimizer's checks do with no delta test ----------------------------------


def test_no_delta_test_for_a_function_is_not_a_failure(tmp_path: Path):
    ws = tmp_path / "ws"
    (ws / "storage").mkdir(parents=True)
    bp = {"lambda_functions": [{"name": "a", "trigger_type": "api", "entry_points": ["GET /a"]}, {"name": "b", "trigger_type": "api", "entry_points": ["GET /b"]}],
          "dynamodb_tables": [], "sqs_queues": [], "dropped_functions": []}
    (ws / "storage" / "blueprint.json").write_text(json.dumps(bp), encoding="utf-8")
    (ws / "storage" / "analysis_report.json").write_text(json.dumps({"entry_points": []}), encoding="utf-8")
    (ws / "output").mkdir()
    (ws / "output" / "template.yaml").write_text("Resources: {}\n", encoding="utf-8")
    case = {"id": "c1", "function": "a", "kind": "boundary", "spec_refs": ["blueprint:x"], "source": "rule:r", "expected_from": "rulebook:r",
            "request": {"method": "GET", "path": "/a", "identity": None}, "expect": {"status": 200}}
    p = ws / "verification" / "delta" / "events" / "a" / "c1.json"
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps(case), encoding="utf-8")
    r = verify.run_validate(str(ws), functions=["b"], assets=verify.DELTA_ASSETS)
    assert r["status"] == "PASS" and r["total"] == 0
    assert verify.run_equivalence(str(ws), mode="diff", functions=["b"], assets=verify.DELTA_ASSETS)["status"] == "NOTHING_TO_COMPARE"
