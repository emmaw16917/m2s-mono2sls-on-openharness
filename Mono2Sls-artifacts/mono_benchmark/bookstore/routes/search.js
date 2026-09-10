/**
 * Search Routes
 * Provides full-text search functionality using DynamoDB scan
 */

const express = require('express');
const router = express.Router();
const { docClient } = require('../utils/dynamodb');
const { dynamodb: dynamodbConfig } = require('../config');

/**
 * GET /search?q=keyword
 * Search books (by name, author, category)
 * Query params: q - Search keyword
 */
router.get('/', async (req, res, next) => {
  try {
    const query = req.query.q;

    if (!query) {
      return res.status(400).json({
        error: 'Missing required query parameter: q'
      });
    }

    const params = {
      TableName: dynamodbConfig.booksTable
    };

    const data = await docClient.scan(params).promise();

    const lowerQuery = query.toLowerCase();
    const results = (data.Items || []).filter(item => {
      return (
        (item.name && item.name.toLowerCase().includes(lowerQuery)) ||
        (item.author && item.author.toLowerCase().includes(lowerQuery)) ||
        (item.category && item.category.toLowerCase().includes(lowerQuery))
      );
    });

    res.json({
      total: results.length,
      hits: results.map(item => ({
        _source: item
      }))
    });
  } catch (error) {
    console.error('Error in GET /search:', error);
    next(error);
  }
});

module.exports = router;
