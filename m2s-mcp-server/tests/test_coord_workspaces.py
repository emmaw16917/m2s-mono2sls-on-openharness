"""v3 M2 (W2.9): two workspaces run at the same time, each with its own M2S_WORKSPACE.

The v1 chain switched a global ~/.openharness/settings.json entry; v3 workers
get the workspace through their own mcp.json environment, so nothing global is
touched. The test runs two dispatchers in threads and checks both finish and
stay in their own directories.
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from coord_fakes import make_dispatcher  # noqa: E402

from coord.checks import CheckResult  # noqa: E402
from coord.workers import WorkerJob  # noqa: E402


def _job(ws: Path) -> WorkerJob:
    return WorkerJob(task_id="A", attempt=1, role="fake", ws=ws, coord_dir=ws / "coord",
                     system_prompt="", prompt="", scope=(), max_turns=1, timeout_s=1)


def test_each_worker_gets_its_own_workspace_environment(tmp_path):
    one = _job(tmp_path / "ws-one").env()
    two = _job(tmp_path / "ws-two").env()
    assert one["M2S_WORKSPACE"] != two["M2S_WORKSPACE"]
    assert one["M2S_WORKSPACE"].endswith("ws-one")
    assert two["M2S_WORKSPACE"].endswith("ws-two")


def test_two_workspaces_run_concurrently(tmp_path, monkeypatch):
    monkeypatch.setenv("M2S_LOCK_DIR", str(tmp_path / "locks"))
    template_path = Path(__file__).resolve().parent / "fixtures" / "coord_mini.yaml"
    from coord.template import load_template

    template = load_template(path=template_path)

    def ok(ws: Path, spec: dict) -> CheckResult:
        return CheckResult("ok", True, "ok")

    checks = {name: ok for name in ("analysis_report", "gate_blueprint", "gate_code", "gate_final")}
    roots = [tmp_path / "one", tmp_path / "two"]
    for root in roots:
        root.mkdir()
    dispatchers = [make_dispatcher(root, {}, template=template, checks=checks) for root in roots]
    results: list[int] = []
    threads = [threading.Thread(target=lambda d=d: results.append(d.run())) for d in dispatchers]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=180)

    assert results == [0, 0]
    for root in roots:
        assert (root / "ws" / "storage" / "stage_reports" / "S4.json").is_file()
    # each workspace's events name its own ws, never the other one
    for root in roots:
        text = (root / "ws" / "coord" / "events.jsonl").read_text(encoding="utf-8")
        other = str((tmp_path / ("two" if root.name == "one" else "one") / "ws"))
        assert other not in text
