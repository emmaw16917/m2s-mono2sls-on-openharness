const { authProvider } = require('./setup');
const {
  contract,
  request,
  expectStatus,
  createBooking,
  cancelBooking,
} = require('./helpers');

describe('Robustness', () => {
  test('ROB-01a: key endpoints should return JSON responses', async () => {
    const res = await request('get', '/flights/search', {
      params: contract.fixtures.searchQuery,
    });
    expectStatus(res, 200);
    expect(String(res.headers['content-type'] || '')).toMatch(/application\/json/i);
  });

  test('ROB-01b: key endpoints should expose CORS headers', async () => {
    const res = await request('get', '/flights/search', {
      params: contract.fixtures.searchQuery,
    });
    expectStatus(res, 200);
    expect(res.headers['access-control-allow-origin']).toBeDefined();
  });

  test('ROB-03a: repeated unauthenticated protected requests should stay rejected', async () => {
    const requests = await Promise.all([
      request('post', '/bookings', {
        headers: { 'Content-Type': 'application/json' },
        data: {
          outboundFlightId: contract.fixtures.primaryFlightId,
          chargeId: 'ch_test_unauth_1',
        },
      }),
      request('post', '/bookings', {
        headers: { 'Content-Type': 'application/json' },
        data: {
          outboundFlightId: contract.fixtures.primaryFlightId,
          chargeId: 'ch_test_unauth_2',
        },
      }),
      request('get', '/customers/fake-customer/bookings'),
    ]);

    for (const res of requests) {
      expectStatus(res, contract.validationRules.unauthenticatedStatus);
    }
  });

  test('ROB-04a: flight search should be deterministic for the fixture query', async () => {
    const first = await request('get', '/flights/search', { params: contract.fixtures.searchQuery });
    const second = await request('get', '/flights/search', { params: contract.fixtures.searchQuery });

    expectStatus(first, 200);
    expectStatus(second, 200);
    expect(JSON.stringify(first.data)).toBe(JSON.stringify(second.data));
  });

  describe('Authenticated robustness', () => {
    let session;
    let bookingId;

    beforeAll(async () => {
      await authProvider.setup();
      session = await authProvider.createSession('robustness');
    });

    afterAll(async () => {
      if (bookingId) {
        try {
          await cancelBooking(session, bookingId);
        } catch (_) {
          // Best-effort cleanup only.
        }
      }
      await authProvider.teardown();
    });

    test('ROB-02a: booking should tolerate special characters in the optional name field', async () => {
      const res = await createBooking(session, {
        chargeId: 'ch_test_special_name',
        name: 'ASCII plus ce-shi and quotes "ok" and tag <tag>',
      });

      expectStatus(res, 201);
      bookingId = res.data.bookingId || res.data.data?.bookingId;
    });
  });
});
