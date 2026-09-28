'use strict';
// Proxy metrics for one handler + one benchmark event, in local Node.
//   node perf-harness.js <job.json>
// job: {fn_dir, handler, env, event, fixtures_path, depth_latency_ms, cpu_warmup, cpu_reps}
// Prints one line "@@M2S@@<json>":
//   valid run (latency = depth_latency_ms, the second invocation -- warm):
//   status/error, calls, sequential depth, capacity units; warm run
//   (latency 0): median/p90 wall time per
//   invocation over cpu_reps runs after cpu_warmup runs. At zero latency the
//   wall time is the CPU part of warm execution (Windows' 15.6ms clock tick
//   makes process.cpuUsage() useless at this scale).
const path = require('path');

const job = JSON.parse(require('fs').readFileSync(process.argv[2], 'utf8'));
Object.assign(process.env, job.env || {});
process.env.SHIM_FN_DIR = job.fn_dir;
process.env.SHIM_FIXTURES = job.fixtures_path;
process.env.SHIM_LATENCY_MS = '0';
const shim = require('./perf-shim.js');
const out = (o) => process.stdout.write('@@M2S@@' + JSON.stringify(o) + '\n');
for (const k of ['log', 'info', 'warn', 'error', 'debug']) console[k] = () => {};

function depthOf(calls) {
  // Longest chain of calls where each starts after the previous one ended:
  // with a fixed per-call latency this is how many round trips wall time pays.
  const cs = calls.slice().sort((a, b) => a.start - b.start);
  const dp = cs.map(() => 1);
  for (let i = 0; i < cs.length; i++) for (let j = 0; j < i; j++) if (cs[j].end <= cs[i].start + 0.5) dp[i] = Math.max(dp[i], dp[j] + 1);
  return cs.length ? Math.max(...dp) : 0;
}

(async () => {
  if (!shim.patched.length) return out({ error: { type: 'M2SPerf', message: 'perf-shim found no @smithy/node-http-handler under ' + job.fn_dir } });
  const dot = job.handler.lastIndexOf('.');
  let handler;
  try { handler = require(path.join(job.fn_dir, job.handler.slice(0, dot)))[job.handler.slice(dot + 1)]; } catch (e) { return out({ error: { type: 'Runtime.ImportModuleError', message: String(e.message) } }); }
  const ctx = { awsRequestId: 'perf', functionName: 'perf', getRemainingTimeInMillis: () => 30000 };
  const invoke = async () => { try { return { result: await handler(JSON.parse(JSON.stringify(job.event)), ctx) }; } catch (e) { return { error: { type: e.name, message: String(e.message) } }; } };

  shim.state.latency = job.depth_latency_ms || 10;
  // The first invocation pays one-time work (clients, pools, per-thread
  // resources) that staggers its calls; calls and depth describe a warm
  // invocation, the second one. A first invocation that fails is reported.
  let first = await invoke();
  if (!first.error) { shim.reset(); first = await invoke(); }
  const calls = shim.calls();
  const sum = (k) => calls.reduce((n, c) => n + (c[k] || 0), 0);
  const res = {
    status: first.result && typeof first.result === 'object' ? first.result.statusCode : undefined,
    batch_item_failures: first.result && Array.isArray(first.result.batchItemFailures) ? first.result.batchItemFailures.length : undefined,
    error: first.error || null,
    calls: calls.length,
    call_ops: calls.map((c) => c.op + ':' + (c.table || '')),
    depth: depthOf(calls),
    conflicts: calls.filter((c) => c.conflict).length,
    rru: sum('rru'), wru: sum('wru'), sqs_requests: sum('sqs_requests'),
  };
  shim.state.latency = 0;
  for (let i = 0; i < (job.cpu_warmup || 30); i++) await invoke();
  const t = [];
  for (let i = 0; i < (job.cpu_reps || 300); i++) {
    const t0 = process.hrtime.bigint();
    await invoke();
    t.push(Number(process.hrtime.bigint() - t0) / 1e6);
  }
  t.sort((a, b) => a - b);
  res.warm_cpu_ms = { median: +t[Math.floor(t.length / 2)].toFixed(3), p90: +t[Math.floor(t.length * 0.9)].toFixed(3), n: t.length };
  res.max_rss_mb = Math.round(shim.state.maxRss / 1048576);
  out(res);
})().catch((e) => out({ error: { type: 'M2SPerfHarness', message: String(e.stack || e) } }));
