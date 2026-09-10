const {
  contract,
  prepareTarget,
  expectStatus,
  createAnonymousSession,
  migrateCart,
  checkoutCart,
} = require('./helpers');

describe('Auth Compatibility', () => {
  beforeAll(async () => {
    await prepareTarget();
  });

  test('AUTH-01: should reject cart migration without authentication', async () => {
    const anonymous = createAnonymousSession('auth01');
    const res = await migrateCart(anonymous);
    expectStatus(res, contract.validationRules.unauthenticatedStatus);
  });

  test('AUTH-02: should reject checkout without authentication', async () => {
    const anonymous = createAnonymousSession('auth02');
    const res = await checkoutCart(anonymous);
    expectStatus(res, contract.validationRules.unauthenticatedStatus);
  });
});
