'use strict';
// HTTP-level mock: the SDK runs its real middleware stack (serialize, sign,
// retry, deserialize, DocumentClient marshalling); only the network send is
// replaced. Lets us measure the CPU part of warm execution, which the
// send()-level shim skips entirely.
const crypto = require('crypto');
const { Readable } = require('stream');
const { NodeHttpHandler } = require('@smithy/node-http-handler');
// A plain {statusCode, headers, body} object instead of @smithy/protocol-http's
// HttpResponse: newer SDK versions (what `sam build` installs) no longer ship
// that package separately, and the SDK only duck-types the response anyway.
process.env.AWS_ACCESS_KEY_ID = process.env.AWS_ACCESS_KEY_ID || 'AKIDMOCK';
process.env.AWS_SECRET_ACCESS_KEY = process.env.AWS_SECRET_ACCESS_KEY || 'mocksecret';
process.env.AWS_REGION = process.env.AWS_REGION || 'us-east-1';

const book = (i) => ({ id: { S: 'book-' + i }, bookId: { S: 'book-' + i }, name: { S: 'Book name ' + i }, author: { S: 'Author ' + (i % 50) },
  category: { S: 'cat-' + (i % 8) }, price: { N: '9.99' }, salesCount: { N: String((i * 7919) % 1000) }, quantity: { N: '1' } });
// Env overrides so the same file works when preloaded inside a Lambda
// runtime container (NODE_OPTIONS=--require): SHIM_ITEMS = Scan/Query item
// count, SHIM_LATENCY_MS = simulated per-call network latency, SHIM_LOG=1 =
// log each intercepted call to stderr (used to verify a handler really reached
// its SDK calls, not an early 4xx).
const opts = { itemCount: Number(process.env.SHIM_ITEMS || 20) };
const LAT = Number(process.env.SHIM_LATENCY_MS || 0);
const LOG = process.env.SHIM_LOG === '1';
let calls = [];
function body(op, req) {
  switch (op) {
    case 'GetItem': return { Item: book(1) };
    case 'Query': case 'Scan': return { Items: Array.from({ length: opts.itemCount }, (_, i) => book(i)), Count: opts.itemCount, ScannedCount: opts.itemCount };
    case 'PutItem': case 'DeleteItem': case 'TransactWriteItems': return {};
    case 'UpdateItem': return { Attributes: { salesCount: { N: '1' } } };
    case 'BatchWriteItem': return { UnprocessedItems: {} };
    case 'SendMessage': { const mb = JSON.parse(req).MessageBody; return { MessageId: 'm1', MD5OfMessageBody: crypto.createHash('md5').update(mb).digest('hex') }; }
    default: throw new Error('http-shim: no mock for ' + op);
  }
}
NodeHttpHandler.prototype.handle = async function (request) {
  const target = request.headers['x-amz-target'];
  if (!target) throw new Error('http-shim: non-JSON protocol request, extend the shim: ' + request.headers['content-type']);
  const op = target.split('.').pop();
  calls.push(op);
  if (LOG) console.error('[shim] ' + op);
  if (LAT) await new Promise((r) => setTimeout(r, LAT));
  const payload = Buffer.from(JSON.stringify(body(op, request.body)));
  return { response: { statusCode: 200, headers: { 'content-type': 'application/x-amz-json-1.0', 'content-length': String(payload.length) }, body: Readable.from([payload]) } };
};
module.exports = { opts, reset: () => { calls = []; }, calls: () => calls.slice() };
