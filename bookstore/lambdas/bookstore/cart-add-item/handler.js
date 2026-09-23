/**
 * cart-add-item :: POST /api/cart
 *
 * Migrated from bookstore/routes/cart.js lines 68-94 (router.post('/', ...)).
 * Semantics preserved:
 *   - body { bookId, quantity, price }
 *   - falsy check on all three fields (same as the monolith: quantity/price of 0 is
 *     rejected) -> 400 { error: 'Missing required fields: bookId, quantity, price' }
 *   - PutItem on Cart, which overwrites an existing user+book line (the monolith has
 *     no "increase quantity" merge, so none is invented here)
 *   - 200 { message: 'Item added to cart successfully' }
 *
 * Auth adaptation: req.customerId -> event.requestContext.authorizer.claims.sub,
 * written to the renamed "userId" partition key.
 */
'use strict';

const { DynamoDBClient } = require('@aws-sdk/client-dynamodb');
const { DynamoDBDocumentClient, PutCommand } = require('@aws-sdk/lib-dynamodb');

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
    const price = body.price;

    if (!bookId || !quantity || !price) {
      return makeResponse(400, { error: 'Missing required fields: bookId, quantity, price' });
    }

    await docClient.send(new PutCommand({
      TableName: TABLE_NAME,
      Item: {
        userId: userId,
        bookId: bookId,
        quantity: quantity,
        price: price
      }
    }));

    return makeResponse(200, { message: 'Item added to cart successfully' });
  } catch (error) {
    console.error('Error in POST /api/cart:', error);
    return makeResponse(500, { error: 'Internal server error' });
  }
};

// The blueprint declares the SAM handler string as "handler.lambda_handler"; the
// alias keeps both "handler.handler" and "handler.lambda_handler" resolvable.
exports.lambda_handler = exports.handler;
