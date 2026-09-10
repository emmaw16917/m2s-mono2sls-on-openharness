const {
  contract,
  authProvider,
  prepareTarget,
  expectStatus,
  getEndpointContract,
  createSession,
  addCartItem,
  createOrder,
  getBestsellers,
  extractList,
  pollUntil,
} = require('./helpers');

describe('Async Bestsellers Semantics', () => {
  let session;

  beforeAll(async () => {
    await prepareTarget();
    session = await createSession('bookstore-async');
  });

  afterAll(async () => {
    await authProvider.teardown();
  });

  test('ASYNC-01 / TC-10: should eventually expose ordered books in bestsellers', async () => {
    const endpoint = getEndpointContract('TC-10');

    await addCartItem(session, {
      bookId: contract.fixtures.primaryBookId,
      quantity: 2,
      price: 99,
    });

    const createRes = await createOrder(session, [
      { bookId: contract.fixtures.primaryBookId, quantity: 2, price: 99 },
    ]);
    expectStatus(createRes, getEndpointContract('TC-08').successStatus);

    const finalRes = await pollUntil(
      async () => getBestsellers(),
      (res) => {
        if (res.status !== 200) {
          return false;
        }
        const items = Array.isArray(res.data) ? res.data : extractList(res.data, endpoint);
        return items.includes(contract.fixtures.primaryBookId);
      }
    );

    expectStatus(finalRes, endpoint.successStatus);
    const bestsellers = Array.isArray(finalRes.data)
      ? finalRes.data
      : extractList(finalRes.data, endpoint);
    expect(bestsellers.includes(contract.fixtures.primaryBookId)).toBe(true);
  });
});
