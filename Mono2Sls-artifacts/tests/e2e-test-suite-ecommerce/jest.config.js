module.exports = {
  testTimeout: 90000,
  testMatch: ['**/tests/**/*.test.js'],
  verbose: true,
  reporters: [
    'default',
    ['./reporters/json-reporter.js', {}],
  ],
};
