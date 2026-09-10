module.exports = {
  testTimeout: 30000,
  testMatch: ['**/tests/**/*.test.js'],
  verbose: true,
  reporters: [
    'default',
    ['./reporters/json-reporter.js', {}]
  ],
};
