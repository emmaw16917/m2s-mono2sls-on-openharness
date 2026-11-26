"""DynamoDB transaction-conflict emulation for the Python perf shim -- a port
of tx-conflicts.js with the same rules (see there and PERF_OPTIMIZATION_DESIGN_v1.md
§2.1 item 6). The newcomer is rejected, the ongoing request completes:
  - PutItem / UpdateItem / DeleteItem on an item of an ongoing
    TransactWriteItems -> TransactionConflictException;
  - TransactWriteItems with an item of another ongoing TransactWriteItems
    -> TransactionCanceledException, that item's reason TransactionConflict;
  - TransactGetItems with an item of an ongoing TransactWriteItems,
    BatchWriteItem, PutItem, UpdateItem or DeleteItem -> the same.
Thread-safe: Python handlers parallelize with threads. Item identity uses the
marshalled key attributes as sent on the wire (stable JSON), like the JS
module, so both shims agree (tests/test_py_perf.py compares them).
"""

import json
import threading


def stable(v):
    return json.dumps(v, sort_keys=True, separators=(",", ":"))


def _item_key(table, key_or_item, key_attrs, is_item):
    key = key_or_item or {}
    attrs = key_attrs(table) if is_item else None
    if attrs:
        key = {a: key.get(a) for a in attrs}
    return f"{table}|{stable(key)}"


def classify(op, req, key_attrs):
    """-> {op, cls: w|txw|txr|bw, items: [(index, key)]} or None."""
    req = req or {}
    if op == "PutItem":
        return {"op": op, "cls": "w", "items": [(0, _item_key(req.get("TableName"), req.get("Item"), key_attrs, True))]}
    if op in ("UpdateItem", "DeleteItem"):
        return {"op": op, "cls": "w", "items": [(0, _item_key(req.get("TableName"), req.get("Key"), key_attrs, False))]}
    if op in ("TransactWriteItems", "TransactGetItems"):
        items = []
        for i, t in enumerate(req.get("TransactItems") or []):
            v = t.get("Put") or t.get("Update") or t.get("Delete") or t.get("ConditionCheck") or t.get("Get") or {}
            items.append((i, _item_key(v.get("TableName"), v.get("Item") or v.get("Key"), key_attrs, "Item" in v)))
        return {"op": op, "cls": "txw" if op == "TransactWriteItems" else "txr", "items": items}
    if op == "BatchWriteItem":
        items = []
        for table, reqs in (req.get("RequestItems") or {}).items():
            for r in reqs:
                if "PutRequest" in r:
                    items.append((len(items), _item_key(table, r["PutRequest"].get("Item"), key_attrs, True)))
                elif "DeleteRequest" in r:
                    items.append((len(items), _item_key(table, r["DeleteRequest"].get("Key"), key_attrs, False)))
        return {"op": op, "cls": "bw", "items": items}
    return None


def blocks(incoming, ongoing):
    if incoming in ("w", "txw"):
        return ongoing == "txw"
    if incoming == "txr":
        return ongoing in ("txw", "bw", "w")
    return False


class Tracker:
    def __init__(self):
        self._ongoing = []
        self._lock = threading.Lock()

    def try_begin(self, req):
        """Atomically: the conflicting item indices (the request is rejected)
        or [] and the request is now ongoing (call end(token) when done)."""
        with self._lock:
            hit = sorted({i for o in self._ongoing if blocks(req["cls"], o["cls"])
                          for i, k in req["items"] if k in o["keys"]})
            if hit:
                return hit, None
            entry = {"cls": req["cls"], "keys": {k for _, k in req["items"]}}
            self._ongoing.append(entry)
            return [], entry

    def end(self, entry):
        with self._lock:
            if entry in self._ongoing:
                self._ongoing.remove(entry)


def error_body(req, indices):
    if req["cls"] == "w":
        return {"__type": "com.amazonaws.dynamodb.v20120810#TransactionConflictException",
                "message": "Transaction is ongoing for the item (emulated)"}
    at = set(indices)
    reasons = [{"Code": "TransactionConflict", "Message": "Transaction is ongoing for the item (emulated)"} if i in at else {"Code": "None"}
               for i, _ in req["items"]]
    return {"__type": "com.amazonaws.dynamodb.v20120810#TransactionCanceledException",
            "message": "Transaction cancelled, please refer cancellation reasons for specific reasons ["
                       + ", ".join(r["Code"] for r in reasons) + "]",
            "CancellationReasons": reasons}
