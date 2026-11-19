const { DynamoDBClient } = require('@aws-sdk/client-dynamodb');
const { DynamoDBDocumentClient, QueryCommand } = require('@aws-sdk/lib-dynamodb');

// Initialize DynamoDB DocumentClient outside the handler for reuse
const ddbDocClient = DynamoDBDocumentClient.from(
  new DynamoDBClient({
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

    // Build query params for DynamoDB
    const params = {
      TableName: process.env.TODO_TABLE,
      KeyConditionExpression: '#userId = :userId',
      ExpressionAttributeNames: {
        '#userId': 'userId'
      },
      ExpressionAttributeValues: {
        ':userId': userId
      }
    };

    // Execute query
    const command = new QueryCommand(params);
    const result = await ddbDocClient.send(command);

    // Return formatted response
    return {
      statusCode: 200,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        Items: result.Items || [],
        Count: result.Count || 0
      })
    };
  } catch (error) {
    console.error('Failed to fetch todo list:', error);
    return {
      statusCode: 400,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: error.message || 'Internal server error' })
    };
  }
};
