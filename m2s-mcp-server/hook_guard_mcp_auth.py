"""pre_tool_use hook: block `mcp_auth` calls targeting m2s-server specifically.

m2s-server is a local stdio MCP process with no auth of any kind. Models
sometimes call mcp_auth reflexively when they see a newly-connected MCP
server, which pollutes ~/.openharness/settings.json with a fabricated
token and can loop instead of calling the actual tool. This hook denies
only that one (tool_name, server_name) combination and fails open (exit 0,
i.e. allow) for every other tool call and every other MCP server, so
mcp_auth remains fully usable where it's actually needed.

Wired via settings.json -> hooks.pre_tool_use, matcher="mcp_auth".
Reads the event payload from $OPENHARNESS_HOOK_PAYLOAD (set by
HookExecutor._run_command_hook) rather than argv, to avoid Windows
shell-quoting issues with the JSON blob.

Command hooks in OpenHarness block purely on process exit code (see
hooks/executor.py:_run_command_hook: `success = returncode == 0`), so this
script exits non-zero to block and 0 to allow -- it does not need to print
{"ok": ...} JSON (that convention is only for prompt/agent hooks).
"""
from __future__ import annotations

import json
import os
import sys

BLOCKED_SERVERS = {"m2s-server"}


def main() -> int:
    raw = os.environ.get("OPENHARNESS_HOOK_PAYLOAD", "{}")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        # Fail open: never let a bug in this guard script block unrelated tools.
        return 0

    if payload.get("tool_name") != "mcp_auth":
        return 0

    tool_input = payload.get("tool_input") or {}
    server_name = tool_input.get("server_name")
    if server_name in BLOCKED_SERVERS:
        sys.stderr.write(
            f"mcp_auth blocked for '{server_name}': it is a local stdio MCP "
            "server with no authentication. Call mcp__m2s-server__analyze_project, "
            "validate_sam, or run_gate directly -- no auth setup needed.\n"
        )
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
