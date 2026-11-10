# airline-booking 迁移架构决策（A1 / m2s-architect，第 1 次尝试）

事实来源：`storage/analysis_report.json`（P0+S0 规格，每条带出处）、`storage/gap_inventory.json`（G1+G2 缺口）、
`judge/`（冻结裁判的 identities / fixtures / cases / faults / oracle / masks）。
下文 `METHOD path#responses[i]`、`#business_rules[j]` 都指事实库条目；`SI-n` = `blueprint.metadata.spec_resolutions[n]`；
`GAP-n` = `storage/gap_inventory.json` 条目；`judge/...` = 冻结裁判资产。

---

## D-1 认证基建化：四个 /auth 端点删除，网关 Cognito 授权器负责鉴权

- 决定：`file_tags` 里所有文件都带 `Auth` → `cognito.enabled = true`。`POST /auth/register`、`POST /auth/login`、
  `GET /auth/me`、`GET /auth/users` 进 `dropped_functions`（type=endpoint，理由 “Replaced by Cognito - Frontend
  calls AWS Amplify/Cognito SDK directly”）；鉴权中间件 `login_required` / `admin_required` /
  `owner_or_admin_required` / `booking_owner_or_admin_required` / `_get_authenticated_user` 与 `AuthService` 的
  register/authenticate/create_access_token/decode_token/get_user_from_token/is_admin/get_all_users/
  add_to_admin_group/_hash_password/verify_password/_init_default_users 全部进 `dropped_functions`（type=code）。
  七个受保护函数 `auth=required`，链路是 请求 → API Gateway（Cognito 授权器验令牌）→ Lambda（纯业务逻辑、零鉴权代码），
  身份从 `event.requestContext.authorizer.claims.sub` 取。授权判定（本人 / Admin）是业务规则，留在函数里（D-3）。
  不建任何 Cognito 触发器 Lambda。
- 为什么不套用“单体自管会话”的例外：`services/auth.py:71-116` 是**无状态 HS256 JWT 自签自验**，库里没有会话表；
  users 行只回答“这个 sub 还存在吗”和“他的 groups 是什么”，客户端拿的是令牌而不是服务端会话 id
  ——与 Acme Air 那种 `checkForValidSessionCookie` 查 `customerSession` 的形态不同，所以走默认做法。
- 依据：GAP-10、GAP-13（`auth.py:19` SECRET_KEY 未配时每次进程启动随机、`auth.py:21` 60 分钟 exp）；
  角色规则手册“认证基建化，删除而非迁移”“网关鉴权”。
- 考虑过的其他方案：保留单体的 JWT 机制（`cognito.enabled=false`、`authorizer=None`、函数内自校验）。否决理由：
  裁判的身份注入只在“路由需要鉴权”时把 claims 交给函数（`m2s-mcp-server/judge.py` 的 `_unit_identities` +
  `verify-tools/judge-run.js` 的 `buildEvent`），而 `judge/identities.json` 用的是 register+login 步骤（不是自签
  secret 模式），目标侧只会拿到一条替换过的 Authorization 头；把 JWT 校验留在函数里等于所有带身份的 40 多个步骤都
  要函数自己去验一个并不存在的真令牌。
- 影响的函数：bookings-create、booking-get、booking-confirm、booking-cancel、customer-bookings-list、loyalty-get、
  loyalty-add-points（auth=required）；flights-search、flight-get、flight-reserve、flight-release、payments-collect、
  payments-refund、payment-get（auth=none，照 `auth.classification=none` 采用）。关联 SI-6、SI-13。

## D-2 一个端点一个 Lambda；同域 service 代码内联，不拆内部 Lambda、不做同步互调

- 决定：14 个保留端点各一个 Lambda —— `flights-search`、`flight-get`、`flight-reserve`、`flight-release`、
  `bookings-create`、`booking-get`、`booking-confirm`、`booking-cancel`、`customer-bookings-list`、
  `payments-collect`、`payments-refund`、`payment-get`、`loyalty-get`、`loyalty-add-points`；
  外加一个 SQS 消费者 `loyalty-award-points`，共 15 个函数，runtime 全部 `python3.12`（单体是 Flask + boto3 Python）。
  `entry_point_dependencies` 里 14 个端点的 cross_file_calls 全都只是 handler → service → `data/storage.py` 的
  **同域一层调用**，没有任何跨 Lambda 的 SDK 调用，因此这些模块一律作为 `source_files` 内联进各自的包；
  `lambda_invoke_permissions = []`。
- Orchestrator 与 Worker：`bookings-create` 名义上是 orchestrator（协调 catalog / booking / payment / loyalty 四个域），
  但按“同步路径内联保事务性”的规则，占座→建单→收款→确认四步全部内联（D-6），只有积分那一臂摘成消息（D-5）；
  其余 13 个都是单域 Worker。
- 影响的函数：全部 15 个。

## D-3 users 表拆分：凭据归 Cognito，授权属性 groups 留在 DynamoDB（UserGroupsTable）

- 决定：`Airline-Users-{stage}` 的 `password_hash` 绝不进 DynamoDB（凭据归用户池），`email`、`created_at` 用 Cognito
  标准属性（适配映射里 drop 掉）；**`groups` 是授权数据不是凭据**，拆成新表 `UserGroupsTable`
  （PK `sub`(S)，值 = 业务用户 id；属性 `groups` = 组名字符串数组，单体用的名字是 `Admin`）。
- 为什么不用令牌里的 `cognito:groups` 判 Admin：
  1. 单体的 Admin 判定每次请求读的都是**库里的 groups**（`services/auth.py:137`
     `if 'Admin' not in current_user.get('groups', [])`；GAP-13 明确写“改权限改的是库里的 groups，不是 JWT 里那份”），
     换成令牌声明会带来一条单体没有的对外变化（改组要重新登录才生效）。
  2. 适配映射的 `identities.claims` 是所有身份别名共用的一份模板（`adapter.py::target_identity`），无法只给 `admin`
     这一个别名注入组声明；而裁判正是用 `judge/fixtures/users-admin.json`（sub=judge-admin、groups=["Admin"]）
     在**数据**上造出管理员的 —— 用 UserGroupsTable 才能原样承接这份资产。
- 于是七个受保护函数的写法固定：本人判定用 `claims.sub`（不读表）；需要 Admin 时才 `GetItem(UserGroupsTable, sub)`，
  读不到条目或读失败都按 `groups=[]` 处理，**绝不因此 401**（SI-13）。
- 依据：`POST /loyalty/<customer_id>/points#business_rules[0]`、`GET /loyalty/<customer_id>#business_rules[0]`、
  `GET /customers/<customer_id>/bookings#business_rules[0]`、`GET /bookings/<booking_id>#business_rules[1]`、
  `POST /bookings/<booking_id>/confirm#business_rules[0]`、`POST /bookings/<booking_id>/cancel#business_rules[0]`。
- 影响的函数：booking-get、booking-confirm、booking-cancel、customer-bookings-list、loyalty-get、loyalty-add-points。
  关联 GAP-10、GAP-13、SI-6、SI-7。

## D-4 业务用户 id 沿用单体里客户端自选的那个字符串

- 决定：`booking.customer`、`loyalty.customerId`、路径里的 `customer_id` 继续用单体的用户 id 字符串
  （`auth.py:52` `user_id = data.get('userId') or secrets.token_urlsafe(16)`，例 `judge-u1`）；函数一律读
  `claims.sub` 当这个值，`POST /bookings` 也把 `customer` 写成它。适配映射里 `user_id` 与 `claims.sub`
  都渲染成 `judge-${alias}`：u1→judge-u1、u2→judge-u2、admin→judge-admin（分别等于
  `judge/identities.json` 里 u1/u2 注册请求体的 `userId` 和 `judge/fixtures/users-admin.json` 里的 `sub`）。
- 这是契约而不是实现细节：这个 id 出现在请求路径字面（`/customers/judge-u1/bookings`、
  `/loyalty/judge-cust-gold/points`）、写进 fixture（`bookings-owned.json` 的 `customer`）并出现在预期体里
  （`POST /bookings#responses[8]` 的 `customer=<id:u1>`、`GET /customers/...` 的 `customer=<id:u1>`、
  `notification.customerId=<id:u1>`）。把外键换成池生成的 UUID 会改掉这些对外可见值。
- 部署口径（已写进 `cognito.username_note` 与映射的 note）：这个字符串在用户池里就是 username；若真实部署改用池自生成
  的 UUID 作 `sub`，需要一次性改写业务表的 `customer`/`customerId`，函数读 `claims.sub` 的代码不变。
- 影响的函数：bookings-create、booking-get、booking-confirm、booking-cancel、customer-bookings-list、loyalty-get、
  loyalty-add-points、loyalty-award-points。关联 SI-7、GAP-21。

## D-5 唯一的跨函数异步边：下单的积分副作用 → SQS，消费者用确定键天然幂等

- 决定：`bookings-create` 的第 5 步改成 `SendMessage(LoyaltyPointsQueue, {bookingId, customerId, points})`；
  消费者 `loyalty-award-points`（`trigger_type=sqs`、`sqs_source=LoyaltyPointsQueue`）用**整条 PutItem 写确定的键**
  （`LoyaltyTable.id = bookingId`，不再是新生成的 uuid4），其余属性照单体形状：`customerId`、
  `points=int(消息里的 points)`、`flag='active'`、`date=datetime.now().isoformat()`。重复投递只重写同一条目 →
  总分不变 → 幂等，**不需要 IdempotencyTable、也不走 TransactWriteItems**
  （m2s-async-patterns-kb 的 REQUIRED correctness rule 第 2 条：确定键的整条 PutItem 属于“已经幂等”的写法）。
- 选异步的信号：`entry_point_dependencies["POST /bookings"]` 里 `LoyaltyService.process_booking_loyalty` 的
  `return_value_used=false`、`is_awaited=false`；语义上是纯副作用
  （`POST /bookings#business_rules[5]`：失败只 print、不影响 201、不回滚）；单一消费者 → SQS 而非 EventBridge
  （`eventbridge_rules=[]`）。
- 为什么必须是普通 PutItem 而不是事务：`judge/faults/bookings-loyalty-put-fails.json` 按
  `(table=Airline-Loyalty-{stage}, op=PutItem, nth=all)` 注入；包进事务后这次写不再被认成 PutItem，
  单体记录的“积分没写成、`read-loyalty` points=0、下单仍 201”就在目标上复现不出来。
- 幂等键的传递：写进生产者 `bookings-create` 的 notes（消息必须带 `bookingId`）。`points` 由生产者算好
  （`int(payment_result.price)`，模拟模式恒 150），消费者只做 `int()` 与校验。
- 失败上报（SI-10）：消息体不可用（JSON 解不开 / 缺 `customerId` / `points` 非 int|float 或 <=0）= 永久失败 →
  记日志后吞掉，**不**返回 `batchItemFailures`（重投也永远不会成功）；`PutItem` 的 throttle/network/internal =
  可重试 → 返回该条 `batchItemFailures.recordId`（模板配 `ReportBatchItemFailures`），`maxReceiveCount=3` 后进 DLQ。
  两类都不影响已经返回的 HTTP 响应，与单体“失败只 print”的可观察结果一致；因为写幂等，红投安全。
- 通知（`BookingService.notify_booking`）**不**异步：`return_value_used=true`，返回值直接进 201 的 `notification`
  字段（`POST /bookings#business_rules[6]`，class=contract）→ 继续内联为纯模拟函数。
- 关联：SI-9、GAP-19、`sqs_queues[0]`。影响函数：bookings-create、loyalty-award-points。

## D-6 取消端点四步全部内联（这是对 GAP-19 默认做法的有意偏离）

- 决定：`booking-cancel` 在**一个函数内**按单体顺序做完：归属判定读 → `get_booking` 读 → `cancel_booking` 内部读 →
  UpdateItem(status=CANCELLED) → 放座 → 退款 → 通知；失败语义逐条照搬（放座失败只 print、响应仍 200；
  退款抛异常或 stored 行缺 `paymentToken` 的 KeyError 都被同一个 try 吞掉 → `refund={refundId:'N/A',status:'failed'}`；
  通知不包 try，缺 `customer` 键的 KeyError → 500 且 status 已经是 CANCELLED）。
- 偏离默认的理由：`POST /bookings/<booking_id>/cancel#business_rules[5]` 虽被标成 implementation-trace（允许把补偿动作
  异步化），但退款与通知的返回值都在 200 响应体里（`#responses[0]`），必须同步；放座与它们构成一条补偿序列，
  `judge/faults/booking-cancel-seat-release-fails.json` 断言“放座写失败 → 仍 200、booking 已 CANCELLED、座位停在 5”。
  把放座单独做成消息会给 `seatCapacity` 这个共享键引入单体没有的“重复 +1”失败方式（还得再加去重），没有收益。
- 依据：SI-3；GAP-2、GAP-19。影响函数：booking-cancel。

## D-7 保留单体的重复读：四条 trace_arm 不作废，只写“允许的结果集合”

- 决定：`booking-get` 读 booking 两次（归属判定 + handler）、`booking-confirm` 两次（归属判定 + 服务层内部）、
  `booking-cancel` 三次（归属判定 + `get_booking` + `cancel_booking` 内部）。四条 `trace_arm`
  （`GET /bookings/<booking_id>#responses[6]`、`POST /bookings/<booking_id>/confirm#responses[6]`、
  `POST /bookings/<booking_id>/cancel#responses[6]`、`#responses[7]`）都**不作废**：目标上仍然可达，
  允许的结果集合就是单体记录的 400（`{error:'Booking <id> not found'}` / `{error:'Invalid booking ID: <id>'}`，
  `type:'ValueError'`），加上条件写失败那条。
- 依据：重复读本身是实现轨迹（规格 notes 也这么标），但冻结的故障场景按“同一 item 的第几次读”注入：
  `judge/faults/booking-get-second-read-fails.json`（nth 2 → 400 Booking not found）、
  `booking-confirm-second-read-fails.json`（nth 2 → 400 Invalid booking ID）、
  `booking-cancel-second-read-fails.json`（nth 2）、`booking-cancel-third-read-fails.json`（nth 3）、
  `wb-booking-confirm-conditional-fails.json`（条件写失败臂）。合并读会让这些臂不再出现，与单体记录不一致。
- `POST /bookings#responses[5]`（建单前那次重复必填校验）同理保留：用例 `bookings-missing-chargeid#book-no-charge`
  直接断言 400 `{error:'Booking reservation failed: Invalid booking request: missing required fields',
  step:'Reserve Booking'}` 且先放座（`read-flight` 座位回到 1）。
- 关联：SI-1、SI-2。影响函数：booking-get、booking-confirm、booking-cancel、bookings-create。

## D-8 支付账本落库：新增 PaymentsTable，主键按类型标注（复刻 Python dict 的键语义）

- 决定：`PaymentService._payments`（`services/payment.py:21`）这份进程内共享状态迁到 DynamoDB 新表
  `PaymentsTable`（PK `chargeIdKey`）；条目 `{chargeId(原类型), receiptUrl, price, status, mode}`，
  **没有 createdAt**（`payment.py:169-176`，SI-8）。`_refunds`（`payment.py:22`）没有任何端点读它 → 不落表，只记日志
  （GAP-6）。
- 键的写法：`chargeIdKey = '<python 类型名>:<str(值)>'`（`str:ch-u1-1`、`float:12.5`、`bool:True`…）。
  收款按 body 里值的类型拼键；`GET /payments/<charge_id>`（路径参数永远是字符串）与 `POST /payments/refund`
  用 `str:<值>` 查。于是单体的“数字收款号写进去、按字符串读不到 → 400 `Payment 12.5 not found`”
  （J4-3 的更正；用例 `wb-bookings-non-string-chargeid#book-number-charge`→201 + `#read-payment-number`→400）
  在目标上一样成立；list/dict 这类不可哈希值抛 `TypeError`（不包成 ValueError）→ 统一出口 500
  `{error,type:'TypeError'}`（用例 `book-list-charge` → 500 + 回滚文案 + booking 行里 paymentToken 是数组、座位停在 3）。
  响应体不含主键属性。
- 落库的理由：GAP-5 的规则手册默认（m2s-basic-serverless-kb：进程内共享状态 → DynamoDB/S3）。不落库的话
  `POST /payments/collect` 写的记录在别的执行环境里读不到，取消与建单回滚里的“改 status=refunded”也无从判断
  （用例 `payment-read-found-and-missing`、`wb-bookings-payment-record-shared-cache`、
  `cancel-happy-read-back#read-payment`、`wb-bookings-catchall-no-refund`、故障 `wb-bookings-catchall-no-refund`）。
- 单体缺陷（同一 chargeId 命中 200 还是 400 取决于哪个进程服务了请求）被这次迁移消掉，但**不改变任何被记录的
  结果**（裁判在同一后端上顺序跑），因此不记 `deviates_from_monolith`；跨进程可见性是严格变得更好。
- 关联：SI-8（键语义照搬）、SI-14（账本落库）、SI-5（Stripe 分支不迁）、GAP-5、GAP-6、GAP-20。
- 影响函数：payments-collect、payments-refund、payment-get、bookings-create、booking-cancel。

## D-9 表结构照搬，不新增索引；读路径不换 Query、不翻页

- 决定：`FlightTable` PK `id`(S)；`BookingTable` PK `id`(S) + GSI `customer-index`(PK `customer` S, projection ALL)；
  `LoyaltyTable` PK `id`(S) + GSI `customer-flag-index`(PK `customerId` S, SK `flag` S, projection ALL) —— 逐字取自
  `dynamodb_info.table_schemas`（出处 `init_dynamodb_tables.py:34/50/69/81`），不编造属性；BillingMode 一律
  PAY_PER_REQUEST。两个 GSI 照建但**不用于读**：`flights-search`、`customer-bookings-list`、`loyalty-get` 都照搬
  基表 `Scan + FilterExpression`，只取第一页、不处理 `LastEvaluatedKey`（SI-4、GAP-3、Q10 默认照搬），
  也不加 `ConsistentRead`（GAP-14 照搬：单体的写后 Scan 本来就是最终一致）。
- 顺序：`flights-search` 的 `flights` 与 `customer-bookings-list` 的 `bookings` 是 Scan 原样返回、代码里没有排序，
  所以顺序不作要求（SI-11 `result_order="unspecified"`；GAP-15：主键是 uuid4 / 随机串，顺序既不等创建顺序也不可复现）。
  条目集合、第一页上限与字段形状仍是契约。
- `init_dynamodb_tables.py` 进 `dropped_functions`（type=script，Table creation handled by SAM/CloudFormation）。
- 关联：GAP-3、GAP-14、GAP-15、SI-4、SI-11。影响函数：flights-search、customer-bookings-list、loyalty-get（以及
  所有写这三张表的函数）。

## D-10 全局响应头（CORS）：真实响应由函数带，网关自答的用 GatewayResponses

- 决定：`global_middleware[0]`（`app.py:24` `CORS(app)`，flask-cors 默认 `send_wildcard=False`）加在**每个响应**上的头是
  `Access-Control-Allow-Origin`（请求带 Origin 时逐字符回显，否则 `'*'`）与 `Vary: Origin`（只在带 Origin 时）；
  `Access-Control-Allow-Methods` / `Access-Control-Allow-Headers` 只出现在预检（OPTIONS）上。
  代理集成后没有中间件层：
  1. 14 个 API 函数（`flights-search`、`flight-get`、`flight-reserve`、`flight-release`、`bookings-create`、
     `booking-get`、`booking-confirm`、`booking-cancel`、`customer-bookings-list`、`payments-collect`、
     `payments-refund`、`payment-get`、`loyalty-get`、`loyalty-add-points`）的返回值自己带这四个头名：
     真实响应发 `Access-Control-Allow-Origin`（回显/`'*'`）+ 有 Origin 时的 `Vary: Origin`；
     `Access-Control-Allow-Methods` 与 `Access-Control-Allow-Headers` 由共用响应助手只在预检路径上发
     （与单体一致：真实响应没有这两个头，否则裁判会按声明头判多余）。`Content-Type: application/json` 不带 charset。
  2. 网关自答的响应（授权器 401、429 限流、集成 5xx）用 REST 的 `GatewayResponses`：`DEFAULT_4XX` / `DEFAULT_5XX`
     带齐这四个头名（GM2 的机械要求），`UNAUTHORIZED` 带 `Access-Control-Allow-Origin: '*'`
     （单体在无 Origin 的 401 上也是 `'*'`，裁判这些步骤期望的也正是 `'*'`）。
- 这就是 `api_type="rest"` 的唯一理由：头不止 `Access-Control-Allow-Origin: *`（还有逐请求回显的 Origin 与
  `Vary: Origin`），HTTP API 没有 GatewayResponses，网关自答的 401 就带不上这些头（AT1 从规格机械推出
  `gateway_responses`）。
- 依据：`analysis_report.global_middleware[0]`；用例 `search-exact-match#with-origin` 断言
  `access-control-allow-origin: https://judge.example.com` 且 `vary` 含 `Origin`。
- 关联：GAP-9、SI-5（`GET /payments/<charge_id>#responses[2]` 那条真 Stripe 臂的作废在 SI-5 里）。
  注意：**没有**任何 migration-change 裁定指向 `global_middleware[0]`，这些头是保留的契约。

## D-11 统一异常出口、配置注入与包布局

- 决定：
  1. 每个函数复刻 `app.py:28-36` 的出口：未被路由自己接住的 `ValueError` → 400 `{error, type:'ValueError'}`；
     其它异常 → 500 `{error, type: 异常类名}`（`KeyError`、`TypeError` 都走这条）。错误文案一律用 `str(e)` 原样拼
     （故障场景 `reserve-network-drop` 断言文案里带代理 endpoint URL 原文，不要自己编）。
     路由自己的局部 try/except 出口（各端点的 `responses` 列出的那些臂）优先，形状不变。
  2. “未注册路径 / Content-Type 不是 application/json / 请求体不是合法 JSON”这类**根本没进 handler** 的情况由网关
     应答，不再是单体的 `@app.errorhandler(Exception)`（GAP-11；裁判没有这类步骤，故不预写放宽）。
  3. 表名与队列 URL 一律走环境变量（`FLIGHT_TABLE`、`BOOKING_TABLE`、`LOYALTY_TABLE`、`USER_GROUPS_TABLE`、
     `PAYMENTS_TABLE`、`LOYALTY_QUEUE_URL`），值是 SAM 资源占位符 `${LogicalName}`；不再有 `config.py` 的 dotenv 与
     `STAGE` 拼表名（GAP-22，`config.py` 进 dropped_functions）。
  4. 不再有 `sys.path` hack、模块级 boto3 resource 与构造时副作用（GAP-23、GAP-12；
     `data/storage.py:18-26` 的 `__init__` 与 `_init_sample_data` 一起 drop）。
- 影响的函数：全部 15 个。关联：GAP-11、GAP-22、GAP-23、GAP-16。

## D-12 不提供外部支付网关分支（Stripe）

- 决定：`payments-collect`、`payments-refund`、`payment-get` 与 `bookings-create` 的收款步骤只实现模拟分支：
  不引入 `stripe` 依赖、不读 `STRIPE_SECRET_KEY`；`mode` 恒为 `'simulation'`，`price` 恒为 150（与航班票价、入参金额无关，
  `POST /payments/collect#business_rules[2]`）。`GET /payments/<charge_id>#responses[2]`（缓存未命中再走真 Stripe 查）
  这一臂作废；`POST /payments/collect#responses[0]` / `POST /payments/refund#responses[0]` 里“配了 Stripe 时”
  的 amount/currency 形状也不再产生（SI-5）。
- 依据：事实库多处写明本流水线运行在模拟模式（`POST /payments/collect#business_rules[2]` 的 J4 注、
  `GET /payments/<charge_id>#business_rules[3]` 的 note、`requirements.txt` 里 stripe 为可选依赖）；
  `judge/judge_manifest.json` 的 exempted 也写明真 Stripe 的 amount/receipt 形状在模拟模式下无法构造。
  模拟分支的对外形状由 `payment-read-found-and-missing`、`collect-validation-and-record`、
  `refund-validation-and-effect` 逐条断言，全部保持。
- 影响的函数：payments-collect、payments-refund、payment-get、bookings-create、booking-cancel。

## D-13 导入即写库的种子数据不迁；样例航班与默认管理员改为部署期导入

- 决定：两处一次性初始化都不进函数：`data/storage.py:52-110` 的 `_init_sample_data`（用 `scan(Limit=1)` 判空后
  `put_item` FL001/FL002）与 `services/auth.py:247` 的 `_init_default_users`（写 user123/admin123 并把 admin123
  加进 Admin 组）。样例航班与默认管理员改为**部署期的一次性数据导入**（运维步骤，不属于任何端点，也不放在 Lambda
  冷启动里：并发冷启动会重复播种，还会在裁判正在用的表里塞条目）。目标里的“管理员” = 用户池里一个用户 +
  `UserGroupsTable` 一行 `groups=['Admin']`。
- 于是“空表部署上 `GET /flights/search` 返回 FL001/FL002、能用 user@example.com/password123 登录”这条对外行为变化
  按 **migration-change** 裁定（SI-10，targets `GET /flights/search#business_rules[5]`），并记一条
  `deviates_from_monolith=true` 的裁定等人确认。裁判观察不到它：每例跑前都重播 fixture，
  `judge/judge_manifest.json` 的 uncovered（J4-4）也说明任何用例都引用不到导入时的播种，因此没有 affected_cases。
- 依据：GAP-12、GAP-3（storage.py:61 那条 Scan 不是数据读取，只是判空）；规则手册把“去掉导入即写库的种子数据”
  列为 migration-change 的典型。
- 影响的函数：flights-search、flight-get（其余函数只受影响于“表里有没有样例数据”，行为形状不变）。

## D-14 时间戳、随机值与数值口径（不修的缺陷照搬）

- `booking.createdAt` 与 `loyalty.date` 仍是不带时区的 `datetime.now().isoformat()`（GAP-6、GAP-16 照搬；
  不加 UTC、不加 `Z`、不改格式）。裁判把这些位置标成 `<time#N>` 符号，跨环境不比对具体值。
  users 的 `created_at` 与 JWT 的 `iat`/`exp`（`auth.py:61/87/96` 的 `utcnow()`）随认证替换一起交给用户池
  （GAP-7、GAP-8；`fix_admin_user.py` 是独立脚本，GAP-4）。
- 随机值口径：`booking.id` = uuid4；`bookingReference` = `secrets.token_urlsafe(4)`；`refundId` =
  `secrets.token_urlsafe(16)`；`notificationId` 与 `loyalty.id` 见 D-4/D-5（消费者的 `loyalty.id` 有意改成
  bookingId —— 这个值不在任何响应里出现，只作为幂等键，SI-9）。
- 数值口径：DynamoDB 的 Decimal 一律转回 int/float（`storage.py:41-53`）；积分求和按逐条 `int(points)`，任何一条
  转不动或整次读失败都被外层 except 吞掉 → `points=0, level='bronze', remainingPoints=50000`；
  档位 `>=100000 gold / >=50000 silver / 其余 bronze`（SI-12，用例 `loyalty-sum-tiers-and-flag-filter`、
  `wb-loyalty-broken-points-row`、故障 `loyalty-summary-read-fails`）。
- Scan 不翻页、读失败降级成空集合/0 这两条单体缺陷**不修**（Q10：`GET /flights/search#business_rules[3]`、
  `GET /customers/<customer_id>/bookings#business_rules[4]`、`GET /loyalty/<customer_id>#business_rules[3]`，
  故障场景 flight-search-read-fails / customer-bookings-read-fails / loyalty-summary-read-fails /
  flight-get-read-fails / release-precondition-read-fails 都断言降级而不是 5xx）。GAP-1、GAP-2 的多写也不引入事务
  （SI-3：中间态与补偿结果都被读回步骤断言）。
- 影响的函数：bookings-create、booking-cancel、booking-confirm、booking-get、loyalty-get、loyalty-add-points、
  loyalty-award-points、flights-search、customer-bookings-list、flight-get、flight-release、flight-reserve。

## D-15 各函数的 auth 取值与例外清单

- 全部采用规格 `auth.classification`，没有例外：`required` = bookings-create、booking-get、booking-confirm、
  booking-cancel、customer-bookings-list、loyalty-get、loyalty-add-points（七个）；
  `none` = flights-search、flight-get、flight-reserve、flight-release、payments-collect、payments-refund、
  payment-get（七个）。规格里没有 `optional`/`unknown` 的端点（`GET /auth/me` 之类已删除）。
- `loyalty-add-points` 的 `admin_required` 是**授权**不是鉴权：网关只负责“你是谁”，“是不是 Admin”由函数读
  UserGroupsTable 决定（D-3），403 文案 `'Admin privileges required'` 保持
  （`POST /loyalty/<customer_id>/points#responses[3]`）。
- 三个支付端点保持公开（`POST /payments/collect#business_rules[0]`、`POST /payments/refund#business_rules[0]`、
  `GET /payments/<charge_id>#business_rules[2]`：无鉴权、无所有权检查，知道 chargeId 就能读到整条记录）——
  这是单体的对外形状，不顺手加鉴权。
- 关联：GAP-21（`request.current_user` 这种请求上下文状态改由 claims 承载）。

## D-16 裁判资产与本设计的关系（适配映射要点）

- `storage/adapter_map.json`：四张单体表全部有 entry —— Flight/Booking/Loyalty 同名映射，`Airline-Users-{stage}`
  映射到 `UserGroupsTable` 并 drop `password_hash`/`email`/`created_at`；`customer`/`customerId`/`sub` 标
  `as: identity`（在本工作区里是同名同值，因为 D-4 保留了单体的 id 字符串）。`PaymentsTable` 记在
  `target_only_tables`（单体没有对应集合）。`identities.claims` = `{sub, username, email}`，
  `user_id` = `judge-${alias}`，**不写 seed**：seed 模板对所有别名相同，会把 admin 那行的 `groups` 覆盖成 `[]`，
  而 admin 的管理员身份本来就由 `users-admin` fixture 提供。
- `dropped_endpoints` 与 `dropped_functions` 里的四个端点逐字对应，裁判不再在目标上运行这些步骤
  （`judge/cases/` 里 9 个整例只落在 /auth 端点上：register-*、login-*、me-*、users-*）。
- 网关应答的 21 个匿名步骤（7 个受保护端点 × no-header/no-space/bad-token）逐条写进 migration-change 裁定的
  `affected_cases`（`用例#步骤`，见 `storage/rulings.json` R-1）。

## D-17 上游正确性规则（异步消费者幂等）的落实清单

- 幂等键：`bookingId`（生产者 `bookings-create` 必须把它放进消息体）。
- 去重机制：不需要额外表 —— 消费者的写是“确定键（`LoyaltyTable.id = bookingId`）的整条 `PutItem`”，
  重复投递重写同一条目（m2s-async-patterns-kb REQUIRED rule 第 2 条）。
- 失败上报：永久失败（消息体不可用）吞掉并记日志；可重试失败返回 `batchItemFailures` + 模板
  `ReportBatchItemFailures`，`maxReceiveCount=3` 后进 `LoyaltyPointsQueue` 的 DLQ（`sqs_queues[0].dlq=true`、
  `visibility_timeout=300` 覆盖消费者超时）。
- 没有超过 100 条目的批写，因此不需要拆批规则；消费者的批大小 10（`consumer_batch_size`）。
- 唯一的消费者是 `loyalty-award-points`；`bookings-create` 是它的唯一生产者（`publishes_to`）。
- 关联：SI-9、GAP-19。


## D-18 缺口决定索引（`storage/gap_inventory.json` 逐条）

| 缺口 | 决定（详见对应 D 节） | 由谁验证 |
|---|---|---|
| GAP-1 下单多次写没有事务 | 内联在 bookings-create，保留单体的补偿顺序，不引入 TransactWriteItems（D-6、SI-3） | judge: bookings-payment-fails-rollback / bookings-success-read-back / wb-bookings-confirm-rollback-seat-stuck |
| GAP-2 取消端点两次写没有事务 | 内联在 booking-cancel，三步各自失败只记日志（D-6、SI-3） | judge: cancel-happy-read-back / wb-cancel-row-without-paymenttoken |
| GAP-3 Scan 不翻页 | 照搬：只取第一页，不处理 LastEvaluatedKey（D-9、SI-4） | none（裁判数据碰不到 1MB 截断，只能读代码核实） |
| GAP-4 fix_admin_user.py 的 Scan 不翻页 | 脚本删除，不需要处理（D-13） | none |
| GAP-5 支付账本在进程内存 | 迁到 PaymentsTable（D-8、SI-14） | judge: payment-read-found-and-missing / wb-bookings-payment-record-shared-cache / wb-bookings-catchall-no-refund |
| GAP-6 退款流水 _refunds 只改 status | 不迁 _refunds；只保留“就地改 PaymentsTable 那条 status”的可观察效果（D-8） | judge: refund-validation-and-effect / wb-bookings-catchall-no-refund |
| GAP-7 fix_admin_user.py 用 utcnow | 脚本删除，不需要处理 | none |
| GAP-8 JWT 的 iat/exp 用 utcnow | 交给 Cognito 令牌寿命（cognito.token_validity=1h）（D-1） | none |
| GAP-9 全局 CORS 中间件 | 函数自己带 ACAO/Vary（预检两个头由响应助手在 OPTIONS 路径给）+ GatewayResponses；api_type=rest 的唯一理由（D-10） | judge: search-exact-match（#with-origin 断言回显与 Vary） |
| GAP-10 应用自己签发校验令牌 + 9 个受保护端点 | Cognito + 网关授权器，四个 /auth 端点与中间件删除；授权判定留函数（D-1） | judge: get-booking-owner-other-missing / bookings-customer-from-token |
| GAP-11 全局 @app.errorhandler(Exception) | 函数内复刻 ValueError→400 / 其它→500 的形状；没进 handler 的情况由网关应答（D-11） | judge: get-flight-found-and-missing / bookings-auth-and-input-arms / wb-bookings-non-string-chargeid |
| GAP-12 导入即写库的一次性初始化 | 样例航班与默认用户改部署期导入，不进函数（D-13、SI-10） | none（裁判每例重播 fixture，观察不到） |
| GAP-13 令牌密钥/寿命是进程内常量；Admin 读库里的 groups | 密钥与寿命交给用户池；Admin 判定继续回表读 UserGroupsTable（D-3） | judge: add-points-admin-creates-rows / customer-bookings-status-filter |
| GAP-14 Scan 是最终一致（写后可能读不到） | 照搬：不加 ConsistentRead、不加读重试（D-14） | none |
| GAP-15 列表顺序不可依赖 | 照搬不排序 + result_order="unspecified"（D-9、SI-11） | judge: customer-bookings-status-filter / search-exact-match |
| GAP-16 createdAt/date 用 naive 本地时间，与 utcnow 混用 | 照搬 naive ISO，不改 UTC、不加时区（D-14） | none |
| GAP-17 邮箱查重是 check-then-write（注册/登录） | 随注册与登录端点删除一起消失，唯一性由用户池保证（D-1、SI-6） | none（无可比较记录） |
| GAP-18 放座是先读后写的竞态（读 maximumSeating 再条件写） | 照搬两步：GetItem 取上限 + 条件写 seatCapacity < :max（D-14） | judge: release-happy-increments / wb-release-flight-no-maxseating |
| GAP-19 吞掉的即发即忘副作用 | 只有积分那一步改消息 + 幂等消费者；通知/退款/放座保持内联（D-5、D-6、SI-9） | judge: bookings-success-read-back |
| GAP-20 Stripe 模式标志是进程内状态 | Stripe 分支不迁、只有模拟分支（D-12、SI-5） | judge: payment-read-found-and-missing / collect-validation-and-record |
| GAP-21 request.current_user 是进程内请求上下文 | 改读 claims.sub；POST /bookings 仍覆盖 body 的 customerId（D-1、D-4） | judge: bookings-customer-from-token |
| GAP-22 配置从 dotenv/进程环境读且两处各自 fallback | 单一来源：SAM 注入环境变量，代码不拼表名（D-11） | none |
| GAP-23 sys.path hack 造导入路径 | 每个函数包自带所用模块，boto3 客户端在初始化阶段建（D-11） | none |


## D-19 迁移带来的对外变化（`record_ruling` 提议）

- `R-A1-a3-1`（category `migration-change`）：七个受保护端点用例中的 `no-header`、`no-space`、`bad-token` 共 21 个步骤由 API Gateway Cognito authorizer 返回 401 `{message:"Unauthorized"}`，不再由 Flask 返回 `{error}`；单体的三条 401 文案合成这一条，响应头由 `api_gateway.gateway_responses.UNAUTHORIZED`/`DEFAULT_4XX` 补齐四个全局头。`affected_cases` 逐步骤写明：`get-booking-owner-other-missing`、`customer-bookings-single-and-auth`、`loyalty-empty-and-auth-arms`、`bookings-auth-and-input-arms`、`cancel-auth-and-missing`、`confirm-auth-and-missing`、`add-points-validation-and-auth`（各 `#no-header`/`#no-space`/`#bad-token`）。同一用例里的 403 归属判定、400/404 等其它步骤不在这条裁定范围内，仍照搬单体。
- `R-A1-a3-2`（category `migration-change`）：`POST /auth/register`、`POST /auth/login`、`GET /auth/me`、`GET /auth/users` 四个端点不在目标 API 上（`dropped_functions` + `adapter_map.dropped_endpoints`），裁判目标运行跳过这九个 `/auth` 用例的全部步骤；注册、登录、本人资料、用户目录改由 Cognito User Pool API 承担，身份准备走 `adapter_map.identities`。
- 其它迁移差异未预写放宽：GatewayResponses 仅补全局 CORS 头；真实响应仍由函数逐请求回显 `Origin` 并加 `Vary: Origin`（`search-exact-match#with-origin` 是函数路径，不是网关自答）；支付账本落库、Stripe 模拟分支、Cognito 身份替换均未改变被裁判记录的对外状态/响应体。
- `R-A1-a3-3`（category `contract`，不是迁移带来的变化）：补全 `POST /bookings` 请求字段的类型口径——`outboundFlightId` 与 `chargeId` 只按键存在性做必填校验，值不强制转成字符串、不做类型检查，按原类型落库并原样读回；`chargeId` 继续保留单体的类型标注账本键语义（number 收的款用路径参数读不到 → 400；list 抛 TypeError → 500 并按已发生状态回滚）。`affected_cases`：`wb-bookings-non-string-chargeid#book-list-charge`、`wb-bookings-non-string-chargeid#book-number-charge`。
- `R-A1-a3-4`（category `spec-gap`）：规格里那个可选字段 `name` 在目标上不读取、不校验、不落库、不回显；带未知键的请求体与单体一样照常处理，不因此返回 400。当前用例没有发送 `name` 的步骤，所以这条不挂 `affected_cases`。
- 记录说明：第 2 次尝试（`R-A1-a2-*`）因为改动了任务范围外的文件被撤销，其裁定没有生效；本节的 `R-A1-a3-*` 是同一组结论在第 3 次尝试里重新提交的裁定，内容一致、`affected_cases` 精确到步骤。本阶段只写 `storage/blueprint.json`、`storage/DECISIONS.md`、`storage/adapter_map.json`、`storage/gap_inventory.json` 与本任务报告，不再产生任何临时文件。
