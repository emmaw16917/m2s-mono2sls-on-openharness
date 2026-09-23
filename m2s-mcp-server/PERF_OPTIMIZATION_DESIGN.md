# M2S 性能优化设计方案（v1）

> 状态：设计稿，未实现。本文档记录 v1 的落地范围和 v2 的路标，供后续实现时对照。

## 0. 背景与范围

现状：`m2s-migrate` 流水线（P0 → S1 architect → S2 code-developer → S3a/b/c sam-engineer → S4
validator）只验证正确性/一致性（blueprint B0、code C1/C10、template C1-C12），完全没有性能维度。
`gates.py` 的全部 check 都是结构一致性检查，`m2s-sam-kb` 里 `MemorySize: 512` 写死无分级，
没有 `Architectures` 字段。整套流水线目前也刻意不做真实 AWS 部署（`m2s-migrate` §6）。

**v1 目标（本次要落地的范围）：**

1. 新增一个独立的性能优化 agent：`m2s-perf-optimizer`，用**实测**（而不是纯静态规则）驱动
   自查循环，测量方法基于本地 Docker + `sam local`（不接真实 AWS，理由见下文"为什么不用真实
   AWS"）。
2. 现有三个 agent（architect / code-developer / sam-engineer）注入性能相关的"预防性"知识和
   默认值，减少 perf-optimizer 事后要修的量，但**不改变它们现有自查循环的判定范围**（S1 仍只
   认 blueprint B0、S2 仍只认 code C1/C10、S3c 仍只认 template C1-C12 + `validate_sam`）——
   性能不是这几个阶段自查循环的新增判定项，只是它们生成时参考的知识多了一部分。
3. 沿用现有"文件系统是唯一真相 + 主 Agent 轮询做汇合点 + agent 自己迭代到收敛"的协作模式，
   不发明新协议。

**v1 明确不做（留给 v2，见 §7）：**

- 不做按 domain 并行 fan-out。S2 / S3b / perf-optimizer 内部仍是单个 agent 串行处理全部函数。
- 不做真实 AWS 部署 / 真实压测，继续遵守 `m2s-migrate` §6"不做任何 AWS 部署"的既定边界。
- 不做架构决策前移（把性能信号塞进 `analyze_project` 静态分析）——v1 先用最小改动验证"实测
  闭环"这条路子本身跑不跑得通，再决定要不要往架构决策层继续投入。
- 不做 API Gateway REST → HTTP API 切换、不做 Provisioned Concurrency——涉及 Cognito
  Authorizer 语法改动和额外费用决策，改动面/风险都超出 v1 范围。

**为什么用本地 Docker + sam，不接真实 AWS：**

- perf-optimizer 的自查循环需要"测一次、改一次、再测一次"，最多 3 次，要求单次迭代足够快、
  足够便宜。真实 AWS 每次迭代要走 CloudFormation 创建/更新（分钟级）+ 真实 invoke 计费，
  跟"闭环"这个设计初衷矛盾。
- 这台机器目前没有 AWS 凭证；即使以后有，让一个自动重试循环无人值守地持有真实云部署权限，
  风险量级跟"只碰本地 Docker 容器"完全不同，不该塞进自动自查循环。
- 真实数字噪声大（调度到哪台硬件、AWS 控制面负载、网络抖动都不受代码控制），不利于"改前 vs
  改后"这种闭环真正需要的相对比较；本地 Docker 虽然绝对值不代表生产环境，但同机同条件下的
  相对差异是干净可比的。
- 场景目标是"生成**能直接部署**的高效代码"，交付物是代码+模板本身。真正的部署和生产验收
  天然是产物交付之后、由使用这份代码的人自己做的事，不该被生成流水线自己吞下来——这也是
  `m2s-migrate` §6 既有边界的自然延伸，不是新约束。

**环境依赖（v1 需要新装的东西，落地前先确认）：**

- `sam` CLI：`mono2sls`、`mono2esls` 两个 conda env 都已经装好（2026-09-23 确认，版本
  1.160.1）。
- Docker Desktop（`sam local invoke` / `sam local start-api` 都需要）——**2026-09-23 状态：
  已安装但引擎未就绪，需要人工完成下面这几步**，本节记录下来防止环境半途丢失：
  1. **装 WSL2**（需要管理员权限，Claude 自动化session做不了）：以管理员身份打开
     PowerShell 或 CMD，运行 `wsl --install`，然后**重启这台机器**（WSL2 安装几乎总是要求
     重启才能生效）。
  2. 重启后确认 WSL2 真的装好了：任意终端运行 `wsl --status`，不再提示"未安装任何
     Linux 发行版"就算通过。
  3. **启动 Docker Desktop**：开始菜单搜 "Docker Desktop" 打开（或运行
     `"C:\Program Files\Docker\Docker\Docker Desktop.exe"`），首次启动会弹登录/注册页——
     可以登录 Docker 账号，也可以点"跳过登录"直接用，不影响后续 `sam local` 使用。
  4. 验证引擎真的起来了：`"C:\Program Files\Docker\Docker\resources\bin\docker.exe" ps`
     不再报 "backend is not running" / 连不上 `docker_engine` pipe，而是返回一个空的
     容器列表表头，就说明成功了。
  5. 弄完这几步跟 Claude 说一声，继续做 sam CLI + Docker 阶段的代理指标测量
     （`sam build` → `sam local invoke` 冷/热差值 → `sam local start-api`）。
- 可选但建议：LocalStack（单个 Docker 容器统一模拟 DynamoDB / SQS / EventBridge / S3，给
  E2E Latency 测量提供真实下游，避免手写 mock 层）。
- 轻量压测工具：Node 生态用 `autocannon`（`npx autocannon` 免安装）。

---

## 1. 总体拓扑（v1）

在既有 S3c 和 S4 之间插入一个新阶段 `S3p`（perf；字母沿用既有 S3a/S3b/S3c 命名习惯，表示
"S3 之后、S4 之前"，不打乱已有的 `stage_reports/S4.json`、`gate(final)` 这些既定文件契约）：

```
P0（analyze_project）
  ↓
S1  architect               → gate(blueprint)            [不变：自查只认 B0]
  ↓
S2  code-developer           → gate(code)                  [不变：自查只认 C1/C10]
  ↓
S3  sam-engineer(a/b/c)      → gate(template)+validate_sam [不变：自查只认 C1-C12]
  ↓
S3p perf-optimizer（新）      → gate(perf)（新，advisory，不阻断验收）
  ↓
S4  validator（不变职责，位置后移到 perf 之后）→ gate(final)（不变：仍只代表正确性验收，不含性能）
  ↓
验收
```

**为什么 perf-optimizer 放在 S3c 之后、S4 之前，而不是 S4 之后：** perf-optimizer 会实际
修改代码和模板（批量 API 改写、`MemorySize`/`Architectures` 调整），这些修改带有引入新错误
的风险（哪怕很小）。让 S4（正确性权威裁决）在它之后跑，才能保证 `gate(final)` 检验的是**真正
要交付的最终态**，而不是"perf 优化之前看起来正确、优化之后没人再验过"的半成品。

**为什么 `gate(perf)` 不影响 `gate(final)` 的 PASS/FAIL：** 吞吐这类指标本地测出来的可信度
有限（是"回归检测信号"而非"产能数字"），做成 blocking gate 会让流水线因为一个本不该被当真
数字的指标卡死。`gate(perf)` 的作用是让 perf-optimizer 自己判断"要不要继续迭代"，结果作为
**独立报告**（`output/perf_report.json`）附加在最终交付物里，不参与验收判定。

---

## 2. 现有三个 agent 的改动点

### 2.1 m2s-architect（S1）

新增一个**可选**字段（加在 `lambda_functions[]` 条目内部，不新增顶层 key，不破坏
`merge_blueprint_key` 现有的 10-key 白名单约束）：

```json
{"name": "...", "perf_hint": "hot" | "normal", "...": "..."}
```

判定规则（写进 `m2s-architect.md`，作为一条轻量启发式，不是强制）：出现在高频交互路径的端点
（列表查询、下单、登录后首屏数据这类）标 `"hot"`，其余标 `"normal"` 或干脆不写（缺省当
`"normal"`）。这个字段目前只做一件事——给 S3 sam-engineer 决定 `MemorySize` 分级提供参考
信号，**不新增 S1 的自查循环判定项**：B0 检查不需要管这个字段填没填、填得准不准。

### 2.2 m2s-code-developer（S2）

不改变 `gate(code)` 自查循环的判定范围（继续只认 C1/C10），只改变**参考知识**：在"硬性工具
说明"里追加一条，写完代码后除了现有的 `m2s-lambda-coding-kb` / `m2s-async-patterns-kb`，
再 `skill(name="m2s-performance-kb")`（新增，见 §4）读一遍做自我对照，重点是"原单体里循环
调用单条 DB 操作的模式，要改写成批量 API"——这条在现有 KB 第 6 节完全没提，是当前最容易被
忽视的一处。这是"写的时候就写得更好"，不是新增校验步骤。

### 2.3 m2s-sam-engineer（S3）

对 TIER1 参考模板（`m2s-sam-kb`）做三处默认值调整：

1. `Globals.Function` 默认加 `Architectures: [arm64]`（`nodejs22.x`/`python3.12` 都原生
   支持，同负载更快更省，加这一行代价接近零）。
2. `MemorySize` 从写死 `512` 改成分级建议：读 blueprint 里的 `perf_hint`，`hot` 给
   `512-1024`，`normal`/缺省给 `128-256`（纯 CRUD 场景内存加大边际收益低）。**这条只是
   参考建议，不是强制值**——sam-engineer 仍然可以按实际函数复杂度调整，避免变成新的死板
   规则。
3. SQS 事件源默认加 `BatchSize` / `MaximumBatchingWindowInSeconds` 的参考值（当前
   `m2s-sam-kb` 里 SQS 消费者只给了 `VisibilityTimeout`，没有这两个吞吐相关参数）。

**以上三条截至 2026-09-23 都没有真实 A/B 测试数据支撑**（没有在 bookstore 上部署对比过不同
`MemorySize`/`Architectures`/SQS 参数的版本），纯粹依据 AWS 官方通用最佳实践/公开基准——
跟 §5"真实 AWS 部署实测"里批量 API、REST→HTTP API 那两条有真实数据不是一个级别，见 §7 末尾
的详细说明。默认值先按官方基准给，但不要在文档或报告里把这三条描述成"已验证"。

---

## 3. 新增 m2s-perf-optimizer agent

按现有四个 agent `.md`（`m2s-architect.md` / `m2s-code-developer.md` / `m2s-sam-engineer.md`
/ `m2s-validator.md`）的固定结构给完整定义框架，落地时对应新文件
`m2s-mcp-server/agents/m2s-perf-optimizer.md`。

**输入契约：**

- `<WS>/storage/blueprint.json`
- `<WS>/output/lambdas/`（S2 产出，已过 `gate(code)`）
- `<WS>/output/template.yaml`（S3c 产出，已过 `gate(template)` + `validate_sam`）

**工作流程**（这是它跟其它 agent 最大的不同——不是"写代码/写模板"，是"测量 → 改 → 再测量"）：

0. **建立 baseline**：在做任何改动之前，先对 S2/S3c 产出的原始代码跑一遍完整测量（代码阶段
   代理指标，逐函数记录），作为每个函数各自的基准值。后续每一轮改动都对着**这个函数自己的
   baseline** 算 delta，不跟上一轮比、也不跟外部绝对阈值比——这样中途改坏了要撤销时，有一个
   稳定的参照系。
1. **扫描代码库、确定 mock 覆盖范围（不能假设 mock 层现成可用）**：mock 调用计时依赖一个
   拦截 SDK `.send()` 的 mock 层，但这个 mock 层**不是写死能通用的**——不同单体迁移出来的
   代码用到的 AWS 服务、DynamoDB 操作类型、handler 校验所需的 event 字段都不一样（
   2026-09-23 用 bookstore 实测时踩过：一个 handler 因为 mock event 少给了一个校验必需字段，
   提前 400 短路，测出的"耗时"是假的，根本没碰到真实 DB 调用）。所以每次跑之前，perf-optimizer
   必须先做这一步，不能直接复用上一次的 mock 层：
   - `grep -rho "new [A-Za-z]*Command(" output/lambdas/` 找出这次代码库实际用到的全部
     Command 类型，逐一确认 mock 层的覆盖表里都有；缺哪个补哪个（mock 层对未覆盖类型要
     **直接抛错**，不能静默返回空对象——见下面"mock 层本身要设计成什么样"）。
   - 逐个 `require()` 扫一遍 `output/lambdas/` 用到的 `@aws-sdk/client-*` 包，确认对应的
     Client（`S3Client`/`EventBridgeClient`/`LambdaClient` 等）都被 mock 层打了补丁，不是
     只有 DynamoDB/SQS。
   - 对每个 handler，读一遍它自己对 `event.pathParameters`/`event.body`/
     `event.queryStringParameters` 等字段的校验逻辑（`grep "event\."` 定位），构造能让它
     真正走到 DB 调用那条路径的 mock event，不能全体 handler 共用同一份"塞满常见字段"的
     通用 event——通用 event 能跑通大部分 handler，但校验规则更严格的那一小撮会被提前拦截，
     产生"看起来很快但其实没测到真实逻辑"的假数字。
   - 校验手段：如果一个 handler 返回了非 2xx 状态码、或者 `sdk_call_count == 0`，判定这次
     测量不可信，修 mock event 或覆盖表后重测，不能把"提前失败"误当成"这个 handler 很快"
     写进 `perf_report.json`。
2. **代码阶段代理指标测量**（不需要 sam，跑得快）：对每个 handler，跑一次
   `require()`/`import` 计时（module-level init 代理指标）+ 一次 mock 调用计时（handler
   执行耗时代理指标，用上一步确认覆盖完整的 mock 层）。发现循环里单条 DB 调用模式，参照
   §4 第 4 条的操作类型分诊规则决定改批量 API 还是改 `Promise.all`。
3. **sam CLI 阶段测量**（需要 sam + Docker，跑得慢，放后面）：`sam build` → `sam local
   invoke`（冷）+ `sam local start-lambda` 连续两次 invoke（热）算 `T_cold_start` 代理值
   → `sam local start-api` 测 E2E Latency 代理值（如果 LocalStack 可用就配上真实下游）→
   可选轻量压测拿 throughput 弱信号。
4. **要不要保留一个改动的判据**：**这个改动直接相关的指标必须变好（或至少不变差），其它所有
   已测量的指标（这个函数自己的其它指标、以及其它函数的指标）不能因为这次改动而变差**——是
   "局部改善 + 全局不劣化"，不是要求"全局也一起变好"（不合理，一个函数的批量 API 改写没理由
   顺带改善另一个不相关函数的 cold start）。判断改善/劣化时要留一个噪声容忍带（建议
   ±10%~15%，具体数值以后按实测方差调整），落在这个带内算"没有显著差异"，不触发撤销也不算
   改善——本地 Docker 测量本身有波动，拿绝对数字较真会让 agent 在噪声里空转。
5. **批量改 vs 逐处改的粒度**：跨不同函数/不同文件的改动可以在同一轮里一起做、一起测——彼此
   没有因果关系，测完后每个函数各自对着自己的 baseline 独立判断要不要保留，不需要为了隔离
   变量把 N 个函数拆成 N 轮 sam CLI 测量（那一层很贵）。**但同一个函数内部如果要做多处改动
   （比如既改批量 API 又调 `MemorySize`），必须拆开顺序做**：改一处 → 代码阶段测一次 → 定
   下来 → 再改下一处，否则测出结果后分不清是哪个改动起的作用。落到两层测量成本上：代码阶段
   （便宜）可以对每个函数做"改一处→测→留好的/撤坏的"这种逐处筛选；sam CLI 阶段（贵）不再
   逐处测，而是把代码阶段已经筛选确认过的改动 + 这一轮要动的 `MemorySize` 等配置改动打包成
   一批，跑**一次** sam CLI 测量做整体确认——如果这次测量发现某个函数出乎意料变差了，只撤销
   那个函数这一轮加的改动，其它函数保留，不整批推倒重来。
6. **"一次尝试"的定义**：一轮 = 代码阶段的逐处筛选（便宜，轮内可以多次迭代）+ 一次 sam CLI
   阶段的整体确认测量（贵，一轮只跑一次）。最多 3 轮（沿用既有自查循环上限约定）。3 轮后仍
   不理想：不阻塞流水线，如实在报告里写清楚"已尝试的改动 + 最后一轮测量数值 + 还剩什么没
   改"，输出 `STAGE_BLOCKED`（这里的 `STAGE_BLOCKED` 语义跟别的阶段不同——不代表流水线要
   停，只代表"性能这块没收敛"，`gate(final)` 不受影响，主 Agent 直接放行进入 S4）。

**输出契约：**

- `<WS>/output/perf_report.json`：每个函数的测量数值（本地代理指标 + sam CLI 指标，标清楚
  哪些是真实数字哪些是本地代理值/失真说明）、做过的改动、改动前后对比。
- `<WS>/storage/stage_reports/S3p.json`：跟其它阶段一样的工作报告格式。

**禁止事项**（照抄既有模式，加性能特有的几条）：

- 不改 `output/lambdas/` 目录结构、Handler/CodeUri 这些正确性相关字段——只改函数体内部
  逻辑和 `MemorySize`/`Architectures` 这类纯性能配置，不触碰 S4 要验的那些一致性锚点。
- 不做真实 AWS 部署、不使用真实 AWS 凭证。
- Architecture 相关的计时结论不采信——arm64 在本机 x86 上是 QEMU 模拟，只用官方公开基准
  做默认值依据，不拿本地测的 arm64 数字改决策。
- throughput 数字不写成"结论性"表述（不要出现"吞吐提升 30%"这类），只能写成"本地并发测试
  未发现明显退化 / 发现 XX 处并发安全问题"。

---

## 4. 新增 m2s-performance-kb skill（大纲）

给 code-developer 和 perf-optimizer 共用：

1. 批量 API 模式（DynamoDB `BatchGetItem`/`BatchWriteItem`/`TransactWriteItems`，对照"循环
   里的单条 get_item/put_item"识别）。
2. 依赖精简清单（跟现有 `m2s-lambda-coding-kb` 第 11 节呼应，但聚焦在"这个包会不会显著拖慢
   cold start"这个判断维度）。
3. 本地测量方法参考（§5 的内容搬进来，供 perf-optimizer 直接照着操作，不用每次自己发明
   测量脚本）。
4. **批量 API 规则要按 DynamoDB 操作类型分诊，不能一刀切**（2026-09-23 用 bookstore 基准
   实测发现，见 §5 末尾"两个反模式的实测差异"）：
   - `PutItem`/`DeleteItem` 的循环 → 能力所及范围内应该改 `BatchWriteItem`。
   - `UpdateItem`（尤其原子计数器 `ADD`）的循环 → **`BatchWriteItem` 不支持 UpdateItem
     操作**，这种情况能做的优化是**改成 `Promise.all` 并行**，不是"批量 API"。
   - `m2s-performance-kb` §1 写批量 API 示例时要把这条分诊规则放在最前面，不能让 code-developer
     看到"循环 + 单条调用"就无脑套用 `BatchWriteItem`，那样对 `UpdateCommand` 循环会是错误
     的重写建议（DynamoDB 直接拒绝这种请求）。
5. **mock SDK 拦截骨架（配合 §3 步骤 1 使用，不是写死的成品脚本）**：给 perf-optimizer
   提供一份**通用拦截骨架**（在 `DynamoDBDocumentClient.prototype.send`/
   `SQSClient.prototype.send` 等原型上打补丁、按 `command.constructor.name` 分发、每次调用
   加一个固定延迟模拟单次网络往返），但 Command 覆盖表、需要打补丁的 Client 种类、每个
   handler 的 mock event 内容都留空/留最小示例，要求 perf-optimizer 每次运行按 §3 步骤 1
   扫描出来的结果自己补全，不能假设这份骨架已经覆盖当前代码库。骨架对未覆盖的 Command 类型
   要直接抛错，不能静默返回空对象掩盖覆盖不全的问题。

---

## 5. 测量方法

| 指标 | 测法 | 谁测 | 何时能测 |
|---|---|---|---|
| 代码级 init 耗时（cold start 代理） | `require()`/`import` 计时 | perf-optimizer | S2 完成即可，不需要 sam |
| handler 执行耗时（E2E Latency 代理） | mock 调用计时 | perf-optimizer | S2 完成即可，不需要 sam |
| T_cold_start | `sam local invoke`(冷) − `sam local start-lambda` 连续两次(热) 的差值 | perf-optimizer | 需要 S3c 产出完整 `template.yaml` |
| E2E Latency（本地模拟栈） | `sam local start-api` + 可选 LocalStack | perf-optimizer | 需要完整 `template.yaml` |
| Throughput（弱信号） | `sam local start-api` + `autocannon` | perf-optimizer | 需要完整 `template.yaml`，结果仅供参考 |

失真点（写进报告模板，每次都要带上，不能省略）：

- arm64 本地测试受 QEMU 模拟影响不可信，只做官方基准背书的默认值，不拿本地数字改决策。
- `sam local` 不模拟 Cognito Authorizer，E2E Latency 数字不含真实 JWT 校验耗时。
- `sam local` 的并发调度模型跟真实 Lambda 弹性扩容完全不同，throughput 只做"有没有明显并发
  bug"的回归检测用，不能当产能数字用。

### 两个反模式的实测差异（2026-09-23，bookstore 基准，代码阶段代理指标）

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

### sam CLI + Docker 阶段实测（2026-09-23，bookstore 基准，Windows + Docker Desktop + WSL2）

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

### 真实 AWS 部署实测（2026-09-23，一次性参考基准，测完已完全拆除）

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

---

## 6. `m2s-migrate` 编排 skill 需要同步更新的地方

- §1 阶段拓扑图：插入 `S3p`。
- §5 文件契约表：加 `output/perf_report.json`、`storage/stage_reports/S3p.json`。
- §2 spawn prompt 模板要素：新增一条——spawn `m2s-perf-optimizer` 时要在 prompt 里确认
  `sam`/Docker 是否可用，不可用则明确告诉它跳过 sam CLI 阶段测量，只做代码阶段代理指标
  （保证流水线在环境不全时也能优雅降级，不是硬性失败）。
- §3 分层裁决规则：`S3p` 的 `STAGE_BLOCKED` 不触发主 Agent 回炉重 spawn（性能没收敛不算
  "这个阶段失败"），只需要主 Agent 如实记录进最终报告，直接放行进入 S4。

---

## 7. v2 路标（本次不做，留记录）

- S2 / S3b / perf-optimizer 的函数级测量与修正部分按 domain 并行 fan-out；S3a（共享资源）/
  S3c（合并+校验）/ perf-optimizer 的全局决策与实测收尾部分保持全局单例——这是已经讨论定型
  的拓扑，理由：
  - S3a/S3c 的产物（API Gateway、Cognito、DynamoDB 表等共享资源，以及跨 domain 的一致性
    校验）天生是全局单例，按 domain 拆会导致资源重复声明/冲突。
  - perf-optimizer 的 `sam build`/`sam local invoke` 依赖已经合并完整的 `template.yaml`，
    某个 domain 分支手头只有自己那部分函数时根本跑不了，必须等全部 domain 完成后才能进行。
- 共享层（`output/layers/shared/`）并发写冲突规避：先串行生成共享层，再并行 fan-out domain。
- `m2s_merge.py` 扩展成支持 N 个 domain 分片合并（现在是二元 part1+part2），`Outputs:` 段
  建议改成合并脚本用代码自动生成，而不是让某个 domain agent 猜全局函数列表。
- 架构决策前移：`analyze_project` 静态分析新增性能相关量化信号（循环内 DB 调用计数等），
  供 S1 architect 判定 `perf_hint` 时有数据支撑而不是纯启发式猜测。
- **REST API → HTTP API**：2026-09-23 真实 AWS 部署实测（§5"真实 AWS 部署实测"）显示 E2E
  Latency 里 850~1050ms 是 API Gateway + Cognito Authorizer + 网络往返，Lambda 自己只占
  几毫秒到几十毫秒；HTTP API 定价也只有 REST API 的约 1/3.5。v1 因为 Cognito Authorizer
  语法改动、改动面大排除了这条，但现在有真实数据支撑，是 v2 里收益可能最大的一项，应该优先
  评估。
- **Provisioned Concurrency（按 `perf_hint: hot` 决定要不要配）**：真实部署测出的
  `T_cold_start`（`Init Duration`）稳定在 300~385ms，有了这个真实数字，v2 可以算一笔账：
  对哪些端点值得为消除冷启动付预留并发的钱，不用再凭感觉猜。
- **X-Ray `Tracing: Active` 的开销 vs 诊断价值，需要一起权衡，不能孤立决定关不关**：现在
  `Globals.Function.Tracing: Active` 是默认常开，从未量化过它本身对延迟的影响；但它同时是
  唯一能把"850~1050ms 里 API Gateway 和 Cognito Authorizer 各占多少"拆开看的手段（本次
  实测只测出了两者加起来的总数，没有再往下归因）。v2 如果要动这个开关，得先想清楚：关掉省
  下的开销，够不够换来一直看不清 API Gateway/Cognito 各自占比的代价。

**以上三条（连同 v1 里的 MemorySize 分级、`Architectures: arm64`、SQS `BatchSize` 调优）
目前都还没有真实 A/B 测试数据支撑，跟"批量 API 分诊"（本地代理指标 + 真实 `Duration` 互相
印证出 ~5 倍差异）和"REST→HTTP API"（真实 E2E 延迟直接测出主要开销来源）不是一个验证级别**
——MemorySize/arm64/SQS 参数这三条纯粹是照抄 AWS 官方通用最佳实践/公开基准，从来没有在
bookstore 上部署对比版本验证过。v2 如果要继续投入，值得用这次已经跑通、验证过完全可拆除的
"一次性真实部署"流程（§5"真实 AWS 部署实测"），对每一条单独做真实 A/B（比如同一份代码分别
部署 256MB/512MB/1024MB 三个变体比较 `Duration`，或者 x86_64/arm64 各部署一份比较），而不是
继续假设官方基准直接适用于这个场景。
