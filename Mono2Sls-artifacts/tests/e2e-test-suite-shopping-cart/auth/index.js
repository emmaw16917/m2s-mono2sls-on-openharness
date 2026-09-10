const CognitoAuthProvider = require('./cognito-provider');
const CustomJwtAuthProvider = require('./custom-jwt-provider');

function createAuthProvider(config) {
  switch (config.authType) {
    case 'cognito':
      return new CognitoAuthProvider(config.authConfig);
    case 'custom_jwt':
      return new CustomJwtAuthProvider(config.authConfig, config.baseUrl, config.pathPrefix);
    default:
      throw new Error(`Unknown auth type: ${config.authType}`);
  }
}

module.exports = { createAuthProvider };
