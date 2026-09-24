# M2S 生成代码的性能改进：对照官方参考实现 + 用实测数据指导优化

> 2026-09-24 重写版。上一版是边做实验边追加的，新结论只改了一部分章节，导致前后不一致；本版按
> **当前结论**整体重写，原则是：
> - 测量层级只在 §1 定义一次；
> - 作废或已纠正的数据集中列在 §1.3；
> - 每条建议只在 §4 的一张表里定级；
> - 判据（§5）和各层噪声（§1.2）对应起来。
>
> 上一版的逐步推导过程保留在 git 历史里（`f7cb51b` 及之前）。
>
> 本文是 `PERF_OPTIMIZATION_DESIGN.md`（下称 `DESIGN`，v1 设计稿）的续篇。引用 `DESIGN §5` 时一律写到
> **小节名**，因为 §5 下面几个小节的数据来源和有效性不同。

## 0. 结论摘要

1. **m2s 的产物是"忠实翻译"，单体里的性能债被原样搬进了 Lambda。** bookstore 单体里有三处慢写法，m2s 一处
   没改：畅销榜逐本顺序 `UpdateItem`、`scan` 全表 + 内存排序取 Top-20、`scan` 全表 + 内存过滤做搜索。官方
   参考实现用 Redis 有序集合、OpenSearch、DynamoDB Streams 解决了同样的问题。
2. **在基础设施"卫生"上，m2s 比官方更好**：SDK v3、nodejs22、按需计费、不进 VPC、不用定时 warmer。这些
   优化时不能改坏（§2.3）。
3. **"不部署到 AWS"的前提下，能用来判断改动是否有效的是 L0、L1、L2′ 三层**（§1），不是只有 L0/L1。L2′ 指
   `sam build` 之后直接在 Lambda 运行时容器里读计时，需要本机有 Docker；没有 Docker 时退回 L0/L1。
4. **按"能否在流水线里验证"分，性能建议主要是两类**（完整分级见 §4，另有 M−、正确性配套、测量/保护三种）：
   - **可测（M）**：改变调用链结构的代码改写（消费者并行/聚合、orders-create 压浅调用链、热读路径去掉全表
     Scan），用顺序深度、热执行 CPU、L2′ 热调用来判断；打包/依赖改动用 L2′ 的 init 来判断。其中 **esbuild
     打包已经测过，结果是无效**。
   - **经验（E）**：arm64、MemorySize 档位、SQS 批参数、HTTP API、缓存、Provisioned Concurrency。只能做成
     默认规则，报告里要注明"未经本次测量验证"。MemorySize 有一个例外：**"不能低到断崖"这个下限可以用
     L2′ 检查**（M−）。
5. **判据用确定性指标或大效应量指标，不用本地墙钟的小百分比**（§5）。原因有两个：Windows 上
   `setTimeout(8)` 实际要 14.5~15.6ms，sleep 模拟出来的墙钟被量化了；L2′ 运行之间的噪声约 ±3ms。
6. **L3（真实 AWS）数据很窄**：只有 bookstore 一个应用，MemorySize/架构 A/B 只测了 orders-create 一个函数，
   每档 3~4 次调用。所以它只能提供**方向**，不能当收益模型的参数（§6）。
7. **E2E ~900ms 是在测试者本机的慢网络上、用客户端计时量出来的合计数字，从没分解过**。它不能用来判断瓶颈在
   哪一层，也不能用来给优化排优先级（§6.3）。

---

## 1. 测量层级（本文唯一的定义处）

### 1.1 各层是什么、能回答什么

| 层 | 做法 | 能回答 | 不能回答 | 不部署时可用？ |
|---|---|---|---|---|
| **L0 静态分析** | 读代码/模板 | SDK 调用次数；**顺序深度**（后一次调用必须等前一次返回才能发出，这样的调用链最长有几轮）；Scan 出现在哪；依赖；配置值 | 任何毫秒数 | 是 |
| **L1a `require()` 计时** | 本机 Node 进程加载 handler | 仅作冒烟检查（模块能否加载） | **打包/依赖改动的效果**：Windows NTFS 上的模块解析会把结果带偏（§1.3 第 2 条） | 是 |
| **L1b `send()` 层 mock** | `perf-tools/mock-sdk-shim.js` | 调用次数、顺序深度（`getCriticalPathDepth()`，确定性） | 毫秒数（sleep 被 Windows 时钟粒度量化）；SDK 自身的 CPU 开销（被跳过） | 是 |
| **L1c HTTP 层 mock** | `perf-tools/http-shim.js` + `warm_cpu.js` | 热执行的 **CPU 部分**：SDK 序列化、签名、反序列化真实执行；能看出随 Scan 条数增长的成本 | Lambda 上的绝对值（本机 CPU 比 Lambda 按内存分到的快） | 是 |
| **L2 `sam local`** | `sam local invoke/start-lambda/start-api` | 端到端能否跑通（功能验收） | 冷启动（sam 会把代码绑定挂载进容器，冷启动被拖到 1.4~2.2s）；Cognito（不模拟，handler 拿不到 `claims`） | 是（需要 Docker，并且要配好 `NO_PROXY`，见 §1.3） |
| **L2′ RIE 直连** | `sam build` 后直接用 Lambda 运行时镜像起容器，`docker cp` 产物进去，读容器内的 `REPORT Duration` 和 rapid 的 `INIT REPORT`（`perf-tools/rie/`） | 真实 Linux + nodejs22 运行时里的热调用 Duration（按顺序深度分组）；init 的相对比较（不限 CPU、交替多次）；用 `--cpus` 发现"内存太小"的断崖 | 同一深度组内的细小差异；512MB 以上各档之间的差异；CPU 限额下的 init；arm64（本机只能 QEMU 模拟）；`Max Memory Used`（RIE 不统计） | 是（需要 Docker） |
| **L3 真实 AWS** | 一次性部署 / A/B 临时栈，读 CloudWatch `REPORT` | 真实的 Init/Duration、内存曲线、架构、价格 | 自动化闭环（分钟级部署、需要人工授权、要拆除） | **否** |

### 1.2 各层的噪声和分辨率（§5 的阈值都以此为依据）

| 指标 | 实测噪声 | 来源 |
|---|---|---|
| L0 调用次数 / 顺序深度 | 无（确定性） | — |
| L1c `warm_cpu.js` 中位数（300 次热调用） | 同一 handler 5 次独立运行 ≤ ±7% | 实验 5 |
| L2′ 热调用中位数（配置 B：0.289 CPU + 5ms/调用） | 同深度组内约 ±3ms（双峰，约 7.4ms 或约 10.4ms）；不同深度组之间不重叠 | 实验 7 |
| L2′ init（不限 CPU） | 单次运行 82~200ms，12 个函数之间看不出差别；同一函数 5 次交替运行，范围约 ±8%（另有偶发异常值，如 182ms） | 实验 6、7 |
| L1a `require()`（Windows） | 组内约 ±9%，但**有系统性偏差**（NTFS） | 实验 1、6 |
| L1b sleep 墙钟（Windows） | 被 15.6ms 时钟粒度量化 | 实验 4 附带发现 |

### 1.3 作废或已纠正的数据（集中列在这里，其它章节不再重复）

| # | 数据 | 问题 | 处理 |
|---|---|---|---|
| 1 | `DESIGN §5`"sam CLI + Docker 阶段实测"整节：冷 22~38s、热约 4.8s、三个函数完全相同、start-api 返回 502 | 测量时环境里设了 `HTTP_PROXY`、没设 `NO_PROXY`，sam 发往 `localhost` 的调用请求被本地代理截走，约 3.7s 后返回 502/空响应。**测到的是失败的请求，函数根本没执行**（排查见实验 6） | **作废**。替代数据是实验 7。代理已修复（PowerShell 配置文件里加了 `NO_PROXY`） |
| 2 | 实验 1：Windows 上 esbuild 打包让 `require()` 快 40% | 在 L2′ 真实 Linux 运行时里测 init，打包与否没有差异（实验 6），这 40% 来自 NTFS 上模块解析慢 | **结论推翻**。L1a 不再用来判断打包效果 |
| 3 | `DESIGN §5`"两个反模式的实测差异"：单调用 handler 11~24ms | 这是 L1b sleep mock 的墙钟，被 Windows 15.6ms 时钟粒度量化了（**和第 1 条无关**，那是另一个小节） | 倍数关系可用（顺序 30 次 vs 1 次），±10% 量级的差异不可用 |
| 4 | `DESIGN §5`"真实 AWS 部署实测"：E2E ~900ms 被归因为"API Gateway + Cognito + 网络" | 客户端在测试者本机、网络偏慢，这个数字只是**未分解的合计值** | 归因降级为"待验证"；由它推出的"Lambda 内部优化对用户延迟不重要""HTTP API 是 v2 最大收益"两条结论**撤回** |
| 5 | `DESIGN §2.3` 说 MemorySize/arm64"已用真实 AWS 验证" | 实际只是 bookstore 一个函数的一次实测 | 改为"方向一致的一次观测"（§6.1） |
| 6 | `DESIGN §5`"MemorySize / Architectures 真实 A/B 实测"开头：本地 `sam local invoke` 失败，归因于 `MAX_PATH` | 很可能也是第 1 条的代理问题；在短路径下同样失败，加了 `NO_PROXY` 之后就正常了 | `MAX_PATH` 作为 `sam build` 失败的原因仍然成立；`invoke` 失败改归因于代理 |

---

## 2. 对照官方参考实现

| | todo | bookstore |
|---|---|---|
| 官方参考实现 | [aws-samples/lambda-refarch-webapp](https://github.com/aws-samples/lambda-refarch-webapp)（clone 在 `temp/ref/`） | [aws-samples/aws-bookstore-demo-app](https://github.com/aws-samples/aws-bookstore-demo-app)（同上） |
| 单体（逆向而来） | `Mono2Sls-artifacts/mono_benchmark/todo` | `Mono2Sls-artifacts/mono_benchmark/bookstore` |
| m2s 产物 | `Mono2Sls-artifacts/M2S/runs/todo-oh/output` | `Mono2Sls-artifacts/M2S/runs/bookstore-oh/output` |

对照的前提：官方实现是**原生为 serverless 设计**的，m2s 是**从单体翻译**的，而单体本身是从官方实现逆向简化
出来的（Redis/OpenSearch/Neptune 被退化成了 DynamoDB Scan）。所以"m2s 比官方慢"的地方，一部分是翻译没做好，
一部分是单体已经丢掉了官方的专用基础设施。后者要不要补回来是架构决策（§4.3），不是编码问题。

### 2.1 todo（6 个 CRUD 端点，每个 handler 1 次 DynamoDB 调用）

| 维度 | 官方 lambda-refarch-webapp | m2s todo-oh | 评价 |
|---|---|---|---|
| 运行时 / SDK | nodejs12.x，`aws-sdk` v2 整包 + `aws-xray-sdk-core` `captureAWS` + `aws-embedded-metrics` | nodejs22.x，`@aws-sdk/client-dynamodb` + `lib-dynamodb` v3 模块化 | m2s 更好 |
| 连接复用 | 显式 `AWS_NODEJS_CONNECTION_REUSE_ENABLED=1`（v2 必需） | 未设置 | m2s 没问题：v3 默认 keep-alive |
| MemorySize | **1024，注释写明来自 Lambda Power Tuning** | 512（Globals 写死） | 官方的**方法**值得学：用 Power Tuning 实测定值 |
| Architectures | 未设置（x86） | 未设置（x86） | 两者都缺（B1） |
| 打包 | 每函数 `node_modules` | 每函数 `node_modules` | 两者一样；esbuild 打包在 L2′ 里未见收益（A4） |
| API 限流保护 | `UsagePlan`：Burst 100 / Rate 50 / 5000 次每天 | 无 | 官方更好（保护性配置，B5） |
| API 访问日志 | 格式里带 `$context.responseLatency $context.integrationLatency` | 只有 `requestId` + `error.message` | 官方的**格式**更好，能把 E2E 拆成"API 层"和"Lambda"两段（B4）。注：官方模板把 `AccessLogSetting`/`MethodSettings` 写在了 `Properties` 外面，实际部署时不会生效 |
| API 执行日志 | 意图开 `DataTraceEnabled` + `INFO`（同上，位置写错未生效） | 无 | m2s 的做法对（生产不该开 DataTrace） |
| DynamoDB | Provisioned 5/5 | `PAY_PER_REQUEST` + PITR | m2s 更适合低频/突发流量 |
| 依赖 | `uuid` v1 | `uuid` v1（照搬单体） | 都可以换成 `crypto.randomUUID()`（A5） |

todo 的 handler 两边都是"1 次 DynamoDB 调用"，**代码层面没有值得改的性能问题**，差异全在配置层。

### 2.2 bookstore（12 个函数，含 1 个异步消费者）

| 功能 | 官方 aws-bookstore-demo-app | 单体 | m2s bookstore-oh | 评价 |
|---|---|---|---|---|
| 畅销榜读 `GET /bestsellers` | Redis `ZREVRANGE 0 19` | `scan` 全表 + 内存排序取前 20 | 同单体 | 代价随书目量线性增长；Scan 单页 1MB 上限且没分页，数据多了结果会**错**（A3） |
| 畅销榜写 | Orders 表 **DynamoDB Streams**（BatchSize 1）→ `ZINCRBY` | 结账后 `for…await` 逐本 `UpdateItem ADD` | 结账发 SQS → 消费者（BatchSize 10）**两层 for 循环顺序 await** | 一批 10 条消息 × 每单 3 本 = 30 次顺序往返（A1） |
| 搜索 | OpenSearch `multi_match` | `scan` + 内存过滤 | 同单体 | 同畅销榜读（A3） |
| 书目列表 | 有 category 走 GSI `Query`，否则 `Scan` | 同 | 同 | 三者一致；无分页 |
| 结账 `POST /orders` | `put` + 循环 `delete`（回调风格，未等待删除完成，`callback` 可能被多次调用——官方这里有 bug） | `put` → `Promise.all(delete…)` | `Put` → `Promise.all(Delete×N)` → `SendMessage`，**顺序深度 3**，5 次调用 | m2s 正确性比官方好；调用链可以压浅（A2） |
| 冷启动对策 | 另一份模板用 EventBridge 每 10 分钟调一次 warmer | — | 无 | **不要学**：warmer 是旧做法，只能保 1 个并发实例热 |
| 网络 | Redis/OpenSearch/Neptune 迫使部分函数进 VPC | — | 全部不进 VPC | m2s 更好 |
| MemorySize | 256 | — | 512 | 都没实测定值 |
| DynamoDB | Provisioned 1/1 | — | `PAY_PER_REQUEST` | m2s 更好 |
| API 鉴权 | `AWS_IAM`（Identity Pool + SigV4） | JWT 中间件 | Cognito User Pool Authorizer，**所有端点**都鉴权 | 公共只读端点是否需要鉴权，值得在 S1 确认（C3） |
| API 访问日志 | — | — | **完全没有** | B4 |

### 2.3 m2s 已经比官方好的地方（优化时不能改坏）

SDK v3 模块化 + nodejs22；不进 VPC；DynamoDB 按需计费；没有定时 warmer；没有 DataTrace 全量日志；结账的删除
操作用 `Promise.all` 真正等待完成。**不要因为"官方有 warmer / Redis"就照搬**：warmer 已过时；Redis 要进
VPC、有常驻费用，对这个量级的应用不一定划算。

---

## 3. 实验记录

所有实验都用 bookstore-oh 的真实 handler。脚本在 `perf-tools/`（附录 A）；L1 实验的环境在
`D:\tmp\m2s-perf-cmp`，L2′ 实验在 `D:\tmp\m2s-samfine`、`D:\tmp\m2s-bs`（都用短路径，避开 Windows 的 `MAX_PATH`）。

### 实验 1（L1a）：esbuild 打包对 `require()` 的影响 —— 结论已被实验 6 推翻

每组 15 个独立 node 进程，取中位数：orders-create 未打包 94.8ms → 打包后 56.7ms（−40%）；books-list
83.2ms → 46.9ms（−44%）。**这个差距在 Linux 运行时里不存在**（实验 6），保留在这里作为反例：不要在 Windows
上测模块加载。

### 实验 2（L1b）：SQS 消费者改写

事件为一批 10 条消息，每条 3 本书，其中 5 本热门书有重叠，对应 `BatchSize: 10`。

| 写法 | 调用次数 | 顺序深度 | sleep 墙钟 |
|---|---|---|---|
| 现状：双层 `for…await` | 30 | 30 | 467.8ms |
| 全部 `Promise.all` | 30 | 1 | 15.7ms |
| 先按 `bookId` 聚合数量，再 `Promise.all` | **5** | 1 | 15.5ms |

`UpdateItem` 没有批量 API（`DESIGN §4`），所以正确的改写是"聚合 + 并行"，不是 `BatchWriteItem`。聚合后调用
次数降多少，取决于 mock 事件里的 bookId 重复率。这个重复率是人为设定的。

### 实验 3（L1b）：orders-create 调用链（3 本书的购物车）

| 写法 | 调用次数 | 顺序深度 | 取舍 |
|---|---|---|---|
| 现状：`Put` → `Promise.all(Delete×3)` → `SendMessage` | 5 | 3 | — |
| 三段全部并行 | 5 | 1 | ❌ 下单失败时购物车已删、消息已发，不可接受 |
| `TransactWriteItems(Put + Delete×3)` → `SendMessage` | 2 | 2 | 下单与清购物车变成原子操作（顺带修了现状"下单成功、清购物车部分失败"的不一致）；**事务写消耗 2× WCU** |
| `Promise.all([Put, BatchWrite(Delete×N)])` → `SendMessage` | 3 | 2 | 不付 2× WCU，但失去"先确认下单再清购物车"的顺序保证 |
| 单次 `TransactWriteItems`，畅销榜改由 Orders 表 Streams 触发（官方做法） | 1 | 1 | 要改异步机制（C2） |

选哪种由 S2 按单体原有的业务语义决定，perf-optimizer 不能静默改变语义。

### 实验 4（L1b）：全表 Scan 的代价，`send()` 层 mock 看不见

bestsellers-list 在 mock 返回 100 到 100,000 条记录时，墙钟都是约 15.6ms，和"GSI Query Limit 20"一样。原因是
mock 把一次 Scan 当成一次固定延迟的往返，不管读了多少数据。单独测内存排序：10 万条约 10ms。实验 5 用 HTTP 层
mock 测出了这部分成本。

mock 之外的成本只能解析估算，没有实测：
- **读容量**：Scan 按扫描量计费。假设每条 100B，1 万条约 128 RRU/次，而 `Query Limit 20` 约 0.5 RRU/次；
- **1MB 分页**：代码没处理 `LastEvaluatedKey`，数据超过 1MB 时结果是错的。

**附带发现**：Windows 上 `setTimeout(8)` 实测 14.5ms，所有单次调用的结果都卡在约 15.6ms（§1.3 第 3 条）。

### 实验 5（L1c）：热执行的 CPU 部分

`http-shim.js` 把拦截点从 `send()` 下移到 `NodeHttpHandler.handle`，只替换网络收发，SDK 其余部分真实执行。
`warm_cpu.js` 在同一进程里先预热 30 次，再计时 300 次，网络延迟为 0，取中位数：

| handler | 调用次数 | CPU 中位数 |
|---|---|---|
| books-get-by-id / cart-add-item | 1 | ~0.25ms |
| orders-list / books-list（20 条） | 1 | ~0.40ms |
| orders-create | 5 | ~0.81ms |
| 消费者现状（10 条消息 × 3 本） | 30 | ~2.9ms |
| 消费者先聚合再并行 | 5 | ~0.62ms（−78%） |

| Scan/Query 返回条数 | 20 | 500 | 2,000 | 5,000（约一个 1MB 页） |
|---|---|---|---|---|
| bestsellers-list | 0.40ms | 3.2ms | 13.9ms | 34.4ms |
| search-books | 0.39ms | 3.4ms | 15.3ms | 37.4ms |
| books-list | 0.39ms | 3.3ms | 15.0ms | 35.4ms |

CPU 随条数线性增长，主要花在 SDK 反序列化和 DocumentClient 解码上。斜率是实测的；"线上有多少条"是假设。
稳定性：同一 handler 5 次独立运行，波动 ≤ ±7%。

### 实验 6（L2′）：排查 sam 失败的根因 + 在 Linux 运行时里测量

**根因排查**：
1. `sam local invoke --debug`：容器正常经历创建、启动、被调用、被 sam 正常结束；日志是
   `Failed to deserialize response from RIE`，原始响应为空。
2. sam 源码里调用地址是 `container_host="localhost"`，端口只绑定在 `127.0.0.1` 上。
3. 同一容器交替对照：`localhost` 返回 502、耗时 3.7s；`127.0.0.1` 返回 200、耗时 4ms；TCP 连接耗时两者相同，
   改 `Host` 头不影响结果。
4. 环境里有 `HTTP_PROXY=http://127.0.0.1:21081`，没有 `NO_PROXY`（来自
   `Documents\WindowsPowerShell\Microsoft.PowerShell_profile.ps1`）。curl 和 Python `requests` 只看环境变量，
   不看 Windows 系统代理的例外列表。`curl --noproxy '*' http://localhost:...` 返回 200。
5. 修复：配置文件里加 `$env:NO_PROXY = "localhost,127.0.0.1,::1"`（原文件备份为 `.bak-2026-09-24`）。修复后
   不加任何参数，`sam local invoke` 能返回 200；`start-lambda` 热调用约 22~47ms；`start-api` 返回的是 handler
   自己的 500（本地不模拟 Cognito）。

**在 L2′ 里测量**（`perf-tools/rie/rie_measure.sh`）：
- 空函数：init 62ms，热调用 1.3ms（RIE 自身的固定开销）；
- books-get-by-id：init 93~108ms，热调用 3.0~3.5ms；
- orders-create：init 101~115ms，热调用 4.0~5.4ms。

限 0.289 CPU（≈512MB）+ 每次调用 5ms 延迟时：

| 场景 | 热调用中位数 |
|---|---|
| 消费者现状 | 188.8ms |
| 消费者先聚合再并行 | 9.9ms |
| bestsellers-list，20 条 | 8.7ms |
| bestsellers-list，2,000 条 | 19.4ms |

用 `--cpus` 模拟 MemorySize（orders-create，5ms/调用），和真实 A/B（`DESIGN §5`"MemorySize / Architectures
真实 A/B 实测"）对照：

| 等效内存 | 容器内热调用中位数 | 真实 A/B |
|---|---|---|
| 128MB（0.072 CPU） | 115.9ms（双峰，p10 41 / p90 164） | ~174ms |
| 512MB（0.289） | 21.9ms | ~35ms |
| 1024MB（0.579） | 21.7ms | ~24ms |

- 能复现"128MB 断崖式变慢"；
- 复现不了 512→1024 的约 31% 提升，因为本机单核比 Lambda 快；
- CPU 限额下的 init（约 2.4~3.0s）不代表真实情况，真实 Lambda 的 Init 几乎不随内存变化。

**esbuild 打包**（orders-create，不限 CPU，5 次交替运行）：未打包 init 108~117ms（有一次异常值 182ms），打包后
101~117ms，**没有差异**。

### 实验 7（L2′）：bookstore-oh 全部 12 个函数的基线

用 bookstore-oh 自己的 `template.yaml` 执行 `sam build`，12 个函数全部构建成功。先做有效性校验：每个函数用
`VALIDATE=1` 调用一次，13 个测试项（books-list 带/不带 category 各算一项）全部返回 2xx，每个函数的 SDK 调用
次数都和静态分析一致。然后两种配置各测 3 轮，每轮 1 次冷调用 + 30 次热调用：
- **A**：不限 CPU、无延迟；
- **B**：0.289 CPU + 5ms/调用。

脚本、事件、原始结果在 `perf-tools/rie/bookstore/`。下表取 3 轮的中位数：

| 函数 | 调用次数 / 顺序深度 | init_ms（A） | 热调用 A | 热调用 B |
|---|---|---|---|---|
| BooksList（category → Query） | 1 / 1 | 141（119~154） | 5.6ms | 10.4ms |
| BooksList（无 category → Scan） | 1 / 1 | 158（131~158） | 4.5ms | 9.9ms |
| BooksGetById | 1 / 1 | 130（125~175） | 4.8ms | 10.2ms |
| SearchBooks | 1 / 1 | 159（130~168） | 5.2ms | 7.6ms |
| CartListItems | 1 / 1 | 192（164~199） | 5.1ms | 10.5ms |
| CartGetItem | 1 / 1 | 139（86~200） | 4.4ms | 10.3ms |
| CartAddItem | 1 / 1 | 157（82~161） | 4.4ms | 7.6ms |
| CartUpdateItem | 1 / 1 | 143（97~159） | 5.0ms | 10.3ms |
| CartRemoveItem | 1 / 1 | 145（132~160） | 4.8ms | 9.9ms |
| OrdersList | 1 / 1 | 180（87~182） | 5.3ms | 10.6ms |
| BestsellersList | 1 / 1 | 143（88~195） | 5.0ms | 10.6ms |
| **OrdersCreate** | 5 / 3 | 147（146~174） | 7.0ms | **24.4ms**（22.9~24.5） |
| **BestsellersUpdateConsumer** | 30 / 30 | 166（94~167） | 25.3ms | **177ms**（172~204） |

- 配置 B 按顺序深度分成三组，组间不重叠：深度 1 在 7.3~10.8ms，深度 3 在 23~25ms，深度 30 在 172~204ms。
- 组内差异是噪声（约 ±3ms，双峰）。
- 单次 init 在 82~200ms 之间波动，12 个函数之间看不出差别。
- 和 L3 对照：books-list 真实约 8.9ms、配置 B 约 10.4ms；orders-create 真实 35~45ms、配置 B 约 24ms。排序一致，
  但 5ms/调用低估了真实往返时间，调用越多低估越多。**这些毫秒数只用于同一配置下的前后对比。**

---

## 4. 改进点与证据等级（本文唯一的定级处）

### 4.1 等级定义

| 等级 | 含义 | 在流水线里怎么用 |
|---|---|---|
| **M（可测）** | L0/L1/L2′ 能判断改动**是否正向** | 放进 perf-optimizer 自查循环："改 → 测 → 留/撤"，判据见 §5 |
| **M−（可测但信号弱）** | 能测，但效应接近噪声，或结论取决于 mock 输入的假设 | 可以做，但报告里要写明假设；不单独作为"已验证"的依据 |
| **E（经验）** | L0/L1/L2′ 都测不了；依据是官方做法、AWS 公开资料、bookstore 一次 L3 观测 | 只做成默认规则或 advisory，报告里注明"未经本次测量验证" |
| **正确性配套** | 不是性能改动，但某个性能改写**必须**和它一起做，否则会引入正确性问题 | 和对应的性能改写绑定；L0 检查"配没配" |
| **测量/保护** | 不改变性能，是测量手段或保护性配置 | 默认加上，不参与性能判据 |

### 4.2 总表

| # | 改进点 | 等级 | 流水线内的判据（§5） | 已有证据 | 风险 | 负责阶段 | 优先级 |
|---|---|---|---|---|---|---|---|
| A1a | SQS 消费者循环改为 `Promise.all` 并行 | **M** | 顺序深度 30→1；L2′ 配置 B 从深度 30 组落到深度 1 组 | 实验 2、6、7 | 低 | S2 | 1 |
| A1b | 消费者先按 key 聚合再写 | **M−** | 调用次数、L1c CPU 下降（30→5，−78%）；降幅取决于假设的重复率 | 实验 2、5 | 低 | S2 | 1（和 A1a 一起做） |
| A1c | `FunctionResponseTypes: [ReportBatchItemFailures]` + 返回 `batchItemFailures` | **正确性配套**（绑定 A1） | L0：配没配 | 现状整批重投会重复 `ADD` | 低 | S2 + S3 | 1（和 A1 一起做） |
| A2 | orders-create 压浅调用链 | **M**（深度/次数/CPU）；选哪种方案是业务取舍 | 顺序深度 3→2（或 1）；L2′ 配置 B 跨组 | 实验 3 | 中（2× WCU / 顺序保证） | S2 | 2 |
| A3 | 热读路径全表 Scan → GSI / 物化视图；至少要分页 | **M**（CPU，需给定数据量）；RCU 费用、多页往返是 **E** | L0 识别"读路径上有 Scan"；L1c 在假设条数下 CPU 下降；L2′ 配置 B | 实验 4、5、6 | 中（改数据模型） | P0 信号 + S1 + S2 | 2 |
| A4 | esbuild 打包 + minify | **M，已测，结果无效** | L2′ init（≥5 次交替运行） | 实验 6：无差异 | 低 | 不作为默认 | — |
| A5 | `uuid` → `crypto.randomUUID()` | **M−** | L2′ init；单独测 `uuid` 约 10ms vs `crypto` 约 3ms，但在整个 init 里落在噪声内 | `perf-tools/req_uuid.js` | 低（v1→v4 语义变化） | S2 | 4 |
| B1 | `Architectures: [arm64]` | **E** | 无（本机只能 QEMU 模拟） | L3 一次 A/B：不劣于 x86；单价低约 20% | 极低 | S3 | 3 |
| B2a | MemorySize 下限（不低于 256MB） | **M−** | L2′ `--cpus 0.072` 能复现 128MB 断崖；可用来检查"给得太小" | 实验 6 + L3 一次 A/B | 低 | S3 | 3 |
| B2b | MemorySize 档位（热点 512~1024） | **E** | 无（L2′ 分辨不出 512 与 1024） | L3 一次 A/B；官方 todo 用 Power Tuning | 低 | S1 `perf_hint` + S3 | 3 |
| B3 | SQS `BatchSize` / `MaximumBatchingWindowInSeconds` | **E**（连 L3 都没有） | 无 | 无 | 低 | S3 | 4 |
| B4 | API 访问日志加 `responseLatency`/`integrationLatency` | **测量/保护** | — | 官方模板的格式 | 极低 | S3 | 默认加 |
| B5 | API UsagePlan 限流 | **测量/保护** | — | 官方模板有 | 低 | S3（可选） | 可选 |
| C1 | REST API → HTTP API + JWT Authorizer | **E** | 无 | 价格是公开定价（确定）；延迟收益无数据 | 中高 | v2 | 待 E3 |
| C2 | 异步派生数据改用 DynamoDB Streams | **M**（结账少 1 次同步调用）；架构取舍是 **E** | 顺序深度 | 实验 3 | 中 | v2（S1） | v2 |
| C3 | 公共只读 GET 加缓存 | **E** | 无（命中率取决于真实流量） | 无 | 中 | v2 | v2 |
| C4 | Provisioned Concurrency（仅 `perf_hint: hot`） | **E** | 无（纯部署配置 + 费用决策） | L3 Init 300~385ms | 有持续费用 | v2 | v2 |
| §2.3 | 防倒退：SDK v3、不进 VPC、不用 warmer | SDK v3 vs v2 是 **M**（L2′ init，未测）；其余是 **E** | — | — | — | S2 规则 | 默认 |

"已有证据"一栏里的 L3 只能说明**方向**，不说明幅度（§6.1）。

### 4.3 架构级改进（C 类）为什么留到 v2

- **畅销榜**：不需要补回 Redis。DynamoDB 原生方案更合适：
  - (a) 稀疏 GSI（固定分区键 + `salesCount` 排序键），用 `Query ScanIndexForward=false Limit=20` 读取；写入会
    集中在一个分区，写入量高时会成为热点；
  - (b) 消费者维护一条 Top-20 物化视图，读端变成一次 `GetItem`。
- **搜索**：OpenSearch 有常驻最低费用，小书目用"Scan + 分页 + 缓存"可能更划算。选哪种需要**数据量信号**：
  P0 从单体的种子数据（例如 `scripts/init-db.js`）推断表规模，S1 据此决定。
- **异步机制**：Streams 比 SQS 少一次同步调用，而且 Lambda 读 Streams 不收请求费；代价是按分片顺序处理、只保留
  24 小时。这是 blueprint 层的选择。

---

## 5. perf-optimizer 的判据

原则：自查循环里只用**确定性指标**，或者**效应量远大于该层噪声（§1.2）**的指标，避免"在噪声里空转"。

| 改动类型 | 主判据 | 阈值 | 阈值依据 |
|---|---|---|---|
| 调用链结构（并行、聚合、事务、改异步机制） | L0 调用次数不增加、**顺序深度下降** | 深度严格下降 | 确定性 |
| 同上，在真实运行时里确认 | L2′ 配置 B 的热调用中位数 | 必须**跨到更低的深度组**（例如从 23~25ms 组落到 7~11ms 组）；组内变化不算数 | 组内噪声约 ±3ms，组间不重叠（实验 7） |
| 热执行 CPU（聚合、减少返回条数、减少编解码） | L1c `warm_cpu.js` 中位数 | 下降 ≥10%，且其它 handler 回升不超过 10% | 噪声 ≤ ±7%（实验 5） |
| 打包 / 依赖 | L2′ init（不限 CPU） | 改动前后各**交替运行 ≥5 次**，中位数下降 ≥15%，**且两组范围不重叠** | 同一函数交替运行的范围约 ±8%；单次运行波动很大，所以要求范围不重叠，不能只看中位数 |
| MemorySize 下限 | L2′ `--cpus` 按目标内存换算 | 配置 B 的热调用不出现双峰断崖（参照 128MB：p90 是中位数的数倍） | 实验 6 |
| arm64、MemorySize 档位、SQS 参数、HTTP API | 不测 | 按默认规则执行，报告注明"经验默认值" | L0/L1/L2′ 都测不了 |
| L0 命中但无法自动修的问题（热路径 Scan 需要改数据模型、要换异步机制） | 不改 | 写进 `perf_report.json` 的 `advisories`，附假设条数下的 L1c 数据，交给 S1/人工 | — |

每次测量前都要先做**有效性校验**（`DESIGN §3` 步骤 1）：返回 2xx，并且 SDK 调用次数 >0、和静态分析一致。
L1b 用 `getCallLog()`，L2′ 用 `VALIDATE=1`。

报告里只写直接测得的量（例如"顺序深度 30→1、调用次数 30→5、CPU −78%（假设同批 5 本书重复）"），**不写
"预计线上提升 X ms / X%"**。

---

## 6. L3 数据：怎么用、不能怎么用

### 6.1 原始观测（仅 bookstore；不作为模型参数）

L3 一共两次（`DESIGN §5`"真实 AWS 部署实测""MemorySize / Architectures 真实 A/B 实测"），都是一个账号、
us-east-1、同一天，都已拆除。

| 观测 | 取值 | 范围限制 |
|---|---|---|
| 热调用 Duration（512MB） | books-list（深度 1）约 8.9ms；orders-create（深度 3）35~45ms；消费者（3 次顺序调用）19~36ms | 三个函数；每个 3~4 次热调用 |
| Init Duration | 300~385ms，几乎不随内存变化 | 三个函数 |
| MemorySize A/B（orders-create） | 128MB ~174ms、512MB ~35ms、1024MB ~24ms | **一个函数**，每档 3 次热调用 |
| 架构 A/B（orders-create） | arm64 不劣于 x86_64 | 同上 |

**能推广的只有方向**：顺序深度越大，Duration 越长；128MB 对用 SDK v3 的函数明显偏慢；512MB 以上边际收益
递减；arm64 不比 x86 慢。**具体倍数不推广**：它们随 item 大小、操作类型、函数是 CPU 密集型还是 IO 密集型而
变化。所以本文不给"深度 × 往返时间 × 内存系数"这类收益公式。

L3 的作用是：
- 给 §4 的 E 类默认规则提供方向依据；
- 检验 L1/L2′ 的方向是否和真实环境一致。目前一致：深度 1 和深度 3 的排序、128MB 断崖。

### 6.2 L3 真实 A/B 的标准流程

1. 独立临时栈（如 `m2s-perf-ab-<实验名>`），只在待比较的维度上不同，代码用 m2s 原产物。
2. 每个变体 1 次冷调用 + ≥5 次热调用，丢掉前 1~2 次热调用，报中位数和范围。
3. 读 CloudWatch `REPORT` 行，不用客户端墙钟。
4. 拆除：`sam delete`，再手动删除 `DeletionPolicy: Retain` 的表和用户池，核实都已不存在。
5. 内存曲线可以直接用 **AWS Lambda Power Tuning**。
6. 需要人工授权，不进自动自查循环。

### 6.3 E2E 延迟

- **当前唯一的 E2E 数据**是：从测试者本机（网络偏慢，到 us-east-1 跨洋）用客户端计时，经 API Gateway +
  Cognito，热调用约 900ms。这是**未分解的合计值**，里面网络、API 层、鉴权各占多少都不知道。
- 所以它**不能**用来判断瓶颈在哪一层，也**不能**用来给 Lambda 内部优化排优先级。同步端点和异步消费者的调用
  链优化同等对待，按顺序深度的差值排序（§4.2 优先级一栏）。
- **要分解 E2E**，需要：
  - 模板里加 B4 的访问日志字段：`responseLatency − integrationLatency` 是 API 层（含鉴权）的耗时；客户端 E2E
    减去 `responseLatency` 是网络的耗时。这件事不需要 X-Ray；
  - 压测客户端放在同一区域（CloudShell 或临时 Lambda/EC2）；
  - 然后再做 REST vs HTTP API 的 A/B（§8 E3）。价格差（$3.50 vs $1.00 每百万请求）是确定的，延迟差要等分解
    之后再说。

---

## 7. 对 m2s 流水线的改动建议

| 阶段 / 文件 | 改动 | 对应 |
|---|---|---|
| P0 `analyze_project` | 新增静态信号：循环内的 `await` 单条 DB 调用（区分顺序和 `Promise.all`，`DESIGN §4`）；读路径上的 `scan`；从种子数据推断的表规模 | A1、A3 |
| S1 `m2s-architect.md` | 异步派生数据的机制选择（SQS vs Streams）写明理由；公共只读端点是否需要鉴权 | C2、C3 |
| S2 `m2s-performance-kb` | ① 消费者"先聚合再并行"，**必须同时**做部分批失败上报（正确性配套，避免重复 `ADD`）；② 多步写入的调用链深度与 `TransactWriteItems` 的取舍（2× WCU、顺序保证）；③ 读路径的 Scan 必须处理 `LastEvaluatedKey`，并在报告里标注"随数据量增长"；④ 防倒退：不要 warmer、不要 v2 SDK、不进 VPC | A1、A2、A3、§2.3 |
| S3 `m2s-sam-kb` | Globals 加 `Architectures: [arm64]`；MemorySize 按 `perf_hint` 分级、下限 256；SQS 事件加 `FunctionResponseTypes: [ReportBatchItemFailures]`；`BatchSize`/`MaximumBatchingWindowInSeconds` 先给参考值并标注未验证；API 加带延迟字段的 `AccessLogSetting`。**不加** esbuild `Metadata`（A4 已测无效） | A1c、B1~B4 |
| S3p perf-optimizer（`DESIGN §3`） | 判据按 §5；Docker 可用时加 L2′ 测量，不可用时只用 L0/L1；输出区分"已自动修复"和 `advisories` | 全部 |
| `gate(perf)`（advisory） | 纯 L0 检查：arm64、MemorySize ≥256、SQS 消费者配了 `ReportBatchItemFailures`、读路径 Scan 有分页、没有循环内顺序 `await` 单条调用 | A1、A3、B1、B2a |

---

## 8. 后续实验（按信息价值排序）

| # | 实验 | 需要部署？ | 回答的问题 |
|---|---|---|---|
| E0 | 对 `mono_benchmark/` 里的 airline-booking / coffee / ecommerce / shopping-cart 跑流水线，产物上做 L0 扫描 + L1 + L2′ 基线 | 否 | bookstore 上的反模式在其它应用里有多常见；§5 的判据在其它代码库上是否同样稳定 |
| E2 | SQS 消费者：现状 vs 聚合+并行；`BatchSize` 1/10 × `MaximumBatchingWindowInSeconds` 0/5 | 是 | A1 的真实收益；B3 目前完全没有数据 |
| E3 | E2E 分解 + REST vs HTTP API（开延迟访问日志，客户端在同区域） | 是 | ~900ms 的真实构成；HTTP API 有没有延迟收益 |
| E4 | 灌 1 万条数据后：Scan+排序 vs 稀疏 GSI Query vs 物化视图 GetItem | 是 | A3 的真实收益，以及 RCU 解析估算是否成立 |
| E1 | 打包方式在真实 Lambda 上：未打包 / esbuild / esbuild + `@aws-sdk` external | 是 | L2′ 显示无差异；只需确认真实环境（代码从 S3 下载、包体积）是否不同。优先级最低 |

E0 不需要 AWS，应该最先做：它决定本文结论是"bookstore 的特例"还是"m2s 的普遍问题"。

---

## 9. 对 `PERF_OPTIMIZATION_DESIGN.md` 的修正

1. `DESIGN §5`"sam CLI + Docker 阶段实测"：整节数据作废（§1.3 第 1 条），该节开头已加了警告。
2. `DESIGN §5`"两个反模式的实测差异"：单调用 handler 的 11~24ms 被 Windows 时钟粒度量化，只能看倍数（§1.3 第 3 条）。
3. `DESIGN §5`"真实 AWS 部署实测"：E2E 归因降级为"未分解、待验证"（§1.3 第 4 条，§6.3）。
4. `DESIGN §7`："HTTP API 是 v2 收益可能最大的一项"撤回，改为"价格收益确定，延迟收益待 E3"。
5. `DESIGN §2.3`：MemorySize/arm64 从"已用真实 AWS 验证"改为"bookstore 一个函数上方向一致的一次观测"（§6.1）。
6. `DESIGN §3` 步骤 3（sam CLI 阶段测量）：改为 L2′ 的做法（§1.1）；步骤 4 的 ±10~15% 噪声带判据，改为 §5 的表。
7. `DESIGN §5`"MemorySize / Architectures 真实 A/B 实测"开头：本地 `sam local invoke` 失败，改归因于代理问题（§1.3 第 6 条）。

---

## 附录 A：工具索引（`m2s-mcp-server/perf-tools/`）

| 文件 | 层 | 用途 |
|---|---|---|
| `mock-sdk-shim.js` | L1b | 在 `send()` 层拦截；调用日志；`getCriticalPathDepth()` 计算顺序深度 |
| `run_one.js` | L1b | 单个 handler 的调用次数/墙钟 |
| `exp_variants.js` | L1b | 实验 2~4 的改写对比 |
| `http-shim.js` | L1c / L2′ | 在 HTTP 层拦截，SDK 真实执行；环境变量 `SHIM_ITEMS` / `SHIM_LATENCY_MS` / `SHIM_LOG` |
| `warm_cpu.js` | L1c | 热执行 CPU 中位数 |
| `candidate_consumer_agg.js` | — | 消费者"先聚合再并行"的改写样例 |
| `req_time.js` | L1a | `require()` 计时（仅作冒烟检查） |
| `req_uuid.js` | L1a | `uuid` 包 vs 内置 `crypto.randomUUID` 的加载开销（A5） |
| `rie/rie_measure.sh` | L2′ | RIE 直连测量；`VALIDATE=1` 做有效性校验 |
| `rie/bookstore/` | L2′ | 实验 7 的一键重跑脚本、事件、原始结果、汇总脚本 |

环境注意事项：
- 在短路径下构建和运行（`D:\tmp\...`）；
- 如果设置了 `HTTP_PROXY`，必须同时设置 `NO_PROXY=localhost,127.0.0.1,::1`；
- 代码用 `docker cp` 放进容器，不用绑定挂载；
- `*.sh` 由 `.gitattributes` 固定为 LF 换行。
