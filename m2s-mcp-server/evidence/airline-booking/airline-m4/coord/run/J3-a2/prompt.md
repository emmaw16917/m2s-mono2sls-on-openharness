STAGE_ID: J3
MODE: J3

这是 m2s 迁移流水线的一个任务，由 v3 调度器派发；你的角色说明已经在系统提示词里。这是这个任务第 2 次尝试。

## 绝对路径
- MONOLITH = D:\Emma\MyLab\Mono2SLs\Mono2Sls-artifacts\mono_benchmark\airline-booking
- WS = D:\Emma\MyLab\Mono2SLs\Mono2Sls-artifacts\M2S\runs\airline-m4

## 任务
MODE: J3。给 judge/reconcile.json 里的每一条差异（J1 按事实库写的 predicted 和单体实际输出对不上的地方）分类，写进 judge/reconcile_classes.json：fact_error / needs_ruling / case_error / mask_gap，每条写 reason（证据）。mask_gap 要同时在 judge/mask_rules.json 里补屏蔽规则。按你的角色说明里"J3"一节做。

## 回炉信息（调度器转来的，请据此修正）

### 1. 来自 recheck
judge/reconcile_classes.json 的问题：
register-read-back-by-admin/list/body.subset: not classified

上一次的工作报告全文：
{
  "stage_id": "J3",
  "agent": "m2s-verifier",
  "status": "success",
  "summary": "4 条对账差异全部分类：1 条 fact_error（release 400 的 error 文案漏了 'Failed to release seat: ' 包装前缀）、2 条 case_error（POST /bookings 的 Step 1 失败被写成 step='Reserve Booking'，事实库 responses[2] 是 'Reserve Flight'）、1 条 needs_ruling（GET /auth/users 的 users 数组顺序，事实库 business_rules[2] 说顺序不属于对外承诺，而用例格式无法表达无序集合）。无 mask_gap，未新增 judge/mask_rules.json。check_reconcile_classes = PASS。",
  "differences_total": 4,
  "by_class": {
    "fact_error": [
      "release-missing-or-at-max/ghost/body.equals"
    ],
    "needs_ruling": [
      "register-read-back-by-admin/list/body.subset"
    ],
    "case_error": [
      "booking-body-and-field-failures/ghost-flight/body.subset",
      "booking-body-and-field-failures/full-flight/body.subset"
    ],
    "mask_gap": []
  },
  "mask_rules_added": 0,
  "self_check": {
    "tool": "mcp__m2s-server__check_reconcile_classes",
    "status": "PASS",
    "classes": {"fact_error": 1, "needs_ruling": 1, "case_error": 2, "mask_gap": 0}
  },
  "handoff": {
    "needs_ruling": [
      {
        "question": "GET /auth/users 的 users 数组元素顺序是否属于对外契约（迁移后目标不按 DynamoDB scan 原序返回是否算行为一致）",
        "suggested_category": "contract",
        "suggested_relaxation": "order",
        "suggested_layers": ["R"],
        "affected_cases": ["register-read-back-by-admin"],
        "basis": [
          "GET /auth/users#business_rules[2]（不排序，顺序就是 scan 返回顺序，不属于对外承诺）",
          "data/storage.py:352-356（只有 users_table.scan()，取 Items）",
          "services/auth.py:140-150（脱敏推导式保持原顺序，无 sorted）"
        ],
        "why_not_case_error": "响应体是对象时 predicted 只有 equals/subset/contains/has_fields/length；contains 只在响应本身是数组时生效，这里的数组嵌在 {'users': [...]} 里，无法表达\"两个用户都在、先后不管\"，退化成 has_fields ['users'] 会丢掉 read_back 要验证的内容。"
      }
    ],
    "fact_error_annotator": [
      {
        "fact_ref": "POST /flights/<flight_id>/release#responses[1]",
        "also": "POST /flights/<flight_id>/release#business_rules[3]",
        "change": "error 文案改为 'Failed to release seat: Flight <flight_id> does not exist'（storage.py:169 在 try 内抛，被 :185-186 的 except Exception 重新包装；:183-184 的 ConditionalCheckFailedException 分支不带前缀，所以 responses[2] 是对的）",
        "affected_cases": ["release-missing-or-at-max"],
        "then": "J1 回炉更新 predicted['ghost']['body']['equals'].error"
      }
    ],
    "case_error_j1_rework": [
      {
        "case": "booking-body-and-field-failures",
        "steps": ["ghost-flight", "full-flight"],
        "change": "predicted[step]['body']['subset'] 从 {'step': 'Reserve Booking'} 改为 {'step': 'Reserve Flight'}",
        "basis": "POST /bookings#responses[2]（error 以 'Flight reservation failed: ' 开头、step='Reserve Flight'）；单体 app.py:103-113",
        "note": "status=400 与 has_fields ['error'] 无需改动；请求与 fact_refs 保持原样。"
      }
    ]
  },
  "issues": [],
  "notes": "只写了 judge/reconcile_classes.json 与本报告；未修改用例、事实库或 judge/mask_rules.json（无 mask_gap）。行号引用来自单体源码 data/storage.py、services/auth.py、app.py 与 judge/fixtures/*.json 的核对。"
}

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