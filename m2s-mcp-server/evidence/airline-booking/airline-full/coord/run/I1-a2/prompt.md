STAGE_ID: I1

这是 m2s 迁移流水线的一个任务，由 v3 调度器派发；你的角色说明已经在系统提示词里。这是这个任务第 2 次尝试。

## 绝对路径
- MONOLITH = D:\Emma\MyLab\Mono2SLs\Mono2Sls-artifacts\mono_benchmark\airline-booking
- WS = D:\Emma\MyLab\Mono2SLs\Mono2Sls-artifacts\M2S\runs\airline-full

## 任务
按 blueprint 写 output/lambdas/<domain>/<name>/（角色说明里的 S2）：handler 和依赖声明，需要时写 output/layers/shared/；写完跑 run_gate(phase="code") 自查并修正，再按"用裁判自测"一节用 mcp__m2s-server__run_judge_self_test 对自己的函数跑裁判用例。

需要知道"要保持哪些行为"时，只引用规格本身：analysis_report.json 的出处、blueprint.json 的字段（包括 metadata.spec_resolutions）。

## 回炉信息（调度器转来的，请据此修正）

### 1. 来自 checks
上一次尝试改了任务范围之外的文件，已被撤销：['storage/stage_reports/S2.json']。只改这些范围内的文件：['output/lambdas/**', 'output/layers/**', 'storage/DECISIONS.md', 'storage/stage_reports/I1.json']。

上一次的工作报告全文：
{
  "stage_id": "I1",
  "agent": "m2s-code-developer",
  "status": "success",
  "summary": "Generated 15 Python 3.12 Lambda packages under output/lambdas/airline/<name>/ (handler.py plus per-package storage.py/services.py/payments.py), one per blueprint lambda_functions entry, covering exactly their entry_points. No dependency manifests: only stdlib + runtime-built-in boto3 are used, so no requirements.txt/package.json was created and no shared layer directory exists (the blueprint defines none). run_gate(phase='code') PASS. run_judge_self_test: 42/42 cases and 23/23 fault runs pass (7 of the cases are the expected migration-change class).",
  "produced_files": [
    "output/lambdas/airline/booking-cancel/handler.py",
    "output/lambdas/airline/booking-cancel/payments.py",
    "output/lambdas/airline/booking-cancel/services.py",
    "output/lambdas/airline/booking-cancel/storage.py",
    "output/lambdas/airline/booking-confirm/handler.py",
    "output/lambdas/airline/booking-confirm/services.py",
    "output/lambdas/airline/booking-confirm/storage.py",
    "output/lambdas/airline/booking-get/handler.py",
    "output/lambdas/airline/booking-get/services.py",
    "output/lambdas/airline/booking-get/storage.py",
    "output/lambdas/airline/bookings-create/handler.py",
    "output/lambdas/airline/bookings-create/payments.py",
    "output/lambdas/airline/bookings-create/services.py",
    "output/lambdas/airline/bookings-create/storage.py",
    "output/lambdas/airline/customer-bookings-list/handler.py",
    "output/lambdas/airline/customer-bookings-list/services.py",
    "output/lambdas/airline/customer-bookings-list/storage.py",
    "output/lambdas/airline/flight-get/handler.py",
    "output/lambdas/airline/flight-get/storage.py",
    "output/lambdas/airline/flight-release/handler.py",
    "output/lambdas/airline/flight-release/storage.py",
    "output/lambdas/airline/flight-reserve/handler.py",
    "output/lambdas/airline/flight-reserve/storage.py",
    "output/lambdas/airline/flights-search/handler.py",
    "output/lambdas/airline/flights-search/storage.py",
    "output/lambdas/airline/loyalty-add-points/handler.py",
    "output/lambdas/airline/loyalty-add-points/services.py",
    "output/lambdas/airline/loyalty-add-points/storage.py",
    "output/lambdas/airline/loyalty-award-points/handler.py",
    "output/lambdas/airline/loyalty-get/handler.py",
    "output/lambdas/airline/loyalty-get/services.py",
    "output/lambdas/airline/loyalty-get/storage.py",
    "output/lambdas/airline/payment-get/handler.py",
    "output/lambdas/airline/payment-get/payments.py",
    "output/lambdas/airline/payment-get/storage.py",
    "output/lambdas/airline/payments-collect/handler.py",
    "output/lambdas/airline/payments-collect/payments.py",
    "output/lambdas/airline/payments-collect/storage.py",
    "output/lambdas/airline/payments-refund/handler.py",
    "output/lambdas/airline/payments-refund/payments.py",
    "output/lambdas/airline/payments-refund/storage.py"
  ],
  "issues": [
    "advisory / self-test harness: in run_judge_self_test the BlueprintModel passes the monolith's Flask-style route templates (resource='/flights/<flight_id>' etc.) to judge-run.js's pathParams(), which only understands '{param}' / ':param', so the constructed API Gateway events had pathParameters=null and every parameterized step failed (40/42) regardless of the code. To keep the self test meaningful I added a _path_params() helper to the 10 handlers with path parameters: when pathParameters is absent it derives the same values from event.resource and event.path. Real API Gateway always supplies pathParameters, so that branch is dead in production, and V2 (template.yaml uses '{param}') is unaffected. Recommended fix on the harness side: normalize <param> to {param> in BlueprintModel or judge-run.js; the fallback can then be removed.",
    "design note: the payments ledger follows SI-14/D-8 - PaymentsTable keyed by '<python type name>:<str(value)>'. Unhashable charge ids (list/dict) raise an unwrapped TypeError at key generation, matching the monolith's dict-key semantics (SI-8, R-A1-a3-3); the uniform error exit maps non-ValueError exceptions to 500 {error,type} (GAP-11/D-11).",
    "design note: loyalty-award-points is idempotent by construction - the message's bookingId is used as the LoyaltyTable primary key and the whole row is written with a plain PutItem (D-5/SI-9), so redelivery rewrites the same item and totals never double. Permanent payload faults are logged and swallowed; throttle/network/internal and TransactionCanceled are reported through batchItemFailures (the template must set ReportBatchItemFailures, MaxReceiveCount=3 and the DLQ - S3's scope).",
    "design note: condition-failure detection matches ConditionalCheckFailedException (real DynamoDB, DynamoDB Local and the injected kind='conditional' shape) as well as a TransactionCanceledException whose reasons name ConditionalCheckFailed; a plain TransactionConflict never enters the condition-failure branch."
  ]
}

## 完成方式
1. 写工作报告 WS/storage/stage_reports/I1.json（格式按你的角色说明）。
2. 调用 mcp__m2s-server__submit_task(summary=...) 提交，然后结束会话。不需要再输出 STAGE_COMPLETE。
3. 做不下去时，调用 mcp__m2s-server__report_blocked(reason, evidence, suspected_module)，然后结束会话；认为是更早阶段的问题时，在 suspected_module 写那个阶段（例如 S2），并在 evidence 里给出证据。
4. 调度器会自己运行这个任务的检查（gate_code, judge_unchanged），你的自述不作为判定依据。

## 注意
- 如果调用工具或等待过程中遇到网络错误（例如 'Network error: Connection error'），重试 1-2 次再继续，不要因为一次网络错误就直接放弃或提前结束。
- 只改这个任务范围内的文件：output/lambdas/**、output/layers/**、storage/DECISIONS.md、storage/stage_reports/I1.json。范围之外的改动会被撤销，这次尝试判为失败。
- 不要修改流水线自己的工具：m2s-mcp-server/、.openharness/、~/.openharness/ 下的文件。
- 不要读写 WS/coord/（调度器的内部状态）。不要调用 mcp_auth。
- 不要读别的工作区（WS 所在 runs/ 目录下的其他目录、runs-prev-*），也不要参照它们的产物。
- 这份说明可以随时用 mcp__m2s-server__get_task_context 重新取回。