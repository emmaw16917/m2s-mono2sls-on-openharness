const fs = require('fs');
const path = require('path');

const resultsDir = path.join(__dirname, 'results');
const configDir = path.join(__dirname, 'config');
const contract = require(path.join(configDir, 'normalized_business_contract.json'));

function getLatestResult(target) {
  const files = fs.readdirSync(resultsDir)
    .filter(f => f.startsWith(target) && f.endsWith('.json'))
    .sort()
    .reverse();
  if (files.length === 0) return null;
  return JSON.parse(fs.readFileSync(path.join(resultsDir, files[0]), 'utf-8'));
}

function computeEndpointMetrics(configFile) {
  const config = require(path.join(configDir, configFile));
  const generated = extractEndpointsFromArtifact(config);
  const expectedBusiness = contract.businessEndpoints;
  const antiPatternEndpoints = contract.antiPatternEndpoints;

  const correctBusiness = generated.filter((endpoint) =>
    expectedBusiness.some((gt) => gt.method === endpoint.method && endpoint.path === gt.semanticPath)
  );

  const incorrectAuth = generated.filter((endpoint) =>
    antiPatternEndpoints.some((gt) => gt.method === endpoint.method && endpoint.path === gt.semanticPath)
  );

  const extraEndpoints = generated.filter((endpoint) =>
    !expectedBusiness.some((gt) => gt.method === endpoint.method && endpoint.path === gt.semanticPath) &&
    !antiPatternEndpoints.some((gt) => gt.method === endpoint.method && endpoint.path === gt.semanticPath)
  );

  const totalGenerated = generated.length;
  const correctCount = correctBusiness.length;
  const totalExpected = expectedBusiness.length;

  const EP = totalGenerated > 0 ? correctCount / totalGenerated : 0;
  const ER = totalExpected > 0 ? correctCount / totalExpected : 0;
  const F1 = (EP + ER) > 0 ? 2 * EP * ER / (EP + ER) : 0;

  return {
    name: config.name,
    shortName: config.shortName,
    authType: config.authType,
    totalGenerated,
    correctBusiness: correctCount,
    incorrectAuth: incorrectAuth.length,
    extraEndpoints: extraEndpoints.length,
    totalExpected,
    EP: EP.toFixed(4),
    ER: ER.toFixed(4),
    F1: F1.toFixed(4),
    generatedEndpoints: generated,
  };
}

function extractEndpointsFromArtifact(config) {
  const templatePath = path.resolve(__dirname, config.artifactTemplate);
  const contents = fs.readFileSync(templatePath, 'utf-8');
  const lines = contents.split(/\r?\n/);
  const endpoints = [];
  let pendingPath = null;

  for (const line of lines) {
    const pathMatch = line.match(/^\s*Path:\s*(.+)\s*$/);
    if (pathMatch) {
      pendingPath = pathMatch[1].trim().replace(/^['"]|['"]$/g, '');
      continue;
    }

    const methodMatch = line.match(/^\s*Method:\s*([A-Za-z]+)\s*$/);
    if (methodMatch && pendingPath) {
      endpoints.push({
        method: methodMatch[1].toUpperCase(),
        path: normalizeArtifactPath(pendingPath, config.pathPrefix),
      });
      pendingPath = null;
    }
  }

  return dedupeEndpoints(endpoints);
}

function normalizeArtifactPath(pathValue, pathPrefix) {
  const normalizedPrefix = pathPrefix && pathPrefix !== '/' ? pathPrefix : '';
  if (normalizedPrefix && pathValue.startsWith(normalizedPrefix)) {
    return pathValue.slice(normalizedPrefix.length) || '/';
  }
  return pathValue;
}

function dedupeEndpoints(endpoints) {
  const seen = new Set();
  return endpoints.filter((endpoint) => {
    const key = `${endpoint.method} ${endpoint.path}`;
    if (seen.has(key)) {
      return false;
    }
    seen.add(key);
    return true;
  });
}

// ─── Main ─────────────────────────────────────────────────────────────────────

console.log('='.repeat(70));
console.log('  M2S Evaluation Report');
console.log('  ' + new Date().toISOString());
console.log('='.repeat(70));

const configs = fs.readdirSync(configDir)
  .filter((f) => f.endsWith('.json'))
  .filter((f) => require(path.join(configDir, f)).configType === 'target');

const report = { timestamp: new Date().toISOString(), targets: [] };

for (const configFile of configs) {
  const config = require(path.join(configDir, configFile));
  const metrics = computeEndpointMetrics(configFile);
  const testResult = getLatestResult(config.shortName);

  const entry = {
    ...metrics,
    e2e: testResult ? {
      total: testResult.totalTests,
      passed: testResult.passed,
      failed: testResult.failed,
      FCR: testResult.passRate + '%',
      coreFCR: testResult.core.passRate + '%',
      robustnessPassRate: testResult.robustness.passRate + '%',
      duration: testResult.duration + 'ms',
    } : { status: 'NOT RUN' },
  };

  report.targets.push(entry);

  console.log(`\n${'─'.repeat(50)}`);
  console.log(`  ${config.name} (${config.shortName})`);
  console.log(`${'─'.repeat(50)}`);
  console.log(`  Auth type:          ${config.authType}`);
  console.log(`  Endpoint Precision: ${metrics.EP}  (${metrics.correctBusiness}/${metrics.totalGenerated})`);
  console.log(`  Endpoint Recall:    ${metrics.ER}  (${metrics.correctBusiness}/${metrics.totalExpected})`);
  console.log(`  F1-Score:           ${metrics.F1}`);
  console.log(`  Auth endpoints:     ${metrics.incorrectAuth} (should be 0 for best practice)`);
  console.log(`  Extra endpoints:    ${metrics.extraEndpoints}`);
  if (testResult) {
    console.log(`  Core FCR:           ${testResult.core.passRate}%  (${testResult.core.passed}/${testResult.core.total})`);
    console.log(`  Robustness Rate:    ${testResult.robustness.passRate}%  (${testResult.robustness.passed}/${testResult.robustness.total})`);
    console.log(`  Overall Pass Rate:  ${testResult.passRate}%  (${testResult.passed}/${testResult.totalTests})`);
  } else {
    console.log(`  Core FCR:           NOT RUN`);
  }
}

// Comparison table
console.log('\n' + '='.repeat(70));
console.log('  Comparison Table (for paper)');
console.log('='.repeat(70));
console.log('');
console.log('| Method | Auth | EP | ER | F1 | Core FCR | Robustness | Auth EPs | Extra EPs |');
console.log('|--------|------|----|----|----|----------|------------|----------|-----------|');
for (const t of report.targets) {
  const coreFcr = t.e2e.coreFCR || 'N/A';
  const robustness = t.e2e.robustnessPassRate || 'N/A';
  const authLabel = t.authType === 'cognito' ? 'Cognito' : (t.authType === 'custom_jwt' ? 'Custom JWT' : t.authType);
  console.log(`| ${t.shortName} | ${authLabel} | ${t.EP} | ${t.ER} | ${t.F1} | ${coreFcr} | ${robustness} | ${t.incorrectAuth} | ${t.extraEndpoints} |`);
}

// Save to file
const outFile = path.join(resultsDir, `report_${new Date().toISOString().replace(/[:.]/g, '-')}.json`);
fs.writeFileSync(outFile, JSON.stringify(report, null, 2));
console.log(`\nFull report saved to: ${outFile}`);
