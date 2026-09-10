const {
  authProvider,
  prepareTarget,
  createUserSession,
  createRoleSession,
  expectStatus,
  createAuthorizedOrder,
  waitForPackagingRequest,
  startPackaging,
  completePackaging,
  waitForDelivery,
  startDelivery,
  completeDelivery,
  waitForOrderStatus,
  waitForPaymentNotAuthorized,
} = require('./helpers');

describe('Async Semantics', () => {
  let userSession;
  let warehouseSession;
  let deliverySession;

  beforeAll(async () => {
    await prepareTarget();
    userSession = await createUserSession('async-shopper');
    warehouseSession = await createRoleSession('warehouse', 'async-warehouse');
    deliverySession = await createRoleSession('delivery', 'async-delivery');
  });

  afterAll(async () => {
    await authProvider.teardown();
  });

  test('ASYNC-01: should eventually materialize a packaging request after order creation', async () => {
    const orderData = await createAuthorizedOrder(userSession, [
      { key: 'laptop', quantity: 1 },
    ]);

    const packagingRes = await waitForPackagingRequest(warehouseSession, orderData.order.orderId);
    expectStatus(packagingRes, 200);
    expect(packagingRes.data.packagingRequest.orderId).toBe(orderData.order.orderId);
    expect(packagingRes.data.packagingRequest.status).toBe('NEW');
  });

  test('ASYNC-02: should eventually propagate delivery creation and final order completion', async () => {
    const orderData = await createAuthorizedOrder(userSession, [
      { key: 'mouse', quantity: 2 },
    ]);

    await waitForPackagingRequest(warehouseSession, orderData.order.orderId);

    const startPackagingRes = await startPackaging(warehouseSession, orderData.order.orderId);
    expectStatus(startPackagingRes, 200);

    const completePackagingRes = await completePackaging(warehouseSession, orderData.order.orderId);
    expectStatus(completePackagingRes, 200);

    const deliveryRes = await waitForDelivery(deliverySession, orderData.order.orderId);
    expectStatus(deliveryRes, 200);
    expect(deliveryRes.data.delivery.orderId).toBe(orderData.order.orderId);

    const startDeliveryRes = await startDelivery(deliverySession, orderData.order.orderId);
    expectStatus(startDeliveryRes, 200);

    const completeDeliveryRes = await completeDelivery(deliverySession, orderData.order.orderId);
    expectStatus(completeDeliveryRes, 200);

    const orderRes = await waitForOrderStatus(userSession, orderData.order.orderId, 'COMPLETED');
    expectStatus(orderRes, 200);

    const paymentRes = await waitForPaymentNotAuthorized(orderData.paymentToken, orderData.total);
    expectStatus(paymentRes, 200);
    expect(paymentRes.data.ok).toBe(false);
  });
});
