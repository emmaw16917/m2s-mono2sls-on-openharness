const {
  CognitoIdentityProviderClient,
  AdminCreateUserCommand,
  AdminDeleteUserCommand,
  AdminSetUserPasswordCommand,
  InitiateAuthCommand,
  RespondToAuthChallengeCommand,
} = require('@aws-sdk/client-cognito-identity-provider');

const CognitoSrpHelper = require('cognito-srp-helper');

const command = process.argv[2];

if (command === 'create-session') {
  createSession(process.argv[3] || 'user')
    .then((session) => {
      process.stdout.write(JSON.stringify(session));
    })
    .catch((err) => {
      console.error(err);
      process.exit(1);
    });
} else if (command === 'delete-users') {
  deleteUsers(process.argv.slice(3))
    .catch((err) => {
      console.error(err);
      process.exit(1);
    });
} else {
  throw new Error('Usage: node cognito-cli.js <create-session|delete-users> [...]');
}

async function createSession(label) {
  const config = getConfig();
  const client = new CognitoIdentityProviderClient({ region: config.region });
  const user = buildUserIdentity(label, config);

  await client.send(new AdminCreateUserCommand({
    UserPoolId: config.userPoolId,
    Username: user.username,
    UserAttributes: [
      { Name: 'email', Value: user.email },
      { Name: 'email_verified', Value: 'true' },
      { Name: 'name', Value: user.displayName },
    ],
    MessageAction: 'SUPPRESS',
    TemporaryPassword: 'Temp1234!',
  }));

  await client.send(new AdminSetUserPasswordCommand({
    UserPoolId: config.userPoolId,
    Username: user.username,
    Password: user.password,
    Permanent: true,
  }));

  const token = await issueTokenWithSupportedAuthFlows(client, config, user);

  const payload = decodeJwtPayload(token);
  return {
    username: user.username,
    email: user.email,
    password: user.password,
    token,
    sub: payload.sub,
    authHeader: `Bearer ${token}`,
  };
}

async function issueTokenWithSupportedAuthFlows(client, config, user) {
  try {
    const resp = await client.send(new InitiateAuthCommand({
      AuthFlow: 'USER_PASSWORD_AUTH',
      ClientId: config.clientId,
      AuthParameters: {
        USERNAME: user.username,
        PASSWORD: user.password,
      },
    }));

    const token =
      resp.AuthenticationResult && (resp.AuthenticationResult.IdToken || resp.AuthenticationResult.AccessToken);
    if (!token) {
      throw new Error('Cognito auth succeeded but no token was returned');
    }
    return token;
  } catch (err) {
    const msg = String(err && (err.message || err.toString()));
    if (err && err.name === 'InvalidParameterException' && msg.toLowerCase().includes('flow not enabled')) {
      return issueTokenWithUserSrpAuth(client, config, user);
    }
    throw err;
  }
}

async function issueTokenWithUserSrpAuth(client, config, user) {
  const {
    createSrpSession,
    signSrpSession,
    wrapInitiateAuth,
    wrapAuthChallenge,
  } = CognitoSrpHelper;

  const srpSession = createSrpSession(user.username, user.password, config.userPoolId, false);

  const initiateAuthRes = await client.send(new InitiateAuthCommand(
    wrapInitiateAuth(srpSession, {
      ClientId: config.clientId,
      AuthFlow: 'USER_SRP_AUTH',
      AuthParameters: {
        CHALLENGE_NAME: 'SRP_A',
        USERNAME: user.username,
      },
    })
  ));

  const signedSrpSession = signSrpSession(srpSession, initiateAuthRes);

  const respondAuthChallengeRes = await client.send(new RespondToAuthChallengeCommand(
    wrapAuthChallenge(signedSrpSession, {
      ClientId: config.clientId,
      ChallengeName: 'PASSWORD_VERIFIER',
      Session: initiateAuthRes.Session,
      ChallengeResponses: {
        USERNAME: user.username,
      },
    })
  ));

  const token =
    respondAuthChallengeRes.AuthenticationResult && (respondAuthChallengeRes.AuthenticationResult.IdToken || respondAuthChallengeRes.AuthenticationResult.AccessToken);
  if (!token) {
    throw new Error(`SRP auth succeeded but no token was returned: ${JSON.stringify(respondAuthChallengeRes)}`);
  }
  return token;
}

async function deleteUsers(usernames) {
  if (!usernames || usernames.length === 0) {
    return;
  }

  const config = getConfig();
  const client = new CognitoIdentityProviderClient({ region: config.region });

  for (const username of usernames) {
    try {
      await client.send(new AdminDeleteUserCommand({
        UserPoolId: config.userPoolId,
        Username: username,
      }));
    } catch (_) {
      // Best-effort cleanup only.
    }
  }
}

function getConfig() {
  const config = {
    userPoolId: process.env.USER_POOL_ID,
    clientId: process.env.USER_POOL_CLIENT_ID,
    region: process.env.AWS_REGION,
    userEmailDomain: process.env.USER_EMAIL_DOMAIN || 'example.com',
    defaultPassword: process.env.DEFAULT_PASSWORD,
  };

  for (const [key, value] of Object.entries(config)) {
    if (!value) {
      throw new Error(`Missing required Cognito CLI env: ${key}`);
    }
  }

  return config;
}

function buildUserIdentity(label, config) {
  const safeLabel = String(label).replace(/[^a-z0-9-]/gi, '-').toLowerCase();
  const suffix = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  const localPart = `e2e-${safeLabel}-${suffix}`;
  const email = `${localPart}@${config.userEmailDomain}`;

  return {
    username: email,
    email,
    displayName: `E2E ${safeLabel}`,
    password: config.defaultPassword,
  };
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
