const axios = require('axios');
const {
  buildPath,
  buildUrl,
  config,
  contract,
  authProvider,
  getTimeout,
  getCapability,
  prepareTarget,
} = require('./setup');

function getByPath(value, path) {
  if (path === '$') {
    return value;
  }
  if (path === '$.order') {
    return value?.order;
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

function getFixtureProduct(key) {
  const product = contract.fixtures.products[key];
  if (!product) {
    throw new Error(`Unknown fixture product key: ${key}`);
  }
  return JSON.parse(JSON.stringify(product));
}

function buildSelection(items) {
  return items.map((item) => {
    const product = getFixtureProduct(item.key);
    return {
      ...product,
      quantity: item.quantity || 1,
    };
  });
}

function getAddress(kind = 'default') {
  if (kind === 'foreign') {
    return JSON.parse(JSON.stringify(contract.fixtures.foreignAddress));
  }
  return JSON.parse(JSON.stringify(contract.fixtures.defaultAddress));
}

function computeTotal(products, deliveryPrice) {
  return products.reduce((sum, product) => sum + (product.price * (product.quantity || 1)), 0) + deliveryPrice;
}

async function createUserSession(label = 'user') {
  await authProvider.setup();
  return authProvider.createSession(label);
}

async function createRoleSession(role, label = role) {
  await authProvider.setup();
  if (typeof authProvider.createRoleSession === 'function') {
    return authProvider.createRoleSession(role, label);
  }
  if (role === 'admin' && typeof authProvider.createAdminSession === 'function') {
    return authProvider.createAdminSession(label);
  }
  return authProvider.createSession(label);
}

async function preauthPayment(amount, cardNumber = contract.fixtures.defaultCardNumber) {
  return request('post', getEndpointContract('TC-04').semanticPath, {
    headers: { 'Content-Type': 'application/json' },
    data: {
      cardNumber,
      amount,
    },
  });
}

async function checkPayment(paymentToken, amount) {
  return request('post', getEndpointContract('TC-05').semanticPath, {
    headers: { 'Content-Type': 'application/json' },
    data: {
      paymentToken,
      amount,
    },
  });
}

async function validatePayment(session, paymentToken, total) {
  return request('post', '/payment/validate', {
    session,
    headers: { 'Content-Type': 'application/json' },
    data: {
      paymentToken,
      total,
    },
  });
}

async function getDeliveryPricing(session, products, address) {
  return request('post', getEndpointContract('TC-06').semanticPath, {
    session,
    headers: { 'Content-Type': 'application/json' },
    data: { products, address },
  });
}

async function createOrder(session, body) {
  return request('post', getEndpointContract('TC-07').semanticPath, {
    session,
    headers: { 'Content-Type': 'application/json' },
    data: body,
  });
}

async function getOrder(session, orderId) {
  return request('get', buildPath(getEndpointContract('TC-08').semanticPath, { order_id: orderId }), { session });
}

async function listOrders(session) {
  return request('get', getEndpointContract('TC-09').semanticPath, { session });
}

async function updateOrder(session, orderId, body) {
  return request('put', buildPath(getEndpointContract('TC-10').semanticPath, { order_id: orderId }), {
    session,
    headers: { 'Content-Type': 'application/json' },
    data: body,
  });
}

async function deleteOrder(session, orderId) {
  return request('delete', buildPath(getEndpointContract('TC-11').semanticPath, { order_id: orderId }), {
    session,
  });
}

async function listPackagingRequests(session) {
  return request('get', getEndpointContract('TC-12').semanticPath, { session });
}

async function getPackagingRequest(session, orderId) {
  return request('get', buildPath(getEndpointContract('TC-13').semanticPath, { order_id: orderId }), {
    session,
  });
}

async function startPackaging(session, orderId) {
  return request('post', buildPath(getEndpointContract('TC-14').semanticPath, { order_id: orderId }), {
    session,
    headers: { 'Content-Type': 'application/json' },
    data: {},
  });
}

async function updatePackagingProduct(session, orderId, productId, quantity) {
  return request('put', buildPath(getEndpointContract('TC-15').semanticPath, { order_id: orderId }), {
    session,
    headers: { 'Content-Type': 'application/json' },
    data: { productId, quantity },
  });
}

async function completePackaging(session, orderId) {
  return request('post', buildPath(getEndpointContract('TC-16').semanticPath, { order_id: orderId }), {
    session,
    headers: { 'Content-Type': 'application/json' },
    data: {},
  });
}

async function failPackaging(session, orderId, reason = 'Out of stock') {
  return request('post', buildPath(getEndpointContract('TC-17').semanticPath, { order_id: orderId }), {
    session,
    headers: { 'Content-Type': 'application/json' },
    data: { reason },
  });
}

async function listDeliveries(session) {
  return request('get', getEndpointContract('TC-18').semanticPath, { session });
}

async function getDelivery(session, orderId) {
  return request('get', buildPath(getEndpointContract('TC-19').semanticPath, { order_id: orderId }), {
    session,
  });
}

async function startDelivery(session, orderId) {
  return request('post', buildPath(getEndpointContract('TC-20').semanticPath, { order_id: orderId }), {
    session,
    headers: { 'Content-Type': 'application/json' },
    data: {},
  });
}

async function completeDelivery(session, orderId) {
  return request('post', buildPath(getEndpointContract('TC-21').semanticPath, { order_id: orderId }), {
    session,
    headers: { 'Content-Type': 'application/json' },
    data: {},
  });
}

async function failDelivery(session, orderId, reason = 'Wrong address') {
  return request('post', buildPath(getEndpointContract('TC-22').semanticPath, { order_id: orderId }), {
    session,
    headers: { 'Content-Type': 'application/json' },
    data: { reason },
  });
}

async function createAuthorizedOrder(session, items, address = getAddress()) {
  const products = buildSelection(items);
  const pricingRes = await getDeliveryPricing(session, products, address);
  expectStatus(pricingRes, getEndpointContract('TC-06').response.successStatus);
  const deliveryPrice = pricingRes.data.pricing;
  const total = computeTotal(products, deliveryPrice);

  const preauthRes = await preauthPayment(total);
  expectStatus(preauthRes, getEndpointContract('TC-04').response.successStatus);
  const paymentToken = preauthRes.data.paymentToken;

  const createRes = await createOrder(session, {
    products,
    address,
    deliveryPrice,
    paymentToken,
  });
  expectStatus(createRes, getEndpointContract('TC-07').response.successStatus);
  const createdOrder = createRes.data.order || createRes.data;

  return {
    products,
    address,
    deliveryPrice,
    total,
    paymentToken,
    order: createdOrder,
  };
}

async function waitForPackagingRequest(session, orderId) {
  return pollUntil(async () => {
    const res = await getPackagingRequest(session, orderId);
    return res;
  }, (res) => res.status === 200);
}

async function waitForDelivery(session, orderId) {
  return pollUntil(async () => {
    const res = await getDelivery(session, orderId);
    return res;
  }, (res) => res.status === 200);
}

async function waitForOrderStatus(session, orderId, expectedStatus) {
  return pollUntil(async () => {
    const res = await getOrder(session, orderId);
    return res;
  }, (res) => res.status === 200 && (res.data.order?.status || res.data.status) === expectedStatus);
}

async function waitForPaymentNotAuthorized(paymentToken, amount) {
  return pollUntil(async () => {
    const res = await checkPayment(paymentToken, amount);
    return res;
  }, (res) => res.status === 200 && res.data.ok === false);
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
  getCapability,
  prepareTarget,
  request,
  expectStatus,
  getEndpointContract,
  extractObject,
  extractList,
  assertHasFields,
  getFixtureProduct,
  buildSelection,
  getAddress,
  computeTotal,
  createUserSession,
  createRoleSession,
  preauthPayment,
  checkPayment,
  validatePayment,
  getDeliveryPricing,
  createOrder,
  getOrder,
  listOrders,
  updateOrder,
  deleteOrder,
  listPackagingRequests,
  getPackagingRequest,
  startPackaging,
  updatePackagingProduct,
  completePackaging,
  failPackaging,
  listDeliveries,
  getDelivery,
  startDelivery,
  completeDelivery,
  failDelivery,
  createAuthorizedOrder,
  waitForPackagingRequest,
  waitForDelivery,
  waitForOrderStatus,
  waitForPaymentNotAuthorized,
  pollUntil,
  sleep,
};
