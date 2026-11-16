# 异常处理记录

本次运行中异常处理者和人做过的判断，按时间排列。

## 人的决定（任务 HUMAN_RECONCILE，2026-11-13 01:02）

- 决定：接受
- 说明：J3 第 1 轮的 5 条 fact_error 已按端点退回 F2-identity（2 条）与 F2-bestsellers-books-cart-orders-search（3 条），由人工补派（修好 _fact_targets 之前的误路由）；J1/J2/RC/J3 会随 F2 重跑，J4 继续等 J3。规格讨论室 spec-j3-r1 的 2 条 needs_ruling 不变。

## 人的决定（任务 HUMAN_J6，2026-11-13 08:36）

- 决定：接受；新的裁定 R-human-1、R-human-2
- 说明：接受 (a)：未鉴权超大请求体由网关先答 401（migration-change，点名两个 oversize-no-auth 步骤；带凭证的 413 契约保留）；接受 (b)：把 R-A1-a3-1 的记账补到两个 spoof 步骤。裁判保持冻结，不需要改资产或解冻。

## C001-J6-review（review，任务 J6）

- 决定：escalate
- 理由：阶段复查：J6 本身通过（judge v1 冻结，86 用例/284 步，differences=0、2 条按裁定结案，变异 score 1.0、gap 0，覆盖率 584/611），但冻结的裁判里有 4 个步骤在目标上必然由 API Gateway 的 Cognito 授权器应答、却没有在效裁定记账到这些步骤，V2 会把它们判成 fail，白耗一轮实现线。其中一类是状态码级的偏差，按本流水线自己的口径需要人确认，我不自行裁定。

证据（工作区只读核对）：
1) 机制：verify-tools/judge-run.js runUnit —— `route.authorized && !as` 时直接 gatewayAnswer('unauthorized')，不进 Lambda；judge.py `_rulings_in_force`/`judge_unit` 按「用例 + 步骤名」匹配裁定，未记到的步骤若比较出不匹配就是 verdict=fail（不是 migration-change/ruled）。
2) 已被裁定但记账漏了的两步：judge/cases/get-api-cart/wb-cart-x-customer-id-header-ignored.json 的 spoof-read-no-token（GET /api/cart）、spoof-write-no-token（POST /api/cart），无 Authorization、只带 x-customer-id，oracle 记 401 {"error":"Authentication required. Please provide a valid Bearer token."}；管辖它的是 R-A1-a3-1（migration-change，02:18 记录），其 affected_cases 只有 12 个 *-unauthenticated-401 用例的 27 个步骤。这两个用例是 J4 第 2 轮（05:06，为消 J5C 的 gap 变异 negate:middleware/auth.js:19）新加的，写账时不存在，所以覆盖不到（同用例的 spoof-read-u1-token 带有效 token，不受影响；wb-x-customer-id-header-ignored 挂在 GET /api/auth/me，属 R-A1-a3-3 的 dropped 端点，按 dropped 处理，无风险）。
3) 需要人判断的两步：cart-add-oversize-body-413#oversize-no-auth（POST /api/cart）与 order-oversize-body-413#oversize-no-auth（POST /api/orders）——无凭证且请求体 >100KB，单体由 body-parser 在 authMiddleware 之前拒掉，oracle 记的是 413 {"error":"request entity too large"}；目标的授权器先于函数，GAP-12/D-12 决定「在共享 helper 里复刻 body-parser 语义与 413」位于 Lambda 内，走不到，故目标答 401。这不是响应体文案差异，而是可观察状态码 413→401 的改变。R-A1-a3-1 的结论只覆盖「无凭证/坏凭证/refresh 当 access」三类单体本就 401 的请求，任何在效裁定都没有把「网关先于请求体大小检查」登记到这两步。
4) 为什么交人而不是退回 A1：storage/DECISIONS.md:483 里 A1 自己写明「放弃 413」属于需要人确认的那一类，而 GAP-12/D-12 的决定是复刻 413；现在正是这一类偏差在鉴权前置后自动发生，是否接受它不是事实能推出的结论。

要人判断的问题：
(a) 是否接受「未鉴权 + 超大请求体」由网关先答 401、不再复现单体的 413（即作为 migration-change 记入裁定账，点名 cart-add-oversize-body-413#oversize-no-auth、order-oversize-body-413#oversize-no-auth 两步；单体一侧的 413 契约仍由同用例带凭证的 oversize-json/oversize-urlencoded 步骤保住）。
(b) 同一口径下把 R-A1-a3-1 的 affected_cases 补到 wb-cart-x-customer-id-header-ignored#spoof-read-no-token、#spoof-write-no-token ——这是已裁定规则的必然结果，不涉及行为改变，补记可由 A1 以 record_ruling 完成，无需解冻裁判、judge/ 不必改动。
(c) 若人不接受 (a)：413 这一臂只能以 V2 失败暴露，需要在进实现线之前决定是解冻改资产、还是把这两步标为已知不可通过项——这一步我判断不了。
- 给 worker 的反馈：人确认 (a)(b) 后请派 A1（architect）只做裁定账的补记：把两条 migration-change 结论按「用例#步骤」点名到上述 4 个步骤；裁判资产与 blueprint 都不必改动（judge/ 已冻结，不需解冻）。
- 调度器的处理：交给人确认后再继续

## 人的决定（任务 HUMAN_J6，2026-11-13 08:37）

- 决定：接受
- 说明：再次确认（崩溃在 case_decision 落盘前导致恢复时重新发问）：接受 (a) 未鉴权超大请求体由网关先答 401（R-human-1）与 (b) 两个 spoof 步骤补记（R-human-2）；裁定已在 storage/rulings.json，裁判保持冻结。

## C002-B0-review（review，任务 B0）

- 决定：ok
- 理由：进入优化线前的阶段复查未发现问题，B0 基线可用：

1) 基线一致性：工作区 git 树干净，tag B0 = HEAD = a9dea7f（"B0: judge and delta tests pass"），即被打 tag 的代码就是 V2 判定通过的代码，优化线的对照基线成立；`verification/fault_baseline_B0.json` 已就位。

2) 验证线全绿且判据齐全：V1 check_adapter PASS（29 个数据步骤 / 20 用例）；V2 run_judge status=PASS，86 用例 = 54 pass + 14 migration-change + 18 dropped，fail/error 0，fault runs 22 pass + 6 dropped；V3 verdict_delta PASS 12/12；I3 gate(final) PASS 且 consistency_report PASS；storage/stage_reports/ 下 36 份全部 success，无未决 suspects。

3) dropped 的账目站得住：18 个 dropped 用例逐条查过，全部属于被 Cognito 取代的四个认证端点（register / login / refresh / me 及其 wb-* 变体），依据是在效裁定 R-A1-a3-3；没有在效端点的用例被记为 dropped，覆盖没有被静默削弱。

4) 上一次升级（C002 之前的 C001-J6-review）已被人的决定完全闭环：R-human-1（未鉴权 + 超大请求体由网关先答 401）与 R-human-2（R-A1-a3-1 补记两个 spoof 步骤）都在 storage/rulings.json；V2 结果里 cart-add-oversize-body-413、order-oversize-body-413、wb-cart-x-customer-id-header-ignored 均已按 migration-change 结案而非 fail，说明补记生效；judge/ 保持冻结未被改动（各任务 judge_unchanged 检查均通过）。

5) 蓝图结构完整：storage/blueprint.json 含 metadata/dynamodb_tables/cognito(enabled=true，口令下限取自单体唯一被证明的校验 auth.js:40)/api_gateway/12 个 lambda_functions/dropped_functions，A1 的 gate_blueprint✓ 记录在案。

因此本阶段无需 retry、reroute 或交人，可放行 C1/C2/O。提醒（不构成处理动作）：优化线的改动受 R-A1-a3-2 与各列表顺序/新鲜度裁定的约束，热键基准与 guard_benchmarks 的规则 R4 由 run_rie_verdict 自行把关。
- 给 worker 的反馈：（无）
- 调度器的处理：继续

## 人的决定（任务 C1，2026-11-13 13:46）

- 决定：重做；新的裁定 R-human-3
- 说明：接受 REQ-1（R4）的 1 行防御分支未覆盖：记为人工接受的覆盖缺口（R-human-3），O 报告与 perf_report 的 blocked/pending_human 已清除并记 human_acceptance；重开 C1 跑 gate(final)+全量裁判+增量测试，通过后进 C2 归档。

