---
name: m2s-migrate
description: 编排 Mono2Sls → OpenHarness 的单体转 Serverless 迁移流水线（三条线：实现、验证、优化；离线，不部署 AWS）。用户要求"迁移某个单体"、"跑 m2s 流水线"、"生成 template.yaml"时使用。
user-invocable: true
disable-model-invocation: false
argument-hint: "[可选：从某个阶段重跑，例如 'from S2']"
---

# m2s-migrate：Mono2Sls → OpenHarness 迁移编排协议

你（主 Agent）负责编排这条流水线。你自己不写业务代码、不写 blueprint、不写 template——那些工作全部通过 `agent` 工具 spawn 给 §0 列出的子 Agent。

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
mcp__m2s-server__run_validate           （验证线；你在 V2 之后亲自调一次做复核）
mcp__m2s-server__run_coverage           （只给 m2s-verifier 用）
mcp__m2s-server__run_equivalence        （m2s-verifier、m2s-perf-optimizer 用）
mcp__m2s-server__run_proxy_metrics      （只给 m2s-perf-optimizer 用）
mcp__m2s-server__run_rie_verdict        （只给 m2s-perf-optimizer 用）
mcp__m2s-server__update_ledger          （只给 m2s-perf-optimizer 用）
```

Agent 名精确契约（`subagent_type` 必须逐字符匹配，拼错不会报错，只会得到一个没有专属 system prompt 的通用 Agent）：

```
m2s-spec-annotator
m2s-architect
m2s-code-developer
m2s-sam-engineer   （复用同一个定义 spawn 三次：STAGE_ID = S3a / S3b / S3c）
m2s-validator
m2s-verifier       （复用同一个定义 spawn 两次：STAGE_ID = V1 / V2）
m2s-perf-optimizer （STAGE_ID = S5）
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
V1  spawn m2s-verifier（STAGE_ID=V1：只看规格写测试计划、测试事件、故障场景；兼任 architect 的交叉检查）
        ↓ 你自己亲自 run_validate(dry_run=true) 复核格式；V1 报 blocking 的 spec_issues → 退回 S1
S2  spawn m2s-code-developer
        ↓ gate(code)
S3  spawn m2s-sam-engineer × 3（STAGE_ID=S3a → S3b → S3c，串行，不并行）
        ↓ gate(template)
S4  spawn m2s-validator
        ↓ 你自己亲自 gate(final)（权威裁决，绝不委托子 Agent 代跑）
V2  spawn m2s-verifier（STAGE_ID=V2：执行、失败分诊、白盒补测、记录故障行为基准）
        ↓ 你自己亲自 run_validate()（不带参数）复核，并读 WS/verification/verdict_A.json
    验收：gate(final) PASS 且 verdict_A.json 的 status 为 PASS → 在 WS 里打 git tag v0（见 §6）
        ↓
S5  spawn m2s-perf-optimizer（在 v0 上做语义保持的性能改动；每个改动由工具判决，保留的改动逐个 commit）
        ↓
阶段 6 最终检验：你亲自 gate(final) + run_validate()（不带参数，全量回归）→ 最终报告（§6）
```

**V1 必须排在 S2 之前**：这是验证线"只看规格出题"的隔离手段——V1 运行时实现代码还不存在，所以一定看不到。不要为了省时间把 V1 挪到 S2 之后或和 S2 并行。

P0 和 S0 之后的 `check_provenance` 是你自己直接调用 MCP 工具的地方（外加各阶段的 gate 复核）；S0-S4 的实际工作全部通过 `agent` 工具委派。

## §2 spawn prompt 模板要素

每次调用 `agent` 工具时，`prompt` 参数必须包含以下要素（子 Agent 的 system prompt 已经在它自己的 `.md` 里写死了身份和规则，你的 `prompt` 只负责传递"这一次具体要做什么"）：

1. `STAGE_ID`：例如 `S1`、`S3a`
2. Agent 名：例如 `m2s-architect`（写进 `subagent_type` 参数，不是写进 prompt 文本）
3. 绝对路径清单：`MONOLITH`、`WS`（用 §0 的实际路径，不要用变量名字面量）
4. 任务摘要：这一阶段具体要产出什么文件（引用 §5 文件契约表）
5. 回炉信息（如果是你自己发起的重新 spawn——见 §3，每个阶段最多 2 次）：**上一次的工作报告全文 + 你复核用的 gate 报告全文**，拼接进 prompt，不要只给结论
6. 明确写："完成后必须写 `WS/storage/stage_reports/<STAGE_ID>.json`，再输出 `STAGE_COMPLETE: <一句话摘要>`"
7. **明确写："如果调用工具或等待过程中遇到网络错误（例如 'Network error: Connection error'），重试 1-2 次再继续，不要因为一次网络错误就直接放弃或提前结束"**——这是实测过的真实故障模式：单次简单请求通常没问题，但长流程里调用次数一多，偶发的连接错误就会打断正在正常工作的子 Agent（不是逻辑错误，是外部网络抖动）。这条提醒必须写进**每一次** spawn 的 prompt 里，子 Agent 自己的 `.md` 没有这条规则。

**给实现线的 prompt 里禁止写任何和测试有关的内容**。实现线指 S1 architect、S2 code-developer、S3a/S3b/S3c sam-engineer、S4 validator。验证线"出题和答题分开"，靠的就是实现线不知道题目是什么。具体不许写：
- 测试用例、故障场景、fixture、预期结果、断言写法，以及 `verification/` 下的任何文件路径；
- 验证线的情况：用例数量、V1/V2 做了什么，"V1 是按什么标准写预期的"，"哪些改动会被判为失败"；
- 你自己对规格歧义的裁定。**你不做裁定**：规格问题一律退回 architect（§3），裁定写进 blueprint，实现线从 blueprint 读。

需要告诉实现线"要保持哪些行为"时，只能引用规格本身：`analysis_report.json` 的出处、`blueprint.json` 的字段（包括 `metadata.spec_resolutions`）。例外只有两个：
- S1 因为 `spec_issues` 回炉时，可以把 `spec_issues` 全文交给 architect，因为那是规格问题，不是题目；
- V2 判定失败、按 routing 回炉时，只转发 routing 里用规格语言写的 `function`、`spec_refs`、`expected_per_spec`、`observed`（见 §3）。

## §3 分层裁决规则（已大幅简化——回炉判断大部分下放给子 Agent 自己了）

S1/S2/S3c 现在自己负责"跑 gate → 不过就自己改 → 最多 3 次"这一整套（细节见各自的 `.md`），所以你不需要再去猜"这个 check_id 失败该回炉哪个阶段"——写错东西的那个 agent 自己会在同一次会话里发现并修正，比你事后靠 check_id 分类猜测准得多。

你收到某阶段的 `STAGE_COMPLETE`/`STAGE_BLOCKED` 之后，按下面这套简化流程处理：

1. **先做独立复核**：不管子 Agent 报的是 `STAGE_COMPLETE` 还是别的，你自己跑一次对应的检查：S0→`mcp__m2s-server__check_provenance()`，S1→`run_gate(phase="blueprint")`，S2→`run_gate(phase="code")`，S3c 之后→`run_gate(phase="template")`。**这一步不能省**——子 Agent 自己说"过了"不代表真过了，`run_gate` 才是权威。
2. **复核通过** → 直接进入下一阶段，不用做任何回炉判断。
3. **复核没过，分两种情况**：
   - 子 Agent 报的是 `STAGE_BLOCKED` 且在报告里**明确指出**"这不是我这阶段的问题，是上游 XX 阶段的问题"（比如 S3c 自查后发现 C3/C10/C11/C12 报错是 handler 代码本身写错了，不是模板路径问题）→ 相信这个判断，回炉到它指出的那个更早的阶段，重新走一遍（该阶段会再自己走一次完整的 3 次自查循环）。
   - 其他情况（子 Agent 报 `STAGE_COMPLETE` 但你的复核不通过，或者报 `STAGE_BLOCKED` 没有说清楚是谁的问题）→ 把你这次复核的 gate 报告和子 Agent 的工作报告一起塞进新的 spawn prompt，**对同一个阶段再 spawn 一次**（它会重新走一遍自己的 3 次自查循环，这次带着更多上下文）。
4. **每个阶段最多让你自己重新 spawn 2 次**（PIPELINE_REDESIGN J5；子 Agent 内部每次都会自查 3 次）。用满 2 次仍然不通过：停止流水线，把完整的 gate 报告和工作报告原样交给用户，不要继续往后走，也不要自己动手改文件。

S4（validator）不适用上面这套——`final` 是终局裁决，`gate(final)` 永远由你亲自跑，不下放、没有"轻伤放过"。validator 自己没通过就报 `STAGE_BLOCKED`，你按第 4 条的次数上限回炉，用满次数仍不过就如实报告失败（见 §6）。

**验证线（V1/V2）的裁决**：

- **V1**：
  - 独立复核是你自己调 `mcp__m2s-server__run_validate(dry_run=true)`，它只检查测试资产的格式和名字。
  - 然后 `Read WS/verification/test_plan.json`，看 `spec_issues` 里有没有 `status` 为 `open` 的条目。**不管 severity 是 blocking 还是 non_blocking，只要有 open 的，就退回 architect**：
    1. 把所有 open 的 `spec_issues` 全文（带 `id`）放进回炉 prompt，重新 spawn S1，要求它逐条裁定，写进 `blueprint.json` 的 `metadata.spec_resolutions`；
    2. S1 复核 `gate(blueprint)` 通过后，`Read` 一下 blueprint，确认每个 open 的 issue id 在 `spec_resolutions` 里都有对应条目；
    3. 重新 spawn V1，在 prompt 里说明这是 **"V1 回炉：architect 已裁定 spec_issues"**，让它按裁定补用例；
    4. V1 回炉后仍有 open 的 issue，就再走一轮。这类"S1 → V1"的往返**最多 2 轮**。2 轮后仍有 open 的：
       - 如果都是 non_blocking：继续进入 S2，把这些 open issue 原样写进最终报告，交给人；
       - 如果有 blocking：停止流水线，交给人。
  - **你自己不裁定任何规格问题**，也不把 `spec_issues` 或它们的解读写进 S2 及以后阶段的 prompt（§2）。
- **V2**：
  - 独立复核是你自己调一次 `mcp__m2s-server__run_validate()`，**不带参数**，它会重写 `verdict_A.json`。再 `Read WS/verification/verdict_A.json`。**以工具给出的 `status` 为准，不以 V2 的自述为准**。
  - `FAIL` 时，按 `storage/stage_reports/V2.json` 里的 `routing` 退回：
    - `code` → 重新 spawn S2；
    - `template` → 重新 spawn S3c；
    - `spec` → 重新 spawn S1。
    
    回炉 prompt 里**只放** routing 条目的 `function`、`spec_refs`、`expected_per_spec`、`observed`。这些是用规格语言写的缺陷描述。**不要放**用例 id、用例文件、`mismatches`、fixture、断言或 `verification/` 路径（§2）。如果某条 routing 缺少这些字段，或者只能用测试的语言描述，就把它当 `undetermined` 处理，交给人。
  - 被退回的阶段完成后，重新跑它和它之后的 gate，一直到 `gate(final)`，然后**重新 spawn V2**。
  - `routing` 里有 `undetermined`（判断不了该退回谁）→ 停止，把 V2 报告和 `verdict_A.json` 原样交给用户，不要猜。
- **次数**：因验证失败退回某个阶段，每个阶段最多 **2 次**（PIPELINE_REDESIGN J5）；V2 自己修改测试资产的 2 轮不算在这里面（K4）。用满仍不 `PASS`，就停止并如实报告。

## §4 完成度协议（三层信号）

每次 `agent(...)` spawn 后：

1. **进程级**：用 `task_get`/`task_list` 轮询，间隔 ≥30 秒（不要更密集地轮询，会浪费 turn）。
   - `status=failed` 或 `return_code≠0` → **无条件重伤回炉**，不需要再看报告或跑 gate，这是崩溃信号。
   - LLM 被限流（429/超时类错误）→ sleep 30 秒后重试一次轮询，不算失败。
   - **`status=running`/`pending` 且已经轮询了很多次——不代表"卡住了"，继续等，不要自己下判断**。子 Agent 要读多个知识文件、分析报告，几分钟内都是正常耗时。**只有 `task_get` 明确返回 `status=failed` 才算失败**，"感觉等了很久"不是失败信号，不要因为经过时间长就主动判定"process-level failure"。轮询到 20 次（≥10 分钟）仍是 `running`，才可以把这个情况如实写进最终报告交给用户判断，但在那之前不要自己采取任何纠正行动。
   - **不要调用任何"停止任务"类的工具去打断一个仍在 `running`/`pending` 的子 Agent**——除非它明确进入了 `failed` 状态，或者用户直接要求中止。杀掉一个正常工作中的子 Agent 只会浪费已经花掉的时间和调用成本。
   - **不要擅自改写、精简、"优化"子 Agent 的系统提示词或输出契约**——所有 agent `.md` 文件的内容是固定的，你在 `prompt` 参数里只负责传递 §2 列出的那几项（STAGE_ID、绝对路径、任务摘要、回炉时的报告全文），不能因为一次运行慢或者你自己猜测"提示词里的示例造成了混淆"就在下一次 spawn 时删减或改写 agent 的既定设计。如果真的怀疑 agent 定义本身有问题，如实记录在最终报告里交给用户判断，不要自己动手改。
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
verification/test_plan.json           V1 产出（测试计划 + spec_issues）
verification/events/<fn>/<case>.json  V1 产出（V2 可补 wb-*.json 白盒用例、按规格修正 event 类错误）
verification/faults/*.json            V1 产出（故障场景）
storage/stage_reports/V1.json         V1 工作报告
verification/verdict_A.json           run_validate() 写（阶段 A 判定，权威）
verification/coverage/                run_coverage 写
verification/fault_baseline_v0.json   run_equivalence(mode="record", label="v0") 写
storage/stage_reports/V2.json         V2 工作报告（含失败分诊和 routing）
WS/.git + tag v0                      你在验收后打（§6）
perf/benchmarks/<fn>/<id>.json        S5 产出（性能基准事件，改动前提交，之后不改）
perf/requests/REQ-n.json              S5 产出（修改请求）
perf/verdicts/*.json                  run_rie_verdict 写（每次判决的完整数据）
perf/patches/REQ-n.patch              S5 产出（权衡型改动的补丁，交给人）
output/perf_report.json               S5 产出（优化报告）
storage/stage_reports/S5.json         S5 工作报告
WS/.git 里 v0 之后的提交               S5 每保留一个改动提交一次
m2s-mcp-server/perf-rules/ledger.json  update_ledger 写（放在仓库里，跨应用积累）
```

## §6 验收

同时满足下面三条，才算验收通过（阶段 A）。**不做任何 AWS 部署**（离线范围）：
1. `gate(final)` 返回 `status: PASS`；
2. `output/consistency_report.json` 里 `status == "PASS"`；
3. V2 之后你亲自调用 `run_validate()`，`WS/verification/verdict_A.json` 的 `status == "PASS"`。

验收通过后，你亲自用 bash 在 WS 里存 **v0 快照**（WS 是独立的 git 仓库，和上层仓库无关，上层仓库已经 ignore 掉了整个 runs/）：

```
cd "WS"
# 只看 WS 下有没有 .git 目录，不要用 git rev-parse / git status 判断"是不是仓库"：
# WS 在上层 Mono2SLs 仓库里面，那些命令会回答"是"，接下来的 add/commit/tag 就会打到上层仓库
# （上层仓库本来就有一个别的 v0 tag）。
[ -d .git ] || git init
git add -A output verification storage
git -c user.name=m2s-pipeline -c user.email=m2s@local commit -m "v0: stage A accepted"
git tag v0
git log --oneline -1 && git tag   # 确认 tag 存在
```

**v0 之后才发现缺陷时，重新验收**：
1. 缺陷按 §3 的规则退回负责的阶段（规格或设计问题 → S1，代码 → S2，模板 → S3c），后面的 gate 依次重跑；
2. 测试资产缺用例的，重新 spawn V1 回炉补用例（V1 仍然只看规格，不看 `output/`）；
3. 重新 spawn V2（它会重新记录 `fault_baseline_v0.json`），然后你亲自 `run_validate()`；
4. 通过后在 WS 里提交，**把 tag 移到新提交上**：`git -C "WS" tag -f v0`（不要删除历史，不要 `reset`）；
5. 之前 S5 保留的改动如果已经作废，先用 `git revert` 撤掉；然后在新的 v0 上重新跑 S5 和阶段 6。已提交的 `perf/benchmarks/` 可以沿用，不要改。

打完 v0 之后，进入 S5 和阶段 6：

**S5（性能优化）**：
- spawn `m2s-perf-optimizer`（STAGE_ID=S5）。prompt 里只写 §2 规定的要素：WS、MONOLITH、任务摘要、网络错误重试提醒。它读的是 WS 里的 v0 和测试资产，你不用转述任何测试内容（§2 的禁令同样适用）。
- S5 报 `STAGE_BLOCKED` **不会让流水线失败**：它只表示"性能优化没有收敛"，交付的仍是最后一个通过验证的版本。
- S5 结束后，`Read WS/output/perf_report.json` 和 `WS/storage/stage_reports/S5.json`，再用 `git -C "WS" log --oneline v0..HEAD` 核对：报告里每个 `kept`/`kept_inconclusive` 的改动，都要有对应的提交。

**阶段 6（最终检验）**，你亲自做：
1. `run_gate(phase="final")` 必须 `PASS`；
2. `run_validate()`（不带参数，全量回归）必须 `PASS`。

两项都通过：最终回复写明 v0 的 commit、S5 保留和回滚的改动（逐个写判决和关键数据）、`pending_human` 的数量、`verdict_A.json` 的用例数和通过数，以及覆盖率报告和故障行为基准是否存在。**还要写明**：v0 之后 S5 的改动只经过了机械检查（`gate(final)`）和验证线的动态检查（回归、等价性、覆盖），没有经过 validator（S4）的审查（PIPELINE_REDESIGN §1）。V1 仍有 open 的 `spec_issues`（§3 用满往返次数后留下的）也要逐条列出。

任何一项没通过：说明 S5 之后的版本有问题。在 WS 里执行 `git -C "WS" checkout v0 -- output`，然后 `git -C "WS" commit -am "revert S5: stage 6 failed"`，交付 v0 的实现；再重跑一次阶段 6，并如实报告。**不要用 `reset --hard`，不要删 tag。**

如果 `verdict_A.json` 带有 `disclaimer`（运行时检查在本机 Node 完成），要原样写进最终回复；`perf_report.json` 的 `confidence` 是 `low` 时也一样。

**`gate(final)` 不是 `PASS`，就是没有验收通过，没有例外，不允许任何形式的自我宣布成功**。不管前面 P0-S3 完成得多顺利、`gate(template)` 多么干净、代码和模板看起来多完整，只要 `gate(final)` 失败（包括"S4 没跑完导致 `consistency_report.json` 不存在"这种情况），你的最终回复就必须明确说"迁移未完成/失败"，把 `gate(final)` 的完整报告原样贴出来。**禁止说"核心产物已经就绪"、"流水线已成功执行"、"验证步骤不影响产出质量"这类淡化失败的措辞**——`consistency_report.json` 缺失本身就是失败，不是"次要的收尾步骤"。如果 S4 因为你自己的 turn/时间预算不够而没等到完成，如实说明"S4 尚未跑完，不是失败，但也还没验收通过"，不要因为等不下去了就把它包装成成功。

## §7 防截断/防残骸机制

**蓝图（S1）**：这条曾经出过历史崩溃——architect 把 blueprint 分写到 blueprint2/3/4.json 却从没合并，blueprint.json 只剩残骸。现在的解法是 `mcp__m2s-server__merge_blueprint_key` 工具本身：architect 一次只写一个顶层字段，工具在 Python 里做确定性合并、每次调用后立即重新解析校验 JSON 合法性，还会拒绝不在 10 个合法 key 之内的字段名——不再需要"分节文件 + 主 Agent 事后合并"这一层，S1 阶段你不需要自己跑任何合并命令。S1 收到 `STAGE_COMPLETE` 后，直接 `Read WS/storage/blueprint.json` 回读一次确认非空、10 个 key 齐全，再进入 `gate(blueprint)`。

**模板（S3）**：合并职责现在下放给 S3c 自己（因为 S3c 已经是一个会自查+重试的完整会话，不再是"写完就走人"的一次性动作，不需要主 Agent 再介入这一步）。S3c 自己会用 Bash 跑：
```
python "MERGE" template "WS"
```
（`MERGE`/`WS` 换成 §0 里的实际绝对路径），然后自己跑 `gate(template)` + `validate_sam` 的自查循环。你只需要在 S3c 报 `STAGE_COMPLETE` 之后做一次独立复核（§3 第 1 步），不需要自己跑合并命令。
