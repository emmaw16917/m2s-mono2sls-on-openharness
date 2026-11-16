STAGE_ID: F2-bestsellers-books-cart-orders-search

这是 m2s 迁移流水线的一个任务，由 v3 调度器派发；你的角色说明已经在系统提示词里。这是这个任务第 2 次尝试。

## 绝对路径
- MONOLITH = D:\Emma\MyLab\Mono2SLs\Mono2Sls-artifacts\mono_benchmark\bookstore
- WS = D:\Emma\MyLab\Mono2SLs\Mono2Sls-artifacts\M2S\runs\bookstore-m4

## 任务
STAGE_ID=F2-bestsellers-books-cart-orders-search：按你的角色说明完成这一阶段。

## 回炉信息（调度器转来的，请据此修正）

### 1. 来自 human
规格对账（J3）发现事实库和单体的实际行为不一致（判为事实错误）。按单体代码核实，用 annotate_entry_point 修正下面这些事实（每条都要有出处），不要改动无关的事实；改完 check_provenance 必须 PASS。`predicted` 是按事实库推出的预期，`actual` 是单体的实际输出：
[
 {
  "id": "cart-add-non-json-content-type-400/form/status",
  "case": "cart-add-non-json-content-type-400",
  "step": "form",
  "request": {
   "method": "POST",
   "path": "/api/cart"
  },
  "check": "status",
  "predicted": 400,
  "actual": 200,
  "fact_refs": [
   "POST /api/cart#responses[0]",
   "POST /api/cart#responses[2]",
   "POST /api/cart#request"
  ]
 },
 {
  "id": "cart-add-non-json-content-type-400/form/body.subset",
  "case": "cart-add-non-json-content-type-400",
  "step": "form",
  "request": {
   "method": "POST",
   "path": "/api/cart"
  },
  "check": "body.subset",
  "predicted": {
   "error": "Missing required fields: bookId, quantity, price"
  },
  "actual": {
   "message": "Item added to cart successfully"
  },
  "fact_refs": [
   "POST /api/cart#responses[0]",
   "POST /api/cart#responses[2]",
   "POST /api/cart#request"
  ]
 },
 {
  "id": "cart-add-non-json-content-type-400/read/body.equals",
  "case": "cart-add-non-json-content-type-400",
  "step": "read",
  "request": {
   "method": "GET",
   "path": "/api/cart"
  },
  "check": "body.equals",
  "predicted": [],
  "actual": [
   {
    "customerId": "<id:u1>",
    "quantity": "2",
    "price": "10",
    "bookId": "book-001"
   }
  ],
  "fact_refs": [
   "POST /api/cart#responses[0]",
   "POST /api/cart#responses[2]",
   "POST /api/cart#request"
  ]
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