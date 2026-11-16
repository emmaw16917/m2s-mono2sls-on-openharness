STAGE_ID: F2-bookings-customers-flights-loyalty-payments

这是 m2s 迁移流水线的一个任务，由 v3 调度器派发；你的角色说明已经在系统提示词里。这是这个任务第 5 次尝试。

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
  "id": "J4-5",
  "endpoint": "POST /bookings",
  "kind": "missing",
  "rule": "catch-all（app.py:219-248）的补偿只认本次请求的局部变量，而且退款按 chargeId 定位：三个 if 读的是本次调用的 payment_result/booking_id/flight_reserved，但真正退的是 PaymentService.refund_payment(data['chargeId'])（app.py:227）——即“请求体里那个 chargeId 在进程内存 _payments 里对应的那笔收款”，与本次请求自己有没有收到款无关。两条已知可达成因（#responses[7]：缺 outboundFlightId 的 KeyError 发生在 Step 1 取参数时、不可哈希 chargeId 的 TypeError 抛在 Step 3 记账行 services/payment.py:177）都发生在 payment_result 被赋值（app.py:135）之前，payment_result 恒为 None，因此事实库 #business_rules[11] 说的“依次尝试退款、取消预订、放座”在现有可达路径上退款那一步一次都不会执行。外部可见后果（现有事实库推不出、也没有任何用例比较过）：同一进程里某个 chargeId 若先前已被 POST /bookings 或 POST /payments/collect 收款成功（模拟模式写入 status='captured'，services/payment.py:169-177），随后一个带同一个 chargeId 但缺 outboundFlightId 的建单请求虽然返回 500，那笔先前收款的 status 仍是 'captured'——GET /payments/<charge_id> 读回不变；反之任何“catch-all 会退掉请求体里那个 chargeId”的实现（例如把退款条件写成 payment_result 为假时也退、或按 chargeId 无条件补偿）都会把它改成 'refunded'（services/payment.py:276-277），这个差别只在这条用例的读回步骤上可见。【建议 annotator】把 #business_rules[11] 补成“退款只在本请求真的收到过款（payment_result 为真）时才做，退的是本请求 data['chargeId'] 那笔；两条可达成因下 payment_result 恒为 None，故 catch-all 不产生任何退款”。",
  "evidence": [
   {
    "file": "app.py",
    "line": 224,
    "symbol": "if payment_result:"
   },
   {
    "file": "app.py",
    "line": 227,
    "symbol": "PaymentService.refund_payment(data['chargeId'])"
   },
   {
    "file": "app.py",
    "line": 135,
    "symbol": "payment_result = PaymentService.collect_payment(data['chargeId'])"
   },
   {
    "file": "services/payment.py",
    "line": 173,
    "symbol": "'status': 'captured'"
   },
   {
    "file": "services/payment.py",
    "line": 277,
    "symbol": "PaymentService._payments[charge_id]['status'] = 'refunded'"
   }
  ],
  "cases": [
   "wb-booking-catchall-refunds-collected-charge"
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