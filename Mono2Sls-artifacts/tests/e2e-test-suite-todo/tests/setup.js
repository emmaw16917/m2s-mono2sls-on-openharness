const path = require('path');
const { createAuthProvider } = require('../auth');

const TARGET = process.env.TARGET;
if (!TARGET) {
  throw new Error('TARGET env var is required. Set it to a config name, e.g. TARGET=m2s_dsv3');
}

const config = require(path.resolve(__dirname, `../config/${TARGET}.json`));
if (config.configType !== 'target') {
  throw new Error(`Config ${TARGET} is not a runnable target configuration`);
}

const contract = require(path.resolve(__dirname, '../config/normalized_business_contract.json'));
const authProvider = createAuthProvider(config);

function buildUrl(endpointPath) {
  const baseUrl = config.baseUrl.replace(/\/$/, '');
  const pathPrefix = config.pathPrefix ? config.pathPrefix.replace(/\/$/, '') : '';
  return `${baseUrl}${pathPrefix}${endpointPath}`;
}

module.exports = { config, contract, authProvider, buildUrl };
