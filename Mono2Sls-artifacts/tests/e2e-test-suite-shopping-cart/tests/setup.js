const path = require('path');
const { execFileSync } = require('child_process');
const { createAuthProvider } = require('../auth');
const { loadTargetConfig } = require('../config/load-target-config');
const { loadRuntimeState } = require('../state/shopping-cart-state');

const TARGET = process.env.TARGET;
if (!TARGET) {
  throw new Error('TARGET env var is required. Example: TARGET=m2s_dsv3_shopping_cart');
}

const config = loadTargetConfig(TARGET);
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
      execFileSync('node', ['seed-products.js', TARGET], {
        cwd: path.resolve(__dirname, '..'),
        env: process.env,
        stdio: 'inherit',
      });
    });
  }
  return preparePromise;
}

function getRuntimeState() {
  return loadRuntimeState(TARGET);
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
  getRuntimeState,
};
