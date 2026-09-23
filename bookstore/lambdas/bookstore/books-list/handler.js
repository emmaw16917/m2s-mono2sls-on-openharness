/**
 * books-list :: GET /api/books
 *
 * Migrated from bookstore/routes/books.js lines 19-49 (router.get('/', ...)).
 * Semantics preserved verbatim:
 *   - ?category=<value>  -> Query on the "category-index" GSI
 *   - no category filter -> Scan the whole Books table
 *   - the response body is the bare array of book items (res.json(data.Items))
 *
 * Notes:
 *   - The endpoint is mounted behind authMiddleware in server.js, so the route is
 *     protected, but the original handler never reads req.customerId; therefore this
 *     Lambda contains no identity handling at all (auth is enforced by the API Gateway
 *     Cognito authorizer configured in the SAM template).
 *   - Express / body-parser / aws-sdk v2 DocumentClient replaced by the runtime
 *     built-in AWS SDK v3 DynamoDBDocumentClient.
 *   - CommonJS only (require/exports) so Lambda layer + runtime module resolution works.
 */
'use strict';

const { DynamoDBClient } = require('@aws-sdk/client-dynamodb');
const { DynamoDBDocumentClient, QueryCommand, ScanCommand } = require('@aws-sdk/lib-dynamodb');

const TABLE_NAME = process.env.BOOKS_TABLE;
if (!TABLE_NAME) throw new Error('BOOKS_TABLE environment variable is not set');

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
    const category = (event.queryStringParameters || {}).category;

    if (category) {
      const data = await docClient.send(new QueryCommand({
        TableName: TABLE_NAME,
        IndexName: 'category-index',
        KeyConditionExpression: 'category = :category',
        ExpressionAttributeValues: {
          ':category': category
        }
      }));
      return makeResponse(200, data.Items || []);
    }

    const data = await docClient.send(new ScanCommand({
      TableName: TABLE_NAME
    }));
    return makeResponse(200, data.Items || []);
  } catch (error) {
    console.error('Error in GET /api/books:', error);
    return makeResponse(500, { error: 'Internal server error' });
  }
};

// The blueprint declares the SAM handler string as "handler.lambda_handler"; the
// alias keeps both "handler.handler" and "handler.lambda_handler" resolvable.
exports.lambda_handler = exports.handler;
