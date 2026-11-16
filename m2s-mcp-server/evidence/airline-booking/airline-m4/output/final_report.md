# 最终报告：airline-m4

生成时间：2026-11-16 19:59。由调度器按文件和事件生成。

## 最后一次检查

- gate_final：通过。gate(final) PASS
- judge_full：通过。run_judge PASS: {'cases': {'dropped': 14, 'migration-change': 7, 'pass': 34}, 'fault_runs': {'dropped': 4, 'migration-change': 4, 'pass': 21, 'ruled': 2}}
- delta_full：通过。delta tests PASS: 16/16

## 每一层在什么环境下检验

- 裁判的预期（单体）：本机进程，DynamoDB 后端 ddblocal。
- 裁判（目标）：rie，后端 ddblocal，判定 PASS，代码 {'code': 'working tree', 'head': '49e68bb', 'uncommitted_output_changes': []}。
- 增量测试：rie，后端 ddblocal，判定 PASS（16/16），代码 {'code': 'working tree', 'head': '49e68bb', 'uncommitted_output_changes': []}。
- 故障行为基准 B0：rie，20 次运行。
- 这些都是本机检验：RIE 容器和 DynamoDB Local 或 dynalite，没有部署到 AWS。

## 裁判和强度

- 裁判 v1，2026-11-13T15:12:54Z 冻结：55 个用例（{'P': 53, 'R': 2}），27 个故障场景。
- 单体的行覆盖率：872/967（90.2%）。必须的响应 53/64 cited, 14 exempted，业务规则 104/133 cited。
- 变异检验：119 个变异体，抓住 113 个，等价 6 个，检出率 1.0；并行改串行的反向检查：{'serial': 'survived'}。

## 缺口

- 22 条：judge 15，none 7

## 保留的优化

- 改动请求 1 个：kept 1
- 保留 C1（kept）：把取消流程里两步尽力而为的补偿（放座、退款）并发化：退款那次条件写提交到模块级 ThreadPoolExecutor(10)，与本线程的『读航班 + 条件写放座』同时进行，在返回 200 之前 join（future.result()），失败处理、日志文本、refund 降级 dict 和响应体构造全部原地不变。

## 裁定

- 共 9 条，其中人工裁定或确认 0 条，还在等人 0 条。
- R-ROOM-spec-j3-r1-1-1-a1-1（contract，decided）：规格对账差异 register-read-back-by-admin/list/body.subset：GET /auth/users 响应中 users 数组的元素顺序是否属于对外契约（J1 把 2 个元素按下标钉死，单体返回次序相反）？ → 元素顺序不属于契约，其余全部仍是契约。具体判定口径：该步骤的 body.subset 对 `users` 数组按**元素多重集（含重数）**比较，先后顺序不参与判定；除此之外不放宽任何东西——status 仍须 200；`users` 仍须恰含 2 个元素（等长要求保留）；每个元素内部的字段名与取值仍逐字比较（cust
- R-ROOM-spec-j3-r1-1-2-a1-1（contract，decided）：规格对账差异 register-read-back-by-admin/list/body.subset 的执行收尾（承接 R-ROOM-spec-j3-r1-1-1-a1-1）：(1) 裁定的 affected_cases 该写 case#step 还是裸用例 id，对账与裁判能否认上；(2) relaxation=" → 两条都是执行口径，不改变 R-ROOM-spec-j3-r1-1-1-a1-1 的判定内容。

(1) affected_cases 保留 "register-read-back-by-admin#list"，**不改成裸用例 id**。裁判（run_judge）读裁定时按 `#` 分区成用例与步骤：带步骤的条目只把放
- R-A1-a2-1（migration-change，decided）：受保护端点的未认证请求（缺 Authorization 头 / 头格式不是 "Bearer <t>" / 令牌伪造或过期）在目标上由谁应答、响应体是什么形状 → 由 API Gateway 的 Cognito/JWT 授权器应答：状态码仍是 401，响应体是网关默认形状（HTTP API：{"message": "..."}），不再是单体的 {"error": "Authentication required. Please provide a valid token."} /
- R-A1-a2-2（migration-change，decided）：/auth/register、/auth/login、/auth/me、/auth/users 四个端点在目标上不提供，涉及它们的用例与身份准备怎么算 → 四个端点整体作废（迁移决策，不是缺陷）：目标不注册这些路由，裁判不为它们出题、也不在目标上运行以它们为步骤的用例；身份改由 adapter_map.identities 的形状注入（claims.sub = ${id}，${id} 就是单体侧 POST /auth/login 保存的 body.user.sub：cus
- R-A1-a2-3（contract，decided）：列表端点（Scan 结果数组）的元素顺序是否属于对外契约；register-read-back-by-admin#list 的 needs_ruling 结论 → 列表响应的元素顺序不是对外契约：GET /flights/search 的 flights、GET /customers/<customer_id>/bookings 的 bookings（以及已删除的 GET /auth/users 的 users）按 Scan 返回顺序原样给出，代码不排序；比较这些数组时只要求元素
- R-A1-a2-4（contract，decided）：A1 自己新落的 R-A1-a2-3（列表顺序放宽）与已在案的 R-ROOM-spec-j3-r1-1-1-a1-1 / R-ROOM-spec-j3-r1-1-2-a1-1 重复，且它的 affected_cases 多写了 customer-bookings-status-filter、search-route-m → 以在案的两条 ROOM 裁定为准，R-A1-a2-3 不作新增放宽、其 affected_cases 中多出的两项一律作废。准确范围：顺序放宽只施加于 register-read-back-by-admin#list 这一步（按 # 切分，只影响该步骤），判定口径仍是「等长 + 元素多重集（含重数）+ 每个元素字段逐字
- R-ROOM-spec-j3-r2-1-1-a1-1（contract，decided）：规格对账差异 register-read-back-by-admin/list/body.subset（J3 第 2 轮）：GET /auth/users 的 users 数组元素顺序按什么机制放宽——是否要求验证线改写用例、是否写成挂在事实条目上的通用规则、affected_cases 用裸用例 id 还是 case → 判定内容不变（顺序不是契约，其余全是契约），三条执行口径如下：

(1) **由裁判在 R 层做无序多重集比较，验证线不重写用例**。不需要新增断言词、不允许把 body.subset 降级成 has_fields:["users"]。机制：run_judge 里命中 relaxation=order 的裁定会把该步骤
- R-ROOM-spec-j3-r2-1-2-a1-1（contract，decided）：规格对账差异 register-read-back-by-admin/list/body.subset 的结室口径（J3 第 2 轮）：次序放宽实际在哪一层生效、对账侧"已被裁定覆盖"按什么粒度认账、auth 作废类裁定的裸用例 id 要不要加防外溢边界？（承接 R-ROOM-spec-j3-r2-1-1-a1-1 与 → 本差异按在案口径关闭，判定内容一字不改（category=contract、relaxation=order、只放开 R 层、影响面仅 register-read-back-by-admin#list）；本轮补三条执行边界，供 J3 重跑对账与 J4 补强引用：

(1) **放宽只在"目标 vs 单体记录"这一层由比
- R-C003-V2-triage-a1-1（implementation-trace，decided）：白盒故障场景 wb-booking-handler-read-fails-after-authz（nth=2）、wb-booking-confirm-read-fails-after-authz（nth=2）、wb-booking-cancel-read-fails-in-service（nth=3）按「同一条预订的第 → 是实现轨迹，不作 P 层判定。目标在这三条注入涉及的 7 个步骤上不再判失败（其余步骤、其余用例、其它故障场景一律仍严格比对）：booking-read-guards#owner、booking-read-guards#other、booking-confirm-unconfirmed#confirm、booking-

## 仍未解决

- 没有。
