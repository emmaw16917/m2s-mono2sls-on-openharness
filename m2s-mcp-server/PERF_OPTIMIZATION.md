# M2S 生成代码的性能改进：对照官方参考实现 + 用实测数据指导优化

> 2026-09-23。本文是 `PERF_OPTIMIZATION_DESIGN.md`（v1 设计稿，记录了代码阶段代理指标 /
> sam+Docker / 真实 AWS 三层实测的原始数据）的**续篇**：先把 m2s 生成的 todo / bookstore
> 代码跟 AWS 官方参考实现逐项对照，列出可改进点；再把三层实测数据（加上本次新做的几个代理实验）
> 整合成一套"用哪层数据、回答什么问题、如何决定改不改"的方法。原始数据不在这里重复，引用时
> 标注出处（`DESIGN §5` = `PERF_OPTIMIZATION_DESIGN.md` §5）。

## 0. 结论摘要

1. **m2s 的产物是"忠实翻译"，单体里的性能债被原样搬进了 Lambda。** bookstore 单体里的
   `for…await updateBestSeller`（逐条顺序更新）、`scan + 内存排序` 取 Top-20、`scan + 内存过滤`
   做搜索，在 m2s 产物里一行不差地保留了下来。官方参考实现用 ElastiCache Redis 有序集合、
   OpenSearch、DynamoDB Streams 解决了同样的问题——这正是 m2s 缺的"迁移时顺手重构"那一层。
2. **在基础设施"卫生"上，m2s 反而比官方参考实现更好**（SDK v3 模块化、nodejs22、按需计费、
   不进 VPC、不用定时 warmer），这些是要**保住、不能倒退**的地方（§2.3）。
3. **在"不部署、只有代码阶段指标"的真实流水线里，建议分成两类（§3.4 逐条分类）**：
   - **指标能验证是否正向的**：调用链改写（SQS 消费者聚合+并行、orders-create 压浅调用链）
     ——用 mock 调用日志算出的**调用次数 + 顺序往返深度**判断，确定性、无噪声（消费者 30→1、
     orders-create 3→2/1）；
     **热执行 CPU**——用 HTTP 层 mock 让 SDK 真实跑完序列化/签名/反序列化来测（实验 5），消费者
     聚合改写 CPU −78%，全表 Scan 的 CPU 随条数线性增长（20 条 0.39ms → 5000 条 ~35ms）。
   - **只能靠经验/先验的**：MemorySize、arm64、SQS 批参数、访问日志、限流、HTTP API、缓存、
     Streams、Provisioned Concurrency——代码阶段指标对它们测不了。（上一版把"热读路径不要全表
     Scan"也归到这类，实验 5 之后改为可测，前提是给定数据量假设。）**esbuild 打包也降到这一类**：
     Windows 上 `require()` −40% 的结果，在真实 Linux 运行时容器里复现不出来（实验 6）。
4. **最大的方法论修正：热执行不再用"sleep 模拟延迟后的墙钟毫秒"判断，而是拆成两部分分别测**：
   等网络的部分看 SDK 调用次数 + 顺序往返深度（`mock-sdk-shim.js` 的 `getCriticalPathDepth()`），
   CPU 部分看 HTTP 层 mock 下的热执行中位数（`warm_cpu.js`，重复测量波动 ≤±7%）。本次发现
   Windows 上 `setTimeout(8)` 实际要 ~14.5–15.6ms，mock 墙钟被时钟粒度量化了。
7. **sam+Docker 并不是"测不出细粒度指标"，之前的 L2 数据是环境问题造成的无效数据**（实验 6）。根因：
   PowerShell 配置文件设置了 `HTTP_PROXY`/`HTTPS_PROXY`（本地代理 127.0.0.1:21081），但没有设置
   `NO_PROXY`。sam 用 Python `requests` 访问 `http://localhost:<端口>` 调用容器，请求被转给了代理，代理
   约 3.7s 后返回 502/空响应。所以 `sam local invoke` 什么都不打印；`start-lambda` 的"热调用约 4.8s、
   三个函数完全相同"测到的是**失败的请求**；`start-api` 的 502 也是这个原因。设置
   `NO_PROXY=localhost,127.0.0.1,::1`（或加 `--container-host 127.0.0.1`）之后，sam 全部恢复正常。测量时直接用 Lambda 运行时镜像
   （自带 RIE）跑 `sam build` 产物，读容器**内部**的 `REPORT Duration` 和 rapid 的 `INIT REPORT`：
   空函数的固定开销约 1.3ms，1 次调用 vs 5 次调用的 handler 能区分开（~3.2ms vs ~4.6ms），消费者
   改写 189ms → 9.9ms，Scan 20 条 vs 2000 条 8.7ms → 19.4ms；而且是在真实 Linux + 真实 nodejs22
   运行时上测的，还修正了一个 L1 的错误结论（esbuild）。
5. **L3 数据的适用范围很窄，不能当普适模型用**（§4.2）：只来自 bookstore 一个应用、MemorySize/
   架构 A/B 只测了 orders-create 一个函数、每档 3~4 次调用、一个区域一天。能推广的是**方向**
   （调用深度越大 Duration 越长；128MB 明显偏慢；arm64 不劣于 x86），不是具体倍数。
6. **E2E ~900ms 受测试者本机网络影响严重，由它推出的"Lambda 内部优化对用户延迟不重要"这条
   结论撤回**（§4.4）。Lambda 自身的 `Duration`/`Init Duration` 是 AWS 内部计时，不受本机网络
   影响，仍然有效。

---

## 1. 对照对象与数据来源

| | todo | bookstore |
|---|---|---|
| 官方参考实现 | [aws-samples/lambda-refarch-webapp](https://github.com/aws-samples/lambda-refarch-webapp)（clone 在 `temp/ref/`） | [aws-samples/aws-bookstore-demo-app](https://github.com/aws-samples/aws-bookstore-demo-app)（同上） |
| 单体（逆向而来） | `Mono2Sls-artifacts/mono_benchmark/todo` | `Mono2Sls-artifacts/mono_benchmark/bookstore` |
| m2s 产物 | `Mono2Sls-artifacts/M2S/runs/todo-oh/output` | `Mono2Sls-artifacts/M2S/runs/bookstore-oh/output` |

注意对照的前提：官方实现是**原生为 serverless 设计**的（先有架构再有代码），m2s 是**从单体
翻译**的，而单体本身是从官方实现逆向简化出来的（Redis/OpenSearch/Neptune 被退化成了 DynamoDB
Scan）。所以"m2s 比官方慢"的地方，一部分是翻译没做好，一部分是**单体已经丢掉了官方的专用基础
设施**——后者要不要补回来是架构决策，不是编码问题（§3.3）。

数据分四层（§4.1 详细说明每层能回答什么）：

- **L0 静态分析**：读代码/模板就能确定的事实（调用次数、顺序深度、Scan 出现在哪、配置值）。
- **L1 代码阶段代理指标**：`require()` 计时 + mock SDK 执行计时（`perf-tools/`）。
- **L2 sam + Docker**：`sam local` 冷/热调用（`DESIGN §5`，结论：测不出细粒度差异）。
- **L3 真实 AWS**：一次性部署 + 一次 MemorySize/Architectures A/B（`DESIGN §5`，均已拆除）。

---

## 2. 逐项对照

### 2.1 todo（6 个 CRUD 端点，每个 handler 1 次 DynamoDB 调用）

| 维度 | 官方 lambda-refarch-webapp | m2s todo-oh | 评价 |
|---|---|---|---|
| 运行时 / SDK | nodejs12.x，`aws-sdk` v2 整包 + `aws-xray-sdk-core` `captureAWS` + `aws-embedded-metrics` | nodejs22.x，`@aws-sdk/client-dynamodb` + `lib-dynamodb` v3 模块化 | m2s 更好：v3 按需加载，init 更轻 |
| 连接复用 | 显式 `AWS_NODEJS_CONNECTION_REUSE_ENABLED=1`（v2 必需） | 未设置 | m2s 没问题：v3 默认 keep-alive |
| MemorySize | **1024，注释写明来自 Lambda Power Tuning** | 512（Globals 写死） | 官方的**方法**值得学：用 Power Tuning 实测定值 |
| Architectures | 未设置（x86） | 未设置（x86） | 两者都缺；arm64 已有我们自己的真实 A/B（`DESIGN §5`） |
| 打包 | 每函数 `node_modules` | 每函数 `node_modules`（未 bundle） | 两者都缺；见 A4 |
| API 限流保护 | `UsagePlan`：Burst 100 / Rate 50 / 5000 次每天 | 无 | 官方更好（保护下游/成本，非延迟） |
| API 访问日志 | 格式里带 `$context.responseLatency $context.integrationLatency` | 只有 `requestId` + `error.message` | **官方的格式设计更好，而且直接关系到测量**：能把 E2E 拆成"API 层"和"集成（Lambda）"两段，见 B4。（注：官方模板把 `AccessLogSetting`/`MethodSettings` 写在了 `Properties` 外面，实际部署时不会生效——值得学的是格式，不是那段 YAML） |
| API 执行日志 | 意图开 `DataTraceEnabled` + `INFO`（记录完整请求/响应，有延迟和日志费用；同上，位置写错未生效） | 无 | m2s 的做法对（生产不该开 DataTrace） |
| DynamoDB | Provisioned 5/5 | `PAY_PER_REQUEST` + PITR | m2s 更适合低频/突发流量 |
| 依赖 | `uuid` v1 | `uuid` v1（照搬单体） | 两者都可换 `crypto.randomUUID()`（v4，语义需确认），见 A5 |

todo 的 handler 逻辑本身两边都是"1 次 DynamoDB 调用"，**代码层面没有值得改的性能问题**，差异
全在配置和打包层（MemorySize 定值方法、arm64、bundle、访问日志）。

### 2.2 bookstore（12 个函数，含 1 个异步消费者）

| 功能 | 官方 aws-bookstore-demo-app | 单体 | m2s bookstore-oh | 评价 |
|---|---|---|---|---|
| 畅销榜读 `GET /bestsellers` | Redis `ZREVRANGE 0 19`（O(log N + 20)） | `scan` 全表 + 内存排序取前 20 | 同单体：`ScanCommand` + sort + slice | **m2s 最大隐患**：代价随书目量线性增长，且 Scan 单页 1MB 上限，没分页，数据多了结果会**错**（不只是慢） |
| 畅销榜写 | Orders 表 **DynamoDB Streams**（BatchSize 1）→ Lambda → `ZINCRBY` | 结账后 `for…await` 逐本 `UpdateItem ADD` | 结账发 SQS → 消费者（BatchSize 10）里**两层 for 循环顺序 await** `UpdateCommand` | m2s 把单体的顺序循环放大了：一批 10 条消息 × 每单 3 本 = 30 次顺序往返 |
| 搜索 | OpenSearch `multi_match` | `scan` + 内存 `includes` 过滤 | 同单体 | 同畅销榜读：代价随数据量增长 + 1MB 截断 |
| 书目列表 | 有 category 走 GSI `Query`，否则 `Scan` | 同 | 同 | 三者一致；无分页 |
| 结账 `POST /orders` | `put` + 循环 `delete`（回调风格，未等待删除完成，`callback` 可能被多次调用——官方这里其实有 bug） | `put` → `Promise.all(delete…)` | `Put` → `Promise.all(Delete×N)` → `SendMessage`：**3 段串行**，5 次调用 | m2s 正确性比官方好；但三段串行可以压浅，见 A2 |
| 冷启动对策 | 另有一份模板加 **EventBridge 每 10 分钟 warmer**，handler 里 `if (event.source === "warmer") return` | — | 无 | **不要学**：warmer 是 Provisioned Concurrency 出现前的旧做法，只能保 1 个并发实例热 |
| 网络 | Redis/OpenSearch/Neptune 迫使部分函数进 **VPC** | — | 全部不进 VPC | m2s 更好 |
| MemorySize | 256 | — | 512（Globals 写死） | 都没实测定值 |
| DynamoDB | Provisioned 1/1（突发即限流） | — | `PAY_PER_REQUEST` | m2s 更好 |
| API 鉴权 | `AWS_IAM`（Cognito Identity Pool + SigV4） | JWT 中间件 | REST API + Cognito User Pool Authorizer，**所有端点**都挂 `DefaultAuthorizer` | 公共只读端点（书目/搜索/畅销榜）是否需要鉴权值得在 S1 阶段确认——不鉴权的端点也更容易加缓存 |
| API 访问日志 | — | — | **完全没有**（todo 至少还有一个最简格式） | 缺了 E2E 分解的数据源，见 B4 |

### 2.3 m2s 已经比官方好的地方（优化时不能倒退）

SDK v3 模块化 + nodejs22；不进 VPC；DynamoDB 按需计费；没有定时 warmer；没有 DataTrace 全量
日志；结账的删除操作用 `Promise.all` 真正等待完成。后续任何"向官方看齐"的改动都不应该把这些
退回去——尤其是**不要因为"官方有 warmer / Redis"就照搬**，前者已过时，后者要进 VPC、有常驻
费用，对这个量级的应用不一定划算（§3.3）。

---

## 3. 可改进点清单

### 3.1 优先级总表

优先级 = 预期收益 × 证据置信度 ÷ 改动风险。"证据"列标注目前最高到了哪一层数据。**注意**：
"L3 A/B"只代表 bookstore 的 orders-create 一个函数的一次实测（§4.2）；在不部署的流水线里每条
建议到底能不能被指标验证，以 §3.4 的分级为准。

| # | 改进点 | 层 | 预期收益 | 证据 | 风险 | 负责阶段 |
|---|---|---|---|---|---|---|
| A1 | SQS 消费者：聚合 + `Promise.all` + `ReportBatchItemFailures` | 代码+模板 | 批处理时长 ↓ 一个数量级；修重复计数 | L1 实验 + L3 顺序调用实测 | 低 | S2 / S3b |
| A4 | esbuild 打包 + minify | 模板 | ~~Init ↓~~ 在真实运行时容器里未见差异 | L1 Windows −40% 被实验 6 推翻；L3 待测 | 低 | S3（降级为可选） |
| B1 | `Architectures: arm64` | 模板 | warm/Init 略快 + 单价 −20% | **L3 A/B** | 极低 | S3 |
| B2 | MemorySize 分级（不低于 256，热点 512~1024） | 模板 | 128→512 快 ~5×；512→1024 再快 ~31% | **L3 A/B** | 低 | S1 `perf_hint` + S3 |
| A2 | orders-create 压浅调用链 | 代码 | 47ms → 16~31ms（代理） | L1 实验 | 中（事务/一致性取舍） | S2 |
| A3 | 热读路径全表 Scan（畅销榜/搜索/书目） | 代码+数据模型 | 读成本 O(N) → O(1)/O(20)；修 1MB 截断 | L0 + 解析估算；L1 **测不出** | 中（改数据模型） | P0 信号 + S1 + S2 |
| B4 | API 访问日志加延迟字段 | 模板 | 不直接提速，是后续所有 E2E 优化的**测量前提** | 官方模板有 | 极低 | S3 |
| B3 | SQS `MaximumBatchingWindowInSeconds` | 模板 | 低流量时减少调用次数 | 无（仍未验证） | 低 | S3 |
| B5 | API UsagePlan 限流 | 模板 | 保护，非提速 | 官方模板有 | 低 | S3（可选） |
| A5 | `uuid` → `crypto.randomUUID()` | 代码 | 少一个依赖，init 微降 | L0 | 低（v1→v4 语义） | S2 |
| C1 | REST API → HTTP API + JWT Authorizer | 架构 | 请求单价 ↓ ~3.5×；延迟**待分解后再判断** | L3 价格；延迟未分解 | 中高 | v2 |
| C2 | 异步派生数据改用 DynamoDB Streams | 架构 | 结账少 1 次同步调用 | 官方做法；L1 实验 | 中 | v2（S1 决策） |
| C3 | 公共只读 GET 缓存（API GW 缓存 / CloudFront） | 架构 | 热点读几乎不打 Lambda | 无 | 中（缓存失效语义） | v2 |
| C4 | Provisioned Concurrency（仅 `perf_hint: hot`） | 架构 | 消除 300~385ms Init | L3 Init 实测 | 有持续费用 | v2 |

### 3.2 本次新做的代理实验（L1，bookstore-oh 真实 handler，mock 每次 SDK 调用 `sleep(8)`）

脚本已存进 `perf-tools/exp_variants.js`、`perf-tools/req_time.js`，环境在短路径
`D:\tmp\m2s-perf-cmp`（避开 `MAX_PATH`，见 `DESIGN §5`）。每组取中位数。

**实验 1：esbuild 打包对 `require()` 冷加载的影响**（每组 15 个独立 node 进程）——**结论已被实验 6
推翻，保留在这里作为反例**：在 Windows 上测模块加载，结果会被文件系统的差异带偏

| handler | 未打包（现状 `node_modules`） | esbuild bundle + minify | 变化 |
|---|---|---|---|
| orders-create（DynamoDB + SQS 客户端） | 中位 94.8ms（86.8~103.4） | 中位 56.7ms（50.2~62.0），单文件 539KB | **−40%** |
| books-list（仅 DynamoDB） | 中位 83.2ms（76.6~92.8） | 中位 46.9ms（44.5~55.4），单文件 483KB | **−44%** |

限定：这是 Windows NTFS 上的模块解析耗时，文件系统开销可能比 Lambda 的 Linux 环境大，**绝对
值不能直接换算成 Init Duration 的降幅**（真实 Init 300~385ms 里还有运行时引导）。方向是可信的，
幅度要 L3 验证（§6 E1）。

**实验 2：SQS 消费者改写**（一批 10 条消息 × 每单 3 本书，5 本热门书有重叠——对应 `BatchSize: 10`）

| 写法 | 墙钟 | SDK 调用次数 | 顺序往返深度 |
|---|---|---|---|
| 现状：双层 `for…await` | 467.8ms | 30 | 30 |
| 全部 `Promise.all` | 15.7ms | 30 | 1 |
| 先按 `bookId` 聚合数量，再 `Promise.all` | 15.5ms | **5** | 1 |

`UpdateItem` 没有批量 API（`DESIGN §4`），所以正确的改写是"聚合 + 并行"而不是 `BatchWriteItem`。
聚合同时把调用数从 30 降到 5（DynamoDB 写请求费用同比下降）。**另一个正确性问题**：现状只要有
一本书更新失败就 `throw`，整批 10 条消息全部重投，已经成功的 `ADD salesCount` 会被**重复累加**。
改写时必须同时加 `FunctionResponseTypes: [ReportBatchItemFailures]` 并返回
`batchItemFailures`，只重投失败的消息。

**实验 3：orders-create 调用链**（3 本书的购物车）

| 写法 | 墙钟 | 调用次数 | 深度 | 取舍 |
|---|---|---|---|---|
| 现状：`Put` → `Promise.all(Delete×3)` → `SendMessage` | 47.2ms | 5 | 3 | — |
| 三段全部并行 | 15.8ms | 5 | 1 | ❌ 下单失败时购物车已删、消息已发，不可接受 |
| `TransactWriteItems(Put + Delete×3)` → `SendMessage` | 31.2ms | 2 | 2 | 下单与清购物车变原子（顺带修了现状"下单成功、删购物车部分失败"的不一致）；**事务写消耗 2× WCU** |
| `TransactWriteItems` 单次调用，畅销榜改由 Orders 表 Streams 触发（官方做法） | 15.3ms | 1 | 1 | 需要 S1 把异步机制从 SQS 换成 Streams（C2） |

折中方案（不改架构、不付 2× WCU）：`Promise.all([Put, BatchWrite(Delete×N)])` → `SendMessage`，
深度 2、调用 3 次，但失去"先确认下单再删购物车"的顺序保证——是否可接受要看业务语义，由 S2 按
单体原有语义决定，不能由 perf-optimizer 静默改变。

**实验 4：全表 Scan 的代价，代理指标看不见**

`bestsellers-list` 在 mock 返回 100 / 1,000 / 10,000 / 100,000 条记录时，墙钟都是 ~15.6ms，跟
"GSI Query Limit 20" 一模一样——mock 把一次 Scan 当成"一次 8ms 往返"，不管它读了多少数据。单独
测内存排序：10 万条 ~10ms，1 万条 <1.2ms，也不是主要成本。真正的成本在 mock 之外：

- **读容量**：Scan 按扫描的数据量计费。假设每条 100B，1 万本书 ≈ 1MB ≈ 256 个 4KB 单位 →
  最终一致读 ~128 RRU / 次请求；GSI `Query Limit 20` ≈ 2KB → 0.5 RRU。**每次请求 ~250 倍的读
  成本差**，且随书目量线性增长（解析估算，未实测）。
- **1MB 分页上限**：单次 Scan 最多返回 1MB，现有代码不处理 `LastEvaluatedKey`，数据超过 1MB 时
  **畅销榜和搜索结果是错的**。补分页会让延迟随数据量线性增长（多次往返）。

结论：这类"成本随数据量增长"的问题**只能靠 L0 静态规则识别**（"读路径上出现 Scan"），不要指望
L1 测出来。

**实验 5：热执行的 CPU 部分（HTTP 层 mock，2026-09-24）**

原来的 `mock-sdk-shim.js` 在 `DocumentClient.send()` 这一层拦截，SDK 的序列化、SigV4 签名、
中间件、反序列化、DocumentClient 编解码**全被跳过**。但 L3 实测里 128MB 比 512MB 慢约 5 倍、
Init 却几乎不变，说明热执行里有相当一部分是 **CPU**，不只是等网络。新的 `perf-tools/http-shim.js`
把拦截点下移到 `NodeHttpHandler.handle`，只替换网络收发，SDK 其余部分全部真实执行。
`warm_cpu.js` 在同一进程里先预热 30 次，再计时 300 次，网络延迟设为 0，取中位数：

| handler | 调用次数 | 热执行 CPU 中位数 |
|---|---|---|
| books-get-by-id / cart-add-item | 1 | ~0.25ms |
| orders-list / books-list（返回 20 条） | 1 | ~0.40ms |
| orders-create | 5 | ~0.81ms |
| bestsellers-update-consumer（现状，10 条消息 × 3 本） | 30 | ~2.9ms |
| 同上，先聚合再并行（`candidate_consumer_agg.js`） | 5 | ~0.62ms（**−78%**） |

全表 Scan 的数据量效应（这是实验 4 用 `send()` 层 mock 完全看不到的）：

| Scan/Query 返回条数 | bestsellers-list | search-books | books-list |
|---|---|---|---|
| 20 | 0.40ms | 0.39ms | 0.39ms |
| 500 | 3.2ms | 3.4ms | 3.3ms |
| 2,000 | 13.9ms | 15.3ms | 15.0ms |
| 5,000（约一个 1MB Scan 页） | 34.4ms | 37.4ms | 35.4ms |

CPU 随条数线性增长，而且主要花在 SDK 反序列化和 DocumentClient 解码上（10 万条的内存排序只要
~10ms，见实验 4），不是业务逻辑本身。改成"GSI Query Limit 20"或物化视图后，读端就回到 20 条那一行
的水平。

稳定性：同一 handler 连续 5 次独立运行，orders-create 中位数 0.80~0.85ms，books-list（2,000 条）
14.7~16.6ms，波动 ≤±7%，远好于 sleep 墙钟（被 15.6ms 时钟粒度量化）。

限定：
- 绝对值是这台开发机 CPU 的耗时。Lambda 按内存分配 CPU（512MB 约 0.3 个 vCPU），实际耗时会成倍
  放大。所以只能做**同机相对比较**，不能换算成 Lambda Duration。
- 数据量效应的**斜率是真实测出来的**，但"线上表有多少条"仍然是假设。流水线里要从单体种子数据或
  配置推断一个规模，并在报告里写明。
- 这个指标和调用轮数互补：轮数衡量"等网络"，CPU 衡量"算"。热执行 ≈ 两者之和，两个都要看。

**实验 6：sam build 产物 + Lambda 运行时容器（RIE 直连），2026-09-24**

之前 L2 的负面结论（`DESIGN §5`：三个函数的热调用都约 4.8s）以及 `sam local invoke` 静默失败，**根因是
代理配置**，不是 sam 或 Docker 本身的问题。排查过程：

1. `sam local invoke --debug`：容器正常经历 create → start → attach，被调用后由 sam 正常 kill（exit 137）；
   日志里是 `Failed to deserialize response from RIE, returning the raw response as is`，而原始响应是空的。
2. sam 源码（`samcli/local/docker/container.py`）：调用地址是 `container_host="localhost"`，端口只绑在
   `127.0.0.1` 上。
3. 同一个容器上交替对照：`localhost` 是 502、3.7s，`127.0.0.1` 是 200、4ms，两者 TCP 连接耗时相同；
   改 `Host` 头不影响结果。所以请求在到达容器之前就被别的东西接走了。
4. 环境里有 `HTTP_PROXY=http://127.0.0.1:21081`，没有 `NO_PROXY`（来自
   `Documents\WindowsPowerShell\Microsoft.PowerShell_profile.ps1`）。Windows 系统代理的例外列表里虽然有
   `localhost`，但 curl/`requests` 只看环境变量。`curl --noproxy '*' http://localhost:...` 返回 200。
5. 修复：在配置文件里加 `$env:NO_PROXY = "localhost,127.0.0.1,::1"`（已加，原文件备份为 `.bak-2026-09-24`）。
   修复后在新开的 PowerShell 里，不加任何参数 `sam local invoke` 就能返回 200 并打印 REPORT；
   `start-lambda` 的热调用客户端耗时约 22~47ms（之前是 4.8s）；`start-api` 从 502 变成 handler
   自己返回的 500（本地确实不模拟 Cognito，`claims` 为 undefined，被 handler 的 try/catch 捕获）。

结论：`DESIGN §5`"sam CLI + Docker 阶段实测"里的冷/热耗时表，以及"start-api 在 Cognito 路由上 502"
这两条记录**测到的是失败的请求**，应当作废，而不只是"精度不够"。

虽然 sam 已经修好，测量仍然建议用下面的 RIE 直连方式：sam 会把代码绑定挂载进容器，冷启动因此约
1.4~2.2s，不能代表真实的初始化耗时。

做法（`perf-tools/rie/`）：`sam build` 之后，直接 `docker create` 运行时镜像
`public.ecr.aws/lambda/nodejs:22-x86_64`，把构建产物 `docker cp` 进去（绑定挂载会让初始化慢到约 1.6s），
用 `NODE_OPTIONS=--require` 预加载 HTTP 层 mock，调用 1 次冷 + 30 次热，读容器日志。RIE 的
`Init Duration` 字段是占位值（0.03ms），`Max Memory Used` 只回显配置值，这两个都不能用；初始化耗时取 rapid 的
`INIT REPORT(durationMs)`。

基线与区分度（不限 CPU，3 次独立运行）：

| 函数 | init_ms | 热调用 Duration 中位数 |
|---|---|---|
| Noop（空函数，只加载 mock） | 62 | 1.3ms（RIE 固定开销） |
| books-get-by-id（1 次调用） | 93~108 | 3.0~3.5ms |
| orders-create（5 次调用） | 101~115 | 4.0~5.4ms |

运行之间波动约 ±10~15%，比 `warm_cpu.js`（≤±7%）大，但数量级差异看得很清楚。

改写候选（CPU 限额 0.289 ≈ 512MB，每次调用模拟 5ms 延迟；Linux 里 `setTimeout` 是精确的）：

| 场景 | 热调用 Duration 中位数 |
|---|---|
| bestsellers-update-consumer 现状（30 次顺序调用） | 188.8ms |
| 先聚合再并行（5 次并行） | **9.9ms** |
| bestsellers-list，Scan 返回 20 条 | 8.7ms |
| bestsellers-list，Scan 返回 2,000 条 | 19.4ms（p90 65ms，受 CPU 限额节流影响） |

用 `--cpus` 模拟 MemorySize（1769MB = 1 vCPU，orders-create，每次调用 5ms 延迟）：

| 等效内存 | 热调用中位数 | 真实 A/B（`DESIGN §5`） |
|---|---|---|
| 128MB（0.072 CPU） | 115.9ms（p10 41 / p90 164，双峰） | ~174ms |
| 512MB（0.289） | 21.9ms | ~35ms |
| 1024MB（0.579） | 21.7ms | ~24ms |

- 能复现"128MB 断崖式变慢"这个方向；
- **复现不了 512→1024 的 ~31% 提升**：这台开发机单核比 Lambda 快，单次调用的 CPU 工作量在 0.289 核的
  配额内就做完了；
- CPU 限额下的初始化（128MB 档约 2.4~3.0s）**不代表真实情况**：真实 Lambda 的 Init 几乎不随内存变化。

**esbuild 打包在真实运行时里的初始化**（orders-create，不限 CPU，5 次交替运行）：未打包 108~117ms
（有一次异常值 182ms），esbuild bundle + minify 101~117ms，**没有可见差异**。实验 1 在 Windows 上
测到的 `require()` −40%，主要来自 NTFS 上模块解析慢，不能代表 Lambda 的 Linux 环境。模块加载本身在
这里只占约 45~50ms（对比空函数的 62ms）。

**附带发现：mock 墙钟在 Windows 上被量化。** `setTimeout(8)` 实测 14.5ms；上面所有"单次调用"
结果都卡在 ~15.6ms（Windows 默认时钟粒度）。`DESIGN §5` 里单调用 handler 的 11~24ms 也是这个
原因。所以 L1 的墙钟只能用来看**倍数关系**，不能用来判断 ±10% 这种量级的差异——这直接影响
perf-optimizer 的判据设计（§4.3）。

### 3.3 架构级改进（C 类）为什么留到 v2

单体已经把官方的 Redis / OpenSearch 退化成了 DynamoDB Scan。m2s 是否要"补回"专用基础设施，是
**成本与规模的取舍**，不是编码问题：

- 畅销榜：不需要补回 Redis。DynamoDB 原生方案更合适——(a) 稀疏 GSI（固定分区键如 `"ALL"` +
  `salesCount` 排序键），`Query ScanIndexForward=false Limit=20`；或 (b) 消费者在更新计数后顺带
  维护一条"Top-20 物化视图"记录，读端变成单次 `GetItem`。(a) 的写入集中在 GSI 单分区，高写入
  量时会成为热点；对 demo 规模不是问题。
- 搜索：OpenSearch（含 Serverless）有常驻最低费用，小书目用 Scan + 缓存反而更划算。这需要一个
  **数据量信号**：P0 分析时能否估计表规模（例如从单体的种子数据脚本 `scripts/init-db.js` 推断），
  S1 据此选择方案。没有这个信号时，至少要给 Scan 加分页和 `ProjectionExpression`，并在
  `perf_report.json` 里标注"随数据量线性增长"。
- 异步派生数据（畅销榜计数）：Streams 比 SQS 少一次同步调用（实验 3），且 Lambda 读 Streams 不
  收请求费；代价是 Streams 按分片顺序处理、只能 24 小时回溯。这是 S1 的 `sqs_queues` vs
  Streams 选择，要在 blueprint 层决定。

---

### 3.4 证据等级：哪些改动在"不部署"的流水线里能被指标验证

真实流水线的前提：生成阶段不部署到 AWS；sam+Docker 实测给不出细粒度性能信息（`DESIGN §5`）。
所以流水线内部能用的判据只有 **L0 静态分析**、**L1 `require()` 冷加载**、**L1 mock 调用日志
（调用次数、顺序往返深度）**。按这个标准重新给每条建议定级：

| 等级 | 含义 |
|---|---|
| **M（可测）** | 流水线内的代码阶段指标能判断改动**是否正向**，可以放进 perf-optimizer 自查循环"改→测→留/撤" |
| **M−（可测但信号弱）** | 能测，但效应小于噪声，或测量结果取决于 mock 输入的假设 |
| **E（经验）** | 代码阶段指标测不了或测不出，依据是官方参考实现的做法 / AWS 公开资料 / bookstore 一次 L3 实测。只能做成**默认值规则或 advisory**，不能声称"已验证有正向效果" |

| # | 改进点 | 等级 | 用什么指标 / 为什么测不了 |
|---|---|---|---|
| A1a | SQS 消费者循环改 `Promise.all` | **M** | 顺序往返深度 30 → 1，调用次数不变；确定性 |
| A1b | 消费者先按 key 聚合再写 | **M**（方向）/ 幅度依赖假设 | 调用次数 30 → 5，热执行 CPU 2.9ms → 0.62ms（实验 5）。能确定是正向的；但**降幅取决于 mock 事件里 bookId 的重复率**（本次人为设定为 5 本热门书），真实收益大小要看真实订单分布 |
| A1c | `ReportBatchItemFailures` | 非性能项 | 是正确性修复（避免重复 `ADD`）。L0 能检查"配没配"，mock 测不出任何性能差异 |
| A2 | orders-create 压浅调用链 | **M**（深度/次数/CPU）+ **E**（取舍） | 深度 3 → 2/1、调用 5 → 2/1 可测，CPU 用 `warm_cpu.js` 可测（现状 ~0.81ms）；但事务写 2× WCU 的成本、下单与清购物车的一致性语义都是指标看不到的，需要 S2 按业务语义拍板 |
| A4 | esbuild 打包 + minify | **可测，结果为无效** | Windows `require()` −40%；但在 RIE 容器（真实 Linux + nodejs22 运行时）里测 init，打包与否没有差异（实验 6）。以后打包类改动应以 RIE 的 init 为准，不看 Windows 上的 `require()` |
| A5 | `uuid` → `crypto.randomUUID()` | **M−** | 单独测：`require('uuid')` ~10ms vs 内置 `crypto` ~3ms；但放进完整 handler（~90ms，波动约 ±8ms）里落在噪声带内。且改变了 ID 的版本（v1→v4），属于语义变化 |
| A3 | 热读路径全表 Scan → GSI / 物化视图 / 分页 | **M**（CPU，需给定数据量）+ **E**（RCU 费用、分页往返） | `send()` 层 mock 测不出（实验 4）；HTTP 层 mock 能测出热执行 CPU 随条数线性增长（实验 5：20 条 0.39ms → 5000 条 ~35ms），改成 Query Limit 20 后回到 0.4ms。仍然测不了的是 RCU 费用和超过 1MB 时的多次分页往返；"线上有多少条"是假设 |
| B1 | `Architectures: arm64` | **E** | 本机 x86 上跑 arm64 是 QEMU 模拟，不可信；依据是 AWS 公开基准 + bookstore 一次 A/B |
| B2 | MemorySize 分级 | 下限 **M−**，其余 **E** | RIE + `--cpus` 能复现"128MB 断崖式变慢"（实验 6），可以用来查"内存给得太小"；但复现不了 512→1024 的差异，也不能拿它来定具体档位。依据仍然是 bookstore 一次 A/B + 官方 todo 用 Power Tuning 定值 |
| B3 | SQS `MaximumBatchingWindowInSeconds` / `BatchSize` | **E**（且连 L3 都没有） | 本地不模拟 SQS 轮询攒批；也从未真实测过 |
| B4 | API 访问日志加延迟字段 | 非性能项 | 部署后的**测量手段**，本身不改变性能 |
| B5 | UsagePlan 限流 | 非性能项 | 保护性配置 |
| C1 | REST → HTTP API | **E** | 价格差是公开定价（确定）；延迟差没有任何可信数据（§4.4） |
| C2 | 异步派生改 DynamoDB Streams | **M**（深度）+ **E**（架构取舍） | 结账少一次同步调用可测；Streams 的顺序处理/24h 保留等取舍测不了 |
| C3 | 公共只读 GET 加缓存 | **E** | 命中率取决于真实流量，代码阶段无从测量 |
| C4 | Provisioned Concurrency | **E** | 纯部署配置 + 费用决策 |
| §2.3 | 防倒退清单（SDK v3、不进 VPC、不用 warmer…） | 部分 **M** | SDK v3 vs v2 可以用 `require()` 测（未测）；VPC、warmer 属于 E |

**结论**：在不部署的前提下，能靠指标闭环的是三类改动——**改变调用链结构的代码改写**
（看调用次数 + 顺序深度）、**改变热执行 CPU 的改写**（批量/聚合、减少返回条数，看 `warm_cpu.js`）
，以及它们在真实运行时里的效果（RIE 容器内的 `REPORT Duration`，实验 6）。打包/依赖改动**可以测**
（RIE 的 init），但这次测出来 esbuild 没有效果。其余所有配置类、
架构类建议，流水线只能按规则执行并在报告里如实标注为"经验默认值，未经本次测量验证"。

**还有一层没被验证的假设**：即便是 M 类，指标说的也只是"代理指标变好了"。"顺序深度下降 →
真实 Duration 下降"这条映射只在 bookstore 上有一次方向性印证（L3：books-list 深度 1 ≈ 8.9ms，
orders-create 深度 3 ≈ 35~45ms）；"`require()` 下降 → Init Duration 下降"已经被实验 6 证明在
Windows 上不成立；"本机热执行 CPU 下降 → Lambda Duration 下降"方向上有 L3 间接支持（128MB 慢 5 倍
说明 CPU 占比不小），但没有直接对照。

## 4. 如何用实测数据指导优化

### 4.1 四层数据各自能回答什么（已被实测检验过的结论）

| 层 | 能回答 | 不能回答 | 已验证的可信度 |
|---|---|---|---|
| L0 静态 | 调用次数、顺序深度、Scan 位置、依赖清单、配置值是否符合默认规则 | 任何毫秒数 | 确定性，无噪声；**最适合放进自动自查循环** |
| L1 代理 | `require()` 冷加载的相对变化；调用次数 + 顺序深度（等网络部分）；HTTP 层 mock 下的热执行 CPU（含随数据量的变化） | sleep 墙钟的 ±10% 量级差异（Windows 15.6ms 时钟粒度）；Lambda 上的绝对毫秒数；RCU 费用；并发/限流 | 方向与 L3 一致（books-list vs orders-create：L1 约 2~4×，L3 约 5×，`DESIGN §5`） |
| L2 sam+Docker（经 `sam local`，已设置 `NO_PROXY`） | 端到端能不能跑通（功能验收）；`start-lambda --warm-containers` 下的热调用 REPORT | 冷启动（绑定挂载会让它变慢）；Cognito（不模拟，handler 拿不到 `claims`） | 修复代理配置之前的数据全部作废 |
| L2′ sam build + RIE 直连 | 真实 Linux 运行时里的热调用 Duration（固定开销约 1.3ms）；初始化（不限 CPU 时做相对比较）；打包效果；用 `--cpus` 发现"内存太小"的断崖 | 512 以上内存档位之间的差异；CPU 限额下的初始化；arm64（仍是 QEMU）；`Max Memory Used`（RIE 不统计） | 与 L1 方向一致，并纠正了 L1 的 esbuild 结论；运行之间波动约 ±10~15% |
| L3 真实 AWS | 真实 Init/Duration、MemorySize/架构曲线、E2E、价格 | 自动化闭环（分钟级部署 + 需要人工授权 + 结果有调度噪声） | 权威，但每次都要人工授权、测完拆除 |

### 4.2 L3 数据的适用范围：只能当先验，不能当普适模型

L3 一共两次实测（`DESIGN §5`），数据来源非常窄：

| 限制 | 具体情况 |
|---|---|
| 应用 | 只有 bookstore；todo 从未部署过 L3 |
| 函数 | MemorySize/架构 A/B 只测了 orders-create 一个函数（IO 密集型，5 次 SDK 调用） |
| 样本量 | 每档 1 次冷 + 3~4 次热调用；同一函数两次测量的 512MB warm Duration 就差了 ~35ms vs ~45ms |
| 环境 | 一个账号、us-east-1、同一天 |

所以下面这些值**是 bookstore 的观测值，不是可以套到任意应用上的常数**：

| 观测 | 取值（bookstore） | 可推广的部分 | 不可推广的部分 |
|---|---|---|---|
| 单次 DynamoDB/SQS 往返（热，512MB） | ~8~15ms | "顺序深度越大，Duration 越长" | 具体毫秒数：随 item 大小、操作类型、表、区域变化 |
| Init Duration | 300~385ms，几乎不随内存变化 | "内存大小不影响模块加载"与 AWS 文档一致 | 具体值：取决于依赖体积，换一个应用就变 |
| 内存系数 | 128MB ≈ ×5，1024MB ≈ ×0.7 | "128MB 对用 SDK v3 的函数明显偏慢；512 以上边际递减"是方向 | 倍数：CPU 密集型和纯 IO 函数的曲线形状不同 |
| arm64 vs x86 | 不劣于 x86 | 与 AWS 公开基准方向一致 | 幅度 |

因此上一版这里给出的 `est_warm_ms ≈ depth × t_rtt × mem_factor` **不能当收益预测模型用**。能做的
是更弱的事：

- **排序**：同一个应用内，用顺序深度和调用次数的**差值**来决定先改哪个（深度 30→1 的肯定排在
  3→2 前面）。这只依赖"深度和 Duration 正相关"这个方向，不依赖任何常数。
- **报告里不写"预计提升 X ms / X%"**，只写"顺序深度 30→1、调用次数 30→5"这类直接测得的量。

要让 L3 常数有普适性，需要在更多应用上重复测量（§6 E0）。仓库里现成的候选是
`mono_benchmark/` 下还没跑过流水线的 airline-booking、coffee、ecommerce、shopping-cart——先跑出
m2s 产物，在它们上面做 L0/L1，至少能先回答"bookstore 上发现的反模式（循环内顺序调用、热路径
Scan）在其它应用里有多常见"。

### 4.3 perf-optimizer 的判据改用确定性指标

`DESIGN §3` 步骤 4 原来的判据是"相关墙钟指标变好 + 其它指标不在 ±10~15% 噪声带外变差"。实测
表明本地墙钟做不到这个精度，建议改成：

1. **代码改动**（批量/并行/聚合/调用链）：判据是 L0 确定性指标——`sdk_call_count` 不增加、
   **顺序往返深度**下降，且 L1 冒烟跑通（返回 2xx，`sdk_call_count > 0`，即 `DESIGN §3` 步骤 1
   的有效性校验）。深度已经实现：`mock-sdk-shim.js` 记录每次调用的 `[start, end]`，
   `getCriticalPathDepth()` 返回"后一次在前一次结束后才开始"的最长调用链长度。在实验 2/3 上
   测得 30/1/1 和 3/1/2/1，跟人工读代码的结论完全一致，且不受时钟粒度和机器负载影响。
2. **打包改动**（esbuild、依赖删减）：判据是 **L2′ RIE 的 init_ms**（不限 CPU，≥5 次交替运行），下降
   超过 15% 才算有效。不要用 Windows 上的 `require()`：实验 1 测到的 −40% 在实验 6 里消失了。
   Windows 上的 `require()` 只能当快速冒烟检查。
3. **热执行 CPU**（批量/聚合、减少 Scan/Query 返回条数、减少编解码）：判据是 `warm_cpu.js`
   中位数（300 次热调用）下降超过 10%，且其它 handler 没有超过 10% 的回升——同一 handler 5 次独立
   运行波动 ≤±7%，这个阈值是站得住的。涉及数据量的改动，要在报告里写明用的是多少条的假设。
4. **配置改动**（MemorySize/arm64/SQS 参数）：L1/L2 都测不了，直接按 L3 已验证的默认值规则
   执行，不在自查循环里"测"。
5. **L0 静态规则命中但无法自动修的问题**（热路径 Scan、需要改数据模型或异步机制的）：不改，写进
   `perf_report.json` 的 `advisories`，附 §3.2 实验 4 那种解析估算，交给 S1/人工决策。

这样一轮自查循环里全是确定性或大效应量的判据，不会出现"在噪声里空转"。

### 4.4 E2E ~900ms 受本机网络影响，由它推出的结论撤回

上一版根据"E2E ~900ms、Lambda 只占 9~45ms"得出"同步端点的 Lambda 内部优化对用户延迟贡献很小"。
这个结论**不成立**：~900ms 是从测试者本机（网络较慢、到 us-east-1 跨洋）用客户端计时量出来的，
里面网络往返和 TLS 握手占了多少完全没分解。换一个网络正常、与服务同区域或邻近区域的客户端，
E2E 可能只有一两百毫秒，这时 orders-create 的 35~45ms 就占到相当大的比例。

哪些数据受本机网络影响、哪些不受：

| 数据 | 受本机网络影响？ | 结论还成立吗 |
|---|---|---|
| E2E Latency（~900ms，冷 1.6~1.8s） | **是**，客户端计时 | 只能当"这台机器上的用户体验"，不能用来判断瓶颈在哪一层 |
| Throughput（20 并发 13/20 与 20/20） | **很可能**，失败也可能是客户端侧超时/连接问题 | 本来就只当弱信号，现在更弱 |
| Lambda `Duration` / `Init Duration` | 否，AWS 内部计时（Lambda 调 DynamoDB 走 AWS 内网） | 成立 |
| MemorySize / arm64 A/B | 否，同样读 `REPORT` 行 | 成立（但见 §4.2 的样本限制） |
| L1 代理实验 | 否，纯本地 | 成立 |

修正后的优先级原则：**不按 E2E 占比来排优先级**，而是按 §3.4 的证据等级——M 类（调用链结构、
打包）优先做，因为能在流水线里验证；E 类按默认值规则执行，并如实标注。同步端点和异步消费者的
调用链优化同等对待；消费者的深度差距（30→1）比同步端点（3→2）大，按深度差值排序时自然排在前面。

### 4.5 E2E 分解：先补测量手段，再决定 HTTP API 值不值得换

`DESIGN §5` 把 ~850~1050ms 归因为"API Gateway + Cognito Authorizer + 本机到 us-east-1 的网络
往返"，但这三部分当时**没有被分开测**，而且测试客户端的网络本身偏慢（§4.4），所以目前**没有
任何数据**能说明 API 层（含鉴权）占多少、换 HTTP API 能省多少延迟。在 v2 评估 HTTP API 之前：

1. **模板里加访问日志延迟字段**（B4，照官方 lambda-refarch 的做法）：至少记录
   `$context.responseLatency`（API GW 收到请求到返回的总时长）和 `$context.integrationLatency`
   （集成即 Lambda 部分）。两者之差 = API 层自身开销（含鉴权）；客户端 E2E 减去
   `responseLatency` = 网络部分。这不需要 X-Ray，也回答了 `DESIGN §7` 里"X-Ray 开销 vs 诊断
   价值"的顾虑——分解 E2E 这件事不依赖 X-Ray。
2. **压测客户端放在同区域**（CloudShell 或一个临时 Lambda/EC2），排除本机网络。
3. 再做 REST vs HTTP API 的 A/B（§6 E3）。价格差（$3.50 vs $1.00 每百万请求）是确定的，延迟差
   要以分解后的 API 层开销为准。

### 4.6 L3 真实 A/B 的标准流程（沿用 `DESIGN §5` 已跑通的做法）

1. 独立临时栈（如 `m2s-perf-ab-<实验名>`），只在待比较的维度上不同，代码用 m2s 原产物。
2. 每个变体：1 次冷调用（记 `Init Duration`）+ ≥5 次热调用，丢掉前 1~2 次热调用（`DESIGN §5`
   观察到前几次还在收敛），报中位数和范围。
3. 读 CloudWatch `REPORT` 行，不用客户端墙钟。
4. 拆除：`sam delete` + 手动删 `DeletionPolicy: Retain` 的表/用户池，核实栈、函数、表都不存在。
5. 内存曲线可以直接用 **AWS Lambda Power Tuning**（官方 todo 的 `MemorySize: 1024` 就是这么来的）
   代替手工部署多个变体。
6. 需要人工授权，不进自动自查循环——L3 的角色是**校准 §4.2 的常数、验证默认值规则**，不是每次
   迁移都跑。

---

## 5. 对 m2s 流水线的具体改动建议

| 阶段 / 文件 | 改动 | 对应改进点 |
|---|---|---|
| P0 `analyze_project` | 新增静态信号：循环内 `await` 单条 DB 调用（区分顺序 vs `Promise.all`，`DESIGN §4`）；读路径上的 `scan`；可推断的表规模（种子数据量） | A1、A3 |
| S1 `m2s-architect.md` | `perf_hint` 之外，异步派生数据的机制选择（SQS vs Streams）写明理由；公共只读端点是否需要鉴权 | C2、C3 |
| S2 `m2s-performance-kb` | 补 4 条规则：① SQS/Streams 消费者"先聚合再并行"+ 部分批失败上报（含重复 `ADD` 的正确性理由）；② 多步写入的调用链深度与 `TransactWriteItems` 取舍（2× WCU）；③ 读路径 Scan 必须处理 `LastEvaluatedKey` 并在报告里标注；④ 不要 warmer、不要 v2 SDK、不进 VPC（§2.3 防倒退） | A1、A2、A3 |
| S3 `m2s-sam-kb` | Globals 加 `Architectures: [arm64]`；MemorySize 按 `perf_hint` 分级（下限 256）；~~函数加 esbuild `Metadata`~~（实验 6 未见收益，暂不作为默认）；SQS 事件加 `FunctionResponseTypes: [ReportBatchItemFailures]`（`MaximumBatchingWindowInSeconds` 先给参考值，标注未验证）；API 加带延迟字段的 `AccessLogSetting` | A4、B1~B4 |
| S3p perf-optimizer（`DESIGN §3`） | 判据改为 §4.3 的确定性指标；输出区分"已自动修复"和 `advisories` | 全部 |
| `gate(perf)`（advisory） | 可以做成纯 L0 检查：arm64、MemorySize ≥256、SQS 消费者有 `ReportBatchItemFailures`、读路径 Scan 有分页、无循环内顺序 `await` 单条调用 | A1、A3、B1~B3 |

（暂不采用，除非 E1 在真实 Lambda 上测出收益）esbuild 的 SAM 写法（每个函数一段，`sam build` 时需要本机有 esbuild）：

```yaml
    Metadata:
      BuildMethod: esbuild
      BuildProperties:
        Minify: true
        Target: es2022
        EntryPoints:
          - handler.js
```

落地前要确认两件事：`gate(template)` / S4 的一致性检查不会因为多了 `Metadata` 段误报；
`Handler: handler.lambda_handler` 在打包产物里仍然能解析到（esbuild 输出保留 `handler.js` 文件名
和 CommonJS 导出）。

---

## 6. 下一批真实 A/B 实验（按信息价值排序）

| # | 实验 | 变体 | 回答的问题 |
|---|---|---|---|
| E0 | 普适性（不需要部署） | 对 `mono_benchmark/` 里的 airline-booking / coffee / ecommerce / shopping-cart 跑流水线，产物上做 L0 扫描 + L1 深度/`require()` 测量 | bookstore 上发现的反模式在其它应用里有多常见；M 类判据在其它代码库上是否同样稳定 |
| E1 | 打包方式 | orders-create：未打包 / esbuild minify / esbuild + `@aws-sdk` 标记 external（用运行时自带 SDK） | 实验 6 显示本地 Linux 运行时里没有差异；E1 只需要确认真实 Lambda 上也一样（真实环境下代码从 S3 下载，包的体积可能还有影响），优先级下调 |
| E2 | SQS 消费者 | 现状 vs 聚合+并行；`BatchSize` 1/10 × `MaximumBatchingWindowInSeconds` 0/5 | A1 的真实收益；补上 `DESIGN §7` 唯一没验证的 SQS 参数 |
| E3 | E2E 分解 + API 类型 | REST vs HTTP API，都开延迟访问日志，客户端在同区域 | ~900ms 的真实构成；HTTP API 的延迟收益是否存在 |
| E4 | 畅销榜读 | 灌 1 万条数据后：Scan+排序 vs 稀疏 GSI Query vs 物化视图 GetItem | 实验 4 的解析估算（~250× RRU）是否成立 |

E0 不需要 AWS，应最先做——它决定这份文档的结论是"bookstore 特例"还是"m2s 的普遍问题"。
E1、E2 改动小、结论能直接写进默认规则；E3 决定 v2 最大的一项架构改动；E4 决定 A3 的
推荐方案。

---

## 7. 对 `PERF_OPTIMIZATION_DESIGN.md` 的修正

1. `DESIGN §5`"真实 AWS 部署实测"里 E2E 的归因应理解为"API 层 + 鉴权 + 客户端网络的**合计**，
   未分解"，HTTP API 的延迟收益目前没有证据，只有价格收益有（§4.5）。
2. `DESIGN §5` 表格里单调用 handler 的 11~24ms 主要是 Windows 时钟粒度造成的，不代表 handler
   之间的真实差异（§3.2 附带发现）。
3. `DESIGN §3` 步骤 4 的 ±10~15% 噪声带判据，对代码级改动改用 §4.3 的确定性指标。
4. `DESIGN §5` 和 `DESIGN §7` 里基于 E2E ~900ms 的"绝大部分时间花在 API Gateway + Cognito"
   以及"HTTP API 是 v2 收益可能最大的一项"这两个判断，都依赖一个没分解、且受测试者本机慢网络
   影响的数字，应降级为"待验证"（本文 §4.4）。
5. `DESIGN §2.3` 把 MemorySize/arm64 描述为"已用真实 AWS 验证"——准确说法是"在 bookstore 的一个
   函数上有一次方向一致的实测"，不代表对其它应用有同样幅度的收益（本文 §4.2）。
6. `DESIGN §5` 里"sam CLI + Docker 这套本地测量法测不出细粒度优化"的结论，只适用于 `sam local`
   客户端墙钟这种测法。绕开 `sam local invoke`、直接读 RIE 容器内部的 REPORT 行，就能测出细粒度差异
   （本文 §3.2 实验 6）。
7. `DESIGN §5`"sam CLI + Docker 阶段实测"整节的数据（冷 22~38s、热约 4.8s、start-api 502）是在
   `HTTP_PROXY` 没有配 `NO_PROXY` 的环境下测的，sam 发给容器的请求被代理截走了，**测到的是失败的
   请求**，应当作废（本文 §3.2 实验 6）。`DESIGN §5`"MemorySize / Architectures 真实 A/B 实测"开头提到
   的本地 `sam local invoke` 失败，很可能也是同一个原因。
