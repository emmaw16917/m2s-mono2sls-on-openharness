# 运行报告：airline-m4

- 结果：**archiving**
- 单体：D:\Emma\MyLab\Mono2SLs\Mono2Sls-artifacts\mono_benchmark\airline-booking
- 模板：v2-split

- harness（2026-11-13T13:51 起）：m2s f286ec32fa（未提交改动 2 处），OpenHarness 07404fad4b，模型 qwen3.8-flash
- harness（2026-11-13T13:58 起）：m2s 8c06d7f7aa（未提交改动 1 处），OpenHarness 07404fad4b，模型 qwen3.8-flash
- harness（2026-11-13T15:41 起）：m2s 61e37e336b（未提交改动 1 处），OpenHarness 07404fad4b，模型 qwen3.8-flash
- harness（2026-11-16T17:15 起）：m2s 788d9cf385（未提交改动 1 处），OpenHarness 07404fad4b，模型 qwen3.8-flash

| 任务 | 状态 | 启动次数 | 崩溃 | API 中断 | 用时（分钟） | 最后一次检查 |
|---|---|---|---|---|---|---|
| F1 | closed | 0 | 0 | 0 | 0.0 | analyze_project✓；analysis_report✓ |
| G1 | closed | 0 | 0 | 0 | 0.0 | gap_scan✓；gap_inventory✓ |
| G2 | closed | 1 | 0 | 0 | 10.6 | gap_inventory✓ |
| J1 | closed | 4 | 0 | 0 | 37.1 | judge_assets✓ |
| J2 | closed | 0 | 0 | 0 | 0.0 | run_monolith✓ |
| RC | closed | 0 | 0 | 0 | 0.0 | reconcile_spec✓ |
| J3 | closed | 4 | 0 | 0 | 19.1 | reconcile_classified✓ |
| J4 | closed | 3 | 0 | 0 | 67.2 | judge_assets✓；j4_report✓ |
| J5 | closed | 0 | 0 | 0 | 0.0 | run_mutants✓ |
| J5C | closed | 1 | 0 | 0 | 8.1 | mutant_classes✓ |
| J6 | closed | 0 | 0 | 0 | 0.0 | freeze_judge✓ |
| A1 | closed | 4 | 0 | 0 | 114.7 | blueprint_keys✓；gate_blueprint✓；judge_unchanged✓ |
| D1 | closed | 1 | 0 | 0 | 27.5 | delta_assets✓；judge_unchanged✓ |
| I2a | closed | 1 | 0 | 0 | 47.6 | write_scope✓；tools_unchanged✓；judge_unchanged✓；template_part1✓ |
| I2b | closed | 1 | 0 | 0 | 18.8 | write_scope✓；tools_unchanged✓；judge_unchanged✓；template_part2✓ |
| I2c | closed | 1 | 0 | 0 | 11.7 | write_scope✓；tools_unchanged✓；judge_unchanged✓；gate_template✓ |
| I3 | closed | 1 | 0 | 0 | 11.2 | write_scope✓；tools_unchanged✓；judge_unchanged✓；gate_final✓；consistency_pass✓ |
| V1 | closed | 0 | 0 | 0 | 0.0 | check_adapter✓ |
| V2 | closed | 0 | 0 | 0 | 0.0 | run_judge✓ |
| V3 | closed | 1 | 0 | 0 | 6.0 | write_scope✓；tools_unchanged✓；judge_unchanged✓；verdict_delta✓；judge_unchanged✓ |
| B0 | closed | 0 | 0 | 0 | 0.0 | tag_b0✓ |
| O | closed | 2 | 0 | 0 | 84.0 | write_scope✓；tools_unchanged✓；judge_unchanged✓；s5_report✓；judge_unchanged✓ |
| C1 | closed | 0 | 0 | 0 | 0.0 | final✓ |
| C2 | in_progress | 0 | 0 | 0 | 0.0 |  |
| ROOM-spec-j3-r2-3-2 | closed | 2 | 0 | 0 | 100.9 | write_scope✓；tools_unchanged✓；room_turn✓ |
| ROOM-spec-j3-r2-2-2 | closed | 1 | 0 | 0 | 6.4 | write_scope✓；tools_unchanged✓；room_turn✓ |
| ROOM-spec-j3-r2-1-2 | closed | 1 | 0 | 0 | 28.1 | write_scope✓；tools_unchanged✓；room_turn✓；rulings✓ |
| ROOM-spec-j3-r2-0-2 | closed | 1 | 0 | 0 | 5.0 | write_scope✓；tools_unchanged✓；room_turn✓ |
| ROOM-spec-j3-r2-3-1 | closed | 1 | 0 | 0 | 19.2 | write_scope✓；tools_unchanged✓；room_turn✓ |
| ROOM-spec-j3-r2-2-1 | closed | 1 | 0 | 0 | 8.2 | write_scope✓；tools_unchanged✓；room_turn✓ |
| I1-shared | closed | 1 | 0 | 0 | 23.1 | write_scope✓；tools_unchanged✓；judge_unchanged✓；gate_code_pkg✓；judge_unchanged✓ |
| I1-bookings-customers-flights-loyalty-payments | closed | 1 | 0 | 0 | 120.7 | write_scope✓；tools_unchanged✓；judge_unchanged✓；gate_code_pkg✓；judge_unchanged✓ |
| I1-identity | closed | 1 | 0 | 0 | 5.8 | write_scope✓；tools_unchanged✓；judge_unchanged✓；gate_code_pkg✓；judge_unchanged✓ |
| ROOM-spec-j3-r2-1-1 | closed | 1 | 0 | 0 | 5.2 | write_scope✓；tools_unchanged✓；room_turn✓；rulings✓ |
| ROOM-spec-j3-r1-3-2 | closed | 1 | 0 | 0 | 4.3 | write_scope✓；tools_unchanged✓；room_turn✓ |
| ROOM-spec-j3-r1-2-2 | closed | 1 | 0 | 0 | 3.4 | write_scope✓；tools_unchanged✓；room_turn✓ |
| ROOM-spec-j3-r1-1-2 | closed | 1 | 0 | 0 | 6.2 | write_scope✓；tools_unchanged✓；room_turn✓；rulings✓ |
| ROOM-spec-j3-r1-0-2 | closed | 1 | 0 | 0 | 3.7 | write_scope✓；tools_unchanged✓；room_turn✓ |
| ROOM-spec-j3-r1-3-1 | closed | 1 | 0 | 0 | 3.9 | write_scope✓；tools_unchanged✓；room_turn✓ |
| ROOM-spec-j3-r1-2-1 | closed | 1 | 0 | 0 | 13.8 | write_scope✓；tools_unchanged✓；room_turn✓ |
| ROOM-spec-j3-r2-0-1 | closed | 1 | 0 | 0 | 4.2 | write_scope✓；tools_unchanged✓；room_turn✓ |
| RULE_J3_2 | closed | 0 | 0 | 0 | 0.0 | rulings_cover✓ |
| ROOM-spec-j3-r1-1-1 | closed | 1 | 0 | 0 | 7.7 | write_scope✓；tools_unchanged✓；room_turn✓；rulings✓ |
| ROOM-spec-j3-r1-0-1 | closed | 1 | 0 | 0 | 6.3 | write_scope✓；tools_unchanged✓；room_turn✓ |
| RULE_J3_1 | closed | 0 | 0 | 0 | 0.0 | rulings_cover✓ |
| F2-shared | closed | 0 | 0 | 0 | 0.0 | check_provenance✓ |
| F2-bookings-customers-flights-loyalty-payments | closed | 5 | 0 | 0 | 91.5 | write_scope✓；tools_unchanged✓；check_provenance_pkg✓ |
| F2-identity | closed | 2 | 0 | 0 | 25.3 | write_scope✓；tools_unchanged✓；check_provenance_pkg✓ |

## 异常处理

- C001-J6-review：ok → 继续
- C002-V1-triage：escalate → 交给人
- C003-V2-triage：retry → 同一任务再试一轮
- C004-B0-review：ok → 继续

## 交给人

- V1：需要人判断的是：这不是 A1/I1/I2 任何一个产物能修的问题，而是 check_adapter 自身对「目标侧身份 id」的取法与冻结裁判用例冲突——要不要改工具（judge.py/adapter.py 的身份语义），以及按哪种方案改。

证据链（工作区只读核对）：
1) 失败面：verification/check_adapter.json = FAIL，13 用例/23 数据步，失败 6 例（booking-read-guards、customer-bookings-owner-only、customer-bookings-status-filter、loyalty-no-records、loyalty-sum-and-level、loyalty-tier-boundaries），全部是 owner-or-admin 守卫端的读回步：期望 200、实际 403，body 变成 {"error": ...}。涉及函数只有 bookings-get / customers-bookings-list / loyalty-get（都按事实库实现了「非本人且非 Admin → 403」，与单体一致：同用例里 other、other-asking 期望 403 也确实 403，loyalty-sum-and-level#other-asking 亦然，所以代码方向没错）。
2) 单体的身份 id 字面值就是夹具/路径里的那个串：judge/masks.json 里 <id:u1> 的 "value" = "cust-u1"（u2=cust-u2、admin=cust-admin），judge/fixtures/booking-u1.json 的 customer="cust-u1"，冻结用例的请求路径 53 处直接写死 "cust-u1"/"cust-u2"（例如 /loyalty/cust-u1、/customers/cust-u1/bookings），没有任何 ${alias.id} 占位符。因此 A1 在 storage/adapter_map.json 的 identities 注记里写的「u1→cust-u1、${id} 取裁判保存的 body.user.sub」对单体的描述是准确的，而 mapping 也已把 BookingTable.customer / LoyaltyTable.customerId / UserGroupsTable.sub 声明为 {"as":"identity"}。
3) 工具却给目标侧造了一个全新的 id：m2s-mcp-server/judge.py `_unit_identities`（约 2176 行）target_id = uuid5(ID_NAMESPACE, "<unit>/<alias>")，adapter_map.identities.claims={"sub":"${id}"} 于是把 claims.sub 注入成 5a9ba4ed-80f6-5d8b-ae2e-04bda819a37a（= uuid5("booking-read-guards/u1)，我已算出逐字相同，check_adapter 的 "<id:u1> is '5a9ba4ed…' elsewhere" 就是它）。同时 `_plan_units`（约 2202 行）调 `adapter.fixtures_to_target(amap, u["fixtures"])` 没传 ids，2266 行 `tables_to_monolith` 同样没传 ids —— 即 `as: identity` 的翻译在 V1 里是死代码，落库仍是 "cust-u1"，而守卫比较的是 claims.sub 那个 uuid5 → 必然 403。请求路径里的 id 字面量也没有任何翻译，所以即使补上夹具翻译，/loyalty/cust-u1 这类按路径参数比对 owner 的端点仍然 403。
4) A1 无法自救：identities 只允许 {claims,user_id,seed,note} 一套全局模板（adapter.py 约 405 行），变量只来自该别名单体准备步骤的请求体字段（identity_env，约 239 行）+ alias + id；u1/u2 有 register body 的 userId，但 admin 只有 login（变量只有 email），写 ${userId} 会被静态检查判「alias 未定义」，写常量则无法区分别名。把守卫改成不比对身份等于修契约，裁判已冻结且不允许放宽比较。
5) 副作用要请人一并知道：修好之前，这些守卫端的「401/403 阴性步骤通过」是巧合，不构成证据，工具修复后 V1 必须整批重跑。另 tables_after 里 Airline-Users 期望 3 行、实际 1 行的差异是设计如此（目标不为 u1/u2 预置资料行，缺行=groups=[]，SI-10/D-4），属信息项，不是本次失败原因，无需处置。

需要人在两个方案里定夺（我无法判断该选哪个，故不 reroute 也不 retry）：
(A) 让 check_adapter 的 ${id} 取裁判记录的该别名 id 字面值（masks.json 的 value / 单体侧保存的 body.user.sub），两侧 id 相同，`as: identity` 退化为原样映射，现存的映射与代码可原样通过；
(B) 保留「每别名全新 id」语义，但把 ids 映射真正接进 fixtures_to_target/tables_to_monolith，并对请求 path/query/body 里的身份字面量做同一翻译（改动面更大，且 53 处用例字面量都在翻译范围内）。
裁判用例本身（judge/）两种方案都不需要动，符合「不可改裁判」；改的是 m2s-server，超出分诊权限。

## 裁判线

- 规格对账第 1 轮：fact_error 1，needs_ruling 1，case_error 2
- 规格对账第 2 轮：needs_ruling 1
- 规格对账第 1 轮：fact_error 5
- 规格对账第 2 轮：case_error 5
- 裁判 v1 已冻结（2026-11-13T23:12），见 judge/judge_manifest.json

## 裁定

- R-ROOM-spec-j3-r1-1-1-a1-1（m2s-architect，decided，contract）：规格对账差异 register-read-back-by-admin/list/body.subset：GET /auth/users 响应中 users 数组的元素顺序是否属于对外契约（J1 把 2 个元素按下标钉死，单体返回次序相反）？ → 元素顺序不属于契约，其余全部仍是契约。具体判定口径：该步骤的 body.subset 对 `users` 数组按**元素多重集（含重数）**比较，先后顺序不参与判定；除此之外不放宽任何东西——status 仍须 200；`users` 仍须恰含 2 个元素（等长要求保留）；每个元素内部的字段名与取值仍逐字比较（cust-admin / admin@example.com / ["Admin"] 与
…（截断，原文 566 字符）
- R-ROOM-spec-j3-r1-1-2-a1-1（m2s-architect，decided，contract）：规格对账差异 register-read-back-by-admin/list/body.subset 的执行收尾（承接 R-ROOM-spec-j3-r1-1-1-a1-1）：(1) 裁定的 affected_cases 该写 case#step 还是裸用例 id，对账与裁判能否认上；(2) relaxation="order" 在该步骤实际覆盖哪些数组，R-…-a1-1 边界里"元素内部 gr
…（截断，原文 220 字符） → 两条都是执行口径，不改变 R-ROOM-spec-j3-r1-1-1-a1-1 的判定内容。

(1) affected_cases 保留 "register-read-back-by-admin#list"，**不改成裸用例 id**。裁判（run_judge）读裁定时按 `#` 分区成用例与步骤：带步骤的条目只把放宽施加到那个步骤，裸用例条目会把放宽施加到该用例的**全部步骤及其中的每一条差异
…（截断，原文 1244 字符）
- R-A1-a2-1（m2s-architect，decided，migration-change）：受保护端点的未认证请求（缺 Authorization 头 / 头格式不是 "Bearer <t>" / 令牌伪造或过期）在目标上由谁应答、响应体是什么形状 → 由 API Gateway 的 Cognito/JWT 授权器应答：状态码仍是 401，响应体是网关默认形状（HTTP API：{"message": "..."}），不再是单体的 {"error": "Authentication required. Please provide a valid token."} / {"error": "Invalid token format. Use: B
…（截断，原文 317 字符）
- R-A1-a2-2（m2s-architect，decided，migration-change）：/auth/register、/auth/login、/auth/me、/auth/users 四个端点在目标上不提供，涉及它们的用例与身份准备怎么算 → 四个端点整体作废（迁移决策，不是缺陷）：目标不注册这些路由，裁判不为它们出题、也不在目标上运行以它们为步骤的用例；身份改由 adapter_map.identities 的形状注入（claims.sub = ${id}，${id} 就是单体侧 POST /auth/login 保存的 body.user.sub：cust-u1 / cust-u2 / cust-admin）。连带作废：Airlin
…（截断，原文 450 字符）
- R-A1-a2-3（m2s-architect，decided，contract）：列表端点（Scan 结果数组）的元素顺序是否属于对外契约；register-read-back-by-admin#list 的 needs_ruling 结论 → 列表响应的元素顺序不是对外契约：GET /flights/search 的 flights、GET /customers/<customer_id>/bookings 的 bookings（以及已删除的 GET /auth/users 的 users）按 Scan 返回顺序原样给出，代码不排序；比较这些数组时只要求元素集合、每个元素的字段与数量一致，不要求先后。内容、条数、字段名与字段值仍是逐字比
…（截断，原文 214 字符）
- R-A1-a2-4（m2s-architect，decided，contract）：A1 自己新落的 R-A1-a2-3（列表顺序放宽）与已在案的 R-ROOM-spec-j3-r1-1-1-a1-1 / R-ROOM-spec-j3-r1-1-2-a1-1 重复，且它的 affected_cases 多写了 customer-bookings-status-filter、search-route-match 两个裸用例 id——放宽范围以哪一条为准 → 以在案的两条 ROOM 裁定为准，R-A1-a2-3 不作新增放宽、其 affected_cases 中多出的两项一律作废。准确范围：顺序放宽只施加于 register-read-back-by-admin#list 这一步（按 # 切分，只影响该步骤），判定口径仍是「等长 + 元素多重集（含重数）+ 每个元素字段逐字比较 + status/headers/脱敏不变」。customer-booki
…（截断，原文 437 字符）
- R-ROOM-spec-j3-r2-1-1-a1-1（m2s-architect，decided，contract）：规格对账差异 register-read-back-by-admin/list/body.subset（J3 第 2 轮）：GET /auth/users 的 users 数组元素顺序按什么机制放宽——是否要求验证线改写用例、是否写成挂在事实条目上的通用规则、affected_cases 用裸用例 id 还是 case#step？（承接 R-ROOM-spec-j3-r1-1-1-a1-1 与 R
…（截断，原文 242 字符） → 判定内容不变（顺序不是契约，其余全是契约），三条执行口径如下：

(1) **由裁判在 R 层做无序多重集比较，验证线不重写用例**。不需要新增断言词、不允许把 body.subset 降级成 has_fields:["users"]。机制：run_judge 里命中 relaxation=order 的裁定会把该步骤单体记录中出现的每一个数组路径标为 unordered，比较器在这些路径上要求「
…（截断，原文 1325 字符）
- R-ROOM-spec-j3-r2-1-2-a1-1（m2s-architect，decided，contract）：规格对账差异 register-read-back-by-admin/list/body.subset 的结室口径（J3 第 2 轮）：次序放宽实际在哪一层生效、对账侧"已被裁定覆盖"按什么粒度认账、auth 作废类裁定的裸用例 id 要不要加防外溢边界？（承接 R-ROOM-spec-j3-r2-1-1-a1-1 与 R-ROOM-spec-j3-r1-1-1-a1-1 / R-ROOM-spe
…（截断，原文 244 字符） → 本差异按在案口径关闭，判定内容一字不改（category=contract、relaxation=order、只放开 R 层、影响面仅 register-read-back-by-admin#list）；本轮补三条执行边界，供 J3 重跑对账与 J4 补强引用：

(1) **放宽只在"目标 vs 单体记录"这一层由比较器施加，对账层的这条差异靠裁定覆盖关闭，二者机制不同，不要混用**。目标侧：命
…（截断，原文 1457 字符）
- R-C003-V2-triage-a1-1（handler，decided，implementation-trace）：白盒故障场景 wb-booking-handler-read-fails-after-authz（nth=2）、wb-booking-confirm-read-fails-after-authz（nth=2）、wb-booking-cancel-read-fails-in-service（nth=3）按「同一条预订的第 n 次读」注入，在只读一次条目的目标上落点与单体不同，V2 判 3 个 fau
…（截断，原文 301 字符） → 是实现轨迹，不作 P 层判定。目标在这三条注入涉及的 7 个步骤上不再判失败（其余步骤、其余用例、其它故障场景一律仍严格比对）：booking-read-guards#owner、booking-read-guards#other、booking-confirm-unconfirmed#confirm、booking-confirm-unconfirmed#read-booking、booking
…（截断，原文 1259 字符）

## 人的决定

- 2026-11-16T17:15 V1：重做。V1 身份 id 冲突按方案 (A) 处理：check_adapter 的 ${id} 改用裁判记录的别名字面值（masks.json <id:alias>，退化到 register body 的 userId），两侧 id 相同；judge/ 冻结资产不动。工具已修（788d9cf），重跑 V1 的 check_adapter。

裁定共 9 条，其中人工裁定或确认 0 条，还在等人 0 条。

## 缺口清单

- 22 条：judge 15，none 7
