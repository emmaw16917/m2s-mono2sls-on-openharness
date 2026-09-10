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
  getQrCode,
  getOrders,
  getOrder,
  updateOrder,
  updateConfig,
  createUserSession,
  createAdminSession,
} = require('./helpers');

describe('Admin Contract', () => {
  let userSession;
  let adminSession;
  let orderId;

  beforeAll(async () => {
    await prepareTarget();
    userSession = await createUserSession('admin-user');
    adminSession = await createAdminSession('admin-user');

    const created = await createOrder(userSession);
    orderId = extractObject(created.data, getEndpointContract('TC-04')).orderId;
  });

  afterAll(async () => {
    await authProvider.teardown();
  });

  test('TC-06a: should reject QR generation for a non-admin user', async () => {
    const res = await getQrCode(userSession);
    expectStatus(res, contract.validationRules.forbiddenStatus);
  });

  test('TC-06b: should generate QR code for an admin user', async () => {
    const endpoint = getEndpointContract('TC-06');
    const res = await getQrCode(adminSession);

    expectStatus(res, endpoint.successStatus);
    const qrPayload = extractObject(res.data, endpoint);
    expect(typeof qrPayload.qrCode).toBe('string');
  });

  test('TC-08a: should reject order listing for a non-admin user', async () => {
    const res = await request('get', '/orders', {
      session: userSession,
      params: { state: 'CREATED', eventId: testEventId },
    });
    expectStatus(res, contract.validationRules.forbiddenStatus);
  });

  test('TC-10a: should reject order updates for a non-admin user', async () => {
    const res = await updateOrder(userSession, orderId, null, {
      userId: userSession.sub,
      drink: contract.fixtures.validDrink,
      modifiers: contract.fixtures.validModifiers,
    });
    expectStatus(res, contract.validationRules.forbiddenStatus);
  });

  test('TC-10b: should allow admin submit action with a valid drink order', async () => {
    const endpoint = getEndpointContract('TC-10');
    const res = await updateOrder(adminSession, orderId, null, {
      userId: userSession.sub,
      drink: contract.fixtures.validDrink,
      modifiers: contract.fixtures.validModifiers,
    });

    expectStatus(res, endpoint.successStatus);
    const updated = extractObject(res.data, endpoint);
    expect(updated.orderId).toBe(orderId);
  });

  test('TC-10c: should reject invalid drink submissions', async () => {
    const created = await createOrder(userSession, { orderId: `invalid-${Date.now()}` });
    const invalidOrderId = extractObject(created.data, getEndpointContract('TC-04')).orderId;

    const res = await updateOrder(adminSession, invalidOrderId, null, {
      userId: userSession.sub,
      drink: contract.fixtures.invalidDrink,
      modifiers: [],
    });
    expectStatus(res, contract.validationRules.badRequestStatus);
  });

  test('TC-08b: should allow admin list access after submit', async () => {
    const endpoint = getEndpointContract('TC-08');
    const res = await getOrders(adminSession, 'SUBMITTED');

    expectStatus(res, endpoint.successStatus);
    const orders = extractList(res.data, endpoint);
    expect(orders.some((item) => item.SK === orderId || item.orderId === orderId)).toBe(true);
  });

  test('TC-09: should allow admin order detail access', async () => {
    const endpoint = getEndpointContract('TC-09');
    const res = await getOrder(adminSession, orderId);

    expectStatus(res, endpoint.successStatus);
    const order = extractObject(res.data, endpoint);
    expect(order.orderId).toBe(orderId);
    expect(typeof order.orderState).toBe('string');
    expect(order.orderState).toContain(`${testEventId}-`);
  });

  test('TC-10d: should support make and complete transitions for admin', async () => {
    const makeRes = await updateOrder(adminSession, orderId, 'make');
    expectStatus(makeRes, 200);

    const completeRes = await updateOrder(adminSession, orderId, 'complete');
    expectStatus(completeRes, 200);

    const finalOrderRes = await getOrder(adminSession, orderId);
    expectStatus(finalOrderRes, 200);
    const finalOrder = extractObject(finalOrderRes.data, getEndpointContract('TC-09'));
    expect(finalOrder.orderState).toBe(`${testEventId}-COMPLETED`);
  });

  test('TC-11a: should reject config updates for a non-admin user', async () => {
    const res = await updateConfig(userSession, { language: 'fr' });
    expectStatus(res, contract.validationRules.forbiddenStatus);
  });

  test('TC-11b: should allow config updates for an admin user', async () => {
    const endpoint = getEndpointContract('TC-11');
    const res = await updateConfig(adminSession, { language: 'fr' });

    expectStatus(res, endpoint.successStatus);
    const payload = extractObject(res.data, endpoint);
    if (payload.language !== undefined) {
      expect(payload.language).toBe('fr');
    } else if (payload.config && payload.config.language !== undefined) {
      expect(payload.config.language).toBe('fr');
    }
  });
});
