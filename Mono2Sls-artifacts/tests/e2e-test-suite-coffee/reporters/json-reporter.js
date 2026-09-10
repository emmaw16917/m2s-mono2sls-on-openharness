const fs = require('fs');
const path = require('path');

class JsonReporter {
  onRunComplete(testContexts, results) {
    const target = process.env.TARGET || 'unknown';
    const timestamp = new Date().toISOString();
    const testResults = [];

    for (const suite of results.testResults) {
      for (const test of suite.testResults) {
        const category = inferCategory(test.title, test.ancestorTitles);
        testResults.push({
          suite: test.ancestorTitles.join(' > '),
          name: test.title,
          tcId: extractTcId(test.title) || extractTcId(test.ancestorTitles[0]),
          category,
          status: test.status,
          duration: test.duration,
          failureMessages: test.failureMessages,
        });
      }
    }

    const summary = {
      target,
      timestamp,
      totalTests: results.numTotalTests,
      passed: results.numPassedTests,
      failed: results.numFailedTests,
      pending: results.numPendingTests,
      applicable: results.numPassedTests + results.numFailedTests,
      passRate: rate(results.numPassedTests, results.numPassedTests + results.numFailedTests),
      core: summarizeCategory(testResults, 'core'),
      authCompatibility: summarizeCategory(testResults, 'auth'),
      asyncSemantics: summarizeCategory(testResults, 'async'),
      robustness: summarizeCategory(testResults, 'robustness'),
      duration: results.testResults.reduce((sum, suite) => sum + (suite.perfStats?.runtime || 0), 0),
      tests: testResults,
    };

    const outDir = path.resolve(__dirname, '../results');
    if (!fs.existsSync(outDir)) {
      fs.mkdirSync(outDir, { recursive: true });
    }

    const outFile = path.join(outDir, `${target}_${timestamp.replace(/[:.]/g, '-')}.json`);
    fs.writeFileSync(outFile, JSON.stringify(summary, null, 2));
    console.log(`Results written to: ${outFile}`);
  }
}

function extractTcId(text) {
  if (!text) {
    return null;
  }
  const match = text.match(/(?:TC|AUTH|ASYNC|ROB)-\d+[a-z]?/);
  return match ? match[0] : null;
}

function inferCategory(title, ancestorTitles) {
  const merged = [title, ...(ancestorTitles || [])].join(' ');
  if (merged.includes('AUTH-')) {
    return 'auth';
  }
  if (merged.includes('ASYNC-')) {
    return 'async';
  }
  if (merged.includes('ROB-')) {
    return 'robustness';
  }
  return 'core';
}

function summarizeCategory(tests, category) {
  const scoped = tests.filter((test) => test.category === category);
  const total = scoped.length;
  const passed = scoped.filter((test) => test.status === 'passed').length;
  const failed = scoped.filter((test) => test.status === 'failed').length;
  const pending = scoped.filter((test) => test.status === 'pending').length;
  const applicable = passed + failed;

  return {
    total,
    applicable,
    passed,
    failed,
    pending,
    passRate: rate(passed, applicable),
  };
}

function rate(passed, total) {
  return total > 0 ? (passed / total * 100).toFixed(1) : null;
}

module.exports = JsonReporter;
