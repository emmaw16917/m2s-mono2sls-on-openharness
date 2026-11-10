# 最终报告：bookstore-full

生成时间：2026-11-03 21:50。由调度器按文件和事件生成。

## 最后一次检查

- gate_final：通过。gate(final) PASS
- judge_full：通过。run_judge PASS: {'cases': {'dropped': 15, 'migration-change': 13, 'pass': 46}, 'fault_runs': {'dropped': 6, 'pass': 18}}
- delta_full：通过。delta tests PASS: 12/12

## 每一层在什么环境下检验

- 裁判的预期（单体）：本机进程，DynamoDB 后端 ddblocal。
- 裁判（目标）：rie，后端 ddblocal，判定 PASS，代码 {'code': 'working tree', 'head': 'dd0c2fe', 'uncommitted_output_changes': []}。
- 增量测试：rie，后端 ddblocal，判定 PASS（12/12），代码 {'code': 'working tree', 'head': 'dd0c2fe', 'uncommitted_output_changes': []}。
- 故障行为基准 B0：rie，20 次运行。
- 这些都是本机检验：RIE 容器和 DynamoDB Local 或 dynalite，没有部署到 AWS。

## 裁判和强度

- 裁判 v2，2026-11-02T16:24:58Z 冻结：74 个用例（{'P': 74, 'R': 0}），24 个故障场景。
- 单体的行覆盖率：584/611（95.6%）。必须的响应 63/66 cited, 3 exempted，业务规则 114/126 cited。
- 变异检验：93 个变异体，抓住 86 个，等价 7 个，检出率 1.0；并行改串行的反向检查：{'serial': 'survived'}。

## 缺口

- 18 条：delta 2，judge 8，none 8

## 保留的优化

- 改动请求 3 个：kept 2，kept_inconclusive 1
- 保留 C1（kept）：rule:R4
- 保留 C2（kept_inconclusive）：rule:R1
- 保留 C3（kept）：rule:R5

## 裁定

- 共 1 条，其中人工裁定或确认 0 条，还在等人 0 条。
- R-A1-a1-1（migration-change，decided）：受保护端点的未认证 / 无效令牌请求：401 的响应体由谁给出？（裁判在单体上实录的是 authMiddleware 的 {"error": ...}，目标上应答的是 API Gateway 的 Cognito 授权器） → 迁移后的对外结果：状态码仍是 401，表访问一次都不发生（与单体一致），响应头仍带 Access-Control-Allow-Origin: * 与 Access-Control-Allow-Credentials: true（由模板的 GatewayResponses 的 UNAUTHORIZED/AUTHORIZE

## 仍未解决

- 没有。
