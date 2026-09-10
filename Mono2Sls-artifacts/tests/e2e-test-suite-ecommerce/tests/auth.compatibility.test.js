const { getCapability, request, expectStatus, createRoleSession, authProvider } = require('./helpers');

const describeIf = getCapability('supportsAuthCompatibility') ? describe : describe.skip;

describeIf('Auth Compatibility', () => {
  afterAll(async () => {
    await authProvider.teardown();
  });

  test('AUTH-01a: should register a user through the generated auth endpoint', async () => {
    const email = `compat-${Date.now()}@example.com`;
    const res = await request('post', '/auth/register', {
      headers: { 'Content-Type': 'application/json' },
      data: {
        email,
        password: 'TestPass123!',
        role: 'user',
      },
    });

    expectStatus(res, [200, 201]);
    expect(res.data.userId || res.data.sub).toBeDefined();
  });

  test('AUTH-02a: should login through the generated auth endpoint', async () => {
    const email = `login-${Date.now()}@example.com`;
    await request('post', '/auth/register', {
      headers: { 'Content-Type': 'application/json' },
      data: {
        email,
        password: 'TestPass123!',
        role: 'user',
      },
    });

    const res = await request('post', '/auth/login', {
      headers: { 'Content-Type': 'application/json' },
      data: {
        email,
        password: 'TestPass123!',
      },
    });

    expectStatus(res, 200);
    expect(
      res.data.access_token
      || res.data.token
      || res.data.accessToken
      || res.data.idToken
    ).toBeDefined();
  });

  test('AUTH-03a: should return the current user through /auth/me', async () => {
    const session = await createRoleSession('user', 'auth-compat');
    const res = await request('get', '/auth/me', { session });

    expectStatus(res, 200);
    const user = res.data.user || res.data;
    expect(user.userId || user.sub).toBeDefined();
    expect(user.email).toBeDefined();
  });
});
