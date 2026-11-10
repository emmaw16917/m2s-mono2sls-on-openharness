# 运行报告：airline-full

- 结果：**archiving**
- 单体：D:\Emma\MyLab\Mono2SLs\Mono2Sls-artifacts\mono_benchmark\airline-booking
- 模板：v2

- harness（2026-11-03T23:07 起）：m2s 697ae7f860（未提交改动 1 处），OpenHarness 07404fad4b，模型 qwen3.8-flash

| 任务 | 状态 | 启动次数 | 崩溃 | API 中断 | 用时（分钟） | 最后一次检查 |
|---|---|---|---|---|---|---|
| F1 | closed | 0 | 0 | 0 | 0.0 | analyze_project✓；analysis_report✓ |
| F2 | closed | 0 | 0 | 0 | 0.0 | check_provenance✓ |
| G1 | closed | 0 | 0 | 0 | 0.0 | gap_scan✓；gap_inventory✓ |
| G2 | closed | 1 | 0 | 0 | 28.0 | write_scope✓；tools_unchanged✓；judge_unchanged✓；gap_inventory✓ |
| J1 | closed | 0 | 0 | 0 | 0.0 | judge_assets✓ |
| J2 | closed | 0 | 0 | 0 | 0.0 | run_monolith✓ |
| RC | closed | 0 | 0 | 0 | 0.0 | reconcile_spec✓ |
| J3 | closed | 0 | 0 | 0 | 0.0 | reconcile_classified✓ |
| J4 | closed | 0 | 0 | 0 | 0.0 | judge_assets✓；j4_report✓ |
| J5 | closed | 0 | 0 | 0 | 0.0 | run_mutants✓ |
| J5C | closed | 0 | 0 | 0 | 0.0 | mutant_classes✓ |
| J6 | closed | 0 | 0 | 0 | 0.0 | freeze_judge✓ |
| A1 | closed | 3 | 0 | 0 | 107.2 | write_scope✓；tools_unchanged✓；judge_unchanged✓；blueprint_keys✓；gate_blueprint✓；judge_unchanged✓；rulings✓ |
| D1 | closed | 1 | 0 | 0 | 54.8 | write_scope✓；tools_unchanged✓；judge_unchanged✓；delta_assets✓；judge_unchanged✓ |
| I1 | closed | 2 | 0 | 0 | 76.2 | write_scope✓；tools_unchanged✓；judge_unchanged✓；gate_code✓；judge_unchanged✓ |
| I2a | closed | 1 | 0 | 0 | 4.5 | write_scope✓；tools_unchanged✓；judge_unchanged✓；template_part1✓ |
| I2b | closed | 1 | 0 | 0 | 5.8 | write_scope✓；tools_unchanged✓；judge_unchanged✓；template_part2✓ |
| I2c | closed | 1 | 0 | 0 | 3.8 | write_scope✓；tools_unchanged✓；judge_unchanged✓；gate_template✓ |
| I3 | closed | 1 | 0 | 0 | 7.8 | write_scope✓；tools_unchanged✓；judge_unchanged✓；gate_final✓；consistency_pass✓ |
| V1 | closed | 0 | 0 | 0 | 0.0 | check_adapter✓ |
| V2 | closed | 0 | 0 | 0 | 0.0 | run_judge✓ |
| V3 | closed | 1 | 0 | 0 | 17.8 | write_scope✓；tools_unchanged✓；judge_unchanged✓；verdict_delta✓；judge_unchanged✓ |
| B0 | closed | 0 | 0 | 0 | 0.0 | tag_b0✓ |
| O | closed | 1 | 0 | 0 | 70.8 | write_scope✓；tools_unchanged✓；judge_unchanged✓；s5_report✓；judge_unchanged✓ |
| C1 | closed | 0 | 0 | 0 | 0.0 | final✓ |
| C2 | in_progress | 0 | 0 | 0 | 0.0 |  |

## 异常处理

- C001-B0-review：ok → 继续

## 裁定

- R-A1-a3-1（m2s-architect，decided，migration-change）：七个受保护端点上的未登录/坏令牌步骤（no-header、no-space、bad-token）在目标上由谁应答、应答什么？ → 这些步骤不再进入函数：由 API Gateway 的 Cognito 授权器直接应答 401，响应体是 {"message":"Unauthorized"}，不再是单体的 {"error": ...}；单体三条 401 文案（缺 Authorization 头 / 头里没有空格分段 / 令牌无效或过期）合并成这一条，状态码仍是 401。响应头按 api_gateway.gateway_respon
…（截断，原文 392 字符）
- R-A1-a3-2（m2s-architect，decided，migration-change）：四个 /auth 端点（POST /auth/register、POST /auth/login、GET /auth/me、GET /auth/users）在目标上不提供，裁判在这些端点上的步骤怎么处理？ → 目标 API 上没有这四个路由（blueprint dropped_functions 里 type="endpoint" 的四条 + adapter_map.dropped_endpoints），裁判运行这些用例时跳过全部步骤，不比对响应；注册、登录、读取本人资料、列用户改由 Cognito User Pool API 承担（客户端用 Amplify/Cognito SDK 直连），身份准备由 
…（截断，原文 328 字符）
- R-A1-a3-3（m2s-architect，decided，contract）：POST /bookings 的 chargeId / outboundFlightId 规格记成 string，但单体不校验类型：非字符串值在目标上怎么处理？ → 照搬单体，不加类型校验：两个字段都只按"键是否存在"做必填校验，值不转成字符串、不做类型检查，按收到的原类型写入与读回。chargeId 的账本定位键沿用单体的类型标注语义（"值的 python 类型名 + ':' + str(值)"，DynamoDB 主键属性 chargeIdKey）：JSON number 12.5 收的款，用 GET /payments/12.5（路径参数永远是字符串 s:
…（截断，原文 469 字符）
- R-A1-a3-4（m2s-architect，decided，spec-gap）：POST /bookings 规格里那个可选字段 name 在目标上要不要读取、校验或回显？ → 不读取、不校验、不落库、不回显：单体 handler 只取 outboundFlightId / customerId / chargeId（以及 booking 自身字段），name 从未出现在读取路径里，带不带、带什么值都不影响状态码、响应体与写库内容。目标保持这一点：多出来的请求体键一律忽略，不因为未知键返回 400。规格把 name 标成 optional 只是记录请求体可能出现过它，不是
…（截断，原文 205 字符）

裁定共 4 条，其中人工裁定或确认 0 条，还在等人 0 条。

## 缺口清单

- 23 条：judge 13，none 10
