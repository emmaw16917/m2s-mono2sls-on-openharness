/**
 * cart-list-items :: GET /api/cart
 *
 * Migrated from bookstore/routes/cart.js lines 17-33 (router.get('/', ...)).
 * Semantics preserved: a single Query over the Cart partition key returning the
 * bare array of line items (res.json(data.Items)).
 *
 * Auth adaptation:
 *   - The monolith used req.customerId injected by middleware/auth.js (dropped).
 *   - API Gateway's Cognito authorizer already validated the access token, so the
 *     handler only reads the verified user id from the claims and uses it as the
 *     renamed "userId" partition key. No token handling lives here.
 */
'use strict';

const { DynamoDBClient } = require('@aws-sdk/client-dynamodb');
const { DynamoDBDocumentClient, QueryCommand } = require('@aws-sdk/lib-dynamodb');

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

    const data = await docClient.send(new QueryCommand({
      TableName: TABLE_NAME,
      KeyConditionExpression: 'userId = :userId',
      ExpressionAttributeValues: {
        ':userId': userId
      }
    }));

    return makeResponse(200, data.Items || []);
  } catch (error) {
    console.error('Error in GET /api/cart:', error);
    return makeResponse(500, { error: 'Internal server error' });
  }
};

// The blueprint declares the SAM handler string as "handler.lambda_handler"; the
// alias keeps both "handler.handler" and "handler.lambda_handler" resolvable.
exports.lambda_handler = exports.handler;
