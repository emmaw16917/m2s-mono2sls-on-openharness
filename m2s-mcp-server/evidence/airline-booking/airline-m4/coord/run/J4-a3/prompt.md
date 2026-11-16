STAGE_ID: J4
MODE: J4

这是 m2s 迁移流水线的一个任务，由 v3 调度器派发；你的角色说明已经在系统提示词里。这是这个任务第 3 次尝试。

## 绝对路径
- MONOLITH = D:\Emma\MyLab\Mono2SLs\Mono2Sls-artifacts\mono_benchmark\airline-booking
- WS = D:\Emma\MyLab\Mono2SLs\Mono2Sls-artifacts\M2S\runs\airline-m4

## 任务
MODE: J4。白盒补测：可以读单体源码。按 judge/coverage_monolith/summary.json 里还没走到的行（第二轮起还有回炉信息里的 gap 变异体）补用例 judge/cases/<接口 slug>/wb-*.json；代码里有、事实库没有的规则写进 judge/spec_issues.json。按你的角色说明里"J4"一节做，用 mcp__m2s-server__check_judge_assets 自查到 PASS。

## 回炉信息（调度器转来的，请据此修正）

### 1. 来自 J5C
变异检验（J5）有这些变异体没被现有用例抓住，分类判为 gap（改动会改变外部行为）。为每一个补一个能看出差别的用例（`suggest` 是分类时的建议）：
[
 {
  "id": "negate:app.py:224",
  "operator": "negate",
  "file": "app.py",
  "line": 224,
  "before": "if payment_result:",
  "after": "if not (payment_result):",
  "refs": [
   "POST /bookings#business_rules[11]"
  ],
  "outcome": "survived",
  "reason": "app.py:224 是 catch-all 的退款开关。可达的 catch-all 路径里 payment_result 恒为 None（booking-missing-outbound-key 在 app.py:108 取 data['outboundFlightId'] 抛 KeyError；wb-booking-charge-id-non-string-stored-verbatim 的 create-list 在 services/payment.py:177 记账行抛 TypeError，两处都在 Step 3 赋值 payment_result 之前），所以取反后变异体反倒会调用 refund_payment(data['chargeId'])（app.py:227）。模拟模式退款把内存里那笔已收款条目的 status 改成 refunded（services/payment.py:276-277），而 GET /payments/<charge_id> 直接回读 _payments（services/payment.py:298 + app.py:349-353）：同一进程里该 chargeId 若先前收款成功过，单体读回是 services/payment.py:170 写的 status 'captured'，变异体给 'refunded'，外部可见。现有 3 个到 catch-all 的单元既没让 chargeId 与某笔已收款重合，也没有 GET /payments 读回步骤，故没抓到。",
  "suggest": "新用例 wb-booking-catchall-refunds-collected-charge（同进程 3 步）：1) POST /bookings {outboundFlightId: flight-sfo-lax, chargeId: 'pi_shared_catchall'} -> 201；2) POST /bookings 只带 {chargeId: 'pi_shared_catchall'}（缺 outboundFlightId）-> catch-all 500 {error 以 'Unexpected error during booking: ' 开头, message 'The booking has been rolled back. Please try again.'}（POST /bookings#responses[7]、business_rules[11]）；3) GET /payments/pi_shared_catchall（read_back）-> 单体 status 为 'captured'（这条收款没被退掉，GET /payments/<charge_id>#responses[0]）；变异体给 'refunded'。"
 }
]

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