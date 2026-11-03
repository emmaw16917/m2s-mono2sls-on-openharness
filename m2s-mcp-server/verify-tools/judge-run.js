'use strict';
// run_judge's runner (PIPELINE_V2_PLAN.md §4.9, N2.2): the target's whole API,
// simulated on the verification engine, running the judge's multi-step cases.
//
//   node judge-run.js <job.json>
//
// job (built by judge.py run_judge):
//   mode "rie" | "local", backend / ddb_local, tables [{name, create}]  (the target's)
//   functions {name: {name, dir, handler, env, memory, runtime, layers, sqs_events}}
//   queues {queue: {max_receive}}         redrive maxReceiveCount (null: none)
//   gateway {unauthorized, no_route, error_502, timeout_504}: what API Gateway
//                                         answers by itself ({statusCode, headers, body})
//   units [{id, fixtures {table: [items]}, identity, vars {"u1.id": ..},
//           auth {alias: {header {name: value}, claims {..}}},
//           steps [{name, identity?, method, path, query, headers, body | form | raw_body,
//                   save, route: {kind: invoke | gateway | dropped | blocked, function,
//                   resource, authorized, http_api, reason}}],
//           rules [fault rules on the target's tables]}]
//   settle_ms, settle_max_ms, max_receive_default, out_dir
//
// One unit = one judge case (or one case of a fault scenario):
//   - every table is emptied and seeded with the unit's fixtures (already in
//     the target's structure, identity seeds included);
//   - steps run in order on one shared table state; each step is routed to the
//     function serving its method + path (the template's Api events), with the
//     API Gateway event built here: path parameters, query, headers, body, and
//     for an authorized route the claims of the identity whose header the
//     request carries -- none, or a header that is not an identity's, is the
//     authorizer's 401;
//   - after each step the backend settles, then the SQS messages the step sent
//     are delivered to the consumers of their queue (the template's event
//     source mappings, BatchSize records per batch), failed records again
//     until maxReceiveCount, messages those consumers send in turn too -- so a
//     later read-back step sees the asynchronous work;
//   - recorded per step: the request, the response as a client sees it
//     ({status, headers, body, body_kind}, the shape monolith-run.js records),
//     the deliveries; per unit: SDK calls, messages, fault rules fired, tables after.
// Functions start on first use and stay up for the whole run (one execution
// environment each, as a warm Lambda).
//
// Output: <out_dir>/judge_raw.json

const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const { startBackend, cleanup, maskEndpoint } = require('./ddb-proxy');

const job = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const OUT = job.out_dir;
fs.mkdirSync(OUT, { recursive: true });
const log = (...a) => process.stderr.write('[judge-run] ' + a.join(' ') + '\n');
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const { RieInvoker, LocalInvoker, convertCoverage } = require('./invokers')(job);

// ---------------------------------------------------------------- values

function subst(value, vars) {
  if (typeof value === 'string') return value.replace(/\$\{([^}]+)\}/g, (m, k) => (k in vars ? String(vars[k]) : m));
  if (Array.isArray(value)) return value.map((v) => subst(v, vars));
  if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value).map(([k, v]) => [subst(k, vars), subst(v, vars)]));
  return value;
}

function pick(response, dotted) {
  if (dotted === 'status') return response.status;
  const [head, ...rest] = dotted.split('.');
  if (head === 'headers') return (response.headers || {})[rest.join('.').toLowerCase()];
  return rest.reduce((o, k) => (o == null ? undefined : o[k]), response.body);
}

function lower(headers) {
  return Object.fromEntries(Object.entries(headers || {}).map(([k, v]) => [k.toLowerCase(), v]));
}

// A Lambda proxy result as the client receives it (the shape monolith-run.js
// records for the monolith).
function clientView(result) {
  const headers = {};
  for (const [k, v] of Object.entries(result.multiValueHeaders || {})) headers[k.toLowerCase()] = (v || []).length === 1 ? String(v[0]) : (v || []).map(String);
  for (const [k, v] of Object.entries(result.headers || {})) headers[k.toLowerCase()] = String(v);
  let text = result.body == null ? '' : String(result.body);
  if (result.isBase64Encoded) text = Buffer.from(text, 'base64').toString('utf8');
  let body = text;
  let kind = text.length ? 'text' : 'empty';
  if (text.length && /json/i.test(String(headers['content-type'] || ''))) {
    try { body = JSON.parse(text); kind = 'json'; } catch (e) { /* malformed JSON stays text */ }
  }
  return { status: result.statusCode, headers, body: kind === 'empty' ? null : body, body_kind: kind };
}

function isProxyResult(r) {
  return r && typeof r === 'object' && !Array.isArray(r) && Number.isInteger(r.statusCode);
}

// ---------------------------------------------------------------- the API Gateway event

// Path parameters of a concrete path under a template resource ("/api/books/{id}",
// a greedy "{proxy+}"); values as they appear in the path (like verify.build_event).
function pathParams(resource, concrete) {
  const names = [];
  let rx = '';
  for (const part of resource.split(/(\{\w+\+?\}|:\w+)/)) {
    const m = /^\{(\w+)(\+?)\}$/.exec(part) || /^:(\w+)()$/.exec(part);
    if (m) { names.push(m[1]); rx += m[2] ? '(.+)' : '([^/]+)'; } else rx += part.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  }
  const m = new RegExp('^' + rx + '$').exec(concrete);
  if (!m) return null;
  return Object.fromEntries(names.map((n, i) => [n, m[i + 1]]));
}

function claimValue(v, httpApi) {
  if (Array.isArray(v)) return httpApi ? '[' + v.map(String).join(' ') + ']' : v.map(String).join(',');
  return String(v);
}

function buildEvent(unit, step, route, alias, requestId) {
  const [rawPath, qs] = step.path.split(/\?(.*)/s);
  const multi = {};
  for (const [k, v] of new URLSearchParams(qs || '')) (multi[k] = multi[k] || []).push(v);
  for (const [k, v] of Object.entries(step.query || {})) (multi[k] = multi[k] || []).push(String(v));
  const headers = { ...(step.headers || {}) };
  const has = (h) => Object.keys(headers).some((k) => k.toLowerCase() === h);
  let body = null;
  if ('body' in step) {
    body = JSON.stringify(step.body);
    if (!has('content-type')) headers['Content-Type'] = 'application/json';
  } else if ('form' in step) {
    body = new URLSearchParams(Object.entries(step.form).map(([k, v]) => [k, String(v)])).toString();
    if (!has('content-type')) headers['Content-Type'] = 'application/x-www-form-urlencoded';
  } else if ('raw_body' in step) {
    body = step.raw_body;
  }
  if (body !== null && !has('content-length')) headers['Content-Length'] = String(Buffer.byteLength(body));
  const params = pathParams(route.resource, rawPath) || {};
  const method = step.method.toUpperCase();
  const rc = { resourcePath: route.resource, httpMethod: method, path: rawPath, stage: 'Prod', requestId,
               identity: { sourceIp: '127.0.0.1' }, accountId: '000000000000' };
  if (route.authorized && alias) {
    const c = unit.auth[alias].claims;
    const claims = { sub: c.sub, 'cognito:username': c['cognito:username'] || c.sub };
    for (const [k, v] of Object.entries(c)) if (k !== 'sub') claims[k] = claimValue(v, route.http_api);
    rc.authorizer = { claims };
  }
  const single = Object.fromEntries(Object.entries(multi).map(([k, v]) => [k, v[v.length - 1]]));
  return {
    ...(route.http_api ? { version: '1.0' } : {}),
    resource: route.resource, path: rawPath, httpMethod: method,
    headers: Object.keys(headers).length ? headers : null,
    multiValueHeaders: Object.keys(headers).length ? Object.fromEntries(Object.entries(headers).map(([k, v]) => [k, [v]])) : null,
    queryStringParameters: Object.keys(single).length ? single : null,
    multiValueQueryStringParameters: Object.keys(multi).length ? multi : null,
    pathParameters: Object.keys(params).length ? params : null, stageVariables: null, requestContext: rc,
    body, isBase64Encoded: false,
  };
}

// The identity a request authenticates as: the alias whose identity header it
// carries unchanged (a garbage or other token, or none, is no identity).
function authenticatedAs(unit, headers) {
  const h = lower(headers);
  for (const [alias, a] of Object.entries(unit.auth || {})) {
    const want = lower(a.header);
    if (Object.keys(want).length && Object.entries(want).every(([k, v]) => h[k] === v)) return alias;
  }
  return null;
}

// ---------------------------------------------------------------- functions

const invokers = {};
const fnState = {};
let nextPort = 9200;

async function invokerFor(be, name) {
  if (invokers[name]) return invokers[name];
  const fn = job.functions[name];
  const inv = job.mode === 'rie' ? new RieInvoker(fn, be.proxyPort, nextPort++) : new LocalInvoker(fn, be.proxyPort);
  fnState[name] = { started: false, invocations: 0 };
  try {
    await inv.start(path.join(OUT, 'cov', name));
    fnState[name].started = true;
  } catch (e) {
    fnState[name].start_error = String(e.message || e);
    throw new Error('function ' + name + ' could not be started: ' + fnState[name].start_error);
  }
  invokers[name] = inv;
  return inv;
}

async function settle(ctx, faulty) {
  const quiet = faulty ? Math.max(job.settle_ms || 150, 1000) : (job.settle_ms || 150);
  const t0 = Date.now();
  await sleep(Math.min(quiet, 50));
  while (Date.now() - t0 < (job.settle_max_ms || 10000)) {
    if (ctx.inflight === 0 && Date.now() - ctx.last >= quiet) return Date.now() - t0;
    await sleep(20);
  }
  return Date.now() - t0;
}

async function invoke(be, name, event) {
  const inv = await invokerFor(be, name);
  fnState[name].invocations += 1;
  be.functionInvoked(name);
  const r = await inv.invoke(event);
  be.functionReturned();
  return r;
}

// ---------------------------------------------------------------- SQS delivery

function sqsRecord(msg, queue, receiveCount) {
  const attrs = {};
  for (const [k, v] of Object.entries(msg.attributes || {})) {
    attrs[k] = { stringValue: v.StringValue, binaryValue: v.BinaryValue, dataType: v.DataType, stringListValues: [], binaryListValues: [] };
  }
  return {
    messageId: msg.id, receiptHandle: 'rh-' + msg.id + '-' + receiveCount, body: msg.body,
    attributes: { ApproximateReceiveCount: String(receiveCount), SentTimestamp: String(msg.sent_at),
                  SenderId: 'AIDAVERIFY', ApproximateFirstReceiveTimestamp: String(msg.sent_at) },
    messageAttributes: attrs, md5OfBody: crypto.createHash('md5').update(msg.body).digest('hex'),
    eventSource: 'aws:sqs', eventSourceARN: 'arn:aws:sqs:us-east-1:000000000000:' + queue, awsRegion: 'us-east-1',
  };
}

// Deliver every message sent so far that no consumer has seen; failed records
// go again (receive count + 1) until the queue's maxReceiveCount, then to its
// DLQ. Messages consumers send are delivered in the next round.
async function deliver(be, ctx, unitState, faulty) {
  const out = [];
  for (let round = 0; round < 20; round++) {
    const fresh = ctx.messages.slice(unitState.delivered);
    unitState.delivered = ctx.messages.length;
    for (const m of fresh) {
      m.id = m.id || crypto.randomUUID();
      m.sent_at = m.sent_at || Date.now();
      unitState.pending.push({ msg: m, receive: 1 });
    }
    if (!unitState.pending.length) break;
    const batch = unitState.pending;
    unitState.pending = [];
    for (const [name, fn] of Object.entries(job.functions)) {
      for (const ev of fn.sqs_events || []) {
        const mine = batch.filter((p) => p.msg.queue === ev.queue);
        for (let i = 0; i < mine.length; i += ev.batch_size || 10) {
          const chunk = mine.slice(i, i + (ev.batch_size || 10));
          const records = chunk.map((p) => sqsRecord(p.msg, ev.queue, p.receive));
          const r = await invoke(be, name, { Records: records });
          const settled = await settle(ctx, faulty);
          let failed = [];
          if (r.error) failed = chunk;
          else if (ev.report_failures && r.result && Array.isArray(r.result.batchItemFailures)) {
            const ids = new Set(r.result.batchItemFailures.map((f) => f.itemIdentifier));
            failed = chunk.filter((p) => ids.has(p.msg.id));
          }
          const max = (job.queues[ev.queue] || {}).max_receive || job.max_receive_default || 3;
          const dlq = [];
          for (const p of failed) {
            if (p.receive >= max) dlq.push(p.msg.id);
            else unitState.pending.push({ msg: p.msg, receive: p.receive + 1 });
          }
          out.push({ function: name, queue: ev.queue, records: chunk.length, receive_counts: chunk.map((p) => p.receive),
                     error: r.error || null, failed: failed.length, to_dlq: dlq.length, settle_ms: settled,
                     ...(dlq.length ? { dlq_bodies: chunk.filter((p) => dlq.includes(p.msg.id)).map((p) => p.msg.body) } : {}) });
        }
      }
    }
    const unconsumed = batch.filter((p) => !Object.values(job.functions).some((fn) => (fn.sqs_events || []).some((e) => e.queue === p.msg.queue)));
    for (const p of unconsumed) out.push({ queue: p.msg.queue, records: 1, consumer: null });
  }
  return out;
}

// ---------------------------------------------------------------- units

function gatewayAnswer(kind) {
  return clientView(job.gateway[kind]);
}

async function runUnit(be, unit) {
  const rec = { id: unit.id, steps: [] };
  await be.resetTables(unit.fixtures);
  const faulty = (unit.rules || []).length > 0;
  const ctx = be.beginCase(unit.rules);
  const vars = { ...unit.vars };
  const state = { delivered: 0, pending: [] };
  for (const raw of unit.steps) {
    const route = raw.route;
    const out = { name: raw.name, route: route.kind, function: route.function || null };
    if (route.kind === 'dropped' || route.kind === 'blocked') {
      out.reason = route.reason;
      rec.steps.push(out);
      continue;
    }
    const alias = 'identity' in raw ? raw.identity : unit.identity;
    const step = subst(raw, vars);
    if (alias && unit.auth[alias]) step.headers = { ...unit.auth[alias].header, ...(step.headers || {}) };
    const as = authenticatedAs(unit, step.headers);
    out.identity = alias || null;
    out.authenticated_as = as;
    out.request = { method: step.method.toUpperCase(), path: step.path, query: step.query || null,
                    headers: step.headers || {}, body: 'body' in step ? step.body : 'form' in step ? step.form : step.raw_body };
    const t0 = Date.now();
    if (route.kind === 'no_route') {
      out.response = gatewayAnswer('no_route');
      out.gateway = 'no_route';
    } else if (route.authorized && !as) {
      out.response = gatewayAnswer('unauthorized');
      out.gateway = 'unauthorized';
    } else {
      const event = buildEvent(unit, step, route, route.authorized ? as : null, 'req-' + unit.id + '-' + raw.name);
      const r = await invoke(be, route.function, event);
      if (r.error) {
        out.lambda_error = r.error;
        out.response = gatewayAnswer(r.error.type === 'M2SVerifyTimeout' ? 'timeout_504' : 'error_502');
        out.gateway = r.error.type === 'M2SVerifyTimeout' ? 'timeout_504' : 'error_502';
      } else if (!isProxyResult(r.result)) {
        out.lambda_result = r.result;
        out.response = gatewayAnswer('error_502');
        out.gateway = 'malformed_proxy_response';
      } else {
        out.response = clientView(r.result);
      }
    }
    out.ms = Date.now() - t0;
    out.settle_ms = await settle(ctx, faulty);
    out.deliveries = await deliver(be, ctx, state, faulty);
    for (const [k, p] of Object.entries(raw.save || {})) vars[k] = pick(out.response, p);
    rec.steps.push(out);
  }
  be.endCase();
  rec.calls = ctx.calls;
  rec.messages = ctx.messages.map((m) => ({ queue: m.queue, body: m.body, attributes: m.attributes }));
  rec.rules_fired = ctx.rules.map((r) => r.fired);
  rec.tables_after = await be.dumpTables();
  return rec;
}

async function main() {
  // 0.0.0.0: Lambda containers reach the proxy through host.docker.internal.
  const be = await startBackend({ backend: job.backend, ddbLocal: job.ddb_local, tables: job.tables, holdWritesMs: job.hold_writes_ms, bindHost: '0.0.0.0' });
  await be.createTables();
  const out = { mode: job.mode, backend: job.backend === 'ddblocal' ? 'ddblocal' : 'dynalite', node: process.version,
                units: [], functions: fnState };
  try {
    for (const unit of job.units) {
      try {
        out.units.push(await runUnit(be, unit));
      } catch (e) {
        be.endCase();
        out.units.push({ id: unit.id, infra_error: String(e && e.stack || e) });
      }
    }
  } finally {
    for (const [name, inv] of Object.entries(invokers)) {
      try { await inv.stop(path.join(OUT, 'cov', name)); } catch (e) { /* ignore */ }
      fnState[name].logs_tail = String(inv.logs() || '').slice(-3000);
      if (job.coverage) {
        try {
          fnState[name].coverage = await convertCoverage(job.functions[name], path.join(OUT, 'cov', name), path.join(OUT, 'coverage', name));
        } catch (e) { fnState[name].coverage = { error: String(e && e.stack || e) }; }
      }
    }
    be.close();
  }
  fs.writeFileSync(path.join(OUT, 'judge_raw.json'), maskEndpoint(JSON.stringify(out, null, 1), be.proxyPort));
}

main().then(() => process.exit(0), (e) => {
  log('FATAL', e && e.stack || e);
  cleanup();
  try { fs.writeFileSync(path.join(OUT, 'judge_error.txt'), String(e && e.stack || e)); } catch (x) { /* ignore */ }
  process.exit(1);
});
