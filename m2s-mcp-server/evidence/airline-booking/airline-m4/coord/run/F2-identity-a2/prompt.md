STAGE_ID: F2-identity

这是 m2s 迁移流水线的一个任务，由 v3 调度器派发；你的角色说明已经在系统提示词里。这是这个任务第 2 次尝试。

## 绝对路径
- MONOLITH = D:\Emma\MyLab\Mono2SLs\Mono2Sls-artifacts\mono_benchmark\airline-booking
- WS = D:\Emma\MyLab\Mono2SLs\Mono2Sls-artifacts\M2S\runs\airline-m4

## 任务
STAGE_ID=F2-identity：按你的角色说明完成这一阶段。

## 回炉信息（调度器转来的，请据此修正）

### 1. 来自 J4
白盒补测（J4）在单体代码里发现了事实库没有、或者写错了的规则（judge/spec_issues.json）。按代码核实，用 annotate_entry_point 补进或修正事实库（每条都带出处），不要改动无关的事实；改完 check_provenance 必须 PASS：
[
 {
  "id": "J4-3",
  "endpoint": "GET /auth/users",
  "kind": "missing",
  "rule": "storage.get_user 把 DynamoDB 读异常吞掉返回 None（data/storage.py:311-313），而 _get_authenticated_user 把 None 一律当“令牌无效”，所以用户表 GetItem 故障时每个需要认证的端点对外都是 401 'Invalid or expired token'（与“令牌过期/无效”同一条臂、同一段文案），既不是 5xx 也不是 403。事实库各鉴权端点的 401 描述只写了“令牌 sub 在用户表查不到”，没写“读表失败也算查不到”",
  "evidence": [
   {
    "file": "data/storage.py",
    "line": 312,
    "symbol": "print(f\"Error getting user {sub}: {str(e)}\")"
   },
   {
    "file": "data/storage.py",
    "line": 313,
    "symbol": "return None"
   },
   {
    "file": "services/auth.py",
    "line": 120,
    "symbol": "return storage.get_user(payload['sub'])"
   }
  ],
  "cases": [
   "users-admin-list"
  ]
 }
]

## 完成方式
1. 写工作报告 WS/storage/stage_reports/F2-identity.json（格式按你的角色说明）。
2. 调用 mcp__m2s-server__submit_task(summary=...) 提交，然后结束会话。不需要再输出 STAGE_COMPLETE。
3. 做不下去时，调用 mcp__m2s-server__report_blocked(reason, evidence, suspected_module)，然后结束会话；认为是更早阶段的问题时，在 suspected_module 写那个阶段（例如 S2），并在 evidence 里给出证据。
4. 调度器会自己运行这个任务的检查（check_provenance_pkg），你的自述不作为判定依据。

## 注意
- 如果调用工具或等待过程中遇到网络错误（例如 'Network error: Connection error'），重试 1-2 次再继续，不要因为一次网络错误就直接放弃或提前结束。
- 只改这个任务范围内的文件：storage/analysis_report.json、storage/stage_reports/F2-identity.json。范围之外的改动会被撤销，这次尝试判为失败。
- 不要修改流水线自己的工具：m2s-mcp-server/、.openharness/、~/.openharness/ 下的文件。
- 不要读写 WS/coord/（调度器的内部状态）。不要调用 mcp_auth。
- 不要读别的工作区（WS 所在 runs/ 目录下的其他目录、runs-prev-*），也不要参照它们的产物。
- 这份说明可以随时用 mcp__m2s-server__get_task_context 重新取回。