const axios = require('axios');
const { buildUrl, contract } = require('./setup');

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

async function request(method, endpointPath, options = {}) {
  const { session, data, headers = {}, params } = options;
  const mergedHeaders = { ...headers };

  if (session && session.authHeader && !mergedHeaders.Authorization) {
    mergedHeaders.Authorization = session.authHeader;
  }

  return axios({
    method,
    url: buildUrl(endpointPath),
    data,
    params,
    headers: mergedHeaders,
    validateStatus: () => true,
  });
}

function extractTodo(responseData, endpointContract) {
  const todo = firstDefined(responseData, endpointContract?.response?.todoLocation || []);
  expect(todo).toBeDefined();
  expect(Array.isArray(todo)).toBe(false);
  return todo;
}

function extractList(responseData, endpointContract) {
  const list = firstDefined(responseData, endpointContract?.response?.listLocation || []);
  expect(Array.isArray(list)).toBe(true);
  return list;
}

function assertTodoShape(todo, requiredFields = []) {
  expect(todo).toBeDefined();
  expect(typeof todo).toBe('object');

  for (const field of requiredFields) {
    expect(todo[field]).toBeDefined();
  }

  expect(typeof todo.id).toBe('string');
  expect(typeof todo.item).toBe('string');
  expect(typeof todo.completed).toBe('boolean');
}

async function createTodo(session, payload) {
  const createContract = contract.businessEndpoints.find((endpoint) => endpoint.id === 'TC-CREATE');
  const res = await request('post', '/item', {
    session,
    data: payload,
  });

  expect(res.status).toBe(createContract.successStatus);

  const todo = extractTodo(res.data, createContract);
  assertTodoShape(todo, createContract.response.requiredTodoFields);
  return todo;
}

async function deleteTodoIfExists(session, todoId) {
  if (!session || !todoId) {
    return;
  }

  try {
    await request('delete', `/item/${todoId}`, { session });
  } catch (_) {
    // Cleanup is best-effort only.
  }
}

module.exports = {
  request,
  createTodo,
  deleteTodoIfExists,
  extractTodo,
  extractList,
  assertTodoShape,
};
