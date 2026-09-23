/**
 * cart-remove-item :: DELETE /api/cart
 *
 * Migrated from bookstore/routes/cart.js lines 137-161 (router.delete('/', ...)).
 * Semantics preserved:
 *   - the book id is taken from the request BODY (like the monolith), not the path
 *   - missing bookId -> 400 { error: 'Missing required field: bookId' }
 *   - DeleteItem on the caller's own { userId, bookId } key
 *   - 200 { message: 'Item removed from cart successfully' }
 *
 * Auth adaptation: req.customerId -> claims.sub used as the "userId" partition key,
 * so a caller can only ever delete its own line item.
 */
'use strict';

const { DynamoDBClient } = require('@aws-sdk/client-dynamodb');
const { DynamoDBDocumentClient, DeleteCommand } = require('@aws-sdk/lib-dynamodb');

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

    if (!bookId) {
      return makeResponse(400, { error: 'Missing required field: bookId' });
    }

    await docClient.send(new DeleteCommand({
      TableName: TABLE_NAME,
      Key: {
        userId: userId,
        bookId: bookId
      }
    }));

    return makeResponse(200, { message: 'Item removed from cart successfully' });
  } catch (error) {
    console.error('Error in DELETE /api/cart:', error);
    return makeResponse(500, { error: 'Internal server error' });
  }
};

// The blueprint declares the SAM handler string as "handler.lambda_handler"; the
// alias keeps both "handler.handler" and "handler.lambda_handler" resolvable.
exports.lambda_handler = exports.handler;
