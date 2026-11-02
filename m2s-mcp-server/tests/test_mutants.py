"""Tests for mutants.py (J5, PIPELINE_V2_PLAN.md §4.10, N3.3): the operators in
JavaScript and Python, mutants located by the fact base, which units can see a
mutant, and the classification check."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import judge  # noqa: E402
import mutants  # noqa: E402


def test_js_operators():
    m = mutants.mutate_line
    assert m("    return res.status(400).json({ error: 'x' });", "status", "js") == "    return res.status(422).json({ error: 'x' });"
    assert m("  if (!bookId || !quantity) {", "negate", "js") == "  if (!(!bookId || !quantity)) {"
    assert m("  if (a(b) && c) {", "branch_off", "js") == "  if (false) {"
    assert m("res.json({ message: 'ok', total: n })", "field", "js", ["message"]) == "res.json({ message_m2s: 'ok', total: n })"
    assert m("x.message = 1", "field", "js", ["message"]) is None  # not an object key
    assert m("app.use('/api/cart', authMiddleware, cartRoutes);", "auth", "js", middleware=["authMiddleware"]) \
        == "app.use('/api/cart', cartRoutes);"
    assert m("const x = 1;", "negate", "js") is None and m("if (a", "negate", "js") is None  # no closing paren on the line


def test_python_operators():
    m = mutants.mutate_line
    assert m("        return jsonify({'error': 'x'}), 400", "status", "py") == "        return jsonify({'error': 'x'}), 422"
    assert m("    if not data or 'email' not in data:", "negate", "py") == "    if not (not data or 'email' not in data):"
    assert m("    elif x > 1:  # note", "branch_off", "py") == "    elif False:"
    assert m("    return jsonify({'error': str(e), 'type': t}), 500", "field", "py", ["type"]) \
        == "    return jsonify({'error': str(e), 'type_m2s': t}), 500"
    assert m("@login_required", "auth", "py", middleware=["login_required"]) == ""
    assert m("@app.route('/x')", "auth", "py", middleware=["login_required"]) is None


def _ws(tmp_path: Path) -> Path:
    mono = tmp_path / "shop"
    (mono / "routes").mkdir(parents=True)
    (mono / "routes" / "cart.js").write_text(
        "router.post('/', async (req, res) => {\n"
        "  if (!req.body.bookId) {\n"
        "    return res.status(400).json({ error: 'missing' });\n"
        "  }\n"
        "  res.json({ message: 'added' });\n"
        "});\n", encoding="utf-8")
    (mono / "server.js").write_text("app.use('/api/cart', authMiddleware, cartRoutes);\n", encoding="utf-8")
    (mono / "node_modules" / "x").mkdir(parents=True)
    src = lambda f, l: {"file": f"shop/{f}", "line": l}  # noqa: E731
    facts = {"spec_meta": {"monolith_root": str(mono)},
             "entry_points": [{"method": "POST", "path": "/api/cart",
                               "auth": {"classification": "required", "middleware": ["authMiddleware"], "source": [src("server.js", 1)]},
                               "responses": [{"status": 400, "body": {"fields": ["error"]}, "source": [src("routes/cart.js", 2), src("routes/cart.js", 3)]},
                                             {"status": 200, "body": {"fields": ["message"]}, "source": [src("routes/cart.js", 5),
                                                                                                         {"file": "shop/node_modules/x/i.js", "line": 1}]}],
                               "business_rules": [],
                               "data_access": [{"operation": "put", "table": {"default_name": "Cart"}}]}],
             "dynamodb_info": {"table_schemas": [{"table": {"default_name": "Cart"}, "partition_key": {"name": "id"}}]}}
    ws = tmp_path / "ws"
    (ws / "storage").mkdir(parents=True)
    (ws / "storage" / "analysis_report.json").write_text(json.dumps(facts), encoding="utf-8")
    return ws


def test_mutants_are_located_by_the_fact_base(tmp_path: Path):
    facts = judge.Facts(_ws(tmp_path))
    ms = {m["id"]: m for m in mutants.generate(facts)}
    assert set(ms) == {"negate:routes/cart.js:2", "branch_off:routes/cart.js:2", "status:routes/cart.js:3",
                       "field:routes/cart.js:3", "field:routes/cart.js:5", "auth:server.js:1",
                       "drop_write:Cart:PutItem", "serial"}
    assert ms["auth:server.js:1"]["after"] == "app.use('/api/cart', cartRoutes);"
    assert ms["status:routes/cart.js:3"]["refs"] == ["POST /api/cart#responses[0]"]
    assert ms["serial"]["reverse"] is True


def test_which_units_can_see_a_mutant(tmp_path: Path):
    facts = judge.Facts(_ws(tmp_path))
    units = [{"id": "add", "steps": [{"method": "POST", "path": "/api/cart"}]},
             {"id": "other", "steps": [{"method": "GET", "path": "/api/books"}]}]
    coverage = {"add": {"routes/cart.js": {2, 3}}, "other": {}}
    calls = {"add": [{"op": "PutItem", "table": "Cart"}], "other": [{"op": "Query", "table": "Cart"}]}
    by = {m["id"]: mutants.affected_units(m, units, coverage, calls, facts) for m in mutants.generate(facts)}
    assert by["status:routes/cart.js:3"] == ["add"]
    assert by["field:routes/cart.js:5"] == ["add"]  # not covered by a unit: the cases calling the endpoint citing it
    assert by["auth:server.js:1"] == ["add"]  # load-time code: the cases calling the endpoint
    assert by["drop_write:Cart:PutItem"] == ["add"] and by["serial"] == ["add", "other"]


def test_copy_links_node_modules_and_the_removal_never_follows_the_link(tmp_path: Path):
    ws = _ws(tmp_path)
    mono = tmp_path / "shop"
    (mono / "node_modules" / "x" / "keep.js").write_text("1", encoding="utf-8")
    dst = tmp_path / "copy" / "shop"
    mutants._copy_root(mono, dst)
    assert (dst / "node_modules" / "x" / "keep.js").is_file() and mutants._is_junction(dst / "node_modules")
    m = next(x for x in mutants.generate(judge.Facts(ws)) if x["id"] == "status:routes/cart.js:3")
    mutants._apply(dst, m)
    assert "status(422)" in (dst / "routes" / "cart.js").read_text(encoding="utf-8")
    assert "status(400)" in (mono / "routes" / "cart.js").read_text(encoding="utf-8")
    mutants._remove_copy(dst)
    assert not dst.exists() and (mono / "node_modules" / "x" / "keep.js").is_file()


def test_survivors_must_be_classified(tmp_path: Path):
    ws = _ws(tmp_path)
    jd = ws / "judge"
    judge._write(jd / mutants.VALIDATION, {"summary": {}, "mutants": [
        {"id": "a", "outcome": "survived"}, {"id": "b", "outcome": "killed"}, {"id": "c", "outcome": "not_covered"},
        {"id": "serial", "outcome": "survived", "reverse": True}]})
    r = mutants.check_mutant_classes(ws)
    assert r["status"] == "FAIL" and r["survivors"] == 2 and "a: not classified" in r["errors"]
    judge._write(jd / mutants.CLASSES, {"a": {"class": "gap", "reason": "x", "suggest": "y"},
                                        "c": {"class": "equivalent", "reason": "dev mode only"}, "old": {"class": "gap", "reason": "z"}})
    r = mutants.check_mutant_classes(ws)
    assert r["status"] == "PASS" and r["classes"] == {"equivalent": 1, "gap": 1}
    assert [g["id"] for g in mutants.gaps(ws)] == ["a"] and mutants.gaps(ws)[0]["suggest"] == "y"
