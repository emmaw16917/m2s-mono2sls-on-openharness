STAGE_ID: F2-bookings-customers-flights-loyalty-payments

这是 m2s 迁移流水线的一个任务，由 v3 调度器派发；你的角色说明已经在系统提示词里。这是这个任务第 3 次尝试。

## 绝对路径
- MONOLITH = D:\Emma\MyLab\Mono2SLs\Mono2Sls-artifacts\mono_benchmark\airline-booking
- WS = D:\Emma\MyLab\Mono2SLs\Mono2Sls-artifacts\M2S\runs\airline-m4

## 任务
STAGE_ID=F2-bookings-customers-flights-loyalty-payments：按你的角色说明完成这一阶段。

## 回炉信息（调度器转来的，请据此修正）

### 1. 来自 J4
白盒补测（J4）在单体代码里发现了事实库没有、或者写错了的规则（judge/spec_issues.json）。按代码核实，用 annotate_entry_point 补进或修正事实库（每条都带出处），不要改动无关的事实；改完 check_provenance 必须 PASS：
[
 {
  "id": "J4-1",
  "endpoint": "POST /bookings",
  "kind": "missing",
  "rule": "chargeId 没有任何类型校验：请求体里是什么类型就原样写进 booking.paymentToken。数字会被 _python_obj_to_dynamodb 转成 Decimal（float 15.5 → Decimal('15.5')），读回时 _dynamodb_to_python_obj 又把 Decimal 还原成 int（整数值）或 float（非整数值），所以 GET /bookings/<id> 里 paymentToken 的类型跟随输入；数组原样存成列表。模拟收款把这个值用 f-string 直接拼进 receiptUrl（15.5 → '.../receipts/15.5'，['a','b'] → \".../receipts/['a', 'b']\"），price 固定 150 与该值无关。事实库 POST /bookings#request.body[1] 只写 type=string、#business_rules[4] 只说 paymentToken=请求的 chargeId，没有写这条无校验+数值规范化规则",
  "evidence": [
   {
    "file": "data/storage.py",
    "line": 197,
    "symbol": "'paymentToken': booking_data['chargeId']"
   },
   {
    "file": "data/storage.py",
    "line": 37,
    "symbol": "return Decimal(str(obj))"
   },
   {
    "file": "data/storage.py",
    "line": 50,
    "symbol": "return float(obj)"
   },
   {
    "file": "data/storage.py",
    "line": 41,
    "symbol": "return [self._python_obj_to_dynamodb(item) for item in obj]"
   },
   {
    "file": "services/payment.py",
    "line": 166,
    "symbol": "receipt_url = f\"https://payment.example.com/receipts/{charge_id}\""
   }
  ],
  "cases": [
   "wb-booking-charge-id-non-string-stored-verbatim"
  ]
 },
 {
  "id": "J4-2",
  "endpoint": "POST /bookings/<booking_id>/cancel",
  "kind": "missing",
  "rule": "取消流程在同一条预订上连读三次：装饰器（鉴权）、handler 的 BookingService.get_booking、BookingService.cancel_booking 内部又读一次。第三次读落空时抛的是 ValueError('Invalid booking ID: <id>')，经全局 errorhandler 变成 400 {error, type:'ValueError'}，与事实库 cancel#responses[4]（第二次读落空 → 'Booking <id> not found'）文案不同；此时一条写入都没发生（预订状态不变、不放座、不退款）。事实库 cancel 的 responses 里没有这条臂",
  "evidence": [
   {
    "file": "services/booking.py",
    "line": 85,
    "symbol": "raise ValueError(f\"Invalid booking ID: {booking_id}\")"
   },
   {
    "file": "app.py",
    "line": 279,
    "symbol": "BookingService.cancel_booking(booking_id)"
   }
  ],
  "cases": [
   "booking-cancel-and-read"
  ]
 },
 {
  "id": "J4-4",
  "endpoint": "POST /bookings",
  "kind": "missing",
  "rule": "请求体是非空但非对象的 JSON（例如数组 [1,2]）时，不会走 400 'Request body is required'：`if not data` 为假，下一行 `data['customerId'] = request.current_user['sub']` 对列表取下标抛 TypeError。这个赋值在最外层 try 之外，也不属于任何 except ValueError，直接冒到全局 @app.errorhandler(Exception)，因非 ValueError 返回 500 {error, type:'TypeError'}，并且不发生任何表读写（座位没被扣、预订没建）。事实库 POST /bookings#responses[1] 只覆盖“缺失/非 JSON/解析为空”，#responses[7] 的 500 只写了缺 outboundFlightId 的 KeyError 来源",
  "evidence": [
   {
    "file": "app.py",
    "line": 91,
    "symbol": "if not data:"
   },
   {
    "file": "app.py",
    "line": 95,
    "symbol": "data['customerId'] = request.current_user['sub']"
   },
   {
    "file": "app.py",
    "line": 34,
    "symbol": "}), 400 if isinstance(error, ValueError) else 500"
   }
  ],
  "cases": [
   "wb-booking-empty-charge-rollback-and-array-body"
  ]
 }
]

## 完成方式
1. 写工作报告 WS/storage/stage_reports/F2-bookings-customers-flights-loyalty-payments.json（格式按你的角色说明）。
2. 调用 mcp__m2s-server__submit_task(summary=...) 提交，然后结束会话。不需要再输出 STAGE_COMPLETE。
3. 做不下去时，调用 mcp__m2s-server__report_blocked(reason, evidence, suspected_module)，然后结束会话；认为是更早阶段的问题时，在 suspected_module 写那个阶段（例如 S2），并在 evidence 里给出证据。
4. 调度器会自己运行这个任务的检查（check_provenance_pkg），你的自述不作为判定依据。

## 注意
- 如果调用工具或等待过程中遇到网络错误（例如 'Network error: Connection error'），重试 1-2 次再继续，不要因为一次网络错误就直接放弃或提前结束。
- 只改这个任务范围内的文件：storage/analysis_report.json、storage/stage_reports/F2-bookings-customers-flights-loyalty-payments.json。范围之外的改动会被撤销，这次尝试判为失败。
- 不要修改流水线自己的工具：m2s-mcp-server/、.openharness/、~/.openharness/ 下的文件。
- 不要读写 WS/coord/（调度器的内部状态）。不要调用 mcp_auth。
- 不要读别的工作区（WS 所在 runs/ 目录下的其他目录、runs-prev-*），也不要参照它们的产物。
- 这份说明可以随时用 mcp__m2s-server__get_task_context 重新取回。