const {
  contract,
  prepareTarget,
  getRuntimeState,
  expectStatus,
  getEndpointContract,
  extractObject,
  extractList,
  assertProductShape,
  listProducts,
  getProduct,
} = require('./helpers');

describe('Catalog Contract', () => {
  let runtimeState;

  beforeAll(async () => {
    await prepareTarget();
    runtimeState = getRuntimeState();
  });

  test('TC-01: should list seeded products', async () => {
    const endpoint = getEndpointContract('TC-01');
    const res = await listProducts();

    expectStatus(res, endpoint.successStatus);
    const products = extractList(res.data, endpoint);
    expect(products.length).toBeGreaterThanOrEqual(4);
    expect(products.some((product) => product.id === runtimeState.fixtures.primaryProductId)).toBe(true);
    assertProductShape(
      products.find((product) => product.id === runtimeState.fixtures.primaryProductId),
      endpoint.response.requiredProductFields
    );
  });

  test('TC-02: should fetch a known product by id', async () => {
    const endpoint = getEndpointContract('TC-02');
    const res = await getProduct(runtimeState.fixtures.primaryProductId);

    expectStatus(res, endpoint.successStatus);
    const product = extractObject(res.data, endpoint);
    assertProductShape(product, endpoint.response.requiredProductFields);
    expect(product.id).toBe(runtimeState.fixtures.primaryProductId);
  });

  test('TC-03: should reject unknown product ids', async () => {
    const endpoint = getEndpointContract('TC-02');
    const res = await getProduct(contract.fixtures.invalidProductId);
    expectStatus(res, endpoint.notFoundStatus);
  });
});
