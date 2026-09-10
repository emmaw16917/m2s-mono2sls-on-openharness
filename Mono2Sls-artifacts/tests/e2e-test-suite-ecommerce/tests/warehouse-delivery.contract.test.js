const {
  prepareTarget,
  contract,
  authProvider,
  expectStatus,
  getEndpointContract,
  extractObject,
  createUserSession,
  createRoleSession,
  createAuthorizedOrder,
  getOrder,
  listPackagingRequests,
  getPackagingRequest,
  startPackaging,
  updatePackagingProduct,
  completePackaging,
  failPackaging,
  listDeliveries,
  getDelivery,
  startDelivery,
  completeDelivery,
  failDelivery,
  waitForPackagingRequest,
  waitForDelivery,
  waitForOrderStatus,
  waitForPaymentNotAuthorized,
} = require('./helpers');

describe('Warehouse And Delivery Contract', () => {
  let userSession;
  let warehouseSession;
  let deliverySession;

  beforeAll(async () => {
    await prepareTarget();
    userSession = await createUserSession('shopper');
    warehouseSession = await createRoleSession('warehouse', 'warehouse');
    deliverySession = await createRoleSession('delivery', 'delivery');
  });

  afterAll(async () => {
    await authProvider.teardown();
  });

  test('ROB-01: should deny warehouse access to a normal shopper', async () => {
    const res = await listPackagingRequests(userSession);
    expectStatus(res, contract.validationRules.forbiddenStatus);
  });

  test('ROB-02: should deny delivery access to a normal shopper', async () => {
    const res = await listDeliveries(userSession);
    expectStatus(res, contract.validationRules.forbiddenStatus);
  });

  describe('Successful warehouse to delivery flow', () => {
    let orderData;

    beforeAll(async () => {
      orderData = await createAuthorizedOrder(userSession, [
        { key: 'laptop', quantity: 1 },
        { key: 'mouse', quantity: 1 },
      ]);
      await waitForPackagingRequest(warehouseSession, orderData.order.orderId);
    });

    test('TC-12a: should list packaging requests for warehouse staff', async () => {
      const res = await listPackagingRequests(warehouseSession);

      expectStatus(res, getEndpointContract('TC-12').response.successStatus);
      const ids = res.data.packagingRequestIds || [];
      expect(ids).toContain(orderData.order.orderId);
    });

    test('TC-13a: should fetch a packaging request', async () => {
      const res = await getPackagingRequest(warehouseSession, orderData.order.orderId);

      expectStatus(res, getEndpointContract('TC-13').response.successStatus);
      const requestData = extractObject(res.data, getEndpointContract('TC-13'));
      expect(requestData.orderId).toBe(orderData.order.orderId);
      expect(requestData.status).toBe('NEW');
    });

    test('TC-14a: should start packaging', async () => {
      const res = await startPackaging(warehouseSession, orderData.order.orderId);

      expectStatus(res, getEndpointContract('TC-14').response.successStatus);
      expect(res.data.success).toBe(true);
    });

    test('TC-15a: should update packaging product quantity', async () => {
      const res = await updatePackagingProduct(
        warehouseSession,
        orderData.order.orderId,
        orderData.products[1].productId,
        3
      );

      expectStatus(res, getEndpointContract('TC-15').response.successStatus);
      expect(res.data.success).toBe(true);

      const getRes = await getPackagingRequest(warehouseSession, orderData.order.orderId);
      expectStatus(getRes, 200);
      const packagingRequest = getRes.data.packagingRequest;
      const updatedProduct = packagingRequest.products.find((item) => item.productId === orderData.products[1].productId);
      expect(updatedProduct.quantity).toBe(3);
    });

    test('TC-16a: should complete packaging and create a delivery', async () => {
      const res = await completePackaging(warehouseSession, orderData.order.orderId);

      expectStatus(res, getEndpointContract('TC-16').response.successStatus);
      expect(res.data.success).toBe(true);

      const deliveryRes = await waitForDelivery(deliverySession, orderData.order.orderId);
      expectStatus(deliveryRes, 200);
      expect(deliveryRes.data.delivery.orderId).toBe(orderData.order.orderId);
    });

    test('TC-18a: should list deliveries for delivery staff', async () => {
      const res = await listDeliveries(deliverySession);

      expectStatus(res, getEndpointContract('TC-18').response.successStatus);
      const deliveries = res.data.deliveries || [];
      expect(deliveries.some((delivery) => delivery.orderId === orderData.order.orderId)).toBe(true);
    });

    test('TC-19a: should fetch a delivery', async () => {
      const res = await getDelivery(deliverySession, orderData.order.orderId);

      expectStatus(res, getEndpointContract('TC-19').response.successStatus);
      const delivery = extractObject(res.data, getEndpointContract('TC-19'));
      expect(delivery.orderId).toBe(orderData.order.orderId);
      expect(delivery.status).toBe('NEW');
    });

    test('TC-20a: should start delivery', async () => {
      const res = await startDelivery(deliverySession, orderData.order.orderId);

      expectStatus(res, getEndpointContract('TC-20').response.successStatus);
      expect(res.data.success).toBe(true);

      const orderRes = await waitForOrderStatus(userSession, orderData.order.orderId, 'IN_TRANSIT');
      expectStatus(orderRes, 200);
    });

    test('TC-21a: should complete delivery and finish the order', async () => {
      const res = await completeDelivery(deliverySession, orderData.order.orderId);

      expectStatus(res, getEndpointContract('TC-21').response.successStatus);
      expect(res.data.success).toBe(true);

      const orderRes = await waitForOrderStatus(userSession, orderData.order.orderId, 'COMPLETED');
      expectStatus(orderRes, 200);

      const paymentRes = await waitForPaymentNotAuthorized(orderData.paymentToken, orderData.total);
      expectStatus(paymentRes, 200);
      expect(paymentRes.data.ok).toBe(false);
    });
  });

  describe('Failure paths', () => {
    test('TC-17a: should fail packaging and mark the order as PACKAGING_FAILED', async () => {
      const orderData = await createAuthorizedOrder(userSession, [
        { key: 'mug', quantity: 1 },
      ]);

      await waitForPackagingRequest(warehouseSession, orderData.order.orderId);
      const failRes = await failPackaging(warehouseSession, orderData.order.orderId, 'Out of stock');
      expectStatus(failRes, getEndpointContract('TC-17').response.successStatus);
      expect(failRes.data.success).toBe(true);

      const orderRes = await waitForOrderStatus(userSession, orderData.order.orderId, 'PACKAGING_FAILED');
      expectStatus(orderRes, 200);

      const paymentRes = await waitForPaymentNotAuthorized(orderData.paymentToken, orderData.total);
      expectStatus(paymentRes, 200);
    });

    test('TC-22a: should fail delivery and mark the order as DELIVERY_FAILED', async () => {
      const orderData = await createAuthorizedOrder(userSession, [
        { key: 'mouse', quantity: 1 },
      ]);

      await waitForPackagingRequest(warehouseSession, orderData.order.orderId);
      await startPackaging(warehouseSession, orderData.order.orderId);
      await completePackaging(warehouseSession, orderData.order.orderId);
      await waitForDelivery(deliverySession, orderData.order.orderId);
      await startDelivery(deliverySession, orderData.order.orderId);

      const failRes = await failDelivery(deliverySession, orderData.order.orderId, 'Address mismatch');
      expectStatus(failRes, getEndpointContract('TC-22').response.successStatus);
      expect(failRes.data.success).toBe(true);

      const orderRes = await waitForOrderStatus(userSession, orderData.order.orderId, 'DELIVERY_FAILED');
      expectStatus(orderRes, 200);

      const paymentRes = await waitForPaymentNotAuthorized(orderData.paymentToken, orderData.total);
      expectStatus(paymentRes, 200);
    });
  });
});
