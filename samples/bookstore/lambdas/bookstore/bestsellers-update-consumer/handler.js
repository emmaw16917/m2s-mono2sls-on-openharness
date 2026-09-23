/**
 * bestsellers-update-consumer :: SQS trigger on BestsellerUpdateQueue
 *
 * Migrated from bookstore/utils/bestsellers.js (updateBestSeller lines 15-33 and
 * updateBestSellers lines 40-58), which the monolith called in-process from
 * routes/orders.js without awaiting it. The counter logic itself is unchanged:
 * one DynamoDB atomic "ADD salesCount :qty" UpdateItem per purchased book.
 *
 * Message contract (published by orders-create):
 *   { "orderId": "<uuid>", "books": [ { "bookId", "price", "quantity" }, ... ] }
 *
 * Error-handling change forced by the platform, on purpose:
 *   updateBestSeller() returned false (and only logged) on failure, which is safe in
 *   a long-lived process but loses data in a consumer. Here a failing item makes the
 *   handler throw after the batch has been attempted, so SQS redrives the message and
 *   it lands in the configured DLQ after maxReceiveCount (3) instead of vanishing.
 *   Consequence to keep in mind: "ADD" is not idempotent, so a retried message can
 *   double-count the books that already succeeded before the failure.
 *
 * This handler is not an API Gateway target: it returns no statusCode/headers/body.
 */
'use strict';

const { DynamoDBClient } = require('@aws-sdk/client-dynamodb');
const { DynamoDBDocumentClient, UpdateCommand } = require('@aws-sdk/lib-dynamodb');

const TABLE_NAME = process.env.BESTSELLERS_TABLE;
if (!TABLE_NAME) throw new Error('BESTSELLERS_TABLE environment variable is not set');

// Initialised once per cold start and reused across invocations.
const ddbClient = new DynamoDBClient({ region: process.env.AWS_REGION });
const docClient = DynamoDBDocumentClient.from(ddbClient, {
  marshallOptions: { removeUndefinedValues: true }
});

/**
 * bookstore/utils/bestsellers.js :: updateBestSeller(bookId, quantity)
 * DynamoDB atomic counter increment; throws instead of returning false.
 */
async function updateBestSeller(bookId, quantity) {
  await docClient.send(new UpdateCommand({
    TableName: TABLE_NAME,
    Key: { bookId: bookId },
    UpdateExpression: 'ADD salesCount :qty',
    ExpressionAttributeValues: {
      ':qty': quantity
    }
  }));
  console.log(`Updated bestseller: bookId=${bookId}, quantity=${quantity}`);
}

exports.handler = async function handler(event, context) {
  const records = event.Records || [];
  let success = 0;
  let failed = 0;
  let firstError = null;

  for (const record of records) {
    let payload;
    try {
      payload = JSON.parse(record.body);
    } catch (parseError) {
      // Unparseable body will never succeed on retry; count it so the message is
      // redriven to the DLQ rather than silently acknowledged.
      console.error('Failed to parse bestseller update message:', parseError);
      failed += 1;
      firstError = firstError || parseError;
      continue;
    }

    const books = (payload && Array.isArray(payload.books)) ? payload.books : [];
    if (books.length === 0) {
      continue;
    }

    for (const book of books) {
      try {
        await updateBestSeller(book.bookId, book.quantity);
        success += 1;
      } catch (error) {
        console.error('Failed to update bestseller:', error);
        failed += 1;
        firstError = firstError || error;
      }
    }
  }

  console.log(`Bestseller update batch finished: success=${success}, failed=${failed}`);

  if (failed > 0) {
    throw new Error(
      `Bestseller update failed for ${failed} book(s); first error: ` +
      (firstError ? firstError.message : 'unknown')
    );
  }

  return { success, failed };
};

// The blueprint declares the SAM handler string as "handler.lambda_handler"; the
// alias keeps both "handler.handler" and "handler.lambda_handler" resolvable.
exports.lambda_handler = exports.handler;
