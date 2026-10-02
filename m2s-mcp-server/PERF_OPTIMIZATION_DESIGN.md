# M2S 性能优化设计方案（v2：优化线）

> 状态：设计稿，未实现。2026-09-24 在原文件上重写。
>
> v1 设计稿（2026-09-23）里有不少内容已被实验推翻（见 §0.1）。v1 全文保留在 git 历史里（`869c4bd` 及之前），其中的**实测记录**原样移到了本文附录 A。
>
> 配套文档：
> - `PIPELINE_REDESIGN.md`：流水线按三条线（实现、验证、优化）重构，本文只写其中的**优化线**；
> - `PERF_OPTIMIZATION.md`：对照官方参考实现，以及支撑本设计的实验数据（测量层级的定义见其 §1）。

## 0. 背景与范围

### 0.1 v1 设计稿里被推翻或修改的内容

| v1 的内容 | 现在的结论 | 依据 |
|---|---|---|
| perf-optimizer 放在 S3c 之后、validator 之前 | 改为 v0 验收通过**之后**的 S5 | `PIPELINE_REDESIGN.md` §1 |
| 本地测量用 sam CLI + Docker（`sam local`） | 改用 L2′（`sam build` 后直接在 Lambda 运行时容器里读 `REPORT`）。v1 的 sam+Docker 数据是在代理配置错误的环境下测的，已作废 | `PERF_OPTIMIZATION.md` 实验 6、7，§1.3 |
| 判据是"墙钟指标变好 + 其他指标在 ±10~15% 噪声带内" | 改为：代理指标只用来定位和排序，最终判决看 RIE 的热调用 Duration + init + 成本（§3） | 同上 §1.2 |
| `Architectures: arm64` 作为默认值；MemorySize 按 `perf_hint` 分档 | v1 保持原始实现：x86_64、512MB，去掉 `perf_hint`（§5） | L3 数据只来自一个函数（`PERF_OPTIMIZATION.md` §6.1） |
| 性能知识同时注入三个上游 agent | 先只注入 perf-optimizer 跑出基线，再用消融实验逐条决定哪些规则前移到上游（§8） | — |
| perf-optimizer 自己改、自己测 | 改动由 perf-optimizer 做，**判定**由验证线的工具做（§6、§9） | — |
| 批量 API 改写是主要手段 | v1 只允许语义保持的改动；`BatchWriteItem` 合并和跨消息聚合都不允许（§6） | — |
| `ReportBatchItemFailures` 算作性能改进 | 改为上游的正确性规则（消费者保证重投幂等） | `PIPELINE_REDESIGN.md` §2.4 |
| 不做真实 AWS 部署 | **不变**。流水线内不部署；真实 AWS（L3）只用于流水线之外的人工校准实验 | — |

### 0.2 v1 的目标

对已经验收通过的 v0，只做**语义保持**的性能改动。每个改动都必须通过实测判决才会保留；证据不足的退回，或者交给人决定。不追求改动的数量。

---

## 1. 在流水线里的位置

S5 在 v0 验收通过之后运行（见 `PIPELINE_REDESIGN.md` §1）：

```
阶段 4 验收通过 → git tag v0 → S5 perf-optimizer → 阶段 6 最终检验（gate(final) + 全量回归）
```

- S5 报 `STAGE_BLOCKED` **不会**让流水线失败。它只表示"性能优化没有收敛"，交付的仍是最后一个通过验证的版本。
- 所有被保留的改动都在 v0 的基础上逐个提交到 git。回滚就是撤销对应的提交。

---

## 2. 可用的测量手段

测量层级的定义和各层的噪声水平，统一以 `PERF_OPTIMIZATION.md` §1 为准，这里只列 S5 会用到的部分：

| 指标 | 工具 | 在判决中的作用 |
|---|---|---|
| SDK 调用次数、顺序深度 | `run_proxy_metrics` | 代理指标：定位问题、给改动排序；决定一个改动是否属于权衡型（§3.2） |
| 热执行 CPU（`warm_cpu.js`） | `run_proxy_metrics` | 同上 |
| 容量单位估算 | `run_proxy_metrics`（由 `http-shim.js` 根据请求和响应内容估算） | 成本维度的输入（§4） |
| RIE 热调用 Duration | `run_rie_verdict` | **延迟维度的最终判决** |
| RIE init | `run_rie_verdict` | **冷启动维度的最终判决** |
| 回归测试、故障差分、覆盖率 | `run_validate`、`run_equivalence`、`run_coverage` | 一票否决（§3.1） |
| RSS | `run_validate` | 内存护栏，一票否决 |

### 2.1 模拟延迟模型（v1：每次调用固定延迟）

**加在哪一层**：`http-shim.js` 替换了 AWS SDK 最底层的 HTTP 发送（`NodeHttpHandler.prototype.handle`）。
每次 SDK 调用走到这一步时，先等待 `SHIM_LATENCY_MS` 毫秒，然后返回伪造的 200 响应，不走网络。
一次 `send()` 的完整路径如下：

```
handler 代码
 → DocumentClient 编码 → 序列化成 HTTP 请求 → 重试中间件 → SigV4 签名      （真实执行，耗 CPU）
 → NodeHttpHandler.handle：等待 LAT 毫秒 + 返回伪造的响应                   （替代整段网络往返）
 → 反序列化 → DocumentClient 解码                                           （真实执行，耗 CPU）
 → 回到 handler
```

所以 RIE 测到的热调用 Duration 由两部分组成：
- **SDK 前后两段的 CPU 开销**：真实发生，在 `--cpus` 限制下会按比例变慢；
- **每次调用固定 LAT 毫秒的等待**：用 `setTimeout` 实现，不占 CPU，所以不受 CPU 配额影响。容器里是 Linux，计时是精确的。

**v1 的已知局限**（这是有意保持的简化，不是 bug）：

| # | 局限 | 真实情况 | 对测量结果的影响 |
|---|---|---|---|
| 1 | 延迟和数据量无关 | 传输大请求或大响应本身需要时间 | Scan 返回大量数据时的代价被**低估**：随数据量增长的只剩反序列化的 CPU 开销 |
| 2 | 并行调用没有上限 | 受连接池大小、DynamoDB 分区吞吐的限制 | 并行化（R1）的收益是**理想化**的：N 个并行调用只花约 1 个 LAT |
| 3 | 没有建立连接的开销 | 冷启动后的第一次调用要先做 TCP 和 TLS 握手，大约几十毫秒 | 冷调用和首次调用的耗时被**低估**；热调用基本不受影响（SDK v3 默认复用连接） |
| 4 | 没有抖动、限流和重试 | 真实往返时间有波动；会遇到限流，并触发重试 | 测不到长尾延迟；重试中间件永远不会触发 |
| 5 | 所有操作的延迟都一样 | 写入和事务通常比读取慢 | 读写比例不同的改动之间，比较会有偏差 |
| 6 | 模拟器的并发语义（DynamoDB Local 串行执行事务） | 真实 DynamoDB 对重叠的并发事务返回 `TransactionConflict` | 并行化事务的改动在等价性检查和测试里都"没问题"，实际却可能丢更新：这是正确性盲区，不只是计时偏差。**2026-09-27 起由冲突模拟补上**（见下文） |

**第 6 项的补救：冲突模拟**（2026-09-27，`verify-tools/tx-conflicts.js`）
- 验证代理（`controller.js`）和测量垫片（`perf-shim.js`）按 AWS 开发者指南"Transaction conflict handling"的规则拒绝请求：
  - 单条 Put/Update/Delete 碰到进行中的 `TransactWriteItems` 的条目，返回 `TransactionConflictException`；
  - `TransactWriteItems` 碰到另一个进行中的 `TransactWriteItems` 的条目，返回 `TransactionCanceledException`，冲突条目的原因是 `TransactionConflict`；
  - `TransactGetItems` 碰到进行中的任何写入的条目，结果同上。
  - 被拒绝的是后到的请求，先到的照常完成。
- "进行中"的时长：
  - 验证代理里，写事务至少保持 25ms（真实事务要几十毫秒），这样并发发出的重叠请求能被稳定地识别出来，不取决于 DynamoDB Local 的处理速度；
  - 测量垫片里，就是请求等待模拟延迟的那段时间。所以 0ms 延迟时不会出现冲突。
- 没有模拟的情况：`BatchWriteItem` 自身的条目碰到进行中的事务（指南没有说明会怎样）。
- 工具上的体现：
  - `run_validate` 的 `cases_with_conflicts`；
  - `run_equivalence` 的 H1 (c)（§6.1）；
  - `run_proxy_metrics` 的 `conflicts`；
  - `run_rie_verdict` 的冲突否决：候选版本的冲突次数多于基线时，直接判 `revert`。
- 实测（bookstore-v2 的 SQS 消费者，一批 10 条消息，每条消息一个事务）：
  - 全部消息无条件并行（不安全的写法）：键不相交的基准上完全看不出问题。热点基准（每条消息都含同一本书）上，10ms 延迟下有 21 次冲突，2 条消息重试耗尽被报告重投，WRU 从 80 升到 232。等价性差分报 H1 (c)，判决直接否决；
  - 按 R4 分波次执行：两个基准上都没有冲突。键不相交基准的顺序深度 10→1，RIE 热调用 5 轮中位数从 [123..125]ms 降到 [29..59]ms，判决 `kept`；热点基准与基线没有显著差异。

**判决时怎么对待这些局限**：
- 它们在改动前后同样存在，做前后对比时大部分会相互抵消；
- **抵消不了的情况**：改动恰好改变了上表中的某一项。例如：
  - 把多次小调用合并成一次大调用，受第 1、5 项影响；
  - 把串行改成并行，受第 2 项影响；
  - 把初始化代码挪到模块顶层，受第 3 项影响。

  遇到这类改动，要在 `perf_report.json` 里对应改动的 `evidence` 中注明受哪一项局限影响，以及影响的方向（高估还是低估收益）；
- §3.2 第 2 步对权衡型改动测 0/5/15ms 三个值，覆盖的只是"单次往返有多长"这一个维度，不覆盖上表这几项；
- v1 允许的改动里，**R1（改成并行）直接受第 2 项影响**。它的判决结论只代表"在并发不受限的条件下"成立，报告里要写明这一点。

**以后可以考虑的改进**（v1 不做，因为这些参数目前没有真实数据可以校准，L3 只有 bookstore 的少量数据）：
- 按请求和响应的字节数加一段传输时间（固定延迟 + 字节数 ÷ 带宽）；
- 按操作类型分别设置延迟；
- 加随机抖动；
- 限制最大并发数。

这些改进要等 L3 有了按操作类型和数据大小区分的真实往返时间之后，再用来校准参数。

---

## 3. 判决规则

### 3.1 一票否决

以下任何一项不通过，改动都不能保留。不通过时先按 §3.6 分诊，再决定是修代码、交给验证线复核，还是回滚：

1. **有效性校验**：事件确实走到了预期的代码路径（结果符合预期、SDK 调用次数和预期一致）。
2. **正确性 gate**：`gate(final)`。
3. **验证线阶段 B**：回归测试全部符合预期；故障差分满足 H1 的 (a)(b)(c) 三条（§6.1）；改动碰到的代码行被测试覆盖到。没覆盖到的，先请验证线补事件；补完还是覆盖不到，就回滚。
4. **RSS 内存护栏**：实际内存占用接近配置的 MemorySize 时，判定为有风险。

### 3.2 性能判决流程

代理指标（调用次数、顺序深度、`warm_cpu.js`、RIE init 的单次数值）**只用来定位和排序**，不能单独决定一个改动的去留。最终判决看总体效果。

```
第 1 步：先用 10ms 模拟延迟筛一遍（改动前后交替运行）
  ├─ 没有任何代理指标变差，且 RIE 结果"明显变好" → 视为绝对型改动，进入第 3 步
  ├─ 有任何一个代理指标变差（调用次数、顺序深度、warm_cpu） → 视为权衡型，进入第 2 步
  └─ RIE 结果变差 → 进入第 2 步，确认是不是延迟参数造成的

第 2 步：多值测量，模拟延迟取 0 / 5 / 15ms
  ├─ 真实延迟规则（2026-09-30 用户裁定）：5 / 10 / 15ms 全部明显变好，且热调用 CPU 中位数不超过基线的 2 倍
  │    → 延迟维度判为"改善"，0ms 只作参考，不参与判决
  ├─ 在任何一个值下都不变差，且至少一个值下变好 → 延迟维度判为"改善"
  ├─ 结论随延迟参数反转 → 标注"依赖真实往返时间"，作为权衡型交给人
  └─ 其他情况 → 延迟维度判为"变差"或"无显著差异"

第 3 步：三个维度综合判决（热调用 Duration、init、成本）
  ├─ 三个维度都不变差，且至少一个明显变好 → 保留
  ├─ 有的变好、有的变差 → 权衡型：回滚，写进"待人工决策"（§3.4）
  ├─ RIE 结果"无显著差异"，但代理指标显示改善，且 RIE 没有变差 → 保留
  └─ 其他情况（没有一个维度明显变好） → 撤回
```

备选方案：只用一个固定的模拟延迟值做判决，跳过第 2 步。

**为什么 0ms 只作参考**：真实 DynamoDB 一次往返要几毫秒，0ms 只能测出改动的固定开销（例如线程池），没有可以重叠的等待时间。R1 并行化（shopping-cart 的 REQ-1、ecommerce 的 REQ-1/2/3）四次都是同一种形状：0ms 变慢，5/10/15ms 明显变快，每次都要人工裁定，结论都是保留。所以改成上面的规则：
- CPU 上限 `CPU_RATIO_LIMIT = 2.0`，是按已有数据取的：这几次的比值在 1.25–1.84 之间。
- 成本维度一律用 10ms 那一轮计算；以前用的是传入的第一个延迟值，传 `[0, 5, 10, 15]` 时就按 0ms 算了成本。
- 判决结果里的 `realistic_latency_rule` 记录这条规则是否生效、0ms 的方向和 CPU 比值。

### 3.3 "明显变好"和"无显著差异"的定义

- **明显变好或明显变差**：超出该层的噪声，即改动前后**交替运行**，两组结果的范围**不重叠**。
- **无显著差异**：两组结果的范围有重叠。
- **交替运行的次数**：
  - 热调用 Duration 每轮 30 次调用，至少 3 轮；
  - init 至少交替 5 次。单次运行的 init 波动很大（82~200ms），所以不能只看中位数，要求两组范围不重叠。

### 3.4 权衡型改动的处理

权衡型改动**不放进交付物**（回滚），但要写进 `perf_report.json` 的 `pending_human` 一节，内容包括：
- 各维度的测量数据；
- 结论随延迟参数反转的情况；
- 可以直接套用的补丁（`git diff`）。

### 3.5 没有 Docker 时

改用代理指标做判决：顺序深度和 `warm_cpu.js` 代替热调用 Duration；成本照常计算。报告标注"**低置信度**"。init 维度判不了，所以打包和依赖类的改动（包括 R3）**跳过**。

### 3.6 检查没通过时的分诊（2026-09-27 加，K15–K18）

**起因**：bookstore-v2 的 S5 第 3 轮，R4 的改动（REQ-2）各项都通过了（等价、无冲突、覆盖完整），却因为一个用例失败被回滚。那个用例的故障按全局调用顺序数"第 2 个事务"，改成并行之后，故障落到了另一条消息上。以前的规则是"任何一项没通过就回滚"，测试写得有问题时，正确的优化也会丢掉。

**怎么分**：v0 是验收过的版本，所以用"和 v0 等不等价"区分问题出在代码还是测试。`run_equivalence` 会跑受检函数的全部用例，故障固定在 v0 实际打中的操作上。

| gate(final) | run_equivalence | run_validate | run_coverage | 分类 |
|---|---|---|---|---|
| FAIL | — | — | — | 代码问题 |
| PASS | DIFFERENT（H1 任何一项） | 任意 | 任意 | 代码问题：相对 v0 的行为变了，与测试怎么写无关 |
| PASS | EQUIVALENT，基线稳定 | FAIL | 任意 | 测试问题：候选版本在每个用例上都与 v0 一致，某个用例仍然失败，说明它依赖规格没有规定的东西（调用顺序、故障落点、列表顺序） |
| PASS | 失败的用例在 `unstable_in_baseline` 里 | FAIL | 任意 | 测试问题（不稳定） |
| PASS | EQUIVALENT | PASS | 有没覆盖的改动行 | 缺测试 |

**谁来处理**：
- **K15**：分类由工具结果机械地决定。perf-optimizer 是改动的作者，不能自己认定"测试写错了"，也不能改测试。
- **K16 代码问题**：每个改动允许修一次。修改目标是恢复和 v0 一致的行为，修完从头重做全部检查；仍然不通过就回滚。台账分开记录"修过之后才保留"的数量（`kept_after_fix`），这样规则第一次就改对的比例仍然可以统计。
- **K17 测试问题**：
  1. perf-optimizer 存补丁、回滚，把改动标为 `blocked_by_test`，附上证据，继续下一个；
  2. S5 结束后，主 Agent 派 verifier 复核（`STAGE_ID=VR`）。verifier 按规格逐个判断：
     - 用例确实有问题，就修改它，要写明依据哪条规格，不许放宽规格要求的断言；
     - 用例没问题，就维持原样。这时改动回滚，并且说明 H1 漏掉了某种差异，要作为工具问题报告。
  3. 修改后的用例先在 v0 的代码上跑通：`run_validate(ref="v0")`。然后提交测试资产，v0 的 tag **不动**：代码没变，S5 已经保留的改动也不能被算进 v0；
  4. 主 Agent 用"重新判决模式"再派 perf-optimizer，只重新判决被阻塞的补丁；
  5. 每轮 S5 最多复核一次。重新判决时再次被阻塞，直接回滚。
- **K18 缺测试**：标为 `blocked_by_coverage`，走同一个复核流程。verifier 为没覆盖到的行补用例，预期从规格推出；规格没有涉及的分支只断言不变量。确实走不到的分支，写明原因后补不了，这个改动回滚。

---

## 4. 成本口径

单次调用成本（单价做成配置参数，不写死在规则里）：

```
成本 ≈ Σ DynamoDB 容量单位 × 单价 + SQS 请求数 × 单价
     + 计费时长（RIE 热调用 Duration）× 配置内存（GB）× GB-秒单价 + Lambda 每次调用费
```

- **容量单位**：由 `http-shim.js` 根据请求和响应的内容估算，**不按调用次数算**。写入按条目大小每 1KB 向上取整，事务写乘 2；读取按每 4KB 计。
  Scan 的读取量取决于我们假设的数据量（`SHIM_ITEMS` × 条目大小），这个假设要写进报告。
- **配置内存**：v1 固定为 512MB（§5），所以计算费用只随 Duration 变化。
- 延迟维度和成本维度都用到了 Duration，这不算重复计算，因为判决不给维度加权，只看每个维度的方向。

---

## 5. v1 的默认配置

| 配置 | v1 的取值 | 说明 |
|---|---|---|
| MemorySize | **512MB**（原始实现） | 不进入测量循环。只做下限检查：用 `--cpus` 按 512 ÷ 1769 限制 CPU，确认不会出现类似 128MB 那样的断崖（热调用出现双峰、p90 是中位数的数倍） |
| Architectures | **x86_64**（原始实现，不写这个字段） | 本机测不了 arm64（只能 QEMU 模拟），唯一的依据是 bookstore 一个函数的一次 A/B |
| `perf_hint` | **去掉** | MemorySize 不分档之后就没有用处了 |
| Tracing | `Active`（原样） | 开销从来没测过 |
| RIE 的 CPU 配额 | 各函数的 MemorySize ÷ 1769 | v1 统一是 0.289 |

---

## 6. v1 允许的改动范围

### 6.1 等价判据（H1）

改动前后，以下两条都要满足：

- **(a) 实际执行的操作集合相同**：不能多执行，也不能少执行；
- **(b) 对外可见的失败结果相同**：返回值、抛出的错误、会被重投的消息范围都要一致。
- **(c)**（2026-09-27 加）**基线没有冲突的地方，改动后也不能出现冲突**：冲突由冲突模拟产生（§2.1 第 6 项）。即使重试把冲突掩盖了、(a)(b) 都相同，线上的结果仍然取决于时序，比如重试是否会耗尽。

判定方式是验证线阶段 B 的**故障注入差分**（对照 v0），不由改动者自己声明。

**实现上的两点细化**（步骤 2，2026-09-26）：
- **(a) 只比较"生效的调用"**：被注入故障的尝试、被后端拒绝的调用，都没有产生效果，不算进操作集合。这样 SDK 的自动重试不会让同一个操作被算两次。
- **故障固定在"同一个操作"上**：差分时先用当前工具重新运行 v0（从工作区 git 导出），记下每个故障实际落在哪个操作上（操作名、表、条目键、第几次尝试），再把同样的故障注入改动后版本里的同一个操作。
  - 原因：故障场景里的 `nth` 规则按调用顺序计数（"第 2 次调用失败"）。改成并行之后调用顺序变了，故障就会落到另一个条目上，结果看起来"不等价"。其实两个版本对同一组失败的反应完全一致。
  - 在 bookstore 的 R1 改动上，按调用顺序计数时报了假的差异；固定到操作之后，结果是 EQUIVALENT。
  - 负对照（每条消息少更新最后一本书）照样被识别出来，H1 (a)(b) 两条都报差异。
- **(a) 是"集合"**：幂等操作（读、相同的 Put、Delete）重复出现只算一次；UpdateItem（`ADD`）、消息、事务仍然每次都算。
- **handler 返回之后被放弃的请求也要算进去**：例如 `Promise.all` 失败后，兄弟请求的重试还在路上。
  - 用例结束前，工具保持故障规则生效，直到 1 秒内没有新调用（最多 10 秒），再做表快照。
  - 真实的 Lambda 会冻结这部分工作，它可能在下一次调用时继续执行，所以线上这类行为本来就不确定。这里的目标是让两个版本按同一种方式统计。
- **基线稳定性检查**：差分把 v0 跑两遍，两遍结果不一致的 run 标为 `unstable_in_baseline`，不参与判决，并且明确列出来（那些路径上的改动**没有**得到验证）。
- **差分的范围**（2026-09-27 起）：除了故障场景，受检函数的**全部测试用例**也不带场景故障地跑一遍；用例自带的故障同样固定到操作上。
  - 原因：并发方面的改动往往在普通用例上才暴露，例如一批里两条消息改同一个条目。
  - 只跑故障场景引用的用例时，不安全的全并行消费者被判为 EQUIVALENT，因为那些用例里没有重叠的消息。

### 6.2 允许和不允许

| 改动 | v1 是否允许 | 条件或原因 |
|---|---|---|
| 函数内把独立的调用改成并行 | ✅ | 必须用 `allSettled` 或等价写法，失败粒度保持在单条（单条消息）级别，并满足 H1 |
| 去掉不必要的编解码或重复计算 | ✅ | 返回值完全一致 |
| 依赖精简 | ✅ | 构建通过、验证通过 |
| 原代码遇到失败就停止 → 改成并行 | ❌ | 执行的操作集合会变 |
| 并行化可能改到同一条目的事务或条件写入 | ❌（2026-09-27 加），**R4 的做法除外** | 真实 DynamoDB 会以 `TransactionConflict` 取消重叠的并发事务，DynamoDB Local 不模拟这一点（实测 300 个冲突的并发事务无一被取消）。bookstore-v2 的 S5 因此错误地保留了一个这样的改动。现在冲突模拟能识别出这类改动（§2.1 第 6 项） |
| 按条目键分波次并行（R4） | ✅（2026-09-27 加） | 同一波里的消息读写的条目两两不同；每个条目上的执行顺序与原来一致；失败粒度仍是单条消息；必须过 H1 (a)(b)(c)，并且用热点基准做守卫 |
| 跨消息按 key 聚合写入 | ❌ | 失败粒度会变粗；等上游保证幂等之后再考虑 |
| 把多次 Put/Delete 合并成 `BatchWriteItem` | ❌（v1 删掉） | 要处理 `UnprocessedItems`，还要逐条报告失败，复杂度超过收益 |
| 把 `UpdateItem` 合并成批量写 | ❌ | DynamoDB 不支持 |
| 事务化（`TransactWriteItems`） | ❌ → advisory | 改变原子性语义，写入容量消耗翻倍 |
| 数据模型改动（GSI、物化视图） | ❌ → advisory | architect 级别的改动 |
| 异步机制改动（SQS 换 Streams） | ❌ → advisory | architect 级别的改动 |
| 配置类改动（MemorySize、架构、SQS 参数） | ❌ | v1 保持原样（§5） |
| 去掉 `implementation-trace` 条目对应的冗余读（contract 2 的应用） | ✅（2026-10-02 加） | 条目在 blueprint 里已标为 `implementation-trace`；仍要过 H1 (a)(b)(c)、覆盖率和判决。H1 (a) 本来就把读操作当集合比较 |
| 冗余读被 spec 或测试规定成必经步骤（contract 1 的应用，或漏标的条目） | ❌ → 申请 | 写 `spec_relaxation` 申请，请求记为 `pending_spec`，本轮不改代码；流程见 `CONTRACT_TESTING_PLAN.md` §7 |

不允许的改动如果被诊断出来，写进 `perf_report.json` 的 `advisories`，附上诊断数据。它们不参与判决。

---

## 7. 改动的组织方式

- **W1（采用）**：
  - 不同函数、不同文件的改动，可以在同一轮里一起做、一起测。RIE 按函数单独起容器，不同函数的测量互不干扰，各自判决、各自回滚。
  - **同一个函数内的多处改动，拆开顺序做**：改一处 → 判决 → 保留或回滚 → 再改下一处。这样台账才能按规则分别计数，也能避免"一个指标变好，掩盖了另一处变差"。
  - **共享模块或 layer 的改动**当作一个整体改动，所有引用它的函数都要过判决。
  - 顺序做的改动，测到的是"**在已保留的改动基础上**"的效果。每个改动都要记录它是在哪些已保留改动的基础上判决的（`based_on`）。
- **W3（备选，觉得太耗时时启用）**：同一个函数内的多处改动先合在一起测。总体判决通过，就用代理指标分别估计各自的贡献；总体不通过，再拆开逐个排查。代价是台账的计数变成近似值。

---

## 8. 候选改动的来源：规则库 + 数据驱动分析

### 8.1 两个来源，同一套判据

- **规则库**：按规则扫描代码，产出候选改动。
- **数据驱动分析**：perf-optimizer 先测基线，找出热点（顺序深度最大、CPU 最高、成本随数据量增长的函数），然后读代码提出假设。
- 两个来源的候选改动**都必须通过 §3 的判决**才会保留。

### 8.2 v1 规则库（在 E0 之前，只放"因为结构本身就成立"的规则）

| 规则 | 识别条件 | 改写方式 | 判决依据 |
|---|---|---|---|
| **R1** | 循环里对互相独立的操作顺序 `await`，并且原代码对每条都 try/catch、失败后继续 | 改用 `allSettled` 并行，最后汇总结果 | 顺序深度 + RIE |
| **R2** | 同一个请求体被重复解析 | 只解析一次 | `warm_cpu.js` + RIE |
| **R3** | `package.json`（Python 是 `requirements.txt`）里声明了、但代码没有引用的依赖 | 删掉这个依赖 | RIE init。预计会测出"无效"：没被 `require()` 的依赖运行时根本不会加载，只影响部署包大小。可以用它检验台账机制本身能否正确记录"无效" |
| **R4**（2026-09-27 加） | 批处理（典型是 SQS 消费者）逐条 `await` 每条消息的处理；每条消息的写入可能与别的消息改到同一个条目（事务、条件写入），失败按单条处理 | 按条目键分波次：先算出每条消息会读写的条目键（从构造写入的同一个函数里取，不另写一套），每条消息排在"与它共享条目的前面消息中最晚的那一波 + 1"；波与波之间顺序执行，同一波里用 `allSettled` 并行。消息体原地包成 `async (record) => {...}`，不挪位置、不改缩进，`continue` 改成 `return` | 顺序深度 + RIE；热点基准做守卫（`guard_benchmarks`），不得在任何维度变差、不得增加冲突 |

### 8.3 规则台账

- **位置**：`m2s-mcp-server/perf-rules/ledger.json`，放在仓库里，跨应用积累。
- **按应用、按规则分别记录**：命中数（匹配到的代码位置数）、保留数、无显著差异数、权衡型数。
- **数据驱动分析发现的新模式**：经实测判为有效的，记进台账作为**候选规则**。升级为正式规则需要人工审核，不会自动进入规则库。

---

## 9. perf-optimizer 的定义框架

v1 由 perf-optimizer **自己改代码**。"验"和"判"调用验证线的 MCP 工具完成，不由它自己声明。

### 9.0 实现（步骤 2）

- **工具**：`run_proxy_metrics`、`run_rie_verdict`、`update_ledger`（`perf.py`），外加 `run_coverage(changed_since=...)` 检查改动行是否被测试执行到。
- **测量**：
  - `verify-tools/perf-shim.js` 按 §2.1 替换 SDK 最底层的 HTTP 发送，响应根据基准事件的表数据计算：GetItem/BatchGetItem 按键查找，Query 按键条件过滤，Scan 返回整表，写操作不改数据；
  - 代理指标在本机 Node 里测，热 CPU = 零延迟下每次调用的墙钟时间。Windows 时钟粒度 15.6ms，`process.cpuUsage` 在这个尺度上不能用；
  - 判决在 RIE 里测：改动前后交替 5 轮，每轮 1 次冷调用 + 30 次热调用，CPU 配额 = MemorySize ÷ 1769。
- **基准事件由 perf-optimizer 写**（K11）：放在 `WS/perf/benchmarks/`，格式和验证用例相同，并且必须写明 `data_volume`（数据量假设）；在任何改动之前提交，之后不许改。
- **判决逻辑写在工具里**（§3.2 的三步和 §3.3 的"范围不重叠"），perf-optimizer 只负责执行工具返回的 verdict。

### 9.1 输入

- 工作区（git tag `v0`）；
- 验证线的测试资产：`verification/` 目录，以及从中选定的一组**性能基准事件**（正常路径的事件，带数据量假设，改动前后都用这一组）；
- `storage/analysis_report.json`、`storage/blueprint.json`；
- 规则库（§8.2）、台账（只读，用来参考历史结果）；
- 环境信息：有没有 Docker、单价配置。

### 9.2 工作流程

1. **测基线**：对所有函数跑 `run_proxy_metrics`；对性能基准事件跑一次 `run_rie_verdict` 的基线测量。
2. **生成候选改动**：用规则库扫描，加上数据驱动分析，每个候选都写成一份**修改请求**（§9.4）。按 §6 分类：允许的进入候选列表，不允许的写进 advisories。
3. **排序**：按代理指标的改善幅度排序，例如顺序深度的下降量。
4. **逐轮执行**（按 §7 组织）：修改代码 → `gate(final)` → 验证线阶段 B → §3 的判决 →
   - 保留：`git commit`，写清是哪份修改请求、判决证据是什么；
   - 权衡型：回滚，写进 `pending_human`，附上补丁；
   - 检查没通过：按 §3.6 分诊。代码问题修一次；测试问题和缺测试存补丁，标 `blocked_*`，等验证线复核之后重新判决；
   - 其他：回滚。
5. **收尾**：对全部函数跑一次全量回归，更新台账，写报告。

### 9.3 输出

`output/perf_report.json`：

```json
{
  "confidence": "normal | low",
  "environment": {"docker": true, "latency_ms": [10], "prices": {"...": "..."}, "data_volume_assumptions": {"...": "..."}},
  "baseline": {"<function>": {"calls": 0, "depth": 0, "warm_cpu_ms": 0, "rie_warm_ms": 0, "init_ms": 0, "cost_usd": 0}},
  "changes": [
    {"id": "C1", "request": "<修改请求 id>", "source": "rule:R1 | analysis", "functions": ["..."],
     "based_on": ["<之前已保留的改动 id>"],
     "verdict": "kept | reverted | pending_human | kept_inconclusive | blocked_by_test | blocked_by_coverage",
     "fix_attempts": 0,
     "review": {"blocked": "test | coverage", "cases": ["..."], "evidence": {}, "patch": "perf/patches/REQ-n.patch", "outcome": "tests_fixed | tests_kept | cases_added | unreachable"},
     "evidence": {"proxies": {}, "rie": {"10ms": {}, "0ms": {}, "5ms": {}, "15ms": {}}, "init": {}, "cost": {}, "equivalence": {}, "regression": {}, "coverage_of_changed_lines": {}},
     "commit": "<sha>"}
  ],
  "pending_human": [{"change": "C2", "reason": "...", "patch": "<git diff>"}],
  "advisories": [{"diagnosis": "...", "why_not_v1": "...", "evidence": {}}],
  "uncovered_paths": ["..."]
}
```

另外输出 `storage/stage_reports/S5.json`，格式和其他阶段的工作报告一样。

### 9.4 修改请求的格式（v1 自己执行，v2 用来派发）

```json
{
  "id": "REQ-1",
  "source": "rule:R1 | analysis",
  "target": {"file": "...", "function": "...", "lines": [0, 0]},
  "diagnosis": {"metric": "depth", "value": 30, "evidence": "..."},
  "proposal": "...",
  "expected": {"depth": "30→1", "calls": "=", "warm_cpu": "≈"},
  "constraints": ["H1a", "H1b", "failure-granularity: per-message"],
  "semantic_class": "v1-allowed | semantic",
  "owner_v2": "code-developer | sam-engineer | architect",
  "spec_relaxation": {
    "targets": ["<METHOD> <path>#responses[i]", "<METHOD> <path>#business_rules[j]"],
    "why_trace": "为什么这是单体的实现轨迹，而不是业务需求",
    "allowed_outcomes": "放宽后允许的外部结果（状态码 / 表状态 / 消息）",
    "evidence": {"calls": "a -> b", "depth": "a -> b", "measured_by": "run_proxy_metrics on a throw-away edit"},
    "blocking_tests": ["<case id>"]
  }
}
```

`spec_relaxation` 是可选字段（2026-10-02 加），只在申请放宽 spec 时出现，带它的请求 `verdict` 记为 `pending_spec`。限制：
- 只能针对 `implementation-trace` 性质的条目；涉及写入、消息内容、权限判定、单条消息失败粒度的条目不能申请，写进 advisories；
- 必须先在临时改动上量出收益（量完撤回，不提交）；
- 每轮 S5 最多一个申请批次；
- `blocking_tests` 只给主 Agent 看，派给 architect 时要去掉（K10）。

v1 里，perf-optimizer 生成请求后自己执行（`semantic_class` 必须是 `v1-allowed`）。到 v2 放开语义改动时，同一份请求可以由主 Agent 派发给 `owner_v2` 指定的 agent，由它带着自己的知识库和 gate 去修改。

### 9.5 禁止事项

- 不改 `output/lambdas/` 的目录结构，不改 `Handler`、`CodeUri`、事件定义这类影响正确性的字段；
- 不做 §6.2 里不允许的改动；
- 不修改验证线的测试资产，需要补事件时请验证线来补；
- 不做真实 AWS 部署；
- 报告里只写直接测得的量（例如"顺序深度 30→1、调用次数不变、热调用 Duration 从深度 30 组落到深度 1 组"），**不写"预计线上提升 X ms / X%"**。

---

### 9.6 Python 函数（2026-09-28，K19–K25）

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
- **R1、R4 的 Python 写法**（写在 `m2s-perf-optimizer.md` 里，都在测试工作区上跑通过）：
  - 用模块级线程池，最多 10 个线程，这是 botocore 默认连接池的大小。所以对 Python 来说，§2.1 第 2 项"并发不受限"的局限就变成了"并发上限 10"；
  - boto3 的 resource/`Table` 不是线程安全的，每个线程建自己的；
  - 循环体给循环外的变量重新赋值（计数器）的，不适用；
  - R4 里只有一条消息的波次，直接在当前线程里执行：去掉这一条的话，热点守卫基准的 CPU 会变差。
- **实测**（测试工作区；RIE 10ms，5 轮中位数）：

  | 改动 | 结果 |
  |---|---|
  | R4 分波次，SQS 消费者，一批 10 条 | `kept`：[118.4..119.0] → [22.3..28.6]ms；5ms 与 15ms 下同样更快；0ms 没有显著差异；热点守卫基准没有差异 |
  | R1 并行删除 8 个条目 | `kept`：[93.6..94.5] → [17.8..20.1]ms；init 没有差异 |
  | 全部消息无条件并行（不安全） | 热点基准 18 次冲突，1 条消息被上报重投；等价性差分报 H1 (c) |

## 10. prompt 注入策略与消融实验

### 10.1 注入策略（4C）

- **v1 先按 4A 执行**：性能规则**只注入 perf-optimizer**，上游三个 agent 只做忠实翻译，外加 §2.4 的上游正确性规则（见 `PIPELINE_REDESIGN.md` §2.4）。这样得到基线。
- 之后用消融实验，逐条决定哪些规则前移到上游。只有"安全规则"才能前移，即不改变语义、结构上必然更好的规则。会改变语义的优化，永远只由 perf-optimizer 诊断后提出。

### 10.2 消融实验

- **目的**：判断一条规则前移到上游之后，是否让产物更好。
- **比较的指标**：产物的基线指标（调用次数、顺序深度、CPU、RIE）、`gate(final)` 的通过率（确认没有损害正确性）、perf-optimizer 最后还剩多少问题要修。
- **正确性规则不参与消融**，在所有对照组里都保持开启。
- **控制成本**（T1 + T2 + T3）：
  - T1：先在 bookstore 上验证方法可行；
  - T2：只对在 E0 里有命中的规则做多应用消融；
  - T3：每种配置先重复 2 次，看 LLM 输出的波动有多大，再决定重复几次。

---

## 11. 实验计划

| 实验 | 需要部署？ | 内容 | 时机 |
|---|---|---|---|
| bookstore demo | 否 | `PIPELINE_REDESIGN.md` §5 的步骤 0–2：规格信息 → 验证线阶段 A → S5（R1 那一处得出判决） | 最先做 |
| E0 | 否 | 在 `mono_benchmark/` 里另外 4 个应用上跑流水线，统计规则命中情况，测各应用的基线 | demo 之后 |
| 消融实验 | 否 | §10.2 | E0 之后 |
| E2 | 是（人工授权，测完拆除） | SQS 消费者改写的真实收益；`BatchSize` / `MaximumBatchingWindowInSeconds` | 视需要 |
| E3 | 是 | 分解 E2E 延迟；REST 和 HTTP API 对比（客户端放在同一区域） | 视需要 |
| E4 | 是 | 畅销榜读取：Scan、GSI、物化视图的真实成本 | 视需要 |
| E1 | 是 | 打包方式在真实 Lambda 上的效果 | 优先级最低 |

L3 真实 A/B 的操作流程见 `PERF_OPTIMIZATION.md` §6.2。

---

## 12. v2 路标

- 放开语义改动：修改请求派发给负责的 agent；触及语义的改动标记"需重跑完整验收"，交付前重跑验证线阶段 A；**其中"S5 申请放宽 spec → architect 裁定"这一条已落地（2026-10-02，`CONTRACT_TESTING_PLAN.md` §7）**；
- 和单体做差分测试（本地运行单体 + 本地 DynamoDB）：判断故障行为是否符合单体；
- MemorySize、Architectures 的档位选择：只能靠 L3 的 Power Tuning；
- REST API → HTTP API：等 E3 分解出 E2E 延迟的构成后再评估；
- Provisioned Concurrency、缓存：需要真实流量的数据；
- 按 domain 并行展开（v1 设计稿 §7 已讨论过这个拓扑，仍然有效）。

---

## 附录 A：v1 设计稿的实测记录（原 §5，原样保留）

> 以下内容原样保留自 v1 设计稿 §5 的四个实测小节，小节标题不变，方便 `PERF_OPTIMIZATION.md` 引用。文中提到的"§3 步骤 2""§4 第 4 条""§2.3"等都指 **v1 设计稿**的章节（见 git `869c4bd`）。
>
> **这些数据是否有效，以 `PERF_OPTIMIZATION.md` §1.3 为准**：
> - "sam CLI + Docker 阶段实测"整节**作废**：测量时请求被代理截走了，测到的是失败的请求；
> - "两个反模式的实测差异"里的毫秒数，被 Windows 的时钟粒度量化了，只能看倍数关系；
> - "真实 AWS 部署实测"里的 E2E 数字是没有分解过的合计值；
> - 两次真实 AWS 实测都只来自 bookstore，只能看方向。

### A.1 两个反模式的实测差异（2026-09-23，bookstore 基准，代码阶段代理指标）

用 bookstore-oh（真实跑完 P0-S4、`gate(final)` PASS 的产物）实测批量 API 反模式的两个真实
实例，mock 层给每次 DynamoDB/SQS 调用加固定 8ms 延迟模拟单次网络往返（明确是代理值，不是
真实网络延迟）：

| handler | exec_ms | SDK 调用次数 | 调用序列 |
|---|---|---|---|
| `bestsellers-update-consumer`（SQS 消费者，3 本书） | 47.68 | 3 | `UpdateCommand` ×3，**顺序 await，无并行** |
| `orders-create`（结账，3 本书购物车） | 47.49 | 5 | `PutCommand` → 3×`DeleteCommand`（**`Promise.all` 并行**）→ `SendMessageCommand` |
| 其它 10 个单次调用的 handler | 11~24 | 1 | 单条 `Get`/`Put`/`Query`/`Scan`/`Delete`/`Update` |

**发现 1——两个反模式实例"贵法"不一样，不能用同一个预期收益去衡量：**
`bestsellers-update-consumer` 是**完全顺序**的循环（连 `Promise.all` 都没用），耗时和调用
次数都跟 N 线性叠加（47.68ms ≈ 3 × 单次调用耗时）——这是最干净、优化收益最大的实例，并行化
能同时省时间和省请求数。`orders-create` 的 3 个 `DeleteCommand` **已经**用 `Promise.all`
并行发出，47.49ms 的主要构成是"Put → 并行 Delete → SendMessage"这三个**顺序阶段**，不是
delete 本身的顺序开销。把 3 个 delete 合并成 1 个 `BatchWriteCommand` 能把请求次数从 5 降到
3（省成本、降低限流风险），但**墙钟耗时的改善会明显小于** `bestsellers-update-consumer` 那
个案例。结论：perf-optimizer 判断一个循环调用值不值得优先修时，要看它是"顺序 await"还是
"已经 `Promise.all` 并行"——前者时间+请求数双重收益，后者主要是请求数/成本收益，量化判据
不能一概而论。

**发现 2——批量 API 规则要按操作类型分诊**：`bestsellers-update-consumer` 用的是
`UpdateCommand`（DynamoDB 原子 `ADD salesCount`），这类操作**没有 `BatchWriteItem` 等价物**
（`BatchWriteItem` 只支持 `PutRequest`/`DeleteRequest`）——这个实例正确的优化是"循环改
`Promise.all` 并行"，不是"改批量 API"。已经在 §4 补了一条分诊规则。

### A.2 sam CLI + Docker 阶段实测（2026-09-23，bookstore 基准，Windows + Docker Desktop + WSL2）

> **⚠ 2026-09-24 更正：本节的测量数据作废。** 测量时环境里设置了 `HTTP_PROXY` 却没有 `NO_PROXY`，sam 发往
> `localhost` 的调用请求被本地代理截走了，下面的冷/热耗时和 start-api 的 502 测到的都是**失败的请求**，
> 不是函数的执行时间。根因排查和有效的替代数据见 `PERF_OPTIMIZATION.md` §3.2 实验 6、实验 7。

环境本身跑通了：`sam build --use-container` 对 12 个函数全部构建成功；WSL2 装好、Docker
Desktop 引擎起来之后 `docker ps` 正常。但**实际测量结果是一个重要的负面结论，直接影响这一
整层测量方案的可用性**：

**核心负面发现——这套本地测量法在这台 Windows 机器上精度不够，测不出批量 API 这类细粒度优化
的效果**：用 `sam local start-lambda --warm-containers LAZY`（默认参数下 SAM CLI **每次调用
都起新容器**，冷热看起来一样，这本身是个之前不知道的坑，必须显式加 `--warm-containers` 才能
观察到真实冷热差异）连续调用 3 个内部复杂度差异很大的函数：

| 函数 | 内部 SDK 调用模式 | cold（首次） | warm（后续 3 次均值） |
|---|---|---|---|
| `orders-create` | 5 次调用（Put+3×并行Delete+SendMessage） | 37,588ms | ~4,796ms |
| `bestsellers-update-consumer` | 3 次顺序 `UpdateCommand` | 24,331ms | ~4,801ms |
| `books-list` | 1 次 `QueryCommand` | 22,722ms | ~4,795ms |

冷热差异是真实存在的（前提是显式传 `--warm-containers`），但**三个函数的 warm 数字几乎完全
相同**（4795~4801ms），跟它们内部 SDK 调用次数/模式的巨大差异（1 次 vs 3 次顺序 vs 5 次）
完全不成比例——说明这条链路（`boto3`/HTTP → `start-lambda` 自带的 Flask 开发服务器 →
`docker exec` 进容器）本身的固定开销在 ~4.8 秒量级，把我们真正关心的、只有几十毫秒量级的
批量 API 优化效果**完全淹没了**。**结论：这套 sam+Docker 本地测量法目前只能用来做"端到端能
不能跑通"的功能性验证，测不出细粒度代码优化（批量 API、`Promise.all` 并行）的性能差异**——
v2 如果要继续投入这条路线，要么找到消除固定开销的办法（比如绕过 `start-lambda` 的 Flask 层，
直接用更底层的 Lambda RIE 协议），要么承认这一层测不出这类优化、只把它当功能验收用，性能判断
仍然依赖代码阶段的本地代理指标（§3 步骤 2）。

**另外三个环境坑，记录以便 v2 复用这条路线时少踩一次：**

1. **`sam local start-api` 对已登录端点不是"跳过校验"，是直接 502 崩溃**：`sam local` 完全
   不模拟 Cognito Authorizer，走真实 HTTP 请求时 `event.requestContext.authorizer.claims`
   根本不存在（不是"存在但未验证"），任何读 `.claims.sub` 的 handler 会直接抛未捕获异常，
   API Gateway 模拟层按 Lambda 报错的标准行为返回 502。比设计文档 §5 原来写的"不模拟 Cognito
   Authorizer，E2E Latency 数字不含真实 JWT 校验耗时"这条失真点更严重——根本无法测，不是
   "测出来的数字不准"。
2. **LocalStack `latest` 镜像现在需要付费账号才能启动**（`License activation failed`），改用
   `localstack/localstack:3.8`（确认免授权的免费社区版）才跑通，`latest` 标签不能再假设是
   免费可用的。
3. **`sam local` 对 `!Sub ${Environment}-bookstore-books` 这类需要参数插值的资源引用，解析
   出来的环境变量值是原始 CloudFormation 逻辑 ID（如 `OrdersTable`），不是真正算出来的物理
   名（`dev-bookstore-orders`）**——在 LocalStack 里建表要按逻辑 ID 命名，不能按模板里
   `TableName` 属性算出来的名字命名，否则会得到 `ResourceNotFoundException`。

### A.3 真实 AWS 部署实测（2026-09-23，一次性参考基准，测完已完全拆除）

按 §0"为什么用本地 Docker + sam，不接真实 AWS"里说的例外条款——一次性、人工授权、测完即拆的
参考基准——实际部署了一次 `bookstore-oh` 到账号 `882112174488`（`sam deploy`），测完立刻
`sam delete` + 手动删掉 `DeletionPolicy: Retain` 的 4 张 DynamoDB 表（栈删除不会带走它们），
已核实栈、表、函数全部不存在。这份数据填补了 sam+Docker 本地测量法测不出的那个信号——真实
Lambda 的 `REPORT` 日志行直接给 `Init Duration`/`Duration`，不需要再猜代理指标。

**T_cold_start（真实 `Init Duration`）**：三个函数都在 300~385ms 这个窄区间，跟函数内部
SDK 调用次数几乎无关（1 次 vs 3 次 vs 5 次调用差不多）——冷启动成本主要来自 Node.js 运行时
引导 + 加载 SDK 模块本身，不是业务逻辑决定的：

| 函数 | Init Duration |
|---|---|
| books-list（1 次调用） | 341.17ms |
| orders-create（5 次调用） | 384.63ms |
| bestsellers-update-consumer（3 次调用） | 309.15ms |

**T_warm_execution（真实 `Duration`，热启动）——这是本地 sam+Docker 测不出来、真实 AWS 上
一测就很清楚的信号**：

| 函数 | warm Duration | 相对 books-list |
|---|---|---|
| books-list（1 次 `QueryCommand`） | ~8.9ms（6.92~12.78） | 1× |
| bestsellers-update-consumer（3 次顺序 `UpdateCommand`） | ~19~36ms（干净热启动区间，另有几次 94~105ms，疑似并发扩容触发的额外冷启动） | ~2~4× |
| orders-create（Put+3×并行Delete+SendMessage，5 次调用） | ~45.25ms（25.38~65.45） | ~5× |

这组数字**验证了本地测量法完全测不出的东西**：函数内部 SDK 调用模式的真实差异（5 次 vs 1 次
调用）在真实 AWS 上是约 5 倍的热执行耗时差异，跟 §3 步骤 2 的本地 mock 代理指标方向一致（当时
mock 测的 47ms vs 12~24ms 也是几倍关系），说明本地代理指标这条路线本身是可信的，只是 sam+
Docker 这一层的固定开销把信号盖住了（见上一节）。

**E2E Latency（真实，走 API Gateway + 真实 Cognito JWT 校验）**：

| 端点 | cold | warm（4 次均值） |
|---|---|---|
| `GET /api/books` | 1595ms | ~916ms（841~1064） |
| `POST /api/orders`（结账） | 1761ms | ~903ms（843~953） |

**关键发现**：热启动时 Lambda 自己只占几毫秒到几十毫秒（上表 `T_warm_execution`），但 E2E
测出来是 ~900ms——**绝大部分时间（~850~1050ms）花在 API Gateway + Cognito Authorizer 校验
+ 这台机器到 us-east-1 的网络往返上，不是 Lambda 本身**。这给"要不要把 REST API 换成
HTTP API"这个 v1 明确排除掉的改动（§2.3）提供了真实数据支撑——HTTP API 定价约是 REST API
的 1/3.5（$1.00 vs $3.50 每百万请求），如果 v2 要重新评估这个改动，现在有真实基线可以对比。

**Throughput（20 并发，两次独立测试，结果不稳定，如实记录两个数据点）**：第一次 13/20 成功
（65%），延迟 975~1651ms；第二次 20/20 全部成功。没有复现出稳定的限流现象，更像是并发扩容
瞬间的一次性抖动——**跟设计文档一直强调的"throughput 数字只做参考，不当产能数字"完全吻合**，
这里再一次验证：这个量级下测出来的吞吐数字确实不稳定，不该被当作产能规划依据。

**费用（分析估算，不是真实账单——测试量级太小，几十次调用产生的真实费用远低于 1 美分，
CloudWatch 账单延迟也来不及反映）**：按 AWS 公开定价折算，**API Gateway REST API 的请求成本
（$3.50/百万次）远高于 Lambda 本身**（books-list 这类轻量函数约 $0.27/百万次，orders-create
这类稍重的约 $0.57/百万次，主要是计算时长的差异，印证了批量 API 优化除了省调用次数还能省一点
计算时长成本，但这部分远小于 API Gateway 的固定请求成本）；DynamoDB 按需模式和 SQS 在这个量级
下几乎可以忽略；Cognito 在 50,000 MAU 以内免费。**结论：想要明显降本，换 HTTP API 比抠 Lambda
内部批量调用的收益大得多**——这跟上面 E2E Latency 的发现指向同一个改动，进一步佐证"HTTP API"
是个值得 v2 认真评估的点，而不是当初判断的"改动面太大暂不做"。

### A.4 MemorySize / Architectures 真实 A/B 实测（2026-09-23，独立实验栈，测完已拆除）

按用户要求"先看本地能不能测、再看本地调出的值在真实部署时有没有正向效果"做的实验。**本地这
一步直接失败**：`sam local invoke` 在本机话了几个小时高强度测试后开始只打印 `SAM_CONTAINER_ID`
但从不真正创建容器（`docker inspect` 查不到这个 ID），重启 Docker Desktop 也没解决；后来
`sam build --use-container` 甚至原生 `sam build` 都在复制构建产物时报 `FileNotFoundError`。
**排查后定位到真正原因：工作目录路径太深**（`...\claude\jobs\...\scratchpad\...`），叠加
`node_modules` 里 `@aws-sdk/*` 包本身的深层嵌套路径，超过 Windows 260 字符 `MAX_PATH` 限制
导致文件操作静默失败——**换到 `D:\tmp\` 这种短路径后 `sam build`（不用 `--use-container`）
立刻成功**。这是一个比"Docker 不稳定"更精确的根因，记录下来避免以后重复排查（也回过头修正
了 §"sam CLI + Docker 阶段实测"里"Docker 环境不稳定"的归因——部分失败可能同样是路径长度
导致的，不完全是 Docker 本身的问题）。

本地这条路线到此为止没有产出数据，**直接跳到真实 AWS**：用 bookstore-oh 真实的
`orders-create` 源码（未做任何改动），做成 5 个只在 `MemorySize`/`Architectures` 上不同
的独立 Lambda 函数，部署到一个专门的临时栈（`m2s-perf-ab-test`，跟 bookstore-oh 主栈完全
分开），直接 `lambda:Invoke` 各调 4 次（1 次冷 + 3 次热），测完立刻 `sam delete`，已核实
栈/函数/表全部不存在。

**MemorySize（warm Duration，3 次热调用均值）**：

| MemorySize | warm Duration 均值 | Init Duration（冷） |
|---|---|---|
| 128 MB | ~173.7ms（256.74 / 136.05 / 128.22） | 342.88ms |
| 512 MB | ~35.3ms（55.42 / 25.89 / 24.57） | 326.62ms |
| 1024 MB | ~24.3ms（30.03 / 22.49 / 20.27） | 321.33ms |

**真实、清晰的信号**：128MB → 512MB 快了约 5 倍，512MB → 1024MB 再快约 31%（边际收益明显
递减）。`Init Duration` 三档几乎不变（321~343ms）——内存大小基本不影响模块加载/运行时引导
耗时，只影响 handler 内部代码的实际执行速度（更多内存=更多 CPU）。**这条直接推翻了 §2.3
原来"normal/缺省档给 128-256MB"的建议**——128MB 实测明显偏慢，已经把该档下限上调到
256-512MB（见 §2.3 更新）。

**Architectures（warm Duration，同样 3 次热调用，前两次还在收敛，看最后两次更稳）**：

| Architecture | warm Duration（后两次均值） | Init Duration（冷） |
|---|---|---|
| x86_64 | ~27.5ms（28.78 / 26.31） | 287.91ms |
| arm64 | ~23.0ms（27.12 / 18.80） | 273.44ms |

arm64 的 warm Duration 和 Init Duration 都不劣于 x86_64（甚至略快），**这次是真实 Graviton
硬件上测的，不是本地 QEMU 模拟，数字可信**——跟 AWS 官方 Graviton 基准的方向一致，`Globals.
Function` 默认 `Architectures: [arm64]` 这条建议现在有真实数据支撑了。

**没做的部分，如实说明**：SQS `BatchSize`/`MaximumBatchingWindowInSeconds` 这次没有纳入
真实 A/B 实验（本地完全测不了，真实 AWS 这次时间上没排上）——这是目前 MemorySize/
Architectures/SQS 参数三条里唯一还没验证过的，留给 v2 优先补。

---

