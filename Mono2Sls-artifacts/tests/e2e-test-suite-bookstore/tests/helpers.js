const axios = require('axios');
const {
  buildPath,
  buildUrl,
  config,
  contract,
  authProvider,
  getTimeout,
  prepareTarget,
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
  const endpoint = contract.businessEndpoints.find((item) => item.id === id);
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

function assertBookShape(book, requiredFields = []) {
  assertHasFields(book, requiredFields);
  expect(typeof book.id).toBe('string');
  expect(typeof book.name).toBe('string');
  expect(typeof book.author).toBe('string');
  expect(typeof book.category).toBe('string');
  expect(typeof book.price).toBe('number');
}

async function createSession(label) {
  await authProvider.setup();
  return authProvider.createSession(label);
}

async function listBooks(params) {
  const endpoint = getEndpointContract('TC-01');
  return request('get', endpoint.semanticPath, { params });
}

async function getBook(bookId) {
  const endpoint = getEndpointContract('TC-03');
  return request('get', buildPath(endpoint.semanticPath, { book_id: bookId }));
}

async function searchBooks(q) {
  const endpoint = getEndpointContract('TC-05');
  return request('get', endpoint.semanticPath, {
    params: q ? { q } : undefined,
  });
}

async function getCart(session) {
  const endpoint = getEndpointContract('TC-07');
  return request('get', endpoint.semanticPath, { session });
}

async function getCartItem(session, bookId) {
  const endpoint = getEndpointContract('TC-07B');
  return request('get', buildPath(endpoint.semanticPath, { book_id: bookId }), { session });
}

async function addCartItem(session, item) {
  const endpoint = getEndpointContract('TC-07C');
  return request('post', endpoint.semanticPath, {
    session,
    headers: { 'Content-Type': 'application/json' },
    data: item,
  });
}

async function updateCartItem(session, item) {
  const endpoint = getEndpointContract('TC-07D');
  return request('put', endpoint.semanticPath, {
    session,
    headers: { 'Content-Type': 'application/json' },
    data: item,
  });
}

async function deleteCartItem(session, bookId) {
  const endpoint = getEndpointContract('TC-07E');
  return request('delete', endpoint.semanticPath, {
    session,
    headers: { 'Content-Type': 'application/json' },
    data: { bookId },
  });
}

async function createOrder(session, books) {
  const endpoint = getEndpointContract('TC-08');
  return request('post', endpoint.semanticPath, {
    session,
    headers: { 'Content-Type': 'application/json' },
    data: { books },
  });
}

async function listOrders(session) {
  const endpoint = getEndpointContract('TC-09');
  return request('get', endpoint.semanticPath, { session });
}

async function getBestsellers() {
  const endpoint = getEndpointContract('TC-10');
  return request('get', endpoint.semanticPath);
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
      return JSON.stringify({
        status: value.status,
        data: value.data,
      });
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
  prepareTarget,
  request,
  expectStatus,
  getEndpointContract,
  extractObject,
  extractList,
  assertHasFields,
  assertBookShape,
  createSession,
  listBooks,
  getBook,
  searchBooks,
  getCart,
  getCartItem,
  addCartItem,
  updateCartItem,
  deleteCartItem,
  createOrder,
  listOrders,
  getBestsellers,
  pollUntil,
  sleep,
  authProvider,
};
