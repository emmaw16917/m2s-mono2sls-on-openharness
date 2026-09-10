const axios = require('axios');

class CustomJwtAuthProvider {
  constructor(config, baseUrl) {
    this.registerEndpoint = config.registerEndpoint;
    this.loginEndpoint = config.loginEndpoint;
    this.userEmailDomain = config.userEmailDomain || 'example.com';
    this.defaultPassword = config.defaultPassword;
    this.baseUrl = baseUrl;
  }

  async setup() {
    this.ensureConfigured();
  }

  async createSession(label = 'user') {
    const user = this.buildUserIdentity(label);

    const registerResp = await axios.post(`${this.baseUrl}${this.registerEndpoint}`, {
      username: user.username,
      password: user.password,
      email: user.email,
    }, {
      validateStatus: () => true,
    });

    if (![200, 201, 409].includes(registerResp.status)) {
      throw new Error(`Register failed with status ${registerResp.status}: ${JSON.stringify(registerResp.data)}`);
    }

    const resp = await axios.post(`${this.baseUrl}${this.loginEndpoint}`, {
      username: user.username,
      password: user.password,
    });

    const token = resp.data.token || resp.data.accessToken || resp.data.idToken;
    if (!token) {
      throw new Error(`Login succeeded but no token found in response: ${JSON.stringify(resp.data)}`);
    }

    return {
      ...user,
      token,
      authHeader: this.getAuthHeader(token),
    };
  }

  async teardown() {
    // No user deletion endpoint exists in the baseline output.
  }

  getAuthHeader(token) {
    return `Bearer ${token}`;
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
      username: `e2e-${safeLabel}-${suffix}`,
      email: `e2e-${safeLabel}-${suffix}@${this.userEmailDomain}`,
      password: this.defaultPassword,
    };
  }
}

module.exports = CustomJwtAuthProvider;
