# 异常处理记录

本次运行中异常处理者做过的判断，按时间排列。

## C001-J6-review（review，任务 J6）

- 决定：ok
- 理由：阶段复查（裁判冻结 → 设计线已产出蓝图/适配映射 → 实现线尚未开工）未发现需要处理的问题，逐项证据：

1) 冻结本身自洽：judge_manifest v1 = 55 用例 / 189 文件，facts_sha=0519fa1796a0… 与 storage/facts_version.json（facts v7，22:43）逐字相同，即事实库在 22:43 之后没再动过，冻结没有落在过期事实上；变异分数 1.0（119 变异体，113 killed，6 survived 全部有分类），reverse(serial)=survived 且 reverse_ok=true（预期：代理串行化不该被察觉）。

2) 全部只读自查通过：check_provenance PASS（654 处引用可查证，18 个端点字段齐全，unknown_items 3 项属白盒注记）、check_judge_assets PASS（0 error 0 warning，53/64 必需响应被断言、14 项豁免都写了不可达成因或指向具体故障场景）、check_mutant_classes PASS（5 个需登记 survived 全为 equivalent；check 的 5 与 manifest 的 6 之差是 field:services/payment.py:309 已由 J5 就地按 judge/uncovered.json 记为 equivalent，不是漏分类）、check_reconcile_classes PASS（differences=0，1 条经裁定关闭）、check_adapter_map PASS、check_gap_inventory(require_decided) PASS（22 gap 全 decided 且写进 DECISIONS.md）、run_gate blueprint PASS。

3) 唯一的对账差异（register-read-back-by-admin#list 的 users 次序）已被 R-ROOM-spec-j3-r1-1-1/-1-2 + R-ROOM-spec-j3-r2-1-1/-1-2 关闭，R-A1-a2-4 又把 R-A1-a2-3 多写的两个裸用例 id 作废、把放宽严格收在 case#step 上，链条无矛盾；两个讨论室均 closed，无未答复的 spec-issue（notices 只有 7 条 artifact），8 条裁定 status 全为 decided、deviates_from_monolith 全为 false。

4) 两条 migration-change 裁定核对过规则手册原文，确属默认做法而非“有人自行改契约”：R-A1-a2-1（未登录由网关授权器应答 401，body 变 {message}）对应 kb 的「Token validation done by API Gateway Cognito Authorizer」；R-A1-a2-2（/auth/register|login|me|users 四端点整体不迁、身份改由 adapter_map.identities 注入 claims.sub）对应 kb 第 5 节「Auth endpoints (/register, /login, /logout, /refresh-token, /me) are DELETED」与 Rule 3。因此不需要交人确认，也不需要我自己再记一条裁定。

5) 冻结后不会拖爆实现线的两个隐患已排除：(a) 适配映射 dropped_endpoints 恰好列出这四条端点，run_judge 按端点路由为 dropped，我核对了 55 个用例：涉 /auth 的 14 个全是 auth-only，没有任何“混合用例”，故不会有保留端点的断言被顺带跳过；(b) 单体进程内 _payments 的跨调用状态已由 GAP-5 决定建 PaymentTable（adapter_map.target_only_tables 亦记录，test=judge），wb-booking-catchall-refunds-collected-charge 这类读回步骤在目标上有承载处。

6) 两处纯装饰性残留，不构成风险、不值得为它 retry/reroute（裁判已冻结，judge/ 不可再改）：mutant_classes.json 仍留着 negate:app.py:224 的 class=gap 条目，而该变异体在补入上述新用例后已被 killed（J5 从 0.949 升到 1.0），自查只要求 survived 有条目；R-A1-a2-2 正文写“12 个用例”，冻结后实际是 14 个 auth-only 用例，但作废由端点名驱动，多出的 2 个同样不会在目标上运行。

结论：放行，按依赖进入实现线（I1-identity / I1-bookings-customers-flights-loyalty-payments / I1-shared，随后 C1/C2/V1）。无需 retry、无需 reroute、无需交人。
- 给 worker 的反馈：（无）
- 调度器的处理：继续

## C002-V1-triage（triage，任务 V1）

- 决定：escalate
- 理由：需要人判断的是：这不是 A1/I1/I2 任何一个产物能修的问题，而是 check_adapter 自身对「目标侧身份 id」的取法与冻结裁判用例冲突——要不要改工具（judge.py/adapter.py 的身份语义），以及按哪种方案改。

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
- 给 worker 的反馈：（无）
- 调度器的处理：交给人

## 人的决定（任务 V1，2026-11-16 17:15）

- 决定：重做
- 说明：V1 身份 id 冲突按方案 (A) 处理：check_adapter 的 ${id} 改用裁判记录的别名字面值（masks.json <id:alias>，退化到 register body 的 userId），两侧 id 相同；judge/ 冻结资产不动。工具已修（788d9cf），重跑 V1 的 check_adapter。

## C003-V2-triage（triage，任务 V2）

- 决定：retry
- 理由：V2 唯一的失败面是 3 个 fault run，且逐字命中 A1 在 D-20 里预先登记的后果，不是代码、打包或蓝图缺陷：

1) 失败面：cases 55 条全部不失败（pass 34 / migration-change 7 / dropped 14）；fault_runs 27 场景 31 次运行里 fail 3 条——wb-booking-handler-read-fails-after-authz（booking-read-guards #owner 期望 400 实际 200、#other 期望 403 实际 404）、wb-booking-confirm-read-fails-after-authz（#confirm 期望 400 实际 200、#read-booking 期望 200 实际 404）、wb-booking-cancel-read-fails-in-service（#cancel 期望 400 实际 200，表状态 UNCONFIRMED vs CANCELLED、seatCapacity 5 vs 6）。cancel 场景 rules_fired 单体 [true]/目标 [false]，即注入在目标上根本没命中。

2) 原因与 D-20 描述完全一致：单体在 GET/confirm/cancel 里对同一条预订连读 2~3 次（装饰器 + handler/service），三条场景按 nth=2/2/3 注入；目标按 SI-1 每请求只读一次（bookings-get handler 的 read_booking 只有一次 booking_table.get_item，注释即写明「SI-1 - no second read of the same key, so the second-read arm is not implemented」）。于是 nth=3 命中不到（全案只读 2 次）、nth=2 落到同一次运行的另一步那一次读上（#other、#read-booking），命中后由 SI-13 读失败语义答 404。三条的落点与 D-20 预测逐条对得上，因此无需 reroute I1/I2/A1：代码没有可改之处，而 D-20 的「反向约束」还明令禁止为了让注入命中补做冗余读；模板与适配映射无关（注入确实在目标侧生效过）。

3) 为什么我自己落裁定而不交人：这条差异不是「有人选择修掉单体的对外行为」。事实库自己认定读次数是实现轨迹（D-18：「读次数、先读后写是做法，文案与状态码才是契约」；POST /bookings/<booking_id>/cancel#business_rules[8]「三次读的是同一个 Key，合成一次读对外部效果相同」），真实流量下目标的行为（读失败→404、正常→200/403/404 逐字不变）落在单体既有臂内，deviates_from_monolith=false。D-20 更把这件事点名交给我：「V2 报差异时由异常处理者按 D-20 的口径补一条同类裁定」，故按规格补 R-C003-V2-triage-a1-1，affected_cases 严格收到 7 个 case#step（不写裸用例 id），retry 即可。

4) 代价与备案（人已可复核，无需本轮决策）：裁判按 case#step 取裁定、不区分正常与故障运行（judge.py `_rulings_in_force`/`judge_unit`），所以这 7 步在正常运行的判定里也因此不再判 fail（仍照常记录差异，只是不判失败）；这 7 步的对外行为仍由 booking-happy-path、booking-cancel-guards、booking-confirm-guards、booking-cancel-twice、booking-cancel-releases-stored-flight 等用例覆盖，bookings-get 的 200 与 403 文案在 booking-happy-path#read-booking 与 booking-cancel-guards/confirm-guards 里仍被断言。若将来要恢复这 7 步在正常运行中的判定，唯一途径是人解冻裁判、改写这三条按 nth 读注入的场景（改判为按目标侧「唯一一次读」注入），那是人的权限，不是分诊或验证线能做的，故在此登记为信息项而非 escalate 理由。

5) 顺带核对：C002 的方案 (A) 工具修复在本轮已生效——涉身份的用例（booking-read-guards、customer-bookings-owner-only、loyalty-sum-and-level 等）不再出现 403/404 假失败，owner/admin 阴性步骤与单体一致；tables_after.Airline-Users 的 1 行 vs 3 行差异仍是 D-4/SI-10 的设计结果（缺行＝groups=[]），属信息项。
- 给 worker 的反馈：按 storage/DECISIONS.md D-20 预留给 V2 分诊的口径补记裁定 R-C003-V2-triage-a1-1（category=implementation-trace，已 decided 入 storage/rulings.json），请整批重跑 run_judge（不要按 functions/cases 过滤，需要完整 verdict）。预期结果：cases 侧不变（pass 34 / migration-change 7 / dropped 14 仍全部通过）；fault_runs 侧这三条按「同一条预订第 n 次读」注入的场景从 fail 转为 ruled 并计入非失败——wb-booking-handler-read-fails-after-authz/booking-read-guards 的 #owner、#other，wb-booking-confirm-read-fails-after-authz/booking-confirm-unconfirmed 的 #confirm、#read-booking，wb-booking-cancel-read-fails-in-service/booking-cancel-and-read 的 #cancel、#read-booking、#read-seat；其余 21 条 fault run 与 7 条 migration-change 判定口径不变（R-A1-a2-1 的 anon 步骤、R-A1-a2-2 的 14 个 dropped 用例照旧）。run_judge 只把 fail/infra_error 计为失败，裁定生效后 V2 应为 PASS。两条反向约束，重跑时若出现新的失败面不要靠这些手段掩盖：(1) 不得为实现线「让注入命中」而给 bookings-get/confirm/cancel 补做第二次/第三次读，也不得把按主键读失败改写成 400 'Invalid booking ID:'——读次数是轨迹（SI-1/D-18），命中的那一步按 SI-13 仍答 404 'Booking not found'；(2) 裁判已冻结，judge/faults/ 与 judge/oracle_expected.json、judge/fault_baseline_monolith.json 一字不改。若重跑后这 7 步之外又出现 fail，那不属于本裁定范围，另开分诊。
- 调度器的处理：同一任务再试一轮

## C004-B0-review（review，任务 B0）

- 决定：ok
- 理由：阶段复查（裁判与增量测试双绿、B0 已打 tag → 进入优化线）未发现需要 retry/reroute/交人的问题。逐项证据（工作区只读）：

1) B0 的成立条件我独立复核过：tag B0=16cf4ff 即 HEAD，工作树干净；verdict_judge status=PASS（cases 34 pass / 7 migration-change / 14 dropped；fault runs 21 pass / 4 migration-change / 4 dropped / 2 ruled，fail=0）；verdict_delta PASS 16/16 且 assets_digest ddb80a4e6583bdea 与当前 verification/delta 一致（verdict_delta 检查项要求的 coverage/summary.json 与 fault_baseline_B0.json 都在，后者由 V3 以 label=B0 记录 20 次运行、infra_errors 为空）——优化线的三个前提（B0 ref、行为基线、覆盖率基线）齐备。

2) 上一轮的两个开口已真正关闭：(a) 人对 C002 的方案 (A) 已按原口径落地（harness 788d9cf：目标侧 id 取 masks.json 的 <id:alias>，退化到 register body 的 userId，judge/ 一字未动），V1 重跑 check_adapter PASS（13 用例/23 数据步，failed={} 且 other_differences={}），V2 的裁判运行是 17:46 裁定之后、18:02 产出，故 V1 与 V2 的身份语义同源；(b) C003 的 R-C003-V2-triage-a1-1 已 decided 入册，category=implementation-trace、deviates_from_monolith=false、affects.cases 严格是 7 个「用例#步骤」（无裸用例 id），三条按 nth 读注入的场景从 fail 转为 ruled/migration-change，且没有出现该裁定范围之外的新失败面。

3) 本轮重跑的只读自查全部 PASS：check_provenance（654 处引用可查证、18 端点字段齐全、unknown_items=3 为白盒注记）、check_judge_assets（0 error 0 warning，55 用例，53/64 必需响应被断言、14 条豁免都写了不可达成因或指向具体故障场景）、check_adapter_map、check_mutant_classes（5 个 survived 全 equivalent，无 gap 未登记）、check_reconcile_classes（differences=0）、check_gap_inventory(require_decided)（22 条 gap 全 decided、open=0）。9 条裁定 status 全 decided、deviates 全 false；两个 spec 室均 closed、无待答 spec-issue；events.jsonl 里 escalate 只有 1 条且已由人答复，无 open_issue / strength_gaps_left；裁判自 15:12:54Z 冻结后未变（事实库 v7 时间在前），冻结之后的 V1/V2/V3 都带 judge_unchanged 并通过。

4) 两处遗留，登记为信息项、不构成本轮处理：
 (a) I3 的 C6/WARNING：SharedUtilsLayer 挂在 14 个函数上，但 output/lambdas 下 0 处 import shared_utils（也无 sys.path 操作），412 行的 output/layers/shared/shared_utils.py 随交付物挂载却无人引用（V3 覆盖率 0/137），响应头/错误映射/403 文案被逐函数重写 14 遍，与 DECISIONS D-22 的措辞不符。不 reroute 实现线的理由：gates.py 第 27-34 行明确把 C4/C5/C6 定为交给 validator 语义判断的 suspects 而非机械 ERROR，I3 核过的实际后果只是未使用的层 + 漂移面，对外行为零差异（裁判 55 用例 + 27 故障场景、增量 16 用例逐符号比头/正文/表状态都 PASS）；我已抽查漂移是否已经发生——bookings-get/confirm/cancel/customers-bookings-list/loyalty-* 的 403 三句文案、ValueError→400{error,type} 映射、createdAt/loyalty.date 的「无 +00:00」形状与 shared_utils 的 now_iso 逐字一致，SI-14 要求的 Content-Type + Access-Control-Allow-Origin:* 在 14 个 handler 里各有一处且被裁判断言通过，即今天没有漂移、不构成正确性风险；而 reroute 会作废刚打的 B0 并强制重跑 I1-shared+I2a/b/c+I3+V1+V2(≈970s)+V3，为一个 WARNING 级项不划算；这件事的处置手段（删掉层引用，或把重复代码收回层里）的收益恰好是包体/init 维度，属优化线的判决对象：§3.1 的一票否决（gate(final) + 回归 + 与 B0 的 run_equivalence 差分 + 改动行覆盖）和逐改动单独提交（s5_report：每个 kept 改动一个 B0 后的 commit）意味着任何改错都会当场回滚，gate 的 C6 suspects 每轮都重新产出、不会漏。交人亦无必要：这是成本/收益权衡，不是事实裁不了的问题。
 (b) C003 已备案的放宽代价仍然成立但无实际暴露：那 7 个「用例#步骤」若将来出现差异，裁判只记录不判 fail。我核对了当前正常运行中这 7 步的 verdict 全是 pass 且 mismatches=0、rulings=None（放宽只在有差异时才生效，未吞掉今天的任何差异），其对外行为另由 booking-happy-path、booking-cancel-guards、booking-confirm-guards、booking-cancel-twice、booking-cancel-releases-stored-flight 严格断言。给优化线留意：不要为了提高 SDK 调用数或并行度而改动 bookings-get/confirm/cancel 这三次同键读的形状——SI-1/D-20 的反向约束仍在，且这 7 步不能作为「守卫/读回仍然正确」的证据，要靠上面那几个用例与 run_equivalence 差分。

结论：放行，按依赖进入优化线（O），随后 C1（gate(final)+judge+delta 全量）、C2 归档。无需 retry、无需 reroute、无需交人。
- 给 worker 的反馈：（无）
- 调度器的处理：继续

