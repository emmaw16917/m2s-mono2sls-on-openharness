"""Scripted stand-in for an `oh -p` worker (dispatcher tests, no LLM).

    python fake_worker.py <script.json> <run_dir>

The dispatcher's CommandLauncher sets M2S_WORKSPACE, M2S_COORD_DIR,
M2S_TASK_ID, M2S_ATTEMPT and M2S_ROLE. The script maps a task id to a list
of behaviours, one per attempt (the last one repeats):

    {"S2": [{"write": {"storage/stage_reports/S2.json": {"ok": false}}},
            {"write": {...}, "submit": "done"}],
     "handler": [{"action": "retry", "feedback": "..."}]}

A behaviour may: write files (dicts become JSON), "submit" ("done",
"blocked", or null for no submission), give "reason"/"evidence"/"suspected",
"sleep" seconds, print "stream" events (a list of dicts) and "exit" with a code. Tasks missing from the script get
{"write": {"storage/stage_reports/<task>.json": {"ok": true}}, "submit": "done"}.
Handler behaviours are decisions passed to handler_decide.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from coord import tools  # noqa: E402


def main() -> int:
    script = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    ws = Path(os.environ["M2S_WORKSPACE"])
    task = os.environ["M2S_TASK_ID"]
    attempt = int(os.environ["M2S_ATTEMPT"])
    role = os.environ["M2S_ROLE"]

    if role == tools.HANDLER_ROLE:
        decisions = script.get("handler") or [{"action": "escalate", "rationale": "no scripted decision"}]
        count_file = Path(os.environ["M2S_COORD_DIR"]) / "fake_handler_count"
        n = int(count_file.read_text()) if count_file.exists() else 0
        count_file.write_text(str(n + 1))
        decision = decisions[min(n, len(decisions) - 1)]
        tools.handler_decide(task, decision.get("action", "escalate"), decision.get("target", ""),
                             decision.get("feedback", ""), decision.get("rationale", "scripted"))
        return 0

    plan = script.get(task) or [{"write": {f"storage/stage_reports/{task}.json": {"ok": True}}, "submit": "done"}]
    step = plan[min(attempt - 1, len(plan) - 1)]
    time.sleep(step.get("sleep", 0))
    for event in step.get("stream") or []:  # stdout is the worker's stream.jsonl
        print(json.dumps(event, ensure_ascii=False), flush=True)
    for rel, content in (step.get("write") or {}).items():
        path = ws / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(content, ensure_ascii=False) if not isinstance(content, str) else content, encoding="utf-8")
    submit = step.get("submit", "done")
    if submit == "done":
        tools.submit_task(f"fake {task} attempt {attempt}")
    elif submit == "blocked":
        tools.report_blocked(step.get("reason", "blocked"), step.get("evidence", ""), step.get("suspected", ""))
    return int(step.get("exit", 0))


if __name__ == "__main__":
    sys.exit(main())
