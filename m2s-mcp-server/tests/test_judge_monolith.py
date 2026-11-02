"""Symbolic masks, unify and run_monolith (PIPELINE_V2_PLAN.md §4.3-§4.4, N1.2-N1.3)."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import judge  # noqa: E402

U1, U2 = "1b4e28ba-2fa1-11d2-883f-0016d3cca427", "6fa459ea-ee8a-3ca4-894e-db77e160355e"
O1, O2 = "16fd2706-8baf-433b-82eb-8c7fada847da", "886313e1-3b8a-5372-9b90-0c9aee199e5d"


def test_symbols_keep_identities_and_correspondences():
    s = judge.Symbolizer()
    s.seed("<id:u1>", U1, U2)
    a = {"order": {"id": O1, "customerId": U1, "at": 1793515424000, "msg": f"order {O1} placed"},
         "list": [{"id": O1}], "same": "fixed"}
    b = {"order": {"id": O2, "customerId": U2, "at": 1793515425000, "msg": f"order {O2} placed"},
         "list": [{"id": O2}], "same": "fixed"}
    t = s.walk(a, b)
    assert t == {"order": {"id": "<uuid#1>", "customerId": "<id:u1>", "at": "<time#2>", "msg": "order <uuid#1> placed"},
                 "list": [{"id": "<uuid#1>"}], "same": "fixed"}
    assert s.symbols["<uuid#1>"]["first"] == "order.id" and s.unstable == []
    assert ["order.msg", "<uuid#1>"] in s.positions


def test_volatile_parts_inside_strings_and_plain_values():
    s = judge.Symbolizer()
    t = s.walk({"m": "at 2026-11-01T06:44:28.123Z by x", "n": "alpha", "tok": "eyJa.eyJb.c1"},
               {"m": "at 2026-11-01T06:44:29.456Z by x", "n": "beta", "tok": "eyJa.eyJb.c2"})
    assert t == {"m": "at <time#1> by x", "n": "<v#2>", "tok": "<token#3>"}


def test_structural_differences_are_unstable():
    s = judge.Symbolizer()
    s.walk({"a": [1, 2], "b": {"x": 1}, "c": 1}, {"a": [1], "b": {"y": 1}, "c": "1"})
    assert [u["path"] for u in s.unstable] == ["a", "b", "c"]


def test_a_coincidental_equality_gets_its_own_symbol():
    """Two tokens issued within the same second are equal in one pass only."""
    s = judge.Symbolizer()
    s.seed("<token:u1>", "eyJa.eyJp.s1", "eyJa.eyJq.s1")
    t = s.walk({"login": "eyJa.eyJp.s1", "refresh": "eyJa.eyJp.s1"}, {"login": "eyJa.eyJq.s1", "refresh": "eyJa.eyJr.s2"})
    assert t == {"login": "<token:u1>", "refresh": "<token#1>"}
    assert s.symbols["<token#1>"]["note"].startswith("equals another volatile value") and s.unstable == []


O3, O4 = "2a4c6e80-0000-4000-8000-000000000001", "9f8e7d6c-0000-4000-8000-000000000002"


def _run(body, tables=None):
    return {"id": "c", "identities": {}, "steps": [{"name": "read", "response": {
        "status": 200, "headers": {}, "body_kind": "json", "body": body}}], "tables": tables or {}}


def test_a_list_in_a_different_order_between_passes_is_unordered():
    facts = type("F", (), {"tables": {}})()
    a = [{"orderId": O1, "book": "b1"}, {"orderId": O2, "book": "b3"}]
    b = [{"orderId": O4, "book": "b3"}, {"orderId": O3, "book": "b1"}]  # swapped, other ids
    m = judge.mask_unit(facts, [_run(a), _run(b), _run(list(reversed(a)))])
    assert m["unstable"] == [] and m["unordered"] == ["steps.read.body"]
    assert m["checks"][0]["mismatches"] == []


def test_a_list_sorted_by_a_random_id_is_unordered_even_when_the_passes_agree():
    facts = type("F", (), {"tables": {}})()
    a = sorted([{"orderId": O1, "book": "b1"}, {"orderId": O2, "book": "b3"}], key=lambda x: x["orderId"])
    b = sorted([{"orderId": O3, "book": "b1"}, {"orderId": O4, "book": "b3"}], key=lambda x: x["orderId"])
    third = [{"orderId": "ffff0000-0000-4000-8000-000000000001", "book": "b1"},
             {"orderId": "00000000-0000-4000-8000-000000000002", "book": "b3"}]
    m = judge.mask_unit(facts, [_run(a), _run(b), _run(sorted(third, key=lambda x: x["orderId"]))])
    assert m["unordered"] == ["steps.read.body"] and m["checks"][0]["mismatches"] == []


def test_a_list_in_time_order_stays_ordered():
    facts = type("F", (), {"tables": {}})()
    run = lambda t1, t2, first="b1", second="b3": _run([{"at": t1, "book": first}, {"at": t2, "book": second}])
    m = judge.mask_unit(facts, [run("2026-11-01T00:00:01Z", "2026-11-01T00:00:02Z"),
                                run("2026-11-01T00:00:05Z", "2026-11-01T00:00:06Z"),
                                run("2026-11-01T00:00:08Z", "2026-11-01T00:00:09Z", "b3", "b1")])
    assert m["unordered"] == [] and m["checks"][0]["mismatches"]  # the order of the books is behaviour


def test_table_items_match_as_a_set_and_rules_fired_is_whether_it_hit():
    facts = type("F", (), {"tables": {"T": {"keys": {"k": "S"}, "index_keys": {}}}})()
    run = lambda items, fired: {**_run([], {"T": items}), "rules_fired": [fired]}
    m = judge.mask_unit(facts, [run([{"k": O1, "v": 1}, {"k": O2, "v": 2}], 20),
                                run([{"k": O3, "v": 1}, {"k": O4, "v": 2}], 21),
                                run([{"k": "f" + O1[1:], "v": 1}, {"k": "0" + O2[1:], "v": 2}], 11)])
    assert m["template"]["rules_fired"] == [True] and m["checks"][0]["mismatches"] == []


def test_unify_binds_symbols_consistently():
    tpl = {"id": "<uuid#1>", "again": "<uuid#1>", "who": "<id:u1>", "msg": "order <uuid#1> for <id:u1>", "n": 2}
    ok = {"id": "x-1", "again": "x-1", "who": "u-9", "msg": "order x-1 for u-9", "n": 2}
    b = {"<id:u1>": "u-9"}
    assert judge.unify(tpl, ok, b) == [] and b["<uuid#1>"] == "x-1"
    bad = {**ok, "again": "x-2", "msg": "order x-1 for u-8", "n": 3}
    mism = judge.unify(tpl, bad, {"<id:u1>": "u-9"})
    assert {m["path"] for m in mism} == {"again", "msg", "n"}
    assert judge.unify("<v#1>-<v#1>", "a-a", {}) == [] and judge.unify("<v#1>-<v#1>", "a-b", {}) != []
    assert judge.unify("<v#1>", 17, {"<v#1>": "17"}) == []  # bound whole, seen inside a string
    assert judge.unify([1, 2], [1], {})[0]["why"] == "expected 2 items"


def test_times_tokens_and_hashes_are_free_ids_are_bound():
    tpl = {"created": "<time#1>", "updated": "<time#1>", "at": "at <time#1>", "token": "<token:u1>",
           "again": "<token:u1>", "who": "<id:u1>", "who2": "<id:u1>"}
    actual = {"created": "t1", "updated": "t2", "at": "at t3", "token": "a", "again": "b", "who": "u", "who2": "w"}
    assert [m["path"] for m in judge.unify(tpl, actual, {})] == ["who2"]
    assert judge.unify("<time#1>", {"x": 1}, {})[0]["why"] == "expected a single value"
    assert judge.free_symbol("<hash#3>") and not judge.free_symbol("<uuid#3>") and not judge.free_symbol("<id:u2>")
    assert judge.unify({"a": 1}, {"a": 1, "b": 2}, {})[0]["extra"] == ["b"]


def test_derive_run_config(tmp_path: Path):
    root = tmp_path / "mono"
    root.mkdir()
    (root / "package.json").write_text(json.dumps({"main": "missing.js", "scripts": {"start": "node src/main.js"}}))
    (root / "src").mkdir()
    (root / "src" / "main.js").write_text("const p = process.env.APP_PORT || 3000;")
    (root / "node_modules").mkdir()
    (root / "node_modules" / "x.js").write_text("process.env.PORT")
    facts = type("F", (), {"monolith_root": str(root)})()
    rc = judge.derive_run_config(facts)
    assert (rc["entry"], rc["port_env"], rc["runtime"]) == ("src/main.js", "APP_PORT", "node")
    eff = judge.effective_run_config(facts, {"env": {"A": "1"}, "startup_timeout_s": 5, "note": "x"})
    assert eff["env"] == {"A": "1"} and eff["startup_timeout_s"] == 5 and "note" not in eff
    (root / "package.json").unlink()
    (root / "app.py").write_text("")
    with pytest.raises(judge.JudgeError, match="which WSGI application"):
        judge.effective_run_config(facts, None)
    assert judge.effective_run_config(facts, {"app": "app:app", "python": "py"})["runtime"] == "python"


def test_build_units_orders_aliases_and_adds_fault_runs():
    assets = {
        "cases": [{"id": "b", "identity": "u2", "fixtures": ["f1", "f2"],
                   "steps": [{"name": "s", "method": "GET", "path": "/x/${u1.id}", "identity": "u3"}]},
                  {"id": "a", "steps": [{"name": "s", "method": "GET", "path": "/"}]}],
        "fixtures": {"f1": {"T": [{"k": "1"}]}, "f2": {"T": [{"k": "2"}], "U": [{"k": "3"}]}},
        "faults": [{"id": "boom", "cases": ["b"], "rules": [{"kind": "error"}]}]}
    units = judge.build_units(assets)
    assert [u["id"] for u in units] == ["a", "b", "boom/b"]
    assert units[1]["aliases"] == ["u2", "u3", "u1"]
    assert units[1]["fixtures"] == {"T": [{"k": "1"}, {"k": "2"}], "U": [{"k": "3"}]}
    assert units[2]["rules"] == [{"kind": "error"}] and units[0]["rules"] == []


# ---------------------------------------------------------------------------
# run_monolith end to end, on a tiny monolith (node + dynalite)
# ---------------------------------------------------------------------------

TOOLS = Path(__file__).resolve().parents[1] / "verify-tools"
NODE = shutil.which("node")

APP = r"""
const http = require('http');
const fs = require('fs');
const crypto = require('crypto');
const { DynamoDBClient, PutItemCommand, QueryCommand } = require('@aws-sdk/client-dynamodb');
const ddb = new DynamoDBClient({});
const users = {};
// Each start bumps a counter: /api/flaky has 1 item on odd starts, 2 on even.
const starts = Number(fs.existsSync(process.env.COUNTER) ? fs.readFileSync(process.env.COUNTER, 'utf8') : 0) + 1;
fs.writeFileSync(process.env.COUNTER, String(starts));
function json(res, status, body) { res.writeHead(status, { 'content-type': 'application/json' }); res.end(JSON.stringify(body)); }
http.createServer((req, res) => {
  let raw = '';
  req.on('data', (c) => { raw += c; });
  req.on('end', async () => {
    const body = raw ? JSON.parse(raw) : {};
    const who = users[(req.headers.authorization || '').replace('Bearer ', '')];
    if (req.method === 'POST' && req.url === '/api/users') {
      const userId = crypto.randomUUID();
      const token = 'tok-' + crypto.randomUUID();
      users[token] = userId;
      return json(res, 201, { userId, token });
    }
    if (req.url === '/api/flaky') return json(res, 200, starts % 2 ? ['a'] : ['a', 'b']);
    if (req.url === '/api/echo') return json(res, 200, body);
    if (req.url.startsWith('/api/notes')) {
      if (!who) return json(res, 401, { error: 'no' });
      if (body.text === 'crash') setImmediate(() => { throw new Error('boom'); });
      try {
        if (req.method === 'POST') {
          const noteId = crypto.randomUUID();
          await ddb.send(new PutItemCommand({ TableName: 'Notes', Item: { userId: { S: who }, noteId: { S: noteId },
            text: { S: body.text }, createdAt: { S: new Date().toISOString() } } }));
          return json(res, 201, { noteId, message: 'note ' + noteId + ' saved' });
        }
        const r = await ddb.send(new QueryCommand({ TableName: 'Notes', KeyConditionExpression: 'userId = :u',
          ExpressionAttributeValues: { ':u': { S: who } } }));
        return json(res, 200, r.Items.map((i) => ({ userId: i.userId.S, noteId: i.noteId.S, text: i.text.S, createdAt: i.createdAt.S })));
      } catch (e) {
        return json(res, 500, { error: e.name });
      }
    }
    json(res, 404, { error: 'not found' });
  });
}).listen(process.env.PORT);
"""


def _resp(status, kind="error"):
    return {"status": status, "kind": kind, "when": "w", "source": []}


def _monolith_ws(tmp_path: Path, flaky: bool = False, crash: bool = False) -> Path:
    mono = tmp_path / "mono"
    mono.mkdir()
    (mono / "package.json").write_text(json.dumps({"main": "app.js"}))
    (mono / "app.js").write_text(APP)
    eps = [{"method": "POST", "path": "/api/users", "responses": [_resp(201, "success")], "data_access": []},
           {"method": "POST", "path": "/api/notes", "responses": [_resp(401), _resp(201, "success")],
            "data_access": [{"operation": "put", "table": {"default_name": "Notes"}}]},
           {"method": "GET", "path": "/api/notes", "responses": [_resp(401), _resp(200, "success")],
            "data_access": [{"operation": "query", "table": {"default_name": "Notes"}}]},
           {"method": "DELETE", "path": "/api/echo", "responses": [_resp(200, "success")], "data_access": []},
           {"method": "GET", "path": "/api/flaky", "responses": [_resp(200, "success")], "data_access": []}]
    facts = {"spec_meta": {"monolith_root": str(mono)}, "entry_points": eps if flaky else eps[:4],
             "dynamodb_info": {"table_schemas": [{"table": {"default_name": "Notes"},
                                                  "partition_key": {"name": "userId", "type": "S"},
                                                  "sort_key": {"name": "noteId", "type": "S"}}]}}
    ws = tmp_path / "ws"
    (ws / "storage").mkdir(parents=True)
    (ws / "storage" / "analysis_report.json").write_text(json.dumps(facts))
    jd = ws / "judge"

    def w(rel, data):
        p = jd / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data))

    w("identities.json", {"u1": {"monolith": {"steps": [{"name": "signup", "method": "POST", "path": "/api/users",
                                                         "save": {"id": "body.userId", "token": "body.token"}}],
                                              "header": {"Authorization": "Bearer ${token}"}}}})
    w("run_config.json", {"env": {"NODE_PATH": str(TOOLS / "node_modules"), "COUNTER": str(tmp_path / "starts")}})
    w("cases/post-api-users/signup.json", {"endpoint": "POST /api/users", "layer": "P",
                                           "fact_refs": ["POST /api/users#responses[0]"],
                                           "steps": [{"name": "signup", "method": "POST", "path": "/api/users"}],
                                           "predicted": {"signup": {"status": 201}}})
    w("cases/post-api-notes/note-add-read.json", {
        "endpoint": "POST /api/notes", "layer": "P", "identity": "u1",
        "fact_refs": ["POST /api/notes#responses[1]", "GET /api/notes#responses[1]"],
        "steps": [{"name": "add", "method": "POST", "path": "/api/notes", "body": {"text": "hi"}},
                  {"name": "read", "method": "GET", "path": "/api/notes", "read_back": True}],
        "predicted": {"add": {"status": 201}, "read": {"status": 200}}})
    w("cases/post-api-notes/notes-anonymous.json", {
        "endpoint": "POST /api/notes", "layer": "P",
        "fact_refs": ["POST /api/notes#responses[0]", "GET /api/notes#responses[0]"],
        "steps": [{"name": "add", "method": "POST", "path": "/api/notes", "body": {"text": "x"}},
                  {"name": "read", "method": "GET", "path": "/api/notes"}],
        "predicted": {"add": {"status": 401}, "read": {"status": 401}}})
    w("cases/get-api-notes/notes-empty.json", {"endpoint": "GET /api/notes", "layer": "P", "identity": "u1",
                                               "fact_refs": ["GET /api/notes#responses[1]"],
                                               "steps": [{"name": "read", "method": "GET", "path": "/api/notes"}],
                                               "predicted": {"read": {"status": 200, "body": {"equals": []}}}})
    w("cases/delete-api-echo/echo.json", {"endpoint": "DELETE /api/echo", "layer": "P",
                                          "fact_refs": ["DELETE /api/echo#responses[0]"],
                                          "steps": [{"name": "echo", "method": "DELETE", "path": "/api/echo",
                                                     "body": {"bookId": "b1"}}],
                                          "predicted": {"echo": {"status": 200}}})
    w("faults/notes-write-fails.json", {"description": "the note write fails", "cases": ["note-add-read"],
                                        "rules": [{"match": {"table": "Notes", "op": "PutItem"}, "kind": "error"}]})
    if flaky:
        w("cases/get-api-flaky/flaky.json", {"endpoint": "GET /api/flaky", "layer": "P",
                                             "fact_refs": ["GET /api/flaky#responses[0]"],
                                             "steps": [{"name": "get", "method": "GET", "path": "/api/flaky"}],
                                             "predicted": {"get": {"status": 200}}})
    if crash:
        w("cases/post-api-notes/note-crash.json", {
            "endpoint": "POST /api/notes", "layer": "P", "identity": "u1", "fact_refs": ["POST /api/notes#responses[1]"],
            "steps": [{"name": "add", "method": "POST", "path": "/api/notes", "body": {"text": "crash"}},
                      {"name": "again", "method": "POST", "path": "/api/notes", "body": {"text": "x"}}],
            "predicted": {"add": {"status": 201}, "again": {"status": 201}}})
    return ws


needs_node = pytest.mark.skipif(NODE is None or not (TOOLS / "node_modules").exists(),
                                reason="needs node and verify-tools/node_modules")


@needs_node
def test_run_monolith_end_to_end(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("M2S_DDB_BACKEND", "dynalite")
    monkeypatch.setenv("M2S_VERIFY_TMP", str(tmp_path / "tmp"))
    monkeypatch.setattr(judge.verify, "TMP_ROOT", tmp_path / "tmp")
    ws = _monolith_ws(tmp_path)
    out = judge.run_monolith(ws)
    assert out["status"] == "PASS", json.dumps(out, indent=1)[:3000]
    assert (out["backend"], out["cases"], out["fault_runs"]) == ("dynalite", 5, 1)
    oracle = json.loads((ws / "judge" / "oracle_expected.json").read_text(encoding="utf-8"))
    assert oracle["meta"]["backend"] == "dynalite" and oracle["meta"]["run_config"]["entry"] == "app.js"
    steps = oracle["cases"]["note-add-read"]["steps"]
    note = steps["add"]["body"]["noteId"]
    assert judge.WHOLE_SYMBOL_RE.match(note) and steps["add"]["body"]["message"] == f"note {note} saved"
    item = steps["read"]["body"][0]
    assert item["userId"] == "<id:u1>" and item["noteId"] == note and item["createdAt"].startswith("<time#")
    assert oracle["cases"]["note-add-read"]["tables_after"]["Notes"][0]["userId"] == "<id:u1>"
    assert steps["read"]["headers"] == {"content-type": "application/json"}  # date / connection dropped
    assert oracle["cases"]["notes-anonymous"]["steps"]["add"]["status"] == 401
    # A DELETE body reaches the server (Node sends no length for it unless told).
    assert oracle["cases"]["echo"]["steps"]["echo"]["body"] == {"bookId": "b1"}
    fault = json.loads((ws / "judge" / "fault_baseline_monolith.json").read_text(encoding="utf-8"))
    failed = fault["scenarios"]["notes-write-fails"]["note-add-read"]
    # The rule hit (how many times is the SDK's retrying, not compared), then the app answers 500.
    assert failed["rules_fired"] == [True] and failed["steps"]["add"]["status"] == 500
    cov = json.loads((ws / "judge" / "coverage_monolith" / "summary.json").read_text(encoding="utf-8"))
    assert "app.js" in cov["files"] and cov["files"]["app.js"]["covered"] > 0
    per_case = json.loads((ws / "judge" / "coverage_monolith" / "units" / "notes-anonymous.json").read_text(encoding="utf-8"))
    full = json.loads((ws / "judge" / "coverage_monolith" / "units" / "note-add-read.json").read_text(encoding="utf-8"))
    assert set(per_case["app.js"]) < set(full["app.js"])  # the anonymous case stops at the 401
    assert (ws / "judge" / "masks.json").is_file() and (ws / "judge" / "monolith_calls.json").is_file()


@needs_node
def test_run_monolith_reports_what_cannot_be_masked(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("M2S_DDB_BACKEND", "dynalite")
    monkeypatch.setattr(judge.verify, "TMP_ROOT", tmp_path / "tmp")
    ws = _monolith_ws(tmp_path, flaky=True)
    out = judge.run_monolith(ws, coverage=False)
    assert out["status"] == "FAIL"
    flaky = [u for u in out["unstable"] if u["unit"] == "flaky"]
    assert flaky and flaky[0]["between"] == "passes 1 and 2" and "items between passes" in flaky[0]["why"]
    assert out["problems"] == []


@needs_node
def test_a_crash_is_reported_and_the_next_case_gets_a_fresh_monolith(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("M2S_DDB_BACKEND", "dynalite")
    monkeypatch.setattr(judge.verify, "TMP_ROOT", tmp_path / "tmp")
    ws = _monolith_ws(tmp_path, crash=True)
    out = judge.run_monolith(ws, coverage=False)
    assert out["status"] == "FAIL"
    whys = {(p["unit"], p["why"]) for p in out["problems"]}
    assert ("note-crash", "the monolith exited during this case") in whys
    assert {p["unit"] for p in out["problems"]} == {"note-crash"}
    oracle = json.loads((ws / "judge" / "oracle_expected.json").read_text(encoding="utf-8"))
    assert oracle["cases"]["note-crash"]["monolith_exited"] is True
    assert oracle["cases"]["notes-anonymous"]["steps"]["add"]["status"] == 401


def test_run_monolith_needs_passing_assets(tmp_path: Path):
    ws = tmp_path / "ws"
    (ws / "storage").mkdir(parents=True)
    (ws / "storage" / "analysis_report.json").write_text(json.dumps({"entry_points": []}))
    out = judge.run_monolith(ws)
    assert out["status"] == "FAIL" and "check_judge_assets" in out["error"]


# ---------------------------------------------------------------------------
# Python (Flask) monoliths (N3.1)
# ---------------------------------------------------------------------------

FLASK_APP = r'''
import os, uuid, datetime
import boto3
from flask import Flask, request, jsonify

app = Flask(__name__)
table = boto3.resource("dynamodb", region_name="us-east-1").Table(
    os.environ.get("NOTES_TABLE", "Notes-" + os.environ.get("STAGE", "test")))
users = {}


@app.route("/api/users", methods=["POST"])
def signup():
    user_id, token = str(uuid.uuid4()), "tok-" + str(uuid.uuid4())
    users[token] = user_id
    return jsonify({"userId": user_id, "token": token}), 201


@app.route("/api/notes", methods=["POST", "GET"])
def notes():
    who = users.get(request.headers.get("Authorization", "").replace("Bearer ", ""))
    if not who:
        return jsonify({"error": "no"}), 401
    try:
        if request.method == "POST":
            note_id = str(uuid.uuid4())
            table.put_item(Item={"userId": who, "noteId": note_id, "text": request.get_json()["text"],
                                 "createdAt": datetime.datetime.now().isoformat()})
            return jsonify({"noteId": note_id}), 201
        items = table.query(KeyConditionExpression="userId = :u", ExpressionAttributeValues={":u": who})["Items"]
        return jsonify(items)
    except Exception as e:
        return jsonify({"error": type(e).__name__}), 500
'''


def test_python_run_config_and_table_names(tmp_path: Path):
    mono = tmp_path / "mono"
    (mono / "pkg").mkdir(parents=True)
    (mono / "requirements.txt").write_text("Flask==3.0.0\n")
    (mono / "server.py").write_text("from flask import Flask\napplication = Flask(__name__)\n"
                                    "T = os.environ.get('STAGE', 'dev')\n")
    ws = tmp_path / "ws"
    (ws / "storage").mkdir(parents=True)
    (ws / "storage" / "analysis_report.json").write_text(json.dumps({
        "spec_meta": {"monolith_root": str(mono)}, "entry_points": [],
        "dynamodb_info": {"table_schemas": [{"table": {"default_name": "Flights-{stage}"}, "partition_key": {"name": "id"}},
                                            {"table": {"default_name": "Plain"}, "partition_key": {"name": "id"}}]}}))
    facts = judge.Facts(ws)
    rc = judge.derive_run_config(facts)
    assert (rc["runtime"], rc["app"], rc["python"], rc["debug"]) == ("python", "server:application", None, False)
    (mono / "server.py").write_text("x = 1\n")
    (mono / "pkg" / "__init__.py").write_text("def create_app():\n    pass\n")
    assert judge.derive_run_config(facts)["app"] == "pkg:create_app()"
    (mono / "pkg" / "config.py").write_text("STAGE = os.environ.get('STAGE', 'dev')\n")
    assert judge.monolith_table_names(facts) == {"Flights-{stage}": "Flights-dev", "Plain": "Plain"}
    assert judge.monolith_table_names(facts, {"Plain": "Plain-x"})["Plain"] == "Plain-x"
    renamed = judge._rename_tables({"Flights-{stage}": [{"id": "1"}], "x": {"table": "Flights-{stage}", "tables": ["Plain"]}},
                                   {"Flights-{stage}": "Flights-dev", "Plain": "P2"})
    assert renamed == {"Flights-dev": [{"id": "1"}], "x": {"table": "Flights-dev", "tables": ["P2"]}}
    errs = judge._check_run_config(facts, {"debug": True, "python": "py", "app": "a:b", "table_names": {"Nope": "x"}})
    assert errs == ["judge/run_config.json: table_names: 'Nope' is not a table of the fact base"]


def _has(*mods):
    import importlib.util
    return all(importlib.util.find_spec(m) for m in mods)


@needs_node
@pytest.mark.skipif(not _has("flask", "boto3", "werkzeug"), reason="needs flask and boto3 in this interpreter")
def test_run_monolith_on_a_flask_monolith(tmp_path: Path, monkeypatch):
    import sys as _sys
    monkeypatch.setenv("M2S_DDB_BACKEND", "dynalite")
    monkeypatch.setattr(judge.verify, "TMP_ROOT", tmp_path / "tmp")
    ws = _monolith_ws(tmp_path)
    mono = tmp_path / "mono"
    (mono / "package.json").unlink()
    (mono / "app.js").unlink()
    (mono / "app.py").write_text(FLASK_APP)
    (mono / "requirements.txt").write_text("Flask\nboto3\n")
    facts_path = ws / "storage" / "analysis_report.json"
    facts = json.loads(facts_path.read_text())
    facts["dynamodb_info"]["table_schemas"][0]["table"]["default_name"] = "Notes-{stage}"
    for ep in facts["entry_points"]:
        for d in ep["data_access"]:
            d["table"]["default_name"] = "Notes-{stage}"
    facts["entry_points"] = facts["entry_points"][:3]
    facts_path.write_text(json.dumps(facts))
    jd = ws / "judge"
    (jd / "cases" / "delete-api-echo" / "echo.json").unlink()
    fault = json.loads((jd / "faults" / "notes-write-fails.json").read_text())
    fault["rules"][0]["match"]["table"] = "Notes-{stage}"
    (jd / "faults" / "notes-write-fails.json").write_text(json.dumps(fault))
    (jd / "run_config.json").write_text(json.dumps({"python": _sys.executable}))
    coverage = _has("coverage")
    out = judge.run_monolith(ws, coverage=coverage)
    assert out["status"] == "PASS", json.dumps(out, indent=1)[:3000]
    oracle = json.loads((jd / "oracle_expected.json").read_text(encoding="utf-8"))
    assert oracle["meta"]["run_config"]["app"] == "app:app"
    case = oracle["cases"]["note-add-read"]
    item = case["steps"]["read"]["body"][0]
    assert item["userId"] == "<id:u1>" and item["noteId"] == case["steps"]["add"]["body"]["noteId"]
    # the monolith used Notes-test; the record names the table as the fact base does
    assert case["tables_after"]["Notes-{stage}"][0]["userId"] == "<id:u1>"
    failed = json.loads((jd / "fault_baseline_monolith.json").read_text(encoding="utf-8"))["scenarios"]["notes-write-fails"]
    assert failed["note-add-read"]["rules_fired"] == [True] and failed["note-add-read"]["steps"]["add"]["status"] == 500
    if coverage:
        cov = json.loads((jd / "coverage_monolith" / "summary.json").read_text(encoding="utf-8"))
        assert cov["files"]["app.py"]["covered"] > 0


def test_masks_record_an_identity_value_the_same_in_both_passes(tmp_path: Path):
    ws = tmp_path / "ws"
    (ws / "storage").mkdir(parents=True)
    (ws / "storage" / "analysis_report.json").write_text(json.dumps({"entry_points": []}))
    facts = judge.Facts(ws)

    def run(uid, other):
        return {"identities": {"u1": {"vars": {"id": uid}}, "u2": {"vars": {"id": other}}},
                "steps": [{"name": "s", "response": {"status": 200, "headers": {}, "body_kind": "json",
                                                     "body": {"a": uid, "b": other}}}], "tables": {}}

    m = judge.mask_unit(facts, [run("judge-u1", U1), run("judge-u1", U2)])
    assert m["template"]["steps"]["s"]["body"] == {"a": "<id:u1>", "b": "<id:u2>"}
    assert m["symbols"]["<id:u1>"]["value"] == "judge-u1" and "value" not in m["symbols"]["<id:u2>"]


@needs_node
def test_records_do_not_name_the_proxys_random_port():
    """An SDK error can name its endpoint; the port differs in every process, and a later
    run (a mutant, the target) must not differ because of it (found on airline-j1)."""
    import subprocess
    code = ("const {maskEndpoint: m} = require('./ddb-proxy');"
            "console.log(m('URL: \"http://127.0.0.1:55767/\". http://host.docker.internal:55767/q "
            "127.0.0.1:557670 http://127.0.0.1:5000/ localhost:55767', 55767));")
    out = subprocess.run([NODE, "-e", code], cwd=TOOLS, capture_output=True, text=True, encoding="utf-8", check=True).stdout.strip()
    assert out == 'URL: "http://m2s-proxy/". http://m2s-proxy/q 127.0.0.1:557670 http://127.0.0.1:5000/ http://m2s-proxy'


def test_a_stack_trace_is_masked_and_the_target_only_needs_a_string():
    """V2P-18: the monolith's stack is the same in every pass, and no target can repeat it."""
    stack = "ValidationException: nope\n    at Request.extractError (D:\\mono\\json.js:80:27)\n    at emit (x.js:1:2)"
    py = 'Traceback (most recent call last):\n  File "app.py", line 3, in f\nValueError: x'
    s = judge.Symbolizer()
    body = {"error": "nope", "stack": stack, "py": py, "text": "line one\nline two at the end"}
    t = s.walk(body, dict(body))
    assert t["error"] == "nope" and t["text"] == body["text"]
    assert t["stack"].startswith("<trace#") and t["py"].startswith("<trace#")
    assert judge.free_symbol(t["stack"]) and s.unstable == []
    other = "ValidationException: nope\n    at ProtocolLib.get (/var/task/x.js:79:67)"
    assert judge.unify(t, {**body, "stack": other, "py": "whatever"}, {}) == []
    # the error text is still compared, and so is whether there is a stack at all
    assert judge.unify(t, {**body, "error": "other", "stack": other}, {})[0]["path"] == "error"
    assert judge.unify(t, {**body, "stack": 5}, {})[0]["why"].startswith("expected a string")
    missing = {k: v for k, v in body.items() if k != "stack"}
    assert judge.unify(t, missing, {})[0]["missing"] == ["stack"]
