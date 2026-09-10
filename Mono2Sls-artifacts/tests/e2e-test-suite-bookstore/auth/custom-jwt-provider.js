const axios = require('axios');

class CustomJwtAuthProvider {
  constructor(config = {}, baseUrl, pathPrefix = '') {
    this.registerEndpoint = config.registerEndpoint;
    this.loginEndpoint = config.loginEndpoint;
    this.userEmailDomain = config.userEmailDomain || 'example.com';
    this.defaultPassword = config.defaultPassword || process.env.DEFAULT_PASSWORD;
    this.baseUrl = baseUrl;
    this.pathPrefix = pathPrefix || '';
    this.registerEmailField = config.registerEmailField || 'email';
    this.registerPasswordField = config.registerPasswordField || 'password';
    this.registerUserIdField = config.registerUserIdField || 'userId';
    this.loginEmailField = config.loginEmailField || 'email';
    this.loginPasswordField = config.loginPasswordField || 'password';
    this.tokenFields = config.tokenFields || ['access_token', 'token', 'accessToken', 'idToken'];
  }

  async setup() {
    this.ensureConfigured();
  }

  async createSession(label = 'user') {
    const user = this.buildUserIdentity(label);

    const registerBody = {
      [this.registerEmailField]: user.email,
      [this.registerPasswordField]: user.password,
      [this.registerUserIdField]: user.userId,
    };

    const registerResp = await axios.post(this.toUrl(this.registerEndpoint), registerBody, {
      validateStatus: () => true,
    });

    if (![200, 201, 409].includes(registerResp.status)) {
      throw new Error(`Register failed with status ${registerResp.status}: ${JSON.stringify(registerResp.data)}`);
    }

    const loginResp = await axios.post(this.toUrl(this.loginEndpoint), {
      [this.loginEmailField]: user.email,
      [this.loginPasswordField]: user.password,
    }, {
      validateStatus: () => true,
    });

    if (loginResp.status < 200 || loginResp.status >= 300) {
      throw new Error(`Login failed with status ${loginResp.status}: ${JSON.stringify(loginResp.data)}`);
    }

    const token = this.extractToken(loginResp.data);
    const payload = decodeJwtPayload(token);

    return {
      email: user.email,
      password: user.password,
      userId: user.userId,
      token,
      sub: payload.sub || payload.userId || user.userId,
      authHeader: `Bearer ${token}`,
    };
  }

  async teardown() {
    // No deletion endpoint is assumed.
  }

  ensureConfigured() {
    const required = {
      registerEndpoint: this.registerEndpoint,
      loginEndpoint: this.loginEndpoint,
      defaultPassword: this.defaultPassword,
      baseUrl: this.baseUrl,
    };

    for (const [key, value] of Object.entries(required)) {
      if (!value || String(value).includes('<')) {
        throw new Error(`Custom JWT auth config is incomplete: ${key}`);
      }
    }
  }

  buildUserIdentity(label) {
    const safeLabel = String(label).replace(/[^a-z0-9-]/gi, '-').toLowerCase();
    const suffix = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    return {
      email: `e2e-${safeLabel}-${suffix}@${this.userEmailDomain}`,
      password: this.defaultPassword,
      userId: `e2e-${safeLabel}-${suffix}`,
    };
  }

  toUrl(endpoint) {
    const base = String(this.baseUrl || '').replace(/\/$/, '');
    const prefix = this.pathPrefix ? `/${String(this.pathPrefix).replace(/^\/|\/$/g, '')}` : '';
    const normalizedEndpoint = endpoint.startsWith('/') ? endpoint : `/${endpoint}`;
    return `${base}${prefix}${normalizedEndpoint}`;
  }

  extractToken(responseData) {
    const data = parseMaybeJson(responseData);
    for (const field of this.tokenFields) {
      if (data && data[field]) {
        return data[field];
      }
    }
    throw new Error(`Login succeeded but no token found in response: ${JSON.stringify(data)}`);
  }
}

function parseMaybeJson(value) {
  if (typeof value === 'string') {
    return JSON.parse(value);
  }
  return value;
}

function decodeJwtPayload(token) {
  const parts = String(token).split('.');
  if (parts.length < 2) {
    return {};
  }
  try {
    const padded = parts[1].replace(/-/g, '+').replace(/_/g, '/');
    return JSON.parse(Buffer.from(padded, 'base64').toString('utf-8'));
  } catch (_) {
    return {};
  }
}

module.exports = CustomJwtAuthProvider;
