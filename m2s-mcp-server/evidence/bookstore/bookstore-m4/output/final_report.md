# 最终报告：bookstore-m4

生成时间：2026-11-13 13:50。由调度器按文件和事件生成。

## 最后一次检查

- gate_final：通过。gate(final) PASS
- judge_full：通过。run_judge PASS: {'cases': {'dropped': 18, 'migration-change': 14, 'pass': 54}, 'fault_runs': {'dropped': 6, 'pass': 22}}
- delta_full：通过。delta tests PASS: 12/12

## 每一层在什么环境下检验

- 裁判的预期（单体）：本机进程，DynamoDB 后端 ddblocal。
- 裁判（目标）：rie，后端 ddblocal，判定 PASS，代码 {'code': 'working tree', 'head': '9155c26', 'uncommitted_output_changes': ['output/perf_report.json']}。
- 增量测试：rie，后端 ddblocal，判定 PASS（12/12），代码 {'code': 'working tree', 'head': '9155c26', 'uncommitted_output_changes': ['output/perf_report.json']}。
- 故障行为基准 B0：rie，19 次运行。
- 这些都是本机检验：RIE 容器和 DynamoDB Local 或 dynalite，没有部署到 AWS。

## 裁判和强度

- 裁判 v1，2026-11-12T21:35:25Z 冻结：86 个用例（{'P': 84, 'R': 2}），15 个故障场景。
- 单体的行覆盖率：584/611（95.6%）。必须的响应 67/70 cited, 14 exempted，业务规则 90/111 cited。
- 变异检验：107 个变异体，抓住 97 个，等价 10 个，检出率 1.0；并行改串行的反向检查：{'serial': 'survived'}。

## 缺口

- 22 条：delta 1，judge 11，none 10

## 保留的优化

- 改动请求 1 个：kept 1
- 保留 C1（kept）：rule:R4

## 裁定

- 共 11 条，其中人工裁定或确认 3 条，还在等人 0 条。
- R-ROOM-spec-j3-r1-1-1-a1-1（contract，decided）：规格对账差异 search-case-insensitive-200/upper-q/body.subset 与 search-hits-200/author/body.subset（以及同一用例当前只是碰巧按夹具顺序通过的 category 那一步）：事实库只规定命中集合与 total，没有规定 hits 的先后次序 → 列表顺序的通用规则（本条只把 relaxation 施加在下面 affected_cases 列出的步骤上）：
1) 事实库为某个列表响应规定了显式排序键的，顺序属于契约，迁移后必须复现该排序键决定的次序——例如 GET /api/bestsellers#business_rules[1] 规定按 salesCount
- R-ROOM-spec-j3-r1-1-2-a1-1（contract，decided）：列表顺序裁定 R-ROOM-spec-j3-r1-1-1-a1-1 的适用范围与措辞修正（spec-j3-r1 室 seq 3 的 B/C/D、seq 4/5 的 ①②③ 与三.3）：栏 1 的判定条件该写成"有具名排序键"还是"应用层显式排序"；affected_cases 要不要覆盖 GET /api/search → 本条只收窄/补写 R-ROOM-spec-j3-r1-1-1-a1-1，不推翻它：那条的规则结构（顺序按"有没有排序要求"分栏、放宽只到顺序这一层、并列与翻页不在范围内）保持原样，改动在下面四处。

1) 栏 1 的判定条件收窄为"应用层按业务字段显式排序"，不是"响应顺序由某个具名排序键决定"。现况满足这一条的只有 
- R-A1-a3-1（migration-change，decided）：迁移后受保护端点的未鉴权/坏凭证请求由 API Gateway 的 Cognito 授权器应答，响应体与单体的 authMiddleware 不同，怎么比？（R-2） → 受保护端点上「无凭证 / 坏凭证 / 把 refresh token 当 access token 用」这三类请求由 API Gateway 的 Cognito 授权器应答，不进 Lambda：状态码保持 401（与单体一致）；响应体是网关的 {"message":"Unauthorized"}，不复刻单体的 {"er
- R-A1-a3-2（contract，decided）：GET /api/books 与 GET /api/search 的列表顺序：事实库说顺序就是 scan/索引返回顺序、没有规定排序键，迁移后的实现是否必须复现单体的返回次序？（R-3，把 R-1 的通用规则落到具体步骤） → 只在这些步骤上放宽「顺序」：事实库对 GET /api/books（Scan 全表臂与 category-index Query 臂）和 GET /api/search 的 hits 没有规定任何排序键（顺序 = 存储返回顺序），所以迁移后不要求复现单体 DynamoDB 的返回次序；等价性比较把这些列表按多重集比。不
- R-A1-a3-3（migration-change，decided）：四个认证端点与 Bookstore-Users 表被 Cognito 取代，单体在这些端点上的对外契约（响应体、状态码、bcrypt 500、email-index 判重）不再由目标提供，怎么认定？（R-4） → 目标不提供 POST /api/auth/register、POST /api/auth/login、POST /api/auth/refresh、GET /api/auth/me 这四个 HTTP 端点，也不建对应资源；客户端改用 Cognito SDK（Sign UP / signIn / getToken / f
- R-A1-a3-4（contract，decided）：R-A1-a3-2（列表顺序不作契约）的适用范围与措辞需要修正：它把「GET /api/orders、GET /api/cart 的顺序保持严格」写进了结论，而并行房间已 decided 一条裁定说明「表/索引的键序不等于排序契约」；本条 relaxation 到底覆盖哪些步骤？ → 范围与措辞修正（不推翻 R-A1-a3-2 的判定，只把它适用于哪些步骤说清楚，并与 R-ROOM-spec-j3-r1-1-2-a1-1 的口径对齐）：
1) 本条 relaxation=order 的适用面就是 affected_cases 里 GET /api/books 的列表步骤（all / fiction 
- R-ROOM-spec-j3-r2-1-1-a1-1（contract，decided）：规格对账第二轮（本室）两条差异 search-case-insensitive-200/upper-q/body.subset 与 search-hits-200/author/body.subset 还需要新的列表顺序裁定吗？另外，既有两条 order 裁定的 affected_cases 含裸用例条目，在按用例记账 → 本条只做确认与补写记账口径，不新增、不扩大任何放宽。
1) 不需要新裁定：两条差异已由 R-ROOM-spec-j3-r1-1-1-a1-1（列表顺序通用规则）与 R-ROOM-spec-j3-r1-1-2-a1-1（收窄与扩围）覆盖，两条均 status=decided、deviates_from_monolith=
- R-ROOM-spec-j3-r2-1-2-a1-1（contract，decided）：spec-j3-r2 第 3–5 楼提出的四件事：(1) R-ROOM-spec-j3-r2-1-1-a1-1 第 2 点 (b) 的字面判据（"每个条目键集合与取值逐字相等"）在 subset 模板下永远不成立，验证线提出的 (i)-(v) 可执行形式是否认可；(2) 顺序暴露的审计口径——验证线按 predicte → 本条批准验证线/标注员三项请求、纠正一条审计口径、并**新增点名两个步骤进 order 放宽范围**（这是本轮唯一的扩面，理由见第 3 点；不引入任何新的行为放宽）。

1) 认可 (i)-(v) 取代 R-...2-1-1 第 2 点 (b) 的字面写法。代码依据（本轮读实）：reconcile 走 compare_
- R-human-1（migration-change，decided）：未鉴权 + 超大请求体（>100KB）时，单体由 body-parser 在鉴权之前回 413 request entity too large，目标由 API Gateway 的 Cognito 授权器先回 401：是否接受这一状态码改变？ → 接受：作为 migration-change，未鉴权请求由网关先答 401，不再复现单体的 413（点名 cart-add-oversize-body-413#oversize-no-auth、order-oversize-body-413#oversize-no-auth 两步）。同用例带凭证的 oversize-j
- R-human-2（migration-change，decided）：R-A1-a3-1 的记账补到 J4 第 2 轮新增的 wb-cart-x-customer-id-header-ignored 的两个 spoof 步骤：无 Authorization、只带 x-customer-id 时由网关授权器回 401，是否按同一规则记账？ → 是：R-A1-a3-1 的规则（无凭证/坏凭证/refresh 当 access 时由授权器回 401）覆盖这两个步骤；只补记账，不改行为，不解冻裁判。
- R-human-3（implementation-trace，decided）：O 阶段 REQ-1（R4：update-bestsellers 按消息处理）被判 blocked_by_coverage：66 行改动中 1 行（books=[] 的防御性 no-op return，HTTP 侧不可达）没有用例覆盖，其余检查（gate/judge/delta/equivalence）全 PASS。是 → 接受：REQ-1 保留（verdict kept）。未覆盖的 1 行防御分支作为已知覆盖缺口记录；不需要回炉 D1。

## 仍未解决

- C1：the optimizer left changes a person has to look at (blocked=[{'req': 'REQ-1', 'rule': 'R4', 'function': 'update-bestsellers', 'type': 'coverage', 'verdict': 'blocked_by_coverage', 'uncovered_changed_lines': [{'file': 'output/lambdas/bookstore/update-bestsellers/handler.js', 'line': 302, 'code': 'return;', 'statement': '消息 books 为空 → no-op 的防御分支（单体 utils/bestsellers.js:41-43，blueprint:lambda_functions[update-bestsellers].notes）。R4 把 `continue` 改成 `return`，这一行因此算改动行；POST /api/orders 对空 books 数组先返回 400（POST /api/orders#business_rules[0]），生产者不会发出这种消息，所以裁判用例覆盖不到它。'}], 'cases': [], 'patch': 'perf/patches/REQ-1.patch', 'other_checks': 'gate(final) PASS；run_judge(functions=[update-bestsellers]) PASS（10 pass + 2 migration-change，6 个故障运行 pass）；run_validate(functions=[update-bestsellers], assets=verification/delta) PASS 9/9，cases_with_conflicts=[]；run_equivalence(mode=diff, functions=[update-bestsellers], assets=verification/delta) EQUIVALENT（runs 14，differences=[]，unstable_in_baseline=[]）；改动行覆盖 65/66。', 'ask_of_verification_line': '补一条 verification/delta/events/update-bestsellers 用例：批次里混一条 {orderId, books: []} 的 SQS 消息，期望 batch_item_failures=[] 且 BestsellersTable/IdempotencyTable 原封不动。补好后用重新判决模式把 REQ-1 交回来即可（补丁可干净应用）。'}], pending_spec=[])
