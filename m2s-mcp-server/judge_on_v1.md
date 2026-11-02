# 冻结的 bookstore 裁判在两个 v1 产物上的判定（v2 N2.5，2026-11-02）

> 方案：`PIPELINE_V2_PLAN.md` §5 N2.5。裁判：`bookstore-j1` 冻结的 v1（68 个用例、23 个故障场景，2026-11-01 冻结）。
> 被检验的：`bookstore-v3a` 的 v0（commit 74b91b9，v3 M1 的运行）和 `bookstore-v2` 的 v0（commit e098e0f）。两者的 v1 验收都是全部通过。

## 1. 结论

| | bookstore-v3a v0 | bookstore-v2 v0 |
|---|---|---|
| v1 自己的验收 | 通过 | 通过（87/87） |
| 适配映射（architect，MODE: adapter） | 第 1 次尝试通过检查，约 12 分钟 | 第 1 次尝试通过检查，约 7 分钟 |
| `check_adapter`（15 个只读用例、26 个读数据步骤） | 通过 | 通过 |
| `run_judge`：68 个用例 | 36 过、18 不过、14 不运行 | 0 过、54 不过、14 不运行 |
| `run_judge`：23 个故障场景下的用例 | 5 过、12 不过、6 不运行 | 0 过、17 不过、6 不运行 |
| 步骤（含故障场景） | 191 过、41 不过、48 不运行 | 0 过、232 不过、48 不运行 |
| 每次 `run_judge`（RIE、DynamoDB Local） | 134 秒 | 135 秒 |
| 实现缺陷（v1 的测试漏掉的） | **0** | **5 类**，71 个不过的单元全部涉及 |
| 适配映射的问题 | 0 | 0 |
| 裁判本身的问题 | 1 类（17 个单元） | 0（被实现缺陷盖住，见 §3.3） |
| 迁移带来的变化 | 2 类（13 个单元），v1 都有裁定 | 2 类（13 个单元），v1 没有裁定 |

每个不过的步骤都分到了四类之一，没有剩下的（分诊按"每一条差异"归类，一个步骤可以同时属于几类）。不运行的 14 个用例都是 `/api/auth/*` 四个端点的用例，适配映射把它们列为由 Cognito 取代。

**v1 的盲区**：bookstore-v2 的 v1 测试全部通过，裁判在它上面找出 5 类和单体不一致的实现（§3.1），其中 4 类在后来的 bookstore-v3a 里由 v1 的规格问题（SI-12、SI-14）或 gate（GM1–GM3）补上了；bookstore-v3a 上裁判没有找出 v1 的规格裁定之外的差别。

## 2. 怎么跑的

- 工作区：`runs/bookstore-v3a-n2`、`runs/bookstore-v2-n2`，各自从源工作区的 `v0` tag 取出 `output/`、`storage/blueprint.json`、`storage/DECISIONS.md`，`judge/` 和事实库（`storage/analysis_report.json`）取自 `bookstore-j1`（来源记在 `storage/N2_SOURCE.json`）。bookstore-v3a 的事实库和 j1 的完全相同；bookstore-v2 的事实库是更早的一次运行，端点和表一致，但没有 `global_middleware`，所以这里统一用裁判的事实库；
- 模板 `coord/templates/v2-adapter.yaml`：AM（architect 的 adapter 模式，`output/lambdas/**` 对它关闭）→ V1 `check_adapter` → V2 `run_judge`，真实 worker（qwen3.8-flash），`--max-workers 1`，两个工作区串行；
- V2 不通过之后，异常处理者两次都判断"不是看板上的任务能修的"，交给人（§5）。这正是 v1 产物上应有的结果；
- bookstore-v2 的第一次 V1 没通过：`check_adapter` 把读数据步骤上缺 `Access-Control-Allow-Credentials` 头也算成了映射错误。修正（commit 8e53dda）：`check_adapter` 只看读数据步骤的状态码和响应体，头的差别交给 `run_judge`。之后 `resolve --reopen` 重跑，V1 通过。

## 3. 分诊

### 3.1 实现缺陷（v1 的测试漏掉的）——只在 bookstore-v2

| # | 差异 | 涉及 | 单体 | bookstore-v2 | bookstore-v3a 里怎么处理的 |
|---|---|---|---|---|---|
| 1 | 每个响应都缺 `Access-Control-Allow-Credentials: true` | 210 步、70 个单元；其中 40 个单元只差这一点 | 全局中间件 `cors({origin: '*', credentials: true})`，每个响应都带 | blueprint 的 `api_gateway.cors` 没有 credentials，函数也不加 | GM1–GM3（2026-10-05 加的 gate，commit 86639b6，晚于 bookstore-v2 的 v0），SI-20 |
| 2 | 网关自己回的 401 没有 CORS 头（ACAO、ACAC 都没有） | 22 步、11 个单元 | 401 也经过 cors 中间件 | 模板没有 GatewayResponses | GM2：`Globals.Api.GatewayResponses` |
| 3 | 错误响应体没有 `stack` | 17 个单元（5 个用例 + 12 个故障场景） | `config.nodeEnv` 默认 development，500 和解析失败的 400 都是 `{error, stack}` | 只有 `{error}`，没有相应的裁定 | SI-12：保留 stack，只要求存在、不比内容 |
| 4 | 声明为 JSON 但解析不了的请求体回 500 | 4 个用例 | 400（body-parser 的 `err.status`） | 500 | SI-12(2)：解析失败单独处理，回 400 |
| 5 | form 编码的请求体回 500 | 2 个用例（加入购物车后读回、下单的 form 臂） | urlencoded 解析器接受，加入购物车 200 并按字符串存 | 不看 Content-Type，一律按 JSON 解析，500 `not valid JSON` | SI-14 接受 form 编码的简单字段；按字符串存这一点是 v1 的 V2 阶段才发现、改了用例 |

第 5 条就是方案里列的"已知例子"之一：v1 在 bookstore-v3a 上是 V2 阶段才发现、改了用例；裁判在单体的实际输出上建预期（加入购物车后读回的是字符串 `"2"`、`"10.5"`），直接查出 bookstore-v2 没有处理这一臂，而 bookstore-v3a 的同一个用例通过。

### 3.2 迁移带来的变化

| 差异 | 涉及（每个产物） | bookstore-v3a | bookstore-v2 |
|---|---|---|---|
| 匿名或伪造令牌的请求：单体 401 `{error: "Authentication required..."}` / `{error: "Invalid or expired token"}`，目标是 Cognito 授权器的 401 `{message: "Unauthorized"}` | 22 步、11 个用例 | SI-1（migration-change）。网关的 4XX 还多带 `Access-Control-Allow-Methods/-Headers` 两个头（单体只在预检上带），这一点 SI-1 和 GM2 都没有单独写，是模板的选择 | 蓝图的 cognito 设计隐含，没有成文的裁定 |
| 重复的同名查询参数（`?category=a&category=b`、`?q=a&q=b`）：单体 Express 解析成数组，500；API Gateway 只给一个字符串，200 | 2 个用例 | SI-13（migration-change） | 没有裁定 |
| `/api/auth/*` 四个端点由 Cognito 取代，不运行 | 14 个用例、6 个故障场景下的用例 | SI-1–SI-4，`dropped_functions` | `dropped_functions` |

v2 的流水线里，这几项应当是 A1 记下的 `migration-change` 裁定（`storage/rulings.json`，`affects.cases` 写这些用例），`run_judge` 就会把它们判为 `migration-change` 而不是失败；v1 的裁定写在 `blueprint.metadata.spec_resolutions` 里，`run_judge` 不读（V2-07）。

### 3.3 裁判本身的问题——stack 的内容

bookstore-v3a 的 17 个单元（5 个用例 + 12 个故障场景）只差 `body.stack` 的内容：状态码、`error` 文案、其他头都一致。单体的 stack 是它自己的调用栈（`D:\...\node_modules\aws-sdk\lib\...`、`routes/search.js:35` 这类路径和行号），三遍之间不变，所以没有被屏蔽；目标的 stack 是 Lambda 里的调用栈（`/var/task/...`），不可能逐字相同。v1 在 SI-12(3) 里已经裁定"只断言存在和非空、不断言内容"。

这一类在 bookstore-v2 上没有出现，因为 v2 根本不返回 stack（§3.1 第 3 条）。

需要改的是裁判（见方案 §11 V2P-18），改了要升版本、由人重新冻结。

**已处理（2026-11-03）**：V2P-18 确认之后，单体的调用栈改成屏蔽符号，bookstore-j2 重建为裁判 v2（对账 0 条差异，变异检出率不变：93 个抓住 86 个，7 个等价，串行的反向检查没被抓住）。v2 在 bookstore-v3a 的 v0 上：故障场景 18 个通过、6 个不适用，这 12 个只差 stack 的故障场景不再失败；用例 44 个通过、15 个不适用、15 个失败。失败的 15 个里，13 个是网关的 401 和单体的 401 响应体和头不同（SI-13），2 个是重复查询参数（单体 500，目标 200，SI-1）。这些都要在迁移的裁定里说明，不是裁判的问题。

### 3.4 适配映射的问题

没有。两份映射都由 architect 第一次尝试写成，`check_adapter_map` 和 `check_adapter` 都通过。值得一提的写法：bookstore-v3a 的畅销榜表在目标里多一个幂等用的 `appliedOrders`（SI-11），architect 用 `copy_rest: false` 只映射 `bookId` 和 `salesCount`，表内容的反向比较因此不会把这个迁移新增的属性当成差异。

## 4. 方案里要对照的已知例子

| 例子 | 结果 |
|---|---|
| form 编码的请求体（加入购物车后存字符串 `"2"`、`"10.5"`） | 裁判的用例 `cart-add-form-encoded-strings` 用的是单体的实际输出。bookstore-v3a 通过；bookstore-v2 不通过（§3.1 第 5 条） |
| JSON 标量请求体（`null`、`5`、`"x"`，v1 留作未决问题） | **冻结的裁判里没有这样的用例**：J1 写了畸形 JSON（解析失败）的步骤，没有写"合法 JSON 但不是对象"。裁判在这一点上没有覆盖，两个产物都没有被检验。应当在 J4 白盒补测（N3）里补上：单体的 body-parser strict 模式对标量直接 400 |
| Cognito 设置（refresh 令牌 7 天，模板第一次漏写） | 裁判只比对接口行为，这类部署设置看不到，仍然靠 gate（C14） |

## 5. 异常处理者的两次交人

两次 V2 不通过，处理者读了 `verdict_judge.json`、模板和蓝图，判断都是"看板上没有能修的任务"，交给人，没有让 architect 改映射去掩盖差异。它列的问题和本报告的分诊一致：bookstore-v3a 是 401 的形状、stack、重复查询参数三类；bookstore-v2 加上全局响应头、401 的 CORS 头、错误体缺 stack、畸形 JSON、form 编码。两个工作区都停在 `needs_human`（V2），这是 N2.5 的预期终点，不需要继续处理。

## 6. 对裁判和工具的改进

- 已改：`check_adapter` 只看读数据步骤的状态码和响应体（commit 8e53dda）；
- 提议（方案 §11）：V2P-16 `content-type` 只比媒体类型；V2P-17 `check_adapter` 的判定范围；V2P-18 单体的调用栈按实现轨迹处理；
- 后续：J4（N3）补"合法 JSON 但不是对象"的请求体；适配映射可以考虑加一个"目标独有属性"的写法，代替这次 architect 用 `copy_rest: false` 的做法（不急，现有写法已经能表达）。

## 7. 产物位置

- `runs/bookstore-v3a-n2/`、`runs/bookstore-v2-n2/`：`storage/adapter_map.json`、`verification/check_adapter.json`、`verification/verdict_judge.json`、`coord/`（事件、报告、处理者的案例）；
- 分诊规则：本报告 §3 的表格（按"差异的路径和原因"逐条归类，每条规则写明依据）。
