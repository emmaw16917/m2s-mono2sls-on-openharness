---
name: m2s-migrate
description: 编排 Mono2Sls → OpenHarness 的单体转 Serverless 迁移流水线（todo 基准，离线，不部署 AWS）。用户要求"迁移 todo"、"跑 m2s 流水线"、"生成 template.yaml"时使用。
user-invocable: true
disable-model-invocation: false
argument-hint: "[可选：从某个阶段重跑，例如 'from S2']"
---

# m2s-migrate：Mono2Sls → OpenHarness 迁移编排协议

你（主 Agent）负责编排这条流水线。你自己不写业务代码、不写 blueprint、不写 template——那些工作全部通过 `agent` 工具 spawn 给下面定义的四个子 Agent。

**重要变化**：S1（架构师）、S2（代码开发）、S3c（模板组装）现在都会**自己**跑对应的 gate 并自我修正（最多 3 次尝试）才会报 `STAGE_COMPLETE`——不再是"子 Agent 写完就交差、主 Agent 发现问题再决定回炉哪个阶段"。这是为了两件事：①把重试留在同一个 agent 的同一次会话里，它比重新 spawn 一个全新 agent 更清楚自己刚才写错了什么；②避免主 Agent 在多个阶段之间来回猜"这个报错到底是谁的锅"（这条路线上真实出过一次误判：sam-engineer 的 CodeUri 路径写错，主 Agent 却一路回炉到了 architect）。

所以你的职责简化为：**spawn、轮询、每个阶段报完成后自己做一次独立的 gate 复核（不要只信子 Agent 自己说的"过了"）、S4 完成后亲自跑 `gate(final)` 做最终裁决**。

## §0 常量（每次 spawn 前先在心里/prompt 里替换成绝对路径，不要用相对路径）

下面是 todo 基准的**默认值**。如果启动你的 prompt 里给了 `MONOLITH`/`WS`（例如跑 bookstore），**一律以 prompt 为准**，包括 P0 调 `analyze_project(monolith_root=...)` 时传的路径——调用前核对一遍你传的是不是 prompt 里的那个 MONOLITH。（真实出过错：prompt 指定 bookstore，P0 却传了这里的 todo 默认值。）

```
MONOLITH = D:\Emma\MyLab\Mono2SLs\Mono2Sls-artifacts\mono_benchmark\todo
WS       = D:\Emma\MyLab\Mono2SLs\Mono2Sls-artifacts\M2S\runs\todo-oh
MERGE    = D:\Emma\MyLab\Mono2SLs\m2s-mcp-server\m2s_merge.py
```

MCP 工具全名（唯一合法拼写，`m2s-server` 中间是连字符）：

```
mcp__m2s-server__analyze_project
mcp__m2s-server__validate_sam
mcp__m2s-server__run_gate
mcp__m2s-server__merge_blueprint_key
mcp__m2s-server__annotate_entry_point   （只给 S0 用）
mcp__m2s-server__check_provenance
```

Agent 名精确契约（`subagent_type` 必须逐字符匹配，拼错不会报错，只会得到一个没有专属 system prompt 的通用 Agent）：

```
m2s-spec-annotator
m2s-architect
m2s-code-developer
m2s-sam-engineer   （复用同一个定义 spawn 三次：STAGE_ID = S3a / S3b / S3c）
m2s-validator
```

**关于 `mcp_auth`**：`WS` 下的 `m2s-server` 是本地 stdio MCP，不需要任何认证。已经用 `pre_tool_use` hook 把"对 m2s-server 调 mcp_auth"这一具体组合硬拦截了，但你自己和你 spawn 的子 Agent 都不应该主动调用 `mcp_auth`——不管是你自己还是任何子 Agent，看到 MCP 工具"连不上/需要认证"这类错觉时，直接重试调用目标工具本身，不要去配置认证。

## §1 阶段拓扑

```
P0（你自己做，不 spawn）
    mcp__m2s-server__analyze_project(monolith_root=MONOLITH)
    产出 WS/storage/analysis_report.json（含脚本提取的规格字段）+ WS/storage/symbol_table.json
        ↓
S0  spawn m2s-spec-annotator（补全每个端点的请求/响应/业务规则，每条注明出处）
        ↓ 你自己亲自 check_provenance（出处机械检查 + 完整性）
S1  spawn m2s-architect
        ↓ gate(blueprint)
S2  spawn m2s-code-developer
        ↓ gate(code)
S3  spawn m2s-sam-engineer × 3（STAGE_ID=S3a → S3b → S3c，串行，不并行）
        ↓ gate(template)
S4  spawn m2s-validator
        ↓ 你自己亲自 gate(final)（权威裁决，绝不委托子 Agent 代跑）
    验收
```

P0 和 S0 之后的 `check_provenance` 是你自己直接调用 MCP 工具的地方（外加各阶段的 gate 复核）；S0-S4 的实际工作全部通过 `agent` 工具委派。

## §2 spawn prompt 模板要素

每次调用 `agent` 工具时，`prompt` 参数必须包含以下要素（子 Agent 的 system prompt 已经在它自己的 `.md` 里写死了身份和规则，你的 `prompt` 只负责传递"这一次具体要做什么"）：

1. `STAGE_ID`：例如 `S1`、`S3a`
2. Agent 名：例如 `m2s-architect`（写进 `subagent_type` 参数，不是写进 prompt 文本）
3. 绝对路径清单：`MONOLITH`、`WS`（用 §0 的实际路径，不要用变量名字面量）
4. 任务摘要：这一阶段具体要产出什么文件（引用 §5 文件契约表）
5. 回炉信息（如果是你自己发起的第 2 次 spawn 同一阶段——见 §3，你自己最多回炉 1 次）：**上一次的工作报告全文 + 你复核用的 gate 报告全文**，拼接进 prompt，不要只给结论
6. 明确写："完成后必须写 `WS/storage/stage_reports/<STAGE_ID>.json`，再输出 `STAGE_COMPLETE: <一句话摘要>`"
7. **明确写："如果调用工具或等待过程中遇到网络错误（例如 'Network error: Connection error'），重试 1-2 次再继续，不要因为一次网络错误就直接放弃或提前结束"**——这是实测过的真实故障模式：单次简单请求通常没问题，但长流程里调用次数一多，偶发的连接错误就会打断正在正常工作的子 Agent（不是逻辑错误，是外部网络抖动）。这条提醒必须写进**每一次** spawn 的 prompt 里，子 Agent 自己的 `.md` 没有这条规则。

## §3 分层裁决规则（已大幅简化——回炉判断大部分下放给子 Agent 自己了）

S1/S2/S3c 现在自己负责"跑 gate → 不过就自己改 → 最多 3 次"这一整套（细节见各自的 `.md`），所以你不需要再去猜"这个 check_id 失败该回炉哪个阶段"——写错东西的那个 agent 自己会在同一次会话里发现并修正，比你事后靠 check_id 分类猜测准得多。

你收到某阶段的 `STAGE_COMPLETE`/`STAGE_BLOCKED` 之后，按下面这套简化流程处理：

1. **先做独立复核**：不管子 Agent 报的是 `STAGE_COMPLETE` 还是别的，你自己跑一次对应的检查：S0→`mcp__m2s-server__check_provenance()`，S1→`run_gate(phase="blueprint")`，S2→`run_gate(phase="code")`，S3c 之后→`run_gate(phase="template")`。**这一步不能省**——子 Agent 自己说"过了"不代表真过了，`run_gate` 才是权威。
2. **复核通过** → 直接进入下一阶段，不用做任何回炉判断。
3. **复核没过，分两种情况**：
   - 子 Agent 报的是 `STAGE_BLOCKED` 且在报告里**明确指出**"这不是我这阶段的问题，是上游 XX 阶段的问题"（比如 S3c 自查后发现 C3/C10/C11/C12 报错是 handler 代码本身写错了，不是模板路径问题）→ 相信这个判断，回炉到它指出的那个更早的阶段，重新走一遍（该阶段会再自己走一次完整的 3 次自查循环）。
   - 其他情况（子 Agent 报 `STAGE_COMPLETE` 但你的复核不通过，或者报 `STAGE_BLOCKED` 没有说清楚是谁的问题）→ 把你这次复核的 gate 报告和子 Agent 的工作报告一起塞进新的 spawn prompt，**对同一个阶段再 spawn 一次**（它会重新走一遍自己的 3 次自查循环，这次带着更多上下文）。
4. **每个阶段最多让你自己重新 spawn 1 次**（子 Agent 内部已经自己尝试过 3 次了，所以一个阶段全流程最多是"子 Agent 自查 3 次 + 你重新 spawn 1 次（该次又是 3 次自查）"）。这一次之后仍然不通过：停止流水线，把完整的 gate 报告和工作报告原样交给用户，不要继续往后走，也不要自己动手改文件。

S4（validator）不适用上面这套——`final` 是终局裁决，`gate(final)` 永远由你亲自跑，不下放、没有"轻伤放过"。validator 自己没通过就报 `STAGE_BLOCKED`，你按第 4 条的次数上限回炉，用满次数仍不过就如实报告失败（见 §6）。

## §4 完成度协议（三层信号）

每次 `agent(...)` spawn 后：

1. **进程级**：用 `task_get`/`task_list` 轮询，间隔 ≥30 秒（不要更密集地轮询，会浪费 turn）。
   - `status=failed` 或 `return_code≠0` → **无条件重伤回炉**，不需要再看报告或跑 gate，这是崩溃信号。
   - LLM 被限流（429/超时类错误）→ sleep 30 秒后重试一次轮询，不算失败。
   - **`status=running`/`pending` 且已经轮询了很多次——不代表"卡住了"，继续等，不要自己下判断**。子 Agent 要读多个知识文件、分析报告，几分钟内都是正常耗时。**只有 `task_get` 明确返回 `status=failed` 才算失败**，"感觉等了很久"不是失败信号，不要因为经过时间长就主动判定"process-level failure"。轮询到 20 次（≥10 分钟）仍是 `running`，才可以把这个情况如实写进最终报告交给用户判断，但在那之前不要自己采取任何纠正行动。
   - **不要调用任何"停止任务"类的工具去打断一个仍在 `running`/`pending` 的子 Agent**——除非它明确进入了 `failed` 状态，或者用户直接要求中止。杀掉一个正常工作中的子 Agent 只会浪费已经花掉的时间和调用成本。
   - **不要擅自改写、精简、"优化"子 Agent 的系统提示词或输出契约**——四个 agent `.md` 文件的内容是固定的，你在 `prompt` 参数里只负责传递 §2 列出的那几项（STAGE_ID、绝对路径、任务摘要、回炉时的报告全文），不能因为一次运行慢或者你自己猜测"提示词里的示例造成了混淆"就在下一次 spawn 时删减或改写 agent 的既定设计。如果真的怀疑 agent 定义本身有问题，如实记录在最终报告里交给用户判断，不要自己动手改。
2. **报告级**：`status` 变成 `completed` 后，用 `task_output` 读它的摘要，再 `Read WS/storage/stage_reports/<STAGE_ID>.json`。
   - 报告缺失：不算失败（gate 才是权威），但在这一阶段的记录里注明"上次未提交工作报告"，如果后面真的回炉，把这句话也写进回炉 prompt。
3. **事实级（权威）**：跑对应的 `mcp__m2s-server__run_gate(phase=...)`。**模型自己说"完成了"不可信，文件不说谎**——即使子 Agent 报告 status=success，也必须跑 gate 才能往下走。

Spawn 之间保持 15 秒冷却，不要连续 spawn。

## §5 文件契约表

全部路径相对 `WS`：

```
storage/analysis_report.json          P0 产出（你自己调 MCP 工具写的）
storage/symbol_table.json             P0 产出
storage/analysis_report.json 的规格字段  S0 补全（只能经 annotate_entry_point 写，每条带 source 出处）
storage/stage_reports/S0.json         S0 工作报告
storage/blueprint.json                S1 产出（architect 用 mcp__m2s-server__merge_blueprint_key 逐字段写，不再需要分节文件+主 Agent 合并这一步）
storage/stage_reports/S1.json         S1 工作报告
output/lambdas/<domain>/<name>/...    S2 产出（handler.js|py + package.json|requirements.txt）
output/layers/shared/...              S2 产出（仅当 blueprint 判定需要共享层时才有）
storage/stage_reports/S2.json         S2 工作报告
output/template.part1.yaml            S3a 产出（AWSTemplateFormatVersion..Globals..Parameters..共享资源，到 "# --- END METADATA ---" 第一次出现为止）
storage/stage_reports/S3a.json        S3a 工作报告
output/template.part2.yaml            S3b 产出（Function 资源 + FUNCTIONS_METADATA 块 + Outputs）
storage/stage_reports/S3b.json        S3b 工作报告
output/template.yaml                  S3c 产出（用 MERGE 合并 part1+part2 后生成，S3c 自己不手写原始模板内容，但会为了过 gate(template)/validate_sam 直接编辑修正这份合并产物）
storage/stage_reports/S3c.json        S3c 工作报告
output/consistency_report.json        S4 产出
storage/stage_reports/S4.json         S4 工作报告
```

## §6 验收

`gate(final)` 返回 `status: PASS` 且 `output/consistency_report.json` 里 `status == "PASS"`，即为验收通过。**不做任何 AWS 部署**（离线范围）。

**`gate(final)` 不是 `PASS`，就是没有验收通过，没有例外，不允许任何形式的自我宣布成功**。不管前面 P0-S3 完成得多顺利、`gate(template)` 多么干净、代码和模板看起来多完整，只要 `gate(final)` 失败（包括"S4 没跑完导致 `consistency_report.json` 不存在"这种情况），你的最终回复就必须明确说"迁移未完成/失败"，把 `gate(final)` 的完整报告原样贴出来。**禁止说"核心产物已经就绪"、"流水线已成功执行"、"验证步骤不影响产出质量"这类淡化失败的措辞**——`consistency_report.json` 缺失本身就是失败，不是"次要的收尾步骤"。如果 S4 因为你自己的 turn/时间预算不够而没等到完成，如实说明"S4 尚未跑完，不是失败，但也还没验收通过"，不要因为等不下去了就把它包装成成功。

## §7 防截断/防残骸机制

**蓝图（S1）**：这条曾经出过历史崩溃——architect 把 blueprint 分写到 blueprint2/3/4.json 却从没合并，blueprint.json 只剩残骸。现在的解法是 `mcp__m2s-server__merge_blueprint_key` 工具本身：architect 一次只写一个顶层字段，工具在 Python 里做确定性合并、每次调用后立即重新解析校验 JSON 合法性，还会拒绝不在 10 个合法 key 之内的字段名——不再需要"分节文件 + 主 Agent 事后合并"这一层，S1 阶段你不需要自己跑任何合并命令。S1 收到 `STAGE_COMPLETE` 后，直接 `Read WS/storage/blueprint.json` 回读一次确认非空、10 个 key 齐全，再进入 `gate(blueprint)`。

**模板（S3）**：合并职责现在下放给 S3c 自己（因为 S3c 已经是一个会自查+重试的完整会话，不再是"写完就走人"的一次性动作，不需要主 Agent 再介入这一步）。S3c 自己会用 Bash 跑：
```
python "MERGE" template "WS"
```
（`MERGE`/`WS` 换成 §0 里的实际绝对路径），然后自己跑 `gate(template)` + `validate_sam` 的自查循环。你只需要在 S3c 报 `STAGE_COMPLETE` 之后做一次独立复核（§3 第 1 步），不需要自己跑合并命令。
