/**
 * Run E2E tests against all configured targets and produce a combined report.
 *
 * Usage:  node run-all.js
 *   or:   node run-all.js m2s_dsv3 cursor_sonnet45tk
 */

const { execSync } = require('child_process');
const fs = require('fs');
const path = require('path');

const configDir = path.join(__dirname, 'config');
const allConfigs = fs.readdirSync(configDir)
  .filter((f) => f.endsWith('.json'))
  .map((f) => ({
    file: f,
    body: JSON.parse(fs.readFileSync(path.join(configDir, f), 'utf-8')),
  }))
  .filter(({ body }) => body.configType === 'target')
  .map(({ file }) => file.replace('.json', ''));

const targets = process.argv.length > 2 ? process.argv.slice(2) : allConfigs;

console.log('='.repeat(60));
console.log('  M2S Universal E2E Test Runner');
console.log('='.repeat(60));
console.log(`Targets: ${targets.join(', ')}\n`);

const results = {};

for (const target of targets) {
  console.log(`\n${'─'.repeat(50)}`);
  console.log(`  Running tests for: ${target}`);
  console.log(`${'─'.repeat(50)}\n`);

  try {
    execSync(`npx jest --runInBand --forceExit`, {
      cwd: __dirname,
      env: { ...process.env, TARGET: target },
      stdio: 'inherit',
      timeout: 120000,
    });
    results[target] = 'PASS';
  } catch (err) {
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
