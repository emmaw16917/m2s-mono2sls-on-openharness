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
//     dynalite and answers SQS SendMessage itself (recording the message),
//     and rejects requests that touch an item of an ongoing transaction the
//     way real DynamoDB does (tx-conflicts.js; call.conflict = 'emulated');
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
const TX = require('./tx-conflicts');

const job = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const OUT = job.out_dir;
fs.mkdirSync(OUT, { recursive: true });
const log = (...a) => process.stderr.write('[controller] ' + a.join(' ') + '\n');

// ---------------------------------------------------------------------------
// dynalite + recording/fault proxy
// ---------------------------------------------------------------------------

let ctx = null; // { calls, messages, rules: [{rule, seen}] } for the running case
const txTracker = new TX.Tracker(); // ongoing DynamoDB writes, for conflict emulation
const TX_HOLD_MS = 25;

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

// A call's identity: which item / message it is about, independent of call
// order -- the table key (from Key, or the key attributes of Item) or the
// queue + message body, with UUIDs blanked. Used to pin a fault to the same
// operation in two versions whose call order differs (run_equivalence diff).
const TABLE_KEYS = Object.fromEntries((job.tables || []).map((t) => [t.name, t.create.KeySchema.map((k) => k.AttributeName)]));
const UUID_RE = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/gi;
function stable(v) {
  if (Array.isArray(v)) return '[' + v.map(stable).join(',') + ']';
  if (v && typeof v === 'object') return '{' + Object.keys(v).sort().map((k) => JSON.stringify(k) + ':' + stable(v[k])).join(',') + '}';
  return JSON.stringify(v);
}
function identityOf(call) {
  let id = null;
  if (call.key) id = call.key;
  else if (call.item && TABLE_KEYS[call.table]) id = Object.fromEntries(TABLE_KEYS[call.table].map((k) => [k, call.item[k]]));
  else if (call.queue) id = { queue: call.queue, body: call.body };
  else if (call.key_condition) id = { key_condition: call.key_condition, values: call.values, index: call.index };
  else if (call.transact) id = call.transact.map((t) => ({ kind: t.kind, table: t.table,
    key: t.key || (t.item && TABLE_KEYS[t.table] ? Object.fromEntries(TABLE_KEYS[t.table].map((k) => [k, t.item[k]])) : null) }));
  return id === null ? '' : stable(id).replace(UUID_RE, '<uuid>');
}

// match.item: the call reads or writes an item whose attributes include these
// values (its Key / Item, or any item of a transaction) -- targets an
// operation by content, so the fault stays on it whatever the call order.
function touchesItem(call, want) {
  const items = [call.key, call.item];
  for (const t of call.transact || []) items.push(t.key, t.item);
  if (items.some((it) => it && Object.entries(want).every(([k, v]) => stable(it[k]) === stable(v)))) return true;
  // A Query has no Key: it targets the item collection whose key-condition
  // values include every wanted value (attribute names are placeholders there).
  if (call.key_condition && call.values) {
    const vals = Object.values(call.values).map(stable);
    return Object.values(want).every((v) => vals.includes(stable(v)));
  }
  return false;
}

function matchFault(call) {
  if (!ctx) return null;
  let kind = null;
  for (const r of ctx.rules) {
    const m = r.rule.match || {};
    if (m.op && m.op !== call.op) continue;
    if (m.table && m.table !== call.table && m.table !== call.queue && !(call.tables || []).includes(m.table)) continue;
    if (m.item && !touchesItem(call, m.item)) continue;
    if (m.identity !== undefined && m.identity !== call.identity) continue;
    // Every matching rule counts every matching call, even when an earlier
    // rule already fired -- otherwise later rules' "n-th call" counters fall
    // behind (a pinned rule for the 2nd attempt would never fire).
    r.seen += 1;
    const nth = r.rule.nth === undefined ? 'all' : r.rule.nth;
    const hit = nth === 'all' || (Array.isArray(nth) ? nth.includes(r.seen) : nth === r.seen);
    if (hit) r.fired += 1;
    if (hit && !kind) kind = r.rule.kind;
  }
  return kind;
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
  if (req.TransactItems) {
    s.transact_items = req.TransactItems.length;
    s.transact = req.TransactItems.map((t) => {
      const [kind, v] = Object.entries(t)[0];
      const it = { kind, table: v.TableName };
      if (v.Key) it.key = unmarshall(v.Key);
      if (v.Item) it.item = unmarshall(v.Item);
      if (v.UpdateExpression) it.update = v.UpdateExpression;
      if (v.ExpressionAttributeValues) it.values = unmarshall(v.ExpressionAttributeValues);
      return it;
    });
    s.tables = [...new Set(s.transact.map((t) => t.table))];
  }
  if (req.QueueUrl) s.queue = req.QueueUrl.split('/').pop();
  if (op === 'SendMessage') s.body = req.MessageBody;
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

// DynamoDB backend: AWS's DynamoDB Local in a container when Docker is
// available (job.backend = "ddblocal"), dynalite in-process otherwise (no
// transactions -- verify.py reports that as a limitation).
let ddbContainer = null;
async function startDdbLocal() {
  const spec = job.ddb_local;
  const name = 'm2sv-ddb-' + process.pid;
  try { execFileSync(DOCKER, ['rm', '-f', name], { stdio: 'ignore' }); } catch (e) { /* none */ }
  execFileSync(DOCKER, ['run', '-d', '--name', name, '-p', '127.0.0.1::8000', ...(spec.entrypoint ? ['--entrypoint', spec.entrypoint] : []), spec.image, ...spec.args], { stdio: ['ignore', 'pipe', 'pipe'], env: { ...process.env, MSYS_NO_PATHCONV: '1' } });
  ddbContainer = name;
  const firstLine = execFileSync(DOCKER, ['port', name, '8000']).toString().trim().split(String.fromCharCode(10))[0].trim();
  const port = Number(firstLine.split(':').pop());
  for (let i = 0; i < 120; i++) {
    const ok = await new Promise((r) => http.get({ host: '127.0.0.1', port, path: '/' }, (res) => { res.resume(); r(true); }).on('error', () => r(false)));
    if (ok) return { port, close: () => { try { execFileSync(DOCKER, ['rm', '-f', name], { stdio: 'ignore' }); } catch (e) { /* ignore */ } } };
    await sleep(250);
  }
  throw new Error('DynamoDB Local did not start within 30s (' + spec.image + ')');
}

async function startBackends() {
  let dyn, dynPort;
  if (job.backend === 'ddblocal') {
    const d = await startDdbLocal();
    dynPort = d.port;
    dyn = { close: d.close };
  } else {
    dyn = dynalite({ createTableMs: 0, deleteTableMs: 0, updateTableMs: 0 });
    dynPort = await listen(dyn, '127.0.0.1');
  }

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
      call.identity = identityOf(call);
      if (ctx) {
        ctx.inflight += 1;
        ctx.last = Date.now();
        if (ctx.returned) call.after_return = true;
        const c0 = ctx;
        res.on('close', () => { c0.inflight -= 1; c0.last = Date.now(); });
      }
      if (ctx) {
        const k = call.op + '|' + (call.table || call.queue || '') + '|' + call.identity;
        ctx.seen[k] = (ctx.seen[k] || 0) + 1;
        call.occurrence = ctx.seen[k];
      }
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
      if (fault === 'tx_conflict' && service === 'dynamodb') {
        // What real DynamoDB returns when an overlapping transaction is in
        // flight: the transaction is cancelled; the conflicting item's reason
        // is TransactionConflict (DynamoDB Local never produces this).
        let body2;
        if (op === 'TransactWriteItems' || op === 'TransactGetItems') {
          const n = (body.TransactItems || []).length;
          const at = n > 1 ? 1 : 0;
          const reasons = Array.from({ length: n }, (_, i) => (i === at ? { Code: 'TransactionConflict', Message: 'Transaction is ongoing for the item (injected)' } : { Code: 'None' }));
          body2 = { __type: 'com.amazonaws.dynamodb.v20120810#TransactionCanceledException',
                    message: 'Transaction cancelled, please refer cancellation reasons for specific reasons [' + reasons.map((r) => r.Code).join(', ') + ']',
                    CancellationReasons: reasons };
        } else {
          body2 = { __type: 'com.amazonaws.dynamodb.v20120810#TransactionConflictException', message: 'Transaction is ongoing for the item (injected)' };
        }
        res.writeHead(400, { 'content-type': 'application/x-amz-json-1.0' });
        return res.end(JSON.stringify(body2));
      }
      if (fault) {
        const [code, type, message] = faultResponse(fault, service);
        res.writeHead(code, { 'content-type': 'application/x-amz-json-1.0', 'x-amzn-query-error': type.split('#').pop() + ';Sender' });
        return res.end(JSON.stringify({ __type: type, message }));
      }
      // Emulated transaction conflicts (tx-conflicts.js): DynamoDB Local runs
      // transactions one at a time, real DynamoDB rejects a request that
      // touches an item of an ongoing transaction. The rejected call changed
      // nothing, so it is recorded as a backend error, not as a fault (faults
      // are what run_equivalence pins between versions).
      const part = service === 'dynamodb' ? TX.classify(op, body, (t) => TABLE_KEYS[t]) : null;
      if (part) {
        const hit = txTracker.conflicts(part);
        if (hit.length) {
          const err = TX.errorBody(part, hit);
          call.conflict = 'emulated';
          call.backend_error = err.__type;
          res.writeHead(400, { 'content-type': 'application/x-amz-json-1.0' });
          return res.end(JSON.stringify(err));
        }
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
      // A write transaction stays ongoing for at least TX_HOLD_MS (a real one
      // takes tens of ms), so overlapping requests sent concurrently are
      // detected reliably instead of depending on DynamoDB Local's speed.
      const arrived = Date.now();
      const done = part ? txTracker.begin(part) : () => {};
      const hold = part && part.cls === 'txw' ? TX_HOLD_MS : 0;
      const fwd = http.request({ host: '127.0.0.1', port: dynPort, method: req.method, path: req.url, headers: { ...req.headers, host: '127.0.0.1:' + dynPort } }, (up) => {
        const out = [];
        up.on('data', (c) => out.push(c));
        up.on('end', () => {
          const buf = Buffer.concat(out);
          if (up.statusCode >= 400) {
            try { call.backend_error = JSON.parse(buf.toString('utf8')).__type; } catch (e) { call.backend_error = String(up.statusCode); }
          }
          setTimeout(() => {
            done();
            res.writeHead(up.statusCode, up.headers);
            res.end(buf);
          }, Math.max(0, arrived + hold - Date.now()));
        });
      });
      fwd.on('error', (e) => { done(); res.writeHead(500); res.end(String(e)); });
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
  const be = await startBackends();
  await createTables(be);
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
    for (const c of cases) {
      const rec = { id: c.id, function: fn.name, run_key: c.run_key };
      try {
        await resetTables(be, c.fixtures);
        ctx = { calls: [], messages: [], seen: {}, inflight: 0, last: Date.now(), returned: false,
                rules: (c.faults || []).map((rule) => ({ rule, seen: 0, fired: 0 })) };
        if (c.gateway_response) {
          rec.gateway = true;
          rec.result = c.gateway_response;
        } else {
          const out = await inv.invoke(c.event);
          Object.assign(rec, out);
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
})().catch((e) => {
  log('FATAL', e.stack || e);
  if (ddbContainer) { try { execFileSync(DOCKER, ['rm', '-f', ddbContainer], { stdio: 'ignore' }); } catch (x) { /* ignore */ } }
  process.exit(2);
});
