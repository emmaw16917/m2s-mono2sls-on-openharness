# rie/：绕开 `sam local invoke`，直接在 Lambda 运行时容器里测细粒度指标（2026-09-24）

`sam local invoke`/`start-lambda` 在这台 Windows 机器上要么静默失败，要么每次调用带约 4.8s
链路开销。这里的做法：先 `sam build`，再用同一个运行时镜像（`public.ecr.aws/lambda/nodejs:22-x86_64`，
自带 RIE）直接起容器，把构建产物 `docker cp` 进去，用 `127.0.0.1` 调 RIE 的 invoke 接口，最后读容器
日志里的 `REPORT` 行。这些是容器内部计时，不包含客户端链路的开销。

- `template.yaml`：最小 sam 项目（几个 bookstore handler、一个空函数 `Noop` 作为基线）。每个 CodeUri
  目录要放一份 `../http-shim.js`，通过 `NODE_OPTIONS=--require` 预加载。
- 脚本里写死了项目目录 `D:/tmp/m2s-samfine`：使用时把本目录复制到那里（短路径，避开 MAX_PATH），或者改掉脚本里的路径。
- `rie_measure.sh <BuildDir> <event.json> <cpus|0> [-e ...]`：1 次冷调用 + 30 次热调用，输出
  `init_ms`（取自 rapid 的 `INIT REPORT`）和热调用 Duration 的中位数/p10/p90。
  `-e SHIM_LATENCY_MS=5` 模拟每次调用 5ms 的网络延迟（Linux 下定时器是精确的），`-e SHIM_ITEMS=N`
  控制 Scan/Query 返回的条数。`cpus` 按 Lambda 的比例（1769MB = 1 vCPU）换算。

踩过的坑：
- `localhost` 在这台机器上每个请求固定多约 3.7s（先试 IPv6），必须用 `127.0.0.1`。
- 用绑定挂载代码时初始化约 1.6s，`docker cp` 进容器后约 0.1s。
- RIE 的 `Init Duration` 字段是占位值（0.03ms），`Max Memory Used` 只是回显 MemorySize，两个都不能用。
- 新版 SDK（`sam build` 装的）已经没有单独的 `@smithy/protocol-http` 包，`http-shim.js` 已改为不依赖它。

结果和解读见 `../../PERF_OPTIMIZATION.md` §3.2 实验 6。
