"""F2-only integration (2026-11-24): the real v2-split F2 family, run alone.

The previous tests checked the pieces separately (the lock with two raw
processes, the scheduler with a synthetic template). This one runs the actual
F2 TaskSpec from v2-split with two package workers that annotate the one
analysis_report.json through the real provenance tool, and with the real
check_provenance_pkg / check_provenance done checks. It asserts that both F2
package children were started before either exited.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from coord_fakes import FakeGuard  # noqa: E402

import provenance  # noqa: E402
from coord.checks import CheckResult  # noqa: E402
from coord.dispatcher import EXIT_DONE, Dispatcher  # noqa: E402
from coord.template import Template, load_template  # noqa: E402
from coord.workers import CommandLauncher  # noqa: E402

FAKE_ANNOTATOR = Path(__file__).resolve().parent / "fake_annotator.py"
# Long enough that both package workers are alive together even on a fast
# machine; the test asserts the overlap from the event log, not the clock.
WORKER_SLEEP_S = "2.0"


def _monolith_file(path: Path, symbol: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"function {symbol}(req, res) {{\n"
        "  res.send('ok');\n"
        "}\n"
        "\n"
        "module.exports = {};\n",
        encoding="utf-8",
    )


def _endpoint(file: str, symbol: str, path: str) -> dict:
    source = [{"file": file, "line": 1, "symbol": symbol}]
    return {
        "method": "GET", "path": path,
        "handler_location": {"file": file, "start_line": 1, "end_line": 3},
        "auth": {"classification": "none", "middleware": [], "source": source, "origin": "script"},
        "request": {"path_params": [{"name": "id", "type": "string", "required": True,
                                     "source": source, "origin": "script"}],
                    "query": [], "body": [], "headers": []},
        "responses": [{"status": 200, "kind": "success", "body": {"fields": ["id"], "expression": "res.send"},
                       "source": source, "origin": "script"}],
        "data_access": [],
        "business_rules": [],  # empty until F2 annotates: the package check fails, so workers spawn
    }


def _report(mono: Path) -> dict:
    """Ten endpoints in two URL groups: packages.plan (rule 3) gives alpha and beta.

    Citations are written the way spec_extract writes them: prefixed with the
    monolith directory's name (provenance resolves them from monolith_root's
    parent), e.g. "mono/app/alpha.js".
    """
    _monolith_file(mono / "app" / "alpha.js", "alphaHandler")
    _monolith_file(mono / "app" / "beta.js", "betaHandler")
    alpha_file = f"{mono.name}/app/alpha.js"
    beta_file = f"{mono.name}/app/beta.js"
    endpoints = [_endpoint(alpha_file, "alphaHandler", f"/alpha/thing{i}") for i in range(5)]
    endpoints += [_endpoint(beta_file, "betaHandler", f"/beta/thing{i}") for i in range(5)]
    return {"spec_meta": {"monolith_root": str(mono)}, "entry_points": endpoints}


def test_f2_packages_annotate_one_report_in_parallel(tmp_path, monkeypatch):
    monkeypatch.setenv("M2S_LOCK_DIR", str(tmp_path / "locks"))
    ws = tmp_path / "ws"
    mono = tmp_path / "mono"
    report = _report(mono)

    def f1(_spec):
        (ws / "storage").mkdir(parents=True, exist_ok=True)
        (ws / "storage" / "analysis_report.json").write_text(json.dumps(report), encoding="utf-8")
        (ws / "storage" / "symbol_table.json").write_text("[]", encoding="utf-8")
        return CheckResult("analyze_project", True, "fake analyze")

    # F1 -> F2 (the real declaration, unchanged) -> F2DONE: the F2 stage alone.
    full = load_template("v2-split")
    f2_only = Template(name="v2-split-f2",
                       tasks=(full.get("F1"), full.get("F2"), full.get("F2DONE")))
    d = Dispatcher(
        ws, monolith=mono, template=f2_only,
        launcher=CommandLauncher([sys.executable, str(FAKE_ANNOTATOR), WORKER_SLEEP_S]),
        actions={"analyze_project": f1}, max_workers=2, handler=False, phase_review=False,
        role_prompts=lambda role: f"ROLE {role}", guard=FakeGuard(),
        stamp=lambda: {"fake": True}, poll_s=0.05, log=lambda _msg: None,
    )
    d.setup()
    assert d.run() == EXIT_DONE, {t: i["status"] for t, i in d.board.tasks().items()}

    events = d.events.all()
    spawns = [e for e in events if e["type"] == "spawn" and e["task"].startswith("F2-")]
    assert sorted(e["task"] for e in spawns) == ["F2-alpha", "F2-beta"], spawns
    # The point of the change: both package workers started before either exited.
    first_exit = next(i for i, e in enumerate(events)
                      if e["type"] == "worker_exit" and e["task"].startswith("F2-"))
    spawn_positions = [i for i, e in enumerate(events)
                       if e["type"] == "spawn" and e["task"].startswith("F2-")]
    assert spawn_positions[1] < first_exit
    # The shared child closed by its recheck (the global check passed after the
    # children), so it never needed a worker; F2DONE then closed the stage.
    assert not [e for e in events if e["type"] == "spawn" and e["task"] == "F2-shared"]
    statuses = {t: i["status"] for t, i in d.board.tasks().items()}
    assert statuses["F2-alpha"] == statuses["F2-beta"] == statuses["F2-shared"] == "closed"
    assert statuses["F2DONE"] == "closed"

    final = json.loads((ws / "storage" / "analysis_report.json").read_text(encoding="utf-8"))
    assert all(ep["business_rules"] for ep in final["entry_points"])
    checked = provenance.check_provenance(ws)
    assert checked["status"] == "PASS", checked
    assert not (ws / "storage" / ".analysis_report.lock").exists()
    assert not list((ws / "storage").glob("*.tmp"))
