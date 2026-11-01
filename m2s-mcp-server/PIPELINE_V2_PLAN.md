# M2S 流水线 v2 实施方案

> **状态**：已定稿（2026-11-01）。原 §11 的 12 条提议（V2P-01 至 V2P-12）已确认，移进 §10 决策记录。
>
> **依据**：
> - 模块定义：`PIPELINE_REDESIGN_v2.md`（commit 43f5c59，2026-11-01 补充了 Q4、Q11），下文简称"设计稿"；
> - 编排方式：`PIPELINE_V3_PLAN.md`，下文简称"v3 方案"。本方案是 v3 方案 M4"接入 v2 模块"的前提和展开。
>
> **标记**：
> - **【必做】** 本方案的交付范围；
> - **【扩展】** 后续扩展，不在本方案的交付范围（§9）。
>
> **分支**：`feat/v2-modules`（worktree `D:\Emma\MyLab\Mono2SLs-v2`）。开发期间主目录在跑 v3 的真实运行，主目录下的 `m2s-mcp-server/` 不能改（tool guard 会撤回），所以 v2 的代码都在这个分支上写，阶段性合并。

---

## 0. 一页总览

**目标**：实现设计稿里的 v2 模块，在 v3 调度器下跑通 bookstore（Express + DynamoDB）和一个 Flask 应用。

**做法的顺序**：
1. **先做裁判线**（J1、J2、J3、J6）。它是设计稿三个核心想法里的第一个，而且不依赖蓝图，可以单独做、单独测。
2. **先拿裁判检验已有的 v1 产物**（bookstore 的两个 v0）。这一步不需要新的迁移，就能量出"v1 的测试通过了、但和单体不一致"的地方，既检验了裁判本身，也给出 v1 的盲区数据。
3. 再补裁判强度（J4、J5）、Flask 支持、缺口盘点和增量测试（G、D1、V3）。
4. 最后把全部模块写进 `coord/templates/v2.yaml`，在 v3 调度器下完整运行。

**里程碑**：

| 里程碑 | 内容 | 验收标准（摘要） | 范围 |
|---|---|---|---|
| **N0 准备** | 裁定记录 `rulings.json` 和 `record_ruling`；工作区里的版本戳；开发用的分段模板 | 裁定能写入、能触发受影响任务的失效；版本戳在每次运行的工作区里 | 【必做】 |
| **N1 裁判线最小闭环** | J1 用例格式和检查、J1 模式的 verifier、J2 `run_monolith`、J3 对账和事实修正、J6 冻结 | bookstore 建出冻结的裁判：15 个接口都有用例；单体上两遍运行结果稳定；对账出的差异全部分类处理 | 【必做】 |
| **N2 裁判验收** | 适配映射（A1 产出、A2 检查、V1 检查）、`run_judge`（多步请求、整个 API 的模拟、身份映射、符号化比对、R 层判定） | 冻结的 bookstore 裁判跑在 bookstore-v3a 和 bookstore-v2 的 v0 上，每个失败都分诊到位，写出报告 | 【必做】 |
| **N3 Flask 支持和裁判强度** | `run_monolith` 支持 Flask；J4 单体白盒补测；J5 变异检验 | 一个 Flask 应用建出冻结的裁判；两个应用都报告覆盖率和变异检出率 | 【必做】 |
| **N4 缺口和增量测试** | G1 `gap_scan`、G2、A1 的缺口决定和 A2 检查；D1 增量测试；V3；V4 分诊和 B0 | bookstore 的缺口每条都有决定；增量测试在代码出现之前写好；V3 在目标版本上通过 | 【必做】 |
| **N5 v2 全流程** | `v2.yaml` 全流程；I1 的 `run_judge` 自测；O 阶段改用裁判和增量测试；C1、C2 归档 | v2 流水线在 v3 调度下跑通 bookstore 和 Flask 应用（即 v3 方案 M4 的验收） | 【必做】 |
| N6 跨运行升级 | U1–U6 | 至少 2 个应用归档之后 | 【扩展】 |

**主要的后续扩展**（§9）：保留用例（Q1）；acmeair / MongoDB（Q5）；O3 架构改动申请；D1 和 I1 并行（Q8）；改动范围检查（V2-19）；U1–U6。

---

## 1. 起点

### 1.1 已有的、可以直接复用的

| v2 模块 | 现有的东西 | 位置 | 要做的 |
|---|---|---|---|
| M0 开工准备 | v3 的 harness 版本戳（两个仓库的 commit、工具版本、模型、agent 定义和技能的 hash） | `coord/harness.py` | 写进工作区的 `storage/harness_version.json` |
| F1 结构提取 | `analyze_project` | `spec_extract.py`、`spec_extract_py.py` | 不变；G1 需要的个别信号要补（§4.11） |
| F2 语义标注 | `m2s-spec-annotator`、`annotate_entry_point`、`annotate_global_middleware` | `agents/`、`server.py` | 增加"修正模式"和 G2 的"缺口模式" |
| F3 出处检查 | `check_provenance` | `provenance.py` | 不变 |
| A1、A2 | architect、`run_gate(blueprint)` | `agents/`、`gates.py` | A1 增加适配映射和缺口决定两项产出；A2 增加对应的检查 |
| D1 增量测试 | v1 的 V1 模式（只看规格写用例）、`run_validate(dry_run)` | `agents/m2s-verifier.md`、`verify.py` | 范围收窄到 serverless 新增的行为；资产放进 `verification/delta/` |
| I2、I3 | sam-engineer、validator、`gate(final)` | 现有 | 不变；`gate(final)` 增加"裁判未改动" |
| V3 | `run_validate`、`run_coverage`、`run_equivalence(record)` | `verify.py` | 改为作用在增量测试上 |
| O1、O2、O4 | perf-optimizer、`run_proxy_metrics`、`run_rie_verdict`、`run_config_verdict` | `perf.py` | O2 的检查改用裁判和增量测试 |
| 编排 | v3 调度器、任务板、worker 启动、完成检查、事件日志、异常处理者 | `coord/` | 新增 `v2.yaml` 模板、v2 的完成检查和流转规则 |

**验证线的执行引擎**可以复用的部分最多。`verify.py` 已经能把 HTTP 级的用例变成 Lambda 事件，并且模拟 API Gateway：路径参数、Cognito authorizer 对匿名请求返回 401（带 GatewayResponses 的头）、claims。`controller.js` 已经有 dynalite / DynamoDB Local、记录代理、故障注入、RIE 和本机两种执行方式。**还不支持的**：一个用例里有多个请求，并且分别落到不同的函数上（`verify.py` 里没有多步用例）。裁判要求写后读回，所以这是 `run_judge` 主要的新工作（§4.9）。

### 1.2 已经验证过的（2026-11-01，设计稿 Q4）

`verify-tools/monolith-run.js` 和 `monolith-preload.js` 在 bookstore 上跑了 5 个用例，各跑两遍：
- **aws-sdk v2 的单体能连到本地数据库**：用 `node -r monolith-preload.js server.js` 启动。预加载脚本从单体目录解析出应用自己的 `aws-sdk` 实例，设置 endpoint。所有 SDK 调用都经过记录代理，写进 dynalite，后面的请求能读回；
- **按用例记录覆盖率可行（Node）**：单体进程内用 inspector 的 `Profiler.takePreciseCoverage`，每次取数后计数清零。5 个用例的覆盖率各不相同，同一用例两遍完全一致；
- **易变字段能自动找出**：uuid、令牌、etag、时间戳、bcrypt 哈希；
- **发现的问题**：易变值之间有对应关系（购物车的 `customerId` 等于注册时生成的 `userId`），不能简单删掉，见 §4.4（设计稿 Q11）。

### 1.3 还没验证的

| 问题 | 在哪个任务里验证 |
|---|---|
| boto3 的单体能否用 `AWS_ENDPOINT_URL_DYNAMODB` 连到本地数据库（各应用的 boto3 版本是否够新） | N3.1 |
| Python 单体按用例记录覆盖率（coverage.py 的 `switch_context`） | N3.1 |
| 同一组数据，单体侧和目标版本侧的 Scan 顺序是否一致（表结构变了以后很可能不一致） | N2.3 |
| 单体启动的差异：端口变量、启动前的初始化脚本、需要的环境变量 | N1.3（bookstore）、N3.1（Flask） |

---

## 2. 和 v3 的关系

**编排由 v3 调度器负责，没有主 Agent**（V2P-01）：
- 设计稿里"执行者：主 Agent（工具）"的步骤，都变成调度器自己执行的动作（模板里 `runner: dispatcher`），例如 `run_monolith`、`freeze_judge`、`gap_scan`、`check_adapter`、`run_judge`。
- 标为 agent 的模块，变成 worker 任务。
- 设计稿里"主 Agent 复核"的工作，由调度器的完成检查完成。

**开发期用分段模板**：每个里程碑写一个只含相关模块的模板（例如 `v2-judge.yaml`：F1、F2、J1、J2、J3、J6），用 v3 M1 级别的调度器串行运行，不等 v3 的 M2、M3。

**讨论室出现之前**（v3 M3 之前）：
- 设计稿里进入"裁定通道"的问题，由调度器建一个 architect 的裁定任务，把问题清单交给它。这和 M1 里 V1 的 spec_issues 交给 S1 的做法相同；
- 偏离单体的裁定需要人确认（V2-20），走调度器的 `resolve` 命令；
- 判断不了的，交给异常处理者，再不行交给人。
- M3 做完以后，换成规格讨论室和分诊讨论室，模板不用改。

**隔离用 v3 的权限规则保证**（V2P-06）：
- J1 的 worker 禁止读单体代码（设计稿要求 J1"只看事实库，不看代码"，这样事实库的错误才会在 J3 暴露）；
- D1 的 worker 禁止读 `output/lambdas/**`；
- J6 之后，所有角色都禁止写 `judge/**`；
- 实现线可以读 `judge/`（V2-05）。

**和 v3 方案 M4 的对应**：本方案的 N5 就是 M4。v3 方案 M4 表格里"编排侧要补的"各项，分散在本方案的 N1–N5 里实现。M4 的验收应用从"bookstore 和 acmeair"改为"bookstore 和一个 Flask 应用"（V2P-08）。

---

## 3. 总体数据流（v2 一次运行）

```
F1 结构提取 → F2 语义标注 → F3 出处检查 ──► 事实库
                                         │
          ┌──────────────────────────────┼──────────────────────────────┐
          ▼                              ▼                              │
  G1 gap_scan → G2 缺口补充      J1 用例设计（只看事实库）                  │
          │                              │                              │
          ▼                              ▼                              │
  A1 蓝图 + 适配映射 + 缺口决定    J2 run_monolith（单体上跑两遍）           │
          │                              │                              │
          ▼                              ▼                              │
  A2 蓝图检查 ◄── 事实修正同步 ──  J3 对账：事实错误 → F2 修正；要裁定 → 裁定任务
          │                              │
          ▼                              ▼
  D1 增量测试（看不到代码）        J4 白盒补测 → J5 变异检验 → J6 冻结
          │                              │
          ▼                              ▼
  I1 写代码（run_judge 自测） → I2 模板 → I3 一致性
          │
          ▼
  V1 check_adapter → V2 run_judge → V3 增量测试 → V4 分诊 → tag B0
          │
          ▼
  O1–O4 优化（裁判 + 增量测试 + 和 B0 的一致性）→ C1 最终检验 → C2 归档
```

裁判线（J）和设计线（G、A）都只依赖事实库，在模板里是两条平行的依赖链。`--max-workers 1` 时按模板顺序串行；以后调高 worker 数，就能并行。

---

## 4. 组件规格

### 4.1 裁判用例（J1）【必做，N1】

**目录**（相对工作区，和设计稿附录 C 一致）：
- `judge/cases/<接口>/<用例>.json`；
- `judge/fixtures/<名称>.json`：前置数据，**按单体的表结构**写，表名用单体的表名；
- `judge/faults/<场景>.json`：故障场景；
- `judge/identities.json`：身份别名（§4.2）；
- `judge/run_config.json`：单体怎么启动（入口、端口变量、额外的环境变量），由工具从事实库推出，推不出的由 J1 补写。

**一个用例是一串 HTTP 步骤**（V2P-02）：
- 每一步一个请求，可以指定身份别名，可以从前一步的响应里取值（`save` / `${变量}`）；
- **写后读回**：写操作之后，用读接口的步骤把结果读回来，读回的步骤标 `read_back: true`。读回的结果属于外部效果，要比对；
- 每个用例标明所属接口、分层标签初值（P 或 R）、事实库出处；
- `predicted`：J1 按事实库推出的每一步的预期，沿用 v1 的 expect 写法（`status`、`body.equals/contains`、`headers`）。**它只用于 J3 对账**；裁判的期望一律取单体的实际输出。

**`check_judge_assets`**（新 MCP 工具，也作为 J1 的完成检查）：
- 格式检查；
- 每个接口至少有一个用例，包括鉴权失败、参数错误这类事实库里写了的错误分支；
- 事实出处在事实库里存在；
- fixtures 里的表名都是单体的表；
- 用到的身份别名都定义过；
- 引用的 fixtures、faults 文件存在。

**J1 的 worker**：沿用 `m2s-verifier`，在提示词和角色说明里加"J1 模式"一节。路径规则禁止读单体目录。

### 4.2 身份【必做，N1 单体侧，N2 目标侧】

`judge/identities.json` 定义用例里用到的用户别名（如 `u1`、`u2`、`admin`）。每个别名在两边各有一套取得方式：
- **单体侧**（J1 写，`run_monolith` 执行）：
  - 一段准备步骤，例如调用单体的注册、登录接口，从响应里取令牌；
  - 或者用单体配置里的密钥直接签发令牌。
  - 准备步骤不计入比对。
- **目标侧**（A1 写在适配映射的身份映射里，`run_judge` 执行）：claims 模板，例如 `{sub: <id:u1>, email: ...}`。

两边各自生成的用户 id（单体注册时生成的 `userId`，目标侧的 Cognito `sub`）都绑定到同一个符号 `<id:u1>`，这样"购物车的 customerId 等于这个用户的 id"这种对应关系在两边都能检查。

**注册、登录接口本身**：蓝图用 Cognito 取代它们时，这些接口在目标版本里不存在。以它们为对象的用例标成"迁移带来的变化"，不参与 P 层比对（V2-10）。但"匿名请求被拒绝"这类鉴权行为仍然属于 P 层。

### 4.3 `run_monolith`（J2）【必做，N1 Node，N3 Flask】

在 1.2 的验证脚本基础上做成 MCP 工具，同时也是调度器动作：
- **后端**：和 `run_judge` 用同一种数据库后端（V2P-11）。有 Docker 时用 DynamoDB Local，没有时用 dynalite，后端写进裁判清单。记录代理和故障注入从 `controller.js` 抽出来，做成共用模块 `verify-tools/ddb-proxy.js`，两边共用；
- **每个用例**：清空所有表 → 写入 fixtures → 取得用到的身份 → 依次执行步骤 → 记录每一步的响应、全部 SDK 调用、所有表的内容、本用例的覆盖率；
- **两遍运行**：每个用例跑两遍，生成屏蔽表（§4.4）。另外再跑第三遍，按屏蔽表核对，确认屏蔽后结果稳定；
- **故障场景**：按 `judge/faults/` 注入，产出 `fault_baseline_monolith.json`；
- **Node**：`node -r monolith-preload.js <入口>`，统一设置 `TZ=UTC`，清掉代理相关的环境变量；
- **Python**（N3）：用应用自己的 conda 环境，boto3 通过 `AWS_ENDPOINT_URL_DYNAMODB` 连接；覆盖率用 coverage.py 按用例切换上下文；
- **产出**：`judge/oracle_expected.json`、`judge/masks.json`、`judge/fault_baseline_monolith.json`、`judge/coverage_monolith/`。

### 4.4 屏蔽表和比对规则【必做，N1 屏蔽，N2 比对】

**符号化屏蔽**（V2P-05，设计稿 Q11）：
- 两遍运行之间不同的值，不删除，而是按出现顺序换成符号，例如 `<uuid#1>`、`<token#2>`、`<time#3>`；
- 同一个值在不同位置出现（响应、后续请求、表内容），用同一个符号；
- 比对时，要求目标版本在对应位置也是"同一个值"（符号一致），而不只是"有个值"；
- 身份 id 用身份的符号（`<id:u1>`）。

**P 层比对哪些东西**（V2P-03）：
- 每一步的状态码和响应体；
- **声明过的响应头**：全局中间件加的头（事实库的 `global_middleware`）、`content-type`、事实库里明确写了的头；
- 读回步骤的结果。

**不比对的**（T 层）：
- 框架自带、和行为无关的头：`etag`、`x-powered-by`、`content-length`、`date`、`connection`；
- SDK 调用的次数和顺序；
- 表内容本身。写入的效果靠读回步骤体现；某些写入没有读接口可以读回时，把目标版本的表内容经适配映射反向换成单体的结构，再比对。

**R 层**：按裁定记录里声明的范围比较，例如"顺序不作要求"按集合比较、"允许多种结果"按候选集合比较。J3 把"列表元素相同、只是顺序不同"的差异自动归为"放宽候选"，但仍然要 architect 裁定，才能成为 R 层。

### 4.5 规格对账（J3）和事实修正【必做，N1】

1. **`reconcile_spec`**（调度器动作）：逐个用例、逐个步骤比较 J1 的 `predicted` 和单体的实际输出（屏蔽后），把差异写进 `judge/reconcile.json`；
2. **分类任务**（verifier，J3 模式）：给每条差异分类：
   - **事实错误**：单体就是这样，事实库写错了；
   - **需要裁定**：单体本身的缺陷、可能可以放宽的行为；
   - **用例写错**：J1 自己的错误，比如请求写错了；
   - **其实一致**：屏蔽不完整造成的假差异。
3. **流转**（调度器规则）：
   - 事实错误 → F2 修正任务（annotator 修正模式，输入是差异清单）→ F3 重查 → J1 回炉，只改受影响用例的 `predicted` → 重新对账；
   - 需要裁定 → architect 裁定任务（§2）；偏离单体的要人确认；
   - 用例写错 → J1 回炉；
   - 其实一致 → 补屏蔽规则，重跑 J2。
4. **上限**：最多 2 轮，之后仍有未解决的，交给人。
5. **同步设计线**：事实库被修正、并且蓝图已经引用了被改的事实时，A1 的任务标为失效（v3 的失效传播），由调度器决定重新检查还是重做。

### 4.6 裁定记录和 `record_ruling`【必做，N0】

- **`storage/rulings.json`**：只追加。每条包括编号、提出模块、问题、可选方案、裁定者、结论、类别（`contract` / `implementation-trace` / `migration-change` / `spec-gap`，以及放宽的类型）、影响的用例和观测层、依据、时间（设计稿 §4.12）；
- **`record_ruling`**（m2s-server 工具）：按调用者的 `M2S_ROLE` 限制，只有 architect 和异常处理者能调用。人通过调度器的 `resolve` 命令写裁定；
- **失效**：一条裁定影响到的用例或模块，调度器按 v3 的失效规则重新检查或重做；
- **和 v1 的区别**：v1 的裁定写在 `blueprint.metadata.spec_resolutions` 里，v2 的运行不再写那里（V2-07）。

### 4.7 冻结裁判（J6）【必做，N1】

- **`freeze_judge`**（调度器动作）：写 `judge/judge_manifest.json`，内容包括裁判版本号、每个文件的 hash、用例数、P 层和 R 层的用例数、覆盖率、检出率（N3 之后）、后端、事实库的 hash；
- **"裁判未改动"检查**：加进 `gate(final)` 和调度器每个任务的完成检查。hash 对不上的，撤销改动，这个任务判为失败；
- **冻结后**：worker 的路径规则禁止写 `judge/**`。改裁判只能由人批准、升级裁判版本，并重新验收。

### 4.8 适配映射（A1 产出、A2 检查、V1 检查）【必做，N2】

**`storage/adapter_map.json`**，由 architect 在 A1 产出（V2-16）。用**声明式 JSON** 写，不允许写代码做转换（V2P-10）：
- **数据映射**：单体的每张表 → 目标的表；字段可以改名、取常量、换成身份 id、把几个字段拼成复合键或者把复合键拆开、JSON 编码和解码。单体的表在目标里不存在的（比如 users 表被 Cognito 取代），写明去向；
- **身份映射**：每个身份别名在目标侧的 claims 模板，以及用户资料字段放在哪里（Cognito 标准属性，或者某张表）。

**A2 的新检查**（`gates.py` 的 blueprint 阶段）：
- 适配映射覆盖了裁判 fixtures 里出现的每张表；
- 蓝图改了鉴权方式时，有身份映射；
- 缺口清单的每一条都有决定（N4 起）。

**V1 `check_adapter`**（调度器动作）：把单体结构的 fixtures 经映射写进目标版本的表，再调用目标版本的读接口，结果必须和单体读接口在同一份数据上的输出一致（取自裁判里读接口的用例）。防止映射写错，让裁判误判。

### 4.9 `run_judge`【必做，N2；I1 的自测模式在 N5】

在 `verify.py` / `controller.js` 上扩展（V2P-04），不改用 `sam local start-api`（启动慢，而且在本机遇到过 Docker 和路径长度的问题）：
- **整个 API 的模拟**：按模板里各函数的 API 事件，把每一步请求路由到对应的函数；同一个用例的所有步骤共用同一份表状态；
- **异步消息**：一步里发出的 SQS 消息，在下一步之前投递给订阅它的函数（按模板里的事件源映射），这样"下单后读畅销榜"这类读回才有意义；
- **准备数据**：fixtures 经适配映射写入；身份经身份映射变成 claims；
- **比对**：和 `oracle_expected.json` 比，按 §4.4 的规则；
- **产出**：`verification/verdict_judge.json`，逐个用例、逐步列出结果，失败的写明函数、事实出处、期望和实际；
- **执行方式**：有 Docker 时在 Lambda RIE 里运行，否则在本机运行，报告里注明；
- **I1 自测模式**（N5，设计稿 Q6）：模板还不存在时，路由和环境变量从蓝图推出，只能本机运行，只跑指定函数相关的用例。

### 4.10 裁判强度：J4 白盒补测、J5 变异检验【必做，N3】

- **J4**（verifier，J4 模式，此时允许读单体代码）：
  - 看单体的覆盖率报告，给还没走到的分支补用例 `wb-*.json`，补出的用例回到 J2 在单体上运行；
  - 从代码里看出、事实库里没有的规则，**必须**提成规格问题（缺失类），退回 F2 补进事实库。
- **J5 `run_mutants`**（调度器动作 + verifier 判断等价变异）：
  - **变异体按事实库的出处定位**：在业务规则、响应、鉴权所在的行上，按设计稿的算子生成变异：删掉分支、改状态码、删掉响应字段、去掉一次写操作、把条件取反、去掉鉴权检查；
  - **"并行改成串行"是反向检查**：这类变异不改变外部效果，**不应该**被裁判抓住，抓住了说明裁判在判定实现轨迹；
  - **怎么运行**：每个变异体复制一份单体到临时目录（`node_modules` 用目录链接），再用 `run_monolith` 跑裁判；
  - **产出**：`judge/judge_validation.json`：变异体清单、哪些被抓住、检出率。没被抓住的，回 J4 补用例，最多 2 轮。

### 4.11 缺口盘点（G1、G2）【必做，N4】

**G1 `gap_scan`**（调度器动作）按事实库里的机械信号识别缺口，产出 `storage/gap_inventory.json`：

| 类别 | 用到的事实（现有 / 要补） |
|---|---|
| 一个请求里有多次写操作 | 接口的 `data_access` 里有 2 次以上写操作，且没有事务（现有） |
| 即发即忘的副作用 | 调用图里 `is_awaited: false` 且 `return_value_used: false` 的异步调用（现有） |
| 全局中间件 | `global_middleware`（现有） |
| 鉴权语义 | 接口的 `auth` 分类（现有） |
| 单体自身的缺陷：Scan 不翻页 | `data_access` 的 scan 操作，加上"有没有处理 `LastEvaluatedKey`"（**要补**） |
| 进程内状态 | 模块级的可变变量、内存缓存（**要补**，F1 增加一项） |
| 顺序和时间 | 排序调用、`Date.now` / `new Date` / 定时器（**要补**） |
| 文件系统和长连接 | `fs` 写操作、WebSocket（**要补**） |

**要补的信号**加进 `spec_extract.py`（Express），Flask 版在 `spec_extract_py.py` 里对应补上。这几项都只是增加字段，不改动已有的字段。

**G2**（annotator，缺口模式）：补充机械信号识别不出的缺口；不属于已知类别的，标为"新类别候选"。

**A1**：在 `DECISIONS.md` 里对每一条缺口写决定，并把 `gap_inventory.json` 里的状态改为 `decided`（V2-17）。A2 检查是否有遗漏。

### 4.12 增量测试（D1、V3）【必做，N4】

- **D1**（verifier，D1 模式）：只看蓝图、缺口决定、规则手册的必须规则、单体的实际输出，在写代码之前设计；路径规则禁止读 `output/lambdas/**`。资产放在 `verification/delta/`，格式沿用 v1 的验证资产；
- **期望从哪里来**：能对照单体的，期望取"单体执行一次的效果"，从 `oracle_expected.json` 引用（V2-08）；单体里没有对应物的，期望来自裁定或规则手册；
- **`run_validate`** 增加参数，可以指定资产目录（`verification/delta`）；
- **V3**：在增量测试上跑 `run_validate`、`run_coverage`、`run_equivalence(record)`，产出 `verdict_delta.json`、覆盖率和 `fault_baseline_B0.json`。这些都是现有工具，换成作用在增量测试上。

### 4.13 分诊和 B0（V4）【必做，N4】

调度器的流转规则（沿用 v3 M1 里 V2 回炉路由的做法，规则按设计稿 §4.11）：
- `check_adapter` 失败：映射的问题 → A1；读接口代码的问题 → I1。分不清的 → 分诊案例；
- 裁判 P 层失败：有机械信号的直接路由（模块导入失败 → I2 打包问题；SDK 解码错误 → 适配映射或代码）；其余的开分诊案例，由异常处理者判断是 I1、I2 还是 A1。**分诊过程不能改裁判**；
- 增量测试失败：代码 → I1；测试资产 → D1 自修；蓝图 → A1；
- 转给实现线的内容：失败的函数、事实库或规则的出处、期望和实际。裁判用例对实现线可见，可以直接引用；
- 全部通过：在工作区 git 里打 tag `B0`（沿用 v3 的 `tag_v0` 动作，改名为 `tag_b0`）。

### 4.14 优化阶段（O1、O2、O4）【必做，N5；O3 为扩展】

- O1：不变；
- O2 的检查改为：
  - 裁判全部通过（`run_judge`）；
  - 受影响函数的增量测试通过；
  - 和 B0 的行为一致（`run_equivalence`）；
  - 改动到的代码行被测试执行到（`run_coverage(changed_since)`）；
  - 性能判决不变；
- 性能测量按 v3 方案 §4.13 在 AWS 上实测（那部分由 v3 方案负责）；
- O3 架构改动申请列为扩展（V2P-09）：它要开启新一轮"设计 → 增量测试 → 实现 → 验收"，等 B0 之前的部分稳定以后再做；
- O4：不变。

### 4.15 收尾和归档（C1、C2）【必做，N5】

- **C1**：`output/final_report.md` 写明：
  - 每一层在什么环境下检验的（RIE 还是本机、DynamoDB Local 还是 dynalite）；
  - 裁判版本和强度指标；
  - 保留了哪些优化；
  - 裁定了哪些问题、哪些仍未解决；
  - 人工裁定的次数。
- **C2 `archive_run`**：把版本戳、裁定记录、缺口清单、决策记录、裁判清单和强度指标、性能报告、各模块工作报告的索引、最终判定摘要，连同 `coord/` 一起复制到 `m2s-mcp-server/evidence/<应用>/<运行名>/`，只追加。现有的 `update_ledger` 以后改为从证据库生成台账。

### 4.16 v2 模板【必做，N5；分段模板从 N1 起】

任务清单和依赖见附录 A。原则：
- 设计稿里的工具步骤 → `runner: dispatcher`；
- agent 模块 → worker，写入范围按设计稿 §3.3、§3.4 的产出位置；
- 完成检查用各模块的 gate 或检查工具；
- 裁判线和设计线是两条平行的依赖链，在 I1 之前汇合。

---

## 5. 实施计划

每项都标了相对规模（S、M、L），只指开发和本机测试的工作量，不含 LLM 的长时间运行。

### N0 准备【必做】

| 编号 | 内容 | 产出 | 验收 | 依赖 | 规模 |
|---|---|---|---|---|---|
| N0.1 | 工作区版本戳：调度器开工时把 harness 版本戳写进 `storage/harness_version.json`；v2 的工作区目录约定 | `coord/dispatcher.py` | 新运行的工作区里有版本戳 | — | S |
| N0.2 | 裁定记录：`rulings.json` 的结构、`record_ruling` 工具（按角色限制）、调度器 `resolve` 增加写裁定；裁定触发受影响任务失效 | `coord/rulings.py`、`server.py`、`coord/dispatcher.py` | 单元测试：写入、角色限制、只追加；假 worker 测试：裁定后受影响的任务重新检查 | — | M |
| N0.3 | 共用的记录代理：把 `controller.js` 里的 dynalite / DynamoDB Local、记录代理、故障注入抽成 `ddb-proxy.js`，`controller.js` 改为调用它 | `verify-tools/ddb-proxy.js` | 现有的 `run_validate` 测试全部通过（行为不变） | — | M |

**进展（2026-11-01）**：
- N0.1 完成（commit 85af6e0）。目录约定没有另外写代码：`judge/` 的结构按 §4.1，由 N1 的工具创建；
- N0.2 完成（commit a1a85d1）。实现上的取舍：
  - `record_ruling` 只写提议到 `coord/rulings/`，这次尝试通过检查后才收进 `storage/rulings.json`。失败的尝试留下的裁定不算数；
  - `storage/rulings.json` 和 `storage/harness_version.json` 都由调度器从事件日志重建，worker 改了没有效果；
  - 裁定通过 `affects.tasks` 点名受影响的任务。按用例找任务（`affects.cases` → 哪个任务）要等 N1 有了裁判用例再做；
  - 偏离单体的裁定在人确认之前不生效；N1.6 的 `freeze_judge` 要检查没有待确认的裁定。
- N0.3 见 CHANGELOG。

### N1 裁判线最小闭环（bookstore）【必做】

| 编号 | 内容 | 产出 | 验收 | 依赖 | 规模 |
|---|---|---|---|---|---|
| N1.1 | 裁判资产格式和 `check_judge_assets` | `judge.py`（新）、`server.py` | 单元测试覆盖每条检查 | — | M |
| N1.2 | 身份：`identities.json`，单体侧的取得方式（准备步骤或签发） | `judge.py`、`verify-tools/monolith-run.js` | bookstore 上 `u1`、`u2` 两个身份能取得；准备步骤不计入比对 | N1.1 | S |
| N1.3 | `run_monolith`：验证脚本做成工具；`run_config.json`；fixtures、步骤、两遍运行加一遍核对、符号化屏蔽、故障场景、按用例覆盖率；用 `ddb-proxy.js` | `verify-tools/monolith-run.js`、`judge.py`、`server.py` | 用手写的 bookstore 用例（不用 LLM）跑通；第三遍按屏蔽表核对全部稳定；符号能表达"customerId 等于 userId" | N0.3、N1.1、N1.2 | L |
| N1.4 | J1 模式的 verifier：角色说明加 J1 一节；路径规则禁止读单体目录 | `agents/m2s-verifier.md`、`coord/workers.py` | 权限测试：J1 worker 读单体代码被拒绝 | N1.1 | M |
| N1.5 | J3：`reconcile_spec`、J3 模式的分类任务、F2 修正模式、调度器的流转规则（§4.5） | `judge.py`、`agents/`、`coord/dispatcher.py` | 假 worker 测试覆盖四类差异的流转和 2 轮上限 | N1.3、N0.2 | L |
| N1.6 | J6：`freeze_judge`；"裁判未改动"检查；冻结后禁止写 `judge/**` | `judge.py`、`gates.py`、`coord/` | 冻结后改一个用例文件，下一个任务的完成检查失败并撤销 | N1.1 | S |
| N1.7 | 分段模板 `v2-judge.yaml`，在 bookstore 上真实运行 | `coord/templates/v2-judge.yaml`、新工作区 `bookstore-j1` | 见下 | 以上全部 | M |

**N1 的验收标准**（bookstore）：
- 15 个接口都有用例，包括事实库里写了的错误分支；
- 全部用例在单体上跑通，第三遍按屏蔽表核对稳定；
- 对账出的差异全部分类并处理完：事实修正已写进事实库并通过出处检查，裁定已写进 `rulings.json`；
- 裁判已冻结，清单里有用例数、P 层和 R 层的用例数、覆盖率；
- J1 的 worker 没有读过单体代码（权限日志里没有放行的读取）。

**进展（2026-11-01）**：N1.1–N1.6 完成（commit 3a89dd9、1cef0b3 和随后的一个提交，细节见 CHANGELOG）。实现上的取舍：
- **覆盖要求**：每个 500 以下、不是 catch 出来的响应，都要被某个用例引用；5xx 和 catch 分支靠故障场景走到，只统计不强制。J1 确实做不到的，写进 `judge/uncovered.json` 并说明原因（bookstore 上 `GET /api/auth/me` 的 dev 模式分支就是这种情况）；
- **身份**：除了准备步骤，还可以用单体的密钥签发令牌（`sign`），用来造"令牌有效但用户不在表里"这类情况。前提是事实库里写明了密钥；
- **单体侧的 SDK 重试**：spike 里的预加载关掉了单体 SDK 的重试，这会改变单体的行为。现在保留重试次数，只缩短退避时间；
- **J1 的隔离**：单体目录关闭；单体的上一级目录不包含工作区时也一起关闭；提示词里不给单体路径；
- **J3 的裁定任务**：调度器动态新建 architect 任务（`RULE_J3_<轮>`），J6 等它完成。偏离单体的裁定走 V2-20 的人工确认；
- **worker 的工具和角色说明**：一律取自调度器所在的仓库。在 worktree 里运行时，用的就是 v2 分支的工具和角色说明；
- **手写裁判的结果**：bookstore 上手写了 27 个用例和 2 个故障场景（不用 LLM），跑三遍 92 s，0 处不稳定，行覆盖率 89.0%，对账 78 步 0 差异。这些预期是读着单体代码写的，所以差异为 0 只说明工具能用，不代表对账的效果。

**省时的做法**：`bookstore-j1` 可以从 bookstore-v3a 的事实库开始（复制 `storage/analysis_report.json` 和 `symbol_table.json`，727 条出处、F3 已通过），模板里去掉 F1、F2，节省约 40 分钟。复制是安全的，不会重跑 `analyze_project`。

### N2 裁判验收（先在已有的 v1 产物上检验）【必做】

| 编号 | 内容 | 产出 | 验收 | 依赖 | 规模 |
|---|---|---|---|---|---|
| N2.1 | 适配映射：格式、architect 角色说明的 A1 新产出、A2 的新检查 | `judge.py`、`agents/m2s-architect.md`、`gates.py` | 单元测试；bookstore 的映射能表达"users 表 → Cognito"和身份映射 | N1.1 | M |
| N2.2 | 整个 API 的模拟：多步用例、按模板路由到各函数、共用表状态、步骤之间投递 SQS 消息 | `verify.py`、`verify-tools/controller.js` | 手写的多步用例在 bookstore-v3a 的 v0 上跑通，包括"下单 → 读畅销榜" | N0.3 | L |
| N2.3 | `run_judge`：fixtures 经映射写入、身份映射、按屏蔽和 R 层规则比对、`verdict_judge.json` | `judge.py`、`verify.py`、`server.py` | 单元测试覆盖符号比对和 R 层比较；在 v0 上产出逐步的判定 | N2.1、N2.2、N1.6 | L |
| N2.4 | V1 `check_adapter` | `judge.py` | 故意写错一个映射字段，被它查出来 | N2.1、N2.2 | M |
| N2.5 | 检验已有的 v1 产物：给 bookstore-v3a 的 v0（v3 M1 的运行）和 bookstore-v2 的 v0 补适配映射（architect 的"只写映射"任务），跑 `check_adapter` 和 `run_judge`，逐条分诊 | 报告 `judge_on_v1.md` | 见下 | N2.1–N2.4（bookstore-v3a 已于 2026-11-01 打完 v0，tag 在 74b91b9） | M |

**N2 的验收标准**：
- 两个 v0 都有完整的判定结果，`check_adapter` 通过；
- 每个失败都分到四类之一：实现缺陷（v1 的测试漏掉的）、适配映射的问题、裁判本身的问题、迁移带来的变化；
- 报告列出"v1 的测试通过了、但和单体不一致"的清单，作为 v1 盲区的数据。

**报告里要对照的已知例子**（来自 bookstore-v3a，2026-11-01）：
- **form-encoded 请求体**：V1 按事实库预测加入购物车后存的是数字，单体实际存字符串 `"2"`、`"10.5"`（BR-CART-ADD-4，类型不转换）；v1 里是 V2 发现后改了用例。v2 里这类差异应当在 J3 对账时直接分成"事实错误"或由单体输出决定；
- **JSON 标量请求体**（SI-22，人决定留作未决问题）：`application/json` 但内容是 `null`、`5`、`"x"` 时，规格有两种读法，v1 的用例只断言两种读法的交集（400、没有副作用、不断言响应体）。v2 的裁判直接取单体的实际输出（body-parser strict 模式），这条歧义不再需要裁定；报告里核对裁判是否这样处理了；
- **Cognito 设置**：blueprint 写了 refresh 令牌 7 天，模板第一次没写（Cognito 默认 30 天），v1 的测试和 gate 都没发现，阶段复查才发现，之后补了 gate C14。v2 的裁判只比对接口行为，这类部署设置仍然靠 gate；报告里注明这是裁判覆盖不到的一类。

### N3 Flask 支持和裁判强度【必做】

| 编号 | 内容 | 产出 | 验收 | 依赖 | 规模 |
|---|---|---|---|---|---|
| N3.1 | `run_monolith` 支持 Flask：先做可行性检查（boto3 版本和端点变量、coverage.py 按用例切换上下文），再实现 | `verify-tools/`、`judge.py` | 选定的 Flask 应用上，手写用例跑通，按用例的覆盖率可用 | N1.3 | M |
| N3.2 | J4 白盒补测：verifier 的 J4 模式；补出的用例回 J2；发现的新规则必须提成规格问题 | `agents/m2s-verifier.md`、模板 | 假 worker 测试：新规则走"规格问题 → F2"，没有只写进用例 | N1.5 | M |
| N3.3 | J5 `run_mutants`：按事实库出处定位的变异算子（Node 先做，Python 随后）；变异体在临时副本上运行；verifier 判断等价变异 | `judge.py`、`verify-tools/` | bookstore 上产出检出率；"并行改成串行"的变异全部没被抓住 | N1.3 | L |
| N3.4 | Flask 应用的裁判：用分段模板在选定的 Flask 应用上建出冻结的裁判 | 新工作区 | 和 N1 的验收标准相同 | N3.1 | M |

**Flask 应用的选择**：从 airline-booking、ecommerce、shopping-cart 里选一个，以接口数少、启动依赖少为准，在 N3.1 的可行性检查时定。

**N3 的验收标准**：Flask 应用的裁判已冻结；bookstore 和这个应用都在清单里报告覆盖率和变异检出率；"并行改成串行"的变异全部没被抓住。

### N4 缺口盘点和增量测试【必做】

| 编号 | 内容 | 产出 | 验收 | 依赖 | 规模 |
|---|---|---|---|---|---|
| N4.1 | F1 补信号（§4.11 标"要补"的四项），Express 和 Flask 两边 | `spec_extract.py`、`spec_extract_py.py` | 单元测试；已有字段不变（现有测试全部通过） | — | M |
| N4.2 | G1 `gap_scan` | `gaps.py`（新）、`server.py` | bookstore 上识别出已知的缺口（即发即忘的畅销榜更新、全局 CORS、自签发 JWT） | N4.1 | M |
| N4.3 | G2 缺口模式；A1 写缺口决定；A2 检查遗漏 | `agents/`、`gates.py` | 假 worker 测试：有未决定的缺口时 A2 不通过 | N4.2 | S |
| N4.4 | D1 增量测试：verifier 的 D1 模式；`run_validate` 可以指定资产目录；期望引用单体输出 | `agents/m2s-verifier.md`、`verify.py` | 权限测试：D1 worker 读 `output/lambdas` 被拒绝 | N2.3 | M |
| N4.5 | V3：现有工具改为作用在增量测试上；产出 `fault_baseline_B0.json` | `verify.py` | 在一个目标版本上产出三个文件 | N4.4 | S |
| N4.6 | V4 分诊和 B0：调度器流转规则（§4.13）；`tag_b0` | `coord/dispatcher.py` | 假 worker 测试覆盖每条路由 | N2.3、N4.5 | M |

**N4 的验收标准**（bookstore）：缺口清单每条都有决定；增量测试在代码出现之前写好；V3 在目标版本上通过。

### N5 v2 全流程（即 v3 方案 M4）【必做】

| 编号 | 内容 | 产出 | 验收 | 依赖 | 规模 |
|---|---|---|---|---|---|
| N5.1 | `v2.yaml` 全流程模板（附录 A） | `coord/templates/v2.yaml` | 假 worker 跑完整个模板 | N1–N4 | M |
| N5.2 | I1 的 `run_judge` 自测模式：从蓝图推出路由和环境变量，本机运行（设计稿 Q6） | `judge.py`、`verify.py` | code-developer 能对自己的函数跑裁判用例 | N2.3 | M |
| N5.3 | O2 的检查换成裁判 + 增量测试 + 和 B0 的一致性 | `perf.py`、`agents/m2s-perf-optimizer.md` | 一个已知保留的改动（例如 R1 并行化）在新检查下仍然保留 | N2.3、N4.5 | M |
| N5.4 | C1 最终报告；C2 `archive_run`（含 `coord/`） | `judge.py`、`coord/` | 运行结束后证据库里有完整的归档 | N0.1 | M |
| N5.5 | 完整运行：bookstore 和 Flask 应用 | 两个新工作区 | 见下 | 以上全部 | L |

**N5 的验收标准**：
- 两个应用都在 v3 调度下跑到 C2，B0 通过裁判和增量测试；
- 报告里有裁判版本、强度指标、裁定次数（其中人工裁定的次数）；
- 证据库里有两次运行的归档；
- 和同一应用的 v1 运行（bookstore-v3a 等）相比，列出结果的差别。

---

## 6. 测试和验收方法

| 类别 | 做法 | 在哪个里程碑做 |
|---|---|---|
| 单元测试 | 资产格式检查、符号化屏蔽和比对、R 层比较、适配映射、裁定记录、缺口信号、变异算子 | 各里程碑 |
| 工具集成测试（不用 LLM） | 手写的 bookstore 裁判用例，直接跑 `run_monolith` 和 `run_judge`；故意写错映射、故意改坏单体，检查能否发现 | N1 起 |
| 假 worker | 新的流转规则（J3 四类差异、事实修正、裁定、分诊）用 v3 的假 worker 测试 | N1 起 |
| 权限测试 | J1 读单体代码、D1 读 `output/lambdas`、冻结后写 `judge/`，都必须被拒绝 | N1、N4 |
| 真实运行 | 分段模板在 bookstore、Flask 应用上运行；比较验收结果，不逐字节比较 | 各里程碑 |
| 对照 | N2：裁判对 v1 产物的判定；N5：v2 全流程对同一应用的 v1 运行 | N2、N5 |

---

## 7. 指标

- **裁判强度**：单体的分支覆盖率、变异检出率、"并行改串行"变异的误报数；
- **裁判规模**：用例数，P 层和 R 层各多少，屏蔽的字段数；
- **对账**：差异总数，四类各多少；事实修正的条数；
- **裁定**：裁定数，其中需要人确认的数量，人工耗时（设计稿 Q9）；
- **对 v1 产物的判定**（N2）：通过率，以及"v1 的测试通过了、裁判不通过"的条数；
- **各模块的耗时和 token**（来自 v3 的事件日志）。

---

## 8. 风险和应对

| 风险 | 应对 |
|---|---|
| 单体启动方式各不相同（端口、初始化脚本、环境变量） | `run_config.json`：能从事实库推出的由工具推出，推不出的由 J1 补写；启动失败作为环境问题报给人 |
| 屏蔽之后仍有不确定性（例如依赖当前时间的分支、令牌过期） | 第三遍核对能发现；需要时在预加载脚本里固定时钟（可选项，按应用打开） |
| Scan 和无排序的查询，两边的结果顺序不同 | J3 自动归为"放宽候选"，由 architect 裁定成 R 层的"顺序不作要求"。积累到多个应用后，可以晋升为规则手册里的裁定先例 |
| 单体侧和目标侧用的数据库后端不同，行为有差别（例如 dynalite 没有事务、键类型校验不同） | 两边强制用同一种后端，并写进裁判清单；不一致时判定无效 |
| 适配映射写错，导致裁判误判 | V1 `check_adapter` 先于 V2 运行；映射只能用声明式的转换 |
| 整个 API 的模拟和真实 API Gateway 有差别 | 只模拟已经有的部分（路由、authorizer、GatewayResponses）；差别作为已知限制写进报告；真实环境由 O 阶段的 AWS 实测补充 |
| 变异检验的运行时间长（每个变异体都要跑一遍裁判） | 只在事实库出处所在的行上生成变异；每个算子设上限；变异体之间并行（受资源锁限制） |
| LLM 任务的费用和时间 | 开发期先用手写用例测试工具，真实运行用分段模板，每段结束后检查 |

---

## 9. 后续扩展【扩展】

| 项 | 内容 | 什么时候做 |
|---|---|---|
| 保留用例（设计稿 Q1） | 留出一部分裁判用例只在验收时运行 | 容器沙箱就绪以后（v3 方案 §9） |
| acmeair / MongoDB（Q5） | 单体连接容器里的 MongoDB，只做接口层比对 | N5 之后 |
| O3 架构改动申请 | 新一轮"设计 → 增量测试 → 实现 → 验收"，不做和 B0 的一致性比对 | N5 稳定以后 |
| D1 和 I1 并行（Q8） | 权限修复之后可以并行 | v3 M2 之后 |
| 改动范围检查（V2-19） | `check_change_scope` | O3 之后 |
| U1–U6 流水线升级 | 汇总证据库、晋升评审、压测、发布、回补、统一重跑 | 至少 2 个应用归档以后 |
| 故障用例只断言不变量（Q3 的后续方向） | 单体返回 500 的故障用例改为断言"没有部分写入、不对失败的操作返回成功" | N3 之后，看实际的故障用例再定 |

---

## 10. 决策记录

**沿用设计稿已定的**：V2-01 至 V2-15、V2-21（设计稿附录 A）。

**设计稿里标为"提议"的**：
- V2-16、V2-17、V2-20：本方案按提议实施；
- V2-18：归 v3 方案 M5；
- V2-19：列为扩展（见 V2P-12）。

**本方案新提出的**（V2P-01 至 V2P-12，2026-11-01 确认）：

| 编号 | 决定 | 理由 | 代价 |
|---|---|---|---|
| V2P-01 | v2 跑在 v3 调度器下，没有主 Agent；设计稿里主 Agent 执行的工具步骤都变成调度器动作；开发期用分段模板，不等 v3 的 M2、M3；讨论室出现之前，裁定走 architect 裁定任务、异常处理者和人 | v3 已经能跑；确定性的步骤不需要 LLM | M3 之前，有争议的问题不能多方讨论，只能由 architect 一方裁定 |
| V2P-02 | 裁判用例是一串 HTTP 步骤，写后读回作为单独的步骤；用身份别名；测试数据按单体的表结构；J1 写的预期只用于对账 | 设计稿把读回的数据列为外部效果；单步用例表达不了 | `run_judge` 要做多步和跨函数（N2.2，规模 L） |
| V2P-03 | P 层比对：每一步的状态码、响应体、声明过的响应头、读回的结果；`etag` 等框架自带的头和表内容本身不比对（没有读接口的写入除外） | Express 和 API Gateway 自带的头不同，比对它们只会产生假失败 | 漏掉"多了或少了某个未声明的头"这类差别 |
| V2P-04 | `run_judge` 扩展现有的 `verify.py` / `controller.js`，不用 `sam local start-api` | 现有引擎已经有 API Gateway 模拟、记录代理和故障注入；`sam local` 在本机遇到过 Docker 和路径长度的问题 | 自己维护路由和 SQS 投递的模拟 |
| V2P-05 | 易变字段换成符号，保留对应关系（设计稿 Q11） | 1.2 的验证里，`customerId` 必须等于 `userId` | 比对逻辑比"直接删掉"复杂 |
| V2P-06 | 设计稿里的"谁不能看什么"用 v3 的路径规则强制执行：J1 不能读单体代码，D1 不能读实现代码，冻结后谁都不能写 `judge/` | 只靠提示词约束，不可靠 | bash 的递归读取仍是已知的漏洞（v3 W0.5） |
| V2P-07 | 先用冻结的 bookstore 裁判检验已有的两个 v1 产物，再做缺口和增量测试 | 不需要新的迁移就能检验裁判，并量出 v1 的盲区 | 要给 v1 产物补适配映射 |
| V2P-08 | 第二个验收应用用一个 Flask 应用，acmeair（MongoDB）列为扩展；v3 方案 M4 的验收应用相应修改 | MongoDB 只能做接口层比对，不适合作为第一批验收；Flask 覆盖了另一种语言和 SDK | M4 暂时不覆盖 MongoDB |
| V2P-09 | O3 架构改动申请列为扩展；N5 的优化阶段只做 O1、O2、O4 | 先让 B0 之前的部分稳定；O3 相当于再走一遍半条流水线 | v2 第一版不能做加 GSI 这类架构改动（v1 的做法是只写建议） |
| V2P-10 | 适配映射只用声明式的转换，不允许写代码 | 映射也是被检验的对象，代码转换难以检查 | 少数复杂的数据变换可能表达不了，到时候再加转换类型 |
| V2P-11 | 单体侧和目标侧强制用同一种数据库后端，写进裁判清单 | 后端的差别会被误判成行为差别 | 有 Docker 和没 Docker 的机器上建的裁判，不能混用 |
| V2P-12 | 设计稿里的提议：V2-16、V2-17、V2-20 按提议实施，V2-18 归 v3 M5，V2-19 列为扩展 | 这几项在本方案里都有落点 | — |

---

## 11. 待确认

实施中出现的新问题，先记在这里，确认后移进 §10。

| 编号 | 提议 | 理由 | 代价 |
|---|---|---|---|
| V2P-13 | 符号分两类（细化 V2P-05）：id 类（uuid、十六进制串、身份 id、其他值）要求处处相等；时间、令牌、哈希类只表示"这里是一个易变值"，不要求和别处相等 | N1.3 在 bookstore 上实测：同一秒里签发的两个 JWT 完全相同，两遍里一遍相等、一遍不等；`createdAt` 和 `updatedAt` 也常常相等。按相等去要求，单体自己都会不稳定 | 目标版本里两个时间戳"应该相等却不相等"这类差别查不出来 |
| V2P-14 | J3 的分类任务可以读单体代码（J1 不能） | 判断一条差异是事实错误、需要裁定还是用例写错，要看单体代码；J1 的隔离在它写完预期时已经起了作用 | J3 的分类可能受代码影响，但它只分类，不改预期 |

---

## 附录 A：v2 模块到 v3 任务的对照（`v2.yaml` 草稿）

| 任务 | 执行者 | 依赖 | 完成检查 | 首次出现 |
|---|---|---|---|---|
| `M0` | 调度器：版本戳、检查工具未改动 | — | 版本戳存在 | N0 |
| `F1` | 调度器：`analyze_project` | M0 | `analysis_report` | 现有 |
| `F2` | m2s-spec-annotator | F1 | `check_provenance`（即 F3） | 现有 |
| `G1` | 调度器：`gap_scan` | F2 | 缺口清单格式 | N4 |
| `G2` | m2s-spec-annotator（缺口模式） | G1 | 缺口清单格式、出处 | N4 |
| `J1` | m2s-verifier（J1 模式，禁止读单体代码） | F2 | `check_judge_assets` | N1 |
| `J2` | 调度器：`run_monolith` | J1 | 全部用例执行成功，第三遍核对稳定 | N1 |
| `J3` | 调度器：`reconcile_spec`；有差异时建分类任务（verifier J3 模式），再按 §4.5 流转 | J2 | 没有未处理的差异 | N1 |
| `J4` | m2s-verifier（J4 模式）；补出的用例回 J2 | J3 | `check_judge_assets` | N3 |
| `J5` | 调度器：`run_mutants`；verifier 判断等价变异 | J4 | 检出率已记录，未抓住的已处理 | N3 |
| `J6` | 调度器：`freeze_judge` | J5（N1 时为 J3） | 清单存在、hash 一致 | N1 |
| `A1` | m2s-architect：蓝图、适配映射、缺口决定 | G2，以及 J1（适配映射要覆盖 fixtures 的表） | `gate(blueprint)`（含 A2 新检查） | 现有，N2、N4 修改 |
| `D1` | m2s-verifier（D1 模式，禁止读实现代码） | A1、J2 | `run_validate(dry_run, delta)` | N4 |
| `I1` | m2s-code-developer（可用 `run_judge` 自测） | A1、D1、J6 | `gate(code)` | 现有，N5 修改 |
| `I2a`、`I2b`、`I2c` | m2s-sam-engineer | I1 | `gate(template)` 等 | 现有 |
| `I3` | m2s-validator | I2c | `gate(final)`（含裁判未改动） | 现有 |
| `V1` | 调度器：`check_adapter` | I3、J6 | 一致 | N2 |
| `V2` | 调度器：`run_judge` | V1 | P 层全部通过，R 层在裁定范围内 | N2 |
| `V3` | m2s-verifier（V3 模式）+ 工具 | V2 | `verdict_delta` 通过 | N4 |
| `B0` | 调度器：分诊结果为全部通过时 `tag_b0`，否则按 §4.13 流转 | V3 | tag 存在 | N4 |
| `O` | m2s-perf-optimizer | B0 | O2 的五项检查 | 现有，N5 修改 |
| `C1` | 调度器：最终检验和报告 | O | `gate(final)`、`run_judge` 全量、增量测试全量 | N5 |
| `C2` | 调度器：`archive_run` | C1 | 归档完整 | N5 |

---

## 附录 B：数据结构（初稿）

**裁判用例** `judge/cases/<接口>/<用例>.json`：
```json
{
  "id": "cart-add-then-read",
  "endpoint": "POST /api/cart",
  "layer": "P",
  "title": "加入购物车后读回",
  "fact_refs": ["POST /api/cart#responses[1]", "GET /api/cart#responses[0]"],
  "fixtures": ["books-basic"],
  "identity": "u1",
  "steps": [
    {"name": "add", "method": "POST", "path": "/api/cart",
     "body": {"bookId": "book-003", "quantity": 2, "price": 25.0}},
    {"name": "read", "method": "GET", "path": "/api/cart", "read_back": true}
  ],
  "predicted": {
    "add": {"status": 201},
    "read": {"status": 200, "body": {"contains": [{"bookId": "book-003", "quantity": 2}]}}
  }
}
```

**身份** `judge/identities.json`：
```json
{
  "u1": {
    "monolith": {"steps": [
      {"method": "POST", "path": "/api/auth/register",
       "body": {"email": "u1@example.com", "password": "Passw0rd!", "name": "User One"},
       "save": {"token": "body.accessToken", "id": "body.user.userId"}}],
     "header": {"Authorization": "Bearer ${token}"}}
  }
}
```

**单体输出** `judge/oracle_expected.json`（已屏蔽）：
```json
{"cart-add-then-read": {"steps": {
  "add":  {"status": 201, "headers": {"access-control-allow-origin": "*"}, "body": {"customerId": "<id:u1>", "bookId": "book-003"}},
  "read": {"status": 200, "body": [{"customerId": "<id:u1>", "bookId": "book-003", "quantity": 2, "price": 25}]}}}}
```

**屏蔽表** `judge/masks.json`：每个用例里被符号化的位置，以及符号的类别（uuid、token、time、hash、id:别名）。

**对账** `judge/reconcile.json`：每条差异 `{case, step, field, predicted, actual, class?, resolution?}`，`class` 是事实错误、需要裁定、用例写错、其实一致之一。

**裁定** `storage/rulings.json`：见 §4.6。

**适配映射** `storage/adapter_map.json`：
```json
{
  "tables": [
    {"monolith": "Bookstore-Cart", "target": "CartTable",
     "fields": {"customerId": {"from": "customerId", "as": "identity"},
                "bookId": {"from": "bookId"}, "quantity": {"from": "quantity"}, "price": {"from": "price"}}},
    {"monolith": "Bookstore-Users", "target": null, "replaced_by": "cognito"}
  ],
  "identities": {"claims": {"sub": "<id:${alias}>", "email": "${email}"}}
}
```

**判定** `verification/verdict_judge.json`：每个用例、每一步：`pass` / `fail` / `migration-change`；失败的写明函数、事实出处、期望、实际、按哪条规则比较（P，或 R 层的哪条裁定）。

**缺口清单** `storage/gap_inventory.json`：每条 `{id, category, evidence[], endpoints[], rulebook_rule?, default?, status, decision?}`。

---

## 附录 C：Q4 可行性验证记录（2026-11-01）

- 脚本：`verify-tools/monolith-run.js`、`verify-tools/monolith-preload.js`（commit 0d5c032）；
- 单体：`mono_benchmark/bookstore`（Express、aws-sdk v2），dynalite；
- 用例：注册 → 登录 → 读个人资料；按分类列书；加入购物车 → 读回；缺字段的 400；匿名请求的 401。每个用例跑两遍；
- 结果：
  - SDK 调用全部经过记录代理（users 表的 email-index 查询、PutItem、GetItem；books 表的 category-index 查询；cart 表的 PutItem、Query），写入能读回；
  - 状态码两遍一致；
  - 按用例的覆盖率互不相同、两遍完全一致；
  - 自动找出的易变字段：userId、accessToken、refreshToken、etag、createdAt、updatedAt、bcrypt 哈希。
