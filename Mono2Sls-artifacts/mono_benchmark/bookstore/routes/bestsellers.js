/**
 * Bestsellers Routes
 * Handles bestseller list queries
 * Uses DynamoDB Bestsellers table to store and query bestseller data
 */

const express = require('express');
const router = express.Router();
const { docClient } = require('../utils/dynamodb');
const { dynamodb: dynamodbConfig } = require('../config');

/**
 * GET /bestsellers
 * Get Bestsellers List (Top 20)
 */
router.get('/', async (req, res, next) => {
  try {
    const params = {
      TableName: dynamodbConfig.bestsellersTable
    };

    const data = await docClient.scan(params).promise();

    const sorted = (data.Items || [])
      .sort((a, b) => (b.salesCount || 0) - (a.salesCount || 0))
      .slice(0, 20)
      .map(item => item.bookId);

    res.json(sorted);
  } catch (error) {
    console.error('Error in GET /bestsellers:', error);
    res.json([]);
  }
});

module.exports = router;
