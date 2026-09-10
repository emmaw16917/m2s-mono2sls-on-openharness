const AWS = require('aws-sdk');
const { DynamoDBDocumentClient, UpdateCommand } = require('@aws-sdk/lib-dynamodb');

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

    // Build update params for DynamoDB
    const params = {
      TableName: process.env.TODO_TABLE,
      Key: {
        userId: userId,
        id: id
      },
      UpdateExpression: 'set #completed = :value',
      ExpressionAttributeNames: {
        '#completed': 'completed'
      },
      ExpressionAttributeValues: {
        ':value': true
      },
      ReturnValues: 'ALL_NEW'
    };

    // Execute update operation
    const command = new UpdateCommand(params);
    const result = await ddbDocClient.send(command);

    // Return formatted response
    return {
      statusCode: 200,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        message: 'Todo item marked as completed',
        Attributes: result.Attributes
      })
    };
  } catch (error) {
    console.error('Failed to mark todo item as completed:', error);
    return {
      statusCode: 400,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: error.message || 'Internal server error' })
    };
  }
};