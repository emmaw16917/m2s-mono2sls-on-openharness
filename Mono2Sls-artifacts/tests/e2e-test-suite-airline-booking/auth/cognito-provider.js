const {
  CognitoIdentityProviderClient,
  AdminCreateUserCommand,
  AdminDeleteUserCommand,
  AdminSetUserPasswordCommand,
  InitiateAuthCommand,
  RespondToAuthChallengeCommand,
} = require('@aws-sdk/client-cognito-identity-provider');

const CognitoSrpHelper = require('cognito-srp-helper');

class CognitoAuthProvider {
  constructor(config = {}, pathPrefix = '') {
    this.userPoolId = config.userPoolId;
    this.clientId = config.clientId;
    this.region = config.region;
    this.userEmailDomain = config.userEmailDomain || 'example.com';
    this.defaultPassword = config.defaultPassword;
    this.adminCredentials = config.adminCredentials || null;
    this.pathPrefix = pathPrefix || '';
    this.client = new CognitoIdentityProviderClient({ region: this.region });
    this.createdUsers = [];
  }

  async setup() {
    this.ensureConfigured();
  }

  async createSession(label = 'user') {
    const user = this.buildUserIdentity(label);

    await this.client.send(new AdminCreateUserCommand({
      UserPoolId: this.userPoolId,
      Username: user.username,
      UserAttributes: [
        { Name: 'email', Value: user.email },
        { Name: 'email_verified', Value: 'true' },
      ],
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
      token,
      sub: payload.sub,
      authHeader: this.getAuthHeader(token),
    };
  }

  async createAdminSession() {
    if (!this.adminCredentials) {
      return null;
    }

    const token = await this.issueToken(
      this.adminCredentials.username,
      this.adminCredentials.password
    );
    const payload = decodeJwtPayload(token);

    return {
      username: this.adminCredentials.username,
      email: this.adminCredentials.username,
      password: this.adminCredentials.password,
      token,
      sub: payload.sub,
      authHeader: this.getAuthHeader(token),
    };
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

  getAuthHeader(token) {
    return `Bearer ${token}`;
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
    try {
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
    } catch (err) {
      const msg = String(err && (err.message || err.toString()));
      if (err && err.name === 'InvalidParameterException' && msg.toLowerCase().includes('flow not enabled')) {
        return this.issueTokenWithUserSrpAuth(username, password);
      }
      throw err;
    }
  }

  async issueTokenWithUserSrpAuth(username, password) {
    const {
      createSrpSession,
      signSrpSession,
      wrapInitiateAuth,
      wrapAuthChallenge,
    } = CognitoSrpHelper;

    // Username/password SRP still needs the raw password to compute SRP hashes.
    const srpSession = createSrpSession(username, password, this.userPoolId, false);

    const initiateAuthRes = await this.client.send(new InitiateAuthCommand(
      wrapInitiateAuth(srpSession, {
        ClientId: this.clientId,
        AuthFlow: 'USER_SRP_AUTH',
        AuthParameters: {
          CHALLENGE_NAME: 'SRP_A',
          USERNAME: username,
        },
      })
    ));

    const signedSrpSession = signSrpSession(srpSession, initiateAuthRes);

    const respondAuthChallengeRes = await this.client.send(new RespondToAuthChallengeCommand(
      wrapAuthChallenge(signedSrpSession, {
        ClientId: this.clientId,
        ChallengeName: 'PASSWORD_VERIFIER',
        Session: initiateAuthRes.Session,
        ChallengeResponses: {
          USERNAME: username,
        },
      })
    ));

    const token = respondAuthChallengeRes.AuthenticationResult && (respondAuthChallengeRes.AuthenticationResult.IdToken || respondAuthChallengeRes.AuthenticationResult.AccessToken);
    if (!token) {
      throw new Error(`SRP auth succeeded but no token was returned: ${JSON.stringify(respondAuthChallengeRes)}`);
    }
    return token;
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
