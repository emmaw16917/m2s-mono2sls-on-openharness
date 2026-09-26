# M2S 流水线重构设计（v1，设计稿）

> 状态：设计稿，未实现。2026-09-24 根据讨论定稿，决策索引见附录 A。
>
> 配套文档：
> - `PERF_OPTIMIZATION_DESIGN.md`：优化线本身的设计，包括判决规则、允许的改动范围、规则库、perf-optimizer 的定义；
> - `PERF_OPTIMIZATION.md`：对照官方参考实现，以及支撑本设计的实验数据（测量层级 L0/L1/L2/L2′/L3 的定义见其 §1）。

## 0. 为什么要重构

现在的流水线按**产物类型**分工：`static_analysis → architect → code-developer → sam-engineer → validator`。它隐含的假设是：每个
agent 交出一份"正确"的产物，验证只是末端的一道关卡。性能优化的讨论暴露了这个假设的三个问题：

1. **验证本身已经是开发工作**：构造测试事件、配故障注入、搭 RIE 容器、做差分测试，都不是"过一道关卡"能涵盖的。
2. **写代码和写测试需要不同的能力**：让 code-developer 顺手写测试事件，它倾向于写"能通过的用例"，而不是"能发现 bug 的用例"，
   这就是"自己出题自己答"。
3. **验证产生的东西没有归属**：测试事件、故障场景、覆盖率报告、差分结果都附属在 validator 或 perf-optimizer 名下，不是一等产物。

所以要改的是**分工的维度**，不是粒度：从"按产物类型"改为"**按职责线**"。

### 设计原则

- **实测数据做判据**：某个改动有没有效、有没有改变语义，由测量结果决定，不由改动者自己声明。
- **出题和答题分开**：验证线独立于实现线出题；判定由验证线的脚本完成，不由改动者完成。
- **证据不是真理**：`analysis_report.json` 和 blueprint 里的每个判断都要能追溯到出处（文件、行号、理由）。
- **保守交付**：交付物只包含有证据支持的改动；证据不足的改动退回，或交给人决定。

---

## 1. 总体结构

三条线：

```
实现线：静态分析 → architect → 实现（code-developer + sam-engineer + 静态一致性收尾）
验证线：测试设计 → 测试实现 → 执行 → 判定          （V 模型：测试设计只看规格；v1 排在实现之前）
优化线：perf-optimizer（诊断 → 改 → 验 → 判）       （"验"和"判"调用验证线的工具）
```

阶段拓扑（主 Agent 编排，状态用文件传递）：

```
阶段 1  分析        P0 脚本提取 → spawn spec-annotator（LLM 标注，注明出处）→ 出处的机械检查
                    → storage/analysis_report.json（含规格信息）
阶段 2  设计        spawn architect → storage/blueprint.json + storage/DECISIONS.md
阶段 V1 测试设计    验证 agent 第 1 次派发：测试设计 + 黑盒事件；兼任 architect 的交叉检查
                    （v1 排在实现开始之前，此时实现代码还不存在，所以天然看不到代码，见 §3.1）
阶段 3  实现        spawn code-developer（S2）→ spawn sam-engineer（S3a/S3b/S3c）
                    → spawn validator（静态一致性收尾，可修文件）
阶段 4  验证        验证 agent 第 2 次派发：执行 + 白盒补测 + 故障行为基准 → 判定
        ── 验收点：gate(final) + 验证判定通过 → 工作区 git 打 tag `v0`（到这里即算验收通过）──
阶段 5  优化（S5）  spawn perf-optimizer：每个改动都走验证线阶段 B，不通过就回滚
阶段 6  最终检验    gate(final) + 全量回归测试 → 最终报告
```

v0 之后的改动，只经过了机械检查（`gate(final)`）和验证线的动态检查，没有经过 validator 的审查。最终报告要写明这一点。

---

## 2. 实现线

### 2.1 静态分析（阶段 1）

- **做法**：先用 P0 脚本提取，再派发一个专门的 LLM agent `m2s-spec-annotator` 补全。
  - 规格标注单独交给一个 agent：主 Agent 只负责编排，不读单体代码；architect 只引用证据、做决策，不负责提供证据。
- **产出**：`storage/analysis_report.json`（定位是**证据**，不是真理）、`storage/symbol_table.json`。
- **v1 需要新增的规格信息**：

| 字段 | 内容 | 来源 |
|---|---|---|
| `entry_points[].request` | body、query、路径参数的字段，以及类型、是否必填 | 脚本提取 |
| `entry_points[].auth` | 端点级别的 auth 分类（直接标注，例如 `required` 并交给 Cognito），不再让 architect 从文件标签去推断 | 脚本提取 |
| `entry_points[].responses` | 成功和失败时的状态码、响应体结构 | 脚本提取 + LLM 补全 |
| `entry_points[].business_rules` | 从 handler 的条件分支里提炼出的业务规则 | LLM 标注 |
| `dynamodb_info.probable_tables` | 表名和键结构（例如从 `init-db.js` 提取） | 脚本提取 |
| `entry_points[].handler_location` | handler 在源码里的起止行（步骤 0 实现时补充，供标注员和出处检查定位） | 脚本提取 |
| `entry_points[].data_access` | DynamoDB 操作：操作名、表、索引、是否在循环里、经过的调用链（步骤 0 实现时补充：§3.2 要求测试用例写"预期的 SDK 调用序列"，没有这个字段，测试设计在只看规格时无从得知） | 脚本提取 |
| `dynamodb_info.table_schemas` | 每张表的分区键、排序键、GSI（`probable_tables` 只放表名，保持原格式不变） | 脚本提取 |

- **出处要求**：**每一条**（脚本提取的也一样）都写上出处 `source: [{file, line, symbol}]`，并标 `origin: script|llm`。无法确定的项标为 `unknown`，不许猜测。
- **实现**（步骤 0）：脚本在 `m2s-mcp-server/spec_extract.py`（`analyze_project` 调用），出处检查和标注写入在 `provenance.py`。
  脚本只识别 Express（JS/TS）；Python 端点的规格字段全部留给标注员。
- **标注员怎么写入**（K5）：新增 MCP 工具 `annotate_entry_point(method, path, field, value_json)`，一次替换一个端点的一个字段。
  写入前先校验结构、再对这次的值跑出处检查，不通过就整次拒绝并说明是哪条出处的问题。标为 `origin: "script"` 的项必须和已有脚本项完全一致，
  防止标注员把自己的判断标成脚本产出。沿用 `merge_blueprint_key` 的经验：不让 agent 一次重写一个大 JSON。
- **gate**（都由脚本执行）：
  - 报告是否完整（必填字段、出处格式）；
  - **出处的机械检查**：spec-annotator 标注的每个"文件:行号"都必须真实存在，而且那一行（或它附近几行）确实包含它引用的变量名或字段名。
    这一条拦的是 LLM 最典型的错误：编造出处。不通过就退回 spec-annotator。

### 2.2 architect（阶段 2）

- **产出**：`storage/blueprint.json`（决策记录）、`storage/DECISIONS.md`（每个判断的依据）。
- **在现有基础上补充**：
  - 每个判断都要**留下证据**。例如选择通信机制时，要记录调用点、`return_value_used`、`is_awaited` 和理由。
  - 规格信息（请求和响应的结构、业务规则、错误语义）直接**引用** `analysis_report.json`，不重新推断。
- **v1 去掉 `perf_hint`**：MemorySize 统一保持 512MB，它已经没有用处。
- **gate**：
  - architect 自己跑的静态自查，即现有的 B0；
  - **独立交叉检查**：由验证线的测试设计兼任（见 3.2）。

### 2.3 实现（阶段 3）

- **产出**：`output/lambdas/`、`output/template.yaml`。
- **agent 的组织方式**：code-developer 和 sam-engineer **仍然是两个 agent**，S3 保留 a/b/c 三个子阶段，这是为了避免单个 agent
  的输出被截断（见 m2s-migrate §7）。它们在编排上归为同一个"实现阶段"。
- **检查顺序**：写代码 → `gate(code)` → 写模板 → `gate(template)` → validator 做静态一致性检查（代码和模板之间是否一致）。
- **原 validator 移到实现线的末尾**，保留修复文件的能力。这属于"对自己产物的自查"，可以接受。
- **分工**：实现线只检查"**代码和模板之间**"是否一致（环境变量名、handler 路径等）。"**实现和规格之间**"是否一致，由验证线检查（见 3.3）。
- **code-developer 不再写测试事件**，测试事件归验证线。

### 2.4 上游正确性规则（从性能优化里拆出，从 v0 起生效）

这几条是正确性问题，不是性能问题，所以写进实现线的知识库，不交给优化线处理：

| 规则 | 写进哪里 | 说明 |
|---|---|---|
| 消费者要保证**重投是幂等的** | `m2s-async-patterns-kb`（S2）、`m2s-sam-kb`（S3） | 例如按 `messageId` 或 `orderId` 用条件写入去重。`FunctionResponseTypes: [ReportBatchItemFailures]` 加上返回 `batchItemFailures` 只是其中一部分：它把重投范围从整批缩小到单条消息，但不能消除消息内部的重复 `ADD` |
| 读路径上的 Scan **必须处理 `LastEvaluatedKey`** | `m2s-lambda-coding-kb`（S2） | 修的是数据超过 1MB 时结果出错的 bug，不是性能优化 |

这些规则的效果由验证线（测试用例、故障注入）来检验，**不参与性能消融实验**（见 `PERF_OPTIMIZATION_DESIGN.md` §10.2）。

---

## 3. 验证线（V 模型）

### 3.1 形态

- 同一个验证 agent 定义，**派发两次**：
  - **第 1 次**：只看规格，不能看到实现代码。
    - **v1 的做法是调整顺序**：把这次派发排在 architect 之后、实现开始之前。那时实现代码还不存在，所以一定看不到。代价是测试设计和实现不能并行，流水线会变长。
    - **为什么不用权限隔离**：OpenHarness 的 agent 定义里虽然有 `permissions` 字段，派发时也会传给子 agent（`tools/agent_tool.py`），但子进程的启动参数没有用到它，所以它不起作用。另外，按路径限制的规则（`path_rules`）是全局设置，只在工具调用带有文件路径参数时才会检查，在 bash 里 `cat` 一个文件可以直接绕过。
    - **以后的做法**：修改 OpenHarness，让 agent 级别的权限生效，并补上对 bash 读文件的限制。做到之后，测试设计就可以和实现恢复并行。
  - **第 2 次**：在阶段 3 之后。负责执行、白盒补测和判定。
- 每次派发，agent 内部都先写测试计划，再按计划写事件。
- **验证线只负责发现问题和判定，不修改实现线的文件。** 它只修改自己的测试资产（`verification/` 目录）。
- 执行和判定由 **MCP 工具**完成（见 §4.3），和 `run_gate` 并列，优化线也调用同一套工具。

### 3.2 阶段 A：验收（建立 v0 之前）

**测试设计**（第 1 次派发，只看规格）：
- **输入**：只有 `blueprint.json` 和 `analysis_report.json`，不看实现代码。
- **产出**：`verification/test_plan.json`，也就是黑盒测试计划。它按规格出题：逐个端点列出正常路径、异常和边界用例，每个用例都写明**预期结果**（状态码、关键字段、预期的 SDK 调用序列），并注明对应的规格出处。
- **兼任 architect 的交叉检查**：写不出预期结果的，说明规格不自洽，或者漏了端点。把问题写进报告，退回 architect。

**测试实现**（第 1 次派发内完成，或者在第 2 次派发开始时完成）：
- 测试事件：`verification/events/<function>/<case>.json`，每个事件都附带预期结果；
- 故障注入配置：`verification/faults/`。场景包括：第 k 次调用失败、全部失败、限流、超时、部分失败并返回 `batchItemFailures`。

**执行和判定**（第 2 次派发）：
1. **静态复核：实现和规格是否一致**。端点、字段、状态码要和 `analysis_report.json` 对得上。
2. **黑盒测试**：在 RIE 容器里执行测试事件，对照 blueprint 规格判定。v1 只对照规格，对照单体的运行结果放到 v2。
3. **覆盖率**：用 `NODE_V8_COVERAGE` 收集每个 handler 的分支覆盖情况。
4. **白盒补测**：根据覆盖率报告，给没走到的分支针对性地补事件。v1 **只报告**覆盖率，不设门槛。
5. **记录故障行为基准**：在每个故障场景下，记录 v0 的调用集合、返回值和抛出的错误，存为 `verification/fault_baseline_v0.json`。
   v1 只记录，不判断这些行为对不对；判断放到 v2，和差分测试一起做。

**通过标准**：每个事件的实际结果都和它的预期结果一致。"必须返回 2xx"只适用于正常路径的用例。
通过之后，存 **v0 快照**（在工作区的 git 里打 tag `v0`）。

### 3.3 阶段 B：优化期间的差分验证（对照 v0）

优化线每做一个改动，都要跑这一套。对照的是 **v0**，不是规格。

1. **回归测试**：重新执行 v0 的整套用例（黑盒 + 白盒），核对是否都还符合预期。**只跑受影响的函数**，即改了代码的函数，加上引用了被改共享模块的函数。最终交付前跑一次全量。
2. **故障注入差分**：在同一组故障场景下，比较改动前后的：
   - 实际执行的调用集合，对应 H1 的 (a)；
   - 返回值、抛出的错误、`batchItemFailures`，对应 H1 的 (b)。
3. **覆盖率变化**：看改动碰到的代码行有没有被测试执行到。没被执行到的，先请验证线针对这些行补事件；补完还是覆盖不到，这个改动就回滚。
   报告里要写明"本次保留结论覆盖了哪些路径"。

**备选方案（N2，觉得太耗时时启用）**：只做故障注入差分 + `VALIDATE` 冒烟。

### 3.4 失败分诊

验证失败时，按下面的顺序定位原因，并决定退回给谁：

1. **机械初筛**，根据直接信号分类：
   - 报错里带 mock 未覆盖的标记（`http-shim: no mock for ...`）→ 测量工具的问题；
   - `Runtime.ImportModuleError` → 打包或构建的问题；
   - 报错位置在 `node_modules`（SDK 解码）里 → mock 返回的数据结构不对的嫌疑。
2. **LLM 复核**：剩下的情况，由验证 agent 读日志、事件和 handler 代码，判断属于哪一类：事件的问题、handler 的逻辑 bug、模板和代码不一致，还是规格本身的问题。
3. **判断不了的，标为"未定"，交给人**，不去猜应该退回哪个阶段。

退回的对象：测量工具或事件的问题，由验证线自己修；代码问题退回 code-developer；模板问题退回 sam-engineer；规格问题退回 architect。

### 3.5 没有 Docker 时

- **语义检查改在本机 Node 里跑**：执行事件、收集覆盖率、记录故障基准、做故障差分，都用 `http-shim.js` 按 L1c 的方式在本机运行。
  `NODE_V8_COVERAGE` 在本机同样可用。
- **和 RIE 的差别，要写进报告**：
  - 用的依赖不是 `sam build` 的产物，所以 `ImportModuleError` 这类打包问题可能发现不了；
  - Windows 和 Linux 的运行环境不同；
  - Node 的小版本不同。
  
  这些差别对语义检查影响很小，但验收报告仍要标注"**运行时检查在本机 Node 中完成，未在 Lambda 运行时中验证**"。
- **性能判决**仍按 `PERF_OPTIMIZATION_DESIGN.md` §3.5 处理：改用代理指标，标注"低置信度"。

**以后的提速选项**：有 Docker 时，语义检查也在本机 Node 里跑（更快，不用启停容器），RIE 只用于性能判决，外加一次打包冒烟检查。
启用之前，要先做一次对照实验，确认本机 Node 和 RIE 在语义检查上的结果一致。

---

## 4. 编排

### 4.1 回炉规则

- 每个阶段最多由主 Agent 重新派发 **2 次**。原规则是 3 次，为了省时间先降下来，不够再加。
- 退回给哪个阶段，由 3.4 的分诊结果决定。
- **验证线自己修改测试资产的次数单独计算**：分诊结果是测量工具或事件本身的问题时，由验证线自己修。每次运行阶段 A，最多自己修 **2 轮**。
  这不占用实现线的回炉次数；超过 2 轮还没解决，就把问题标为"未定"，交给人。
- 超过次数上限就停止流水线，把完整的报告交给用户。
- **S5（优化）例外**：v0 已经验收通过，所以 S5 报 `STAGE_BLOCKED` 不会让流水线失败。它只表示"性能优化没有收敛"，如实写进报告，交付的仍是最后一个通过验证的版本。

### 4.2 文件契约（新增或变化的部分）

| 文件 | 由谁写 | 说明 |
|---|---|---|
| `storage/analysis_report.json` | 阶段 1（P0 脚本 + `m2s-spec-annotator`） | 新增规格字段，每条都带出处（2.1） |
| `storage/DECISIONS.md` | architect | 每个判断的依据 |
| `verification/test_plan.json` | 验证 agent 第 1 次派发 | 黑盒测试计划，带预期结果和规格出处 |
| `verification/events/`、`verification/faults/` | 验证 agent | 测试事件和故障配置 |
| `verification/coverage/`、`verification/fault_baseline_v0.json`、`verification/verdict_A.json` | 验证 MCP 工具 | 执行结果 |
| 工作区 `.git`（tag `v0`，每个被保留的改动提交一次） | 主 Agent、perf-optimizer | v0 快照；补丁就是 `git diff` |
| `output/perf_report.json` | perf-optimizer | 见 `PERF_OPTIMIZATION_DESIGN.md` §9 |
| `m2s-mcp-server/perf-rules/ledger.json`（**放在仓库里**，不在工作区） | perf-optimizer 追加 | 规则台账，跨应用积累 |

### 4.3 新增的 MCP 工具

| 工具 | 用途 | 依赖 |
|---|---|---|
| `run_validate` | 执行一组事件，逐条核对预期结果；返回状态码、SDK 调用序列、RSS | Docker（RIE）；没有 Docker 时退回本机 Node（§3.5） |
| `run_coverage` | 收集一组事件的分支覆盖率，列出没被执行到的分支 | 同上 |
| `run_equivalence` | 在同一组故障场景下，对照基准版本（默认 `v0`）做差分，输出 H1 (a)(b) 的判定 | 同上 |
| `run_proxy_metrics` | 调用次数、顺序深度、`warm_cpu.js`、容量单位估算 | 仅需本机 Node |
| `check_provenance` | 规格标注的出处机械检查（2.1） | 无 |
| `annotate_entry_point` | spec-annotator 逐端点、逐字段写入规格标注，写入前校验结构和出处（2.1，K5） | 无 |
| `run_rie_verdict` | 按 `PERF_OPTIMIZATION_DESIGN.md` §3 的规则，对改动前后做交替测量并给出判决 | Docker（RIE），没有本机降级 |

所有工具共同的要求：
- 环境变量从 `template.yaml` 读取每个函数的 `Environment`，**不写死**；
- 访问容器用 `127.0.0.1`，并且显式绕过代理；
- 代码用 `docker cp` 放进容器，不用绑定挂载；
- 在短路径下工作；
- 测试事件带上 Cognito claims。

现有的实验脚本（`perf-tools/`）是这些工具的原型。

---

## 5. 推进计划

分步推进，先在 bookstore 上做出初级 demo，之后再考虑 E0（在其他 benchmark 上跑流水线）：

| 步骤 | 内容 | 在 bookstore 上完成的标志 |
|---|---|---|
| 0 | 补规格信息：扩展 P0 脚本；新增 `m2s-spec-annotator` agent；做出 `check_provenance`；产出新版 `analysis_report.json` | 每个端点都有请求结构、响应结构和业务规则，并注明出处；出处检查全部通过 |
| 1 | 验证线：做出 §4.3 的 MCP 工具，新增验证 agent，跑通阶段 A | 生成 v0 快照，并附上覆盖率报告和故障行为基准 |
| 2 | 优化线：perf-optimizer v1、规则 R1–R3、台账，跑通阶段 B | R1 那一处（消费者改成并行）得出判决，并写进台账 |
| 3 | 调整阶段拓扑：原 validator 的职责移到实现线，m2s-migrate 的编排改为新结构 | 用新结构把 bookstore 从头到尾跑通 |
| 4 | E0 和消融实验 | 其他应用的规则命中情况；各条规则前移到上游后的效果 |

步骤 0–2 就是初级 demo。步骤 3 改动最大，放在 demo 之后。

---

## 6. 实现前需要确认的问题

目前没有。原来列在这里的 4 个问题已经定了（K1–K4，见附录 A）。
实现过程中如果发现新的问题，记在这里，确认后再动手。

---

## 附录 A：决策索引

讨论时用的编号，便于回查：

| 编号 | 决策 | 所在章节 |
|---|---|---|
| D1 | 写两份文档；`PERF_OPTIMIZATION_DESIGN.md` 在原文件上重写 | — |
| D4 | 先验收，存 v0，再做优化；每个被保留的改动都要重新过检查 | §1、§3.3 |
| F1-1（已被 K3 修改） | 没有 Docker 时仍然算验收通过；运行时检查不再跳过，而是改在本机 Node 里跑 | §3.5 |
| F2、H3 | 幂等和分页改为上游的正确性规则 | §2.4 |
| F4 | v0 快照用工作区里的 git 保存 | §4.2 |
| G1 | 失败分诊：机械初筛 + LLM 复核，判断不了的标为"未定" | §3.4 |
| G2 | 工具的环境变量从模板读取 | §4.3 |
| G3、G4 | 验证线独立出题，由覆盖率驱动补事件；v1 只报告覆盖率 | §3.2 |
| G5 | 和单体做差分测试放到 v2 | §3.2 |
| J1（顺序已按 K2 调整） | V 模型：测试设计只看规格 | §1、§3 |
| J2、⑤ | 原 validator 移到实现线末尾，保留修复能力；code-developer 和 sam-engineer 仍是两个 agent | §2.3 |
| J3、⑥ | 同一个验证 agent 派发两次，第一次不能看到实现代码（怎么做到，见 K2） | §3.1 |
| J4 | 执行和判定升级为 MCP 工具 | §4.3 |
| J5 | 回炉上限 2 次 | §4.1 |
| J6、D8 | 分步推进，规格信息先做；先做 bookstore demo，再考虑 E0 | §5 |
| N1（备选 N2） | 阶段 B = 回归测试 + 故障差分 + 覆盖率变化；回归只跑受影响的函数 | §3.3 |
| ② | 每个事件都带预期结果 | §3.2 |
| ③ O1 | 实现线查"代码和模板之间"，验证线查"实现和规格之间" | §2.3、§3.2 |
| ④ P1 | 由测试设计兼任 architect 的交叉检查 | §3.2 |
| ⑦ R1 | 改动碰到的代码没被测试覆盖到：先补事件，覆盖不到就回滚 | §3.3 |
| 规格来源 | 脚本提取 + LLM 标注，注明出处；v1 就补 | §2.1 |
| K1 | 规格的 LLM 标注由新增的 `m2s-spec-annotator` 完成，并做出处的机械检查 | §2.1、§4.3 |
| K2 | v1 通过调整顺序实现隔离：测试设计排在实现开始之前；以后改 OpenHarness，让 agent 级别的权限生效，再恢复并行 | §1、§3.1 |
| K3 | 没有 Docker 时，语义检查改在本机 Node 里跑；"有 Docker 也在本机跑语义检查"作为以后的提速选项，启用前先做对照实验 | §3.5 |
| K4 | 验证线自己修改测试资产，每次运行阶段 A 最多 2 轮，不占用实现线的回炉次数 | §4.1 |
| K5 | 标注员通过新增的 MCP 工具 `annotate_entry_point` 逐端点写入，写入时就做结构和出处检查 | §2.1、§4.3 |
| K6 | 步骤 0 只把 S0 接入 m2s-migrate 的 P0 之后；architect 引用规格字段的改动（§2.2）留到后续步骤 | §5 |
