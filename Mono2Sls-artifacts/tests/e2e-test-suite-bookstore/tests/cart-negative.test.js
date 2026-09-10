const {
  contract,
  authProvider,
  prepareTarget,
  expectStatus,
  getEndpointContract,
  createSession,
  getCartItem,
  updateCartItem,
  deleteCartItem,
} = require('./helpers');

describe('Cart Negative Cases', () => {
  let session;

  beforeAll(async () => {
    await prepareTarget();
    session = await createSession('bookstore-negative');
  });

  afterAll(async () => {
    await authProvider.teardown();
  });

  test('TC-NEG-01: should return 404 for getting a non-existent cart item', async () => {
    const endpoint = getEndpointContract('TC-07B');
    const res = await getCartItem(session, contract.fixtures.invalidBookId);
    expectStatus(res, endpoint.notFoundStatus);
  });

  test('TC-NEG-02: should handle update of a non-existent cart item', async () => {
    const res = await updateCartItem(session, {
      bookId: contract.fixtures.invalidBookId,
      quantity: 5,
    });
    expect([400, 404]).toContain(res.status);
  });

  test('TC-NEG-03: should handle delete of a non-existent cart item', async () => {
    const res = await deleteCartItem(session, contract.fixtures.invalidBookId);
    expect([200, 204, 404]).toContain(res.status);
  });
});
