const {
  contract,
  authProvider,
  prepareTarget,
  getRuntimeState,
  expectStatus,
  createAnonymousSession,
  createSession,
  addToCart,
  getCart,
  updateCart,
  checkoutCart,
} = require('./helpers');

describe('Negative Cases', () => {
  let runtimeState;

  beforeAll(async () => {
    await prepareTarget();
    runtimeState = getRuntimeState();
  });

  afterAll(async () => {
    await authProvider.teardown();
  });

  test('TC-NEG-01: should handle adding an invalid product to cart', async () => {
    const anonymous = createAnonymousSession('neg01');
    await getCart(anonymous);

    const res = await addToCart(anonymous, contract.fixtures.invalidProductId, 1);
    expect([400, 404, 500]).toContain(res.status);
  });

  test('TC-NEG-02: should handle updating a non-existent cart item', async () => {
    const anonymous = createAnonymousSession('neg02');
    await getCart(anonymous);

    const res = await updateCart(anonymous, contract.fixtures.invalidProductId, 5);
    expect([400, 404, 500]).toContain(res.status);
  });

  test('TC-NEG-03: should handle checkout on an empty authenticated cart', async () => {
    const session = await createSession('neg03-user');
    const res = await checkoutCart(session);
    expect([200, 400]).toContain(res.status);
  });
});
