/**
 * Validator Service
 * Generates and verifies QR codes, creates new orders
 */

const { customAlphabet } = require('nanoid');
const database = require('./database');
const orderManager = require('./orderManager');
const authService = require('./authService');

const TIME_INTERVAL = 5 * 60 * 1000; // 5 minutes
const CODE_LENGTH = 10;

const nanoid = customAlphabet('0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz', 21);

/**
 * Generate QR code
 * GET /qr-code?eventId=ABC (admin)
 */
async function getQRCode(req, res) {
  try {
    if (!req.query.eventId) {
      return res.status(400).json({ error: 'Bad Request', message: 'Missing eventId parameter' });
    }

    const eventId = req.query.eventId;

    const configKey = `config-${eventId}`;
    const eventConfig = await database.getItem('config', { PK: configKey });

    if (!eventConfig) {
      return res.status(400).json({
        error: 'Bad Request',
        message: 'No matching event ID found'
      });
    }

    const availableTokens = eventConfig.drinksPerBarcode;

    const CURRENT_TIME_BUCKET_ID = parseInt(Date.now() / TIME_INTERVAL);
    const bucketPK = `${eventId}-${CURRENT_TIME_BUCKET_ID}`;

    let bucket = await database.getItem('validator', { PK: bucketPK });

    if (!bucket) {
      bucket = {
        PK: bucketPK,
        last_id: CURRENT_TIME_BUCKET_ID,
        last_code: customAlphabet('0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ', CODE_LENGTH)(),
        start_ts: TIME_INTERVAL * CURRENT_TIME_BUCKET_ID,
        start_full: new Date(TIME_INTERVAL * CURRENT_TIME_BUCKET_ID).toString(),
        end_ts: (TIME_INTERVAL * CURRENT_TIME_BUCKET_ID) + (TIME_INTERVAL - 1),
        end_full: new Date((TIME_INTERVAL * CURRENT_TIME_BUCKET_ID) + (TIME_INTERVAL - 1)).toString(),
        availableTokens: parseInt(availableTokens),
        eventId
      };

      await database.putItem('validator', bucket);
    }

    res.status(200).json({
      bucket,
      qrCode: bucket.last_code,
      message: 'QR code generated'
    });
  } catch (error) {
    console.error('[Validator] Failed to generate QR code:', error);
    res.status(500).json({ error: 'Internal Server Error', message: error.message });
  }
}

/**
 * Verify QR code and create order
 * POST /qr-code?eventId=ABC&token=xxx (authenticated user)
 */
async function verifyQRCode(req, res) {
  try {
    if (!req.query || !req.query.eventId) {
      return res.status(400).json({ error: 'Bad Request', message: 'Missing eventId parameter' });
    }

    if (!req.query.token) {
      return res.status(400).json({ error: 'Bad Request', message: 'Missing token parameter' });
    }

    const eventId = req.query.eventId;
    const token = req.query.token;
    const userId = authService.getUserId(req);

    const CURRENT_TIME_BUCKET_ID = parseInt(Date.now() / TIME_INTERVAL);
    const bucketPK = `${eventId}-${CURRENT_TIME_BUCKET_ID}`;

    const bucket = await database.getItem('validator', { PK: bucketPK });

    if (!bucket) {
      return res.status(400).json({ error: 'Invalid Code', message: 'Invalid verification code' });
    }

    if (token !== bucket.last_code) {
      return res.status(400).json({ error: 'Invalid Code', message: 'Invalid verification code' });
    }

    if (bucket.availableTokens < 1) {
      return res.status(200).json({ error: 'No Tokens', message: 'No tokens remaining' });
    }

    bucket.availableTokens--;
    await database.updateItem('validator', { PK: bucketPK }, { availableTokens: bucket.availableTokens });

    const orderId = nanoid();

    const result = await orderManager.createOrder(orderId, userId, eventId);

    if (!result.success) {
      return res.status(400).json({
        error: 'Shop Unavailable',
        message: 'Shop is not open, cannot create order'
      });
    }

    res.status(200).json({
      orderId,
      message: 'Order created',
      availableTokens: bucket.availableTokens
    });
  } catch (error) {
    console.error('[Validator] Failed to verify QR code:', error);
    res.status(500).json({ error: 'Internal Server Error', message: error.message });
  }
}

module.exports = {
  getQRCode,
  verifyQRCode
};
