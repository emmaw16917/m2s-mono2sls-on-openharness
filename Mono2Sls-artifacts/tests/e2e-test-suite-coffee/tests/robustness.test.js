const {
  prepareTarget,
  testEventId,
  expectStatus,
  getEndpointContract,
  extractObject,
  extractList,
  getConfig,
  getAllConfigs,
} = require('./helpers');

describe('Robustness', () => {
  beforeAll(async () => {
    await prepareTarget();
  });

  test('ROB-01: config responses should return valid JSON with correct Content-Type', async () => {
    const endpoint = getEndpointContract('TC-01');
    const res = await getConfig();

    expectStatus(res, endpoint.successStatus);
    expect(res.headers['content-type']).toMatch(/application\/json/);
    expect(typeof res.data).toBe('object');
  });

  test('ROB-02: should handle concurrent config requests', async () => {
    const endpoint = getEndpointContract('TC-01');
    const promises = Array.from({ length: 5 }, () => getConfig());

    const results = await Promise.all(promises);
    for (const res of results) {
      expectStatus(res, endpoint.successStatus);
      const config = extractObject(res.data, endpoint);
      expect(config.PK).toBe(`config-${testEventId}`);
    }
  });
});
