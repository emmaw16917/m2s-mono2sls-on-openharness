# OpenHarness patches required by this pipeline

The M2S v3 dispatcher (`m2s-mcp-server/coord/`) and the permission model need a few
changes that the upstream release does not have. They live here as a patch series
because the fork has not been published separately.

- Upstream: <https://github.com/HKUDS/OpenHarness.git>
- Base commit: **9b2efd7** (the commit this series was generated against)
- Branch/commit the series was generated from: `feat/cli-settings-flags` @ **e4eb3f0**

## Apply

```bash
git clone https://github.com/HKUDS/OpenHarness.git
cd OpenHarness
git checkout 9b2efd7
git am /path/to/known-issues/openharness-m2s/*.patch   # in filename order
# install as usual (pip install -e . or the project's own instructions)
```

`git apply` works too, in the same order. If upstream has moved past 9b2efd7, the
series may need a rebase; the commit subjects below identify the changes.

## What each patch does

| Patch | Change | Needed by |
|---|---|---|
| 0001 | `task-worker` stdin is decoded as UTF-8, not the OS codepage | Windows with a non-UTF-8 console (GBK/Chinese locale); same fix as `../openharness-gbk-stdin-fix.patch` |
| 0002 | Retry transient transport errors in the OpenAI-compatible client | every long run (model network errors) |
| 0003 | Retry transport errors from the vendored HTTP stacks (openai 3.x httpx2/httpcore2) | every long run |
| 0004 | `--settings`, tool and MCP flags actually take effect in `-p` mode | **the v3 dispatcher** — it passes a per-task `--settings` and `--mcp-config`; without this the worker's `m2s-server` lacks `M2S_COORD_DIR`/`M2S_TASK_ID` and `submit_task` fails with "not started by the v3 dispatcher" |
| 0005 | Read/write-scoped path rules, `writable_paths`, deny-before-allow | the v3 dispatcher's per-task permission overlays (`deny_read`, scope) |
| 0006 | Subagents inherit the parent's permission overlay and definition limits | both orchestration paths (a worker cannot escape its restrictions by spawning a subagent) |
| 0007 | `assistant_complete` carries token usage in `stream-json` mode | the run report's token column (without it the column stays 0) |

The v1 skill path (`m2s-migrate` driven by one `oh -p` session) only needs 0001; the
v3 dispatcher needs the whole series.

## Checks

- `git log --oneline -7` shows the seven subjects in the table above.
- The integration test `m2s-mcp-server/tests/test_coord_perm.py` (gated by
  `M2S_RUN_LLM_TESTS=1`) exercises the permission overlay with a real worker;
  `tests/test_coord_j1_perm.py` covers the read isolation of the judge line.
- `python -m coord.toolchain install` installs the pinned `br` / `agent_mail` tools
  the dispatcher uses.
