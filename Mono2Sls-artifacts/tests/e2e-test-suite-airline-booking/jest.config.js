module.exports = {
  testTimeout: 60000,
  testMatch: ['**/tests/**/*.test.js'],
  verbose: true,
  reporters: [
    'default',
    ['./reporters/json-reporter.js', {}],
  ],
};
