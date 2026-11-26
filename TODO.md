# TODO（v3 流水线）

## 2026-11-24

- [x] F2 并行标注：共享 `analysis_report.json` + 跨进程文件锁
  - 现状：`v2-split.yaml` 的 F2 子任务 scope 都包含 `storage/analysis_report.json`，dispatcher 的 `scopes_conflict()` 因此把它们串行化。实测 JSON I/O 很便宜：289 KB 的报告，`annotate_entry_point` 约 45–51 ms/次；airline-m4 的 F2-bookings 第一次尝试 50.4 分钟里只有约 3 秒花在报告读写上。串行成本主要是 LLM 时间，不是文件时间。
  - 目标：允许 `F2-<pkg>` 并行跑，只让“读-改-写”落在同一个临界区里。
  - 实现：
    - [x] 在 `provenance.annotate_entry_point` / `annotate_global_middleware` / `annotate_shared_entry` 的 `_load → validate → _save` 全程加跨进程锁。锁文件 `WS/storage/.analysis_report.lock`（O_EXCL 创建；pid 死亡或 TTL 过期可回收；等待超时抛 `SpecError`，做法参照 `coord/locks.py`）。
    - [x] `provenance._save` 改用 PID 唯一的 tmp 文件名 + `os.replace`（Windows 上对读打开的替换失败会短暂重试）。
    - [x] 在模板/调度器里给 F2 声明 `shared_write: [storage/analysis_report.json]`，`scopes_conflict()` 只在“双方都声明同一路径”时放行；`write_scope` 检查保留。
    - [x] `F2-shared` 仍在所有包任务之后运行（`shared_after_children: true`，展开时 `shared_write` 也复制给子任务）。
    - [x] 测试：`tests/test_provenance_lock.py`（两个真实进程并发 annotate 无 lost update；锁超时/异常释放；死进程/过期锁回收；空锁不被抢占）；`tests/test_coord_locks.py`（`shared_write` 放行调度、单方声明仍串行、锁/临时文件不算 write_scope 越界）；`tests/test_coord_f2_parallel.py`（F2-only 集成：真实 v2-split 的 F2 定义 + 两个真实 annotate 进程同时跑，真实 `check_provenance_pkg` / 全局 `check_provenance` 均 PASS）；并发标注后两项检查仍 PASS。
  - 完成（2026-11-24）：`coord/template.py`、`coord/dispatcher.py`、`coord/templates/v2-split.yaml`、`provenance.py`、`coord/prompts.py`、`agents/m2s-spec-annotator.md`、`PIPELINE_V3_PLAN.md`。按包拆分的 F2 子任务现在用 `check_provenance(endpoint=...)` 自查本包端点（整份由 F2-shared 收口）。
  - 真 LLM 实跑（2026-11-24，`coffee-f2-par-1`，workspace 在 `Mono2Sls-artifacts/M2S/runs/coffee-f2-par-1`）：`v2-split --max-workers 3 --stop-after F2`，coffee 10 端点展开为 4 个包 + shared。F2-identity(14.6min)/config-myorders(23.5)/order-journey-orders(54.4) 三个 LLM worker 16:13:56–59 同时启动，qr-code 在 identity 关闭后接手槽位（24.8min）；全部 `check_provenance_pkg` PASS，F2-shared 的全局 `check_provenance` PASS（0 errors / 0 incomplete），运行以 `stopped`（退出码 5）结束。F2 墙钟 54.6 min，串行约 117 min（约 2.1×）；4 次关闭 facts v1→v4，无锁/临时文件残留。token 合计 12.35M 入 / 142k 出。观察：coffee 的 global_middleware 已由脚本填好且没有端点使用 `ref:`，F2-shared 走 recheck 直接关闭，未写 `shared_rules`——这是既有语义（全局检查不要求 shared_code 条目），不是本次改动引入。
  - 前置：先确认 F2 是当前运行的关键路径。airline-m4 的 F2 合计 116.8 min，但 I1 120.7、A1 114.7、O 84.0、J4 67.2；若 F2 不是主瓶颈，保持现状。
  - 备选：单 LLM worker 只适合 ≤10 个端点的小应用；跨机/多进程再考虑“每包 fragment + 确定性合并”，不要直接合并整份 report。

## 2026-11-25

- [x] 拆分 `m2s-verifier` 角色：v1 退役后把 v1 内容移出 v2 的 system prompt
  - 背景：`coord/workers.py::role_prompt()` 只按 role 读整个文件，不按 mode 过滤；J1/J3/J4/J5/D1/V3 的 system prompt 里仍含 V1/V2/VR/VS 和 V1/V2 共用的测试资产格式，约占角色正文 49%。实测 J1-a1 的 `settings.json` system_prompt 32,812 字符，V1/V2/VS/VR 标题都在。
  - 目标：
    - `m2s-mcp-server/agents/m2s-verifier.md` → 只保留 v2 内容（J1/J3/J4/J5/D1/V3 + 通用工具/禁止事项/完成信号）。
    - 新建 `m2s-mcp-server/agents/m2s-verifier-v1.md` → 旧 v1 内容（V1/V2/VR/VS + V1/V2 共用的测试资产格式）。
    - 同步 `~/.openharness/agents/` 两份文件（运行时 `coord/workers.py` 读这里，不读仓库源）。
    - v2 模板（`v2.yaml`、`v2-split.yaml`、`v2-judge.yaml`、`v2-gaps.yaml`、`v2-adapter.yaml`）保持 `role: m2s-verifier`。
    - v1 模板（`v1.yaml`、`v1-split.yaml`）的 `role:` 改为 `m2s-verifier-v1`。如果“v1 模板保留旧 role”是指保留 `role: m2s-verifier` 这个名字，则新角色需另起名（例如 `m2s-verifier-v2`）；按“m2s-verifier.md 改用新 role”的要求，推荐让 `m2s-verifier-v1` 指向旧内容。
  - 完成（2026-11-25）：
    - `agents/m2s-verifier.md` 现在只含 v2：J1/J3/J4/J5/D1/V3 + 通用身份/工具/禁止事项/完成信号；正文 26,614 字符，`role_prompt` 输出 26,393 字符（原 32,812，约 −20%），**不含 `## V1/## V2/## VR/## VS`，也不含任何 V1/v1 字面量**。
    - 新建 `agents/m2s-verifier-v1.md`（19,749 字符）：V1/V2/VS/VR + 原测试资产格式（含 test plan）完整保留；`role_prompt` 输出 19,536 字符。
    - 保留在 v2 文件里的“增量测试资产格式”是 D1 明确引用的（`expected_from`、`怎么写预期`、故障场景），不能随 v1 一起移走；V1 专属的 test plan 和 V1/V2 叙述留在 v1 文件。两个文件的资产 schema 各自完整、无悬空引用。
    - `v1.yaml` / `v1-split.yaml` 的 V1/V2 改为 `role: m2s-verifier-v1`；所有 v2 模板保持 `m2s-verifier`。
    - `coord/notices.py` 把 `m2s-verifier-v1` 加入 `VERIFY_ROLES`、`spec-issue` 读写集合和 `ROOM_ROLES`（讨论室的参与者仍由 `rooms.py` 按 v2 生成，v1 不使用讨论室）。
    - 两份文件已同步到 `~/.openharness/agents/`（与仓库副本逐字节一致）。注意：部署副本现在来自 v3 分支；在 master 工作区上跑时 `gate(final)` 的 TOOLS 检查会认为部署副本与 master 的旧 `m2s-verifier.md` 不一致，合并到 master 或重新同步后恢复。
  - 兼容性检查：
    - `coord/harness.py::_agents()` 会扫描 `m2s-mcp-server/agents/*.md`，新增文件会进入 harness stamp；确认 run 记录/测试快照能接受新 agent 名。
    - `coord/notices.py`、`coord/rooms.py` 里的角色集合目前只写 `m2s-verifier`；如果 v1 模板还要跑，需要把 `m2s-verifier-v1` 加入相应集合，或明确 v1 不再使用公告板/讨论室。
    - `tests/test_coord_j1_perm.py` 的 `role_prompt("m2s-verifier")` 断言、`tests/test_coord_harness.py` 的 agent 枚举、`tests/test_coord_leak.py` 的 VERIFY 列表，按拆分后的角色名更新。
    - 增加断言：`m2s-verifier` 的 prompt 不含 `## V1/## V2/## VR/## VS`；`m2s-verifier-v1` 仍含 V1/V2/VR/VS。
  - 验收：
    - v2 的 J1/J3/J4/J5C/D1/V3 worker 的 `settings.json` system_prompt 不含 V1/V2/VR/VS。
    - v1 的 V1/V2 worker（若还跑）仍能读到 V1/V2/VR/VS。
    - 现有 coord 测试全绿；harness stamp 记录两份 agent。
  - 测试：
    - `tests/test_coord_j1_perm.py::test_verifier_role_split_keeps_v1_out_of_the_v2_prompt`：两边的标题、模板角色、D1 资产格式断言。
    - `tests/test_coord_harness.py::test_agent_definitions_include_the_split_verifier_roles`：harness stamp 枚举含两份 agent。
    - `tests/test_coord_leak.py`：`m2s-verifier-v1` 的公告可见性。
    - 运行结果：`test_coord_j1_perm/j1_perm/leak/harness/notices/units` 49 passed；`test_gates/toolchain` 99 passed, 1 skipped。
  - 备选：如果确认 v1 模板永远不再跑，可以跳过拆分，直接删掉 V1/V2/VR/VS 和 V1/V2 共用的测试资产格式。

- [ ]（可选）dispatcher 按 mode 只注入对应小节
  - 目标：即使同一个角色文件里有多套 mode，也只把当前 mode 的小节 + 通用小节注入 system prompt。
  - 实现：
    - `coord/workers.py::role_prompt(role, mode=None)` 或新增 `role_prompt_for(role, mode)`；
    - `coord/dispatcher.py::_spawn()` 把 `spec.mode` 传进去（没有 mode 的任务保持注入全文）；
    - 按 `## ` 标题切分：保留身份/总则、工具、禁止事项、完成信号，加上 `## <MODE>` 一节；`J5C` 用 `## J5`，V1/V2 等按 mode 映射。
    - 如果拆分已经完成，可只对 `m2s-verifier.md` 内的 J1/J3/J4/J5/D1/V3 做过滤；`m2s-verifier-v1.md` 同理。
  - 测试：
    - J1 prompt 含 `## J1`，不含 `## J3/## J4/## D1/## V3/## V1...`；
    - D1 prompt 含 `## D1`，不含 J 段；
    - 无 mode 的旧任务仍注入全文。
  - 优先级：低于拆分；拆分已经消除 v1/v2 交叉污染，这一项是进一步的 token 优化。

- [x] 拆分 `m2s-architect` 角色：把 v1 残余移出 v2 的 system prompt
  - 背景：`coord/workers.py::role_prompt()` 只按 role 读整个文件，不按 mode 过滤；A1 的 system prompt 里仍含 v1 的 `## 回炉：裁定验证线提出的 spec_issues`、`## 裁定 S5 的 spec 放宽申请`、`STAGE_COMPLETE/STAGE_BLOCKED` 和 `stage_reports/S1.json` 硬编码。v1 已退役。
  - 目标：
    - 新建 `m2s-mcp-server/agents/m2s-architect-v1.md` → 旧 v1 内容（S1 的 spec_issues 回炉、S5 spec 放宽、STAGE_COMPLETE/STAGE_BLOCKED、S1 报告路径）。
    - `m2s-mcp-server/agents/m2s-architect.md` → v2/A1 内容，并按下面 5 条清理。
    - 同步 `~/.openharness/agents/` 两份文件（运行时 `coord/workers.py` 读这里，不读仓库源）。
    - v2 模板（`v2-split.yaml` 的 A1、`v2-adapter.yaml` 的 AM、`v2-gaps.yaml` 的 A1）保持 `role: m2s-architect`。
    - v1 模板（`v1.yaml`、`v1-split.yaml`）如果还要能跑，`role:` 改为 `m2s-architect-v1`；如果确认永远不再跑，`m2s-architect-v1.md` 只作归档，模板保持不动或删除。
  - `m2s-architect.md` 的清理：
    1. 删掉 `## 回炉：裁定验证线提出的 spec_issues` 和 `## 裁定 S5 的 spec 放宽申请`；
    2. 把 `STAGE_COMPLETE/STAGE_BLOCKED` 改成 `submit_task/report_blocked`；
    3. 把 `stage_reports/S1.json` 改成 `storage/stage_reports/<task_id>.json`；
    4. 更新 frontmatter description（去掉“S1 阶段……由 m2s-migrate 编排 skill spawn”，写明 v2 A1 + `MODE: ruling/adapter/gaps`）；
    5. 保留 `MODE: ruling`、`MODE: adapter`、`MODE: gaps`，除非对应的 `v2-adapter` / `v2-gaps` 模板也退役。
  - 完成（2026-11-25）：
    - `agents/m2s-architect.md` 只保留 v2/A1：共有内容（身份/原则/数据架构/API 类型/幂等/契约测试等）+ `MODE: ruling` / 适配映射 / `MODE: adapter` / 缺口决定 / `## 迁移带来的对外变化（v2，A1 的裁定）`（原文件里这段没有标题，现在补上）+ `MODE: gaps`。
    - 删掉了 v1 的 `## 回炉：裁定验证线提出的 spec_issues` 与 `## 裁定 S5 的 spec 放宽申请`；输入契约里的 spec_issues 回炉行一并去掉。
    - 报告路径改为 `storage/stage_reports/<task_id>.json`（`stage_id` 同），完成方式改为 `submit_task` / `report_blocked`；`STAGE_COMPLETE/STAGE_BLOCKED`、`stage_reports/S1.json` 在 v2 prompt 里为 0 次。
    - 顺带清掉共享段落里的 v1 阶段名：`P0/S0` → `F1/F2`、`S3/S5` → `I2/O`、`S2/S3` → `I1/I2`、`主 Agent` → `调度器`、`V2 判失败` → `裁判判失败`。
    - 新建 `agents/m2s-architect-v1.md`：旧 v1 内容完整保留（`## 测试资产格式` 之外的全部 S1 设计内容 + V1 回炉 + S5 裁定 + S1 报告 `"agent": "m2s-architect-v1"` + STAGE 完成信号）；不含 v2 的 MODE/适配映射/缺口决定/record_ruling。
    - `v1.yaml` / `v1-split.yaml` 的 S1 改为 `role: m2s-architect-v1`；`v2-split` 的 A1、`v2-adapter` 的 AM、`v2-gaps` 的 A1 保持 `m2s-architect`。
    - 兼容：`dispatcher._route_role()` 按模板解析路由角色（`m2s-architect` 或 `m2s-architect-v1`），`_route_target` / `_flow_failed` 的 suspect 集合 / 分诊讨论室的模块归因都改用它，v1 的 `spec`/`blueprint` 回炉仍能找到 S1；`notices.py` 把 `m2s-architect-v1` 加入 `IMPL_ROLES`、`spec-issue` 读写集合和 `ROOM_ROLES`（v1 不使用讨论室，`rooms.py` 的参与者未改）。
    - 两份文件已同步到 `~/.openharness/agents/`（逐字节一致）。master 工作区的 TOOLS 检查同样会因部署副本来自 v3 而报告不一致，合并/重新同步后恢复。
  - 兼容性/测试：
    - `coord/harness.py::_agents()` 扫描 `m2s-mcp-server/agents/*.md`，确认新 agent 进入 harness stamp。
    - `coord/notices.py` / `rooms.py` 的角色集合按需加入 `m2s-architect-v1`（v1 不使用讨论室可跳过）。
    - 测试断言：`role_prompt("m2s-architect")` 不含 `## 回炉` / `## 裁定 S5` / `STAGE_COMPLETE` / `stage_reports/S1.json`；仍含 `## MODE: ruling/adapter/gaps`；`role_prompt("m2s-architect-v1")` 仍含 v1 段。
    - 现有 coord 测试全绿。
  - 测试：
    - `tests/test_coord_j1_perm.py::test_architect_role_split_keeps_v1_out_of_the_v2_prompt`（标题、settings.json system_prompt、模板角色）
    - `tests/test_coord_j1_perm.py::test_v1_spec_routing_resolves_the_v1_architect`（v1 的 spec/blueprint 路由仍到 S1）
    - `tests/test_coord_harness.py`（stamp 记录 m2s-architect-v1）、`tests/test_coord_leak.py`（v1 架构师的公告可见性）
    - 结果：j1_perm/harness/leak/units 45 passed；notices/rooms/gates/toolchain/v2-split 加载/full_flow 非 slow 项 123 passed, 8 skipped
  - 备选：如果 v1 连归档都不需要，可以不做拆分，直接按 5 条清理 `m2s-architect.md`，并删除 v1 模板/角色。

- [ ] ruling 的受影响任务支持包级精度：`affected_endpoints` / `affected_packages`
  - 现状：`record_ruling` 的 `affected_tasks` 只接受模板 ID；`_apply_ruling()` 对 `split: package` 的族展开**所有**子任务（F2/G2/J1 都是）。一个只影响某个包的裁定会 stale 整个族。F2/G2/J1 子任务带 recheck，通常只是重跑检查，但会产生大量 stale 级联。
  - 方案：给 `record_ruling` 增加可选 `affected_endpoints`、`affected_packages`、`affected_shared`；`_apply_ruling()` 对 F2/G2 用 `work_packages.json` / `_fact_targets` 的 endpoint→package 映射选子任务，`affected_shared=true`（或 `affected_targets` 里的 `"F2:shared"`）再加 shared child；本地包与 shared 取**并集**；J1 用 `affected_cases` 的 case 路径/包映射或 `affected_packages`；没有选择器时保持“展开整个族”的保守默认。
  - 同时影响某个包和 shared 时：A1 同时写 `affected_endpoints`/`affected_packages` 和 `affected_shared=true`；dispatcher 选 `F2-<pkg>` + `F2-shared`，`F2-shared` 仍按 `shared_after_children` 在所有包任务之后运行。若端点映射不到任何包，默认落到 shared。
  - 不建议再加一个 LLM 判断跨包：A1 已经知道受影响的端点/事实，让它在 ruling 里声明更便宜、更确定；LLM 只适合作为缺选择器时的 fallback，不应每次裁定都调用。
  - 测试：单包端点只 stale 该包 child；shared/无端点落到 shared；本地包 + shared 同时 stale（并集）；无选择器仍展开全族；J1 按 case/package 选 child。
  - 优先级：中低；airline-m4 里 A1 的 4 条 ruling `ruling_applied.stale` 都是空，说明这次运行的主因不是 A1 裁定展开（见下一条）。

- [x] 先验证/修复 `_fact_targets()` 的族选择
  - 现状：`_fact_targets()` 用 `families[-1]` 选 `m2s-spec-annotator` 的 split family；v2-split 里 F2 和 G2 都是这个 role，`families[-1]` 是 **G2**，不是 F2。`tests/test_coord_split.py` 只测了只有 F2 的模板，没覆盖 G2。
  - 影响：J3/J4 的 `fact_error` 可能被送到 G2 children（gap 任务），而不是 F2（事实库任务）；需要确认当前 v2-split 是否实际触发。airline-m4 的事件里是 F2-bookings 被回炉，说明当时运行的代码/模板可能早于这个组合，不能直接用它判断当前代码。
  - 修复：让 `_after_j3` / `_after_j4` 显式传 F2 family，或 `_fact_targets` 按 `upstream_of=J1` 选择 F2；加一个同时有 F2 和 G2 的 v2-split 测试。
  - 完成（2026-11-25）：
    - **复现确认**：用当前代码 + 真实 `v2-split` 模板（F2 与 G2 都已展开）调用 `_fact_targets`，实际返回 `['G2-alpha','G2-beta','G2-gamma','G2-shared']`——当前代码确实误投到 G2，不是历史遗留。
    - 原因：G2 按包拆分是 2026-11-16 加进 v2-split 的；在这之前 F2 是唯一的 annotator 族，`families[-1]` 恰好正确。airline-m4 的事件（F2-bookings 回炉）运行在加 G2 之前的模板上，因此不能证明当前代码正确。
    - 修复：新增 `Dispatcher._fact_family()`：从 J1（`mode: J1`）的 deps 里找 `<family>:*`，且该 family 是 `role: m2s-spec-annotator`、`split: package/domain` 的任务 → 返回 F2；只有模板没写这条关系时才回退到原来的“最后一个 annotator 族”。`_fact_targets()` 改用 `_fact_family()`，`_after_j3` / `_after_j4` 的非拆分分支保持不变。
    - 测试：`tests/test_coord_split.py` 新增
      - `test_fact_targets_pick_the_judge_family_not_the_gap_family`（真实 v2-split，F2+G2 都展开；修复前得到 G2-*，修复后得到 F2-*）
      - `test_j3_fact_errors_rework_f2_not_the_gap_family`（端到端调用 `_after_j3`，断言 rework 目标是 F2-alpha、没有 G2 任务）
      - `test_j4_spec_issues_rework_f2_not_the_gap_family`（端到端调用 `_after_j4`，`spec_issues_routed.to == ["F2-beta"]`，rework 目标是 F2-beta）
    - 结果：三项测试通过；既有 `test_shared_child_waits_for_the_package_children` 仍通过。
  - 优先级：高于 ruling 包级精度；如果当前代码真的把 fact_error 送到 G2，会直接造成错误回炉。

## 2026-11-26

- [ ] C1 全量检查失败时精细回退：保留前面成功的 O 改动
  - 排序：本项在下面的 B0P/O1/O2 落地后实现；先让 B0P 版 C1 支持“O2 → B0P”的简单回退，再叠加精细回退。不要先按 B0/O 实现一套再迁移到 B0P，否则会重复改 C1、回退基线、commit 识别和报告同步。
  - 现状：`coord/dispatcher.py::_act_final_v2` 在 `gate_final` / `judge_full` / `delta_full` 任一失败时，执行 `git checkout B0 -- output` 并提交，把 B0 之后**所有** O 的 `output/` 改动一起回退；即使前面的 REQ 已单独通过并提交，也会被最后一个失败或改动间交互连累。只重跑一次全量检查，仍失败就升级给人。
  - 目标：优先保留已经通过全量检查的改动，而不是直接整体回 B0P（B0P 方案下的最终回退基线）。
  - 实现（二选一或组合）：
    - 从最新 O2 commit 开始逐个回退：识别 `B0P..HEAD` 中 O2 的提交，`git revert --no-edit <sha>` 或 `git checkout <sha>^ -- output` 后重跑三项全量检查；通过就停，失败继续回退上一个；全部回退仍失败才回 B0P。
    - 维护“全量检查点”：每个 kept REQ 之后（或每 N 个）打一个内部 tag/记录 commit，C1 直接回退到最近一个全量通过的检查点，再重跑三项检查确认。
  - 注意：
    - C1 目前只回退 `output/`，`perf/`、`verification/` 下 O 的报告/判决可能仍是旧状态；精细回退后要同步 `perf_report.json` 的 `kept` 列表、`O.json` 和 `verification/*`，避免报告说 kept 而代码已回退。
    - 每次回退后的三项全量检查成本不低（judge + 全量 delta），需要限制尝试次数（例如最多回退 3 个 commit 或 2 个检查点），并记录每次的失败原因。
    - 若失败来自改动间交互（单个 REQ 的局部检查都通过），逐个回退可能仍找不到“只保留部分”的干净集合；此时应整体回退到 B0P 或让人决定。
  - 测试：
    - 构造 3 个 kept REQ，最后一个破坏全量检查：C1 回退最后一个后通过，前两个保留；
    - 构造交互失败（两个 REQ 单独通过、合起来失败）：按策略回退到检查点或 B0P，报告准确；
    - `perf_report.json` / `O.json` / `verification/verdict_*` 与最终 `output/` 一致；没有残留的 `o_reverted` 误报。
  - 与 B0P 方案的关系：若采用下面的 `B0 → O1/P → B0P → O2 → C1`，C1 的**最终回退基线是 B0P**（不是 B0），绝不回退 O1 已纳入 B0P 的架构级优化；但 C1 失败时先按上面的精细回退，从最新 O2 commit 开始逐个回退并重跑全量检查，尽量保留前面成功的 O2 改动。只有精细回退找不到通过状态或超过预算时，才整体回退 O2 到 B0P；仍失败则升级给人。

- [ ] 采用 `B0 → O1/P → B0P → O2 → C1`；O1 用方式 B（owners 写最终候选，P 只测量/判决），O2 直接做局部改动
  - 决策：
    - `B0`：纯迁移验收基线（当前 `tag_b0` 的产物），不包含 O1 的架构级性能改动。
    - `O1/P`：B0 之后的性能探索/测量阶段；O1 **不写**生产代码、blueprint、template、delta tests，只写 `perf/**` 和 `storage/stage_reports/O1.json`；候选由 A1/D1/I2c/I1 写最终版本。
    - `B0P`：B0 + O1 接受并通过验证/测量的性能改动；O2 的新基线。
    - `O2`：当前 O 的局部、语义保持优化；从 B0P 开始，直接写 `output/**`，不再经过 owners 重写。
    - `C1`：最终检查/报告；B0P 落地时先支持简单回退 O2 → B0P，精细回退按上一项后做；失败时优先精细回退 O2（保留前面成功的 O2 改动），找不到通过状态或超过预算再整体回退 O2 到 B0P；仍失败则升级。绝不回退到 B0。
  - 目标流程：
    ```text
    B0
      → O1/P：写 benchmark、测基线、给出候选清单/预期指标
      → A1 / D1 / I2c / I1：逐个实现最终候选（不是 throwaway patch）
      → O1/P：跑正确性 + 性能测量；通过且有收益 → 保留；否则回退
      → V2P：对 B0P 候选重跑全量 judge
      → V3P：对 B0P 候选重跑增量验证/覆盖率/等价性 + B0P 故障基线
      → B0P：tag B0 + 已接受的 O1 改动
      → O2：从 B0P 做局部优化
      → C1：最终检查；优先精细回退 O2，失败再整体回退到 B0P
    ```
  - O1 方式 B 的合同：
    - O1 只负责：benchmark（`perf/benchmarks/**`）、基线、候选请求（`perf/requests/**` 或 `perf/triage.json`）、测量/判决、`O1.json`；
    - 候选 owner：
      - A1：blueprint / `adapter_map.json` / `DECISIONS.md` / 数据模型 / GSI 设计；
      - D1：`verification/delta/**` 的最终用例/故障场景；
      - I1：`output/lambdas/**` 的最终代码；
      - I2a/I2b/I2c：`output/template*.yaml` 的最终模板；
    - O1 **不能**修改 `output/**`、`storage/blueprint.json`、`storage/adapter_map.json`、`verification/**`、`judge/**`，也不能为了让候选通过而改测试；
    - 一个候选一个 owner 提交/判决，不把多个独立候选合在一起；候选被拒时回退该候选，不影响已接受的 B0P 改动；
    - O1 的测量基线是 B0（候选相对 B0）或 B0P 当前已接受部分；O1 的候选证据单独记录，不进入 O2 的 `perf_report`。
  - 候选接受条件（全部满足才进 B0P）：
    - `gate_final`、`gate_blueprint`、`gate_template`、`check_adapter`、`judge`、delta `validate/equivalence/coverage` 全部通过；
    - delta tests 是 D1/VS 的最终版本，不是 O1 临时改的；
    - 有实际测量收益：代理指标 +（短名单）RIE/AWS；记录 before/after、成本、guard、适用条件；
    - 不改变 judge 期望；J6 之后裁判冻结，改变外部结果的“优化”直接拒绝或回到 J6 之前；
    - 正式实现后重测一次，临时补丁的收益不能直接当最终收益。
  - B0P 验收前必须跑（全部候选接受之后）：
    - V2P：`run_judge()` 全量；`judge_unchanged`；
    - V3P：`run_validate(assets="verification/delta")` 全量；delta 等价性以 B0 为 baseline（工具需要支持显式 baseline/B0P）；`run_coverage(..., with_judge=true, assets="verification/delta")`；`run_equivalence(mode="record", label="B0P", ...)` 写 `verification/fault_baseline_B0P.json`；
    - `gate_final`、`gate(blueprint)`、`gate(template)`、`check_adapter`；
    - 每个 accepted 候选的最终性能证据（proxy + RIE/AWS）和正式实现后的 after 重测；
    - 全部通过才允许 `_act_tag_b0p`；无候选时跳过 V2P/V3P，B0P 与 B0 同一 commit。
  - 调度/模板实现要点：
    - **编排归属是 dispatcher，不是 O1**。O1 是 LLM worker，不能 spawn A1/D1/I2c/I1，也不能绕过 board/scope/lock；O1 只写候选计划、`perf/**` 和 `O1.json`，dispatcher 读取候选并展开/路由 owner 任务、启动 O1 测量、接受/回退、进入下一个候选。
    - `v2-split.yaml` 增加 `O1`、候选 owner 任务、`V2P`、`V3P`、`B0P`、`O2`；`C1` deps 改为 `[O2]`；
    - O1 方式 B 需要一个候选循环：O1 产出候选 → owner 实现 → O1 判决 → 接受/回退 → 下一个；具体形式（dispatcher 自有 `perf_triage` action、任务族 `O1-<n>-*`，还是固定 `O1-plan → owner → O1-measure` 的有限轮次）待定，但**编排权必须在 dispatcher**；这是本项最大的实现难点；
    - 候选 owner 的路由可复用 `_route_target`（`code`→I1、`template`→I2c、`spec/blueprint`→A1），D1 需要单独的测试 owner 路由；
    - 谁提交候选（owner 提交还是 dispatcher 在 O1 接受后提交）要定；O1 没有 `output/**` 写权限，不能自己提交代码；
    - `B0P` tag action：扩展 `_commit_and_tag`/新增 `_act_tag_b0p`；B0P 接受条件包含 V2P 的 `verdict_judge`、V3P 的 `verdict_delta`/覆盖率和 `judge_unchanged`；无候选时 B0P 可以指向 B0 同一 commit；
    - O2 的 `s5_report`、`run_equivalence`、`C1` 的 baseline 改为 B0P；`run_equivalence` 目前找 B0，需要显式 `baseline_ref="B0P"` 或可配置 tag；
    - B0P 的故障基线单独写 `verification/fault_baseline_B0P.json`（或 `run_equivalence(label="B0P")`），O2 的 `run_equivalence`/`s5_report`/C1 都用 B0P；`verdict_delta` 的 `assets_digest` 必须对应 B0P 的测试资产；
    - `closing.final_report` 分开记录 `B0 → B0P`（O1 架构收益）和 `B0P → final`（O2 局部收益）。
  - 角色/prompt 文档：
    - `m2s-perf-optimizer.md` 增加 `MODE: O1/P`（方式 B：只测量/判决，不写生产代码）和保留 `MODE: O2` 的当前局部流程；或新建独立 `m2s-perf-triage.md`，避免 O1/O2 合同混在一个 prompt；
    - A1/D1/I1/I2 增加“性能候选实现”模式的说明：读 `perf/triage.json`/`perf/requests`，只实现被 O1 选中的候选，完成后交给 O1 测量，不自行宣布 kept；
    - `coord/prompts.py` 增加 O1、候选 owner、V3P、B0P 的摘要和完成信号；
    - `PERF_OPTIMIZATION_DESIGN.md` / `PIPELINE_V3_PLAN.md` 增加 B0/B0P/O1/O2 两基线模型和证据格式。
  - 测试：
    - 模板 DAG：`B0 → O1 → 候选 owner → V2P → V3P → B0P → O2 → C1`；O1 无 `output/**` 写权限；
    - O1 候选通过 → B0P 包含该改动；候选被拒 → B0P 不变；
    - 无 O1 候选时 B0P 与 B0 同一 commit，O2 行为与当前 O 一致；
    - O2 的 `s5_report` / `run_equivalence` 使用 B0P；C1 先精细回退 O2，失败再整体回退到 B0P；
    - O1 改测试会被 write_scope/check 拒绝；O1 不能改 judge；
    - 最终报告同时有 `B0 → B0P` 和 `B0P → final` 两段。
  - 开放问题（实现前要定）：
    - O1 候选循环用 dispatcher action 还是任务族；最多几个候选/几轮；
    - 候选 owner 任务是否都跑一次，还是按目标只跑 A1/D1/I1/I2 中相关的一个；
    - 谁提交候选、谁回退候选；O1 只测量时如何保证接受后的 commit 原子性；
    - V2P/V3P 是否作为独立任务，还是复用 V2/V3 的检查并重跑；
    - GSI/数据模型候选如果改变 judge 期望，是否一律拒绝并转 pre-J6 人工流程。

- [x] 拆分 `m2s-code-developer` 角色：把 v1 的 S2 完成协议移出 I1 的 system prompt
  - 背景：`coord/workers.py::role_prompt()` 按 role 读整个文件，不按 mode 过滤；I1 的 prompt 仍含 S2 / `STAGE_COMPLETE/STAGE_BLOCKED` / `stage_reports/S2.json` / `主 Agent`，与 v3 的 `submit_task/report_blocked` 和 I1 scope 冲突（`S2.json` 不在 I1 写入范围，会被 `write_scope` 撤销）。v1 已退役。
  - 目标：
    - 新建 `m2s-mcp-server/agents/m2s-code-developer-v1.md` → 旧 v1 内容（S2 阶段、STAGE 完成信号、S2 报告路径、主 Agent 输入契约）。
    - `m2s-mcp-server/agents/m2s-code-developer.md` → 只保留 v2/I1 内容。
    - 同步 `~/.openharness/agents/` 两份文件（运行时 `coord/workers.py` 读这里，不读仓库源）。
    - v2 模板（`v2-split.yaml` 的 I1）保持 `role: m2s-code-developer`。
    - v1 模板（`v1.yaml`、`v1-split.yaml`）如果还要能跑，`role:` 改为 `m2s-code-developer-v1`；如果确认永远不再跑，v1 文件只作归档，模板保持不动或删除。
  - `m2s-code-developer.md` 的清理：
    1. 删掉 `STAGE_COMPLETE/STAGE_BLOCKED`，改成 `submit_task` / `report_blocked`；
    2. `stage_reports/S2.json` → `storage/stage_reports/<task_id>.json`（I1 子任务实际是 `I1-<pkg>.json` / `I1-shared.json`）；
    3. S2/S3 阶段名 → I1/I2；
    4. 更新 frontmatter description（去掉“S2 阶段……由 m2s-migrate 编排 skill spawn”）；
    5. “blueprint 不清楚/机制没写”时写 `report_blocked(reason, evidence, suspected_module="A1")`，不要 `STAGE_BLOCKED`；
    6. 保留 v2 judge 自测、`run_gate(phase="code")`、共享层、Cognito、contract 2 等内容。
  - 完成（2026-11-26）：
    - `agents/m2s-code-developer.md` 只保留 v2/I1：完成方式 `submit_task`/`report_blocked`，报告 `storage/stage_reports/<task_id>.json`；S2→I1、S3→I2、`S1 合并后的产物/主 Agent`→`A1 写好的产物/调度器`；blueprint 机制没写时 `report_blocked(..., suspected_module="A1")`；保留裁判自测、`run_gate(phase="code")`、共享层、Cognito、contract 2。`role_prompt` 中 `STAGE_COMPLETE/STAGE_BLOCKED`、`stage_reports/S2.json`、`主 Agent` 均为 0。
    - 新建 `agents/m2s-code-developer-v1.md`：旧 S2 内容（STAGE 完成信号、S2 报告路径、主 Agent 输入契约、S3 阶段引用）完整保留；不含 v2 的裁判自测。
    - `v1.yaml` / `v1-split.yaml` 的 S2 改为 `role: m2s-code-developer-v1`；`v2.yaml` / `v2-split.yaml` / `v2-gaps.yaml` 的 I1 保持 `m2s-code-developer`。
    - `coord/notices.py` 把 `m2s-code-developer-v1` 加入 `IMPL_ROLES`、`spec-issue` 可写集合和 `ROOM_ROLES.triage`。
    - 顺带修正 `coord/prompts.py` 的 v2 摘要：A1 不再写“角色说明里的 S1”，I1 不再写“角色说明里的 S2”，I2a/I2b/I2c 改用 `STAGE_ID=I2a/I2b/I2c`。
    - 两份文件已同步到 `~/.openharness/agents/`（逐字节一致）。
  - 兼容性/测试：
    - `coord/harness.py::_agents()` 扫描 `m2s-mcp-server/agents/*.md`，确认新 agent 进入 harness stamp。
    - `coord/notices.py` / `rooms.py` 的角色集合按需加入 `m2s-code-developer-v1`（v1 不使用讨论室可跳过）。
    - 测试断言：`role_prompt("m2s-code-developer")` 不含 `STAGE_COMPLETE/STAGE_BLOCKED` / `stage_reports/S2.json` / `主 Agent`，仍含 `submit_task` / `report_blocked` / `run_judge_self_test`；`role_prompt("m2s-code-developer-v1")` 仍含 v1 段；模板角色正确。
    - 现有 coord 测试全绿。
  - 测试：
    - `tests/test_coord_j1_perm.py::test_code_developer_role_split_keeps_s2_out_of_the_v2_prompt`（含 settings.json system_prompt 与模板角色）
    - 同一测试文件里的 v1 路由断言扩展为 `code`→S2、`template`→S3c
    - `tests/test_coord_harness.py`（stamp 记录两份新 agent）、`tests/test_coord_leak.py`（v1 角色公告可见性）
    - 结果：j1_perm/harness/leak/units 47 passed；notices/rooms/rulings/gates/toolchain/split/full_flow 非 slow 项 135 passed, 8 skipped
  - 备选：如果 v1 连归档都不需要，直接按 6 条清理 `m2s-code-developer.md`，并删除 v1 模板/角色。

- [x] 拆分 `m2s-sam-engineer` 角色：把 v1 的 S3/SG 和完成协议移出 I2 的 system prompt
  - 背景：`coord/workers.py::role_prompt()` 按 role 读整个文件，不按 mode 过滤；I2a/I2b/I2c 的 prompt 仍含 S3a/S3b/S3c、`STAGE_COMPLETE/STAGE_BLOCKED`、`stage_reports/<STAGE_ID>.json`、**“S3a/S3b 不调用任何 MCP 工具”**、master 的硬编码 merge 路径和 SG（GSI 修复）模式。后两条最危险：I2a/I2b 不调 `submit_task` 就无法提交；写 `S3a.json` 不在 I2a scope 内，会被 `write_scope` 撤销。v1 已退役。
  - 目标：
    - 新建 `m2s-mcp-server/agents/m2s-sam-engineer-v1.md` → 旧 v1 内容（S3a/S3b/S3c、SG、STAGE 完成信号、S3a/S3b/S3c 报告路径）。
    - `m2s-mcp-server/agents/m2s-sam-engineer.md` → 只保留 v2/I2 内容。
    - 同步 `~/.openharness/agents/` 两份文件（运行时 `coord/workers.py` 读这里，不读仓库源）。
    - v2 模板（`v2-split.yaml` 的 I2a/I2b/I2c）保持 `role: m2s-sam-engineer`。
    - v1 模板（`v1.yaml`、`v1-split.yaml`）如果还要能跑，`role:` 改为 `m2s-sam-engineer-v1`；如果确认永远不再跑，v1 文件只作归档，模板保持不动或删除。
  - `m2s-sam-engineer.md` 的清理：
    1. S3a/S3b/S3c → I2a/I2b/I2c；
    2. `STAGE_COMPLETE/STAGE_BLOCKED` → `submit_task` / `report_blocked`；
    3. `storage/stage_reports/<STAGE_ID>.json` → `storage/stage_reports/<task_id>.json`，`stage_id` 用 I2a/I2b/I2c；
    4. 把“S3a/S3b 不调用任何 MCP 工具”改成“I2a/I2b 不调用 `run_gate`/`validate_sam`；完成时必须调用 `submit_task` / `report_blocked`”；
    5. 合并命令不要硬编码 `D:\Emma\MyLab\Mono2SLs\m2s-mcp-server\m2s_merge.py`，用 prompt 里的 `MERGE`（v3 worktree 路径）；
    6. S2 → I1，S3 → I2，主 Agent → 调度器；
    7. SG（GSI 修复）段移到 v1 文件或删除（当前 v2-split 没有 SG 任务/模板）；
    8. 更新 frontmatter description（去掉 S3 + `m2s-migrate`）；
    9. 保留 TIER 1/2、API type、PayloadFormatVersion 1.0、全局响应头、幂等表、CodeUri/Handler、merge、`run_gate`/`validate_sam`、`m2s-sam-kb`。
  - 完成（2026-11-26）：
    - `agents/m2s-sam-engineer.md` 改为 I2a/I2b/I2c：I2a/I2b 不调用 `run_gate`/`validate_sam`（分片不是完整模板），但每个子阶段结束时必须 `submit_task`、做不下去时 `report_blocked`；I2c 用 prompt 的 `MERGE` 跑 `python "<MERGE>" template "<WS>"`，不再硬编码 `D:\Emma\MyLab\Mono2SLs` 或 `envs\mono2sls`；报告 `storage/stage_reports/<task_id>.json`；SG 段移除；S3a/S3b/S3c/S2/主 Agent/STAGE 在 prompt 中均为 0。
    - 保留 TIER 1/2、认证规则、API 类型、PayloadFormatVersion 1.0、全局响应头、幂等表、CodeUri/Handler、ReportBatchItemFailures、DynamoDBCrudPolicy、I2c 自查循环与两个常见坑、`m2s-sam-kb`。
    - 新建 `agents/m2s-sam-engineer-v1.md`：旧 S3a/S3b/S3c + SG + STAGE 协议 + S3 报告路径完整保留（与原文逐字一致，仅 frontmatter 改名），其中的合并命令仍是 v1 的硬编码路径。
    - `v1.yaml` / `v1-split.yaml` 的 S3a/S3b/S3c 改为 `role: m2s-sam-engineer-v1`；`v2.yaml` / `v2-split.yaml` / `v2-gaps.yaml` 的 I2 角色保持 `m2s-sam-engineer`。
    - `coord/notices.py` 把 `m2s-sam-engineer-v1` 加入 `IMPL_ROLES` 和 `ROOM_ROLES.triage`。
    - 两份文件已同步到 `~/.openharness/agents/`（逐字节一致）。
  - 兼容性/测试：
    - `coord/harness.py::_agents()` 扫描 `m2s-mcp-server/agents/*.md`，确认新 agent 进入 harness stamp。
    - `coord/notices.py` / `rooms.py` 的角色集合按需加入 `m2s-sam-engineer-v1`（v1 不使用讨论室可跳过）。
    - 测试断言：`role_prompt("m2s-sam-engineer")` 不含 `S3a/S3b/S3c` / `STAGE_COMPLETE/STAGE_BLOCKED` / `stage_reports/S3a.json` / `STAGE_ID = SG` / “不调用任何 MCP 工具”，仍含 `I2a/I2b/I2c`、`submit_task`、`report_blocked`、`MERGE`、`m2s-sam-kb`；`role_prompt("m2s-sam-engineer-v1")` 仍含 S3a/S3b/S3c/SG/STAGE 段；模板角色正确。
    - 现有 coord 测试全绿。
  - 测试：
    - `tests/test_coord_j1_perm.py::test_sam_engineer_role_split_keeps_s3_and_sg_out_of_the_v2_prompt`（含 settings.json system_prompt、硬编码路径与模板角色）
    - 相邻回归与上一项合并运行：47 passed（角色/权限/单元）+ 135 passed, 8 skipped（notices/rooms/rulings/gates/toolchain/split/full_flow 非 slow）
  - 备选：如果 v1 连归档都不需要，直接按 9 条清理 `m2s-sam-engineer.md`，并删除 v1 模板/角色。

- [x] 拆分 `m2s-perf-optimizer` 角色：v1 S5 与当前 O 分离
  - 背景：`m2s-perf-optimizer.md` 现在是“v1 S5 主体 + 一段 v2 O 覆盖层”：仍有 `v0`、`S5.json`、`STAGE_COMPLETE/STAGE_BLOCKED`、`主 Agent`、VR/VS 复核、重新判决模式、SG、`CONTRACT_TESTING_PLAN.md` 引用；而 v3 的 O 是 B0 基线、写 `O.json`、用 `submit_task/report_blocked`、四项正确性检查 + RIE/AWS 判决。
  - 目标：
    - 新建 `m2s-mcp-server/agents/m2s-perf-optimizer-v1.md` → 旧 v1 S5 内容（v0、S5.json、STAGE 协议、VR/VS、重新判决、SG）。
    - `m2s-mcp-server/agents/m2s-perf-optimizer.md` → 只保留当前 v2 O：B0 基线、`O.json`、四项检查、RIE/AWS、blocked/pending 处理。
    - v2 模板（`v2-split.yaml` 的 O）保持 `role: m2s-perf-optimizer`；v1 模板如果还要能跑，`role:` 改为 `m2s-perf-optimizer-v1`，否则 v1 文件只归档。
    - 注意：v3 当前 `coord/workers.py` 读 `m2s-mcp-server/agents`；`~/.openharness/agents` 是旧配置/`oh` 侧副本，按需同步，不能当作 v3 的运行时真源。
  - `m2s-perf-optimizer.md` 的清理：
    1. `v0` → `B0`，`S5.json` → `O.json`；
    2. `STAGE_COMPLETE/STAGE_BLOCKED` → `submit_task` / `report_blocked`；
    3. 删除 VR/VS 复核、重新判决模式、SG、`主 Agent`；v2 的 `blocked_by_test`/`blocked_by_coverage`/`pending_spec` 按现规则留在报告里交人；
    4. 更新 frontmatter description（去掉 S5 + `m2s-migrate`）；
    5. 保留 v2 四项检查、`M2S_AWS=1` / 非 1 的判决选择、`s5_report`/`judge_unchanged`、`update_ledger`。
  - 测试：`role_prompt("m2s-perf-optimizer")` 不含 `v0` / `S5.json` / `STAGE_COMPLETE` / `VR` / `VS` / `主 Agent`，仍含 `B0`、`O.json`、`submit_task`、`report_blocked`、`M2S_AWS`；`role_prompt("m2s-perf-optimizer-v1")` 仍含 v1 段；模板角色正确；现有 coord 测试全绿。
  - 完成（2026-11-26）：
    - `agents/m2s-perf-optimizer.md` 重写为 v3 O：B0 基线、`O.json`、四项检查（judge / delta validate / delta equivalence / delta coverage）、`M2S_AWS=1` 与 RIE 的判决选择、`submit_task`/`report_blocked`；删除旧基线的 S5 协议、复核/重新判决、规格放宽、SG、主 Agent 和 `CONTRACT_TESTING_PLAN.md` 引用；请求的 `semantic_class` 改为 `allowed`。
    - 新建 `agents/m2s-perf-optimizer-v1.md`：旧 S5 内容完整保留（仅 frontmatter 改名、设计文档路径改 `PERF_OPTIMIZATION_DESIGN_v1.md`、删除 v2 覆盖层与 AWS 工具段、AWS 禁用条款改为 v1 的"不做真实 AWS 部署"）；重新判决段保留 `STAGE_ID=VR`/`VS` 的复核说明。
    - `v1.yaml` / `v1-split.yaml` 的 S5 改为 `m2s-perf-optimizer-v1`；v2 模板保持 `m2s-perf-optimizer`。`coord/notices.py` 的 `IMPL_ROLES` 加入 v1 角色；`coord/prompts.py` 的 O 摘要去掉 S5 引用并补 `O.json`；`README.md` 的 agent 列表同步。
    - `~/.openharness/agents/m2s-perf-optimizer.md`、`-v1.md`、`m2s-code-developer.md`、`m2s-verifier-v1.md` 已同步（逐字节一致）。
    - 测试：新增 `tests/test_coord_j1_perm.py::test_perf_optimizer_role_split_keeps_s5_out_of_the_v2_prompt`（v2 prompt 的六项禁词与 `B0`/`O.json`/`submit_task`/`report_blocked`/`M2S_AWS`，v1 段，settings.json system_prompt，模板角色）；`tests/test_coord_harness.py` 的 harness stamp、`tests/test_coord_leak.py` 的可见性矩阵加入 v1 角色。`test_coord_j1_perm.py + test_coord_harness.py + test_coord_leak.py` 29 passed；`test_coord_units.py` 19 passed。

- [x] 拆分 `PERF_OPTIMIZATION_DESIGN.md`：v1 归档与 v2 O 设计分离
  - 背景：该文档标题写“v2：优化线”，但主体是 v1 设计 + §2.2 v2 AWS 补丁：§1 仍写 S5/v0，§3.6 仍写 VR/VS，§5 是 v1 默认配置，§9 输出仍是 `S5.json`，附录是 v1 实验；而 §3/§6/§8 仍被 v2 O 使用。
  - 目标：
    - 新建 `PERF_OPTIMIZATION_DESIGN_v1.md`：v1 的 S5/v0 流程、sam+Docker/模拟延迟、VR/VS、SG、v1 默认配置、v1 prompt/消融、附录实验记录。
    - `PERF_OPTIMIZATION_DESIGN.md` 只保留 v2 O：B0 基线、judge/delta 四项检查、§2.2 AWS 判决、RIE 默认/代理降级、允许改动、规则库、输出 schema、测量窗口；可保留仍然有效的 v1 实验证据作为附录/引用。
    - 更新所有引用：`m2s-perf-optimizer.md`、`PIPELINE_V2_PLAN.md`、`PERF_OPTIMIZATION.md`、代码注释等。
  - 验收：v2 文档不含 S5/v0/VR/VS/SG 流程；v1 归档保留旧内容；引用路径正确；`m2s-perf-optimizer.md` 指向 v2 文档。
  - 完成（2026-11-26）：
    - 新建 `PERF_OPTIMIZATION_DESIGN_v1.md`（716 行）：v1 的 §0–§12 与附录 A.1–A.4 归档，删除仅属于 v2 的 §2.2 AWS 判决；§3 的 AWS 引用改为指向 v2 文档。
    - `PERF_OPTIMIZATION_DESIGN.md` 重写为 v2 O（约 24KB）：§0 背景、§1 B0 → O → C1、§2 测量（§2.1 RIE 默认与模拟延迟指针、§2.2 AWS）、§3 判决（四项检查、RIE/代理、分诊、A/B 档）、§4 成本、§5 允许改动与 H1、§6 改动组织、§7 规则库/台账、§8 O 框架（输入、流程、`perf_report`/`O.json`、请求、禁止事项、Python）、§9 测量窗口、附录 A.1/A.3/A.4（沿用 v1 编号；A.2 只在 v1 归档）。
    - 引用同步：`agents/m2s-perf-optimizer.md` / `-v1.md`、`PIPELINE_REDESIGN.md`、`CONTRACT_TESTING_PLAN.md`、`m2s-migrate/SKILL.md`、`m2s-verifier-v1.md`、`perf-tools/README.md`、`perf-rules/v2_requests.json`、`perf.py`/`server.py`/`verify.py`/`verify-tools/py/*` 注释；`PIPELINE_V3_PLAN.md` 的 S5 角色与进度同步。
    - 验收：v2 文档 grep 不到 `S5` / `v0` / `VR` / `VS` / `SG` / `STAGE`；v1 归档保留旧内容；代码与文档的章节引用已按新编号更新（`run_config_verdict` → §3.4、`run_rie_verdict` → §3.2、AWS → §2.2）。

- [x] 把 v2 `PERF_OPTIMIZATION_DESIGN.md` 整理成精简 skill
  - 背景：完整文档约 55KB，直接做成 `SKILL.md` 会被 `skill()` 整份加载，token 成本高；现有 `.openharness/skills/m2s-performance-kb` 约 6KB，是“写代码时的性能反模式”，不是 O 的判决框架，且 O 角色当前没有调用任何 skill。
  - 目标：
    - 新建 `.openharness/skills/m2s-perf-optimizer-kb/SKILL.md`（建议 5–10KB）：B0 基线、允许/不允许改动、四项正确性检查、RIE/AWS 选择、`perf_report`/`O.json` 输出、blocked/pending 处理、测量窗口纪律。
    - 完整 v2 设计放 `references/PERF_OPTIMIZATION_DESIGN.md`（或保留仓库文档并在 skill 里指路径），不要在 SKILL.md 里复制 55KB。
    - `m2s-perf-optimizer.md` 明确写 `skill(name="m2s-perf-optimizer-kb")`；保留 `m2s-performance-kb` 作为 I1 写代码时的反模式参考（可顺带让 I1 调用它）。
  - 注意：v2-split 的 project skills 从 `D:\Emma\MyLab\Mono2SLs-v3\.openharness\skills` 加载（worker 的 `--cwd` 是仓库根）；`m2s-mcp-server\.openharness` 不是 skill 目录。harness stamp 会记录 `project_skills_sha256`。
  - 验收：O prompt 含 `m2s-perf-optimizer-kb` 调用；SKILL.md 不含 S5/v0/VR/VS/STAGE；references 可读取；没有大段重复设计文档。
  - 完成（2026-11-26）：
    - 新建 `.openharness/skills/m2s-perf-optimizer-kb/SKILL.md`（约 8.3KB）：B0 基线、允许/不允许的改动、四项检查、分诊、RIE/AWS 选择、`perf_report`/`O.json` 输出、blocked/pending 处理、AWS 测量窗口纪律；grep 不到 `S5` / `v0` / `VR` / `VS` / `STAGE`。
    - `references/PERF_OPTIMIZATION_DESIGN.md` 是 v2 设计的完整副本（约 42KB），SKILL.md 同时给出仓库路径；没有把设计文档塞进 SKILL.md。
    - `agents/m2s-perf-optimizer.md` 在"身份与使命"和"工具"两处显式写 `skill(name="m2s-perf-optimizer-kb")`；`agents/m2s-code-developer.md`（I1）增加 `skill(name="m2s-performance-kb")` 的反模式参考（可选调用，不改变 gate 判定）。
    - 测试：`test_perf_optimizer_role_split_keeps_s5_out_of_the_v2_prompt` 断言 O prompt 含 `m2s-perf-optimizer-kb`；技能目录名与 frontmatter 的 `name` 一致。
