STAGE_ID: ROOM-spec-j3-r2-0-1
MODE: room

这是 m2s 迁移流水线的一个任务，由 v3 调度器派发；你的角色说明已经在系统提示词里。这是这个任务第 1 次尝试。

## 绝对路径
- MONOLITH = D:\Emma\MyLab\Mono2SLs\Mono2Sls-artifacts\mono_benchmark\bookstore
- WS = D:\Emma\MyLab\Mono2SLs\Mono2Sls-artifacts\M2S\runs\bookstore-m4

## 任务
STAGE_ID=ROOM-spec-j3-r2-0-1：按你的角色说明完成这一阶段。

## 讨论室
这是 spec 讨论室 `spec-j3-r2`，主题：规格对账（J3）有争议的差异需要裁定：
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
  "reason": "命中的集合与 total 都对（total=2，book-001 与 book-002 都在），只有 hits 两条的先后不同：oracle 是 [book-002, book-001]，用例按夹具顺序断言 [book-001, book-002]。事实库没有给这个顺序任何契约地位：GET /api/search#responses[1] 的 body 描述写'顺序为 scan 顺序，无排序、无截断'，#business_rules[3] 写'无相关性评分、无排序、无分页参数'，#business_rules[1] 写全表 scan 且只取第一页。顺序也确实无法从输入推出（同一夹具 books.json 顺序是 book-001,002,101,102,999，本步返回反序，而 search-hits-200 的 category 步返回 [book-101, book-102] 恰好正序——纯属 scan 落盘顺序的巧合）。它也不是易变值屏蔽不全：run_monolith 的 3 遍取值一致（否则会像 uuid/token 那样被符号化），所以不属于 mask_gap；被检验的行为（大小写不敏感子串匹配 #business_rules[2]/[4]）与顺序无关，在裁定落地前擅自把位置断言删掉会削弱用例并让'列表顺序要不要复现单体'这个问题失去比较依据，因此不归 case_error。这是一个'迁移后可能可以放宽的行为'，需要 architect 裁定。已在 spec 房间 spec-j3-r1（seq 1）请求一条通用的列表顺序裁定；storage/rulings.json 尚不存在，裁定未落地，维持 needs_ruling。",
  "fact_ref": "GET /api/search#responses[1]",
  "fix": "请裁定：事实库为列表响应指定了显式排序键的，顺序属于契约、必须复现（例如 GET /api/bestsellers#business_rules[1] 的 salesCount 降序，我不会放宽）；事实库说顺序即 scan/存储顺序、未给排序键的，顺序属于实现轨迹、不比较，并列（同排序键值）的相对次序同样不比较；relaxation=order，layers=[R]，raised_by=J3，affected_cases 含 search-case-insensitive-200(upper-q)、search-hits-200(author 及一并处理的 category)。裁定落地后我把本步改成顺序无关：total=2（数字）、hits 长度恰好为 2（钉住集合相等，防多返回的 bug 溜过）、两条命中无序包含 _source.id=book-001 与 book-002，并保留 status、_source 包装结构、条目属性不裁剪不新增、CORS 头这些契约断言。若裁判比较器不支持嵌套数组的无序包含，请给出等价写法；否则我会把'命中集合内容'登记为残余盲区而不是当成已覆盖。"
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
  "reason": "与 search-case-insensitive-200/upper-q/body.subset 同一根因（GET /api/search 多命中时 hits 的 scan 顺序）：本步命中的集合与 total 都对，只有位置是 [book-002, book-001] 而 predicted 断言 [book-001, book-002]。事实库明确不承诺顺序（#responses[1]'顺序为 scan 顺序，无排序'、#business_rules[3]'无排序'、#business_rules[1] 全表 scan）；本用例其余两步（name 只有一条命中、category 两条命中恰好与夹具同序）通过，正说明通过与否取决于 scan 落盘顺序而非规格。不是 mask_gap（3 遍取值稳定，未被符号化），也不宜直接归 case_error（是否要求目标复现单体 scan 顺序是迁移决策，事实库答不了，需 architect 定；同一问题还影响本用例 category 步以及以后任何无排序键的列表接口）。spec-j3-r1 房间已发言请求通用裁定，storage/rulings.json 仍不存在。",
  "fact_ref": "GET /api/search#business_rules[3]",
  "fix": "按同一条 order 裁定处理：本步改为 total=2 + hits 长度为 2 + 无序包含 book-001/book-002（两条的 author='J.R.R. Tolkien' 仍断言），保留 #business_rules[3] 要求的 {_source: 完整条目} 结构与属性不裁剪不新增、types.total=number、本步已有的 CORS 头断言（全局中间件）与 #business_rules[5] 的共享可见性；category 步（q=programming，目前只是碰巧与夹具同序）请裁定文字里明确允许 J1 一并改成顺序无关，无需再走一轮对账。"
 }
]。你这一轮以 `m2s-verifier` 身份发言。
规则：只在你这一轮发言；用 `mcp__m2s-server__room_post(room_id, kind, body)` 提交一条意见，然后用 `submit_task` 结束。不要修改工作区文件（这是讨论，不是实现）。
讨论室和公告板里给实现线看的内容只能有：失败的函数、出处、期望和实际结果；不能出现 verification/ 下的路径或原文，否则会被拒绝。
spec 讨论室：architect 的发言里用 `record_ruling` 写出结论；triage 讨论室：每个嫌疑模块用 JSON 归因 {"module": "code|template|spec|任务 id", "reason": "...", "confidence": "high|medium|low"}。
当前讨论：
[]

## 完成方式
1. 写工作报告 WS/storage/stage_reports/ROOM-spec-j3-r2-0-1.json（格式按你的角色说明）。
2. 调用 mcp__m2s-server__submit_task(summary=...) 提交，然后结束会话。不需要再输出 STAGE_COMPLETE。
3. 做不下去时，调用 mcp__m2s-server__report_blocked(reason, evidence, suspected_module)，然后结束会话；认为是更早阶段的问题时，在 suspected_module 写那个阶段（例如 S2），并在 evidence 里给出证据。
4. 调度器会自己运行这个任务的检查（room_turn），你的自述不作为判定依据。

## 注意
- 如果调用工具或等待过程中遇到网络错误（例如 'Network error: Connection error'），重试 1-2 次再继续，不要因为一次网络错误就直接放弃或提前结束。
- 只改这个任务范围内的文件：storage/stage_reports/ROOM-spec-j3-r2-0-1.json、storage/analysis_report.json。范围之外的改动会被撤销，这次尝试判为失败。
- 不要修改流水线自己的工具：m2s-mcp-server/、.openharness/、~/.openharness/ 下的文件。
- 不要读写 WS/coord/（调度器的内部状态）。不要调用 mcp_auth。
- 不要读别的工作区（WS 所在 runs/ 目录下的其他目录、runs-prev-*），也不要参照它们的产物。
- 这份说明可以随时用 mcp__m2s-server__get_task_context 重新取回。