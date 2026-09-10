const AWS = require('aws-sdk');
const { DynamoDBDocumentClient, PutCommand } = require('@aws-sdk/lib-dynamodb');
const { v1: uuidv1 } = require('uuid');

// Initialize DynamoDB DocumentClient outside the handler for reuse
const ddbDocClient = DynamoDBDocumentClient.from(
  new AWS.DynamoDB({
    region: process.env.AWS_REGION || 'us-east-1'
  })
);

exports.handler = async function(event, context) {
  try {
    // Extract user ID from Cognito authorizer claims
    const userId = event.requestContext.authorizer.claims.sub;

    // Validate userId presence
    if (!userId) {
      return {
        statusCode: 401,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: 'Unauthorized: Missing user ID in token' })
      };
    }

    // Parse request body
    let body;
    try {
      body = JSON.parse(event.body);
    } catch (e) {
      return {
        statusCode: 400,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: 'Invalid JSON in request body' })
      };
    }

    const { item, completed } = body;

    // Validate required field
    if (!item) {
      return {
        statusCode: 400,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: 'Invalid request: Todo item content cannot be empty' })
      };
    }

    const now = new Date().toISOString();
    const todoItem = {
      userId: userId,
      id: uuidv1(),
      item: item,
      completed: completed || false,
      creation_date: now,
      lastupdate_date: now
    };

    // Build put params for DynamoDB
    const params = {
      TableName: process.env.TODO_TABLE,
      Item: todoItem
    };

    // Execute put operation
    const command = new PutCommand(params);
    await ddbDocClient.send(command);

    // Return formatted response
    return {
      statusCode: 200,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        message: 'Todo item created successfully',
        item: todoItem
      })
    };
  } catch (error) {
    console.error('Failed to create todo item:', error);
    return {
      statusCode: 400,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: error.message || 'Internal server error' })
    };
  }
};