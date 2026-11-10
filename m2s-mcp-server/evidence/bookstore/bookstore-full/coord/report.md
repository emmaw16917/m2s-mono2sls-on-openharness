# 运行报告：bookstore-full

- 结果：**archiving**
- 单体：D:\Emma\MyLab\Mono2SLs\Mono2Sls-artifacts\mono_benchmark\bookstore
- 模板：v2

- harness（2026-11-03T16:20 起）：m2s 697ae7f860（未提交改动 0 处），OpenHarness 07404fad4b，模型 qwen3.8-flash

| 任务 | 状态 | 启动次数 | 崩溃 | API 中断 | 用时（分钟） | 最后一次检查 |
|---|---|---|---|---|---|---|
| F1 | closed | 0 | 0 | 0 | 0.0 | analyze_project✓；analysis_report✓ |
| F2 | closed | 0 | 0 | 0 | 0.0 | check_provenance✓ |
| G1 | closed | 0 | 0 | 0 | 0.0 | gap_scan✓；gap_inventory✓ |
| G2 | closed | 1 | 0 | 0 | 20.2 | write_scope✓；tools_unchanged✓；judge_unchanged✓；gap_inventory✓ |
| J1 | closed | 0 | 0 | 0 | 0.0 | judge_assets✓ |
| J2 | closed | 0 | 0 | 0 | 0.0 | run_monolith✓ |
| RC | closed | 0 | 0 | 0 | 0.0 | reconcile_spec✓ |
| J3 | closed | 0 | 0 | 0 | 0.0 | reconcile_classified✓ |
| J4 | closed | 0 | 0 | 0 | 0.0 | judge_assets✓；j4_report✓ |
| J5 | closed | 0 | 0 | 0 | 0.0 | run_mutants✓ |
| J5C | closed | 0 | 0 | 0 | 0.0 | mutant_classes✓ |
| J6 | closed | 0 | 0 | 0 | 0.0 | freeze_judge✓ |
| A1 | closed | 1 | 0 | 0 | 72.6 | write_scope✓；tools_unchanged✓；judge_unchanged✓；blueprint_keys✓；gate_blueprint✓；judge_unchanged✓；rulings✓ |
| D1 | closed | 1 | 0 | 0 | 15.3 | write_scope✓；tools_unchanged✓；judge_unchanged✓；delta_assets✓；judge_unchanged✓ |
| I1 | closed | 1 | 0 | 0 | 56.1 | write_scope✓；tools_unchanged✓；judge_unchanged✓；gate_code✓；judge_unchanged✓ |
| I2a | closed | 1 | 0 | 0 | 18.8 | write_scope✓；tools_unchanged✓；judge_unchanged✓；template_part1✓ |
| I2b | closed | 1 | 0 | 0 | 22.1 | write_scope✓；tools_unchanged✓；judge_unchanged✓；template_part2✓ |
| I2c | closed | 1 | 0 | 0 | 3.7 | write_scope✓；tools_unchanged✓；judge_unchanged✓；gate_template✓ |
| I3 | closed | 1 | 0 | 0 | 12.9 | write_scope✓；tools_unchanged✓；judge_unchanged✓；gate_final✓；consistency_pass✓ |
| V1 | closed | 0 | 0 | 0 | 0.0 | check_adapter✓ |
| V2 | closed | 0 | 0 | 0 | 0.0 | run_judge✓ |
| V3 | closed | 1 | 0 | 0 | 13.8 | write_scope✓；tools_unchanged✓；judge_unchanged✓；verdict_delta✓；judge_unchanged✓ |
| B0 | closed | 0 | 0 | 0 | 0.0 | tag_b0✓ |
| O | closed | 1 | 0 | 0 | 73.3 | write_scope✓；tools_unchanged✓；judge_unchanged✓；s5_report✓；judge_unchanged✓ |
| C1 | closed | 0 | 0 | 0 | 0.0 | final✓ |
| C2 | in_progress | 0 | 0 | 0 | 0.0 |  |

## 异常处理

- C001-B0-review：ok → 继续

## 裁定

- R-A1-a1-1（m2s-architect，decided，migration-change）：受保护端点的未认证 / 无效令牌请求：401 的响应体由谁给出？（裁判在单体上实录的是 authMiddleware 的 {"error": ...}，目标上应答的是 API Gateway 的 Cognito 授权器） → 迁移后的对外结果：状态码仍是 401，表访问一次都不发生（与单体一致），响应头仍带 Access-Control-Allow-Origin: * 与 Access-Control-Allow-Credentials: true（由模板的 GatewayResponses 的 UNAUTHORIZED/AUTHORIZER_FAILURE/ACCESS_DENIED 项给出），但响应体换成网关固定的
…（截断，原文 561 字符）

裁定共 1 条，其中人工裁定或确认 0 条，还在等人 0 条。

## 缺口清单

- 18 条：delta 2，judge 8，none 8
