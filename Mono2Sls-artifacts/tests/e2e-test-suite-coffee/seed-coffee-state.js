const path = require('path');
const { prepareTargetState } = require('./state/coffee-state');

const target = process.env.TARGET || process.argv[2];
if (!target) {
  throw new Error('TARGET env var or CLI arg is required. Example: TARGET=cursor_sonnet45tk_coffee node seed-coffee-state.js');
}

const rawConfig = require(path.resolve(__dirname, `config/${target}.json`));
const config = applyEnvOverrides(rawConfig);
const eventId = process.env.TEST_EVENT_ID || `E2E_${Date.now()}`;

prepareTargetState(config, eventId)
  .then(() => {
    console.log(`Coffee state prepared for ${target} with eventId=${eventId}`);
  })
  .catch((error) => {
    console.error(error);
    process.exit(1);
  });

function applyEnvOverrides(baseConfig) {
  return {
    ...baseConfig,
    baseUrl: process.env.BASE_URL || baseConfig.baseUrl,
    pathPrefix: process.env.PATH_PREFIX || baseConfig.pathPrefix,
    region: process.env.AWS_REGION || baseConfig.region,
    validatorTable: process.env.VALIDATOR_TABLE || baseConfig.validatorTable,
    configTable: process.env.CONFIG_TABLE || baseConfig.configTable,
    countingTable: process.env.COUNTING_TABLE || baseConfig.countingTable,
    ordersTable: process.env.ORDERS_TABLE || baseConfig.ordersTable,
    orderJourneyTable: process.env.ORDER_JOURNEY_TABLE || baseConfig.orderJourneyTable,
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
