"""v3 M2 (W2.4): work-package planning on the seven benchmark apps.

Expected groupings live in fixtures/work_packages/expected.json. Regenerate it
only when a rule changes on purpose: run coord.packages.write on freshly
analyzed workspaces of the seven apps and dump `{app: {package_id: [METHOD path]}}`.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import wrap_m2s  # noqa: E402
from coord import packages  # noqa: E402

BENCH = Path(__file__).resolve().parents[2] / "Mono2Sls-artifacts" / "mono_benchmark"
EXPECTED = Path(__file__).resolve().parent / "fixtures" / "work_packages" / "expected.json"
APPS = ["acmeair", "bookstore", "airline-booking", "ecommerce", "coffee", "shopping-cart", "todo"]


@pytest.fixture(scope="module")
def plans(tmp_path_factory):
    out = {}
    for app in APPS:
        ws = tmp_path_factory.mktemp(app.replace("-", "_"))
        wrap_m2s.analyze(str(BENCH / app), str(ws))
        out[app] = (ws, packages.write(ws))
    return out


def _grouping(result):
    return {p["id"]: sorted(f"{e['method']} {e['path']}" for e in p["endpoints"]) for p in result["packages"]}


def test_expected_groupings(plans):
    expected = json.loads(EXPECTED.read_text(encoding="utf-8"))
    for app in APPS:
        assert _grouping(plans[app][1]) == expected[app], app


def test_self_checks(plans):
    for app in APPS:
        result = plans[app][1]
        check = result["self_check"]
        assert check["every_endpoint_once"] is True, app
        assert check["duplicate_ids"] == [], app
        assert 2 <= check["package_count"] <= 6, (app, check)
        assert check["over_budget"] == [], app


def test_rule1_operational_package(plans):
    acmeair = {e["path"] for p in plans["acmeair"][1]["packages"] if p["id"] == "ops" for e in p["endpoints"]}
    assert "/rest/api/checkstatus" in acmeair and "/rest/api/loader/load" in acmeair
    assert "/rest/api/config/runtime" in acmeair
    assert "/rest/api/bookings/bookflights" not in acmeair
    # a business endpoint whose URL contains "check" stays out of ops
    ecommerce = {e["path"] for p in plans["ecommerce"][1]["packages"] if p["kind"] == "ops" for e in p["endpoints"]}
    assert "/api/payment-3p/check" not in ecommerce
    # identity-looking "preauth" is not identity
    identity = {e["path"] for p in plans["ecommerce"][1]["packages"] if p["id"] == "identity" for e in p["endpoints"]}
    assert "/api/payment-3p/preauth" not in identity


def test_rule2_identity_package(plans):
    bookstore = {e["path"] for p in plans["bookstore"][1]["packages"] if p["id"] == "identity" for e in p["endpoints"]}
    assert bookstore == {"/api/auth/register", "/api/auth/login", "/api/auth/refresh", "/api/auth/me"}
    acmeair = {e["path"] for p in plans["acmeair"][1]["packages"] if p["id"] == "identity" for e in p["endpoints"]}
    assert {"/rest/api/login", "/rest/api/login/logout", "/:tokenid", "/byuserid/:user"} <= acmeair


def test_rule6_small_app_is_not_split(plans):
    todo = plans["todo"][1]["packages"]
    assert [p["id"] for p in todo] == ["identity", "app"]
    assert {p["kind"] for p in todo} == {"identity", "app"}


def test_shared_code_across_packages(plans):
    bookstore = plans["bookstore"][1]["shared_code"]
    auth = next(s for s in bookstore if s["function"] == "authMiddleware")
    assert auth["file"] == "bookstore/middleware/auth.js"
    assert len(auth["used_by"]) == 2
    coffee = plans["coffee"][1]["shared_code"]
    assert {s["function"] for s in coffee} >= {"authenticateToken", "requireAdmin"}


def test_hubs_excluded_from_affinity(plans):
    shopping = plans["shopping-cart"][1]
    assert "shopping-cart-monolith" in shopping["hubs"]["tables"]
    assert any("app.py" in f for f in shopping["hubs"]["files"])
    acmeair = plans["acmeair"][1]
    assert "acmeair/routes/index.js" in acmeair["hubs"]["files"]


def test_plan_is_deterministic(plans):
    ws, first = plans["bookstore"]
    report_path = ws / "storage" / "analysis_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    symbols = json.loads((ws / "storage" / "symbol_table.json").read_text(encoding="utf-8"))
    again = packages.plan(report, symbols, hashlib.sha256(report_path.read_bytes()).hexdigest())
    assert json.dumps(again, sort_keys=True, ensure_ascii=False) == json.dumps(first, sort_keys=True, ensure_ascii=False)
