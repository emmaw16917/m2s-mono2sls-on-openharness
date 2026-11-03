'use strict';
// Verification-line execution engine (driven by ../verify.py).
//
//   node controller.js <job.json>
//
// Starts the DynamoDB backend behind the recording proxy (ddb-proxy.js:
// DynamoDB Local or dynalite), then runs each test case against the real
// handler:
//   - every SDK request from the handler goes to the proxy (the handler's
//     AWS_ENDPOINT_URL_DYNAMODB / _SQS point at it); the proxy records the
//     call, applies the case's fault rules, forwards DynamoDB calls to the
//     backend and answers SQS SendMessage itself (recording the message),
//     and rejects requests that touch an item of an ongoing transaction the
//     way real DynamoDB does (tx-conflicts.js; call.conflict = 'emulated');
//   - before each case every table is emptied and seeded with the case's
//     fixtures, after it every table is dumped.
// Handlers run either in the Lambda runtime image with RIE (mode "rie",
// code docker-cp'd in, not bind-mounted) or in a local Node child process
// (mode "local", the no-Docker fallback of PIPELINE_REDESIGN.md §3.5).
// With job.coverage set, V8 coverage is collected per function and turned
// into an istanbul coverage-final.json by c8.
//
// Output: <job.out_dir>/raw_results.json -- raw observations only; judging
// them against expectations is verify.py's job.

const fs = require('fs');
const path = require('path');
const http = require('http');
const { spawn, execFileSync } = require('child_process');
const { startBackend, cleanup } = require('./ddb-proxy');

const job = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const OUT = job.out_dir;
fs.mkdirSync(OUT, { recursive: true });
const log = (...a) => process.stderr.write('[controller] ' + a.join(' ') + '\n');

// ---------------------------------------------------------------------------
// Invokers
// ---------------------------------------------------------------------------

const { RieInvoker, LocalInvoker, convertCoverage } = require('./invokers')(job);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

(async () => {
  // 0.0.0.0: Lambda containers reach the proxy through host.docker.internal.
  const be = await startBackend({ backend: job.backend, ddbLocal: job.ddb_local, tables: job.tables, holdWritesMs: job.hold_writes_ms, bindHost: '0.0.0.0' });
  await be.createTables();
  log('proxy on', be.proxyPort, 'mode', job.mode);
  const results = { mode: job.mode, backend: job.backend || 'dynalite', cases: [], functions: {} };
  let port = 9100;
  for (const fn of job.functions) {
    const cases = job.cases.filter((c) => c.function === fn.name);
    if (!cases.length) continue;
    const covDir = path.join(OUT, 'v8cov', fn.name);
    const inv = job.mode === 'rie' ? new RieInvoker(fn, be.proxyPort, port++) : new LocalInvoker(fn, be.proxyPort);
    const fres = { started: false };
    try {
      await inv.start(covDir);
      fres.started = true;
    } catch (e) {
      fres.start_error = String(e.message || e);
      for (const c of cases) results.cases.push({ id: c.id, function: fn.name, run_key: c.run_key, infra_error: 'function could not be started: ' + fres.start_error });
      results.functions[fn.name] = fres;
      continue;
    }
    let first = true;
    for (const c of cases) {
      const rec = { id: c.id, function: fn.name, run_key: c.run_key };
      try {
        // Functions with a bounded-staleness cache: every case starts in a
        // fresh execution environment, so no case sees another's cached data.
        if (fn.isolate_cases && !first) await inv.restart(covDir);
        first = false;
        await be.resetTables(c.fixtures);
        const ctx = be.beginCase(c.faults);
        if (c.gateway_response) {
          rec.gateway = true;
          rec.result = c.gateway_response;
        } else {
          be.functionInvoked(fn.name);
          const out = await inv.invoke(c.event);
          Object.assign(rec, out);
          if (c.repeat) {
            // The same request again in this execution environment (cache hits).
            rec.repeats = [out];
            for (let k = 1; k < c.repeat; k++) rec.repeats.push(await inv.invoke(c.event));
          }
          // Work the handler abandoned (e.g. retries of a sibling request
          // after Promise.all rejected) keeps hitting the backend after it
          // returns. Keep the fault rules active and wait until it is quiet,
          // so both versions of a comparison account for it the same way.
          be.functionReturned();
          const t0 = Date.now();
          const busy = ctx.inflight > 0 || (c.faults || []).length > 0;
          const quiet = busy ? 1000 : 150;
          while (Date.now() - t0 < 10000 && (ctx.inflight > 0 || Date.now() - ctx.last < quiet)) await sleep(50);
          rec.settle_ms = Date.now() - t0;
        }
        rec.calls = ctx.calls;
        rec.messages = ctx.messages;
        rec.rules_fired = ctx.rules.map((r) => r.fired);
        be.endCase();
        rec.tables_after = await be.dumpTables();
      } catch (e) {
        be.endCase();
        rec.infra_error = String(e.stack || e);
      }
      results.cases.push(rec);
    }
    await inv.stop(covDir);
    fres.logs_tail = inv.logs().slice(-4000);
    if (job.coverage) {
      try { fres.coverage = await convertCoverage(fn, covDir, path.join(OUT, 'coverage', fn.name)); } catch (e) { fres.coverage = { error: String(e.stack || e) }; }
    }
    results.functions[fn.name] = fres;
  }
  fs.writeFileSync(path.join(OUT, 'raw_results.json'), JSON.stringify(results, null, 2));
  be.close();
  log('done', results.cases.length, 'cases');
  process.exit(0);
})().catch((e) => {
  log('FATAL', e.stack || e);
  cleanup();
  process.exit(2);
});
