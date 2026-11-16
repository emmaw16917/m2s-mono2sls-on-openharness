STAGE_ID: J3
MODE: J3

这是 m2s 迁移流水线的一个任务，由 v3 调度器派发；你的角色说明已经在系统提示词里。这是这个任务第 2 次尝试。

## 绝对路径
- MONOLITH = D:\Emma\MyLab\Mono2SLs\Mono2Sls-artifacts\mono_benchmark\bookstore
- WS = D:\Emma\MyLab\Mono2SLs\Mono2Sls-artifacts\M2S\runs\bookstore-m4

## 任务
MODE: J3。给 judge/reconcile.json 里的每一条差异（J1 按事实库写的 predicted 和单体实际输出对不上的地方）分类，写进 judge/reconcile_classes.json：fact_error / needs_ruling / case_error / mask_gap，每条写 reason（证据）。mask_gap 要同时在 judge/mask_rules.json 里补屏蔽规则。按你的角色说明里"J3"一节做。

## 回炉信息（调度器转来的，请据此修正）

### 1. 来自 recheck
judge/reconcile_classes.json 的问题：
auth-endpoints-bad-json-400/reg-form-encoding/status: not classified
auth-endpoints-bad-json-400/reg-form-encoding/body.subset: not classified
cart-add-non-json-content-type-400/form/status: not classified
cart-add-non-json-content-type-400/form/body.subset: not classified
cart-add-non-json-content-type-400/read/body.equals: not classified
search-case-insensitive-200/upper-q/body.subset: not classified
search-hits-200/author/body.subset: not classified

上一次的工作报告全文：
{
  "stage_id": "J3",
  "agent": "m2s-verifier",
  "status": "success",
  "summary": "对账的 7 条差异全部归类：5 条 fact_error（事实库把 body-parser 的请求体解析写成\"只有 JSON 才解析\"，漏了 server.js:34 的 bodyParser.urlencoded，导致表单编码用例的 3 步预期全线偏掉）、2 条 needs_ruling（/api/search 的 hits 顺序来自 DynamoDB scan，事实库明说无排序，需要 architect 裁定是否放宽顺序）；没有 case_error，也没有 mask_gap，因此不需要新增屏蔽规则。",
  "differences_total": 7,
  "classes": {
    "fact_error": [
      "auth-endpoints-bad-json-400/reg-form-encoding/status",
      "auth-endpoints-bad-json-400/reg-form-encoding/body.subset",
      "cart-add-non-json-content-type-400/form/status",
      "cart-add-non-json-content-type-400/form/body.subset",
      "cart-add-non-json-content-type-400/read/body.equals"
    ],
    "needs_ruling": [
      "search-case-insensitive-200/upper-q/body.subset",
      "search-hits-200/author/body.subset"
    ],
    "case_error": [],
    "mask_gap": []
  },
  "counts": {"fact_error": 5, "needs_ruling": 2, "case_error": 0, "mask_gap": 0},
  "mask_rules": "未新增：7 条差异都不是易变值屏蔽不全造成的假差异（表单/扫描顺序都是稳定可复现的值），judge/mask_rules.json 不需要创建。",
  "root_causes": [
    {
      "what": "事实库把请求体解析写成\"content-type 不是 JSON 就不解析、req.body 为 {}\"",
      "evidence": "bookstore/server.js:33-34 同时挂了 bodyParser.json() 与 bodyParser.urlencoded({extended:true})；routes/auth.js:22/25/33/40、routes/cart.js:70/72 直接从 req.body 取值。单体实际行为：表单编码 register 返回 201、表单编码 POST /api/cart 返回 200 且条目里的 quantity/price 是字符串（与 POST /api/cart#business_rules[1] 一致）。",
      "sites_to_fix": [
        "POST /api/auth/register#request.headers[content-type]",
        "POST /api/auth/register#responses[6]",
        "POST /api/auth/login#request.headers[content-type]",
        "POST /api/auth/login#responses[5]",
        "POST /api/auth/refresh#request.headers[content-type]",
        "POST /api/auth/refresh#responses[5]",
        "POST /api/cart#request（漏记：没有任何请求体解析事实）"
      ],
      "also": "宜补一条全局请求解析条目（覆盖 server.js:33-34 两个中间件与各自 100kb 默认上限）；现有 cors 全局条目只是提到 bodyParser.urlencoded 存在但声明\"不在本项记录\"。",
      "followup": "事实库修正后 J1 需更新 auth-endpoints-bad-json-400/reg-form-encoding（预期 201 + responses[4] 的形状）与 cart-add-non-json-content-type-400 的 form（预期 200 + responses[1]）和 read（预期含一条 quantity/price 为字符串的条目）；请求本身不用改。"
    },
    {
      "what": "/api/search 的 hits 顺序：事实说\"顺序为 scan 顺序，无排序\"，用例按夹具顺序断言了位置",
      "evidence": "GET /api/search#responses[1]、business_rules[3]（无排序）、business_rules[1]（全表 scan）；routes/search.js:31-46 只做 filter+map，不排序。实际 q=tolkien 返回 [book-002, book-001]，而同一用例 q=programming 返回 [book-101, book-102] 与夹具顺序一致——顺序无法从夹具推出。",
      "asked_of_architect": "是否要求迁移后的实现复现单体的 scan 顺序；建议 relaxation: \"order\"（层 R）。裁定放宽后 J1 把这两步改成顺序无关断言（total + 对 hits 用 contains 断言每个 _source.id/作者），其余契约断言不放宽。",
      "note": "storage/rulings.json 目前不存在，这条顺序问题还没有任何裁定覆盖。"
    }
  ],
  "self_check": {
    "tool": "check_reconcile_classes",
    "status": "PASS",
    "differences": 7,
    "classes": {"fact_error": 5, "needs_ruling": 2, "case_error": 0, "mask_gap": 0},
    "errors": []
  },
  "recheck_feedback_addressed": "上次 recheck 报的 7 条 not classified 就是本次全部差异清单（judge/reconcile_classes.json 当时还不存在），现已逐条分类并给出证据。",
  "routing": [
    {"to": "annotator", "reason": "修正 bodyParser.urlencoded 的请求体解析事实（7 个位置，见 root_causes[0].sites_to_fix），随后 J1 更新 2 个用例的 predicted"},
    {"to": "architect", "reason": "裁定 /api/search 命中列表的顺序是否必须与单体 scan 顺序一致（建议 relaxation: order），随后 J1 把 2 个用例的 hits 断言改成顺序无关"}
  ],
  "issues": [],
  "outputs": [
    "judge/reconcile_classes.json"
  ]
}

## 完成方式
1. 写工作报告 WS/storage/stage_reports/J3.json（格式按你的角色说明）。
2. 调用 mcp__m2s-server__submit_task(summary=...) 提交，然后结束会话。不需要再输出 STAGE_COMPLETE。
3. 做不下去时，调用 mcp__m2s-server__report_blocked(reason, evidence, suspected_module)，然后结束会话；认为是更早阶段的问题时，在 suspected_module 写那个阶段（例如 S2），并在 evidence 里给出证据。
4. 调度器会自己运行这个任务的检查（reconcile_classified），你的自述不作为判定依据。

## 注意
- 如果调用工具或等待过程中遇到网络错误（例如 'Network error: Connection error'），重试 1-2 次再继续，不要因为一次网络错误就直接放弃或提前结束。
- 只改这个任务范围内的文件：judge/reconcile_classes.json、judge/mask_rules.json、storage/stage_reports/J3.json。范围之外的改动会被撤销，这次尝试判为失败。
- 不要修改流水线自己的工具：m2s-mcp-server/、.openharness/、~/.openharness/ 下的文件。
- 不要读写 WS/coord/（调度器的内部状态）。不要调用 mcp_auth。
- 不要读别的工作区（WS 所在 runs/ 目录下的其他目录、runs-prev-*），也不要参照它们的产物。
- 这份说明可以随时用 mcp__m2s-server__get_task_context 重新取回。