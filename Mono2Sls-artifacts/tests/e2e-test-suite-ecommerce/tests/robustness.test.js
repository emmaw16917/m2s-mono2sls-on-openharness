const {
  prepareTarget,
  contract,
  authProvider,
  request,
  expectStatus,
  createUserSession,
  buildSelection,
  getAddress,
  getDeliveryPricing,
  preauthPayment,
  createOrder,
} = require('./helpers');

describe('Robustness', () => {
  let userSession;

  beforeAll(async () => {
    await prepareTarget();
    userSession = await createUserSession('robustness');
  });

  afterAll(async () => {
    await authProvider.teardown();
  });

  test('ROB-03: should reject invalid card numbers', async () => {
    const res = await request('post', '/payment-3p/preauth', {
      headers: { 'Content-Type': 'application/json' },
      data: {
        cardNumber: '1234',
        amount: 1000,
      },
    });

    expectStatus(res, contract.validationRules.badRequestStatus);
  });

  test('ROB-04: should reject an invalid payment token during order creation', async () => {
    const products = buildSelection([{ key: 'mug', quantity: 1 }]);
    const pricingRes = await getDeliveryPricing(userSession, products, getAddress());
    expectStatus(pricingRes, 200);

    const res = await createOrder(userSession, {
      products,
      address: getAddress(),
      deliveryPrice: pricingRes.data.pricing,
      paymentToken: '00000000-0000-0000-0000-000000000000',
    });

    expectStatus(res, contract.validationRules.badRequestStatus);
  });

  test('ROB-05: should reject a wrong delivery price during order creation', async () => {
    const products = buildSelection([{ key: 'laptop', quantity: 1 }]);
    const preauthRes = await preauthPayment(99999);
    expectStatus(preauthRes, 200);

    const res = await createOrder(userSession, {
      products,
      address: getAddress(),
      deliveryPrice: 1234,
      paymentToken: preauthRes.data.paymentToken,
    });

    expectStatus(res, contract.validationRules.badRequestStatus);
  });
});
