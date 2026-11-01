'use strict';
// Preloaded into the monolith (node -r monolith-preload.js <entry>) by monolith-run.js
// (PIPELINE_REDESIGN_v2.md J2 run_monolith, open question Q4).
//
// 1. Points the monolith's own aws-sdk (v2) and @aws-sdk (v3) clients at the recording
//    proxy: the module is resolved from the monolith's directory, so it is the same
//    instance the application requires (a plain require here would resolve from
//    verify-tools/ and configure a different copy).
// 2. With M2S_COV_PORT set, starts precise V8 coverage in-process and serves
//    POST /take on that port: it returns the coverage collected since the last take and
//    resets the counters (Profiler.takePreciseCoverage), so each case gets its own counts.

const path = require('path');
const http = require('http');

const endpoint = process.env.M2S_DDB_ENDPOINT;
const appDir = process.cwd();

function fromApp(name) {
  try {
    return require(require.resolve(name, { paths: [appDir] }));
  } catch (err) {
    return null;
  }
}

if (endpoint) {
  const AWS = fromApp('aws-sdk');
  if (AWS && AWS.config) {
    // The SDK keeps its own retry count (DynamoDB: 10) -- whether a throttled
    // call ends in success or in an error is the monolith's behaviour, and the
    // target keeps its SDK's retries too. Only the backoff is shortened (5 ms
    // base instead of 50 ms), so a fault that never clears does not take
    // ~50 s per call; that changes timing, not outcomes.
    AWS.config.update({ dynamodb: { endpoint, retryDelayOptions: { base: 5 } },
                        sqs: { endpoint, retryDelayOptions: { base: 5 } } });
  }
  // aws-sdk v3 honours AWS_ENDPOINT_URL_DYNAMODB / AWS_ENDPOINT_URL_SQS, set by the runner.
}

const covPort = Number(process.env.M2S_COV_PORT || 0);
if (covPort) {
  const inspector = require('inspector');
  const session = new inspector.Session();
  session.connect();
  const post = (method, params) => new Promise((resolve, reject) =>
    session.post(method, params || {}, (err, res) => (err ? reject(err) : resolve(res))));
  const ready = post('Profiler.enable').then(() => post('Profiler.startPreciseCoverage', { callCount: true, detailed: true }));
  const appPrefix = 'file:///' + appDir.replace(/\\/g, '/').replace(/^\//, '');
  const server = http.createServer(async (req, res) => {
    try {
      await ready;
      const { result } = await post('Profiler.takePreciseCoverage');
      const own = result.filter((s) => s.url.toLowerCase().startsWith(appPrefix.toLowerCase())
        && !s.url.includes('/node_modules/'));
      res.writeHead(200, { 'content-type': 'application/json' });
      res.end(JSON.stringify({ result: own }));
    } catch (err) {
      res.writeHead(500);
      res.end(String(err && err.stack || err));
    }
  });
  server.listen(covPort, '127.0.0.1');
  server.unref();
}
