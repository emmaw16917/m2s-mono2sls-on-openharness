"""A fake F2 package worker that annotates through the real provenance tool.

    python fake_annotator.py <sleep-seconds> <run_dir>

The dispatcher supplies M2S_WORKSPACE, M2S_COORD_DIR and M2S_TASK_ID
(F2-<package> or F2-shared). The package id selects this worker's endpoints
from storage/work_packages.json; every endpoint gets a business rule through
provenance.annotate_entry_point, so two such workers exercise the real
cross-process lock. The sleep keeps both workers alive at the same time, which
the test asserts on (two F2 spawns before the first F2 exit).
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import provenance  # noqa: E402
from coord import tools  # noqa: E402


def main() -> int:
    ws = Path(os.environ["M2S_WORKSPACE"])
    task = os.environ["M2S_TASK_ID"]
    time.sleep(float(sys.argv[1]) if len(sys.argv) > 1 else 0.0)
    package = task.split("-", 1)[1] if "-" in task else task
    if package != "shared":
        plan = json.loads((ws / "storage" / "work_packages.json").read_text(encoding="utf-8"))
        mine = next(p for p in plan["packages"] if p["id"] == package)
        report = json.loads((ws / "storage" / "analysis_report.json").read_text(encoding="utf-8"))
        by_key = {(ep["method"], ep["path"]): ep for ep in report["entry_points"]}
        for item in mine["endpoints"]:
            ep = by_key[(item["method"], item["path"])]
            source = dict(ep["auth"]["source"][0])  # an existing, valid citation
            rule = {"rule": f"{package}: business rule for {ep['path']}", "kind": "other",
                    "source": [source], "origin": "llm"}
            result = provenance.annotate_entry_point(ws, ep["method"], ep["path"], "business_rules",
                                                     json.dumps([rule]))
            if not result.get("ok"):
                print(json.dumps({"type": "error", "message": f"annotate failed: {result}"}), flush=True)
                return 2
    (ws / "storage" / "stage_reports").mkdir(parents=True, exist_ok=True)
    (ws / "storage" / "stage_reports" / f"{task}.json").write_text(
        json.dumps({"ok": True, "summary": f"{task} annotated {package}"}), encoding="utf-8")
    tools.submit_task(f"{task} done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
