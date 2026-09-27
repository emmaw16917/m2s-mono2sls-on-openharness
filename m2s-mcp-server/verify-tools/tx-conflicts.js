'use strict';
// DynamoDB transaction-conflict emulation (PERF_OPTIMIZATION_DESIGN.md §2.1
// item 6), shared by controller.js (verification proxy) and perf-shim.js
// (measurement shim).
//
// DynamoDB Local runs transactions one at a time and never returns a
// conflict. Real DynamoDB rejects a request that touches an item of an
// ONGOING request ("Transaction conflict handling in DynamoDB", developer
// guide); the newcomer is rejected, the ongoing request completes:
//   - PutItem / UpdateItem / DeleteItem on an item of an ongoing
//     TransactWriteItems -> TransactionConflictException;
//   - TransactWriteItems with an item of another ongoing TransactWriteItems
//     -> TransactionCanceledException, that item's reason TransactionConflict;
//   - TransactGetItems with an item of an ongoing TransactWriteItems,
//     BatchWriteItem, PutItem, UpdateItem or DeleteItem -> the same.
// Not modelled: a BatchWriteItem's own items against an ongoing transaction
// (the guide does not say what happens).
//
// Item identity uses the marshalled key attributes as sent on the wire, so
// no unmarshalling is needed and both callers agree on it.

function stable(v) {
  if (Array.isArray(v)) return '[' + v.map(stable).join(',') + ']';
  if (v && typeof v === 'object') return '{' + Object.keys(v).sort().map((k) => JSON.stringify(k) + ':' + stable(v[k])).join(',') + '}';
  return JSON.stringify(v);
}

// keyAttrs(table) -> key attribute names, or undefined when unknown (the
// whole item then stands for its key, which only ever matches itself).
function itemKey(table, keyOrItem, keyAttrs, isItem) {
  let key = keyOrItem || {};
  const attrs = isItem ? keyAttrs(table) : null;
  if (attrs) key = Object.fromEntries(attrs.map((a) => [a, key[a]]));
  return table + '|' + stable(key);
}

// -> {op, cls: 'w' | 'txw' | 'txr' | 'bw', items: [{index, key}]}, or null
// for operations that never take part in a conflict (reads, SQS, ...).
function classify(op, req, keyAttrs) {
  req = req || {};
  switch (op) {
    case 'PutItem':
      return { op, cls: 'w', items: [{ index: 0, key: itemKey(req.TableName, req.Item, keyAttrs, true) }] };
    case 'UpdateItem':
    case 'DeleteItem':
      return { op, cls: 'w', items: [{ index: 0, key: itemKey(req.TableName, req.Key, keyAttrs, false) }] };
    case 'TransactWriteItems':
    case 'TransactGetItems':
      return {
        op,
        cls: op === 'TransactWriteItems' ? 'txw' : 'txr',
        items: (req.TransactItems || []).map((t, index) => {
          const v = t.Put || t.Update || t.Delete || t.ConditionCheck || t.Get || {};
          return { index, key: v.Item ? itemKey(v.TableName, v.Item, keyAttrs, true) : itemKey(v.TableName, v.Key, keyAttrs, false) };
        }),
      };
    case 'BatchWriteItem': {
      const items = [];
      for (const [table, reqs] of Object.entries(req.RequestItems || {})) {
        for (const r of reqs) {
          if (r.PutRequest) items.push({ index: items.length, key: itemKey(table, r.PutRequest.Item, keyAttrs, true) });
          else if (r.DeleteRequest) items.push({ index: items.length, key: itemKey(table, r.DeleteRequest.Key, keyAttrs, false) });
        }
      }
      return { op, cls: 'bw', items };
    }
    default:
      return null;
  }
}

// Does an ongoing request of class `ongoing` reject a newcomer of class `incoming`?
function blocks(incoming, ongoing) {
  if (incoming === 'w' || incoming === 'txw') return ongoing === 'txw';
  if (incoming === 'txr') return ongoing === 'txw' || ongoing === 'bw' || ongoing === 'w';
  return false;
}

class Tracker {
  constructor() { this.ongoing = new Set(); }

  // Indices of the newcomer's items that conflict with an ongoing request
  // (empty: no conflict).
  conflicts(req) {
    const hit = new Set();
    for (const o of this.ongoing) {
      if (!blocks(req.cls, o.cls)) continue;
      for (const it of req.items) if (o.keys.has(it.key)) hit.add(it.index);
    }
    return [...hit].sort((a, b) => a - b);
  }

  // Marks the request ongoing; call the returned function when it completes.
  begin(req) {
    const entry = { cls: req.cls, keys: new Set(req.items.map((i) => i.key)) };
    this.ongoing.add(entry);
    return () => this.ongoing.delete(entry);
  }
}

// The error body real DynamoDB returns to the rejected newcomer (JSON protocol).
function errorBody(req, indices) {
  if (req.cls === 'w') {
    return { __type: 'com.amazonaws.dynamodb.v20120810#TransactionConflictException', message: 'Transaction is ongoing for the item (emulated)' };
  }
  const at = new Set(indices);
  const reasons = req.items.map((it) => (at.has(it.index) ? { Code: 'TransactionConflict', Message: 'Transaction is ongoing for the item (emulated)' } : { Code: 'None' }));
  return {
    __type: 'com.amazonaws.dynamodb.v20120810#TransactionCanceledException',
    message: 'Transaction cancelled, please refer cancellation reasons for specific reasons [' + reasons.map((r) => r.Code).join(', ') + ']',
    CancellationReasons: reasons,
  };
}

module.exports = { classify, blocks, Tracker, errorBody, stable };
