// Calibration Lambda (e2e_calibrate.py): times each DynamoDB / SQS operation
// the pipeline's handlers use, from inside a Lambda in the same region as the
// table, with the SDK the runtime bundles. One invocation = one sweep; the
// caller invokes it several times and takes medians.
import { DynamoDBClient, GetItemCommand, PutItemCommand, UpdateItemCommand, DeleteItemCommand, QueryCommand, ScanCommand,
  BatchGetItemCommand, BatchWriteItemCommand, TransactWriteItemsCommand, TransactGetItemsCommand } from "@aws-sdk/client-dynamodb";
import { SQSClient, SendMessageCommand, SendMessageBatchCommand } from "@aws-sdk/client-sqs";

const ddb = new DynamoDBClient({});
const sqs = new SQSClient({});
const T = process.env.TABLE, Q = process.env.QUEUE_URL;
const PAD = "x".repeat(700);   // items of ~1 KB, like the pipeline's records
const item = (pk, sk) => ({ pk: { S: pk }, sk: { S: sk }, g: { S: "grp-" + pk.slice(-1) }, n: { N: "1" }, pad: { S: PAD } });

async function seed() {
  // 20 items under one partition (Query / Scan return 20) and in one GSI group
  for (let i = 0; i < 20; i += 10) {
    const reqs = Array.from({ length: 10 }, (_, k) => ({ PutRequest: { Item: { ...item("p0", "s" + (i + k)), g: { S: "grp" } } } }));
    await ddb.send(new BatchWriteItemCommand({ RequestItems: { [T]: reqs } }));
  }
}

let seq = 0;
const OPS = {
  GetItem: () => new GetItemCommand({ TableName: T, Key: { pk: { S: "p0" }, sk: { S: "s1" } } }),
  Query: () => new QueryCommand({ TableName: T, KeyConditionExpression: "pk = :p", ExpressionAttributeValues: { ":p": { S: "p0" } }, Limit: 20 }),
  QueryGSI: () => new QueryCommand({ TableName: T, IndexName: "ByGroup", KeyConditionExpression: "g = :g", ExpressionAttributeValues: { ":g": { S: "grp" } }, Limit: 20 }),
  Scan: () => new ScanCommand({ TableName: T, Limit: 20 }),
  PutItem: () => new PutItemCommand({ TableName: T, Item: item("w" + (seq++ % 50), "s") }),
  UpdateItem: () => new UpdateItemCommand({ TableName: T, Key: { pk: { S: "p0" }, sk: { S: "s2" } }, UpdateExpression: "ADD n :one", ExpressionAttributeValues: { ":one": { N: "1" } } }),
  DeleteItem: () => new DeleteItemCommand({ TableName: T, Key: { pk: { S: "d" + (seq++ % 50) }, sk: { S: "s" } } }),
  BatchGetItem: () => new BatchGetItemCommand({ RequestItems: { [T]: { Keys: Array.from({ length: 10 }, (_, k) => ({ pk: { S: "p0" }, sk: { S: "s" + k } })) } } }),
  BatchWriteItem: () => new BatchWriteItemCommand({ RequestItems: { [T]: Array.from({ length: 10 }, (_, k) => ({ PutRequest: { Item: item("b" + k, "s" + (seq++ % 5)) } })) } }),
  TransactGetItems: () => new TransactGetItemsCommand({ TransactItems: [0, 1].map((k) => ({ Get: { TableName: T, Key: { pk: { S: "p0" }, sk: { S: "s" + k } } } })) }),
  TransactWriteItems: () => new TransactWriteItemsCommand({ TransactItems: [0, 1].map((k) => ({ Put: { TableName: T, Item: item("t" + k + "-" + (seq++ % 50), "s") } })) }),
};
const SQS_OPS = {
  SendMessage: () => new SendMessageCommand({ QueueUrl: Q, MessageBody: JSON.stringify({ k: seq++, pad: PAD }) }),
  SendMessageBatch: () => new SendMessageBatchCommand({ QueueUrl: Q, Entries: Array.from({ length: 10 }, (_, k) => ({ Id: "m" + k, MessageBody: JSON.stringify({ k, pad: PAD }) })) }),
};

const pct = (xs, p) => { const s = [...xs].sort((a, b) => a - b); return s[Math.min(s.length - 1, Math.floor(p * (s.length - 1)))]; };

export const handler = async (event) => {
  const n = event.n || 100, warm = event.warm || 10;
  if (event.seed) await seed();
  const out = {};
  for (const [name, make] of [...Object.entries(OPS).map(([k, f]) => [k, f]), ...Object.entries(SQS_OPS)]) {
    const client = name in SQS_OPS ? sqs : ddb;
    for (let i = 0; i < warm; i++) await client.send(make());
    const ms = [];
    for (let i = 0; i < n; i++) {
      const t0 = process.hrtime.bigint();
      await client.send(make());
      ms.push(Number(process.hrtime.bigint() - t0) / 1e6);
    }
    out[name] = { n, p10: +pct(ms, 0.1).toFixed(2), p50: +pct(ms, 0.5).toFixed(2), p90: +pct(ms, 0.9).toFixed(2),
                  mean: +(ms.reduce((a, b) => a + b, 0) / n).toFixed(2) };
  }
  return { node: process.version, memory: process.env.AWS_LAMBDA_FUNCTION_MEMORY_SIZE, arch: process.arch, ops: out };
};
