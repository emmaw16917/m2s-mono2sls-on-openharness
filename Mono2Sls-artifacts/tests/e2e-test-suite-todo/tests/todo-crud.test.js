const { contract, authProvider } = require('./setup');
const {
  assertTodoShape,
  deleteTodoIfExists,
  extractList,
  extractTodo,
  request,
} = require('./helpers');

const createContract = contract.businessEndpoints.find((ep) => ep.id === 'TC-CREATE');
const listContract = contract.businessEndpoints.find((ep) => ep.id === 'TC-LIST');
const getContract = contract.businessEndpoints.find((ep) => ep.id === 'TC-GET');
const updateContract = contract.businessEndpoints.find((ep) => ep.id === 'TC-UPDATE');
const doneContract = contract.businessEndpoints.find((ep) => ep.id === 'TC-DONE');
const deleteContract = contract.businessEndpoints.find((ep) => ep.id === 'TC-DELETE');

let session;
let createdTodoId;

beforeAll(async () => {
  await authProvider.setup();
  session = await authProvider.createSession('crud');
});

afterAll(async () => {
  await deleteTodoIfExists(session, createdTodoId);
  await authProvider.teardown();
});

describe('TC-01: Create Todo', () => {
  test('TC-01a: should create a todo with valid payload', async () => {
    const res = await request('post', '/item', {
      session,
      data: {
        item: 'E2E Test Todo',
        completed: false,
      },
    });

    expect(res.status).toBe(createContract.successStatus);
    const todo = extractTodo(res.data, createContract);
    assertTodoShape(todo, createContract.response.requiredTodoFields);
    expect(todo.item).toBe('E2E Test Todo');
    expect(todo.completed).toBe(false);
    createdTodoId = todo.id;
  });

  test('TC-01b: should reject create with empty body', async () => {
    const res = await request('post', '/item', {
      session,
      data: {},
    });

    expect(res.status).toBe(contract.validationRules.invalidCreatePayloadStatus);
  });
});

describe('TC-02: List Todos', () => {
  test('TC-02a: should return a list containing the created todo', async () => {
    const res = await request('get', '/item', { session });

    expect(res.status).toBe(listContract.successStatus);
    const items = extractList(res.data, listContract);
    const found = items.find((item) => item.id === createdTodoId);
    expect(found).toBeDefined();
    assertTodoShape(found, getContract.response.requiredTodoFields);
  });
});

describe('TC-03: Get Todo by ID', () => {
  test('TC-03a: should return the specific todo', async () => {
    const res = await request('get', `/item/${createdTodoId}`, { session });

    expect(res.status).toBe(getContract.successStatus);
    const todo = extractTodo(res.data, getContract);
    assertTodoShape(todo, getContract.response.requiredTodoFields);
    expect(todo.id).toBe(createdTodoId);
    expect(todo.item).toBe('E2E Test Todo');
  });

  test('TC-03b: should return 404 for non-existent ID', async () => {
    const res = await request('get', '/item/non-existent-id-12345', { session });
    expect(res.status).toBe(getContract.notFoundStatus);
  });
});

describe('TC-04: Update Todo', () => {
  test('TC-04a: should update the todo text and persist it', async () => {
    const res = await request('put', `/item/${createdTodoId}`, {
      session,
      data: {
        item: 'Updated E2E Todo',
        completed: false,
      },
    });

    expect(res.status).toBe(updateContract.successStatus);
    const todo = extractTodo(res.data, updateContract);
    assertTodoShape(todo, updateContract.response.requiredTodoFields);
    expect(todo.item).toBe('Updated E2E Todo');
    expect(todo.completed).toBe(false);

    const getRes = await request('get', `/item/${createdTodoId}`, { session });
    expect(getRes.status).toBe(getContract.successStatus);
    const fetched = extractTodo(getRes.data, getContract);
    expect(fetched.item).toBe('Updated E2E Todo');
    expect(fetched.completed).toBe(false);
  });
});

describe('TC-05: Mark Todo Done', () => {
  test('TC-05a: should mark the todo as completed and persist it', async () => {
    const res = await request('post', `/item/${createdTodoId}/done`, {
      session,
      data: {},
    });

    expect(res.status).toBe(doneContract.successStatus);
    const todo = extractTodo(res.data, doneContract);
    assertTodoShape(todo, doneContract.response.requiredTodoFields);
    expect(todo.completed).toBe(true);

    const getRes = await request('get', `/item/${createdTodoId}`, { session });
    expect(getRes.status).toBe(getContract.successStatus);
    const fetched = extractTodo(getRes.data, getContract);
    expect(fetched.completed).toBe(true);
  });
});

describe('TC-06: Delete Todo', () => {
  test('TC-06a: should delete the todo', async () => {
    const res = await request('delete', `/item/${createdTodoId}`, { session });
    expect(res.status).toBe(deleteContract.successStatus);
  });

  test('TC-06b: deleted todo should no longer be retrievable', async () => {
    const res = await request('get', `/item/${createdTodoId}`, { session });
    expect(res.status).toBe(contract.validationRules.postDeleteGetStatus);
    createdTodoId = null;
  });
});
