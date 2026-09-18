"""subagent_stop audit hook: appends a JSON line per subagent completion to
runs/todo-oh/audit.log. Pure bookkeeping -- never blocks (always exits 0,
so block_on_failure is irrelevant), never mutates the subagent's result.

Wired via settings.json -> hooks.subagent_stop (no matcher needed: this
list is already scoped to the subagent_stop event).

Payload fields (per HookExecutor / AgentTool's SUBAGENT_STOP emission):
    event, agent_id, task_id, backend_type, status, return_code,
    description, subagent_type, team, mode
No subagent text output is included in this payload (confirmed during P0
research), so this audit trail only ever records completion metadata, not
transcript content.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

AUDIT_LOG = Path(r"D:\Emma\MyLab\Mono2SLs\Mono2Sls-artifacts\M2S\runs\todo-oh\audit.log")


def main() -> int:
    raw = os.environ.get("OPENHARNESS_HOOK_PAYLOAD", "{}")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        payload = {"raw": raw}

    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "subagent_type": payload.get("subagent_type"),
        "task_id": payload.get("task_id"),
        "agent_id": payload.get("agent_id"),
        "status": payload.get("status"),
        "return_code": payload.get("return_code"),
        "backend_type": payload.get("backend_type"),
        "mode": payload.get("mode"),
        "description": payload.get("description"),
    }

    try:
        AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
        with AUDIT_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as exc:
        # Never fail the hook over a logging problem -- audit is best-effort.
        sys.stderr.write(f"hook_audit_subagent_stop: failed to write audit log: {exc}\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
