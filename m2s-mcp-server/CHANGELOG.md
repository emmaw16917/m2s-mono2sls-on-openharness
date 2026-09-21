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
