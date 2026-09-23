/**
 * bestsellers-list :: GET /api/bestsellers
 *
 * Migrated from bookstore/routes/bestsellers.js lines 16-34 (router.get('/', ...)).
 * Semantics preserved:
 *   - Scan the Bestsellers table
 *   - sort descending by salesCount (missing counts treated as 0)
 *   - top 20, projected to the bare bookId array (res.json(sorted))
 *   - the original catch block answers 200 with an empty array instead of bubbling
 *     the error to the Express error handler -> reproduced here.
 *
 * Notes:
 *   - Requires auth at the gateway but never uses the caller identity.
 *   - Reads/writes of salesCount itself live in bestsellers-update-consumer.
 */
'use strict';

const { DynamoDBClient } = require('@aws-sdk/client-dynamodb');
const { DynamoDBDocumentClient, ScanCommand } = require('@aws-sdk/lib-dynamodb');

const TABLE_NAME = process.env.BESTSELLERS_TABLE;
if (!TABLE_NAME) throw new Error('BESTSELLERS_TABLE environment variable is not set');

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
    const data = await docClient.send(new ScanCommand({
      TableName: TABLE_NAME
    }));

    const sorted = (data.Items || [])
      .sort((a, b) => (b.salesCount || 0) - (a.salesCount || 0))
      .slice(0, 20)
      .map((item) => item.bookId);

    return makeResponse(200, sorted);
  } catch (error) {
    // Parity with the monolith: a broken ranking read degrades to an empty list.
    console.error('Error in GET /api/bestsellers:', error);
    return makeResponse(200, []);
  }
};

// The blueprint declares the SAM handler string as "handler.lambda_handler"; the
// alias keeps both "handler.handler" and "handler.lambda_handler" resolvable.
exports.lambda_handler = exports.handler;
