---
name: m2s-perf-optimizer-kb
description: v3 优化线 O2 的判决框架——B0P 基线、允许/不允许的性能改动、四项正确性检查、RIE/AWS 判决选择、perf_report.json 与 O2.json 的输出、blocked/pending 处理和测量窗口纪律；也说明 O1/P → B0P 的两基线流程。m2s-perf-optimizer 开始工作前加载；完整设计见 references/PERF_OPTIMIZATION_DESIGN.md。
user-invocable: true
disable-model-invocation: false
---

# v3 优化线（O）判决框架

这份文档是 **O 的角色说明的配套速查**，不是新的 gate。判定永远以 MCP 工具的结果为准：`run_judge`、`run_validate`、`run_equivalence`、`run_coverage` 决定行为有没有被破坏，`run_rie_verdict` / `run_aws_verdict` 决定有没有变快。O 不自己声明"有效"或"等价"。

工作区里必须有 tag `B0P`（B0 + O1 已接受的架构级改动；旧工作区用 `B0`）。O2 只做语义保持的改动；证据不足就回滚，改动的数量不是目标。

## 1. 允许和不允许（速查）

| 改动 | 是否允许 |
|---|---|
| 函数内把独立的调用改成并行（`allSettled` 或等价写法，失败粒度仍是单条） | ✅ |
| 去掉不必要的编解码或重复计算 | ✅ |
| 删掉声明了但没被引用的依赖 | ✅ |
| 只把后面的**读**提前并行（R1b），中间的提前返回是 `error_propagation` | ✅ |
| 按条目键分波次并行（R4），同一波条目两两不同、每个条目的顺序不变 | ✅ |
| 只改模板 `Globals.Function.Architectures`（R5，arm64） | ✅ |
| 模块级 TTL 缓存（R8），只针对 `bounded_staleness` 裁定的表 | ✅ |
| 事务/条件写入改成并行（可能改到同一条目） | ❌，只能按 R4 做 |
| 遇到失败就停止 → 改成并行（提前的操作里有写或发消息） | ❌ |
| 越过 `authz` 守卫提前读 | ❌ |
| 跨消息按 key 聚合写入；`BatchWriteItem` 合并；`UpdateItem` 批量写 | ❌ |
| 新增 GSI、改数据模型、换异步机制、换 API 类型 | ❌，只写 advisory |
| 改 MemorySize / SQS 参数 / Handler / CodeUri / 事件定义 / 目录结构 | ❌ |
| 冗余读被 spec 或测试规定成必经步骤 | ❌，记 `pending_spec`，交给人 |

不允许的改动写进 `perf_report.json` 的 `advisories`，附诊断数据；不要改。

## 2. 四项正确性检查

受影响的函数（改了代码的函数）每次都要跑完四项，不能第一项失败就停（分诊需要全部结果）：

1. `run_judge(functions=[...])` → `PASS`。裁判的期望是单体的实际行为。
2. `run_validate(functions=[...], assets="verification/delta")` → `PASS`；没有增量测试时 `total: 0` 算通过。
3. `run_equivalence(mode="diff", functions=[...], assets="verification/delta")` → `EQUIVALENT`；没有增量测试时 `NOTHING_TO_COMPARE` 算通过，由第 1 项负责。R1b 加 `speculative_reads=[...]`。
4. `run_coverage(functions=[...], changed_since="HEAD", with_judge=true, assets="verification/delta")` → `all_covered` 为 true。

AWS 模式（默认）下，第 1、2、4 项加 `mode="local"`（不需要 Docker、不花钱）；第 3 项仍是 `mode="diff"`。`M2S_AWS=0` 时用默认模式。另外：事件必须真的走到预期代码路径；RSS 接近 MemorySize 时判有风险。

## 3. 分诊

| run_judge | run_validate(delta) | run_equivalence(delta) | run_coverage(delta) | 分类 | 处理 |
|---|---|---|---|---|---|
| FAIL | — | — | — | 代码问题（裁判 = 单体行为，不存在"测试写错"） | 修一次，否则回滚 |
| PASS | FAIL | `DIFFERENT` | 任意 | 代码问题 | 修一次，否则回滚 |
| PASS | FAIL | `EQUIVALENT` 且基线稳定 | 任意 | 测试问题 | `blocked_by_test` |
| PASS | FAIL | 失败用例在 `unstable_in_baseline` | 任意 | 测试问题（不稳定） | `blocked_by_test` |
| PASS | PASS | `EQUIVALENT` / `NOTHING_TO_COMPARE` | 有未覆盖改动行 | 缺测试 | `blocked_by_coverage` |
| — | — | `INFRA_ERROR` | — | 工具问题 | 重跑一次，仍出错回滚并写 `infra` |

- 代码问题最多修一次；修改目标是恢复和基线一致的行为，不是迎合用例。
- `blocked_by_test` / `blocked_by_coverage`：存补丁到 `perf/patches/`、`git checkout -- output` 回滚、把证据写进 `changes[].review`、继续下一个。没有复核或重新判决；这些改动留在报告里交给人。
- `pending_spec`：不改代码，记 `spec_relaxation.targets` 和临时改动上量到的收益；不修改 spec/测试，不申请 GSI。
- `DIFFERENT` 时不许自己改判；可判 `pending_human` 并在 `issues` 里写明分析。

## 4. 性能判决：RIE 默认，AWS 可选

代理指标（调用次数、顺序深度、warm CPU、容量单位、成本）只用来定位和排序；最终判决由工具给。

- **默认**：`run_rie_verdict(benchmark=..., guard_benchmarks=[...])`。工具按固定规则处理：10ms 先筛；权衡型在 0/5/15ms 下测；5/10/15ms 全部明显变好且热 CPU ≤ 2× 基线 → 延迟维度改善（0ms 只参考，`realistic_latency_rule`）；三维度都不变差且至少一个明显变好 → `kept`；一好一坏 → `pending_human`；RIE 无差异但代理指标更好且 RIE 没变差 → `kept_inconclusive`；其他 → `revert`。R4 的热点守卫基准任何维度变差或出现冲突 → `revert`；候选比基线多出冲突 → 直接 `revert`。
- **没有 Docker**：`NO_DOCKER` → 只看 `run_proxy_metrics`，改动标 `kept_inconclusive`，`confidence: low`；R3 跳过。
- **默认（AWS 模式）**：`run_aws_verdict(benchmark=..., baseline="HEAD", ...)`。主维度是 Lambda 热 Duration 和每请求边际成本（不含 API Gateway）；端到端 p50/p95、冷启动是 guard；负载没走到改动函数 → `INCONCLUSIVE`；超预算 → `pending_human`；含 API Gateway 的总成本只进报告。只有 `kept` / `kept_inconclusive` 才提交；`pending_human` / `INCONCLUSIVE` 写进报告交给人。`M2S_AWS=0` 时退回 RIE/代理。

**测量窗口纪律**（AWS）：每个 pass 等到下一个整分钟（`minute_gap`）；Lambda REPORT 按 RequestId 去重、以 `until=下一个 pass 开始` 截断；DynamoDB 统计 GSI；SQS 按"发送 + 2×接收"估算；Duration 只算热调用；负载结束后统一等 90 秒再读。

## 5. 输出

`output/perf_report.json`（完整 schema 见 design §8.3）：

```json
{
  "confidence": "normal | low",
  "judged_with": "aws（默认；M2S_AWS=0 时没有）",
  "baseline": {"<function>": {"calls": 0, "depth": 0, "warm_cpu_ms": 0, "rie_warm_ms": 0, "init_ms": 0, "cost_usd": 0}},
  "changes": [{"id": "C1", "request": "REQ-1", "source": "rule:R1 | analysis",
               "based_on": [], "verdict": "kept | reverted | pending_human | kept_inconclusive | blocked_by_test | blocked_by_coverage | pending_spec",
               "fix_attempts": 0, "review": {}, "spec_relaxation": {},
               "evidence": {"proxies": {}, "rie": {}, "judge": {}, "equivalence": {}, "coverage_of_changed_lines": {}, "aws": {}},
               "commit": "<sha>"}],
  "pending_human": [], "advisories": [], "uncovered_paths": [],
  "rule_scan": {"R1": {"hits": 0, "locations": []}, "R4": {"hits": 0, "locations": [], "not_applicable": []}}
}
```

- 每个保留的改动单独 `git commit`（从 B0P 起算，`s5_report` 会核对提交数）。
- AWS 模式（默认）下，每个本次判过的保留改动都要有 `evidence.aws.verdict` 为 kept/kept_inconclusive，顶层写 `"judged_with": "aws"`。`M2S_AWS=0` 时不需要。
- `storage/stage_reports/O2.json`：格式同其他阶段，写明 kept / reverted / pending_human / blocked_by_test / blocked_by_coverage / pending_spec 数量；`blocked` 列表给调度器。
- 收尾：全量 `run_judge()` + `run_validate(assets="verification/delta")` 都 PASS；`update_ledger()`；提交报告。失败则回退 O2 的全部提交（`git checkout B0P -- output` 后提交）并如实报告。
- 只写直接测得的量，不写"预计线上提升 X ms / X%"。

## 6. O1/P 与两条基线

```text
B0 → O1/P 计划 → owner 写最终候选 → O1M 测量 → accept/revert
   → V2P（全量 judge）→ V3P（全量 delta）→ tag B0P → O2 → C1
```

- O1 只写 `perf/benchmarks/**`、`perf/candidates.json`、`perf/triage.json` 和 `storage/stage_reports/O1.json`；owner（A1/D1/I1/I2）写最终候选并提交 `perf(cand-<id>): ...`；O1M 只测一个候选并写 `perf/verdicts/<id>.json`。
- B0P 是 O2 的基线；没有候选时 B0P = B0。
- C1 失败时先从最新 O2 commit 开始逐个回退并重跑全量检查，尽量保留前面的 O2 改动；找不到通过状态才整体回到 B0P，绝不回退 O1 已纳入 B0P 的改动。
- 详细合同见设计文档 §0.3、§3.5、§8.7。

## 7. 完整设计

- `references/PERF_OPTIMIZATION_DESIGN.md`：v2 O 的完整设计（同样是仓库里的 `m2s-mcp-server/PERF_OPTIMIZATION_DESIGN.md`）。
- 旧优化线的归档（模拟延迟模型与六项局限、冲突模拟、v1 默认配置、消融实验、附录实测记录）：`m2s-mcp-server/PERF_OPTIMIZATION_DESIGN_v1.md`。
- 角色说明：`m2s-mcp-server/agents/m2s-perf-optimizer.md`（写法示例、逐轮执行、工具参数）。
