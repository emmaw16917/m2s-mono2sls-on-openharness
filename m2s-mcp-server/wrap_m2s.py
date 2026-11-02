"""Reuse layer over the Mono2Sls (M2S) CrewAI codebase for the MCP server.

M2S itself top-level-imports `crewai` in several modules (file_tools.py,
sam_validate tool, etc.), which we don't want as a dependency here. So this
module only imports the parts of M2S that are pure-stdlib and safe to reuse
directly (`static_analyzer.py`), and re-implements the small pieces that
would otherwise drag in crewai (CFN-tagged YAML loading, cfn-lint
invocation).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

# M2S_ROOT: the M2S package root (contains src/preprocessor/static_analyzer.py).
# Resolved relative to this file so it works regardless of cwd.
M2S_ROOT = Path(__file__).resolve().parent.parent / "Mono2Sls-artifacts" / "M2S"
M2S_SRC = M2S_ROOT / "src"

if str(M2S_SRC) not in sys.path:
    sys.path.insert(0, str(M2S_SRC))

from preprocessor.static_analyzer import run_static_analysis  # noqa: E402

import spec_extract
import global_middleware  # noqa: E402
import data_store  # noqa: E402
import runtime_signals  # noqa: E402


def analyze(monolith_root: str, workspace: str) -> dict[str, Any]:
    """Run M2S's static analyzer and write analysis_report.json + symbol_table.json
    into `workspace`, mirroring static_analyzer.py's main() split behavior.

    Returns a summary dict (not the full report) suitable for an MCP tool result.
    """
    monolith_path = Path(monolith_root).resolve()
    if not monolith_path.exists():
        raise FileNotFoundError(f"monolith_root not found: {monolith_path}")

    ws = Path(workspace).resolve()
    storage_dir = ws / "storage"
    storage_dir.mkdir(parents=True, exist_ok=True)

    report = run_static_analysis(monolith_path)
    spec_summary = spec_extract.enrich_report(report, monolith_path)
    # app-wide middleware that changes every response (CORS, security headers,
    # header hooks): outside every route, so route-level extraction misses it
    report["global_middleware"] = global_middleware.detect(monolith_path)
    # what a long-running process gets for free (process state, time, files, ...):
    # the signals of the gap scan (v2 G1)
    report["runtime_signals"] = runtime_signals.scan(monolith_path)
    # a non-DynamoDB data store (MongoDB): its access patterns, which S1 must
    # map onto DynamoDB tables (gate DM1/DM2)
    store = data_store.detect(monolith_path)
    if store:
        report["source_data_store"] = store

    # Mirror static_analyzer.main(): pop symbol_table, write it separately.
    # Nested under storage/ to match M2S's own storage/-output/ convention,
    # which the architect/code-developer/sam-engineer agents (P2) also follow.
    symbol_table = report.pop("symbol_table", [])
    symbol_table_path = storage_dir / "symbol_table.json"
    with symbol_table_path.open("w", encoding="utf-8") as f:
        json.dump(symbol_table, f, indent=2, ensure_ascii=False)

    report_path = storage_dir / "analysis_report.json"
    with report_path.open("w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    entry_points = report.get("entry_points", [])

    return {
        "report_path": str(report_path),
        "symbol_table_path": str(symbol_table_path),
        "entry_point_count": len(entry_points),
        # Just the endpoint identities -- the full spec fields are in the
        # report file and would bloat the tool result.
        "entry_points": [
            {k: ep.get(k) for k in ("file", "method", "path", "handler_function")} for ep in entry_points
        ],
        "spec": spec_summary,
        "config_info": report.get("config_info", {}),
        "dynamodb_info": report.get("dynamodb_info", {}),
        "source_data_store": {k: (len(v) if isinstance(v, list) else v) for k, v in (report.get("source_data_store") or {}).items()},
    }


def _find_cfn_lint() -> str:
    """Locate the cfn-lint executable, falling back to the console-script
    exe next to the current interpreter if it's not on PATH (a known issue
    when this server is launched by a parent process with a different PATH,
    e.g. OpenHarness's task-worker subprocess)."""
    import shutil

    found = shutil.which("cfn-lint")
    if found:
        return found

    candidate = Path(sys.executable).parent / "cfn-lint.exe"
    if candidate.exists():
        return str(candidate)

    candidate = Path(sys.executable).parent / "Scripts" / "cfn-lint.exe"
    if candidate.exists():
        return str(candidate)

    raise FileNotFoundError(
        "cfn-lint not found on PATH or next to the current interpreter "
        f"({sys.executable})"
    )


#: cfn-lint on this project's templates normally finishes in low single-digit
#: seconds. Observed once hanging indefinitely (root cause not confirmed --
#: cfn-lint exposes no obvious auto-network behavior via --help, so it may be
#: an environment fluke rather than a network stall) with no timeout on the
#: subprocess call, which then hung the calling agent (and the orchestrator
#: polling it) forever, since nothing in this pipeline can distinguish "still
#: legitimately working" from "will never return" without an explicit bound.
_VALIDATE_SAM_TIMEOUT_SECONDS = 90


def _kill_process_tree(pid: int) -> None:
    """Kill pid and all its descendants. On Windows a bare Popen.kill() only
    kills the immediate child; cfn-lint.exe itself spawns a child python.exe,
    which then leaks as a zombie stuck at interpreter startup if only the
    parent is killed. taskkill /T walks the whole tree."""
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/T", "/F", "/PID", str(pid)],
            capture_output=True,
        )
    else:
        try:
            proc = subprocess.Popen(["pkill", "-TERM", "-P", str(pid)])
            proc.wait(timeout=5)
        except Exception:
            pass
        try:
            import os
            import signal

            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def validate_sam(template_path: str) -> dict[str, Any]:
    """Run cfn-lint against a SAM template. Returns {ok, errors, warnings, raw}."""
    template = Path(template_path).resolve()
    if not template.exists():
        return {
            "ok": False,
            "errors": [f"template not found: {template}"],
            "warnings": [],
            "raw": "",
        }

    cfn_lint = _find_cfn_lint()

    #: Root-caused (see m2s-sam-engineer S3c diagnostic report): when this
    #: subprocess is spawned from inside the MCP task-worker context, it
    #: hangs indefinitely at interpreter startup unless stdin is explicitly
    #: closed -- the child appears to block reading from an inherited stdin
    #: that the MCP stdio server itself is holding open. Confirmed unrelated
    #: to template content or cfn-lint version; identical direct (non-MCP)
    #: invocations of this same binary/function always succeeded in <5s.
    popen_kwargs: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
    }
    if sys.platform == "win32":
        popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

    proc = subprocess.Popen([cfn_lint, str(template)], **popen_kwargs)
    try:
        stdout, stderr = proc.communicate(timeout=_VALIDATE_SAM_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        _kill_process_tree(proc.pid)
        try:
            proc.communicate(timeout=5)
        except Exception:
            pass
        return {
            "ok": False,
            "errors": [
                f"cfn-lint did not finish within {_VALIDATE_SAM_TIMEOUT_SECONDS}s and was killed "
                "(process tree terminated). "
                "This is not a template problem -- retry the call; if it keeps timing out, "
                "investigate the cfn-lint installation/environment rather than editing the template."
            ],
            "warnings": [],
            "raw": "",
        }

    returncode = proc.returncode
    raw = (stdout or "") + (stderr or "")
    lines = [line for line in raw.splitlines() if line.strip()]
    errors = [line for line in lines if "E" in line.split(" ", 1)[0][:1] or line.startswith("E")]
    warnings = [line for line in lines if line.startswith("W")]

    return {
        "ok": returncode == 0,
        "errors": errors if errors else (lines if returncode != 0 else []),
        "warnings": warnings,
        "raw": raw,
    }
