"""v3 M2 (W2.1): data access through same-file helpers.

Handlers that call a helper in the same file must show the DynamoDB operations
that helper performs. Before M2 only helpers inside *required* modules were
followed, so acmeair-like handlers looked like they touched nothing.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import spec_extract  # noqa: E402

SRC = """\
const AWS = require('aws-sdk');
const docClient = new AWS.DynamoDB.DocumentClient();
const ORDERS = process.env.ORDERS_TABLE || 'Orders-Dev';
const config = { ordersTable: ORDERS };

function writeOrder(order) {
  return docClient.put({ TableName: config.ordersTable, Item: order }).promise();
}

function auditOrder(order) {
  return writeOrder(order);
}

router.post('/orders', async (req, res) => {
  await auditOrder(req.body);
  res.status(201).json({});
});
"""


def _handler(src: str):
    start = src.index("router.post('/orders'")
    brace = src.index("{", start)
    end = spec_extract._match_close(src, brace)
    return spec_extract._Handler("samefile/server.js", src, start, end)


def test_same_file_helper_is_followed(tmp_path):
    app = tmp_path / "samefile"
    app.mkdir()
    (app / "server.js").write_text(SRC, encoding="utf-8")
    project = spec_extract._Project(app)
    access = spec_extract._extract_data_access(project, _handler(SRC))
    assert [(a["operation"], a["table"]["config_key"], a.get("via")) for a in access] == [
        ("put", "ordersTable", "auditOrder -> writeOrder"),
    ]


def test_handler_does_not_follow_itself(tmp_path):
    """The handler's own range is in `seen`, so a recursive-looking call does
    not recurse and duplicate the operation."""
    app = tmp_path / "samefile"
    app.mkdir()
    src = SRC.replace("await auditOrder(req.body);", "await writeOrder(req.body);")
    (app / "server.js").write_text(src, encoding="utf-8")
    project = spec_extract._Project(app)
    access = spec_extract._extract_data_access(project, _handler(src))
    assert len(access) == 1


MONGO_DA = """\
module.exports = function (settings) {
  var module = {};
  var mongoClient = require('mongodb').MongoClient;
  var dbclient = null;
  module.findOne = function (collectionname, key, callback) {
    dbclient.collection(collectionname, function (error, collection) {
      collection.find({_id: key}).toArray(callback);
    });
  };
  return module;
};
"""

MONGO_APP = """\
const express = require('express');
const app = express();
const dataaccess = require('./dataaccess')({});
app.get('/things/:id', function (req, res) {
  dataaccess.findOne('thing', req.params.id, function (e, d) { res.send(d); });
});
app.get('/gadgets/:id', function (req, res) {
  dataaccess.findOne('gadget', req.params.id, function (e, d) { res.send(d); });
});
app.listen(3000);
"""


def test_mongo_access_pattern_attached_to_reaching_endpoint(tmp_path):
    """W2.2: the collection op lives in a wrapper; each endpoint gets only the
    patterns its own call site reaches (not the wrapper's other callers)."""
    import json

    import wrap_m2s

    app = tmp_path / "mini"
    app.mkdir()
    (app / "package.json").write_text(
        '{"name":"mini","version":"1.0.0","dependencies":{"express":"^4.0.0","mongodb":"^3.0.0"}}',
        encoding="utf-8")
    (app / "dataaccess.js").write_text(MONGO_DA, encoding="utf-8")
    (app / "server.js").write_text(MONGO_APP, encoding="utf-8")
    ws = tmp_path / "ws"
    wrap_m2s.analyze(str(app), str(ws))
    report = json.loads((ws / "storage" / "analysis_report.json").read_text(encoding="utf-8"))
    by_path = {ep["path"]: ep for ep in report["entry_points"]}
    assert [(a["operation"], a["table"]["default_name"]) for a in by_path["/things/:id"]["data_access"]] == [("find", "thing")]
    assert [(a["operation"], a["table"]["default_name"]) for a in by_path["/gadgets/:id"]["data_access"]] == [("find", "gadget")]
