"""v3 M2 (W2.7): split-by-package expansion and its dependency graph.

A minimal template plays the v1 line: P0 expands S0/V1, S1 expands S2 (plus
S2:shared), and S3 waits for every S2 child. Fake workers and always-pass
checks keep it fast; the assertions are about the board graph, not the work.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from coord_fakes import make_dispatcher  # noqa: E402

from coord.checks import CheckResult  # noqa: E402
from coord.dispatcher import EXIT_DONE, EXIT_STOPPED  # noqa: E402
from coord.template import TaskSpec, load_template  # noqa: E402


def test_dynamic_spec_keeps_package_metadata():
    spec = TaskSpec.from_json({"id": "S2-books", "package": "books",
                               "endpoints": ["GET /api/books"], "functions": ["books-list"]})
    data = spec.to_json()
    assert data["package"] == "books"
    assert data["functions"] == ["books-list"]
    assert data["endpoints"] == ["GET /api/books"]

TEMPLATE = """\
name: split-test
tasks:
  - id: P0
    runner: dispatcher
    action: analyze_project
    scope: [storage/analysis_report.json]
    check: [ok]
  - id: S0
    role: m2s-spec-annotator
    split: package
    expand_after: [P0]
    deps: [P0]
    scope: ["storage/stage_reports/S0-{pkg}.json"]
    check: [ok]
  - id: S1
    role: m2s-architect
    deps: ["S0:*"]
    scope: [storage/blueprint.json, storage/stage_reports/S1.json]
    check: [ok]
  - id: V1
    role: m2s-verifier
    split: package
    expand_after: [P0]
    deps: [S1, "S0:*"]
    scope: ["verification/**", "storage/stage_reports/V1-{pkg}.json"]
    check: [ok]
  - id: S2
    role: m2s-code-developer
    split: package
    split_shared: true
    expand_after: [S1]
    deps: [S1, "V1:*"]
    scope: ["output/lambdas/{pkg_dir}/**", "storage/stage_reports/S2-{pkg}.json"]
    shared_scope: [output/layers/**, storage/stage_reports/S2-shared.json]
    check: [ok]
  - id: S3
    role: m2s-sam-engineer
    deps: ["S2:*"]
    scope: [output/template.yaml, storage/stage_reports/S3.json]
    check: [ok]
"""


def _report() -> dict:
    endpoints = []
    for group in ("alpha", "beta", "gamma"):
        for i in range(4):
            endpoints.append({
                "method": "GET", "path": f"/{group}/thing{i}",
                "handler_location": {"file": "app/server.js", "start_line": 1, "end_line": 5},
                "auth": {"classification": "none", "middleware": []},
                "request": {"path_params": [], "query": [], "body": [], "headers": []},
                "data_access": [],
            })
    return {"entry_points": endpoints}


def _ok(ws: Path, spec: dict) -> CheckResult:
    return CheckResult("ok", True, "ok")


def test_split_families_expand_and_wire(tmp_path):
    template_path = tmp_path / "split.yaml"
    template_path.write_text(TEMPLATE, encoding="utf-8")
    template = load_template(path=template_path)
    ws = tmp_path / "ws" / "storage"
    ws.mkdir(parents=True)
    (ws / "analysis_report.json").write_text(json.dumps(_report()), encoding="utf-8")
    (tmp_path / "ws" / "storage" / "blueprint.json").write_text(json.dumps({
        "lambda_functions": [
            {"name": "alpha-fn", "entry_points": [f"GET /alpha/thing{i}" for i in range(4)]},
            {"name": "beta-fn", "entry_points": [f"GET /beta/thing{i}" for i in range(4)]},
            {"name": "gamma-fn", "entry_points": [f"GET /gamma/thing{i}" for i in range(4)]},
        ],
    }), encoding="utf-8")

    d = make_dispatcher(tmp_path, {}, template=template, checks={"ok": _ok})
    d.setup()
    assert d.run() == EXIT_DONE, d.board.status("S3")

    tasks = d.board.tasks()

    def board_deps(task: str) -> set[str]:
        show = d.board.br.run("show", tasks[task]["issue"], "--json").json()
        row = show[0] if isinstance(show, list) else show
        return {x["title"] for x in row.get("dependencies") or []}

    children = sorted(t for t in tasks if t.startswith("S2-"))
    assert children == ["S2-alpha", "S2-beta", "S2-gamma", "S2-shared"]
    assert {"S0-alpha", "S0-beta", "S0-gamma"} <= board_deps("S1")
    for pkg in ("alpha", "beta", "gamma"):
        assert f"S0-{pkg}" in tasks and f"V1-{pkg}" in tasks
        # S2:<pkg> depends on its own V1 child and on S1
        spec = tasks[f"S2-{pkg}"]["spec"]
        assert f"V1-{pkg}" in spec["deps"] and "S1" in spec["deps"]
        assert f"S0-{pkg}" in tasks[f"V1-{pkg}"]["spec"]["deps"]
        assert tasks[f"S2-{pkg}"]["spec"]["functions"] == [f"{pkg}-fn"]
        assert tasks[f"S2-{pkg}"]["spec"]["scope"][0] == f"output/lambdas/{pkg}/**"
    # the shared child uses shared_scope and waits for every V1 child
    shared = tasks["S2-shared"]["spec"]
    assert shared["scope"][0] == "output/layers/**"
    assert {d for d in shared["deps"] if d.startswith("V1-")} == {"V1-alpha", "V1-beta", "V1-gamma"}
    # S3 waited for every S2 child
    assert set(board_deps("S3")) >= set(children)


def test_stop_after_does_not_run_dynamic_children(tmp_path):
    template_path = tmp_path / "split.yaml"
    template_path.write_text(TEMPLATE, encoding="utf-8")
    template = load_template(path=template_path)
    ws = tmp_path / "ws" / "storage"
    ws.mkdir(parents=True)
    (ws / "analysis_report.json").write_text(json.dumps(_report()), encoding="utf-8")

    d = make_dispatcher(tmp_path, {}, template=template, checks={"ok": _ok}, stop_after="P0")
    d.setup()
    assert d.run() == EXIT_STOPPED
    assert not [e for e in d.events.all() if e["type"] == "spawn"]
    # the children exist (the expansion ran) but none of them started
    assert "S0-alpha" in d.board.tasks()


def test_upstream_ignores_split_family_deps():
    template = load_template("v1-split")
    assert "S3c" in template.upstream("S4")  # S2:* is a family, not a task id
    assert "S3c" in template.upstream("FINAL")


DOMAIN_TEMPLATE = """\
name: domain-test
tasks:
  - id: A1
    runner: dispatcher
    action: analyze_project
    scope: [storage/blueprint.json, storage/work_packages.json]
    check: [ok]
  - id: D1
    role: m2s-verifier
    deps: [A1]
    scope: [verification/delta/**, storage/stage_reports/D1.json]
    check: [ok]
  - id: I1
    role: m2s-code-developer
    split: domain
    split_shared: true
    expand_after: [A1]
    deps: [A1, D1]
    scope: ["output/lambdas/{app}/{fn}/**", "storage/stage_reports/I1-{pkg}.json"]
    shared_scope: [output/layers/**, storage/stage_reports/I1-shared.json]
    check: [ok]
  - id: I2
    role: m2s-sam-engineer
    deps: ["I1:*", "I1:shared"]
    scope: [output/template.yaml, storage/stage_reports/I2.json]
    check: [ok]
"""


def test_domain_split_expands_by_impl_packages(tmp_path):
    """v3 M4/W4.2: I1 follows the blueprint's domains (impl_packages), one child
    per domain with its functions and per-function directories, plus the shared
    layer; I2 waits for the children and the shared task."""
    template_path = tmp_path / "domain.yaml"
    template_path.write_text(DOMAIN_TEMPLATE, encoding="utf-8")
    template = load_template(path=template_path)
    ws = tmp_path / "ws"

    def a1(spec):
        (ws / "storage").mkdir(parents=True, exist_ok=True)
        (ws / "storage" / "work_packages.json").write_text(json.dumps({
            "version": 1,
            "packages": [{"id": "alpha"}, {"id": "beta"}],
            "impl_packages": {"alpha": {"functions": ["a1-fn", "a2-fn"], "dir": "alpha"},
                              "beta": {"functions": ["b1-fn"], "dir": "beta"}},
        }), encoding="utf-8")
        return CheckResult("analyze_project", True, "ok")

    d = make_dispatcher(tmp_path, {}, template=template, checks={"ok": _ok}, actions={"analyze_project": a1})
    d.setup()
    assert d.run() == EXIT_DONE, d.board.status("I2")

    tasks = d.board.tasks()
    assert "I1" not in tasks  # the family itself is not a board task
    assert {"I1-alpha", "I1-beta", "I1-shared"} <= set(tasks)
    alpha = tasks["I1-alpha"]["spec"]
    assert alpha["functions"] == ["a1-fn", "a2-fn"] and alpha["package"] == "alpha"
    assert alpha["scope"][0] == "output/lambdas/monolith/a1-fn/**"
    assert alpha["scope"][1] == "output/lambdas/monolith/a2-fn/**"
    assert "storage/stage_reports/I1-alpha.json" in alpha["scope"]
    assert tasks["I1-shared"]["spec"]["scope"][0] == "output/layers/**"

    def board_deps(task):
        show = d.board.br.run("show", tasks[task]["issue"], "--json").json()
        row = show[0] if isinstance(show, list) else show
        return {x["title"] for x in row.get("dependencies") or []}

    assert {"I1-alpha", "I1-beta", "I1-shared"} <= board_deps("I2")
    assert ["a1-fn", "a2-fn"] == tasks["I1-alpha"]["spec"]["functions"]


def test_v2_split_template_loads():
    template = load_template("v2-split")
    f2 = template.get("F2")
    assert f2.split == "package" and f2.split_shared and f2.extra.get("shared_after_children")
    i1 = template.get("I1")
    assert i1.split == "domain" and i1.split_shared and "I1:shared" in template.get("I2a").deps


def test_domain_split_falls_back_to_the_blueprint(tmp_path):
    """W4.2: when A1 did not fill impl_packages, the mapping is computed from
    the blueprint's entry_points and the work packages (the M3 open issue)."""
    template_path = tmp_path / "domain.yaml"
    template_path.write_text(DOMAIN_TEMPLATE, encoding="utf-8")
    template = load_template(path=template_path)
    ws = tmp_path / "ws"

    def a1(spec):
        (ws / "storage").mkdir(parents=True, exist_ok=True)
        (ws / "storage" / "work_packages.json").write_text(json.dumps({
            "version": 1,
            "packages": [
                {"id": "alpha", "endpoints": [{"method": "GET", "path": f"/alpha/thing{i}"} for i in range(4)]},
                {"id": "beta", "endpoints": [{"method": "GET", "path": "/beta/thing0"}]},
            ],
            "impl_packages": None,
        }), encoding="utf-8")
        (ws / "storage" / "blueprint.json").write_text(json.dumps({
            "lambda_functions": [
                {"name": "alpha-fn", "entry_points": ["GET /alpha/thing0", "GET /alpha/thing1"]},
                {"name": "beta-fn", "entry_points": ["GET /beta/thing0"]},
            ],
        }), encoding="utf-8")
        return CheckResult("analyze_project", True, "ok")

    d = make_dispatcher(tmp_path, {}, template=template, checks={"ok": _ok}, actions={"analyze_project": a1})
    d.setup()
    assert d.run() == EXIT_DONE, d.board.status("I2")

    tasks = d.board.tasks()
    assert tasks["I1-alpha"]["spec"]["functions"] == ["alpha-fn"]
    assert tasks["I1-beta"]["spec"]["functions"] == ["beta-fn"]
    assert any(e["type"] == "impl_packages_fallback" for e in d.events.all())


SHARED_AFTER_TEMPLATE = """\
name: shared-after-test
tasks:
  - id: F1
    runner: dispatcher
    action: analyze_project
    scope: [storage/analysis_report.json, storage/symbol_table.json]
    check: [ok]
  - id: F2
    role: m2s-spec-annotator
    split: package
    split_shared: true
    shared_after_children: true
    expand_after: [F1]
    deps: [F1]
    scope: [storage/analysis_report.json, "storage/stage_reports/F2-{pkg}.json"]
    shared_scope: [storage/analysis_report.json, storage/stage_reports/F2-shared.json]
    check: [ok]
  - id: G1
    runner: dispatcher
    action: analyze_project
    deps: ["F2:*", "F2:shared"]
    scope: [storage/gap_inventory.json]
    check: [ok]
"""


def test_shared_child_waits_for_the_package_children(tmp_path):
    """v2-split's F2-shared consolidates and validates the whole fact base, so
    it runs after every F2 child; G1 waits for both."""
    template_path = tmp_path / "shared.yaml"
    template_path.write_text(SHARED_AFTER_TEMPLATE, encoding="utf-8")
    template = load_template(path=template_path)
    ws = tmp_path / "ws" / "storage"
    ws.mkdir(parents=True)
    (ws / "analysis_report.json").write_text(json.dumps(_report()), encoding="utf-8")

    d = make_dispatcher(tmp_path, {}, template=template, checks={"ok": _ok})
    d.setup()
    assert d.run() == EXIT_DONE

    tasks = d.board.tasks()
    children = sorted(t for t in tasks if t.startswith("F2-"))
    assert children == ["F2-alpha", "F2-beta", "F2-gamma", "F2-shared"]

    def board_deps(task):
        show = d.board.br.run("show", tasks[task]["issue"], "--json").json()
        row = show[0] if isinstance(show, list) else show
        return {x["title"] for x in row.get("dependencies") or []}

    assert {"F2-alpha", "F2-beta", "F2-gamma"} <= board_deps("F2-shared")
    assert {"F2-alpha", "F2-beta", "F2-gamma", "F2-shared"} <= board_deps("G1")

    # J3/J4 fact corrections route to the child that owns the endpoint; an
    # endpoint no package owns goes to the shared child.
    targets = d._fact_targets([
        {"endpoint": "GET /alpha/thing0"},
        {"request": {"method": "GET", "path": "/beta/thing1"}},
        {"fact_refs": ["GET /gamma/thing2#responses[0]"]},
        {"endpoint": "GET /unknown"},
    ])
    assert sorted(targets) == ["F2-alpha", "F2-beta", "F2-gamma", "F2-shared"]
    assert targets["F2-alpha"][0]["endpoint"] == "GET /alpha/thing0"


def _run_split_ws(tmp_path):
    template_path = tmp_path / "split.yaml"
    template_path.write_text(TEMPLATE, encoding="utf-8")
    template = load_template(path=template_path)
    ws = tmp_path / "ws" / "storage"
    ws.mkdir(parents=True)
    (ws / "analysis_report.json").write_text(json.dumps(_report()), encoding="utf-8")
    (tmp_path / "ws" / "storage" / "blueprint.json").write_text(json.dumps({
        "lambda_functions": [
            {"name": "alpha-fn", "entry_points": [f"GET /alpha/thing{i}" for i in range(4)]},
            {"name": "beta-fn", "entry_points": [f"GET /beta/thing{i}" for i in range(4)]},
            {"name": "gamma-fn", "entry_points": [f"GET /gamma/thing{i}" for i in range(4)]},
        ],
    }), encoding="utf-8")
    d = make_dispatcher(tmp_path, {}, template=template, checks={"ok": _ok})
    d.setup()
    assert d.run() == EXIT_DONE, d.board.status("S3")
    return d


def test_rework_of_a_package_child_reaches_template_downstream(tmp_path):
    """v3 M3: reworking one package child must reach S3, which waits on the
    whole family through board edges the template calls "S2:*"."""
    d = _run_split_ws(tmp_path)
    assert d.board.status("S3") == "closed"
    assert "S3" in d._downstream(["S2-alpha"])

    d._request_rework("S2-alpha", "test feedback", reason="test", by="test")
    assert d.board.status("S2-alpha") == "open"
    assert d.board.status("S3") == "open"  # stale -> open, will recheck


def test_upstream_open_sees_a_reopened_child(tmp_path):
    """A task running while one of its board upstreams is no longer closed was
    started against the previous upstream; _reconcile sends it back."""
    d = _run_split_ws(tmp_path)
    assert d._upstream_open("S3") == []
    d.board.set_status("S2-alpha", "stale")
    d.board.set_status("S2-alpha", "open")
    assert "S2-alpha" in d._upstream_open("S3")


def test_upstream_reopened_uses_the_event_log(tmp_path):
    """The upstream may be closed again by the time the dispatcher restarts;
    the round_start event after the downstream task's spawn still proves it."""
    d = _run_split_ws(tmp_path)
    assert d._upstream_reopened("S3") == []
    d.events.append("round_start", task="S2-alpha", cause="rework", mode="rerun", by="test")
    assert d._upstream_reopened("S3") == ["S2-alpha"]


def test_route_target_picks_the_package_child_by_function(tmp_path):
    """v3 M2: V2 failures route to "code"; with work packages the role names
    the split family, so the routing entry's function picks its child."""
    d = _run_split_ws(tmp_path)
    assert d._route_target("code", "S3", "beta-fn") == "S2-beta"
    assert d._route_target("code", "S3") is None  # no function, no target


class _Exited:
    kind = "worker"

    def poll(self):
        return 0

    def timed_out(self):
        return False

    def kill(self):
        pass


def test_collect_survives_a_cancel_in_the_same_pass(tmp_path, monkeypatch):
    """A room conclusion can rework a package child while _collect handles the
    room turn: _make_stale kills and drops the running V2, and the loop must
    neither delete it twice nor evaluate a cancelled worker."""
    template_path = tmp_path / "split.yaml"
    template_path.write_text(TEMPLATE, encoding="utf-8")
    d = make_dispatcher(tmp_path, {}, template=load_template(path=template_path), checks={"ok": _ok})
    room, v2 = _Exited(), _Exited()
    d.running = {"ROOM-x": room, "V2": v2}
    seen = []

    def fake_on_exit(handle, timed_out, rc):
        seen.append(handle)
        if handle is room:  # like _request_rework -> _make_stale
            d.running.pop("V2", None)
            v2.kill()

    monkeypatch.setattr(d, "_on_exit", fake_on_exit)
    d._collect()
    assert seen == [room]
    assert d.running == {}


def test_rework_of_a_template_task_reaches_expanded_families(tmp_path):
    """Template downstream edges name split families (V1, S2), which are not
    tasks on the board; the walk must use the board's edges instead of asking
    the board for a family's status."""
    d = _run_split_ws(tmp_path)
    d._request_rework("S1", "test feedback", reason="test", by="test")
    assert d.board.status("S1") == "open"
    for task in ("V1-alpha", "S2-alpha", "S2-shared", "S3"):
        assert d.board.status(task) == "open", task


def test_ruling_naming_a_family_expands_to_its_children(tmp_path):
    """A human ruling may name "S2"; the tasks that can rework are its children."""
    d = _run_split_ws(tmp_path)
    ruling = {
        "id": "R-test", "question": "q", "conclusion": "c", "basis": "b", "category": "contract",
        "affects": {"tasks": ["S2"], "cases": [], "layers": []}, "raised_by": "test",
        "deviates_from_monolith": False, "status": "decided", "task": None, "attempt": None,
        "time": "t", "decider": "human",
    }
    d._apply_ruling(ruling, by="human")
    for task in ("S2-alpha", "S2-shared"):
        assert d.board.status(task) == "open", task
