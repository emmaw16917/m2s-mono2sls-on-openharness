/**
 * todo-complete-item — POST /item/{id}/done
 *
 * Migrated from todo/routes/todo.js lines 167-206 (router.post('/item/:id/done'))
 * with the DynamoDB client that used to live in todo/config/db.js (lines 14-23).
 *
 * Migration notes:
 *  - req.params.id -> event.pathParameters.id.
 *  - router.use(authenticateToken) -> API Gateway Cognito authorizer; identity is
 *    read from event.requestContext.authorizer.claims, no auth code here.
 *  - Behaviour kept identical to the monolith: this endpoint only flips
 *    completed = true (it does not touch lastupdate_date) and a 400 with the
 *    DynamoDB message is returned when the id does not exist for this user.
 */
const { DynamoDBClient } = require('@aws-sdk/client-dynamodb');
const { DynamoDBDocumentClient, UpdateCommand } = require('@aws-sdk/lib-dynamodb');

const ddbClient = new DynamoDBClient({});
const docClient = DynamoDBDocumentClient.from(ddbClient, {
    marshallOptions: { removeUndefinedValues: true },
});

const TODO_TABLE = process.env.TODO_TABLE;
if (!TODO_TABLE) {
    throw new Error('TODO_TABLE environment variable is not set');
}

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
            UpdateExpression: 'set #field = :value',
            ExpressionAttributeNames: {
                '#field': 'completed',
            },
            ExpressionAttributeValues: {
                ':value': true,
            },
            ReturnValues: 'ALL_NEW',
        };

        const result = await docClient.send(new UpdateCommand(params));

        return makeResponse(200, {
            message: 'Todo item marked as completed',
            Attributes: result.Attributes,
        });
    } catch (error) {
        console.error('Failed to mark todo item as completed:', error);
        return makeResponse(400, { message: error.message });
    }
};

exports.lambda_handler = exports.handler;
