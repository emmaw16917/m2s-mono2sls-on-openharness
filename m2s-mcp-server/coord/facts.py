"""Fact-base version record (PIPELINE_V3_PLAN.md M4/W4.1).

The fact base (`storage/analysis_report.json`) is corrected by the annotator
tasks (v1 S0/<pkg>, v2 F2). Every change gets a version: the dispatcher appends
a `facts` event with the sha256 of the file and the next version number, posts
an `artifact` notice, and writes this workspace file. Tasks record the version
they started from, so a report can tell which facts a result was computed on.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

WORKSPACE_FILE = Path("storage") / "facts_version.json"


def write(ws: Path | str, entry: dict[str, Any]) -> bool:
    """Write the version record from the latest `facts` event; True when changed."""
    path = Path(ws) / WORKSPACE_FILE
    text = json.dumps({"version": int(entry.get("version") or 0), "sha": entry.get("sha") or "",
                       "updated_at": entry.get("ts") or ""}, ensure_ascii=False, indent=2) + "\n"
    try:
        if path.read_text(encoding="utf-8") == text:
            return False
    except OSError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return True


def read(ws: Path | str) -> dict[str, Any] | None:
    try:
        return json.loads((Path(ws) / WORKSPACE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
