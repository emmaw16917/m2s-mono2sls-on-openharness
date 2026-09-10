const {
  prepareTarget,
  testEventId,
  expectStatus,
  getEndpointContract,
  extractObject,
  extractList,
  assertHasFields,
  getConfig,
  getAllConfigs,
  getOrderJourney,
} = require('./helpers');

describe('Public Contract', () => {
  beforeAll(async () => {
    await prepareTarget();
  });

  test('TC-01: should return seeded config by eventId', async () => {
    const endpoint = getEndpointContract('TC-01');
    const res = await getConfig();

    expectStatus(res, endpoint.successStatus);
    const config = extractObject(res.data, endpoint);
    assertHasFields(config, endpoint.response.requiredFields);
    expect(config.PK).toBe(`config-${testEventId}`);
    expect(Array.isArray(config.menu)).toBe(true);
    expect(config.storeOpen).toBe(true);
  });

  test('TC-02: should expose seeded config in config scan', async () => {
    const endpoint = getEndpointContract('TC-02');
    const res = await getAllConfigs();

    expectStatus(res, endpoint.successStatus);
    const configs = extractList(res.data, endpoint);
    expect(configs.some((item) => item.PK === `config-${testEventId}`)).toBe(true);
  });

  test('TC-03: should return an empty journey for a missing order', async () => {
    const endpoint = getEndpointContract('TC-03');
    const orderId = `missing-${Date.now()}`;
    const res = await getOrderJourney(orderId);

    expectStatus(res, endpoint.successStatus);
    const journey = extractObject(res.data, endpoint);
    assertHasFields(journey, endpoint.response.requiredFields);
    expect(journey.orderId).toBe(orderId);
    expect(Array.isArray(journey.events)).toBe(true);
    expect(journey.eventCount).toBe(0);
  });
});
