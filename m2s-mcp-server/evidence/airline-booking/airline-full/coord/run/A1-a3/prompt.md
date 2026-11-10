STAGE_ID: A1

这是 m2s 迁移流水线的一个任务，由 v3 调度器派发；你的角色说明已经在系统提示词里。这是这个任务第 3 次尝试。

## 绝对路径
- MONOLITH = D:\Emma\MyLab\Mono2SLs\Mono2Sls-artifacts\mono_benchmark\airline-booking
- WS = D:\Emma\MyLab\Mono2SLs\Mono2Sls-artifacts\M2S\runs\airline-full

## 任务
按规格设计 serverless 架构（角色说明里的 S1）：用 mcp__m2s-server__merge_blueprint_key 逐个顶层字段写 storage/blueprint.json，设计决定记进 storage/DECISIONS.md。还有三项：storage/adapter_map.json（"适配映射"一节）、storage/gap_inventory.json 的每条决定（"缺口决定"一节）、迁移带来的对外变化写成裁定（"迁移带来的对外变化"一节）。写完跑 run_gate(phase="blueprint") 自查并修正。

需要知道"要保持哪些行为"时，只引用规格本身：analysis_report.json 的出处、blueprint.json 的字段（包括 metadata.spec_resolutions）。

## 回炉信息（调度器转来的，请据此修正）

### 1. 来自 checks
上一次尝试改了任务范围之外的文件，已被撤销：['tmp_sqs.json']。只改这些范围内的文件：['storage/blueprint.json', 'storage/DECISIONS.md', 'storage/adapter_map.json', 'storage/gap_inventory.json', 'storage/stage_reports/A1.json']。

上一次的工作报告全文：
{
  "stage_id": "A1",
  "agent": "m2s-architect",
  "attempt": 2,
  "status": "success",
  "summary": "完成 airline-booking 架构蓝图：14 个 API Lambda 加 1 个 SQS 消费者，Cognito 接管认证，5 张 DynamoDB 表，1 个幂等 SQS 队列，4 个 /auth 端点及相关认证代码删除。第 2 次尝试补齐 23 条缺口、适配映射及迁移/规格裁定。gate(blueprint) PASS。",
  "produced_files": [
    "storage/blueprint.json",
    "storage/DECISIONS.md",
    "storage/adapter_map.json",
    "storage/gap_inventory.json",
    "storage/stage_reports/A1.json"
  ],
  "checks": {
    "gate_blueprint": "PASS",
    "check_adapter_map": "PASS",
    "check_gap_inventory_require_decided": "PASS (23/23 decided)",
    "check_provenance": "PASS",
    "gate_blueprint_after_metadata_fix": "PASS"
  },
  "issues": [
    "R-A1-a2-1: protected GET /bookings unauthenticated steps now gateway 401 {message}.",
    "R-A1-a2-2: all seven protected-endpoint anonymous-case sets now gateway 401 {message}.",
    "R-A1-a2-3: four /auth endpoints are dropped and replaced by Cognito APIs.",
    "R-A1-a2-4: POST /bookings outboundFlightId has no type validation.",
    "R-A1-a2-5: POST /bookings chargeId preserves monolith type-tagged ledger key semantics.",
    "R-A1-a2-6: POST /bookings optional name field is ignored.",
    "Metadata notes and SI-9 now use the actual consumer name loyalty-award-points."
  ],
  "correctness_notes": {
    "async_idempotency": "LoyaltyPointsQueue consumer writes the whole LoyaltyTable item with deterministic primary key id=bookingId, so redelivery is a harmless overwrite.",
    "cross_lambda": "No synchronous Lambda-to-Lambda calls; one producer/consumer SQS edge only."
  }
}

## 完成方式
1. 写工作报告 WS/storage/stage_reports/A1.json（格式按你的角色说明）。
2. 调用 mcp__m2s-server__submit_task(summary=...) 提交，然后结束会话。不需要再输出 STAGE_COMPLETE。
3. 做不下去时，调用 mcp__m2s-server__report_blocked(reason, evidence, suspected_module)，然后结束会话；认为是更早阶段的问题时，在 suspected_module 写那个阶段（例如 S2），并在 evidence 里给出证据。
4. 调度器会自己运行这个任务的检查（blueprint_keys, gate_blueprint, judge_unchanged），你的自述不作为判定依据。

## 注意
- 如果调用工具或等待过程中遇到网络错误（例如 'Network error: Connection error'），重试 1-2 次再继续，不要因为一次网络错误就直接放弃或提前结束。
- 只改这个任务范围内的文件：storage/blueprint.json、storage/DECISIONS.md、storage/adapter_map.json、storage/gap_inventory.json、storage/stage_reports/A1.json。范围之外的改动会被撤销，这次尝试判为失败。
- 不要修改流水线自己的工具：m2s-mcp-server/、.openharness/、~/.openharness/ 下的文件。
- 不要读写 WS/coord/（调度器的内部状态）。不要调用 mcp_auth。
- 不要读别的工作区（WS 所在 runs/ 目录下的其他目录、runs-prev-*），也不要参照它们的产物。
- 这份说明可以随时用 mcp__m2s-server__get_task_context 重新取回。