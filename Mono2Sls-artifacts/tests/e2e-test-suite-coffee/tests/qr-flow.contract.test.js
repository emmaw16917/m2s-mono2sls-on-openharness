const {
  authProvider,
  prepareTarget,
  expectStatus,
  getEndpointContract,
  extractObject,
  extractList,
  getQrCode,
  verifyQrCode,
  getMyOrders,
  createUserSession,
  createAdminSession,
} = require('./helpers');

describe('QR Flow Contract', () => {
  let userSession;
  let adminSession;

  beforeAll(async () => {
    await prepareTarget();
    userSession = await createUserSession('qr-user');
    adminSession = await createAdminSession('qr-admin');
  });

  afterAll(async () => {
    await authProvider.teardown();
  });

  test('TC-07: should create an order through QR verification', async () => {
    const qrRes = await getQrCode(adminSession);
    expectStatus(qrRes, getEndpointContract('TC-06').successStatus);
    const qrPayload = extractObject(qrRes.data, getEndpointContract('TC-06'));

    const verifyRes = await verifyQrCode(userSession, qrPayload.qrCode);
    expectStatus(verifyRes, getEndpointContract('TC-07').successStatus);
    const verifyPayload = extractObject(verifyRes.data, getEndpointContract('TC-07'));

    expect(typeof verifyPayload.orderId).toBe('string');
    expect(verifyPayload.availableTokens).toBe(2);

    const myOrdersRes = await getMyOrders(userSession);
    expectStatus(myOrdersRes, getEndpointContract('TC-05').successStatus);
    const myOrders = extractList(myOrdersRes.data, getEndpointContract('TC-05'));
    expect(myOrders.some((item) => item.SK === verifyPayload.orderId || item.orderId === verifyPayload.orderId)).toBe(true);
  });
});
