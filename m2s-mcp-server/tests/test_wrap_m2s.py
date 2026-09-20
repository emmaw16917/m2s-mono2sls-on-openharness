"""Tests for wrap_m2s.py, focused on validate_sam's timeout handling."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

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
    instead of propagating TimeoutExpired or hanging the test."""
    template = tmp_path / "template.yaml"
    template.write_text("AWSTemplateFormatVersion: '2010-09-09'\n", encoding="utf-8")

    with patch("wrap_m2s._find_cfn_lint", return_value="cfn-lint"):
        with patch(
            "wrap_m2s.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd=["cfn-lint"], timeout=wrap_m2s._VALIDATE_SAM_TIMEOUT_SECONDS),
        ):
            result = wrap_m2s.validate_sam(str(template))

    assert result["ok"] is False
    assert "did not finish within" in result["errors"][0]
    assert str(wrap_m2s._VALIDATE_SAM_TIMEOUT_SECONDS) in result["errors"][0]


def test_validate_sam_passes_timeout_to_subprocess_run(tmp_path):
    template = tmp_path / "template.yaml"
    template.write_text("AWSTemplateFormatVersion: '2010-09-09'\n", encoding="utf-8")

    with patch("wrap_m2s._find_cfn_lint", return_value="cfn-lint"):
        with patch("wrap_m2s.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = ""
            mock_run.return_value.stderr = ""
            wrap_m2s.validate_sam(str(template))

    assert mock_run.call_args.kwargs.get("timeout") == wrap_m2s._VALIDATE_SAM_TIMEOUT_SECONDS


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
