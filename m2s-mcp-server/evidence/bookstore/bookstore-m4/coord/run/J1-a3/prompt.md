STAGE_ID: J1
MODE: J1

这是 m2s 迁移流水线的一个任务，由 v3 调度器派发；你的角色说明已经在系统提示词里。这是这个任务第 3 次尝试。

## 绝对路径
- WS = D:\Emma\MyLab\Mono2SLs\Mono2Sls-artifacts\M2S\runs\bookstore-m4

## 任务
MODE: J1。只看事实库（用 mcp__m2s-server__judge_facts 读，不要整个打印 analysis_report.json，也不要读工具源码），写裁判用例：judge/cases/<接口 slug>/<用例>.json、judge/fixtures/、judge/faults/、judge/identities.json（需要时 judge/uncovered.json）。按你的角色说明里"J1"一节写；写完用 mcp__m2s-server__check_judge_assets 自查，直到 PASS。单体源码对你是关闭的。

## 回炉信息（调度器转来的，请据此修正）

### 1. 来自 recheck
check_judge_assets 报告的问题：
POST /api/cart#responses[6] (413: 请求体超过 102400 字节：server.js:33 的 bodyParser.json()（Content-Type: application/json）) is not cited by any case: write a case whose steps reach it and cite it in fact_refs, or list it in judge/uncovered.json with the reason
PUT /api/cart#responses[6] (413: 请求体超过 102400 字节：server.js:33 的 bodyParser.json()（Content-Type: application/json）) is not cited by any case: write a case whose steps reach it and cite it in fact_refs, or list it in judge/uncovered.json with the reason
DELETE /api/cart#responses[6] (413: 请求体超过 102400 字节：server.js:33 的 bodyParser.json()（Content-Type: application/json）) is not cited by any case: write a case whose steps reach it and cite it in fact_refs, or list it in judge/uncovered.json with the reason
POST /api/orders#responses[6] (413: 请求体超过 102400 字节：server.js:33 的 bodyParser.json()（Content-Type: application/json）) is not cited by any case: write a case whose steps reach it and cite it in fact_refs, or list it in judge/uncovered.json with the reason

上一次的工作报告全文：
{
  "stage_id": "J1",
  "agent": "m2s-verifier",
  "status": "success",
  "mode": "J1",
  "attempt": 2,
  "summary": "第 2 次尝试：按 J3 对账的 5 条 case_error 修正了 2 个用例的 predicted（urlencoded 请求体在 facts v5 里已经被改成'照常解析'，旧预期停留在'非 JSON 不解析'的过时事实上），并按 J3 的盲区提醒把 text/plain 那一步拆成独立用例 cart-add-text-plain-400-no-write，让'#business_rules[2] 后半句（两个解析中间件都不匹配才按缺字段处理、不写表）'仍然可被检验。其余 72 个用例、fixtures、identities、13 个故障场景、uncovered 14 条保持不变；2 条 needs_ruling（列表顺序）没有动，等 architect 的 order 裁定落地后再改。裁判用例 74 个 / 233 步，check_judge_assets = PASS。",
  "cases": 74,
  "steps": 233,
  "by_endpoint": {
    "DELETE /api/cart": 5,
    "GET /api/auth/me": 3,
    "GET /api/bestsellers": 4,
    "GET /api/books/:id": 4,
    "GET /api/books": 4,
    "GET /api/cart/:bookId": 4,
    "GET /api/cart": 4,
    "GET /api/orders": 3,
    "GET /api/search": 6,
    "POST /api/auth/login": 4,
    "POST /api/auth/refresh": 3,
    "POST /api/auth/register": 5,
    "POST /api/cart": 11,
    "POST /api/orders": 8,
    "PUT /api/cart": 6
  },
  "read_back_cases": 36,
  "layers": {
    "P": 73,
    "R": 1
  },
  "cited_responses": "63/66 必需响应被引用（14 条写进 judge/uncovered.json）；fact_refs 里的响应引用共 95 条",
  "cited_business_rules": 82,
  "identities": [
    "u1",
    "u2"
  ],
  "fixtures": [
    "books",
    "bestsellers",
    "bestsellers-many"
  ],
  "fault_scenarios": 13,
  "fault_scenario_ids": [
    "bestsellers-read-fails",
    "bestsellers-write-fails",
    "books-read-fails",
    "books-read-throttled",
    "cart-delete-fails",
    "cart-read-fails",
    "cart-update-conditional-fails",
    "cart-write-throttled-once",
    "orders-cart-delete-network",
    "orders-read-fails",
    "orders-write-fails",
    "users-read-fails",
    "users-write-fails"
  ],
  "uncovered": [
    {
      "ref": "GET /api/auth/me#responses[0]",
      "reason": "handler 自己抛的 401 'Authentication required' 只在 AUTH_DEV_MODE=true 且请求既没有可用 token 也没带 x-customer-id 时出现（authMiddleware 会把 req.customerId 设成哨兵值 'default-customer-id'）。裁判用默认配置（AUTH_DEV_MODE=false，事实库 GET /api/auth/me#business_rules[7]/[8] 都写明此路默认不可达）跑单体，无凭证请求在中间件里就返回 responses[4]，任何 HTTP 请求都走不到这一臂。"
    },
    {
      "ref": "GET /api/auth/me#responses[1]",
      "reason": "404 'User not found' 需要“accessToken 合法但 Bookstore-Users 里没有该 userId 的条目”。JWT 用服务端密钥签名，裁判伪造不出指向别的 userId 的 token；而身份准备总是先注册（PutItem）出条目，单体也没有任何删除用户的接口，所以主键 GetItem 必然命中。"
    },
    {
      "ref": "POST /api/auth/refresh#responses[2]",
      "reason": "401 'User not found' 与上面同一条前提：需要“refreshToken 合法但用户条目不存在”。refreshToken 只能由注册接口签发（identities 的 u1.refresh），签发时条目已经写入，单体没有删除用户的接口，条目必然存在。"
    },
    {
      "ref": "GET /api/bestsellers#responses[1]",
      "reason": "in_catch 降级臂：只有 scan Bookstore-Bestsellers 抛异常时才会返回 200 + 空数组。正常请求（表为空或有条目）都走 responses[0]。这一臂由故障场景 bestsellers-read-fails 覆盖，不写成用例的预期。"
    },
    {
      "ref": "POST /api/auth/refresh#responses[4]",
      "reason": "500 只在 Bookstore-Users 的 GetItem 抛 SDK 错误或签名密钥未配置时出现；请求本身无法触发。由故障场景 users-read-fails 记录。"
    },
    {
      "ref": "GET /api/auth/me#responses[3]",
      "reason": "500 只在 GetItem 抛 SDK 错误时出现，请求本身无法触发。由故障场景 users-read-fails 记录。"
    },
    {
      "ref": "GET /api/books#responses[2]",
      "reason": "500 只在 scan/Query Bookstore-Books 抛 SDK 错误时出现，请求本身无法触发。由故障场景 books-read-fails 记录。"
    },
    {
      "ref": "GET /api/books/:id#responses[2]",
      "reason": "500 只在 GetItem 抛 SDK 错误时出现。由故障场景 books-read-fails 记录。"
    },
    {
      "ref": "GET /api/cart#responses[1]",
      "reason": "500 只在 Query Bookstore-Cart 抛 SDK 错误时出现。由故障场景 cart-read-fails 记录。"
    },
    {
      "ref": "GET /api/cart/:bookId#responses[2]",
      "reason": "500 只在 GetItem 抛 SDK 错误时出现。由故障场景 cart-read-fails 记录。"
    },
    {
      "ref": "GET /api/orders#responses[1]",
      "reason": "500 只在 Query Bookstore-Orders 抛 SDK 错误时出现。由故障场景 orders-read-fails 记录。"
    },
    {
      "ref": "POST /api/cart#responses[3]",
      "reason": "500 只在 PutItem 抛 SDK 错误时出现（三个字段都非空才能走到写）。由故障场景 cart-write-throttled-once 记录限流后的重试行为。"
    },
    {
      "ref": "PUT /api/cart#responses[3]",
      "reason": "500 只在 UpdateItem 抛异常时出现。由故障场景 cart-update-conditional-fails 记录。"
    },
    {
      "ref": "DELETE /api/cart#responses[3]",
      "reason": "500 只在 DeleteItem 抛异常时出现（bookId 非空即会执行删除）。由故障场景 cart-delete-fails 记录。"
    }
  ],
  "cases_changed": [
    {
      "case": "auth-endpoints-bad-json-400",
      "file": "judge/cases/post-api-auth-register/auth-endpoints-bad-json-400.json",
      "changed": "只改 predicted 与 fact_refs/title，请求（4 步）未动",
      "detail": "reg-form-encoding（POST /api/auth/register，form 提交 email+password，identity=null）：predicted 从 400 {'error':'Email and password are required'} 改为 201 + subset {message:'User registered successfully', user.email:'form@example.com', user.name:'form'}，易变的 user.userId/accessToken/refreshToken 只用 has_fields+types 断言存在与类型，不写字面值；fact_refs 补 POST /api/auth/register#responses[4] 与 #business_rules[6]（name 默认取邮箱 @ 前缀）；标题补上'urlencoded 表单字段照常解析 -> 201'。reg-bad/login-bad/refresh-bad 三步（Content-Type: application/json + 截断 JSON -> 400）保持不变，仍承载 #responses[6]。",
      "basis": "J3 case_error auth-endpoints-bad-json-400/reg-form-encoding/{status,body.subset}；事实库 facts v5 的 POST /api/auth/register#request.headers[content-type]、#responses[0].note、#responses[4].when、#responses[6].when、#business_rules[6]"
    },
    {
      "case": "cart-add-non-json-content-type-400",
      "file": "judge/cases/post-api-cart/cart-add-non-json-content-type-400.json",
      "changed": "predicted 全部重写 + 撤掉 text 步（移入新用例）+ fact_refs/标题更新；form 与 read 两步请求未动",
      "detail": "form：400 'Missing required fields' -> 200 + subset {'message':'Item added to cart successfully'}（#responses[1] 明确不回显条目内容，故不加条目字段断言）；read：[] -> 长度 1 且 contains {'bookId':'book-001','quantity':'2','price':'10'}，quantity/price 必须是字符串（#request.body[1]/[2] 与 #business_rules[1]/[2] 的注记：urlencoded 一定是字符串），customerId 是易变的身份值，不写字面值。标题改为'urlencoded 表单提交与 JSON 外部效果相同'。",
      "basis": "J3 case_error cart-add-non-json-content-type-400/{form/status,form/body.subse
…（截断，原文 12932 字符）

## 完成方式
1. 写工作报告 WS/storage/stage_reports/J1.json（格式按你的角色说明）。
2. 调用 mcp__m2s-server__submit_task(summary=...) 提交，然后结束会话。不需要再输出 STAGE_COMPLETE。
3. 做不下去时，调用 mcp__m2s-server__report_blocked(reason, evidence, suspected_module)，然后结束会话；认为是更早阶段的问题时，在 suspected_module 写那个阶段（例如 S2），并在 evidence 里给出证据。
4. 调度器会自己运行这个任务的检查（judge_assets），你的自述不作为判定依据。

## 注意
- 如果调用工具或等待过程中遇到网络错误（例如 'Network error: Connection error'），重试 1-2 次再继续，不要因为一次网络错误就直接放弃或提前结束。
- 只改这个任务范围内的文件：judge/cases/**、judge/fixtures/**、judge/faults/**、judge/identities.json、judge/run_config.json、judge/uncovered.json、storage/stage_reports/J1.json。范围之外的改动会被撤销，这次尝试判为失败。
- 不要修改流水线自己的工具：m2s-mcp-server/、.openharness/、~/.openharness/ 下的文件。
- 不要读写 WS/coord/（调度器的内部状态）。不要调用 mcp_auth。
- 不要读别的工作区（WS 所在 runs/ 目录下的其他目录、runs-prev-*），也不要参照它们的产物。
- 这份说明可以随时用 mcp__m2s-server__get_task_context 重新取回。