const path = require('path');
const { resetMutableState } = require('./state/bookstore-state');

const target = process.env.TARGET || process.argv[2];
if (!target) {
  throw new Error('TARGET env var or CLI arg is required. Example: TARGET=cursor_sonnet45tk_bookstore npm run reset:state');
}

const config = require(path.resolve(__dirname, `./config/${target}.json`));

resetMutableState(applyEnvOverrides(config))
  .then(() => {
    console.log(`Mutable state reset for ${target}`);
  })
  .catch((err) => {
    console.error(err);
    process.exit(1);
  });

function applyEnvOverrides(config) {
  return {
    ...config,
    baseUrl: process.env.BASE_URL || config.baseUrl,
    pathPrefix: process.env.PATH_PREFIX || config.pathPrefix,
    region: process.env.AWS_REGION || config.region,
    booksTable: process.env.BOOKS_TABLE || config.booksTable,
    cartTable: process.env.CART_TABLE || config.cartTable,
    ordersTable: process.env.ORDERS_TABLE || config.ordersTable,
    bestsellersTable: process.env.BESTSELLERS_TABLE || config.bestsellersTable,
    usersTable: process.env.USERS_TABLE || config.usersTable,
    authConfig: {
      ...(config.authConfig || {}),
      userPoolId: process.env.USER_POOL_ID || config.authConfig?.userPoolId,
      clientId: process.env.USER_POOL_CLIENT_ID || config.authConfig?.clientId,
      region: process.env.AWS_REGION || config.authConfig?.region,
      defaultPassword: process.env.DEFAULT_PASSWORD || config.authConfig?.defaultPassword,
    },
  };
}
