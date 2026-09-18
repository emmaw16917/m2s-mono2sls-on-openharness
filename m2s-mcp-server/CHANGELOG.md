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

### 对已提交内容的小修正

`wrap_m2s.analyze()` 原来把 `analysis_report.json` / `symbol_table.json` 直接写在工作区根目录，P1 设计 `gates.py` 时改成统一遵循 M2S 自己的 `storage/`-`output/` 目录约定（`<workspace>/storage/analysis_report.json` 等），因为 P2 的 architect/code-developer/sam-engineer 也会照抄 M2S 的这套目录习惯。已同步修正，不影响 P0 已验证的分析结果本身（entry_point_count 仍是 8）。

## P1 — gates.py + pytest（2026-09-18）

### 产出

- `gates.py`：把 M2S `tasks.yaml` 里 C1/C2/C3/C7/C10/C11/C12 的机械检查（含 `_CFNLoader`，从 `file_tools.py` 移植、去掉 crewai 依赖）和 B0（blueprint 完整性 + 残骸探测）用纯 Python 重写为确定性函数；C4/C5/C6/C8/C9 实现为"suspects 预筛"（只做机械抽取，不下判决，供 P2 的 m2s-validator 子 Agent 做语义裁决）。`run_gate(phase, workspace)` 按 `blueprint`/`code`/`template`/`final` 四个阶段分发，重伤判定规则照抄计划里的 run_gate 分阶段表。
- `server.py` 里 `run_gate` 工具的占位实现已替换为真正调用 `gates.run_gate()`。
- `tests/test_gates.py`：31 个 pytest 用例，覆盖每个机械检查的好/坏两侧、`run_gate` 四阶段分发、以及一个真实的"blueprint2/3/4.json 残骸"夹具（`tests/fixtures/bad_blueprint_residue/`，对应计划里记录的历史崩溃场景）。全部通过。

### 验证

1. `pytest tests/test_gates.py`：31 passed。
2. **对 M2S 真实参考产物跑 `run_gate`**（`Mono2Sls-artifacts/M2S` 本身，其 `output/consistency_report.json` 已经是 M2S 自己判定的 `status: PASS`）：`blueprint`/`code`/`template`/`final` 四个阶段全部返回 `PASS`，`mechanical_failures` 均为 0 —— 与 M2S 的既有判定一致，说明重写的检查逻辑没有偏离原始语义。
3. 通过 `server.run_gate()`（MCP 工具函数本身，不只是 `gates.py` 模块）对真实数据再跑一次，结果一致，确认 MCP 层接线正确。

### 已知限制 / 留给后续阶段的说明

- **C4 suspects 会正常出现"误报"，这是设计使然**：对 todo 的 6 个 Node Lambda，C4 都会标出 `AWS_REGION` "used_but_not_declared"，但 `AWS_REGION` 是 Lambda 运行时自动注入的环境变量，不需要在 template 里声明——这正是 tasks.yaml 里 C4 描述的语义判断部分（哪些"未声明"其实是合理的），机械抽取只负责把候选列出来，`m2s-validator`（P2）需要知道这条例外规则再下判决，不能直接把 suspects 列表当 ERROR 处理。
- **C5 suspects 目前只扫描 Python 的 `boto3.client/resource(...)` 调用**，忠实照抄了 tasks.yaml C5 原文的范围；todo 是纯 Node.js 项目，用的是 `DynamoDBDocumentClient` 而非 boto3，所以这次真实数据验证里 C5 suspects 是空的（不代表"没有 IAM 策略问题"，只代表"机械抽取的范围本来就不覆盖 Node.js SDK 调用模式"）。如果后续要迁移的单体里有 Python Lambda 大量使用 boto3，这条检查会启用；纯 Node.js 场景下 C5 的实际把关目前完全依赖 validator 的语义判断。

### 修正：C7b / C8 / C9 从 suspects 升级为硬检查

复盘后发现，把 C4/C5/C6/C8/C9 五个一起归成"suspects 预筛"这个分组本身不对——真正的分界线不是"是哪几个 check_id"，而是**要不要理解任意生成代码的语义**：

- C1、C2、C7 是纯结构化数据比对（目录名、CodeUri、Path/Method 字符串），已经是硬检查。
- **C7b、C8、C9 其实也是纯结构化比对**（`blueprint.json` 的 auth/publishes_to/trigger_type/sqs_queues/eventbridge_rules 字段 ↔ `template.yaml` 的 Auth/Policies/Environment/Events/Resources），完全不需要读取任意的 handler 源码。tasks.yaml 自己也把 C8/C9 单独分在 "Phase D: Async resource checks"，明确排除在需要"读一次 handler"的 Phase B（C3/C4/C5/C6）之外。之前误把它们和 C4/C5/C6 归在一起做成"只抽取不裁决"的 suspects，是分组标准用错了。
- 只有 C4（环境变量语义 + 共享层间接访问的追踪）、C5（哪个 SDK 调用对应哪条 IAM Policy）、C6（判断 import 语句是否构成"依赖共享层"）需要正则/语义解析任意代码内容，continue 保留为 suspects——机械抽取在这类场景下有真实的假阳性/假阴性风险，而 gate 是权威裁决，误判会白白消耗重试次数（N=2 上限），不如交给 validator 结合上下文判断。

处理：新增 `check_c7b`（逐端点核对 `Auth: Authorizer: NONE` 覆盖是否和 blueprint auth 字段一致）、`check_c8`（SQS 队列/Policy/QUEUE_URL/Event source 四项交叉核对）、`check_c9`（EventBridge Rule/Target/Permission/trigger_type 四项交叉核对），全部接入 `run_gate` 的 `template`/`final` 阶段；对应从 suspects 里删掉 `suspects_c8_sqs`/`suspects_c9_eventbridge`。`run_gate("template")` 的 `suspects` 字段现在只剩 `{C4, C5, C6}`。

新增 16 个 pytest 用例（C7b 4 个、C8 5 个、C9 6 个、外加各自的"无异步流程时应为空"用例），全部通过（**测试总数 31 → 47**）；重新对 M2S 真实参考数据跑 `run_gate("template")`，仍然是 `PASS`、0 个机械失败——todo 本身没有 auth="none" 端点也没有 SQS/EventBridge，新增的三个硬检查在真实数据上正确保持沉默，没有引入误报。

## P2 — 知识 Skill + 编排 Skill + 四个 Agent 定义（2026-09-18）

### 补做：P1 遗漏的 `m2s_merge.py`

写 agent/skill 之前发现一个缺口：`m2s-migrate` 编排协议和 architect/sam-engineer 的分节写入设计，都依赖一个确定性合并工具，但 P1 只做了 `gates.py`，没有做 `m2s_merge.py`——这正是计划里"防复发措施：分节文件 + 确定性合并 + B0 gate"里"确定性合并"那一环，缺了就等于双保险只剩一半。补上了：

- `merge_blueprint(workspace)`：把 `storage/blueprint_sections/*.json`（按文件名排序）做 JSON 顶层 key 并集，写出 `storage/blueprint.json`。纯 JSON 操作，没有 YAML/CFN 标签的有损往返问题。
- `merge_template(workspace)`：把 `output/template.part1.yaml` 和 `template.part2.yaml` **按纯文本拼接**，而不是解析成 YAML 对象再重新序列化——因为 `gates.py` 的 `_CFNLoader`（从 `file_tools.py` 移植）是有损的：`!Ref TodoItems` 解析后就变成裸字符串 `"TodoItems"`，重新序列化会把 CFN 内建函数标签丢光，产出一个语法错误的模板。文本拼接没有这个风险，切分点直接复用真实 `template.yaml` 里本来就有的 `# --- END METADATA ---` 注释锚点。
- `tests/test_merge.py`：6 个用例，其中 `test_merge_matches_real_m2s_reference` 是最有说服力的一个——把真实 M2S 的 `blueprint.json` 按两个 section 拆开、把真实 `template.yaml` 按真实的 METADATA 锚点行拆成两个 part，合并回去后跟原文件逐字节相等（blueprint 侧），或者跑 `gates.py` 仍判 `PASS`（template 侧）。测试总数 47 → 53，全部通过。

### 知识 Skill（4 个，纯包装，内容原样来自 M2S）

`.openharness/skills/` 下新建：`m2s-basic-serverless-kb`、`m2s-async-patterns-kb`、`m2s-lambda-coding-kb`、`m2s-sam-kb`，各自把 `M2S/knowledge/*.md` 的对应文件原样搬进 `SKILL.md` 正文，只在文件开头加了 YAML frontmatter（`name`/`description`）。`description` 里写清楚了"哪个 agent 在什么场景下加载"，方便模型判断要不要主动加载。

### 编排 Skill：`m2s-migrate`

`.openharness/skills/m2s-migrate/SKILL.md`，按计划 §0-§7 大纲写：常量表（MONOLITH/WS/MERGE 绝对路径、MCP 工具全名、四个 agent 名精确契约）、阶段拓扑（P0 主 Agent 自己跑 `analyze_project` 不 spawn → S1→S2→S3(×3)→S4 → 主 Agent 亲自 `gate(final)`）、分层裁决表（重伤/轻伤怎么处理，含"template 阶段失败但集中在 C3/C10/C11/C12 时应该回炉 code-developer 而不是 sam-engineer"这条容易漏的规则）、三层完成度信号（进程级/报告级/事实级）、文件契约表、蓝图防截断双保险（明确写死"合并是主 Agent 的职责，子 Agent 只管写分片文件，不能自己调 `m2s_merge.py`"，避免子 Agent 半途崩溃连累合并步骤）。

### 四个 Agent 定义（`~/.openharness/agents/*.md`，不在本仓库版本控制范围内——见下方"已知缺口"）

`m2s-architect`、`m2s-code-developer`、`m2s-sam-engineer`、`m2s-validator`。frontmatter 只写 `name`/`description`/`model: inherit`，**不写 `tools`/`skills`/`mcpServers`/`permissions`**——P0 已经核实过 `agent_tool.py` 只透传 `system_prompt`+`model` 给子进程，这些字段"已解析未接线"，写了也没用，正文必须自包含（显式点名 skill 名 + MCP 工具全名）。四个 agent 的身份/职责翻译自 M2S 自己的 `agents.yaml`（role/goal/backstory），不是凭空编的。

`m2s-sam-engineer` 是同一个定义文件复用三次（S3a/S3b/S3c），正文里用 `STAGE_ID` 条件分支写清楚三次分别做什么——这是因为 OpenHarness 的 Agent Definition 是静态文件，不支持"同一个 agent 根据参数变身份"，只能在正文里自己做分支。

四个 agent 的正文都显式写了"不要调用 `mcp_auth`"（P0 踩过的坑），以及各自的输出契约最后一步"写 `storage/stage_reports/<STAGE_ID>.json` 再输出 `STAGE_COMPLETE:`"。

### 验证

1. 用 OpenHarness 真实的 `load_skill_registry()`/`get_all_agent_definitions()`（cwd=Mono2SLs）加载，5 个 skill 全部 `source=project`，`command_name` 正确对应目录名；4 个 agent 全部加载成功，`model='inherit'`，且确认 `tools`/`skills`/`mcp_servers`/`permissions` 字段确实是空的（符合"正文自包含"的设计）。
2. **真实 spawn 验证**：用 `oh -p` 让主 Agent 通过 `agent` 工具 spawn 一次 `m2s-validator` 的身份确认空跑（明确要求它不读写文件、不调用工具）。`task_get` 返回的 `argv` 字段里能看到 `--task-worker --system-prompt '## 身份与使命\n\n你是一名严谨的 QA 工程师...'`——**证实 `--system-prompt` 整体覆盖确实生效，不是文档猜测**。子 Agent 的回复正确复述了 `m2s-validator.md` 里写的身份（QA 工程师）、MCP 工具全名（`validate_sam`/`run_gate`）、skill 名（`m2s-sam-kb`/`m2s-lambda-coding-kb`），并且这次严格遵守了"不调用工具"的指令，没有重演 P0 那次乱调 `mcp_auth` 的问题（这次任务本身更简单，不代表模型可靠性问题已经解决，见下方风险提醒）。
3. 顺带拿到了 `task_get` 的真实字段名（`id`/`type`/`status`/`description`/`return_code`/`ended_at`/`prompt`/`argv`），`type` 确认是 `"local_agent"`，与之前核实的事实一致。

### 已知缺口 / 留给 P3-P4 的说明

- **四个 agent `.md` 的运行位置（`C:\Users\36029\.openharness\agents\`）本身不在仓库版本控制范围内**（照抄计划文件清单的路径约定，跟 `~/.openharness/settings.json` 一样是用户级配置，OpenHarness 只认这个固定路径，无法指向仓库内路径）。为了不丢失这份内容，已经把内容原样镜像了一份到 `m2s-mcp-server/agents/`（`m2s-architect.md`/`m2s-code-developer.md`/`m2s-sam-engineer.md`/`m2s-validator.md`），作为版本控制下的 source of truth；`~/.openharness/agents/` 下的是"部署副本"。**这两处目前需要手动保持同步**——以后改了 agent 定义，两边都要改（或者写个同步脚本，留给 P4）。
- **模型可靠性仍是悬而未决的风险**：这次 dry-run 测试任务很简单（"不要调用任何工具，只复述身份"），模型顺利照做，但不能就此认为 P0 发现的"qwen-plus 会无视明确指令自作主张调用工具"问题已经解决——S1-S4 的真实任务复杂得多（读写多个文件、调用真实 MCP 工具、处理回炉逻辑），P3 全链跑通时才是真正的压力测试。
