const { authProvider, getCapability } = require('./setup');
const {
  request,
  expectStatus,
  createChargeId,
} = require('./helpers');

const describeIf = getCapability('supportsAuthCompatibility') ? describe : describe.skip;

describeIf('Auth Compatibility', () => {
  test('AUTH-01a: should register a user through the generated auth endpoint', async () => {
    const email = `compat-${Date.now()}@example.com`;
    const res = await request('post', '/auth/register', {
      headers: { 'Content-Type': 'application/json' },
      data: {
        email,
        password: 'TestPass123!',
        userId: `compat-${Date.now()}`,
      },
    });

    expectStatus(res, [200, 201]);
    expect(res.data.sub || res.data.user?.sub).toBeDefined();
  });

  test('AUTH-02a: should login through the generated auth endpoint', async () => {
    const email = `login-${Date.now()}@example.com`;
    await request('post', '/auth/register', {
      headers: { 'Content-Type': 'application/json' },
      data: {
        email,
        password: 'TestPass123!',
        userId: `login-${Date.now()}`,
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
    let session;
    try {
      await authProvider.setup();
      session = await authProvider.createSession('auth-compat');

      const res = await request('get', '/auth/me', { session });
      expectStatus(res, 200);
      expect(res.data.sub).toBeDefined();
      expect(res.data.email).toBeDefined();
    } finally {
      await authProvider.teardown();
    }
  });

  test('AUTH-04a: should reject unauthenticated /auth/me requests', async () => {
    const res = await request('get', '/auth/me');
    expectStatus(res, [401, 403]);
  });

  test('AUTH-05a: should reject malformed bearer tokens on /auth/me', async () => {
    const res = await request('get', '/auth/me', {
      headers: { Authorization: `Bearer ${createChargeId('invalid-token')}` },
    });
    expectStatus(res, [401, 403]);
  });

  test('AUTH-06a: should reject /auth/users for a non-admin test account', async () => {
    let session;
    try {
      await authProvider.setup();
      session = await authProvider.createSession('auth-users');

      const res = await request('get', '/auth/users', { session });
      expectStatus(res, [401, 403]);
    } finally {
      await authProvider.teardown();
    }
  });
});
