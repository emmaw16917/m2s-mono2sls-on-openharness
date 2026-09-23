/**
 * orders-create :: POST /api/orders  (checkout)
 *
 * Migrated from bookstore/routes/orders.js lines 45-107 (router.post('/', ...)).
 * Checkout semantics preserved step by step:
 *   1. body { books: [{ bookId, price, quantity }, ...] }; a missing / non-array /
 *      empty array -> 400 { error: 'Missing or invalid books array' }
 *   2. PutItem the order (orderId = UUID, orderDate = Date.now() epoch millis, books
 *      stored as-is)
 *   3. delete every checked-out cart line for the caller, concurrently, and only
 *      continue once all deletes settled (same Promise.all ordering as the monolith)
 *   4. answer 200 { message: 'Order created successfully', orderId }
 *
 * Async adaptation (the only behavioural change forced by Lambda):
 *   The monolith called utils/bestsellers.updateBestSellers() without awaiting it,
 *   i.e. a pure fire-and-forget side effect whose result is never used (see the
 *   .then()/.catch() block at routes/orders.js:87-97). That background work cannot
 *   survive the freeze of a Lambda execution environment, so the counter update is
 *   handed to SQS (BestsellerUpdateQueue) and executed by bestsellers-update-consumer.
 *   The send MUST be awaited: execution freezes once the handler returns and a
 *   non-awaited send can be silently dropped. Like the original, a publication
 *   failure is logged but never fails an already-persisted order.
 *
 * Auth adaptation: req.customerId -> claims.sub, written as the "userId" key that
 * both the Orders and Cart tables now use.
 */
'use strict';

const { randomUUID } = require('crypto');
const { DynamoDBClient } = require('@aws-sdk/client-dynamodb');
const { DynamoDBDocumentClient, PutCommand, DeleteCommand } = require('@aws-sdk/lib-dynamodb');
const { SQSClient, SendMessageCommand } = require('@aws-sdk/client-sqs');

const ORDERS_TABLE = process.env.ORDERS_TABLE;
const CART_TABLE = process.env.CART_TABLE;
const QUEUE_URL = process.env.BESTSELLER_QUEUE_URL;
if (!ORDERS_TABLE) throw new Error('ORDERS_TABLE environment variable is not set');
if (!CART_TABLE) throw new Error('CART_TABLE environment variable is not set');
if (!QUEUE_URL) throw new Error('BESTSELLER_QUEUE_URL environment variable is not set');

// Initialised once per cold start and reused across invocations.
const ddbClient = new DynamoDBClient({ region: process.env.AWS_REGION });
const docClient = DynamoDBDocumentClient.from(ddbClient, {
  marshallOptions: { removeUndefinedValues: true }
});
const sqsClient = new SQSClient({ region: process.env.AWS_REGION });

function makeResponse(statusCode, body) {
  return {
    statusCode,
    headers: {
      'Content-Type': 'application/json',
      'Access-Control-Allow-Origin': '*'
    },
    body: JSON.stringify(body)
  };
}

exports.handler = async function handler(event, context) {
  try {
    const userId = event.requestContext.authorizer.claims.sub;
    const body = JSON.parse(event.body || '{}');
    const books = body.books;

    if (!books || !Array.isArray(books) || books.length === 0) {
      return makeResponse(400, { error: 'Missing or invalid books array' });
    }

    // 1. Create the order record.
    const orderId = randomUUID();

    await docClient.send(new PutCommand({
      TableName: ORDERS_TABLE,
      Item: {
        userId: userId,
        orderId: orderId,
        orderDate: Date.now(),
        books: books
      }
    }));

    // 2. Remove the checked-out items from the cart (inline and synchronous, so the
    //    cart is already cleared when the customer sees the response).
    await Promise.all(books.map((book) => docClient.send(new DeleteCommand({
      TableName: CART_TABLE,
      Key: {
        userId: userId,
        bookId: book.bookId
      }
    }))));

    // 3. Hand the bestseller counter update to the queue instead of running the
    //    in-process helper. Awaited, but non-fatal to the order itself.
    try {
      await sqsClient.send(new SendMessageCommand({
        QueueUrl: QUEUE_URL,
        MessageBody: JSON.stringify({ orderId: orderId, books: books })
      }));
    } catch (publishError) {
      console.error('Error publishing bestseller update for order ' + orderId + ':', publishError);
    }

    return makeResponse(200, {
      message: 'Order created successfully',
      orderId: orderId
    });
  } catch (error) {
    console.error('Error in POST /api/orders:', error);
    return makeResponse(500, { error: 'Internal server error' });
  }
};

// The blueprint declares the SAM handler string as "handler.lambda_handler"; the
// alias keeps both "handler.handler" and "handler.lambda_handler" resolvable.
exports.lambda_handler = exports.handler;
