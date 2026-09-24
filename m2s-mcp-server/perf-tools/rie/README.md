# rie/：绕开 `sam local invoke`，直接在 Lambda 运行时容器里测细粒度指标（2026-09-24）

`sam local invoke` 之前在这台机器上静默失败、`start-lambda` 每次调用约 4.8s，根因是 `HTTP_PROXY`
没有配 `NO_PROXY`，sam 发往 `localhost` 的请求被代理截走了（已在 PowerShell 配置文件里补上
`NO_PROXY`）。sam 修好之后，测量仍然用这里的做法，因为 sam 会绑定挂载代码，冷启动会失真。

这里的做法：先 `sam build`，再用同一个运行时镜像（`public.ecr.aws/lambda/nodejs:22-x86_64`，
自带 RIE）直接起容器，把构建产物 `docker cp` 进去，用 `127.0.0.1` 调 RIE 的 invoke 接口，最后读容器
日志里的 `REPORT` 行。这些是容器内部计时，不包含客户端链路的开销。

- `rie_measure.sh <Function> <event.json> <cpus|0> [-e ...]`：在 `sam build` 的输出目录下运行
  （默认 `BUILD_ROOT=.aws-sam/build`，建议放在 `D:\tmp\...` 这类短路径下）。它会把 `../http-shim.js`
  （可用 `SHIM=` 覆盖）和构建产物一起 `docker cp` 进容器。
  - `VALIDATE=1`：只调用一次，输出状态码和 SDK 调用次数。**正式测量之前必须先跑这一步**：非 2xx 或
    调用次数为 0，说明事件没有让 handler 走到真实的代码路径，测出来的是假数字。
  - 默认模式：1 次冷调用 + 30 次热调用，输出 `init_ms`（取自 rapid 的 `INIT REPORT`）和热调用 Duration
    的中位数/p10/p90。
  - `-e SHIM_LATENCY_MS=5` 模拟每次调用 5ms 的网络延迟（Linux 下定时器是精确的）；`-e SHIM_ITEMS=N`
    控制 Scan/Query 返回的条数；`cpus` 按 Lambda 的比例换算（1769MB = 1 vCPU）。
- `template.yaml` / `events/` / `noop_handler.js`：实验 6 用的最小 sam 项目（几个 handler 和一个空函数
  基线）。对完整应用，直接用应用自己的 `template.yaml` 执行 `sam build` 即可，见 `PERF_OPTIMIZATION.md` 实验 7。

踩过的坑：
- 如果环境里有 `HTTP_PROXY`，一定要同时设置 `NO_PROXY=localhost,127.0.0.1,::1`，否则发往 `localhost` 的请求
  会被代理截走（约 3.7s 后返回 502）。脚本里直接用 `127.0.0.1`。
- 用绑定挂载代码时初始化约 1.6s，`docker cp` 进容器后约 0.1s。
- RIE 的 `Init Duration` 字段是占位值（0.03ms），`Max Memory Used` 只是回显 MemorySize，两个都不能用。
- 新版 SDK（`sam build` 装的）已经没有单独的 `@smithy/protocol-http` 包，`http-shim.js` 已改为不依赖它。

结果和解读见 `../../PERF_OPTIMIZATION.md` §3.2 实验 6。
