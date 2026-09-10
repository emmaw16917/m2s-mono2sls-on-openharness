const path = require('path');
const { execFileSync } = require('child_process');
const { createAuthProvider } = require('../auth');

const TARGET = process.env.TARGET;
if (!TARGET) {
  throw new Error('TARGET env var is required. Example: TARGET=m2s_dsv3_bookstore');
}

const rawConfig = require(path.resolve(__dirname, `../config/${TARGET}.json`));
if (rawConfig.configType !== 'target') {
  throw new Error(`Config ${TARGET} is not a runnable target configuration`);
}

const config = applyEnvOverrides(rawConfig);
const contract = require(path.resolve(__dirname, '../config/normalized_business_contract.json'));
const authProvider = createAuthProvider(config);

let preparePromise = null;

function buildUrl(endpointPath) {
  const baseUrl = String(config.baseUrl).replace(/\/$/, '');
  const pathPrefix = config.pathPrefix ? `/${String(config.pathPrefix).replace(/^\/|\/$/g, '')}` : '';
  const normalizedPath = endpointPath.startsWith('/') ? endpointPath : `/${endpointPath}`;
  return `${baseUrl}${pathPrefix}${normalizedPath}`;
}

function buildPath(template, params = {}) {
  return Object.entries(params).reduce((current, [key, value]) => {
    return current.replace(`{${key}}`, encodeURIComponent(String(value)));
  }, template);
}

function getTimeout(name, fallback) {
  return (config.timeouts && config.timeouts[name]) || fallback;
}

function getCapability(name) {
  return Boolean(config.capabilities && config.capabilities[name]);
}

async function prepareTarget() {
  if (!preparePromise) {
    preparePromise = Promise.resolve().then(() => {
      execFileSync('node', ['seed-catalog.js', TARGET], {
        cwd: path.resolve(__dirname, '..'),
        env: process.env,
        stdio: 'inherit',
      });

      execFileSync('node', ['reset-bookstore-state.js', TARGET], {
        cwd: path.resolve(__dirname, '..'),
        env: process.env,
        stdio: 'inherit',
      });
    });
  }
  return preparePromise;
}

module.exports = {
  config,
  contract,
  authProvider,
  buildUrl,
  buildPath,
  getTimeout,
  getCapability,
  prepareTarget,
};

function applyEnvOverrides(baseConfig) {
  return {
    ...baseConfig,
    baseUrl: process.env.BASE_URL || baseConfig.baseUrl,
    pathPrefix: process.env.PATH_PREFIX || baseConfig.pathPrefix,
    region: process.env.AWS_REGION || baseConfig.region,
    booksTable: process.env.BOOKS_TABLE || baseConfig.booksTable,
    cartTable: process.env.CART_TABLE || baseConfig.cartTable,
    ordersTable: process.env.ORDERS_TABLE || baseConfig.ordersTable,
    bestsellersTable: process.env.BESTSELLERS_TABLE || baseConfig.bestsellersTable,
    usersTable: process.env.USERS_TABLE || baseConfig.usersTable,
    authConfig: {
      ...(baseConfig.authConfig || {}),
      userPoolId: process.env.USER_POOL_ID || baseConfig.authConfig?.userPoolId,
      clientId: process.env.USER_POOL_CLIENT_ID || baseConfig.authConfig?.clientId,
      region: process.env.AWS_REGION || baseConfig.authConfig?.region,
      defaultPassword: process.env.DEFAULT_PASSWORD || baseConfig.authConfig?.defaultPassword,
    },
  };
}
