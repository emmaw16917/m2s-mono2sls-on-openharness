STAGE_ID: J4
MODE: J4

这是 m2s 迁移流水线的一个任务，由 v3 调度器派发；你的角色说明已经在系统提示词里。这是这个任务第 1 次尝试。

## 绝对路径
- MONOLITH = D:\Emma\MyLab\Mono2SLs\Mono2Sls-artifacts\mono_benchmark\bookstore
- WS = D:\Emma\MyLab\Mono2SLs\Mono2Sls-artifacts\M2S\runs\bookstore-m4

## 任务
MODE: J4。白盒补测：可以读单体源码。按 judge/coverage_monolith/summary.json 里还没走到的行（第二轮起还有回炉信息里的 gap 变异体）补用例 judge/cases/<接口 slug>/wb-*.json；代码里有、事实库没有的规则写进 judge/spec_issues.json。按你的角色说明里"J4"一节做，用 mcp__m2s-server__check_judge_assets 自查到 PASS。

## 回炉信息（调度器转来的，请据此修正）

### 1. 来自 recheck
白盒补测（J4）还没有做过。

## 完成方式
1. 写工作报告 WS/storage/stage_reports/J4.json（格式按你的角色说明）。
2. 调用 mcp__m2s-server__submit_task(summary=...) 提交，然后结束会话。不需要再输出 STAGE_COMPLETE。
3. 做不下去时，调用 mcp__m2s-server__report_blocked(reason, evidence, suspected_module)，然后结束会话；认为是更早阶段的问题时，在 suspected_module 写那个阶段（例如 S2），并在 evidence 里给出证据。
4. 调度器会自己运行这个任务的检查（judge_assets, j4_report），你的自述不作为判定依据。

## 注意
- 如果调用工具或等待过程中遇到网络错误（例如 'Network error: Connection error'），重试 1-2 次再继续，不要因为一次网络错误就直接放弃或提前结束。
- 只改这个任务范围内的文件：judge/cases/**、judge/fixtures/**、judge/faults/**、judge/spec_issues.json、storage/stage_reports/J4.json。范围之外的改动会被撤销，这次尝试判为失败。
- 不要修改流水线自己的工具：m2s-mcp-server/、.openharness/、~/.openharness/ 下的文件。
- 不要读写 WS/coord/（调度器的内部状态）。不要调用 mcp_auth。
- 不要读别的工作区（WS 所在 runs/ 目录下的其他目录、runs-prev-*），也不要参照它们的产物。
- 这份说明可以随时用 mcp__m2s-server__get_task_context 重新取回。