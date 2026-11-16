STAGE_ID: I2a

这是 m2s 迁移流水线的一个任务，由 v3 调度器派发；你的角色说明已经在系统提示词里。这是这个任务第 2 次尝试。

## 绝对路径
- MONOLITH = D:\Emma\MyLab\Mono2SLs\Mono2Sls-artifacts\mono_benchmark\bookstore
- WS = D:\Emma\MyLab\Mono2SLs\Mono2Sls-artifacts\M2S\runs\bookstore-m4

## 任务
这一步是角色说明里的 STAGE_ID=S3a：写 output/template.part1.yaml（AWSTemplateFormatVersion 到 Globals、Parameters、共享资源，以第一次出现的 "# --- END METADATA ---" 结束）。

需要知道"要保持哪些行为"时，只引用规格本身：analysis_report.json 的出处、blueprint.json 的字段（包括 metadata.spec_resolutions）。

## 回炉信息（调度器转来的，请据此修正）

### 1. 来自 checks
上一次尝试没有调用 submit_task 就结束了（可能只回复了文字，或者用完了轮数）。这次完成后一定要调用 submit_task。

上一次的工作报告全文：
{
  "stage_id": "I2a",
  "agent": "m2s-sam-engineer",
  "status": "success",
  "attempt": 1,
  "summary": "按 blueprint 写了 output/template.part1.yaml：AWSTemplateFormatVersion/Transform/Description、Parameters(Environment)、Globals.Function(只有 Runtime nodejs22.x / Timeout 30 / MemorySize 512 / Architectures arm64，没有 Handler、没有 Environment)、共享资源 REST API(BookstoreApi：StageName、EndpointConfiguration REGIONAL、Cors 三字段、AccessLogSetting、Cognito DefaultAuthorizer、七类 GatewayResponses 的头)、ApiLogGroup、CognitoUserPool(+Client，密码策略五项全写)、五张 DynamoDB 表(BooksTable 带 category-index GSI；IdempotencyTable 带 TimeToLiveSpecification expiresAt)、BestsellerUpdateQueue+DLQ(VisibilityTimeout 300 / maxReceiveCount 3)、SharedLayer(layers/shared/nodejs/ + BuildMethod nodejs22.x)，末尾以第一次出现且唯一出现的 '# --- END METADATA ---' 结束，并在块里额外记下 S3b 需要的逻辑名(api_gateway_logical_name / shared_layer_logical_names / cognito 池名)。另在 storage/DECISIONS.md 追加一节 '实现附记（I2a / S3a）'（未改动既有 D-1…D-41）。",
  "produced_files": [
    "output/template.part1.yaml",
    "storage/DECISIONS.md (append: 实现附记 I2a/S3a 一节)",
    "storage/stage_reports/I2a.json"
  ],
  "not_produced_here": [
    "output/template.part2.yaml（12 个 AWS::Serverless::Function 资源块、函数事件、Outputs）属于 I2b/S3b；本任务不写函数资源，也不读 output/lambdas 下的 handler 代码，CodeUri/Handler 的真实值由 S3b 按磁盘确认。"
  ],
  "design_notes_from_spec": {
    "api_type": "REST（api_gateway.api_type='rest'，rest_features=[gateway_responses, cognito_user_pools_authorizer]）；S3a 侧落成一个 AWS::Serverless::Api，客户端 clients='same_region' → EndpointConfiguration Type: REGIONAL（AT2）。HTTP API / HttpApi 一个都没写。",
    "authentication": "cognito.enabled=true → UserPool + UserPoolClient；授权器在 Api 资源上设 DefaultAuthorizer，11 个 auth=required 端点继承（blueprint 里没有任何 auth='none' 端点，所以 S3b 不需要 Authorizer: NONE 覆盖）。PasswordPolicy 五项全写以匹配 cognito.password_policy（gate C14 逐条比对：MinimumLength 6 / RequireNumbers true / 其余 false）；无 LambdaConfig、无 OAuth/Hosted UI。",
    "database": "keys 全部照搬 analysis_report.dynamodb_info.table_schemas（init-db.js:72/74/82/118/120/150/152/228/230），AttributeDefinitions 只列键属性；BooksTable 的 category-index 是 HASH-only + ProjectionType ALL；Bookstore-Users 不建表（adapter_map 里 target=null/replaced_by=cognito）。",
    "idempotency_table": "dynamodb_tables[4]（target_only，purpose 写明去重）按 m2s-sam-kb 'Idempotency Table + Partial Batch Response' 生成，带 TimeToLiveSpecification AttributeName: expiresAt / Enabled: true（D-3 Pattern A 的 7 天标记 > 队列 4 天保留期）。",
    "async": "sqs_queues[0] → BestsellerUpdateQueue（VisibilityTimeout 300、RedrivePolicy → BestsellerUpdateQueueDLQ、maxReceiveCount 3）；BatchSize/FunctionResponseTypes 属事件源，留给 S3b（blueprint 写明 report_batch_item_failures=true）。eventbridge_rules=[] / s3_buckets=[] / lambda_invoke_permissions=[] → 模板里没有这三类资源。",
    "global_response_headers": "见 DECISIONS.md 实现附记的 'GatewayResponses 的头' 一节：DEFAULT_4XX/DEFAULT_5XX 写全 global_middleware[0] 的六个头名（GM2 按名字逐个查，任何 ERROR 会让 gate(final) 判 FAIL）；授权器那一臂（UNAUTHORIZED/ACCESS_DENIED/MISSING_AUTHENTICATION_TOKEN/AUTHORIZER_*）只写 api_gateway.gateway_responses.headers 的三项，与 R-A1-a3-1 '网关 401 的头与单体记录一致' 对齐，且裁判工具模拟网关 401 时优先读 UNAUTHORIZED。",
    "tracing_and_throttling": "不写 Globals.Function.Tracing、也不写 Api 的 TracingEnabled（api_gateway.api_type_reason：本应用不用 X-Ray）；不写 MethodSettings/限流（api_gateway.throttling：加了会凭空多出 429）。"
  },
  "self_check": {
    "marker": "文件末尾是 '# --- END METADATA ---'，全文只出现一次（grep -c = 1），S3c 的切割锚点可用；已用 m2s_merge.merge_template 在临时目录拼桩 part2 验证合并结果解析正常（top keys 顺序 AWSTemplateFormatVersion…Resources 后接 part2 的资源与 Outputs）。",
    "mechanical_checks_run_locally_on_this_part": "用工作区的 gates.py（只读，未调用任何 mcp__m2s-server__ 工具、未改动流水线代码）跑：check_gm_template=[]、check_c14=[]、check_api_type_template=[]、check_c13=[]、check_c9=[]；check_c8 只有两条 '...has no matching template Function'，那两条要等 I2b 的 Function 资源存在才可能消掉，不是 part1 的缺陷。",
    "sam_build_layer_probe": "临时目录里单独 build 这个 LayerVersion：'sam build' 成功，产物 build/SharedLayer/nodejs/shared.js，即运行时 /opt/nodejs/shared.js（package.json 不存在只打印一条 warning，不影响构建）——与 I1-shared 附记里 handler require 的路径一致，也满足 C13。",
    "cfn_lint": "临时目录里把 part1 与一个桩 part2（一个函数 + Outputs）合并后跑 cfn-lint：rc=0、无 finding。最终 template.yaml 的正式 validate_sam 由 S3c 负责。"
  },
  "issues": [
    {
      "severity": "spec-tension",
      "topic": "GM2 与 D-9 第 3 条的口径差：网关自答响应要带的头名比 blueprint 的 gateway_responses.headers 多四个",
      "detail": "gates._contract_headers 返回 global_middleware[0].headers 的**全部六个**名字（含单体只在 OPTIONS 预检上发的 Access-Control-Allow-Methods / Access-Control-Allow-Headers / Vary / Content-Length），而 GM2 要求这六个名字都出现在 DEFAULT_4XX 与 DEFAULT_5XX 上（按名字查，不看值）；blueprint.api_gateway.gateway_responses.headers 只列了三项，D-9 第 3 条禁止的又是**函数**在真实响应里带这四项。只写三项会得到 2 条 GM2 ERROR，而 gate(final) 对任何 ERROR 判 FAIL，所以模板按 GM2 的字面要求写全六个名字：GatewayResponses 只改写 API Gateway 自己生成的响应，永远碰不到函数返回的业务响应，因此 D-9 第 1/3 条（真实响应只带那两个头）仍然成立。Content-Length 的字面量取单体预检那一臂的值（0 = 空体），真实部署时 API Gateway 自己按响应体重算；规格里被要求逐符号比较的头只有 api_gateway.function_response_headers 记的那两个 CORS 头 + Content-Type。若有人要收紧成 blueprint 的三项，需要的是对 GM2 的一条裁定（或让 S0 单独标注'只发预检的头'），不是改函数代码。本阶段没有改动任何流水线工具。",
      "suspected_module": "A1"
    },
    {
      "severity": "note",
      "topic": "共享层资源归 part1，S3b 必须引用",
      "detail": "blueprint 没有 layers 字段，但 output/layers/shared/nodejs/shared.js 真实存在，且 I1-shared 附记明确 '模板侧（S3）需要 ContentUri: layers/shared/nodejs/ + Metadata.BuildMethod: nodejs22.x'。它是被 12 个函数共享的构件，所以写在本 part（逻辑名 SharedLayer，已写进 SHARED_RESOURCES_METADATA 的 shared_layer_logical_names）。S3b 必须在每个函数上加 Layers: [!Ref SharedLayer]，否则 GM1 依赖的响应头/请求解析 helper 在运行时不存在（本地 selftest 的 harness 也是按 build/SharedLayer 这个目录名找的）。如果 S3b 又自己写了一份 LayerVersion，S3c 合并时会出现同名重复，需要保留 part1 这一份。",
      "suspected_module": ""
    },
    {
      "severity": "note",
      "topic": "物理名与本地验证的关系",
      "detail": "表/队列/池都给了 TableName/QueueName/UserPoolName/ClientName 物理名（m2s-sam-kb 参考写法），前缀来自 Parameters.Environment（默认 dev）与 AWS::StackName。本地裁判/验证按**逻辑名**建表与解析环境变量（verify.Model：tables 以逻辑 id 为键，!Ref BooksTable 解析成 BooksTable），所以物理名只影响真实部署，不影响任何测试；S3b 的环境变量请照 blueprint 写 !Re
…（截断，原文 6892 字符）

## 完成方式
1. 写工作报告 WS/storage/stage_reports/I2a.json（格式按你的角色说明）。
2. 调用 mcp__m2s-server__submit_task(summary=...) 提交，然后结束会话。不需要再输出 STAGE_COMPLETE。
3. 做不下去时，调用 mcp__m2s-server__report_blocked(reason, evidence, suspected_module)，然后结束会话；认为是更早阶段的问题时，在 suspected_module 写那个阶段（例如 S2），并在 evidence 里给出证据。
4. 调度器会自己运行这个任务的检查（template_part1），你的自述不作为判定依据。

## 注意
- 如果调用工具或等待过程中遇到网络错误（例如 'Network error: Connection error'），重试 1-2 次再继续，不要因为一次网络错误就直接放弃或提前结束。
- 只改这个任务范围内的文件：output/template.part1.yaml、storage/DECISIONS.md、storage/stage_reports/I2a.json。范围之外的改动会被撤销，这次尝试判为失败。
- 不要修改流水线自己的工具：m2s-mcp-server/、.openharness/、~/.openharness/ 下的文件。
- 不要读写 WS/coord/（调度器的内部状态）。不要调用 mcp_auth。
- 不要读别的工作区（WS 所在 runs/ 目录下的其他目录、runs-prev-*），也不要参照它们的产物。
- 这份说明可以随时用 mcp__m2s-server__get_task_context 重新取回。