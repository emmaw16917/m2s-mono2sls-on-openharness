/**
 * presso monolith application entry point
 * Coffee order management system
 */

const express = require('express');
const cors = require('cors');
const app = express();

const database = require('./services/database');
const validatorService = require('./services/validator');
const orderManagerService = require('./services/orderManager');
const configService = require('./services/config');
const orderJourneyService = require('./services/orderJourney');
const authService = require('./services/authService');

app.use(cors());
app.use(express.json());
app.use(express.urlencoded({ extended: true }));

database.initialize().catch(err => {
  console.error('Database initialization failed:', err);
});

app.use((req, res, next) => {
  console.log(`[${new Date().toISOString()}] ${req.method} ${req.path}`);
  next();
});

// ==================== Authentication API ====================

app.post('/register', (req, res) => {
  authService.register(req, res);
});

app.post('/login', (req, res) => {
  authService.login(req, res);
});

app.get('/me', authService.authenticateToken, (req, res) => {
  authService.getCurrentUser(req, res);
});

// ==================== Validator Service API ====================

app.get('/qr-code', authService.authenticateToken, authService.requireAdmin, (req, res) => {
  validatorService.getQRCode(req, res);
});

app.post('/qr-code', authService.authenticateToken, (req, res) => {
  validatorService.verifyQRCode(req, res);
});

// ==================== Order Manager API ====================

app.post('/orders', authService.authenticateToken, (req, res) => {
  orderManagerService.createOrderHandler(req, res);
});

app.get('/orders', authService.authenticateToken, authService.requireAdmin, (req, res) => {
  orderManagerService.getOrders(req, res);
});

app.get('/myOrders', authService.authenticateToken, (req, res) => {
  orderManagerService.getMyOrders(req, res);
});

app.get('/orders/:id', authService.authenticateToken, authService.requireAdmin, (req, res) => {
  orderManagerService.getOrderById(req, res);
});

app.put('/orders/:id', authService.authenticateToken, authService.requireAdmin, (req, res) => {
  orderManagerService.updateOrder(req, res);
});

// ==================== Config Service API ====================

app.get('/config', (req, res) => {
  configService.getConfig(req, res);
});

app.put('/config', authService.authenticateToken, authService.requireAdmin, (req, res) => {
  configService.updateConfig(req, res);
});

app.get('/config/all', (req, res) => {
  configService.scanConfig(req, res);
});

// ==================== Order Journey API ====================

app.get('/order-journey/:orderId', async (req, res) => {
  try {
    const events = await orderJourneyService.getOrderJourney(req.params.orderId);
    res.status(200).json({
      orderId: req.params.orderId,
      eventCount: events.length,
      events
    });
  } catch (error) {
    console.error('Failed to get order journey:', error);
    res.status(500).json({ error: 'Internal Server Error', message: error.message });
  }
});

// ==================== Error Handling ====================

app.use((req, res) => {
  res.status(404).json({
    error: 'Not Found',
    message: `Cannot ${req.method} ${req.path}`
  });
});

app.use((err, req, res, next) => {
  console.error('Error:', err);
  res.status(err.status || 500).json({
    error: err.message || 'Internal Server Error',
    ...(process.env.NODE_ENV === 'development' && { stack: err.stack })
  });
});

// ==================== Server Startup ====================

const PORT = process.env.PORT || 3000;

app.listen(PORT, () => {
  console.log('='.repeat(50));
  console.log(`presso started — http://localhost:${PORT}`);
  console.log('='.repeat(50));
  console.log('\nAPI Endpoints:');
  console.log('  POST /register                   - User registration');
  console.log('  POST /login                      - User login');
  console.log('  GET  /me                         - Current user info [auth]');
  console.log('  GET  /qr-code                    - Generate QR code [admin]');
  console.log('  POST /qr-code                    - Verify QR code & create order [auth]');
  console.log('  POST /orders                     - Create order [auth]');
  console.log('  GET  /orders                     - List orders [admin]');
  console.log('  GET  /myOrders                   - My orders [auth]');
  console.log('  GET  /orders/:id                 - Order details [admin]');
  console.log('  PUT  /orders/:id                 - Update order [admin]');
  console.log('  GET  /config                     - Get config');
  console.log('  PUT  /config                     - Update config [admin]');
  console.log('  GET  /config/all                 - All configs');
  console.log('  GET  /order-journey/:orderId     - Order journey');
  console.log('='.repeat(50));
});

process.on('SIGTERM', () => process.exit(0));
process.on('SIGINT', () => process.exit(0));

module.exports = app;
