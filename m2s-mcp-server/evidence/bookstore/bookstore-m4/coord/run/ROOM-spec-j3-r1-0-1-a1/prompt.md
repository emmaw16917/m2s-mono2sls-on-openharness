STAGE_ID: ROOM-spec-j3-r1-0-1
MODE: room

这是 m2s 迁移流水线的一个任务，由 v3 调度器派发；你的角色说明已经在系统提示词里。这是这个任务第 1 次尝试。

## 绝对路径
- MONOLITH = D:\Emma\MyLab\Mono2SLs\Mono2Sls-artifacts\mono_benchmark\bookstore
- WS = D:\Emma\MyLab\Mono2SLs\Mono2Sls-artifacts\M2S\runs\bookstore-m4

## 任务
STAGE_ID=ROOM-spec-j3-r1-0-1：按你的角色说明完成这一阶段。

## 讨论室
这是 spec 讨论室 `spec-j3-r1`，主题：规格对账（J3）有争议的差异需要裁定：
[
 {
  "id": "search-case-insensitive-200/upper-q/body.subset",
  "case": "search-case-insensitive-200",
  "step": "upper-q",
  "request": {
   "method": "GET",
   "path": "/api/search"
  },
  "check": "body.subset",
  "predicted": {
   "total": 2,
   "hits": [
    {
     "_source": {
      "id": "book-001"
     }
    },
    {
     "_source": {
      "id": "book-002"
     }
    }
   ]
  },
  "actual": {
   "total": 2,
   "hits": [
    {
     "_source": {
      "cover": "https://covers.example/book-002.png",
      "author": "J.R.R. Tolkien",
      "price": 20,
      "name": "The Silmarillion",
      "rating": 4.2,
      "id": "book-002",
      "category": "fiction"
     }
    },
    {
     "_source": {
      "cover": "https://covers.example/book-001.png",
      "author": "J.R.R. Tolkien",
      "price": 15.5,
      "name": "The Hobbit",
      "rating": 4.5,
      "id": "book-001",
      "category": "fiction"
     }
    }
   ]
  },
  "fact_refs": [
   "GET /api/search#business_rules[2]",
   "GET /api/search#business_rules[4]",
   "GET /api/search#responses[1]"
  ],
  "reason": "命中的集合是对的，只有顺序不对：预期 hits 按夹具顺序 [book-001, book-002]，单体实际返回 [book-002, book-001]，total=2 两边一致。事实库明确说顺序不由业务决定：GET /api/search#responses[1] \"顺序为 scan 顺序，无排序、无截断\"、business_rules[3] \"无相关性评分、无排序、无分页参数\"、business_rules[1] 全表 scan 不处理 LastEvaluatedKey。routes/search.js 只是把 filter 命中的条目 push 进数组，顺序完全由 DynamoDB scan 返回 Items 的顺序给出，夹具顺序推不出它（同一用例的 mixed-name/mixed-target 只有一条命中所以没暴露；search-hits-200 的 category 那一步两条命中恰好按夹具顺序通过，说明顺序是易变项而非规格项）。问题不是用例写错也不是屏蔽不全，而是事实没有回答\"迁移后的实现是否必须复现单体的 scan 顺序\"——迁移后 Books 表结构/访问方式可能变化，顺序很可能不同。请 architect 裁定（建议 relaxation: \"order\"，层 R），裁定之后 J1 把这两步的预期改成顺序无关的集合断言（total + 对 hits 用 contains 断言每个 _source.id），保留 business_rules[2]/[4] 真正要测的大小写不敏感子串匹配。",
  "fact_ref": "GET /api/search#responses[1]",
  "fix": "裁定命中列表顺序是否必须与单体 scan 顺序一致；若放宽（relaxation: order），search-case-insensitive-200/upper-q 改为 body.contains 断言两个 _source.id + total=2，不断言位置。"
 },
 {
  "id": "search-hits-200/author/body.subset",
  "case": "search-hits-200",
  "step": "author",
  "request": {
   "method": "GET",
   "path": "/api/search"
  },
  "check": "body.subset",
  "predicted": {
   "total": 2,
   "hits": [
    {
     "_source": {
      "id": "book-001",
      "author": "J.R.R. Tolkien"
     }
    },
    {
     "_source": {
      "id": "book-002",
      "author": "J.R.R. Tolkien"
     }
    }
   ]
  },
  "actual": {
   "total": 2,
   "hits": [
    {
     "_source": {
      "cover": "https://covers.example/book-002.png",
      "author": "J.R.R. Tolkien",
      "price": 20,
      "name": "The Silmarillion",
      "rating": 4.2,
      "id": "book-002",
      "category": "fiction"
     }
    },
    {
     "_source": {
      "cover": "https://covers.example/book-001.png",
      "author": "J.R.R. Tolkien",
      "price": 15.5,
      "name": "The Hobbit",
      "rating": 4.5,
      "id": "book-001",
      "category": "fiction"
     }
    }
   ]
  },
  "fact_refs": [
   "GET /api/search#responses[1]",
   "GET /api/search#business_rules[1]",
   "GET /api/search#business_rules[2]",
   "GET /api/search#business_rules[3]",
   "GET /api/search#auth",
   "GET /api/search#request"
  ],
  "reason": "与 search-case-insensitive-200/upper-q 同源：q=tolkien 命中集合与 total=2 都对，只是实际顺序是 [book-002, book-001]，预期按夹具顺序写了 [book-001, book-002]（`body.subset` 对数组要求等长且逐位置递归包含，位置一变即失败）。事实库说 hits 顺序就是 scan 顺序、无排序（GET /api/search#responses[1]、business_rules[3]），也没有任何业务规则规定排序，因此顺序属于实现轨迹，不是契约；同用例 category 那一步（book-101/book-102）恰好按夹具顺序通过，进一步说明这个顺序不是能从规格推出来的。要不要目标复现单体 scan 顺序，事实库没答，需要 architect 裁定（建议 relaxation: \"order\"）。裁定放宽后 J1 把这一步改成顺序无关断言（total=2 + hits 里 contains {_source:{id:'book-001',author:'J.R.R. Tolkien'}} 与 {_source:{id:'book-002',author:'J.R.R. Tolkien'}}），本用例其它契约部分（business_rules[3] 的条目属性不裁剪——name 那一步对整条 _source 的断言、responses[1] 的 hits 项只包一个 _source、全局 CORS 头）保持不放宽。",
  "fact_ref": "GET /api/search#responses[1]",
  "fix": "同 search-case-insensitive-200/upper-q：需要一条关于列表顺序的裁定；放宽后 search-hits-200/author 改为顺序无关的 contains 断言，其余断言不动。"
 }
]。你这一轮以 `m2s-verifier` 身份发言。
规则：只在你这一轮发言；用 `mcp__m2s-server__room_post(room_id, kind, body)` 提交一条意见，然后用 `submit_task` 结束。不要修改工作区文件（这是讨论，不是实现）。
讨论室和公告板里给实现线看的内容只能有：失败的函数、出处、期望和实际结果；不能出现 verification/ 下的路径或原文，否则会被拒绝。
spec 讨论室：architect 的发言里用 `record_ruling` 写出结论；triage 讨论室：每个嫌疑模块用 JSON 归因 {"module": "code|template|spec|任务 id", "reason": "...", "confidence": "high|medium|low"}。
当前讨论：
[]

## 完成方式
1. 写工作报告 WS/storage/stage_reports/ROOM-spec-j3-r1-0-1.json（格式按你的角色说明）。
2. 调用 mcp__m2s-server__submit_task(summary=...) 提交，然后结束会话。不需要再输出 STAGE_COMPLETE。
3. 做不下去时，调用 mcp__m2s-server__report_blocked(reason, evidence, suspected_module)，然后结束会话；认为是更早阶段的问题时，在 suspected_module 写那个阶段（例如 S2），并在 evidence 里给出证据。
4. 调度器会自己运行这个任务的检查（room_turn），你的自述不作为判定依据。

## 注意
- 如果调用工具或等待过程中遇到网络错误（例如 'Network error: Connection error'），重试 1-2 次再继续，不要因为一次网络错误就直接放弃或提前结束。
- 只改这个任务范围内的文件：storage/stage_reports/ROOM-spec-j3-r1-0-1.json、storage/analysis_report.json。范围之外的改动会被撤销，这次尝试判为失败。
- 不要修改流水线自己的工具：m2s-mcp-server/、.openharness/、~/.openharness/ 下的文件。
- 不要读写 WS/coord/（调度器的内部状态）。不要调用 mcp_auth。
- 不要读别的工作区（WS 所在 runs/ 目录下的其他目录、runs-prev-*），也不要参照它们的产物。
- 这份说明可以随时用 mcp__m2s-server__get_task_context 重新取回。