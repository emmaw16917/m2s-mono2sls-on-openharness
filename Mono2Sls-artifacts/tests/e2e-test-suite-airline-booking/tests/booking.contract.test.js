const { authProvider } = require('./setup');
const {
  contract,
  request,
  expectStatus,
  getEndpointContract,
  extractObject,
  extractList,
  assertBookingShape,
  assertPaymentShape,
  createBooking,
  getBooking,
  cancelBooking,
  listCustomerBookings,
  getCustomerId,
} = require('./helpers');

describe('Booking Contract', () => {
  test('TC-06a: should reject booking creation without authentication', async () => {
    const res = await request('post', '/bookings', {
      headers: { 'Content-Type': 'application/json' },
      data: {
        outboundFlightId: contract.fixtures.primaryFlightId,
        chargeId: 'ch_test_unauth',
      },
    });

    expectStatus(res, contract.validationRules.unauthenticatedStatus);
  });

  describe('Authenticated booking flow', () => {
    let session;
    let bookingId;
    let wasCancelled = false;

    beforeAll(async () => {
      await authProvider.setup();
      session = await authProvider.createSession('booking-contract');
    });

    afterAll(async () => {
      if (session && bookingId && !wasCancelled) {
        try {
          await cancelBooking(session, bookingId);
        } catch (_) {
          // Best-effort cleanup only.
        }
      }
      await authProvider.teardown();
    });

    test('TC-07a: should create a booking for an authenticated user', async () => {
      const endpoint = getEndpointContract('TC-07');
      const res = await createBooking(session, {
        outboundFlightId: contract.fixtures.primaryFlightId,
        chargeId: 'ch_test_core',
        name: 'Core booking test',
      });

      expectStatus(res, endpoint.successStatus);
      const created = extractObject(res.data, endpoint);
      expect(created.status).toBe('CONFIRMED');
      expect(typeof created.bookingId).toBe('string');
      expect(typeof created.bookingReference).toBe('string');
      expect(created.payment).toBeDefined();
      assertPaymentShape(created.payment, ['receiptUrl', 'price']);
      bookingId = created.bookingId;
    });

    test('TC-08a: should fetch the owner booking details', async () => {
      const endpoint = getEndpointContract('TC-08');
      const res = await getBooking(session, bookingId);

      expectStatus(res, endpoint.successStatus);
      const booking = extractObject(res.data, endpoint);
      assertBookingShape(booking, endpoint.response.requiredBookingFields);
      expect(booking.id).toBe(bookingId);
      expect(booking.customer).toBe(getCustomerId(session));
    });

    test('TC-09a: should list the owner bookings', async () => {
      const endpoint = getEndpointContract('TC-09');
      const res = await listCustomerBookings(session, getCustomerId(session));

      expectStatus(res, endpoint.successStatus);
      const bookings = extractList(res.data, endpoint);
      expect(bookings.some((booking) => booking.id === bookingId)).toBe(true);
    });

    test('TC-10a: should cancel the owner booking', async () => {
      const endpoint = getEndpointContract('TC-10');
      const res = await cancelBooking(session, bookingId);

      expectStatus(res, endpoint.successStatus);
      const cancelled = extractObject(res.data, endpoint);
      expect(cancelled.bookingId).toBe(bookingId);
      expect(cancelled.status).toBe('CANCELLED');
      expect(cancelled.refund).toBeDefined();
      expect(cancelled.refund.refundId).toBeDefined();
      wasCancelled = true;
    });

    test('TC-11a: should persist cancelled booking state', async () => {
      const res = await getBooking(session, bookingId);
      expectStatus(res, 200);
      const booking = extractObject(res.data, getEndpointContract('TC-08'));
      expect(booking.status).toBe('CANCELLED');
    });

    test('TC-12a: should reject invalid booking payloads', async () => {
      const res = await request('post', '/bookings', {
        session,
        headers: { 'Content-Type': 'application/json' },
        data: {
          outboundFlightId: contract.fixtures.primaryFlightId,
        },
      });

      expectStatus(res, contract.validationRules.badRequestStatus);
    });
  });
});
