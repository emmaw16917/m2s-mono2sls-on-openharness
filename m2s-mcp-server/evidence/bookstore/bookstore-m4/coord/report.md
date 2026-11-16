# 运行报告：bookstore-m4

- 结果：**archiving**
- 单体：D:\Emma\MyLab\Mono2SLs\Mono2Sls-artifacts\mono_benchmark\bookstore
- 模板：v2-split

- harness（2026-11-12T23:24 起）：m2s 8900e0d2c6（未提交改动 0 处），OpenHarness 07404fad4b，模型 qwen3.8-flash
- harness（2026-11-13T01:02 起）：m2s 63ab894a72（未提交改动 0 处），OpenHarness 07404fad4b，模型 qwen3.8-flash
- harness（2026-11-13T08:37 起）：m2s f286ec32fa（未提交改动 0 处），OpenHarness 07404fad4b，模型 qwen3.8-flash

| 任务 | 状态 | 启动次数 | 崩溃 | API 中断 | 用时（分钟） | 最后一次检查 |
|---|---|---|---|---|---|---|
| F1 | closed | 0 | 0 | 0 | 0.0 | analyze_project✓；analysis_report✓ |
| G1 | closed | 0 | 0 | 0 | 0.0 | gap_scan✓；gap_inventory✓ |
| G2 | closed | 1 | 0 | 0 | 11.3 | gap_inventory✓ |
| J1 | closed | 3 | 0 | 0 | 54.1 | write_scope✓；tools_unchanged✓；judge_assets✓ |
| J2 | closed | 0 | 0 | 0 | 0.0 | run_monolith✓ |
| RC | closed | 0 | 0 | 0 | 0.0 | reconcile_spec✓ |
| J3 | closed | 2 | 0 | 0 | 11.9 | reconcile_classified✓ |
| J4 | closed | 2 | 0 | 0 | 22.4 | judge_assets✓；j4_report✓ |
| J5 | closed | 0 | 0 | 0 | 0.0 | run_mutants✓ |
| J5C | closed | 1 | 0 | 0 | 2.1 | mutant_classes✓ |
| J6 | closed | 0 | 0 | 0 | 0.0 | freeze_judge✓ |
| A1 | closed | 3 | 0 | 0 | 39.0 | blueprint_keys✓；gate_blueprint✓；judge_unchanged✓ |
| D1 | closed | 1 | 0 | 0 | 20.1 | delta_assets✓；judge_unchanged✓ |
| I2a | closed | 2 | 0 | 0 | 52.5 | write_scope✓；tools_unchanged✓；judge_unchanged✓；template_part1✓ |
| I2b | closed | 1 | 0 | 0 | 16.6 | write_scope✓；tools_unchanged✓；judge_unchanged✓；template_part2✓ |
| I2c | closed | 1 | 0 | 0 | 5.5 | write_scope✓；tools_unchanged✓；judge_unchanged✓；gate_template✓ |
| I3 | closed | 1 | 0 | 0 | 10.2 | write_scope✓；tools_unchanged✓；judge_unchanged✓；gate_final✓；consistency_pass✓ |
| V1 | closed | 0 | 0 | 0 | 0.0 | check_adapter✓ |
| V2 | closed | 0 | 0 | 0 | 0.0 | run_judge✓ |
| V3 | closed | 1 | 0 | 0 | 4.9 | write_scope✓；tools_unchanged✓；judge_unchanged✓；verdict_delta✓；judge_unchanged✓ |
| B0 | closed | 0 | 0 | 0 | 0.0 | tag_b0✓ |
| O | closed | 1 | 0 | 0 | 36.2 | write_scope✓；tools_unchanged✓；judge_unchanged✓；s5_report✓；judge_unchanged✓ |
| C1 | closed | 0 | 0 | 0 | 0.0 | final✓ |
| C2 | in_progress | 0 | 0 | 0 | 0.0 |  |
| HUMAN_J6 | closed | 0 | 0 | 0 | 0.0 |  |
| ROOM-spec-j3-r2-3-2 | closed | 1 | 0 | 0 | 14.6 | write_scope✓；tools_unchanged✓；room_turn✓ |
| ROOM-spec-j3-r2-2-2 | closed | 1 | 0 | 0 | 5.7 | write_scope✓；tools_unchanged✓；room_turn✓ |
| ROOM-spec-j3-r2-1-2 | closed | 1 | 0 | 0 | 17.4 | write_scope✓；tools_unchanged✓；room_turn✓；rulings✓ |
| ROOM-spec-j3-r2-0-2 | closed | 1 | 0 | 0 | 4.7 | write_scope✓；tools_unchanged✓；room_turn✓ |
| ROOM-spec-j3-r2-3-1 | closed | 1 | 0 | 0 | 9.0 | write_scope✓；tools_unchanged✓；room_turn✓ |
| ROOM-spec-j3-r2-2-1 | closed | 1 | 0 | 0 | 5.7 | write_scope✓；tools_unchanged✓；room_turn✓ |
| ROOM-spec-j3-r2-1-1 | closed | 1 | 0 | 0 | 6.7 | write_scope✓；tools_unchanged✓；room_turn✓；rulings✓ |
| I1-shared | closed | 1 | 0 | 0 | 51.3 | write_scope✓；tools_unchanged✓；judge_unchanged✓；gate_code_pkg✓；judge_unchanged✓ |
| I1-bestsellers-books-cart-orders-search | closed | 2 | 1 | 0 | 166.4 | write_scope✓；tools_unchanged✓；judge_unchanged✓；gate_code_pkg✓；judge_unchanged✓ |
| I1-identity | closed | 1 | 0 | 0 | 13.0 | write_scope✓；tools_unchanged✓；judge_unchanged✓；gate_code_pkg✓；judge_unchanged✓ |
| ROOM-spec-j3-r1-3-2 | closed | 1 | 0 | 0 | 12.8 | write_scope✓；tools_unchanged✓；room_turn✓ |
| ROOM-spec-j3-r1-2-2 | closed | 1 | 0 | 0 | 7.3 | write_scope✓；tools_unchanged✓；room_turn✓ |
| ROOM-spec-j3-r1-1-2 | closed | 1 | 0 | 0 | 12.2 | write_scope✓；tools_unchanged✓；room_turn✓；rulings✓ |
| ROOM-spec-j3-r1-0-2 | closed | 1 | 0 | 0 | 4.8 | write_scope✓；tools_unchanged✓；room_turn✓ |
| ROOM-spec-j3-r1-3-1 | closed | 1 | 0 | 0 | 6.8 | write_scope✓；tools_unchanged✓；room_turn✓ |
| ROOM-spec-j3-r2-0-1 | closed | 1 | 0 | 0 | 5.7 | write_scope✓；tools_unchanged✓；room_turn✓ |
| RULE_J3_2 | closed | 0 | 0 | 0 | 0.0 | rulings_cover✓ |
| ROOM-spec-j3-r1-2-1 | closed | 1 | 0 | 0 | 5.9 | write_scope✓；tools_unchanged✓；room_turn✓ |
| ROOM-spec-j3-r1-1-1 | closed | 1 | 0 | 0 | 8.6 | write_scope✓；tools_unchanged✓；room_turn✓；rulings✓ |
| ROOM-spec-j3-r1-0-1 | closed | 1 | 0 | 0 | 19.9 | write_scope✓；tools_unchanged✓；room_turn✓ |
| HUMAN_RECONCILE | closed | 0 | 0 | 0 | 0.0 |  |
| RULE_J3_1 | closed | 0 | 0 | 0 | 0.0 | rulings_cover✓ |
| F2-shared | closed | 1 | 0 | 0 | 7.4 | check_provenance✓ |
| F2-bestsellers-books-cart-orders-search | closed | 3 | 0 | 0 | 47.6 | write_scope✓；tools_unchanged✓；check_provenance_pkg✓ |
| F2-identity | closed | 2 | 0 | 0 | 25.0 | write_scope✓；tools_unchanged✓；check_provenance_pkg✓ |

## 异常处理

- C001-J6-review：escalate → 交给人确认后再继续
- C002-B0-review：ok → 继续

## 交给人

- HUMAN_RECONCILE：规格对账的这些差异在模板里没有可以退回的任务：[
 {
  "id": "auth-endpoints-bad-json-400/reg-form-encoding/status",
  "case": "auth-endpoints-bad-json-400",
  "step": "reg-form-encoding",
  "request": {
   "method": "POST",
   "path": "/api/auth/register"
  },
  "check": "status",
  "predicted": 400,
  "actual": 201,
  "fact_refs": [
   "POST /api/auth/register#responses[6]",
   "POST /api/auth/login#responses[5]",
   "POST /api/auth/refresh#responses[5]",
   "POST /api/auth/register#request",
   "POST /api/auth/login#request"
  ],
  "reason": "单体确实会解析表单编码的请求体：bookstore/server.js:33-34 同时挂了 app.use(bodyParser.json()) 和 app.use(bodyParser.urlencoded({ extended: true }))，所以 Content-Type: application/x-www-form-urlencoded 的请求体也会被填进 req.body：routes/auth.js:22 取出 email/password 得到字符串 'form@example.com' / 'Passw0rd!'，第 25 行的真值守卫、第 33 行的邮箱格式与第 40 行的长度检查都通过，最终按 responses[4] 返回 201。事实库写的是相反的话：POST /api/auth/register#request.headers[content-type] 写 \"body-parser 只在 content-type 匹配 json 时填充 req.body，否则 req.body 保持 {}\"，POST /api/auth/register#responses[6] 写 \"Content-Type 不是 JSON 类型时请求体不被解析，req.body 为 {}，落到上面那条 400\"。同一个错在 POST /api/auth/login#request.headers[content-type]/#responses[5] 和 POST /api/auth/refresh#request.headers[content-type]/#responses[5] 也各有一份。事实库的 cors 全局中间件条目里已经提到 bodyParser.urlencoded 存在（\"app.use(bodyParser.json()) 与 app.use(bodyParser.urlencoded(...)) 不加响应头……故不在本项记录\"），但请求解析这件事没有端点级事实覆盖，而且现有说法是错的。annotator 要改这几项为：content-type 为 application/json 或 application/x-www-form-urlencoded 时 body-parser 都会填充 req.body（urlencoded 解出的值一律是字符串，extended:true 只影响 qs 嵌套语法），其他 content-type（如 text/plain）才保持 req.body 为 {}；并宜补一条全局请求解析条目记录 server.js:33-34 这两个中间件与各自的 100kb 默认上限。改完后 J1 把 reg-form-encoding 的预期换成 201（本差异不是易变值屏蔽不全，也不是用例请求写错——用例发的是合法表单，单体照事实库之外的方式解析了它）。",
  "fact_ref": "POST /api/auth/register#responses[6]"
 },
 {
  "id": "auth-endpoints-bad-json-400/reg-form-encoding/body.subset",
  "case": "auth-endpoints-bad-json-400",
  "step": "reg-form-encoding",
  "request": {
   "method": "POST",
   "path": "/api/auth/register"
  },
  "check": "body.subset",
  "predicted": {
   "error": "Email and password are required"
  },
  "actual": {
   "message": "User registered successfully",
   "user": {
    "userId": "<uuid#3>",
    "email": "form@example.com",
    "name": "form"
   },
   "accessToken": "<token#4>",
   "refreshToken": "<token#5>"
  },
  "fact_refs": [
   "POST /api/auth/register#responses[6]",
   "POST /api/auth/login#responses[5]",
   "POST /api/auth/refresh#responses[5]",
   "POST /api/auth/register#request",
   "POST /api/auth/login#request"
  ],
  "reason": "单体的实际响应 {message:'User registered successfully', user:{userId:<uuid>, email:'form@example.com', name:'form'}, accessToken:<token>, refreshToken:<token>} 与事实库 POST /api/auth/register#responses[4] 的描述逐项吻合（user.name='form' 正是 business_rules[6]：name 缺省时取 email 的 @ 前缀；表单里没有 name），也与 business_rules[9] 的双 token 描述一致。预期写成 400 'Email and password are required' 只因为上面那条 content-type 事实错了（见 reg-form-encoding/status：server.js:34 的 bodyParser.urlencoded 会把表单字段填进 req.body，routes/auth.js:25 的守卫不会触发）。修的都是同一条事实库错误，fact_ref 指向应当被采信的那一项。",
  "fact_ref": "POST /api/auth/register#responses[4]"
 },
 {
  "id": "cart-add-non-json-content-type-400/form/status",
  "case": "cart-add-non-json-content-type-400",
  "step": "form",
  "request": {
   "method": "POST",
   "path": "/api/cart"
  },
  "check": "status",
  "predicted": 400,
  "actual": 200,
  "fact_refs": [
   "POST /api/cart#responses[0]",
   "POST /api/cart#responses[2]",
   "POST /api/cart#request"
  ],
  "reason": "同一个 bodyParser.urlencoded 事实错误（bookstore/server.js:34），落到购物车上：表单请求体被解析成 {bookId:'book-001', quantity:'2', price:'10'}（值都是字符串），routes/cart.js:72 的 `if (!bookId || !quantity || !price)` 真值判断全部通过，PutItem 成功，返回 200。事实库对 POST /api/cart#request 完全没写请求体是怎么解析的（headers 只记了 authorization 和 x-customer-id），属于漏记；而它另外几个端点写的\"非 JSON content-type 不解析\"是错的，J1 按那个说法推的 400。事实库里能对上实际行为的只有 POST /api/cart#business_rules[1]（quantity、price 原样存进条目，可以是数字也可以是字符串）——这条恰好印证表单路径存在。annotator 应给 POST /api/cart#request 补上 content-type/body 解析事实（json 与 x-www-form-urlencoded 都解析，urlencoded 的值是字符串；其他 content-type 得到 req.body={} 走 responses[0] 的缺字段 400），并与 register/login/refresh 的同类修正保持一致。用例里 text/plain 那一步仍按原预期 400（没有差异，说明未解析这条路是对的）。",
  "fact_ref": "POST /api/cart#request"
 },
 {
  "id": "cart-add-non-json-content-type-400/form/body.subset",
  "case": "cart-add-non-json-content-type-400",
  "step": "form",
  "request": {
   "method": "POST",
   "path": "/api/cart"
  },
  "check": "body.subset",
  "predicted": {
   "error": "Missing required fields: bookId, quantity, price"
  },
  "actual": {
   "message": "Item added to cart successfully"
  },
  "fact_refs": [
   "POST /api/cart#responses[0]",
   "POST /api/cart#responses[2]",
   "POST /api/cart#request"
  ],
  "reason": "实际响应体 {message:'Item added to cart successfully'} 与 POST /api/cart#responses[1] 的描述逐字一致（不回显条目内容）；预期写的是 responses[0] 的错误体，源于同一处 body 解析事实缺失/错误（server.js:34 的 bodyParser.urlencoded 会填充 req.body，因此不会缺字段）。属于事实库要改、改完 J1 更新预期，不是用例请求写错，也不是屏蔽不全。",
  "fact_ref": "POST /api/cart#responses[1]"
 },
 {
  "id": "cart-add-non-json-content-type-400/read/body.equals",
  "case": "cart-add-non-json-content-type-400",
  "step": "read",
  "request": {
   "method": "GET",
   "path": "/api/cart"
  },
  "check": "body.equals",
  "predicted": [],
  "actual": [
   {
    "customerId": "<id:u1>",
    "quantity": "2",
    "price": "10",
    "bookId": "book-001"
   }
  ],
  "fact_refs": [
   "POST /api/cart#responses[0]",
   "POST /api/cart#responses[2]",
   "POST /api/cart#request"
  ],
  "reason": "读回这一步本身没错，错在上游：form 那一步实际成功写入了条目，所以 GET /api/cart 返回一条而不是空数组。实际内容 [{customerId:<id:u1>, quantity:'2', price:'10', bookId:'book-001'}] 与事实库完全一致——GET /api/cart#responses[0]（元素即 Bookstore-Cart 的条目、按 bookId 升序、只有第一页）、business_rules[2]（不 join 不补字段），quantity/price 是字符串也正落在 POST /api/cart#business_rules[1] 说的\"原样存进条目，可以是数字也可以是字符串\"里。所以要改的还是同一条请求体解析事实（server.js:34）：事实库修正后 J1 把这一步的预期改成含这一条字符串值条目的数组，无需改请求。",
  "fact_ref": "GET /api/cart#respo
…（截断，原文 6013 字符）
- HUMAN_J6：阶段复查要求人确认：阶段复查：J6 本身通过（judge v1 冻结，86 用例/284 步，differences=0、2 条按裁定结案，变异 score 1.0、gap 0，覆盖率 584/611），但冻结的裁判里有 4 个步骤在目标上必然由 API Gateway 的 Cognito 授权器应答、却没有在效裁定记账到这些步骤，V2 会把它们判成 fail，白耗一轮实现线。其中一类是状态码级的偏差，按本流水线自己的口径需要人确认，我不自行裁定。

证据（工作区只读核对）：
1) 机制：verify-tools/judge-run.js runUnit —— `route.authorized && !as` 时直接 gatewayAnswer('unauthorized')，不进 Lambda；judge.py `_rulings_in_force`/`judge_unit` 按「用例 + 步骤名」匹配裁定，未记到的步骤若比较出不匹配就是 verdict=fail（不是 migration-change/ruled）。
2) 已被裁定但记账漏了的两步：judge/cases/get-api-cart/wb-cart-x-customer-id-header-ignored.json 的 spoof-read-no-token（GET /api/cart）、spoof-write-no-token（POST /api/cart），无 Authorization、只带 x-customer-id，oracle 记 401 {"error":"Authentication required. Please provide a valid Bearer token."}；管辖它的是 R-A1-a3-1（migration-change，02:18 记录），其 affected_cases 只有 12 个 *-unauthenticated-401 用例的 27 个步骤。这两个用例是 J4 第 2 轮（05:06，为消 J5C 的 gap 变异 negate:middleware/auth.js:19）新加的，写账时不存在，所以覆盖不到（同用例的 spoof-read-u1-token 带有效 token，不受影响；wb-x-customer-id-header-ignored 挂在 GET /api/auth/me，属 R-A1-a3-3 的 dropped 端点，按 dropped 处理，无风险）。
3) 需要人判断的两步：cart-add-oversize-body-413#oversize-no-auth（POST /api/cart）与 order-oversize-body-413#oversize-no-auth（POST /api/orders）——无凭证且请求体 >100KB，单体由 body-parser 在 authMiddleware 之前拒掉，oracle 记的是 413 {"error":"request entity too large"}；目标的授权器先于函数，GAP-12/D-12 决定「在共享 helper 里复刻 body-parser 语义与 413」位于 Lambda 内，走不到，故目标答 401。这不是响应体文案差异，而是可观察状态码 413→401 的改变。R-A1-a3-1 的结论只覆盖「无凭证/坏凭证/refresh 当 access」三类单体本就 401 的请求，任何在效裁定都没有把「网关先于请求体大小检查」登记到这两步。
4) 为什么交人而不是退回 A1：storage/DECISIONS.md:483 里 A1 自己写明「放弃 413」属于需要人确认的那一类，而 GAP-12/D-12 的决定是复刻 413；现在正是这一类偏差在鉴权前置后自动发生，是否接受它不是事实能推出的结论。

要人判断的问题：
(a) 是否接受「未鉴权 + 超大请求体」由网关先答 401、不再复现单体的 413（即作为 migration-change 记入裁定账，点名 cart-add-oversize-body-413#oversize-no-auth、order-oversize-body-413#oversize-no-auth 两步；单体一侧的 413 契约仍由同用例带凭证的 oversize-json/oversize-urlencoded 步骤保住）。
(b) 同一口径下把 R-A1-a3-1 的 affected_cases 补到 wb-cart-x-customer-id-header-ignored#spoof-read-no-token、#spoof-write-no-token ——这是已裁定规则的必然结果，不涉及行为改变，补记可由 A1 以 record_ruling 完成，无需解冻裁判、judge/ 不必改动。
(c) 若人不接受 (a)：413 这一臂只能以 V2 失败暴露，需要在进实现线之前决定是解冻改资产、还是把这两步标为已知不可通过项——这一步我判断不了。
- HUMAN_J6：阶段复查要求人确认：阶段复查：J6 本身通过（judge v1 冻结，86 用例/284 步，differences=0、2 条按裁定结案，变异 score 1.0、gap 0，覆盖率 584/611），但冻结的裁判里有 4 个步骤在目标上必然由 API Gateway 的 Cognito 授权器应答、却没有在效裁定记账到这些步骤，V2 会把它们判成 fail，白耗一轮实现线。其中一类是状态码级的偏差，按本流水线自己的口径需要人确认，我不自行裁定。

证据（工作区只读核对）：
1) 机制：verify-tools/judge-run.js runUnit —— `route.authorized && !as` 时直接 gatewayAnswer('unauthorized')，不进 Lambda；judge.py `_rulings_in_force`/`judge_unit` 按「用例 + 步骤名」匹配裁定，未记到的步骤若比较出不匹配就是 verdict=fail（不是 migration-change/ruled）。
2) 已被裁定但记账漏了的两步：judge/cases/get-api-cart/wb-cart-x-customer-id-header-ignored.json 的 spoof-read-no-token（GET /api/cart）、spoof-write-no-token（POST /api/cart），无 Authorization、只带 x-customer-id，oracle 记 401 {"error":"Authentication required. Please provide a valid Bearer token."}；管辖它的是 R-A1-a3-1（migration-change，02:18 记录），其 affected_cases 只有 12 个 *-unauthenticated-401 用例的 27 个步骤。这两个用例是 J4 第 2 轮（05:06，为消 J5C 的 gap 变异 negate:middleware/auth.js:19）新加的，写账时不存在，所以覆盖不到（同用例的 spoof-read-u1-token 带有效 token，不受影响；wb-x-customer-id-header-ignored 挂在 GET /api/auth/me，属 R-A1-a3-3 的 dropped 端点，按 dropped 处理，无风险）。
3) 需要人判断的两步：cart-add-oversize-body-413#oversize-no-auth（POST /api/cart）与 order-oversize-body-413#oversize-no-auth（POST /api/orders）——无凭证且请求体 >100KB，单体由 body-parser 在 authMiddleware 之前拒掉，oracle 记的是 413 {"error":"request entity too large"}；目标的授权器先于函数，GAP-12/D-12 决定「在共享 helper 里复刻 body-parser 语义与 413」位于 Lambda 内，走不到，故目标答 401。这不是响应体文案差异，而是可观察状态码 413→401 的改变。R-A1-a3-1 的结论只覆盖「无凭证/坏凭证/refresh 当 access」三类单体本就 401 的请求，任何在效裁定都没有把「网关先于请求体大小检查」登记到这两步。
4) 为什么交人而不是退回 A1：storage/DECISIONS.md:483 里 A1 自己写明「放弃 413」属于需要人确认的那一类，而 GAP-12/D-12 的决定是复刻 413；现在正是这一类偏差在鉴权前置后自动发生，是否接受它不是事实能推出的结论。

要人判断的问题：
(a) 是否接受「未鉴权 + 超大请求体」由网关先答 401、不再复现单体的 413（即作为 migration-change 记入裁定账，点名 cart-add-oversize-body-413#oversize-no-auth、order-oversize-body-413#oversize-no-auth 两步；单体一侧的 413 契约仍由同用例带凭证的 oversize-json/oversize-urlencoded 步骤保住）。
(b) 同一口径下把 R-A1-a3-1 的 affected_cases 补到 wb-cart-x-customer-id-header-ignored#spoof-read-no-token、#spoof-write-no-token ——这是已裁定规则的必然结果，不涉及行为改变，补记可由 A1 以 record_ruling 完成，无需解冻裁判、judge/ 不必改动。
(c) 若人不接受 (a)：413 这一臂只能以 V2 失败暴露，需要在进实现线之前决定是解冻改资产、还是把这两步标为已知不可通过项——这一步我判断不了。
- C1：the optimizer left changes a person has to look at (blocked=[{'req': 'REQ-1', 'rule': 'R4', 'function': 'update-bestsellers', 'type': 'coverage', 'verdict': 'blocked_by_coverage', 'uncovered_changed_lines': [{'file': 'output/lambdas/bookstore/update-bestsellers/handler.js', 'line': 302, 'code': 'return;', 'statement': '消息 books 为空 → no-op 的防御分支（单体 utils/bestsellers.js:41-43，blueprint:lambda_functions[update-bestsellers].notes）。R4 把 `continue` 改成 `return`，这一行因此算改动行；POST /api/orders 对空 books 数组先返回 400（POST /api/orders#business_rules[0]），生产者不会发出这种消息，所以裁判用例覆盖不到它。'}], 'cases': [], 'patch': 'perf/patches/REQ-1.patch', 'other_checks': 'gate(final) PASS；run_judge(functions=[update-bestsellers]) PASS（10 pass + 2 migration-change，6 个故障运行 pass）；run_validate(functions=[update-bestsellers], assets=verification/delta) PASS 9/9，cases_with_conflicts=[]；run_equivalence(mode=diff, functions=[update-bestsellers], assets=verification/delta) EQUIVALENT（runs 14，differences=[]，unstable_in_baseline=[]）；改动行覆盖 65/66。', 'ask_of_verification_line': '补一条 verification/delta/events/update-bestsellers 用例：批次里混一条 {orderId, books: []} 的 SQS 消息，期望 batch_item_failures=[] 且 BestsellersTable/IdempotencyTable 原封不动。补好后用重新判决模式把 REQ-1 交回来即可（补丁可干净应用）。'}], pending_spec=[])

## 裁判线

- 规格对账第 1 轮：fact_error 5，needs_ruling 2
- 规格对账第 2 轮：needs_ruling 2，case_error 5
- 裁判 v1 已冻结（2026-11-13T05:35），见 judge/judge_manifest.json

## 裁定

- R-ROOM-spec-j3-r1-1-1-a1-1（m2s-architect，decided，contract）：规格对账差异 search-case-insensitive-200/upper-q/body.subset 与 search-hits-200/author/body.subset（以及同一用例当前只是碰巧按夹具顺序通过的 category 那一步）：事实库只规定命中集合与 total，没有规定 hits 的先后次序——迁移后的实现是否必须复现单体 DynamoDB scan 的返回顺序？请把这
…（截断，原文 239 字符） → 列表顺序的通用规则（本条只把 relaxation 施加在下面 affected_cases 列出的步骤上）：
1) 事实库为某个列表响应规定了显式排序键的，顺序属于契约，迁移后必须复现该排序键决定的次序——例如 GET /api/bestsellers#business_rules[1] 规定按 salesCount 降序（缺失按 0）取前 20，本裁定不放宽它，畅销榜那条顺序断言照旧逐位置比较
…（截断，原文 1195 字符）
- R-ROOM-spec-j3-r1-1-2-a1-1（m2s-architect，decided，contract）：列表顺序裁定 R-ROOM-spec-j3-r1-1-1-a1-1 的适用范围与措辞修正（spec-j3-r1 室 seq 3 的 B/C/D、seq 4/5 的 ①②③ 与三.3）：栏 1 的判定条件该写成"有具名排序键"还是"应用层显式排序"；affected_cases 要不要覆盖 GET /api/search 两个用例里其余的位置敏感步骤；relaxation 是否作用于条目内部的请求体
…（截断，原文 234 字符） → 本条只收窄/补写 R-ROOM-spec-j3-r1-1-1-a1-1，不推翻它：那条的规则结构（顺序按"有没有排序要求"分栏、放宽只到顺序这一层、并列与翻页不在范围内）保持原样，改动在下面四处。

1) 栏 1 的判定条件收窄为"应用层按业务字段显式排序"，不是"响应顺序由某个具名排序键决定"。现况满足这一条的只有 GET /api/bestsellers#business_rules[1]（r
…（截断，原文 3175 字符）
- R-A1-a3-1（m2s-architect，decided，migration-change）：迁移后受保护端点的未鉴权/坏凭证请求由 API Gateway 的 Cognito 授权器应答，响应体与单体的 authMiddleware 不同，怎么比？（R-2） → 受保护端点上「无凭证 / 坏凭证 / 把 refresh token 当 access token 用」这三类请求由 API Gateway 的 Cognito 授权器应答，不进 Lambda：状态码保持 401（与单体一致）；响应体是网关的 {"message":"Unauthorized"}，不复刻单体的 {"error":"Authentication required. Please pr
…（截断，原文 575 字符）
- R-A1-a3-2（m2s-architect，decided，contract）：GET /api/books 与 GET /api/search 的列表顺序：事实库说顺序就是 scan/索引返回顺序、没有规定排序键，迁移后的实现是否必须复现单体的返回次序？（R-3，把 R-1 的通用规则落到具体步骤） → 只在这些步骤上放宽「顺序」：事实库对 GET /api/books（Scan 全表臂与 category-index Query 臂）和 GET /api/search 的 hits 没有规定任何排序键（顺序 = 存储返回顺序），所以迁移后不要求复现单体 DynamoDB 的返回次序；等价性比较把这些列表按多重集比。不放宽内容：状态码、条目数/total、每个条目的属性集合与取值、hits 每项只
…（截断，原文 408 字符）
- R-A1-a3-3（m2s-architect，decided，migration-change）：四个认证端点与 Bookstore-Users 表被 Cognito 取代，单体在这些端点上的对外契约（响应体、状态码、bcrypt 500、email-index 判重）不再由目标提供，怎么认定？（R-4） → 目标不提供 POST /api/auth/register、POST /api/auth/login、POST /api/auth/refresh、GET /api/auth/me 这四个 HTTP 端点，也不建对应资源；客户端改用 Cognito SDK（Sign UP / signIn / getToken / fetchUserAttributes）。连带结果：①Bookstore-User
…（截断，原文 908 字符）
- R-A1-a3-4（m2s-architect，decided，contract）：R-A1-a3-2（列表顺序不作契约）的适用范围与措辞需要修正：它把「GET /api/orders、GET /api/cart 的顺序保持严格」写进了结论，而并行房间已 decided 一条裁定说明「表/索引的键序不等于排序契约」；本条 relaxation 到底覆盖哪些步骤？ → 范围与措辞修正（不推翻 R-A1-a3-2 的判定，只把它适用于哪些步骤说清楚，并与 R-ROOM-spec-j3-r1-1-2-a1-1 的口径对齐）：
1) 本条 relaxation=order 的适用面就是 affected_cases 里 GET /api/books 的列表步骤（all / fiction / programming / unknown / empty-value / 
…（截断，原文 1057 字符）
- R-ROOM-spec-j3-r2-1-1-a1-1（m2s-architect，decided，contract）：规格对账第二轮（本室）两条差异 search-case-insensitive-200/upper-q/body.subset 与 search-hits-200/author/body.subset 还需要新的列表顺序裁定吗？另外，既有两条 order 裁定的 affected_cases 含裸用例条目，在按用例记账的粒度下怎么保证不静默吞掉同用例的内容级差异？ → 本条只做确认与补写记账口径，不新增、不扩大任何放宽。
1) 不需要新裁定：两条差异已由 R-ROOM-spec-j3-r1-1-1-a1-1（列表顺序通用规则）与 R-ROOM-spec-j3-r1-1-2-a1-1（收窄与扩围）覆盖，两条均 status=decided、deviates_from_monolith=false、category=contract、relaxation=order
…（截断，原文 1443 字符）
- R-ROOM-spec-j3-r2-1-2-a1-1（m2s-architect，decided，contract）：spec-j3-r2 第 3–5 楼提出的四件事：(1) R-ROOM-spec-j3-r2-1-1-a1-1 第 2 点 (b) 的字面判据（"每个条目键集合与取值逐字相等"）在 subset 模板下永远不成立，验证线提出的 (i)-(v) 可执行形式是否认可；(2) 顺序暴露的审计口径——验证线按 predicted 模板数出"6 步 + 4 处嵌套"并结论"当前套件对 V2 没有仅顺序不同但
…（截断，原文 443 字符） → 本条批准验证线/标注员三项请求、纠正一条审计口径、并**新增点名两个步骤进 order 放宽范围**（这是本轮唯一的扩面，理由见第 3 点；不引入任何新的行为放宽）。

1) 认可 (i)-(v) 取代 R-...2-1-1 第 2 点 (b) 的字面写法。代码依据（本轮读实）：reconcile 走 compare_step → body.subset → verify._subset，其中列表
…（截断，原文 5791 字符）
- R-human-1（human，decided，migration-change）：未鉴权 + 超大请求体（>100KB）时，单体由 body-parser 在鉴权之前回 413 request entity too large，目标由 API Gateway 的 Cognito 授权器先回 401：是否接受这一状态码改变？ → 接受：作为 migration-change，未鉴权请求由网关先答 401，不再复现单体的 413（点名 cart-add-oversize-body-413#oversize-no-auth、order-oversize-body-413#oversize-no-auth 两步）。同用例带凭证的 oversize-json / oversize-urlencoded 步骤仍保持单体 413 契约
…（截断，原文 201 字符）
- R-human-2（human，decided，migration-change）：R-A1-a3-1 的记账补到 J4 第 2 轮新增的 wb-cart-x-customer-id-header-ignored 的两个 spoof 步骤：无 Authorization、只带 x-customer-id 时由网关授权器回 401，是否按同一规则记账？ → 是：R-A1-a3-1 的规则（无凭证/坏凭证/refresh 当 access 时由授权器回 401）覆盖这两个步骤；只补记账，不改行为，不解冻裁判。
- R-human-3（human，decided，implementation-trace）：O 阶段 REQ-1（R4：update-bestsellers 按消息处理）被判 blocked_by_coverage：66 行改动中 1 行（books=[] 的防御性 no-op return，HTTP 侧不可达）没有用例覆盖，其余检查（gate/judge/delta/equivalence）全 PASS。是否接受该改动？ → 接受：REQ-1 保留（verdict kept）。未覆盖的 1 行防御分支作为已知覆盖缺口记录；不需要回炉 D1。

## 人的决定

- 2026-11-13T01:02 HUMAN_RECONCILE：接受。J3 第 1 轮的 5 条 fact_error 已按端点退回 F2-identity（2 条）与 F2-bestsellers-books-cart-orders-search（3 条），由人工补派（修好 _fact_targets 之前的误路由）；J1/J2/RC/J3 会随 F2 重跑，J4 继续等 J3。规格讨论室 spec-j3-r1 的 2 条 needs_ruling 不变。
- 2026-11-13T08:36 HUMAN_J6：接受。接受 (a)：未鉴权超大请求体由网关先答 401（migration-change，点名两个 oversize-no-auth 步骤；带凭证的 413 契约保留）；接受 (b)：把 R-A1-a3-1 的记账补到两个 spoof 步骤。裁判保持冻结，不需要改资产或解冻。
- 2026-11-13T08:37 HUMAN_J6：接受。再次确认（崩溃在 case_decision 落盘前导致恢复时重新发问）：接受 (a) 未鉴权超大请求体由网关先答 401（R-human-1）与 (b) 两个 spoof 步骤补记（R-human-2）；裁定已在 storage/rulings.json，裁判保持冻结。
- 2026-11-13T13:46 C1：重做。接受 REQ-1（R4）的 1 行防御分支未覆盖：记为人工接受的覆盖缺口（R-human-3），O 报告与 perf_report 的 blocked/pending_human 已清除并记 human_acceptance；重开 C1 跑 gate(final)+全量裁判+增量测试，通过后进 C2 归档。

裁定共 11 条，其中人工裁定或确认 3 条，还在等人 0 条。

## 缺口清单

- 22 条：delta 1，judge 11，none 10
