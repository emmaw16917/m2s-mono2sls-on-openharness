const { execSync } = require('child_process');

const target = process.argv[2];
if (!target) {
  throw new Error('Usage: node run-target.js <target>');
}

execSync('npx jest --runInBand --forceExit', {
  cwd: __dirname,
  env: { ...process.env, TARGET: target },
  stdio: 'inherit',
  timeout: 120000,
});
