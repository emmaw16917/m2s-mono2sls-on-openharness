const path = require('path');
const { createAuthProvider } = require('../auth');

const TARGET = process.env.TARGET;
if (!TARGET) {
  throw new Error('TARGET env var is required. Example: TARGET=m2s_dsv3_airline');
}

const config = require(path.resolve(__dirname, `../config/${TARGET}.json`));
if (config.configType !== 'target') {
  throw new Error(`Config ${TARGET} is not a runnable target configuration`);
}

const contract = require(path.resolve(__dirname, '../config/normalized_business_contract.json'));
const authProvider = createAuthProvider(config);

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

function getCapability(name) {
  return Boolean(config.capabilities && config.capabilities[name]);
}

function getTimeout(name, fallback) {
  return (config.timeouts && config.timeouts[name]) || fallback;
}

module.exports = {
  config,
  contract,
  authProvider,
  buildUrl,
  buildPath,
  getCapability,
  getTimeout,
};
