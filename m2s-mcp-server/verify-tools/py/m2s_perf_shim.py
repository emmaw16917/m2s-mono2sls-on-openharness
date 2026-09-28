"""Optimization-line measurement shim for Python handlers -- the boto3 twin of
perf-shim.js (PERF_OPTIMIZATION_DESIGN.md §2.1).

Imported at interpreter start (sitecustomize.py next to this file, on
PYTHONPATH in an RIE container) or by m2s_perf_harness.py locally. It
patches botocore.httpsession.URLLib3Session.send -- the last step of every
boto3 call -- so botocore's real serialize / sign / retry / parse work still
runs, but the network round trip is replaced by:
  - a fixed wait of SHIM_LATENCY_MS (the v1 latency model), then
  - a response computed from the benchmark's table data (SHIM_FIXTURES):
    GetItem / BatchGetItem look items up by key, Query filters on the key
    condition, Scan returns the whole table; writes succeed without changing
    the data (every invocation sees the same data volume).
A write that touches an item of an ongoing transaction is rejected the way
real DynamoDB does (m2s_tx_conflicts.py); a request is ongoing while it
waits out its latency. Every call is recorded with start/end times and
estimated capacity units; SHIM_LOG=1 prints one "[m2s-shim] {json}" line per
call on stderr, the format perf.py parses for both runtimes.

Known simplifications (as the Node shim): no 1MB pagination, no
FilterExpression, latency independent of payload size and operation, and no
connection-pool limit (botocore's default pool is 10 connections).
"""

import json
import math
import os
import re
import sys
import threading
import time
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import m2s_tx_conflicts as TX  # noqa: E402

LOG = os.environ.get("SHIM_LOG") == "1"
state = {"latency": float(os.environ.get("SHIM_LATENCY_MS") or 0), "tables": {}, "calls": [], "max_rss": 0,
         "tx": TX.Tracker(), "lock": threading.Lock()}
if os.environ.get("SHIM_FIXTURES"):
    with open(os.environ["SHIM_FIXTURES"], encoding="utf-8") as fh:
        state["tables"] = json.load(fh).get("tables") or {}


# -- DynamoDB JSON -------------------------------------------------------------
def marshall(v):
    if v is None:
        return {"NULL": True}
    if isinstance(v, bool):
        return {"BOOL": v}
    if isinstance(v, str):
        return {"S": v}
    if isinstance(v, (int, float)):
        return {"N": str(v)}
    if isinstance(v, list):
        return {"L": [marshall(x) for x in v]}
    return {"M": {k: marshall(x) for k, x in v.items()}}


def _num(s):
    f = float(s)
    return int(f) if f.is_integer() and "." not in s and "e" not in s.lower() else f


def unmarshall(a):
    if not isinstance(a, dict):
        return a
    if "S" in a:
        return a["S"]
    if "N" in a:
        return _num(a["N"])
    if "BOOL" in a:
        return a["BOOL"]
    if "NULL" in a:
        return None
    if "L" in a:
        return [unmarshall(x) for x in a["L"]]
    if "M" in a:
        return {k: unmarshall(x) for k, x in a["M"].items()}
    if "SS" in a:
        return a["SS"]
    if "NS" in a:
        return [_num(x) for x in a["NS"]]
    return a


def marshall_item(o):
    return {k: marshall(v) for k, v in (o or {}).items()}


def unmarshall_item(o):
    return {k: unmarshall(v) for k, v in (o or {}).items()}


def item_size(o):
    return sum(len(k.encode()) + len(json.dumps(v, separators=(",", ":"), ensure_ascii=False).encode()) for k, v in (o or {}).items())


# -- Table model ---------------------------------------------------------------
def table(name):
    t = state["tables"].get(name)
    if t is None:
        raise RuntimeError(f"perf-shim: no fixture table {name} (known: {', '.join(state['tables'])})")
    return t


def key_of(t, item, attrs=None):
    return "|".join(json.dumps(item.get(k)) for k in (attrs or t["key"]))


def find_by_key(t, key):
    want = key_of(t, key)
    return next((i for i in t["items"] if key_of(t, i) == want), None)


def _name(n, names):
    return (names or {}).get(n, n) if n.startswith("#") else n


def query_items(req):
    t = table(req["TableName"])
    attrs = (t.get("indexes") or {}).get(req["IndexName"]) if req.get("IndexName") else t["key"]
    if not attrs:
        raise RuntimeError(f"perf-shim: unknown index {req.get('IndexName')} on {req['TableName']}")
    vals = unmarshall_item(req.get("ExpressionAttributeValues"))
    names = req.get("ExpressionAttributeNames")
    items = list(t["items"])
    for c in re.split(r"\s+AND\s+(?![^()]*\))", str(req.get("KeyConditionExpression") or ""), flags=re.I):
        m = re.match(r"^\s*\(?\s*([#\w]+)\s*(=|<=|>=|<|>)\s*(:\w+)\s*\)?\s*$", c)
        if m:
            a, op, v = _name(m.group(1), names), m.group(2), vals.get(m.group(3))
            ops = {"=": lambda x: x == v, "<": lambda x: x is not None and x < v, "<=": lambda x: x is not None and x <= v,
                   ">": lambda x: x is not None and x > v, ">=": lambda x: x is not None and x >= v}
            items = [i for i in items if ops[op](i.get(a))]
            continue
        m = re.search(r"begins_with\s*\(\s*([#\w]+)\s*,\s*(:\w+)\s*\)", c, re.I)
        if m:
            a = _name(m.group(1), names)
            items = [i for i in items if isinstance(i.get(a), str) and i[a].startswith(vals.get(m.group(2)))]
            continue
        m = re.search(r"([#\w]+)\s+BETWEEN\s+(:\w+)\s+AND\s+(:\w+)", c, re.I)
        if m:
            a = _name(m.group(1), names)
            items = [i for i in items if i.get(a) is not None and vals[m.group(2)] <= i[a] <= vals[m.group(3)]]
    if len(attrs) > 1:
        items.sort(key=lambda i: (i.get(attrs[1]) is None, i.get(attrs[1])))
    if req.get("ScanIndexForward") is False:
        items.reverse()
    if req.get("Limit"):
        items = items[: req["Limit"]]
    return items


# -- Capacity units (on-demand request units) -----------------------------------
def rru(nbytes, consistent):
    return max(1, math.ceil(nbytes / 4096)) * (1 if consistent else 0.5)


def wru(nbytes):
    return max(1, math.ceil(nbytes / 1024))


def respond(op, req):
    cu = {"rru": 0, "wru": 0, "sqs_requests": 0}
    if op == "GetItem":
        it = find_by_key(table(req["TableName"]), unmarshall_item(req["Key"]))
        cu["rru"] = rru(item_size(it), req.get("ConsistentRead"))
        body = {"Item": marshall_item(it)} if it else {}
    elif op == "BatchGetItem":
        responses = {}
        for tn, spec in req["RequestItems"].items():
            t = table(tn)
            found = [x for x in (find_by_key(t, unmarshall_item(k)) for k in spec["Keys"]) if x]
            for x in found:
                cu["rru"] += rru(item_size(x), spec.get("ConsistentRead"))
            responses[tn] = [marshall_item(x) for x in found]
        body = {"Responses": responses, "UnprocessedKeys": {}}
    elif op in ("Query", "Scan"):
        items = query_items(req) if op == "Query" else table(req["TableName"])["items"]
        cu["rru"] = rru(sum(item_size(i) for i in items), req.get("ConsistentRead"))
        body = {"Items": [marshall_item(i) for i in items], "Count": len(items), "ScannedCount": len(items)}
    elif op == "PutItem":
        cu["wru"] = wru(item_size(unmarshall_item(req["Item"])))
        body = {}
    elif op in ("UpdateItem", "DeleteItem"):
        t = table(req["TableName"])
        key = unmarshall_item(req["Key"])
        old = find_by_key(t, key)
        cu["wru"] = wru(max(item_size(old), item_size(key) + item_size(unmarshall_item(req.get("ExpressionAttributeValues")))))
        rv = req.get("ReturnValues")
        body = {"Attributes": marshall_item(old or key)} if rv and rv != "NONE" else {}
    elif op == "BatchWriteItem":
        for reqs in req["RequestItems"].values():
            for r in reqs:
                cu["wru"] += wru(item_size(unmarshall_item((r.get("PutRequest") or {}).get("Item") or (r.get("DeleteRequest") or {}).get("Key"))))
        body = {"UnprocessedItems": {}}
    elif op == "TransactWriteItems":
        for t in req["TransactItems"]:
            x = t.get("Put") or t.get("Update") or t.get("Delete") or t.get("ConditionCheck")
            cu["wru"] += 2 * wru(item_size(unmarshall_item(x.get("Item") or x.get("Key"))))
        body = {}
    elif op == "SendMessage":
        cu["sqs_requests"] = max(1, math.ceil(len(req["MessageBody"].encode()) / 65536))
        body = {"MessageId": str(uuid.uuid4()), "MD5OfMessageBody": _md5(req["MessageBody"])}
    elif op == "SendMessageBatch":
        cu["sqs_requests"] = 1
        body = {"Successful": [{"Id": e["Id"], "MessageId": str(uuid.uuid4()), "MD5OfMessageBody": _md5(e["MessageBody"])} for e in req["Entries"]],
                "Failed": []}
    else:
        raise RuntimeError(f"perf-shim: no stub for {op}")
    return body, cu


def _md5(s):
    import hashlib
    return hashlib.md5(s.encode()).hexdigest()


def _rss_mb():
    try:
        import resource
        return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024)  # KB on Linux
    except Exception:  # noqa: BLE001 -- no resource module on Windows
        return 0


# -- The patched send ------------------------------------------------------------
class _Raw:
    def __init__(self, data):
        self._data = data

    def stream(self, *_a, **_k):
        yield self._data


def handle(request):
    from botocore.awsrequest import AWSResponse
    target = request.headers.get("X-Amz-Target") or request.headers.get("x-amz-target")
    if isinstance(target, bytes):
        target = target.decode()
    if not target:
        raise RuntimeError("perf-shim: non-JSON protocol request (" + str(request.headers.get("Content-Type")) + ")")
    op = target.split(".")[-1]
    raw = request.body
    if hasattr(raw, "read"):
        raw = raw.read()
    req = json.loads(raw.decode() if isinstance(raw, (bytes, bytearray)) else (raw or "{}"))
    call = {"op": op, "table": req.get("TableName") or (req["QueueUrl"].rsplit("/", 1)[-1] if req.get("QueueUrl") else None),
            "start": time.perf_counter() * 1000}
    with state["lock"]:
        state["calls"].append(call)
    part = TX.classify(op, req, lambda t: (state["tables"].get(t) or {}).get("key"))
    hit, entry = state["tx"].try_begin(part) if part else ([], None)
    status = 200
    try:
        if state["latency"]:
            time.sleep(state["latency"] / 1000)
        if hit:
            # Rejected: the round trip and the capacity are still paid, nothing is written.
            call["conflict"] = True
            call.update(respond(op, req)[1])
            status, body = 400, TX.error_body(part, hit)
        else:
            body, cu = respond(op, req)
            call.update(cu)
    finally:
        if entry is not None:
            state["tx"].end(entry)
    call["end"] = time.perf_counter() * 1000
    rss = _rss_mb()
    state["max_rss"] = max(state["max_rss"], rss)
    if LOG:
        # Compact separators: perf.py parses '"max_rss_mb":N' and '"conflict":true'
        # exactly as JSON.stringify writes them in the Node shim.
        sys.stderr.write("[m2s-shim] " + json.dumps({**call, "max_rss_mb": state["max_rss"]}, separators=(",", ":")) + "\n")
        sys.stderr.flush()
    payload = json.dumps(body).encode()
    return AWSResponse(request.url, status, {"content-type": "application/x-amz-json-1.0", "content-length": str(len(payload))}, _Raw(payload))


def _patch():
    try:
        from botocore import httpsession
    except ImportError:
        sys.stderr.write("[m2s-shim] WARNING: botocore not importable, nothing patched\n")
        return False
    if not getattr(httpsession.URLLib3Session, "_m2s_patched", False):
        httpsession.URLLib3Session.send = lambda self, request: handle(request)
        httpsession.URLLib3Session._m2s_patched = True
    return True


patched = _patch()


def reset():
    with state["lock"]:
        state["calls"] = []


def calls():
    with state["lock"]:
        return list(state["calls"])
