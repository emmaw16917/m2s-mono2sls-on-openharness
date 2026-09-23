'use strict';
// Usage: node warm_cpu.js <handlerFile> <api|sqs> [itemCount]
// Warm-execution CPU proxy: module loaded once, 30 warm-up invocations, then
// 300 timed invocations with zero network latency; prints median/p90 per invocation.
const shim = require('./http-shim.js');
Object.assign(process.env, { BESTSELLER_QUEUE_URL: 'https://sqs.us-east-1.amazonaws.com/123456789012/q', BESTSELLERS_TABLE: 't', BOOKS_TABLE: 't', CART_TABLE: 't', ORDERS_TABLE: 't' });
const [file, kind = 'api', n = '20'] = process.argv.slice(2);
shim.opts.itemCount = Number(n);
console.log = () => {};
const mod = require('./' + file);
const books = [1, 2, 3].map((i) => ({ bookId: 'book-' + i, price: 9.99, quantity: 1 }));
const api = { pathParameters: { id: 'book-1', bookId: 'book-1' }, queryStringParameters: { q: 'book' },
  body: JSON.stringify({ bookId: 'book-1', quantity: 2, price: 9.99, books }), requestContext: { authorizer: { claims: { sub: 'u1' } } } };
const sqs = { Records: Array.from({ length: 10 }, (_, i) => ({ body: JSON.stringify({ orderId: 'o' + i, books: [0, 1, 2].map((j) => ({ bookId: 'b' + ((i + j) % 5), quantity: 1 })) }) })) };
const ev = kind === 'sqs' ? sqs : api;
(async () => {
  for (let i = 0; i < 30; i++) await mod.handler(ev, {});
  const t = []; let res;
  for (let i = 0; i < 300; i++) { shim.reset(); const t0 = process.hrtime.bigint(); res = await mod.handler(ev, {}); t.push(Number(process.hrtime.bigint() - t0) / 1e6); }
  t.sort((a, b) => a - b);
  process.stdout.write(JSON.stringify({ file, items: Number(n), status: res && res.statusCode, calls: shim.calls().length, median_ms: +t[150].toFixed(3), p90_ms: +t[270].toFixed(3) }) + '\n');
})().catch((e) => { process.stdout.write('ERR ' + file + ' ' + e.message + '\n'); });
