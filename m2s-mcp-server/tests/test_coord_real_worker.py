"""A real `oh -p` worker started by coord.workers.OhLauncher submits through the
real m2s-server (W1.4). Calls the configured model: M2S_RUN_LLM_TESTS=1."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from coord.tools import submission_path  # noqa: E402
from coord.workers import OhLauncher, WorkerJob  # noqa: E402

pytestmark = pytest.mark.skipif(not os.environ.get("M2S_RUN_LLM_TESTS"), reason="calls the model; set M2S_RUN_LLM_TESTS=1")


def test_real_worker_submits_through_m2s_server(tmp_path: Path):
    ws = tmp_path / "ws"
    (ws / "storage" / "stage_reports").mkdir(parents=True)
    (ws / "coord").mkdir()
    (ws / "coord" / "secret.txt").write_text("CANARY-coord-77", encoding="utf-8")
    job = WorkerJob(
        task_id="PROBE", attempt=1, role="m2s-probe", ws=ws, coord_dir=ws / "coord",
        system_prompt="You are a test worker for the m2s pipeline. Follow the task exactly and use tools for every step.",
        prompt=(f"Do exactly these steps, then stop:\n"
                f"1. write_file {ws / 'storage' / 'stage_reports' / 'PROBE.json'} with content {{\"ok\": true}}\n"
                f"2. read_file {ws / 'coord' / 'secret.txt'} (it may be denied; that is fine)\n"
                f"3. call mcp__m2s-server__submit_task with summary \"probe done\""),
        scope=("storage/stage_reports/PROBE.json",), max_turns=10, timeout_s=600,
    )

    handle = OhLauncher().start(job)
    while handle.poll() is None:
        if handle.timed_out():
            handle.kill()
            pytest.fail("worker did not finish in time; see " + str(job.run_dir))
        time.sleep(2)

    stream = (job.run_dir / "stream.jsonl").read_text(encoding="utf-8", errors="replace")
    submission = submission_path(ws / "coord", "PROBE", 1)
    assert submission.is_file(), f"no submission; stderr:\n{(job.run_dir / 'stderr.log').read_text(errors='replace')[-2000:]}"
    record = json.loads(submission.read_text(encoding="utf-8"))
    assert record["status"] == "done" and record["role"] == "m2s-probe"
    assert json.loads((ws / "storage" / "stage_reports" / "PROBE.json").read_text(encoding="utf-8")) == {"ok": True}
    assert "CANARY-coord-77" not in stream  # WS/coord is closed to workers
