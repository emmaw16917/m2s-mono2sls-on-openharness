/**
 * Bestsellers Update Utility Functions
 * Uses DynamoDB atomic counter to update bestseller rankings
 */

const { docClient } = require('./dynamodb');
const { dynamodb: dynamodbConfig } = require('../config');

/**
 * Update Bestseller List
 * @param {string} bookId - Book ID
 * @param {number} quantity - Quantity sold
 * @returns {Promise<boolean>} Whether update was successful
 */
async function updateBestSeller(bookId, quantity) {
  try {
    const params = {
      TableName: dynamodbConfig.bestsellersTable,
      Key: { bookId: bookId },
      UpdateExpression: 'ADD salesCount :qty',
      ExpressionAttributeValues: {
        ':qty': quantity
      }
    };

    await docClient.update(params).promise();
    console.log(`Updated bestseller: bookId=${bookId}, quantity=${quantity}`);
    return true;
  } catch (error) {
    console.error('Failed to update bestseller:', error);
    return false;
  }
}

/**
 * Batch Update Bestsellers
 * @param {Array} books - Array of books, each element containing {bookId, quantity}
 * @returns {Promise<Object>} Update result statistics
 */
async function updateBestSellers(books) {
  if (!books || books.length === 0) {
    return { success: 0, failed: 0 };
  }

  let success = 0;
  let failed = 0;

  for (const book of books) {
    const result = await updateBestSeller(book.bookId, book.quantity);
    if (result) {
      success++;
    } else {
      failed++;
    }
  }

  return { success, failed };
}

module.exports = {
  updateBestSeller,
  updateBestSellers
};
