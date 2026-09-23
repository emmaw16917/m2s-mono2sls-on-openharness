'use strict';
// Candidate rewrite of bestsellers-update-consumer: aggregate per bookId, then parallel UpdateItem.
const { DynamoDBClient } = require('@aws-sdk/client-dynamodb');
const { DynamoDBDocumentClient, UpdateCommand } = require('@aws-sdk/lib-dynamodb');
const docClient = DynamoDBDocumentClient.from(new DynamoDBClient({ region: process.env.AWS_REGION }));
const TABLE_NAME = process.env.BESTSELLERS_TABLE;
exports.handler = async (event) => {
  const totals = new Map();
  for (const r of event.Records || []) for (const b of JSON.parse(r.body).books || []) totals.set(b.bookId, (totals.get(b.bookId) || 0) + b.quantity);
  await Promise.all([...totals].map(([bookId, q]) => docClient.send(new UpdateCommand({ TableName: TABLE_NAME, Key: { bookId }, UpdateExpression: 'ADD salesCount :q', ExpressionAttributeValues: { ':q': q } }))));
  return { batchItemFailures: [] };
};
