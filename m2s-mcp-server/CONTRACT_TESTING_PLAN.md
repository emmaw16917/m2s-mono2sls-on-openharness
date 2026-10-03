# 改动计划：测试只断言"和单体效果一样"（契约测试）

日期：2026-10-02　状态：**待确认**

## 0. 目标与边界

**目标**：从生成 v0 的第一轮测试起，验证线只断言外部能观察到的效果（外部契约），不断言单体的实现轨迹，也就是不规定读了几次、按什么顺序调用、故障落在第几次调用上。这样：

- code-developer 可以写出自然的 serverless 代码，不必逐行照抄单体的分层结构；
- S5 的优化不会再因为"spec 规定了步骤"而被卡住（V2-01、V2-03 这类情况）。

**不变的部分**：
- 写操作、发出的消息、最终表状态、单条消息的失败粒度，仍然都要断言，因为这些就是外部效果；
- 故障场景仍然要测；
- 覆盖率要求不变；
- H1（a、b、c 三项）不变，继续负责保证"和 v0 一样"。

**生效范围**：只对新跑的应用生效。用 blueprint 里的 `metadata.verification_contract: 2` 作为开关；现有 5 个工作区没有这个字段，按旧规则校验，它们的用例不动。

**判断标准**：一条断言或故障规则，如果在"代码少读一次 / 换一种顺序 / 把两步合成一步，但外部效果完全一样"的情况下会失败，它就是实现轨迹，不允许出现。

## 1. 规格层（S0 spec-annotator、S1 architect）

| # | 改动 | 文件 |
|---|---|---|
| 1.1 | `business_rules` 的每一条新增字段 `class`，取值为 `contract`（外部效果）或 `implementation-trace`（单体内部的做法，例如"service 再查一次"）。`responses` 里只由实现轨迹触发的那一臂（例如"第二次读不到 → 400"），在 `when` 里标注 `trace_arm: true` | `agents/m2s-spec-annotator.md`，`provenance.py`（校验 `class` 字段存在且取值合法） |
| 1.2 | architect 复核 spec-annotator 的分类，有异议的写成 SI 裁定。对每个 `trace_arm`，architect 要么作废这一臂，要么给出允许的结果集合（例如 `{400 ValueError, 200 正常完成}`），并在 SI 里写 `class: implementation-trace` | `agents/m2s-architect.md` |
| 1.3 | blueprint 写入 `metadata.verification_contract: 2` | `agents/m2s-architect.md`，`gates.py`（新应用的 gate(blueprint) 要求有这个字段） |
| 1.4 | 设计文档：§2.1 和 §3.2 改写（测试计划不再写"预期的 SDK 调用序列"）；新增"契约测试"一节，写明 §0 的判断标准 | `PIPELINE_REDESIGN.md` |

## 2. 测试资产格式（verify.py，只在 contract 2 下生效）

| # | 规则 | 实现位置 |
|---|---|---|
| 2.1 | **`expect.sdk_calls` 只能出现写操作和消息**：`includes`/`excludes` 里只允许 PutItem、UpdateItem、DeleteItem、BatchWriteItem、TransactWriteItems、SendMessage(Batch)；读操作写进去直接拒收。`total` 不再允许使用（总调用数就是轨迹）。`excludes` 仍允许出现读操作，例如"403 时不得读 Payment 表"，这属于外部契约（越权访问） | `_check_expect` |
| 2.2 | 新增 `expect.trace`：内容随意，只记录、不判定，供人工阅读和 S4 参考 | `_check_expect`、`judge` |
| 2.3 | **故障规则不能按位置钉**：`nth` 不是 `'all'` 时，`match` 里必须带 `item`。`nth` 的含义变成"这个条目上的第 N 次操作"。不带 `item` 的 `nth: k` 直接拒收 | `_check_rules` |
| 2.4 | 新增 `expect.status_one_of`，以及 `expect.alternatives: [ {...}, {...} ]`：结果满足其中任意一组完整预期就算通过。只能在引用了 SI 裁定的用例里使用（`spec_refs` 里要有 `spec_resolutions[SI-x]`） | `_check_expect`、`judge` |
| 2.5 | **`not_applicable` 判定**：故障规则一次都没有被触发，并且用例的 `spec_refs` 引用了一条 `class: implementation-trace` 的裁定时，判为 `not_applicable`，不算失败，单独列在报告里。没有这样的裁定时，仍然判失败（防止 code-developer 删掉步骤来绕开故障测试） | `judge`、`run_validate` 的汇总 |
| 2.6 | 测试夹具支持相对时间：复用 `perf._fill`，让 `fixtures` 里可以写 `{now_ms // 300000 + i}` 这类表达式。这一条不受开关限制，对所有应用都生效，同时修好 coffee 的 7 条失败 | `_prepare_runs` 播种之前 |
| 2.7 | 单元测试：上面每条规则各有一个"接受"用例和一个"拒收"用例；旧格式（没有开关）的回归测试保持通过 | `tests/test_verify.py` |

## 3. agent 规则

| # | 改动 | 文件 |
|---|---|---|
| 3.1 | verifier 出题规则：按 §0 的判断标准出题；白盒用例可以为了覆盖率去走特定分支，但断言只能写外部效果；故障一律按条目钉；遇到 `trace_arm` 时，按 SI 裁定写 `alternatives` 或者不出题 | `agents/m2s-verifier.md` |
| 3.2 | code-developer：spec 里标为 `implementation-trace` 的条目只作参考，不必照抄；`contract` 条目必须满足 | `agents/m2s-code-developer.md` |
| 3.3 | validator（S4）：静态复核只对照 `contract` 条目，`implementation-trace` 条目不算偏差 | `agents/m2s-validator.md` |
| 3.4 | perf-optimizer：§6.2 加一行，删掉 `implementation-trace` 条目对应的冗余读属于允许的改动（仍然要过 H1 和判决）；修改请求新增 `spec_relaxation` 字段，用于在旧应用上申请放宽 | `agents/m2s-perf-optimizer.md`，`PERF_OPTIMIZATION_DESIGN.md` §6.2、§9.4、§12 |

## 4. 已有工作区的处理（这次不批量迁移）

- 5 个工作区维持旧规则，只修 2.6 带出来的 coffee 夹具：把这 7 条（以及其他写了绝对时间戳的用例）的夹具改成相对时间，然后重跑 validate。这属于测试资产修正，不重新打 v0。
- V2-01、V2-03 留在 backlog 里，标注"等这些应用迁移到 contract 2 时再做"。

## 5. 验证这套方案

1. 单元测试全部通过（2.7），旧工作区的 `run_validate(dry_run)` 全部 PASS（证明开关生效、旧格式不受影响）。
2. 新跑一个应用，走完 contract 2 的完整流水线，验收标准：
   - V1 产出里没有读操作断言，也没有按位置钉的故障规则；
   - gate、validate、S5 全部通过；
   - 统计 `not_applicable` 和 `alternatives` 的数量，并逐条人工抽查。

   候选应用：todo-oh（体量最小）。也可以重跑 airline（最好验证，V2-01 应该能直接落地）。**请你选一个。**

## 6. 执行顺序和提交

| 步 | 内容 | 提交 |
|---|---|---|
| 1 | 设计文档：1.4、3.4 的文档部分 | 1 个 commit |
| 2 | verify.py：2.1–2.6 加单元测试 | 1 个 commit |
| 3 | provenance.py / gates.py：1.1、1.3 | 1 个 commit |
| 4 | 5 份 agent 定义：1.1–1.3、3.1–3.4 | 1 个 commit |
| 5 | coffee 夹具修复并重跑 validate | 工作区内 commit |
| 6 | §7 的申请流程：文档、`perf.py`/`gates.py` 的状态与校验、perf-optimizer / architect / verifier / m2s-migrate 编排的规则 | 1 个 commit |
| 7 | 用 airline 的 V2-01 走一遍 §7 的流程（旧应用，contract 1） | 工作区内 commit + backlog 更新 |
| 8 | 新应用试跑（§5 第 2 项） | 等你选好应用后再开始 |

第 1–6 步不涉及外部服务，可以连续做完；第 7、8 步要派发 agent、跑流水线，开始前我会先告诉你。

## 7. S5 → S1 的 spec 放宽申请流程

**用途**：兜底。新应用（contract 2）里，实现轨迹应该在 S0/S1 就已经标出来，这条流程很少会用到；它主要服务于旧应用（contract 1），以及新应用里 spec-annotator 漏标的条目。

**原则**：
- perf-optimizer 只能**申请**，不能自己改 spec；
- 主 Agent 只负责**派发**，不做裁定（沿用 K9）；
- architect 是唯一能**裁定**的角色。

### 7.1 流程

| 步 | 谁 | 做什么 | 产出 |
|---|---|---|---|
| 1 申请 | perf-optimizer | 修改请求里写 `spec_relaxation`（格式见 7.2）；这条请求的 `verdict` 记为 `pending_spec`，**本轮不改代码** | `perf/requests/REQ-x.json`，`perf_report.json` 新增 `pending_spec` 列表 |
| 2 派发 | 主 Agent | S5 结束后收集所有 `pending_spec` 请求，派给 architect 的时候只传 spec 语言：条目、理由、允许的结果、收益数据。不传代码 diff，也不传用例（沿用 K10） | — |
| 3 裁定 | architect | 逐条决定接受或驳回。接受的，写一条 SI：`class: implementation-trace`，`origin: perf-request REQ-x`，附上被作废的那一臂和允许的结果集合；驳回的，写明理由 | `blueprint.json` 的 `metadata.spec_resolutions` |
| 4 测试跟进 | verifier（V1 回炉） | 只改引用了被裁定条目的用例：按位置钉的故障改成按条目钉；被作废的那一臂撤下，或改写成 `alternatives`；新用例的 `spec_refs` 指向新的 SI；然后跑 `run_validate(dry_run)` | `verification/` 目录下的改动 |
| 5 重新验收 | 主 Agent | 用新用例在 v0 的代码上跑全量 `run_validate(ref="v0")`，通过后提交测试资产。**v0 的 tag 不动**（2026-10-02 airline 首例中曾执行 `tag -f v0`，把 S5 已保留的改动带进了 v0，已改正）。没通过就进入失败分诊（§3.4 / K15–K18） | 测试资产的提交 |
| 6 重新判决 | perf-optimizer | 只处理被接受的请求，按普通请求的完整流程判决（等价性、验证、覆盖率、RIE），结果是 kept 或 reverted | 正常的 commit 和 verdict 文件 |
| 7 记账 | 主 Agent | 最终报告里单列"本次 spec 放宽"：每条 SI、对应请求、判决结果；ledger 里记录申请数、接受数、保留数 | 最终报告，`ledger.json` |

### 7.2 `spec_relaxation` 的格式（加进 §9.4）

```json
"spec_relaxation": {
  "targets": ["POST /bookings/<booking_id>/cancel#responses[6]", "...#business_rules[0]"],
  "why_trace": "该臂只在第二次读同一条 booking 失败时出现，是单体装饰器加 service 两层各读一次的副作用，不是业务需求",
  "allowed_outcomes": "授权读成功之后，后续读失败或条目消失时：{400 ValueError，或按正常取消完成}；Flight 和 Payment 要么都不变，要么按正常取消执行",
  "evidence": {"calls": "7 -> 5", "depth": "7 -> 5", "verdict_file": "..."},
  "blocking_tests": ["wb-bookings-confirm-second-read-fails-400"]
}
```

`blocking_tests` 只给主 Agent 看，派给 architect 的时候要去掉（K10）。

### 7.3 限制

- 每个应用每轮 S5 最多一个申请批次：architect 驳回的，本轮不能再申请；要申请就进下一轮 S5。这样 S5 和 S1 之间不会无限往返。
- 申请的前提是收益可测：perf-optimizer 必须先用 `run_proxy_metrics` 在一个临时改动上量出收益（不提交，量完撤回），没有收益数据的不能申请。
- 只能放宽 `implementation-trace` 性质的条目。涉及写入的条目、表、消息内容、权限判定，以及单条消息失败粒度的条目，一律不能申请，perf-optimizer 应该写进 advisories。
- 最终报告里所有因申请产生的 SI，都列进"需人复核"。

### 7.4 工具和文档改动

| 文件 | 改动 |
|---|---|
| `PERF_OPTIMIZATION_DESIGN.md` | §6.2 加一行"spec 放宽申请"；§9.4 加 `spec_relaxation`；§12 把"修改请求派发"的一部分标为已落地 |
| `PIPELINE_REDESIGN.md` | §4.1 回炉规则加上 S5 → S1 → V1 → v0 → S5 这条路径及轮数限制 |
| `perf.py` | `update_ledger` 认识 `pending_spec`，并统计 spec 的申请数、接受数、保留数 |
| `gates.py` | gate(final) 检查：已接受的 SI 必须有对应的重判记录（kept 或 reverted），`pending_spec` 不能遗留到交付 |
| `agents/m2s-perf-optimizer.md` | 申请的条件、格式、限制 |
| `agents/m2s-architect.md` | 怎样裁定 perf 申请；SI 里要写 `origin` 和 `class` |
| `agents/m2s-verifier.md` | 针对 SI 的定向回炉（只改受影响的用例） |
| `.openharness/skills/m2s-migrate/SKILL.md` | 编排：S5 之后，如果有 `pending_spec`，就走 7.1 的第 2–6 步 |

### 7.6 扩展：新增 GSI 的申请（2026-10-03，见 §8）

### 7.5 第一个案例：airline 的 V2-01

在 contract 1 的 airline 工作区上手动走一遍 7.1：
- perf-optimizer 申请放宽 cancel 和 confirm 的 responses[6] 里"第二次读失败"那一臂；
- architect 裁定；
- V1 改 `wb-bookings-confirm-second-read-fails-400` 等用例，以及 5 条按位置钉的故障规则；
- 重打 v0；
- 重新判决 V2-01。

预期结果：调用 7→5、深度 7→5，按 10ms 延迟模型约省 20ms。不管裁定接受还是驳回，都把结果写回 `v2_requests.json`。

## 8. 扩展：新增 GSI（数据模型改动）的申请（2026-10-03）

§7 的流程只允许 perf-optimizer 改代码。读路径能用**现成** GSI 时（airline 的两个函数）够用；要**新建** GSI 时，还要改模板，有时还要给已有数据补一个属性。扩展如下：

| 步 | 谁 | 做什么 |
|---|---|---|
| 1 申请 | perf-optimizer | 请求在 `spec_relaxation` 之外带 `data_model_change`（格式见下）。收益在临时改动上量：临时改模板加 GSI、临时改读代码，`run_proxy_metrics` 量完全部撤回 |
| 2 裁定 | architect | 接受时：SI 里写 `data_model_change`（gate CT5 检查格式），并用 `merge_blueprint_key` 把 GSI 加进 blueprint 的 `dynamodb_tables`。优先用表里**已有**的属性做索引键；必须新增属性时，写清它的取值、由哪些函数写、已有数据怎么回填 |
| 3 改模板 | sam-engineer（STAGE_ID=SG） | 只把 blueprint 里这一个 GSI 写进 `output/template.yaml`（AttributeDefinitions + GlobalSecondaryIndexes），别的不动；`run_gate(phase="template")` + `validate_sam` |
| 4 改用例 | verifier（VS） | 同 §7；另外，如果有新属性，在相关用例的夹具里给已有行补上这个属性（模拟回填后的数据）。不新增对新属性本身的断言 |
| 5 重新验收 | 主 Agent | 同 §7：`run_validate(ref="v0")` 全量，提交测试资产，v0 tag 不动 |
| 6 重新判决 | perf-optimizer | 改读代码（Query 新 GSI），有新属性时也改写入代码；照常走等价性、验证、覆盖率、RIE |

**等价性怎么判**：已接受的 SI 里 `data_model_change.new_attributes` 声明的属性，`run_equivalence` 比较时会忽略（写入的 Item、UpdateExpression 里给它赋值的 SET 子句、表的最终状态），其余写入照常严格比较。这样"多写一个索引键"不会被判成不等价，但任何别的写入差异仍然会。

**部署注意**：新属性的回填（`backfill`）不在本地验证范围内，交付报告里要单列，部署前执行。新 GSI 本身由 DynamoDB 自动回填已有数据，但只覆盖带有索引键属性的行。

`data_model_change` 格式：

```json
"data_model_change": {
  "table": "<blueprint 表的 logical_name>",
  "index": {"name": "<GSI 名>", "keys": [{"attribute": "isNew", "type": "HASH"}, {"attribute": "createdDate", "type": "RANGE"}],
            "projection": "ALL | KEYS_ONLY | INCLUDE:<a,b>"},
  "new_attributes": [{"name": "gsiPk", "value": "常量 'ALL' / 由哪个字段推出", "writers": ["写这张表的函数"],
                      "backfill": "已有数据怎么补：例如部署前对每行 SET gsiPk='ALL'"}],
  "readers": ["改为 Query 这个 GSI 的函数"]
}
```
