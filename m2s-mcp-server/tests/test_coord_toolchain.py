"""coord.toolchain: pinned installs are verified before unpacking (no network)."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from coord import toolchain  # noqa: E402
from coord.policy import POLICY_YAML  # noqa: E402


def _zip(path: Path, members: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return path


@pytest.fixture
def fake_tool(tmp_path: Path, monkeypatch) -> tuple[toolchain.PinnedTool, Path]:
    archive = _zip(tmp_path / "fake.zip", {"fake.exe": b"MZ", "README.md": b"hi"})
    tool = toolchain.PinnedTool(
        name="fake",
        version="1.0.0",
        url="https://example.invalid/fake.zip",
        sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
        executables=("fake.exe",),
    )
    monkeypatch.setitem(toolchain.PINNED, "fake", tool)
    return tool, archive


def test_install_verifies_and_unpacks(tmp_path: Path, fake_tool):
    tool, archive = fake_tool
    root = tmp_path / "tools"

    target = toolchain.install("fake", root=root, archive=archive)

    assert target == root / "fake-1.0.0"
    assert (target / "fake.exe").read_bytes() == b"MZ"
    assert json.loads((target / ".installed.json").read_text(encoding="utf-8"))["sha256"] == tool.sha256
    assert toolchain.exe_path("fake", root=root) == target / "fake.exe"
    assert toolchain.installed_versions(root)["fake"]["path"] == str(target)


def test_install_is_idempotent(tmp_path: Path, fake_tool):
    _, archive = fake_tool
    root = tmp_path / "tools"
    target = toolchain.install("fake", root=root, archive=archive)
    (target / "marker").write_text("keep", encoding="utf-8")

    assert toolchain.install("fake", root=root, archive=archive) == target
    assert (target / "marker").exists()


def test_install_rejects_checksum_mismatch(tmp_path: Path, fake_tool):
    tampered = _zip(tmp_path / "tampered.zip", {"fake.exe": b"EVIL"})

    with pytest.raises(toolchain.ToolchainError, match="does not match pinned"):
        toolchain.install("fake", root=tmp_path / "tools", archive=tampered)
    assert not (tmp_path / "tools" / "fake-1.0.0").exists()


def test_install_rejects_unsafe_member_paths(tmp_path: Path, monkeypatch):
    archive = _zip(tmp_path / "evil.zip", {"../escape.exe": b"MZ", "fake.exe": b"MZ"})
    monkeypatch.setitem(
        toolchain.PINNED,
        "fake",
        toolchain.PinnedTool("fake", "1.0.0", "https://example.invalid/evil.zip",
                             hashlib.sha256(archive.read_bytes()).hexdigest(), ("fake.exe",)),
    )

    with pytest.raises(toolchain.ToolchainError, match="unsafe member"):
        toolchain.install("fake", root=tmp_path / "tools", archive=archive)
    assert not (tmp_path / "escape.exe").exists()


def test_exe_path_explains_missing_install(tmp_path: Path):
    with pytest.raises(toolchain.ToolchainError, match="coord.toolchain install"):
        toolchain.exe_path("br", root=tmp_path)


def test_tools_root_can_be_overridden(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("M2S_TOOLS_ROOT", str(tmp_path))
    assert toolchain.tools_root() == tmp_path


def test_policy_keeps_allow_bypass_at_top_level():
    lines = POLICY_YAML.splitlines()
    assert lines[0] == "allow_bypass: false"
    assert '"checking -> closed":' in POLICY_YAML and "done_check" in POLICY_YAML


@pytest.mark.skipif(not os.environ.get("M2S_RUN_TOOL_SMOKE"), reason="set M2S_RUN_TOOL_SMOKE=1 to run against the installed tools")
def test_smoke_against_installed_tools(capsys):
    from coord import smoke_tools

    assert smoke_tools.main([]) == 0, capsys.readouterr().out
