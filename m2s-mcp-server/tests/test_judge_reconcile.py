"""J3 reconciliation, mask rules and J6 freezing (PIPELINE_V2_PLAN.md §4.5, §4.7; N1.5, N1.6)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import gates  # noqa: E402
import judge  # noqa: E402

from test_judge_assets import CASES, make_ws  # noqa: E402

STEP = {"status": 200, "headers": {"content-type": "application/json", "access-control-allow-origin": "*"},
        "body_kind": "json", "body": {"orderId": "<uuid#1>", "total": "<v#2>", "items": [{"bookId": "b1", "qty": 2}]}}


def test_compare_step_reports_each_failed_prediction():
    pred = {"status": 201,
            "headers": {"equals": {"Access-Control-Allow-Origin": "*", "Vary": "Origin"}, "absent": ["Content-Type"]},
            "body": {"subset": {"orderId": "o-1"}, "has_fields": ["orderId", "missing"], "types": {"total": "number"}}}
    checks = [d["check"] for d in judge.compare_step(pred, STEP, {"<v#2>": "number"})]
    assert checks == ["status", "headers.equals.vary", "headers.absent.content-type", "body.subset",
                      "body.has_fields.missing"]
    listed = {**STEP, "body": STEP["body"]["items"]}
    checks = [d["check"] for d in judge.compare_step(
        {"status": 200, "body": {"contains": [{"bookId": "b1"}, {"bookId": "b9"}], "length": 2}}, listed)]
    assert checks == ["body.contains[1]", "body.length"]
    # the symbol stands for a number: types.total holds
    assert judge.compare_step({"status": 200, "body": {"types": {"total": "string"}}}, STEP, {"<v#2>": "number"})[0][
        "actual"] == "number"
    assert judge.compare_step({"status": 200}, {"error": "ECONNREFUSED"})[0]["check"] == "request"
    assert judge.compare_step({"status": 200, "body": {"has_fields": ["orderId"]}}, STEP) == []


def _oracle(ws: Path, cases: dict) -> None:
    """An oracle in which every step answers its predicted status, except where `cases` says otherwise."""
    out = {}
    for c in judge.load_assets(ws)["cases"]:
        steps = {}
        for s in c["steps"]:
            status = cases.get((c["id"], s["name"]), c["predicted"][s["name"]]["status"])
            steps[s["name"]] = {"status": status, "headers": {}, "body_kind": "json", "body": []}
        out[c["id"]] = {"steps": steps, "messages": [], "rules_fired": [], "tables_after": {}}
    judge._write(ws / "judge" / "oracle_expected.json",
                 {"meta": {"backend": "dynalite", "inputs_sha": judge.inputs_sha(ws)}, "cases": out})


def test_reconcile_writes_differences_with_stable_ids(tmp_path: Path):
    ws = make_ws(tmp_path)
    _oracle(ws, {("cart-errors", "missing"): 200})
    (ws / "judge" / "reconcile_classes.json").write_text("{}", encoding="utf-8")
    out = judge.reconcile(ws)
    # "read" of cart-empty predicts equals [] and the fake oracle body is [] -> no difference
    assert out["status"] == "DIFFERENCES" and out["differences"] == 2 and out["by_check"]["status"] == 1
    rec = json.loads((ws / "judge" / "reconcile.json").read_text(encoding="utf-8"))
    ids = [d["id"] for d in rec["differences"]]
    assert "cart-errors/missing/status" in ids and "cart-add-then-read/read/body.contains[0]" in ids
    d = next(d for d in rec["differences"] if d["id"] == "cart-errors/missing/status")
    assert (d["predicted"], d["actual"], d["request"]) == (400, 200, {"method": "POST", "path": "/api/cart"})
    assert not (ws / "judge" / "reconcile_classes.json").exists()  # classified afresh each time


def test_rulings_settle_differences(tmp_path: Path):
    ws = make_ws(tmp_path)
    _oracle(ws, {("cart-errors", "missing"): 200})
    (ws / "storage" / "rulings.json").write_text(json.dumps({"rulings": [
        {"id": "R-1", "status": "decided", "affects": {"cases": ["cart-errors"]}},
        {"id": "R-2", "status": "rejected", "affects": {"cases": ["cart-add-then-read"]}}]}), encoding="utf-8")
    out = judge.reconcile(ws)
    assert (out["differences"], out["resolved_by_rulings"]) == (1, 1)
    rec = json.loads((ws / "judge" / "reconcile.json").read_text(encoding="utf-8"))
    assert rec["resolved_by_rulings"][0]["rulings"] == [{"id": "R-1", "status": "decided"}]


def test_classification_check(tmp_path: Path):
    ws = make_ws(tmp_path)
    _oracle(ws, {("cart-errors", "missing"): 200})
    judge.reconcile(ws)
    a, b = "cart-errors/missing/status", "cart-add-then-read/read/body.contains[0]"
    write = lambda classes: (ws / "judge" / "reconcile_classes.json").write_text(json.dumps(classes), encoding="utf-8")
    assert "not classified" in " ".join(judge.check_reconcile_classified(ws)["errors"])
    write({a: {"class": "fact_error"}, b: {"class": "typo", "reason": "x"}, "ghost": {"class": "case_error", "reason": "x"}})
    errs = " ".join(judge.check_reconcile_classified(ws)["errors"])
    assert "reason is required" in errs and "class must be one of" in errs and "ghost: no such difference" in errs
    write({a: {"class": "fact_error", "reason": "the monolith accepts it"}, b: {"class": "mask_gap", "reason": "y"}})
    assert "judge/mask_rules.json" in " ".join(judge.check_reconcile_classified(ws)["errors"])
    (ws / "judge" / "mask_rules.json").write_text(json.dumps([{"path": "steps.read.body", "reason": "r"}]), encoding="utf-8")
    out = judge.check_reconcile_classified(ws)
    assert out["status"] == "PASS" and out["classes"] == {"fact_error": 1, "needs_ruling": 0, "case_error": 0, "mask_gap": 1}


def test_mask_rules_force_symbols_where_both_passes_agree():
    rules = [{"path": "steps.*.body.date", "class": "time", "reason": "today"}, {"path": "steps.x.body.n", "cases": ["other"]}]
    facts = type("F", (), {"tables": {}})()
    run = lambda d: {"id": "c", "identities": {}, "steps": [{"name": "x", "response": {
        "status": 200, "headers": {}, "body_kind": "json", "body": {"date": "2026-11-01", "n": 1, "id": d}}}], "tables": {}}
    m = judge.mask_unit(facts, [run("a"), run("b"), run("c")], rules, case_id="c")
    assert m["template"]["steps"]["x"]["body"] == {"date": "<time#1>", "n": 1, "id": "<v#2>"}
    assert m["checks"][0]["mismatches"] == []
    assert judge._check_mask_rules([{"path": "", "class": "weird", "cases": ["nope"]}], {"c"}) and \
        judge._check_mask_rules([{"path": "a.*", "reason": "r", "cases": ["c"]}], {"c"}) == []


def test_freeze_needs_a_ready_judge_and_then_holds_it(tmp_path: Path):
    ws = make_ws(tmp_path)
    out = judge.freeze_judge(ws)
    assert out["status"] == "FAIL" and "oracle_expected.json missing" in " ".join(out["problems"])
    _oracle(ws, {("cart-errors", "missing"): 200})
    assert "differences with the fact base" in " ".join(judge.freeze_judge(ws)["problems"])
    _oracle(ws, {})
    case = ws / "judge" / "cases" / "post-api-cart" / "cart-add-then-read.json"
    data = json.loads(case.read_text(encoding="utf-8"))
    data["predicted"]["read"] = {"status": 200}
    case.write_text(json.dumps(data), encoding="utf-8")
    assert "run it again" in " ".join(judge.freeze_judge(ws)["problems"])  # cases changed after the oracle
    _oracle(ws, {})
    (ws / "storage" / "rulings.json").write_text(json.dumps({"rulings": [
        {"id": "R-9", "status": "proposed", "affects": {"cases": []}}]}), encoding="utf-8")
    assert "waiting for a person: ['R-9']" in " ".join(judge.freeze_judge(ws)["problems"])
    (ws / "storage" / "rulings.json").unlink()

    out = judge.freeze_judge(ws)
    assert out["status"] == "PASS" and out["version"] == 1 and out["cases"] == len(CASES)
    m = judge.manifest(ws)
    assert m["backend"] == "dynalite" and m["layers"] == {"P": 4, "R": 1} and "judge/reconcile.json" in m["files"]
    assert judge.freeze_judge(ws) == {"status": "PASS", "version": 1, "unchanged": True}
    assert judge.judge_changes(ws) == {"changed": [], "added": [], "removed": []}
    assert gates.check_judge_frozen(ws) == []

    # Windows with core.autocrlf=true checks the LF blob out as CRLF; the
    # frozen manifest hashes the text, not the checkout's line endings.
    lf = case.read_bytes()
    case.write_bytes(lf.replace(b"\n", b"\r\n"))
    assert judge.judge_changes(ws) == {"changed": [], "added": [], "removed": []}
    case.write_bytes(lf)

    case.write_text(case.read_text(encoding="utf-8").replace('"status": 200', '"status": 201', 1), encoding="utf-8")
    (ws / "judge" / "extra.json").write_text("{}", encoding="utf-8")
    assert judge.judge_changes(ws) == {"changed": ["judge/cases/post-api-cart/cart-add-then-read.json"],
                                       "added": ["judge/extra.json"], "removed": []}
    assert {f["check_id"] for f in gates.check_judge_frozen(ws)} == {"JUDGE"} and len(gates.check_judge_frozen(ws)) == 2
    assert "only a person" in judge.freeze_judge(ws)["error"]


def test_not_frozen_means_no_findings(tmp_path: Path):
    ws = make_ws(tmp_path)
    assert judge.judge_changes(ws) is None and gates.check_judge_frozen(ws) == []


def test_an_identity_id_the_same_in_every_pass_is_compared_as_its_value(tmp_path: Path):
    """A monolith that lets the client choose the user id: J1 can predict the literal,
    the oracle keeps the symbol (the target's id differs)."""
    ws = make_ws(tmp_path)
    _oracle(ws, {})
    oracle = json.loads((ws / "judge" / "oracle_expected.json").read_text(encoding="utf-8"))
    name = "read"
    oracle["cases"]["cart-add-then-read"]["steps"][name]["body"] = [{"bookId": "book-1", "customerId": "<id:u1>", "note": "for <id:u1>"}]
    judge._write(ws / "judge" / "oracle_expected.json", oracle)
    path = next(p for p in (ws / "judge" / "cases").rglob("cart-add-then-read.json"))
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["predicted"][name] = {"status": raw["predicted"][name]["status"],
                              "body": {"contains": [{"customerId": "judge-u1", "note": "for judge-u1"}]}}
    path.write_text(json.dumps(raw), encoding="utf-8")
    symbol = {"class": "identity", "first": None, "type": "string"}
    masks = {"units": {"cart-add-then-read": {"symbols": {"<id:u1>": {**symbol, "value": "judge-u1"}}}}}
    judge._write(ws / "judge" / "masks.json", masks)
    assert not [d for d in judge.reconcile(ws) and json.loads((ws / "judge" / "reconcile.json").read_text(encoding="utf-8"))["differences"]
                if d["case"] == "cart-add-then-read"]
    # a prediction may also name the symbol itself (J1 told so by a reconciliation)
    raw["predicted"][name]["body"]["contains"][0]["customerId"] = "<id:u1>"
    path.write_text(json.dumps(raw), encoding="utf-8")
    judge.reconcile(ws)
    rec = json.loads((ws / "judge" / "reconcile.json").read_text(encoding="utf-8"))
    assert not [d for d in rec["differences"] if d["case"] == "cart-add-then-read"]
    raw["predicted"][name]["body"]["contains"][0]["customerId"] = "judge-u1"
    path.write_text(json.dumps(raw), encoding="utf-8")
    masks["units"]["cart-add-then-read"]["symbols"]["<id:u1>"] = symbol  # a volatile id: no value, the literal cannot match
    judge._write(ws / "judge" / "masks.json", masks)
    judge.reconcile(ws)
    rec = json.loads((ws / "judge" / "reconcile.json").read_text(encoding="utf-8"))
    assert [d["check"] for d in rec["differences"] if d["case"] == "cart-add-then-read"] == ["body.contains[0]"]
