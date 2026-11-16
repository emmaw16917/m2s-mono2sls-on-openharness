STAGE_ID: F2-bestsellers-books-cart-orders-search

这是 m2s 迁移流水线的一个任务，由 v3 调度器派发；你的角色说明已经在系统提示词里。这是这个任务第 3 次尝试。

## 绝对路径
- MONOLITH = D:\Emma\MyLab\Mono2SLs\Mono2Sls-artifacts\mono_benchmark\bookstore
- WS = D:\Emma\MyLab\Mono2SLs\Mono2Sls-artifacts\M2S\runs\bookstore-m4

## 任务
STAGE_ID=F2-bestsellers-books-cart-orders-search：按你的角色说明完成这一阶段。

## 回炉信息（调度器转来的，请据此修正）

### 1. 来自 J4
白盒补测（J4）在单体代码里发现了事实库没有、或者写错了的规则（judge/spec_issues.json）。按代码核实，用 annotate_entry_point 补进或修正事实库（每条都带出处），不要改动无关的事实；改完 check_provenance 必须 PASS：
[
 {
  "id": "J4-1",
  "endpoint": "POST /api/orders",
  "kind": "missing",
  "rule": "books 元素里的 quantity 缺失或不是数字时，畅销榜的后台更新会失败而被静默吞掉：utils/bestsellers.js 用 UpdateExpression 'ADD salesCount :qty' 且 :qty 直接取 book.quantity，DynamoDB 对 ADD 的操作数要求 number 类型（NULL/字符串/缺失都会让 UpdateItem 报 ValidationException，SDK 不重试），该异常被 updateBestSeller 的 catch 捕获、只写日志并返回 false，updateBestSellers 只是把 failed 计数加一。因此外部可见的结果是：订单仍返回 200、购物车条目照常被删、Bookstore-Orders 里 books 原样保存这个缺 quantity 的元素，但 Bookstore-Bestsellers 里这个 bookId 的 salesCount 不变（条目原本不存在时也不会被新建，于是这个 bookId 根本不出现在 GET /api/bestsellers 里）。事实库只写了 business_rules[6]（'每个 {bookId, quantity} 让 salesCount 增加 quantity'）和 business_rules[10]（元素缺 bookId 时 → DeleteItem 抛错 → 500），没有写'quantity 缺失/非数字'这一臂：按 business_rules[6] 的字面理解会以为 quantity 缺失也能记一次销量。",
  "evidence": [
   {
    "file": "bookstore/utils/bestsellers.js",
    "line": 20,
    "symbol": "UpdateExpression: 'ADD salesCount :qty',"
   },
   {
    "file": "bookstore/utils/bestsellers.js",
    "line": 22,
    "symbol": "':qty': quantity"
   },
   {
    "file": "bookstore/utils/bestsellers.js",
    "line": 29,
    "symbol": "} catch (error) {"
   },
   {
    "file": "bookstore/utils/bestsellers.js",
    "line": 49,
    "symbol": "const result = await updateBestSeller(book.bookId, book.quantity);"
   },
   {
    "file": "bookstore/routes/orders.js",
    "line": 87,
    "symbol": "updateBestSellers(books).then(result => {"
   }
  ],
  "cases": [
   "wb-order-qty-not-number"
  ]
 },
 {
  "id": "J4-2",
  "endpoint": "GET /api/search",
  "kind": "missing",
  "rule": "GET /api/search 的匹配把条目里的 name、author、category 当成字符串直接调用 toLowerCase：这三个字段里只要有任何一个是“真值但不是字符串”（例如 Bookstore-Books 里某条书目的 name 是数字 42、author 是布尔值），item.name.toLowerCase() 就抛 TypeError，被 handler 的 catch 经 next(error) 交给统一错误中间件 → 整个请求 500。关键是它与被查询词无关：即使 q 只命中别的正常条目、甚至一条都不命中，坏条目照样让整次搜索失败（filter 会遍历 scan 返回的每一条）。同一份数据用 GET /api/books、GET /api/books/:id 读都正常返回 200（那两个端点不碰这些字段）。事实库 business_rules[2] 只说“某个字段缺失或为假值时该字段不参与匹配”，business_rules[6]/responses[2] 只举了 q 不是字符串（数组）导致 toLowerCase 抛错的例子，没有写条目字段是非字符串真值时同样会崩。Bookstore-Books 没有任何写接口，这种数据只能由外部导入的种子数据造成，因此需要一个裁定：迁移后是照抄“整次搜索 500”，还是跳过类型不对的字段（照抄会让目标端多背一条脆弱性，跳过则与单体的可见结果不同）。",
  "evidence": [
   {
    "file": "bookstore/routes/search.js",
    "line": 35,
    "symbol": "(item.name && item.name.toLowerCase().includes(lowerQuery)) ||"
   },
   {
    "file": "bookstore/routes/search.js",
    "line": 36,
    "symbol": "(item.author && item.author.toLowerCase().includes(lowerQuery)) ||"
   },
   {
    "file": "bookstore/routes/search.js",
    "line": 37,
    "symbol": "(item.category && item.category.toLowerCase().includes(lowerQuery))"
   },
   {
    "file": "bookstore/routes/search.js",
    "line": 47,
    "symbol": "} catch (error) {"
   }
  ],
  "cases": [
   "wb-search-non-string-name-500"
  ]
 },
 {
  "id": "J4-3",
  "endpoint": "POST /api/cart",
  "kind": "missing",
  "rule": "server.js:33-34 用默认参数挂了 bodyParser.json() 和 bodyParser.urlencoded({extended:true})，两者的 limit 默认都是 100kb（body-parser/lib/types/json.js:57 `bytes.parse(opts.limit || '100kb')`）。请求体超过 102400 字节时 raw-body 抛 type='entity.too.large'、status=413 的错误，这个错误发生在 authMiddleware 和各 handler 之前，因此：带不带 Authorization 都一样返回 413（不会先 401），响应体由 server.js:50-56 的统一错误中间件给出 {'error':'request entity too large'}（NODE_ENV=development 默认值下还带 stack），并且仍然带 CORS 头。这一条适用于每一个有请求体的端点（POST /api/auth/register、POST /api/auth/login、POST /api/auth/refresh、POST /api/cart、PUT /api/cart、DELETE /api/cart、POST /api/orders），事实库的 responses 里只有 400（entity.parse.failed，JSON 不合法）一臂，没有任何地方写 413。注意：迁移到 API Gateway 后请求体上限是 10MB，同一个请求在目标上不会 413 而是会进到 handler，所以这条更像是要裁定“迁移变更”（例如按 spec-gap/migration-change 放宽）而不是照抄；本轮没有为它写用例（要用例就得塞一个 >102400 字节的请求体，且目标端无法等价复现）。",
  "evidence": [
   {
    "file": "bookstore/server.js",
    "line": 33,
    "symbol": "app.use(bodyParser.json());"
   },
   {
    "file": "bookstore/server.js",
    "line": 34,
    "symbol": "app.use(bodyParser.urlencoded({ extended: true }));"
   },
   {
    "file": "bookstore/server.js",
    "line": 52,
    "symbol": "res.status(err.status || 500).json({"
   }
  ],
  "cases": []
 }
]

## 完成方式
1. 写工作报告 WS/storage/stage_reports/F2-bestsellers-books-cart-orders-search.json（格式按你的角色说明）。
2. 调用 mcp__m2s-server__submit_task(summary=...) 提交，然后结束会话。不需要再输出 STAGE_COMPLETE。
3. 做不下去时，调用 mcp__m2s-server__report_blocked(reason, evidence, suspected_module)，然后结束会话；认为是更早阶段的问题时，在 suspected_module 写那个阶段（例如 S2），并在 evidence 里给出证据。
4. 调度器会自己运行这个任务的检查（check_provenance_pkg），你的自述不作为判定依据。

## 注意
- 如果调用工具或等待过程中遇到网络错误（例如 'Network error: Connection error'），重试 1-2 次再继续，不要因为一次网络错误就直接放弃或提前结束。
- 只改这个任务范围内的文件：storage/analysis_report.json、storage/stage_reports/F2-bestsellers-books-cart-orders-search.json。范围之外的改动会被撤销，这次尝试判为失败。
- 不要修改流水线自己的工具：m2s-mcp-server/、.openharness/、~/.openharness/ 下的文件。
- 不要读写 WS/coord/（调度器的内部状态）。不要调用 mcp_auth。
- 不要读别的工作区（WS 所在 runs/ 目录下的其他目录、runs-prev-*），也不要参照它们的产物。
- 这份说明可以随时用 mcp__m2s-server__get_task_context 重新取回。