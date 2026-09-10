const { authProvider, getCapability } = require('./setup');
const {
  request,
  expectStatus,
} = require('./helpers');

const describeIf = getCapability('supportsAuthCompatibility') ? describe : describe.skip;

describeIf('Auth Compatibility', () => {
  afterAll(async () => {
    await authProvider.teardown();
  });

  test('AUTH-01: should register a user through /register', async () => {
    const username = `compat-${Date.now()}`;
    const res = await request('post', '/register', {
      headers: { 'Content-Type': 'application/json' },
      data: {
        username,
        password: 'TestPass123!',
      },
    });

    expectStatus(res, [200, 201]);
    expect(res.data.token || res.data.access_token).toBeDefined();
    expect(res.data.user?.username).toBe(username);
  });

  test('AUTH-02: should login a registered user through /login', async () => {
    const username = `login-${Date.now()}`;
    await request('post', '/register', {
      headers: { 'Content-Type': 'application/json' },
      data: {
        username,
        password: 'TestPass123!',
      },
    });

    const res = await request('post', '/login', {
      headers: { 'Content-Type': 'application/json' },
      data: {
        username,
        password: 'TestPass123!',
      },
    });

    expectStatus(res, 200);
    expect(
      res.data.token
      || res.data.access_token
      || res.data.accessToken
      || res.data.idToken
    ).toBeDefined();
  });

  test('AUTH-03: should return the current user through /me', async () => {
    await authProvider.setup();
    const session = await authProvider.createSession('auth-me');
    const res = await request('get', '/me', { session });

    expectStatus(res, 200);
    expect(res.data.user?.username || res.data.user?.userId).toBeDefined();
  });
});
