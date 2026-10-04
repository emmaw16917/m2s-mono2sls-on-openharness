"""Tests for spec_extract_py.py (P0 spec fields of Flask entry points), run
against the real Python monoliths in mono_benchmark/ like test_spec.py does
for bookstore."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import provenance  # noqa: E402
import wrap_m2s  # noqa: E402

BENCH = Path(__file__).resolve().parents[2] / "Mono2Sls-artifacts" / "mono_benchmark"


@pytest.fixture(scope="module")
def reports(tmp_path_factory):
    out = {}
    for app in ("shopping-cart", "ecommerce", "airline-booking"):
        ws = tmp_path_factory.mktemp(app)
        wrap_m2s.analyze(str(BENCH / app), str(ws))
        out[app] = (ws, json.loads((ws / "storage" / "analysis_report.json").read_text(encoding="utf-8")))
    return out


def _ep(report, method, path):
    return next(e for e in report["entry_points"] if e["method"] == method and e["path"] == path)


def test_every_flask_endpoint_is_extracted_with_valid_citations(reports):
    for app, (ws, report) in reports.items():
        assert all(e["spec_status"]["script"] == "extracted" for e in report["entry_points"]), app
        result = provenance.check_provenance(str(ws))
        assert result["errors"] == [], (app, result["errors"][:3])
        # what is left for the annotator: business rules (and unknown items)
        assert all("business_rules" in m or "no success response" in m or m.startswith("global_middleware[") for m in result["incomplete"])


def test_request_fields_and_required_from_4xx_guards(reports):
    report = reports["shopping-cart"][1]
    body = {f["name"]: f for f in _ep(report, "POST", "/auth/register")["request"]["body"]}
    assert body["username"]["required"] is True and body["password"]["required"] is True  # if not username or not password: 400
    assert body["email"]["required"] == "unknown"
    params = _ep(report, "GET", "/product/<product_id>")["request"]["path_params"]
    assert [(p["name"], p["required"]) for p in params] == [("product_id", True)]
    body = {f["name"]: f for f in _ep(reports["ecommerce"][1], "POST", "/api/auth/register")["request"]["body"]}
    assert body["email"]["required"] is True and body["password"]["required"] is True  # 'email' not in data -> 400
    assert body["role"]["required"] is False  # data.get('role', 'user')


def test_auth_from_decorators(reports):
    report = reports["ecommerce"][1]
    assert _ep(report, "POST", "/api/auth/register")["auth"]["classification"] == "none"
    me = _ep(report, "GET", "/api/auth/me")["auth"]
    assert me["classification"] == "required" and me["middleware"] == ["jwt_required"]
    assert _ep(report, "GET", "/api/delivery/deliveries")["auth"]["middleware"] == ["delivery_required"]


def test_responses_status_and_fields(reports):
    responses = _ep(reports["shopping-cart"][1], "POST", "/auth/register")["responses"]
    assert (201, "success", ["message", "userId"]) in [(r["status"], r["kind"], r["body"]["fields"]) for r in responses]
    assert {r["status"] for r in responses if r["kind"] == "error"} == {400}


def test_data_access_follows_calls_and_resolves_tables(reports):
    # route -> models.create_user -> table.put_item; table = get_table() -> Table(TABLE_NAME)
    da = _ep(reports["shopping-cart"][1], "POST", "/auth/register")["data_access"]
    put = next(d for d in da if d["operation"] == "put_item")
    assert put["via"] == "create_user"
    assert (put["table"]["env_var"], put["table"]["default_name"]) == ("DYNAMODB_TABLE_NAME", "shopping-cart-monolith")
    # Model.method -> get_users_table() -> get_table('TABLE_USERS_NAME') -> app.config[...] -> Config
    for app in ("shopping-cart", "ecommerce", "airline-booking"):
        das = [d for e in reports[app][1]["entry_points"] for d in e["data_access"]]
        assert das and all(d["table"]["env_var"] not in (None, "unknown") for d in das), app
    # module-level instance storage = DataStorage(); self.flight_table set in __init__
    da = _ep(reports["airline-booking"][1], "GET", "/flights/<flight_id>")["data_access"]
    assert [(d["operation"], d["table"]["env_var"], d["table"]["default_name"]) for d in da] == [("get_item", "FLIGHT_TABLE_NAME", "Airline-Flight-{stage}")]


def test_table_schemas_from_create_table_calls_and_dicts(reports):
    schemas = {s["table"]["default_name"]: s for s in reports["ecommerce"][1]["dynamodb_info"]["table_schemas"]}
    wh = schemas["ecommerce-warehouse"]  # create_warehouse_table(client, table_name), names from a tuple list
    assert (wh["partition_key"]["name"], wh["sort_key"]["name"]) == ("orderId", "productId")
    assert {g["index_name"] for g in wh["gsis"]} == {"product-index", "orderId-new-index"}
    names = {s["table"]["default_name"] for s in reports["airline-booking"][1]["dynamodb_info"]["table_schemas"]}
    assert names == {"Airline-Flight-{stage}", "Airline-Booking-{stage}", "Airline-Loyalty-{stage}", "Airline-Users-{stage}"}
