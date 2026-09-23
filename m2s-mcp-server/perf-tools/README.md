# perf-tools（本地代理指标测量脚本，实验产物）

这两个文件是 2026-09-23 测 `bookstore-oh` 本地代码阶段代理指标（`require()` 耗时、mock 调用
执行耗时）时写的，从临时 scratch 目录搬进仓库保存，避免机器重启丢失。**不是 perf-optimizer
的正式实现**，只是一份验证过可行的参考脚本——设计细节见
`../PERF_OPTIMIZATION_DESIGN.md` §3 步骤 1、§4 第 5 条。

## 文件

- `mock-sdk-shim.js`：通用 mock 拦截骨架。`installShim({clientPrototypes, responseTable})`
  在指定的 SDK Client 原型上打补丁拦截 `.send()`，按 `command.constructor.name` 分发到
  `responseTable`，每次调用加固定延迟（默认 8ms，模拟单次网络往返，明确是代理值）。未覆盖的
  Command 类型直接抛错，不静默返回空对象。文件里的 `EXAMPLE_RESPONSE_TABLE_FROM_BOOKSTORE`
  只是 bookstore 这一次实测的覆盖表样本，**换一个代码库必须重新扫描确定覆盖范围，不能直接
  照搬这份表**（这条踩过坑：`cart-add-item` 用一份通用 mock event 测出来是假数字，因为校验
  规则没被满足就 400 短路了）。
- `run_one.js`：单个 handler 的测量 harness，命令行用法：
  ```
  node run_one.js <handlerFile> <api|sqs>
  ```
  内部设了 bookstore 用到的全部环境变量、构造了 API Gateway / SQS 两种事件形状；换代码库要
  按 §3 步骤 1 重新构造 per-handler 的 mock event。

## 如何复现（bookstore-oh 这次的测法）

1. 在这个目录（或任意 scratch 目录）`npm install @aws-sdk/client-dynamodb @aws-sdk/lib-dynamodb @aws-sdk/client-sqs`
   （版本对齐 `output/lambdas/*/package.json` 里声明的版本）。
2. 把 `output/lambdas/<domain>/<name>/handler.js` 复制进同一目录（改个不冲突的文件名）。
3. `node run_one.js <复制后的文件名> api`（或 `sqs`，取决于 `trigger_type`）。

2026-09-23 bookstore-oh 12 个 handler 的实测结果记在 `../PERF_OPTIMIZATION_DESIGN.md` §5
"两个反模式的实测差异"里，不在这里重复。

## 2026-09-23 新增：改写候选对比实验

- `req_time.js`：单进程 `require()` 计时，用来比较未打包 handler 与 esbuild 打包产物的冷加载。
- `exp_variants.js`：用同一个 mock 层对比 SQS 消费者、orders-create 的几种改写，以及 Scan 数据量
  对代理指标的（不）影响。

两者的用法写在文件头注释里，结果和解读见 `../PERF_OPTIMIZATION.md` §3.2。注意 Windows 上
`setTimeout(8)` 实际约 14.5~15.6ms，mock 墙钟会被这个时钟粒度量化。

## 2026-09-24 新增：热执行 CPU 代理（HTTP 层 mock）

- `http-shim.js`：在 `@smithy/node-http-handler` 的 `NodeHttpHandler.prototype.handle` 上打补丁，
  只替换网络收发。SDK 的序列化、SigV4 签名、重试中间件、反序列化、DocumentClient 编解码全部真实
  执行。`mock-sdk-shim.js` 在 `send()` 层拦截，这些开销都会被跳过。按 `x-amz-target` 分发，只支持 JSON 协议
  （DynamoDB、SQS 3.699 都是）；未覆盖的操作直接抛错。
- `warm_cpu.js`：`node warm_cpu.js <handler.js> <api|sqs> [Scan/Query 返回条数]`。同一进程内先预热 30 次，
  再计时 300 次，输出单次调用的中位数/p90。网络延迟为 0，测的是纯 CPU 部分。
- `candidate_consumer_agg.js`：bestsellers-update-consumer 的"先聚合再并行"改写样例。

判读：`mock-sdk-shim.js` 的调用轮数度量"等网络"部分，`warm_cpu.js` 度量"CPU"部分，两者合起来才是
热执行。结果见 `../PERF_OPTIMIZATION.md` §3.2 实验 5。绝对值是这台开发机的 CPU，不等于 Lambda；只做同机相对比较。
