"""coord.harness: the run's harness stamp (repos, tools, models, prompt hashes)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from coord import harness  # noqa: E402


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def fake_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "m2s-mcp-server" / "perf-rules").mkdir(parents=True)
    (repo / "m2s-mcp-server" / "gates.py").write_text("x = 1\n", encoding="utf-8")
    (repo / "m2s-mcp-server" / "perf-rules" / "ledger.json").write_text("{}\n", encoding="utf-8")
    (repo / "notes.md").write_text("outside the harness\n", encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "add", ".")
    _git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "init")
    return repo


def test_repo_state_reports_only_harness_paths(fake_repo: Path):
    (fake_repo / "m2s-mcp-server" / "gates.py").write_text("x = 2\n", encoding="utf-8")
    (fake_repo / "m2s-mcp-server" / "perf-rules" / "ledger.json").write_text('{"a": 1}\n', encoding="utf-8")
    (fake_repo / "notes.md").write_text("changed\n", encoding="utf-8")

    state = harness._repo_state(fake_repo, harness.HARNESS_PATHS)

    assert len(state["commit"]) == 40
    # The first porcelain line starts with a space; the path must survive intact.
    assert state["dirty"] == ["m2s-mcp-server/gates.py"]


def test_clean_repo_has_no_dirty_paths(fake_repo: Path):
    assert harness._repo_state(fake_repo, harness.HARNESS_PATHS)["dirty"] == []


def test_stamp_resolves_inherited_models_without_secrets(tmp_path: Path, monkeypatch):
    config = tmp_path / "oh"
    (config / "agents").mkdir(parents=True)
    (config / "settings.json").write_text(
        json.dumps({
            "model": "qwen3.8-flash",
            "active_profile": "qwen",
            "api_key": "sk-secret",
            "profiles": {"qwen": {"provider": "dashscope", "base_url": "https://example/v1", "api_key": "sk-secret"}},
        }),
        encoding="utf-8",
    )
    (config / "agents" / "m2s-architect.md").write_text("---\nname: m2s-architect\nmodel: inherit\n---\nbody\n", encoding="utf-8")
    (config / "agents" / "m2s-verifier.md").write_text("---\nname: m2s-verifier\nmodel: qwen-max\n---\nbody\n", encoding="utf-8")
    monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(config))
    monkeypatch.setattr(harness, "AGENTS_DIR", config / "agents")  # the role definitions workers get

    stamp = harness.stamp()

    assert stamp["type"] == "harness"
    assert stamp["agents"]["m2s-architect"]["effective_model"] == "qwen3.8-flash"
    assert stamp["agents"]["m2s-verifier"]["effective_model"] == "qwen-max"
    assert stamp["models"]["provider"] == "dashscope"
    assert set(stamp["tools"]) == {"br", "agent_mail"}
    assert "sk-secret" not in json.dumps(stamp)


def test_agent_definitions_include_the_split_v1_roles():
    """2026-11-25: the v1 verifier and architect are separate agents, so the
    harness stamp records both role files."""
    agents = harness._agents(harness.AGENTS_DIR, "model")
    assert {"m2s-verifier", "m2s-verifier-v1", "m2s-architect", "m2s-architect-v1",
            "m2s-code-developer", "m2s-code-developer-v1", "m2s-sam-engineer", "m2s-sam-engineer-v1"} <= set(agents)
    assert agents["m2s-verifier"]["file"] == "m2s-verifier.md"
    assert agents["m2s-verifier-v1"]["file"] == "m2s-verifier-v1.md"
    assert agents["m2s-architect-v1"]["file"] == "m2s-architect-v1.md"


def test_prompt_hash_changes_with_content(tmp_path: Path):
    root = tmp_path / "skills"
    (root / "a").mkdir(parents=True)
    (root / "a" / "SKILL.md").write_text("one", encoding="utf-8")
    first = harness._tree_sha256(root)
    (root / "a" / "SKILL.md").write_text("two", encoding="utf-8")

    assert first != harness._tree_sha256(root)
    assert harness._tree_sha256(tmp_path / "missing") is None


def test_is_clean():
    assert harness.is_clean({"m2s": {"dirty": []}, "openharness": {"dirty": []}})
    assert not harness.is_clean({"m2s": {"dirty": ["m2s-mcp-server/gates.py"]}, "openharness": {"dirty": []}})


def test_workspace_record_keeps_the_start_and_each_different_resume():
    a1 = {"type": "harness", "time": "t1", "m2s": {"commit": "a"}}
    a2 = {"type": "harness", "time": "t2", "m2s": {"commit": "a"}}  # same harness, later
    b = {"type": "harness", "time": "t3", "m2s": {"commit": "b"}}
    a3 = {"type": "harness", "time": "t4", "m2s": {"commit": "a"}}  # back to a: a change again

    record = harness.workspace_record([a1, a2, b, b, a3])

    assert record["started_with"] == a1
    assert [s["time"] for s in record["resumed_with"]] == ["t3", "t4"]
    assert harness.same_harness(a1, a2) and not harness.same_harness(a1, b)


def test_write_workspace_record_rewrites_only_when_needed(tmp_path: Path):
    stamps = [{"time": "t1", "m2s": {"commit": "a"}}]
    path = tmp_path / "storage" / "harness_version.json"

    assert harness.write_workspace_record(tmp_path, stamps)
    assert not harness.write_workspace_record(tmp_path, stamps)
    path.write_text("{}", encoding="utf-8")  # someone edited it: the event log wins
    assert harness.write_workspace_record(tmp_path, stamps)
    assert json.loads(path.read_text(encoding="utf-8"))["started_with"]["m2s"]["commit"] == "a"
