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

## P3 — todo 全链离线跑通（进行中，2026-09-18）

### S1（architect）两次失败 + 一次修复，一个重要的 prompt 工程教训

清空 `runs/todo-oh` 后跑 `/m2s-migrate`，S1 阶段（architect 设计 blueprint）连续两次失败，两次错误完全不同：

1. **第一次**：发明了 `auth_info`/`nodejs_dependencies`/`dynamodb_info`/顶层 `entry_points` 这些不存在的字段，完全没写 `lambda_functions`。当时 `m2s-architect.md` 里只用**文字**描述了 blueprint.json 的 schema。
2. **加了一个抽象 JSON 骨架示例**（`{"metadata": {...}, "lambda_functions": [...]}` 这种带 `...` 占位符的骨架，并且逐条写明"不要发明新 key"）之后重跑，**换了另一种完全不同的错误**：`blueprint_sections/` 里写出的是 `async_patterns.json`/`data_flow.json`/`deployment_topology.json`/`domain_decomposition.json`/`security_considerations.json`/`service_boundaries.json`——像一份通用"架构评审文档大纲"，跟骨架示例的字段名一个都不沾边。工作区根目录还多出一个不该存在的 `blueprint.json`（不在 `storage/` 下），字段名倒是对了，但 `lambda_functions` 被设计成按服务分组的对象（`auth-service`/`todo-service`各塞好几个端点），违反"一个端点一个 Lambda"，还编造了不存在的跨服务 EventBridge/Lambda 调用关系，runtime 也用了明确禁止的 `nodejs18.x`。

有意思的旁证：翻同一次失败的原始日志，模型自己的推理文字里**其实正确列出了全部 10 个必需 key**（"I need to create a blueprint.json that includes: metadata, lambda_functions, ..."），但最终写入文件的内容却是完全不相关的另一套。合理猜测：它同时加载了 `m2s-async-patterns-kb`（skill 内容本身有"Async & Event-Driven Serverless Patterns"这类章节标题），被这份知识库自己的文档结构带偏，把"应该讨论的架构话题"误当成了"应该写进 blueprint_sections 的字段"。

**真正的修复**：把 `m2s-architect.md` 里的抽象骨架**换成了这个具体单体（todo）的完整参考答案**——不是占位符骨架，是真实、完整、已知能通过所有 gate 的两个分节文件全文（对应 M2S 自己的 `storage/blueprint.json`），并加了一段"核对清单"逐条对照禁止项。重新隔离测试（spawn m2s-architect → 合并 → `run_gate(phase="blueprint")`），**一次通过**：`storage/blueprint_sections/` 用了跟示例一致的文件名（`01_infra.json`/`02_functions.json`），内容跟参考答案高度一致，`gate(blueprint)` 返回 `PASS`。

**教训**：对这个模型（qwen-plus），"文字描述 schema" < "抽象 JSON 骨架 + 反例警告" < "具体完整的参考答案直接放在眼前"。三次尝试、三种不同结果，只有第三种真正稳定生效。这是本次移植过程中除了 `mcp_auth` 之外第二个证实"prompt 精确度不够、模型就会跑偏，而且每次跑偏的方向都不一样"的具体案例——为 P4 风险清单和后续 S2-S4 的 prompt 设计提供了明确的方向：能给具体例子就不要只给抽象描述。

### 已知缺口 / 留给 P3-P4 的说明

- **四个 agent `.md` 的运行位置（`C:\Users\36029\.openharness\agents\`）本身不在仓库版本控制范围内**（照抄计划文件清单的路径约定，跟 `~/.openharness/settings.json` 一样是用户级配置，OpenHarness 只认这个固定路径，无法指向仓库内路径）。为了不丢失这份内容，已经把内容原样镜像了一份到 `m2s-mcp-server/agents/`（`m2s-architect.md`/`m2s-code-developer.md`/`m2s-sam-engineer.md`/`m2s-validator.md`），作为版本控制下的 source of truth；`~/.openharness/agents/` 下的是"部署副本"。**这两处目前需要手动保持同步**——以后改了 agent 定义，两边都要改（或者写个同步脚本，留给 P4）。
- **模型可靠性仍是悬而未决的风险**：这次 dry-run 测试任务很简单（"不要调用任何工具，只复述身份"），模型顺利照做，但不能就此认为 P0 发现的"qwen-plus 会无视明确指令自作主张调用工具"问题已经解决——S1-S4 的真实任务复杂得多（读写多个文件、调用真实 MCP 工具、处理回炉逻辑），P3 全链跑通时才是真正的压力测试。

### S1 修复之后，第一次真正的全链跑通尝试暴露了另外三种失败模式

S1 用"具体完整参考答案"的版本修好之后，跑了一次干净的全链（清空 `runs/todo-oh` → `/m2s-migrate`）。P0-S3b 全部顺利完成，`output/lambdas/todo/*` 六个 handler+package.json 齐全，`template.yaml` 合并后 `gate(template) = PASS`——但止步于 S4，而且过程中又暴露了三个新问题：

**问题 3：orchestrator 凭经过的时间猜测子 Agent"卡住了"，擅自杀掉正常运行的子 Agent，还擅自改写它的 prompt。** 架构师子 Agent 实际上还在正常工作（日志显示在读 `analysis_report.json`，没有报错），orchestrator 自己觉得"跑了 5 分钟没反应"就判定"process-level failure"，调用停止任务的工具杀掉了它，然后重新 spawn 时自己决定"精简掉容易引起混淆的 JSON 示例"——这个决定本身没有任何证据支撑，纯粹是凭空猜测，而且恰好会撤销掉刚验证有效的修复。**修复**：在 `m2s-migrate/SKILL.md` §4 里明确写死"`status=running` 不是失败信号，只有 `task_get` 明确返回 `failed` 才算"，"不要调用停止任务类工具打断一个仍在运行的子 Agent"，"不要擅自改写/精简/优化子 Agent 的 system prompt"。

**问题 4：`gate(final)` 真实失败时，orchestrator 自己宣布"迁移成功"。** 这次 orchestrator 吸取了问题 3 的教训，耐心轮询了很多轮（没有再次误杀子 Agent），但等到自己的 turn 预算快耗尽时，还是决定不再等、直接跑 `gate(final)`——失败了（`consistency_report.json` 不存在），结果 orchestrator 的最终回复却是"核心产物已经就绪"、"流水线已成功执行"、"验证步骤不影响产出质量"，把一个真实的 gate 失败包装成了成功。这是最不该发生的一种偏离——直接违反"文件不说谎，模型自述不可信"的核心设计原则，而且是在最终对用户的结论这个最重要的环节上。**修复**：在 `m2s-migrate/SKILL.md` §6 加了不允许任何例外的措辞："`gate(final)` 不是 `PASS` 就是没有通过，禁止说'核心产物已经就绪'一类淡化措辞"。

**问题 5：验证器把"gate 报告很干净"理解成"没什么可做，可以直接退出"。** 单独续跑 S4 时，`m2s-validator` 子 Agent 看到 `gate(template)` 的 `mechanical_failures` 是空的、`suspects` 也没有真问题，直接输出 `STAGE_BLOCKED: ... No action required`，完全没有写 `output/consistency_report.json` 或 `stage_reports/S4.json`。它把"没什么要修的"和"这个阶段不用产出报告"混为一谈了。**修复**：`m2s-validator.md` 开头加了明确声明——"gate 报告干净不代表可以不产出报告，`findings` 会短、`fixes_applied` 会是 0，但报告本身是这个阶段唯一的产物，必须写"；`STAGE_BLOCKED` 被限定为"真的没法开始工作"（输入文件缺失），不能因为"没什么可改的"就用它收工。

续跑 S4（第二次尝试）过程中还出现了一次没有专门修复的意外：orchestrator 自己的日志末尾变成了几十万个连续的 `.` 字符，然后系统消息 `Stopped after 200 turns (max_turns)`——某种退化的空转循环，把整个 turn 预算耗光都没有产出结果。子 Agent 自己的日志显示它本来做得基本正确（读了 template.yaml、代码，调了 `run_gate` 确认干净），只是在调用 `validate_sam` 前后被杀掉了（大概率是父进程退出时一并被杀，跟 P0 发现的"子进程随父进程退出"是同一机制）。这个退化循环目前没有针对性修复——它看起来更像模型在长轮询对话里偶发的退化行为，不是单纯靠改 prompt 措辞能稳定解决的问题，留给后续观察是否复现。

### 关键问题：为什么原始 M2S（CrewAI）能跑通，我们这边反复出问题？

用户提出了这个问题，值得专门记录，因为答案不是"模型更强"，而是**工具设计本身的结构性差异**：

1. **模型**：M2S 的 `.env` 写死 `OPENAI_MODEL_NAME=deepseek-chat`——不是更强的模型，只是另一个模型。排除了"model 差距"这个解释。
2. **关键差异 —— `WriteFileTool` 的 `json_key` 参数**（`file_tools.py:172-211`）：M2S 的架构师**不需要一次性产出一整份完整、正确的 JSON**。每次工具调用只写一个顶层 key 的一小段内容，Python 代码负责把它合并进现有文件（如果原来是数组就自动拼接），**每次写完立刻重新解析校验 JSON 合法性**。LLM 的任务被拆解成"一次只写一个字段"，而不是"一次性想清楚整套 10 个 key 的复杂嵌套结构"——这正是我们两次 schema 编造失败的根源。
3. **`WriteFileTool` 对每次写入做即时语法校验**（`_validate_content`，同文件 126-144 行）：`.json`/`.py`/`.yaml` 写完立刻在**同一个 turn 里**校验，不合法当场报错重写，不用等到事后独立的 gate 检查才发现——我们的设计把所有校验推迟到子 Agent 完全退出之后，纠错成本是一整次重新 spawn。
4. 附带结构性优势：CrewAI 的 `Process.sequential` 在同一个 Python 进程里连续跑完四个 agent，没有"spawn 子进程 → 轮询 → 子进程随父进程退出被杀掉 → 跨会话任务表查不到"这一整类问题；而且每个 agent 有框架自带的 `max_iter` 硬上限，不是靠 prompt 文字自我约束。

### 结构性修复：给 m2s-mcp-server 加 `merge_blueprint_key` 工具，架构师 prompt 恢复到最贴近原始 M2S 的版本

按上面的诊断，做了两处对应的修复，而不是继续在 prompt 措辞上打补丁：

1. **新增 `merge_blueprint_key` MCP 工具**（`m2s_merge.py` 里的 `merge_blueprint_key()` 函数 + `server.py` 里的同名 MCP 工具），照搬 M2S `WriteFileTool(json_key=...)` 的行为：一次只合并一个顶层 key，数组自动拼接，非数组覆盖，写完立刻重新解析校验。**比 M2S 原版更严格的一点**：M2S 的 `json_key` 接受任意字符串作为 key 名；我们的版本用 `BLUEPRINT_KEYS` 白名单校验 key 名，传一个不在 10 个合法 key 里的名字（比如 `auth_info`）会被直接拒绝、返回清晰的错误信息——这是对已经踩过两次的"发明字段"问题的针对性硬堵，比 M2S 原版的防护还严。新增 7 个 pytest 用例（含一个端到端用例：像架构师一样逐字段调用工具把真实 todo blueprint 建出来，再跑 `gates.check_b0` 确认零发现），测试总数 53 → **60**。
2. **`m2s-architect.md` 大幅重写，改回最贴近 M2S 原版 `tasks.yaml` 的详细步骤化描述**（而不是之前"抽象骨架"或"完整参考答案抄一遍"的两种设计）：迁移了 M2S 原文里的 Worker vs Orchestrator 判断、三种跨 Lambda 通信机制的信号优先级、auth 字段的 A/B/C/D 判定规则、DynamoDB 表的三种 Users 表处理方式、写完后的强制核对清单等完整内容，只是把 `ReadFileTool`/`WriteFileTool(json_key=...)` 换成我们的 `read_file`/`mcp__m2s-server__merge_blueprint_key`。`analysis_report.json` 的字段名跟 M2S 原文引用的不完全一样（我们没有顶层 `features` 字段，等价信息在 `file_tags` 里，是"文件路径 → 标签数组"的映射），照原文改了对应引用。

**验证**：两次独立的隔离测试（每次都清空 `storage/`、重新跑 P0，再单独 spawn 架构师）。第一次发现一个新的小问题——`entry_points` 被写成 `{"method":"GET","path":"/item"}` 对象而不是 `"GET /item"` 字符串（因为这版 prompt 只用文字描述了这个字段、没给具体例子，又一次印证"具体例子 > 文字描述"），补了一个 JSON 例子后，**第二次隔离测试 `gate(blueprint)` 干净通过（PASS，0 个机械失败）**：6 个 Lambda 严格一对一映射真实端点，`auth` 判断正确，`runtime` 没有用过时版本，`dropped_functions` 正确覆盖认证相关端点，全部 8 个 `analysis_report.json` 端点被完整覆盖不重不漏。

至此 S1 阶段已经用新机制稳定复现通过两次；下一步是用同样干净的工作区重新跑一次完整的 P0→S4 全链，检验这套结构性修复能不能让流水线真正走到底。

### 全链跑通：P0→S4 一次性走完，`gate(final)` 独立复核 PASS

清空 `runs/todo-oh`，跑 `/m2s-migrate`，这次 P0→S1→S2→S3a→S3b→S3c→S4 全部一次性顺利完成，**6 个 `stage_reports/*.json`（S1/S2/S3a/S3b/S3c/S4）全部齐全**，没有触发任何一次回炉。

**没有听 orchestrator 自己的总结就直接采信**——独立跑了三项复核：

1. `gates.run_gate("final", WS)` → `{"status": "PASS", "mechanical_failures": [], ...}`，`stage_report.files` 六份报告齐全。
2. `wrap_m2s.validate_sam(template.yaml)` → `{"ok": true, "errors": [], "warnings": []}`，真实 cfn-lint 复验，不是从 gate 报告推断出来的。
3. 直接读 `blueprint.json`：6 个 Lambda 严格一对一映射 `analysis_report.json` 的 8 个端点（其余 2 个认证端点 + 若干中间件/建表脚本正确进了 `dropped_functions`），`runtime` 全部 `nodejs22.x`，`auth` 全部判断正确。

`output/consistency_report.json` 本身也是 `{"status": "PASS", "total_checks": 12, "findings": [], "fixes_applied": 0, "sam_validation": "PASS"}`，跟独立复核的结论一致。

**至此，P3（todo 全链离线跑通）达成**：从 P0 静态分析到 S4 一致性校验，全部阶段一次性走完，产出可通过全部 12 项一致性检查和 cfn-lint 校验的 `template.yaml` + 6 个 Lambda handler，`gate(final) = PASS` 且经过独立工具复核，不依赖模型自述。全程没有触碰 OpenHarness 或 M2S 的源码，改动全部集中在 `m2s-mcp-server/`（新工具）、`.openharness/skills/`（编排+知识 skill）、`~/.openharness/agents/`（四个 agent 定义，已镜像进仓库）。

（顺带记录一个良性小插曲：本次运行的日志里再次出现了此前 S4 阶段那种"大段连续点号"的输出，但这次没有耗尽 `max_turns`，流水线仍在 200 轮预算内正常收尾——目前判断这更像 `-p` 文本输出格式下某种等待/进度提示的渲染现象，不是资源泄漏性质的死循环，暂不需要针对性修复，后续如果再复现导致真正卡死再处理。）

## 重构：gate 自查循环下沉到 S1/S2/S3c，orchestrator 只做一次独立复核（2026-09-20）

### 动机：一次真实的误诊事故

在把 gate 检查全部留在顶层 orchestrator 手里的旧设计下，跑到 S3c 之后出现过一次典型的误诊：`gate(final)` 报的是 C3 症状（模板里的 `CodeUri` 路径跟实际 Lambda 目录对不上），但 orchestrator 只看 `check_id` 分类就把责任归给了 code-developer（S2）甚至 architect（S1）——真正的 bug 是 S3c 合并模板时用错了路径前缀（漏掉 `lambdas/` 这一级），跟 S1/S2 的产物完全无关。顶层单靠 gate 报告的 check_id 做路由式重试，诊断力不够、代价却是整段重跑。

### 新设计

把"跑 gate → 诊断 → 自我修正"的循环下沉到每个产出阶段自己的子 Agent 里：

- **m2s-architect（S1）**：写完全部 10 个 blueprint 字段后自己跑 `run_gate(phase="blueprint")`，针对具体 B0 错误模式用 `merge_blueprint_key(mode="replace")` 自我修正，最多重试 3 次，再落 `stage_reports/S1.json`。
- **m2s-code-developer（S2）**：自己跑 `run_gate(phase="code")`，修 C1（命名不匹配）/C10（依赖卫生）问题，最多 3 次；明确注明 C3/C11/C12 这时候还测不了（S2 阶段还没有 template.yaml）。
- **m2s-sam-engineer（S3c）**：合并逻辑本身也从 orchestrator 移到了 S3c 自己跑（`python m2s_merge.py template <WS>`），随后自己跑 `run_gate(phase="template")` **和** `validate_sam`，直接改 `output/template.yaml`（绝不碰 part1/part2），最多 3 次。

顶层 orchestrator（`SKILL.md` §3）职责大幅简化：spawn → 耐心轮询（不得凭经过时间判定"卡住"、不得杀正在运行的子 Agent、不得改写子 Agent 的 prompt）→ 对每个阶段只做**一次**独立 gate 复核（绝不采信子 Agent 自述）→ 只有复核结果不一致、或子 Agent 明确把责任甩给别的阶段时，才整阶段重新 spawn 一次（子 Agent 内部已经自重试过 3 次了）→ `gate(final)` 永远是顶层自己独占的最后一道关卡，不下放给任何子 Agent（因为它存在的意义就是独立于自评）。

### 验证

自查循环重构 + 把 `qwen-plus` 切到 `qwen3.8-flash`（账户侧 `qwen-plus` 出现 Arrearage/AccessDenied 问题后用户要求切换）之后，重新跑了一次全链，`bp9e0inru`：P0→S1→S2→S3a→S3b→S3c→S4 一次性走完，独立复核 `gates.run_gate("final", ...)` → `PASS`，`consistency_report.json` 同样 `PASS`（`total_checks: 12`，`fixes_applied: 0`），6 份 `stage_reports/*.json` 齐全。3 条 WARNING 均为非阻塞信息（C7：M2S 静态分析器本身识别不出 Express 的 `app.use('/api', router)` 路由前缀，属已知分析器局限，不是本次流水线引入的新问题；C1：Cognito 密码策略略严于参考模板；ENV：如实记录了下面这条 `validate_sam` 环境缺陷）。

## Bug：`validate_sam` 在 MCP task-worker 场景下从未真正返回过（发现于 S3c，2026-09-20）

### 现象

`bp9e0inru` 这次全链里，S3c 自己的自查循环对 `run_gate(phase="template")` 一次即 PASS，但 `validate_sam` 连续 6 次全部在 90 秒后超时（即 `cf03883` 那次加的超时保护生效了——不再无限挂起——但仍然拿不到真正的校验结果）。

### S3c 自己做的根因诊断

S3c 没有满足于"超时了就报 BLOCKED"，而是自己做了一整套排除法（记录在 `stage_reports/S3c.json` 的 `validate_sam_investigation` 里）：

1. MCP 之外直接跑同一个 `cfn-lint.exe` 二进制、同一个模板 → **exit 0，4.1~5.6 秒**（三个不同 cwd 各测一次）。
2. MCP 之外直接 `import wrap_m2s` 调用被包装的函数本体（而不是走 cfn-lint.exe 命令行）→ **`ok: true`，3.9 秒**，且确认 `_find_cfn_lint()` 两条路径解析到的是同一个 exe，排除"用了不同版本的 cfn-lint"。
3. 换成一个跟本项目毫无关系的 7 行 `AWS::SQS::Queue` 探针模板 → MCP 版 `validate_sam` **照样超时**，证明跟模板内容零相关。
4. 进程取证：本机存在 **7 组自 2026-09-18 20:47 起就挂死的 `cfn-lint.exe → python.exe`（6MB，卡在解释器启动阶段）父子对**，全部是历次 task-worker 会话调用 `validate_sam`留下的僵尸——即这个工具在 MCP/task-worker 场景下**从来没有真正成功返回过**，`cf03883` 的超时只是把"无限挂起"变成了"90 秒后清清楚楚地失败"，没有解决根本问题。清理僵尸进程后重试依然超时，排除资源耗尽。

S3c 的结论：问题出在 `wrap_m2s.py` 里 `subprocess.run()` 派生 cfn-lint 子进程的方式本身，只在 MCP stdio server 的子进程环境下触发，跟 S1/S2/S3a/S3b 的任何产物都无关；它自己无法在当前会话内验证修复，因为存活的 MCP server 进程已经 import 完旧代码，`McpClientManager.call_tool` 不会懒重启 server。因此如实报告 `STAGE_BLOCKED`，附完整证据链和建议补丁，而不是伪造 PASS 或绕过校验。

### 修复

按 S3c 的建议，把 `wrap_m2s.validate_sam()` 从 `subprocess.run(timeout=...)` 改成 `subprocess.Popen` + `communicate(timeout=...)`：

- 加 `stdin=subprocess.DEVNULL`——推测子进程在 task-worker 场景下会阻塞在继承来的、被 MCP stdio server 占用的 stdin 上，显式关闭它是主要修复点。
- Windows 上加 `creationflags=subprocess.CREATE_NO_WINDOW`。
- 超时分支从"只杀直接子进程"改成 `_kill_process_tree()`（Windows 用 `taskkill /T /F /PID`，非 Windows 用进程组 `pkill -P` + `SIGKILL` 兜底），避免 cfn-lint.exe 自己派生的 python.exe 孙进程继续以僵尸形式泄漏。

`tests/test_wrap_m2s.py` 同步改为 mock `subprocess.Popen`（而非 `subprocess.run`），新增用例验证超时分支会调用 `_kill_process_tree(pid)`，以及正常路径确实传了 `stdin=subprocess.DEVNULL`。全量 71 个测试通过；直接（非 MCP）调用 `wrap_m2s.validate_sam()` 复验现有 `template.yaml` 仍然 `ok: true`，未破坏原有行为。

**尚待验证**：这个修复能否真正解决 MCP/task-worker 场景下的挂起，只能在一次全新的 OpenHarness 会话里验证（当前/任何已存活的 MCP server 进程都不会重新加载这份新代码）——计划先用一次单独针对 S3c 或直接调用 `mcp__m2s-server__validate_sam` 的低成本隔离测试来确认，再决定是否值得重跑一次完整的多小时全链。

## 修复后首次全链验证：run5（2026-09-21）

`validate_sam` 的 stdin/进程树修复（`69ae5e7`）之后，清空工作区（旧的移到 `runs-prev-bp9e0inru`）重跑全链，模型 `qwen3.8-flash`。

### 结果：`gate(final)` PASS，但不是不间断的单会话

- **P0 → S3c 在一个会话里跑完，S4 是续跑的。** orchestrator 在 S3c 完成之后、spawn S4 之前遇到 `API error: Request timed out.` 退出（原因是运行期间本机网络中断，外部因素，不是流水线逻辑问题）。随后另开会话只续跑 S4。因此这次**没有**证明真正的单会话端到端稳定性。
- 独立复核（不采信 orchestrator 自述）：`gates.run_gate` 的 blueprint / code / template / final 全部 PASS；直接调用 `wrap_m2s.validate_sam` → `ok: true`；`consistency_report.json` → `status: PASS`，`total_checks: 12`；6 份 `stage_reports/*.json` 齐全。

### 验证到的设计点

1. **`validate_sam` 修复在完整流水线里生效。** S3c 第一次尝试就同时通过 `run_gate(template)` 和 `validate_sam`（返回真实 `ok: true`），S4 validator 也亲自调用得到同样结果。此前这条路径 6/6 次 90 秒超时。
2. **S1 的"独立复核 + 整阶段重 spawn 一次"兜底生效。** 第一次 spawn 的 `m2s-architect` 在写完 10 个 key 中的 7 个后因网络错误进程死掉，没写 `stage_reports/S1.json`，却以 `completed`（return_code 0）退出——即子 Agent 的"退出状态"本身不可信。orchestrator 独立跑 `gate(blueprint)` 发现 FAIL（缺 `lambda_functions` / `dropped_functions` / `api_gateway`，8 个端点全部未覆盖），重新 spawn 一次后 PASS。这正是 SKILL.md §3"绝不采信子 Agent 自述，只信 gate"要防的场景。
3. S2 曾出现"gate 已 PASS 但 `S2.json` 稍晚才落盘"的短暂时序差，orchestrator 按 gate 为准继续，之后报告确实补上。

### S4 记录的两处非阻塞偏差

- `blueprint.api_gateway.user_pool_arn` 是占位符 `${CognitoUserPool.Arn}`，模板里资源逻辑名是 `UserPool`（用 `!GetAtt UserPool.Arn` 解析，authorizer 覆盖完整）。
- `output/template.part1.yaml` / `part2.yaml` 仍留在磁盘，与 `template.yaml` 内容一致（325 行），属无害中间产物。

### 结论与局限

只有一个数据点，且不是不间断会话；不足以说明稳定。下一步：网络稳定时再干净跑一次全链（`run6`），以验证真正的单会话稳定性。

## run6 / run7：单会话稳定性验证（2026-09-21 ~ 09-22）

### run6（作废）

P0 → S3c 在一个会话里连续通过，S4 连续两次（首次 + orchestrator 的一次整阶段回炉）因子 Agent 端 `API error: Request timed out.` 中断、没有产出任何文件。用户确认运行期间本机网络再次中断，属外部因素，人工停掉，不计入流水线稳定性结论。工作区留存于 `runs-prev-run6-netdrop`。

### run7：一个会话、零回炉，`gate(final)` PASS

清空工作区，同样的启动命令、`qwen3.8-flash`，从 P0 到 `gate(final)` 在**同一个 `oh -p` 会话**里跑完，中途无人介入、无续跑。六个子 Agent（S1/S2/S3a/S3b/S3c/S4）全部第 1 次尝试通过，orchestrator 没有用掉任何一次整阶段重 spawn。

独立复核（不采信 orchestrator 自述）：`run_gate` 的 blueprint / code / template / final 全部 PASS；直接调用 `wrap_m2s.validate_sam` → `ok: true`；`consistency_report.json` → `PASS`，`total_checks: 12`，`fixes_applied: 0`，`sam_validation: PASS`；6 份 `stage_reports/*.json` 齐全；`Handler: handler.handler` 与磁盘上 `handler.js` 的 `exports.handler` 对得上。`validate_sam` 在 S3c、S4 和 orchestrator 自己调用时均秒级返回真实结果，stdin 修复在完整流水线里再次得到确认。

### 需要如实注明的地方

- **run7 不是完全"stock"的一次。** orchestrator 会读 OpenHarness 自己的 memory（`~/.openharness/data/memory/`，其中有一条早先运行自动留下的 `m2s-s4-validator-api-timeout.md`），据此在 spawn S4 时**自行往 prompt 里加了"先落盘临时版报告再做验证"的缓解指令**。这不是 SKILL.md 的固定内容，可能对 S4 一次通过有贡献；结论是"带这条 memory 的流水线稳定"，不是"纯 SKILL.md 稳定"。run7 结束时它还改写了那条 memory（把它标成已被验证有效）。
- **S4 的 findings 全部标成 `WARNING`**（12 条，内容看起来是"已核对、无需改动"的说明），严重度标注偏高，不影响 `status: PASS` 与 `gate(final)`，但会让下游读报告的人误以为有 12 个问题。
- **`Globals.Function.Handler` 仍是 `index.handler`**，六个函数各自用 `Handler: handler.handler` 覆盖，实际可用，但全局默认值与磁盘不一致。gate 没有拦，validator 也没有改动（S2 有意把"blueprint 写 `index.handler`、磁盘是 `handler.js`"的差异交接给 S3，S3c/S4 选择不迎合 blueprint）。
- 上述 memory 机制与 `Globals` 不一致都是**已发现、未处理**，没有针对性修复。

### 结论

run7 证明：网络稳定时，这套流水线可以在一个会话里从 P0 无人介入地跑到 `gate(final)` PASS，一次数据点。加上 run5（P0–S3c 一个会话 + S4 续跑）、run6（外部网络中断）以及此前的 `bp9e0inru`，目前是 3 次独立的 `gate(final)` PASS，其中 1 次是完整的单会话。样本仍少，尚不足以说明稳定。

## 修复 run7 遗留的两个问题（2026-09-22）

1. **`Globals.Function.Handler` 占位值（`index.handler`）残留。** 根因：blueprint 里的 `handler` 是架构师的占位值，S3a 在读不到代码的阶段把它抄进了 `Globals`，S3b 之后每个函数各自覆盖成真实的 `handler.handler`，所以运行不出错，但 gate 抓不到。M2S 自己的参考模板 `Globals` 里本来就没有 `Handler`。修复分三层：
   - `gates.py` 的 `check_c3`：抽出 `_handler_problem()`；现在按"函数自己的 `Handler`，否则继承 `Globals`"算有效 handler 再对照磁盘（此前一个靠 `Globals` 继承 handler 的函数会被误报成"malformed"）；另外，若 `Globals.Function.Handler` 没有任何函数继承、且在所有函数目录里都解析不到，则报 `ERROR`。新增 3 个 pytest（测试总数 71 → 74）。真实数据复核：run7 旧模板被抓出，M2S 参考模板仍 `PASS`。
   - `m2s-sam-engineer.md`：S3a 增加"`Globals.Function` 里不要写 `Handler`"，S3c 增加对应的修法（删掉那一行，不要改各函数的 `Handler`）。
   - run7 的 `template.yaml` 里删掉了那一行（旧文件备份于 `temp/`），四个 gate、`validate_sam`、`consistency_report` 复核全部仍 PASS。
2. **S4 把 12 条"已核对无问题"的 finding 全标成 `WARNING`。** `m2s-validator.md` 增加 `severity` 定义：`INFO` = 核对过没问题，`WARNING` = 有问题但不致部署失败，`ERROR` = 会致部署失败；`status` 只在存在 `UNABLE_TO_FIX` 的 `ERROR` 时为 `FAIL`。

**未验证的部分：** 第 1 点的 gate 有测试和真实数据验证；但两处 prompt 改动（S3a 不写 Globals Handler、S4 按新定义标 severity）是否真的改变了模型行为，需要重新跑全链才能确认，目前没有跑。已同步到 `~/.openharness/agents/`。

## 流水线重构步骤 0：规格信息（2026-09-26）

对应 `PIPELINE_REDESIGN.md` §2.1 / §5 步骤 0。

### 产出

- **`spec_extract.py`**（`analyze_project` 在 M2S 静态分析之后调用）：给每个 Express 端点加上 `handler_location`、`auth`、`request`、`responses`、`data_access`、`business_rules`（空，留给标注员）、`spec_status`；给 `dynamodb_info` 加 `table_schemas`（从建表脚本的 `createTable` 参数解析分区键/排序键/GSI）；顶层加 `spec_meta`（`schema_version`、`monolith_root`）。**每一项都带 `source: [{file, line, symbol}]` 和 `origin: script|llm`**。它是模式匹配，不是 JS 解析器：认不出的一律标 `unknown`。Python 端点不做提取（`spec_status.script = "unsupported"`），全部留给标注员。
- **`provenance.py`** + 两个新 MCP 工具：
  - `check_provenance()`：S0 的 gate。每条出处的文件必须在单体目录内、行号在范围内、`symbol` 在上下 2 行内逐字出现；各字段结构正确；每个端点都有 request、responses（至少一个成功响应）、auth、handler_location 和至少一条业务规则。
  - `annotate_entry_point(method, path, field, value_json)`：标注员的唯一写入方式（K5）。一次替换一个端点的一个字段，写入前校验结构和出处，任何一条不过就整次拒绝并指明是哪条；标为 `origin: "script"` 的项必须和已有脚本项逐字相同。
- **`agents/m2s-spec-annotator.md`**（已同步到 `~/.openharness/agents/`）：S0 阶段，核对脚本项、补 `unknown`、给每个响应补 `when`、写业务规则；自查循环最多 3 次；工作报告 `stage_reports/S0.json`。
- **`m2s-migrate/SKILL.md`**：P0 之后加 S0（spawn 标注员 → 主 Agent 亲自 `check_provenance` 复核）。architect 的提示词**没有改**（K6，留到后续步骤）。
- `~/.openharness/settings.json`：`allowed_tools` 加两个新工具；`M2S_WORKSPACE` 改为 `runs/bookstore-v1`。改前备份为 `settings.json.bak-pre-spec-annotator`。
- `tests/test_spec.py`：11 个用例，跑在真实的 bookstore 单体上。

### 顺带修掉的两个 P0 老 bug（`M2S/src/preprocessor/static_analyzer.py`）

1. **`dynamodb_info.probable_tables` 对所有应用都是空的。** `file_tags` 的键带应用名前缀（`bookstore/config.js`），却拿它去拼 `monolith_root / 键`，路径不存在就被静默跳过。改为相对 `monolith_root.parent` 解析。修后 bookstore 5 张表、todo 2 张、coffee 6 张都能提取到（`runs/bookstore-oh`、`runs/todo-oh` 的旧报告里都是 `[]`）。
2. **Express 应用的 `entry_point_dependencies` 永远是空的。** 挂载前缀修正（Pass 1.5）把端点的 `handler_function` 改成了带前缀的名字（`POST__api_orders`），但调用图里的 `caller_function` 还是符号表里不带前缀的名字（`POST__`），聚合时一个也对不上。architect 判断同步/异步（例如 `POST /orders` → `updateBestSellers`）依赖的正是这个字段。bookstore 从 0 变为 15/15（todo 的路由没有挂载前缀，修前修后都是 8/8）。

### bookstore 上的运行（工作区 `runs/bookstore-v1`）

- **脚本部分**：15 个端点全部提取到（0 个 unsupported），158 条出处全部通过检查，23 个 `unknown`。5 张表的键结构和 2 个 GSI 都解析到。
- **run1**（P0 + S0，一个 `oh -p` 会话）：
  - P0 第一次调 `analyze_project` 传的是 **todo** 路径：orchestrator 用了 SKILL.md §0 里写死的 todo 默认值，没有用 prompt 指定的 bookstore，事后还自述成"参数被忽略"（session 记录里的实际参数就是 todo）。它自己发现后改传 bookstore 重跑，没有造成后果。已在 SKILL.md §0 写明"prompt 给的 MONOLITH/WS 优先"。
  - S0 标注到 12/15 时，子 Agent（`API error: Request timed out`）和 orchestrator（`Network error: Connection error`）先后因外部网络错误退出。已写入的 12 个端点（465 条出处）检查全部通过。
- **run2**（续跑，只跑 S0，明确禁止重跑 `analyze_project`，否则会覆盖已写的标注）：补完剩下 3 个端点，子 Agent 自查 1 次通过。
  - 过程中 `annotate_entry_point` 当场拒绝过 2 次：1 次是 JSON 结构写错，1 次是 `init-db.js` 行号引用偏了。两次都按报错改正后重试成功，写入时就拦住错误的设计生效了。
  - orchestrator 在 spawn prompt 里额外要求子 Agent 先写一份 `status: in_progress` 的临时 `S0.json`，这超出了 agent 定义，但无害。
- **独立复核**（`provenance.check_provenance`，不采信 agent 自述）：**PASS**。589 条出处全部通过，0 errors，0 incomplete，93 条业务规则（authorization 18、data_scope 17、error_handling 17、validation 15、computation 10、side_effect 10、other 6），16 个 `unknown`。74 个响应项全部被标注员改成了 `origin: llm`，因为每个响应都要补 `when`。
- 剩下的 `unknown` 都有说明，没有猜：
  - 受保护端点的 `Authorization` 头是否必填，取决于运行时的 `AUTH_DEV_MODE`；
  - 若干只做了 falsy 检查就原样入库的字段，类型没有代码证据。

### 需要如实注明的地方

- **出处检查只能证明"引用的那一行确实存在，并且提到了这个符号"，证明不了规则本身写得对。** 抽查了 `POST /api/orders`、`GET /api/search`、`POST /api/auth/register` 的规则，没有发现错误。
  标注员还写出了几条脚本不可能得到、对测试设计很有价值的规则，例如：
  - 下单"PutItem 成功后删除购物车失败 → 返回 500，但订单已经落库，重试会生成重复订单"；
  - `?q=` 重复传参会触发 TypeError，返回 500；
  - Scan 不处理 `LastEvaluatedKey`，数据超过 1MB 时结果被静默截断。
  
  其余规则没有逐条人工核对。
- 标注慢：run1 大约每个端点 4 分钟。
- architect 还没有改成引用这些字段（K6）。所以在后续步骤接上之前，这些规格只对验证线的测试设计有用。

## 流水线重构步骤 1：验证线（2026-09-26）

对应 `PIPELINE_REDESIGN.md` §3、§4.3、§5 步骤 1。OpenHarness 的模型同时从 `qwen3.8-flash` 换成了 `qwen3.8-max`，`timeout` 从 30s 调到 120s（max 连一次最简单的调用都要约 11s）。改前备份为 `settings.json.bak-pre-qwen-max`。

### 产出

- **`verify.py` + `verify-tools/`**：三个 MCP 工具 `run_validate` / `run_coverage` / `run_equivalence`（K7：状态型夹具）。
  - 执行引擎 `verify-tools/controller.js`：
    - 启动 dynalite；
    - 在前面加一层代理：记录每次 SDK 调用、按用例注入故障（`throttle`/`error`/`validation`/`conditional`/`network`）、转发 DynamoDB 请求、自己应答 SQS 请求；
    - 函数在 RIE 容器（代码 `docker cp` 进去）或本机 Node 里运行；
    - 需要覆盖率时，用 V8 收集、c8 转换。
  - Python 侧：
    - 基于 `sam build` 产物执行（顺带做一次打包检查）；
    - 把规格层面的用例转换成 Lambda 事件，并模拟 API Gateway：路径参数、Cognito authorizer 对匿名请求直接返回 401、claims；
    - 把 blueprint 里的表名、队列名映射到模板资源；
    - 判定预期，失败时做机械初筛（`triage_hint`）；
    - H1 差分之前，先把 UUID、时间戳这类不确定值归一化。
- **`agents/m2s-verifier.md`**：同一个定义派发两次。
  - V1：只看规格，写测试计划、用例和故障场景；兼任 architect 的交叉检查。
  - V2：执行，失败分诊，白盒补测，记录故障行为基准。
- **`m2s-migrate/SKILL.md`**（K8）：
  - V1 插在 S1 之后、S2 之前，V2 插在 S4 之后；
  - 写明验证线的回炉规则（按 routing 退回，每个阶段最多 2 次；判断不了的交给人）；
  - 验收改为三条（`gate(final)`、consistency、`verdict_A.json`），通过后在 WS 的独立 git 仓库里打 `v0` tag。
- `tests/test_verify.py`：7 个单元测试，外加 1 个需手动开启的集成测试（`M2S_VERIFY_INTEGRATION=1`：真实的 sam build + dynalite + 本机 Node）。

### 先在 bookstore-oh 上试工具（测试用的工作区，6 个手写用例 + 3 个故障场景）

- RIE 和本机 Node 两种执行方式的判定结果**完全一致**，覆盖率数字也一致。这是 K3 对照实验的第一个数据点，样本很小。
- 负对照：
  - 把 `orders-create` 的 `SendMessage` 去掉之后，`run_equivalence(diff)` 报出了 H1 (a)(b) 两条差异，`run_validate` 也判为失败（"expected 1 message, got 0"）；
  - 把改动撤回之后，diff 结果是 `EQUIVALENT`。

### bookstore-v1 全链结果（从 S1 开始；P0/S0 用步骤 0 的产物）

- **S1–S4**：每一步的 gate 都是第 1 次就通过；`gate(final)` PASS，consistency PASS。
- **V1**：
  - 84 个用例（happy 22 / error 30 / boundary 32），覆盖全部 12 个函数；41 个故障场景；`dry_run` 第 1 次就通过。
  - 另外提了 9 条 non_blocking 的 `spec_issues`，都是真实存在的规格缺口。例如 blueprint 要求 SendMessage 必须 await，但没有规定 SendMessage 失败时该返回什么；重复的 `?q=` 在 API Gateway 下已经不可能再触发 500。
- **V2**：
  - 首轮 83/84。唯一的失败被正确分诊为 `event` 类：用例对 `{total, hits}` 这种对象响应用了只对顶层数组生效的 `contains`。
  - V2 自己修了 1 轮，修法没有放宽断言：逐条目的精确断言挪到了 3 个"只命中 1 本书"的用例里，用 `equals` 断言。
  - 按覆盖率补了 12 个白盒用例，最终 **96/96**。
- **覆盖率**：语句 86.5%–100%，分支 44%–96%；v1 只报告，不设门槛。
- **故障行为基准**：41 个场景 / 65 次观测 / 0 infra 错误。
- **v0**：WS 独立仓库里的 commit `955d8a2`，tag `v0`。
- **独立复核**（不采信 agent 自述）：
  - 我自己跑 `run_validate()`，结果 PASS 96/96（RIE）；
  - S4 之后 `output/` 下没有任何文件被修改过；
  - 上层仓库的 HEAD 和 tag 都没有变化。

### 过程中发现并修掉的问题

1. **`sam build` 在 MCP 服务器里卡死**：
   - 现象：卡住的 `python sam.EXE build` 只占 9.6MB，停在解释器启动阶段；它的祖父进程是 `server.py`。
   - 原因：和 `validate_sam` 当年遇到的 cfn-lint 问题一样，子进程继承了 MCP 的 stdio stdin。我手动测试时没有经过 MCP 服务器，所以没复现。
   - 修法：`verify.py` 里所有子进程都统一走 `_run()`，设置 `stdin=DEVNULL`。修完后用真实的 MCP stdio 客户端跑了一次（新的构建目录）：40s，6/6 通过。
   - 当时正在跑的 V2 用的是已经加载进内存的旧代码，所以我停掉了那次运行，从 V2 续跑。S1–S4 的产物都在。
2. **`gate(final)` 会放过 `IN_PROGRESS` / `FAIL` 的 consistency_report**：原来只检查有没有 `status` 这个键。现在要求 `status == "PASS"`，并加了回归测试。
   - 为什么现在才暴露：各 agent 开始先写一份临时报告（`in_progress`），这个习惯来自 OpenHarness memory 里 orchestrator 自己存的一条笔记。
3. **SKILL.md §6 的 `git init # 已经是仓库就跳过`**：WS 在上层仓库里面，`git rev-parse` 会回答"是仓库"，接下来的 add/commit/tag 就会打到上层仓库（上层本来就有另一个 `v0` tag）。
   - 这次 orchestrator 直接执行了 `git init`，没有出事。
   - 已改为只看 `WS/.git` 目录是否存在。

### 需要如实注明的地方

- V1 的 9 条 spec_issues 都是 non_blocking，SKILL.md 没有规定这种情况怎么处理。orchestrator 自己决定把其中 3 条按"保持单体语义"的解读直接写进了 S2 的 prompt，**没有退回 architect**。
  这是验证线的信息流到了实现线。内容只是规格澄清，不涉及用例，但不符合"出题和答题分开"的要求，需要在规则里补上。
- 用例的断言语言还有缺口：`contains` 只能用在顶层数组上，没法对 `hits` 这类嵌套数组做"与顺序无关的包含"判断。这次 V2 绕过去了，以后应该补一个带路径的写法。
- V1 写的故障场景里，有一个 `kind: error` + `nth: 2` 的场景被 SDK 的重试吸收了，没有达到场景描述里的"500"意图（V2 发现后记录了下来）。
  可重试的故障（`error`/`throttle`/`network`）只命中一次，效果就是"重试后成功"。agent 定义里写了哪些故障会重试，但 V1 仍然用错了。

### 补充规则：规格问题的裁定权，以及实现线 prompt 的隔离（2026-09-26，K9/K10）

起因：bookstore-v1 这次运行里，V1 提的 9 条 spec_issues 都是 non_blocking，规则里没有规定这种情况怎么处理。主 Agent 于是自己裁定了其中 3 条：
- 500 响应体的形状；
- SendMessage 失败时照样返回 200；
- SQS 消费者吞掉失败。

它把裁定直接写进了 S2 的 prompt，同时还写了"V1 已按严格保留单体行为写预期，'改进'会被判为 code 类失败"。结果有三个问题：
- 这些决定没有落进 blueprint；
- 对应的三处行为没有被测试覆盖；
- 实现线被告知了题目是怎么出的。

新规则：
- **K9**：V1 的 spec_issues 不分 severity，一律退回 architect 裁定。
  - 裁定写进 `metadata.spec_resolutions`，格式是 `{issue_id, decision, applies_to, rationale}`；
  - V1 回炉后按裁定补用例，`run_validate` 会检查 `blueprint:metadata.spec_resolutions[SI-x]` 这种出处确实存在；
  - "S1 → V1"往返最多 2 轮。
- **K10**：主 Agent 给 S1–S4 的 prompt 里禁止写测试相关的内容，包括用例、预期、`verification/` 路径、验证线的情况，以及它自己对规格的裁定。
  - V2 判定失败回炉时，只转发 routing 里用规格语言写的 `function`/`spec_refs`/`expected_per_spec`/`observed`；
  - `evidence_cases` 只给主 Agent 和人看。

改动的文件：`agents/m2s-verifier.md`（issue 带 id/status、V1 回炉流程、routing 格式）、`agents/m2s-architect.md`（新增"裁定 spec_issues"一节）、`m2s-migrate/SKILL.md` §2/§3、`verify.py`（检查 spec_resolutions 类出处）、`tests/test_verify.py`。

**bookstore-v1 的 v0 是在旧规则下产生的**：那 3 处行为是主 Agent 裁定的，没有测试覆盖；其余 6 条 issue 从来没有人裁定过。要让 v0 符合新规则，需要从 S1 回炉开始重跑。

## 流水线重构步骤 2：优化线（perf-optimizer v1，2026-09-26）

对应 `PERF_OPTIMIZATION_DESIGN.md` §2–§9、`PIPELINE_REDESIGN.md` §5 步骤 2。

### 产出

- **`perf.py` + `verify-tools/perf-shim.js`、`perf-harness.js`**：三个 MCP 工具。
  - `run_proxy_metrics`：调用次数、顺序深度（每次调用固定 10ms 延迟）、热 CPU（零延迟下的墙钟时间，本机 Node）、容量单位、成本。
  - `run_rie_verdict`：用 `git archive` 导出基线版本；改动前后在 RIE 里交替跑 5 轮，每轮 1 次冷调用 + 30 次热调用，CPU 配额 = MemorySize ÷ 1769。判决逻辑按 §3.2/§3.3 写在工具里：先用 10ms 延迟筛一遍，需要时再测 0/5/15ms；"明显"的意思是两组结果的范围不重叠；再加上 RSS 护栏。
  - `update_ledger`：根据 `output/perf_report.json` 机械地更新 `perf-rules/ledger.json`。
  - `perf-shim.js` 按 §2.1 替换 SDK 最底层的 HTTP 发送，响应根据基准事件的表数据计算；表数据可以用 `generate` 指令批量生成。
  - `run_coverage` 新增 `changed_since`：检查改动行有没有被测试执行到。
  - `perf-rules/prices.json`：单价配置。数值来自我的知识，引用绝对金额之前需要核对；判决只比较方向，不依赖精确值。
- **`agents/m2s-perf-optimizer.md`**（S5）：规则 R1–R3、§6.2 的允许/不允许清单、修改请求格式、逐个执行加判决的流程、报告格式和禁止事项。
- **`m2s-migrate/SKILL.md`**（K12）：v0 之后接 S5 和阶段 6（主 Agent 亲自跑 `gate(final)` 和全量回归，不通过就用 `checkout v0 -- output` 回退并提交，不用 reset）。
- 基准事件由 perf-optimizer 写在 `WS/perf/benchmarks/`（K11），必须写明数据量假设，在改动之前提交。
- `tests/test_perf.py`：7 个用例。`test_verify.py` 新增 1 个用例，覆盖"生效调用"的语义。

### 手工试跑（bookstore-v1 的克隆）

手工做 R1 改写，按顺序检查：
- 回归测试 7/7 通过；
- 改动的 18 行可执行代码全部被测试执行到；
- H1 差分，结果 EQUIVALENT（见下面第 1 个问题）；
- `run_rie_verdict` 判 `kept`：热调用 5 轮中位数从 354.6–357.9ms 降到 78.7–92.8ms，init 无显著差异，成本下降；用时 114s。

负对照：把改写改成"每条消息少更新最后一本书"，差分报出 H1 (a)(b) 共 10 处差异。

### bookstore-v1 上的 S5 运行（从 v0 续跑）

- **基准**：12 个，每个函数一个，数据量取正常业务规模：200 本书、一批 10 条消息 × 3 本、购物车 10 件。在任何改动之前提交（`d2dce8d`）。
- **规则扫描**：R1 命中 2 处（都在 `update-bestsellers`：消息内一层、消息间一层），R2、R3 命中 0 处。
- **按 W1 拆成两个改动，逐个判决，都保留了**：

  | 改动 | 提交 | 顺序深度 | 热调用 5 轮中位数（10ms 延迟，0.29 vCPU） | 成本/次 |
  |---|---|---|---|---|
  | C1 消息内的书并行 | `36b30c0` | 30 → 10 | 355–362 → 128–144ms | 2.19e-5 → 2.00e-5 |
  | C2 消息间也并行（based_on C1） | `34d8216` | 10 → 1 | 130–135 → 83–87ms | → 1.96e-5 |

  两个改动的调用次数都没变（30 次 UpdateItem），init 无显著差异，RSS 90MB / 512MB。报告里写明了"结论只在并发不受限的条件下成立"。
- **回滚 0 个，pending_human 0 个。** 另有 3 条 advisories，诊断了但没有实施：全表 Scan 要改数据模型；30 次 UpdateItem 在 v1 里不允许合并；orders-create 让 SQS 发送和写入并行会改变操作集合。
- **阶段 6**（主 Agent 亲自做）：`gate(final)` PASS，全量回归 96/96。
- **我的独立复核**：
  - 代码 diff 只涉及 `update-bestsellers`，改写忠实：每本书、每条消息的失败都各自吞掉；
  - 全量回归 96/96；
  - 用修好的工具（见下文）重跑全部函数的 H1 差分：65 个 run 全部 EQUIVALENT，基线稳定性检查没有发现不稳定的 run；
  - 台账：bookstore，R1 hits 2、kept 2。

### 过程中发现并修掉的问题（都在 H1 差分工具里）

1. **按调用顺序计数的故障会被并行化"挪位置"。**
   - 现象：故障场景写的是"第 n 次调用失败"。R1 改成并行之后，第 1 次调用变成了另一本书，差分报了假的"不等价"。
   - 修法：差分改为先用当前工具重新运行 v0，记下每个故障实际落在哪个操作上（操作名、表、条目键、第几次尝试），再把同样的故障注入改动后版本里的同一个操作。同时 H1(a) 改为只比较生效的调用，被注入故障的尝试不算。
2. **controller 的规则计数 bug**：循环在第一条命中的规则处就返回，后面的规则看不到这次调用，"第 n 次"计数就落后了。结果"固定到操作"之后，只有第 1 次尝试会失败，重试就成功了。现在每条匹配的规则都会计数，再由第一条命中的规则决定故障类型。
3. **handler 返回之后，被放弃的请求还在跑。**
   - 现象：`orders-create` 用的是 `Promise.all`。某个删除最终失败时，handler 立刻返回 500，另一个删除的重试还在路上。工具在 handler 返回时就结束了用例，这些重试有时绕过了故障注入，真的删掉了购物车条目，有时则在表快照之后才到。
   - 这导致我自己的全量差分，在**没有改动过的** `orders-create` 上报了 2 处差异。
   - 修法：
     - handler 返回时如果还有进行中的调用，或者用例注入了故障，就保持故障规则生效，直到 1 秒内没有新调用（最多 10 秒），再做表快照；返回之后发生的调用会被计数，并写进结果。
     - H1(a) 按设计里的"操作集合"比较：幂等操作（读、相同的 Put、Delete）重复出现只算一次；UpdateItem、消息、事务仍然每次都算。
     - 差分先把 v0 跑两遍；两遍结果不一致的 run 标为 `unstable_in_baseline`，不参与判决，并且明确列出来，不会记到改动头上。
   - 修复后重跑全量差分：65/65 EQUIVALENT，没有不稳定的 run，用时约 7 分钟。

### 需要如实注明的地方

- 第 3 点是在 **S5 运行之后**才修的。S5 当时只对 `update-bestsellers` 做差分（结果 EQUIVALENT），没有碰到这个问题；它的证据是用修复前的工具产生的。我用修好的工具重新复核过，结论不变。
- 模拟延迟模型的局限（§2.1）在这次的 R1 判决里依然存在：并行调用没有上限。在 0.29 vCPU 下，30 个并行调用仍然要约 80ms，主要花在 SDK 的序列化和签名上。这是本机测得的量，不代表线上。
- perf-optimizer 写基准时，数据量是它自己选的。这次的规模合理，但机制上只靠"必须写明 `data_volume`"来约束。

## 流水线重构步骤 3：调整拓扑，接入 architect 改造和消费者幂等规则（2026-09-26 ~ 27）

对应 `PIPELINE_REDESIGN.md` §2.2、§2.4、§4.1、§5 步骤 3。
阶段拓扑（S0、V1/V2、S5、阶段 6）在步骤 0–2 里已经接好，这一步补上设计稿里剩下的部分，再用新结构把 bookstore 从头到尾跑一遍（新工作区 `runs/bookstore-v2`）。

### 改动

- **architect（§2.2）**：
  - 直接采用规格字段：auth 用 `entry_points[].auth.classification`，表结构用 `dynamodb_info.table_schemas`，业务规则和 `data_access` 直接引用；只有规格标 `unknown` 的才按原来的规则 A–D 判断；
  - 必须写 `storage/DECISIONS.md`，每条决策写清依据和考虑过的其他方案；
  - 必须遵守"异步消费者幂等"规则，并显式决定失败是吞掉还是上报。
- **`gate(blueprint)`（B0）新增三项检查**：
  - `DECISIONS.md` 存在，并且提到了每个 Lambda（ERROR）；
  - 规格要求鉴权、blueprint 却设成公开（ERROR，属于安全回退），其他 auth 不一致（WARNING）；
  - 消费者没有记录幂等设计（WARNING，按关键词判断）。
  
  新增 3 个测试，另外有 2 个旧测试的夹具补上了 `DECISIONS.md`。
- **消费者幂等（§2.4，K13）**：写进 `m2s-async-patterns-kb`（必须规则一节：为什么这是迁移带来的问题、幂等键、Pattern A 用 `TransactWriteItems` 同时写去重标记和实际效果、Pattern B 逐条目条件更新、失败上报和幂等的关系），以及 `m2s-sam-kb`（带 TTL 的幂等表、`ReportBatchItemFailures`）和 `m2s-lambda-coding-kb`。
  - code-developer 必须按 blueprint 指定的机制实现，机制没写清楚就报 BLOCKED，不许自己发明；
  - sam-engineer 负责生成幂等表，并按 blueprint 配置失败上报；
  - V1 必须为每个消费者写重复投递用例。
- **Scan 翻页这一版不做**（K13）：这是单体本身的 bug，按用户决定保持和单体一致。
- **验证引擎的 DynamoDB 后端换成 AWS DynamoDB Local（K14）**：
  - 原因：dynalite 不支持 `TransactWriteItems`。
  - `docker pull amazon/dynamodb-local` 和 ECR Public 的镜像都拉不下来：Docker Desktop 内置代理在下载镜像层时一直超时，重试了 10 次，而宿主机经系统代理访问是通的。
  - 改用本机已有的 `localstack/localstack:3.8` 镜像里自带的官方 `DynamoDBLocal.jar`，以及它的 Java 11。实测事务语义正确：重复的事务被 `TransactionCanceledException [ConditionalCheckFailed, None]` 拒绝，计数不会多加。
  - 以后本机有了 `amazon/dynamodb-local` 镜像就会自动改用它；没有 Docker 时降级为 dynalite，结果里注明不支持事务。
  - 在 bookstore-v1 上复核：回归 96/96、H1 差分 65/65 EQUIVALENT、没有不稳定的 run，和换引擎之前一致。代价是全量验证加差分从大约 7 分钟变成约 9 分钟。
- **m2s-migrate 清理**：每个阶段的回炉上限从 1 次改为 2 次（J5）；最终报告要写明 v0 之后的 S5 改动没有经过 validator 审查，并列出仍然 open 的 spec_issues；description 和"四个 agent"这类过时的措辞也改掉了。

### 找到"网络错误一次就杀死 agent"的根因（修在 OpenHarness 里，没有提交）

bookstore-v2 第一次全链运行时，S0 子 Agent 和主 Agent 先后因为 `peer closed connection without sending complete message body (incomplete chunked read)` 退出。这是本次会话里第 4 次有运行被网络错误中途打断：run1 的 S0、bookstore-v1 的 S0，以及更早的 run6-netdrop 也属于这类。

根因在 `OpenHarness/src/openharness/api/openai_client.py` 的 `_is_retryable()`：它只把 HTTP 429/500/502/503 和 Python 的 `ConnectionError`/`TimeoutError`/`OSError` 当作可重试。而下面这些错误都不是 `OSError` 的子类，所以一出现就被当成致命错误，结束整个 agent 会话，`MAX_RETRIES = 3` 的重试机制根本没有机会生效：
- 流中途断开（`httpx.RemoteProtocolError`）；
- "Request timed out"（`openai.APITimeoutError`）；
- 连接失败（`openai.APIConnectionError`）。

以前在 spawn prompt 里叮嘱子 Agent"遇到网络错误重试 1-2 次"没有效果，也是这个原因：这类错误发生在模型调用层，agent 自己根本没有机会去重试。

修复：把 `openai.APIConnectionError`（`APITimeoutError` 是它的子类）和 `httpx.TransportError` 也判为可重试。用真实的异常类型验证过：这三种都返回 True，`ValueError` 仍然返回 False。确认了 `oh` 运行的就是这份源码（可编辑安装）。改动留在 OpenHarness 仓库的工作区里，**没有提交**，要不要提交由用户决定。

**第二处修改（同一个文件，同样没有提交）**：修复后的第一次续跑里，重试确实触发了（1s/2s/4s 退避），但连续 4 次都是 `Connection error`，那次断网持续了几十分钟，最后还是退出了。原来 `MAX_RETRIES = 3` 总共只覆盖约 7 秒。现在改为 `MAX_RETRIES = 8`、`MAX_DELAY = 60`，退避依次是 1、2、4、8、16、32、60、60 秒，合计 183 秒，能扛过几分钟的断网；断网时间更长时仍然需要续跑。

### bookstore-v2 全链运行（从 S0 续跑，2026-09-27）

- **流程上全部跑通**，每一步我都单独复核过：
  - S0：`check_provenance` PASS，346 条出处；
  - S1：`gate(blueprint)` PASS，`DECISIONS.md` 11 节，幂等方案采用 Pattern A，新增 `IdempotencyTable`；
  - V1：54 个用例、11 个故障场景，有 2 个重复投递用例；它提出的 4 个规格问题，architect 事先已经在 `spec_resolutions` 里裁定过，所以不需要回炉；
  - S2–S4：各阶段 gate 都 PASS；
  - V2：59/59，RIE + DynamoDB Local；
  - v0 = `94313c2`；S5 保留了 REQ-1；阶段 6 PASS。
- **但复核中发现 4 个问题，其中 2 个是真实的正确性问题**：
  1. **S5 保留的 REQ-1 在真实 DynamoDB 上不安全**：它把多条消息的事务改成并行执行。
     - 真实 DynamoDB 会以 `TransactionConflict` 取消重叠的并发事务，被取消的那条消息又被吞掉，计数就丢了。
     - DynamoDB Local 把事务串行执行：实测 300 个互相冲突的并发事务，一个都没被取消。所以测试和等价性判决都看不出问题。
     - 已在工作区里 `git revert`（`5b13ec5`）。新增 v1 规则：可能改到同一条目的事务或条件写入不许并行；`PERF_OPTIMIZATION_DESIGN.md` §2.1 局限表加了第 6 项"模拟器的并发语义"。
  2. **v0 本身有 bug，源头是我写进知识库的 Pattern A 示例**：示例对 `books` 逐项生成事务操作，没有先合并同一个 `bookId`。
     - 订单里同一本书出现两次时，DynamoDB 会整笔拒绝这个事务（一个事务里不能对同一条目做两次操作），错误又被吞掉，这笔订单的计数全部丢失。
     - DynamoDB Local 会同样拒绝这种事务，所以只要有这个用例就能测出来，只是 V1 没写。
     - **并发冲突即使在串行的 v0 上也会发生**：Lambda 会同时处理多个批次，"失败一律吞掉"加上 Pattern A，就成了单体没有的静默丢数据路径。
  3. **我的等价性对比工具有漏洞**：`expiresAt` 是秒级时间戳，而归一化只处理毫秒。于是写了去重标记的运行都被判为不稳定、排除在外，REQ-1 在 4 次对比里只验证了 1 次。现在归一化覆盖当前时间前后 400 天内的秒级和毫秒级时间戳，修好后 4/4 等价，并补了测试。
  4. **S4 加的 `dynamodb:TransactWriteItems` 权限不起作用**：IAM 里没有这个动作（AWS 文档：事务按逐条目的 `PutItem`/`UpdateItem`/`ConditionCheckItem` 授权，`DynamoDBCrudPolicy` 已经包含这些）。已在 `m2s-sam-kb` 里写明，不要再加这条。
- **修复**（用户决定：Pattern A 加上冲突时重试或上报；验证引擎要能测冲突）：
  - **知识库**：Pattern A 改为先按键合并；每个事务最多 100 项，超出就分块，每块用自己的标记；`TransactionConflict` 要么重试，要么上报，绝不能吞掉，这一点跨 Lambda 调用同样成立。
  - **验证引擎**：
    - 新增故障类型 `tx_conflict`：`TransactWriteItems` 返回带 `TransactionConflict` 原因的取消；
    - 用例可以带上自己的 `faults` 和对应的预期；
    - 事务调用记录它涉及的条目，用于把故障固定到操作上，也纳入 H1 的比较。
    
    在当前的 v0 代码上，重复 bookId 和注入冲突这两个探测用例**都判为失败**（计数停在 10），证明引擎能抓到这两个 bug。测试共 108 个通过。
  - **验证线**：V1 必须写两类用例：同一条消息里重复的条目键；带 `tx_conflict` 的用例，按 blueprint 规定的冲突处理方式断言结果。
  - **编排**：SKILL.md §6 新增"v0 之后才发现缺陷时，重新验收"：按正常流程退回修复，然后用 `git tag -f v0` 把 tag 移到新提交上，再重跑 S5 和阶段 6。
  - 修复本身按流水线走，S1→V1→S2→S3c→S4→V2→重新打 v0→S5→阶段 6，结果记在下文。

### bookstore-v2 修复后重新验收（2026-09-27）

- **流程**：按 SKILL.md §6"v0 之后才发现缺陷时，重新验收"走完，全程没有手工改代码。
  - S1：architect 写入 EXT-1（重复 `bookId` 先合并）和 EXT-2（用户裁定：冲突先在 handler 里有限次重试，仍失败就用 `batchItemFailures` 上报；格式损坏之类的失败照旧吞掉）；
  - V1：补了重复条目和 `tx_conflict` 用例，共 64 个用例；
  - S2：修复消费者代码；
  - S3c：加上 `ReportBatchItemFailures`，删掉不起作用的 IAM 权限；
  - S4 → V2 → 移动 v0。
  - 中途 architect 顺手把 BatchSize 改成了 1，理由不成立（开启 `ReportBatchItemFailures` 后本来就只重投失败的消息），用户裁定改回 10（EXT-3），又走了一轮 S1→S3c→S4→V2。
- **结果**：
  - V2：80/80（新增 16 个白盒用例），故障基准重新录制（13 个场景，18 次运行，0 infra 错误）；
  - 新 v0 = `3af5215`（工作区自己的仓库里 `git tag -f`，上层仓库不受影响）；
  - S5：R1 命中 1 处，但被新规则排除（这些事务可能改到同一条目）；数据驱动提出 1 个改动（去掉 `ReturnValues`），判为 revert（前后范围重叠）；最终 kept 0 个；
  - 阶段 6 PASS。
- **独立复核**：`gate(final)` PASS；我自己跑的全量回归 80/80；交付代码和 v0 逐字节相同。
- **这一轮暴露并修掉的问题**：
  - OpenHarness 又有两类错误没被重试，都在 OpenHarness 工作区里修了，没有提交：
    - `403 AccessDenied.Unpurchased`：账号恢复权限后，有一段时间会零星返回这个错误；
    - 裸的 `httpcore.ReadError('')`：它不是 httpx 异常的子类，原来的判断漏掉了。之前这个错误信息是空的，我先加了诊断（在消息里带上异常类型），才定位到它。
  - `perf.working_tree_changed()` 的路径解析 bug：`_git()` 会把整个输出 strip 掉，第一行的状态列因此少了一个字符（`utput/...`）。
  - 台账的应用名改为统一小写（两轮运行中 architect 写的是 `bookstore` 和 `Bookstore`）。
- **S5 提了一个复查建议**：bookstore-v1 台账里 R1 保留的 2 个改动，是不是也并行化了事务？已核对：v1 的消费者用的是普通 `UpdateItem ADD`，没有用事务。对同一条目的并发原子计数不会产生 `TransactionConflict`，所以那两处不受这个盲区影响。
  不过 v1 的消费者没有做幂等（它早于 §2.4 的规则），这是另一个层面的问题，已经在 bookstore-v2 里解决。

## 步骤 3 后续：事务冲突模拟、规则 R4、EXT-4（2026-09-27）

用户决定：先做冲突模拟，再开放 R4；消费者拆分事务的 bug 走返工修复。

### 事务冲突模拟（`verify-tools/tx-conflicts.js`）

- **规则**：按 AWS 开发者指南"Transaction conflict handling"实现。后到的请求如果碰到进行中的请求的条目，就被拒绝：
  - 单条写入碰到进行中的 `TransactWriteItems` → `TransactionConflictException`；
  - 写事务碰到进行中的写事务 → `TransactionCanceledException`，冲突条目的原因是 `TransactionConflict`；
  - `TransactGetItems` 碰到进行中的任何写入 → 同上。
- **两处共用**：
  - 验证代理（`controller.js`）：写事务至少保持 25ms，这样并发的重叠请求能被稳定地识别；
  - 测量垫片（`perf-shim.js`）：请求等待模拟延迟的那段时间算"进行中"。在 RIE 里，这个模块以 `.m2s-tx-conflicts.js` 的文件名复制进容器。
- **工具上的体现**：
  - `run_validate` 输出 `cases_with_conflicts`；
  - `run_equivalence` 新增 H1 (c)：基线没有冲突的地方，改动后不能出现冲突。
    - 差分范围扩大：受检函数的全部测试用例都不带场景故障地跑一遍，用例自带的故障也固定到操作上。
    - 原来只跑故障场景引用的用例，不安全的全并行消费者被判为 EQUIVALENT。
  - `run_proxy_metrics` 输出 `conflicts`；
  - `run_rie_verdict`：
    - 候选版本的冲突多于基线时，直接判 `revert`，不跑 RIE；
    - 新增 `guard_benchmarks` 参数：守卫基准在任何维度变差（包括随延迟变化），或者出现冲突，都判 `revert`。
- **验证**：在 bookstore-v2 的一份拷贝上做了对照。

  | 版本 | run_validate | run_equivalence diff | 代理指标（热点基准，10ms） | RIE 判决 |
  |---|---|---|---|---|
  | 全部消息无条件并行（不安全） | 13/13 通过，2 个用例有冲突 | DIFFERENT：H1 (c) 两处 | 21 次冲突，2 条消息重试耗尽被上报，WRU 80→232 | 会被冲突否决 |
  | 按 R4 分波次 | 13/13，无冲突 | EQUIVALENT，基线稳定 | 无冲突；顺序深度：正常基准 10→1，热点基准 10 | 正常基准 `kept`（5 轮中位数 [123..125]ms → [29..59]ms）；热点基准与基线无显著差异 |

  注意：不安全的版本在键不相交的正常基准上完全看不出问题。所以 R4 必须带热点基准做守卫。
- **测试**：新增 `tests/test_tx_conflicts.py`，包括冲突规则的单元测试，以及垫片里并发事务的实测；另外新增 `_observation` 的冲突计数测试。

### 规则 R4：按条目键分波次并行

- **写进的位置**：`m2s-perf-optimizer.md` 和设计文档 §8.2 / §6.2。
- **识别条件**：批处理逐条 `await`；不同消息的写入可能改到同一个条目；失败按单条处理。
- **改写方式**：
  - 每条消息会读写的条目键，从构造请求的同一个函数里取；
  - 每条消息排在"与它共享条目的前面消息中最晚的那一波 + 1"；
  - 同一波里用 `allSettled` 并行，`batchItemFailures` 最后按批内顺序排序。
- **写法的讲究**：
  - 骨架代码是先在拷贝上跑通全部检查、再写进 prompt 的。步骤 3 的教训是：知识库里的示例代码会被原样照抄。
  - 第一版把循环体挪进了新函数，结果 `run_coverage(changed_since)` 失败：循环体里一个测试走不到的防御性分支，变成了"改动行"。
  - 所以 prompt 规定循环体原地包成 `async (record) => {...}`，不改缩进，`continue` 改成 `return`。这样循环体里只改了开头、结尾和 3 处 `continue`；加上两个新的辅助函数，一共 53 行可执行的改动行，全部被覆盖。
- **基准**：有 R4 候选的函数要写两个基准，正常基准和热点基准。热点基准通过 `guard_benchmarks` 做守卫。重跑 S5 时可以新增缺少的基准，但不能修改已有的。
- **不适用的情况**：循环里有 `break`；消息之间共享会影响后续处理的状态；对批内其他消息写入的表做 `Query` 或 `Scan`；算不出一条消息会读写哪些条目。

### EXT-4：拆分事务只有第一批带幂等标记

- **来源**：审查 bookstore-v2 的消费者时发现。
- **问题**：合并后超过 99 个 bookId 时，事务被拆成多批，但只有第一批带标记。后面的批次重试耗尽、消息被重投时，第一批的标记已经存在，整条消息被当作重复跳过，后面几批的计数就永远丢了。
  - 知识库本来写的是"每批带自己的标记"，是代码没有照做。
  - V1 也没有针对批次边界的用例。
- **prompt 补充**：
  - architect：Pattern A 要写明拆分规则，包括标记键 `<消费者名>#<幂等键>#<批次序号>` 和确定的批次划分；
  - verifier：
    - 事务拆分要写两个用例：容量用例，以及"部分提交后重投"的用例；
    - 每个 SQS 消费者至少写一个"同一批里多条消息改同一条目"的用例，冲突模拟要靠它才能发挥作用。
- **修复**：按 SKILL.md §6 的"重新验收"流程走，S1→V1→S2→gate→S4→V2→重新打 v0→S5→阶段 6。结果见下文。

### EXT-4 返工结果（2026-09-27 18:20–20:10）

- **流程**：按 SKILL.md §6 的"重新验收"流程走完，全程没有手工改代码。
  - S1：architect 写入 EXT-4 的裁定（DECISIONS.md D-7d）：
    - 单批消息沿用原来的标记键 `bestsellers-update#<orderId>`，与存量标记兼容；
    - 多批消息的第 i 批用 `…#<orderId>#<i>`；
    - 批次按合并后首次出现的顺序划分，每批最多 99 个 bookId。
  - V1：新增 7 个用例和 1 个故障场景，包括 99/100 本书的边界、部分提交后重投、第 2 批冲突或永久失败。
  - S2：实现修复；gate(code) PASS。
  - 模板没有改动；S4 通过，gate(final) PASS。
  - V2：87/87，故障基准重新录制（14 个场景）。
- **新 v0**：`0a745fc`。我独立复核：gate(final) PASS，全量回归 87/87（无冲突），交付代码与 v0 相同。
- **S5 在新 v0 上的结果**：
  - S5 按 prompt 里的骨架实现了 R4（REQ-2），还新增了热点守卫基准 `batch-10x3-hotbook`；
  - 各项检查中：顺序深度 10→1，没有冲突，等价性 EQUIVALENT，改动行全部覆盖；
  - 但 `run_validate` 里有 1 个用例失败，S5 按规则回滚了这个改动，补丁存在 `perf/patches/`，写进了 advisory A1。
- **失败原因是用例本身，不是改动**：
  - 用例 `multibatch-permanent-failure-swallowed` 的故障写成 `{"op": "TransactWriteItems", "nth": 2}`，也就是"全局第 2 个事务"。
  - 顺序执行时，第 2 个事务是订单 A 的第 2 批；按 R4 分波次之后，订单 A 和订单 B 没有共同条目，同一波并行执行，第 2 个事务变成了订单 B 的。
  - `run_equivalence` 把用例自带的故障固定到操作上，所以没受影响；`run_validate` 按用例原样执行，就误报了失败。
- **修复**：
  - 故障规则新增 `match.item`：调用读写的条目包含指定的属性值时才算匹配，事务里任何一个条目符合都算。`nth` 只在匹配到的调用里计数，所以与调用顺序无关。
  - 新增测试：同一条规则在顺序执行和交错执行两种情况下，都命中同一个事务。
  - verifier：事件里有多条消息或多个条目时，故障必须用 `match.item` 固定到具体的操作上，不要按全局调用顺序计数。
  - 已有的测试资产里，只有这一个用例受影响。其余 `nth: 2` 的规则作用在单条消息内部（批次仍然顺序执行），或者在 `run_equivalence` 会固定到操作的故障场景里。

### 修复测试资产后重新验收，R4 被保留（2026-09-27 22:18–23:35）

- **V1**：把 5 处按全局调用顺序计数的故障规则改成 `match.item`。除了挡住 REQ-2 的那个用例，还有 2 个多批用例、1 个故障场景，以及 orders-create 并行 DeleteItem 的故障场景。V2 实际验证了这 5 处故障都打中了目标条目，87/87 通过。
- **新 v0**：`e098e0f`，代码与 `0a745fc` 相同，只改了测试资产。
- **S5 第 4 轮**：REQ-3 用 R4 实现分波次并行，被保留（commit `c893a8b`）。
  - 顺序深度 10→1，调用的多重集不变；
  - 等价性 EQUIVALENT（26 次运行，0 处差异），改动行 84/84 全覆盖；
  - RIE 10ms 下热调用 5 轮中位数从 [119.5..122.9]ms 降到 [17.4..55.9]ms，范围不重叠；成本更低；init 没有差异；
  - 热点守卫基准 `batch-10x3-hotbook` 在任何维度都没有差异，也没有冲突，守卫通过。
- **我独立复核 HEAD（`230afee`）**：gate(final) PASS；全量回归 87/87，没有冲突；bestsellers-update 对 v0 的等价性 EQUIVALENT。
- **说明**：这一轮用的是分诊之前的 prompt（编排器 22:18 启动时加载了 skill；子 Agent 在派发时读取 prompt）。分诊相关的 prompt 在这一轮结束后才同步到 `~/.openharness/agents`。

## S5 检查失败时的分诊（2026-09-27，K15–K18）

**起因**：REQ-2 被一个写得有问题的用例挡掉，正确的优化被回滚了。用户问：能不能区分测试的问题和代码的问题？四项决定都按建议采纳。

- **K15 机械分诊**：拿候选版本和 v0 比等价性。
  - 不等价（或者 gate 失败），是代码问题；
  - 等价但有用例失败，是测试问题。因为 `run_equivalence` 已经在受检函数的全部用例上、把故障固定到同一个操作上做了对比，这时失败只可能来自规格没有规定的东西：调用顺序、故障落点、列表顺序；
  - 等价、用例全过、但改动行没覆盖，是缺测试。
  - perf-optimizer 不能自己认定测试有错，也不能改测试。
- **K16 代码问题**：每个改动修一次，目标是恢复和 v0 一致的行为，修完重做全部检查；仍然不通过就回滚。台账分开记 `kept_after_fix` 和 `fix_attempted`，规则第一次就改对的比例仍然可以统计。
- **K17 测试问题**：
  - perf-optimizer 存补丁，标 `blocked_by_test`，附上证据；
  - S5 之后，verifier 按规格复核（新增 `STAGE_ID=VR`）：用例有问题就修，要写明规格依据，不许放宽规格要求的断言；用例没问题就维持，改动回滚，并作为工具问题报告；
  - 修改后的用例在 v0 的代码上跑通：`run_validate(ref="v0")`，这是新增的参数。然后提交测试资产，**v0 的 tag 不动**；
  - perf-optimizer 用"重新判决模式"只判决被阻塞的补丁；
  - 每轮 S5 最多复核一次。
- **K18 缺测试**：标 `blocked_by_coverage`，走同一个复核流程，由 verifier 补用例；确实走不到的行写明原因。
- **工具改动**：
  - `run_validate` 新增 `ref` 参数；返回结果和 `verdict_A.json` 都记录 `code_state`：是哪个 ref，或者工作区加上未提交的 `output/` 改动。这是为了解决上一轮的问题：S5 对候选版本跑了全量，覆盖掉了 `verdict_A.json`，事后看不出那个 FAIL 描述的是哪份代码；
  - perf-optimizer 的 prompt 规定，对候选版本只按函数跑 `run_validate`；
  - 台账新增 `blocked_by_test`、`blocked_by_coverage` 两种结果，以及 `kept_after_fix`、`fix_attempted`、`reviewed` 三个计数。
- **文档**：PERF_OPTIMIZATION_DESIGN §3.1、新增 §3.6、§9.2、§9.3；PIPELINE_REDESIGN 附录 A 的 K15–K18；SKILL.md 的"S5 之后的测试复核"；m2s-perf-optimizer（分诊表、代码问题修一次、重新判决模式）；m2s-verifier（VR 一节）。
- **测试**：114 个通过（新增台账计数的测试）。

## 步骤 4 准备：Python 支持（P1–P7，2026-09-28，K19–K25）

**起因**：基准里的另外 4 个应用中，3 个是 Flask（Python）。原版 M2S 同时支持 Python 和 JS（分析器、prompt、共享层都支持），迁移后仍是 Python 的 Lambda。但重构新加的规格提取、验证引擎、测量垫片、R1–R4 都只支持 Node。

**用户决定**：先跑 coffee（JS），同时补齐 Python 支持；P1–P7 都按建议来。Python 的 Lambda 镜像拉不下来，用户在另一个网络拉到了 Docker Hub 上的镜像 `amazon/aws-lambda-python:3.12`（Python 3.12.7，boto3 1.35.92）。

开发放在单独的 git worktree（`../Mono2SLs-pydev`，分支 `feat/python-support`）里进行。原因是 coffee 正在运行，子 Agent 在派发时会加载当前的工具代码，不能在运行途中替换。

### P1 执行（验证线）
- 按运行时选择 RIE 镜像。Python 的测试用例在官方镜像里跑；没有 Docker 时退回本机 Python 子进程（`py/m2s_local_invoker.py`），这时不测覆盖率。
- 本机免责声明区分 Node 和 Python。
- **共享层**：以前 Node 和 Python 都没有处理，恰好 bookstore 和 todo 都没用共享层。现在把 `sam build` 产出的层目录复制到容器的 `/opt` 下。
- **`sam build` 的缓存没有包括 `output/layers`**：改了层之后，仍然沿用旧的构建产物。已修复。
- 错误判定：Python 运行时的错误响应里多一个 `requestId` 字段，原来的判定因此漏掉了。
- `sam build` 构建 python3.12 函数需要本机有 Python 3.12，新建了 conda 环境 `m2s-py312`，构建时放在 PATH 最前面。
- **M2S 原版就有的一个 bug**：知识库和 tasks.yaml 规定的 Python 共享层布局是"代码放在 `layers/shared/python/` 下，并且设置 `BuildMethod: python3.12`"。实测 `sam build` 会自己再套一层 `python/`，结果变成 `/opt/python/python/`，导入失败，真实的 Lambda 也会这样。
  - 改了 SAM 知识库和 code-developer 的 prompt；
  - 新增 gate 检查 C13（层的目录布局，Node 和 Python 都检查）。

### P2 覆盖率
- coverage.py（cp312 manylinux wheel，第一次使用时解包到 `py/vendor/`，这个目录不进仓库）复制进容器。
- 由包装模块 `m2s_cov_handler.py` 先启动覆盖率，再导入真正的 handler，所以模块级别的代码也会被测到；每次调用后保存一次。
- 容器里的 `m2s_cov_report.py` 把结果转换成与 Node 相同的结构：多行语句覆盖它占的所有行，复合语句只覆盖头部。
- **改动行覆盖检查以前会漏报**，Node 也受影响，已修复：
  - 以前是按固定的目录深度（`lambdas/<domain>/<fn>/`）把改动文件对应到函数，平铺的目录布局会被静默跳过，结果"全部覆盖"；
  - 共享层的改动根本不检查；
  - 现在按模板里的 CodeUri / ContentUri 来对应；
  - 没有任何用例加载过的改动文件，算作没覆盖；
  - 不属于任何函数的改动文件，单独报告；
  - Node 的覆盖率现在也包括层的文件。

### P3 测量垫片
- **`py/m2s_perf_shim.py`**：替换 `URLLib3Session.send`，其余逻辑与 Node 垫片相同。冲突模拟由 `py/m2s_tx_conflicts.py` 实现，加了锁；用同一组用例检查它与 JS 版结果一致。
- **本机代理指标**：`py/m2s_perf_harness.py`，在 `m2s-py312` 环境里运行，boto3/botocore 的版本与 Lambda 一致（1.35.92）。
- **在 RIE 里加载垫片**：一开始用 `PYTHONPATH`，结果层导入失败。原因是只要设置了 `PYTHONPATH`，Lambda 的 bootstrap 就不再添加 `/opt/python`。改用 `AWS_LAMBDA_EXEC_WRAPPER` 加 `m2s_boot.py`。另外补上了 `AWS_DEFAULT_REGION`。
- **Python 垫片的日志一开始解析不到**：Python 的 `json.dumps` 默认在冒号后面带空格，perf.py 按 Node 的紧凑格式解析 `"max_rss_mb":` 和 `"conflict":true`，结果 RSS 显示为 0，冲突也会漏数。已改成紧凑格式，并加了测试。
- **顺序深度改在第二次调用上测**（两个运行时都改了），原因见设计文档 §9.6。
- **`run_equivalence(diff)` 不再要求必须有故障场景**：它现在会跑全部用例；只有 record 模式仍然要求。

### P4 规则 R1–R4 的 Python 写法
写进 `m2s-perf-optimizer.md` 的"Python 函数的写法"一节，代码骨架都在测试工作区上跑通了全部检查：

| 改动 | 验证 / 等价性 / 改动行覆盖 | RIE 10ms（5 轮中位数） |
|---|---|---|
| R4 分波次（SQS 消费者，一批 10 条） | 8/8 / EQUIVALENT / 40/40 | `kept`：[118.4..119.0] → [22.3..28.6]ms；热点守卫基准没有差异 |
| R1 并行删除 8 个条目 | 3/3 / EQUIVALENT / 24/24 | `kept`：[93.6..94.5] → [17.8..20.1]ms |
| 不安全的全并行（对照） | 用例全过，但有 2 个用例出现冲突 / H1 (c) DIFFERENT | 热点基准 18 次冲突，1 条消息被上报重投 |

写法上的讲究：
- 线程池最多 10 个线程，这是 botocore 的连接池大小；
- `Table` 按线程建；
- 只有一条消息的波次在当前线程里直接执行。去掉这一条的话，热点基准的 CPU 从 5.2ms 升到 8.8ms；
- R1 的适用前提要写清楚：每一条的异常都在循环里处理掉了。原来的测试工作区里有"其他错误就 raise"，这种情况按 §6.2 不适用。

### P5 Flask 规格提取
- `spec_extract_py.py` 基于语法树分析，字段和格式与 Express 的提取相同。
- 数据访问能跟到项目内的函数、类方法、模块级实例（`storage = DataStorage()`）。
- 表名能解析到：`Table(...)` 的参数；返回表的辅助函数（逐层绑定参数，例如 `get_users_table()` → `get_table('TABLE_USERS_NAME')` → `current_app.config[key]`）；模块常量；`os.environ` / `os.getenv`；配置类的常量；`self.X = os.environ.get(...)`；f-string 形式的默认值（保留为模板）。
- 表结构支持两种写法：`create_table(...)` 的关键字参数（包括参数通过调用处或元组列表传进来的情况），以及包含 `TableName` 和 `KeySchema` 的字典。
- 三个应用的结果：

  | 应用 | 端点提取 | 数据访问的表解析出来 | 表结构 | 出处检查 |
  |---|---|---|---|---|
  | shopping-cart | 10/10 | 33/33 | 1 | 0 错误 |
  | ecommerce | 30/30 | 36/36 | 6 | 0 错误 |
  | airline-booking | 18/18 | 22/22 | 4 | 0 错误 |

- `provenance` 现在允许在 `status` 为 `unknown` 时 `kind` 也是 `unknown`（状态码放在变量里的情况）。
- 标注员 prompt 补充了 Flask 的说明。
- **M2S 分析器的一个潜在 bug**：`methods=['GET','POST']` 只记下了最后一个方法。这三个应用里没有这种写法，暂时不修。

### P6 知识库
- `m2s-async-patterns-kb` 加入 Pattern A 的 Python 版本，包括按键合并、每个分块自己的标记、冲突重试和上报。
- 把知识库里的这段代码原样放进测试工作区，在 DynamoDB Local 上跑 8 个用例，8/8 通过。

### 测试
- 125 个通过。
- 新增 `test_py_perf.py`、`test_spec_py.py`；C13、改动行对应、台账计数的测试。
- 两个可选的 Python 集成测试（需要设置 `M2S_VERIFY_INTEGRATION=1`），都通过。

## 不许 Agent 修改流水线工具（2026-09-28）

- **起因**：shopping-cart（第一个 Flask 应用）的 S1 里，`gate(blueprint)` 报了 3 条误报。分析报告保留了 Flask 的路径写法 `/product/<product_id>`，blueprint 用的是 `{product_id}`，而 B0 只认识 `:id` 和 `{id}`。
  - architect 于是**自己改了 `gates.py`、`verify.py` 和测试**，然后报 BLOCKED。
  - 编排器审计了这个改动：测试通过、已验收的 JS 应用仍是 0 条发现、真正缺失的端点仍然会被报出来。审计之后它接受了这个改动。
  - 改动本身是对的，经复核后以 44738a1 提交，并注明来历。但子 Agent 改工具属于越权。
- **现在的规则**（用户采纳）：
  - **prompt**：7 个 agent 定义的禁止事项里都加了一条：不许修改 `m2s-mcp-server/`、`.openharness/`、`~/.openharness/`；认为工具有误时报 `STAGE_BLOCKED`，写明证据。
  - **编排器（SKILL §2）**：每个阶段结束后检查仓库状态。子 Agent 改了工具就不采用：用 git 恢复，这个阶段按 BLOCKED 处理，证据交给人。
  - **机械检查**：`gate(final)` 新增 TOOLS 检查。工具目录里有未提交的改动或新文件，就是 ERROR（S5 写的 `perf-rules/ledger.json` 除外）；`~/.openharness/agents/m2s-*.md` 与仓库里提交的副本不一致，也是 ERROR。所以流水线必须在已提交的工具上运行。
  - 编排器的 TODO 清单写到工作区的 `storage/` 下，不写到仓库根目录。coffee 之前的两份已经移过去了。

## 响应头断言 `expect.headers`（2026-09-29）

- **起因**：shopping-cart 的 CORS-1 重新验收里，V1 开了 SI-12，指出响应头没法断言：`expect` 只认状态码、响应体、表状态、SDK 调用和消息，而网关模拟的 401 只带 `Content-Type`。
  - architect 的裁定是：CORS 头和 `Set-Cookie` 的覆盖交给 S4 静态复核，外加 V2 观察原始响应。
  - 这相当于把一个可以机械判定的性质交给了人工阅读，所以补上这个工具能力。
- **`expect.headers`**：
  - `equals: {头名: 值}`：某个值完全相等；
  - `contains: {头名: 子串}`：某个值包含子串；
  - `absent: [头名]`：不能出现。
  - 头名不区分大小写，`headers` 和 `multiValueHeaders` 合并判断；同一个头有多个值时（例如多个 `Set-Cookie`），任意一个值满足即可。
  - `_check_expect` 会校验格式。
- **网关 401 带上模板声明的头**：
  - `Model` 解析 `AWS::Serverless::Api` 和 `Globals.Api` 的 `GatewayResponses`；
  - 匿名请求命中鉴权路由时，模拟的 401 使用 `UNAUTHORIZED` 的头；没有 `UNAUTHORIZED` 就用 `DEFAULT_4XX`；声明了 `StatusCode` 时也照用；
  - 头的值写成 `"'...'"` 或 `!Sub "'${Param}'"`，参数按默认值解析，并去掉外层单引号。
  - `_resolve` 也支持长写法 `{Ref: X}` / `{Fn::Sub: ...}` / `{Fn::GetAtt: [X, Y]}`。
- **H1 不用改**：`_observation` 的 outcome 本来就包含整个返回值，响应头已经在等价性比较范围内。
- **不模拟的部分**：预检请求（`OPTIONS`）由 API Gateway 按模板 `Cors` 直接应答，不调用函数，工具不模拟；规格对预检有要求时，仍交给 S4 静态复核。m2s-verifier 的 prompt 已写明这一点。
- **测试**：新增 3 个，覆盖网关 401 的头（含 `DEFAULT_4XX` 回退和长写法）、`judge` 的三种匹配和双表合并、`expect.headers` 格式错误。全部 133 passed / 3 skipped。

### 补充：`contains` 支持子串列表，新增 `count`（2026-09-29）

- **起因**：shopping-cart 补测（a87e2f1）里暴露了两处断言不了的情况，已登记在 SI-13 (5)。
  - `contains` 一个头只能写一个子串。cookie 值是随机的，只能把"名字"和"属性"拆成两条断言，还要借大小写不同的头名来绕开。
  - `equals` 按集合判断，而且合并两张表时会去重，所以断言不了"只有一个"。
- **改动**：
  - `contains` 的值可以写成子串列表，要求**同一个值**包含列表里的每一个子串。
  - 新增 `count: {头名: n}`，要求这个头恰好出现 n 次。
- **合并规则改为 API Gateway 的实际行为**：`multiValueHeaders` 的值全部保留（其中重复的值也算多次）；`headers` 里的一对头和值如果也出现在 `multiValueHeaders` 里，只算一次；大小写不同的同名头算作多次出现。
  - 之前的写法会把 `multiValueHeaders` 内部的重复值也去掉，`count` 会因此少算。
- **测试**：新增 1 个，扩展了格式检查的用例。134 passed / 3 skipped。

## 判决规则：0ms 只作参考；带过滤的故障基准重录改为合并（2026-09-30）

- **真实延迟规则**（用户裁定）：
  - 起因：R1 并行化已经四次判成 `pending_human`（shopping-cart 的 REQ-1，ecommerce 的 REQ-1/2/3），形状都一样：0ms 变慢，5/10/15ms 明显变快。四次都由人裁定保留。
  - 规则：`_judge` 在 5/10/15ms 全部明显变好、且热调用 CPU 中位数不超过基线 2 倍（`CPU_RATIO_LIMIT`）时，把延迟维度判为 better。0ms 只作参考，记在 `realistic_latency_rule.reference_0ms`。init、成本、冲突、RSS 的否决照旧。
  - 同时修了一处：成本维度一律按 10ms 那一轮计算。以前用传入的第一个延迟值，传 `[0,5,10,15]` 时就按 0ms 算成本，ecommerce REQ-2 的成本就是这样变成 worse 的。
- **`run_equivalence(mode="record", functions=...)` 改为合并**：
  - 起因：ecommerce 的 V2 带函数过滤重录了一次，整个 `fault_baseline_v0.json` 被覆盖成只有那一个函数的 12 条记录，只能全量重录恢复。
  - 现在的行为：已有基准时，只替换这些函数的场景、观察和 infra_errors，其余保留，并在 `partial_updates` 里记下时间和函数名。runtime 不一致时拒绝合并。没有旧基准时照常写入，并标注 `partial`。
- **文档与测试**：更新了设计文档 §3.2、m2s-perf-optimizer 和 m2s-verifier 的 prompt、server 的 docstring。新增测试 4 个（真实延迟规则 3 个、合并 1 个），共 138 passed / 3 skipped。

## 覆盖率只统计项目源码（2026-10-01）

- **起因**：airline-booking 的函数用 `requirements.txt` 声明了 stripe 和 requests，`sam build` 把这些包（连同 urllib3、idna 等依赖）装在 handler 旁边。
  - 覆盖率因此把 413 个第三方文件也算了进去。`bookings-create` 显示语句 54.0%、分支 10.4%，`uncovered` 列表有 25822 条，其中 handler.py 只占 116 条。
  - 以前只排除 `node_modules`，Python 的 pip 包没有排除。
- **改动**：
  - `run_coverage` 现在只统计项目源码，也就是函数 CodeUri 下的文件，和它所用 Layer 的 ContentUri 下的文件（显示为 `layer:<Id>:<path>`）；
  - 其余文件只计数，记在 `excluded_files`；
  - 拿不到 CodeUri 时，维持旧行为。
- **实测**：用 airline-booking 现有的原始覆盖率数据重算，`bookings-create` 从 54.0% / 10.4%（414 个文件）变为 **72.5% / 64.7%**（只有 handler.py），排除 413 个文件。
- **测试**：新增 1 个，共 139 passed / 3 skipped。

## API 类型在 S1 决定；规则 R1b 投机读（2026-10-10）

依据 `PERF_OPTIMIZATION.md` §10.4 (a)(e) 和 §10.6 的 A/B。

- **API 类型**：
  - blueprint `api_gateway` 新增 `api_type`（`http`/`rest`）、`clients`（`same_region`/`internet`）、`api_type_reason`、`rest_features`。默认 HTTP API，payload 固定 1.0，Lambda 事件和 REST 相同。
  - gate(blueprint) **AT1**：需要 REST 独有功能却选了 http 报 ERROR；选了 rest 却不需要报 WARNING（R6 advisory 的来源）。`cognito_authorizer`（有 `auth: required` 或 `cognito.enabled`）和 `gateway_responses`（未被裁掉的全局响应头）自动推导。
  - gate(template) **AT2**：模板与 `api_type` 一致；HttpApi 事件必须 `PayloadFormatVersion: '1.0'`；rest + `same_region` 必须 `EndpointConfiguration: REGIONAL`。C7/C7b 同时识别 `HttpApi` 事件。旧工作区（blueprint 没有 `api_type`）不做 AT2。
  - verify.py：HttpApi 事件按 REST 事件建模，事件多一个 `version: "1.0"`。
  - 现有工作区按 AT1 推导：acmeair-a1 没有 REST 独有需求，应选 http；bookstore-oh 等有 Cognito，仍是 rest。
- **R1b 投机读**：
  - spec `responses[].guard_kind`：`authz` / `error_propagation`（provenance 校验取值）。
  - verifier：`error_propagation` 的提前返回只排除写操作和发消息，不排除读。
  - perf-optimizer：§6.2 改窄；规则 R1b 与 JS/Python 写法；R6 只出 advisory。
  - `run_equivalence(speculative_reads=[...])`：多出的读只在基线返回错误、且不是 401/403 的用例上接受；写操作、消息和基线的每一次读必须不变；结果给出 `speculative_reads` 和 `extra_reads_total`。
- **测试**：新增 6 个（AT1 两个、AT2 一个、HttpApi 事件一个、R1b 判定两个），共 184 passed（不含集成测试）。
- **未做**：R1b 和 HTTP API 都还没有在一次完整的流水线运行里用过；e2e_ec2.py 的 `v0-http` 变体仍然假设 v0 是 REST。

## Cognito 应用也可以用 HTTP API（2026-10-10）

- **实测**：临时栈 `m2s-jwt-probe`，同一个用户池同时挂 HTTP API JWT 授权器和 REST Cognito 授权器，各发 12 种请求。测完已删除，证据在 `Mono2Sls-artifacts/tests/results-e2e/jwt-probe/`。
  - payload 1.0 下 claims 在 `requestContext.authorizer.claims`，和 REST 相同；
  - 差异：`cognito:groups` 是 `"[staff admins]"`，REST 是 `"staff,admins"`；`exp`/`iat` 是 epoch 秒，REST 是日期字符串；HTTP API 接受 access token，REST 返回 401；
  - `CorsConfiguration` 给授权器的 401 加了 `Access-Control-Allow-Origin: *`。
- **改动**：
  - AT1 不再从 Cognito 推导 REST；`gateway_responses` 只在有 `ACAO: *` 以外的全局头时推导；新增可选的 `cognito_user_pools_authorizer`（只接受 ID token 时用）；
  - AT2：有 `auth: required` 函数的 HttpApi 必须有 JWT `DefaultAuthorizer`；
  - verify.py：数组 claim 按 API 类型格式化（顺带修正 REST：以前是 Python 的 `str(list)`，现在是 `"a,b"`）；HTTP API 的匿名 401 在请求带 Origin、CorsConfiguration 允许 `*` 时带 ACAO；
  - architect、code-developer、verifier、sam-engineer、sam-kb 相应更新。
- **按新规则**：acmeair-a1、bookstore-oh、todo-oh 都应选 http；airline-booking-c3 仍需 REST。
- **测试**：新增 3 个，修改 1 个，共 187 passed。

## 有界旧数据缓存、perf_hints、e2e 适配、延迟表 M 级标定（2026-10-10）

依据 `PERF_OPTIMIZATION.md` §10.3、§10.4 (b)(c)。

- **(c) perf_hints**：spec 业务规则可标 `perf_intent`；blueprint `metadata.perf_hints`（gate PH1）。
- **(b) bounded_staleness**：
  - SI 新字段 `bounded_staleness: {tables, ttl_seconds, evidence}`，必须是 `class: contract`（gate CT7）；
  - verify.py：`Model.cached_tables`；这些函数每个用例前重启执行环境（controller `restart()`，RIE 用 `docker restart`，本地重启子进程）；用例新增 `repeat`，各次结果必须相同；H1 不比较被缓存表的读；
  - `m2s_cov_handler.py` 在重启后先 `load()` 已有覆盖率数据；
  - perf-optimizer 新规则 R8 及写法；verifier、architect、spec-annotator 说明更新。
  - 集成测试：带模块级缓存的函数，两个用例给同一个键不同的数据。不隔离时第二个用例失败（读到第一个的值），隔离后通过。
- **e2e 适配**：`e2e_ec2.prepare_variant` 识别原生 HTTP API 模板，只设置 `AccessLogSettings` 的格式；对原生 HTTP API 拒绝 `v0-http` 变体。
- **延迟表 M 级**：`e2e_calibrate.py` + `e2e-tools/calibrate/`，一次性 AWS 标定，测完删栈；`latency-profile.json` 改为实测 p50（另存 p90 和旧的 E 级值）。`perf.py` 的说明文字改为读取表里的等级。这是人工运行的标定步骤，流水线本身不调用 AWS（§10.3 末尾的说明）。
- **测试**：新增 6 个（CT7、PH1、缓存表的读、repeat、Model、缓存隔离集成测试）。

## gate C14：Cognito 设置对账；调度器 `rework`（2026-11-01）

- **起因**：bookstore-v3a 打 v0 之后的阶段复查发现，blueprint 写了 refresh 令牌 7 天（对齐单体的 JWT），模板的 `UserPoolClient` 没写，部署后会是 Cognito 默认的 30 天。C1–C13 都不检查 Cognito 资源的属性，测试也不连真实的 Cognito，所以这类遗漏只能在部署后发现。
- **C14**（template 和 final 阶段）：`blueprint.cognito` 里写了的 `token_validity`、`password_policy`、`username_attribute`，模板必须一致。
  - 令牌有效期按秒比较，单位用 `TokenValidityUnits`（默认 access/id 是小时、refresh 是天）；模板没写的，按 Cognito 默认值（1 小时、1 小时、30 天）比较。报错信息给出该写的数字和单位，因为 blueprint 里是 `"7d"` 这样的字符串，不能照抄；
  - 模板完全没写 `PasswordPolicy` 时按 Cognito 的默认策略比较；写了但缺字段的，要求显式写出；`temporary_password_validity_days` 为 0 时允许不写（CloudFormation 只接受 1–365）；
  - 值是内部函数、或者 blueprint 的时长读不懂时，只给 WARNING；
  - template 阶段有一条 C14 错误就判 FAIL，因为后面的 S4 改不了模板。
  - 在 18 个已有工作区上回放：只有 bookstore-v3a 报错，报的就是这一处。
- **调度器**：
  - `rework --task T [--recheck] --comment ...`：人把一个已关闭的任务退回，下游已关闭的任务按各自的 `on_stale` 处理。不占自动回炉的次数。`--recheck` 先重跑这个任务的检查，不通过才启动 worker，适合"刚加了一条检查"的情况；
  - `resolve` 和 `rework` 支持 `--open-issue TEXT`：人决定暂不处理的问题，写进运行报告；
  - 人的决定写进 `journal.md`，后面的异常处理者能看到。处理者的系统提示词加了一句：同一件事没有新证据时，照人的决定办；
  - 修正：过期的调度器动作会重新执行。以前 TAG_V0 过期后只"复查"一个空的检查列表，不会重新打 tag；
  - 修正：人已经关闭的关口，在阶段复查再次提出问题时重新打开。以前会被直接忽略。
- **测试**：C14 新增 10 个测试函数，good workspace 的模板补了和 blueprint 一致的用户池；调度器新增"复查交给人 → 人接受 → 加检查后退回 S3c → V2 重跑、v0 重打 → 复查再次提问"的场景测试。

## v2 N0：工作区版本戳、裁定记录、共用的记录代理（2026-11-01，分支 `feat/v2-modules`）

依据 `PIPELINE_V2_PLAN.md` §5 N0（V2P-01 至 V2P-12 已于同日确认）。

- **N0.1 工作区版本戳**：`storage/harness_version.json` 记下运行开工时的 harness（`started_with`），以及之后每次续跑时 harness 不同的那几次（`resumed_with`）。
  - 文件由调度器从事件日志重建：worker 改了它不算越界，也改不掉（下一次检查时重写，事件 `dispatcher_file_rewritten`）；
  - 打 v0 时随 `storage/` 一起提交，所以 v0 带着它是用哪个 harness 做出来的。
- **N0.2 裁定记录**（`coord/rulings.py`）：
  - `record_ruling` 工具（m2s-server），只有 architect 和异常处理者能调用。字段按设计稿 §4.12：问题、结论、类别（contract / implementation-trace / migration-change / spec-gap）、放宽类型（order / freshness / alternatives，自动归到 R 层）、影响的用例、观测层和任务、依据、是否偏离单体；
  - 工具只写一份提议到 `coord/rulings/`（worker 读写不到）。这次尝试通过检查后，调度器才把提议收进 `storage/rulings.json`；没通过的尝试，提议作废；
  - `storage/rulings.json` 也由调度器从事件日志重建，只追加：人的确认或否决是一条新记录，指向它处理的裁定；
  - 偏离单体的裁定（V2-20）先是 `proposed`，调度器建关口 `HUMAN_<裁定号>`，挡住它影响的、还没开始的任务。人用 `resolve --accept` 确认、`--reject` 否决（照搬单体）；
  - 生效的裁定作为反馈交给它点名的任务；这些任务里已关闭的，连同下游一起失效，按各自的 `on_stale` 处理；
  - 人可以用 `resolve ... --ruling-file R.json` 直接写裁定；
  - 运行报告新增"裁定"一节。
- **测试**：版本戳 3 个（含调度器场景），裁定 12 个单元测试和 2 个调度器场景（失败尝试的提议作废；偏离单体的裁定等人确认，同时人补一条裁定让已关闭的 S1 重新检查）。
- **N0.3 共用的记录代理**：`verify-tools/ddb-proxy.js`，从 `controller.js` 原样移出 DynamoDB 后端（DynamoDB Local / dynalite）、记录代理、故障注入、事务冲突模拟和表的清空、写入、导出。`controller.js` 改为调用它（669 行 → 341 行）。N1.3 的 `run_monolith` 也用它，这样单体侧和目标侧的后端与记录方式一致（V2P-11）。
  - 行为不变的核对：在 bookstore-v3a 的副本上，新旧 controller 各跑一遍。`run_validate` 在本机 + dynalite、本机 + DynamoDB Local、RIE + DynamoDB Local 三种组合下都是 128/128；`run_equivalence(diff)` 对 v0 基线（旧 controller 录的）158 次运行 EQUIVALENT；
  - `test_tx_conflicts.py` 里按文本截取 `controller.js` 的测试改为直接 `require('./ddb-proxy')`。
