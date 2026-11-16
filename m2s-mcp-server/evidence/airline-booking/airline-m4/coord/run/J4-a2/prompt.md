STAGE_ID: J4
MODE: J4

这是 m2s 迁移流水线的一个任务，由 v3 调度器派发；你的角色说明已经在系统提示词里。这是这个任务第 2 次尝试。

## 绝对路径
- MONOLITH = D:\Emma\MyLab\Mono2SLs\Mono2Sls-artifacts\mono_benchmark\airline-booking
- WS = D:\Emma\MyLab\Mono2SLs\Mono2Sls-artifacts\M2S\runs\airline-m4

## 任务
MODE: J4。白盒补测：可以读单体源码。按 judge/coverage_monolith/summary.json 里还没走到的行（第二轮起还有回炉信息里的 gap 变异体）补用例 judge/cases/<接口 slug>/wb-*.json；代码里有、事实库没有的规则写进 judge/spec_issues.json。按你的角色说明里"J4"一节做，用 mcp__m2s-server__check_judge_assets 自查到 PASS。

## 回炉信息（调度器转来的，请据此修正）

### 1. 来自 J3
事实库已按下面这些差异修正。只更新这些用例的 predicted，使它符合修正后的事实库（重新读事实库），不改请求：
[
 {
  "id": "wb-booking-charge-id-non-string-stored-verbatim/create-list/status",
  "case": "wb-booking-charge-id-non-string-stored-verbatim",
  "step": "create-list",
  "request": {
   "method": "POST",
   "path": "/bookings"
  },
  "check": "status",
  "predicted": 201,
  "actual": 500,
  "fact_refs": [
   "POST /bookings#responses[6]",
   "POST /bookings#business_rules[4]",
   "POST /bookings#business_rules[5]",
   "GET /bookings/<booking_id>#responses[3]",
   "GET /flights/<flight_id>#responses[0]"
  ],
  "reason": "单体对数组 chargeId 不是 201。事实库 POST /bookings#business_rules[15] 写『数组递归转换后原样读回成 list』、#responses[6] 写『非字符串 chargeId（15.5、['a','b']）会直接出现在 URL 里』、#request.body[1] note 写『请求体里是数字/数组/空串都照样接受』，这三处与实际矛盾：Step 2 的 put_item 确实能把 list 写进 paymentToken（data/storage.py:197 'paymentToken': booking_data['chargeId']、data/storage.py:40-41 _python_obj_to_dynamodb 对 list 逐项递归、data/storage.py:204-205 put_item），但 Step 3 模拟收款在 return 之前要做记账 PaymentService._payments[charge_id] = payment_record（services/payment.py:177；_payments 是 Dict[str, dict]，services/payment.py:21），list 作字典键抛 TypeError: unhashable type: 'list'。app.py:135-136 的 Step 3 只 except ValueError，TypeError 逃过该分支，冒到最外层 except Exception（app.py:219），返回 500 {'error': 'Unexpected error during booking: unhashable type: 'list'', 'message': 'The booking has been rolled back. Please try again.'}（app.py:245-248），即事实库 #responses[7] 那条臂，只是 #responses[7] 的 when 只举了 KeyError 一个成因。receiptUrl 的 f-string（services/payment.py:166）本身能拼出 \".../receipts/['a', 'b']\"，但异常发生在它之后的记账行，所以永远不会出现在响应里。这是事实库写错/漏写，不是用例写错：用例的 predicted 正是按事实库这几条写的。",
  "fact_ref": "POST /bookings#business_rules[15]",
  "fix": "改 POST /bookings#business_rules[15]：标量 chargeId（int/float）原样进 paymentToken 并被 f-string 拼进 receiptUrl；数组/集合等不可哈希的值在 Step 3 模拟收款的 PaymentService._payments[charge_id] 记账处抛 TypeError('unhashable type: list')，不是 ValueError，落到 catch-all 返回 500，无 bookingId。同步修正 #responses[6] 的『['a','b'] 会直接出现在 URL 里』、#request.body[1] note 的『数组……照样接受』，并在 #responses[7] 的 when 里补上这个成因（#business_rules[11] 已描述该臂的回滚顺序，与实际一致）。"
 },
 {
  "id": "wb-booking-charge-id-non-string-stored-verbatim/create-list/body.subset",
  "case": "wb-booking-charge-id-non-string-stored-verbatim",
  "step": "create-list",
  "request": {
   "method": "POST",
   "path": "/bookings"
  },
  "check": "body.subset",
  "predicted": {
   "status": "CONFIRMED",
   "payment": {
    "receiptUrl": "https://payment.example.com/receipts/['a', 'b']",
    "price": 150
   }
  },
  "actual": {
   "error": "Unexpected error during booking: unhashable type: 'list'",
   "message": "The booking has been rolled back. Please try again."
  },
  "fact_refs": [
   "POST /bookings#responses[6]",
   "POST /bookings#business_rules[4]",
   "POST /bookings#business_rules[5]",
   "GET /bookings/<booking_id>#responses[3]",
   "GET /flights/<flight_id>#responses[0]"
  ],
  "reason": "同一条事实错误的响应体侧：predicted 按 POST /bookings#responses[6] 写了 status='CONFIRMED' + payment.receiptUrl=\"https://payment.example.com/receipts/['a', 'b']\" + price=150，单体实际返回的是 #responses[7] 那一臂的体 {'error': \"Unexpected error during booking: unhashable type: 'list'\", 'message': 'The booking has been rolled back. Please try again.'}（app.py:245-248），没有 bookingId/bookingReference/status/payment/notification。根因见 create-list/status：不可哈希的 chargeId 在模拟收款记账行 services/payment.py:177 抛 TypeError，Step 3 的 except 只接 ValueError（app.py:136）。事实库修正后这一步的 predicted 应改成 500 + 这两个字段。",
  "fact_ref": "POST /bookings#responses[6]",
  "fix": "事实库修正同 create-list/status；本步 predicted 由 J1 按修正后的 #responses[7] 改写（error 以 'Unexpected error during booking: ' 开头，message='The booking has been rolled back. Please try again.'，无 bookingId）。"
 },
 {
  "id": "wb-booking-charge-id-non-string-stored-verbatim/read-list/status",
  "case": "wb-booking-charge-id-non-string-stored-verbatim",
  "step": "read-list",
  "request": {
   "method": "GET",
   "path": "/bookings/${bidList}"
  },
  "check": "status",
  "predicted": 200,
  "actual": 404,
  "fact_refs": [
   "POST /bookings#responses[6]",
   "POST /bookings#business_rules[4]",
   "POST /bookings#business_rules[5]",
   "GET /bookings/<booking_id>#responses[3]",
   "GET /flights/<flight_id>#responses[0]"
  ],
  "reason": "上一步 500 的连锁差异，仍然源自事实库那条『数组原样存成 list、能读回』：500 的响应体不含 bookingId（app.py:245-248），用例的 save: {bidList: body.bookingId} 取不到值，read-list 实际打在字面量路径 /bookings/${bidList} 上，装饰器按主键读不到条目 → GET /bookings/<booking_id>#responses[1] 404 {'error': 'Booking not found'}，而不是 predicted 的 200。事实库说的可观察结果是错的：那条预订确实在 Step 2 被写进 Airline-Booking（app.py:119 reserve_booking 在 TypeError 之前已成功，paymentToken=['a','b']），随后 catch-all 里 cancel_booking 把它打成 CANCELLED（app.py:231-234），但它的 id 是服务端 uuid4 且响应不返回，单体也没有任何列表端点能按 customer 列出预订，所以这条残留条目对外只在 GET /flights/<id> 的 seatCapacity 上可见（见 seat-after）。annotator 修 #business_rules[15]/#responses[6] 时应明确：不可哈希 chargeId 的建单没有可读回的 bookingId。",
  "fact_ref": "GET /bookings/<booking_id>#responses[1]",
  "fix": "事实库修正后 J1 需重排这一步：数组臂不能写 read-back 步骤（500 不返回 id）；若要检验『建单失败不留 CONFIRMED 预订』这一可观察契约，用 seat-after 的 seatCapacity 承担，或按 judge/uncovered.json 说明该 CANCELLED 残留条目在单体的接口下读不回来。"
 },
 {
  "id": "wb-booking-charge-id-non-string-stored-verbatim/read-list/body.subset",
  "case": "wb-booking-charge-id-non-string-stored-verbatim",
  "step": "read-list",
  "request": {
   "method": "GET",
   "path": "/bookings/${bidList}"
  },
  "check": "body.subset",
  "predicted": {
   "status": "CONFIRMED",
   "paymentToken": [
    "a",
    "b"
   ],
   "customer": "cust-u1"
  },
  "actual": {
   "error": "Booking not found"
  },
  "fact_refs": [
   "POST /bookings#responses[6]",
   "POST /bookings#business_rules[4]",
   "POST /bookings#business_rules[5]",
   "GET /bookings/<booking_id>#responses[3]",
   "GET /flights/<flight_id>#responses[0]"
  ],
  "reason": "与 read-list/status 同一根因的体侧：predicted 按事实库 POST /bookings#business_rules[4]（paymentToken=请求的 chargeId，事实库又称数组原样读回成 list）+ GET /bookings/<booking_id>#responses[3] 写了 status='CONFIRMED'、paymentToken=['a','b']、customer
…（截断，原文 8144 字符）

### 2. 来自 J3
规格对账（J3）判定下面这些用例写错了，按 reason 修改：
[
 {
  "id": "wb-booking-charge-id-non-string-stored-verbatim/create-list/status",
  "case": "wb-booking-charge-id-non-string-stored-verbatim",
  "step": "create-list",
  "request": {
   "method": "POST",
   "path": "/bookings"
  },
  "check": "status",
  "predicted": 201,
  "actual": 500,
  "fact_refs": [
   "POST /bookings#responses[6]",
   "POST /bookings#business_rules[4]",
   "POST /bookings#business_rules[5]",
   "GET /bookings/<booking_id>#responses[3]",
   "GET /flights/<flight_id>#responses[0]"
  ],
  "reason": "单体是对的，用例的预期是旧的。上一轮 J3 把这条差异判为事实库错误并交给 annotator，事实库已经改过来了（storage/analysis_report.json 现写于本轮 oracle 生成之前）：POST /bookings#business_rules[15] 第 (3) 臂、#request.body[1] 的 note、#responses[7] 的成因 (2) 三处现在都明确说“非空数组/对象这类不可哈希的 chargeId 到不了 201”。代码核对一致：Step 3 调 PaymentService.collect_payment（app.py:135），模拟模式先算 receipt_url（services/payment.py:166），再做内存记账 PaymentService._payments[charge_id] = payment_record（services/payment.py:177，_payments 是 Dict，services/payment.py:21）——list 作字典键抛 TypeError: unhashable type: 'list'；Step 3 只 except ValueError（app.py:136），TypeError 逃过它，落到最外层 catch-all（app.py:219）返回 500。actual=500 与修正后的事实库相符，所以差异的来源只剩 predicted：这个 wb 用例是 J4 在事实库修正之前写的，predicted 仍按旧事实写 201。",
  "fact_ref": "POST /bookings#responses[7]",
  "fix": "J1 回炉只改 predicted（请求照旧）：create-list.status 由 201 改 500。"
 },
 {
  "id": "wb-booking-charge-id-non-string-stored-verbatim/create-list/body.subset",
  "case": "wb-booking-charge-id-non-string-stored-verbatim",
  "step": "create-list",
  "request": {
   "method": "POST",
   "path": "/bookings"
  },
  "check": "body.subset",
  "predicted": {
   "status": "CONFIRMED",
   "payment": {
    "receiptUrl": "https://payment.example.com/receipts/['a', 'b']",
    "price": 150
   }
  },
  "actual": {
   "error": "Unexpected error during booking: unhashable type: 'list'",
   "message": "The booking has been rolled back. Please try again."
  },
  "fact_refs": [
   "POST /bookings#responses[6]",
   "POST /bookings#business_rules[4]",
   "POST /bookings#business_rules[5]",
   "GET /bookings/<booking_id>#responses[3]",
   "GET /flights/<flight_id>#responses[0]"
  ],
  "reason": "同一条旧事实留下的旧预期。predicted 断言 201 的响应体里有 status='CONFIRMED' 和 payment.receiptUrl=\"https://payment.example.com/receipts/['a', 'b']\"，但修正后的事实库 #responses[6] 已经明说“非空数组/对象这类不可哈希的 chargeId 到不了这一臂”，#responses[7] 给出这一臂的体：fields 只有 error、message，error 以 'Unexpected error during booking: ' 开头、不返回 bookingId；#business_rules[15] 第 (3) 臂还补了“receiptUrl 永远不会出现在响应里”。代码可核对：TypeError 抛在 services/payment.py:177，即 collect_payment 的 return 之前，receipt_url 那个字符串根本没机会被返回，所以响应里不可能有 payment 对象；实际体是 app.py:246-247 的两项，error 末尾是 unhashable type: 'list'，与 #responses[7] 的 description 完全对应。另外这条 predicted 即便按旧事实也写得过具体：它把 Python 的 list repr 字面量直接拼死进 receiptUrl，而事实库从没规定过 URL 里数组的书写形式。",
  "fact_ref": "POST /bookings#responses[7]",
  "fix": "J1 回炉改 predicted：create-list.body 撤掉 status/payment 那组断言，改成按 #responses[7] 的两项——message 用 'The booking has been rolled back. Please try again.' 等值，error 只断前缀 'Unexpected error during booking: '（后缀是 TypeError 的字符串表示，事实库说它随实际类型而变，不钉字面值）；同时撤掉这一步的 save: {bidList: body.bookingId}（500 体里没有 bookingId）。"
 },
 {
  "id": "wb-booking-charge-id-non-string-stored-verbatim/read-list/status",
  "case": "wb-booking-charge-id-non-string-stored-verbatim",
  "step": "read-list",
  "request": {
   "method": "GET",
   "path": "/bookings/${bidList}"
  },
  "check": "status",
  "predicted": 200,
  "actual": 404,
  "fact_refs": [
   "POST /bookings#responses[6]",
   "POST /bookings#business_rules[4]",
   "POST /bookings#business_rules[5]",
   "GET /bookings/<booking_id>#responses[3]",
   "GET /flights/<flight_id>#responses[0]"
  ],
  "reason": "这一步的请求本身走不通，和创建臂的预期无关。用例写的是 GET /bookings/${bidList}，而 bidList 来自 create-list 的 save body.bookingId；按修正后的事实库 #responses[7]，这一臂“响应里没有 bookingId”，所以变量取不到值，请求实际打在字面量路径 /bookings/${bidList} 上，这个 id 在表里不存在，单体按 #responses[1] 返回 404 {error: 'Booking not found'}——actual 正是这条事实，单体没问题。predicted 200 是从旧的 201 预期推下来的连锁后果。补充：那条真正残留的 CANCELLED 预订用 id 读不到（id 是服务端 uuid4，app.py 建单流程 500 时不返回），单体唯一的按客户列出预订的接口是 GET /customers/<customer_id>/bookings（app.py:309），修正后的 #responses[7] 也已经写明“那条残留条目只能从 GET /customers/<customer_id>/bookings 看到”。",
  "fact_ref": "GET /bookings/<booking_id>#responses[1]",
  "fix": "J1 回炉把这一步换成 GET /customers/${u1.id}/bookings（身份 u1），这样写后读回的语义才成立；404 这一臂不需要单独出题（已有别的用例覆盖 GET /bookings/<booking_id> 的 404）。"
 },
 {
  "id": "wb-booking-charge-id-non-string-stored-verbatim/read-list/body.subset",
  "case": "wb-booking-charge-id-non-string-stored-verbatim",
  "step": "read-list",
  "request": {
   "method": "GET",
   "path": "/bookings/${bidList}"
  },
  "check": "body.subset",
  "predicted": {
   "status": "CONFIRMED",
   "paymentToken": [
    "a",
    "b"
   ],
   "customer": "cust-u1"
  },
  "actual": {
   "error": "Booking not found"
  },
  "fact_refs": [
   "POST /bookings#responses[6]",
   "POST /bookings#business_rules[4]",
   "POST /bookings#business_rules[5]",
   "GET /bookings/<booking_id>#responses[3]",
   "GET /flights/<flight_id>#responses[0]"
  ],
  "reason": "上一条的连锁项：路径带着未解析的 ${bidList}，单体在 GET /bookings/<booking_id>#responses[1] 就返回 {error: 'Booking not found'}，predicted 那组 status='CONFIRMED' / paymentToken=['a','b'] / customer='cust-u1' 自然对不上，而 CONFIRMED 这个前提本身就是旧事实的产物（#business_rules[15] 第 (3) 臂现在说这条预订会被 catch-all 的 cancel_booking 打成 CANCELLED，不是 CONFIRMED）。paymentToken=['a','b'] 这半截仍然是对的：#business_rules[4] 说 paymentToken=请求的 chargeId，put_item 能把 list 原样写进去（data/storage.py:40-41 逐项递归转换）。",
  "fact_ref": "GET /customers/<customer_id>/bookings#responses[2]",
  "fix": "配合上一条改用列表接口后，预期按修正后的事实库写成 #responses[2] 的 bookings 数组里 contains 一项 {status: 'CANCELLED', paymentToken: ['a', 'b'], customer: 'cust-u1', bookingOutboundFlightId: 'flight-sfo-lax'}；用 contains 不用按下标取（GET /customers/<customer_id>/bookings#business_rules[3] 是 Scan，没有排序），createdAt/bookingReference 不断言。"
 },
 {
  "id": "wb-booking-charge-id-non-string-stored-verbatim/seat-after/body.subset",
  "case":
…（截断，原文 7916 字符）

## 完成方式
1. 写工作报告 WS/storage/stage_reports/J4.json（格式按你的角色说明）。
2. 调用 mcp__m2s-server__submit_task(summary=...) 提交，然后结束会话。不需要再输出 STAGE_COMPLETE。
3. 做不下去时，调用 mcp__m2s-server__report_blocked(reason, evidence, suspected_module)，然后结束会话；认为是更早阶段的问题时，在 suspected_module 写那个阶段（例如 S2），并在 evidence 里给出证据。
4. 调度器会自己运行这个任务的检查（judge_assets, j4_report），你的自述不作为判定依据。

## 注意
- 如果调用工具或等待过程中遇到网络错误（例如 'Network error: Connection error'），重试 1-2 次再继续，不要因为一次网络错误就直接放弃或提前结束。
- 只改这个任务范围内的文件：judge/cases/**、judge/fixtures/**、judge/faults/**、judge/spec_issues.json、storage/stage_reports/J4.json。范围之外的改动会被撤销，这次尝试判为失败。
- 不要修改流水线自己的工具：m2s-mcp-server/、.openharness/、~/.openharness/ 下的文件。
- 不要读写 WS/coord/（调度器的内部状态）。不要调用 mcp_auth。
- 不要读别的工作区（WS 所在 runs/ 目录下的其他目录、runs-prev-*），也不要参照它们的产物。
- 这份说明可以随时用 mcp__m2s-server__get_task_context 重新取回。