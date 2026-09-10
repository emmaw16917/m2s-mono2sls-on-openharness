const {
  prepareTarget,
  request,
  expectStatus,
  getEndpointContract,
  extractList,
  extractObject,
  assertHasFields,
  getFixtureProduct,
} = require('./helpers');

describe('Products Contract', () => {
  beforeAll(async () => {
    await prepareTarget();
  });

  test('TC-01a: should list seeded products', async () => {
    const endpoint = getEndpointContract('TC-01');
    const res = await request('get', endpoint.semanticPath);

    expectStatus(res, endpoint.response.successStatus);
    const products = extractList(res.data, endpoint);
    expect(products.length).toBeGreaterThanOrEqual(3);
    expect(products.some((product) => product.productId === getFixtureProduct('laptop').productId)).toBe(true);
  });

  test('TC-02a: should fetch a seeded product by id', async () => {
    const endpoint = getEndpointContract('TC-02');
    const fixture = getFixtureProduct('laptop');
    const res = await request('get', `/products/${fixture.productId}`);

    expectStatus(res, endpoint.response.successStatus);
    const product = extractObject(res.data, endpoint);
    assertHasFields(product, endpoint.response.requiredFields);
    expect(product.productId).toBe(fixture.productId);
    expect(product.name).toBe(fixture.name);
  });

  test('TC-03a: should filter products by category', async () => {
    const endpoint = getEndpointContract('TC-03');
    const res = await request('get', '/products/category/Electronics');

    expectStatus(res, endpoint.response.successStatus);
    const products = extractList(res.data, endpoint);
    expect(products.length).toBeGreaterThanOrEqual(2);
    expect(products.every((product) => product.category === 'Electronics')).toBe(true);
  });
});
