/**
 * search-books :: GET /api/search?q=<keyword>
 *
 * Migrated from bookstore/routes/search.js lines 16-51 (router.get('/', ...)).
 * Semantics preserved verbatim, including the Elasticsearch-shaped payload:
 *   - missing ?q            -> 400 { error: 'Missing required query parameter: q' }
 *   - full-table Scan on Books + in-memory case-insensitive substring match on
 *     name / author / category (the monolith scans too; no new search backend is invented)
 *   - success                -> 200 { total, hits: [{ _source: <book> }, ...] }
 *
 * Notes:
 *   - Requires auth at the gateway but never uses the caller identity.
 *   - CommonJS only; AWS SDK v3 DocumentClient instead of aws-sdk v2.
 */
'use strict';

const { DynamoDBClient } = require('@aws-sdk/client-dynamodb');
const { DynamoDBDocumentClient, ScanCommand } = require('@aws-sdk/lib-dynamodb');

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
    const query = (event.queryStringParameters || {}).q;

    if (!query) {
      return makeResponse(400, { error: 'Missing required query parameter: q' });
    }

    const data = await docClient.send(new ScanCommand({
      TableName: TABLE_NAME
    }));

    const lowerQuery = String(query).toLowerCase();
    const results = (data.Items || []).filter((item) => (
      (item.name && item.name.toLowerCase().includes(lowerQuery)) ||
      (item.author && item.author.toLowerCase().includes(lowerQuery)) ||
      (item.category && item.category.toLowerCase().includes(lowerQuery))
    ));

    return makeResponse(200, {
      total: results.length,
      hits: results.map((item) => ({ _source: item }))
    });
  } catch (error) {
    console.error('Error in GET /api/search:', error);
    return makeResponse(500, { error: 'Internal server error' });
  }
};

// The blueprint declares the SAM handler string as "handler.lambda_handler"; the
// alias keeps both "handler.handler" and "handler.lambda_handler" resolvable.
exports.lambda_handler = exports.handler;
