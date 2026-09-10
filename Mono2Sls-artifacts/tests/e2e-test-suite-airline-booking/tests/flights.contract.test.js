const {
  contract,
  request,
  expectStatus,
  getEndpointContract,
  extractObject,
  extractList,
  assertFlightShape,
  getFlight,
  searchFlights,
} = require('./helpers');

const searchableFlightId = contract.fixtures.primaryFlightId;
const invalidFlightId = contract.fixtures.invalidFlightId;

describe('Flights Contract', () => {
  test('TC-01a: should search flights from the source-monolith fixture schedule without authentication', async () => {
    const endpoint = getEndpointContract('TC-01');
    const res = await searchFlights();

    expectStatus(res, endpoint.successStatus);
    const flights = extractList(res.data, endpoint);
    expect(flights.some((flight) => flight.id === searchableFlightId)).toBe(true);
    const targetFlight = flights.find((flight) => flight.id === searchableFlightId);
    assertFlightShape(targetFlight, endpoint.response.requiredFlightFields);
  });

  test('TC-02a: should reject flight search with missing required query parameters', async () => {
    const res = await request('get', '/flights/search', {
      params: {
        departureCode: contract.fixtures.searchQuery.departureCode,
        arrivalCode: contract.fixtures.searchQuery.arrivalCode,
      },
    });

    expectStatus(res, contract.validationRules.badRequestStatus);
  });

  test('TC-03a: should fetch a known flight by id without authentication', async () => {
    const endpoint = getEndpointContract('TC-03');
    const res = await getFlight(searchableFlightId);

    expectStatus(res, endpoint.successStatus);
    const flight = extractObject(res.data, endpoint);
    assertFlightShape(flight, endpoint.response.requiredFlightFields);
    expect(flight.id).toBe(searchableFlightId);
  });

  test('TC-04a: should reject unknown flight ids', async () => {
    const endpoint = getEndpointContract('TC-03');
    const res = await getFlight(invalidFlightId);
    expectStatus(res, endpoint.notFoundStatus);
  });

  test('TC-05a: reserve and release should change observable seat capacity', async () => {
    const endpoint = getEndpointContract('TC-05');
    const beforeRes = await getFlight(searchableFlightId);
    expectStatus(beforeRes, 200);
    const before = extractObject(beforeRes.data, getEndpointContract('TC-03'));

    const reserveRes = await request('post', `/flights/${searchableFlightId}/reserve`, {
      headers: { 'Content-Type': 'application/json' },
      data: {},
    });
    expectStatus(reserveRes, endpoint.successStatus);
    const reserveBody = extractObject(reserveRes.data, endpoint);
    expect(reserveBody.status).toBe('SUCCESS');

    const afterReserveRes = await getFlight(searchableFlightId);
    expectStatus(afterReserveRes, 200);
    const afterReserve = extractObject(afterReserveRes.data, getEndpointContract('TC-03'));
    expect(afterReserve.seatCapacity).toBe(before.seatCapacity - 1);

    const releaseRes = await request('post', `/flights/${searchableFlightId}/release`, {
      headers: { 'Content-Type': 'application/json' },
      data: {},
    });
    expectStatus(releaseRes, endpoint.successStatus);
    const releaseBody = extractObject(releaseRes.data, endpoint);
    expect(releaseBody.status).toBe('SUCCESS');

    const afterReleaseRes = await getFlight(searchableFlightId);
    expectStatus(afterReleaseRes, 200);
    const afterRelease = extractObject(afterReleaseRes.data, getEndpointContract('TC-03'));
    expect(afterRelease.seatCapacity).toBe(before.seatCapacity);
  });
});
