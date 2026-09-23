/**
 * todo-get-item — GET /item/{id}
 *
 * Migrated from todo/routes/todo.js lines 40-75 (router.get('/item/:id')) with the
 * DynamoDB client that used to live in todo/config/db.js (lines 14-23).
 *
 * Migration notes:
 *  - req.params.id -> event.pathParameters.id (API Gateway proxy integration).
 *  - router.use(authenticateToken) -> API Gateway Cognito authorizer; identity is
 *    read from event.requestContext.authorizer.claims, no auth code here.
 *  - aws-sdk v2 DocumentClient.get -> v3 DynamoDBDocumentClient + GetCommand.
 *  - Response body keeps the monolith shape { "Item": {...} }.
 */
const { DynamoDBClient } = require('@aws-sdk/client-dynamodb');
const { DynamoDBDocumentClient, GetCommand } = require('@aws-sdk/lib-dynamodb');

const ddbClient = new DynamoDBClient({});
const docClient = DynamoDBDocumentClient.from(ddbClient, {
    marshallOptions: { removeUndefinedValues: true },
});

const TODO_TABLE = process.env.TODO_TABLE;
if (!TODO_TABLE) {
    throw new Error('TODO_TABLE environment variable is not set');
}

// Same id shape check as the monolith (uuid v1 ids are [0-9a-f-]).
const VALID_ID_RE = /^[\w-]+$/;

function makeResponse(statusCode, body) {
    return {
        statusCode,
        headers: {
            'Content-Type': 'application/json',
            'Access-Control-Allow-Origin': '*',
        },
        body: JSON.stringify(body),
    };
}

function getUsername(event) {
    const claims =
        (event.requestContext &&
            event.requestContext.authorizer &&
            event.requestContext.authorizer.claims) ||
        {};
    return claims.username || claims['cognito:username'] || claims.sub || null;
}

exports.handler = async function handler(event, context) {
    try {
        const username = getUsername(event);
        if (!username) {
            return makeResponse(401, {
                message: 'No authenticated user in request context. Please log in first.',
            });
        }

        const id = (event.pathParameters || {}).id;

        if (!id || !VALID_ID_RE.test(id)) {
            return makeResponse(400, {
                message: 'Invalid request: Invalid todo item ID',
            });
        }

        const params = {
            TableName: TODO_TABLE,
            Key: {
                'cognito-username': username,
                id: id,
            },
        };

        const result = await docClient.send(new GetCommand(params));

        if (!result.Item) {
            return makeResponse(404, { message: 'Todo item not found' });
        }

        return makeResponse(200, { Item: result.Item });
    } catch (error) {
        console.error('Failed to fetch todo item:', error);
        return makeResponse(400, { message: error.message });
    }
};

exports.lambda_handler = exports.handler;
