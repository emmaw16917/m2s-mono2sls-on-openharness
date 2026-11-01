"""judge.check_judge_assets (PIPELINE_V2_PLAN.md §4.1, N1.1)."""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import judge  # noqa: E402

SRC = [{"file": "app/x.js", "line": 1, "symbol": "x"}]


def _resp(status, kind="error", **kw):
    return {"status": status, "kind": kind, "when": f"when {status}", "source": SRC, **kw}


FACTS = {
    "spec_meta": {"schema_version": 1},
    "global_middleware": [{"kind": "cors"}],
    "dynamodb_info": {"table_schemas": [
        {"table": {"default_name": "Shop-Cart"}, "partition_key": {"name": "customerId", "type": "S"},
         "sort_key": {"name": "bookId", "type": "S"}, "gsis": []},
        {"table": {"default_name": "Shop-Users"}, "partition_key": {"name": "userId", "type": "S"},
         "gsis": [{"index_name": "email-index", "partition_key": {"name": "email", "type": "S"}}]},
        {"table": {"default_name": "Shop-Books"}, "partition_key": {"name": "id", "type": "S"}, "gsis": []},
    ]},
    "entry_points": [
        {"method": "POST", "path": "/api/auth/register",
         "responses": [_resp(400), _resp(201, "success"), _resp(500, in_catch=True)],
         "data_access": [{"operation": "put", "table": {"default_name": "Shop-Users"}}],
         "business_rules": [{"id": "BR-1"}]},
        {"method": "POST", "path": "/api/cart",
         "responses": [_resp(401, guard_kind="authz"), _resp(400), _resp(200, "success"), _resp(500, in_catch=True)],
         "data_access": [{"operation": "put", "table": {"default_name": "Shop-Cart"}}],
         "business_rules": [{"id": "BR-2"}, {"id": "BR-3"}]},
        {"method": "GET", "path": "/api/cart",
         "responses": [_resp(401, guard_kind="authz"), _resp(200, "success")],
         "data_access": [{"operation": "query", "table": {"default_name": "Shop-Cart"}}]},
        {"method": "GET", "path": "/api/cart/:bookId",
         "responses": [_resp(200, "success")],
         "data_access": [{"operation": "get", "table": {"default_name": "Shop-Cart"}}]},
    ],
}

IDENTITIES = {"u1": {"monolith": {
    "steps": [{"name": "register", "method": "POST", "path": "/api/auth/register",
               "body": {"email": "u1@example.com", "password": "Passw0rd!"},
               "save": {"token": "body.accessToken", "id": "body.user.userId"}}],
    "header": {"Authorization": "Bearer ${token}"}}}}

CASES = {
    "post-api-cart/cart-add-then-read.json": {
        "endpoint": "POST /api/cart", "layer": "P", "title": "add then read back",
        "fact_refs": ["POST /api/cart#responses[2]", "POST /api/cart#business_rules[0]", "GET /api/cart#responses[1]",
                      "global_middleware[0]"],
        "fixtures": ["books"], "identity": "u1",
        "steps": [
            {"name": "add", "method": "POST", "path": "/api/cart", "body": {"bookId": "b1", "quantity": 2},
             "save": {"book": "body.bookId"}},
            {"name": "read", "method": "GET", "path": "/api/cart", "read_back": True},
            {"name": "one", "method": "GET", "path": "/api/cart/${book}?x=${u1.id}"}],
        "predicted": {"add": {"status": 200}, "read": {"status": 200, "body": {"contains": [{"bookId": "b1"}]}},
                      "one": {"status": 200}}},
    "post-api-cart/cart-errors.json": {
        "endpoint": "POST /api/cart", "layer": "P",
        "fact_refs": ["POST /api/cart#responses[0]", "POST /api/cart#responses[1]", "GET /api/cart#responses[0]"],
        "steps": [
            {"name": "anon", "method": "POST", "path": "/api/cart", "body": {}},
            {"name": "anon-read", "method": "GET", "path": "/api/cart"},
            {"name": "missing", "method": "POST", "path": "/api/cart", "identity": "u1", "form": {"bookId": "b1"}}],
        "predicted": {"anon": {"status": 401}, "anon-read": {"status": 401}, "missing": {"status": 400}}},
    "get-api-cart/cart-empty.json": {
        "endpoint": "GET /api/cart", "layer": "R", "identity": "u1",
        "fact_refs": ["GET /api/cart#responses[1]"],
        "steps": [{"name": "read", "method": "GET", "path": "/api/cart"}],
        "predicted": {"read": {"status": 200, "body": {"equals": []}}}},
    "get-api-cart-bookid/cart-one.json": {
        "endpoint": "GET /api/cart/:bookId", "layer": "P", "identity": "u1",
        "fact_refs": ["GET /api/cart/:bookId#responses[0]"],
        "steps": [{"name": "one", "method": "GET", "path": "/api/cart/b1"}],
        "predicted": {"one": {"status": 200}}},
    "post-api-auth-register/register.json": {
        "endpoint": "POST /api/auth/register", "layer": "P",
        "fact_refs": ["POST /api/auth/register#responses[0]", "POST /api/auth/register#responses[1]"],
        "steps": [
            {"name": "bad", "method": "POST", "path": "/api/auth/register", "raw_body": "{oops",
             "headers": {"content-type": "application/json"}},
            {"name": "ok", "method": "POST", "path": "/api/auth/register", "body": {"email": "a@b.c"},
             "save": {"token": "body.accessToken"}},
            {"name": "me", "method": "GET", "path": "/api/cart", "headers": {"Authorization": "Bearer ${token}"},
             "read_back": True}],
        "predicted": {"bad": {"status": 400}, "ok": {"status": 201}, "me": {"status": 200}}},
}

FIXTURES = {"books": {"Shop-Books": [{"id": "b1", "price": 9}], "Shop-Users": [{"userId": "x", "email": "e"}]}}
FAULTS = {"cart-put-fails": {"description": "the cart write fails", "cases": ["cart-add-then-read"],
                             "rules": [{"match": {"table": "Shop-Cart", "op": "PutItem"}, "kind": "error"}]}}


def make_ws(tmp_path: Path, *, cases=None, fixtures=None, faults=None, identities=IDENTITIES, extra=None) -> Path:
    ws = tmp_path / "ws"
    (ws / "storage").mkdir(parents=True)
    (ws / "storage" / "analysis_report.json").write_text(json.dumps(FACTS), encoding="utf-8")
    jd = ws / "judge"
    for rel, c in (CASES if cases is None else cases).items():
        p = jd / "cases" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(c if isinstance(c, str) else json.dumps(c), encoding="utf-8")
    for name, data in (FIXTURES if fixtures is None else fixtures).items():
        (jd / "fixtures").mkdir(parents=True, exist_ok=True)
        (jd / "fixtures" / f"{name}.json").write_text(json.dumps(data), encoding="utf-8")
    for name, data in (FAULTS if faults is None else faults).items():
        (jd / "faults").mkdir(parents=True, exist_ok=True)
        (jd / "faults" / f"{name}.json").write_text(json.dumps(data), encoding="utf-8")
    if identities is not None:
        (jd / "identities.json").write_text(json.dumps(identities), encoding="utf-8")
    for name, data in (extra or {}).items():
        (jd / name).write_text(json.dumps(data), encoding="utf-8")
    return ws


def test_endpoint_slug():
    assert judge.endpoint_slug("GET /api/books/:id") == "get-api-books-id"
    assert judge.endpoint_slug("POST /books/<int:id>/reviews") == "post-books-int-id-reviews"


def test_a_complete_judge_passes(tmp_path: Path):
    out = judge.check_judge_assets(make_ws(tmp_path))
    assert out["status"] == "PASS", out["errors"]
    s = out["summary"]
    assert s["cases"] == 5 and s["layers"] == {"P": 4, "R": 1}
    assert s["required_responses"] == "8/8 cited"
    assert s["optional_open"] == ["POST /api/auth/register#responses[2]", "POST /api/cart#responses[3]"]
    assert s["business_rules"] == "1/3 cited" and s["read_back_cases"] == 2 and s["identities"] == ["u1"]


def _mutate(path: str, fn):
    cases = copy.deepcopy(CASES)
    fn(cases[path])
    return cases


ADD = "post-api-cart/cart-add-then-read.json"


@pytest.mark.parametrize("cases, message", [
    (_mutate(ADD, lambda c: c.update(colour="red")), "unknown keys ['colour']"),
    (_mutate(ADD, lambda c: c.update(endpoint="POST /api/nope")), "is not an entry point of the fact base"),
    (_mutate(ADD, lambda c: c.update(layer="T")), "layer must be P or R"),
    (_mutate(ADD, lambda c: c.update(fact_refs=[])), "fact_refs must be a non-empty list"),
    (_mutate(ADD, lambda c: c["fact_refs"].append("POST /api/cart#responses[9]")), "responses has 4 items"),
    (_mutate(ADD, lambda c: c["fact_refs"].append("POST /api/cart#nonsense")), "must look like"),
    (_mutate(ADD, lambda c: c["fact_refs"].append("global_middleware[3]")), "global_middleware has 1 items"),
    (_mutate(ADD, lambda c: c["fact_refs"].append("GET /api/cart/:bookId#responses[0]") or c["steps"].pop()),
     "cites GET /api/cart/:bookId, which no step calls"),
    (_mutate(ADD, lambda c: c.update(fixtures=["nope"])), "fixture 'nope' not found"),
    (_mutate(ADD, lambda c: c.update(identity="admin")), "identity 'admin' is not defined"),
    (_mutate(ADD, lambda c: c["steps"][1].update(identity="ghost")), "identity 'ghost' is not defined"),
    (_mutate(ADD, lambda c: c["steps"][1].update(name="add")), "name 'add' is used by an earlier step"),
    (_mutate(ADD, lambda c: c["steps"][1].pop("name")), "name is required"),
    (_mutate(ADD, lambda c: c["steps"][0].update(method="FETCH")), "method must be one of"),
    (_mutate(ADD, lambda c: c["steps"][0].update(path="api/cart")), "path must be a string starting with '/'"),
    (_mutate(ADD, lambda c: c["steps"][0].update(form={"a": "b"})), "at most one of body / form / raw_body"),
    (_mutate(ADD, lambda c: c["steps"][0].update(save={"x": "response.body"})), "save.x must be"),
    (_mutate(ADD, lambda c: c["steps"][2].update(path="/api/cart/${nope}")), "${nope} is not saved by an earlier step"),
    (_mutate(ADD, lambda c: c["steps"][2].update(path="/api/cart/${u1.email}")), "identity u1 saves ['id', 'token']"),
    (_mutate(ADD, lambda c: c["steps"][2].update(path="/api/cart/${u9.id}")), "'u9' is not an identity"),
    (_mutate(ADD, lambda c: c["steps"][2].update(path="/api/books")), "does not match an endpoint of the fact base"),
    (_mutate(ADD, lambda c: c["steps"].pop(0)), "no step calls the endpoint under test POST /api/cart"),
    (_mutate(ADD, lambda c: c["steps"][0].update(read_back=True)), "read_back is for a GET"),
    (_mutate("get-api-cart/cart-empty.json", lambda c: c["steps"][0].update(read_back=True)),
     "read_back needs an earlier step that writes"),
    (_mutate(ADD, lambda c: c["predicted"].pop("read")), "predicted has no entry for step 'read'"),
    (_mutate(ADD, lambda c: c["predicted"].update(ghost={"status": 1})), "predicted.ghost names no step"),
    (_mutate(ADD, lambda c: c["predicted"]["add"].pop("status")), "status (an integer) is required"),
    (_mutate(ADD, lambda c: c["predicted"]["read"].update(body={"like": 1})), "body must use"),
    (_mutate(ADD, lambda c: c["predicted"]["read"].update(headers={"has": 1})), "headers must use"),
    ({**CASES, "post-api-cart/x.json": "{not json"}, "invalid JSON"),
    ({**CASES, "get-api-cart/dup.json": {**CASES["get-api-cart/cart-empty.json"], "id": "cart-errors"}},
     "id 'cart-errors' is also used by"),
    ({**CASES, "get-api-cart/misfiled.json": {**CASES["get-api-cart-bookid/cart-one.json"], "id": "m"}},
     "belongs in judge/cases/get-api-cart-bookid/"),
])
def test_case_checks(tmp_path: Path, cases, message):
    out = judge.check_judge_assets(make_ws(tmp_path, cases=cases))
    assert out["status"] == "FAIL"
    assert any(message in e for e in out["errors"]), out["errors"]


def test_coverage_checks(tmp_path: Path):
    cases = {k: v for k, v in CASES.items() if not k.startswith("get-api-cart-bookid")}
    errs = judge.check_judge_assets(make_ws(tmp_path, cases=cases))["errors"]
    assert any(e.startswith("GET /api/cart/:bookId: no case") for e in errs)
    assert any(e.startswith("GET /api/cart/:bookId#responses[0] (200") for e in errs)
    assert not any("reads the effect back" in e for e in errs)
    no_read_back = _mutate(ADD, lambda c: c["steps"][1].pop("read_back"))
    errs = judge.check_judge_assets(make_ws(tmp_path / "2", cases=no_read_back))["errors"]
    assert any(e.startswith("POST /api/cart writes (put) but none of its cases reads the effect back") for e in errs)


def test_uncovered_exempts_with_a_reason(tmp_path: Path):
    cases = {k: v for k, v in CASES.items() if not k.startswith("get-api-cart-bookid")}
    unc = [{"ref": "GET /api/cart/:bookId#responses[0]", "reason": "no fixture can reach it"},
           {"ref": "POST /api/cart#responses[2]", "reason": "already cited"}]
    out = judge.check_judge_assets(make_ws(tmp_path, cases=cases, extra={"uncovered.json": unc}))
    assert [e for e in out["errors"] if "#responses" in e] == []
    assert any(e.startswith("GET /api/cart/:bookId: no case") for e in out["errors"])  # an endpoint still needs a case
    assert any("lists POST /api/cart#responses[2], which a case cites" in w for w in out["warnings"])
    assert "2 exempted" in out["summary"]["required_responses"]
    bad = [{"ref": "POST /api/cart#business_rules[0]", "reason": "x"}, {"ref": "POST /api/cart#responses[0]"},
           {"ref": "PUT /api/x#read_back", "reason": "x"}]
    errs = judge.check_judge_assets(make_ws(tmp_path / "2", extra={"uncovered.json": bad}))["errors"]
    assert any("uncovered.json[0]: ref must be" in e for e in errs)
    assert any("uncovered.json[1]: needs ref and a non-empty reason" in e for e in errs)
    assert any("uncovered.json[2]: no entry point PUT /api/x" in e for e in errs)


@pytest.mark.parametrize("fixtures, message", [
    ({"books": {"Books": [{"id": "b1"}]}}, "'Books' is not a monolith table"),
    ({"books": {"Shop-Cart": [{"customerId": "c1"}]}}, "has no key attribute 'bookId'"),
    ({"books": {"Shop-Books": [{"id": 7}]}}, "Shop-Books[0].id must be a string"),
    ({"books": {"Shop-Users": [{"userId": "u", "email": 5}]}}, "index key of type S"),
    ({"books": {"Shop-Books": [{"id": "b1"}, {"id": "b1"}]}}, "repeats the key"),
    ({"books": {"Shop-Books": {"id": "b1"}}}, "must be a list of items"),
])
def test_fixture_checks(tmp_path: Path, fixtures, message):
    errs = judge.check_judge_assets(make_ws(tmp_path, fixtures=fixtures))["errors"]
    assert any(message in e for e in errs), errs


def test_unused_fixture_is_a_warning(tmp_path: Path):
    out = judge.check_judge_assets(make_ws(tmp_path, fixtures={**FIXTURES, "spare": {}}))
    assert out["status"] == "PASS" and out["warnings"] == ["judge/fixtures/spare.json is not used by any case"]


def _ident(fn):
    ids = copy.deepcopy(IDENTITIES)
    fn(ids["u1"]["monolith"])
    return ids


@pytest.mark.parametrize("identities, message", [
    ({"U1": IDENTITIES["u1"]}, "an alias is lower case"),
    ({"u1": {"monolith": {}}}, "use exactly one of steps"),
    (_ident(lambda m: m["steps"][0]["save"].pop("id")), "must save id"),
    (_ident(lambda m: m.update(header={"Authorization": "Bearer ${jwt}"})), "${jwt} is not saved by the preparation"),
    (_ident(lambda m: m.pop("header")), "header must map header names"),
    (_ident(lambda m: m["steps"][0].update(path="/api/login")), "is not an endpoint of the fact base"),
    (_ident(lambda m: m["steps"][0].update(read_back=True)), "unknown keys ['read_back']"),
    ({"u1": {"monolith": {"sign": {"secret": "s", "claims": {"userId": "x"}, "alg": "RS256"}, "id": "x",
                          "header": {"Authorization": "Bearer ${token}"}}}}, "only HS256"),
    ({"u1": {"monolith": {"sign": {"secret": "s", "claims": {}}, "header": {"Authorization": "Bearer ${token}"}}}},
     "sign needs id"),
])
def test_identity_checks(tmp_path: Path, identities, message):
    errs = judge.check_judge_assets(make_ws(tmp_path, identities=identities))["errors"]
    assert any(message in e for e in errs), errs


def test_signed_identity_passes(tmp_path: Path):
    ids = {"u1": {"monolith": {"sign": {"secret": "s", "claims": {"userId": "user-1"}, "expires_in_s": 600},
                               "id": "user-1", "header": {"Authorization": "Bearer ${token}"}}}}
    assert judge.check_judge_assets(make_ws(tmp_path, identities=ids))["status"] == "PASS"


@pytest.mark.parametrize("rules, message", [
    ([{"match": {"table": "Shop-Cart"}, "kind": "explode"}], "kind must be one of"),
    ([{"match": {"table": "Cart"}, "kind": "error"}], "'Cart' is not a monolith table"),
    ([{"match": {"op": "Query", "table": "Shop-Cart"}, "kind": "error"}], "names a read operation"),
    ([{"match": {"table": "Shop-Cart"}, "nth": 2, "kind": "error"}], "needs match.item"),
    ([{"match": {"table": "Shop-Cart", "item": {}}, "kind": "error"}], "must be a non-empty object"),
    ([{"match": {"table": "Shop-Cart", "where": 1}, "kind": "error"}], "may only have op / table / queue / item"),
    ([{"match": {"table": "Shop-Cart"}, "nth": 0, "kind": "error"}], "nth must be"),
])
def test_fault_rule_checks(tmp_path: Path, rules, message):
    faults = {"f": {"description": "d", "cases": ["cart-errors"], "rules": rules}}
    errs = judge.check_judge_assets(make_ws(tmp_path, faults=faults))["errors"]
    assert any(message in e for e in errs), errs


def test_fault_scenario_checks(tmp_path: Path):
    faults = {"f": {"cases": ["nope"], "rules": [{"match": {"table": "Shop-Cart"}, "kind": "error"}], "when": 1}}
    errs = judge.check_judge_assets(make_ws(tmp_path, faults=faults))["errors"]
    assert any("description is required" in e for e in errs)
    assert any("case 'nope' not found" in e for e in errs)
    assert any("unknown keys ['when']" in e for e in errs)


def test_run_config_checks(tmp_path: Path):
    mono = tmp_path / "mono"
    mono.mkdir()
    (mono / "server.js").write_text("", encoding="utf-8")
    facts = {**FACTS, "spec_meta": {"monolith_root": str(mono)}}
    ws = make_ws(tmp_path, extra={"run_config.json": {"entry": "server.js", "env": {"NODE_ENV": "test"}}})
    (ws / "storage" / "analysis_report.json").write_text(json.dumps(facts), encoding="utf-8")
    assert judge.check_judge_assets(ws)["status"] == "PASS"
    (ws / "judge" / "run_config.json").write_text(json.dumps(
        {"entry": "app.js", "env": {"N": 1}, "runtime": "ruby", "port": 3}), encoding="utf-8")
    errs = judge.check_judge_assets(ws)["errors"]
    assert any("entry 'app.js' is not a file of the monolith" in e for e in errs)
    assert any("env values must be strings" in e for e in errs)
    assert any("runtime must be node or python" in e for e in errs)
    assert any("unknown key 'port'" in e for e in errs)


def test_no_cases_and_no_facts(tmp_path: Path):
    out = judge.check_judge_assets(make_ws(tmp_path, cases={}))
    assert out["status"] == "FAIL" and "has no cases" in out["errors"][0]
    with pytest.raises(judge.JudgeError, match="fact base"):
        judge.check_judge_assets(tmp_path / "empty")
