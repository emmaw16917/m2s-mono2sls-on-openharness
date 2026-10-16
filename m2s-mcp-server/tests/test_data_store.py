"""P0 MongoDB access patterns (data_store.py), gate DM1/DM2, and the
spec_extract fixes found on Acme Air (commented-out routes, named handlers,
config-conditional registrations)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import data_store  # noqa: E402
import gates  # noqa: E402
import spec_extract  # noqa: E402

DAO = """
module.exports = function (settings) {
  var module = {};
  var mongoClient = require('mongodb').MongoClient;
  var dbclient = null;
  module.dbNames = { customerName: "customer", bookingName: "booking" };
  module.findOne = function (collectionname, key, callback) {
    dbclient.collection(collectionname, function (error, collection) {
      collection.find({_id: key}).toArray(callback);
    });
  };
  module.findBy = function (collectionname, condition, callback) {
    dbclient.collection(collectionname, function (error, collection) {
      collection.find(condition).toArray(callback);
    });
  };
  module.remove = function (collectionname, condition, callback) {
    dbclient.collection(collectionname, function (error, collection) {
      collection.deleteOne({_id: condition._id}, callback);
    });
  };
  module.count = function (collectionname, condition, callback) {
    dbclient.collection(collectionname, function (error, collection) {
      collection.count(condition, callback);
    });
  };
  return module;
};
"""

ROUTES = """
module.exports = function (dataaccess, settings) {
  var module = {};
  module.dbNames = { customerName: "customer", bookingName: "booking" };
  module.getCustomer = function (req, res) {
    dataaccess.findOne(module.dbNames.customerName, req.params.user, function (e, c) { res.send(c); });
  };
  module.bookingsByUser = function (req, res) {
    var criteria = {customerId: req.params.user, when: {$gte: 5}};
    dataaccess.findBy(module.dbNames.bookingName, criteria, function (e, d) { res.send(d); });
  };
  module.cancel = function (req, res) {
    dataaccess.remove(module.dbNames.bookingName, {'_id': req.body.number, 'customerId': req.body.userid}, function () { res.send('ok'); });
  };
  countItems = function (dbName, callback) {
    dataaccess.count(dbName, {}, callback);
  };
  module.countBookings = function (req, res) {
    countItems(module.dbNames.bookingName, function (e, n) { res.send(n.toString()); });
  };
  return module;
};
"""

APP = """
var express = require('express');
var settings = {};
var app = express();
var router = express.Router();
var dataaccess = new require('./dao/index.js')(settings);
var routes = new require('./routes/index.js')(dataaccess, settings);
dataaccess.init(function (error) {
  if (error) {
    console.log(error);
  } else {
    router.get('/customer/:user', routes.getCustomer);
    if (settings.v2) {
      router.get('/bookings/:user', routes.bookingsByUser);
    } else {
      router.get('/bookings/:user', routes.getCustomer);
    }
    router.post('/cancel', cancel);
    router.get('/count', routes.countBookings);
    /*
    router.get('/disabled', routes.getCustomer);
    */
  }
});
function cancel(req, res) {
  routes.cancel(req, res);
}
app.use('/', router);
"""


def _app(tmp_path: Path) -> Path:
    root = tmp_path / "mongoapp"
    (root / "dao").mkdir(parents=True)
    (root / "routes").mkdir()
    (root / "dao" / "index.js").write_text(DAO, encoding="utf-8")
    (root / "routes" / "index.js").write_text(ROUTES, encoding="utf-8")
    (root / "app.js").write_text(APP, encoding="utf-8")
    return root


def _by(store, coll, op):
    return [p for p in store["access_patterns"] if p["collection"] == coll and p["operation"] == op]


def test_detect_resolves_wrappers_and_forwarders(tmp_path):
    store = data_store.detect(_app(tmp_path))
    assert store["kind"] == "mongodb" and store["driver"] == "mongodb"
    assert {c["name"] for c in store["collections"]} == {"customer", "booking"}
    assert store["unresolved"] == []
    [get] = _by(store, "customer", "find")
    assert get["filter_fields"] == [{"name": "_id", "match": "eq"}] and get["via"] == "findOne"
    # filter held in a variable, with a range operator
    [by_user] = _by(store, "booking", "find")
    assert by_user["filter_fields"] == [{"name": "customerId", "match": "eq"}, {"name": "when", "match": "range"}]
    # the wrapper ignores part of what the caller passes
    [cancel] = _by(store, "booking", "delete")
    assert cancel["filter_fields"] == [{"name": "_id", "match": "eq"}] and "customerId" in cancel["note"]
    # countItems(dbName) forwards to dataaccess.count(dbName, {})
    [count] = _by(store, "booking", "count")
    assert count["filter_fields"] == [] and count["via"] == "countItems -> count"


def test_detect_none_without_mongo(tmp_path):
    (tmp_path / "app.js").write_text("const AWS = require('aws-sdk');\n", encoding="utf-8")
    assert data_store.detect(tmp_path) is None


def test_spec_extract_named_handlers_comments_and_conditions(tmp_path):
    root = _app(tmp_path)
    eps = [{"file": "mongoapp/app.js", "method": m, "path": p, "handler_function": "h"} for m, p in (
        ("GET", "/customer/:user"), ("GET", "/bookings/:user"), ("GET", "/bookings/:user"),
        ("POST", "/cancel"), ("GET", "/count"), ("GET", "/disabled"))]
    report = {"entry_points": eps}
    spec_extract.enrich_report(report, root)
    got = {(e["method"], e["path"]): e for e in report["entry_points"]}
    assert set(got) == {("GET", "/customer/:user"), ("GET", "/bookings/:user"), ("POST", "/cancel"), ("GET", "/count")}
    dropped = report["spec_meta"]["dropped_entry_points"]
    assert any("/disabled" in d and "commented out" in d for d in dropped)
    assert any("/bookings/:user" in d and "duplicate" in d for d in dropped)
    # obj.member handler resolved into routes/index.js
    assert got[("GET", "/customer/:user")]["handler_location"]["file"] == "mongoapp/routes/index.js"
    # a same-file function that only delegates is followed once more
    assert got[("POST", "/cancel")]["handler_location"]["file"] == "mongoapp/routes/index.js"
    assert {f["name"] for f in got[("POST", "/cancel")]["request"]["body"]} == {"number", "userid"}
    # if (error) {...} else {...} is not a config condition; settings.v2 is
    assert "registration_condition" not in got[("GET", "/customer/:user")]
    b = got[("GET", "/bookings/:user")]
    assert b["registration_condition"]["expression"] == "settings.v2"
    assert [a["condition"] for a in b["registration_alternatives"]] == ["settings.v2", "!(settings.v2)"]


def _dm_ws(tmp_path: Path, data_model, tables=None) -> Path:
    ws = tmp_path / "ws"
    (ws / "storage").mkdir(parents=True)
    store = {"kind": "mongodb", "access_patterns": [
        {"id": "AP1", "collection": "booking", "operation": "find", "filter_fields": [{"name": "customerId", "match": "eq"}],
         "source": [{"file": "a/r.js", "line": 3, "symbol": "findBy"}]},
        {"id": "AP2", "collection": "booking", "operation": "delete", "filter_fields": [{"name": "_id", "match": "eq"}],
         "source": [{"file": "a/r.js", "line": 4, "symbol": "remove"}]},
        {"id": "AP3", "collection": "booking", "operation": "count", "filter_fields": [],
         "source": [{"file": "a/r.js", "line": 5, "symbol": "count"}]},
    ]}
    (ws / "storage" / "analysis_report.json").write_text(json.dumps({"source_data_store": store}), encoding="utf-8")
    bp = {"dynamodb_tables": tables if tables is not None else [
        {"logical_name": "BookingTable", "partition_key": {"name": "id", "type": "S"},
         "gsi": [{"index_name": "customerId-index", "partition_key": {"name": "customerId", "type": "S"}}]}]}
    if data_model is not None:
        bp["data_model"] = data_model
    (ws / "storage" / "blueprint.json").write_text(json.dumps(bp), encoding="utf-8")
    return ws


GOOD_DM = {
    "source": "mongodb",
    "collections": [{"collection": "booking", "table": "BookingTable", "field_mapping": {"_id": "id"}}],
    "access_patterns": [
        {"ids": ["AP1"], "via": "Query", "table": "BookingTable", "index": "customerId-index", "key_condition": ["customerId"]},
        {"ids": ["AP2"], "via": "DeleteItem", "table": "BookingTable", "key_condition": ["id"]},
        {"ids": ["AP3"], "via": "Scan", "table": "BookingTable", "reason": "count of all bookings (admin endpoint)"},
    ],
}


def test_dm_good_mapping_passes(tmp_path):
    assert gates.check_dm(_dm_ws(tmp_path, GOOD_DM)) == []


def test_dm_missing_section_and_unmapped_pattern(tmp_path):
    [f] = gates.check_dm(_dm_ws(tmp_path, None))
    assert f["check_id"] == "DM1" and "data_model" in f["description"]
    dm = json.loads(json.dumps(GOOD_DM))
    dm["access_patterns"].pop(0)
    msgs = [f["description"] for f in gates.check_dm(_dm_ws(tmp_path / "b", dm))]
    assert any("AP1" in m and "mapped 0 times" in m for m in msgs)


def test_dm_rejects_unserved_keys_and_unjustified_scan(tmp_path):
    dm = json.loads(json.dumps(GOOD_DM))
    dm["access_patterns"][0] = {"ids": ["AP1"], "via": "Query", "table": "BookingTable", "key_condition": ["id"]}
    dm["access_patterns"][2].pop("reason")
    msgs = [f["description"] for f in gates.check_dm(_dm_ws(tmp_path, dm))]
    assert any("customerId" in m and "filter_expression" in m for m in msgs)
    assert any("Scan needs a reason" in m for m in msgs)
    dm = json.loads(json.dumps(GOOD_DM))
    dm["access_patterns"][1]["key_condition"] = ["customerId"]
    msgs = [f["description"] for f in gates.check_dm(_dm_ws(tmp_path / "b", dm))]
    assert any("DeleteItem needs the full primary key" in m for m in msgs)


def test_dm_skips_dynamodb_monoliths(tmp_path):
    ws = tmp_path / "ws"
    (ws / "storage").mkdir(parents=True)
    (ws / "storage" / "analysis_report.json").write_text(json.dumps({"entry_points": []}), encoding="utf-8")
    assert gates.check_dm(ws) == []


def test_gm2_rejects_unknown_gateway_response_type(tmp_path):
    ws = tmp_path / "ws"
    (ws / "storage").mkdir(parents=True)
    (ws / "storage" / "analysis_report.json").write_text(json.dumps({}), encoding="utf-8")
    tpl = {"Globals": {"Api": {"GatewayResponses": {"INTERNAL_ERROR": {}, "DEFAULT_4XX": {}}}}}
    msgs = [f["description"] for f in gates.check_gm_template(ws, tpl)]
    assert len(msgs) == 1 and "INTERNAL_ERROR" in msgs[0]


def test_spec_extract_prefix_from_settings_json(tmp_path):
    root = tmp_path / "svc"
    root.mkdir()
    (root / "settings.json").write_text('{"contextRoot": "/rest/api"}', encoding="utf-8")
    (root / "app.js").write_text("""var fs = require('fs');
var settings = JSON.parse(fs.readFileSync('settings.json', 'utf8'));
var express = require('express');
var app = express();
var router = express.Router();
router.post('/login', function (req, res) { res.send(req.body.login); });
app.use(settings.contextRoot, router);
""", encoding="utf-8")
    report = {"entry_points": [{"file": "svc/app.js", "method": "POST", "path": "/login", "handler_function": "h"}]}
    spec_extract.enrich_report(report, root)
    [ep] = report["entry_points"]
    assert ep["path"] == "/rest/api/login" and ep["path_prefix"]["prefix"] == "/rest/api"
    assert [f["name"] for f in ep["request"]["body"]] == ["login"]
