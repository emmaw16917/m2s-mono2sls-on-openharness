'use strict';
// HTTP-level mock: the SDK runs its real middleware stack (serialize, sign,
// retry, deserialize, DocumentClient marshalling); only the network send is
// replaced. Lets us measure the CPU part of warm execution, which the
// send()-level shim skips entirely.
const crypto = require('crypto');
const { Readable } = require('stream');
const { NodeHttpHandler } = require('@smithy/node-http-handler');
const { HttpResponse } = require('@smithy/protocol-http');
Object.assign(process.env, { AWS_ACCESS_KEY_ID: 'AKIDMOCK', AWS_SECRET_ACCESS_KEY: 'mocksecret', AWS_REGION: 'us-east-1' });

const book = (i) => ({ id: { S: 'book-' + i }, bookId: { S: 'book-' + i }, name: { S: 'Book name ' + i }, author: { S: 'Author ' + (i % 50) },
  category: { S: 'cat-' + (i % 8) }, price: { N: '9.99' }, salesCount: { N: String((i * 7919) % 1000) }, quantity: { N: '1' } });
const opts = { itemCount: 20 };
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
  const payload = Buffer.from(JSON.stringify(body(op, request.body)));
  return { response: new HttpResponse({ statusCode: 200, headers: { 'content-type': 'application/x-amz-json-1.0', 'content-length': String(payload.length) }, body: Readable.from([payload]) }) };
};
module.exports = { opts, reset: () => { calls = []; }, calls: () => calls.slice() };
