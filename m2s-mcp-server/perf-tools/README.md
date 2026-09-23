# perf-tools（本地代理指标测量脚本，实验产物）

这两个文件是 2026-09-23 测 `bookstore-oh` 本地代码阶段代理指标（`require()` 耗时、mock 调用
执行耗时）时写的，从临时 scratch 目录搬进仓库保存，避免机器重启丢失。**不是 perf-optimizer
的正式实现**，只是一份验证过可行的参考脚本。

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
  重新构造 per-handler 的 mock event（不能全体 handler 共用一份通用 event，见上面
  `cart-add-item` 那条踩过的坑）。

## 如何复现（bookstore-oh 这次的测法）

1. 在这个目录（或任意 scratch 目录）`npm install @aws-sdk/client-dynamodb @aws-sdk/lib-dynamodb @aws-sdk/client-sqs`
   （版本对齐 `output/lambdas/*/package.json` 里声明的版本）。
2. 把 `output/lambdas/<domain>/<name>/handler.js` 复制进同一目录（改个不冲突的文件名）。
3. `node run_one.js <复制后的文件名> api`（或 `sqs`，取决于 `trigger_type`）。
