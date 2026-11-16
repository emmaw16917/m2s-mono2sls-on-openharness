STAGE_ID: J3
MODE: J3

这是 m2s 迁移流水线的一个任务，由 v3 调度器派发；你的角色说明已经在系统提示词里。这是这个任务第 4 次尝试。

## 绝对路径
- MONOLITH = D:\Emma\MyLab\Mono2SLs\Mono2Sls-artifacts\mono_benchmark\airline-booking
- WS = D:\Emma\MyLab\Mono2SLs\Mono2Sls-artifacts\M2S\runs\airline-m4

## 任务
MODE: J3。给 judge/reconcile.json 里的每一条差异（J1 按事实库写的 predicted 和单体实际输出对不上的地方）分类，写进 judge/reconcile_classes.json：fact_error / needs_ruling / case_error / mask_gap，每条写 reason（证据）。mask_gap 要同时在 judge/mask_rules.json 里补屏蔽规则。按你的角色说明里"J3"一节做。

## 回炉信息（调度器转来的，请据此修正）

### 1. 来自 recheck
judge/reconcile_classes.json 的问题：
wb-booking-charge-id-non-string-stored-verbatim/create-list/status: not classified
wb-booking-charge-id-non-string-stored-verbatim/create-list/body.subset: not classified
wb-booking-charge-id-non-string-stored-verbatim/read-list/status: not classified
wb-booking-charge-id-non-string-stored-verbatim/read-list/body.subset: not classified
wb-booking-charge-id-non-string-stored-verbatim/seat-after/body.subset: not classified

上一次的工作报告全文：
{
  "stage_id": "J3",
  "agent": "m2s-verifier",
  "attempt": 3,
  "status": "success",
  "summary": "本轮 judge/reconcile.json 的 5 条差异全部来自同一个 wb 用例 wb-booking-charge-id-non-string-stored-verbatim 的数组 chargeId 臂，5 条全部判为 fact_error：事实库 POST /bookings 声称不可哈希的数组 chargeId 也能建单成功（#business_rules[15]『数组递归转换后原样读回成 list』、#responses[6] 的『['a','b'] 会直接出现在 URL 里』、#request.body[1] note 的『数组……照样接受』），而单体的实际行为是在 Step 3 模拟收款的记账行 services/payment.py:177（PaymentService._payments 是 Dict，services/payment.py:21）抛 TypeError('unhashable type: 'list')，Step 3 只 except ValueError（app.py:135-136），于是落进最外层 catch-all（app.py:219）返回 500 无 bookingId，并按标记回滚（booking_id 存在 → cancel_booking，flight_reserved → release_flight_seat，payment_result 为 None → 不退款）。predicted 201/读回 200/seatCapacity=3 就是按这条错误事实推出来的，read-list 的 404 与 seat-after 的 4 都是它的连锁后果，单体这几处行为与 #business_rules[7]/[11] 的回滚描述一致，因此根因只有事实库一处。无 needs_ruling、无 case_error、无 mask_gap，未新增 judge/mask_rules.json。check_reconcile_classes = PASS。",
  "differences_total": 5,
  "by_class": {
    "fact_error": [
      "wb-booking-charge-id-non-string-stored-verbatim/create-list/status",
      "wb-booking-charge-id-non-string-stored-verbatim/create-list/body.subset",
      "wb-booking-charge-id-non-string-stored-verbatim/read-list/status",
      "wb-booking-charge-id-non-string-stored-verbatim/read-list/body.subset",
      "wb-booking-charge-id-non-string-stored-verbatim/seat-after/body.subset"
    ],
    "needs_ruling": [],
    "case_error": [],
    "mask_gap": []
  },
  "mask_rules_added": 0,
  "self_check": {
    "tool": "mcp__m2s-server__check_reconcile_classes",
    "status": "PASS",
    "classes": {"fact_error": 5, "needs_ruling": 0, "case_error": 0, "mask_gap": 0}
  },
  "monolith_evidence": [
    "app.py:135 payment_result = PaymentService.collect_payment(data['chargeId'])；app.py:136 只 except ValueError as e",
    "services/payment.py:21 _payments: Dict[str, dict] = {}；services/payment.py:166 receipt_url = f\"https://payment.example.com/receipts/{charge_id}\"（f-string 本身对 list 能拼出 \".../receipts/['a', 'b']\"）；services/payment.py:177 PaymentService._payments[charge_id] = payment_record ← 数组作字典键，TypeError: unhashable type: 'list'，发生在那次 return 之前",
    "app.py:219 except Exception as e（catch-all）；app.py:224 if payment_result 为 None 不成立 → 不退款；app.py:231-234 cancel_booking(booking_id)；app.py:240-241 release_flight_seat；app.py:245-248 返回 500 {error: 'Unexpected error during booking: unhashable type: list', message: 'The booking has been rolled back. Please try again.'}，不含 bookingId",
    "app.py:119 booking_id = BookingService.reserve_booking(data)（Step 2 在 TypeError 之前已成功）；data/storage.py:197 'paymentToken': booking_data['chargeId']；data/storage.py:40-41 _python_obj_to_dynamodb 对 list 逐项递归；data/storage.py:204-205 put_item 能把 list 写进去 → 表里留下一条 CANCELLED 条目",
    "seat-after 核算：judge/fixtures/flights.json flight-sfo-lax seatCapacity=5；create-float 成功扣 1 → 4；create-list Step 1 扣到 3 后 Step 3 抛错，catch-all 放回 1（data/storage.py:163-178 release_flight_seat 的 update_item seatCapacity+1，条件 seatCapacity<maximumSeating 通过）→ 实际 4，maximumSeating 10 不变",
    "read-list 实际 404 的机制：500 体无 bookingId → 用例 save {bidList: body.bookingId} 取不到值 → 请求打在字面量 /bookings/${bidList} → GET /bookings/<booking_id>#responses[1] 404 'Booking not found'；单体只有 /bookings POST 与 /bookings/<id> GET/confirm/cancel（app.py:77/251/259/271），没有按 customer 列出预订的接口，那条 id 是服务端 uuid4 且不返回，所以残留的 CANCELLED 条目在单体接口下读不回来"
  ],
  "handoff": {
    "fact_error_annotator": [
      {
        "fact_ref": "POST /bookings#business_rules[15]",
        "problem": "『数组递归转换后原样读回成 list』这个端到端结论不成立：storage 层确实能存能读 list，但整条建单流程在 Step 3 模拟收款的字典记账处抛 TypeError('unhashable type: list')，数组 chargeId 永远拿不到 201",
        "fix": "改写为：标量 chargeId（int/float，含 15.5 → Decimal → 读回 15.5/15）原样进 paymentToken 并被 f-string 拼进 receiptUrl；数组等不可哈希的值在 services/payment.py:177 抛 TypeError，不是 ValueError，落进 catch-all 返回 500（无 bookingId），预订被 cancel_booking 打成 CANCELLED、座位放回、未退款"
      },
      {
        "fact_ref": "POST /bookings#responses[6]",
        "problem": "description 里的『非字符串 chargeId（15.5、['a','b']）会直接出现在 URL 里』把数组列为 201 的一例",
        "fix": "只保留标量例子（15.5 等），明确数组 chargeId 走 500 那一臂"
      },
      {
        "fact_ref": "POST /bookings#request.body[1]",
        "problem": "note 里『请求体里是数字/数组/空串都照样接受（……数组原样存成 list）』",
        "fix": "数组不被接受：建单在 Step 3 因 unhashable 失败；空串仍按 #business_rules[15] 走 Step 3 的 ValueError('Invalid Charge ID') → 400（与数组不同，因为空串可哈希）"
      },
      {
        "fact_ref": "POST /bookings#responses[7]",
        "problem": "when 只举了『缺 outboundFlightId 时 KeyError』这一个成因，导致数组 chargeId 这条 500 无法引用",
        "fix": "在 when 里补上『不可哈希的 chargeId（数组）在模拟收款记账处抛 TypeError』这类成因，并注明该臂按 payment_result/booking_id/flight_reserved 回滚、不返回 bookingId"
      },
      {
        "fact_ref": "judge/spec_issues.json 的 J4-1",
        "problem": "这条 spec_issue 的 rule 文本就是错误来源（『数组原样存成列表』），annotator 是照它写进 #business_rules[15] 的",
        "fix": "同一处修正要落到 J4-1 上，否则 J4 下一轮还会按同一条规则出题"
      }
    ],
    "needs_ruling": [],
    "case_error_j1_rework": [
      {
        "case": "wb-booking-charge-id-non-string-stored-verbatim",
        "note": "事实库修正后，本用例数组臂的 predicted 由 J1 按新事实重写：create-list 改 500 + {error 以 'Unexpected error during booking: ' 开头, message='The booking has been rolled back. Please try again.'}；read-list 无法读回（500 不返回 bookingId，id 也不可从任何列表接口得到），J1 应撤下这一步或改用 seat-after 承担『失败不留 CONFIRMED 预订』的可观察校验，并按需在 judge/uncovered.json 记一句该 CANCELLED 残留条目在单体接口下读不回来；seat-after 的 seatCapacity 改 4。本轮未改用例、未改事实库。",
        "create_float_arms_unchanged": "create-float / read-float 两步本轮无差异，标量 chargeId 那半条事实是对的"
      }
    ]
  },
  "previous_round_differences_status": [
    {
      "id": "register-read-back-by-admin/list/body.subset",
      "class": "needs_ruling",
      "this_round": "已消化：本轮出现在 reconcile.json 的 resolved_by_rulings（R-ROOM-spec-j3-r1-1/2、R-ROOM-spec-j3-r2-1/2、R-A1-a2-2/3/4 均 decided），不再在 differences 里，故未再写分类"
    }
  ],
  "files_written": [
    "judge/reconcile_classes.json",
    "
…（截断，原文 6932 字符）

## 完成方式
1. 写工作报告 WS/storage/stage_reports/J3.json（格式按你的角色说明）。
2. 调用 mcp__m2s-server__submit_task(summary=...) 提交，然后结束会话。不需要再输出 STAGE_COMPLETE。
3. 做不下去时，调用 mcp__m2s-server__report_blocked(reason, evidence, suspected_module)，然后结束会话；认为是更早阶段的问题时，在 suspected_module 写那个阶段（例如 S2），并在 evidence 里给出证据。
4. 调度器会自己运行这个任务的检查（reconcile_classified），你的自述不作为判定依据。

## 注意
- 如果调用工具或等待过程中遇到网络错误（例如 'Network error: Connection error'），重试 1-2 次再继续，不要因为一次网络错误就直接放弃或提前结束。
- 只改这个任务范围内的文件：judge/reconcile_classes.json、judge/mask_rules.json、storage/stage_reports/J3.json。范围之外的改动会被撤销，这次尝试判为失败。
- 不要修改流水线自己的工具：m2s-mcp-server/、.openharness/、~/.openharness/ 下的文件。
- 不要读写 WS/coord/（调度器的内部状态）。不要调用 mcp_auth。
- 不要读别的工作区（WS 所在 runs/ 目录下的其他目录、runs-prev-*），也不要参照它们的产物。
- 这份说明可以随时用 mcp__m2s-server__get_task_context 重新取回。