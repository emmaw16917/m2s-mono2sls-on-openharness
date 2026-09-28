---
name: m2s-async-patterns-kb
description: SQS/EventBridge 等异步与事件驱动模式的选型决策树和配置规则，用于 blueprint 中 has_async_flows=true 的跨 Lambda 调用场景。m2s-architect 做同步/异步决策、m2s-code-developer 写异步代码、m2s-validator 校验 C8/C9 时加载。
user-invocable: true
disable-model-invocation: false
---

# Async & Event-Driven Serverless Patterns

This document covers asynchronous communication patterns for serverless migrations.
Use these patterns when the architecture blueprint identifies async candidates
via cross-Lambda call graph analysis.

---

## Decision Flowchart: Sync vs Async

For each cross-Lambda call relationship identified in `entry_point_dependencies`:

```
Q1: Does the caller NEED the callee's return value for its HTTP response?
  │
  ├─ YES → SYNCHRONOUS Lambda Invoke (RequestResponse)
  │
  └─ NO → Q2: Does this action trigger operations in 2+ different service domains?
           │
           ├─ YES → EventBridge (fan-out / event-driven)
           │
           └─ NO → SQS (point-to-point async)
```

---

## Pattern 1: SQS — Point-to-Point Async

**When to use**:
- One producer, one consumer
- The caller does NOT need the callee's return value
- The callee performs a side-effect that can tolerate seconds/minutes of delay
- Examples: counter updates, email/notification sending, log enrichment, image processing

**Key configuration**:
- `VisibilityTimeout`: must be ≥ consumer Lambda timeout (SQS default: 30s; recommend ≥ 60s)
- Dead-Letter Queue (DLQ): always configure with `maxReceiveCount: 3`
- `BatchSize`: 1–10,000 messages per invocation for Standard queues (1–10 for FIFO); default: 10

### Producer Code (in API Lambda)

```python
import boto3, json, os

sqs = boto3.client('sqs')

def lambda_handler(event, context):
    # ... process main request ...
    order_id = create_order(...)

    # Send message to SQS instead of calling function directly
    sqs.send_message(
        QueueUrl=os.environ['BESTSELLER_QUEUE_URL'],
        MessageBody=json.dumps({
            'orderId': order_id,
            'books': purchased_books
        })
    )

    # Return HTTP response after SQS message is confirmed sent
    return {
        'statusCode': 200,
        'body': json.dumps({'orderId': order_id})
    }
```

```javascript
// Node.js (CommonJS) — ⚠️ MUST await the send call
const { SQSClient, SendMessageCommand } = require('@aws-sdk/client-sqs');
const sqsClient = new SQSClient({});

exports.handler = async (event) => {
    const orderId = await createOrder(/* ... */);

    // ⚠️ MUST await — Lambda freezes execution after handler returns.
    // Non-awaited async sends (.then().catch() without await, or fire-and-forget
    // promises) may be silently dropped, causing message loss.
    await sqsClient.send(new SendMessageCommand({
        QueueUrl: process.env.BESTSELLER_QUEUE_URL,
        MessageBody: JSON.stringify({ orderId, books: purchasedBooks })
    }));

    return { statusCode: 200, body: JSON.stringify({ orderId }) };
};
```

### Consumer Lambda (SQS-triggered)

```python
import json

def lambda_handler(event, context):
    for record in event['Records']:
        payload = json.loads(record['body'])
        process_bestseller_update(payload['books'])

    # No HTTP response format needed (no statusCode/headers/body)
    # Return value is ignored by SQS trigger
```

### SAM Template

```yaml
# Queue + DLQ
BestsellerUpdateQueue:
  Type: AWS::SQS::Queue
  Properties:
    QueueName: !Sub '${AWS::StackName}-bestseller-updates'
    VisibilityTimeout: 60
    RedrivePolicy:
      deadLetterTargetArn: !GetAtt BestsellerUpdateDLQ.Arn
      maxReceiveCount: 3

BestsellerUpdateDLQ:
  Type: AWS::SQS::Queue
  Properties:
    QueueName: !Sub '${AWS::StackName}-bestseller-updates-dlq'

# Consumer Lambda (SQS-triggered)
UpdateBestsellersFunction:
  Type: AWS::Serverless::Function
  Properties:
    CodeUri: lambdas/orders/update-bestsellers/
    Handler: handler.lambda_handler
    Events:
      SQSEvent:
        Type: SQS
        Properties:
          Queue: !GetAtt BestsellerUpdateQueue.Arn
          BatchSize: 10
    Policies:
      - SQSPollerPolicy:
          QueueName: !GetAtt BestsellerUpdateQueue.QueueName

# Producer Lambda needs SQSSendMessagePolicy + env var
OrderCheckoutFunction:
  Type: AWS::Serverless::Function
  Properties:
    # ... standard Api Event ...
    Policies:
      - SQSSendMessagePolicy:
          QueueName: !GetAtt BestsellerUpdateQueue.QueueName
    Environment:
      Variables:
        BESTSELLER_QUEUE_URL: !Ref BestsellerUpdateQueue
```

---

## Pattern 2: EventBridge — Fan-Out / Event-Driven

**When to use**:
- One event triggers multiple independent consumers across different service domains
- Semantic: "something happened" (event notification), not "do this task" (command)
- Cross-domain decoupling: producer doesn't know/care about consumers
- Event chains: each step emits a "completed" event triggering the next step
- Examples: order completed → loyalty points + notification + analytics

**Key configuration**:
- Use the default event bus (no custom bus needed for most cases)
- Event pattern matching on `source` + `detail-type`
- Each consumer needs `AWS::Lambda::Permission` for EventBridge invocation

### Producer Code (in API Lambda)

```python
import boto3, json, os

events = boto3.client('events')

def lambda_handler(event, context):
    # ... process main request ...
    order = complete_order(...)

    # Publish event — multiple consumers will react independently
    events.put_events(Entries=[{
        'Source': 'app.orders',
        'DetailType': 'OrderCompleted',
        'Detail': json.dumps({
            'orderId': order['id'],
            'userId': order['userId'],
            'totalAmount': order['total']
        })
    }])

    return {
        'statusCode': 200,
        'body': json.dumps({'orderId': order['id'], 'status': 'completed'})
    }
```

```javascript
// Node.js (CommonJS) — ⚠️ MUST await the put_events call
const { EventBridgeClient, PutEventsCommand } = require('@aws-sdk/client-eventbridge');
const ebClient = new EventBridgeClient({});

exports.handler = async (event) => {
    const order = await completeOrder(/* ... */);

    await ebClient.send(new PutEventsCommand({
        Entries: [{
            Source: 'app.orders',
            DetailType: 'OrderCompleted',
            Detail: JSON.stringify({
                orderId: order.id,
                userId: order.userId,
                totalAmount: order.total
            })
        }]
    }));

    return { statusCode: 200, body: JSON.stringify({ orderId: order.id }) };
};
```

### Consumer Lambda (EventBridge-triggered)

```python
import json

def lambda_handler(event, context):
    detail = event['detail']
    user_id = detail['userId']
    order_id = detail['orderId']

    add_loyalty_points(user_id, order_id)

    # No HTTP response format needed
```

### SAM Template

```yaml
# EventBridge Rule
OrderCompletedRule:
  Type: AWS::Events::Rule
  Properties:
    EventPattern:
      source:
        - "app.orders"
      detail-type:
        - "OrderCompleted"
    Targets:
      - Arn: !GetAtt ProcessLoyaltyFunction.Arn
        Id: "ProcessLoyalty"
      - Arn: !GetAtt SendNotificationFunction.Arn
        Id: "SendNotification"

# Permission for EventBridge to invoke each target Lambda
ProcessLoyaltyEventPermission:
  Type: AWS::Lambda::Permission
  Properties:
    FunctionName: !Ref ProcessLoyaltyFunction
    Action: lambda:InvokeFunction
    Principal: events.amazonaws.com
    SourceArn: !GetAtt OrderCompletedRule.Arn

SendNotificationEventPermission:
  Type: AWS::Lambda::Permission
  Properties:
    FunctionName: !Ref SendNotificationFunction
    Action: lambda:InvokeFunction
    Principal: events.amazonaws.com
    SourceArn: !GetAtt OrderCompletedRule.Arn

# Producer Lambda needs EventBridgePutEventsPolicy
OrderCheckoutFunction:
  Type: AWS::Serverless::Function
  Properties:
    # ... standard Api Event ...
    Policies:
      - EventBridgePutEventsPolicy:
          EventBusName: default
```

---

## Pattern 3: Synchronous Lambda Invoke (Keep When Needed)

**When to use**:
- The caller MUST wait for the callee's result to construct its own HTTP response
- Data consistency requires atomic/synchronous execution
- Examples: payment charge → order confirmation, inventory check → availability

```python
import boto3, json, os

lambda_client = boto3.client('lambda')

response = lambda_client.invoke(
    FunctionName=os.environ['PROCESS_PAYMENT_FUNCTION_NAME'],
    InvocationType='RequestResponse',
    Payload=json.dumps({'chargeId': charge_id, 'amount': total})
)
result = json.loads(response['Payload'].read())
```

---

## Consumer Lambda Design Notes

- SQS consumers receive `event['Records']` — iterate and parse `record['body']`
- EventBridge consumers receive `event['detail']` directly
- Neither type returns API Gateway response format (no `statusCode`/`headers`/`body`)
- Consumer Lambdas have `trigger_type` of `"sqs"` or `"eventbridge"` in the blueprint
- Consumer Lambdas do NOT have `entry_points` (they are not API-triggered)
- Consumer Lambda `source_files` come from the callee module in the original monolith

## Error Handling Best Practices

- **SQS**: Failed messages retry automatically (up to `maxReceiveCount`), then go to DLQ
- **EventBridge**: Failed invocations can be configured with retry policy and DLQ on the rule target
- **Both**: Consumer Lambdas should be idempotent (safe to process the same message twice)

---

## REQUIRED correctness rule: consumers must be idempotent under redelivery

This is a **correctness rule, not an optimization** (PIPELINE_REDESIGN.md §2.4). It applies from v0 on to every SQS- or EventBridge-triggered consumer.

**Why this comes from the migration itself.** In the monolith, a fire-and-forget side effect (for example `updateBestSellers(books)` called without `await`) ran **once**, inside the same process: it could be lost on a crash, but never applied twice. Once the architect turns it into a message plus a consumer, delivery becomes **at-least-once**:
- SQS standard queues may deliver the same message more than once;
- if the consumer times out or crashes mid-batch, the **whole batch** becomes visible again and is reprocessed, including the messages that had already been applied;
- EventBridge targets are retried as well.

A consumer that is not idempotent therefore makes the migrated system *less* correct than the monolith. Typical symptoms are double-counted `ADD` counters and duplicated audit events.

**The rule.** Processing the same message twice must leave exactly the same final state as processing it once.

1. **Idempotency key.** Use a stable business key carried in the message body, such as `orderId`. If the key is not there, the producer must add it.
   - Do not rely on SQS `messageId` alone: a redelivery keeps the same `messageId`, but if the producer retries `SendMessage` itself, the resend gets a new one.
2. **Writes that are already idempotent need nothing extra:**
   - `PutItem` of a complete item whose key is deterministic (not a freshly generated UUID);
   - `DeleteItem`;
   - `UpdateItem` that only `SET`s absolute values.
3. **Writes that are not idempotent need a dedupe mechanism.** These include `ADD` / `list_append` updates, `PutItem` with a generated key, and sending further messages. Choose one pattern and write the choice into the blueprint (the function's `notes`) and into `DECISIONS.md`:

   **Pattern A (preferred): an atomic dedupe marker plus the effect, in one `TransactWriteItems`.**
   ```javascript
   const { TransactWriteCommand } = require('@aws-sdk/lib-dynamodb');
   // 1. Merge entries that target the same item FIRST. DynamoDB rejects a transaction
   //    containing two operations on one item (ValidationException "Transaction request
   //    cannot include multiple operations on one item"), and an order may list the
   //    same book twice -- without merging, the whole message fails and is lost.
   const qtyByBook = new Map();
   for (const b of books) qtyByBook.set(b.bookId, (qtyByBook.get(b.bookId) || 0) + b.quantity);
   // 2. marker item: pk = "<consumer>#<idempotency key>", expiresAt = TTL (epoch seconds)
   try {
     await docClient.send(new TransactWriteCommand({ TransactItems: [
       { Put: { TableName: process.env.IDEMPOTENCY_TABLE,
                Item: { pk: `update-bestsellers#${orderId}`, expiresAt: Math.floor(Date.now() / 1000) + 7 * 86400 },
                ConditionExpression: 'attribute_not_exists(pk)' } },
       ...[...qtyByBook].map(([bookId, q]) => ({ Update: { TableName: process.env.BESTSELLERS_TABLE, Key: { bookId },
                UpdateExpression: 'ADD salesCount :q', ExpressionAttributeValues: { ':q': q } } })),
     ] }));
   } catch (err) {
     const dup = err.name === 'TransactionCanceledException' && err.CancellationReasons?.[0]?.Code === 'ConditionalCheckFailed';
     if (!dup) throw err;            // real failure: let the message be retried (see "failure reporting")
     // duplicate delivery: already applied -> success, nothing to do
   }
   ```

   The same in Python (boto3). This version also carries the per-chunk markers and the conflict retry described below; it has been run against DynamoDB Local with duplicate deliveries, a duplicate key in one message, a 100-item message split into two chunks, a redelivery after only the first chunk committed, and injected conflicts:
   ```python
   import json, os, random, time
   import boto3
   from botocore.exceptions import ClientError

   ddb = boto3.client("dynamodb")  # transactions are a client operation; clients are thread-safe, resources are not
   CONSUMER = "update-bestsellers"
   MAX_EFFECTS = 99                # 100 items per transaction, one of them the chunk's own marker
   MAX_RETRIES = 3
   RETRYABLE = {"ProvisionedThroughputExceededException", "ThrottlingException", "RequestLimitExceeded",
                "InternalServerError", "ServiceUnavailable", "TransactionConflictException"}

   def merge_by_key(books):        # one operation per item; dicts keep first-appearance order
       merged = {}
       for b in books:
           merged[b["bookId"]] = merged.get(b["bookId"], 0) + int(b.get("quantity") or 0)
       return merged

   def transaction_chunks(order_id, merged):
       updates = [{"Update": {"TableName": os.environ["BESTSELLERS_TABLE"], "Key": {"bookId": {"S": k}},
                              "UpdateExpression": "ADD salesCount :q", "ExpressionAttributeValues": {":q": {"N": str(q)}}}}
                  for k, q in merged.items()]
       chunks = [updates[i:i + MAX_EFFECTS] for i in range(0, len(updates), MAX_EFFECTS)]
       expires_at = str(int(time.time()) + 7 * 86400)
       out = []
       for i, chunk in enumerate(chunks):
           pk = f"{CONSUMER}#{order_id}#{i}" if len(chunks) > 1 else f"{CONSUMER}#{order_id}"
           marker = {"Put": {"TableName": os.environ["IDEMPOTENCY_TABLE"], "Item": {"pk": {"S": pk}, "expiresAt": {"N": expires_at}},
                             "ConditionExpression": "attribute_not_exists(pk)"}}
           out.append([marker, *chunk])  # marker first: a duplicate shows as CancellationReasons[0]
       return out

   def classify(error):
       code = error.response.get("Error", {}).get("Code")
       if code == "TransactionCanceledException":
           reasons = error.response.get("CancellationReasons") or []
           if reasons and reasons[0].get("Code") == "ConditionalCheckFailed":
               return "duplicate"   # this chunk was committed by an earlier delivery
           if any(r.get("Code") == "TransactionConflict" for r in reasons):
               return "retryable"
           return "permanent"
       return "retryable" if code in RETRYABLE else "permanent"

   def apply_message(order_id, books):
       """'applied' | 'duplicate' | 'failed_retryable' (report it) | 'failed_permanent'"""
       chunks = transaction_chunks(order_id, merge_by_key(books))
       duplicates = 0
       for items in chunks:
           for attempt in range(MAX_RETRIES + 1):
               if attempt:
                   time.sleep((min(50 * 2 ** (attempt - 1), 800) + random.randint(0, 50)) / 1000)
               try:
                   ddb.transact_write_items(TransactItems=items)
                   break
               except ClientError as error:
                   kind = classify(error)
                   if kind == "duplicate":
                       duplicates += 1
                       break
                   if kind == "permanent":
                       return "failed_permanent"
           else:
               return "failed_retryable"   # never swallow: report it, redelivery skips the committed chunks
       return "duplicate" if duplicates == len(chunks) else "applied"

   def lambda_handler(event, context):
       failures = []
       for record in event.get("Records") or []:
           try:
               body = json.loads(record["body"])
               order_id, books = body["orderId"], body["books"]
           except (ValueError, KeyError, TypeError):
               print(f"Skipping malformed message {record.get('messageId')}")
               continue
           if apply_message(order_id, books) == "failed_retryable":
               failures.append({"itemIdentifier": record["messageId"]})
       return {"batchItemFailures": failures}  # with FunctionResponseTypes: [ReportBatchItemFailures]
   ```
   - In boto3 the cancellation reasons are in `error.response["CancellationReasons"]`, the error code in `error.response["Error"]["Code"]`.
   - The low-level client takes typed values (`{"S": ...}`, `{"N": "3"}`); a `Table` resource has no `transact_write_items`.
   - The marker and the effects commit together or not at all, so a crash between them cannot happen.
   - **One operation per item.** Merge the entries that target the same key before building `TransactItems` (sum quantities, keep the last `SET` value, and so on). The merged result must equal applying the entries one by one, as the monolith did.
   - **At most 100 operations per transaction**, the marker included. When a message has more distinct items than that, split them into chunks, and give each chunk its own marker (`<consumer>#<key>#<chunk index>`): each chunk is then exactly-once on its own. Alternatively, use Pattern B.
   - **Transactions that touch the same item conflict.** If another transaction on one of the same items is in flight, DynamoDB cancels one of them with `CancellationReasons[i].Code === 'TransactionConflict'`. That is not a duplicate; it is a transient failure. So:
     - This happens **across Lambda invocations too**: SQS scales a consumer out to many concurrent batches under load, and two of them may update the same popular item. Processing the messages of one batch one after another only reduces conflicts; it does not remove them.
     - So a `TransactionConflict` must be retried (a bounded in-handler retry with backoff) or reported as a failure (`batchItemFailures`, redelivered later; idempotency makes that safe). It must **never be swallowed**: swallowing it silently loses the update.
     - Consequently, "swallow failures like the monolith did" is **not** an acceptable policy for conflict cancellations under Pattern A. The monolith's plain `ADD` never conflicted, so swallowing a conflict is a new loss mode that the migration introduced.
     - Pattern B uses no transactions, so concurrent updates on one item simply serialize and do not conflict.
     - Note: DynamoDB Local serializes transactions and never produces `TransactionConflict`, so local tests cannot show this. It has to be designed in.
   - Transactional writes consume 2x write capacity. Record that trade-off; it is a correctness decision, not something the optimization line may undo.

   **Pattern B (per item): a conditional update that remembers which keys were applied.** For example: `UpdateExpression: 'ADD salesCount :q, appliedOrders :oset'`, `ConditionExpression: 'NOT contains(appliedOrders, :oid)'`, and a `ConditionalCheckFailedException` means "already applied". This needs no transactions, but the set grows forever, so it must be pruned or bounded.

4. **Where markers live.** Use a dedicated table, e.g. `IdempotencyTable` with partition key `pk` (S) and TTL on `expiresAt`. Keep markers longer than the queue's retention plus retries: SQS retention is 4 days by default, so 7 days is a safe TTL.

**Failure reporting is a separate decision.** Returning `{ batchItemFailures: [{ itemIdentifier: messageId }] }`, with `FunctionResponseTypes: [ReportBatchItemFailures]` on the event source, makes only the failed messages retry instead of the whole batch. That narrows the redelivery scope, **but does not replace idempotency**:
- the whole batch is still redelivered on a timeout or crash;
- a message whose effects were partially applied is still retried in full, unless the effects are transactional.

If the monolith swallowed failures (logged and continued), the architect must decide explicitly, and record it in `DECISIONS.md`, between:
- (a) keep swallowing: failures are lost, exactly like the monolith, and the DLQ is unreachable;
- (b) report failures so they retry: this requires idempotency.

Idempotency itself is required in both cases, because redelivery on a timeout or crash happens either way.
