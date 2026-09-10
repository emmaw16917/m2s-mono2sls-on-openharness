const {
  contract,
  prepareTarget,
  request,
  expectStatus,
  getEndpointContract,
} = require('./helpers');

describe('Auth Enforcement', () => {
  beforeAll(async () => {
    await prepareTarget();
  });

  test('AUTH-01: GET /cart without auth should be rejected', async () => {
    const endpoint = getEndpointContract('TC-07');
    const res = await request('get', endpoint.semanticPath);
    expectStatus(res, contract.validationRules.unauthenticatedStatus);
  });

  test('AUTH-02: POST /cart without auth should be rejected', async () => {
    const endpoint = getEndpointContract('TC-07C');
    const res = await request('post', endpoint.semanticPath, {
      headers: { 'Content-Type': 'application/json' },
      data: { bookId: contract.fixtures.primaryBookId, quantity: 1, price: 10 },
    });
    expectStatus(res, contract.validationRules.unauthenticatedStatus);
  });

  test('AUTH-03: POST /orders without auth should be rejected', async () => {
    const endpoint = getEndpointContract('TC-08');
    const res = await request('post', endpoint.semanticPath, {
      headers: { 'Content-Type': 'application/json' },
      data: { books: [{ bookId: contract.fixtures.primaryBookId, quantity: 1, price: 10 }] },
    });
    expectStatus(res, contract.validationRules.unauthenticatedStatus);
  });

  test('AUTH-04: GET /orders without auth should be rejected', async () => {
    const endpoint = getEndpointContract('TC-09');
    const res = await request('get', endpoint.semanticPath);
    expectStatus(res, contract.validationRules.unauthenticatedStatus);
  });
});
