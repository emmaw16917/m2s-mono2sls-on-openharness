/**
 * cart-get-item :: GET /api/cart/{bookId}
 *
 * Migrated from bookstore/routes/cart.js lines 40-61 (router.get('/:bookId', ...)).
 * Semantics preserved:
 *   - GetItem on the composite Cart key
 *   - found     -> 200 with the bare line item
 *   - not found -> 404 { error: 'Item not found in cart' }
 *
 * Auth adaptation: identity comes from event.requestContext.authorizer.claims.sub
 * (verified Cognito Sub) and is stored/read as the renamed "userId" key attribute.
 * Because the partition key is the caller's own id, cross-user reads stay impossible,
 * exactly like the monolith's { customerId, bookId } lookup.
 */
'use strict';

const { DynamoDBClient } = require('@aws-sdk/client-dynamodb');
const { DynamoDBDocumentClient, GetCommand } = require('@aws-sdk/lib-dynamodb');

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
    const bookId = (event.pathParameters || {}).bookId;

    if (!bookId) {
      return makeResponse(400, { error: 'Missing required path parameter: bookId' });
    }

    const data = await docClient.send(new GetCommand({
      TableName: TABLE_NAME,
      Key: {
        userId: userId,
        bookId: bookId
      }
    }));

    if (data.Item) {
      return makeResponse(200, data.Item);
    }
    return makeResponse(404, { error: 'Item not found in cart' });
  } catch (error) {
    console.error('Error in GET /api/cart/{bookId}:', error);
    return makeResponse(500, { error: 'Internal server error' });
  }
};

// The blueprint declares the SAM handler string as "handler.lambda_handler"; the
// alias keeps both "handler.handler" and "handler.lambda_handler" resolvable.
exports.lambda_handler = exports.handler;
