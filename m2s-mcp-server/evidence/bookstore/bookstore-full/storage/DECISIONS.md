# bookstore 迁移决策记录（A1 / m2s-architect）

事实来源：`storage/analysis_report.json`（schema_version 1，业务规则带 `class` ⇒ contract 2）、
`storage/gap_inventory.json`、`judge/cases|fixtures|identities.json`（裁判已冻结，judge_manifest version 2）。
下文引用的 `BR-*` / `#responses[i]` / `#business_rules[i]` 都是规格里带出处的条目；`GAP-n` 是缺口编号；
`SI-n` 是 `metadata.spec_resolutions` 里的裁定。每个函数的名字都在某一条里出现。

---

## D-1 端点与函数一一对应（一个 endpoint 一个 Lambda）

15 个端点里 11 个业务端点各做一个 API Lambda，4 个认证端点删除（D-3）：

| 函数 | 端点 | auth | 规格依据 |
|---|---|---|---|
| `books-list` | GET /api/books | required | `GET /api/auth…` 之外的 app 级 authMiddleware（server.js:41），`auth.classification=required` |
| `books-get-by-id` | GET /api/books/{id} | required | 同上（server.js:41） |
| `cart-list` | GET /api/cart | required | server.js:42 |
| `cart-get-by-book` | GET /api/cart/{bookId} | required | server.js:42 |
| `cart-add` | POST /api/cart | required | server.js:42 |
| `cart-update` | PUT /api/cart | required | server.js:42 |
| `cart-remove` | DELETE /api/cart | required | server.js:42 |
| `orders-list` | GET /api/orders | required | server.js:43 |
| `orders-create` | POST /api/orders | required | server.js:43 |
| `bestsellers-list` | GET /api/bestsellers | required | server.js:44 |
| `search-books` | GET /api/search | required | server.js:45 |
| `bestsellers-update` | （无 HTTP 入口，trigger_type=sqs） | — | 见 D-5/D-6 |

- 取舍：11 个端点的 `auth.classification` 全是 `required`，blueprint 里也全是 `auth="required"`，**没有一处偏离规格**（`gate(blueprint)` 的 B0 auth 对照因此不会报安全回退）。没有 `optional`/`unknown` 分类：`optionalAuthMiddleware`（middleware/auth.js:69）在单体里没挂到任何路由上（server.js:38-45 只用 `authMiddleware`），所以不存在需要取舍的可选鉴权端点；它作为代码项进 `dropped_functions`。
- 没有把多个端点塞进一个函数、没有内部路由/switch；`cart-add`/`cart-update`/`cart-remove` 共用 `/api/cart` 路径但按方法拆成三个函数（规则 1）。
- 每个函数都是 Worker：`entry_point_dependencies` 里每个端点的 cross_file_calls 只指向 `../utils/dynamodb` 的 docClient 调用与 `../config`（`POST /api/orders` 额外指向 `../utils/bestsellers`），**没有跨业务域的同步需求**，所以同步路径全部内联在各自的 handler 里，`lambda_invoke_permissions = []`。

## D-2 认证基建化：Cognito 授权器 + 函数内零鉴权

`file_tags` 里 `config.js`/`server.js`/`middleware/auth.js`/`routes/auth.js`/`utils/jwt.js` 都带 `Auth` 标签 ⇒ `cognito.enabled = true`。
流程是 `请求 → API Gateway（Cognito 授权器验 JWT）→ Lambda（纯业务逻辑）`，身份取 `event.requestContext.authorizer.claims.sub`。

- 单体不是"自管理会话"的例外情形：`middleware/auth.js:49` 走 `verifyAccessToken(token)`，是无状态 JWT 验签，**不查会话表**、不发 cookie（`utils/jwt.js` 只签/验令牌），客户端依赖的是 Bearer JWT 契约，而这条契约由用户池签发的 JWT 继续满足 ⇒ 按规则手册迁移到 Cognito（GAP-10 的默认做法），不走"保留单体会话机制"那条分支。
- 因此 `users` 表、`utils/jwt.js`、`utils/password.js`、`authMiddleware`、`optionalAuthMiddleware` 都不迁移（见 D-3、D-4）。
- 相关裁定：SI-11（认证整体替换）、SI-12（`x-customer-id` 不再被任何函数读取）、J4-3（同一条契约的裁定）。

## D-3 删除的端点 / 表 / 脚本（`dropped_functions`）

| 项 | type | 理由（出处） |
|---|---|---|
| `POST /api/auth/register` | endpoint | Replaced by Cognito：BR-REG-1..10 的校验、uuid 生成 userId、bcrypt 哈希、写 users 行、签发双令牌都不再是后端职责（SI-11） |
| `POST /api/auth/login` | endpoint | Replaced by Cognito（email-index 查用户 + bcrypt.compare + 签令牌，BR-LGN-*） |
| `POST /api/auth/refresh` | endpoint | Replaced by Cognito 的 `InitiateAuth(REFRESH_TOKEN_AUTH)`（BR-RFS-1..8：不轮换 refreshToken、无撤销记录） |
| `GET /api/auth/me` | endpoint | Replaced by Cognito：客户端从自己的 ID token/SDK 读资料；单体的 `GetItem users → 剔除 password` 没有对应实现（BR-ME-5/6） |
| `authMiddleware` / `optionalAuthMiddleware` | code | Replaced by API Gateway Cognito Authorizer（D-2、D-4） |
| `bookstore/utils/jwt.js` / `bookstore/utils/password.js` | code | 令牌签发与口令哈希归用户池；共享 HS256 占位密钥（config.js:11-12）随之消失（GAP-15） |
| `bookstore/scripts/init-db.js` | script | Table creation handled by SAM CloudFormation（五张表进模板）；其中的 `initializeSampleBooks` 示例数据不再预置（SI-16、D-9） |
| `bookstore/server.js` | script | Express 引导换成 API Gateway + Lambda；两个进程级中间件的对外效果分别由 SI-01/D-14（CORS）与 SI-03/D-13（错误体）保持 |
| `bookstore/config.js` | code | 只剩表名/区域这类配置，改成 Lambda 环境变量（SI-13、D-15） |
| `Bookstore-Users` | table | 整表交给 Cognito：行里只有 `{userId, email, password(bcrypt), name, createdAt, updatedAt}`（BR-REG-6）—— 一个凭据字段 + 全是 Cognito 标准属性，没有头像/偏好/地址一类业务资料 ⇒ 处理方式 ③，**不建 UserProfiles 表、不把任何凭据字段迁进 DynamoDB** |

## D-4 网关鉴权与 401 的对外差异

函数里没有任何鉴权代码（没有 verifyToken、没有查令牌表）。未认证/无效令牌的 401 由网关授权器直接应答：
状态码仍是 401，但响应体是网关固定的 `{"message":"Unauthorized"}`，不是单体的
`{"error":"Authentication required. Please provide a valid Bearer token."}` / `{"error":"Invalid or expired token"}`，
而且这两条文案在目标里无法区分（授权器只区分"有合法 JWT"和"没有"）。这条变化按规则手册写成了
`record_ruling`（category=migration-change，规则手册默认做法的必然结果，不标 deviates_from_monolith），
精确到步骤：`books-catalog-scan#anon/#garbage-token`、`books-get-one-hit-and-miss#anon/#garbage-token`、
`cart-list-unauthenticated#anon/#garbage-token`、`cart-add-anonymous#anon/#garbage-token`、
`cart-get-one-hit-and-miss#anon/#garbage-token`、`cart-update-unauthenticated#anon/#garbage-token`、
`cart-delete-unauthenticated#anon/#garbage-token`、`orders-create-unauthenticated#anon/#garbage-token`、
`orders-list-unauthenticated-and-isolated#anon/#garbage-token`、`search-validation-and-unauthenticated#anon/#garbage-token`、
`bestsellers-unauthenticated#anon/#garbage-token`、`wb-cart-x-customer-id-cannot-scope-data#anon-x-customer-id`、
`wb-orders-x-customer-id-cannot-authenticate#anon-x-id`。
认证端点自己的 401/400 臂（me/login/refresh/register 的用例）不作为差异处理：那些端点整体不迁移，
裁判按 `adapter_map.dropped_endpoints` 把步骤判为 dropped。

## D-5 唯一的跨函数通信：下单后的畅销榜累加 → SQS

`call_graph`：`POST__api_orders` 在 `routes/orders.js:87` 调 `updateBestSellers`，`return_value_used=false`、`is_awaited=false`；
`entry_point_dependencies["POST /api/orders"]` 同样只有这一条跨模块的非 awaited 调用。
语义上也确认是即发即忘：`.then(result => console...)` + `.catch(error => console.error('Error updating bestsellers:'...))`，
响应体里没有它的任何结果（BR-ORD-CRT-6：『失败不影响状态码或响应体』）。
⇒ 判为**异步**；单一消费者（只有畅销榜这一路）、不是扇出 ⇒ **SQS**（不用 EventBridge），
`eventbridge_rules = []`；同步调用链不需要 ⇒ `lambda_invoke_permissions = []`。

- 生产者 `orders-create`：`publishes_to = [{type: "sqs", target: "BestsellerUpdateQueue"}]`，消息体 `{orderId, books}`，
  `books` 是请求数组的原样（含每个元素的 `bookId/quantity`，**quantity 不做类型转换**，SI-06 / J4-2），
  这样消费者才能既复现"数字才累加"也复现"字符串 quantity 被拒"。`orderId` 是幂等键的一部分（D-6）。
- 消费者 `bestsellers-update`：`trigger_type="sqs"`、`sqs_source="BestsellerUpdateQueue"`，没有 `entry_points`/`auth`。
- SendMessage 必须 await（Lambda 返回后冻结进程），但不等消费者 ⇒ 对外仍然是"响应发出时榜单可能还没更新"。
- 队列配置：`BestsellerUpdateQueue` + `BestsellerUpdateDLQ`、`maxReceiveCount=3`、`VisibilityTimeout=60`（>= 消费者超时）、
  Standard 队列（单体这一路本来就没有顺序保证）。
- `POST /api/orders` 的另一处"响应后才发生的可见性差异"（GAP-17）就是这条链路，见 D-6 末段。

## D-6 上游正确性规则：`bestsellers-update` 消费者必须幂等（Pattern B 变体，标记放独立表）

副作用是 `UpdateExpression: 'ADD salesCount :qty'`（utils/bestsellers.js:20）—— 原子累加，天然**不**幂等；
SQS 至少一次投递，重复投递会把销量多加。⇒ 必须有去重机制（m2s-async-patterns-kb 的 REQUIRED 规则）。

- **幂等键**：`(orderId, books 数组下标)`，标记 `pk = "bestsellers-update#<orderId>#<下标>"`。
  只用 `orderId` 不够：同一本书在一次下单里出现两次是合法输入并且单体会累加两次
  （BR-ORD-CRT-3『不去重』+ 用例 `orders-create-duplicate-bookid` 期望销量累加两次），按下标编号才能把两个元素都算上。
- **选定做法**：条件写认领 + 效果保持单体的那一次 `UpdateItem ADD`（Pattern B 的"条件写记住已应用的键"，
  只是把记的地方从业务行挪到专用表）：
  1. `PutItem IdempotencyTable {pk, orderId, bookId, createdAt, expiresAt}` + `ConditionExpression: attribute_not_exists(pk)`；
     `ConditionalCheckFailedException` ⇒ 这一笔之前已经处理过 ⇒ 跳过该元素。
  2. `UpdateItem BestsellersTable Key {bookId}` + `UpdateExpression 'ADD salesCount :qty'`，`:qty` 是消息里的原值，
     **不加 ConditionExpression** ⇒ 首次销量自动建行（BR-ORD-CRT-9）。
- 为什么不选 Pattern A（`TransactWriteItems` 同时提交标记与效果）：
  1. 效果调用会从 `UpdateItem` 变成 `TransactWriteItems`，而**裁判已经冻结**：
     `judge/faults/wb-orders-bestseller-update-fails.json` 的规则是 `match {table: Bookstore-Bestsellers, op: UpdateItem}`，
     表名由适配映射换成目标表名、op 不改 —— 用事务的话这条故障注入打不中目标，
     故障跑里畅销榜反而会被写成有行（单体是没有），并且 `rules_fired` 两侧不一致；
  2. 事务会给热点行（同一本书被两个订单同时卖出）引入 `TransactionConflict` 这种单体没有的失败模式
     （`m2s-tx-conflicts` 会真的模拟它），把本来一定会成功的原子 ADD 变成可取消的事务；
  3. 一条消息一个事务还会撞上"一个事务不能两次操作同一个条目"，必须先把 books 按 bookId 合并 —— 合并就要处理
     `quantity` 类型（SI-06 明令禁止归一化），且单体内"某一本书失败不影响其它书"的隔离性会变。
- 为什么不选 Pattern B 的原样写法（`ADD salesCount :q, appliedKeys :k` + `NOT contains(appliedKeys, :k)`）：
  它把去重键塞进畅销榜业务行，单体的行只有 `{bookId, salesCount}`（fixture `judge/fixtures/bestsellers.json` 就是这样），
  多出来的属性会出现在裁判比对的表快照里（本来只作 advisory，但没必要引入），而且这个集合会无限增长（KB 自己要求"prune or bound"）。
- **顺序与失败窗口**：先认领后施效 ⇒ 最坏是"认领成功、进程在施效前崩掉 ⇒ 这一笔少加"，
  这恰好是单体本来就有的失败模式（`updateBestSellers` 的 Promise 靠进程活着才跑得完，GAP-17）；
  反过来先施效后认领则会在重投时**多加**，那是比单体更差的新错误，不可接受。
- **失败上报**（SI-10）：永久错误（`ValidationException` 等，例如 quantity 是字符串 ⇒ 正是单体被吞掉的那类失败，
  BR-ORD-CRT-9/14）记日志后跳过该元素、不返回 `batchItemFailures` ⇒ 与单体的 `catch → return false → 计入 failed → 只写日志` 一致，
  代价是 DLQ 基本触发不到；临时错误（限流、事务冲突、网络）做有界退避重试，重试用尽返回 `batchItemFailures`
  （事件源配 `ReportBatchItemFailures`）让 SQS 重投，重投的安全性由认领标记保证（KB：冲突不得吞掉）。
- **幂等表**：`IdempotencyTable`，分区键 `pk` (S)，TTL 属性 `expiresAt`（秒，7 天 > SQS 保留 4 天 + 重试），
  已加进 `bestsellers-update` 的 `environment_variables.IDEMPOTENCY_TABLE`，并在适配映射里登记为 `target_only_tables`。
- 这一路的重投/幂等**裁判看不到**（裁判的用例步骤之间只投递一次消息，`judge/cases/post-api-orders/wb-orders-bestseller-update-fault-baseline.json`
  也没有读回畅销榜），所以 GAP-2/GAP-17 的决定用 `test: "delta"`：由增量测试断言
  "同一 (orderId, 下标) 重复投递不重复累加" 与 "消息体里带 orderId"。

## D-7 表结构：以 `dynamodb_info.table_schemas` 为准，只改用户外键

`dynamodb_info.used = true`，五张表的分区键/排序键/GSI 直接照抄规格出处，不编造字段：

| 目标 logical_name | 单体表 | 主键 | GSI |
|---|---|---|---|
| `BooksTable` | Bookstore-Books | `id` (S) | `category-index`：分区键 `category` (S)，投影 ALL（init-db.js:82） |
| `CartTable` | Bookstore-Cart | `customerId` (S) + `bookId` (S) | 无 |
| `OrdersTable` | Bookstore-Orders | `customerId` (S) + `orderId` (S) | 无 |
| `BestsellersTable` | Bookstore-Bestsellers | `bookId` (S) | 无 |
| `IdempotencyTable` | （单体没有） | `pk` (S) | 无；TTL `expiresAt`（D-6） |

- `Bookstore-Users`（含 `email-index`）整表删除（D-3），`email-index` 也随之消失 —— 它唯一的用途是注册查重与登录取用户（BR-REG-4 / BR-LGN-3），
  这两件事都归用户池。
- **用户外键的变化**：`CartTable.customerId` / `OrdersTable.customerId` 的值从单体自造的 `users.userId`（uuid v4）
  改成 Cognito Sub（仍是 UUID 字符串，类型不变），见 `adapter_map.json` 的 `fields: {customerId: {from: customerId, as: identity}}`
  与 `identities.claims.sub = ${id}`。属性名不改，以免牵动 `books`/`orderDate` 这些原样快照的比较。
- 属性类型/取值不改：`orderDate` 是 `Date.now()` 毫秒整数（SI-14 / GAP-8）、`quantity`/`price` 按请求原样（可能是字符串，SI-06）、
  畅销榜行只有 `{bookId, salesCount}`（J4-2）。

## D-8 `books` 快照与购物车条目的写入语义（保持，不"修好"）

- `orders-create` 把 `books` 数组原样存进订单行，读回也不重算（BR-ORD-CRT-2、BR-ORD-LIST-5）；
  不逐个校验元素（BR-ORD-CRT-3），非对象元素的炸法也照搬（BR-ORD-CRT-13 / J4-1，见 D-13）。
- `cart-add` 是无条件整行 `PutItem`（覆盖，不累加、不去重，BR-CART-ADD-3，SI-23 只裁调用形态）；
  `cart-update` 是无条件 `UpdateItem SET` ⇒ upsert：改一个不存在的 bookId 会凭空创建一条没有 `price` 的行并返回 200
  （BR-CART-UPD-4，`cart-update-upserts-missing-item` 用例断言的就是这个）—— 这是单体的缺陷形状，规则手册 Q10 要求默认照搬，不改。

## D-9 数据不预置

`scripts/init-db.js` 的 `initializeSampleBooks`（导入即写 books 表）不迁移：目标交付时四张业务表是空的，
数据由调用方写入（SI-16）。这属于 migration-change：单体的"新部署自带目录"不是任何端点的外部行为，
裁判的用例自带 fixture（`judge/fixtures/books.json` 等），不依赖种子数据。

## D-10 读路径不排序、不投影、不补全（保持）

- `orders-list` / `cart-list` / `books-list` / `search-books` 都不排序、不截断、不加 FilterExpression
  （BR-ORD-LIST-4、BR-BK-6、BR-SRC-6）：返回顺序就是 Query/Scan 的返回顺序，用例不得对先后下断言。
  我**没有**为它们申请 `result_order: "unspecified"` 放宽：单体的顺序本来就没有保证，裁判的 masks 已经把
  这些列表按无序集合比较（`judge/masks.json` 的 `unordered`），再放宽一次等于多关掉一层检查。
  只有 `bestsellers-list` 的排序是显式契约（BR-BST-2/3：按 salesCount 降序 + slice(0,20) + 只输出 bookId），必须实现。
- 不按 bookId 去 books 表补全（BR-CART-LIST-4、BR-BST-6）：已下架的书照样出现在榜单里、购物车里的幽灵 bookId 照样返回。

## D-11 不翻页、不加强一致读（`monolith_defect` 默认照搬）

GAP-3 / GAP-4 / GAP-5 / GAP-13 / GAP-18：全仓库没有一处 `LastEvaluatedKey` / `ExclusiveStartKey` / `ConsistentRead` / `Limit`。
决定是**照搬**：`books-list`/`bestsellers-list`/`search-books` 各只发一次 Scan（或一次 GSI Query），
`cart-list`/`orders-list` 各只发一次 Query，都只取本次 `Items` ⇒ 数据超过单次 1MB 时结果被静默截断、
写后立读可能读到旧值（GAP-18，本地后端立即可见所以裁判看不出来）。
不加缓存（单体没有 TTL 缓存 ⇒ 不写 `bounded_staleness`，也不给 S5 留缓存许可）。
理由：规则手册 PIPELINE_REDESIGN_v2 Q10 对单体缺陷默认照搬；改成翻页会扩大返回行集，属于对外行为变化，得由人确认。
验证方式：GAP-3/4/5/13/18 的截断与新鲜度在裁判的数据量下走不到 ⇒ `test: "none"`（只能读代码核实）。

## D-12 查询串重复键的数组语义（REST 的附带理由之一）

`?category=a&category=b`（`books-list`）与 `?q=a&q=b`（`search-books`）在单体里是数组，
前者把数组当 DynamoDB 的键值发出去 ⇒ 500 `One or more parameter values were invalid: Condition parameter type does not match schema type`；
后者调 `.toLowerCase()` ⇒ 500 `query.toLowerCase is not a function`（`books-duplicate-category-param`、`search-duplicate-q-param` 两个用例）。
决定：函数从 `multiValueQueryStringParameters` 构造查询参数（同一个键多个值 ⇒ 数组；一个值 ⇒ 字符串），
不得取最后一个、不得去重（SI-05）。
这条也影响 API 类型的选择：REST 的 payload 1.0 里有 `multiValueQueryStringParameters`，HTTP API 的 1.0 没有，
所以 `books-list`/`search-books` 想复现单体的数组语义就只能用 REST（见 D-14）。

## D-13 错误响应体由函数自己复刻（GAP-11）

单体所有 `catch` 都是 `next(error)`，出口是 `server.js:50-56` 的全局错误中间件：
`status = err.status || 500`，body `{error: err.message || 'Internal Server Error', stack: NODE_ENV==='development' ? err.stack : undefined}`，
而 `config.js:10` 的 `NODE_ENV` 默认就是 `development`（仓库里没有 `.env`）⇒ **默认部署的每个异常响应都带完整堆栈**。
决定：每个 API 函数自己 catch，返回同样形状（含 `stack`），并让 `err.message` 逐字保持 SDK/运行时抛出的消息
（裁判按符号比较 body，`<trace#N>` 只把 stack 掩成"任意字符串"，`error` 必须原样，见
`books-duplicate-category-param`、`search-non-string-name-500`、`wb-orders-non-object-elements-500-order-still-written`、
`cart-add-write-fault-baseline`(500 `Transaction is ongoing for the item (injected)`)、`orders-cart-delete-fails-all`(500 `Internal server error (injected)`)）。
两个例外：`bestsellers-list` 吞掉读失败返回 200 + `[]`（BR-BST-5 / SI-08）；
请求体 JSON 解析失败是 400（body-parser 的 `err.status=400`，SI-04 / GAP-12），不是 500。
异常不得逃逸成 API Gateway 的 502/500 `{"message": ...}`（SI-03）。

## D-14 应用级 CORS 中间件在目标里的两层落点（GAP-9）+ API 类型选 REST

`global_middleware[0]` 是 `cors({origin:'*', credentials:true})`：每个响应带
`Access-Control-Allow-Origin: *` 与 `Access-Control-Allow-Credentials: true`；
`Access-Control-Allow-Methods` / `Access-Control-Allow-Headers` **只在 OPTIONS 预检响应上**（`cors/lib/index.js:163-181`），
预检在单体里由中间件直接以 204 应答，不进任何 handler。
代理集成后没有中间件层，模板的 `Cors` 块只生成 OPTIONS 方法、不给真实响应加头 ⇒

1. 11 个 API 函数（`books-list`、`books-get-by-id`、`cart-list`、`cart-get-by-book`、`cart-add`、`cart-update`、`cart-remove`、
   `orders-list`、`orders-create`、`bestsellers-list`、`search-books`）在**每个**响应上返回
   `Access-Control-Allow-Origin: *` + `Access-Control-Allow-Credentials: true` + `Content-Type: application/json`
   （裁判实录里每个步骤的 headers 都只有这两个 CORS 头 + content-type，多返回 Allow-Methods/Allow-Headers 会被判成"单体没发的头"）；
2. 网关自己应答的响应（授权器的 401、限流 429、集成错误 5xx）用 `GatewayResponses` 补上同样的头：
   `DEFAULT_4XX`/`DEFAULT_5XX` 带全部四个头名（满足 GM2，也是单体在预检上发的那一套）；
   授权器拒绝走的具体类型 `UNAUTHORIZED`/`AUTHORIZER_FAILURE`/`ACCESS_DENIED` 单独配置，只带
   `Access-Control-Allow-Origin` 与 `Access-Control-Allow-Credentials`，与单体的非预检 401 完全一致；
3. 预检由 API Gateway 的 CORS 配置应答（`api_gateway.cors`）。

给 S2/S3 的落地提醒（避免 GM1 与裁判打架）：`gate` 的 GM1 是**文本检查**——它要求每个函数目录（或共用 layer）的源码里出现 `global_middleware` 的每一个头名。
四个头名都定义在同一个中间件条目里，所以 S2 要把它们集中写进一个共用模块/layer（例如 `cors.js` 里同时列出
`ACCESS_CONTROL_ALLOW_ORIGIN / ACCESS_CONTROL_ALLOW_CREDENTIALS / ACCESS_CONTROL_ALLOW_METHODS / ACCESS_CONTROL_ALLOW_HEADERS`，
只有前两个进真实响应，后两个只用于预检与 GatewayResponses 的兜底项）；**不要为了让 GM1 过就把后两个头塞进函数的响应里**，
那会让裁判在每一步报出"单体没发的头"。GM2 要求 `DEFAULT_4XX`/`DEFAULT_5XX` 覆盖全部四个头名（`api_gateway.gateway_responses` 已经这么写），
授权器应答走的是更具体的 `UNAUTHORIZED`/`AUTHORIZER_FAILURE`/`ACCESS_DENIED`，只带那两个 ⇒ 与单体的 401 实录逐符号一致。

`api_type`：选 **REST**，`rest_features=["gateway_responses"]`，`api_type_reason` 见 `api_gateway` 字段 ——
依据是 `gate` 的 AT1 会自动从 `global_middleware` 推导出 `gateway_responses`（除 `ACAO:*` 之外还有 `ACAC:true`），
HTTP API 的 `CorsConfiguration` 给网关自应答的 401 加不了这个头；另外 D-12 的重复查询串数组语义也需要 REST 的
`multiValueQueryStringParameters`。`clients="internet"`（单体是给浏览器用的带 CORS API，`credentials:true` 就是证据）。
没有用其它 REST 独有能力：不要求"只接受 ID token"（单体用自己的 HS256 JWT，迁移到用户池后 access/ID token 的差异不影响契约）、
没有 X-Ray/WAF/API Key/请求模型/缓存/资源策略/EDGE 端点的需求出处。
单体这套头本身是自相矛盾的（`ACAO:*` + `ACAC:true` 浏览器规范不允许带凭据的跨源响应用通配 Origin，规格 note 已说明）——
**照搬，不顺手修**：改 Origin 策略属于 `migration-change`，要人确认；这里不引入。

## D-15 进程级配置与客户端（GAP-16）

`utils/dynamodb.js:10-11` 在模块 require 时建一次客户端并复用，`config.js:7` / `server.js:7` 各自 `require('dotenv').config()`，
`config.js:16-18` 只在建客户端时读凭据 env（仓库没有 `.env` ⇒ 落到 SDK 默认凭据链）。
决定：表名/队列 URL/幂等表名通过 Lambda 环境变量注入（`environment_variables` 里的 `${资源逻辑名}` 占位由模板换成 `!Ref`），
区域与凭据交给 Lambda 执行角色（不再有手工 access key），DocumentClient 仍在 handler 模块作用域建一次以获得同样的连接复用（SI-13）。
`NODE_ENV`、`JWT_*`、`AUTH_DEV_MODE` 这些配置项随对应代码一起消失（D-3）。这是进程内状态换到函数级的必然结果，
没有对外可观察行为（GAP-16 ⇒ `test: "none"`）。

## D-16 令牌寿命/撤销（GAP-15）与 `me` 的 404 臂

单体的令牌规则在端点之外：accessToken 默认 `1h`、refreshToken 默认 `7d`、共享 HS256 密钥、refreshToken 不轮换、
没有登出也没有黑名单 ⇒ 一经签出有效期内撤不掉。迁移后由用户池接管，我把用户池的有效期设成与单体默认一致
（access 60 分钟、refresh 7 天，`cognito.token_validity`），不引入单体没有的撤销语义；
`POST /api/auth/refresh` 的 `401 'User not found'` 臂随端点删除（`judge/uncovered.json` 也早就注明它用请求走不到）。
`record_ruling` 里这条记成 migration-change（规则手册默认做法的必然结果）。

## D-17 `x-customer-id` 与 devMode 身份注入（GAP-14 / J4-3）

`middleware/auth.js:19-26` 与 `:36-40` 的两个分支都以 `config.auth.devMode` 为前提，默认配置（`AUTH_DEV_MODE` 未设置 ⇒ false，
仓库里没有 `.env`）根本不进去，所以**单体的默认对外契约**就是：只带 `x-customer-id` 不带合法 Bearer token ⇒ 401 且一次表访问都不发生；
带合法 token 时该头里填别人的 id 也改变不了读写作用域（`wb-me-x-customer-id-ignored`、
`wb-cart-x-customer-id-cannot-scope-data`、`wb-orders-x-customer-id-cannot-authenticate` 断言的就是这条）。
决定：目标里没有任何函数读这个头，身份只来自 `claims.sub`（SI-12）；devMode 的"无 token 冒充任意 customerId"和
"匿名请求共享 `default-customer-id`"两种能力随中间件一起消失（那本来就是只有开发配置下才有的越权路径，
不迁移它不属于"收窄单体的对外行为"——默认配置下它不存在）。`GET /api/auth/me` 里把 `default-customer-id`
判为未认证的那道守卫（BR-ME-3）也随端点一起删除。
401 的响应体差异见 D-4。

## D-18 contract 2：`verification_contract=2`、分类复核与逐条 `trace_arm` 裁定

`metadata.verification_contract = 2`（规格里 126 条业务规则带 `class`：116 `contract` + 10 `implementation-trace`）。

- 10 条 `implementation-trace` 全部逐条给了 SI，并逐字把被裁定的规格条目写进 `targets`：
  SI-18 `POST /api/auth/register#business_rules[4]`（BR-REG-5）、SI-19 `POST /api/auth/login#business_rules[4]`（BR-LGN-5）、
  SI-20 `GET /api/books/:id#business_rules[6]`（BR-BKI-7）、SI-21 `GET /api/cart#business_rules[6]`（BR-CART-LIST-7）、
  SI-22 `GET /api/cart/:bookId#business_rules[5]`（BR-CART-GET1-6）、SI-23 `POST /api/cart#business_rules[6]`（BR-CART-ADD-7）、
  SI-24 `PUT /api/cart#business_rules[5]`（BR-CART-UPD-6）、SI-25 `DELETE /api/cart#business_rules[5]`（BR-CART-DEL-6）、
  SI-26 `POST /api/orders#business_rules[4]`（BR-ORD-CRT-5）、SI-27 `GET /api/search#business_rules[8]`（BR-SRC-9）。
- 复核结论：这 10 条确实只是单体的内部做法（SDK 调用次数、有无缓存/预取、`ReturnValues` 拿回来的值没 Used、
  `Promise.all` 的并发形态、并发注册的非原子窗口），裁定的都只是"内部步骤不作断言"，
  写入内容、消息是否发出、权限判定、单条消息的失败粒度这些契约一条都没动。
  两条要特别划清界线的：
  SI-23（`cart-add`）—— 允许"只有一次无条件整行写"，但**不允许**改成先读后合并或加条件写，
  因为覆盖语义/落库属性是 BR-CART-ADD-3/4 的契约；
  SI-26（`orders-create` 的删除并发形态）—— 允许并发或串行，但"响应前全部删除完成"与"被拒时 5xx 且订单行保留"
  仍是 BR-ORD-CRT-7 的契约；实现仍选逐条 `DeleteItem`（不是 `BatchWriteItem`），一是保留"部分已删、部分被拒"的可观察差异，
  二是裁判的故障场景按 `op=DeleteItem` + 具体 `bookId` 出题（`judge/faults/orders-cart-delete-fails-all.json`、
  `orders-cart-delete-fails-once.json`），换成 BatchWriteItem 这些注入就打不中目标。
- 反向复核（把误标为实现轨迹的改回契约）：`POST /api/orders#business_rules[4]`（BR-ORD-CRT-5）标的是实现轨迹，但它自己
  的 note 已经写明失败可见性属契约，我没有扩大它的放宽范围。其余 116 条 `contract` 抽查后没有发现被错标的内部步骤。
- 规格里没有任何响应臂带 `trace_arm`（只有 `class`），CT3 因此没有待裁项。
- 验证线提出的三条 spec_issue（`judge/spec_issues.json` 的 J4-1/J4-2/J4-3）已逐条裁定进
  `metadata.spec_resolutions`（都是 `contract`：照搬单体的可观察后果），并对应 D-13/D-6/D-17。
- `metadata.perf_hints`：只有 BR-ORD-CRT-5 带 `perf_intent="batch"` ⇒ PH-1（kind `batch`，`orders-create`）。
  单体没有缓存/预取，因此没有 `cache`/`prefetch` 项，也没有 `bounded_staleness` 契约（D-11）。

## D-19 缺口编号 → 决定与验证方式（对应 `storage/gap_inventory.json`）

| 缺口 | 决定 | test |
|---|---|---|
| GAP-1 `multi_write`（下单写 3 次无事务） | 不加事务：`orders-create` 内联 `PutItem` 订单 + 逐条 `cart DeleteItem` 并全部 await，畅销榜改 SQS（D-5/D-6/SI-15） | judge：`orders-create-cart-delete-fault-baseline`、`orders-create-happy-clears-ordered-items`、`wb-orders-non-object-elements-500-order-still-written` |
| GAP-2 `fire_and_forget`（updateBestSellers） | SQS + 消费者幂等（D-5/D-6） | delta（重复投递的幂等裁判看不到） |
| GAP-3/GAP-4/GAP-5 Scan 不翻页（bestsellers/books/search） | 照搬（D-11/SI-07） | none（数据远小于 1MB，只能读代码核实） |
| GAP-6 bestsellers 排序 | 实现排序 + top20 + 只输出 bookId，并列名次的相对次序不断言（BR-BST-3/4） | judge：`bestsellers-ranked-top20`、`bestsellers-unknown-book-in-list`、`bestsellers-empty-table` |
| GAP-7 `Date.now()`（register 的 createdAt/updatedAt） | 随认证端点与 users 表一起删除，时间戳由用户池的标准属性承担（D-3） | none |
| GAP-8 `Date.now()`（orderDate） | 保持毫秒整数 Number（SI-14） | judge：`orders-create-clears-ordered-items-and-counts`、`orders-list-empty-then-after-create` |
| GAP-9 CORS 中间件 | 函数每个响应带 ACAO/ACAC + GatewayResponses 带网关自应答的头 + Cors 配置应答预检（D-14/SI-01） | judge：`orders-create-clears-ordered-items-and-counts`、`books-list-by-category`、`cart-add-overwrite-and-readback` |
| GAP-10 应用自签令牌 | Cognito 用户池 + API Gateway 授权器，认证端点删除（D-2/D-3/D-4） | judge：`cart-add-ignores-body-customerid`、`cart-update-cross-user`、`cart-delete-cross-user`、`orders-create-unknown-bookid-and-forged-price` |
| GAP-11 全局错误中间件 | 函数自己复刻 `{error, stack}` + `err.status||500`（D-13/SI-03） | judge：`books-duplicate-category-param`、`search-non-string-name-500`、`cart-add-write-fault-baseline`、`orders-cart-delete-fails-all` 对应的用例 |
| GAP-12 body-parser | 函数自己按 Content-Type 解析 JSON / urlencoded；非法 JSON ⇒ 400 `{error, stack}`；100kb 上限不复刻（D-13/SI-04/SI-17） | judge：`cart-add-form-encoded-strings`、`cart-add-validation`、`orders-create-validation` |
| GAP-13 Query 不翻页 | 照搬（D-11） | none |
| GAP-14 devMode 身份注入 | 不迁移 devMode；`x-customer-id` 无人读取（D-17/SI-12） | judge：`wb-cart-x-customer-id-cannot-scope-data`、`wb-orders-x-customer-id-cannot-authenticate` |
| GAP-15 令牌寿命与撤销 | 用户池接管，有效期对齐单体默认（access 60min / refresh 7d），不新增撤销语义（D-16） | none（令牌的过期/撤销不在任何用例的请求路径上可观察） |
| GAP-16 进程启动时的配置与客户端 | 环境变量注入 + 执行角色凭据 + handler 模块作用域建一次客户端（D-15/SI-13） | none（无对外可观察行为） |
| GAP-17 响应后才发生的畅销榜写 | SQS 消费者（D-5/D-6）；不在响应里等，也不承诺延迟上限 | delta |
| GAP-18 最终一致读（没有 ConsistentRead） | 照搬：不加 ConsistentRead、不加缓存（D-11） | none（本地后端立即可见，裁判看不出来） |

## D-20 没有的东⻄：文件存储、EventBridge、同步跨函数调用、Cognito 触发器

- `file_tags` 里没有 `FileUpload`/`FileStorage`，`config_info.nodejs_dependencies` 里也没有 multer/s3 之类的依赖
  ⇒ `s3_buckets = []`、`metadata.has_file_storage = false`。
- 没有扇出到 2 个以上业务域的事件 ⇒ `eventbridge_rules = []`、`metadata.architecture_pattern` 只因 SQS 这一路是 "Event-Driven"。
- 没有任何"调用方需要返回值"的跨域调用 ⇒ `lambda_invoke_permissions = []`。
- 不建 `post_confirmation` 或任何 Cognito 触发 Lambda（规则禁止；单体的注册流程除了写 users 行也没有别的副作用）。
