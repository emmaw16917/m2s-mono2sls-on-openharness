STAGE_ID: J4
MODE: J4

这是 m2s 迁移流水线的一个任务，由 v3 调度器派发；你的角色说明已经在系统提示词里。这是这个任务第 2 次尝试。

## 绝对路径
- MONOLITH = D:\Emma\MyLab\Mono2SLs\Mono2Sls-artifacts\mono_benchmark\bookstore
- WS = D:\Emma\MyLab\Mono2SLs\Mono2Sls-artifacts\M2S\runs\bookstore-m4

## 任务
MODE: J4。白盒补测：可以读单体源码。按 judge/coverage_monolith/summary.json 里还没走到的行（第二轮起还有回炉信息里的 gap 变异体）补用例 judge/cases/<接口 slug>/wb-*.json；代码里有、事实库没有的规则写进 judge/spec_issues.json。按你的角色说明里"J4"一节做，用 mcp__m2s-server__check_judge_assets 自查到 PASS。

## 回炉信息（调度器转来的，请据此修正）

### 1. 来自 J5C
变异检验（J5）有这些变异体没被现有用例抓住，分类判为 gap（改动会改变外部行为）。为每一个补一个能看出差别的用例（`suggest` 是分类时的建议）：
[
 {
  "id": "negate:middleware/auth.js:19",
  "operator": "negate",
  "file": "middleware/auth.js",
  "line": 19,
  "before": "if (config.auth.devMode) {",
  "after": "if (!(config.auth.devMode)) {",
  "refs": [
   "GET /api/auth/me#business_rules[7]"
  ],
  "outcome": "survived",
  "reason": "改动会改变外部行为，只是现有用例看不出来。config.js:15 `AUTH_DEV_MODE = process.env.AUTH_DEV_MODE === 'true'` 默认 false（裁判用默认配置跑单体，judge/run_config.json 不存在，没有设这个环境变量），所以 middleware/auth.js:19 取反后 `if (!(config.auth.devMode))` 在裁判的每一次请求里都成立，第 20-25 行的开发模式快速通道反而被执行：只要请求带了 x-customer-id 头，中间件就把头的值原样写进 req.customerId 并 next()，完全跳过后面的 Bearer token 校验。真实单体在默认配置下必须忽略这个头（事实库 GET /api/auth/me#request.headers[1] 写明“config.js:15 默认 false，此时该头被忽略”；GET /api/auth/me#business_rules[7] 写明该头只在 AUTH_DEV_MODE=true 时才被当身份），无 token 的请求应得到 responses[4] 的 401 'Authentication required. Please provide a valid Bearer token.'。变异体没被抓住是因为裁判用例一个都没发过这个头：judge/cases/ 下 grep 'x-customer-id' 零命中（101 个单元全部运行也无人察觉），所以这条不属于“裁判条件下走不到”的死代码，而是鉴权绕过方向的覆盖缺口。",
  "suggest": "给 GET /api/auth/me 加一个用例（例如 judge/cases/get-api-auth-me/x-customer-id-header-ignored.json，endpoint 用 'GET /api/auth/me'，identity 设为 null）：步骤 1 不带 Authorization、只带 headers {\"x-customer-id\": \"<u2 的 userId>\"}（值用 ${u2.id} 由身份准备存下），expected status 401，body.contains/equals 取单体实际录到的 responses[4] 文本 'Authentication required. Please provide a valid Bearer token.'（不是 'Invalid or expired token'）；步骤 2 read_back 用 u1 身份正常取回自己的记录，说明身份确实只能来自 token。再加一步带 u1 的 token 同时把 x-customer-id 设成 u2 的 id，expected 仍是 u1 自己的条目（fact_refs: GET /api/auth/me#business_rules[1]、[7]、responses[4]）。同一手法可用于其他挂 authMiddleware 的端点（例如 GET /api/cart 带 x-customer-id 无 token → 401）。"
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