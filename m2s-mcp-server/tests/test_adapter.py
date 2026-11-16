"""Tests for adapter.py: the adapter map's format, its item mapping in both
directions and the identity mapping (PIPELINE_V2_PLAN.md §4.8, N2.1)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import adapter  # noqa: E402

MONO_TABLES = {
    "Bookstore-Books": {"keys": {"id": "S"}, "index_keys": {"category": "S"}},
    "Bookstore-Cart": {"keys": {"customerId": "S", "bookId": "S"}, "index_keys": {}},
    "Bookstore-Users": {"keys": {"userId": "S"}, "index_keys": {"email": "S"}},
}
ENDPOINTS = ["GET /api/books", "POST /api/cart", "POST /api/auth/register", "GET /api/auth/me"]
TARGET = {"BooksTable": ["id"], "CartTable": ["pk"]}
IDENTITIES = {"u1": {"monolith": {"steps": [{"method": "POST", "path": "/api/auth/register",
                                             "body": {"email": "u1@example.com", "password": "Passw0rd!", "name": "User One"},
                                             "save": {"id": "body.user.userId", "token": "body.accessToken"}}],
                                  "header": {"Authorization": "Bearer ${token}"}}}}

# the bookstore case of the plan: users -> Cognito, cart keyed by a composite
BOOKSTORE_MAP = {
    "tables": [
        {"monolith": "Bookstore-Books", "target": "BooksTable"},
        {"monolith": "Bookstore-Cart", "target": "CartTable",
         "fields": {"pk": {"format": "${customerId}#${bookId}"},
                    "customerId": {"from": "customerId", "as": "identity"}}},
        {"monolith": "Bookstore-Users", "target": None, "replaced_by": "cognito",
         "reason": "Cognito keeps the users (SI-1)"},
    ],
    "identities": {"claims": {"sub": "${id}", "email": "${email}", "name": "${name}"}},
    "dropped_endpoints": [{"endpoint": "POST /api/auth/register", "replaced_by": "cognito", "reason": "SI-1"},
                          {"endpoint": "GET /api/auth/me", "replaced_by": "cognito", "reason": "SI-1"}],
}


def check(amap, **kw):
    args = dict(monolith_tables=MONO_TABLES, endpoints=ENDPOINTS, target_tables=TARGET, identities=IDENTITIES,
                auth_replaced=True)
    args.update(kw)
    return adapter.check_adapter_map(amap, **args)


def test_the_bookstore_map_expresses_users_to_cognito_and_identities():
    assert check(BOOKSTORE_MAP, fixtures={"books": {"Bookstore-Books": [{"id": "b1", "name": "x", "price": 1}]},
                                          "cart": {"Bookstore-Cart": [{"customerId": "c1", "bookId": "b1", "quantity": 2}]}}) == []
    ident = adapter.target_identity(BOOKSTORE_MAP, "u1", IDENTITIES["u1"]["monolith"], "sub-1")
    assert ident == {"claims": {"sub": "sub-1", "email": "u1@example.com", "name": "User One"}, "user_id": "sub-1", "seed": []}
    out = adapter.fixtures_to_target(BOOKSTORE_MAP, {"Bookstore-Cart": [{"customerId": "c1", "bookId": "b1", "quantity": 2}],
                                                     "Bookstore-Users": [{"userId": "c1"}]})
    assert out == {"CartTable": [{"bookId": "b1", "quantity": 2, "pk": "c1#b1", "customerId": "c1"}]}


def test_items_map_back_to_the_monolith_structure():
    entry = BOOKSTORE_MAP["tables"][1]
    item = {"customerId": "c1", "bookId": "b1", "quantity": 2}
    assert adapter.to_monolith(entry, adapter.to_target(entry, item)) == item
    back = adapter.tables_to_monolith(BOOKSTORE_MAP, {"CartTable": [{"pk": "c1#b1", "customerId": "c1", "bookId": "b1"}],
                                                      "IdempotencyTable": [{"k": 1}]})
    assert back == {"Bookstore-Cart": [{"customerId": "c1", "bookId": "b1"}]}


def test_rename_const_split_json_and_drop():
    entry = {"monolith": "Bookstore-Cart", "target": "CartTable",
             "fields": {"owner": {"from": "customerId"}, "gsiPk": {"const": "ALL"},
                        "c0": {"from": "code", "split": "-", "part": 0}, "c1": {"from": "code", "split": "-", "part": 1},
                        "books": {"from": "books", "encode": "json"}},
             "drop": {"secret": "not stored on the target"}}
    item = {"customerId": "c1", "bookId": "b1", "code": "AB-12", "books": [{"x": 1}], "secret": "s"}
    t = adapter.to_target(entry, item)
    assert t == {"bookId": "b1", "owner": "c1", "gsiPk": "ALL", "c0": "AB", "c1": "12", "books": '[{"x":1}]'}
    assert adapter.to_monolith(entry, t) == {"bookId": "b1", "customerId": "c1", "code": "AB-12", "books": [{"x": 1}]}


def test_identity_fields_follow_the_id_mapping():
    entry = BOOKSTORE_MAP["tables"][1]
    t = adapter.to_target(entry, {"customerId": "mono-1", "bookId": "b1"}, ids={"mono-1": "sub-1"})
    assert t["customerId"] == "sub-1"
    assert adapter.to_monolith(entry, t, ids={"sub-1": "mono-1"})["customerId"] == "mono-1"


def test_missing_tables_keys_and_identities_are_reported():
    amap = {"tables": [{"monolith": "Bookstore-Books", "target": "BooksTable"},
                       {"monolith": "Bookstore-Cart", "target": "CartTable"}]}
    errs = check(amap)
    assert any("no entry for the monolith table Bookstore-Users" in e for e in errs)
    assert any("target key attribute(s) ['pk']" in e for e in errs)
    assert any("identities is required" in e for e in errs)


def test_code_in_a_mapping_is_refused():
    amap = {**BOOKSTORE_MAP, "tables": [{"monolith": "Bookstore-Books", "target": "BooksTable",
                                         "fields": {"price": {"from": "price", "js": "v * 100"}}},
                                        *BOOKSTORE_MAP["tables"][1:]]}
    assert any("unknown keys ['js']" in e for e in check(amap))


def test_unknown_targets_endpoints_and_identity_variables():
    amap = {**BOOKSTORE_MAP,
            "tables": [{"monolith": "Bookstore-Books", "target": "Books"}, *BOOKSTORE_MAP["tables"][1:]],
            "identities": {"claims": {"sub": "${id}", "phone": "${phone}"}},
            "dropped_endpoints": [{"endpoint": "POST /api/auth/logout", "reason": "x"}]}
    errs = check(amap)
    assert any("target 'Books' is not a table of the target" in e for e in errs)
    assert any("['phone'], which alias u1 does not define" in e for e in errs)
    assert any("'POST /api/auth/logout' is not an endpoint" in e for e in errs)


def test_a_format_without_separator_cannot_round_trip():
    amap = {**BOOKSTORE_MAP, "tables": [BOOKSTORE_MAP["tables"][0],
                                        {"monolith": "Bookstore-Cart", "target": "CartTable",
                                         "fields": {"pk": {"format": "${customerId}${bookId}"}}},
                                        BOOKSTORE_MAP["tables"][2]]}
    assert any("need a separator" in e for e in check(amap))


def test_round_trip_catches_a_mapping_that_loses_data():
    amap = {**BOOKSTORE_MAP, "tables": [BOOKSTORE_MAP["tables"][0],
                                        {"monolith": "Bookstore-Cart", "target": "CartTable",
                                         "fields": {"pk": {"format": "${customerId}#${bookId}"},
                                                    "qty": {"from": "quantity", "split": "#", "part": 0}}},
                                        BOOKSTORE_MAP["tables"][2]]}
    errs = check(amap, fixtures={"cart": {"Bookstore-Cart": [{"customerId": "c", "bookId": "b", "quantity": 2}]}})
    assert any("does not map back to itself" in e for e in errs)
