const axios = require('axios');
const {
  buildPath,
  buildUrl,
  config,
  contract,
  authProvider,
  getTimeout,
  prepareTarget,
  getRuntimeState,
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

function createAnonymousSession(label = 'anonymous') {
  return {
    label,
    cookieJar: {},
  };
}

async function createSession(label = 'user') {
  await authProvider.setup();
  const session = await authProvider.createSession(label);
  session.cookieJar = session.cookieJar || {};
  return session;
}

function cloneSessionWithCookies(session, label = 'cloned') {
  return {
    ...session,
    label,
    cookieJar: { ...(session.cookieJar || {}) },
  };
}

function copyCookies(fromSession, toSession) {
  toSession.cookieJar = {
    ...(toSession.cookieJar || {}),
    ...(fromSession.cookieJar || {}),
  };
}

async function request(method, endpointPath, options = {}) {
  const { session, headers = {}, data, params } = options;
  const mergedHeaders = { ...headers };

  if (session && session.authHeader && !mergedHeaders.Authorization) {
    mergedHeaders.Authorization = session.authHeader;
  }

  const cookieHeader = buildCookieHeader(session);
  if (cookieHeader && !mergedHeaders.Cookie && !mergedHeaders.cookie) {
    mergedHeaders.Cookie = cookieHeader;
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

  if (session) {
    updateSessionCookies(session, response);
  }

  return {
    ...response,
    data: parseMaybeJson(response.data),
  };
}

function buildCookieHeader(session) {
  if (!session || !session.cookieJar) {
    return '';
  }

  return Object.entries(session.cookieJar)
    .map(([key, value]) => `${key}=${value}`)
    .join('; ');
}

function updateSessionCookies(session, response) {
  if (!session.cookieJar) {
    session.cookieJar = {};
  }

  const rawSetCookie = response?.headers?.['set-cookie'] || response?.headers?.['Set-Cookie'];
  if (!rawSetCookie) {
    return;
  }

  const cookieHeaders = Array.isArray(rawSetCookie) ? rawSetCookie : [rawSetCookie];
  for (const header of cookieHeaders) {
    const cookiePair = String(header).split(';')[0];
    const separatorIndex = cookiePair.indexOf('=');
    if (separatorIndex <= 0) {
      continue;
    }

    const key = cookiePair.slice(0, separatorIndex).trim();
    const value = cookiePair.slice(separatorIndex + 1).trim();
    if (key) {
      session.cookieJar[key] = value;
    }
  }
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

function assertProductShape(product, requiredFields = []) {
  assertHasFields(product, requiredFields);
  expect(typeof product.id).toBe('string');
  expect(typeof product.name).toBe('string');
  expect(typeof product.price).toBe('number');
}

function findCartItem(products, productId) {
  return (products || []).find((item) => {
    return item?.sk === productId
      || item?.sk === `product#${productId}`
      || item?.productId === productId
      || item?.productDetail?.id === productId;
  });
}

async function listProducts() {
  const endpoint = getEndpointContract('TC-01');
  return request('get', endpoint.semanticPath);
}

async function getProduct(productId) {
  const endpoint = getEndpointContract('TC-02');
  return request('get', buildPath(endpoint.semanticPath, { product_id: productId }));
}

async function getCart(session) {
  const endpoint = getEndpointContract('TC-04');
  return request('get', endpoint.semanticPath, { session });
}

async function addToCart(session, productId, quantity) {
  const endpoint = getEndpointContract('TC-05');
  return request('post', endpoint.semanticPath, {
    session,
    headers: { 'Content-Type': 'application/json' },
    data: { productId, quantity },
  });
}

async function updateCart(session, productId, quantity) {
  const endpoint = getEndpointContract('TC-06');
  return request('put', buildPath(endpoint.semanticPath, { product_id: productId }), {
    session,
    headers: { 'Content-Type': 'application/json' },
    data: { quantity },
  });
}

async function migrateCart(session) {
  const endpoint = getEndpointContract('TC-08');
  return request('post', endpoint.semanticPath, {
    session,
    headers: { 'Content-Type': 'application/json' },
    data: {},
  });
}

async function checkoutCart(session) {
  const endpoint = getEndpointContract('TC-09');
  return request('post', endpoint.semanticPath, {
    session,
    headers: { 'Content-Type': 'application/json' },
    data: {},
  });
}

async function getCartTotal(productId) {
  const endpoint = getEndpointContract('TC-10');
  return request('get', buildPath(endpoint.semanticPath, { product_id: productId }));
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
  authProvider,
  prepareTarget,
  getRuntimeState,
  request,
  expectStatus,
  getEndpointContract,
  extractObject,
  extractList,
  assertHasFields,
  assertProductShape,
  createAnonymousSession,
  createSession,
  cloneSessionWithCookies,
  copyCookies,
  findCartItem,
  listProducts,
  getProduct,
  getCart,
  addToCart,
  updateCart,
  migrateCart,
  checkoutCart,
  getCartTotal,
  pollUntil,
  sleep,
};
