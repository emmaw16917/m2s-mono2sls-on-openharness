---
name: m2s-performance-kb
description: Serverless 迁移中常见性能反模式的参考规则（批量 API、依赖精简）。m2s-code-developer 写完代码后自我对照时加载；后续 m2s-perf-optimizer 落地后也会复用。
user-invocable: true
disable-model-invocation: false
---

# Serverless 性能参考规则（迁移场景）

这份文档是**写代码时的自我对照参考**，不是新的校验项——`gate(code)`/`gate(template)` 的判定
范围不因为读了这份文档而改变。目的是让第一版代码就少踩性能坑，减少后续需要专门修正的量。

---

## 1. 批量 API 模式（最容易被忽视的一处）

原单体代码里很常见的写法是"循环里对同一张表逐条查/写"——这种模式直接照搬进 Lambda handler，
会把一次业务操作变成 N 次串行的 DynamoDB 往返，延迟随数据量线性增长。迁移时要识别这种模式，
改写成批量 API。

### 识别信号（在原单体代码或你刚写出来的 handler 里看到就要改）

```javascript
// ❌ 反模式：循环里逐条 get
for (const id of itemIds) {
    const item = await dynamoDB.send(new GetCommand({ TableName, Key: { id } }));
    results.push(item.Item);
}
```

```python
# ❌ 反模式：循环里逐条 put
for order in orders:
    table.put_item(Item=order)
```

### Python（boto3）—— 批量读用 `batch_get_item`，批量写用 `Table.batch_writer()`

```python
import boto3

dynamodb = boto3.resource('dynamodb')
table = dynamodb.Table(os.environ['TABLE_NAME'])

# 批量读（跨表也可以，这里演示单表）——注意批量读的 API 挂在 resource 上，不是 Table 上
response = dynamodb.batch_get_item(
    RequestItems={
        os.environ['TABLE_NAME']: {'Keys': [{'id': i} for i in item_ids]}
    }
)
items = response['Responses'][os.environ['TABLE_NAME']]
# DynamoDB 单次 BatchGetItem 最多 100 个 key，超过要自己分批循环调用（分批调用 API 不算反模式，
# 分批调用单条 API 才是）

# 批量写——用 batch_writer() 上下文管理器，自动分批 + 自动重试未处理项，
# 比手写 batch_write_item 更省心，是 boto3 官方推荐写法
with table.batch_writer() as batch:
    for order in orders:
        batch.put_item(Item=order)

# 需要跨表原子性（要么全部成功要么全部失败）时用 TransactWriteItems，不是循环 put_item
dynamodb.meta.client.transact_write_items(
    TransactItems=[
        {'Put': {'TableName': orders_table, 'Item': order_item}},
        {'Update': {'TableName': inventory_table, 'Key': {'sku': sku},
                     'UpdateExpression': 'SET stock = stock - :qty',
                     'ExpressionAttributeValues': {':qty': qty}}},
    ]
)
```

### Node.js（AWS SDK v3, CommonJS）

```javascript
const {
    DynamoDBClient
} = require('@aws-sdk/client-dynamodb');
const {
    DynamoDBDocumentClient, BatchGetCommand, BatchWriteCommand, TransactWriteCommand
} = require('@aws-sdk/lib-dynamodb');

const ddbClient = new DynamoDBClient({});
const dynamoDB = DynamoDBDocumentClient.from(ddbClient);
const TABLE_NAME = process.env.TABLE_NAME;

// 批量读（单次最多 100 个 key，超过要分批调用 BatchGetCommand，不要退回逐条 GetCommand）
const { Responses } = await dynamoDB.send(new BatchGetCommand({
    RequestItems: { [TABLE_NAME]: { Keys: itemIds.map(id => ({ id })) } }
}));
const items = Responses[TABLE_NAME];

// 批量写（单次最多 25 个操作，超过要分批）
await dynamoDB.send(new BatchWriteCommand({
    RequestItems: {
        [TABLE_NAME]: orders.map(order => ({ PutRequest: { Item: order } }))
    }
}));

// 需要跨表原子性时用 TransactWriteCommand，不是循环 PutCommand
await dynamoDB.send(new TransactWriteCommand({
    TransactItems: [
        { Put: { TableName: ordersTable, Item: orderItem } },
        { Update: { TableName: inventoryTable, Key: { sku },
                     UpdateExpression: 'SET stock = stock - :qty',
                     ExpressionAttributeValues: { ':qty': qty } } },
    ]
}));
```

> **规则**：看到"循环 + 单条 get/put/update/delete"就要判断能不能合并成
> `BatchGetItem`/`batch_writer`/`BatchWriteCommand`/`TransactWriteItems`。批量 API 本身
> 单次也有上限（读 100 项、写 25 项），超过上限要分批调用批量 API，**分批调用批量 API 不是
> 反模式，分批调用单条 API 才是**——区别在于每次网络往返处理的是一批数据还是一条数据。

---

## 2. 依赖精简（跟 cold start 直接相关）

Lambda 的 INIT 阶段（模块加载 + 顶层代码执行）耗时跟依赖体积正相关——依赖越重、`node_modules`
越大，部署包解压和模块解析越慢。写完代码后检查：

- **只 import/require 真正用到的模块**，不要因为"原单体里有这个工具函数"就把整个工具库
  （比如只用了 `lodash.get` 却 `require('lodash')` 整个包）搬过来。
- **不要引入原单体里为了兼容旧代码而加的 polyfill/兼容层**——迁移到 Node.js 22.x/Python 3.12
  这类现代运行时，原单体里针对旧版本运行时打的补丁通常已经不需要了。
- Python：能用运行时内置模块（`json`/`datetime`/`re` 等）解决的，不要为了图方便引入第三方
  等价库。
- 这一条跟现有 `m2s-lambda-coding-kb` 第 11 节（依赖文件卫生，`gate(code)` C10 会查）是不同
  维度——C10 查的是"依赖文件里有没有写不该写的东西"，这里说的是"写进依赖文件里的东西是不是
  真的需要这么重"，C10 查不出来，靠这条自我对照。

---

## 3. 后续会补充的内容（占位，暂不适用于 S2 阶段）

本地实测方法（`sam local invoke` 测 cold start、`sam local start-api` 测 E2E Latency 等）
留给 `m2s-perf-optimizer` 落地时再补进这份文档——那部分需要完整的 `template.yaml`，S2 阶段
用不上，暂不在这里展开。
