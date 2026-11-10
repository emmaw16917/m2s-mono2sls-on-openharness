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
