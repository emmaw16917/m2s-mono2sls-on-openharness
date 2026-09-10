const {
  authProvider,
  contract,
  prepareTarget,
  getCapability,
} = require('./setup');
const {
  expectStatus,
  getEndpointContract,
  extractObject,
  createOrder,
  getOrderJourney,
  updateOrder,
  createUserSession,
  createAdminSession,
  pollUntil,
} = require('./helpers');

describe('Order Journey Semantics', () => {
  let userSession;
  let adminSession;

  beforeAll(async () => {
    await prepareTarget();
    userSession = await createUserSession('journey-user');
    adminSession = await createAdminSession('journey-admin');
  });

  afterAll(async () => {
    await authProvider.teardown();
  });

  test('ASYNC-01: should expose the full order journey after lifecycle transitions', async () => {
    const createRes = await createOrder(userSession);
    expectStatus(createRes, getEndpointContract('TC-04').successStatus);
    const created = extractObject(createRes.data, getEndpointContract('TC-04'));
    const orderId = created.orderId;

    await updateOrder(adminSession, orderId, null, {
      userId: userSession.sub,
      drink: contract.fixtures.validDrink,
      modifiers: contract.fixtures.validModifiers,
    });
    await updateOrder(adminSession, orderId, 'make');
    await updateOrder(adminSession, orderId, 'complete');

    const fetchJourney = async () => {
      const res = await getOrderJourney(orderId);
      expectStatus(res, getEndpointContract('TC-03').successStatus);
      return extractObject(res.data, getEndpointContract('TC-03'));
    };

    const journey = getCapability('asyncOrderJourney')
      ? await pollUntil(fetchJourney, (value) => {
        const detailTypes = (value.events || []).map((item) => item.detailType);
        return ['Order.Created', 'Order.Submitted', 'Order.Making', 'Order.Completed']
          .every((type) => detailTypes.includes(type));
      })
      : await fetchJourney();

    const detailTypes = journey.events.map((item) => item.detailType);
    expect(detailTypes).toEqual(expect.arrayContaining([
      'Order.Created',
      'Order.Submitted',
      'Order.Making',
      'Order.Completed',
    ]));
  });
});
