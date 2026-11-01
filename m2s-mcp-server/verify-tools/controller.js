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

const DOCKER = process.env.M2S_DOCKER || 'docker';
const RIE_IMAGE = job.rie_image || 'public.ecr.aws/lambda/nodejs:22-x86_64';
const RIE_IMAGE_PY = job.rie_image_python || 'amazon/aws-lambda-python:3.12';
const COV_FLUSH = path.join(__dirname, 'cov-flush.js');
const LOCAL_INVOKER = path.join(__dirname, 'local-invoker.js');
const PY_TOOLS = path.join(__dirname, 'py');
const isPython = (fn) => String(fn.runtime || '').startsWith('python');

function sleep(ms) { return new Promise((r) => setTimeout(r, ms)); }

function postJson(port, pathName, payload, timeoutMs) {
  return new Promise((resolve, reject) => {
    const data = Buffer.from(JSON.stringify(payload));
    const req = http.request({ host: '127.0.0.1', port, method: 'POST', path: pathName, headers: { 'content-type': 'application/json', 'content-length': data.length }, timeout: timeoutMs }, (res) => {
      const chunks = [];
      res.on('data', (c) => chunks.push(c));
      res.on('end', () => resolve({ status: res.statusCode, headers: res.headers, body: Buffer.concat(chunks).toString('utf8') }));
    });
    req.on('timeout', () => { req.destroy(new Error('invoke timed out after ' + timeoutMs + 'ms')); });
    req.on('error', reject);
    req.end(data);
  });
}

function baseEnv(fn, endpointHost, proxyPort) {
  // Queue URLs point at the proxy too: newer SQS clients send to the host in
  // QueueUrl rather than to the configured endpoint.
  const env = {};
  for (const [k, v] of Object.entries(fn.env || {})) env[k] = String(v).replace(/^https:\/\/sqs\.[a-z0-9-]+\.amazonaws\.com\//, `http://${endpointHost}:${proxyPort}/`);
  return {
    AWS_REGION: 'us-east-1', AWS_DEFAULT_REGION: 'us-east-1',
    AWS_ACCESS_KEY_ID: 'AKIDVERIFY', AWS_SECRET_ACCESS_KEY: 'verify',
    AWS_ENDPOINT_URL_DYNAMODB: `http://${endpointHost}:${proxyPort}`,
    AWS_ENDPOINT_URL_SQS: `http://${endpointHost}:${proxyPort}`,
    AWS_LAMBDA_FUNCTION_NAME: fn.name, AWS_LAMBDA_FUNCTION_MEMORY_SIZE: String(fn.memory || 512),
    ...env,
  };
}

class RieInvoker {
  constructor(fn, proxyPort, port) {
    this.fn = fn; this.port = port; this.name = 'm2sv-' + fn.name.toLowerCase().replace(/[^a-z0-9-]/g, '-');
    this.env = baseEnv(fn, 'host.docker.internal', proxyPort);
    this.py = isPython(fn);
    this.image = this.py ? RIE_IMAGE_PY : RIE_IMAGE;
    this.cmd = fn.handler;
    if (job.coverage && this.py) {
      // m2s_cov_handler.py starts coverage.py, then imports the real handler.
      this.env.M2S_REAL_HANDLER = fn.handler;
      this.cmd = 'm2s_cov_handler.handler';
    } else if (job.coverage) {
      this.env.NODE_V8_COVERAGE = '/tmp/m2s-cov'; this.env.NODE_OPTIONS = '--require /var/task/.m2s-cov-flush.js';
    }
  }
  docker(args, opts = {}) { return execFileSync(DOCKER, args, { stdio: ['ignore', 'pipe', 'pipe'], ...opts }).toString(); }
  async start() {
    try { this.docker(['rm', '-f', this.name]); } catch (e) { /* not there */ }
    const envArgs = Object.entries(this.env).flatMap(([k, v]) => ['-e', `${k}=${v}`]);
    this.docker(['create', '--name', this.name, '-p', `127.0.0.1:${this.port}:8080`, '--add-host', 'host.docker.internal:host-gateway', ...envArgs, this.image, this.cmd]);
    this.docker(['cp', this.fn.dir + path.sep + '.', `${this.name}:/var/task`]);
    // Layers: sam build's build/<LayerId>/ holds python/ or nodejs/, which
    // Lambda extracts under /opt.
    for (const dir of this.fn.layers || []) this.docker(['cp', dir + path.sep + '.', `${this.name}:/opt`]);
    if (job.coverage && this.py) {
      this.docker(['cp', path.join(PY_TOOLS, 'm2s_cov_handler.py'), `${this.name}:/var/task/m2s_cov_handler.py`]);
      this.docker(['cp', path.join(PY_TOOLS, 'm2s_cov_report.py'), `${this.name}:/opt/m2s-cov-report.py`]);
      this.docker(['cp', job.py_vendor + path.sep + '.', `${this.name}:/opt/m2s-cov`]);
    } else if (job.coverage) this.docker(['cp', COV_FLUSH, `${this.name}:/var/task/.m2s-cov-flush.js`]);
    this.docker(['start', this.name]);
    await sleep(1000);
  }
  async invoke(event) {
    const t0 = Date.now();
    const r = await postJson(this.port, '/2015-03-31/functions/function/invocations', event, (job.invoke_timeout_s || 60) * 1000);
    let payload = null;
    try { payload = JSON.parse(r.body); } catch (e) { payload = r.body; }
    // The runtime's error payload: Node sends errorType/errorMessage/trace,
    // Python also stackTrace and requestId.
    const isError = payload && typeof payload === 'object' && !Array.isArray(payload) && 'errorType' in payload && 'errorMessage' in payload && Object.keys(payload).every((k) => ['errorType', 'errorMessage', 'trace', 'stackTrace', 'requestId'].includes(k));
    return isError ? { error: { type: payload.errorType, message: payload.errorMessage }, ms: Date.now() - t0 } : { result: payload, ms: Date.now() - t0 };
  }
  async stop(covDir) {
    if (job.coverage && this.py) {
      // coverage.py data -> istanbul-shaped JSON, made inside the container
      // (its Python and coverage.py), then copied out.
      fs.mkdirSync(covDir, { recursive: true });
      try {
        this.docker(['exec', '-w', '/tmp', this.name, 'python3', '/opt/m2s-cov-report.py', '/tmp/m2s-cov/.coverage', '/tmp/m2s-cov/coverage-final.json']);
        this.docker(['cp', `${this.name}:/tmp/m2s-cov/coverage-final.json`, path.join(covDir, 'coverage-final.json')]);
      } catch (e) { log('python coverage report failed for', this.fn.name, e.message); }
    } else if (job.coverage) {
      await sleep(600); // cov-flush writes every 200ms
      fs.mkdirSync(covDir, { recursive: true });
      try { this.docker(['cp', `${this.name}:/tmp/m2s-cov/.`, covDir]); } catch (e) { log('coverage copy failed for', this.fn.name, e.message); }
    }
    if (!job.keep_containers) { try { this.docker(['rm', '-f', this.name]); } catch (e) { /* ignore */ } }
  }
  async restart() {
    // A new runtime process in the same container (module state gone, /tmp
    // kept): Node coverage snapshots stay in /tmp/m2s-cov, coverage.py loads
    // its data file before measuring again.
    if (job.coverage && !this.py) await sleep(600); // cov-flush writes every 200ms
    this.docker(['restart', '-t', '1', this.name]);
    await sleep(1000);
  }
  logs() { try { return this.docker(['logs', this.name], { stdio: ['ignore', 'pipe', 'pipe'] }); } catch (e) { return ''; } }
}

class LocalInvoker {
  constructor(fn, proxyPort) {
    this.fn = fn;
    this.py = isPython(fn);
    this.env = { ...process.env, ...baseEnv(fn, '127.0.0.1', proxyPort) };
    delete this.env.NODE_OPTIONS;
    for (const k of ['HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy']) delete this.env[k];
  }
  async start(covDir) {
    if (this.py) {
      // Coverage of Python functions needs the RIE container (convertCoverage says so).
      Object.assign(this.env, { PYTHONIOENCODING: 'utf-8', PYTHONDONTWRITEBYTECODE: '1', PYTHONUNBUFFERED: '1' });
      const layerDirs = (this.fn.layers || []).map((d) => path.join(d, 'python'));
      this.proc = spawn(job.python_exe || 'python', [path.join(PY_TOOLS, 'm2s_local_invoker.py'), this.fn.dir, this.fn.handler, ...layerDirs], { env: this.env, stdio: ['pipe', 'pipe', 'pipe'] });
    } else {
      if (job.coverage) { fs.mkdirSync(covDir, { recursive: true }); this.env.NODE_V8_COVERAGE = covDir; }
      this.proc = spawn(process.execPath, [LOCAL_INVOKER, this.fn.dir, this.fn.handler], { env: this.env, stdio: ['pipe', 'pipe', 'pipe'] });
    }
    this.buf = ''; this.waiters = []; this.stderr = '';
    this.proc.stdout.on('data', (c) => {
      this.buf += c.toString('utf8');
      let i;
      while ((i = this.buf.indexOf('\n')) >= 0) {
        const line = this.buf.slice(0, i); this.buf = this.buf.slice(i + 1);
        if (line.startsWith('@@M2S@@')) this.waiters.shift()(JSON.parse(line.slice(7)));
      }
    });
    this.proc.stderr.on('data', (c) => { this.stderr += c.toString('utf8'); });
  }
  invoke(event) {
    return new Promise((resolve) => {
      const timer = setTimeout(() => resolve({ error: { type: 'M2SVerifyTimeout', message: 'invoke timed out' } }), (job.invoke_timeout_s || 60) * 1000);
      this.waiters.push((msg) => { clearTimeout(timer); resolve(msg); });
      this.proc.stdin.write(JSON.stringify(event) + '\n');
    });
  }
  async stop() {
    this.proc.stdin.end();
    await new Promise((r) => this.proc.on('exit', r));
  }
  async restart(covDir) {
    const logs = this.stderr;
    await this.stop();
    await this.start(covDir);
    this.stderr = logs;
  }
  logs() { return this.stderr; }
}

// ---------------------------------------------------------------------------
// Coverage (V8 -> istanbul via c8)
// ---------------------------------------------------------------------------

function convertPythonCoverage(fn, covDir, reportDir) {
  if (job.mode !== 'rie') return { error: 'coverage of Python functions needs Docker (the Lambda Python image); the local fallback does not measure it' };
  const src = path.join(covDir, 'coverage-final.json');
  if (!fs.existsSync(src)) return { error: 'coverage.py wrote no data (see logs_tail)' };
  // Container paths -> build dirs on this machine: /var/task/ is the
  // function, /opt/ is whichever layer holds the file.
  const toHost = (p) => {
    if (p.startsWith('/var/task/')) return path.join(fn.dir, ...p.slice('/var/task/'.length).split('/'));
    if (p.startsWith('/opt/')) {
      const rel = p.slice('/opt/'.length).split('/');
      for (const d of fn.layers || []) { const h = path.join(d, ...rel); if (fs.existsSync(h)) return h; }
    }
    return p;
  };
  const data = JSON.parse(fs.readFileSync(src, 'utf8'));
  const out = {};
  for (const [p, fc] of Object.entries(data)) { const h = toHost(p); out[h] = { ...fc, path: h }; }
  fs.mkdirSync(reportDir, { recursive: true });
  fs.writeFileSync(path.join(reportDir, 'coverage-final.json'), JSON.stringify(out));
  return { report_dir: reportDir };
}

async function convertCoverage(fn, covDir, reportDir) {
  if (isPython(fn)) return convertPythonCoverage(fn, covDir, reportDir);
  const files = fs.existsSync(covDir) ? fs.readdirSync(covDir).filter((f) => f.endsWith('.json')) : [];
  if (!files.length) return { error: 'no V8 coverage files were written' };
  if (job.mode === 'rie') {
    // Container paths -> build dir on this machine, so c8 can read the sources.
    const fileUrl = (dir) => 'file:///' + dir.replace(/\\/g, '/').replace(/^\/+/, '') + '/';
    const hostUrl = fileUrl(fn.dir);
    // /opt/<rel> belongs to whichever layer build dir holds <rel>.
    const optUrl = (url) => {
      const rel = decodeURIComponent(url.slice('file:///opt/'.length)).split('/');
      for (const d of fn.layers || []) if (fs.existsSync(path.join(d, ...rel))) return fileUrl(d) + rel.join('/');
      return url;
    };
    for (const f of files) {
      const p = path.join(covDir, f);
      const data = JSON.parse(fs.readFileSync(p, 'utf8'));
      for (const r of data.result) r.url = r.url.startsWith('file:///opt/') ? optUrl(r.url) : r.url.replace('file:///var/task/', hostUrl);
      data['source-map-cache'] = {};
      fs.writeFileSync(p, JSON.stringify(data));
    }
  }
  const Report = require('c8/lib/report');
  const dirs = [fn.dir, ...(fn.layers || [])];
  const report = Report({
    reporter: ['json', 'json-summary'], reportsDirectory: reportDir, tempDirectory: covDir,
    include: dirs.map((d) => d.replace(/\\/g, '/') + '/**'), exclude: ['**/node_modules/**', '**/.m2s-cov-flush.js'], excludeNodeModules: true,
    all: false, resolve: '', omitRelative: true, allowExternal: true, src: dirs,
  });
  await report.run();
  return { report_dir: reportDir };
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

(async () => {
  // 0.0.0.0: Lambda containers reach the proxy through host.docker.internal.
  const be = await startBackend({ backend: job.backend, ddbLocal: job.ddb_local, tables: job.tables, bindHost: '0.0.0.0' });
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
          ctx.returned = true;
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
