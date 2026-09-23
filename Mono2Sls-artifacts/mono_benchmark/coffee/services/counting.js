/**
 * Counting Service
 * Generates unique order numbers via atomic counter
 */

const database = require('./database');

/**
 * Get next order number (atomic increment)
 */
async function getOrderId(eventId) {
  const counterKey = `orderID-${eventId}`;
  const orderNumber = await database.incrementCounter('counting', { PK: counterKey });
  return orderNumber;
}

/**
 * Reset order number counter
 */
async function resetOrderId(eventId) {
  const counterKey = `orderID-${eventId}`;
  await database.updateItem('counting', { PK: counterKey }, { IDvalue: 0 });
}

module.exports = {
  getOrderId,
  resetOrderId
};
