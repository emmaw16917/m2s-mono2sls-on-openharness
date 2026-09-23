# M2S: Mono2Sls monolith → serverless migration, on OpenHarness

This repo has two things:

1. **The pipeline itself** (`m2s-mcp-server/`, `.openharness/skills/`,
   `Mono2Sls-artifacts/`) — clone this, install the prerequisites below, and you can
   run the same migration pipeline against any of the monolith apps under
   `Mono2Sls-artifacts/mono_benchmark/` (`todo`, `bookstore`, `coffee`, `ecommerce`,
   `shopping-cart`, `airline-booking`).
2. **Already-generated baseline output** under `samples/` — see `samples/README.md`.
   These are "no performance optimizations applied yet" samples used to compare
   against the official/reference serverless implementations each monolith was
   originally reverse-engineered from.

If you're here to generate a new app's sample (e.g. `coffee`), you want section
**"Running the pipeline"** below, then **"Contributing a new sample back"** at the end.

## What the pipeline does

Migrates a monolith app (Express/Flask, under `Mono2Sls-artifacts/mono_benchmark/`) to
an AWS SAM serverless design — API Gateway, Lambda, DynamoDB, Cognito, SQS as needed —
entirely offline (no AWS credentials or deployment required to generate and validate
the code). It's orchestrated as an OpenHarness skill (`m2s-migrate`) that spawns four
sub-agents (architect → code-developer → sam-engineer → validator) and self-checks at
every stage against a deterministic MCP tool server (`m2s-mcp-server/`).

For the full "why does it work this way" story (six real LLM failure modes hit and
fixed during development, design rationale for every gate check), read
`m2s-mcp-server/CHANGELOG.md`. `m2s-mcp-server/README.md` is the detailed "how do I run
it" reference this file summarizes.

## Prerequisites

1. **One conda environment (or venv)** running both OpenHarness (`oh`) and the MCP
   server — a single env works fine as long as `mcp` is a **1.x** release (not 2.x:
   FastMCP's `Tool.inputSchema` attribute was renamed to `input_schema` in 2.x, so
   `mcp==2.x` makes every MCP connection silently fail — zero tools registered, no
   visible error. `pip show mcp` and check if this ever regresses). Install:
   - [`HKUDS/OpenHarness`](https://github.com/HKUDS/OpenHarness) per its own
     instructions (gives you `oh`).
   - `pip install aws-sam-cli cfn-lint boto3 pyyaml` (server-side tooling — `gates.py`,
     `wrap_m2s.py`'s `validate_sam`/`analyze_project`). `_find_cfn_lint()` in
     `wrap_m2s.py` locates `cfn-lint` next to whichever Python interpreter is running
     the MCP server (falls back through PATH → next to `sys.executable` → its
     `Scripts/` dir), so this works with no extra PATH configuration regardless of
     which single env you use.

   (Earlier versions of this project used two separate envs because one of them had
   only OpenHarness's own dependencies installed, not because of an actual conflict —
   confirmed by running `server.py`, `validate_sam`, and `analyze_project` all
   successfully under one env with the packages above.)
2. **Node.js 22.x** (the generated Lambda handlers target `nodejs22.x`) and
   **Python 3.12** if a monolith generates Python handlers.
3. **On Windows with a non-UTF-8 console codepage (e.g. GBK/Chinese locale)**: apply
   `known-issues/openharness-gbk-stdin-fix.patch` to your OpenHarness checkout first.
   Without it, a spawned sub-agent whose prompt (or whose tool output) contains any
   non-ASCII text gets **silently corrupted, not just crashed** — `sys.stdin.readline()`
   in `run_task_worker` decodes the parent's UTF-8 bytes using the OS codepage instead.
   `PYTHONUTF8=1`/`PYTHONIOENCODING=utf-8` on the parent process does **not** fix this
   (the `agent` spawn tool has no way to pass env vars to the child) — you need the
   actual code patch. See the patch file's comment for how it was diagnosed and
   verified (a byte-level round-trip test, not just inspection).

## Wiring up the MCP server

In your OpenHarness settings (`~/.openharness/settings.json` by default), add (adjust
paths to wherever you cloned this repo, and to your conda env's `python.exe` — the same
env you installed OpenHarness into, per "Prerequisites" above):

```json
{
  "mcp_servers": {
    "m2s-server": {
      "type": "stdio",
      "command": "<path-to-your-env>/python.exe",
      "args": ["<REPO_ROOT>/m2s-mcp-server/server.py"],
      "env": { "M2S_WORKSPACE": "runs/<app>-oh", "PYTHONUTF8": "1" },
      "cwd": "<REPO_ROOT>/Mono2Sls-artifacts/M2S"
    }
  },
  "permission": {
    "allowed_tools": [
      "mcp__m2s-server__analyze_project",
      "mcp__m2s-server__validate_sam",
      "mcp__m2s-server__run_gate",
      "mcp__m2s-server__merge_blueprint_key"
    ]
  },
  "hooks": {
    "pre_tool_use": [{
      "type": "command",
      "command": "<path-to-your-env>/python.exe <REPO_ROOT>/m2s-mcp-server/hook_guard_mcp_auth.py",
      "matcher": "mcp_auth", "block_on_failure": true, "timeout_seconds": 10
    }],
    "subagent_stop": [{
      "type": "command",
      "command": "<path-to-your-env>/python.exe <REPO_ROOT>/m2s-mcp-server/hook_audit_subagent_stop.py",
      "block_on_failure": false, "timeout_seconds": 10
    }]
  }
}
```

`M2S_WORKSPACE` picks which app you're migrating this session — change it (and restart
the OpenHarness session so the MCP server picks up the new env var) per app. Workspace
output lands at `<REPO_ROOT>/Mono2Sls-artifacts/M2S/runs/<app>-oh/`.

**Agent definitions**: OpenHarness only loads agent `.md` files from
`~/.openharness/agents/` (a fixed user-level path, no project-level equivalent) — copy
the four files from `m2s-mcp-server/agents/` there.

**Skills**: `.openharness/skills/` in this repo is discovered automatically as long as
you run OpenHarness with this repo (or a directory under it) as `cwd` — no separate
wiring needed.

## Running the pipeline

From this repo's root:

```powershell
& "<path-to-openharness-env>\oh.exe" -p "Follow the orchestration protocol in .openharness/skills/m2s-migrate, but use these constants instead of the todo example in SKILL.md section 0:
MONOLITH = <REPO_ROOT>\Mono2Sls-artifacts\mono_benchmark\<app>
WS = <REPO_ROOT>\Mono2Sls-artifacts\M2S\runs\<app>-oh
Run it through all of P0-S4 to gate(final). Use plain ASCII-only text in every spawn prompt. If your own top-level model call fails with a connection error, retry 2-3 times before giving up." --output-format text --max-turns 200
```

Set `PYTHONUTF8=1` / `PYTHONIOENCODING=utf-8` in the shell environment before invoking
`oh` (belt-and-suspenders alongside the GBK patch above).

**Don't trust the model's own final summary.** Verify independently:

```bash
ls Mono2Sls-artifacts/M2S/runs/<app>-oh/output/consistency_report.json  # should exist
python -c "import json; print(json.load(open('Mono2Sls-artifacts/M2S/runs/<app>-oh/output/consistency_report.json'))['status'])"  # should print PASS
```

A `gate(final)` PASS + a real `consistency_report.json` with `status: PASS` is the only
thing that counts as "migration succeeded" — one session in this project's own history
produced a fully fabricated success report (invented task IDs, a stack trace citing a
file that didn't exist) with none of the underlying work actually done. Always check
the file, not the chat output.

## Contributing a new sample back

Once you have a real `gate(final)` PASS for a new app:

1. Copy `Mono2Sls-artifacts/M2S/runs/<app>-oh/output/{lambdas,template.yaml,consistency_report.json}`
   and `Mono2Sls-artifacts/M2S/runs/<app>-oh/storage/{blueprint.json,analysis_report.json}`
   (and `audit.log` if present) into a new `samples/<app>/` folder here, matching the
   layout of `samples/todo/` and `samples/bookstore/`.
2. Don't include `.aws-sam/` (regenerable) or `template.part1.yaml`/`template.part2.yaml`
   (intermediate merge inputs — `template.yaml` is the merged final product).
3. Open a PR with a short note of which app and confirmation that `gate(final)` actually
   passed (paste the real `consistency_report.json` status, not a chat summary).

See `samples/README.md` for the full per-app file listing and what each artifact means.
