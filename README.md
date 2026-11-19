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
the code). There are two orchestration paths, both driving the same deterministic MCP
tool server (`m2s-mcp-server/`):

- **the original skill** (`m2s-migrate`): one `oh -p` session that spawns the
  architect → code-developer → sam-engineer → validator sub-agents in order;
- **the v3 dispatcher** (`m2s-mcp-server/coord/`): a deterministic Python loop over a
  task board (`br`). It starts one `oh -p` worker per task, gives each worker a role,
  a task spec, a file scope, a permission overlay and the MCP tools, then runs the
  task's checks itself. The v2 modules (fact base, frozen judge, delta tests,
  adapter check) and the v1 modules both run under it. See
  `docs/pipeline-visual.html` for the full picture of every step, how workers are
  dispatched and how they communicate.

`m2s-mcp-server/README.md` is the detailed "how do I run it / where is everything"
reference this file summarizes.

### Update 2026-10: what was added since the first snapshot

- **Spec line** — `spec_extract.py` / `spec_extract_py.py`, `provenance.py`,
  `global_middleware.py` enrich `analysis_report.json` with per-endpoint contracts, app-wide
  middleware and source locations; the `m2s-spec-annotator` agent reviews them (S0).
- **MongoDB monoliths** — `data_store.py` extracts Mongo access patterns and indexes, the
  architect maps them onto DynamoDB tables (gates DM1/DM2). New benchmark:
  `mono_benchmark/acmeair` (acmeair-nodejs, Apache-2.0).
- **Verification line** — `verify.py` + `verify-tools/` run spec-level test cases written by the
  `m2s-verifier` agent against the generated handlers (RIE containers + DynamoDB Local, or local
  Node + dynalite without Docker), plus coverage and fault-injection equivalence checks.
  Needs `npm install` in `m2s-mcp-server/verify-tools/` and `sam` on PATH (or `M2S_SAM`).
- **Optimization line** — `perf.py` + `perf-rules/` and the `m2s-perf-optimizer` agent (S5):
  rule-driven performance changes, each kept only if the verification line still passes.
- **API type choice** — gates AT1/AT2 pick HTTP API vs REST API per app.
- **Orchestration** — `oh_chain.py` chains/resumes long `oh -p` runs.
- **Optional AWS benchmarking** — `e2e_aws.py`, `e2e_ec2.py`, `e2e_jmeter.py`,
  `e2e_calibrate.py`, `e2e_ec2_recompute.py`, `e2e-tools/`: deploy a generated workspace and
  the monolith to a real AWS account and compare them with JMeter from the same region.
  These need AWS credentials and **create billable resources**; the pipeline itself never
  needs them.

### Update 2026-11: v2/v3 modules and the dispatcher

- **v2 verification modules** — `judge.py` (frozen judge: cases, fixtures, fault
  scenarios, `run_monolith`, `run_judge`, `check_adapter`), `adapter.py` (the adapter
  map), `mutants.py` (mutation testing of the judge), `gaps.py` (gap inventory),
  `runtime_signals.py`. The `m2s-verifier` agent gains the J1/J3/J4/J5/D1/V3 modes;
  `m2s-spec-annotator` gains the fact-base (F2) and gap (G2) modes.
- **v3 dispatcher** — `coord/`: a `br` task board, one `oh -p` worker per task, file
  scopes and machine locks, spec/triage rooms over agent_mail, rulings
  (`storage/rulings.json`), exception handlers, and an event-sourced resume. Templates
  `v1-split.yaml` and `v2-split.yaml` split work by work package / blueprint domain
  (with shared children). Start it with
  `python -m coord.dispatcher run --ws <workspace> --monolith <app> --template v2-split --max-workers 3`.
- **M4 acceptance** — `bookstore-m4` and `airline-m4` ran the v2-split pipeline to C2
  with a frozen judge; the runs are archived under `evidence/` in the internal repo,
  not in this snapshot.
- **Visualization** — `docs/pipeline-visual.html`: the four lines, every step's job,
  how workers are dispatched, and every communication channel (board, files, mail,
  rooms, rulings, handlers, human gates, locks).

## Prerequisites

1. **One conda environment (or venv)** running both OpenHarness (`oh`) and the MCP
   server — a single env works fine as long as `mcp` is a **1.x** release (not 2.x:
   FastMCP's `Tool.inputSchema` attribute was renamed to `input_schema` in 2.x, so
   `mcp==2.x` makes every MCP connection silently fail — zero tools registered, no
   visible error. `pip show mcp` and check if this ever regresses). Install:
   - OpenHarness: clone [`HKUDS/OpenHarness`](https://github.com/HKUDS/OpenHarness),
     check out **9b2efd7**, then apply the M2S patch series in
     [`known-issues/openharness-m2s/`](known-issues/openharness-m2s/README.md)
     (`git am known-issues/openharness-m2s/*.patch`, in filename order; the
     series was generated from branch `feat/cli-settings-flags` @ `e4eb3f0`).
     The upstream release lacks the `--settings` / `--mcp-config` behaviour and
     the read/write path rules the v3 dispatcher relies on: with a stock `oh`
     the workers cannot submit their tasks and the per-task permission overlays
     do not apply. The v1 skill path only needs patch 0001 (the GBK stdin fix).
   - `pip install aws-sam-cli cfn-lint boto3 pyyaml` (server-side tooling — `gates.py`,
     `wrap_m2s.py`'s `validate_sam`/`analyze_project`). `_find_cfn_lint()` in
     `wrap_m2s.py` locates `cfn-lint` next to whichever Python interpreter is running
     the MCP server (falls back through PATH → next to `sys.executable` → its
     `Scripts/` dir), so this works with no extra PATH configuration regardless of
     which single env you use.
   - `pip install pytest` (optional, only needed to run `m2s-mcp-server/tests/`).

   (Earlier versions of this project used two separate envs because one of them had
   only OpenHarness's own dependencies installed, not because of an actual conflict —
   confirmed by running `server.py`, `validate_sam`, `analyze_project`, and the full
   74-test `pytest` suite all successfully under one env with the packages above,
   from a fresh clone of this exact repo.)
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

**Sanity-check your setup** before wiring up OpenHarness — this alone catches most
"did I install the right packages" problems in seconds, no MCP/OpenHarness config
needed yet:

```bash
cd m2s-mcp-server
python -m pytest tests/ -q   # the offline suite; some tests are opt-in:
                            # M2S_RUN_SLOW=1 (full flows), M2S_RUN_LLM_TESTS=1 (real workers)
```

The real-reference merge test reads `Mono2Sls-artifacts/M2S/` (or the directory named
by `M2S_REFERENCE_ROOT`) and skips when that snapshot is absent.

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
the seven files from `m2s-mcp-server/agents/` there.

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

### Or run it under the v3 dispatcher

```bash
cd m2s-mcp-server
python -m coord.dispatcher run \
  --ws <REPO_ROOT>/Mono2Sls-artifacts/M2S/runs/<app>-v2 \
  --monolith <REPO_ROOT>/Mono2Sls-artifacts/mono_benchmark/<app> \
  --template v2-split --max-workers 3
```

It writes `coord/dispatcher.log` and `coord/report.md`, and can resume the same
workspace after an interruption. `M2S_MAIL=1` (with the `agent_mail` MCP server
running) enables the notices board and the discussion rooms; the tool prerequisites
for the dispatcher (`br`, `agent_mail`) are installed by `python -m coord.toolchain
install` (see `coord/toolchain.py` for the pinned versions and checksums).

The dispatcher requires the **patched** OpenHarness from
[`known-issues/openharness-m2s/`](known-issues/openharness-m2s/README.md): it passes a
per-task `--settings` / `--mcp-config` so each worker sees its own scope and the
`M2S_TASK_ID` environment that `submit_task` needs. With the stock upstream `oh` those
flags are inert, and the workers fail before they can submit.

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
