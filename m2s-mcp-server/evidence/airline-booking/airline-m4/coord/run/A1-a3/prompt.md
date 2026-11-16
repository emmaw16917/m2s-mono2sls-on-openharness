STAGE_ID: A1

这是 m2s 迁移流水线的一个任务，由 v3 调度器派发；你的角色说明已经在系统提示词里。这是这个任务第 3 次尝试。

## 绝对路径
- MONOLITH = D:\Emma\MyLab\Mono2SLs\Mono2Sls-artifacts\mono_benchmark\airline-booking
- WS = D:\Emma\MyLab\Mono2SLs\Mono2Sls-artifacts\M2S\runs\airline-m4

## 任务
按规格设计 serverless 架构（角色说明里的 S1）：用 mcp__m2s-server__merge_blueprint_key 逐个顶层字段写 storage/blueprint.json，设计决定记进 storage/DECISIONS.md。还有三项：storage/adapter_map.json（"适配映射"一节）、storage/gap_inventory.json 的每条决定（"缺口决定"一节）、迁移带来的对外变化写成裁定（"迁移带来的对外变化"一节）。写完跑 run_gate(phase="blueprint") 自查并修正。

需要知道"要保持哪些行为"时，只引用规格本身：analysis_report.json 的出处、blueprint.json 的字段（包括 metadata.spec_resolutions）。

## 回炉信息（调度器转来的，请据此修正）

### 1. 来自 recheck
run_gate(phase="blueprint") 报告：
{
 "status": "FAIL",
 "errors": [
  {
   "check_id": "CT3",
   "severity": "ERROR",
   "description": "POST /bookings/<booking_id>/cancel#responses[6] is marked trace_arm but no implementation-trace spec_resolution targets it (void it, or give its allowed outcomes).",
   "affected_resource": "blueprint.json"
  }
 ],
 "mechanical_failures": [
  {
   "check_id": "CT3",
   "severity": "ERROR",
   "description": "POST /bookings/<booking_id>/cancel#responses[6] is marked trace_arm but no implementation-trace spec_resolution targets it (void it, or give its allowed outcomes).",
   "affected_resource": "blueprint.json"
  }
 ]
}

上一次的工作报告全文：
{
  "stage_id": "A1",
  "agent": "m2s-architect",
  "status": "success",
  "summary": "按规格设计出 airline-booking 的 serverless 架构：18 个端点中 14 个各建一个 API Lambda（python3.12，auth 逐端点采用 auth.classification：7 required / 7 none），4 个 /auth/* 端点 + 鉴权中间件/令牌服务/建表脚本/导入期种子共 15 项进 dropped_functions；5 张表（Flight/Booking/Loyalty 按 table_schemas 原样 + PaymentTable 承接进程内收款状态 + UserGroupsTable 承接 Users 表拆分出的组归属），Cognito 用户池 + HTTP API(payload 1.0) JWT 授权器，零跨 Lambda 通信（sqs/eventbridge/invoke 均空，故幂等强制规则无适用对象）。adapter_map、22 条 gap 决定、work_packages.impl_packages 全部写完并自查通过；4 条裁定落库。gate(blueprint)=PASS（1 次尝试），check_adapter_map=PASS、check_gap_inventory(require_decided)=PASS。",
  "produced_files": [
    "storage/blueprint.json",
    "storage/DECISIONS.md",
    "storage/adapter_map.json",
    "storage/gap_inventory.json",
    "storage/work_packages.json",
    "storage/rulings.json",
    "storage/stage_reports/A1.json"
  ],
  "gate": {
    "phase": "blueprint",
    "status": "PASS",
    "attempts": 1,
    "mechanical_failures": [],
    "suspects": {}
  },
  "other_checks": {
    "check_adapter_map": "PASS",
    "check_gap_inventory": {"status": "PASS", "gaps": 22, "open": []},
    "spec_ref_scan": "SI 与函数 notes 里引用的 128 处 #responses[i]/#business_rules[j] 全部回查 analysis_report 存在（脚本核对，0 处越界）",
    "decisions_md": "14 个函数名全部出现在某条决策里（B0 亦核对）"
  },
  "key_decisions": {
    "D-1": "一 endpoint 一 Lambda：flights-search / flights-get / flights-reserve / flights-release / bookings-create / bookings-get / bookings-confirm / bookings-cancel / customers-bookings-list / payments-collect / payments-refund / payments-get / loyalty-get / loyalty-add-points",
    "D-2": "api_type=http + payload 1.0 + clients=internet；未使用任何 REST 独有功能（global_middleware 只有 ACAO:*），已在 DECISIONS 记录 HTTP API JWT 授权器同时接受 access token 的后果",
    "D-3": "认证基建化：Cognito 替代，POST /auth/register、POST /auth/login、GET /auth/me、GET /auth/users 删除；判定这不是『单体自管会话』例外（令牌是无状态 HS256 JWT、落库的是凭据记录而非会话记录，GAP-12 的每进程随机密钥不可迁）",
    "D-4": "Users 表走『拆分』：email/password_hash/created_at 不迁（凭据归 Cognito），groups 留成 UserGroupsTable(sub, groups)，因为规格明写 Admin 判定看库里当前记录，且裁判的 admin 身份只由 admin-user 夹具准备",
    "D-5": "7 个 required / 7 个 none 逐端点等于 auth.classification，无一处偏离；403/404 文案逐字保持，401 交网关",
    "D-6": "无跨 Lambda 通信：bookings-create 的六步 Saga（含忠诚分写入）同步内联，保住『201 时积分已到账』（booking-happy-path/loyalty-after 断言 points=150），并避免给单体明确要求可见的重复加分（business_rules[13]）套上幂等表",
    "D-11": "PaymentService._payments 迁成 PaymentTable（GAP-5 规则手册默认做法）；_refunds 无读取方不建表；Stripe 分支不迁（SI-7）",
    "D-14": "契约 2 开启；4 条 trace_arm 全部裁定（SI-1 作废三条双读臂，SI-2 保留 confirm 条件写失败臂并钉死 400）；反向纠正 3 条 payments 的 class 误标",
    "D-18": "落 4 条裁定：R-A1-a2-1（网关 401 body，精确到 9 个 case#step）、R-A1-a2-2（/auth/* 与身份准备机制，12 个整案）、R-A1-a2-3（order 放宽，与在案 ROOM 裁定重复）、R-A1-a2-4（更正：作废 a2-3 多出的两个裸用例 id，放宽唯一范围仍是 register-read-back-by-admin#list）"
  },
  "issues": [
    "SI-1/SI-2（implementation-trace）：GET /bookings/<booking_id>#responses[4]、POST /bookings/<booking_id>/confirm#responses[4]、POST /bookings/<booking_id>/cancel#responses[4] 三条『两次读之间被并发删除』的臂作废（目标各只读一次并复用条目）；confirm#responses[5]（条件写失败）不作废，结果钉死为 400 {'error': 'Booking <id> not found', 'type': 'ValueError'}，因为故障场景 booking-confirm-update-fails 依赖这一臂。",
    "SI-3：POST /bookings 缺 outboundFlightId 键时保持单体的 500（KeyError 落进 catch-all，body 带 message='The booking has been rolled back. Please try again.'），不修成 400 —— 这是单体缺陷，按 Q10 照搬。",
    "SI-4：chargeId 键存在但为空假值时的拒单点裁定为 Step 3（先写 UNCONFIRMED 预订，再 400 'Payment failed: Invalid Charge ID' 带 bookingId 并回滚），回答了 J1 提出的事实库两处说法不一致。",
    "SI-5：notification 的完整字段裁定为 {notificationId, customerId, price, status, subject}（建单 status='confirmed'/subject='Booking confirmed for <reference>'，取消 status='cancelled'/price=0/subject='Booking cancelled for most recent booking'）。",
    "SI-6/SI-7：payments 的『状态放进程内存』判为实现轨迹（迁 PaymentTable），三条 Stripe 臂判为 migration-change（不迁外部依赖，目标恒走模拟分支）。",
    "SI-8/SI-9/SI-10：认证平台更换带来的三处对外变化已裁定（端点删除、401 body 形状、'sub 查不到就 401' 不再复刻），403 文案与 Admin 判定的实时性保持逐字一致。",
    "SI-11/SI-12/SI-13/SI-14/SI-15/SI-16/SI-17/SI-18：种子数据去掉、Scan 不翻页不加索引、吞异常降级照搬、CORS 头由函数自己带（HTTP API 无 GatewayResponses）、时间戳裸 ISO 形状、Decimal→int/float、不加幂等/去重/状态机校验、取消的补偿不原子——均给出可判定结论。",
    "GAP-3/GAP-4/GAP-7/GAP-8/GAP-12/GAP-14/GAP-15 的 test 选 none 并写明理由（截断要 >1MB 才可见；运维脚本不在请求路径；/auth/* 不提供故无函数行为可测；裁判每用例先清表再播种，导入期种子在记录里本就不存在），其余 15 条挂 judge_cases。",
    "work_packages.impl_packages：identity 包 0 个函数（4 个端点全部由 Cognito 替代），bookings-customers-flights-loyalty-payments 包 14 个函数、dir=bookings_flights；共享的 owner/admin 判定与响应/CORS 助手落在共用 layer，不列进包。",
    "给 S2/S3 的两个易错点：(1) 函数里不写任何令牌校验代码，但 authorization 判定（Admin/owner）必须在函数内并读 UserGroupsTable，不要把它当成网关职责漏掉，也不要改读 claims 里的 cognito:groups；(2) 每个响应都要带 Access-Control-Allow-Origin: *，模板的 Cors 只生成预检。",
    "本次尝试未修改 judge/ 任何文件；未修改 output/；未修改流水线工具（m2s-mcp-server/、.openharness/、~/.openharness/）。首次 run_gate 前的两处自我修正（补 bookings-create 的 entry_points、去掉 bookings-cancel 无用的 LOYALTY_TABLE_NAME）通过 merge_blueprint_key(mode=replace) 完成。"
  ]
}

## 完成方式
1. 写工作报告 WS/storage/stage_reports/A1.json（格式按你的角色说明）。
2. 调用 mcp__m2s-server__submit_task(summary=...) 提交，然后结束会话。不需要再输出 STAGE_COMPLETE。
3. 做不下去时，调用 mcp__m2s-server__report_blocked(reason, evidence, suspected_module)，然后结束会话；认为是更早阶段的问题时，在 suspected_module 写那个阶段（例如 S2），并在 evidence 里给出证据。
4. 调度器会自己运行这个任务的检查（blueprint_keys, gate_blueprint, judge_unchanged），你的自述不作为判定依据。

## 注意
- 如果调用工具或等待过程中遇到网络错误（例如 'Network error: Connection error'），重试 1-2 次再继续，不要因为一次网络错误就直接放弃或提前结束。
- 只改这个任务范围内的文件：storage/blueprint.json、storage/DECISIONS.md、storage/adapter_map.json、storage/gap_inventory.json、storage/work_packages.json、storage/stage_reports/A1.json。范围之外的改动会被撤销，这次尝试判为失败。
- 不要修改流水线自己的工具：m2s-mcp-server/、.openharness/、~/.openharness/ 下的文件。
- 不要读写 WS/coord/（调度器的内部状态）。不要调用 mcp_auth。
- 不要读别的工作区（WS 所在 runs/ 目录下的其他目录、runs-prev-*），也不要参照它们的产物。
- 这份说明可以随时用 mcp__m2s-server__get_task_context 重新取回。