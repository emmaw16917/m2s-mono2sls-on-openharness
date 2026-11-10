# 最终报告：airline-full

生成时间：2026-11-04 05:48。由调度器按文件和事件生成。

## 最后一次检查

- gate_final：通过。gate(final) PASS
- judge_full：通过。run_judge PASS: {'cases': {'dropped': 9, 'migration-change': 7, 'pass': 35}, 'fault_runs': {'dropped': 3, 'pass': 23}}
- delta_full：通过。delta tests PASS: 25/25

## 每一层在什么环境下检验

- 裁判的预期（单体）：本机进程，DynamoDB 后端 ddblocal。
- 裁判（目标）：rie，后端 ddblocal，判定 PASS，代码 {'code': 'working tree', 'head': '604d1ce', 'uncommitted_output_changes': []}。
- 增量测试：rie，后端 ddblocal，判定 PASS（25/25），代码 {'code': 'working tree', 'head': '604d1ce', 'uncommitted_output_changes': []}。
- 故障行为基准 B0：rie，33 次运行。
- 这些都是本机检验：RIE 容器和 DynamoDB Local 或 dynalite，没有部署到 AWS。

## 裁判和强度

- 裁判 v1，2026-11-02T14:36:26Z 冻结：51 个用例（{'P': 48, 'R': 3}），26 个故障场景。
- 单体的行覆盖率：872/967（90.2%）。必须的响应 76/77 cited, 2 exempted，业务规则 92/116 cited。
- 变异检验：138 个变异体，抓住 129 个，等价 9 个，检出率 1.0；并行改串行的反向检查：{'serial': 'survived'}。

## 缺口

- 23 条：judge 13，none 10

## 保留的优化

- 改动请求 2 个：kept 1，pending_human 1
- 保留 C1（kept）：rule:R4

## 裁定

- 共 4 条，其中人工裁定或确认 0 条，还在等人 0 条。
- R-A1-a3-1（migration-change，decided）：七个受保护端点上的未登录/坏令牌步骤（no-header、no-space、bad-token）在目标上由谁应答、应答什么？ → 这些步骤不再进入函数：由 API Gateway 的 Cognito 授权器直接应答 401，响应体是 {"message":"Unauthorized"}，不再是单体的 {"error": ...}；单体三条 401 文案（缺 Authorization 头 / 头里没有空格分段 / 令牌无效或过期）合并成这一条，状
- R-A1-a3-2（migration-change，decided）：四个 /auth 端点（POST /auth/register、POST /auth/login、GET /auth/me、GET /auth/users）在目标上不提供，裁判在这些端点上的步骤怎么处理？ → 目标 API 上没有这四个路由（blueprint dropped_functions 里 type="endpoint" 的四条 + adapter_map.dropped_endpoints），裁判运行这些用例时跳过全部步骤，不比对响应；注册、登录、读取本人资料、列用户改由 Cognito User Pool AP
- R-A1-a3-3（contract，decided）：POST /bookings 的 chargeId / outboundFlightId 规格记成 string，但单体不校验类型：非字符串值在目标上怎么处理？ → 照搬单体，不加类型校验：两个字段都只按"键是否存在"做必填校验，值不转成字符串、不做类型检查，按收到的原类型写入与读回。chargeId 的账本定位键沿用单体的类型标注语义（"值的 python 类型名 + ':' + str(值)"，DynamoDB 主键属性 chargeIdKey）：JSON number 12.
- R-A1-a3-4（spec-gap，decided）：POST /bookings 规格里那个可选字段 name 在目标上要不要读取、校验或回显？ → 不读取、不校验、不落库、不回显：单体 handler 只取 outboundFlightId / customerId / chargeId（以及 booking 自身字段），name 从未出现在读取路径里，带不带、带什么值都不影响状态码、响应体与写库内容。目标保持这一点：多出来的请求体键一律忽略，不因为未知键返回 4

## 仍未解决

- 没有。
