'use strict';
// Usage: node run_one.js <handlerFile> <eventKind>
// eventKind: "api" (default) or "sqs"
process.env.AWS_REGION = 'us-east-1';
process.env.BESTSELLER_QUEUE_URL = 'https://sqs.test/q';
process.env.BESTSELLERS_TABLE = 't';
process.env.BOOKS_TABLE = 't';
process.env.CART_TABLE = 't';
process.env.ORDERS_TABLE = 't';

const { installShim, resetCallLog, getCallLog, EXAMPLE_RESPONSE_TABLE_FROM_BOOKSTORE } = require('./mock-sdk-shim.js');
// This run is re-measuring the SAME bookstore-oh codebase the example table
// was scanned from, so reusing it is valid here -- a different codebase must
// re-scan per PERF_OPTIMIZATION_DESIGN.md §3 step 1, not copy this table.
const { DynamoDBDocumentClient } = require('@aws-sdk/lib-dynamodb');
const { SQSClient } = require('@aws-sdk/client-sqs');
installShim({
  clientPrototypes: [DynamoDBDocumentClient, SQSClient],
  responseTable: EXAMPLE_RESPONSE_TABLE_FROM_BOOKSTORE,
});

const handlerFile = process.argv[2];
const eventKind = process.argv[3] || 'api';

const mod = require('./' + handlerFile);

const threeBooks = [
  { bookId: 'book-1', price: 9.99, quantity: 1 },
  { bookId: 'book-2', price: 14.99, quantity: 2 },
  { bookId: 'book-3', price: 7.5, quantity: 1 },
];

const apiEvent = {
  httpMethod: 'GET',
  pathParameters: { id: 'book-1', bookId: 'book-1' },
  queryStringParameters: { category: 'fiction', q: 'test' },
  body: JSON.stringify({ bookId: 'book-1', quantity: 2, books: threeBooks }),
  requestContext: {
    authorizer: {
      claims: { sub: 'mock-user-id-0001' },
    },
  },
};

const sqsEvent = {
  Records: [
    {
      body: JSON.stringify({ orderId: 'mock-order-1', books: threeBooks }),
    },
  ],
};

const event = eventKind === 'sqs' ? sqsEvent : apiEvent;

(async () => {
  resetCallLog();
  const t0 = process.hrtime.bigint();
  let result;
  let errorMsg = null;
  try {
    result = await mod.handler(event, {});
  } catch (e) {
    errorMsg = e.message;
  }
  const elapsedMs = Number(process.hrtime.bigint() - t0) / 1e6;
  const calls = getCallLog();
  console.log(JSON.stringify({
    handlerFile,
    exec_ms: Number(elapsedMs.toFixed(2)),
    sdk_call_count: calls.length,
    sdk_calls: calls,
    statusCode: result && result.statusCode,
    error: errorMsg,
  }));
})();
