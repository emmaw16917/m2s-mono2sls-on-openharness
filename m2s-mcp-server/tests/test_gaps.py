"""The gap inventory and the delta tests' checks (PIPELINE_V2_PLAN.md §4.11, §4.12, N4.1-N4.5)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import gaps  # noqa: E402
import gates  # noqa: E402
import runtime_signals  # noqa: E402
import verify  # noqa: E402

# the runs live in the main checkout; a worktree of the tools sits next to it
_REPO = Path(__file__).resolve().parents[2]
J2_FACTS = next((f for r in (_REPO, _REPO.parent / "Mono2SLs")
                 if (f := r / "Mono2Sls-artifacts" / "M2S" / "runs" / "bookstore-j2" / "storage" / "analysis_report.json").is_file()), None)


def express_app(root: Path) -> Path:
    app = root / "app"
    (app / "routes").mkdir(parents=True)
    (app / "package.json").write_text('{"name": "app"}\n', encoding="utf-8")
    (app / "routes" / "orders.js").write_text(
        "const fs = require('fs');\n"
        "let counter = 0;\n"
        "const seen = new Map();\n"
        "router.get('/a', async (req, res) => {\n"
        "  const now = Date.now();\n"
        "  const r = await docClient.scan({TableName: 'T'}).promise();\n"
        "  fs.writeFileSync('/tmp/x', 'y');\n"
        "  res.json(r.Items.sort((a, b) => a.n - b.n));\n"
        "});\n"
        "router.get('/b', async (req, res) => {\n"
        "  let key;\n"
        "  do {\n"
        "    const r = await docClient.scan({TableName: 'T', ExclusiveStartKey: key}).promise();\n"
        "    key = r.LastEvaluatedKey;\n"
        "  } while (key);\n"
        "  setInterval(() => {}, 1000);\n"
        "  res.json([]);\n"
        "});\n", encoding="utf-8")
    return app


def flask_app(root: Path) -> Path:
    app = root / "pyapp"
    app.mkdir(parents=True)
    (app / "app.py").write_text(
        "import time\n"
        "import threading\n"
        "from flask import Flask, jsonify, session\n"
        "app = Flask(__name__)\n"
        "_cache = {}\n"
        "COUNT = 0\n"
        "@app.route('/a')\n"
        "def a():\n"
        "    global COUNT\n"
        "    COUNT += 1\n"
        "    threading.Thread(target=print).start()\n"
        "    session['x'] = time.time()\n"
        "    return jsonify(table.scan()['Items'])\n", encoding="utf-8")
    return app


def test_signals_on_express(tmp_path: Path):
    s = runtime_signals.scan(express_app(tmp_path))
    scans = s["scans"]
    assert [x["paginates"] for x in scans] == [False, True]
    assert {x["kind"] for x in s["process_state"]} >= {"module_variable", "module_collection"}
    assert {x["kind"] for x in s["time_and_order"]} >= {"now", "sort", "timer"}
    assert {x["kind"] for x in s["files_and_connections"]} >= {"fs_write"}
    for items in s.values():
        for item in items:
            src = item["source"][0]
            assert src["file"].startswith("app/") and isinstance(src["line"], int) and src["symbol"] and item["origin"] == "script"


def test_signals_on_flask(tmp_path: Path):
    s = runtime_signals.scan(flask_app(tmp_path))
    kinds = {x["kind"] for x in s["process_state"]}
    assert {"module_collection", "global_statement", "session"} <= kinds or {"module_collection", "session"} <= kinds
    assert s["scans"] and s["scans"][0]["paginates"] is False
    assert any(x["kind"] == "now" for x in s["time_and_order"])
    assert s["background"]


def test_analyze_adds_signals_and_changes_nothing_else(tmp_path: Path, monkeypatch):
    import wrap_m2s

    app = express_app(tmp_path / "mono")
    ws = tmp_path / "ws"
    wrap_m2s.analyze(str(app), str(ws))
    with_signals = json.loads((ws / "storage" / "analysis_report.json").read_text(encoding="utf-8"))
    assert with_signals["runtime_signals"]["scans"]
    monkeypatch.setattr(runtime_signals, "scan", lambda root: {})
    wrap_m2s.analyze(str(app), str(ws))
    without = json.loads((ws / "storage" / "analysis_report.json").read_text(encoding="utf-8"))
    with_signals.pop("runtime_signals")
    without.pop("runtime_signals")
    assert with_signals == without


# --- the inventory ----------------------------------------------------------------


def report_ws(tmp_path: Path) -> Path:
    """A small fact base over a small monolith: a write-twice endpoint, a global middleware."""
    app = express_app(tmp_path / "mono")
    ws = tmp_path / "ws"
    (ws / "storage").mkdir(parents=True)
    report = {
        "spec_meta": {"monolith_root": str(app)},
        "entry_points": [{"method": "POST", "path": "/a", "handler_location": {"file": "app/routes/orders.js", "start_line": 4, "end_line": 9},
                          "auth": {"classification": "none"},
                          "data_access": [{"operation": "update", "table": {"default_name": "T"}, "source": [{"file": "app/routes/orders.js", "line": 6, "symbol": "scan"}]},
                                          {"operation": "put", "table": {"default_name": "U"}, "source": [{"file": "app/routes/orders.js", "line": 6, "symbol": "scan"}]}]}],
        "global_middleware": [{"kind": "cors", "framework": "express", "headers": {"Access-Control-Allow-Origin": "*"},
                               "source": [{"file": "app/routes/orders.js", "line": 1, "symbol": "require"}]}],
        "runtime_signals": runtime_signals.scan(app),
    }
    (ws / "storage" / "analysis_report.json").write_text(json.dumps(report), encoding="utf-8")
    return ws


def decide(ws: Path, test: str = "none", mention: bool = True) -> dict:
    data = gaps.load(ws)
    for g in data["gaps"]:
        g["status"] = "decided"
        g["decision"] = {"choice": "按默认做法", "why": "规则手册", "test": test}
    gaps.inventory_path(ws).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    if mention:
        (ws / "storage" / "DECISIONS.md").write_text("\n".join(f"## {g['id']}" for g in data["gaps"]), encoding="utf-8")
    return data


def test_gap_scan_lists_the_categories(tmp_path: Path):
    ws = report_ws(tmp_path)
    r = gaps.gap_scan(ws)
    cats = r["by_category"]
    assert {"multi_write", "process_state", "time", "order_and_consistency", "files_and_connections", "global_middleware",
            "monolith_defect"} <= set(cats)
    data = gaps.load(ws)
    assert all(g["status"] == "open" and g["source"] == "G1" and g["evidence"] for g in data["gaps"])
    assert gaps.check_inventory(ws)["status"] == "PASS"


def test_gap_scan_again_keeps_ids_and_decisions(tmp_path: Path):
    ws = report_ws(tmp_path)
    gaps.gap_scan(ws)
    before = decide(ws)
    r = gaps.gap_scan(ws)
    after = gaps.load(ws)
    assert r["added"] == 0 and r["open"] == 0
    assert [(g["id"], g["decision"]) for g in before["gaps"]] == [(g["id"], g["decision"]) for g in after["gaps"]]


def test_gap_scan_marks_a_vanished_signal_and_leaves_g2_alone(tmp_path: Path):
    ws = report_ws(tmp_path)
    gaps.gap_scan(ws)
    data = gaps.load(ws)
    data["gaps"].append({"id": "GAP-99", "key": "g2:x", "category": "new", "candidate_name": "连接池", "summary": "s",
                         "evidence": [{"file": "app/routes/orders.js", "line": 1, "symbol": "require"}], "endpoints": [],
                         "source": "G2", "status": "open"})
    gaps.inventory_path(ws).write_text(json.dumps(data), encoding="utf-8")
    report = json.loads((ws / "storage" / "analysis_report.json").read_text(encoding="utf-8"))
    report["global_middleware"] = []
    (ws / "storage" / "analysis_report.json").write_text(json.dumps(report), encoding="utf-8")
    gaps.gap_scan(ws)
    after = {g["key"]: g for g in gaps.load(ws)["gaps"]}
    assert after["global_middleware:cors:0"]["no_longer_detected"] is True
    assert after["g2:x"]["id"] == "GAP-99" and "no_longer_detected" not in after["g2:x"]


def test_gap_scan_does_not_touch_the_fact_base(tmp_path: Path):
    ws = report_ws(tmp_path)
    report_path = ws / "storage" / "analysis_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report.pop("runtime_signals")
    report_path.write_text(json.dumps(report), encoding="utf-8")
    before = report_path.read_bytes()
    assert gaps.gap_scan(ws)["by_category"]["process_state"] >= 1  # read from the monolith's code
    assert report_path.read_bytes() == before


@pytest.mark.skipif(J2_FACTS is None, reason="needs the bookstore-j2 fact base")
def test_gap_scan_finds_the_known_gaps_of_bookstore(tmp_path: Path):
    """N4.2's acceptance, against the frozen fact base of the bookstore run when present."""
    ws = tmp_path / "ws"
    (ws / "storage").mkdir(parents=True)
    (ws / "storage" / "analysis_report.json").write_bytes(J2_FACTS.read_bytes())
    gaps.gap_scan(ws)
    data = gaps.load(ws)["gaps"]
    assert any(g["category"] == "fire_and_forget" and "POST /api/orders" in g["endpoints"] for g in data)
    assert any(g["category"] == "global_middleware" for g in data)
    assert any(g["category"] == "auth_semantics" and "令牌" in g["summary"] for g in data)
    assert gaps.check_inventory(ws)["status"] == "PASS"


@pytest.mark.parametrize("change,message", [
    (lambda g: g.update(category="nope"), "category must be"),
    (lambda g: g.update(evidence=[{"file": "app/routes/orders.js", "line": 1, "symbol": "not on that line"}]), "does not mention"),
    (lambda g: g.update(evidence=[{"file": "app/none.js", "line": 1, "symbol": "x"}]), "not a file of the monolith"),
    (lambda g: g.update(endpoints=["GET /nope"]), "not in the fact base"),
    (lambda g: g.update(extra=1), "unknown keys"),
    (lambda g: g.update(status="decided"), "needs decision"),
    (lambda g: g.update(status="decided", decision={"choice": "a", "why": "b", "test": "maybe"}), "decision.test"),
    (lambda g: g.update(category="new"), "candidate_name"),
])
def test_check_inventory_catches(tmp_path: Path, change, message: str):
    ws = report_ws(tmp_path)
    gaps.gap_scan(ws)
    data = gaps.load(ws)
    change(data["gaps"][0])
    gaps.inventory_path(ws).write_text(json.dumps(data), encoding="utf-8")
    r = gaps.check_inventory(ws)
    assert r["status"] == "FAIL" and any(message in e for e in r["errors"])


def test_a2_fails_while_a_gap_is_undecided(tmp_path: Path):
    """N4.3's acceptance: gate(blueprint)'s AG."""
    ws = report_ws(tmp_path)
    assert gates.check_gaps(ws) == []  # no inventory, no check
    gaps.gap_scan(ws)
    findings = gates.check_gaps(ws)
    assert findings and all(f["check_id"] == "AG" and f["severity"] == "ERROR" for f in findings)
    assert "have no decision" in findings[-1]["description"]
    decide(ws, mention=False)
    assert any("does not mention" in f["description"] for f in gates.check_gaps(ws))
    decide(ws)
    assert gates.check_gaps(ws) == []


# --- delta tests: same format, their own directory ---------------------------------

BLUEPRINT = {
    "metadata": {"verification_contract": 2},
    "lambda_functions": [{"name": "books-get", "trigger_type": "api", "auth": "none", "entry_points": ["GET /api/books/:id"]}],
    "dynamodb_tables": [{"logical_name": "Books", "partition_key": "id"}], "sqs_queues": [], "dropped_functions": [],
}


def delta_ws(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    (ws / "storage").mkdir(parents=True)
    (ws / "judge").mkdir()
    (ws / "storage" / "blueprint.json").write_text(json.dumps(BLUEPRINT), encoding="utf-8")
    (ws / "storage" / "analysis_report.json").write_text(json.dumps({"entry_points": []}), encoding="utf-8")
    (ws / "judge" / "oracle_expected.json").write_text(json.dumps({"cases": {"j1": {"steps": {"s1": {"status": 200}}}}}), encoding="utf-8")
    (ws / "storage" / "rulings.json").write_text(json.dumps({"rulings": [{"id": "R-1"}]}), encoding="utf-8")
    (ws / "storage" / "gap_inventory.json").write_text(json.dumps({"version": 1, "gaps": [
        {"id": "GAP-1", "category": "time", "status": "decided", "decision": {"choice": "c", "why": "w", "test": "delta"}},
        {"id": "GAP-2", "category": "time", "status": "decided", "decision": {"choice": "c", "why": "w", "test": "judge"}}]}), encoding="utf-8")
    return ws


def write_case(ws: Path, name: str = "c1", **kw) -> None:
    case = {"id": name, "function": "books-get", "kind": "boundary", "spec_refs": ["blueprint:x"], "source": "GAP-1",
            "expected_from": "monolith:j1/s1", "request": {"method": "GET", "path": "/api/books/b1", "identity": None},
            "expect": {"status": 200}, **kw}
    case = {k: v for k, v in case.items() if v is not None}
    p = ws / "verification" / "delta" / "events" / "books-get" / f"{name}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(case), encoding="utf-8")


def dry(ws: Path) -> dict:
    return verify.run_validate(str(ws), dry_run=True, assets=verify.DELTA_ASSETS)


def test_delta_tests_pass_the_dry_run(tmp_path: Path):
    ws = delta_ws(tmp_path)
    write_case(ws)
    r = dry(ws)
    assert r["status"] == "PASS", r["schema_errors"]
    assert r["cases"] == 1


def test_delta_directory_is_separate_from_v1_assets(tmp_path: Path):
    ws = delta_ws(tmp_path)
    write_case(ws)
    assert [c["id"] for c in verify.load_cases(ws, verify.DELTA_ASSETS)] == ["c1"]
    assert verify.load_cases(ws) == []
    with pytest.raises(verify.VerifyError, match="no test cases"):
        verify.run_validate(str(ws), dry_run=True)
    with pytest.raises(verify.VerifyError, match="under verification"):
        verify.load_cases(ws, "../judge")


@pytest.mark.parametrize("change,message", [
    ({"source": None}, "source must be"),
    ({"source": "whatever"}, "source must be"),
    ({"source": "GAP-7"}, "GAP-7 is not in"),
    ({"expected_from": None}, "expected_from must be"),
    ({"expected_from": "guess:x"}, "expected_from must be"),
    ({"expected_from": "monolith:j1/nope"}, "no case/step"),
    ({"expected_from": "monolith:nope/s1"}, "no case/step"),
    ({"expected_from": "ruling:R-9"}, "no such ruling"),
    ({"expected_from": "decision:GAP-9"}, "no such gap"),
    ({"expect": {"status": 404}}, "the monolith answered 200"),
])
def test_delta_case_checks(tmp_path: Path, change: dict, message: str):
    ws = delta_ws(tmp_path)
    write_case(ws, source="GAP-1")
    write_case(ws, "c2", **change)
    r = dry(ws)
    assert r["status"] == "FAIL" and any(message in e for e in r["schema_errors"]), r["schema_errors"]


@pytest.mark.parametrize("expected_from", ["ruling:R-1", "decision:GAP-1", "rulebook:消费者幂等", "monolith:j1/s1"])
def test_delta_expectations_may_come_from(tmp_path: Path, expected_from: str):
    ws = delta_ws(tmp_path)
    write_case(ws, expected_from=expected_from)
    assert dry(ws)["status"] == "PASS"


def test_every_gap_with_test_delta_needs_a_case(tmp_path: Path):
    ws = delta_ws(tmp_path)
    write_case(ws, source="rule:消费者幂等", expected_from="rulebook:消费者幂等")
    r = dry(ws)
    assert r["status"] == "FAIL" and any("GAP-1" in e and "no delta test cites" in e for e in r["schema_errors"])
    assert not any("GAP-2" in e for e in r["schema_errors"])  # test: judge
    write_case(ws, "c2", source=["GAP-1", "rule:x"])
    assert dry(ws)["status"] == "PASS"


def test_v1_runs_ignore_the_delta_fields_and_keep_gm3(tmp_path: Path):
    """Without assets nothing changes: the extra keys are just allowed."""
    ws = delta_ws(tmp_path)
    case = {"id": "c1", "function": "books-get", "kind": "happy", "spec_refs": ["blueprint:x"],
            "request": {"method": "GET", "path": "/api/books/b1", "identity": None}, "expect": {"status": 200}}
    p = ws / "verification" / "events" / "books-get" / "c1.json"
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps(case), encoding="utf-8")
    assert verify.run_validate(str(ws), dry_run=True)["status"] == "PASS"
    report = {"entry_points": [], "global_middleware": [{"kind": "cors", "headers": {"Access-Control-Allow-Origin": "*"}}]}
    (ws / "storage" / "analysis_report.json").write_text(json.dumps(report), encoding="utf-8")
    assert any("GM3" in e for e in verify.run_validate(str(ws), dry_run=True)["schema_errors"])
    write_case(ws)
    assert dry(ws)["status"] == "PASS"  # delta tests are not held to GM3


def test_assets_digest_changes_with_the_tests(tmp_path: Path):
    ws = delta_ws(tmp_path)
    write_case(ws)
    before = verify.assets_digest(ws, verify.DELTA_ASSETS)
    assert before == verify.assets_digest(ws, verify.DELTA_ASSETS)
    write_case(ws, title="changed")
    assert before != verify.assets_digest(ws, verify.DELTA_ASSETS)


def test_verdict_delta_check(tmp_path: Path):
    from coord.checks import verdict_delta

    ws = delta_ws(tmp_path)
    write_case(ws)
    vdir = ws / "verification"
    r = verdict_delta(ws)
    assert not r.ok and "verdict_delta.json 不存在" in r.relay and "fault_baseline_B0" in r.relay and "summary.json" in r.relay
    (vdir / "coverage").mkdir()
    (vdir / "coverage" / "summary.json").write_text("{}", encoding="utf-8")
    (vdir / "fault_baseline_B0.json").write_text("{}", encoding="utf-8")
    digest = verify.assets_digest(ws, verify.DELTA_ASSETS)
    (vdir / "verdict_delta.json").write_text(json.dumps({"status": "FAIL", "assets_digest": digest, "failed": [{"id": "c1"}]}), encoding="utf-8")
    r = verdict_delta(ws)
    assert not r.ok and "c1" in r.relay
    (vdir / "verdict_delta.json").write_text(json.dumps({"status": "PASS", "assets_digest": digest, "passed": 1, "total": 1}), encoding="utf-8")
    assert verdict_delta(ws).ok
    write_case(ws, title="changed")
    r = verdict_delta(ws)
    assert not r.ok and "改过" in r.relay
