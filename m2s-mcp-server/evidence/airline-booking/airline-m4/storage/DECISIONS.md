# airline-booking → Serverless 架构决策记录（A1）

规格事实来源：`storage/analysis_report.json`（P0 + F2 标注，每条带出处）；蓝图：`storage/blueprint.json`。
本文的 `SI-n` 指 `blueprint.metadata.spec_resolutions[n]`；`GAP-n` 指 `storage/gap_inventory.json` 的条目（决定写在同文件的 `decision` 字段里）。
裁判资产：`judge/cases/`（46 例）、`judge/fixtures/`、`judge/identities.json`、`judge/faults/`（12 场景）。适配映射：`storage/adapter_map.json`。

---

## D-1 一个端点一个 Lambda；14 个 API 函数与它们的来源文件

18 个单体端点里 14 个迁成 14 个独立 Lambda（`trigger_type: "api"`，runtime `python3.12`，handler `handler.lambda_handler`），4 个 `/auth/*` 走 D-3 删除：

| 函数 | 端点 | 单体处理函数 + 被调模块（`entry_point_dependencies`） |
|---|---|---|
| flights-search | GET /flights/search | app.py:38 + services/catalog.py + data/storage.py |
| flights-get | GET /flights/{flight_id} | app.py:55 + catalog + storage |
| flights-reserve | POST /flights/{flight_id}/reserve | app.py:62 + catalog + storage |
| flights-release | POST /flights/{flight_id}/release | app.py:69 + catalog + storage |
| bookings-create | POST /bookings | app.py:77 + booking + catalog + payment + loyalty + storage |
| bookings-get | GET /bookings/{booking_id} | app.py:251 + booking + storage |
| bookings-confirm | POST /bookings/{booking_id}/confirm | app.py:259 + booking + storage |
| bookings-cancel | POST /bookings/{booking_id}/cancel | app.py:271 + booking + catalog + payment + storage |
| customers-bookings-list | GET /customers/{customer_id}/bookings | app.py:309 + booking + storage |
| payments-collect | POST /payments/collect | app.py:319 + payment + storage |
| payments-refund | POST /payments/refund | app.py:334 + payment + storage |
| payments-get | GET /payments/{charge_id} | app.py:349 + payment + storage |
| loyalty-get | GET /loyalty/{customer_id} | app.py:357 + loyalty + storage |
| loyalty-add-points | POST /loyalty/{customer_id}/points | app.py:365 + loyalty + storage |

- 依据：`analysis_report.entry_points[].handler_location` 给出每个端点的行号范围（最宽的 POST /bookings 是 app.py:77-248）；`entry_point_dependencies[].cross_file_calls` 给出每个端点第一层被调模块，全部内联进该函数的 `source_files`，不做内部路由/switch（角色说明的核心原则 1）。
- 七个带 `login_required` / `admin_required` / `owner_or_admin_required` / `booking_owner_or_admin_required` 的端点（bookings-create、bookings-get、bookings-confirm、bookings-cancel、customers-bookings-list、loyalty-get、loyalty-add-points）额外把 `services/auth.py` 列进 `source_files`：**只搬 authorization 判定**（owner 比对、Admin 组判定），不搬令牌校验（D-3/D-5）。
- 命名按「域-动作」，一个业务域一个目录（`storage/work_packages.json.impl_packages`，D-17）。

## D-2 API 类型：HTTP API（payload 1.0），客户端在公网；access token 也能调用

- 决定：`api_gateway.api_type = "http"`、`type = "HTTP"`、`rest_features = []`、`api_type_reason` 见蓝图、`clients = "internet"`、`payload_format_version = "1.0"`。
- 依据：`analysis_report.global_middleware` 只有一项，`headers` 只有 `Access-Control-Allow-Origin: *`（app.py:24 `CORS(app)`，默认配置），按 gate 的 `api_type_needs` 推导不出 `gateway_responses`（该项只在有 ACAO:* 以外的头时才需要）；单体没有 X-Ray / WAF / API Key / 请求模型校验 / 缓存 / 资源策略的要求；`cognito_user_pools_authorizer`（只认 ID token）也没有被要求。选 http 的收益按同应用实测：p50 低约 5ms、每百万请求 $1.00 vs $3.50。
- **HTTP API + JWT 授权器的已知后果（本条就是要求的记录）**：授权器同时接受 Cognito 的 access token 和 id token（REST 的 Cognito 授权器只认 id token），并且 access token 的 claims 里没有 `email` 等用户属性。本设计的函数只依赖 `claims.sub`（预订/积分的 owner 比对），因此两种 token 都能正确工作；groups 不来自 claims（D-4/SI-10）。若将来某个端点要把 `claims.email` 当身份依据，必须先改成 REST 或在函数里拒绝无 email 的令牌。
- `clients = "internet"`：单体为浏览器开放跨域（`CORS(app)`，`global_middleware[0].applies_to = all_routes`），没有证据表明调用方全在同区域。若压测客户端在同区域，S5 可依此再评估。

## D-3 认证基建化：Cognito 替代，四个 /auth/* 端点删除

- 决定：`cognito.enabled = true`；`POST /auth/register`、`POST /auth/login`、`GET /auth/me`、`GET /auth/users` 进 `dropped_functions`（type `endpoint`），理由是 "Replaced by Cognito - Frontend calls AWS Amplify/Cognito SDK directly"；`login_required` / `admin_required` / `owner_or_admin_required` / `booking_owner_or_admin_required` 与 `AuthService` 的签发/验签/口令散列代码一并进 `dropped_functions`（type `code`），理由 "Replaced by API Gateway Cognito Authorizer"。不创建任何 Cognito 触发器 Lambda。
- 依据：`file_tags` 中 app.py / config.py / services/auth.py / init_dynamodb_tables.py 都带 `Auth`；`entry_point_dependencies` 显示 `/auth/login` 的调用是 `authenticate_user` + `create_access_token`（自己验口令、自己签 HS256 令牌，services/auth.py:73-100）。GAP-10 自带规则手册默认做法：`m2s-basic-serverless-kb` Cognito User Pool + "Auth endpoints (/register, /login, /logout, /me) are DELETED"。
- 为什么**不是**「单体自管会话」例外：那个例外的形状是登录端点把会话 id 存进自己的表、中间件按 id 查会话表（如 Acme Air 的 `checkForValidSessionCookie` → `customerSession`）。这里的令牌是无状态签名的 JWT（`decode_token` 只做验签 + exp 检查，services/auth.py:103-112），落库的只有用户凭据记录，没有会话记录；GAP-12 还指出 HS256 密钥在未设环境变量时**每进程随机**（"sessions won't persist across restarts"，config.py:57-60），说明这套令牌本来就不是可移植的对外契约。客户端契约里被保留的是 `Authorization: Bearer <JWT>` 这个形状和 1 小时有效期（`cognito.token_validity`），换签发方不改变它。
- 连带作废的规格条目集中写在 SI-8（含 `POST /auth/register#business_rules[2]` 的同 email 重复注册缺陷、`GET /auth/users#business_rules[3]` 的分页不完整缺陷、`POST /auth/login#business_rules[4]` 的 `expires_in/user.groups` 响应形状）。

## D-4 Users 表：凭据不迁，组归属拆成 UserGroupsTable

- 决定：`Airline-Users-{stage}` 的 `password_hash` / `email` / `created_at` **不进 DynamoDB**（认证数据归 Cognito 用户池，`email` 作为标准属性兼登录名）；表里唯一需要保留的业务数据是组归属，拆成 `UserGroupsTable(sub S 主键, groups L)`。适配映射把单体表映射到这张表：`sub` 按 identity 处理、`groups` 原样复制、其余三个属性 drop（见 `storage/adapter_map.json`）。
- 依据：Users 表处理方式 ②「凭据归 Cognito、业务字段单独建表」。为什么不干脆整表删除、改用 claims 里的 `cognito:groups`：`POST /loyalty/<customer_id>/points#business_rules[0]` 与 `GET /auth/users#business_rules[1]` 都明确规定 Admin 判定读的是**用户表里的当前记录**而不是令牌里的 groups 声明（"改组立刻生效、撤组旧令牌立刻 403"），把 groups 放进 claims 会改掉这条对外行为；而且 `judge/identities.json` 的 admin 别名只由 `judge/fixtures/admin-user.json`（Users 表一行 `groups: ["Admin"]`）准备，目标侧没有别的途径能区分 admin 与普通用户。表读同时满足了契约和裁判。
- `fix_admin_user.py`（直接改 Users 表口令散列与 groups 的运维脚本）随之删除；等价操作是 Cognito 的 `AdminSetUserPassword` + 在 `UserGroupsTable` 写一行 groups。

## D-5 网关鉴权 + 函数内 authorization：auth 字段的取值

- 决定：7 个函数 `auth = "required"`（bookings-create、bookings-get、bookings-confirm、bookings-cancel、customers-bookings-list、loyalty-get、loyalty-add-points），7 个 `auth = "none"`（flights-search、flights-get、flights-reserve、flights-release、payments-collect、payments-refund、payments-get）。流程：`Request → HTTP API（JWT 授权器验 Cognito 令牌）→ Lambda`，函数从 `event.requestContext.authorizer.claims.sub` 取身份，内部**零令牌校验代码**。
- 依据：逐端点采用 `entry_points[].auth.classification`——F2 判定的 `required`（9 个）里 2 个随 `/auth/*` 删除，剩 7 个照抄；`none` 的 9 个里 2 个删除，剩 7 个照抄。没有任何一个端点采用规格之外的取值，也没有 `optional`/`unknown` 需要取舍。`payment`/`flight` 三对端点的 `none` 是单体的真实行为（`POST /payments/collect#business_rules[0]`：「任何请求只要知道 chargeId 就能触发收款」；`POST /flights/<flight_id>/reserve#business_rules[0]` 同理）——照搬，不顺手加锁；这是安全面上的单体缺陷，若要收紧属于 `migration-change`，需要人确认（GAP-10 的 note 已记录这一点）。
- 401 的响应体变化见 SI-9；403/404 的响应体逐字保持见 SI-10，判定实现写在每个函数的 `notes` 里。

## D-6 跨函数通信：一条都不需要（sqs_queues / eventbridge_rules / lambda_invoke_permissions 全为 []）

- 决定：唯一的编排端点 bookings-create 把「扣座 → 建 UNCONFIRMED 预订 → 收款 → 确认 → 加积分 → 通知」六步**全部同步内联**在同一函数里；不发 SQS、不发 EventBridge、不同步 invoke 任何函数。`has_async_flows = false`、`architecture_pattern = "Basic Serverless Architecture"`。
- 依据（逐条按同步/异步三机制的信号判断）：
  - `entry_point_dependencies[POST /bookings].cross_file_calls`：`reserve_booking`、`collect_payment`、`confirm_booking`、`notify_booking` 四条 `return_value_used=true` → 返回值直接进 201 响应体（`POST /bookings#responses[6]` 的 `bookingId/bookingReference/payment/notification`），必须是同步路径 → 内联。
  - `process_booking_loyalty` 与 `reserve_flight_seat`/`release_flight_seat`/`cancel_booking`/`refund_payment` 是 `return_value_used=false, is_awaited=false`。后四条是 Saga 的补偿步骤，与主写同一事务边界（`#business_rules[7]` 要求逐步回滚），只能内联。只有 `process_booking_loyalty`（Step 5，`#business_rules[9]`：失败只记日志、不影响 201）是真正的即发即忘候选。
  - 裁定把 Step 5 也留在同步路径，理由：(1) 单体在返回 201 **之前**完成积分写入，客户端立刻 GET 就能读到——`booking-happy-path` 用例的 `loyalty-after` 步骤断言 `{"points": 150, "level": "bronze", "remainingPoints": 49850}`，`booking-same-charge-twice` 的 `loyalty-after` 断言积分翻倍。改成 SQS 会把这条变成最终一致，属于「单体行为不照搬」，得先要人确认放宽（`relaxation: freshness`）；(2) 积分写入是 `put_item` 追加新行（`LoyaltyService.add_loyalty_points`，storage.py:266-283），**天然不幂等**，一旦走至少一次投递就必须引入幂等表和 TransactWriteItems 去重（m2s-async-patterns-kb 的强制正确性规则），而单体恰恰**要求**重复加分可见（`POST /loyalty/<customer_id>/points#business_rules[7]`、SI-17），去重机制反倒与另一条契约冲突；(3) 这一步只有一次 DynamoDB 写（约 4ms 量级），解耦省不下响应时间，只多一个失败面。
  - 扇出检查：单体没有任何一处「一个动作触发 2 个以上域的独立消费者」，`call_graph` 里 55 条边全是 app.py→services/*→data/storage.py 的纵向调用，EventBridge 无适用对象。
- 后果：本设计没有 SQS/EventBridge 消费者，因此「异步消费者必须幂等」这条上游规则**没有适用对象**；据此也**不**创建 IdempotencyTable（见 D-13）。

## D-7 表结构与用户外键

- 决定：四张表按 `dynamodb_info.table_schemas` 原样落地（`FlightTable` id/S 无 GSI；`BookingTable` id/S + GSI `customer-index`(customer/S)；`LoyaltyTable` id/S + GSI `customer-flag-index`(customerId/S + flag/S)；均 `PAY_PER_REQUEST`），不新增、不改名、不编造属性；另加 `PaymentTable`（D-11）与 `UserGroupsTable`（D-4）。
- 用户外键：单体的 `Airline-Booking.customer` / `Airline-Loyalty.customerId` / `Airline-Users.sub` **本来就已经是字符串 sub**（注册时由客户端提供 `userId`，`POST /bookings#business_rules[4]`：`customer=登录用户 sub`），所以这里不存在「整数 user_id → Cognito Sub」的重键迁移；改变的是 sub 的**签发方**（单体自己生成/客户端指定 → Cognito 用户池的 sub）。属性名保持不变，语义改为「调用方的 `claims.sub`」；bookings-create 写入时一律用 claims.sub 覆写（`#business_rules[1]`）。适配映射按 identity 处理这三个字段（`storage/adapter_map.json`），目标侧用户 id 就是 `judge/identities.json` 里保存的 `body.user.sub`（`cust-u1`/`cust-u2`/`cust-admin`），与 `judge/fixtures/` 里的取值一致，因此夹具条目映射后原样可回。
- 表名不再由 `STAGE` 拼（`f'Airline-Flight-{stage}'`）：目标用 SAM 逻辑名 + 环境变量（`FLIGHT_TABLE_NAME: ${FlightTable}` 等），`config.py` 的 dotenv 机制进 `dropped_functions`（D-10 的配套）。

## D-8 读路径保持 Scan，不借迁移换索引

- 决定：flights-search、customers-bookings-list、loyalty-get 三个列表/求和端点保持「Scan + FilterExpression、只取第一页 Items、不处理 LastEvaluatedKey、无分页参数」；不给航班表加 (departureAirportCode, arrivalAirportCode, departureDate) 的 GSI，也不把预订列表切到已有的 `customer-index`。
- 依据：SI-12 / GAP-3（monolith_defect，规则手册默认做法「照搬单体，要偏离须人确认」）；`GET /flights/search#business_rules[2]` 的 note 要求迁移后外部可观察的过滤语义一致；`GET /customers/<customer_id>/bookings#business_rules[3]`、`GET /loyalty/<customer_id>#business_rules[2]`（积分可能算少）同样是契约。`BookingTable`/`LoyaltyTable` 上那两个 GSI 按 table_schemas 保留但代码不使用（删它属于性能决策，交给 S5 依数据量判断）。

## D-9 全局响应头：只有 ACAO:*，由函数自己带（GM1/GM2）

- 决定：`Access-Control-Allow-Origin: *`（`global_middleware[0]`，headers 仅此一项）**保留**，落地方式：每个 API 函数的返回值都带 `Access-Control-Allow-Origin: *` 与 `Content-Type: application/json`，统一由共用 layer 的响应助手构造；预检 OPTIONS 由 HTTP API 的 `CorsConfiguration`（allow_origins `["*"]`）应答；不写 `GatewayResponses`（HTTP API 没有这个资源，且头只有 ACAO:* 一项，网关自答的 401/429 由 `CorsConfiguration` 覆盖）。14 个函数的 `notes` 都带这一条（SI-14）。
- 没有把该头判为要去掉：去掉跨域属于 `migration-change`，需求里没有这个意图。

## D-10 Flask 全局错误处理器 → 函数内等价映射（GAP-11）

- 决定：`@app.errorhandler(Exception)`（app.py:28-34）不作为全局中间件迁移，但它定义的状态码映射是契约，逐函数内联：`ValueError` → 400 `{'error': str(e), 'type': 'ValueError'}`，其它异常 → 500 `{'error', 'type'}`。因此单体这些「不存在」臂照搬为 400 而不是 404：`GET /flights/<flight_id>#responses[1]`（'Flight with ID … not found'）、`POST /payments/<charge_id>#responses[2]`（'Payment … not found'）、`POST /loyalty/<customer_id>/points#responses[4]`（'Points must be a positive number'）；而 404 只有 booking 装饰器那一处（`GET /bookings/<booking_id>#responses[1]`）。bookings-create 的 KeyError 臂保持 500（SI-3）。
- 单体里 `request.get_json()` 在非 JSON 请求体上抛的 werkzeug 异常 → 500：目标用 `json.loads(body)` 包 try 复现「坏 JSON → 5xx」这一族结果，具体 `error` 文案随解析库不同（F2 在 `POST /auth/register#responses[3]` 的 note 里已标 framework-related；随端点删除而作废的只有 /auth/*，其余端点保持 500 语义）。

## D-11 支付：进程内状态迁成 PaymentTable，Stripe 分支不迁

- 决定：(a) `PaymentService._payments` 这份唯一的收款记录存放处迁到 `PaymentTable(chargeId S)`，条目形状照搬内存记录 `{chargeId, receiptUrl, price, status, mode}`；payments-collect `PutItem`（重复收款整条覆盖），payments-refund 条件 `UpdateItem`（`attribute_exists(chargeId)`，失败=什么都没发生过，绝不新建条目），payments-get `GetItem`。(b) `PaymentService._refunds` **不持久化**：没有任何端点读它（`services/payment.py:296-300` 写、无读点）。(c) Stripe 分支整体不迁：不装 `stripe`、不读 `STRIPE_SECRET_KEY`，只实现单体的默认运行态模拟分支（SI-7）。
- 依据：GAP-5 带规则手册默认做法（m2s-basic-serverless-kb：跨调用共享的数据放 DynamoDB/S3，不放函数实例）；SI-6 把「放内存里」判为轨迹并把契约钉成四条可观察结果。可测性：`collect-then-read-payment#read-back`、`refund-then-read-payment#read-back`、`payment-read-missing#read-present` 在目标上每个步骤都是独立调用（不保证同实例），只有落到表里才稳定成立——所以 GAP-5 的 `test` 记 `judge`。GAP-21/GAP-5 里「同一进程才读得到」这一点确实与单体不同，但单体那半是可变的进程状态而不是契约（`GET /payments/<charge_id>#business_rules[1]` 的 note 已说明形状随写入路径变化）。

## D-12 去掉导入即写库的种子数据（GAP-14 / GAP-15）

- 决定：目标的模块导入路径不写任何条目：不写 FL001/FL002（`storage._init_sample_data`，storage.py:32 在构造函数里调用），也不建 user@example.com / admin@example.com（`AuthService._init_default_users`，auth.py:247 模块级调用）。航班目录由部署侧数据或测试夹具提供；表为空时 flights-search 返回 `200 {'flights': []}`、flights-get 返回 400 not found（SI-11）。
- 依据：这是单体作为长进程白得的行为（gap 类别 `files_and_connections`）；`judge/fixtures/flights.json` 等已为每个用显式播种航班，没有任何用例依赖导入期种子。Cognito 侧账号由管理员建立，`judge/identities.json` 的三个别名通过 `adapter_map.identities` 映射（D-14）。

## D-13 幂等与失败上报：本设计没有需要它的地方

- 决定：不引入幂等表、不加 `batchItemFailures`、不给任何写操作加条件去重。
- 依据：m2s-async-patterns-kb 的强制规则只约束 SQS/EventBridge 消费者；本设计零消费者（D-6）。反过来，单体的「重复调用产生重复效果」是明确契约：`POST /bookings#business_rules[13]`（同一 chargeId 重复下单重复扣座重复收款）、`POST /loyalty/<customer_id>/points#business_rules[7]`（加分两次就翻倍）、`POST /payments/collect#business_rules[7]`、`POST /bookings/<booking_id>/cancel#business_rules[5]`（重复取消重复退款）——加幂等会直接违反这些条目（SI-17）。
- 失败上报：单体所有吞失败的补偿（`#business_rules[7]`/`#business_rules[9]`/`#business_rules[11]`、SI-18）保持吞（只写日志、响应照旧），因为这是可观察的响应内容与表状态本身。

## D-14 契约 2：分类复核、trace_arm 裁定、顺序裁定

- `metadata.verification_contract = 2`（CT1）：`entry_points[].business_rules` 全部带 `class`（事实库 v6：126 条 contract、7 条 implementation-trace；`responses[].trace_arm` 5 条）。
- 复核结论：7 条 implementation-trace 中 3 条属于被删除的 `/auth/*`（`POST /auth/login#business_rules[5]`、`GET /auth/me#business_rules[6]`、`GET /auth/users#business_rules[8]`——v6 之后种子那条从 [7] 移到 [8]，见 D-21），随 SI-8/SI-11 作废；`POST /flights/<flight_id>/release#business_rules[3]`、`GET /bookings/<booking_id>#business_rules[4]`、`POST /bookings/<booking_id>/confirm#business_rules[5]`、`POST /bookings/<booking_id>/cancel#business_rules[8]` 四条同意分类（读次数、先读后写是做法，文案与状态码才是契约）。
- 反向纠正 3 条：`POST /payments/collect#business_rules[3]`、`POST /payments/refund#business_rules[3]`、`GET /payments/<charge_id>#business_rules[1]` 被 S0 标成 contract，但它们规定的是「状态放在进程内存里」这个单体做法，判为 implementation-trace（SI-6）；对应的可观察结果（收款后可读回、未收款 400、退款恒成功）另立 contract 条目保住。
- 5 条 `trace_arm` 全部裁定（CT2/CT3）：SI-1 作废四条「同一条预订在同一次请求里被连读多次、后面某一次落空」的臂——`GET /bookings/<booking_id>#responses[4]`、`POST /bookings/<booking_id>/confirm#responses[4]`、`POST /bookings/<booking_id>/cancel#responses[4]`（第二次读落空）与 `POST /bookings/<booking_id>/cancel#responses[6]`（第三次读落空，文案 `'Invalid booking ID: <booking_id>'`；事实库 v6 按 J4-2 新增的臂，第 4 次尝试回炉补裁）。bookings-get / bookings-confirm / bookings-cancel 三个函数各只按 id 读一次条目，同一份条目既做 owner/admin 判定又做业务逻辑（`POST /bookings/<booking_id>/cancel#business_rules[8]` 自己写明「三次读的是同一个 Key，合成一次读对外部效果相同」），所以第二、第三次读在目标里不存在，它们对应的 400 也不存在。SI-2 保留 confirm 的条件写失败臂并把结果钉死为 400 `{'error': 'Booking <id> not found', 'type': 'ValueError'}`（故障场景 booking-confirm-update-fails 依赖它）。作废臂对故障场景的后果见 D-20。
- 顺序（J3 的 needs_ruling / 讨论室 spec-j3-r1）：`GET /auth/users` 的 `users` 数组顺序不是契约（`GET /auth/users#business_rules[2]` 明写「顺序就是 scan 返回顺序，不属于对外承诺」），已由在案裁定 **R-ROOM-spec-j3-r1-1-1-a1-1**（收尾 **R-ROOM-spec-j3-r1-1-2-a1-1**）关闭：category=contract、`relaxation=order`、layers=[R]、affected_cases=`register-read-back-by-admin#list`（带步骤；裸用例 id 会把放宽铺到全用例，不做）。并且该端点被删除（D-3），这一步本就不在目标上运行。**其它列表端点不援用这条放宽**：flights-search 的 `flights`、customers-bookings-list 的 `bookings` 仍按现有口径严格逐项比较（m2s-verifier 的机器核对：46 个用例里 ≥2 个对象元素的嵌套数组断言只有那 1 处）；A1 自己误落的一条更宽裁定已由 **R-A1-a2-4** 收窄作废。GAP-18 的决定只写实现约束：目标不新增排序，也不为匹配字面次序加 `sorted()`。

## D-15 适配映射（storage/adapter_map.json）与缺口决定

- `tables`：`Airline-Flight-{stage}` → FlightTable、`Airline-Booking-{stage}` → BookingTable（`customer` 标 identity）、`Airline-Loyalty-{stage}` → LoyaltyTable（`customerId` 标 identity）、`Airline-Users-{stage}` → UserGroupsTable（`sub` 标 identity、`groups` 原样、`email`/`password_hash`/`created_at` drop，理由：凭据归 Cognito，D-4）。
- `identities`：`claims.sub = ${id}`（裁判在目标侧以网关已验证身份的形状注入 claims；`${id}` 就是 `judge/identities.json` 登录步骤保存的 `body.user.sub`），并带 `${email}`；不提供 `seed`（u1/u2 在目标侧无需任何用户行，缺 `UserGroupsTable` 行 = groups []，见 D-4）；admin 的组归属来自 `admin-user` 夹具经表映射写入。
- `dropped_endpoints`：四个 `/auth/*`，与 `dropped_functions` 的 type `endpoint` 条目一一对应。
- `target_only_tables`：PaymentTable（D-11）。UserGroupsTable 不是 target-only，它是 `Airline-Users-{stage}` 的映射目标。
- 22 条缺口全部 `decided`，逐条写在 `storage/gap_inventory.json`，并按要求在本文提到编号：GAP-1/GAP-2/GAP-19/GAP-20 → D-6 + SI-18 + SI-17（多写不原子、补偿失败仍吞，照搬单体）；GAP-3/GAP-4 → D-8；GAP-5 → D-11；GAP-6/GAP-7/GAP-8/GAP-22 → D-16（时间戳形态，SI-15）；GAP-9 → D-9；GAP-10/GAP-12/GAP-13 → D-3/D-4/D-5 + SI-8/SI-9/SI-10；GAP-11 → D-10；GAP-14/GAP-15 → D-12；GAP-16 → SI-13；GAP-17 → D-16（read-after-write：同一函数内用条件写代替「写完再读回来确认」，或读回时 `ConsistentRead=true`，保证 Step 4 不因最终一致性失败）；GAP-18 → D-14；GAP-21 → D-11 + SI-7。

## D-16 时间、read-after-write 与数值形状

- 时间戳：`createdAt` / `date` 写「不带时区后缀的 ISO-8601 字符串」，与单体 `datetime.now().isoformat()` 同形；Lambda 本地时区即 UTC，所以实现统一取 UTC 当下时间（SI-15）。JWT 的 `iat/exp` 与 `created_at` 随认证迁移到 Cognito（SI-8）。GAP-6/GAP-7/GAP-8 的裁定：值本身按符号掩码比较（`judge/masks.json` 里 `class: "time"`），形状不得改变；`fix_admin_user.py` 的时间戳不在请求路径上（GAP-7 → D-4 该脚本删除）。
- GAP-17（写完立刻读回）：单体在 POST /bookings 里 `put_item` 之后用不带 ConsistentRead 的 `get_item` 读回同一条（Step 4），读不到就走退款+回滚+500。目标**不得**依赖这种「恰好读得到」：Step 4 直接对同一条目做条件 `UpdateItem`（`attribute_exists(id)`，本来就有），需要条目内容时要么复用 Step 2 已构造的对象、要么 `ConsistentRead=true`。这样单体的成功路径（201 + CONFIRMED）在最终一致性下仍然成立——外部契约是「201 时预订已 CONFIRMED」，不是「中间读过一次」。
- 数值：`Decimal` → 整数还原成 int、非整数还原成 float（SI-16），写库前 `float → Decimal(str(v))`。适用于 flights-search / flights-get / bookings-get 的条目与 loyalty-get 的 `points`/`remainingPoints`。

## D-17 工作包与领域目录（storage/work_packages.json.impl_packages）

- `identity` 包（4 个 `/auth/*` 端点）→ `functions: []`，`dir: "identity"`：这些端点全部由 Cognito 取代（D-3），该包没有实现工作，只剩 Cognito 用户池/用户组配置（在模板里，不属于任何函数）。
- `bookings-customers-flights-loyalty-payments` 包（14 个端点）→ 14 个函数，`dir: "bookings_flights"`。
- 共用代码：`airline-booking/services/auth.py` 的 `admin_required` / `login_required`（F2 标为两个包共用）在目标里只剩 authorization 判定，且 `login_required` 的令牌部分整体作废（D-3/D-5）——因此它落在 `identity` 包之外的共用 layer（响应/错误助手 + `require_admin` / `require_owner_or_admin` / `require_booking_owner` 三个判定函数），不列进 `impl_packages`（共享层是单独的任务）。

## D-18 迁移带来的对外变化（已用 record_ruling 落进 storage/rulings.json）

四条 A1 裁定：`R-A1-a2-1`（未认证 401 由网关应答，body 形状改变，精确到 9 个匿名/坏头/伪造步骤）、`R-A1-a2-2`（四个 /auth/* 端点与身份准备机制由 Cognito 替代，12 个用例整案不在目标上运行）、`R-A1-a2-3`（列表顺序放宽，与在案 R-ROOM-spec-j3-r1-1-1-a1-1 重复）、`R-A1-a2-4`（**更正**：作废 R-A1-a2-3 里多出的两个裸用例 id，顺序放宽的唯一范围仍是 `register-read-back-by-admin#list`）。下表第 5/6/7 行（支付状态跨实例、Stripe 臂、种子条目）**没有**预先放宽：三者都不会在裁判的比较里产生差异——支付读回在两侧都成功、Stripe 臂在录制时已判不可达（judge/uncovered.json）、种子条目被「每用例先清表再播种」清掉（见 GAP-14/GAP-15 的 test=none 理由）。「宁可少写」，这些留给异常处理者按需补。


| # | 变化 | 类别 | 影响 |
|---|---|---|---|
| 1 | 未认证请求由网关授权器应答 401，body 是网关默认的 `{"message": …}`，不再是单体的三句 `{"error": …}` | migration-change（规则手册默认做法，deviates=false） | SI-9；`booking-auth-guards#anon/#bad-format/#forged`、`booking-read-guards#anon`、`booking-confirm-guards#anon`、`booking-cancel-guards#anon`、`customer-bookings-owner-only#anon`、`loyalty-sum-and-level#anon`、`points-guards#anon` |
| 2 | 已删除路由（`/auth/register`、`/auth/login`、`/auth/me`、`/auth/users`）由网关应答 404 `{"message": "Not Found"}` | migration-change（同上） | 12 个 /auth/* 用例整体不在目标上运行（`adapter_map.dropped_endpoints`） |
| 3 | 身份来源改为 Cognito sub；「用户被删后旧令牌立刻 401」不再复刻（令牌在 exp 内始终有效） | migration-change（deviates=false，Cognito 替代的直接后果） | SI-10；`me-token-of-deleted-or-forged-sub#forged-token` 属于已删除端点 |
| 4 | 管理员组归属：`Airline-Users.groups` → Cognito 组 + `UserGroupsTable.groups`（每请求查表，403 文案不变） | contract（对外行为不变，只是数据来源） | D-4；`points-guards#user-token` 等 403 步骤应逐字匹配 |
| 5 | 支付状态跨实例可见（单体只在同一进程内可见） | contract + implementation-trace（SI-6） | 无差异方向：目标让 `#read-back` 更稳；不放宽任何断言 |
| 6 | Stripe 相关三臂不可达（不迁外部依赖） | migration-change（deviates=false） | SI-7；`judge/uncovered.json` 已判这三臂在本运行态不可达 |
| 7 | 导入期种子条目不再产生 | migration-change（deviates=false） | SI-11；无用例依赖 |
| 8 | 列表元素顺序不作要求（内容/数量/字段仍逐字比） | contract + `relaxation: order` | D-14；`register-read-back-by-admin#list`（该步同时因端点删除而不运行） |
| 9 | 合并读之后，「第 n 次读同一条预订」的注入场景在目标上落点不同（第 2/3 次读不存在） | implementation-trace（SI-1 的直接后果，判定口径见 D-20） | **不预先落裁定**：裁判的裁定按「用例#步骤」生效且不分故障/正常运行，写进去会连正常步骤一起放过（宁可少写）；V2 报差异时由异常处理者按 D-20 的口径补一条同类裁定，或由验证线撤掉这三条注入场景 |

## D-19 POST /bookings 的两条 TypeError 臂怎么在目标上复现（SI-19）

- 决定（`metadata.spec_resolutions` SI-19，class=contract）：
  1. **chargeId 按可哈希性分档**（`POST /bookings#business_rules[15]`）——可哈希标量（非空串/int/float）→ 201，`paymentToken` 与 `receiptUrl` 都跟随输入值（15.5 → `.../receipts/15.5`）、`price` 恒 150；假值（空串/空数组/空对象/0）→ Step 2 照常写入、Step 3 抛 `ValueError('Invalid Charge ID')` → 400 + 回滚（SI-4）；**非空数组/对象**（不可哈希）→ Step 2 照常写入，Step 3 在返回之前必须把 chargeId 当键用（单体是 `PaymentService._payments[charge_id]`，services/payment.py:21/:177；目标是 PaymentTable 的主键，主键只能是标量），**实现要在写 PaymentTable 之前显式判可哈希性并抛出与单体同文的 `TypeError("unhashable type: 'list'")`**，让它逃过 Step 3 的 `except ValueError`、落进最外层 catch-all → `500 {'error': "Unexpected error during booking: unhashable type: 'list'", 'message': 'The booking has been rolled back. Please try again.'}`，回滚照常（预订被打成 CANCELLED、座位放回、PaymentTable 不留这笔收款），响应体不含 bookingId。
  2. **请求体是非空但非 JSON 对象的 JSON**（`#business_rules[16]`、`#responses[8]`）→ 不给 400 `'Request body is required'`，并把「用已验证身份覆写 customerId」（`data['customerId'] = claims.sub`，app.py:95）留在业务 try 之外，让它自然抛 TypeError → 全局错误处理映射成 `500 {'error': <str(e)>, 'type': 'TypeError'}`，四张表零读零写。JSON 假值仍走 400。
- 依据：J4 登记的 `judge/spec_issues.json` J4-1/J4-4（「代码里有、事实库没有」）已由 F2 收进事实库并标 `class: contract`；`judge/reconcile.json` 现有 5 条差异正是这一臂（J3 判为 predicted 落后于事实库，用例 `wb-booking-charge-id-non-string-stored-verbatim`、`wb-booking-empty-charge-rollback-and-array-body`）。
- 考虑过的其他方案：让 SDK 报错顶替（ParamValidationError 的文本与单体不同，500 的 body 会对不上）；把它「修」成 400 校验错（单体缺陷照搬原则，Q10 / SI-3 同口径）。
- 影响的函数：bookings-create。

## D-20 作废的读臂对故障场景的后果（第 4 次尝试补写）

- 事实：单体在 GET /bookings、confirm、cancel 里对同一条预订连读 2~3 次，裁判用三条按「第 n 次读」注入的场景记录这些臂的单体输出（`judge/faults/wb-booking-handler-read-fails-after-authz.json` nth=2、`wb-booking-confirm-read-fails-after-authz.json` nth=2、`wb-booking-cancel-read-fails-in-service.json` nth=3；`judge/fault_baseline_monolith.json` 里三条的落点分别是 `booking-read-guards#owner` 400、`booking-confirm-unconfirmed#confirm` 400、`booking-cancel-and-read#cancel` 400）。
- 决定：目标只做一次读（SI-1），因此这三条注入在目标上要么命中不到（`booking-cancel-and-read` 全案对该条目 2 次读 < nth=3），要么落到别的步骤（`booking-read-guards` 的第 2 次读落在 `#other` 那次 GET、`booking-confirm-unconfirmed` 的第 2 次读落在 `#read-booking`）。命中之后由 **SI-13 的读失败语义**回答（按主键读失败＝与「不存在」同一结果 → 404 `'Booking not found'`），不会再产出 `'Booking <id> not found'` / `'Invalid booking ID: <id>'` 这两句 400。
- 为什么不预先写放宽裁定：`judge_unit` 按「用例#步骤」取裁定，且不区分正常运行与故障运行；把 `booking-cancel-and-read#cancel` 这类核心步骤预先标成 implementation-trace，等于同时放过该步骤在正常运行里的任何缺陷——违反「宁可少写」。留给 V2 报差异之后按本节口径补裁定。
- 反向约束：实现不得为了让注入命中而补做冗余读（那会把已被裁定为实现轨迹的做法重新变成必经步骤，perf 规则也明令禁止靠加读来凑数）。
- 影响的函数：bookings-get、bookings-confirm、bookings-cancel。

## D-21 被删除端点的种子账号臂（SI-11 的 targets 补一条）

- 决定：`GET /auth/users#business_rules[8]`（空用户表上首次列出 `user123`/`admin123`，来源是 `AuthService._init_default_users` 的导入期播种，S0 标 implementation-trace）并入 SI-11 的 targets，与 D-12 同一口径作废：目标没有任何导入期写入，账号由 Cognito 侧建立，裁判用 `judge/fixtures` + `adapter_map.identities` 准备身份。
- 依据：`judge/spec_issues.json` 之外的事实修订（v6）把这条播种臂单独列出；它不影响任何保留端点的对外行为（`GET /auth/users` 已被删除，见 dropped_functions）。
- 影响的函数：无（该端点没有对应的目标函数）。
- 引用勘误（同一条事实修订留下的）：SI-8 的 targets/rationale 里写的 `GET /auth/users#business_rules[7]` 是 v5 的索引；v6 之后 [8] 才是导入期播种那条（implementation-trace，由 SI-11 作废），现在的 [7] 是「用户记录缺 `email` 键 → `KeyError` → 500 `{error, type}`」（contract），它随整个端点一起不提供，SI-8 把它列进 targets 的结论不变。`POST /auth/login#business_rules[5]`、`GET /auth/me#business_rules[6]` 两个索引未变。

## D-22 共用 layer 的落地（I1-shared 产出，包任务按此引用）

- 决定：D-17 交给共享层的那部分已经写好，只有一个文件、一个模块：**`output/layers/shared/shared_utils.py`**，导入名 `shared_utils`。运行时 python3.12 + layer 的 `Metadata.BuildMethod: python3.12`，所以 `.py` 放在 ContentUri 根上，不再套一层 `python/`（C13；`sam build` 会自己把它放进 `/opt/python`）。包任务的 handler 用 `import shared_utils as su` 复用下面这些，**不要**在 `output/layers/shared/` 下另写文件、也不要在各自目录里复制一份响应/鉴权助手（同一份契约写 14 遍必然漂移）；确实要改这个模块的语义，回 I1-shared 改一处。
- 模块提供的东西与各自的出处：
  - `GLOBAL_RESPONSE_HEADERS` / `response_headers()` / `json_response(payload, status_code, headers=None)` / `error_response(status, message, error_type=None, **extra)`：每个响应的 `Content-Type: application/json` + `Access-Control-Allow-Origin: *`（`analysis_report.global_middleware[0]` = `CORS(app)`，SI-14 / D-9；GM1 逐函数检查，函数没写头而只靠模板 Cors 块不算过）。403/404 的响应体只有 `error` 一个键时不要传 `error_type`——单体装饰器产出的就是 `{'error': ...}`。
  - `error_from_exception(exc)`：单体 `@app.errorhandler(Exception)`（app.py:28-34）的状态码映射 `ValueError → 400 {'error','type'}`、其它 `→ 500 {'error','type'}`（D-10 / GAP-11）。需要不同状态码的那几臂（SI-2 的条件写失败、SI-13 的降级读、SI-3 的 500 catch-all）在函数里自己先接住，再落到这里。
  - `InvalidRequestJson` + `parse_json_body(event)`：无 body/空 body 返回 `None`（让函数写自己的『Request body is required』/『points is required』那一臂），坏 JSON 抛 `InvalidRequestJson`（非 ValueError）→ 经 `error_from_exception` 得 500，与 D-10「坏 JSON → 5xx」同口径；数组/标量 JSON 原样返回（SI-19 那两条 TypeError 臂要用）。
  - `get_claims(event)` / `get_user_id(event)`：身份只从 `event.requestContext.authorizer.claims` 读，用户 id 用 `sub`（D-5 / SI-10；不用 `email`，HTTP API 的 access token 里没有它；不解析 `exp`/`iat`，网关已验，SI-9）。
  - `require_admin(event)`、`require_owner_or_admin(event, owner_id)`、`require_booking_owner(event, booking)`：单体 `services/auth.py` 四个装饰器里唯一保留下来的 authorization 判定（D-3 / D-5 / SI-10 / D-17），返回 `(user_id, None)` 或 `(None, 该直接返回的响应)`，形状沿用单体的 `_get_authenticated_user`。三句 403 与那句 404 的文案由 SI-10 要求逐字保持，写死在模块里：`Admin privileges required` / `Access denied. You can only access your own resources.` / `Access denied. You can only access your own bookings.` / `Booking not found`。
  - `get_groups(user_id)` / `is_admin(user_id)`：Admin 判定每请求读一次 `UserGroupsTable`（表名来自环境变量 `USER_GROUPS_TABLE_NAME`，blueprint 只给了真正要判定的 6 个函数：bookings-get、bookings-confirm、bookings-cancel、customers-bookings-list、loyalty-get、loyalty-add-points——**bookings-create 没有这个环境变量，也不该调 `require_*`**，它的身份只是覆写 `customerId`）。读不到行、`groups` 缺键、或这次读本身失败，一律按 `[]`（非 Admin）处理，绝不 401、绝不 5xx（SI-10「缺行视为 groups=[]」、SI-13 的降级读）。判定不读 claims 里的组声明（D-4：改组要立刻生效；`adapter_map.identities` 也特意不往 claims 放 `cognito:groups`）。
  - `to_jsonable(obj)` / `to_storable(obj)`：`Decimal` → 整数还原 `int`、非整数 `float`（响应侧，`json_response` 已自动套用，SI-16 / D-16）；`float` → `Decimal(str(v))`（写库侧，PutItem/UpdateItem 前自己套）。
  - `now_iso()`：不带时区后缀的 ISO-8601（`booking.createdAt`、`loyalty.date`；SI-15 / D-16，带 `+00:00` 会破坏裁判的 `<time#N>` 符号匹配）。
  - `new_uuid()` / `random_token(nbytes)`：条目 id 用 `uuid4` 串，`bookingReference` 用 `random_token(4)`、`notificationId` 用 `random_token(16)`（SI-5）。
  - `dynamodb_resource()`：懒建的 boto3 DynamoDB resource（只有连接，没有任何请求间状态；业务状态一律进表，D-11 / GAP-5）。不写 `endpoint_url`——验证运行器用 `AWS_ENDPOINT_URL_DYNAMODB` 指本地 DynamoDB，botocore 自己认（已用 dynalite 实测 `sub` 主键读、`L`/`SS` 两种 `groups`、缺行与读失败四条路径）。
- 依据：D-17 把 `admin_required` / `login_required` 的剩余语义划给共享层；SI-14 明写「放在共用 layer 的响应助手里统一构造」；SI-10/D-5 明写 403/404 逐字保持且判定留在函数内。owner/admin 门槛先比 owner 再查组：owner 相符时结果已定，那次组读与结论无关——读几次是 SI-1 判过的实现轨迹，不是契约。
- 影响的函数：14 个 API 函数全部（响应头 + 错误映射 + 数值/时间形状）；其中 6 个用 `require_*` 与 `is_admin`。
