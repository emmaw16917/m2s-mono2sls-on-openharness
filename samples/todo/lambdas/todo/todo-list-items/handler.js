/**
 * todo-list-items — GET /item
 *
 * Migrated from todo/routes/todo.js lines 10-38 (router.get('/item')) with the
 * DynamoDB client that used to live in todo/config/db.js (lines 14-23).
 *
 * Migration notes:
 *  - Auth: the Express middleware `authenticateToken` (router.use at todo.js line 8)
 *    is replaced by the API Gateway Cognito user-pool authorizer, so this handler
 *    contains ZERO auth code. The identity is read from the pre-validated
 *    event.requestContext.authorizer.claims instead of req.user.username.
 *  - aws-sdk v2 `AWS.DynamoDB.DocumentClient` -> SDK v3 DynamoDBDocumentClient.
 *  - Clients/table name are initialised outside the handler (cold-start reuse).
 *  - CommonJS only (require / exports) so layer + runtime module resolution works.
 */
const { DynamoDBClient } = require('@aws-sdk/client-dynamodb');
const { DynamoDBDocumentClient, QueryCommand } = require('@aws-sdk/lib-dynamodb');

const ddbClient = new DynamoDBClient({});
const docClient = DynamoDBDocumentClient.from(ddbClient, {
    marshallOptions: { removeUndefinedValues: true },
});

// Injected by the SAM template (blueprint: TODO_TABLE = !Ref TodoItemsTable).
const TODO_TABLE = process.env.TODO_TABLE;
if (!TODO_TABLE) {
    throw new Error('TODO_TABLE environment variable is not set');
}

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

/**
 * Identity is already verified by API Gateway; we only read the claims.
 * The monolith signed its own JWT with { username }, the table partition key is
 * literally named "cognito-username", so the Cognito user name is the right value.
 */
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

        const params = {
            TableName: TODO_TABLE,
            // "cognito-username" contains a dash, which is not a valid
            // KeyConditionExpression identifier -> keep the placeholder.
            KeyConditionExpression: '#username = :username',
            ExpressionAttributeNames: {
                '#username': 'cognito-username',
            },
            ExpressionAttributeValues: {
                ':username': username,
            },
        };

        const result = await docClient.send(new QueryCommand(params));

        return makeResponse(200, {
            Items: result.Items || [],
            Count: result.Count || 0,
        });
    } catch (error) {
        console.error('Failed to fetch todo list:', error);
        return makeResponse(400, { message: error.message });
    }
};

// Handler-string compatibility alias: the blueprint's `handler` field is not
// "handler.handler"; exporting under both names keeps C3 green whichever
// function name the SAM template declares for this module.
exports.lambda_handler = exports.handler;
