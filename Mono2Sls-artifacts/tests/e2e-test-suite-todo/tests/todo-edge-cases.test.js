const { authProvider, contract } = require('./setup');
const {
  createTodo,
  deleteTodoIfExists,
  extractList,
  extractTodo,
  request,
} = require('./helpers');

let session;
const createdIds = [];

beforeAll(async () => {
  await authProvider.setup();
  session = await authProvider.createSession('robustness');
});

afterAll(async () => {
  for (const id of createdIds) {
    await deleteTodoIfExists(session, id);
  }
  await authProvider.teardown();
});

describe('ROB-10: Response Format', () => {
  test('ROB-10a: responses should be valid JSON', async () => {
    const res = await request('get', '/item', { session });
    expect(res.status).toBe(200);
    expect(res.headers['content-type']).toMatch(/application\/json/);
  });
});

describe('ROB-11: Multiple Item Operations', () => {
  test('ROB-11a: should handle creating and listing multiple todos', async () => {
    const items = ['Todo A', 'Todo B', 'Todo C'];

    for (const text of items) {
      const todo = await createTodo(session, { item: text });
      createdIds.push(todo.id);
    }

    const listRes = await request('get', '/item', { session });
    expect(listRes.status).toBe(200);

    const list = extractList(listRes.data, contract.businessEndpoints.find((ep) => ep.id === 'TC-LIST'));
    const observed = new Set(list.map((item) => item.item));
    for (const text of items) {
      expect(observed.has(text)).toBe(true);
    }
  });
});

describe('ROB-12: Concurrent Requests', () => {
  test('ROB-12a: should handle parallel create requests', async () => {
    const promises = Array.from({ length: 5 }, (_, index) =>
      request('post', '/item', {
        session,
        data: {
          item: `Concurrent Todo ${index}`,
          completed: false,
        },
      })
    );

    const results = await Promise.all(promises);
    for (const res of results) {
      expect(res.status).toBe(200);
      const todo = extractTodo(res.data, contract.businessEndpoints.find((ep) => ep.id === 'TC-CREATE'));
      createdIds.push(todo.id);
    }
  });
});
