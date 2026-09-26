# m2s-mcp-server

Runbook for the Mono2Sls → OpenHarness migration pipeline (implements the plan at
`C:\Users\36029\.claude\plans\golden-discovering-sloth.md`). This directory holds the
deterministic MCP tools that back the migration; the orchestration skill and the four
migration agents live outside this directory (see "Where things live" below) because
OpenHarness only discovers them at fixed paths.

For a detailed, chronological account of what was built, what broke, and why —
including six distinct LLM failure modes hit and fixed during development — read
`CHANGELOG.md`. This README is the "how do I run it / where is everything" reference;
the CHANGELOG is the "why does it work this way" reference.

## What this does

Migrates the `todo` monolith under `Mono2Sls-artifacts/mono_benchmark/todo` to an AWS
SAM serverless design, entirely offline (no AWS credentials, no deployment). The
pipeline produces:

- `storage/blueprint.json` — the architecture design (Lambdas, DynamoDB, Cognito, API
  Gateway, dropped auth endpoints)
- `output/lambdas/<domain>/<name>/` — one handler + dependency file per Lambda
- `output/template.yaml` — a SAM template that passes `cfn-lint`
- `output/consistency_report.json` — a 12-check cross-validation report

## Prerequisites

- conda env `mono2sls` (M2S's own env): has FastMCP (`mcp` 1.28.1) and `cfn-lint.exe`
  next to `python.exe`. Runs `server.py`, `gates.py`, `m2s_merge.py`, `wrap_m2s.py`.
- conda env `mono2esls` (OpenHarness's own env): runs `oh`. Must have `mcp` pinned to a
  **1.x** release (see "Known environment gotchas" below) — this is unrelated to this
  project's own code but will silently break every MCP tool if violated.
- `~/.openharness/settings.json` already has `mcp_servers.m2s-server`,
  `permission.allowed_tools` (the four `mcp__m2s-server__*` tools), and two hooks
  wired up (see "Where things live"). If you're setting this up fresh on another
  machine, see "Fresh-machine setup" below.

## Running the pipeline

```powershell
# from D:\Emma\MyLab\Mono2SLs
& "C:\Users\36029\miniconda3\envs\mono2esls\Scripts\oh.exe" -p "/m2s-migrate 严格按 SKILL.md 里的阶段拓扑执行，从 P0 开始，不要跳过任何一步的 gate 检查。全程不要调用 mcp_auth。你自己的进程必须保持存活直到整条流水线结束（成功、失败或达到重试上限）才能退出——不要在 spawn 子 Agent 后过早结束会话，必须持续轮询直到子 Agent 状态变成 completed 或 failed，只有 task_get 明确返回 failed 才算失败，不要凭经过时间猜测子 Agent 卡住了，也不要主动停止仍在运行的子 Agent 或改写它的 system prompt。如果 gate(final) 不是 PASS，最终回复必须明确说迁移未完成，不要说成功。" --output-format text --max-turns 200
```

Why the prompt is this long: the orchestration skill's own text (`m2s-migrate/SKILL.md`)
covers the *protocol*, but three behavioral guardrails were cheap enough to also repeat
in the invoking prompt as belt-and-suspenders, because each one was an actual failure
mode hit during development (see CHANGELOG "P3" section): declaring the run stalled
based on elapsed time and killing a healthy subagent, self-rewriting a spawned agent's
prompt, and declaring success when `gate(final)` actually failed. Run with
`PYTHONIOENCODING=utf-8` / `PYTHONUTF8=1` set if invoking from a shell where the
console codepage isn't UTF-8 (Windows GBK consoles otherwise crash on emoji/CJK output
partway through a long run).

**Always start from a clean workspace** unless you're deliberately resuming a partial
run (see "Resuming from a specific stage" below):

```bash
rm -rf "D:\Emma\MyLab\Mono2SLs\Mono2Sls-artifacts\M2S\runs"
```

A full clean run takes on the order of tens of minutes (four sequential agent spawns,
each with a 15s inter-spawn cooldown and 20-30s poll intervals, plus real LLM latency
per step). Run it in the background and wait for the process to exit; don't try to
babysit it turn-by-turn.

### Verifying the result yourself (don't trust the model's own summary)

The orchestrator's final chat message is not authoritative — independently confirmed
during P3 that it can (and once did, before a fix) claim success when `gate(final)`
had actually failed. Check the real artifacts:

```bash
# from m2s-mcp-server/, in the mono2sls env
conda run -n mono2sls python -c "import gates,json; print(json.dumps(gates.run_gate('final', r'D:\Emma\MyLab\Mono2SLs\Mono2Sls-artifacts\M2S\runs\todo-oh'), indent=2))"
cat "D:\Emma\MyLab\Mono2SLs\Mono2Sls-artifacts\M2S\runs\todo-oh\output\consistency_report.json"
```

Both should say `"status": "PASS"` with empty `mechanical_failures`/`findings`. If they
disagree with what the orchestrator said in chat, the gate result is the one to
believe.

### Resuming from a specific stage

If S1-S3 already produced a valid `blueprint.json`/`template.yaml` (check with
`gates.run_gate("template", WS)` first), you don't need to redo them — just tell the
orchestrator which stage to start from and that the earlier ones are already verified.
This is much cheaper than a full rerun when only the last stage needs another attempt.

## Where things live (and why it's split across three locations)

OpenHarness only discovers skills and agents at fixed paths, so this isn't all under
one directory:

| What | Where | Why there |
|---|---|---|
| MCP tools, gates, merge logic | `m2s-mcp-server/` (this dir) | Ordinary Python package, run as a stdio MCP server |
| Orchestration + knowledge skills | `Mono2Sls\.openharness\skills\{m2s-migrate,m2s-*-kb}\SKILL.md` | Project-level skill discovery walks up from cwd to git root looking for `.openharness/skills/` |
| Four agent definitions | `~/.openharness/agents/*.md` (user-level, fixed path) | OpenHarness only loads agent `.md` files from this one path — no project-level equivalent exists. **Mirrored into `m2s-mcp-server/agents/`** as the version-controlled source of truth; the two copies must currently be kept in sync by hand (see CHANGELOG's "已知缺口" section — a sync script is a good P5 candidate) |
| MCP server registration, tool permissions, hooks | `~/.openharness/settings.json` (user-level, not in this repo — contains a live API key) | User-level settings; back up before editing (`settings.json.bak-pre-m2s` sits next to it from the original P0 edit) |

If you sync a changed agent `.md` from `~/.openharness/agents/` back into
`m2s-mcp-server/agents/` (or vice versa), diff them first — don't assume they're still
identical.

## MCP tools (`mcp__m2s-server__*`)

| Tool | What it does |
|---|---|
| `analyze_project(monolith_root?)` | Runs M2S's static analyzer plus `spec_extract.py` (per-endpoint `handler_location`/`auth`/`request`/`responses`/`data_access`, `dynamodb_info.table_schemas`, every item with a `source` citation), writes `storage/analysis_report.json` + `storage/symbol_table.json` |
| `annotate_entry_point(method, path, field, value_json)` | How `m2s-spec-annotator` (S0) writes: replaces one spec field of one endpoint after schema + provenance checks; rejects the whole call on any bad citation (`provenance.py`) |
| `check_provenance()` | S0 gate: every `{file, line, symbol}` citation must exist and the symbol must appear within 2 lines; every endpoint must have request, responses (incl. a success one), auth and at least one business rule |
| `merge_blueprint_key(key, value_json)` | Merges one top-level key into `storage/blueprint.json` (arrays concatenate, everything else overwrites); rejects any key not in the 10-key blueprint schema; re-validates JSON after every write. This is how `m2s-architect` builds the blueprint — one field at a time, not as one giant document (see CHANGELOG for why that distinction mattered) |
| `validate_sam(template_path)` | Runs `cfn-lint` against a SAM template |
| `run_gate(phase)` | Runs the deterministic consistency checks for `blueprint` / `code` / `template` / `final` (see `gates.py`) |

`gates.py` reimplements M2S's own `tasks.yaml` C1-C12 mechanical checks (plus a B0
blueprint-completeness check) as plain Python instead of an LLM-judged CrewAI task.
C4/C5/C6 stay as "suspects" (mechanical extraction, no verdict) because they require
interpreting arbitrary generated code; everything else is a hard pass/fail gate. See
`gates.py`'s module docstring and the CHANGELOG's "P1" section for the reasoning, and
the "refactor(gates)" commit for why C7b/C8/C9 were promoted from suspects to hard
checks after initial review.

## Hooks

Both wired into `~/.openharness/settings.json` under `hooks`:

- `pre_tool_use` (`hook_guard_mcp_auth.py`, matcher `mcp_auth`): blocks the model's
  self-invoked `mcp_auth` tool calls specifically when targeting `m2s-server` (which
  needs no auth of any kind), while leaving `mcp_auth` fully usable for any other MCP
  server. Exists because the model reliably tries to "configure auth" for any newly
  connected MCP server even when told not to (see CHANGELOG's "P0" section).
- `subagent_stop` (`hook_audit_subagent_stop.py`): pure audit logging, appends one JSON
  line per subagent completion to `Mono2Sls-artifacts\M2S\runs\todo-oh\audit.log`
  (`subagent_type`, `task_id`, `status`, `return_code`, timestamp — no transcript
  content, since the hook payload never includes it). Never blocks anything
  (`block_on_failure: false`); a logging failure is swallowed, not surfaced as a
  pipeline error.

## Known environment gotchas

These aren't bugs in this project's code, but they will silently break it if
regressed:

- **`mcp` package version in `mono2esls`**: must be a **1.x** release. OpenHarness's
  own `src/openharness/mcp/client.py` reads the pre-2.0 `Tool.inputSchema` attribute
  (renamed to `input_schema` in `mcp` 2.x), so `mcp==2.x` in the OpenHarness env makes
  every MCP connection silently fail (`state=failed`, zero tools registered, no
  visible error to the model). If `mcp__m2s-server__*` tools stop showing up, check
  `pip show mcp` in `mono2esls` first. `mono2sls` is unaffected by this (server and
  client speak the same 1.x dialect there).
- **A spawned subagent dies when its parent `oh` process exits.** If you tell the
  orchestrator to "spawn and don't wait," the child gets torn down mid-work as soon as
  the parent's runtime shuts down — there is no independent detached-process survival.
  Any test harness that spawns a subagent must keep the parent process alive
  (poll `task_get` in the same session) until the subagent reaches a terminal state.
- **`conda run python -c "<multi-line script>"`** fails on Windows with
  `NotImplementedError: Support for scripts where arguments contain newlines`. Write a
  throwaway `.py` file and run that instead of inlining multi-line `-c` scripts.
- **Windows console codepage (GBK) crashes on CJK/emoji output** from a long `oh -p`
  run. Set `PYTHONIOENCODING=utf-8` and `PYTHONUTF8=1` before invoking `oh`.
