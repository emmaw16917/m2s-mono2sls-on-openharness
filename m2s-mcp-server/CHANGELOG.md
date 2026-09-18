# m2s-mcp-server 实施日志

对照 `C:\Users\36029\.claude\plans\golden-discovering-sloth.md`（Mono2Sls → OpenHarness 移植方案）记录每个阶段实际做了什么、验证结果、以及过程中发现的问题和处理方式。

## P0 — 环境验证 + MCP 骨架冒烟测试（2026-09-18）

### 产出

- `server.py`：FastMCP stdio 入口，注册 `analyze_project` / `validate_sam` / `run_gate` 三个工具。`run_gate` 当前是占位实现（返回 `status: not_implemented`），真正的确定性校验逻辑是 P1 的 `gates.py`。
- `wrap_m2s.py`：M2S 复用适配层。直接 `import` M2S 的 `preprocessor.static_analyzer.run_static_analysis`（纯 stdlib，无需绕过 crewai 依赖）；`validate_sam` 自行实现 cfn-lint 子进程调用（不复用 M2S 的 `SAMValidateTool`，因为该文件顶层 `import crewai`）。
- `hook_guard_mcp_auth.py`：见下方"发现的问题 2"。

### 环境验证

- conda env `mono2sls`：确认自带 FastMCP（`mcp` 1.28.1）、`cfn-lint.exe` 与 `python.exe` 同目录（`Scripts/cfn-lint.exe`）。
- conda env `mono2esls`（OpenHarness 运行环境）：见下方"发现的问题 1"。

### 功能验证

1. `wrap_m2s.analyze()` 对 `mono_benchmark/todo` 跑分析，产出的 8 个 entry_points 与 M2S 现存参考 `storage/analysis_report.json` **逐条完全一致**；`symbol_table.json` 分离写入的行为复刻了 `static_analyzer.main()`。
2. `wrap_m2s.validate_sam()` 对现存 `output/template.yaml` 跑 cfn-lint，返回 `ok: true`，PATH 兜底（`Path(sys.executable).parent/"cfn-lint.exe"`）按预期生效。
3. 用真实 MCP stdio 客户端连接 `server.py`，确认 3 个工具协议层可见。
4. 在 OpenHarness 里注册 `mcp_servers.m2s-server`（`~/.openharness/settings.json`），跑通一次完整的 `oh -p` 端到端调用：模型正确调用 `mcp__m2s-server__analyze_project`，拿到 `entry_point_count=8`，与参考数据一致。

### 发现的问题

**1. OpenHarness 自身环境里 `mcp` 库版本与源码不匹配（严重，影响所有 MCP server，非 m2s 专属）**

`mono2esls`（OpenHarness 运行环境）原装的是 `mcp==2.2.0`，但 OpenHarness 源码 `src/openharness/mcp/client.py:276` 访问的是 `tool.inputSchema`（mcp 1.x 的驼峰命名字段）。mcp 2.x 把该字段重命名为 `input_schema`（下划线），导致 OpenHarness 每次连接任何 MCP server、解析工具列表时都会抛 `AttributeError: 'Tool' object has no attribute 'inputSchema'`，连接静默标记为 `failed`，工具从不注册进 ToolRegistry——**在这次修复之前，这套 OpenHarness 装机上任何 MCP server 都连不上**，与我们的 server 实现无关。

处理方式（未改动 OpenHarness 源码，只调整依赖版本）：`conda run -n mono2esls pip install "mcp==1.30.0"`（OpenHarness 自己声明的依赖是 `mcp>=1.0.0`，降级完全在其兼容范围内；M2S 侧 `mono2sls` 环境本来也是 1.28.1）。降级后重新验证：`state=connected`，3 个工具全部正确注册。

**2. 模型（qwen-plus）在连接新 MCP server 时会自发调用内置 `mcp_auth` 工具，编造一个假 token 写入全局 `settings.json`**

`mcp_auth`（`tools/mcp_auth_tool.py`）是 OpenHarness 内置的"给 MCP server 配置认证"工具，设计给真正需要认证的 server 用。qwen-plus 在看到新连接的 `m2s-server`（我们的 stdio server 完全不需要认证）时，会主动调用它，把编造的 `Bearer dummy-token` 写进 `~/.openharness/settings.json` 的 `mcp_servers.m2s-server.env.MCP_AUTH_TOKEN`，即使 prompt 里已明确写"不要调用 mcp_auth"也照样触发，并且会在"重连-重试"里循环，从未真正完成任务。

处理方式：没有用全局 `permission.denied_tools` 一刀切封掉 `mcp_auth`（会连累其他真正需要认证的 server 用不了这个工具），而是加了一个精确打击的 `pre_tool_use` hook（`hook_guard_mcp_auth.py`，`matcher: "mcp_auth"`）：只在 `tool_input.server_name == "m2s-server"` 时拦截，其余情况一律放行。已单测三种场景（拦截 / 放行其他 server / 放行其他工具）；接入 OpenHarness 后重新端到端测试，模型不再触碰 `mcp_auth`，直接调用 `analyze_project` 完成任务。

**3. 附带发现：`-p`（非交互打印模式）和 task-worker 子进程共用同一个 `_noop_permission`**

`ui/app.py` 里 `run_print_mode` 和 task-worker 走的都是 `permission_prompt=_noop_permission`（恒真），意味着不止子 Agent，**主进程用 `oh -p` 跑，任何需要确认的写操作也会被无条件放行**。这次的 `mcp_auth` 问题正是在 `-p` 模式下被无声执行验证到的。已记录进迁移计划的风险表；P1 起所有 `-p`/task-worker 调用前后都应确认 `~/.openharness/settings.json` 没有非预期变化。

### 本地环境改动（均为可逆的依赖/配置变更，未触碰 OpenHarness 或 M2S 源码）

- `~/.openharness/settings.json`：新增 `mcp_servers.m2s-server`、`permission.allowed_tools`（3 个 m2s 工具）、`hooks.pre_tool_use`（`hook_guard_mcp_auth.py`）。改动前备份为 `~/.openharness/settings.json.bak-pre-m2s`。
- `mono2esls` conda 环境：`mcp` 从 `2.2.0` 降级到 `1.30.0`。
