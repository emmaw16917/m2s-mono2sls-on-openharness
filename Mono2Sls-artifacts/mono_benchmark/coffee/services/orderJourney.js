/**
 * Order Journey Service
 * Records audit log of order state changes
 */

const database = require('./database');

/**
 * Record an order event
 */
async function recordEvent(detailType, detail) {
  try {
    const orderId = detail?.orderId;
    if (!orderId) return;

    const eventRecord = {
      PK: orderId,
      SK: new Date().toISOString(),
      detailType,
      orderDetails: JSON.stringify(detail),
      timestamp: Date.now()
    };

    await database.putItem('orderJourneyEvents', eventRecord);
  } catch (error) {
    console.error('[OrderJourney] Failed to record event:', error);
  }
}

/**
 * Get full order journey
 */
async function getOrderJourney(orderId) {
  const events = await database.query('orderJourneyEvents', { PK: orderId });
  events.sort((a, b) => new Date(a.SK) - new Date(b.SK));
  return events;
}

module.exports = {
  recordEvent,
  getOrderJourney
};
