const path = require('path');
const { execFileSync } = require('child_process');

class CognitoAuthProvider {
  constructor(config = {}) {
    this.userPoolId = config.userPoolId || process.env.USER_POOL_ID;
    this.clientId = config.clientId || process.env.USER_POOL_CLIENT_ID;
    this.region = config.region || process.env.AWS_REGION;
    this.userEmailDomain = config.userEmailDomain || 'example.com';
    this.defaultPassword = config.defaultPassword || process.env.DEFAULT_PASSWORD;
    this.createdUsers = [];
  }

  async setup() {
    this.ensureConfigured();
  }

  async createSession(label = 'user') {
    const raw = execFileSync('node', [
      path.join(__dirname, 'cognito-cli.js'),
      'create-session',
      label,
    ], {
      env: this.buildEnv(),
      encoding: 'utf-8',
      stdio: ['ignore', 'pipe', 'inherit'],
    });

    const session = JSON.parse(raw);
    if (session && session.username) {
      this.createdUsers.push(session.username);
    }
    return session;
  }

  async teardown() {
    if (this.createdUsers.length === 0) {
      return;
    }

    const usernames = this.createdUsers.splice(0);
    try {
      execFileSync('node', [
        path.join(__dirname, 'cognito-cli.js'),
        'delete-users',
        ...usernames,
      ], {
        env: this.buildEnv(),
        encoding: 'utf-8',
        stdio: ['ignore', 'pipe', 'inherit'],
      });
    } catch (_) {
      // Best-effort cleanup only.
    }
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

  buildEnv() {
    return {
      ...process.env,
      USER_POOL_ID: this.userPoolId,
      USER_POOL_CLIENT_ID: this.clientId,
      AWS_REGION: this.region,
      USER_EMAIL_DOMAIN: this.userEmailDomain,
      DEFAULT_PASSWORD: this.defaultPassword,
    };
  }
}

module.exports = CognitoAuthProvider;
