const { loadTargetConfig } = require('./config/load-target-config');
const { resetMutableState } = require('./state/shopping-cart-state');

const target = process.env.TARGET || process.argv[2];
if (!target) {
  throw new Error('TARGET env var or CLI arg is required. Example: TARGET=cursor_sonnet45tk_shopping_cart npm run reset:state');
}

const config = loadTargetConfig(target);

resetMutableState(config)
  .then(() => {
    console.log(`Mutable shopping-cart state reset for ${target}`);
  })
  .catch((err) => {
    console.error(err);
    process.exit(1);
  });
