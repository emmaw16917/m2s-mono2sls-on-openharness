const { execSync } = require('child_process');
const fs = require('fs');
const path = require('path');

const configDir = path.join(__dirname, 'config');
const allConfigs = fs.readdirSync(configDir)
  .filter((file) => file.endsWith('.json'))
  .map((file) => ({
    file,
    body: JSON.parse(fs.readFileSync(path.join(configDir, file), 'utf-8')),
  }))
  .filter(({ body }) => body.configType === 'target')
  .map(({ file }) => file.replace('.json', ''));

const targets = process.argv.length > 2 ? process.argv.slice(2) : allConfigs;

console.log('='.repeat(60));
console.log('  Bookstore E2E Runner');
console.log('='.repeat(60));
console.log(`Targets: ${targets.join(', ')}\n`);

const results = {};

for (const target of targets) {
  console.log(`\n${'-'.repeat(50)}`);
  console.log(`Running tests for: ${target}`);
  console.log(`${'-'.repeat(50)}\n`);

  try {
    execSync('npx jest --runInBand --forceExit', {
      cwd: __dirname,
      env: { ...process.env, TARGET: target },
      stdio: 'inherit',
      timeout: 10 * 60 * 1000,
    });
    results[target] = 'PASS';
  } catch (_) {
    results[target] = 'FAIL (some tests failed)';
  }
}

console.log('\n' + '='.repeat(60));
console.log('  Summary');
console.log('='.repeat(60));
for (const [target, status] of Object.entries(results)) {
  console.log(`  ${target}: ${status}`);
}
console.log('\nDetailed results in: ./results/');
