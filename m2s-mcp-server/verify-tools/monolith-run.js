'use strict';
// Runs the monolith itself against test cases and records its external effects
// (PIPELINE_REDESIGN_v2.md J2 run_monolith; spike for open question Q4).
//
//   node monolith-run.js <job.json>
//
// - dynalite (in-memory DynamoDB) behind a recording proxy; the monolith runs as a host
//   process (node -r monolith-preload.js <entry>) with its SDK pointed at the proxy;
// - before each case every table is emptied and seeded with the case's fixtures (written
//   in the monolith's own table schema); the case's HTTP steps run in order, values can
//   be saved from a response (save: {var: "body.path"}) and used later as ${var};
// - after each case: every response, every recorded SDK call, a dump of every table and,
//   with job.coverage, the V8 coverage of exactly this case (the preload resets the
//   counters on each take);
// - job.repeat runs every case that many times; fields that differ between runs are
//   reported as volatile (candidates for the mask table).
//
// Output: <job.out_dir>/monolith_raw.json

const fs = require('fs');
const path = require('path');
const http = require('http');
const net = require('net');
const { spawn } = require('child_process');
const dynalite = require('dynalite');
const { DynamoDBClient, CreateTableCommand, ScanCommand, DeleteItemCommand } = require('@aws-sdk/client-dynamodb');
const { DynamoDBDocumentClient, PutCommand } = require('@aws-sdk/lib-dynamodb');
const { unmarshall } = require('@aws-sdk/util-dynamodb');

const job = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const OUT = job.out_dir;
fs.mkdirSync(OUT, { recursive: true });
const log = (...a) => process.stderr.write('[monolith-run] ' + a.join(' ') + '\n');
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function listen(server, host) {
  return new Promise((resolve) => server.listen(0, host, () => resolve(server.address().port)));
}

function freePort() {
  return new Promise((resolve) => {
    const s = net.createServer();
    s.listen(0, '127.0.0.1', () => { const p = s.address().port; s.close(() => resolve(p)); });
  });
}

// ---------------------------------------------------------------- backend + recording proxy

let calls = null; // SDK calls of the running case

async function startBackend() {
  const dyn = dynalite({ createTableMs: 0, deleteTableMs: 0, updateTableMs: 0 });
  const dynPort = await listen(dyn, '127.0.0.1');
  const proxy = http.createServer((req, res) => {
    const chunks = [];
    req.on('data', (c) => chunks.push(c));
    req.on('end', () => {
      const raw = Buffer.concat(chunks);
      const target = req.headers['x-amz-target'] || '';
      let body = {};
      try { body = raw.length ? JSON.parse(raw.toString('utf8')) : {}; } catch (e) { /* not JSON */ }
      const call = { op: target.split('.')[1] || '(unknown)', table: body.TableName || null };
      if (body.Key) call.key = unmarshall(body.Key);
      if (body.IndexName) call.index = body.IndexName;
      if (calls) calls.push(call);
      const fwd = http.request({ host: '127.0.0.1', port: dynPort, method: req.method, path: req.url,
        headers: { ...req.headers, host: '127.0.0.1:' + dynPort } }, (up) => {
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
  const proxyPort = await listen(proxy, '127.0.0.1');
  const admin = new DynamoDBClient({ region: 'us-east-1', endpoint: 'http://127.0.0.1:' + dynPort,
    credentials: { accessKeyId: 'x', secretAccessKey: 'x' } });
  return { dyn, proxy, proxyPort, admin, doc: DynamoDBDocumentClient.from(admin) };
}

async function scanAll(be, table) {
  const items = [];
  let start;
  do {
    const r = await be.admin.send(new ScanCommand({ TableName: table, ExclusiveStartKey: start }));
    items.push(...(r.Items || []));
    start = r.LastEvaluatedKey;
  } while (start);
  return items;
}

async function resetAndSeed(be, fixtures) {
  for (const t of job.tables) {
    const keys = t.create.KeySchema.map((k) => k.AttributeName);
    for (const item of await scanAll(be, t.name)) {
      const key = Object.fromEntries(keys.map((k) => [k, item[k]]));
      await be.admin.send(new DeleteItemCommand({ TableName: t.name, Key: key }));
    }
  }
  for (const [table, items] of Object.entries(fixtures || {})) {
    for (const item of items) await be.doc.send(new PutCommand({ TableName: table, Item: item }));
  }
}

async function dumpTables(be) {
  const out = {};
  for (const t of job.tables) {
    const keys = t.create.KeySchema.map((k) => k.AttributeName);
    out[t.name] = (await scanAll(be, t.name)).map(unmarshall)
      .sort((a, b) => JSON.stringify(keys.map((k) => a[k])).localeCompare(JSON.stringify(keys.map((k) => b[k]))));
  }
  return out;
}

// ---------------------------------------------------------------- the monolith process

async function startMonolith(proxyPort) {
  const port = await freePort();
  const covPort = job.coverage ? await freePort() : 0;
  const env = { ...process.env, ...(job.env || {}) };
  for (const k of ['HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy', 'NODE_OPTIONS']) delete env[k];
  Object.assign(env, {
    PORT: String(port), TZ: 'UTC',
    M2S_DDB_ENDPOINT: 'http://127.0.0.1:' + proxyPort,
    AWS_ENDPOINT_URL_DYNAMODB: 'http://127.0.0.1:' + proxyPort,
    AWS_ENDPOINT_URL_SQS: 'http://127.0.0.1:' + proxyPort,
    AWS_REGION: env.AWS_REGION || 'us-east-1', AWS_ACCESS_KEY_ID: 'x', AWS_SECRET_ACCESS_KEY: 'x',
    M2S_COV_PORT: String(covPort),
  });
  const preload = path.join(__dirname, 'monolith-preload.js');
  const proc = spawn(process.execPath, ['-r', preload, job.entry], { cwd: job.monolith_root, env,
    stdio: ['ignore', 'pipe', 'pipe'] });
  const logs = [];
  proc.stdout.on('data', (d) => logs.push(d.toString()));
  proc.stderr.on('data', (d) => logs.push(d.toString()));
  let exited = null;
  proc.on('exit', (code) => { exited = code; });
  for (let i = 0; i < 200; i++) {
    if (exited !== null) throw new Error('monolith exited (' + exited + ') before listening:\n' + logs.join('').slice(-2000));
    const up = await new Promise((r) => http.get({ host: '127.0.0.1', port, path: '/' }, (res) => { res.resume(); r(true); })
      .on('error', () => r(false)));
    if (up) return { proc, port, covPort, logs };
    await sleep(100);
  }
  proc.kill();
  throw new Error('monolith did not listen on port ' + port + ' within 20s');
}

function takeCoverage(mono) {
  if (!mono.covPort) return Promise.resolve(null);
  return new Promise((resolve) => {
    const req = http.request({ host: '127.0.0.1', port: mono.covPort, method: 'POST', path: '/take' }, (res) => {
      const out = [];
      res.on('data', (c) => out.push(c));
      res.on('end', () => { try { resolve(JSON.parse(Buffer.concat(out).toString('utf8')).result); } catch (e) { resolve(null); } });
    });
    req.on('error', () => resolve(null));
    req.end();
  });
}

// Per file: the functions and blocks that ran at least once (byte ranges), plus counts.
function summarizeCoverage(result) {
  if (!result) return null;
  const files = {};
  for (const script of result) {
    const rel = path.relative(job.monolith_root, decodeURIComponent(script.url.replace(/^file:\/\/\/?/, ''))).replace(/\\/g, '/');
    const ran = [];
    for (const fn of script.functions) {
      for (const r of fn.ranges) {
        if (r.count > 0) ran.push([fn.functionName || '(anonymous)', r.startOffset, r.endOffset, r.count]);
      }
    }
    if (ran.length) files[rel] = ran;
  }
  return files;
}

// ---------------------------------------------------------------- cases

function subst(value, vars) {
  if (typeof value === 'string') return value.replace(/\$\{(\w+)\}/g, (m, k) => (k in vars ? vars[k] : m));
  if (Array.isArray(value)) return value.map((v) => subst(v, vars));
  if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, subst(v, vars)]));
  return value;
}

function pick(obj, dotted) {
  return dotted.split('.').reduce((o, k) => (o == null ? undefined : o[k]), obj);
}

function request(port, step) {
  return new Promise((resolve) => {
    const body = step.body === undefined ? null : (typeof step.body === 'string' ? step.body : JSON.stringify(step.body));
    const headers = { ...(step.headers || {}) };
    if (body !== null && !Object.keys(headers).some((h) => h.toLowerCase() === 'content-type')) headers['content-type'] = 'application/json';
    const req = http.request({ host: '127.0.0.1', port, method: step.method, path: step.path, headers }, (res) => {
      const out = [];
      res.on('data', (c) => out.push(c));
      res.on('end', () => {
        const text = Buffer.concat(out).toString('utf8');
        let parsed = text;
        try { parsed = JSON.parse(text); } catch (e) { /* not JSON */ }
        const h = { ...res.headers };
        delete h.date; delete h.connection; delete h['keep-alive'];
        resolve({ status: res.statusCode, headers: h, body: parsed });
      });
    });
    req.on('error', (e) => resolve({ error: String(e) }));
    if (body !== null) req.write(body);
    req.end();
  });
}

async function runCase(be, mono, c) {
  await resetAndSeed(be, c.fixtures);
  await takeCoverage(mono); // drop what seeding / the previous case left
  calls = [];
  const vars = {};
  const steps = [];
  for (const raw of c.steps) {
    const step = subst(raw, vars);
    const r = await request(mono.port, step);
    for (const [k, p] of Object.entries(raw.save || {})) vars[k] = pick(r, p);
    steps.push({ name: raw.name || step.method + ' ' + step.path, request: { method: step.method, path: step.path }, response: r });
  }
  await sleep(job.settle_ms || 50); // fire-and-forget work of the last request
  const recorded = calls;
  calls = null;
  const coverage = summarizeCoverage(await takeCoverage(mono));
  return { id: c.id, steps, calls: recorded, tables: await dumpTables(be), coverage };
}

// Leaf paths whose values differ between two runs of the same case.
function volatile(a, b, at = '', out = []) {
  if (a && b && typeof a === 'object' && typeof b === 'object' && Array.isArray(a) === Array.isArray(b)) {
    for (const k of new Set([...Object.keys(a), ...Object.keys(b)])) volatile(a[k], b[k], at ? at + '.' + k : k, out);
  } else if (JSON.stringify(a) !== JSON.stringify(b)) {
    out.push(at);
  }
  return out;
}

async function main() {
  const be = await startBackend();
  for (const t of job.tables) await be.admin.send(new CreateTableCommand(t.create));
  const mono = await startMonolith(be.proxyPort);
  log('monolith on', mono.port, 'proxy on', be.proxyPort, 'coverage', mono.covPort || 'off');
  const results = { monolith_root: job.monolith_root, entry: job.entry, backend: 'dynalite', node: process.version, cases: [] };
  try {
    for (const c of job.cases) {
      const runs = [];
      for (let i = 0; i < (job.repeat || 1); i++) runs.push(await runCase(be, mono, c));
      const strip = (r) => ({ steps: r.steps, calls: r.calls, tables: r.tables });
      const varying = runs.length > 1 ? volatile(strip(runs[0]), strip(runs[1])) : [];
      results.cases.push({ id: c.id, runs, volatile: varying });
      log(c.id, runs.map((r) => r.steps.map((s) => s.response.status).join('/')).join(' | '), 'volatile', varying.length);
    }
  } finally {
    results.monolith_log = mono.logs.join('').slice(-4000);
    mono.proc.kill();
    be.proxy.close();
    be.dyn.close();
  }
  fs.writeFileSync(path.join(OUT, 'monolith_raw.json'), JSON.stringify(results, null, 1));
}

main().then(() => process.exit(0), (e) => { log(e && e.stack || e); process.exit(1); });
