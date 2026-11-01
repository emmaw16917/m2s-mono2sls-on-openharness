'use strict';
// DynamoDB backend behind a recording / fault-injecting proxy, shared by the
// verification engine (controller.js: Lambda handlers) and, from v2 N1.3, by
// run_monolith (monolith-run.js: the monolith itself), so both sides of a
// comparison run on the same backend with the same recording
// (PIPELINE_V2_PLAN.md §4.3, V2P-11).
//
//   const { startBackend } = require('./ddb-proxy');
//   const be = await startBackend({ backend, ddbLocal, tables, bindHost });
//   await be.createTables();
//   await be.resetTables(fixtures);           // empty every table, seed fixtures
//   const ctx = be.beginCase(faultRules);     // record calls of this case
//   ...  point the code under test at http://<host>:<be.proxyPort>
//   be.endCase();                              // ctx.calls / ctx.messages / ctx.rules
//   const tables = await be.dumpTables();
//   be.close();
//
// - backend "ddblocal": AWS's DynamoDB Local in a container (opts.ddbLocal =
//   {image, args, entrypoint}); otherwise dynalite in-process (no transactions);
// - the proxy records every call (summarize / identityOf), applies the case's
//   fault rules (matchFault), forwards DynamoDB calls to the backend, answers
//   SQS SendMessage itself (recording the message), and rejects requests that
//   touch an item of an ongoing transaction the way real DynamoDB does
//   (tx-conflicts.js; call.conflict = 'emulated').

const http = require('http');
const crypto = require('crypto');
const { execFileSync } = require('child_process');
const dynalite = require('dynalite');
const { DynamoDBClient, CreateTableCommand, DeleteItemCommand, ScanCommand } = require('@aws-sdk/client-dynamodb');
const { DynamoDBDocumentClient, PutCommand } = require('@aws-sdk/lib-dynamodb');
const { unmarshall } = require('@aws-sdk/util-dynamodb');
const TX = require('./tx-conflicts');

const DOCKER = process.env.M2S_DOCKER || 'docker';
const TX_HOLD_MS = 25;
const READ_OPS = new Set(['GetItem', 'BatchGetItem', 'Query', 'Scan']);
const UUID_RE = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/gi;

function sleep(ms) { return new Promise((r) => setTimeout(r, ms)); }

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

function stable(v) {
  if (Array.isArray(v)) return '[' + v.map(stable).join(',') + ']';
  if (v && typeof v === 'object') return '{' + Object.keys(v).sort().map((k) => JSON.stringify(k) + ':' + stable(v[k])).join(',') + '}';
  return JSON.stringify(v);
}

// A call's identity: which item / message it is about, independent of call
// order -- the table key (from Key, or the key attributes of Item) or the
// queue + message body, with UUIDs blanked. Used to pin a fault to the same
// operation in two versions whose call order differs (run_equivalence diff).
function identityOf(call, tableKeys) {
  let id = null;
  if (call.key) id = call.key;
  else if (call.item && tableKeys[call.table]) id = Object.fromEntries(tableKeys[call.table].map((k) => [k, call.item[k]]));
  else if (call.queue) id = { queue: call.queue, body: call.body };
  else if (call.key_condition) id = { key_condition: call.key_condition, values: call.values, index: call.index };
  else if (call.transact) id = call.transact.map((t) => ({ kind: t.kind, table: t.table,
    key: t.key || (t.item && tableKeys[t.table] ? Object.fromEntries(tableKeys[t.table].map((k) => [k, t.item[k]])) : null) }));
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

function matchFault(ctx, call) {
  if (!ctx) return null;
  let kind = null;
  for (const r of ctx.rules) {
    const m = r.rule.match || {};
    // "@read": any read operation -- a fault on "the read of this table",
    // whichever operation the implementation uses (contract 2).
    if (m.op === '@read' ? !READ_OPS.has(call.op) : m.op && m.op !== call.op) continue;
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
  if (req.ExpressionAttributeNames) s.names = req.ExpressionAttributeNames;
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
      if (v.ExpressionAttributeNames) it.names = v.ExpressionAttributeNames;
      return it;
    });
    s.tables = [...new Set(s.transact.map((t) => t.table))];
  }
  if (req.QueueUrl) s.queue = req.QueueUrl.split('/').pop();
  if (op === 'SendMessage') s.body = req.MessageBody;
  return s;
}

function sqsReply(ctx, op, req) {
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

// Containers started by startDdbLocal, for cleanup after a fatal error.
const containers = new Set();

function removeContainer(name) {
  try { execFileSync(DOCKER, ['rm', '-f', name], { stdio: 'ignore' }); } catch (e) { /* not there */ }
  containers.delete(name);
}

function cleanup() {
  for (const name of [...containers]) removeContainer(name);
}

async function startDdbLocal(spec) {
  const name = 'm2sv-ddb-' + process.pid;
  removeContainer(name);
  execFileSync(DOCKER, ['run', '-d', '--name', name, '-p', '127.0.0.1::8000', ...(spec.entrypoint ? ['--entrypoint', spec.entrypoint] : []), spec.image, ...spec.args], { stdio: ['ignore', 'pipe', 'pipe'], env: { ...process.env, MSYS_NO_PATHCONV: '1' } });
  containers.add(name);
  const firstLine = execFileSync(DOCKER, ['port', name, '8000']).toString().trim().split(String.fromCharCode(10))[0].trim();
  const port = Number(firstLine.split(':').pop());
  // 90s: the localstack image's JVM sometimes takes > 30s to bind under load
  // (two R5 rollout runs on 2026-10-03 failed at 30s).
  for (let i = 0; i < 360; i++) {
    const ok = await new Promise((r) => http.get({ host: '127.0.0.1', port, path: '/' }, (res) => { res.resume(); r(true); }).on('error', () => r(false)));
    if (ok) return { port, close: () => removeContainer(name) };
    await sleep(250);
  }
  throw new Error('DynamoDB Local did not start within 90s (' + spec.image + ')');
}

// opts: backend ("ddblocal" | anything else = dynalite), ddbLocal (image spec),
// tables ([{name, create}]), bindHost for the proxy ("0.0.0.0" so Lambda
// containers reach it through host.docker.internal; default "127.0.0.1").
async function startBackend(opts) {
  const tables = opts.tables || [];
  const tableKeys = Object.fromEntries(tables.map((t) => [t.name, t.create.KeySchema.map((k) => k.AttributeName)]));
  const txTracker = new TX.Tracker(); // ongoing DynamoDB writes, for conflict emulation
  let ctx = null; // { calls, messages, seen, inflight, last, returned, rules: [{rule, seen, fired}] } of the running case

  let dyn, dynPort;
  if (opts.backend === 'ddblocal') {
    const d = await startDdbLocal(opts.ddbLocal);
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
      call.identity = identityOf(call, tableKeys);
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
      const fault = matchFault(ctx, call);
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
      const part = service === 'dynamodb' ? TX.classify(op, body, (t) => tableKeys[t]) : null;
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
        const reply = sqsReply(ctx, op, body);
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
  const proxyPort = await listen(proxy, opts.bindHost || '127.0.0.1');

  const admin = new DynamoDBClient({ region: 'us-east-1', endpoint: 'http://127.0.0.1:' + dynPort, credentials: { accessKeyId: 'x', secretAccessKey: 'x' } });
  const doc = DynamoDBDocumentClient.from(admin);

  async function scanAll(table) {
    const items = [];
    let start;
    do {
      const r = await admin.send(new ScanCommand({ TableName: table, ExclusiveStartKey: start }));
      items.push(...(r.Items || []));
      start = r.LastEvaluatedKey;
    } while (start);
    return items;
  }

  return {
    proxyPort, dynPort, admin, doc, proxy, dyn,
    get ctx() { return ctx; },
    // Start recording a case: its calls, its SQS messages, its fault rules.
    beginCase(rules) {
      ctx = { calls: [], messages: [], seen: {}, inflight: 0, last: Date.now(), returned: false,
              rules: (rules || []).map((rule) => ({ rule, seen: 0, fired: 0 })) };
      return ctx;
    },
    endCase() { const c = ctx; ctx = null; return c; },
    async createTables() {
      for (const t of tables) await admin.send(new CreateTableCommand(t.create));
    },
    // Tables are created once; between cases every item is deleted. (Deleting
    // and recreating tables races dynalite's asynchronous DeleteTable.)
    async resetTables(fixtures) {
      for (const t of tables) {
        const keys = tableKeys[t.name];
        for (const item of await scanAll(t.name)) {
          await admin.send(new DeleteItemCommand({ TableName: t.name, Key: Object.fromEntries(keys.map((k) => [k, item[k]])) }));
        }
      }
      for (const [table, items] of Object.entries(fixtures || {})) {
        for (const item of items) await doc.send(new PutCommand({ TableName: table, Item: item }));
      }
    },
    async dumpTables() {
      const out = {};
      for (const t of tables) out[t.name] = (await scanAll(t.name)).map((i) => unmarshall(i));
      return out;
    },
    close() { proxy.close(); dyn.close(); },
  };
}

module.exports = { startBackend, cleanup, summarize, identityOf, matchFault, touchesItem, stable };
