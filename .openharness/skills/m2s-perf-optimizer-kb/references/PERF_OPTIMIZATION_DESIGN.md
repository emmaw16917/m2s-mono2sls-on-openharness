# M2S 性能优化设计方案（v2：O 优化线）

> 状态：v3 O 的设计稿。v1 的旧优化线设计（旧基线、模拟延迟与 sam+Docker、复核与重新判决、数据模型扩展、v1 默认配置、prompt 注入与消融实验）归档在 `PERF_OPTIMIZATION_DESIGN_v1.md`；本文不再重复那些流程。
>
> 配套文档：
> - `PIPELINE_V3_PLAN.md`：v3 流水线（J1 裁判、D1 增量测试、B0 → O → C1）；
> - `PERF_OPTIMIZATION.md`：对照官方参考实现，以及支撑优化线的实验数据（测量层级的定义见其 §1）；
> - `.openharness/skills/m2s-perf-optimizer-kb/`：O 的判决框架摘要，`references/PERF_OPTIMIZATION_DESIGN.md` 是本文的副本。

## 0. 背景与范围

### 0.1 v3 O 的位置与目标

v3 的优化线只有一个阶段 **O**，在实现通过裁判和增量测试、并打下 tag `B0` 之后运行。O 对已经验收通过的 B0 只做**语义保持**的性能改动；每个改动都必须通过四项正确性检查和一次性能判决，证据不足就回滚。改动的数量不是目标。

与旧优化线的关键区别：

| 旧优化线 | v3 O |
|---|---|
| 基线是旧 tag，检查是验证线阶段 B（回归、故障差分、覆盖率） | 基线是 **B0**，检查是 **judge + 增量测试**：`run_judge`、`run_validate(assets="verification/delta")`、`run_equivalence(mode="diff", assets="verification/delta")`、`run_coverage(..., with_judge=true, assets="verification/delta")` |
| 测试问题和缺测试由验证线复核后重新判决 | 没有复核和重新判决流程；`blocked_by_test` / `blocked_by_coverage` / `pending_spec` 留在报告里交给人 |
| 默认用 RIE 的模拟延迟判决 | 默认仍是 RIE；`M2S_AWS=1` 时用真实 AWS 判决（§2.2）。本地四项检查在 AWS 模式下用 `mode="local"` |
| 允许通过 spec 放宽申请改规格、改数据模型 | O 不改 spec、不新建 GSI；这类需求只写 `advisories` 或 `pending_spec` |

### 0.2 不做什么

- 不做架构级改动（换 API 类型、换异步机制、换表结构）——只写 advisory；
- 不做真实的 AWS 部署，除非 `M2S_AWS=1` 且人已确认费用（§2.2）；
- 不修改裁判、增量测试、故障场景，也不修改单体源码；
- 不追求把单体里的所有性能债都改掉；语义保持是硬约束。

---

## 1. 在流水线里的位置

```
V3 通过 → tag B0 → O（优化线）→ C1（gate(final_v2) + 全量裁判回归）
```

- O 的工作区是 B0 之后的提交；每个保留的改动单独提交，回滚就是撤销对应的提交。
- O 报 `report_blocked` **不会**让流水线失败。它表示优化没有收敛，交付的仍是最后一个通过检查的版本。
- C1 的 `judge_unchanged` 检查裁判资产没有被改动；O 只能写 `output/`、`perf/` 和自己的工作区报告。

---

## 2. 可用的测量手段

测量层级的定义和各层噪声水平统一以 `PERF_OPTIMIZATION.md` §1 为准，这里只列 O 会用到的部分：

| 指标 | 工具 | 在判决中的作用 |
|---|---|---|
| SDK 调用次数、顺序深度 | `run_proxy_metrics` | 代理指标：定位问题、给改动排序；决定一个改动是否属于权衡型 |
| 热执行 CPU（`warm_cpu.js`） | `run_proxy_metrics` | 同上；并行化改动的 CPU 护栏 |
| 容量单位估算 | `run_proxy_metrics`（由 `http-shim.js` 根据请求和响应内容估算） | 成本维度的输入（§4） |
| RIE 热调用 Duration | `run_rie_verdict` | **延迟维度的默认判决** |
| RIE init | `run_rie_verdict` | **冷启动维度的默认判决** |
| 裁判、增量测试、故障差分、覆盖率 | `run_judge`、`run_validate`、`run_equivalence`、`run_coverage` | 一票否决（§3.1） |
| RSS | `run_validate` / `run_judge` | 内存护栏，一票否决 |
| 真实 AWS 的 Duration、成本、端到端 guard | `run_aws_verdict` | `M2S_AWS=1` 时的判决（§2.2） |

### 2.1 RIE 默认路径与模拟延迟

`run_rie_verdict` 在本机的 Lambda 运行时容器（RIE）里交替测量改动前后：每轮 1 次冷调用 + 30 次热调用，至少 3 轮；CPU 配额按 MemorySize ÷ 1769 限制。SDK 最底层的 HTTP 发送被测量垫片替换，每次调用先等待一个固定的模拟延迟，再返回按基准数据计算的响应。因此测到的是"SDK 前后两段真实 CPU + 固定等待"。

完整的延迟模型、六项已知局限（延迟与数据量无关、并发不受限、没有建连开销、没有抖动和限流、各操作延迟相同、并发事务的冲突语义）以及冲突模拟（`tx-conflicts.js` / `m2s_tx_conflicts.py`）的细节见 `PERF_OPTIMIZATION_DESIGN_v1.md` §2.1。对 O 的判决来说，最重要的三条是：

- **并发不受限**：R1 / R4 的并行化收益是理想化的；报告里要写明"结论只在并发不受限的条件下成立"。
- **冲突模拟**：改动引入了基线没有的并发事务冲突时，`run_equivalence` 会报 H1 (c)，`run_rie_verdict` 直接判 `revert`。这是正确性盲区的补丁，不是可选项。
- **固定延迟**：延迟参数不改变代码的调用次数和顺序深度；代理指标仍然有效，但不能单独决定去留。

没有 Docker 时，`run_rie_verdict` 报 `NO_DOCKER`：O 只能用 `run_proxy_metrics` 的前后对比，把改动标为 `kept_inconclusive`、报告的 `confidence` 设为 `low`；打包和依赖类改动（R3）跳过。这就是"代理降级"（§3.2）。

### 2.2 AWS 判决模式（O 专用，2026-11-19 定）

**适用范围**：只用于 O 的性能判决。正确性（裁判、增量、等价、覆盖）仍在本地 `mode="local"` 跑——不需要 Docker，也不产生 AWS 费用；AWS 只提供性能、成本、覆盖三类证据。默认关闭：`M2S_AWS=1` 且人确认费用才启用；RIE 判决仍是默认路径，AWS 模式是可选替换。

**判决指标**

| 角色 | 指标 | 来源 | 判定 |
|---|---|---|---|
| 主维度 | Lambda 热调用 Duration | CloudWatch / REPORT，剔除冷启动 | 两版 ABAB，范围不重叠 = 明显 |
| 主维度 | 边际成本/请求 | Lambda Duration × GB-秒 + 请求费 + DynamoDB RRU/WRU + SQS；**不含 API Gateway** | 同上；报告另列含 API Gateway 的总成本 |
| guard | 端到端 p50 | 压测客户端（JTL / 样本） | 不得明显变差 |
| guard | 端到端 p95 | 同上 | 不得明显变差 |
| guard | 冷启动 | Init Duration、冷启动次数 | 默认 guard；REQ 明确针对冷启动/包体时才升为维度 |
| 必须 | 覆盖 | 改动函数的 CloudWatch 调用次数 > 0 | 否则 `INCONCLUSIVE`，不判定 |

**判决表**

| 情况 | 判决 |
|---|---|
| 本地正确性 / 冲突 / RSS 否决 | `revert` |
| 测量费用估计超过 `budget_usd` | `pending_human`（钱已经花了，结果交给人判断） |
| AWS 覆盖不到改动函数 | `INCONCLUSIVE`（先修负载） |
| Lambda Duration 或边际成本明显 worse（超噪声） | `revert` |
| 没有任何维度 worse，且 Duration / 边际成本至少一个明显 better | `kept` |
| Duration 在噪声内，但代理指标 better 且没有 worse | `kept_inconclusive` |
| 一好一坏（如 Duration 好、成本差） | `pending_human`（存补丁、回滚） |
| p50 / p95 / 冷启动 guard 明显 worse | `pending_human`（或按 REQ 升为维度） |

**单改动流程**

- 每个改动单独测；基线是**当前 HEAD**（B0 + 已 kept 的改动），不是永远对 B0；
- 维护一个滚动基线栈（当前 HEAD）；B0 栈只用于 B0 对 B0 的噪声校准和最终报告；候选一栈，测完拆掉；
- 所有测量在 `aws-load-client` 锁下 ABAB；阈值来自 `calibrate_aws_noise`（B0 对 B0）；≥3 轮、每轮 ≥10 个热样本；
- 互不影响的改动（不同函数、无调用关系）可以分别对同一基线测；有交互的按顺序累积测；
- 判决结果与 RIE 模式同构（kept / kept_inconclusive / pending_human / revert / INCONCLUSIVE），写进 `perf_report.evidence.aws`。

**与 RIE 模式的差异**

- 去掉 0/5/15ms 模拟延迟扫描和 `realistic_latency_rule`（真实 AWS 只有一个往返时间，用噪声校准 + ABAB 代替）；
- 冷启动从独立维度降为 guard；
- 成本判决用边际成本，含 API Gateway 的总成本只进报告；
- `run_rie_verdict` 保留为无 AWS / 离线模式：Docker 可用时沿用，不可用时按 §3.2 只给代理指标并标低置信度。

**函数内细节的作用**：Duration 只回答“快了多少”。LLM 的改进建议（REQ 的 `diagnosis` 与 `advisories`）还要引用函数内证据——代理指标的调用次数/顺序深度/热 CPU、CloudWatch 的 Duration 分位与 init、DynamoDB 容量、日志，以及 X-Ray 开启时的子段耗时（如果权限允许）——再结合代码阅读。每条建议都要在 `perf_report` 里给出 metric/value/evidence；架构类建议只进 `advisories`，O 不能直接实施。

**实现（2026-11-19）**：`perf.run_aws_verdict`（MCP 工具 `run_aws_verdict`）按上面的判决表和 guard 出结果；`coord.aws.measure_aws(ref=None)` 部署工作树，基线栈以 `M2SBaseline=<HEAD sha>` 标签跟随 HEAD（HEAD 变了自动重部署，同一 HEAD 的重复测量才复用）；O 的 prompt 按 `M2S_AWS` 生成“AWS 实测已开启/未开启”两段之一，并把 `M2S_AWS_LOAD_CMD`、`M2S_AWS_BUDGET_USD` 传下去；`s5_report` 在 `M2S_AWS=1` 且本次 O 的 spawn 事件记录了 `aws: true`（或报告标注 `judged_with: "aws"`、已有改动带 AWS 判决）时，要求本次判过、仍保留的改动都有 `evidence.aws.verdict`；spawn 事件同时记录启动时的 HEAD，本次 O 启动前已提交的 kept 改动（O-only 重跑旧工作区）不追溯。真实 AWS 冒烟要人确认费用后执行。

**负载与数据**：`run_aws_verdict` 的环境除 `M2S_AWS_TARGET` 外还带 `M2S_AWS_OUTPUTS`（栈输出，含 Cognito pool/client）、`M2S_AWS_RESOURCES`（逻辑 id → 物理名）、`M2S_AWS_WS`、`M2S_AWS_BENCHMARK`；`seed_cmd`（默认取 `M2S_AWS_SEED_CMD`）在每个栈部署后、第一轮测量之前运行一次，负责把 benchmark 的 `fixtures`/`generate` 数据写进该栈的 DynamoDB，并把 identity.sub 占位符换成真实 Cognito sub。仓库里的参考实现是 `perf-tools/aws_load.py`：复用 e2e 的 Cognito 会话；HTTP benchmark 按 request 重复发送，SQS benchmark 把 `sqs.records` 按轮发送（每轮换 orderId，避免幂等表跳过）。只要 `M2S_AWS_LOAD_CMD`/`M2S_AWS_SEED_CMD` 指向它，O 就能在空表的新栈上得到有效测量。

---

## 3. 判决规则

### 3.1 四项正确性检查（一票否决）

O 的每个改动，受影响的函数（= 改了代码的函数）都要跑完下面四项；任何一项不通过，改动都不能保留。四项要一起看，不能第一项失败就停——§3.3 的分诊需要全部结果。

1. **裁判**：`run_judge(functions=[...])` 要求 `PASS`。裁判的期望是单体的实际行为，冻结在 `judge/` 里。
2. **增量测试**：`run_validate(functions=[...], assets="verification/delta")` 要求 `PASS`。这个函数没有增量测试时返回 `total: 0`，算通过。
3. **与 B0 的等价性**：`run_equivalence(mode="diff", functions=[...], assets="verification/delta")` 要求 `EQUIVALENT`；没有增量测试时返回 `NOTHING_TO_COMPARE`，算通过，由第 1 项负责。判据是 H1（§5.1）的 (a)(b)(c)。
4. **改动行覆盖**：`run_coverage(functions=[...], changed_since="HEAD", with_judge=true, assets="verification/delta")` 要求 `all_covered` 为 true。裁判的用例或增量测试执行到了，都算覆盖。

另外两项仍然生效：

- **有效性校验**：事件确实走到了预期的代码路径（结果符合预期、SDK 调用次数和预期一致）。
- **RSS 内存护栏**：实际内存占用接近配置的 MemorySize 时，判定为有风险。

`M2S_AWS=1` 时，第 1、2、4 项加 `mode="local"`（不需要 Docker、不花钱）；第 3 项的模式参数仍是 `mode="diff"`。

### 3.2 性能判决（默认 RIE，AWS 可选）

代理指标（调用次数、顺序深度、`warm_cpu.js`、RIE init 的单次数值）**只用来定位和排序**，不能单独决定一个改动的去留。最终判决看总体效果，由工具给出：

- **默认（未开启 AWS）**：`run_rie_verdict`。工具按固定规则处理：
  - 先用 10ms 模拟延迟筛一遍（改动前后交替运行）；没有代理指标变差且 RIE 明显变好 → 绝对型改动，直接进入三维度综合判决；
  - 有代理指标变差 → 权衡型，在 0 / 5 / 15ms 下测量；5/10/15ms 全部明显变好、且热调用 CPU 中位数不超过基线的 2 倍时，延迟维度判为"改善"，0ms 只作参考（判决里的 `realistic_latency_rule`）；结论随延迟参数反转的，标"依赖真实往返时间"，作为权衡型交给人；
  - 三个维度（热调用 Duration、init、成本）都不变差、且至少一个明显变好 → `kept`；有的变好、有的变差 → `pending_human`；RIE 无显著差异但代理指标改善且 RIE 没有变差 → `kept_inconclusive`；其他 → `revert`。
  - R4 的请求带热点守卫基准（`guard_benchmarks`）：守卫基准在任何维度变差、或出现冲突，工具判 `revert`。候选版本比基线多出冲突时，工具直接判 `revert`，不跑 RIE。
- **代理降级（没有 Docker）**：`run_rie_verdict` 报 `NO_DOCKER`；O 只能看 `run_proxy_metrics` 的前后对比，改动标 `kept_inconclusive`，`confidence` 设为 `low`。打包和依赖类改动（R3）跳过。
- **AWS 模式**：`M2S_AWS=1` 时改用 `run_aws_verdict`，判据见 §2.2；本地四项检查先做完，且用 `mode="local"`。

O 只执行工具返回的 verdict，不自己改判。工具判 `pending_human` 时，存补丁、回滚，并写进报告的 `pending_human`。

### 3.3 检查没通过时的分诊

B0 是验收过的版本，所以用"和 B0 等不等价"区分问题出在代码还是测试。**分诊按工具结果机械地做，不凭改动者自己判断"测试写错了"**：

| run_judge | run_validate(delta) | run_equivalence(delta) | run_coverage(delta) | 分类 | 处理 |
|---|---|---|---|---|---|
| FAIL | — | — | — | 代码问题：裁判的期望是单体的实际行为，不存在"测试写错了" | 修一次，仍不通过就回滚 |
| PASS | FAIL | `DIFFERENT`（H1 任何一项） | 任意 | 代码问题：相对 B0 的行为变了，与测试怎么写无关 | 修一次，仍不通过就回滚 |
| PASS | FAIL | `EQUIVALENT`，基线稳定 | 任意 | 测试问题：候选版本在每个用例上都与 B0 一致，某个增量用例仍失败，说明它依赖规格没有规定的东西（调用顺序、故障落点、列表顺序） | `blocked_by_test` |
| PASS | FAIL | 失败的用例在 `unstable_in_baseline` 里 | 任意 | 测试问题（不稳定） | `blocked_by_test` |
| PASS | PASS | `EQUIVALENT` / `NOTHING_TO_COMPARE` | 有没覆盖的改动行 | 缺测试 | `blocked_by_coverage` |
| PASS | PASS | `EQUIVALENT` / `NOTHING_TO_COMPARE` | `all_covered` | 通过 | 进入性能判决 |
| — | — | `INFRA_ERROR` | — | 工具问题 | 重跑一次；仍然出错就回滚，原因写 `infra` |

- **代码问题**：`fix_attempts == 0` 时允许修一次，目标是恢复和 B0 一致的行为，不是迎合某个用例；修完从语法检查起重做全部检查。修过一次仍不通过就回滚，判 `revert`。
- **`blocked_by_test` / `blocked_by_coverage`**：存补丁（`perf/patches/REQ-n.patch`）、回滚，把证据写进报告的 `changes[].review`，继续下一个请求。**这个阶段没有人复核或重新判决这些改动**，它们留在报告里由调度器交给人。
- **`pending_spec`**：冗余读被 spec 或测试规定成必经步骤时，本轮不改代码；在报告里记 `verdict: "pending_spec"` 和 `spec_relaxation.targets`，附上代理指标测得的收益（临时改动上量完就撤回），交给人。不要自己改 spec、不要改测试、不要申请新建 GSI。
- **`DIFFERENT` 时不许自己改判**：即使认为差异来自工具局限，也只能判 `pending_human` 并在 `issues` 里写明分析。

### 3.4 按改动分档的检查量

v2 只有两档：

| 档 | 改动 | 每个改动的检查 | 整轮结束时 |
|---|---|---|---|
| **A 只改配置** | 模板里只改 `Architectures`（R5） | `run_config_verdict`：只改了模板且只改这一项、gate(template)、sam build、编译模块的架构标记；成本按单价算。**不跑**四项检查、不跑 RIE（代码没变，本地 x86 跑不出差别） | 不需要额外回归 |
| **B 只改代码** | R1–R4、R1b、R8、去冗余读等，不改规格 | 只对改过的函数：四项正确性检查 + 一个基准的 `run_rie_verdict`（或 `run_aws_verdict`） | C1：gate(final_v2) + 全量裁判回归 |

旧优化线的 C/D 档（spec 放宽、改数据模型）在 v2 不存在；这类需求只写 `pending_spec` / `advisories`。

---

## 4. 成本口径

单次调用成本（单价做成配置参数，不写死在规则里）：

```
成本 ≈ Σ DynamoDB 容量单位 × 单价 + SQS 请求数 × 单价
     + 计费时长（RIE 热调用 Duration）× 配置内存（GB）× GB-秒单价 + Lambda 每次调用费
```

- **容量单位**：由 `http-shim.js` 根据请求和响应的内容估算，**不按调用次数算**。写入按条目大小每 1KB 向上取整，事务写乘 2；读取按每 4KB 计。
  Scan 的读取量取决于我们假设的数据量（`SHIM_ITEMS` × 条目大小），这个假设要写进报告。
- **配置内存**：按 `output/template.yaml` 的实际配置；v3 的产物默认 512MB。成本维度只随 Duration 和容量变化。
- 延迟维度和成本维度都用到了 Duration，这不算重复计算，因为判决不给维度加权，只看每个维度的方向。

---

## 5. 允许和不允许的改动范围

### 5.1 等价判据（H1）

改动前后，以下两条都要满足：

- **(a) 实际执行的操作集合相同**：不能多执行，也不能少执行；
- **(b) 对外可见的失败结果相同**：返回值、抛出的错误、会被重投的消息范围都要一致；
- **(c) 基线没有冲突的地方，改动后也不能出现冲突**：冲突由冲突模拟产生（`PERF_OPTIMIZATION_DESIGN_v1.md` §2.1 第 6 项）。即使重试把冲突掩盖了、(a)(b) 都相同，线上的结果仍然取决于时序，比如重试是否会耗尽。

判定方式是增量测试的**故障注入差分**（对照 B0），不由改动者自己声明。

**实现上的两点细化**：

- **(a) 只比较"生效的调用"**：被注入故障的尝试、被后端拒绝的调用，都没有产生效果，不算进操作集合。这样 SDK 的自动重试不会让同一个操作被算两次。
- **故障固定在"同一个操作"上**：差分时先用当前工具重新运行 B0（从工作区 git 导出），记下每个故障实际落在哪个操作上（操作名、表、条目键、第几次尝试），再把同样的故障注入改动后版本里的同一个操作。
  - 原因：故障场景里的 `nth` 规则按调用顺序计数（"第 2 次调用失败"）。改成并行之后调用顺序变了，故障就会落到另一个条目上，结果看起来"不等价"。其实两个版本对同一组失败的反应完全一致。
  - 在 bookstore 的 R1 改动上，按调用顺序计数时报了假的差异；固定到操作之后，结果是 EQUIVALENT。
  - 负对照（每条消息少更新最后一本书）照样被识别出来，H1 (a)(b) 两条都报差异。
- **(a) 是"集合"**：幂等操作（读、相同的 Put、Delete）重复出现只算一次；UpdateItem（`ADD`）、消息、事务仍然每次都算。
- **handler 返回之后被放弃的请求也要算进去**：例如 `Promise.all` 失败后，兄弟请求的重试还在路上。
  - 用例结束前，工具保持故障规则生效，直到 1 秒内没有新调用（最多 10 秒），再做表快照。
  - 真实的 Lambda 会冻结这部分工作，它可能在下一次调用时继续执行，所以线上这类行为本来就不确定。这里的目标是让两个版本按同一种方式统计。
- **基线稳定性检查**：差分把 B0 跑两遍，两遍结果不一致的 run 标为 `unstable_in_baseline`，不参与判决，并且明确列出来（那些路径上的改动**没有**得到验证）。
- **差分的范围**：除了故障场景，受检函数的**全部增量测试用例**也不带场景故障地跑一遍；用例自带的故障同样固定到操作上。
  - 原因：并发方面的改动往往在普通用例上才暴露，例如一批里两条消息改同一个条目。
  - 只跑故障场景引用的用例时，不安全的全并行消费者会被判为 EQUIVALENT，因为那些用例里没有重叠的消息。

### 5.2 允许和不允许

| 改动 | 是否允许 | 条件或原因 |
|---|---|---|
| 函数内把独立的调用改成并行 | ✅ | 必须用 `allSettled` 或等价写法，失败粒度保持在单条（单条消息）级别，并满足 H1 |
| 去掉不必要的编解码或重复计算 | ✅ | 返回值完全一致 |
| 依赖精简 | ✅ | 构建通过、增量测试和裁判通过 |
| 原代码遇到失败就停止 → 改成并行 | ❌ | 执行的操作集合会变 |
| 原代码遇到失败就停止 → 只把后面的读提前并行（R1b） | ✅ | 挡在中间的提前返回必须是 `error_propagation`；越过 `authz` 的不允许 |
| 并行化可能改到同一条目的事务或条件写入 | ❌，**R4 的做法除外** | 真实 DynamoDB 会以 `TransactionConflict` 取消重叠的并发事务；冲突模拟能识别出这类改动（§5.1 (c)） |
| 按条目键分波次并行（R4） | ✅ | 同一波里的消息读写的条目两两不同；每个条目上的执行顺序与原来一致；失败粒度仍是单条消息；必须过 H1 (a)(b)(c)，并且用热点基准做守卫 |
| 跨消息按 key 聚合写入 | ❌ | 失败粒度会变粗 |
| 把多次 Put/Delete 合并成 `BatchWriteItem` | ❌ | 要处理 `UnprocessedItems`，还要逐条报告失败，复杂度超过收益 |
| 把 `UpdateItem` 合并成批量写 | ❌ | DynamoDB 不支持 |
| 事务化（`TransactWriteItems`） | ❌ → advisory | 改变原子性语义，写入容量消耗翻倍 |
| 新增 GSI | ❌ → advisory | v2 没有新建 GSI 的流程；架构级改动只写进 `advisories` |
| 其他数据模型改动（换表结构、物化视图） | ❌ → advisory | architect 级别的改动 |
| 异步机制改动（SQS 换 Streams） | ❌ → advisory | architect 级别的改动 |
| 架构改成 arm64（规则 R5） | ✅ | 只改模板 `Globals.Function.Architectures`；全量检查通过；构建产物里没有编译模块（有的话判 `pending_human`）。本地测不出 arm64 的速度，成本按 arm64 单价计（§7.2 R5） |
| 其他配置类改动（MemorySize、SQS 参数） | ❌ | 保持模板原样；MemorySize 要靠真实 AWS 的 Power Tuning |
| 去掉 `implementation-trace` 条目对应的冗余读（contract 2 的应用） | ✅ | 条目在 blueprint 里已标为 `implementation-trace`；仍要过 H1 (a)(b)(c)、覆盖率和判决。H1 (a) 本来就把读操作当集合比较 |
| 冗余读被 spec 或测试规定成必经步骤（contract 1 的应用，或漏标的条目） | ❌ → `pending_spec` | 本轮不改代码；在报告里记 `pending_spec`，交给人 |

不允许的改动如果被诊断出来，写进 `perf_report.json` 的 `advisories`，附上诊断数据。它们不参与判决。

---

## 6. 改动的组织方式

- **W1（采用）**：
  - 不同函数、不同文件的改动，可以在同一轮里一起做、一起测。RIE 按函数单独起容器，不同函数的测量互不干扰，各自判决、各自回滚。
  - **同一个函数内的多处改动，拆开顺序做**：改一处 → 判决 → 保留或回滚 → 再改下一处。这样台账才能按规则分别计数，也能避免"一个指标变好，掩盖了另一处变差"。
  - **共享模块或 layer 的改动**当作一个整体改动，所有引用它的函数都要过判决。
  - 顺序做的改动，测到的是"**在已保留的改动基础上**"的效果。每个改动都要记录它是在哪些已保留改动的基础上判决的（`based_on`）。
- **W3（备选，觉得太耗时时启用）**：同一个函数内的多处改动先合在一起测。总体判决通过，就用代理指标分别估计各自的贡献；总体不通过，再拆开逐个排查。代价是台账的计数变成近似值。

---

## 7. 候选改动的来源：规则库 + 数据驱动分析

### 7.1 两个来源，同一套判据

- **规则库**：按规则扫描代码，产出候选改动。
- **数据驱动分析**：O 先测基线，找出热点（顺序深度最大、CPU 最高、成本随数据量增长的函数），然后读代码提出假设。
- 两个来源的候选改动**都必须通过 §3 的判决**才会保留。

### 7.2 规则库

| 规则 | 识别条件 | 改写方式 | 判决依据 |
|---|---|---|---|
| **R1** | 循环里对互相独立的操作顺序 `await`，并且原代码对每条都 try/catch、失败后继续 | 改用 `allSettled` 并行，最后汇总结果 | 顺序深度 + RIE |
| **R2** | 同一个请求体被重复解析 | 只解析一次 | `warm_cpu.js` + RIE |
| **R3** | `package.json`（Python 是 `requirements.txt`）里声明了、但代码没有引用的依赖 | 删掉这个依赖 | RIE init。预计会测出"无效"：没被 `require()` 的依赖运行时根本不会加载，只影响部署包大小。可以用它检验台账机制本身能否正确记录"无效" |
| **R4** | 批处理（典型是 SQS 消费者）逐条 `await` 每条消息的处理；每条消息的写入可能与别的消息改到同一个条目（事务、条件写入），失败按单条处理 | 按条目键分波次；写法见 `agents/m2s-perf-optimizer.md` 的 R4 一节 | 顺序深度 + RIE；热点基准做守卫（`guard_benchmarks`），不得在任何维度变差、不得增加冲突 |
| **R5** | 模板的 `Globals.Function`（以及各函数自己）没有 `Architectures: [arm64]`；运行时是 Python 或 Node.js | 在 `Globals.Function` 加 `Architectures: [arm64]`，代码不动 | `run_config_verdict`（§3.4 A 档）；构建产物里有编译模块（`.so`/`.pyd`/`.node`）时判 `pending_human` |
| **R6**（只出 advisory） | blueprint 选了 `api_type: "rest"`，但没有用到任何 REST 独有功能 | 不改模板；写 advisory：HTTP API 的请求单价差、实测参考、验证办法（`e2e_ec2.py` 的 HTTP 变体做真实 A/B） | 不参与判决 |
| **R1b** | 函数里有两段以上串行的读，中间隔着一个 `error_propagation` 的提前返回；后面那次读的参数不依赖前面读到的结果 | 把读一起发出，再按原顺序检查结果；写法见 `agents/m2s-perf-optimizer.md` 的 R1b 一节 | H1 + 覆盖率 + RIE；`speculative_reads` |
| **R8** | blueprint 有接受的 `bounded_staleness` SI，`applies_to` 含这个函数；函数对这些表的读在热路径上 | 模块级 TTL 缓存，写法见 `agents/m2s-perf-optimizer.md` 的 R8 一节 | 覆盖率 + RIE；基准要写命中率假设 |

### 7.3 规则台账

- **位置**：`m2s-mcp-server/perf-rules/ledger.json`，放在仓库里，跨应用积累。
- **按应用、按规则分别记录**：命中数（匹配到的代码位置数）、保留数、无显著差异数、权衡型数。
- **数据驱动分析发现的新模式**：经实测判为有效的，记进台账作为**候选规则**。升级为正式规则需要人工审核，不会自动进入规则库。

---

## 8. O 的定义框架

O **自己改代码**。"验"和"判"调用裁判线/测量线的 MCP 工具完成，不由它自己声明。

### 8.1 输入

- 工作区（git tag `B0`）；
- 冻结的裁判 `judge/`，增量测试 `verification/delta/`；
- `storage/analysis_report.json`、`storage/blueprint.json`、`storage/adapter_map.json`、`storage/rulings.json`；
- 规则库（§7.2）、台账（只读，用来参考历史结果）；
- 环境信息：有没有 Docker、单价配置、`M2S_AWS` 开关。

### 8.2 工作流程

1. **前置检查**：`WS/.git` 存在、tag `B0` 存在、`output/` 没有未提交的改动。
2. **写基准**：在 `perf/benchmarks/` 写基准事件，提交；之后不许再改。R4 候选要写正常基准和热点守卫基准。
3. **测基线**：`run_proxy_metrics`。
4. **生成候选改动**：规则库扫描 + 数据驱动分析，每个候选写成修改请求（§8.4）。按 §5 分类，不允许的写进 `advisories`；被 spec 规定的冗余读写 `pending_spec`。
5. **排序**：按代理指标的改善幅度排序。
6. **逐轮执行**（按 §6 组织）：修改代码 → `gate(final)` → 四项正确性检查 → 分诊（§3.3）→ 性能判决（§3.2）→ 保留则 `git commit`，否则回滚/存补丁。
7. **收尾**：全量 `run_judge()` + `run_validate(assets="verification/delta")`；更新台账；写 `perf_report.json` 和 `storage/stage_reports/O.json`。

### 8.3 输出

`output/perf_report.json`：

```json
{
  "confidence": "normal | low",
  "judged_with": "aws（仅 M2S_AWS=1 时）",
  "environment": {"docker": true, "latency_ms": [10], "prices": {"...": "..."}, "data_volume_assumptions": {"...": "..."}},
  "baseline": {"<function>": {"calls": 0, "depth": 0, "warm_cpu_ms": 0, "rie_warm_ms": 0, "init_ms": 0, "cost_usd": 0}},
  "changes": [
    {"id": "C1", "request": "<修改请求 id>", "source": "rule:R1 | analysis", "functions": ["..."],
     "based_on": ["<之前已保留的改动 id>"],
     "verdict": "kept | reverted | pending_human | kept_inconclusive | blocked_by_test | blocked_by_coverage | pending_spec",
     "fix_attempts": 0,
     "review": {"blocked": "test | coverage", "cases": ["..."], "evidence": {}, "patch": "perf/patches/REQ-n.patch"},
     "spec_relaxation": {"ruling": "pending", "targets": ["..."]},
     "evidence": {"proxies": {}, "rie": {"10ms": {}, "0ms": {}, "5ms": {}, "15ms": {}}, "init": {}, "cost": {},
                  "judge": {}, "equivalence": {"assets": "verification/delta", "status": "EQUIVALENT"}, "regression": {},
                  "coverage_of_changed_lines": {}, "aws": {"verdict": "kept", "dimensions": {}, "guards": {}, "cost": {}, "saved_to": "..."}},
     "commit": "<sha>"}
  ],
  "pending_human": [{"change": "C2", "reason": "...", "patch": "<git diff>"}],
  "advisories": [{"diagnosis": "...", "why_not_v2": "...", "evidence": {}}],
  "uncovered_paths": ["..."]
}
```

另外输出 `storage/stage_reports/O.json`，格式和其他阶段的工作报告一样，另外写明 kept / reverted / pending_human / blocked_by_test / blocked_by_coverage / pending_spec 各多少个，以及 `blocked` 列表（REQ id、类型、用例或未覆盖的行、补丁路径）。

### 8.4 修改请求的格式

```json
{
  "id": "REQ-1",
  "source": "rule:R1 | analysis",
  "target": {"file": "...", "function": "...", "lines": [0, 0]},
  "diagnosis": {"metric": "depth", "value": 30, "evidence": "..."},
  "proposal": "...",
  "expected": {"depth": "30→1", "calls": "=", "warm_cpu": "≈"},
  "constraints": ["H1a", "H1b", "failure-granularity: per-message"],
  "semantic_class": "allowed",
  "benchmark": "batch-10x3",
  "guard_benchmarks": ["hot-batch-10x1"]
}
```

`guard_benchmarks` 只在 R4 的请求里出现。`spec_relaxation` 只在 `pending_spec` 的记录里出现（不在请求里执行）：

```json
"spec_relaxation": {
  "targets": ["<METHOD> <path>#responses[i]", "<METHOD> <path>#business_rules[j]"],
  "why_trace": "为什么这是单体的实现轨迹，而不是业务需求",
  "allowed_outcomes": "放宽后允许的外部结果（状态码 / 表状态 / 消息）",
  "evidence": {"calls": "a -> b", "depth": "a -> b", "measured_by": "run_proxy_metrics on a throw-away edit"}
}
```

限制：只能针对 `implementation-trace` 性质的条目；涉及写入、消息内容、权限判定、单条消息失败粒度的条目不能申请，写进 advisories；必须先在临时改动上量出收益（量完撤回，不提交）。

### 8.5 禁止事项

- 不改 `output/lambdas/` 的目录结构，不改 `Handler`、`CodeUri`、事件定义这类影响正确性的字段；
- 不做 §5.2 里不允许的改动；
- 不修改裁判、增量测试和故障场景；
- 不新建 GSI，不修改 spec，不走 spec 放宽流程；
- 不做真实 AWS 部署（`M2S_AWS=1` 除外）；
- 报告里只写直接测得的量（例如"顺序深度 30→1、调用次数不变、热调用 Duration 从深度 30 组落到深度 1 组"），**不写"预计线上提升 X ms / X%"**。

### 8.6 Python 函数（`python3.12`）

基准里有 3 个 Python（Flask）应用，迁移后是 `python3.12` 的 Lambda。优化线对 Python 的支持如下：

- **测量垫片**：`verify-tools/py/m2s_perf_shim.py` 替换 `botocore.httpsession.URLLib3Session.send`，位置和 Node 垫片替换的 `NodeHttpHandler.handle` 相同。
  - botocore 自己的序列化、签名、重试和解析照常运行；
  - 延迟模型、按夹具数据计算响应、容量单位、冲突模拟，都与 Node 版一致；
  - 冲突模拟由 `m2s_tx_conflicts.py` 实现，这是 `tx-conflicts.js` 的移植，加了锁，用同一组用例检查两者结果一致。
- **代理指标**：在本机 Python 3.12 里运行（conda 环境 `m2s-py312`，装了和 Lambda 相同版本的 boto3/botocore 1.35.92）。
- **RIE**：使用官方镜像 `amazon/aws-lambda-python:3.12`（Python 3.12.7）。
  - 垫片通过 `AWS_LAMBDA_EXEC_WRAPPER` 加载，不能用 `PYTHONPATH`：一旦设置了 `PYTHONPATH`，Lambda 的 bootstrap 就不再把层的路径（`/opt/python...`）加进 `sys.path`，层就导入不了；
  - 容器里同时设置 `AWS_REGION` 和 `AWS_DEFAULT_REGION`，botocore 读的是后者，真实 Lambda 两个都会设置。
- **顺序深度改在第二次调用上测**（Node 和 Python 都改了）。第一次调用要做一次性的准备工作，例如建线程池、每个线程建自己的 resource，这会把调用错开。实测 Python R1 在第一次调用上测得深度 8→3，热调用上是 8→1。
- **R1、R4 的 Python 写法**：见 `agents/m2s-perf-optimizer.md` 的 Python 一节（模块级线程池、每线程自己的 resource、单条消息的波次直接在当前线程执行）。实测（测试工作区；RIE 10ms，5 轮中位数）：

  | 改动 | 结果 |
  |---|---|
  | R4 分波次，SQS 消费者，一批 10 条 | `kept`：[118.4..119.0] → [22.3..28.6]ms；5ms 与 15ms 下同样更快；0ms 没有显著差异；热点守卫基准没有差异 |
  | R1 并行删除 8 个条目 | `kept`：[93.6..94.5] → [17.8..20.1]ms；init 没有差异 |
  | 全部消息无条件并行（不安全） | 热点基准 18 次冲突，1 条消息被上报重投；等价性差分报 H1 (c) |

---

## 9. 测量窗口的纪律

沿用 `PERF_OPTIMIZATION.md` §10.1 的教训：

- 每个 pass 先等到下一个整分钟再开始（`minute_gap`），相邻 pass 不共享 DynamoDB 的分钟桶；
- Lambda 指标取自各函数日志里的 REPORT 行、按 RequestId 去重、以 `until=下一个 pass 开始` 截断；
- DynamoDB 容量同时统计 GSI；
- SQS 请求按“发送 + 2×接收”（接收后假设删除一次）估算；
- 判决用的 Duration 只算热调用；成本用 REPORT 的 billed ms（含 init）；
- 所有窗口都在负载结束后统一等待 90 秒再读取。

AWS 判决的 ABAB 测量、`aws-load-client` 锁和 B0 噪声校准见 §2.2。

---

## 附录 A：仍然有效的 v1 实验证据

> 完整的 v1 附录见 `PERF_OPTIMIZATION_DESIGN_v1.md` 附录 A。这里只保留对 v2 判决仍然有参考价值的部分；v1 附录 A.2（sam CLI + Docker 阶段实测）已作废（代理配置错误，测到的是失败请求），不在这里重复。小节编号沿用 v1 的 A.1 / A.3 / A.4，方便旧引用直接对应。

### A.1 两个反模式的实测差异（2026-09-23，bookstore 基准，代码阶段代理指标）

用 bookstore-oh（真实跑完 P0-S4、`gate(final)` PASS 的产物）实测批量 API 反模式的两个真实实例，mock 层给每次 DynamoDB/SQS 调用加固定 8ms 延迟模拟单次网络往返（明确是代理值，不是真实网络延迟）：

| handler | exec_ms | SDK 调用次数 | 调用序列 |
|---|---|---|---|
| `bestsellers-update-consumer`（SQS 消费者，3 本书） | 47.68 | 3 | `UpdateCommand` ×3，**顺序 await，无并行** |
| `orders-create`（结账，3 本书购物车） | 47.49 | 5 | `PutCommand` → 3×`DeleteCommand`（**`Promise.all` 并行**）→ `SendMessageCommand` |
| 其它 10 个单次调用的 handler | 11~24 | 1 | 单条 `Get`/`Put`/`Query`/`Scan`/`Delete`/`Update` |

**发现 1**：两个反模式的"贵法"不一样。`bestsellers-update-consumer` 是完全顺序的循环，耗时和调用次数都跟 N 线性叠加（47.68ms ≈ 3 × 单次调用耗时）——并行化能同时省时间和请求数。`orders-create` 的 3 个 `DeleteCommand` 已经用 `Promise.all` 并行，47.49ms 的主要构成是"Put → 并行 Delete → SendMessage"三个顺序阶段；把 delete 合并成 `BatchWriteCommand` 能降请求次数，但墙钟耗时的改善明显更小。

**发现 2**：批量 API 规则要按操作类型分诊。`bestsellers-update-consumer` 用的是 `UpdateCommand`（DynamoDB 原子 `ADD salesCount`），这类操作没有 `BatchWriteItem` 等价物；正确的优化是"循环改并行"，不是"改批量 API"。

### A.3 真实 AWS 部署实测（2026-09-23，一次性参考基准，测完已完全拆除）

按一次性、人工授权、测完即拆的例外条款，部署了一次 `bookstore-oh` 到账号 `882112174488`，测完立刻 `sam delete` + 手动删掉 `DeletionPolicy: Retain` 的 4 张 DynamoDB 表，已核实栈、表、函数全部不存在。这份数据填补了 sam+Docker 本地测量法测不出的信号——真实 Lambda 的 `REPORT` 日志行直接给 `Init Duration`/`Duration`。

**T_cold_start（真实 `Init Duration`）**：三个函数都在 300~385ms 这个窄区间，跟函数内部 SDK 调用次数几乎无关（1 次 vs 3 次 vs 5 次调用差不多）——冷启动成本主要来自 Node.js 运行时引导 + 加载 SDK 模块本身，不是业务逻辑决定的：

| 函数 | Init Duration |
|---|---|
| books-list（1 次调用） | 341.17ms |
| orders-create（5 次调用） | 384.63ms |
| bestsellers-update-consumer（3 次调用） | 309.15ms |

**T_warm_execution（真实 `Duration`，热启动）**：

| 函数 | warm Duration | 相对 books-list |
|---|---|---|
| books-list（1 次 `QueryCommand`） | ~8.9ms（6.92~12.78） | 1× |
| bestsellers-update-consumer（3 次顺序 `UpdateCommand`） | ~19~36ms（干净热启动区间，另有几次 94~105ms，疑似并发扩容触发的额外冷启动） | ~2~4× |
| orders-create（Put+3×并行Delete+SendMessage，5 次调用） | ~45.25ms（25.38~65.45） | ~5× |

这组数字验证了本地测量法完全测不出的东西：函数内部 SDK 调用模式的真实差异（5 次 vs 1 次调用）在真实 AWS 上是约 5 倍的热执行耗时差异，跟本地 mock 代理指标的方向一致（当时 mock 测的 47ms vs 12~24ms 也是几倍关系），说明代理指标这条路线本身是可信的。

**E2E Latency（真实，走 API Gateway + 真实 Cognito JWT 校验）**：

| 端点 | cold | warm（4 次均值） |
|---|---|---|
| `GET /api/books` | 1595ms | ~916ms（841~1064） |
| `POST /api/orders`（结账） | 1761ms | ~903ms（843~953） |

热启动时 Lambda 自己只占几毫秒到几十毫秒，但 E2E 测出来是 ~900ms——绝大部分时间（~850~1050ms）花在 API Gateway + Cognito Authorizer 校验 + 这台机器到 us-east-1 的网络往返上，不是 Lambda 本身。这给 HTTP API 的评估提供了方向性数据，但 E2E 没有分解过，不能归因到单个组件。

### A.4 MemorySize / Architectures 真实 A/B 实测（2026-09-23，独立实验栈，测完已拆除）

用 bookstore-oh 真实的 `orders-create` 源码（未做任何改动），做成 5 个只在 `MemorySize`/`Architectures` 上不同的独立 Lambda 函数，部署到一个专门的临时栈（`m2s-perf-ab-test`，跟主栈完全分开），直接 `lambda:Invoke` 各调 4 次（1 次冷 + 3 次热），测完立刻 `sam delete`。

**MemorySize（warm Duration，3 次热调用均值）**：

| MemorySize | warm Duration 均值 | Init Duration（冷） |
|---|---|---|
| 128 MB | ~173.7ms（256.74 / 136.05 / 128.22） | 342.88ms |
| 512 MB | ~35.3ms（55.42 / 25.89 / 24.57） | 326.62ms |
| 1024 MB | ~24.3ms（30.03 / 22.49 / 20.27） | 321.33ms |

128MB → 512MB 快了约 5 倍，512MB → 1024MB 再快约 31%（边际收益明显递减）。`Init Duration` 三档几乎不变（321~343ms）——内存大小基本不影响模块加载/运行时引导耗时，只影响 handler 内部代码的实际执行速度。**128MB 实测明显偏慢**，v3 的模板默认不低于 512MB。

**Architectures（warm Duration，同样 3 次热调用，前两次还在收敛，看最后两次更稳）**：

| Architecture | warm Duration（后两次均值） | Init Duration（冷） |
|---|---|---|
| x86_64 | ~27.5ms（28.78 / 26.31） | 287.91ms |
| arm64 | ~23.0ms（27.12 / 18.80） | 273.44ms |

arm64 的 warm Duration 和 Init Duration 都不劣于 x86_64（甚至略快），这次是真实 Graviton 硬件上测的。R5 的 arm64 建议有真实数据支撑。

**没做的部分，如实说明**：SQS `BatchSize`/`MaximumBatchingWindowInSeconds` 这次没有纳入真实 A/B 实验（本地完全测不了，真实 AWS 这次时间上没排上），留给后续补。
