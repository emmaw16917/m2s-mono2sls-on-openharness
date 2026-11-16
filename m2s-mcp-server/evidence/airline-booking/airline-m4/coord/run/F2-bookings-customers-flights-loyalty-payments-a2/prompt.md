STAGE_ID: F2-bookings-customers-flights-loyalty-payments

这是 m2s 迁移流水线的一个任务，由 v3 调度器派发；你的角色说明已经在系统提示词里。这是这个任务第 2 次尝试。

## 绝对路径
- MONOLITH = D:\Emma\MyLab\Mono2SLs\Mono2Sls-artifacts\mono_benchmark\airline-booking
- WS = D:\Emma\MyLab\Mono2SLs\Mono2Sls-artifacts\M2S\runs\airline-m4

## 任务
STAGE_ID=F2-bookings-customers-flights-loyalty-payments：按你的角色说明完成这一阶段。

## 回炉信息（调度器转来的，请据此修正）

### 1. 来自 J3
规格对账（J3）发现事实库和单体的实际行为不一致（判为事实错误）。按单体代码核实，用 annotate_entry_point 修正下面这些事实（每条都要有出处），不要改动无关的事实；改完 check_provenance 必须 PASS。`predicted` 是按事实库推出的预期，`actual` 是单体的实际输出：
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