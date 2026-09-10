const axios = require('axios');
const {
  buildPath,
  buildUrl,
  config,
  contract,
  authProvider,
  getTimeout,
  prepareTarget,
  testEventId,
} = require('./setup');

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

function createOrderId(label = 'order') {
  return `e2e-${label}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

async function getConfig(session = null, eventId = testEventId) {
  const endpoint = getEndpointContract('TC-01');
  return request('get', endpoint.semanticPath, { session, params: { eventId } });
}

async function getAllConfigs(session = null) {
  const endpoint = getEndpointContract('TC-02');
  return request('get', endpoint.semanticPath, { session });
}

async function getOrderJourney(orderId, session = null) {
  const endpoint = getEndpointContract('TC-03');
  return request('get', buildPath(endpoint.semanticPath, { order_id: orderId }), { session });
}

async function createOrder(session, overrides = {}) {
  const endpoint = getEndpointContract('TC-04');
  return request('post', endpoint.semanticPath, {
    session,
    headers: { 'Content-Type': 'application/json' },
    data: {
      orderId: createOrderId('direct'),
      eventId: testEventId,
      ...overrides,
    },
  });
}

async function getMyOrders(session) {
  const endpoint = getEndpointContract('TC-05');
  return request('get', endpoint.semanticPath, { session });
}

async function getQrCode(adminSession, eventId = testEventId) {
  const endpoint = getEndpointContract('TC-06');
  return request('get', endpoint.semanticPath, { session: adminSession, params: { eventId } });
}

async function verifyQrCode(session, token, eventId = testEventId) {
  const endpoint = getEndpointContract('TC-07');
  return request('post', endpoint.semanticPath, {
    session,
    params: { eventId, token },
  });
}

async function getOrders(adminSession, state, eventId = testEventId) {
  const endpoint = getEndpointContract('TC-08');
  return request('get', endpoint.semanticPath, {
    session: adminSession,
    params: { state, eventId, maxItems: 100 },
  });
}

async function getOrder(adminSession, orderId) {
  const endpoint = getEndpointContract('TC-09');
  return request('get', buildPath(endpoint.semanticPath, { order_id: orderId }), {
    session: adminSession,
  });
}

async function updateOrder(adminSession, orderId, action, body = {}, eventId = testEventId) {
  const endpoint = getEndpointContract('TC-10');
  const params = { eventId };
  if (action) {
    params.action = action;
  }
  return request('put', buildPath(endpoint.semanticPath, { order_id: orderId }), {
    session: adminSession,
    headers: { 'Content-Type': 'application/json' },
    params,
    data: body,
  });
}

async function updateConfig(adminSession, body, eventId = testEventId) {
  const endpoint = getEndpointContract('TC-11');
  return request('put', endpoint.semanticPath, {
    session: adminSession,
    headers: { 'Content-Type': 'application/json' },
    params: { eventId },
    data: body,
  });
}

async function createUserSession(label) {
  await authProvider.setup();
  return authProvider.createSession(label);
}

async function createAdminSession(label) {
  await authProvider.setup();
  return authProvider.createAdminSession(label);
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

  throw new Error(`Condition not satisfied within ${timeoutMs}ms. Last value: ${summarizeForError(lastValue)}`);
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function summarizeForError(value) {
  if (value && typeof value === 'object' && 'status' in value) {
    try {
      return JSON.stringify({ status: value.status, data: value.data });
    } catch (_) {
      return `[status=${value.status}]`;
    }
  }
  try {
    return JSON.stringify(value);
  } catch (_) {
    return String(value);
  }
}

module.exports = {
  config,
  contract,
  authProvider,
  prepareTarget,
  testEventId,
  request,
  expectStatus,
  getEndpointContract,
  extractObject,
  extractList,
  assertHasFields,
  createOrderId,
  getConfig,
  getAllConfigs,
  getOrderJourney,
  createOrder,
  getMyOrders,
  getQrCode,
  verifyQrCode,
  getOrders,
  getOrder,
  updateOrder,
  updateConfig,
  createUserSession,
  createAdminSession,
  pollUntil,
  sleep,
};
