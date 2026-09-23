"""Tests for wrap_m2s.py, focused on validate_sam's timeout handling."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import wrap_m2s  # noqa: E402


def test_validate_sam_missing_template(tmp_path):
    result = wrap_m2s.validate_sam(str(tmp_path / "does-not-exist.yaml"))
    assert result["ok"] is False
    assert "not found" in result["errors"][0]


def test_validate_sam_timeout_returns_error_instead_of_hanging(tmp_path):
    """Real incident: cfn-lint hung indefinitely with no timeout on the
    subprocess call, freezing the calling agent (and the orchestrator
    polling it) forever. Confirms the timeout path returns a clear result
    instead of propagating TimeoutExpired or hanging the test, and that the
    process tree is killed rather than just the immediate child (root
    cause: cfn-lint.exe spawns a child python.exe that otherwise leaks as
    an orphaned zombie stuck at interpreter startup)."""
    template = tmp_path / "template.yaml"
    template.write_text("AWSTemplateFormatVersion: '2010-09-09'\n", encoding="utf-8")

    mock_proc = MagicMock()
    mock_proc.pid = 4242
    mock_proc.communicate.side_effect = [
        subprocess.TimeoutExpired(cmd=["cfn-lint"], timeout=wrap_m2s._VALIDATE_SAM_TIMEOUT_SECONDS),
        ("", ""),
    ]

    with patch("wrap_m2s._find_cfn_lint", return_value="cfn-lint"):
        with patch("wrap_m2s.subprocess.Popen", return_value=mock_proc):
            with patch("wrap_m2s._kill_process_tree") as mock_kill:
                result = wrap_m2s.validate_sam(str(template))

    assert result["ok"] is False
    assert "did not finish within" in result["errors"][0]
    assert str(wrap_m2s._VALIDATE_SAM_TIMEOUT_SECONDS) in result["errors"][0]
    mock_kill.assert_called_once_with(4242)


def test_validate_sam_uses_devnull_stdin_and_passes_timeout(tmp_path):
    """The root-caused fix: cfn-lint's subprocess must not inherit stdin
    (it hangs at interpreter startup in the MCP task-worker context
    otherwise), and the timeout must still be enforced via communicate()."""
    template = tmp_path / "template.yaml"
    template.write_text("AWSTemplateFormatVersion: '2010-09-09'\n", encoding="utf-8")

    mock_proc = MagicMock()
    mock_proc.pid = 1234
    mock_proc.returncode = 0
    mock_proc.communicate.return_value = ("", "")

    with patch("wrap_m2s._find_cfn_lint", return_value="cfn-lint"):
        with patch("wrap_m2s.subprocess.Popen", return_value=mock_proc) as mock_popen:
            wrap_m2s.validate_sam(str(template))

    assert mock_popen.call_args.kwargs.get("stdin") == subprocess.DEVNULL
    assert mock_proc.communicate.call_args.kwargs.get("timeout") == wrap_m2s._VALIDATE_SAM_TIMEOUT_SECONDS


def test_validate_sam_real_todo_template_still_passes():
    """Regression check against the real reference template (also covered
    by _smoke_p0.py, but as a proper pytest case): confirms the timeout
    plumbing didn't break the happy path."""
    m2s_root = Path(r"D:\Emma\MyLab\Mono2SLs\Mono2Sls-artifacts\M2S")
    template = m2s_root / "output" / "template.yaml"
    if not template.exists():
        return  # environment-dependent fixture, skip if not present
    result = wrap_m2s.validate_sam(str(template))
    assert result["ok"] is True
    assert result["errors"] == []
