STAGE_ID: J1
MODE: J1

这是 m2s 迁移流水线的一个任务，由 v3 调度器派发；你的角色说明已经在系统提示词里。这是这个任务第 2 次尝试。

## 绝对路径
- WS = D:\Emma\MyLab\Mono2SLs\Mono2Sls-artifacts\M2S\runs\bookstore-m4

## 任务
MODE: J1。只看事实库（用 mcp__m2s-server__judge_facts 读，不要整个打印 analysis_report.json，也不要读工具源码），写裁判用例：judge/cases/<接口 slug>/<用例>.json、judge/fixtures/、judge/faults/、judge/identities.json（需要时 judge/uncovered.json）。按你的角色说明里"J1"一节写；写完用 mcp__m2s-server__check_judge_assets 自查，直到 PASS。单体源码对你是关闭的。

## 回炉信息（调度器转来的，请据此修正）

### 1. 来自 J3
规格对账（J3）判定下面这些用例写错了，按 reason 修改：
[
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
  "reason": "用例的 predicted 停留在 J1 写用例时的旧事实（当时请求体解析事实只承认 JSON），单体实际是 201：bookstore/server.js:34 除了 bodyParser.json()（第 33 行）还注册了 bodyParser.urlencoded({extended:true})，表单里的 email/password 被正常解析，routes/auth.js:22 的真值判断、:33 的邮箱正则、:40 的 password.length>=6 对字符串 'Passw0rd!' 全部通过。事实库在 F2 修正轮（facts v5，sha a81e889e）已经改对并明确排除了这一臂：POST /api/auth/register#request.headers[content-type] 写明 urlencoded 提交照常解析并走到 201；#responses[0].note 写明'form 表单的 urlencoded 提交…字段齐全时不走这一臂而是 201'；#responses[6].when 写明'不会仅仅因为 Content-Type 不是 JSON 就返回这一臂'。现在事实、单体输出、oracle 三者一致，偏差只剩用例里那条 predicted=400，属于用例写错（预期比事实库说的还具体/过时），不需要再改事实库。",
  "fact_ref": "POST /api/auth/register#responses[4]",
  "fix": "J1 只改 predicted，不改请求（identity=null 正确，#business_rules[11] 说注册是公开接口）：status 改成 201；body 按 #responses[4] 断言 message='User registered successfully'、user.email='form@example.com'、user.name='form'（本步未带 name，按 #business_rules[6] 默认取邮箱 @ 前缀）；user.userId、accessToken、refreshToken 是易变值（oracle 里已屏蔽成 <uuid#3>/<token#4>/<token#5>），只写 has_fields+types，不要 equals 字面值。建议把 fact_refs 里的 #responses[6] 换成/补上 #responses[4] 与 #business_rules[6]，并把用例标题里'表单编码 → 400'的措辞一并纠正；同用例 reg-bad/login-bad/refresh-bad 三步（Content-Type: application/json + 截断 JSON → 400）保持不变，它们仍承载 #responses[6]。"
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
  "reason": "与 auth-endpoints-bad-json-400/reg-form-encoding/status 同一根因、同一步：predicted 断的是 400 {'error':'Email and password are required'} 这一臂，而该臂的前提（urlencoded 不被解析）已被事实库在 facts v5 否定——POST /api/auth/register#responses[0].note 与 #responses[4].when 都写明 urlencoded 表单字段会正常解析并返回 201；单体 server.js:34 的 bodyParser.urlencoded({extended:true}) 与 routes/auth.js:22 的实际行为与修正后的事实一致（oracle 实际体 = {'message':'User registered successfully', user{userId,email,name}, accessToken, refreshToken}）。事实无需再改，是用例预期过时。",
  "fact_ref": "POST /api/auth/register#responses[4]",
  "fix": "同上一步的 fix：body 断言按 #responses[4] 的字段集合改写——message/user.email/user.name 用确定的字符串，userId/accessToken/refreshToken 用 has_fields+types（不比较字面值，也不比较其长度、算法等实现细节）。"
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
  "reason": "predicted=400 依据的是 J1 当时的旧事实'content-type 不是 JSON 就不解析'；单体实际是 200：bookstore/server.js:34 注册了 bodyParser.urlencoded({extended:true})，routes/cart.js:72 的 if (!bookId || !quantity || !price) 对表单解析出的字符串 'book-001'/'2'/'10' 全部为真，于是 :87 docClient.put 成功后返回 'Item added to cart successfully'。事实库已在 facts v5 改对：POST /api/cart#request.headers[content-type] 与 #business_rules[2] 写明 urlencoded 与 JSON 外部效果相同，#responses[0].when 加了'只有 Content-Type 既不匹配 json 也不匹配 urlencoded 时才会落到这一臂'，#responses[1].when 写明两种编码都能走到 200。事实与单体一致，剩下的偏差在用例预期。用例标题里的前提只对同用例的 text 步骤（text/plain，该步对账通过）成立。",
  "fact_ref": "POST /api/cart#responses[1]",
  "fix": "J1 把 form 步的 predicted 改成 status=200 + body {'message':'Item added to cart successfully'}（#responses[1] 明确不回显条目内容，不要断言条目字段）；text 步保持 400 + 'Missing required fields'（#responses[0]，两个解析中间件都不匹配）；fact_refs 建议补 POST /api/cart#business_rules[2]，并把用例标题/意图改写成'只有两个解析中间件都不匹配的 Content-Type 才按缺字段处理'。"
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
  "reason": "与 cart-add-non-json-content-type-400/form/status 同步、同一步、同一根因：断的是 400 的 {'error':'Missing required fields: bookId, quantity, price'}（POST /api/cart#responses[0]），而事实库在 facts v5 已把该臂的前提收窄到'Content-Type 既不匹配 json 也不匹配 urlencoded'，urlencoded 表单按 #responses[1] 返回 200 + {'message':'Item added to cart successfully'}；单体的 urlencoded 解析中间件（server.js:34）与此一致。不是事实库错，是用例预期过时。",
  "fact_ref": "POST /api/cart#responses[1]",
  "fix": "body 断言改成 subset {'message':'Item added to cart successfully'}；不加其他字段断言（#responses[1] 说不回显条目内容）。"
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
   "POST /api/
…（截断，原文 6947 字符）

## 完成方式
1. 写工作报告 WS/storage/stage_reports/J1.json（格式按你的角色说明）。
2. 调用 mcp__m2s-server__submit_task(summary=...) 提交，然后结束会话。不需要再输出 STAGE_COMPLETE。
3. 做不下去时，调用 mcp__m2s-server__report_blocked(reason, evidence, suspected_module)，然后结束会话；认为是更早阶段的问题时，在 suspected_module 写那个阶段（例如 S2），并在 evidence 里给出证据。
4. 调度器会自己运行这个任务的检查（judge_assets），你的自述不作为判定依据。

## 注意
- 如果调用工具或等待过程中遇到网络错误（例如 'Network error: Connection error'），重试 1-2 次再继续，不要因为一次网络错误就直接放弃或提前结束。
- 只改这个任务范围内的文件：judge/cases/**、judge/fixtures/**、judge/faults/**、judge/identities.json、judge/run_config.json、judge/uncovered.json、storage/stage_reports/J1.json。范围之外的改动会被撤销，这次尝试判为失败。
- 不要修改流水线自己的工具：m2s-mcp-server/、.openharness/、~/.openharness/ 下的文件。
- 不要读写 WS/coord/（调度器的内部状态）。不要调用 mcp_auth。
- 不要读别的工作区（WS 所在 runs/ 目录下的其他目录、runs-prev-*），也不要参照它们的产物。
- 这份说明可以随时用 mcp__m2s-server__get_task_context 重新取回。