'use strict';
// Verification-line execution engine (driven by ../verify.py).
//
//   node controller.js <job.json>
//
// Starts dynalite (an in-memory DynamoDB) behind a recording proxy, then runs
// each test case against the real handler:
//   - every SDK request from the handler goes to the proxy (the handler's
//     AWS_ENDPOINT_URL_DYNAMODB / _SQS point at it); the proxy records the
//     call, applies the case's fault rules, forwards DynamoDB calls to
//     dynalite and answers SQS SendMessage itself (recording the message);
//   - before each case every table is recreated and seeded with the case's
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
const crypto = require('crypto');
const { spawn, execFileSync } = require('child_process');
const dynalite = require('dynalite');
const { DynamoDBClient, CreateTableCommand, DeleteItemCommand, ScanCommand } = require('@aws-sdk/client-dynamodb');
const { DynamoDBDocumentClient, PutCommand } = require('@aws-sdk/lib-dynamodb');
const { unmarshall } = require('@aws-sdk/util-dynamodb');

const job = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const OUT = job.out_dir;
fs.mkdirSync(OUT, { recursive: true });
const log = (...a) => process.stderr.write('[controller] ' + a.join(' ') + '\n');

// ---------------------------------------------------------------------------
// dynalite + recording/fault proxy
// ---------------------------------------------------------------------------

let ctx = null; // { calls, messages, rules: [{rule, seen}] } for the running case

function listen(server, host) {
  return new Promise((resolve) => server.listen(0, host, () => resolve(server.address().port)));
}

function faultResponse(kind, service) {
  const ddb = {
    throttle: [400, 'com.amazonaws.dynamodb.v20120810#ProvisionedThroughputExceededException', 'Rate of requests exceeds the allowed throughput (injected)'],
    error: [500, 'com.amazonaws.dynamodb.v20120810#InternalServerError', 'Internal server error (injected)'],
    validation: [400, 'com.amazon.coral.validate#ValidationException', 'Validation error (injected)'],
    conditional: [400, 'com.amazonaws.dynamodb.v20120810#ConditionalCheckFailedException', 'The conditional request failed (injected)'],
  };
  const sqs = {
    throttle: [400, 'com.amazonaws.sqs#RequestThrottled', 'Request throttled (injected)'],
    error: [500, 'com.amazonaws.sqs#InternalError', 'Internal error (injected)'],
    validation: [400, 'com.amazonaws.sqs#InvalidParameterValue', 'Invalid parameter (injected)'],
    conditional: [400, 'com.amazonaws.sqs#InvalidParameterValue', 'Invalid parameter (injected)'],
  };
  return (service === 'sqs' ? sqs : ddb)[kind];
}

function matchFault(call) {
  if (!ctx) return null;
  for (const r of ctx.rules) {
    const m = r.rule.match || {};
    if (m.op && m.op !== call.op) continue;
    if (m.table && m.table !== call.table && m.table !== call.queue) continue;
    r.seen += 1;
    const nth = r.rule.nth === undefined ? 'all' : r.rule.nth;
    const hit = nth === 'all' || (Array.isArray(nth) ? nth.includes(r.seen) : nth === r.seen);
    if (hit) return r.rule.kind;
  }
  return null;
}

function summarize(op, req) {
  // Just enough of each request to compare call sets across versions.
  const s = {};
  if (req.TableName) s.table = req.TableName;
  if (req.IndexName) s.index = req.IndexName;
  if (req.Key) s.key = unmarshall(req.Key);
  if (req.Item) s.item = unmarshall(req.Item);
  if (req.UpdateExpression) s.update = req.UpdateExpression;
  if (req.ConditionExpression) s.condition = req.ConditionExpression;
  if (req.KeyConditionExpression) s.key_condition = req.KeyConditionExpression;
  if (req.ExpressionAttributeValues) s.values = unmarshall(req.ExpressionAttributeValues);
  if (req.RequestItems) s.request_items = Object.fromEntries(Object.entries(req.RequestItems).map(([t, v]) => [t, Array.isArray(v) ? v.length : (v.Keys || []).length]));
  if (req.TransactItems) s.transact_items = req.TransactItems.length;
  if (req.QueueUrl) s.queue = req.QueueUrl.split('/').pop();
  return s;
}

function sqsReply(op, req) {
  const md5 = (s) => crypto.createHash('md5').update(s).digest('hex');
  if (op === 'SendMessage') {
    ctx && ctx.messages.push({ queue: req.QueueUrl.split('/').pop(), body: req.MessageBody, attributes: req.MessageAttributes || null });
    return { MessageId: crypto.randomUUID(), MD5OfMessageBody: md5(req.MessageBody) };
  }
  if (op === 'SendMessageBatch') {
    const ok = req.Entries.map((e) => {
      ctx && ctx.messages.push({ queue: req.QueueUrl.split('/').pop(), body: e.MessageBody, attributes: e.MessageAttributes || null });
      return { Id: e.Id, MessageId: crypto.randomUUID(), MD5OfMessageBody: md5(e.MessageBody) };
    });
    return { Successful: ok, Failed: [] };
  }
  return null;
}

async function startBackends() {
  const dyn = dynalite({ createTableMs: 0, deleteTableMs: 0, updateTableMs: 0 });
  const dynPort = await listen(dyn, '127.0.0.1');

  const proxy = http.createServer((req, res) => {
    const chunks = [];
    req.on('data', (c) => chunks.push(c));
    req.on('end', () => {
      const raw = Buffer.concat(chunks);
      const target = req.headers['x-amz-target'] || '';
      const [prefix, op] = target.split('.');
      const service = prefix === 'AmazonSQS' ? 'sqs' : prefix.startsWith('DynamoDB') ? 'dynamodb' : 'unknown';
      let body = {};
      try { body = raw.length ? JSON.parse(raw.toString('utf8')) : {}; } catch (e) { /* non-JSON protocol */ }
      const call = { op: op || '(unknown)', service, ...summarize(op, body) };
      if (service === 'unknown') {
        call.error = 'unsupported protocol ' + (req.headers['content-type'] || '');
        ctx && ctx.calls.push(call);
        res.writeHead(400, { 'content-type': 'application/x-amz-json-1.0' });
        return res.end(JSON.stringify({ __type: 'M2SVerify#Unsupported', message: 'verify proxy: only JSON-protocol DynamoDB/SQS calls are supported' }));
      }
      const fault = matchFault(call);
      if (fault) call.fault = fault;
      ctx && ctx.calls.push(call);
      if (fault === 'network') { req.socket.destroy(); return; }
      if (fault) {
        const [code, type, message] = faultResponse(fault, service);
        res.writeHead(code, { 'content-type': 'application/x-amz-json-1.0', 'x-amzn-query-error': type.split('#').pop() + ';Sender' });
        return res.end(JSON.stringify({ __type: type, message }));
      }
      if (service === 'sqs') {
        const reply = sqsReply(op, body);
        if (!reply) {
          res.writeHead(400, { 'content-type': 'application/x-amz-json-1.0' });
          return res.end(JSON.stringify({ __type: 'com.amazonaws.sqs#UnsupportedOperation', message: 'verify proxy: SQS ' + op + ' not mocked' }));
        }
        res.writeHead(200, { 'content-type': 'application/x-amz-json-1.0' });
        return res.end(JSON.stringify(reply));
      }
      const fwd = http.request({ host: '127.0.0.1', port: dynPort, method: req.method, path: req.url, headers: { ...req.headers, host: '127.0.0.1:' + dynPort } }, (up) => {
        const out = [];
        up.on('data', (c) => out.push(c));
        up.on('end', () => {
          const buf = Buffer.concat(out);
          if (up.statusCode >= 400) {
            try { call.backend_error = JSON.parse(buf.toString('utf8')).__type; } catch (e) { call.backend_error = String(up.statusCode); }
          }
          res.writeHead(up.statusCode, up.headers);
          res.end(buf);
        });
      });
      fwd.on('error', (e) => { res.writeHead(500); res.end(String(e)); });
      fwd.end(raw);
    });
  });
  const proxyPort = await listen(proxy, '0.0.0.0');

  const admin = new DynamoDBClient({ region: 'us-east-1', endpoint: 'http://127.0.0.1:' + dynPort, credentials: { accessKeyId: 'x', secretAccessKey: 'x' } });
  return { dyn, proxy, proxyPort, admin, doc: DynamoDBDocumentClient.from(admin) };
}

async function createTables(be) {
  for (const t of job.tables) await be.admin.send(new CreateTableCommand(t.create));
}

// Tables are created once; between cases every item is deleted. (Deleting
// and recreating tables races dynalite's asynchronous DeleteTable.)
async function resetTables(be, fixtures) {
  for (const t of job.tables) {
    const keys = t.create.KeySchema.map((k) => k.AttributeName);
    let start;
    do {
      const r = await be.admin.send(new ScanCommand({ TableName: t.name, ExclusiveStartKey: start }));
      for (const item of r.Items) {
        const Key = Object.fromEntries(keys.map((k) => [k, item[k]]));
        await be.admin.send(new DeleteItemCommand({ TableName: t.name, Key }));
      }
      start = r.LastEvaluatedKey;
    } while (start);
  }
  for (const [table, items] of Object.entries(fixtures || {})) {
    for (const item of items) await be.doc.send(new PutCommand({ TableName: table, Item: item }));
  }
}

async function dumpTables(be) {
  const out = {};
  for (const t of job.tables) {
    const items = [];
    let start;
    do {
      const r = await be.admin.send(new ScanCommand({ TableName: t.name, ExclusiveStartKey: start }));
      items.push(...r.Items.map((i) => unmarshall(i)));
      start = r.LastEvaluatedKey;
    } while (start);
    out[t.name] = items;
  }
  return out;
}

// ---------------------------------------------------------------------------
// Invokers
// ---------------------------------------------------------------------------

const DOCKER = process.env.M2S_DOCKER || 'docker';
const RIE_IMAGE = job.rie_image || 'public.ecr.aws/lambda/nodejs:22-x86_64';
const COV_FLUSH = path.join(__dirname, 'cov-flush.js');
const LOCAL_INVOKER = path.join(__dirname, 'local-invoker.js');

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
    if (job.coverage) { this.env.NODE_V8_COVERAGE = '/tmp/m2s-cov'; this.env.NODE_OPTIONS = '--require /var/task/.m2s-cov-flush.js'; }
  }
  docker(args, opts = {}) { return execFileSync(DOCKER, args, { stdio: ['ignore', 'pipe', 'pipe'], ...opts }).toString(); }
  async start() {
    try { this.docker(['rm', '-f', this.name]); } catch (e) { /* not there */ }
    const envArgs = Object.entries(this.env).flatMap(([k, v]) => ['-e', `${k}=${v}`]);
    this.docker(['create', '--name', this.name, '-p', `127.0.0.1:${this.port}:8080`, '--add-host', 'host.docker.internal:host-gateway', ...envArgs, RIE_IMAGE, this.fn.handler]);
    this.docker(['cp', this.fn.dir + path.sep + '.', `${this.name}:/var/task`]);
    if (job.coverage) this.docker(['cp', COV_FLUSH, `${this.name}:/var/task/.m2s-cov-flush.js`]);
    this.docker(['start', this.name]);
    await sleep(1000);
  }
  async invoke(event) {
    const t0 = Date.now();
    const r = await postJson(this.port, '/2015-03-31/functions/function/invocations', event, (job.invoke_timeout_s || 60) * 1000);
    let payload = null;
    try { payload = JSON.parse(r.body); } catch (e) { payload = r.body; }
    const isError = payload && typeof payload === 'object' && !Array.isArray(payload) && 'errorType' in payload && 'errorMessage' in payload && Object.keys(payload).every((k) => ['errorType', 'errorMessage', 'trace', 'stackTrace'].includes(k));
    return isError ? { error: { type: payload.errorType, message: payload.errorMessage }, ms: Date.now() - t0 } : { result: payload, ms: Date.now() - t0 };
  }
  async stop(covDir) {
    if (job.coverage) {
      await sleep(600); // cov-flush writes every 200ms
      fs.mkdirSync(covDir, { recursive: true });
      try { this.docker(['cp', `${this.name}:/tmp/m2s-cov/.`, covDir]); } catch (e) { log('coverage copy failed for', this.fn.name, e.message); }
    }
    if (!job.keep_containers) { try { this.docker(['rm', '-f', this.name]); } catch (e) { /* ignore */ } }
  }
  logs() { try { return this.docker(['logs', this.name], { stdio: ['ignore', 'pipe', 'pipe'] }); } catch (e) { return ''; } }
}

class LocalInvoker {
  constructor(fn, proxyPort) {
    this.fn = fn;
    this.env = { ...process.env, ...baseEnv(fn, '127.0.0.1', proxyPort) };
    delete this.env.NODE_OPTIONS;
    for (const k of ['HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy']) delete this.env[k];
  }
  async start(covDir) {
    if (job.coverage) { fs.mkdirSync(covDir, { recursive: true }); this.env.NODE_V8_COVERAGE = covDir; }
    this.proc = spawn(process.execPath, [LOCAL_INVOKER, this.fn.dir, this.fn.handler], { env: this.env, stdio: ['pipe', 'pipe', 'pipe'] });
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
  logs() { return this.stderr; }
}

// ---------------------------------------------------------------------------
// Coverage (V8 -> istanbul via c8)
// ---------------------------------------------------------------------------

async function convertCoverage(fn, covDir, reportDir) {
  const files = fs.existsSync(covDir) ? fs.readdirSync(covDir).filter((f) => f.endsWith('.json')) : [];
  if (!files.length) return { error: 'no V8 coverage files were written' };
  if (job.mode === 'rie') {
    // Container paths -> build dir on this machine, so c8 can read the sources.
    const hostUrl = 'file:///' + fn.dir.replace(/\\/g, '/').replace(/^\/+/, '') + '/';
    for (const f of files) {
      const p = path.join(covDir, f);
      const data = JSON.parse(fs.readFileSync(p, 'utf8'));
      for (const r of data.result) r.url = r.url.replace('file:///var/task/', hostUrl);
      data['source-map-cache'] = {};
      fs.writeFileSync(p, JSON.stringify(data));
    }
  }
  const Report = require('c8/lib/report');
  const report = Report({
    reporter: ['json', 'json-summary'], reportsDirectory: reportDir, tempDirectory: covDir,
    include: [fn.dir.replace(/\\/g, '/') + '/**'], exclude: ['**/node_modules/**', '**/.m2s-cov-flush.js'], excludeNodeModules: true,
    all: false, resolve: '', omitRelative: true, allowExternal: true, src: [fn.dir],
  });
  await report.run();
  return { report_dir: reportDir };
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

(async () => {
  const be = await startBackends();
  await createTables(be);
  log('proxy on', be.proxyPort, 'mode', job.mode);
  const results = { mode: job.mode, cases: [], functions: {} };
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
    for (const c of cases) {
      const rec = { id: c.id, function: fn.name, run_key: c.run_key };
      try {
        await resetTables(be, c.fixtures);
        ctx = { calls: [], messages: [], rules: (c.faults || []).map((rule) => ({ rule, seen: 0 })) };
        if (c.gateway_response) {
          rec.gateway = true;
          rec.result = c.gateway_response;
        } else {
          const out = await inv.invoke(c.event);
          Object.assign(rec, out);
        }
        rec.calls = ctx.calls;
        rec.messages = ctx.messages;
        ctx = null;
        rec.tables_after = await dumpTables(be);
      } catch (e) {
        ctx = null;
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
  be.proxy.close(); be.dyn.close();
  log('done', results.cases.length, 'cases');
  process.exit(0);
})().catch((e) => { log('FATAL', e.stack || e); process.exit(2); });
