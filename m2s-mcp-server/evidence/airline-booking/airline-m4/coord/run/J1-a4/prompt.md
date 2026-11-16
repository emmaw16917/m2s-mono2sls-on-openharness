STAGE_ID: J1
MODE: J1

这是 m2s 迁移流水线的一个任务，由 v3 调度器派发；你的角色说明已经在系统提示词里。这是这个任务第 4 次尝试。

## 绝对路径
- WS = D:\Emma\MyLab\Mono2SLs\Mono2Sls-artifacts\M2S\runs\airline-m4

## 任务
MODE: J1。只看事实库（用 mcp__m2s-server__judge_facts 读，不要整个打印 analysis_report.json，也不要读工具源码），写裁判用例：judge/cases/<接口 slug>/<用例>.json、judge/fixtures/、judge/faults/、judge/identities.json（需要时 judge/uncovered.json）。按你的角色说明里"J1"一节写；写完用 mcp__m2s-server__check_judge_assets 自查，直到 PASS。单体源码对你是关闭的。

## 回炉信息（调度器转来的，请据此修正）

### 1. 来自 recheck
check_judge_assets 报告的问题：
POST /bookings/<booking_id>/cancel#responses[6] (400: 第三次读落空：装饰器（鉴权）和 handler 的 BookingService.get_booking 都读到了这条预订，BookingService.can) is not cited by any case: write a case whose steps reach it and cite it in fact_refs, or list it in judge/uncovered.json with the reason

上一次的工作报告全文：
{
  "stage_id": "J1",
  "agent": "m2s-verifier",
  "status": "success",
  "attempt": 3,
  "summary": "第 3 次尝试：按 J3 对账结论修 predicted，只改回炉信息点到的 2 个用例、3 个步骤，请求一字未动。1 条事实库修正（POST /flights/<flight_id>/release#responses[1] / business_rules[3] 的 error 文案带 'Failed to release seat: ' 包装前缀）→ 更新 release-missing-or-at-max 的 ghost 步 equals.error；2 条用例写错（booking-body-and-field-failures 的 ghost-flight / full-flight 把 Step 1 失败的 step 值写成了 'Reserve Booking'，事实库 POST /bookings#responses[2] 明写 'Reserve Flight'）→ 改成 'Reserve Flight'。资产总量不变：46 个用例、18 个接口全覆盖、8 份夹具、3 个身份、12 个故障场景、13 条 uncovered。check_judge_assets = PASS（errors 空、warnings 空）。",
  "cases": 46,
  "by_endpoint": {
    "GET /flights/search": 2,
    "GET /flights/<flight_id>": 1,
    "POST /flights/<flight_id>/reserve": 2,
    "POST /flights/<flight_id>/release": 2,
    "POST /bookings": 6,
    "GET /bookings/<booking_id>": 1,
    "POST /bookings/<booking_id>/confirm": 3,
    "POST /bookings/<booking_id>/cancel": 4,
    "GET /customers/<customer_id>/bookings": 2,
    "POST /payments/collect": 2,
    "POST /payments/refund": 2,
    "GET /payments/<charge_id>": 1,
    "GET /loyalty/<customer_id>": 3,
    "POST /loyalty/<customer_id>/points": 3,
    "POST /auth/register": 5,
    "POST /auth/login": 3,
    "GET /auth/me": 2,
    "GET /auth/users": 2
  },
  "layers": {
    "P": 44,
    "R": 2
  },
  "identities": [
    "u1",
    "u2",
    "admin"
  ],
  "identity_preparation": {
    "u1": "POST /auth/register(userId=cust-u1) + POST /auth/login → token=id(cust-u1)",
    "u2": "POST /auth/register(userId=cust-u2) + POST /auth/login → token=id(cust-u2)",
    "admin": "依赖夹具 admin-user（groups=['Admin'], password_hash=SHA-256('Adm1n!Pass')）+ POST /auth/login；单体没有授予 Admin 的接口，见 rework.basis。凡使用该别名的用例都必须声明 admin-user 夹具。"
  },
  "fixtures": 8,
  "fault_scenarios": 12,
  "uncovered_count": 13,
  "uncovered": [
    {
      "ref": "POST /flights/<flight_id>/reserve#responses[2]",
      "reason": "需要 update_item 抛出“非条件失败”的 DynamoDB 异常（表不存在/权限/服务端错），任何请求序列都造不出来；由故障场景 reserve-update-network / booking-create-reserve-fails 在故障基准里走这条臂。"
    },
    {
      "ref": "POST /flights/<flight_id>/release#responses[3]",
      "reason": "同上：只有给座位更新注入非条件异常才会走到；由故障场景 reserve-update-network / booking-cancel-release-seat-fails 覆盖。"
    },
    {
      "ref": "POST /bookings#responses[5]",
      "reason": "Step 4 确认失败要求预订在“收款成功之后、确认之前”被并发删除或写失败，正常请求序列无法制造这个时间窗；只有事务冲突/并发删除才走到，裁判运行是串行的，故标记未覆盖。"
    },
    {
      "ref": "GET /bookings/<booking_id>#responses[4]",
      "reason": "需要装饰器读到预订之后、handler 再读之前被并发删除；单步串行的用例无法制造这个两次读之间的窗口，storage 层也没有别的路径能让第二次读失败而第一次成功。"
    },
    {
      "ref": "POST /bookings/<booking_id>/confirm#responses[4]",
      "reason": "需要装饰器的存在性检查通过之后预订被并发删除（service 内第二次 get_booking 落空）；串行用例走不到。并发被删的臂由故障场景 booking-confirm-update-fails 走 responses[5]。"
    },
    {
      "ref": "POST /bookings/<booking_id>/confirm#responses[5]",
      "reason": "需要 attribute_exists 条件失败，即条目在鉴权读之后被并发删除，请求序列本身造不出来；故障场景 booking-confirm-update-fails 用条件失败注入来走同一条臂。"
    },
    {
      "ref": "POST /bookings/<booking_id>/cancel#responses[4]",
      "reason": "需要装饰器读过预订之后、handler 再读之前被并发删除，串行请求造不出来。"
    },
    {
      "ref": "POST /bookings/<booking_id>/cancel#responses[5]",
      "reason": "需要 notify_booking 在 customer 已存在的情况下抛异常（500）；预订条目总是带 customer（建单时由 sub 覆写），没有接口能把它去掉。"
    },
    {
      "ref": "POST /payments/collect#responses[3]",
      "reason": "需要配置 STRIPE_SECRET_KEY 且 Stripe 返回错误；裁判运行用默认环境（未配 Stripe），这一臂在本运行态下不可达。"
    },
    {
      "ref": "POST /payments/refund#responses[3]",
      "reason": "同上：只有配置了 STRIPE_SECRET_KEY 且 Stripe 退款报错才会走到。"
    },
    {
      "ref": "GET /payments/<charge_id>#responses[1]",
      "reason": "内存未命中后回落到 Stripe 查询，需要 STRIPE_SECRET_KEY；默认运行态不存在这一臂。"
    },
    {
      "ref": "POST /loyalty/<customer_id>/points#responses[5]",
      "reason": "需要 loyalty_table.put_item 写失败，正常请求序列造不出来；由故障场景 loyalty-points-put-fails 在故障基准里走这条臂。"
    },
    {
      "ref": "GET /auth/me#responses[2]",
      "reason": "需要用户表记录缺 email 键（KeyError → 500），而注册接口总会写 email，单体的接口也无法改/删用户记录的 email；只能直接改表，身份又只能用接口准备，故未覆盖。"
    }
  ],
  "coverage_notes": {
    "required_responses": "53/63 直接引用，13 条写进 judge/uncovered.json（含并发窗口、Stripe 未配置、非条件写异常等无法用请求序列制造的臂）",
    "optional_responses": "7/10 已覆盖（5xx 与 catch 出来的分支由故障场景走到）；仍开放的 3 条：POST /bookings#responses[5]、POST /bookings/<booking_id>/cancel#responses[5]、GET /auth/me#responses[2]，理由见 uncovered.json",
    "business_rules": "97/130 被用例引用",
    "read_back_cases": 24
  },
  "check_judge_assets": {
    "status": "PASS",
    "errors": [],
    "warnings": [],
    "rounds": 1
  },
  "questions": [
    {
      "ref": "POST /bookings#request.body[2]",
      "question": "chargeId 传空字符串时到底在 Step 2（validate_booking_request 是否看真值）还是 Step 3（collect_payment 抛 Invalid Charge ID）被拒，事实库两处说法不一致：business_rules[3] 说“缺任何一个”，request 注释说“为空假值时在 Step 3”。用例 booking-body-and-field-failures 的 empty-charge 步骤因此只断言 status=400 + error 字段存在，不断言 step/bookingId；两臂都回滚座位，座位读回断言不受影响。"
    },
    {
      "ref": "POST /bookings#responses[6]",
      "question": "notification 除 status/subject 外的完整字段事实库没写全，只断言 notification.status='confirmed'；bookingReference 是随机串，只断言存在。"
    },
    {
      "ref": "GET /flights/<flight_id>#responses[0]",
      "question": "数值属性“由 Decimal 还原成 int/float”没写清什么情况下是 float，夹具只用整数 ticketPrice（150/99）避开表示差异。"
    },
    {
      "ref": "POST /auth/register#responses[3]",
      "question": "get_json 抛的框架异常文案与 type 名随 Flask/Werkzeug 版本变化（事实库自己标注 framework-related），只断言 status=500 + 有 error/type 字段。POST /auth/login#responses[3] 同理。"
    },
    {
      "ref": "POST /payments/collect#responses[0]",
      "question": "模拟模式 200 响应用了 equals（事实库明说“两个字段”）；若单体实际多返回字段，这条 expected 会先在对账里暴露。GET /payments/<charge_id> 的内存记录用 subset。"
    },
    {
      "ref": "GET /auth/users#responses[2]",
      "question": "users 顺序事实库明确说不属对外承诺（business_rules[2]），因此 register-read-back-by-admin 标 layer R；表里只有 1 条用户时用的是逐字 P 比对（users-admin-list）。"
    },
    {
      "ref": "POST /auth/regi
…（截断，原文 11279 字符）

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