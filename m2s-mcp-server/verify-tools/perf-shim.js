'use strict';
// Optimization-line measurement shim (PERF_OPTIMIZATION_DESIGN.md §2.1).
//
// Preloaded into the handler's process (NODE_OPTIONS=--require in an RIE
// container, or required by perf-harness.js locally). It patches
// NodeHttpHandler.prototype.handle -- the last step of every AWS SDK call --
// so the SDK's real serialize / sign / retry / deserialize work still runs,
// but the network round trip is replaced by:
//   - a fixed wait of SHIM_LATENCY_MS (the v1 latency model), then
//   - a response computed from the benchmark's table data (SHIM_FIXTURES):
//     GetItem / BatchGetItem look items up by key, Query filters on the key
//     condition, Scan returns the whole table; writes succeed without
//     changing the data (every invocation sees the same data volume).
// Every call is recorded with start/end timestamps (to compute sequential
// depth) and an estimate of the capacity units it would consume.
//
// Env: SHIM_FIXTURES (JSON file), SHIM_LATENCY_MS, SHIM_LOG=1 (one JSON line
// per call on stderr), SHIM_FN_DIR (where the handler's node_modules are;
// default /var/task).
//
// A write that touches an item of an ongoing transaction is rejected the way
// real DynamoDB does (tx-conflicts.js; call.conflict = true). A request is
// ongoing while it waits out its latency, so conflicts only show at a
// non-zero SHIM_LATENCY_MS.
//
// Known simplifications (reported by the tools): no 1MB pagination, no
// FilterExpression, latency independent of payload size and operation.

const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const { Readable } = require('stream');
// In an RIE container the shim is copied in as /var/task/.m2s-perf-shim.js
// next to .m2s-tx-conflicts.js; locally it runs from verify-tools/.
const TX = require(fs.existsSync(path.join(__dirname, '.m2s-tx-conflicts.js')) ? './.m2s-tx-conflicts.js' : './tx-conflicts.js');

process.env.AWS_ACCESS_KEY_ID = process.env.AWS_ACCESS_KEY_ID || 'AKIDPERF';
process.env.AWS_SECRET_ACCESS_KEY = process.env.AWS_SECRET_ACCESS_KEY || 'perf';
process.env.AWS_REGION = process.env.AWS_REGION || 'us-east-1';

const FN_DIR = process.env.SHIM_FN_DIR || '/var/task';
const LOG = process.env.SHIM_LOG === '1';
const state = {
  latency: Number(process.env.SHIM_LATENCY_MS || 0),
  tables: {},
  calls: [],
  maxRss: 0,
  tx: new TX.Tracker(),
};
if (process.env.SHIM_FIXTURES) state.tables = JSON.parse(fs.readFileSync(process.env.SHIM_FIXTURES, 'utf8')).tables || {};

// -- DynamoDB JSON ----------------------------------------------------------
function marshall(v) {
  if (v === null || v === undefined) return { NULL: true };
  if (typeof v === 'string') return { S: v };
  if (typeof v === 'number') return { N: String(v) };
  if (typeof v === 'boolean') return { BOOL: v };
  if (Array.isArray(v)) return { L: v.map(marshall) };
  return { M: Object.fromEntries(Object.entries(v).map(([k, x]) => [k, marshall(x)])) };
}
function unmarshall(a) {
  if (!a || typeof a !== 'object') return a;
  if ('S' in a) return a.S;
  if ('N' in a) return Number(a.N);
  if ('BOOL' in a) return a.BOOL;
  if ('NULL' in a) return null;
  if ('L' in a) return a.L.map(unmarshall);
  if ('M' in a) return Object.fromEntries(Object.entries(a.M).map(([k, x]) => [k, unmarshall(x)]));
  if ('SS' in a) return a.SS;
  if ('NS' in a) return a.NS.map(Number);
  return a;
}
const marshallItem = (o) => Object.fromEntries(Object.entries(o).map(([k, v]) => [k, marshall(v)]));
const unmarshallItem = (o) => Object.fromEntries(Object.entries(o || {}).map(([k, v]) => [k, unmarshall(v)]));
// Approximate DynamoDB item size: attribute names + JSON-encoded values.
const itemSize = (o) => Object.entries(o || {}).reduce((n, [k, v]) => n + Buffer.byteLength(k) + Buffer.byteLength(JSON.stringify(v)), 0);

// -- Table model --------------------------------------------------------------
function table(name) {
  const t = state.tables[name];
  if (!t) throw new Error('perf-shim: no fixture table ' + name + ' (known: ' + Object.keys(state.tables).join(', ') + ')');
  return t;
}
const keyOf = (t, item, keyAttrs) => (keyAttrs || t.key).map((k) => JSON.stringify(item[k])).join('|');
function findByKey(t, key) {
  const want = keyOf(t, key);
  return t.items.find((i) => keyOf(t, i) === want) || null;
}
function resolveName(n, names) { return n.startsWith('#') ? (names || {})[n] : n; }
// Items matching an AND-list of comparisons / begins_with / BETWEEN (the
// subset KeyConditionExpression allows; also used for simple FilterExpressions).
function matchAll(items, expr, vals, names) {
  if (!expr) return items;
  let e = String(expr).trim();
  // boto3 wraps combined conditions: "(#n0 = :v0 AND #n1 = :v1)"
  while (e.startsWith('(') && e.endsWith(')') && !/\)[^()]*\(/.test(e.slice(1, -1).replace(/\([^()]*\)/g, ''))) e = e.slice(1, -1).trim();
  const conds = e.split(/\s+AND\s+(?![^()]*\))/i);
  for (const c of conds) {
    let m;
    if ((m = /^\s*\(?\s*([#\w]+)\s*(=|<=|>=|<|>)\s*(:\w+)\s*\)?\s*$/.exec(c))) {
      const a = resolveName(m[1], names), v = vals[m[3]], op = m[2];
      items = items.filter((i) => (op === '=' ? i[a] === v : op === '<' ? i[a] < v : op === '<=' ? i[a] <= v : op === '>' ? i[a] > v : i[a] >= v));
    } else if ((m = /begins_with\s*\(\s*([#\w]+)\s*,\s*(:\w+)\s*\)/i.exec(c))) {
      const a = resolveName(m[1], names);
      items = items.filter((i) => typeof i[a] === 'string' && i[a].startsWith(vals[m[2]]));
    } else if ((m = /([#\w]+)\s+BETWEEN\s+(:\w+)\s+AND\s+(:\w+)/i.exec(c))) {
      const a = resolveName(m[1], names);
      items = items.filter((i) => i[a] >= vals[m[2]] && i[a] <= vals[m[3]]);
    }
  }
  return items;
}

// [read, returned]: DynamoDB bills what the key condition (or a Scan) reads,
// before a FilterExpression drops rows from the response.
function queryItems(req) {
  const t = table(req.TableName);
  const keyAttrs = req.IndexName ? (t.indexes || {})[req.IndexName] : t.key;
  if (!keyAttrs) throw new Error('perf-shim: unknown index ' + req.IndexName + ' on ' + req.TableName);
  const vals = unmarshallItem(req.ExpressionAttributeValues);
  const names = req.ExpressionAttributeNames;
  let items = matchAll(t.items.slice(), req.KeyConditionExpression, vals, names);
  const sk = keyAttrs[1];
  if (sk) items.sort((x, y) => (x[sk] < y[sk] ? -1 : x[sk] > y[sk] ? 1 : 0));
  if (req.ScanIndexForward === false) items.reverse();
  if (req.Limit) items = items.slice(0, req.Limit);
  return [items, matchAll(items, req.FilterExpression, vals, names)];
}

function scanItems(req) {
  const items = table(req.TableName).items;
  return [items, matchAll(items, req.FilterExpression, unmarshallItem(req.ExpressionAttributeValues), req.ExpressionAttributeNames)];
}

// -- Capacity units (on-demand request units) ---------------------------------
const rru = (bytes, consistent) => Math.max(1, Math.ceil(bytes / 4096)) * (consistent ? 1 : 0.5);
const wru = (bytes) => Math.max(1, Math.ceil(bytes / 1024));

function respond(op, req) {
  const cu = { rru: 0, wru: 0, sqs_requests: 0 };
  let body;
  switch (op) {
    case 'GetItem': {
      const it = findByKey(table(req.TableName), unmarshallItem(req.Key));
      cu.rru = rru(itemSize(it), req.ConsistentRead);
      body = it ? { Item: marshallItem(it) } : {};
      break;
    }
    case 'BatchGetItem': {
      const Responses = {};
      for (const [tn, spec] of Object.entries(req.RequestItems)) {
        const t = table(tn);
        Responses[tn] = spec.Keys.map((k) => findByKey(t, unmarshallItem(k))).filter(Boolean).map((i) => { cu.rru += rru(itemSize(i), spec.ConsistentRead); return marshallItem(i); });
      }
      body = { Responses, UnprocessedKeys: {} };
      break;
    }
    case 'Query': {
      const [read, items] = queryItems(req);
      cu.rru = rru(read.reduce((n, i) => n + itemSize(i), 0), req.ConsistentRead);
      body = { Items: items.map(marshallItem), Count: items.length, ScannedCount: read.length };
      break;
    }
    case 'Scan': {
      const [read, items] = scanItems(req);
      cu.rru = rru(read.reduce((n, i) => n + itemSize(i), 0), req.ConsistentRead);
      body = { Items: items.map(marshallItem), Count: items.length, ScannedCount: read.length };
      break;
    }
    case 'PutItem':
      cu.wru = wru(itemSize(unmarshallItem(req.Item)));
      body = {};
      break;
    case 'UpdateItem':
    case 'DeleteItem': {
      const t = table(req.TableName);
      const key = unmarshallItem(req.Key);
      const old = findByKey(t, key);
      cu.wru = wru(Math.max(itemSize(old), itemSize(key) + itemSize(unmarshallItem(req.ExpressionAttributeValues))));
      const rv = req.ReturnValues;
      body = rv && rv !== 'NONE' ? { Attributes: marshallItem(old || key) } : {};
      break;
    }
    case 'BatchWriteItem':
      for (const reqs of Object.values(req.RequestItems)) for (const r of reqs) cu.wru += wru(itemSize(unmarshallItem((r.PutRequest || {}).Item || (r.DeleteRequest || {}).Key)));
      body = { UnprocessedItems: {} };
      break;
    case 'TransactWriteItems':
      for (const t of req.TransactItems) { const x = t.Put || t.Update || t.Delete || t.ConditionCheck; cu.wru += 2 * wru(itemSize(unmarshallItem(x.Item || x.Key))); }
      body = {};
      break;
    case 'SendMessage':
      cu.sqs_requests = Math.max(1, Math.ceil(Buffer.byteLength(req.MessageBody) / 65536));
      body = { MessageId: crypto.randomUUID(), MD5OfMessageBody: crypto.createHash('md5').update(req.MessageBody).digest('hex') };
      break;
    case 'SendMessageBatch':
      cu.sqs_requests = 1;
      body = { Successful: req.Entries.map((e) => ({ Id: e.Id, MessageId: crypto.randomUUID(), MD5OfMessageBody: crypto.createHash('md5').update(e.MessageBody).digest('hex') })), Failed: [] };
      break;
    default:
      throw new Error('perf-shim: no stub for ' + op);
  }
  return { body, cu };
}

// -- Patch every copy of NodeHttpHandler the handler could be using -----------
function findHandlerCopies(root) {
  // Walk node_modules trees only (package -> its own node_modules), so
  // nested copies of @smithy/node-http-handler are found without scanning
  // every package's dist/ directory.
  const out = [];
  const pkg = (p) => {
    if (path.basename(p) === 'node-http-handler' && path.basename(path.dirname(p)) === '@smithy') out.push(p);
    const nested = path.join(p, 'node_modules');
    if (fs.existsSync(nested)) nm(nested);
  };
  const nm = (dir) => {
    let entries;
    try { entries = fs.readdirSync(dir, { withFileTypes: true }); } catch (e) { return; }
    for (const e of entries) {
      if (!e.isDirectory() || e.name.startsWith('.')) continue;
      const p = path.join(dir, e.name);
      if (e.name.startsWith('@')) {
        for (const sub of fs.readdirSync(p, { withFileTypes: true })) if (sub.isDirectory()) pkg(path.join(p, sub.name));
      } else pkg(p);
    }
  };
  nm(path.join(root, 'node_modules'));
  return out;
}

async function handle(request) {
  const target = request.headers['x-amz-target'] || '';
  if (!target) throw new Error('perf-shim: non-JSON protocol request (' + request.headers['content-type'] + ')');
  const op = target.split('.').pop();
  const req = request.body ? JSON.parse(typeof request.body === 'string' ? request.body : Buffer.from(request.body).toString('utf8')) : {};
  const call = { op, table: req.TableName || (req.QueueUrl ? req.QueueUrl.split('/').pop() : undefined), start: performance.now() };
  state.calls.push(call);
  const part = TX.classify(op, req, (t) => (state.tables[t] || {}).key);
  const hit = part ? state.tx.conflicts(part) : [];
  let status = 200, body;
  if (hit.length) {
    // Rejected: the round trip and the capacity are still paid (a cancelled
    // transaction consumes capacity), nothing is written.
    call.conflict = true;
    if (state.latency) await new Promise((r) => setTimeout(r, state.latency));
    Object.assign(call, respond(op, req).cu);
    status = 400;
    body = TX.errorBody(part, hit);
  } else {
    const done = part ? state.tx.begin(part) : () => {};
    try {
      if (state.latency) await new Promise((r) => setTimeout(r, state.latency));
      const r = respond(op, req);
      body = r.body;
      Object.assign(call, r.cu);
    } finally {
      done();
    }
  }
  call.end = performance.now();
  const rss = process.memoryUsage().rss;
  if (rss > state.maxRss) state.maxRss = rss;
  if (LOG) process.stderr.write('[m2s-shim] ' + JSON.stringify({ ...call, max_rss_mb: Math.round(state.maxRss / 1048576) }) + '\n');
  const payload = Buffer.from(JSON.stringify(body));
  return { response: { statusCode: status, headers: { 'content-type': 'application/x-amz-json-1.0', 'content-length': String(payload.length) }, body: Readable.from([payload]) } };
}

const patched = [];
for (const dir of findHandlerCopies(FN_DIR)) {
  try {
    const mod = require(dir);
    if (mod.NodeHttpHandler && !mod.NodeHttpHandler.prototype.__m2sPatched) {
      mod.NodeHttpHandler.prototype.handle = handle;
      mod.NodeHttpHandler.prototype.__m2sPatched = true;
      patched.push(dir);
    }
  } catch (e) { /* not loadable */ }
}
if (!patched.length) process.stderr.write('[m2s-shim] WARNING: no @smithy/node-http-handler found under ' + FN_DIR + '\n');

module.exports = {
  state,
  patched,
  reset() { state.calls = []; },
  calls() { return state.calls.slice(); },
};
