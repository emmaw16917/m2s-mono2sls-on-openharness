const path = require('path');

function loadTargetConfig(target) {
  if (!target) {
    throw new Error('TARGET env var or CLI arg is required');
  }

  const rawConfig = require(path.resolve(__dirname, `${target}.json`));
  if (rawConfig.configType !== 'target') {
    throw new Error(`Config ${target} is not a runnable target configuration`);
  }

  return applyEnvOverrides(rawConfig);
}

function applyEnvOverrides(baseConfig) {
  return {
    ...baseConfig,
    baseUrl: process.env.BASE_URL || baseConfig.baseUrl,
    pathPrefix: process.env.PATH_PREFIX || baseConfig.pathPrefix,
    region: process.env.AWS_REGION || baseConfig.region,
    tableName: process.env.TABLE_NAME || baseConfig.tableName,
    authConfig: {
      ...(baseConfig.authConfig || {}),
      userPoolId: process.env.USER_POOL_ID || baseConfig.authConfig?.userPoolId,
      clientId: process.env.USER_POOL_CLIENT_ID || baseConfig.authConfig?.clientId,
      region: process.env.AWS_REGION || baseConfig.authConfig?.region,
      defaultPassword: process.env.DEFAULT_PASSWORD || baseConfig.authConfig?.defaultPassword,
    },
  };
}

module.exports = {
  loadTargetConfig,
  applyEnvOverrides,
};
