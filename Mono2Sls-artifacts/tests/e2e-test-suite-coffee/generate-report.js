const fs = require('fs');
const path = require('path');

const resultsDir = path.join(__dirname, 'results');
const configDir = path.join(__dirname, 'config');
const contract = require(path.join(configDir, 'normalized_business_contract.json'));

function getLatestResult(target) {
  if (!fs.existsSync(resultsDir)) {
    return null;
  }

  const files = fs.readdirSync(resultsDir)
    .filter((file) => file.startsWith(target) && file.endsWith('.json'))
    .sort()
    .reverse();

  if (files.length === 0) {
    return null;
  }

  return JSON.parse(fs.readFileSync(path.join(resultsDir, files[0]), 'utf-8'));
}

function computeEndpointMetrics(configFile) {
  const config = require(path.join(configDir, configFile));
  const generated = extractEndpointsFromArtifact(config);
  const expectedBusiness = contract.businessEndpoints;
  const antiPatternEndpoints = contract.antiPatternEndpoints;

  const correctBusiness = generated.filter((endpoint) =>
    expectedBusiness.some((gt) => gt.method === endpoint.method && gt.semanticPath === endpoint.path)
  );

  const incorrectAuth = generated.filter((endpoint) =>
    antiPatternEndpoints.some((gt) => gt.method === endpoint.method && gt.semanticPath === endpoint.path)
  );

  const extraEndpoints = generated.filter((endpoint) =>
    !expectedBusiness.some((gt) => gt.method === endpoint.method && gt.semanticPath === endpoint.path) &&
    !antiPatternEndpoints.some((gt) => gt.method === endpoint.method && gt.semanticPath === endpoint.path)
  );

  const totalGenerated = generated.length;
  const correctCount = correctBusiness.length;
  const totalExpected = expectedBusiness.length;
  const EP = totalGenerated > 0 ? correctCount / totalGenerated : 0;
  const ER = totalExpected > 0 ? correctCount / totalExpected : 0;
  const F1 = (EP + ER) > 0 ? (2 * EP * ER) / (EP + ER) : 0;

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
        path: pendingPath,
      });
      pendingPath = null;
    }
  }

  return dedupeEndpoints(endpoints);
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

function metricOrNA(category, field, result) {
  if (!result || !result[category]) {
    return 'N/A';
  }
  if (field === 'passRate' && getApplicableCount(result[category]) === 0) {
    return 'N/A';
  }
  const value = result[category][field];
  return value == null ? 'N/A' : value;
}

function asPercent(value) {
  return value == null || value === 'N/A' ? 'N/A' : `${value}%`;
}

function getApplicableCount(categoryResult) {
  if (!categoryResult) {
    return 0;
  }
  if (typeof categoryResult.applicable === 'number') {
    return categoryResult.applicable;
  }
  return (categoryResult.passed || 0) + (categoryResult.failed || 0);
}

console.log('='.repeat(72));
console.log('  Coffee Evaluation Report');
console.log(`  ${new Date().toISOString()}`);
console.log('='.repeat(72));

const configs = fs.readdirSync(configDir)
  .filter((file) => file.endsWith('.json'))
  .filter((file) => require(path.join(configDir, file)).configType === 'target');

const report = { timestamp: new Date().toISOString(), targets: [] };

for (const configFile of configs) {
  const config = require(path.join(configDir, configFile));
  const metrics = computeEndpointMetrics(configFile);
  const testResult = getLatestResult(config.shortName);

  const entry = {
    ...metrics,
    e2e: testResult ? {
      total: testResult.totalTests,
      applicable: testResult.applicable,
      passed: testResult.passed,
      failed: testResult.failed,
      overallPassRate: asPercent(testResult.passRate),
      coreFCR: asPercent(metricOrNA('core', 'passRate', testResult)),
      authCompatibilityRate: asPercent(metricOrNA('authCompatibility', 'passRate', testResult)),
      asyncRate: asPercent(metricOrNA('asyncSemantics', 'passRate', testResult)),
      robustnessRate: asPercent(metricOrNA('robustness', 'passRate', testResult)),
      duration: `${testResult.duration}ms`,
    } : { status: 'NOT RUN' },
  };

  report.targets.push(entry);
}

if (!fs.existsSync(resultsDir)) {
  fs.mkdirSync(resultsDir, { recursive: true });
}
const outFile = path.join(resultsDir, `report_${new Date().toISOString().replace(/[:.]/g, '-')}.json`);
fs.writeFileSync(outFile, JSON.stringify(report, null, 2));
console.log(`Full report saved to: ${outFile}`);
