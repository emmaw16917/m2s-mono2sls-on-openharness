const {
  contract,
  request,
  expectStatus,
  getEndpointContract,
  extractObject,
  assertPaymentShape,
} = require('./helpers');

describe('Payments Contract', () => {
  test('TC-16a: should collect payment with a valid charge id without authentication', async () => {
    const endpoint = getEndpointContract('TC-16');
    const res = await request('post', endpoint.semanticPath, {
      headers: { 'Content-Type': 'application/json' },
      data: { chargeId: 'ch_test_payment_collect' },
    });

    expectStatus(res, endpoint.successStatus);
    const payment = extractObject(res.data, endpoint);
    assertPaymentShape(payment, endpoint.response.requiredPaymentFields);
  });

  test('TC-17a: should refund payment with a valid charge id without authentication', async () => {
    const endpoint = getEndpointContract('TC-17');
    const res = await request('post', endpoint.semanticPath, {
      headers: { 'Content-Type': 'application/json' },
      data: { chargeId: 'ch_test_payment_refund' },
    });

    expectStatus(res, endpoint.successStatus);
    const refund = extractObject(res.data, endpoint);
    expect(refund.refundId).toBeDefined();
  });

  test('TC-18a: should expose payment lookup semantics for a collected charge id', async () => {
    const endpoint = getEndpointContract('TC-18');
    const chargeId = 'ch_test_payment_get';

    const collectRes = await request('post', '/payments/collect', {
      headers: { 'Content-Type': 'application/json' },
      data: { chargeId },
    });
    expectStatus(collectRes, 200);

    const res = await request('get', `/payments/${chargeId}`);
    expectStatus(res, endpoint.successStatus);
    const payment = extractObject(res.data, endpoint);
    expect(payment).toBeDefined();
  });

  test('TC-19a: should reject payment requests missing chargeId', async () => {
    const collectRes = await request('post', '/payments/collect', {
      headers: { 'Content-Type': 'application/json' },
      data: {},
    });
    expectStatus(collectRes, contract.validationRules.badRequestStatus);

    const refundRes = await request('post', '/payments/refund', {
      headers: { 'Content-Type': 'application/json' },
      data: {},
    });
    expectStatus(refundRes, contract.validationRules.badRequestStatus);
  });
});
