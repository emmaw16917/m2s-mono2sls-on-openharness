/**
 * books-get-by-id :: GET /api/books/{id}
 *
 * Migrated from bookstore/routes/books.js lines 56-76 (router.get('/:id', ...)).
 * Semantics preserved:
 *   - GetItem on Books with the { id } partition key
 *   - found    -> 200 with the bare book item (res.json(data.Item))
 *   - not found-> 404 with { error: 'Book not found' }
 *
 * Notes:
 *   - Route is auth-required at the gateway but the original handler does not use
 *     the caller identity, so nothing is read from the authorizer claims here.
 *   - CommonJS only; AWS SDK v3 DocumentClient instead of the monolith's aws-sdk v2.
 */
'use strict';

const { DynamoDBClient } = require('@aws-sdk/client-dynamodb');
const { DynamoDBDocumentClient, GetCommand } = require('@aws-sdk/lib-dynamodb');

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
    const bookId = (event.pathParameters || {}).id;

    if (!bookId) {
      return makeResponse(400, { error: 'Missing required path parameter: id' });
    }

    const data = await docClient.send(new GetCommand({
      TableName: TABLE_NAME,
      Key: {
        id: bookId
      }
    }));

    if (data.Item) {
      return makeResponse(200, data.Item);
    }
    return makeResponse(404, { error: 'Book not found' });
  } catch (error) {
    console.error('Error in GET /api/books/{id}:', error);
    return makeResponse(500, { error: 'Internal server error' });
  }
};

// The blueprint declares the SAM handler string as "handler.lambda_handler"; the
// alias keeps both "handler.handler" and "handler.lambda_handler" resolvable.
exports.lambda_handler = exports.handler;
