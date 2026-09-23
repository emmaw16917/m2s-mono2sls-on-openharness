/**
 * Order Manager Service
 * Handles order CRUD operations and state management
 *
 * Order state machine:
 *   CREATED -> SUBMITTED -> MAKING -> COMPLETED
 *                  |          |
 *              CANCELLED  CANCELLED
 */

const database = require('./database');
const authService = require('./authService');
const orderJourney = require('./orderJourney');
const counting = require('./counting');

// ==================== Helpers ====================

/**
 * Validate drink order against the menu
 */
function sanitizeOrder(order, menu) {
  if (!order || !order.drink) return false;

  const drinkItem = menu.find(item => item.drink === order.drink);
  if (!drinkItem) return false;

  if (order.modifiers && order.modifiers.length > 0) {
    for (const modifier of order.modifiers) {
      let found = false;
      for (const allowedModifier of drinkItem.modifiers) {
        if (allowedModifier.Options && allowedModifier.Options.includes(modifier)) {
          found = true;
          break;
        }
      }
      if (!found) return false;
    }
  }

  return true;
}

// ==================== Order Creation ====================

/**
 * POST /orders endpoint handler
 */
async function createOrderHandler(req, res) {
  try {
    const { orderId, eventId } = req.body;
    const userId = authService.getUserId(req);

    if (!orderId || !eventId) {
      return res.status(400).json({ error: 'Bad Request', message: 'Missing orderId or eventId' });
    }

    const result = await createOrder(orderId, userId, eventId);

    if (!result.success) {
      return res.status(400).json({
        error: 'Shop Unavailable',
        message: 'Shop is not open, cannot create order'
      });
    }

    res.status(201).json({ orderId, message: 'Order created' });
  } catch (error) {
    console.error('[OrderManager] Failed to create order:', error);
    res.status(500).json({ error: 'Internal Server Error', message: error.message });
  }
}

/**
 * Core order creation logic.
 * Checks shop status, writes to DB with CREATED state.
 */
async function createOrder(orderId, userId, eventId) {
  const config = await database.getItem('config', { PK: `config-${eventId}` });

  if (!config || config.storeOpen !== true) {
    return { success: false, reason: 'shop_unavailable' };
  }

  const order = {
    PK: 'orders',
    SK: orderId,
    USERID: userId,
    ORDERSTATE: `${eventId}-CREATED`,
    robot: false,
    TS: Date.now()
  };

  await database.putItem('orders', order);
  await orderJourney.recordEvent('Order.Created', { orderId, userId, eventId });

  return { success: true };
}

// ==================== Query Endpoints ====================

/**
 * List orders by state
 * GET /orders?state=CREATED&eventId=ABC&maxItems=100
 */
async function getOrders(req, res) {
  try {
    const { state, eventId, maxItems = 100 } = req.query;

    if (!state || !eventId) {
      return res.status(400).json({
        error: 'Bad Request',
        message: 'Missing state or eventId parameter'
      });
    }

    const orders = await database.query('orders', {
      IndexName: 'GSI-status',
      ORDERSTATE: `${eventId}-${state}`,
      Limit: parseInt(maxItems)
    });

    res.status(200).json(orders);
  } catch (error) {
    console.error('[OrderManager] Failed to list orders:', error);
    res.status(500).json({ error: 'Internal Server Error', message: error.message });
  }
}

/**
 * Get current user's orders
 * GET /myOrders
 */
async function getMyOrders(req, res) {
  try {
    const userId = authService.getUserId(req);

    const orders = await database.query('orders', {
      IndexName: 'GSI-userId',
      USERID: userId,
      Limit: 100
    });

    res.status(200).json(orders);
  } catch (error) {
    console.error('[OrderManager] Failed to get user orders:', error);
    res.status(500).json({ error: 'Internal Server Error', message: error.message });
  }
}

/**
 * Get single order details
 * GET /orders/:id
 */
async function getOrderById(req, res) {
  try {
    const orderId = req.params.id;
    const order = await database.getItem('orders', { PK: 'orders', SK: orderId });

    if (!order) {
      return res.status(404).json({ error: 'Not Found', message: 'Order not found' });
    }

    res.status(200).json({
      orderId: order.SK,
      drinkOrder: order.drinkOrder ? JSON.parse(order.drinkOrder) : null,
      orderState: order.ORDERSTATE,
      orderNumber: order.orderNumber,
      baristaUserId: order.baristaUserId,
      TS: order.TS
    });
  } catch (error) {
    console.error('[OrderManager] Failed to get order details:', error);
    res.status(500).json({ error: 'Internal Server Error', message: error.message });
  }
}

// ==================== Order Updates ====================

/**
 * Update order
 * PUT /orders/:id?action=complete&eventId=ABC
 */
async function updateOrder(req, res) {
  try {
    const orderId = req.params.id;
    const { action, eventId } = req.query;
    const userId = authService.getUserId(req);

    if (!eventId) {
      return res.status(400).json({ error: 'Bad Request', message: 'Missing eventId parameter' });
    }

    const order = await database.getItem('orders', { PK: 'orders', SK: orderId });

    if (!order) {
      return res.status(404).json({ error: 'Not Found', message: 'Order not found' });
    }

    if (action === 'complete') {
      await handleCompleteOrder(orderId, eventId, order, res);
    } else if (action === 'cancel') {
      await handleCancelOrder(orderId, eventId, order, res);
    } else if (action === 'make') {
      await handleMakeOrder(orderId, eventId, userId, order, res);
    } else if (action === 'unmake') {
      await handleUnmakeOrder(orderId, eventId, order, res);
    } else if (!action || action === 'submit') {
      await handleSubmitOrder(orderId, eventId, userId, req.body, order, res);
    } else {
      res.status(400).json({ error: 'Bad Request', message: 'Invalid action' });
    }
  } catch (error) {
    console.error('[OrderManager] Failed to update order:', error);
    res.status(500).json({ error: 'Internal Server Error', message: error.message });
  }
}

/**
 * Customer submits drink selection
 * CREATED -> SUBMITTED
 */
async function handleSubmitOrder(orderId, eventId, userId, body, order, res) {
  const config = await database.getItem('config', { PK: `config-${eventId}` });
  if (!config) {
    return res.status(400).json({ error: 'Bad Request', message: 'Config not found' });
  }

  if (!sanitizeOrder(body, config.menu)) {
    return res.status(400).json({ error: 'Invalid Order', message: 'Invalid order' });
  }

  if (body.userId !== order.USERID) {
    return res.status(403).json({ error: 'Forbidden', message: 'User ID mismatch' });
  }

  const orderNumber = await counting.getOrderId(eventId);

  await database.updateItem('orders', { PK: 'orders', SK: orderId }, {
    drinkOrder: JSON.stringify(body),
    orderNumber,
    ORDERSTATE: `${eventId}-SUBMITTED`
  });

  await orderJourney.recordEvent('Order.Submitted', { orderId, userId, eventId, orderNumber });

  res.status(200).json({ message: 'Order submitted', orderId, orderNumber });
}

/**
 * Barista claims an order
 * SUBMITTED -> MAKING
 */
async function handleMakeOrder(orderId, eventId, baristaUserId, order, res) {
  await database.updateItem('orders', { PK: 'orders', SK: orderId }, {
    baristaUserId,
    ORDERSTATE: `${eventId}-MAKING`
  });

  await orderJourney.recordEvent('Order.Making', {
    orderId, eventId, baristaUserId, userId: order.USERID
  });

  res.status(200).json({ message: 'Order claimed', orderId });
}

/**
 * Barista unclaims an order
 * MAKING -> SUBMITTED
 */
async function handleUnmakeOrder(orderId, eventId, order, res) {
  await database.updateItem('orders', { PK: 'orders', SK: orderId }, {
    baristaUserId: '',
    ORDERSTATE: `${eventId}-SUBMITTED`
  });

  res.status(200).json({ message: 'Order unclaimed', orderId });
}

/**
 * Complete an order
 * MAKING -> COMPLETED
 */
async function handleCompleteOrder(orderId, eventId, order, res) {
  await database.updateItem('orders', { PK: 'orders', SK: orderId }, {
    ORDERSTATE: `${eventId}-COMPLETED`
  });

  await orderJourney.recordEvent('Order.Completed', {
    orderId, eventId, userId: order.USERID
  });

  res.status(200).json({ message: 'Order completed', orderId });
}

/**
 * Cancel an order
 * Any state -> CANCELLED
 */
async function handleCancelOrder(orderId, eventId, order, res) {
  await database.updateItem('orders', { PK: 'orders', SK: orderId }, {
    ORDERSTATE: `${eventId}-CANCELLED`
  });

  await orderJourney.recordEvent('Order.Cancelled', {
    orderId, eventId, userId: order.USERID
  });

  res.status(200).json({ message: 'Order cancelled', orderId });
}

module.exports = {
  createOrder,
  createOrderHandler,
  getOrders,
  getMyOrders,
  getOrderById,
  updateOrder
};
