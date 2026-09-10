const axios = require('axios');
const { buildPath, buildUrl, config, contract, getTimeout } = require('./setup');

function getByPath(value, path) {
  if (path === '$') {
    return value;
  }

  return String(path)
    .split('.')
    .reduce((current, key) => (current == null ? undefined : current[key]), value);
}

function firstDefined(value, candidatePaths = []) {
  for (const path of candidatePaths) {
    const resolved = getByPath(value, path);
    if (resolved !== undefined && resolved !== null) {
      return resolved;
    }
  }
  return undefined;
}

function parseMaybeJson(value) {
  if (typeof value === 'string') {
    try {
      return JSON.parse(value);
    } catch (_) {
      return value;
    }
  }
  return value;
}

async function request(method, endpointPath, options = {}) {
  const { session, headers = {}, data, params } = options;
  const mergedHeaders = { ...headers };

  if (session && session.authHeader && !mergedHeaders.Authorization) {
    mergedHeaders.Authorization = session.authHeader;
  }

  const response = await axios({
    method,
    url: buildUrl(endpointPath),
    headers: mergedHeaders,
    data,
    params,
    timeout: getTimeout('requestTimeoutMs', 30000),
    validateStatus: () => true,
  });

  return {
    ...response,
    data: parseMaybeJson(response.data),
  };
}

function expectStatus(res, expected) {
  const expectedList = Array.isArray(expected) ? expected : [expected];
  expect(expectedList).toContain(res.status);
}

function getEndpointContract(id) {
  const endpoint = contract.businessEndpoints.find((item) => item.id === id)
    || contract.authCompatibilityEndpoints.find((item) => item.id === id);
  if (!endpoint) {
    throw new Error(`No endpoint contract found for ${id}`);
  }
  return endpoint;
}

function extractObject(responseData, endpointContract) {
  const objectValue = firstDefined(responseData, endpointContract?.response?.objectLocation || []);
  expect(objectValue).toBeDefined();
  expect(Array.isArray(objectValue)).toBe(false);
  return objectValue;
}

function extractList(responseData, endpointContract) {
  const listValue = firstDefined(responseData, endpointContract?.response?.listLocation || []);
  expect(Array.isArray(listValue)).toBe(true);
  return listValue;
}

function assertHasFields(value, requiredFields = []) {
  expect(value).toBeDefined();
  expect(typeof value).toBe('object');
  for (const field of requiredFields) {
    expect(value[field]).toBeDefined();
  }
}

function assertFlightShape(flight, requiredFields = []) {
  assertHasFields(flight, requiredFields);
  expect(typeof flight.id).toBe('string');
  expect(typeof flight.seatCapacity).toBe('number');
  expect(typeof flight.maximumSeating).toBe('number');
}

function assertBookingShape(booking, requiredFields = []) {
  assertHasFields(booking, requiredFields);
  expect(typeof booking.id).toBe('string');
  expect(typeof booking.customer).toBe('string');
  expect(typeof booking.status).toBe('string');
}

function assertPaymentShape(payment, requiredFields = []) {
  assertHasFields(payment, requiredFields);
  expect(typeof payment.receiptUrl).toBe('string');
  expect(typeof payment.price).toBe('number');
}

function assertLoyaltyShape(loyalty, requiredFields = []) {
  assertHasFields(loyalty, requiredFields);
  expect(typeof loyalty.points).toBe('number');
  expect(typeof loyalty.level).toBe('string');
  expect(typeof loyalty.remainingPoints).toBe('number');
}

function createChargeId(label = 'default') {
  return `${contract.fixtures.paymentChargePrefix}${label}_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
}

async function searchFlights(query = contract.fixtures.searchQuery, session = null) {
  const endpoint = getEndpointContract('TC-01');
  const res = await request('get', endpoint.semanticPath, {
    session,
    params: query,
  });
  return res;
}

async function getFlight(flightId, session = null) {
  const endpoint = getEndpointContract('TC-03');
  const res = await request('get', buildPath(endpoint.semanticPath, { flight_id: flightId }), { session });
  return res;
}

async function createBooking(session, overrides = {}) {
  const endpoint = getEndpointContract('TC-07');
  const res = await request('post', endpoint.semanticPath, {
    session,
    headers: { 'Content-Type': 'application/json' },
    data: {
      outboundFlightId: contract.fixtures.primaryFlightId,
      chargeId: createChargeId('booking'),
      name: 'Airline E2E Booking',
      ...overrides,
    },
  });
  return res;
}

async function getBooking(session, bookingId) {
  const endpoint = getEndpointContract('TC-08');
  return request('get', buildPath(endpoint.semanticPath, { booking_id: bookingId }), { session });
}

async function cancelBooking(session, bookingId) {
  const endpoint = getEndpointContract('TC-10');
  return request('post', buildPath(endpoint.semanticPath, { booking_id: bookingId }), {
    session,
    headers: { 'Content-Type': 'application/json' },
    data: {},
  });
}

async function listCustomerBookings(session, customerId) {
  const endpoint = getEndpointContract('TC-09');
  return request('get', buildPath(endpoint.semanticPath, { customer_id: customerId }), { session });
}

async function getLoyalty(session, customerId) {
  const endpoint = getEndpointContract('TC-15');
  return request('get', buildPath(endpoint.semanticPath, { customer_id: customerId }), { session });
}

async function pollUntil(fetcher, predicate, options = {}) {
  const timeoutMs = options.timeoutMs || getTimeout('eventualConsistencyTimeoutMs', 30000);
  const intervalMs = options.intervalMs || getTimeout('pollIntervalMs', 2000);
  const startedAt = Date.now();

  let lastValue;
  while ((Date.now() - startedAt) < timeoutMs) {
    lastValue = await fetcher();
    if (predicate(lastValue)) {
      return lastValue;
    }
    await sleep(intervalMs);
  }

  throw new Error(`Condition not satisfied within ${timeoutMs}ms. Last value: ${JSON.stringify(lastValue)}`);
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function getCustomerId(session) {
  return session && session.sub;
}

function pathExistsInArtifact(endpoint) {
  return endpoint && endpoint.semanticPath;
}

module.exports = {
  config,
  contract,
  request,
  expectStatus,
  getEndpointContract,
  extractObject,
  extractList,
  assertHasFields,
  assertFlightShape,
  assertBookingShape,
  assertPaymentShape,
  assertLoyaltyShape,
  createChargeId,
  searchFlights,
  getFlight,
  createBooking,
  getBooking,
  cancelBooking,
  listCustomerBookings,
  getLoyalty,
  pollUntil,
  sleep,
  getCustomerId,
  pathExistsInArtifact,
};
