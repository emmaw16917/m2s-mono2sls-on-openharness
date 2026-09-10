const { contract, authProvider } = require('./setup');
const {
  createTodo,
  deleteTodoIfExists,
  extractList,
  request,
} = require('./helpers');

let userA;
let userB;
let userATodoId;

beforeAll(async () => {
  await authProvider.setup();
  userA = await authProvider.createSession('isolation-a');
  userB = await authProvider.createSession('isolation-b');
});

afterAll(async () => {
  await deleteTodoIfExists(userA, userATodoId);
  await authProvider.teardown();
});

describe('TC-07: Authentication Enforcement', () => {
  test('TC-07: all protected endpoints should require authentication', async () => {
    const protectedEndpoints = [
      { method: 'get', path: '/item' },
      { method: 'get', path: '/item/fake-id' },
      { method: 'post', path: '/item', data: { item: 'blocked' } },
      { method: 'put', path: '/item/fake-id', data: { item: 'blocked', completed: false } },
      { method: 'post', path: '/item/fake-id/done', data: {} },
      { method: 'delete', path: '/item/fake-id' },
    ];

    for (const { method, path, data } of protectedEndpoints) {
      const res = await request(method, path, { data });
      expect(contract.validationRules.unauthenticatedStatus).toContain(res.status);
    }
  });
});

describe('TC-08: Invalid Token Handling', () => {
  test('TC-08a: should reject request with malformed token', async () => {
    const res = await request('get', '/item', {
      headers: { Authorization: 'Bearer invalid.token.here' },
    });

    expect(contract.validationRules.unauthenticatedStatus).toContain(res.status);
  });
});

describe('TC-09: User Data Isolation', () => {
  test('TC-09a: user B should not see items created by user A', async () => {
    const created = await createTodo(userA, { item: 'User A private todo' });
    userATodoId = created.id;

    const listRes = await request('get', '/item', { session: userB });
    expect(listRes.status).toBe(200);
    const items = extractList(listRes.data, contract.businessEndpoints.find((ep) => ep.id === 'TC-LIST'));
    expect(items.some((item) => item.id === userATodoId)).toBe(false);
  });

  test('TC-09b: user B should not retrieve user A item by ID', async () => {
    const res = await request('get', `/item/${userATodoId}`, { session: userB });
    expect(res.status).toBe(404);
  });
});
