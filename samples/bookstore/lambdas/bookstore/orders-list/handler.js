/**
 * orders-list :: GET /api/orders
 *
 * Migrated from bookstore/routes/orders.js lines 18-34 (router.get('/', ...)).
 * Semantics preserved: one Query over the Orders partition key returning the bare
 * array of order records (res.json(data.Items)); each item keeps orderDate as epoch
 * milliseconds and the books array [{ bookId, price, quantity }].
 *
 * Auth adaptation: req.customerId -> event.requestContext.authorizer.claims.sub,
 * queried through the renamed "userId" partition key.
 */
'use strict';

const { DynamoDBClient } = require('@aws-sdk/client-dynamodb');
const { DynamoDBDocumentClient, QueryCommand } = require('@aws-sdk/lib-dynamodb');

const TABLE_NAME = process.env.ORDERS_TABLE;
if (!TABLE_NAME) throw new Error('ORDERS_TABLE environment variable is not set');

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
    console.error('Error in GET /api/orders:', error);
    return makeResponse(500, { error: 'Internal server error' });
  }
};

// The blueprint declares the SAM handler string as "handler.lambda_handler"; the
// alias keeps both "handler.handler" and "handler.lambda_handler" resolvable.
exports.lambda_handler = exports.handler;
