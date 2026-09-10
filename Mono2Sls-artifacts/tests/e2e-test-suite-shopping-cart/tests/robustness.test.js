const {
  prepareTarget,
  getRuntimeState,
  expectStatus,
  getEndpointContract,
  extractList,
  extractObject,
  assertProductShape,
  listProducts,
  getProduct,
} = require('./helpers');

describe('Robustness', () => {
  let runtimeState;

  beforeAll(async () => {
    await prepareTarget();
    runtimeState = getRuntimeState();
  });

  test('ROB-01: catalog responses should return valid JSON with correct Content-Type', async () => {
    const endpoint = getEndpointContract('TC-01');
    const res = await listProducts();

    expectStatus(res, endpoint.successStatus);
    expect(res.headers['content-type']).toMatch(/application\/json/);
    expect(typeof res.data).toBe('object');
  });

  test('ROB-02: should handle concurrent product detail requests', async () => {
    const endpoint = getEndpointContract('TC-02');
    const productId = runtimeState.fixtures.primaryProductId;
    const promises = Array.from({ length: 5 }, () => getProduct(productId));

    const results = await Promise.all(promises);
    for (const res of results) {
      expectStatus(res, endpoint.successStatus);
      const product = extractObject(res.data, endpoint);
      expect(product.id).toBe(productId);
    }
  });

  test('ROB-03: list and get should return consistent data for the same product', async () => {
    const listEndpoint = getEndpointContract('TC-01');
    const getEndpoint = getEndpointContract('TC-02');
    const productId = runtimeState.fixtures.primaryProductId;

    const listRes = await listProducts();
    expectStatus(listRes, listEndpoint.successStatus);
    const products = extractList(listRes.data, listEndpoint);
    const fromList = products.find((product) => product.id === productId);
    expect(fromList).toBeDefined();

    const getRes = await getProduct(productId);
    expectStatus(getRes, getEndpoint.successStatus);
    const fromGet = extractObject(getRes.data, getEndpoint);

    expect(fromGet.name).toBe(fromList.name);
    expect(fromGet.price).toBe(fromList.price);
  });
});
