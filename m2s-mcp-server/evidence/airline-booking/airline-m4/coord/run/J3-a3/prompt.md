STAGE_ID: J3
MODE: J3

这是 m2s 迁移流水线的一个任务，由 v3 调度器派发；你的角色说明已经在系统提示词里。这是这个任务第 3 次尝试。

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
  "attempt": 2,
  "status": "success",
  "summary": "本轮 judge/reconcile.json 只剩 1 条差异（register-read-back-by-admin/list/body.subset，上一轮的 4 条里 1 条 fact_error 与 2 条 case_error 已由 annotator 修事实库、J1 改用例消化，未再出现）。已分类为 needs_ruling：差异只在 users 数组的元素顺序，事实库 GET /auth/users#business_rules[2] 明写『顺序不属于对外承诺』，单体 data/storage.py:352-356 只有 scan、services/auth.py:139-150 无排序，用例也已按规则标 layer=R；当前裁判用例格式无法表达『嵌套数组两元素都在、先后不限』（subset 对数组逐位匹配、contains 只作用于响应体本身是数组），故需 architect 落一条 order 放宽裁定（category=contract, relaxation=order, layers=[R], deviates_from_monolith=false），裁定后本差异应进入 reconcile.resolved_by_rulings。上一轮的文件在本次运行中缺失（recheck 报 not classified），已按当前差异清单重建 judge/reconcile_classes.json。无 mask_gap，未新增 judge/mask_rules.json。check_reconcile_classes = PASS。",
  "differences_total": 1,
  "by_class": {
    "fact_error": [],
    "needs_ruling": [
      "register-read-back-by-admin/list/body.subset"
    ],
    "case_error": [],
    "mask_gap": []
  },
  "mask_rules_added": 0,
  "self_check": {
    "tool": "mcp__m2s-server__check_reconcile_classes",
    "status": "PASS",
    "classes": {"fact_error": 0, "needs_ruling": 1, "case_error": 0, "mask_gap": 0}
  },
  "handoff": {
    "needs_ruling": [
      {
        "question": "GET /auth/users 响应里 users 数组的元素顺序是否属于对外契约（迁移后目标不按 DynamoDB scan 原序返回是否算行为一致）",
        "suggested_category": "contract",
        "suggested_relaxation": "order",
        "suggested_layers": ["R"],
        "deviates_from_monolith": false,
        "affected_cases": ["register-read-back-by-admin"],
        "affected_steps": ["register-read-back-by-admin/list"],
        "basis": [
          "GET /auth/users#business_rules[2]（不排序、不分页、不接受查询参数；条目顺序就是 DynamoDB scan 的返回顺序，顺序不属于对外承诺）",
          "GET /auth/users#responses[2]（users 每项 {sub,email,groups}，无排序或分页字段）",
          "data/storage.py:352-356（get_all_users 只有 users_table.scan() 并返回 Items，无排序）",
          "services/auth.py:139-150（脱敏推导式逐条映射，保持原顺序，无 sorted）",
          "judge/fixtures/admin-user.json + register 步骤：两遍运行的元素集合一致，只有 cust-admin / cust-reg-3 先后不同（顺序由哈希键分布决定）"
        ],
        "why_not_case_error": "predicted 词汇里响应体是对象时只有 equals / subset / contains / has_fields / length；subset 对数组要求等长并逐位置递归包含，contains 仅在响应体本身是数组时生效，has_fields 只能用 users.0.sub 这类位置路径（仍钉顺序）。改成 has_fields:[\"users\"] 或只留一个元素会丢掉 read_back 要验证的内容，等于为实现放宽断言。用例也已按 J1 规则标 layer=\"R\"，说明写用例侧认为顺序不判定。",
        "why_not_fact_error": "事实库没有写错：它已经声明顺序不属于对外承诺，单体实际返回的元素与 predicted 完全一致，只是次序不同。",
        "why_not_mask_gap": "屏蔽规则处理逐遍变化的值；这里两遍取值相同、只是次序不同，加屏蔽既解决不了差异又会掩盖真实变化。",
        "expected_effect_of_ruling": "V2 在 R 层按无序集合比较该数组（长度与逐元素字段仍逐字比较）；对账重跑后本差异进入 meta/差异清单的 resolved_by_rulings，不再是未消化差异。",
        "fallback_if_ruling_rejects_relaxation": "若裁定认定顺序属于契约，则与 GET /auth/users#business_rules[2] 矛盾，应改判 fact_error 交 annotator 修正事实库，并由 J1 按 scan 实际顺序更新 predicted。",
        "related_room": "spec-j3-r1（上一轮已以 m2s-verifier 发言支持 needs_ruling 并建议该放宽裁定）"
      }
    ],
    "fact_error_annotator": [],
    "case_error_j1_rework": []
  },
  "previous_round_differences_status": [
    {"id": "release-missing-or-at-max/ghost/body.equals", "class": "fact_error", "this_round": "未再出现（事实库已由 annotator 修正或 predicted 已更新）"},
    {"id": "booking-body-and-field-failures/ghost-flight/body.subset", "class": "case_error", "this_round": "未再出现（J1 回炉已把 step 改为 'Reserve Flight'）"},
    {"id": "booking-body-and-field-failures/full-flight/body.subset", "class": "case_error", "this_round": "未再出现（同上）"},
    {"id": "register-read-back-by-admin/list/body.subset", "class": "needs_ruling", "this_round": "仍在，本轮重新分类为 needs_ruling"}
  ],
  "issues": [],
  "notes": "本轮只写 judge/reconcile_classes.json 与本报告；未改用例、未改事实库、未新增 judge/mask_rules.json（无 mask_gap），未读 storage/analysis_report.json 全文（用 judge_facts 取 GET /auth/users 一项）。行号来自单体 data/storage.py、services/auth.py 与 judge/fixtures/admin-user.json、judge/cases/post-auth-register/register-read-back-by-admin.json 的核对。"
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