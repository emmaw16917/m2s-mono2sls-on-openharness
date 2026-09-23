/**
 * todo-update-item — PUT /item/{id}
 *
 * Migrated from todo/routes/todo.js lines 117-165 (router.put('/item/:id')) with the
 * DynamoDB client that used to live in todo/config/db.js (lines 14-23).
 *
 * Migration notes:
 *  - req.params.id -> event.pathParameters.id, req.body -> JSON.parse(event.body).
 *  - router.use(authenticateToken) -> API Gateway Cognito authorizer; identity is
 *    read from event.requestContext.authorizer.claims, no auth code here.
 *  - The key carries "cognito-username", so a user can only ever update their own
 *    row (same ownership rule the monolith relied on).
 *  - aws-sdk v2 DocumentClient.update -> v3 DynamoDBDocumentClient + UpdateCommand.
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

        // Same validation order as the monolith: id first, then body fields.
        if (!id || !VALID_ID_RE.test(id)) {
            return makeResponse(400, {
                message: 'Invalid request: Invalid todo item ID',
            });
        }

        let body;
        try {
            body = JSON.parse(event.body || '{}');
        } catch (parseError) {
            return makeResponse(400, {
                message: 'Invalid request: request body is not valid JSON',
            });
        }
        const { item, completed } = body || {};

        if (item === undefined || completed === undefined) {
            return makeResponse(400, {
                message: 'Invalid request: Missing required fields',
            });
        }

        const params = {
            TableName: TODO_TABLE,
            Key: {
                'cognito-username': username,
                id: id,
            },
            UpdateExpression: 'set completed = :c, lastupdate_date = :lud, #i = :i',
            ExpressionAttributeNames: {
                '#i': 'item',
            },
            ExpressionAttributeValues: {
                ':c': completed,
                ':lud': new Date().toISOString(),
                ':i': item,
            },
            ReturnValues: 'ALL_NEW',
        };

        const result = await docClient.send(new UpdateCommand(params));

        return makeResponse(200, {
            message: 'Todo item updated successfully',
            Attributes: result.Attributes,
        });
    } catch (error) {
        console.error('Failed to update todo item:', error);
        return makeResponse(400, { message: error.message });
    }
};

exports.lambda_handler = exports.handler;
