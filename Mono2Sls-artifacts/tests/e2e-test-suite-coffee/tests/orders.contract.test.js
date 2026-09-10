const {
  contract,
  authProvider,
  prepareTarget,
  testEventId,
  request,
  expectStatus,
  getEndpointContract,
  extractObject,
  extractList,
  createOrder,
  getMyOrders,
  createUserSession,
} = require('./helpers');

describe('Orders Contract', () => {
  let userSession;
  let orderId;

  beforeAll(async () => {
    await prepareTarget();
    userSession = await createUserSession('orders-user');
  });

  afterAll(async () => {
    await authProvider.teardown();
  });

  test('TC-04a: should reject order creation without authentication', async () => {
    const res = await request('post', '/orders', {
      headers: { 'Content-Type': 'application/json' },
      data: { orderId: 'unauth-order', eventId: testEventId },
    });
    expectStatus(res, contract.validationRules.unauthenticatedStatus);
  });

  test('TC-04b: should create an order for an authenticated user', async () => {
    const endpoint = getEndpointContract('TC-04');
    const res = await createOrder(userSession);

    expectStatus(res, endpoint.successStatus);
    const created = extractObject(res.data, endpoint);
    expect(typeof created.orderId).toBe('string');
    expect(created.message).toBeDefined();
    orderId = created.orderId;
  });

  test('TC-04c: should reject invalid create-order payloads', async () => {
    const res = await request('post', '/orders', {
      session: userSession,
      headers: { 'Content-Type': 'application/json' },
      data: { eventId: testEventId },
    });
    expectStatus(res, contract.validationRules.badRequestStatus);
  });

  test('TC-05: should return the current user orders', async () => {
    const endpoint = getEndpointContract('TC-05');
    const res = await getMyOrders(userSession);

    expectStatus(res, endpoint.successStatus);
    const orders = extractList(res.data, endpoint);
    expect(orders.some((item) => item.SK === orderId || item.orderId === orderId)).toBe(true);
  });
});
