'use strict';
// Local-Node stand-in for the Lambda runtime (no-Docker fallback).
//   node local-invoker.js <functionDir> <module.export>
// Reads one JSON event per stdin line, invokes the handler, writes one
// "@@M2S@@<json>" line per event to stdout: {result} or {error:{type,message}},
// plus ms and rss. Exits (flushing NODE_V8_COVERAGE) when stdin closes.
const path = require('path');
const readline = require('readline');
const [dir, handlerSpec] = process.argv.slice(2);
const dot = handlerSpec.lastIndexOf('.');
const modPath = path.join(dir, handlerSpec.slice(0, dot));
const exportName = handlerSpec.slice(dot + 1);
let handler = null;
let loadError = null;
try { handler = require(modPath)[exportName]; if (typeof handler !== 'function') loadError = { type: 'Runtime.HandlerNotFound', message: handlerSpec + ' is undefined or not exported' }; }
catch (e) { loadError = { type: e.code === 'MODULE_NOT_FOUND' ? 'Runtime.ImportModuleError' : 'Runtime.UserCodeSyntaxError', message: String(e.message) }; }
const emit = (o) => process.stdout.write('@@M2S@@' + JSON.stringify(o) + '\n');
let n = 0;
const rl = readline.createInterface({ input: process.stdin });
rl.on('line', async (line) => {
  const event = JSON.parse(line);
  if (loadError) return emit({ error: loadError, ms: 0 });
  const context = { awsRequestId: 'req-' + (++n), functionName: process.env.AWS_LAMBDA_FUNCTION_NAME, memoryLimitInMB: process.env.AWS_LAMBDA_FUNCTION_MEMORY_SIZE, getRemainingTimeInMillis: () => 30000, callbackWaitsForEmptyEventLoop: true };
  const t0 = Date.now();
  try {
    const result = await handler(event, context);
    emit({ result: result === undefined ? null : result, ms: Date.now() - t0, rss_mb: Math.round(process.memoryUsage().rss / 1048576) });
  } catch (e) {
    emit({ error: { type: (e && e.name) || 'Error', message: String((e && e.message) || e) }, ms: Date.now() - t0, rss_mb: Math.round(process.memoryUsage().rss / 1048576) });
  }
});
rl.on('close', () => setTimeout(() => process.exit(0), 50));
