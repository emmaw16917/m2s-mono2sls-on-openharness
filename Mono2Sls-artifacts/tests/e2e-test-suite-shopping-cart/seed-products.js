const { loadTargetConfig } = require('./config/load-target-config');
const { prepareTargetState } = require('./state/shopping-cart-state');

const target = process.env.TARGET || process.argv[2];
if (!target) {
  throw new Error('TARGET env var or CLI arg is required. Example: TARGET=cursor_sonnet45tk_shopping_cart npm run seed:products');
}

const config = loadTargetConfig(target);

prepareTargetState(config, target)
  .then((runtimeState) => {
    console.log(`Seeded shopping-cart runtime data for ${target}`);
    console.log(`  runId: ${runtimeState.runId}`);
    console.log(`  primaryProductId: ${runtimeState.fixtures.primaryProductId}`);
    console.log(`  secondaryProductId: ${runtimeState.fixtures.secondaryProductId}`);
  })
  .catch((err) => {
    console.error(err);
    process.exit(1);
  });
