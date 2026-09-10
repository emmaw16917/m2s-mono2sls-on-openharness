const {
  prepareTarget,
  contract,
  authProvider,
  request,
  expectStatus,
  getEndpointContract,
  extractObject,
  extractList,
  buildSelection,
  getAddress,
  computeTotal,
  createUserSession,
  preauthPayment,
  checkPayment,
  validatePayment,
  getDeliveryPricing,
  createOrder,
  getOrder,
  listOrders,
  updateOrder,
  deleteOrder,
} = require('./helpers');

describe('Orders Contract', () => {
  let userSession;

  beforeAll(async () => {
    await prepareTarget();
    userSession = await createUserSession('orders');
  });

  afterAll(async () => {
    await authProvider.teardown();
  });

  test('TC-04a: should pre-authorize a payment token', async () => {
    const endpoint = getEndpointContract('TC-04');
    const res = await preauthPayment(12345);

    expectStatus(res, endpoint.response.successStatus);
    expect(typeof res.data.paymentToken).toBe('string');
  });

  test('TC-05a: should validate a fresh payment token through payment check', async () => {
    const preauthRes = await preauthPayment(9000);
    expectStatus(preauthRes, 200);

    const res = await checkPayment(preauthRes.data.paymentToken, 9000);
    expectStatus(res, getEndpointContract('TC-05').response.successStatus);
    expect(res.data.ok).toBe(true);
  });

  test('TC-06a: should reject delivery pricing without authentication', async () => {
    const res = await request('post', '/orders/delivery-pricing', {
      headers: { 'Content-Type': 'application/json' },
      data: {
        products: buildSelection([{ key: 'laptop', quantity: 1 }]),
        address: getAddress(),
      },
    });

    expectStatus(res, contract.validationRules.unauthenticatedStatus);
  });

  test('TC-06b: should compute delivery pricing for an authenticated user', async () => {
    const products = buildSelection([{ key: 'laptop', quantity: 1 }, { key: 'mouse', quantity: 2 }]);
    const res = await getDeliveryPricing(userSession, products, getAddress());

    expectStatus(res, getEndpointContract('TC-06').response.successStatus);
    expect(res.data.pricing).toBe(0);
  });

  describe('Authenticated order lifecycle', () => {
    let orderId;
    let paymentToken;
    let oldTotal;
    let newTotal;

    test('TC-07b: should reject order creation without authentication', async () => {
      const res = await request('post', '/orders', {
        headers: { 'Content-Type': 'application/json' },
        data: {
          products: buildSelection([{ key: 'laptop', quantity: 1 }]),
          address: getAddress(),
          deliveryPrice: 0,
          paymentToken: '00000000-0000-0000-0000-000000000000',
        },
      });

      expectStatus(res, contract.validationRules.unauthenticatedStatus);
    });

    test('TC-07a: should create an order with a valid payment token', async () => {
      const products = buildSelection([{ key: 'laptop', quantity: 1 }, { key: 'mouse', quantity: 2 }]);
      const address = getAddress();
      const pricingRes = await getDeliveryPricing(userSession, products, address);
      expectStatus(pricingRes, 200);

      oldTotal = computeTotal(products, pricingRes.data.pricing);
      const preauthRes = await preauthPayment(oldTotal);
      expectStatus(preauthRes, 200);
      paymentToken = preauthRes.data.paymentToken;

      const validateRes = await validatePayment(userSession, paymentToken, oldTotal);
      expectStatus(validateRes, 200);
      expect(validateRes.data.ok).toBe(true);

      const createRes = await createOrder(userSession, {
        products,
        address,
        deliveryPrice: pricingRes.data.pricing,
        paymentToken,
      });

      expectStatus(createRes, getEndpointContract('TC-07').response.successStatus);
      const created = extractObject(createRes.data, getEndpointContract('TC-07'));
      expect(created.status).toBe('NEW');
      expect(created.userId).toBe(userSession.sub);
      orderId = created.orderId;
    });

    test('TC-08a: should fetch the created order', async () => {
      const res = await getOrder(userSession, orderId);

      expectStatus(res, getEndpointContract('TC-08').response.successStatus);
      const order = extractObject(res.data, getEndpointContract('TC-08'));
      expect(order.orderId).toBe(orderId);
      expect(order.paymentToken).toBe(paymentToken);
    });

    test('TC-09a: should list the owner orders', async () => {
      const res = await listOrders(userSession);

      expectStatus(res, getEndpointContract('TC-09').response.successStatus);
      const orders = extractList(res.data, getEndpointContract('TC-09'));
      expect(orders.some((order) => order.orderId === orderId)).toBe(true);
    });

    test('TC-10a: should update an order and reduce the authorized amount', async () => {
      const updatedProducts = buildSelection([{ key: 'laptop', quantity: 1 }, { key: 'mouse', quantity: 1 }]);
      const updateRes = await updateOrder(userSession, orderId, {
        products: updatedProducts,
        deliveryPrice: 0,
      });

      expectStatus(updateRes, getEndpointContract('TC-10').response.successStatus);
      const updated = updateRes.data.order || updateRes.data;
      expect(updated.status).toBe('NEW');
      newTotal = computeTotal(updatedProducts, 0);
      expect(newTotal).toBeLessThan(oldTotal);

      const oldAmountCheck = await checkPayment(paymentToken, oldTotal);
      expectStatus(oldAmountCheck, 200);
      expect(oldAmountCheck.data.ok).toBe(false);

      const newAmountCheck = await checkPayment(paymentToken, newTotal);
      expectStatus(newAmountCheck, 200);
      expect(newAmountCheck.data.ok).toBe(true);
    });

    test('TC-11a: should delete an order that is still NEW', async () => {
      const deleteRes = await deleteOrder(userSession, orderId);
      expectStatus(deleteRes, getEndpointContract('TC-11').response.successStatus);
      expect(deleteRes.data.success).toBe(true);

      const getRes = await getOrder(userSession, orderId);
      expectStatus(getRes, 404);
    });
  });
});
