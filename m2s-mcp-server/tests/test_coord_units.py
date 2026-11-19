"""Fast unit tests of the coordination layer (no br, no LLM)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from coord import tools  # noqa: E402
from coord.checks import ToolGuard  # noqa: E402
from coord.events import EventLog  # noqa: E402
from coord.prompts import IMPL_NOTE, worker_prompt  # noqa: E402
from coord.snapshot import Shadow  # noqa: E402
from coord.template import in_scope, load_template  # noqa: E402
from coord.workers import WorkerJob, role_prompt, worker_settings  # noqa: E402


# --- template --------------------------------------------------------------------


def test_v1_template_loads_with_dependencies_in_order():
    template = load_template("v1")
    ids = [t.id for t in template.tasks]
    assert ids[:3] == ["P0", "S0", "S1"] and ids[-1] == "FINAL"
    assert template.downstream("S2") == ["S3a", "S3b", "S3c", "S4", "V2", "TAG_V0", "S5", "FINAL"]
    assert template.get("V2").on_stale == "rerun"
    assert all(t.role for t in template.tasks if t.runner == "agent")
    assert template.upstream("V1") == ["P0", "S0", "S1", "V1"]
    assert template.upstream("TAG_V0")[-1] == "TAG_V0" and "S5" not in template.upstream("TAG_V0")


def test_stop_after_must_name_a_template_task(tmp_path: Path):
    from coord.dispatcher import Dispatcher
    with pytest.raises(ValueError, match="not a task"):
        Dispatcher(tmp_path / "ws", tmp_path / "mono", stop_after="S9")
    assert not (tmp_path / "ws").exists()


def test_scope_globs():
    scope = ("output/lambdas/**", "storage/stage_reports/S2.json")
    assert in_scope("output/lambdas/catalog/search/handler.js", scope)
    assert in_scope("storage/stage_reports/S2.json", scope)
    assert not in_scope("storage/blueprint.json", scope)
    assert not in_scope("output/template.yaml", scope)
    assert in_scope("verification/fault_baseline_v0.json", ("verification/fault_baseline_*.json",))
    assert not in_scope("verification/x/fault_baseline_v0.json", ("verification/fault_baseline_*.json",))


# --- events ------------------------------------------------------------------------


def test_event_counters(tmp_path: Path):
    log = EventLog(tmp_path / "events.jsonl")
    log.append("spawn", task="S2", attempt=1)
    log.append("worker_exit", task="S2", attempt=1, crashed=True)
    log.append("spawn", task="S2", attempt=2)
    log.append("feedback", task="S2", source="checks", text="fix C3")
    assert log.spawns("S2") == 2 and log.crashes("S2") == 1 and log.attempts("S2") == 1
    assert [f["text"] for f in log.pending_feedback("S2")] == ["fix C3"]

    log.append("round_start", task="S2", cause="rework", reason="verification", mode="rerun")
    assert log.attempts("S2") == 0 and log.reopen_mode("S2") == "rerun"
    assert log.rework_rounds("S2", "verification") == 1
    log.append("spawn", task="S2", attempt=3)
    assert log.reopen_mode("S2") is None and log.pending_feedback("S2") == []
    assert EventLog(tmp_path / "events.jsonl").count("spawn", "S2") == 3  # survives a restart


# --- shadow snapshots ----------------------------------------------------------------


def test_shadow_detects_and_restores_changes(tmp_path: Path):
    ws = tmp_path / "ws"
    (ws / "storage").mkdir(parents=True)
    (ws / "storage" / "blueprint.json").write_text("v1", encoding="utf-8")
    (ws / "coord").mkdir()
    shadow = Shadow(ws)
    before = shadow.snapshot("before")

    (ws / "storage" / "blueprint.json").write_text("tampered", encoding="utf-8")
    (ws / "output").mkdir()
    (ws / "output" / "new.js").write_text("x", encoding="utf-8")
    (ws / "coord" / "events.jsonl").write_text("ignored", encoding="utf-8")
    changes = shadow.changes(before)

    assert sorted(changes) == [("A", "output/new.js"), ("M", "storage/blueprint.json")]
    shadow.restore(before, changes)
    assert (ws / "storage" / "blueprint.json").read_text(encoding="utf-8") == "v1"
    assert not (ws / "output" / "new.js").exists()
    assert not (ws / ".git").exists()  # the workspace's own repo is never created


# --- worker tools ----------------------------------------------------------------------


@pytest.fixture
def dispatched(tmp_path: Path, monkeypatch) -> Path:
    coord = tmp_path / "coord"
    monkeypatch.setenv("M2S_COORD_DIR", str(coord))
    monkeypatch.setenv("M2S_TASK_ID", "S2")
    monkeypatch.setenv("M2S_ATTEMPT", "2")
    monkeypatch.setenv("M2S_ROLE", "m2s-code-developer")
    return coord


def test_submit_and_block_write_the_submission(dispatched: Path):
    assert tools.submit_task("done it")["ok"]
    record = json.loads(tools.submission_path(dispatched, "S2", 2).read_text(encoding="utf-8"))
    assert record["status"] == "done" and record["summary"] == "done it"

    tools.report_blocked("C3 fails", "gate output", "S1")
    record = json.loads(tools.submission_path(dispatched, "S2", 2).read_text(encoding="utf-8"))
    assert record["status"] == "blocked" and record["suspected_module"] == "S1"


def test_only_the_handler_may_decide(dispatched: Path, monkeypatch):
    assert not tools.handler_decide("C001", "retry")["ok"]
    monkeypatch.setenv("M2S_ROLE", tools.HANDLER_ROLE)
    assert not tools.handler_decide("C001", "jump")["ok"]
    assert not tools.handler_decide("C001", "reroute")["ok"]  # needs a target
    assert tools.handler_decide("C001", "reroute", target="S2", rationale="handler bug")["ok"]
    assert json.loads((dispatched / "cases" / "C001.decision.json").read_text(encoding="utf-8"))["target"] == "S2"


def test_tools_refuse_outside_a_dispatcher_run(monkeypatch):
    monkeypatch.delenv("M2S_COORD_DIR", raising=False)
    monkeypatch.delenv("M2S_TASK_ID", raising=False)
    result = tools.safe(tools.submit_task, "x")
    assert result["ok"] is False and "dispatcher" in result["error"]


# --- tool guard -----------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t", *args], check=True, capture_output=True)


def test_tool_guard_reverts_only_files_that_were_clean(tmp_path: Path):
    repo = tmp_path / "repo"
    (repo / "m2s-mcp-server").mkdir(parents=True)
    (repo / "m2s-mcp-server" / "gates.py").write_text("clean\n", encoding="utf-8")
    (repo / "m2s-mcp-server" / "verify.py").write_text("clean\n", encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "init")
    (repo / "m2s-mcp-server" / "verify.py").write_text("person's own edit\n", encoding="utf-8")
    guard = ToolGuard(repo=repo, agents_dir=tmp_path / "agents")
    guard.capture()

    (repo / "m2s-mcp-server" / "gates.py").write_text("worker edit\n", encoding="utf-8")
    (repo / "m2s-mcp-server" / "verify.py").write_text("person's own edit + worker edit\n", encoding="utf-8")
    (repo / "m2s-mcp-server" / "new_tool.py").write_text("worker\n", encoding="utf-8")
    found = guard.violations()

    assert found["repo_new"] == ["m2s-mcp-server/gates.py", "m2s-mcp-server/new_tool.py"]
    assert found["repo_changed"] == ["m2s-mcp-server/verify.py"]
    guard.revert(found["repo_new"])
    assert (repo / "m2s-mcp-server" / "gates.py").read_text(encoding="utf-8") == "clean\n"
    assert not (repo / "m2s-mcp-server" / "new_tool.py").exists()
    assert "person's own edit" in (repo / "m2s-mcp-server" / "verify.py").read_text(encoding="utf-8")


# --- prompts and worker settings ------------------------------------------------------------------


def test_worker_prompt_carries_the_v1_spawn_elements(tmp_path: Path):
    template = load_template("v1")
    prompt = worker_prompt(template.get("S3c"), tmp_path / "ws", tmp_path / "mono", 2,
                           [{"source": "checks", "text": "C3 handler path wrong"}])
    for needle in ("STAGE_ID: S3c", "MERGE =", "第 2 次尝试", "C3 handler path wrong", "submit_task",
                   "report_blocked", "网络错误", "m2s-mcp-server/", IMPL_NOTE):
        assert needle in prompt, needle
    v2 = worker_prompt(template.get("V2"), tmp_path / "ws", tmp_path / "mono", 1, [])
    assert IMPL_NOTE not in v2


def test_o_prompt_measurement_section_follows_the_aws_flag(tmp_path: Path, monkeypatch):
    """O judges on AWS only when the run was approved for it; the prompt must
    not carry a static "no AWS" sentence (design §2.2)."""
    template = load_template("v2-split")
    o = template.get("O")
    monkeypatch.delenv("M2S_AWS", raising=False)
    local = worker_prompt(o, tmp_path / "ws", tmp_path / "mono", 1, [])
    assert "不要部署到 AWS" in local and "run_aws_verdict" in local and "run_rie_verdict" in local
    monkeypatch.setenv("M2S_AWS", "1")
    monkeypatch.setenv("M2S_AWS_LOAD_CMD", "load.sh --rate 50")
    monkeypatch.setenv("M2S_AWS_BUDGET_USD", "3.5")
    aws = worker_prompt(o, tmp_path / "ws", tmp_path / "mono", 1, [])
    assert "不要部署到 AWS" not in aws
    assert "run_aws_verdict" in aws and "AWS 实测已开启" in aws
    assert "load.sh --rate 50" in aws and "3.5" in aws
    assert "evidence.aws" in aws and "calibrate_aws_noise" in aws
    other = worker_prompt(template.get("I1"), tmp_path / "ws", tmp_path / "mono", 1, [])
    assert "run_aws_verdict" not in other


def test_an_o_prompt_without_a_load_command_says_to_block(tmp_path: Path, monkeypatch):
    template = load_template("v2-split")
    monkeypatch.setenv("M2S_AWS", "1")
    monkeypatch.delenv("M2S_AWS_LOAD_CMD", raising=False)
    text = worker_prompt(template.get("O"), tmp_path / "ws", tmp_path / "mono", 1, [])
    assert "report_blocked" in text and "M2S_AWS_LOAD_CMD" in text


def test_worker_env_carries_the_aws_settings(tmp_path: Path, monkeypatch):
    """The m2s-server process gets job.env(); without these the server would
    answer DISABLED even though the run is approved for AWS."""
    monkeypatch.setenv("M2S_AWS", "1")
    monkeypatch.setenv("M2S_AWS_LOAD_CMD", "load.sh --rate 50")
    monkeypatch.delenv("M2S_AWS_BUDGET_USD", raising=False)
    job = WorkerJob("O", 1, "m2s-perf-optimizer", tmp_path / "ws", tmp_path / "ws" / "coord", "R", "p", (), 10, 10.0)
    env = job.env()
    assert env["M2S_AWS"] == "1" and env["M2S_AWS_LOAD_CMD"] == "load.sh --rate 50"
    assert "M2S_AWS_BUDGET_USD" not in env


def test_worker_settings_scope_and_isolation(tmp_path: Path):
    template = load_template("v1")
    spec = template.get("S2")
    job = WorkerJob("S2", 1, "m2s-code-developer", tmp_path / "ws", tmp_path / "ws" / "coord", "ROLE", "do it",
                    spec.scope, 100, 60.0)
    settings = worker_settings(job, other_servers=["other"])
    perm = settings["permission"]

    assert settings["system_prompt"] == "ROLE" and settings["mcp_servers"] == {"other": None}
    assert perm["mode"] == "full_auto"
    assert f"{tmp_path / 'ws'}/output/lambdas/*" in perm["writable_paths"]
    assert perm["path_rules"][0] == {"pattern": f"{tmp_path / 'ws'}/coord/*", "allow": False}
    assert any(r["pattern"].endswith("m2s-mcp-server/*") and r["access"] == "write" for r in perm["path_rules"])

    handler = WorkerJob("C001", 1, "handler", tmp_path / "ws", tmp_path / "ws" / "coord", "H", "p", (), 60, 60.0, read_coord=True)
    handler_perm = worker_settings(handler, [])["permission"]
    assert not any("coord" in r["pattern"] for r in handler_perm["path_rules"])
    assert all(not p.startswith(str(tmp_path / "ws")) for p in handler_perm["writable_paths"])


def test_role_prompt_strips_frontmatter(tmp_path: Path):
    (tmp_path / "m2s-architect.md").write_text("---\nname: m2s-architect\nmodel: inherit\n---\n\n# Architect\nbody\n", encoding="utf-8")
    assert role_prompt("m2s-architect", tmp_path) == "# Architect\nbody"


def test_other_runs_are_closed_to_workers(tmp_path: Path):
    from coord.dispatcher import other_run_dirs
    runs = tmp_path / "M2S" / "runs"
    for d in ("bookstore-v3a", "bookstore-v2", "acmeair-a1"):
        (runs / d).mkdir(parents=True)
    (tmp_path / "M2S" / "runs-prev-run5").mkdir()
    (tmp_path / "M2S" / "knowledge").mkdir()
    (runs / "notes.txt").write_text("x", encoding="utf-8")
    ws = runs / "bookstore-v3a"
    denied = other_run_dirs(ws, tmp_path / "mono" / "bookstore")
    assert denied == tuple(str(d) for d in sorted([runs / "acmeair-a1", runs / "bookstore-v2", tmp_path / "M2S" / "runs-prev-run5"]))
    assert other_run_dirs(ws, runs / "acmeair-a1" / "mono") == (str(runs / "bookstore-v2"), str(tmp_path / "M2S" / "runs-prev-run5"))

    job = WorkerJob("S2", 1, "m2s-code-developer", ws, ws / "coord", "ROLE", "do it", (), 100, 60.0, deny_read=denied)
    rules = worker_settings(job, [])["permission"]["path_rules"]
    assert {"pattern": f"{runs / 'bookstore-v2'}/*", "allow": False} in rules


def test_api_failure_detection(tmp_path: Path):
    from coord.dispatcher import is_infra_error, stream_end_error
    run = tmp_path / "run"
    run.mkdir()
    assert stream_end_error(run) is None  # no stream at all
    lines = [{"type": "tool_completed", "tool_name": "bash", "output": "ok"},
             {"type": "error", "message": "Network error: Connection error.. Check your internet connection.", "recoverable": True}]
    (run / "stream.jsonl").write_text("\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")
    assert is_infra_error(stream_end_error(run))
    lines.append({"type": "assistant_complete", "text": "done"})
    (run / "stream.jsonl").write_text("\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")
    assert stream_end_error(run) is None  # recovered later: the session did not end on the error
    assert is_infra_error("API error: Error code: 503 - service unavailable")
    assert is_infra_error("API error: Error code: 429 - rate limited")
    assert not is_infra_error("API error: Error code: 400 - invalid request")
    assert not is_infra_error("Model returned an empty assistant message.")


def test_a_content_refusal_is_not_an_outage():
    from coord.dispatcher import is_content_refusal, is_infra_error
    msg = ('API error: data: {"error":{"code":"data_inspection_failed","param":null,"message":"Input text data may '
           'contain inappropriate content.","type":"data_inspection_failed"},"id":"chatcmpl-2dd8-500-af05"}')
    assert is_content_refusal(msg) and not is_infra_error(msg)
    assert not is_content_refusal("Network error: Connection error.")


def test_task_usage_sums_assistant_complete_usage(tmp_path: Path):
    from coord.dispatcher import task_usage
    coord = tmp_path / "coord"
    for attempt, lines in {
        "a1": [{"type": "assistant_complete", "text": "", "usage": {"input_tokens": 100, "output_tokens": 20}},
               {"type": "tool_started", "tool_name": "bash", "tool_input": {}}],
        "a2": [{"type": "assistant_complete", "text": "", "usage": {"input_tokens": 7, "output_tokens": 3}},
               {"type": "assistant_complete", "text": ""}],  # an older stream without usage contributes 0
    }.items():
        run = coord / "run" / f"O-{attempt}"
        run.mkdir(parents=True)
        (run / "stream.jsonl").write_text("\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")
    assert task_usage(coord, "O") == (107, 23)
    assert task_usage(coord, "V2") == (0, 0)  # no run directory at all
