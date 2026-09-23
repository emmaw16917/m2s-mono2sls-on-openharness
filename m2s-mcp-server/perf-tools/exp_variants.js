'use strict';
// 2026-09-23 bookstore-oh rewrite-candidate comparison (see ../PERF_OPTIMIZATION.md §3.2).
// Setup: copy output/lambdas/bookstore/{orders-create,bestsellers-update-consumer,bestsellers-list}/handler.js
// here as h_<name>.js, npm install the @aws-sdk packages, then: node exp_variants.js
process.env.AWS_REGION='us-east-1';process.env.BESTSELLER_QUEUE_URL='x';for(const k of ['BESTSELLERS_TABLE','BOOKS_TABLE','CART_TABLE','ORDERS_TABLE'])process.env[k]='t';
const { installShim, resetCallLog, getCallLog, getCriticalPathDepth } = require('./mock-sdk-shim.js');
const lib = require('@aws-sdk/lib-dynamodb'); const { SQSClient, SendMessageCommand } = require('@aws-sdk/client-sqs');
const { DynamoDBDocumentClient, UpdateCommand, PutCommand, DeleteCommand, TransactWriteCommand } = lib;
installShim({ clientPrototypes:[DynamoDBDocumentClient,SQSClient], responseTable:{
  PutCommand:{},DeleteCommand:{},UpdateCommand:{},SendMessageCommand:{},TransactWriteCommand:{},
  ScanCommand:(c)=>({Items:global.__scanItems||[]}), QueryCommand:(c)=>({Items:(global.__scanItems||[]).slice(0,20)}) }});
const doc = DynamoDBDocumentClient.from(new (require('@aws-sdk/client-dynamodb').DynamoDBClient)({}));
const sqs = new SQSClient({});
const time = async (fn) => { resetCallLog(); const t0=process.hrtime.bigint(); await fn(); return {ms:+(Number(process.hrtime.bigint()-t0)/1e6).toFixed(1), calls:getCallLog().length, depth:getCriticalPathDepth()}; };
const med = async (fn, n=7) => { const r=[]; for(let i=0;i<n;i++) r.push(await time(fn)); r.sort((a,b)=>a.ms-b.ms); return r[Math.floor(n/2)]; };

(async () => {
  // ---- B: SQS consumer, BatchSize=10 records x 3 books, 5 distinct hot books ----
  const records = Array.from({length:10},(_,i)=>({body:JSON.stringify({orderId:'o'+i,books:[0,1,2].map(j=>({bookId:'b'+((i+j)%5),quantity:1}))})}));
  const consumer = require('./h_bestsellers-update-consumer.js');
  const origLog = console.log; console.log = ()=>{};
  const B_cur = await med(()=>consumer.handler({Records:records}));
  const upd = (bookId,q)=>doc.send(new UpdateCommand({TableName:'t',Key:{bookId},UpdateExpression:'ADD salesCount :q',ExpressionAttributeValues:{':q':q}}));
  const B_par = await med(()=>Promise.all(records.flatMap(r=>JSON.parse(r.body).books.map(b=>upd(b.bookId,b.quantity)))));
  const B_agg = await med(async()=>{const m=new Map();for(const r of records)for(const b of JSON.parse(r.body).books)m.set(b.bookId,(m.get(b.bookId)||0)+b.quantity);await Promise.all([...m].map(([k,q])=>upd(k,q)));});
  // ---- C: orders-create variants, 3-book cart ----
  const books=[1,2,3].map(i=>({bookId:'b'+i,quantity:1,price:9.99}));
  const oc = require('./h_orders-create.js');
  const ev={body:JSON.stringify({books}),requestContext:{authorizer:{claims:{sub:'u1'}}}};
  const C_cur = await med(()=>oc.handler(ev));
  const put=()=>doc.send(new PutCommand({TableName:'t',Item:{userId:'u1',orderId:'x',books}}));
  const dels=()=>books.map(b=>doc.send(new DeleteCommand({TableName:'t',Key:{userId:'u1',bookId:b.bookId}})));
  const msg=()=>sqs.send(new SendMessageCommand({QueueUrl:'x',MessageBody:'{}'}));
  const tx=()=>doc.send(new TransactWriteCommand({TransactItems:[{Put:{TableName:'t',Item:{}}},...books.map(b=>({Delete:{TableName:'t',Key:{}}}))]}));
  const C_allpar = await med(()=>Promise.all([put(),...dels(),msg()]));
  const C_txsqs = await med(async()=>{await tx(); await msg();});
  const C_txonly = await med(()=>tx());
  // ---- D: bestsellers-list full Scan + in-memory sort vs GSI Query Limit 20 ----
  const bl = require('./h_bestsellers-list.js');
  const D = {};
  for (const n of [100,1000,10000,100000]) { global.__scanItems=Array.from({length:n},(_,i)=>({bookId:'b'+i,salesCount:(i*7919)%1000})); D['scan_'+n]=await med(()=>bl.handler({}),5); }
  global.__scanItems=Array.from({length:20},(_,i)=>({bookId:'b'+i,salesCount:1000-i}));
  D.gsi_query_limit20 = await med(()=>doc.send(new (lib.QueryCommand)({TableName:'t',IndexName:'x',KeyConditionExpression:'k=:k',ExpressionAttributeValues:{':k':'ALL'},ScanIndexForward:false,Limit:20})),5);
  console.log = origLog;
  console.log(JSON.stringify({B:{current_sequential:B_cur,promise_all:B_par,aggregate_then_parallel:B_agg},C:{current:C_cur,all_parallel:C_allpar,transact_then_sqs:C_txsqs,transact_only_stream:C_txonly},D},null,1));
})();
