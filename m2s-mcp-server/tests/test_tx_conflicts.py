"""Transaction-conflict emulation (verify-tools/tx-conflicts.js) and its use
in the measurement shim. Runs small Node scripts; skipped without node."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[1] / "verify-tools"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None or not (TOOLS / "node_modules").exists(),
                                reason="needs node and verify-tools/node_modules")


def run_node(script: str) -> dict:
    p = subprocess.run([NODE, "-e", script], cwd=TOOLS, capture_output=True, text=True, encoding="utf-8",
                       stdin=subprocess.DEVNULL, timeout=120)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout.strip().splitlines()[-1])


def test_classify_and_conflict_rules():
    out = run_node(r"""
const TX = require('./tx-conflicts');
const keys = (t) => ({ Books: ['bookId'], Idem: ['pk'] })[t];
const S = (s) => ({ S: s });
const txw = (...items) => TX.classify('TransactWriteItems', { TransactItems: items }, keys);
const marker = (id) => ({ Put: { TableName: 'Idem', Item: { pk: S(id), expiresAt: { N: '1' } } } });
const book = (id) => ({ Update: { TableName: 'Books', Key: { bookId: S(id) }, UpdateExpression: 'ADD c :q' } });
const t = new TX.Tracker();
const r = {};
const a = txw(marker('o1'), book('b1'), book('b2'));
r.putKeyIgnoresNonKeyAttrs = a.items[0].key === 'Idem|{"pk":{"S":"o1"}}';
const doneA = t.begin(a);
r.overlapTx = t.conflicts(txw(marker('o2'), book('b3'), book('b2')));
r.disjointTx = t.conflicts(txw(marker('o3'), book('b9')));
r.sameMarker = t.conflicts(txw(marker('o1'), book('b9')));
r.singleWriteOnTxItem = t.conflicts(TX.classify('UpdateItem', { TableName: 'Books', Key: { bookId: S('b1') } }, keys));
r.getItemIgnored = TX.classify('GetItem', { TableName: 'Books', Key: { bookId: S('b1') } }, keys);
r.body = TX.errorBody(txw(marker('o2'), book('b3'), book('b2')), [2]);
r.singleBody = TX.errorBody(TX.classify('PutItem', { TableName: 'Books', Item: { bookId: S('b1') } }, keys), [0]).__type;
doneA();
r.afterDone = t.conflicts(txw(marker('o2'), book('b2')));
const doneW = t.begin(TX.classify('UpdateItem', { TableName: 'Books', Key: { bookId: S('b5') } }, keys));
r.txOverOngoingSingleWrite = t.conflicts(txw(marker('o4'), book('b5')));
r.singleOverSingle = t.conflicts(TX.classify('UpdateItem', { TableName: 'Books', Key: { bookId: S('b5') } }, keys));
r.txGetOverSingleWrite = t.conflicts(TX.classify('TransactGetItems', { TransactItems: [{ Get: { TableName: 'Books', Key: { bookId: S('b5') } } }] }, keys));
doneW();
console.log(JSON.stringify(r));
""")
    assert out["putKeyIgnoresNonKeyAttrs"]
    assert out["overlapTx"] == [2]
    assert out["disjointTx"] == []
    assert out["sameMarker"] == [0]
    assert out["singleWriteOnTxItem"] == [0]
    assert out["getItemIgnored"] is None
    assert out["body"]["__type"].endswith("#TransactionCanceledException")
    assert [c["Code"] for c in out["body"]["CancellationReasons"]] == ["None", "None", "TransactionConflict"]
    assert out["singleBody"].endswith("#TransactionConflictException")
    assert out["afterDone"] == []
    # Documented rules only: an ongoing single write blocks TransactGetItems,
    # not a write transaction or another single write.
    assert out["txOverOngoingSingleWrite"] == []
    assert out["singleOverSingle"] == []
    assert out["txGetOverSingleWrite"] == [0]


SHIM_SCRIPT = r"""
process.env.SHIM_FN_DIR = process.cwd();  // absolute, as /var/task in RIE
process.env.SHIM_LATENCY_MS = '10';
const fs = require('fs'), os = require('os'), path = require('path');
const fx = path.join(os.tmpdir(), 'm2s-txc-fixtures-' + process.pid + '.json');
fs.writeFileSync(fx, JSON.stringify({ tables: { Books: { key: ['bookId'], items: [] }, Idem: { key: ['pk'], items: [] } } }));
process.env.SHIM_FIXTURES = fx;
const shim = require('./perf-shim.js');
const { DynamoDBClient, TransactWriteItemsCommand } = require('@aws-sdk/client-dynamodb');
const client = new DynamoDBClient({ region: 'us-east-1', endpoint: 'http://127.0.0.1:9', maxAttempts: 1 });
const S = (s) => ({ S: s });
const msg = (order, books) => new TransactWriteItemsCommand({ TransactItems: [
  { Put: { TableName: 'Idem', Item: { pk: S(order) }, ConditionExpression: 'attribute_not_exists(pk)' } },
  ...books.map((b) => ({ Update: { TableName: 'Books', Key: { bookId: S(b) }, UpdateExpression: 'ADD c :q', ExpressionAttributeValues: { ':q': { N: '1' } } } })),
] });
const send = (c) => client.send(c).then(() => 'ok', (e) => e.name + ':' + (e.CancellationReasons || []).map((r) => r.Code).join('/'));
(async () => {
  const r = {};
  shim.reset();
  r.parallel = await Promise.all([send(msg('o1', ['b1'])), send(msg('o2', ['b1'])), send(msg('o3', ['b2']))]);
  r.parallelConflicts = shim.calls().filter((c) => c.conflict).length;
  shim.reset();
  r.sequential = [];
  for (const [o, b] of [['o1', 'b1'], ['o2', 'b1'], ['o3', 'b2']]) r.sequential.push(await send(msg(o, [b])));
  r.sequentialConflicts = shim.calls().filter((c) => c.conflict).length;
  fs.unlinkSync(fx);
  console.log(JSON.stringify(r));
})();
"""


def test_shim_rejects_overlapping_concurrent_transactions():
    out = run_node(SHIM_SCRIPT)
    # o1 and o2 share book b1: whichever reaches the "service" second is
    # rejected (the SDK's async middleware decides the order); o3 is disjoint.
    assert sorted(out["parallel"][:2]) == ["TransactionCanceledException:None/TransactionConflict", "ok"]
    assert out["parallel"][2] == "ok"
    assert out["parallelConflicts"] == 1
    assert out["sequential"] == ["ok", "ok", "ok"]
    assert out["sequentialConflicts"] == 0


def test_proxy_fault_match_item_ignores_call_order():
    """A case-level fault pinned with match.item hits the transaction that
    writes that item whichever order the calls arrive in (ddb-proxy.js)."""
    out = run_node(r"""
const { matchFault } = require('./ddb-proxy');
const tx = (...pks) => ({ op: 'TransactWriteItems', transact: pks.map((pk) => ({ kind: 'Put', table: 'Idem', item: { pk, expiresAt: 1 } })) });
const run = (calls) => {
  const ctx = { rules: [{ rule: { match: { op: 'TransactWriteItems', item: { pk: 'c#perm#1' } }, nth: 1, kind: 'validation' }, seen: 0, fired: 0 }] };
  return calls.map((c) => matchFault(ctx, c));
};
console.log(JSON.stringify({
  sequential: run([tx('c#perm#0'), tx('c#perm#1'), tx('c#ok')]),
  interleaved: run([tx('c#perm#0'), tx('c#ok'), tx('c#perm#1')]),
}));
""")
    assert out["sequential"] == [None, "validation", None]
    assert out["interleaved"] == [None, None, "validation"]
