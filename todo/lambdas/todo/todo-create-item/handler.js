/**
 * todo-create-item — POST /item
 *
 * Migrated from todo/routes/todo.js lines 77-115 (router.post('/item')) with the
 * DynamoDB client that used to live in todo/config/db.js (lines 14-23).
 *
 * Migration notes:
 *  - req.body -> JSON.parse(event.body) (API Gateway proxy body is a JSON string).
 *  - router.use(authenticateToken) -> API Gateway Cognito authorizer; identity is
 *    read from event.requestContext.authorizer.claims, no auth code here.
 *  - uuid v1 id generation is kept (same package the monolith used), aws-sdk v2
 *    DocumentClient.put -> v3 DynamoDBDocumentClient + PutCommand.
 */
const { DynamoDBClient } = require('@aws-sdk/client-dynamodb');
const { DynamoDBDocumentClient, PutCommand } = require('@aws-sdk/lib-dynamodb');
const { v1: uuidv1 } = require('uuid');

const ddbClient = new DynamoDBClient({});
const docClient = DynamoDBDocumentClient.from(ddbClient, {
    marshallOptions: { removeUndefinedValues: true },
});

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

        let body;
        try {
            body = JSON.parse(event.body || '{}');
        } catch (parseError) {
            return makeResponse(400, {
                message: 'Invalid request: request body is not valid JSON',
            });
        }

        const { item, completed } = body || {};

        if (!item) {
            return makeResponse(400, {
                message: 'Invalid request: Todo item content cannot be empty',
            });
        }

        const now = new Date().toISOString();
        const todoItem = {
            'cognito-username': username,
            id: uuidv1(),
            item: item,
            completed: completed || false,
            creation_date: now,
            lastupdate_date: now,
        };

        await docClient.send(
            new PutCommand({
                TableName: TODO_TABLE,
                Item: todoItem,
            })
        );

        return makeResponse(200, {
            message: 'Todo item created successfully',
            item: todoItem,
        });
    } catch (error) {
        console.error('Failed to create todo item:', error);
        return makeResponse(400, { message: error.message });
    }
};

exports.lambda_handler = exports.handler;
