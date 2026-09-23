/**
 * Application Configuration File
 * Manages all environment variables and configuration parameters
 */

// Load environment variables
require('dotenv').config();

const PORT = process.env.PORT || 3000;
const NODE_ENV = process.env.NODE_ENV || 'development';
const JWT_SECRET = process.env.JWT_SECRET || 'your-secret-key-change-in-production';
const JWT_REFRESH_SECRET = process.env.JWT_REFRESH_SECRET || 'your-refresh-secret-key-change-in-production';
const JWT_ACCESS_TOKEN_EXPIRY = process.env.JWT_ACCESS_TOKEN_EXPIRY || '1h';
const JWT_REFRESH_TOKEN_EXPIRY = process.env.JWT_REFRESH_TOKEN_EXPIRY || '7d';
const AUTH_DEV_MODE = process.env.AUTH_DEV_MODE === 'true';
const AWS_REGION = process.env.AWS_REGION || 'us-east-1';
const AWS_ACCESS_KEY_ID = process.env.AWS_ACCESS_KEY_ID;
const AWS_SECRET_ACCESS_KEY = process.env.AWS_SECRET_ACCESS_KEY;
const BOOKS_TABLE = process.env.BOOKS_TABLE || 'Bookstore-Books';
const CART_TABLE = process.env.CART_TABLE || 'Bookstore-Cart';
const ORDERS_TABLE = process.env.ORDERS_TABLE || 'Bookstore-Orders';
const USERS_TABLE = process.env.USERS_TABLE || 'Bookstore-Users';
const BESTSELLERS_TABLE = process.env.BESTSELLERS_TABLE || 'Bookstore-Bestsellers';

const config = {
  // Server configuration
  port: PORT,
  nodeEnv: NODE_ENV,

  // JWT configuration
  jwt: {
    secret: JWT_SECRET,
    refreshSecret: JWT_REFRESH_SECRET,
    accessTokenExpiry: JWT_ACCESS_TOKEN_EXPIRY,
    refreshTokenExpiry: JWT_REFRESH_TOKEN_EXPIRY
  },

  // Auth mode configuration
  auth: {
    devMode: AUTH_DEV_MODE
  },

  // AWS configuration
  aws: {
    region: AWS_REGION,
    accessKeyId: AWS_ACCESS_KEY_ID,
    secretAccessKey: AWS_SECRET_ACCESS_KEY
  },

  // DynamoDB table names
  dynamodb: {
    booksTable: BOOKS_TABLE,
    cartTable: CART_TABLE,
    ordersTable: ORDERS_TABLE,
    usersTable: USERS_TABLE,
    bestsellersTable: BESTSELLERS_TABLE
  }
};

module.exports = config;
