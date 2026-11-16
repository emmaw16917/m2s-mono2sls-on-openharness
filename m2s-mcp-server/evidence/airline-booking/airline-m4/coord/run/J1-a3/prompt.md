STAGE_ID: J1
MODE: J1

这是 m2s 迁移流水线的一个任务，由 v3 调度器派发；你的角色说明已经在系统提示词里。这是这个任务第 3 次尝试。

## 绝对路径
- WS = D:\Emma\MyLab\Mono2SLs\Mono2Sls-artifacts\M2S\runs\airline-m4

## 任务
MODE: J1。只看事实库（用 mcp__m2s-server__judge_facts 读，不要整个打印 analysis_report.json，也不要读工具源码），写裁判用例：judge/cases/<接口 slug>/<用例>.json、judge/fixtures/、judge/faults/、judge/identities.json（需要时 judge/uncovered.json）。按你的角色说明里"J1"一节写；写完用 mcp__m2s-server__check_judge_assets 自查，直到 PASS。单体源码对你是关闭的。

## 回炉信息（调度器转来的，请据此修正）

### 1. 来自 J3
事实库已按下面这些差异修正。只更新这些用例的 predicted，使它符合修正后的事实库（重新读事实库），不改请求：
[
 {
  "id": "release-missing-or-at-max/ghost/body.equals",
  "case": "release-missing-or-at-max",
  "step": "ghost",
  "request": {
   "method": "POST",
   "path": "/flights/flight-ghost/release"
  },
  "check": "body.equals",
  "predicted": {
   "error": "Flight flight-ghost does not exist",
   "type": "ValueError"
  },
  "actual": {
   "error": "Failed to release seat: Flight flight-ghost does not exist",
   "type": "ValueError"
  },
  "fact_refs": [
   "POST /flights/<flight_id>/release#responses[1]",
   "POST /flights/<flight_id>/release#responses[2]",
   "POST /flights/<flight_id>/release#business_rules[2]",
   "POST /flights/<flight_id>/release#business_rules[3]",
   "POST /flights/<flight_id>/release#business_rules[4]",
   "GET /flights/<flight_id>#responses[0]"
  ],
  "reason": "事实库漏了错误消息的包装前缀。POST /flights/<flight_id>/release#responses[1] 的 body.description 写的是 \"error 是 'Flight <flight_id> does not exist'\"，business_rules[3] 也写 \"航班不存在时报 'Flight <id> does not exist'\"；单体实际返回 'Failed to release seat: Flight flight-ghost does not exist'。原因：data/storage.py:167-169 的 raise ValueError(f'Flight {flight_id} does not exist') 写在 release_flight_seat 的 try 块内部，被同函数 data/storage.py:185-186 的 except Exception as e: raise ValueError(f'Failed to release seat: {str(e)}') 兜住并重新包装，只有 ConditionalCheckFailedException 分支（:183-184，对应 responses[2]）能不带前缀冒出来——这也解释了同用例的 at-max 步没有差异。type='ValueError' 和 status=400 事实库是对的。",
  "fact_ref": "POST /flights/<flight_id>/release#responses[1]",
  "fix": "annotator 修正事实库：POST /flights/<flight_id>/release#responses[1] 的 body.description 与 business_rules[3] 里的 error 文案改为以 'Failed to release seat: ' 开头、后接 'Flight <flight_id> does not exist'（即完整串 'Failed to release seat: Flight <flight_id> does not exist'），出处 data/storage.py:167-169 与 :185-186；随后 J1 把 predicted['ghost']['body']['equals'] 的 error 更新为该完整串。受影响的只有 judge/cases/post-flights-flight-id-release/release-missing-or-at-max.json（该事实没被别的用例引用）。"
 }
]

规格对账（J3）判定下面这些用例写错了，按 reason 修改：
[
 {
  "id": "booking-body-and-field-failures/ghost-flight/body.subset",
  "case": "booking-body-and-field-failures",
  "step": "ghost-flight",
  "request": {
   "method": "POST",
   "path": "/bookings"
  },
  "check": "body.subset",
  "predicted": {
   "step": "Reserve Booking"
  },
  "actual": {
   "error": "Flight reservation failed: Flight flight-ghost is fully booked or does not exist",
   "step": "Reserve Flight"
  },
  "fact_refs": [
   "POST /bookings#responses[1]",
   "POST /bookings#responses[2]",
   "POST /bookings#responses[3]",
   "POST /bookings#responses[4]",
   "POST /bookings#business_rules[2]",
   "POST /bookings#business_rules[3]",
   "POST /bookings#business_rules[7]",
   "POST /bookings#business_rules[12]",
   "GET /flights/<flight_id>#responses[0]"
  ],
  "reason": "事实库没有错、单体也没有错，是用例把两条不同的 400 分支的预期写串了。POST /bookings#responses[2]（Step 1 扣座失败）明写 error 以 'Flight reservation failed: ' 开头、step='Reserve Flight'，POST /bookings#responses[3] 才是 step='Reserve Booking'。单体代码 app.py:103-113 在 Step 1 的 except ValueError 里返回 {'error': f'Flight reservation failed: {str(e)}', 'step': 'Reserve Flight'}（app.py:116-129 才是 'Reserve Booking'）。本步请求体是 {'outboundFlightId': 'flight-ghost', 'chargeId': 'pi_ghost'}，航班不存在，reserve_flight_seat 抛 ValueError（data/storage.py:158-159）在 Step 1 就被拦下，根本走不到 Step 2，所以实际输出 step='Reserve Flight' 与事实库一致。用例 predicted.ghost-flight 却写了 subset {'step': 'Reserve Booking'}（同一步的 status=400 与 has_fields ['error'] 都是对的，只有 step 值写错）。",
  "fix": "把 predicted['ghost-flight']['body']['subset'] 改成 {'step': 'Reserve Flight'}（fact_refs 已经含 POST /bookings#responses[2]，不用改请求）"
 },
 {
  "id": "booking-body-and-field-failures/full-flight/body.subset",
  "case": "booking-body-and-field-failures",
  "step": "full-flight",
  "request": {
   "method": "POST",
   "path": "/bookings"
  },
  "check": "body.subset",
  "predicted": {
   "step": "Reserve Booking"
  },
  "actual": {
   "error": "Flight reservation failed: Flight flight-full is fully booked or does not exist",
   "step": "Reserve Flight"
  },
  "fact_refs": [
   "POST /bookings#responses[1]",
   "POST /bookings#responses[2]",
   "POST /bookings#responses[3]",
   "POST /bookings#responses[4]",
   "POST /bookings#business_rules[2]",
   "POST /bookings#business_rules[3]",
   "POST /bookings#business_rules[7]",
   "POST /bookings#business_rules[12]",
   "GET /flights/<flight_id>#responses[0]"
  ],
  "reason": "同上，同一个用例的另一个分支：请求体是 {'outboundFlightId': 'flight-full', 'chargeId': 'pi_full'}，fixtures/flights.json 里 flight-full 的 seatCapacity 已为 0，条件 seatCapacity > :zero 失败，reserve_flight_seat 抛 ValueError('Flight flight-full is fully booked or does not exist')（data/storage.py:159-161），属于 POST /bookings#responses[2]：事实库明写该响应 error 以 'Flight reservation failed: ' 开头、step='Reserve Flight'；单体 app.py:112-113 就是这样返回的。'Reserve Booking' 是 responses[3]（Step 2 失败）的 step 值，用例把它错写到了 Step 1 的这一步。实际输出与事实库完全一致。",
  "fix": "把 predicted['full-flight']['body']['subset'] 改成 {'step': 'Reserve Flight'}"
 }
]

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