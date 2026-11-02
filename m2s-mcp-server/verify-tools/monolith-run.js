'use strict';
// run_monolith (PIPELINE_V2_PLAN.md §4.3, J2): runs the monolith itself against
// the judge cases and records what it does, for the judge's expectations.
//
//   node monolith-run.js <job.json>
//
// job (built by judge.py):
//   monolith_root, run {runtime, entry, args, port_env, env, ready_path, startup_timeout_s;
//                       Python: python (the venv's interpreter), app ("module:attr"), debug},
//   backend / ddb_local / tables      the DynamoDB backend (ddb-proxy.js), the
//                                     same kind run_judge uses (V2P-11)
//   identities {alias: monolith spec} how each alias is obtained (judge/identities.json)
//   units [{id, fixtures {table: [items]}, identity, aliases [..], steps [..], rules [..]}]
//                                     one unit = one case, or one case of a fault scenario
//   passes, coverage, settle_ms, settle_max_ms, pass_gap_ms, out_dir
//
// Every pass starts the monolith afresh (node -r monolith-preload.js <entry>, or for
// Python py/m2s_monolith.py in the monolith's venv,
// TZ=UTC, no proxy variables, its SDK pointed at the recording proxy) and
// runs every unit in order:
//   - every table is emptied and seeded with the unit's fixtures;
//   - each alias the unit uses is prepared (its steps, e.g. register, or a
//     token signed with the monolith's key) -- not part of the record;
//   - the steps run in order; values can be saved (save {var: "body.a.b"})
//     and used as ${var}, an alias's values as ${alias.var};
//   - after each step the runner waits until the backend has been quiet for
//     settle_ms (fire-and-forget work of that request), so a later read-back
//     step sees it -- the target side delivers queued messages between steps;
//   - recorded: every step's request and response, the SDK calls and SQS
//     messages of the steps, which fault rules fired, every table afterwards,
//     and (first pass, job.coverage) the lines this unit ran.
// Passes start at least pass_gap_ms apart, so second-resolution timestamps
// differ between passes and are recognised as volatile.
//
// Output: <out_dir>/monolith_raw.json

const fs = require('fs');
const path = require('path');
const http = require('http');
const net = require('net');
const crypto = require('crypto');
const { fileURLToPath } = require('url');
const { spawn } = require('child_process');
const { startBackend, cleanup } = require('./ddb-proxy');

const job = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const OUT = job.out_dir;
fs.mkdirSync(OUT, { recursive: true });
const log = (...a) => process.stderr.write('[monolith-run] ' + a.join(' ') + '\n');
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function freePort() {
  return new Promise((resolve) => {
    const s = net.createServer();
    s.listen(0, '127.0.0.1', () => { const p = s.address().port; s.close(() => resolve(p)); });
  });
}

// ---------------------------------------------------------------- the monolith process

async function startMonolith(proxyPort) {
  const run = job.run || {};
  const port = await freePort();
  const covPort = job.coverage ? await freePort() : 0;
  const env = { ...process.env };
  for (const k of ['HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy', 'ALL_PROXY', 'all_proxy', 'NODE_OPTIONS',
    'AWS_PROFILE', 'AWS_SESSION_TOKEN']) delete env[k];
  const region = (run.env || {}).AWS_REGION || 'us-east-1';
  Object.assign(env, run.env || {}, {
    [run.port_env || 'PORT']: String(port), TZ: 'UTC', NO_PROXY: '127.0.0.1,localhost', no_proxy: '127.0.0.1,localhost',
    M2S_DDB_ENDPOINT: 'http://127.0.0.1:' + proxyPort,
    AWS_ENDPOINT_URL_DYNAMODB: 'http://127.0.0.1:' + proxyPort,
    AWS_ENDPOINT_URL_SQS: 'http://127.0.0.1:' + proxyPort,
    AWS_REGION: region, AWS_DEFAULT_REGION: region, AWS_ACCESS_KEY_ID: 'x', AWS_SECRET_ACCESS_KEY: 'x',
    M2S_COV_PORT: String(covPort),
  });
  let proc;
  if (run.runtime === 'python') {
    // The monolith's own venv; m2s_monolith.py serves its WSGI app (no reloader,
    // no debugger), caps botocore's retry backoff and measures coverage.
    // PYTHONDONTWRITEBYTECODE: no __pycache__ is written into the monolith's source tree.
    Object.assign(env, { PYTHONUNBUFFERED: '1', PYTHONIOENCODING: 'utf-8', PYTHONDONTWRITEBYTECODE: '1' });
    delete env.PYTHONPATH; delete env.PYTHONHOME;
    proc = spawn(run.python, [path.join(__dirname, 'py', 'm2s_monolith.py'), job.monolith_root, run.app, String(port),
      run.debug ? '1' : '0'], { cwd: job.monolith_root, env, stdio: ['ignore', 'pipe', 'pipe'] });
  } else {
    const preload = path.join(__dirname, 'monolith-preload.js');
    proc = spawn(process.execPath, ['-r', preload, run.entry, ...(run.args || [])],
      { cwd: job.monolith_root, env, stdio: ['ignore', 'pipe', 'pipe'] });
  }
  const logs = [];
  proc.stdout.on('data', (d) => logs.push(d.toString()));
  proc.stderr.on('data', (d) => logs.push(d.toString()));
  const mono = { proc, port, covPort, logs, exited: null };
  mono.done = new Promise((r) => proc.on('exit', (code) => { mono.exited = code === null ? 'signal' : code; r(); }));
  const deadline = Date.now() + 1000 * (run.startup_timeout_s || 30);
  while (Date.now() < deadline) {
    if (mono.exited !== null) throw new Error('the monolith exited (' + mono.exited + ') before listening:\n' + logs.join('').slice(-3000));
    const up = await new Promise((r) => http.get({ host: '127.0.0.1', port, path: run.ready_path || '/', agent: false },
      (res) => { res.resume(); r(true); }).on('error', () => r(false)));
    if (up) return mono;
    await sleep(100);
  }
  proc.kill();
  throw new Error('the monolith did not listen on ' + (run.port_env || 'PORT') + '=' + port + ' within ' +
    (run.startup_timeout_s || 30) + 's:\n' + logs.join('').slice(-3000));
}

// One retry: a start that fails once (a cold node_modules, a port taken in
// between) is not yet the monolith's problem.
async function startMonolithRetry(proxyPort) {
  try {
    return await startMonolith(proxyPort);
  } catch (e) {
    log('start failed, retrying once:', String(e.message || e).split('\n')[0]);
    return startMonolith(proxyPort);
  }
}

async function stopMonolith(mono) {
  if (mono.exited === null) { mono.proc.kill(); await Promise.race([mono.done, sleep(5000)]); }
}

// ---------------------------------------------------------------- coverage

function takeCoverage(mono) {
  if (!mono.covPort) return Promise.resolve(null);
  return new Promise((resolve) => {
    const req = http.request({ host: '127.0.0.1', port: mono.covPort, method: 'POST', path: '/take', agent: false }, (res) => {
      const out = [];
      res.on('data', (c) => out.push(c));
      res.on('end', () => {
        try {
          const data = JSON.parse(Buffer.concat(out).toString('utf8'));
          // Node: V8 script coverage (result); Python: lines per file already (files)
          resolve(data.files ? { files: data.files } : data.result);
        } catch (e) { resolve(null); }
      });
    });
    req.on('error', () => resolve(null));
    req.end();
  });
}

const sources = new Map();

function sourceOf(file) {
  if (!sources.has(file)) {
    let text = null;
    try { text = fs.readFileSync(file, 'utf8'); } catch (e) { /* gone */ }
    sources.set(file, text);
  }
  return sources.get(file);
}

// Per file (relative to the monolith root): the lines that ran ("ran") and
// the lines inside code V8 reported with a zero count ("zero"). The innermost
// range decides the count of a character (ranges sorted outer to inner); a
// line ran when a non-blank character of it ran.
function lineCoverage(result) {
  if (!result) return null;
  if (result.files) return result.files; // coverage.py (m2s_monolith.py) did this already
  const files = {};
  for (const script of result) {
    let file;
    try { file = fileURLToPath(script.url); } catch (e) { continue; }
    const text = sourceOf(file);
    if (text === null) continue;
    const counts = new Int32Array(text.length).fill(-1);
    const ranges = [];
    for (const fn of script.functions) for (const r of fn.ranges) ranges.push(r);
    ranges.sort((a, b) => a.startOffset - b.startOffset || b.endOffset - a.endOffset);
    for (const r of ranges) counts.fill(r.count, r.startOffset, Math.min(r.endOffset, text.length));
    const ran = new Set();
    const zero = new Set();
    let line = 1;
    for (let i = 0; i < text.length; i++) {
      const ch = text.charCodeAt(i);
      if (ch === 10) { line += 1; continue; }
      if (ch === 32 || ch === 9 || ch === 13) continue;
      if (counts[i] > 0) ran.add(line);
      else if (counts[i] === 0) zero.add(line);
    }
    for (const l of ran) zero.delete(l);
    const rel = path.relative(job.monolith_root, file).replace(/\\/g, '/');
    files[rel] = { ran: [...ran].sort((a, b) => a - b), zero: [...zero].sort((a, b) => a - b) };
  }
  return files;
}

// ---------------------------------------------------------------- requests

function subst(value, vars) {
  if (typeof value === 'string') return value.replace(/\$\{([^}]+)\}/g, (m, k) => (k in vars ? String(vars[k]) : m));
  if (Array.isArray(value)) return value.map((v) => subst(v, vars));
  if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value).map(([k, v]) => [subst(k, vars), subst(v, vars)]));
  return value;
}

// A saved value: "status", "body", "body.a.0.b", "headers.<name>".
function pick(response, dotted) {
  if (dotted === 'status') return response.status;
  const [head, ...rest] = dotted.split('.');
  if (head === 'headers') return (response.headers || {})[rest.join('.').toLowerCase()];
  return rest.reduce((o, k) => (o == null ? undefined : o[k]), response.body);
}

function send(port, step) {
  return new Promise((resolve) => {
    let p = step.path;
    if (step.query && Object.keys(step.query).length) {
      p += (p.includes('?') ? '&' : '?') + new URLSearchParams(Object.entries(step.query).map(([k, v]) => [k, String(v)])).toString();
    }
    const headers = { ...(step.headers || {}) };
    const has = (h) => Object.keys(headers).some((k) => k.toLowerCase() === h);
    let body = null;
    if ('body' in step) {
      body = JSON.stringify(step.body);
      if (!has('content-type')) headers['content-type'] = 'application/json';
    } else if ('form' in step) {
      body = new URLSearchParams(Object.entries(step.form).map(([k, v]) => [k, String(v)])).toString();
      if (!has('content-type')) headers['content-type'] = 'application/x-www-form-urlencoded';
    } else if ('raw_body' in step) {
      body = step.raw_body;
    }
    // Always say how long the body is: without it Node sends a DELETE (or
    // GET / OPTIONS) body with neither Content-Length nor chunked encoding,
    // and the server reads no body at all. Real clients send the length.
    if (body !== null && !has('content-length')) headers['content-length'] = String(Buffer.byteLength(body));
    const request = { method: step.method.toUpperCase(), path: p, headers, body };
    const req = http.request({ host: '127.0.0.1', port, method: request.method, path: p, headers, agent: false }, (res) => {
      const out = [];
      res.on('data', (c) => out.push(c));
      res.on('end', () => {
        const text = Buffer.concat(out).toString('utf8');
        let parsed = text;
        let kind = text.length ? 'text' : 'empty';
        if (text.length && /json/i.test(res.headers['content-type'] || '')) {
          try { parsed = JSON.parse(text); kind = 'json'; } catch (e) { /* malformed JSON stays text */ }
        }
        resolve({ request, response: { status: res.statusCode, headers: { ...res.headers }, body: kind === 'empty' ? null : parsed, body_kind: kind } });
      });
    });
    // The error code, not the message: the message names the (random) port.
    req.on('error', (e) => resolve({ request, response: { error: String(e && (e.code || e.message) || e) } }));
    if (body !== null) req.write(body);
    req.end();
  });
}

// Wait until the backend has been quiet for settle_ms: the request's
// fire-and-forget work is done (or settle_max_ms passed).
async function settle(ctx) {
  const quiet = job.settle_ms || 80;
  const t0 = Date.now();
  await sleep(quiet);
  while (Date.now() - t0 < (job.settle_max_ms || 3000)) {
    if (ctx.inflight === 0 && Date.now() - ctx.last >= quiet) return;
    await sleep(10);
  }
}

function b64url(buf) {
  return Buffer.from(buf).toString('base64').replace(/=+$/, '').replace(/\+/g, '-').replace(/\//g, '_');
}

function signHS256(sign, vars) {
  const now = Math.floor(Date.now() / 1000);
  const payload = { ...subst(sign.claims, vars), iat: now, exp: now + (sign.expires_in_s || 3600) };
  const head = b64url(JSON.stringify({ alg: 'HS256', typ: 'JWT' })) + '.' + b64url(JSON.stringify(payload));
  return head + '.' + b64url(crypto.createHmac('sha256', sign.secret).update(head).digest());
}

// ---------------------------------------------------------------- units

async function prepare(be, mono, alias) {
  const spec = job.identities[alias];
  const own = {};
  const steps = [];
  if (spec.sign) {
    own.id = spec.id;
    own.token = signHS256(spec.sign, { id: spec.id });
  } else {
    const ctx = be.beginCase([]);
    for (const raw of spec.steps) {
      const r = await send(mono.port, subst(raw, own));
      await settle(ctx);
      for (const [k, p] of Object.entries(raw.save || {})) own[k] = pick(r.response, p);
      steps.push({ name: raw.name || null, status: r.response.status, error: r.response.error });
    }
    be.endCase();
  }
  return { vars: own, header: subst(spec.header, own), steps };
}

async function runUnit(be, mono, unit, withCoverage) {
  await be.resetTables(unit.fixtures);
  if (withCoverage) await takeCoverage(mono); // what the previous unit left
  const vars = {};
  const prep = {};
  for (const alias of unit.aliases) {
    const p = await prepare(be, mono, alias);
    prep[alias] = p;
    for (const [k, v] of Object.entries(p.vars)) vars[alias + '.' + k] = v;
  }
  if (withCoverage) await takeCoverage(mono); // preparation is not the unit's
  const ctx = be.beginCase(unit.rules);
  const steps = [];
  for (const raw of unit.steps) {
    const alias = 'identity' in raw ? raw.identity : unit.identity;
    const step = subst(raw, vars);
    if (alias) step.headers = { ...prep[alias].header, ...(step.headers || {}) };
    const r = await send(mono.port, step);
    await settle(ctx);
    for (const [k, p] of Object.entries(raw.save || {})) vars[k] = pick(r.response, p);
    steps.push({ name: raw.name, identity: alias || null, request: r.request, response: r.response });
  }
  be.endCase();
  const coverage = withCoverage ? lineCoverage(await takeCoverage(mono)) : undefined;
  return {
    id: unit.id,
    identities: Object.fromEntries(Object.entries(prep).map(([a, p]) => [a, { vars: p.vars, steps: p.steps }])),
    steps, calls: ctx.calls, messages: ctx.messages, rules_fired: ctx.rules.map((r) => r.fired),
    tables: await be.dumpTables(), coverage, monolith_exited: mono.exited !== null,
  };
}

async function main() {
  const be = await startBackend({ backend: job.backend, ddbLocal: job.ddb_local, tables: job.tables, bindHost: '127.0.0.1' });
  await be.createTables();
  const out = { backend: job.backend === 'ddblocal' ? 'ddblocal' : 'dynalite', node: process.version, passes: [],
                load_coverage: null, monolith_logs: [] };
  try {
    let lastStart = 0;
    for (let pass = 0; pass < (job.passes || 3); pass++) {
      await sleep(Math.max(0, lastStart + (job.pass_gap_ms || 1100) - Date.now()));
      lastStart = Date.now();
      let mono = await startMonolithRetry(be.proxyPort);
      const withCoverage = Boolean(job.coverage) && pass === 0;
      if (withCoverage) out.load_coverage = lineCoverage(await takeCoverage(mono));
      const results = [];
      try {
        for (const unit of job.units) {
          let restarted = false;
          if (mono.exited !== null) {
            // The previous unit brought the monolith down (recorded there as
            // monolith_exited); the next unit gets a fresh one.
            log('the monolith exited (' + mono.exited + ') during pass ' + (pass + 1) + '; restarting before ' + unit.id);
            out.monolith_logs.push(mono.logs.join('').slice(-4000));
            mono = await startMonolithRetry(be.proxyPort);
            restarted = true;
          }
          const r = await runUnit(be, mono, unit, withCoverage);
          if (restarted) r.restarted_before = true;
          results.push(r);
        }
      } finally {
        await stopMonolith(mono);
        out.monolith_logs.push(mono.logs.join('').slice(-4000));
      }
      out.passes.push(results);
      log('pass', pass + 1, 'done:', results.length, 'units');
    }
  } finally {
    be.close();
  }
  fs.writeFileSync(path.join(OUT, 'monolith_raw.json'), JSON.stringify(out, null, 1));
}

main().then(() => process.exit(0), (e) => {
  log('FATAL', e && e.stack || e);
  cleanup();
  try { fs.writeFileSync(path.join(OUT, 'monolith_error.txt'), String(e && e.stack || e)); } catch (x) { /* ignore */ }
  process.exit(1);
});
