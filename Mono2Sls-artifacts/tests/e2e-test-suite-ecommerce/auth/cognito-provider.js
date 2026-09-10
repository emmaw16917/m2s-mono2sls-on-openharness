const {
  CognitoIdentityProviderClient,
  AdminCreateUserCommand,
  AdminSetUserPasswordCommand,
  AdminDeleteUserCommand,
  InitiateAuthCommand,
} = require('@aws-sdk/client-cognito-identity-provider');

class CognitoAuthProvider {
  constructor(config = {}) {
    this.userPoolId = config.userPoolId || process.env.USER_POOL_ID;
    this.clientId = config.clientId || process.env.USER_POOL_CLIENT_ID;
    this.region = config.region || process.env.AWS_REGION;
    this.userEmailDomain = config.userEmailDomain || 'example.com';
    this.defaultPassword = config.defaultPassword || process.env.DEFAULT_PASSWORD;
    this.roleAttributeName = config.roleAttributeName || null;
    this.supportsRoleBootstrap = Boolean(config.supportsRoleBootstrap);
    this.client = new CognitoIdentityProviderClient({ region: this.region });
    this.createdUsers = [];
  }

  async setup() {
    this.ensureConfigured();
  }

  async createSession(label = 'user') {
    return this.createUser(label, null);
  }

  async createRoleSession(role, label = role || 'user') {
    if (!this.supportsRoleBootstrap || !this.roleAttributeName || !role) {
      return this.createSession(label);
    }
    return this.createUser(label, role);
  }

  async createAdminSession(label = 'admin') {
    return this.createRoleSession('admin', label);
  }

  async teardown() {
    for (const username of this.createdUsers.splice(0)) {
      try {
        await this.client.send(new AdminDeleteUserCommand({
          UserPoolId: this.userPoolId,
          Username: username,
        }));
      } catch (_) {
        // Best-effort cleanup only.
      }
    }
  }

  async createUser(label, role) {
    const user = this.buildUserIdentity(label);
    const attributes = [
      { Name: 'email', Value: user.email },
      { Name: 'email_verified', Value: 'true' },
    ];

    if (role && this.roleAttributeName) {
      attributes.push({ Name: this.roleAttributeName, Value: role });
    }

    await this.client.send(new AdminCreateUserCommand({
      UserPoolId: this.userPoolId,
      Username: user.username,
      UserAttributes: attributes,
      MessageAction: 'SUPPRESS',
      TemporaryPassword: 'Temp1234!',
    }));

    await this.client.send(new AdminSetUserPasswordCommand({
      UserPoolId: this.userPoolId,
      Username: user.username,
      Password: user.password,
      Permanent: true,
    }));

    const token = await this.issueToken(user.username, user.password);
    const payload = decodeJwtPayload(token);
    this.createdUsers.push(user.username);

    return {
      username: user.username,
      email: user.email,
      password: user.password,
      role: role || 'user',
      token,
      sub: payload.sub,
      authHeader: `Bearer ${token}`,
    };
  }

  buildUserIdentity(label) {
    const safeLabel = String(label).replace(/[^a-z0-9-]/gi, '-').toLowerCase();
    const suffix = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    const localPart = `e2e-${safeLabel}-${suffix}`;
    const email = `${localPart}@${this.userEmailDomain}`;

    return {
      username: email,
      email,
      password: this.defaultPassword,
    };
  }

  async issueToken(username, password) {
    const resp = await this.client.send(new InitiateAuthCommand({
      AuthFlow: 'USER_PASSWORD_AUTH',
      ClientId: this.clientId,
      AuthParameters: {
        USERNAME: username,
        PASSWORD: password,
      },
    }));

    const token = resp.AuthenticationResult && (resp.AuthenticationResult.IdToken || resp.AuthenticationResult.AccessToken);
    if (!token) {
      throw new Error('Cognito auth succeeded but no token was returned');
    }
    return token;
  }

  ensureConfigured() {
    const required = {
      userPoolId: this.userPoolId,
      clientId: this.clientId,
      region: this.region,
      defaultPassword: this.defaultPassword,
    };

    for (const [key, value] of Object.entries(required)) {
      if (!value || String(value).includes('<')) {
        throw new Error(`Cognito auth config is incomplete: ${key}`);
      }
    }
  }
}

function decodeJwtPayload(token) {
  const parts = String(token).split('.');
  if (parts.length < 2) {
    return {};
  }

  const padded = parts[1].replace(/-/g, '+').replace(/_/g, '/');
  const decoded = Buffer.from(padded, 'base64').toString('utf-8');
  return JSON.parse(decoded);
}

module.exports = CognitoAuthProvider;
