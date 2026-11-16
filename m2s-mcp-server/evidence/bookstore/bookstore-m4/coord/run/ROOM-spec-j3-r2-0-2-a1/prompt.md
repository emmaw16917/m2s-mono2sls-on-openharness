STAGE_ID: ROOM-spec-j3-r2-0-2
MODE: room

这是 m2s 迁移流水线的一个任务，由 v3 调度器派发；你的角色说明已经在系统提示词里。这是这个任务第 1 次尝试。

## 绝对路径
- MONOLITH = D:\Emma\MyLab\Mono2SLs\Mono2Sls-artifacts\mono_benchmark\bookstore
- WS = D:\Emma\MyLab\Mono2SLs\Mono2Sls-artifacts\M2S\runs\bookstore-m4

## 任务
STAGE_ID=ROOM-spec-j3-r2-0-2：按你的角色说明完成这一阶段。

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
[
 {
  "seq": 1,
  "ts": "2026-11-13T02:30:09+0800",
  "room": "spec-j3-r2",
  "kind": "spec",
  "sender_role": "m2s-verifier",
  "task": "ROOM-spec-j3-r2-0-1",
  "body": "【m2s-verifier】结论：本室列出的两条差异都已经被在效裁定覆盖，**不需要新裁定**，请求按\"已裁定、无需第三轮对账\"关闭本室。房间主题里的 needs_ruling 清单是裁定落地前的旧快照。\n\n一、现状核对（我这一轮实际读的账，不是复述）\n- storage/rulings.json 现有两条，都是 category=contract + relaxation=order + layers=[R]：R-ROOM-spec-j3-r1-1-1-a1-1（列表顺序通用规则）、R-ROOM-spec-j3-r1-1-2-a1-1（收窄与扩围）。两者的 affects.cases 已同时点名 search-case-insensitive-200#upper-q、search-hits-200#author（以及同一用例其余 4 个列表步骤和两条裸用例条目）。\n- judge/reconcile.json 当前 differences=0、resolved_by_rulings=2，就是本室这两条。我按步骤粒度逐字复核过这 2 条明细：total 均为 2、命中的 id 集合均为 {book-001, book-002}、每个条目 _source 的键集合与取值逐字相同、包装结构相同，差别只在先后——符合 R-...1-1 栏 2 \"仅顺序不同\"的前提，没有被折叠掉的内容差异（这正是 R-...1-2 第 6 点要求 J1 每轮做的复核，我做了）。\n\n二、我撤回上一轮（J3）自己提的两个请求，避免被当成待办\n1. \"若比较器不支持嵌套数组的无序包含请给等价写法\"——已由 R-...1-2 第 6 点确认：标记从 steps.<name> 起递归整个模板，命中的就是 body.hits 这一层，嵌套数组的无序比较生效。集合内容仍在比较，**不登记为残余盲区**。\n2. \"把 upper-q/author 改成 length + 无序 contains\"这个退化写法我自己撤回：/api/search 的响应体是对象、hits 是嵌套数组，改成 contains 会丢掉\"就是这两本书\"的集合检查。与 R-...1-1 拒绝选项 D 的口径一致。J1 不改写这三步的 P 层预期。\n\n三、验证线接下来按裁定执行的三条口径（请 architect 事后据此核我）\n- P 层记账：因为预期保持原样，这两条会每轮对账都出现在 resolved_by_rulings。我不把它当回归信号，只读明细不看聚合计数；一旦某轮 resolved_by_rulings 的条目数与 differences 的条目数不一致、或出现非\"仅顺序\"的明细，我单独升级上报。\n- V2 / run_judge 的判据：这些步骤**只因先后不同**而失败（多重集、长度、条目内容、total 全相等）时，我判为 relaxation 机制未生效的测量问题，记 tool_issues，不退给实现线；任何**内容级**差异（少一条、多一条、属性被裁剪或新增、_source 包装变化、total 与长度不等）在 order 放宽下仍必须判 fail，照旧分诊。这与 R-...1-2 第 6 点\"order 不参与判 ruled\"一致。\n- 不外推：本条不得用来放宽 (a) GET /api/bestsellers（应用层显式排序，R-...1-2 第 1 点，继续逐位置比较）；(b) 条目内层的请求体快照数组（下单读回类步骤，第 4 点）；(c) 同分并列次序与第一页截断/翻页完整性（第 5 点，属 GAP-6 与 GAP-3/4/5/15，各自独立裁定）。谁援引本条放宽这些，我按超范围驳回。\n\n四、一句提醒（不是新问题，不需要裁定）：affected_cases 里的裸用例条目会让这两个用例以后新增的 /api/search 列表步骤自动继承本条；但**别的接口**若由 J4 新增无排序键的列表步骤并出现顺序差异，不能援引本条，要另走一轮对账/裁定——我会在 J4/V2 按这个口径把关。\n\n五、给调度器的落地请求：本室两条差异在 J3 的分类应从 needs_ruling 更新为\"已由 R-ROOM-spec-j3-r1-1-1-a1-1 与 R-ROOM-spec-j3-r1-1-2-a1-1 裁定\"（reconcile.json 已经如此记账）。裁定文字本身我不提修改意见。"
 },
 {
  "seq": 2,
  "ts": "2026-11-13T02:36:31+0800",
  "room": "spec-j3-r2",
  "kind": "spec",
  "sender_role": "m2s-architect",
  "task": "ROOM-spec-j3-r2-1-1",
  "body": "【m2s-architect】同意关闭本室：两条差异不需要新裁定。verifier 的现状核对我这一轮独立重读账目后确认无误，并另记一条**确认型裁定 R-ROOM-spec-j3-r2-1-1-a1-1**（不新增、不扩大任何放宽，只把两处口径钉死）。\n\n一、我实际核了什么（不是复述上一楼）\n- rulings.json 现 6 条：R-ROOM-spec-j3-r1-1-1-a1-1 与 R-ROOM-spec-j3-r1-1-2-a1-1 均为 category=contract、relaxation=order、layers=[R]、status=decided、deviates_from_monolith=false（立即在效，无需人确认），affected_cases 已同时点名 search-case-insensitive-200#upper-q 与 search-hits-200#author。R-A1-a3-2/4 分别点名 GET /api/books 与 search-no-match 的列表步骤。\n- 对账账：differences=0、resolved_by_rulings=2，正是本室这两条，各自引用 ...1-1（decided）。我按步骤逐字看明细：total 均 2、命中 id 集合均 {book-001, book-002}、每条目 _source 键集合与取值逐字相同、包装结构相同，差别只在先后——满足\"仅顺序不同\"的前提，没有被折叠掉的内容差异。\n- 事实库原文核对：GET /api/search#responses[1]\"顺序为 scan 顺序，无排序、无截断\"、#business_rules[3]\"无相关性评分、无排序、无分页参数……条目属性不裁剪也不额外添加\"、#business_rules[1] 全表 scan 不处理 LastEvaluatedKey。反面例子 GET /api/bestsellers#business_rules[1] 的 salesCount 降序 + slice(0,20) 仍是契约。\n- 机制（只读裁判代码核实，不是推测）：relaxation=order 时该步骤模板从 steps.<name> 起递归标记所有列表路径，命中的就是 body.hits 这层，嵌套数组无序比较生效、条目内容与条目数仍逐字比较——**命中集合内容不登记为残余盲区**；裸用例条目与 \"case#step\" 同时存在时裸用例优先且与书写顺序无关。\n\n二、我纠正 verifier 一处、升格一处（这两点是本轮唯一的新增内容，已写进 R-...2-1-1 第 2/3 点）\n1) 纠正不变量：differences 与 resolved_by_rulings 是互斥两桶，当前 differences=0 是机制的构造结果、不是回归信号，\"两桶条目数不一致就上报\"这条不成立。有效的 tripwire 只有两条：(a) 每个 resolved 条目引用的裁定仍在效且 relaxation=order；(b) 该条目明细确属\"仅顺序不同\"（total 相等、长度相等、每条目键集合与取值逐字相等、包装结构相同）。\n2) 升格为强制口径：裁定记账按用例剥掉步骤名，所以这两个 search 用例**将来出现的内容级差异也会落到 resolved_by_rulings**，对账阶段的 differences 看不见它——这是裸用例条目的代价，不是回归。因此\"每轮逐条按步骤复核 resolved_by_rulings\"从纪律升格为强制：凡 (b) 不成立的，一律按未裁定差异处理、单独升级上报、必要时开新一轮对账，不得以\"已裁定\"关闭。V2 是第二道防线：只有 order 类裁定的步骤在剩余不匹配非空时判 **fail**（order 不参与判 ruled），内容差异最终一定失败。\n\n三、三条执行口径我签字确认（verifier 第三点，逐条与裁定文字比对过）\n- P 层预期保持原样，不改写为 length + 无序 contains：与 ...1-1 第 5 点一致，退化写法会把\"就是这两本书\"的集合检查丢掉，属过度屏蔽，我不批准；这两条每轮都会出现在 resolved_by_rulings，按上面第 2 点的方式读，不当回归信号。\n- V2 判据：只因先后不同而失败 = relaxation 机制未生效，记 tool_issues、不退实现线；任何内容级差异（少一条/多一条/属性被裁剪或新增/_source 包装变化/total 或长度不等）在 order 放宽下仍必须 fail 并照旧分诊。这条与我读到的判定分支一致。\n- 不外推：bestsellers 的应用层显式排序、条目内请求体原样快照数组、同分并列名次（GAP-6/SI-10）、第一页截断与翻页完整性（GAP-3/4/5/15/SI-11）各自独立裁定，谁援引本条放宽这些按超范围驳回——与本条结论第 5 点相同。\n\n四、给调度器：本室两条差异的分类请从 needs_ruling 更新为\"已由 R-ROOM-spec-j3-r1-1-1-a1-1、R-ROOM-spec-j3-r1-1-2-a1-1 裁定，并经 R-ROOM-spec-j3-r2-1-1-a1-1 确认\"，本室关闭，无需第三轮对账。既有两条裁定文字我不提修改意见；新增的只有\"记账不变量纠正\"和\"逐条按步骤复核为强制\"两处。"
 },
 {
  "seq": 3,
  "ts": "2026-11-13T02:44:38+0800",
  "room": "spec-j3-r2",
  "kind": "spec",
  "sender_role": "m2s-spec-annotator",
  "task": "",
  "body": "【m2s-spec-annotator】同意关闭本室：两条差异不需要新裁定，也不需要第三轮对账。下面第 1、2 点是我这一轮在单体源码里独立核到的（不是复述事实库），其中第 2 点把前二楼称为\"巧合\"的东西解开了；第 3、4 点是规格线自己的账。\n\n1) 裁定前提的源码核对（栏 1 / 栏 2 的分界在代码里是真的存在）\n- routes/search.js:26-46 只有三个动作：`await docClient.scan(params)`（第 30 行）→ `(data.Items || []).filter(...)`（第 33 行）→ `results.map(item => ({_source: item}))`（第 43-44 行）。我把整个 routes/ 目录 grep 了一遍 `.sort(`/`.slice(`，**命中只有 routes/bestsellers.js:25-26 一处**；books.js 两臂（query 臂 24-35、scan 臂 36-43 区间）与 cart/orders 都没有重排调用。所以 R-...1-2 第 1 点\"现况满足栏 1 的只有畅销榜\"我核过源码，成立，反面例子也成立。\n- filter 与 map 都保序，因此 hits 的先后完全等于 data.Items 的先后，中间没有任何重排点。这正是栏 2 的依据：代码里既没有排序键，也没有第二排序键，规格没有为先后承诺任何东西。\n\n2) 根因唯一性的硬证据（补前二楼缺的一环）\n- 从 J2 在单体上录到的实际响应里抽 id 先后：GET /api/books 的 scan 臂两步（books-list-all-200#all、books-list-by-category-200#empty-value）给出同一条顺序 **[book-002, book-999, book-101, book-102, book-001]**；本室两条差异的 [book-002, book-001]、以及通过的 #category / #substring-space 的 [book-101, book-102]、[book-002, book-001]，全都是这一条全局顺序的**子序列**。\n- 也就是说单体没有\"每个查询各自的顺序\"，只有一条被所有读路径共享的存储顺序；没有哪个 q 会触发隐藏重排，因此规格侧不存在需要额外修正的第二根因。这也给出 R-...1-1 依据栏那句的源码级解释：J2 三遍取值稳定，是因为被 seed 落盘的物理顺序对单体自己可复现；可复现的是单体，不是任何实现，所以它必须由裁定解决、不由屏蔽解决。\n- 顺带纠正本室主题里一句措辞（不影响结论，也不提裁定文字修改意见）：主题写\"顺序确实无法从输入推出\"偏严。准确说法是\"无法从规格/夹具文件顺序推出，但在单体内可推出（同一条全局 scan 序）\"。这句若照原样留在记录里，将来可能被反过来论证\"既然可复现就是契约\"，请按上面这个口径理解。\n\n3) 事实库这一轮为什么不该动（同意二楼，补一条我一直没写进规格的解读纪律）\n- 引用安全性：本室主题、reconcile.json 的两条明细、R-...1-1/...1-2 的 basis 全是按 `#business_rules[3]`、`#responses[1]` 这类**下标**引用事实的。我若为了把\"顺序\"从句从 shape 条款里拆出来而增删规则条目，下标会整体平移，这些引用就指到别的规则上。约束：任何后续规格补写只能\"在列表末尾追加 + 原地改某条自己的文字\"，不重排既有下标。\n- class 字段的口径：`GET /api/search#business_rules[3]` 与 `GET /api/books#business_rules[2]` 都是 class=contract，而\"无排序\"就写在里面。谁把 class=contract 读成\"本条每个从句都要复现\"，就会推出\"顺序是契约\"的相反结论。正确读法：contract 规则里被**肯定陈述**的部分（形状 {total,hits}、total=hits 长度、每项只包一个 _source、属性不裁剪不新增）是契约；被**否定陈述**的\"无排序/不处理 LastEvaluatedKey\"是在陈述代码里缺少那个步骤，它为栏 2 提供依据，本身不承诺次序。若调度器想让这一点将来可机械判定，我可以在两个端点的 business_rules **末尾各追加**一条 class=implementation-trace 的肯定式表述（\"响应列表的先后即存储返回的先后，handler 在 scan/query 之后没有任何重排调用\"，出处 search.js:33/43、books.js 两臂的 `res.json(data.Items)`），不动任何现有下标。这三处都不改变本轮任何一个被比较的值，所以我建议等下一次规格轮顺手做，本室期间不动事实库（本任务也明确不改工作区文件）。\n\n4) 规格线真正的待办：架构在 R-...1-2 第 7 点批准过两处补写，事实库至今没落地（这不是新问题，是欠账，请排一次规格标注轮）\n- a. `GET /api/books#responses[0]`（category-index 那一臂）的 body.description 现在只写\"书籍条目数组……只有第一页\"，完全没有顺序语义，而兄弟项 responses[1]（scan 臂）写了\"顺序即 scan 返回顺序，无排序\"。裁定的措辞是\"只按 category-index 键序返回第一页、无应用层排序\"。我核过依据：books.js 那一臂只有 `IndexName: 'category-index'` + `KeyConditionExpression: 'category = :category'`，建表脚本里该索引只有 HASH 键 category、sort_key 为 null —— 属\"键序不构成排序契约\"一类（R-...1-2 第 2 点）。原地改文字，引用安全。\n- b. `GET /api/bestsellers#business_rules[2]` 现在写\"同分条目的相对次序不保证稳定\"，这句偏保守且不准确：比较键只有一个（bestsellers.js:25 `(b.salesCount||0)-(a.salesCount||0)`），并列由输入顺序决定，而 V8 的 Array#sort 自 V8 7.0（Node 11+）起是稳定的，package.json:38-40 `engines: node >= 14.0.0` 满足；所以对同一份表内容单体的并列名次**可复现**。这正是裁定第 7 点要求改写成\"单体对同一表内容可复现，但对迁移后的实现不构成约束\"的原因。这一句直接连着 GAP-6 / SI-10，我改时会把\"可复现\"和\"不构成约束\"两句写进同一条，免得下一轮有人拿\"不保证稳定\"去论证畅销榜的整体次序也可以放宽——那是明确超范围的。\n- c. 另外自报一处我写坏的文本：`GET /api/search#business_rules[4]` 末尾\"不做其化的正规化\"是残句（原意：除 toLowerCase 外不做其他正规化，如 Unicode NFKC／变音符折叠；代码只有 search.js:32 的 toLowerCase）。纯文字原地修正，不改下标，进我的 issues，不需要裁定。\n\n5) 结论与给调度器的请求：本室按二楼第四条关闭；两条差异的分类更新为\"已由 R-ROOM-spec-j3-r1-1-1-a1-1、R-ROOM-spec-j3-r1-1-2-a1-1 裁定，并经 R-ROOM-spec-j3-r2-1-1-a1-1 确认\"。另请排一次规格标注轮落地第 4 点的 a/b 两处（架构已批准、至今未写）与第 4 点 c 的文本修正；约束照第 3 点——只追加、不重排下标，改完我跑 check_provenance 到 PASS。第 3 点提到的 implementation-trace 追加项请一并裁定要不要做（它不影响本轮任何比较，我倾向\"下一轮顺手做\"而不是为它单开一轮）。我对既有三条裁定的文字没有修改意见。"
 },
 {
  "seq": 4,
  "ts": "2026-11-13T02:53:13+0800",
  "room": "spec-j3-r2",
  "kind": "spec",
  "sender_role": "m2s-verifier",
  "task": "",
  "body": "【m2s-verifier】同意关闭本室，不新增裁定。本轮我实际重核了账目并做了前三楼都没做的一次全套件审计，报告四处需要落地纠正/确认，其中第 2 点是对刚升格的强制复核口径的唯一修改意见。\n\n1) 现状核对（我这一轮自己读的，不是复述）\n- rulings.json 现 7 条：R-ROOM-spec-j3-r1-1-1-a1-1、R-ROOM-spec-j3-r1-1-2-a1-1、R-A1-a3-2、R-A1-a3-4 为 category=contract + relaxation=order；R-ROOM-spec-j3-r2-1-1-a1-1（确认型）affects.cases 只点名本室两步，与\"不扩面\"一致，我核过。R-...2-1-1 的 basis 写\"现有 6 条\"是它自己插入前的计数，请按 7 条读，别当不一致去追。\n- reconcile.json：differences=0、resolved_by_rulings=2，正是本室两条，各自引用 R-...1-1（status=decided）。resolved 桶里出现这两条本身就是机制在效的实证（若 order 放宽未被识别，它们会在 differences）。\n\n2) 对 R-...2-1-1 第 2 点 (b) 的一处可执行化纠正（请 architect 认可或驳回）\n字面写法\"每个条目键集合与取值逐字相等\"在本套件永远不成立：J1 的 predicted 是 subset 模板，被裁定的两步只点名 id（#author 另点名 author），而 actual 每条带 6~7 个键，且夹具本身键集合不齐（book-102 无 cover、book-999 只有 id/price/cover）。照字面执行，我每轮都必须\"升级上报\"，强制复核会变成噪音。我采用的判据（(2b) 的可执行形式）：\n  (i) total 相等；(ii) 被放宽的列表长度相等；(iii) 存在 predicted↔actual 的双射，使每个 predicted 条目在**它自己点名的键**上与配对条目取值逐字相等；(iv) 包装结构相同（每项恰一个 _source）；(v) actual 条目之间的键集合差异只来自夹具，不来自响应。\n本轮按 (i)–(v) 逐条复核：total 均 2、长度均 2、{book-001, book-002} 一一对应、被点名的 id/author 取值逐字相同、包装相同 → 两条确属\"仅顺序不同\"。\n补一句防误读：裸用例条目把两个用例整体纳入 order，但内容覆盖并没有被削弱——同用例 #name 步（单命中，顺序放宽对它无意义）逐字点名 6 个属性，#author 步在无序配对下仍逐条验证两本书的 author。\n\n3) 全套件顺序暴露审计（本轮新增结论：**不需要再扩 affected_cases**）\n我枚举了整个裁判套件里位置敏感的列表断言（subset/equals 内长度 ≥2 的数组），一共只有 7 步：\n- search 3 步（#upper-q、#author、#category）——都已被既有 order 裁定点名，YES；\n- bestsellers 3 步（#list、#u1、#u2，用 equals 逐位置点名 4 元素 id 序）——**故意不被覆盖**，因为它们只依赖 GET /api/bestsellers#business_rules[1] 的 salesCount 降序。我核过夹具：bestsellers.json 的 salesCount = 30/20/10/缺失(按 0)，bestsellers-many.json = 25..1，无并列，所以这些断言不碰 GAP-6/SI-10 的并列名次，V2 不会因并列而误报。\n结论：当前套件对 V2 没有任何\"仅顺序不同但未覆盖\"的潜在误报步骤，affected_cases 这个集合不多不少，本室之后无需第三轮对账。\n两条前瞻护栏（我在 J4/V2 按此把关，超范围就驳回）：(a) 以后若有新增用例给畅销榜引入并列 salesCount，等于把并列名次变成断言，须另引 GAP-6 裁定；(b) cart/orders 列表步今天用 contains/not_contains（成员语义、顺序无关），谁把它们改写成 equals 数组就引入了新的顺序暴露，不得援引本条。\n\n4) 对规格标注轮（annotator 第 4 点）的验证线意见\n- \"只追加、原地改文字、不重排下标\"对验证线是**硬要求**而不是偏好：233 个步骤全靠 `#business_rules[k]` 下标引事实，重排会让引用指到别的规则，引用检查要么报错要么静默错引（静默错引更危险）。我扫过现有引用以确认可行性：`GET /api/bestsellers#business_rules[2]` 目前没有任何用例引用 → b) 的并列名次改写对验证资产零影响；`GET /api/search#business_rules[4]` 只被 search-case-insensitive-200 引用，它引的是\"大小写不敏感匹配\"这一行为，残句修正不改变被比较的值 → predicted 不动；a) 的 books responses[0] 补写也不需要改用例，books 的列表步已由 R-A1-a3-2/4 点名。\n- implementation-trace 追加项：同意\"下一轮顺手做\"，但它对验证线有实际用途（不只是文档），请排进下一次规格轮并**追加到两个端点 business_rules 末尾**（search 现有 [0]–[6] → 追加为 [7]），不要中间插入。有了肯定式的\"scan/query 之后无任何重排调用\"条款，将来别的接口出现同类顺序差异时 J3 可以更快定性，测试资产线也能据此给出 not_applicable 的引用。\n\n5) 一处与顺序无关的残余盲区，登记给下一个裁判资产轮（不需要裁定，本室期间我不动文件）\n#business_rules[3]\"条目属性不裁剪也不额外添加\"与 #responses[1]\"hits 每项只有一个 _source 字段\"今天**没有任何断言能抓住\"新增\"**：所有 search 步都是 subset，目标若给 _source 多加字段、或给 hit 加 `_score` 兄弟键，都看不见。事实库支持的可判定修法：把 search-hits-200#name（q=hobbit，单命中）改为 equals：{\"total\":1,\"hits\":[{\"_source\": book-001 的全部 7 个属性}]}——单元素列表不引入顺序暴露，同时把裁剪与新增两头都钉住。我会在 J1 修订或 J4 追加时做，并记入工作报告。\n\n6) 给调度器：本室按二楼第四条关闭；两条差异的记账按 R-...2-1-1 第 6 点更新。一点提醒：J3 的分类枚举只有 fact_error / needs_ruling / case_error / mask_gap 四类（这是 check_reconcile_classes 的合法值），**不存在\"已裁定\"这一类**，而这两条现在已经住在 resolved_by_rulings 桶里、不再出现在 differences，所以下一轮 J3 不会再被要求为它们分类——请照此关闭，不要为了一个分类字段去动裁判工具的枚举。"
 }
]

## 完成方式
1. 写工作报告 WS/storage/stage_reports/ROOM-spec-j3-r2-0-2.json（格式按你的角色说明）。
2. 调用 mcp__m2s-server__submit_task(summary=...) 提交，然后结束会话。不需要再输出 STAGE_COMPLETE。
3. 做不下去时，调用 mcp__m2s-server__report_blocked(reason, evidence, suspected_module)，然后结束会话；认为是更早阶段的问题时，在 suspected_module 写那个阶段（例如 S2），并在 evidence 里给出证据。
4. 调度器会自己运行这个任务的检查（room_turn），你的自述不作为判定依据。

## 注意
- 如果调用工具或等待过程中遇到网络错误（例如 'Network error: Connection error'），重试 1-2 次再继续，不要因为一次网络错误就直接放弃或提前结束。
- 只改这个任务范围内的文件：storage/stage_reports/ROOM-spec-j3-r2-0-2.json、storage/analysis_report.json。范围之外的改动会被撤销，这次尝试判为失败。
- 不要修改流水线自己的工具：m2s-mcp-server/、.openharness/、~/.openharness/ 下的文件。
- 不要读写 WS/coord/（调度器的内部状态）。不要调用 mcp_auth。
- 不要读别的工作区（WS 所在 runs/ 目录下的其他目录、runs-prev-*），也不要参照它们的产物。
- 这份说明可以随时用 mcp__m2s-server__get_task_context 重新取回。