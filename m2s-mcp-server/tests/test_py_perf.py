"""The Python perf shim (verify-tools/py/m2s_perf_shim.py) and its
conflict emulation, run in subprocesses so the botocore patch stays out of
the test process. Also checks that the Python and JS conflict modules agree."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[1] / "verify-tools"
PY = TOOLS / "py"


def run_py(script: str, env: dict | None = None) -> tuple[dict, str]:
    import os
    p = subprocess.run([sys.executable, "-c", script], cwd=PY, capture_output=True, text=True, encoding="utf-8",
                       env={**os.environ, **(env or {})}, stdin=subprocess.DEVNULL, timeout=120)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout.strip().splitlines()[-1]), p.stderr


pytest.importorskip("botocore")

SHIM_SCRIPT = r"""
import json, os, sys, threading
sys.path.insert(0, os.getcwd())
import m2s_perf_shim as shim
import boto3
client = boto3.client('dynamodb', region_name='us-east-1', endpoint_url='http://127.0.0.1:9',
                      aws_access_key_id='x', aws_secret_access_key='x',
                      config=__import__('botocore.config').config.Config(retries={'max_attempts': 1}))
S = lambda s: {'S': s}
def msg(order, book):
    return {'TransactItems': [{'Put': {'TableName': 'Idem', 'Item': {'pk': S(order)}}},
                              {'Update': {'TableName': 'Books', 'Key': {'bookId': S(book)}, 'UpdateExpression': 'ADD c :q',
                                          'ExpressionAttributeValues': {':q': {'N': '1'}}}}]}
def send(order, book, out, i):
    try:
        client.transact_write_items(**msg(order, book)); out[i] = 'ok'
    except client.exceptions.TransactionCanceledException as e:
        out[i] = 'cancelled:' + '/'.join(r['Code'] for r in e.response.get('CancellationReasons', []))
r = {}
shim.reset()
out = [None] * 3
ts = [threading.Thread(target=send, args=(o, b, out, i)) for i, (o, b) in enumerate([('o1', 'b1'), ('o2', 'b1'), ('o3', 'b2')])]
[t.start() for t in ts]; [t.join() for t in ts]
r['parallel'] = out
r['parallel_conflicts'] = sum(1 for c in shim.calls() if c.get('conflict'))
shim.reset()
seq = [None] * 3
for i, (o, b) in enumerate([('o1', 'b1'), ('o2', 'b1'), ('o3', 'b2')]):
    send(o, b, seq, i)
r['sequential'] = seq
r['sequential_conflicts'] = sum(1 for c in shim.calls() if c.get('conflict'))
print(json.dumps(r))
"""


def test_python_shim_rejects_overlapping_concurrent_transactions(tmp_path):
    fx = tmp_path / "fx.json"
    fx.write_text(json.dumps({"tables": {"Books": {"key": ["bookId"], "items": []}, "Idem": {"key": ["pk"], "items": []}}}), encoding="utf-8")
    out, stderr = run_py(SHIM_SCRIPT, {"SHIM_FIXTURES": str(fx), "SHIM_LATENCY_MS": "50", "SHIM_LOG": "1"})
    assert sorted(out["parallel"][:2]) == ["cancelled:None/TransactionConflict", "ok"]
    assert out["parallel"][2] == "ok"
    assert out["parallel_conflicts"] == 1
    assert out["sequential"] == ["ok", "ok", "ok"] and out["sequential_conflicts"] == 0
    # perf.py parses the log lines of both shims with the compact JSON form.
    lines = [l for l in stderr.splitlines() if l.startswith("[m2s-shim] ")]
    assert lines and all('"max_rss_mb":' in l for l in lines)
    assert sum('"conflict":true' in l for l in lines) == 1


VECTORS = [
    ("TransactWriteItems", {"TransactItems": [{"Put": {"TableName": "Idem", "Item": {"pk": {"S": "o1"}, "x": {"N": "1"}}}},
                                               {"Update": {"TableName": "Books", "Key": {"bookId": {"S": "b1"}}}}]}),
    ("TransactWriteItems", {"TransactItems": [{"Put": {"TableName": "Idem", "Item": {"pk": {"S": "o2"}}}},
                                               {"Update": {"TableName": "Books", "Key": {"bookId": {"S": "b1"}}}}]}),
    ("UpdateItem", {"TableName": "Books", "Key": {"bookId": {"S": "b1"}}}),
    ("TransactGetItems", {"TransactItems": [{"Get": {"TableName": "Books", "Key": {"bookId": {"S": "b9"}}}}]}),
    ("PutItem", {"TableName": "Books", "Item": {"bookId": {"S": "b9"}, "y": {"S": "z"}}}),
    ("TransactGetItems", {"TransactItems": [{"Get": {"TableName": "Books", "Key": {"bookId": {"S": "b9"}}}}]}),
    ("GetItem", {"TableName": "Books", "Key": {"bookId": {"S": "b1"}}}),
]


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_python_and_js_conflict_modules_agree():
    """Each vector arrives while every earlier accepted request is still
    ongoing; both modules must reject the same requests on the same items."""
    js = f"""
const TX = require('./tx-conflicts');
const keys = (t) => ({{Books: ['bookId'], Idem: ['pk']}})[t];
const t = new TX.Tracker(); const out = [];
for (const [op, req] of {json.dumps(VECTORS)}) {{
  const part = TX.classify(op, req, keys);
  if (!part) {{ out.push(null); continue; }}
  const hit = t.conflicts(part); out.push(hit);
  if (!hit.length) t.begin(part);
}}
console.log(JSON.stringify(out));
"""
    p = subprocess.run(["node", "-e", js], cwd=TOOLS, capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr
    js_out = json.loads(p.stdout)
    py_out, _ = run_py(f"""
import json, m2s_tx_conflicts as TX
keys = lambda t: {{'Books': ['bookId'], 'Idem': ['pk']}}.get(t)
t = TX.Tracker(); out = []
for op, req in {VECTORS!r}:
    part = TX.classify(op, req, keys)
    if part is None:
        out.append(None); continue
    hit, entry = t.try_begin(part)
    out.append(hit)
print(json.dumps(out))
""")
    assert py_out == js_out == [[], [1], [0], [], [], [0], None]
