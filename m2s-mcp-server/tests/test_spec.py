"""Tests for spec_extract.py (P0 spec fields) and provenance.py
(check_provenance / annotate_entry_point), run against the real bookstore
monolith in mono_benchmark/."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import provenance  # noqa: E402
import wrap_m2s  # noqa: E402

BOOKSTORE = Path(__file__).resolve().parents[2] / "Mono2Sls-artifacts" / "mono_benchmark" / "bookstore"


@pytest.fixture(scope="module")
def ws(tmp_path_factory):
    ws = tmp_path_factory.mktemp("ws")
    wrap_m2s.analyze(str(BOOKSTORE), str(ws))
    return ws


def _report(ws):
    return json.loads((ws / "storage" / "analysis_report.json").read_text(encoding="utf-8"))


def _ep(report, method, path):
    return next(e for e in report["entry_points"] if e["method"] == method and e["path"] == path)


def test_analyzer_fixes_tables_and_dependencies(ws):
    report = _report(ws)
    assert report["dynamodb_info"]["probable_tables"] == [
        "Bookstore-Bestsellers", "Bookstore-Books", "Bookstore-Cart", "Bookstore-Orders", "Bookstore-Users"
    ]
    deps = {d["handler_function"] for d in report["entry_point_dependencies"]}
    assert "POST__api_orders" in deps


def test_request_required_and_types(ws):
    report = _report(ws)
    cart_post = _ep(report, "POST", "/api/cart")["request"]["body"]
    assert {f["name"]: f["required"] for f in cart_post} == {"bookId": True, "quantity": True, "price": True}
    books = _ep(report, "GET", "/api/books")["request"]
    assert books["query"][0]["name"] == "category" and books["query"][0]["required"] is False
    by_id = _ep(report, "GET", "/api/books/:id")["request"]["path_params"]
    assert by_id[0]["name"] == "id" and by_id[0]["required"] is True
    orders = _ep(report, "POST", "/api/orders")["request"]["body"][0]
    assert orders["name"] == "books" and orders["type"] == "array" and orders["required"] is True


def test_auth_classification(ws):
    report = _report(ws)
    assert _ep(report, "POST", "/api/auth/login")["auth"]["classification"] == "none"
    assert _ep(report, "GET", "/api/auth/me")["auth"]["classification"] == "required"
    assert _ep(report, "GET", "/api/cart")["auth"]["middleware"] == ["authMiddleware"]


def test_responses_and_data_access(ws):
    report = _report(ws)
    register = _ep(report, "POST", "/api/auth/register")["responses"]
    assert sorted({r["status"] for r in register}) == [201, 400, 409, 500]
    bestsellers = _ep(report, "GET", "/api/bestsellers")["responses"]
    assert any(r.get("in_catch") and r["body"]["expression"] == "[]" for r in bestsellers)
    access = _ep(report, "POST", "/api/orders")["data_access"]
    ops = [(a["operation"], a["table"]["config_key"], a["repeated"]) for a in access]
    assert ops == [("put", "ordersTable", False), ("delete", "cartTable", True), ("update", "bestsellersTable", True)]
    assert access[2]["via"] == "updateBestSellers -> updateBestSeller"


def test_table_schemas(ws):
    schemas = {t["table"]["config_key"]: t for t in _report(ws)["dynamodb_info"]["table_schemas"]}
    assert schemas["cartTable"]["partition_key"] == {"name": "customerId", "type": "S"}
    assert schemas["cartTable"]["sort_key"] == {"name": "bookId", "type": "S"}
    assert schemas["usersTable"]["gsis"][0]["index_name"] == "email-index"


def test_script_sources_all_pass_but_rules_missing(ws):
    result = provenance.check_provenance(ws)
    assert result["errors"] == []
    assert result["checked_sources"] > 100
    # business_rules not yet annotated, plus bookstore's configured cors({...}) whose headers S0 must determine
    assert len(result["incomplete"]) == 16
    assert all("business_rules" in msg or msg.startswith("global_middleware[0]") for msg in result["incomplete"])


def test_annotate_rejects_fabricated_source(ws):
    bad = [{"rule": "x", "kind": "validation", "origin": "llm",
            "source": [{"file": "bookstore/routes/orders.js", "line": 12, "symbol": "Array.isArray(books)"}]}]
    result = provenance.annotate_entry_point(ws, "POST", "/api/orders", "business_rules", json.dumps(bad))
    assert result["ok"] is False and "does not appear" in result["errors"][0]


def test_annotate_rejects_missing_file_and_escape(ws):
    for file in ("bookstore/routes/nope.js", "../M2S/src/main.py"):
        bad = [{"rule": "x", "kind": "other", "origin": "llm", "source": [{"file": file, "line": 1, "symbol": "x"}]}]
        result = provenance.annotate_entry_point(ws, "GET", "/api/cart", "business_rules", json.dumps(bad))
        assert result["ok"] is False and "does not exist" in result["errors"][0]


def test_annotate_rejects_forged_script_origin(ws):
    responses = _ep(_report(ws), "POST", "/api/orders")["responses"]
    responses[0]["status"] = 401
    result = provenance.annotate_entry_point(ws, "POST", "/api/orders", "responses", json.dumps(responses))
    assert result["ok"] is False and "origin" in result["errors"][0]


def test_annotate_accepts_and_completes(tmp_path):
    wrap_m2s.analyze(str(BOOKSTORE), str(tmp_path))
    report = _report(tmp_path)
    for ep in report["entry_points"]:
        h = ep["handler_location"]
        rules = [{"rule": "handler exists", "kind": "other", "origin": "llm",
                  "source": [{"file": h["file"], "line": h["start_line"], "symbol": "req"}]}]
        result = provenance.annotate_entry_point(tmp_path, ep["method"], ep["path"], "business_rules", json.dumps(rules))
        assert result["ok"], result
    gm = provenance.annotate_global_middleware(tmp_path, 0, json.dumps({"Access-Control-Allow-Origin": "*"}),
                                               "cors({origin: '*'})")
    assert gm["ok"], gm
    final = provenance.check_provenance(tmp_path)
    assert final["status"] == "PASS", final
    assert _ep(_report(tmp_path), "GET", "/api/cart")["spec_status"]["annotated_fields"] == ["business_rules"]


def test_annotate_unknown_endpoint(ws):
    with pytest.raises(provenance.SpecError):
        provenance.annotate_entry_point(ws, "POST", "/api/nope", "responses", "[]")


def test_business_rule_class_and_trace_arm():
    import provenance
    errors: list = []
    provenance._validate_field({}, "business_rules", [{"rule": "r", "kind": "other", "source": [], "origin": "llm", "class": "maybe"}],
                               "ep.business_rules", errors)
    assert any("'class' must be one of" in e for e in errors)


def test_global_middleware_detected_and_annotated(ws):
    gm = _report(ws)["global_middleware"]
    assert [(g["kind"], g["framework"], g["options"], g["headers"]) for g in gm] == [("cors", "express-cors", True, None)]
    bad = provenance.annotate_global_middleware(ws, 0, json.dumps({"Access-Control-Allow-Origin": 1}))
    assert bad["ok"] is False and "string values" in bad["errors"][0]
