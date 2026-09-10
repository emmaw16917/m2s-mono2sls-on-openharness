const { authProvider, contract, getCapability } = require('./setup');
const {
  expectStatus,
  extractObject,
  getEndpointContract,
  createBooking,
  cancelBooking,
  getFlight,
  getLoyalty,
  getCustomerId,
  pollUntil,
} = require('./helpers');

const describeIf = (getCapability('asyncLoyaltyAfterBooking') || getCapability('asyncSeatReleaseAfterCancel'))
  ? describe
  : describe.skip;

let session;

beforeAll(async () => {
  if (!(getCapability('asyncLoyaltyAfterBooking') || getCapability('asyncSeatReleaseAfterCancel'))) {
    return;
  }
  await authProvider.setup();
  session = await authProvider.createSession('async');
});

afterAll(async () => {
  if (!(getCapability('asyncLoyaltyAfterBooking') || getCapability('asyncSeatReleaseAfterCancel'))) {
    return;
  }
  await authProvider.teardown();
});

describeIf('Async Semantics', () => {
  const loyaltyDescribe = getCapability('asyncLoyaltyAfterBooking') ? describe : describe.skip;
  loyaltyDescribe('Booking -> Loyalty eventual consistency', () => {
    test('ASYNC-01a: successful booking should eventually increase loyalty points', async () => {
      const customerId = getCustomerId(session);
      const beforeRes = await getLoyalty(session, customerId);
      expectStatus(beforeRes, 200);
      const before = extractObject(beforeRes.data, getEndpointContract('TC-15'));

      const createRes = await createBooking(session, {
        outboundFlightId: contract.fixtures.secondaryFlightId,
        name: 'Async loyalty booking',
      });
      expectStatus(createRes, 201);
      const booking = extractObject(createRes.data, getEndpointContract('TC-07'));

      const afterRes = await pollUntil(
        async () => getLoyalty(session, customerId),
        (response) => {
          if (response.status !== 200) {
            return false;
          }
          const loyalty = extractObject(response.data, getEndpointContract('TC-15'));
          return loyalty.points > before.points;
        },
        contract.asyncExpectations.loyaltyAfterBooking
      );

      const after = extractObject(afterRes.data, getEndpointContract('TC-15'));
      expect(after.points).toBeGreaterThan(before.points);

      await cancelBooking(session, booking.bookingId);
    });
  });

  const seatDescribe = getCapability('asyncSeatReleaseAfterCancel') ? describe : describe.skip;
  seatDescribe('Cancel -> Seat restoration eventual consistency', () => {
    test('ASYNC-02a: cancelling a booking should eventually restore seat capacity', async () => {
      const flightId = contract.fixtures.secondaryFlightId;
      const beforeRes = await getFlight(flightId);
      expectStatus(beforeRes, 200);
      const before = extractObject(beforeRes.data, getEndpointContract('TC-03'));

      const createRes = await createBooking(session, {
        outboundFlightId: flightId,
        name: 'Async seat restore booking',
      });
      expectStatus(createRes, 201);
      const booking = extractObject(createRes.data, getEndpointContract('TC-07'));

      const cancelRes = await cancelBooking(session, booking.bookingId);
      expectStatus(cancelRes, 200);

      const restoredRes = await pollUntil(
        async () => getFlight(flightId),
        (response) => {
          if (response.status !== 200) {
            return false;
          }
          const currentFlight = extractObject(response.data, getEndpointContract('TC-03'));
          return currentFlight.seatCapacity === before.seatCapacity;
        },
        contract.asyncExpectations.seatRestorationAfterCancel
      );

      const restored = extractObject(restoredRes.data, getEndpointContract('TC-03'));
      expect(restored.seatCapacity).toBe(before.seatCapacity);
    });
  });
});
