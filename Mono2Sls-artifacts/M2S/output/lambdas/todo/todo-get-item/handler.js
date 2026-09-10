const AWS = require('aws-sdk');
const { DynamoDBDocumentClient, GetCommand } = require('@aws-sdk/lib-dynamodb');

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

    // Extract path parameter
    const id = event.pathParameters?.id;

    // Validate ID format
    if (!id || !/^[-\w]+$/.test(id)) {
      return {
        statusCode: 400,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: 'Invalid request: Invalid todo item ID' })
      };
    }

    // Build get params for DynamoDB
    const params = {
      TableName: process.env.TODO_TABLE,
      Key: {
        userId: userId,
        id: id
      }
    };

    // Execute get operation
    const command = new GetCommand(params);
    const result = await ddbDocClient.send(command);

    // Handle not found
    if (!result.Item) {
      return {
        statusCode: 404,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: 'Todo item not found' })
      };
    }

    // Return formatted response
    return {
      statusCode: 200,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(result)
    };
  } catch (error) {
    console.error('Failed to fetch todo item:', error);
    return {
      statusCode: 400,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: error.message || 'Internal server error' })
    };
  }
};