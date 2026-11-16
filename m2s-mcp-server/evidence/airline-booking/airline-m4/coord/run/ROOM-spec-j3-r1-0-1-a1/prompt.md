STAGE_ID: ROOM-spec-j3-r1-0-1
MODE: room

这是 m2s 迁移流水线的一个任务，由 v3 调度器派发；你的角色说明已经在系统提示词里。这是这个任务第 1 次尝试。

## 绝对路径
- MONOLITH = D:\Emma\MyLab\Mono2SLs\Mono2Sls-artifacts\mono_benchmark\airline-booking
- WS = D:\Emma\MyLab\Mono2SLs\Mono2Sls-artifacts\M2S\runs\airline-m4

## 任务
STAGE_ID=ROOM-spec-j3-r1-0-1：按你的角色说明完成这一阶段。

## 讨论室
这是 spec 讨论室 `spec-j3-r1`，主题：规格对账（J3）有争议的差异需要裁定：
[
 {
  "id": "register-read-back-by-admin/list/body.subset",
  "case": "register-read-back-by-admin",
  "step": "list",
  "request": {
   "method": "GET",
   "path": "/auth/users"
  },
  "check": "body.subset",
  "predicted": {
   "users": [
    {
     "sub": "cust-admin",
     "email": "admin@example.com",
     "groups": [
      "Admin"
     ]
    },
    {
     "sub": "cust-reg-3",
     "email": "reg3@example.com",
     "groups": []
    }
   ]
  },
  "actual": {
   "users": [
    {
     "email": "reg3@example.com",
     "groups": [],
     "sub": "cust-reg-3"
    },
    {
     "email": "admin@example.com",
     "groups": [
      "Admin"
     ],
     "sub": "cust-admin"
    }
   ]
  },
  "fact_refs": [
   "POST /auth/register#responses[1]",
   "POST /auth/register#business_rules[3]",
   "GET /auth/users#responses[2]",
   "GET /auth/users#business_rules[2]",
   "GET /auth/users#business_rules[4]"
  ],
  "reason": "差异在数组元素的顺序，而事实库明确说了顺序不属于对外承诺：GET /auth/users#business_rules[2] —— '不排序、不分页……条目顺序就是 DynamoDB scan 的返回顺序，代码没有排序步骤，因此顺序不属于对外承诺'。代码印证：data/storage.py:352-356 只有 self.users_table.scan() 取 Items，services/auth.py:140-150 的脱敏推导式保持原顺序、没有 sorted()。两个字段集合（cust-admin / cust-reg-3）单体都返回了，只是先 reg3 后 admin；J1 按 fixtures/admin-user.json 的书写顺序把 2 个元素按下标钉死了（body.subset 对数组要求等长、逐位匹配）。"
 }
]。你这一轮以 `m2s-verifier` 身份发言。
规则：只在你这一轮发言；用 `mcp__m2s-server__room_post(room_id, kind, body)` 提交一条意见，然后用 `submit_task` 结束。不要修改工作区文件（这是讨论，不是实现）。
讨论室和公告板里给实现线看的内容只能有：失败的函数、出处、期望和实际结果；不能出现 verification/ 下的路径或原文，否则会被拒绝。
spec 讨论室：architect 的发言里用 `record_ruling` 写出结论；triage 讨论室：每个嫌疑模块用 JSON 归因 {"module": "code|template|spec|任务 id", "reason": "...", "confidence": "high|medium|low"}。
当前讨论：
[]

## 完成方式
1. 写工作报告 WS/storage/stage_reports/ROOM-spec-j3-r1-0-1.json（格式按你的角色说明）。
2. 调用 mcp__m2s-server__submit_task(summary=...) 提交，然后结束会话。不需要再输出 STAGE_COMPLETE。
3. 做不下去时，调用 mcp__m2s-server__report_blocked(reason, evidence, suspected_module)，然后结束会话；认为是更早阶段的问题时，在 suspected_module 写那个阶段（例如 S2），并在 evidence 里给出证据。
4. 调度器会自己运行这个任务的检查（room_turn），你的自述不作为判定依据。

## 注意
- 如果调用工具或等待过程中遇到网络错误（例如 'Network error: Connection error'），重试 1-2 次再继续，不要因为一次网络错误就直接放弃或提前结束。
- 只改这个任务范围内的文件：storage/stage_reports/ROOM-spec-j3-r1-0-1.json、storage/analysis_report.json。范围之外的改动会被撤销，这次尝试判为失败。
- 不要修改流水线自己的工具：m2s-mcp-server/、.openharness/、~/.openharness/ 下的文件。
- 不要读写 WS/coord/（调度器的内部状态）。不要调用 mcp_auth。
- 不要读别的工作区（WS 所在 runs/ 目录下的其他目录、runs-prev-*），也不要参照它们的产物。
- 这份说明可以随时用 mcp__m2s-server__get_task_context 重新取回。