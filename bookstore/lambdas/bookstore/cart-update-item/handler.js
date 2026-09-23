/**
 * cart-update-item :: PUT /api/cart
 *
 * Migrated from bookstore/routes/cart.js lines 101-130 (router.put('/', ...)).
 * Semantics preserved:
 *   - body { bookId, quantity }; falsy check on both
 *     -> 400 { error: 'Missing required fields: bookId, quantity' }
 *   - UpdateItem "SET quantity = :quantity" scoped to the caller's own partition,
 *     ReturnValues ALL_NEW (the monolith asked for it, so the write is unchanged)
 *   - 200 { message: 'Cart updated successfully' } (the updated item is not echoed)
 *
 * Auth adaptation: req.customerId -> claims.sub written as the "userId" key.
 */
'use strict';

const { DynamoDBClient } = require('@aws-sdk/client-dynamodb');
const { DynamoDBDocumentClient, UpdateCommand } = require('@aws-sdk/lib-dynamodb');

const TABLE_NAME = process.env.CART_TABLE;
if (!TABLE_NAME) throw new Error('CART_TABLE environment variable is not set');

// Initialised once per cold start and reused across invocations.
const ddbClient = new DynamoDBClient({ region: process.env.AWS_REGION });
const docClient = DynamoDBDocumentClient.from(ddbClient, {
  marshallOptions: { removeUndefinedValues: true }
});

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
    const bookId = body.bookId;
    const quantity = body.quantity;

    if (!bookId || !quantity) {
      return makeResponse(400, { error: 'Missing required fields: bookId, quantity' });
    }

    await docClient.send(new UpdateCommand({
      TableName: TABLE_NAME,
      Key: {
        userId: userId,
        bookId: bookId
      },
      UpdateExpression: 'SET quantity = :quantity',
      ExpressionAttributeValues: {
        ':quantity': quantity
      },
      ReturnValues: 'ALL_NEW'
    }));

    return makeResponse(200, { message: 'Cart updated successfully' });
  } catch (error) {
    console.error('Error in PUT /api/cart:', error);
    return makeResponse(500, { error: 'Internal server error' });
  }
};

// The blueprint declares the SAM handler string as "handler.lambda_handler"; the
// alias keeps both "handler.handler" and "handler.lambda_handler" resolvable.
exports.lambda_handler = exports.handler;
