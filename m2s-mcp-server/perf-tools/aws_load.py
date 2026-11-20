"""Load one O benchmark against a deployed stack.

This is the `load_cmd` (and, with --seed, the `seed_cmd`) that
`perf.run_aws_verdict` runs. It reuses the e2e harness (Cognito session via
`e2e_aws.cognito_session`, benchmark fixtures in the same format as the local
perf shim) instead of inventing a second load format.

Environment (set by coord/aws.py's measure_aws):

    M2S_AWS_TARGET       stack endpoint
    M2S_AWS_OUTPUTS      JSON: the stack's outputs (Cognito pool/client ids)
    M2S_AWS_RESOURCES    JSON: logical id -> {type, physical}
    M2S_AWS_WS           workspace root
    M2S_AWS_BENCHMARK    benchmark file relative to the workspace
    M2S_AWS_REGION       us-east-1 by default
    M2S_AWS_APP          bookstore | airline (inferred from the workspace name)

Load mode:
    M2S_AWS_OUT                 where to write {"samples": [ms], ...}
    M2S_AWS_LOAD_REQUESTS       request count, default 30
    M2S_AWS_LOAD_CONCURRENCY    clients, default 1
    M2S_AWS_LOAD_TIMEOUT        per request seconds, default 30

Seed mode (--seed; run by measure_aws once per stack, before the passes):
    writes the benchmark's `fixtures` and `generate` data to the deployed
    DynamoDB tables. The benchmark identity's placeholder (identity.sub, e.g.
    "u-perf-1") is replaced by the real Cognito sub, so the handler's
    claims.sub points at the seeded rows.

HTTP benchmarks send the benchmark's request; SQS benchmarks send their
`sqs.records` to the first deployed queue, one round at a time (each round gets
a fresh orderId suffix so the consumer's idempotency table does not skip it).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
import threading
import time
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

M2S = Path(__file__).resolve().parents[1]
if str(M2S) not in sys.path:
    sys.path.insert(0, str(M2S))

import e2e_aws  # noqa: E402
import perf  # noqa: E402

# A worktree may not carry the e2e suites' node_modules; point the reuser at
# the checkout that has them.
if os.environ.get("M2S_E2E_TESTS"):
    e2e_aws.TESTS = Path(os.environ["M2S_E2E_TESTS"])

SESSION_DIR = Path(tempfile.gettempdir()) / "m2s-aws-load-sessions"
REGION = os.environ.get("M2S_AWS_REGION", e2e_aws.REGION)


def _json_env(name: str, default: Any) -> Any:
    raw = os.environ.get(name, "")
    return json.loads(raw) if raw.strip() else default


def _output(outputs: dict[str, str], *names: str) -> str:
    for name in names:
        if outputs.get(name):
            return str(outputs[name])
    for key, value in outputs.items():
        if value and any(key.endswith(name) for name in names):
            return str(value)
    return ""


def _app_name(ws: Path) -> str:
    explicit = os.environ.get("M2S_AWS_APP", "").strip().lower()
    if explicit in e2e_aws.APPS:
        return explicit
    return "airline" if "airline" in ws.name.lower() else "bookstore"


def _session(ws: Path, outputs: dict[str, str]) -> tuple[str, str]:
    """A Cognito session for the caller; cached per pool/client (the id token
    lasts an hour) so each pass does not create another user."""
    app_name = _app_name(ws)
    app = e2e_aws.APPS[app_name]
    pool = _output(outputs, "CognitoUserPoolId", "UserPoolId")
    client = _output(outputs, "CognitoUserPoolClientId", "UserPoolClientId")
    if not pool or not client:
        raise SystemExit(f"{app_name}: stack outputs carry no Cognito user pool/client id")
    key = hashlib.sha1(f"{pool}|{client}".encode()).hexdigest()[:16]
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    path = SESSION_DIR / f"{key}.json"
    try:
        cached = json.loads(path.read_text(encoding="utf-8"))
        if float(cached.get("exp", 0)) > time.time() + 120:
            return str(cached["authHeader"]), str(cached.get("sub") or "")
    except (OSError, ValueError, KeyError):
        pass
    cfg = {"authConfig": {"userPoolId": pool, "clientId": client, "region": REGION}}
    auth, sub = e2e_aws.cognito_session(app, cfg)
    claims = e2e_aws._jwt_claims(auth)
    path.write_text(json.dumps({"authHeader": auth, "sub": sub or claims.get("sub", ""),
                                "exp": claims.get("exp", 0)}), encoding="utf-8")
    return auth, str(sub or claims.get("sub") or "")


def _subst(value: Any, old: str, new: str) -> Any:
    if not old:
        return value
    if isinstance(value, str):
        return value.replace(old, new)
    if isinstance(value, list):
        return [_subst(v, old, new) for v in value]
    if isinstance(value, dict):
        return {k: _subst(v, old, new) for k, v in value.items()}
    return value


def _table_of(resources: dict[str, dict[str, str]], logical: str) -> str:
    for lid, resource in resources.items():
        if resource.get("type") != "AWS::DynamoDB::Table":
            continue
        if lid == logical or lid.lower() == logical.lower():
            return str(resource.get("physical") or "")
    # fixtures sometimes use a blueprint name ("Books") instead of the logical id
    for lid, resource in resources.items():
        if resource.get("type") != "AWS::DynamoDB::Table":
            continue
        if lid.lower().startswith(logical.lower()) or logical.lower().startswith(lid.lower().replace("table", "")):
            return str(resource.get("physical") or "")
    return ""


def _seed(ws: Path, bench: dict[str, Any], resources: dict[str, dict[str, str]], sub_old: str, sub_new: str) -> dict[str, Any]:
    import boto3

    dynamodb = boto3.resource("dynamodb", region_name=REGION)
    bench = _subst(bench, sub_old, sub_new)
    written: dict[str, int] = {}
    missing: list[str] = []
    for logical, items in (bench.get("fixtures") or {}).items():
        table = _table_of(resources, logical)
        if not table:
            missing.append(logical)
            continue
        with dynamodb.Table(table).batch_writer() as batch:
            for item in items:
                batch.put_item(Item=item)
        written[table] = written.get(table, 0) + len(items)
    for logical, spec in (bench.get("generate") or {}).items():
        table = _table_of(resources, logical)
        if not table:
            missing.append(logical)
            continue
        count = int(spec.get("count") or 0)
        template = spec.get("template") or {}
        with dynamodb.Table(table).batch_writer() as batch:
            for i in range(count):
                batch.put_item(Item=perf._fill(template, i))
        written[table] = written.get(table, 0) + count
    return {"written": written, "missing_tables": missing}


def _request(bench: dict[str, Any], target: str, auth: str, sub_old: str, sub_new: str) -> dict[str, Any]:
    req = _subst(bench.get("request") or {}, sub_old, sub_new)
    path = str(req.get("path") or "")
    headers = {str(k): str(v) for k, v in (req.get("headers") or {}).items()}
    headers.setdefault("Content-Type", "application/json")
    if auth:
        headers["Authorization"] = auth
    return {"method": str(req.get("method") or "GET").upper(), "url": target.rstrip("/") + path,
            "params": req.get("query") or {}, "body": req.get("body"), "headers": headers}


def _load_sqs(bench: dict[str, Any], resources: dict[str, dict[str, str]]) -> dict[str, Any]:
    """Send the benchmark's SQS records `rounds` times. Each round gets its own
    orderId suffix, or the consumer's idempotency table would skip every repeat
    and the changed code path would stop running."""
    import boto3

    sqs = boto3.client("sqs", region_name=REGION)

    def find_queue(resource_map: dict[str, dict[str, str]]) -> str:
        for resource in resource_map.values():
            if resource.get("type") == "AWS::SQS::Queue" and resource.get("physical"):
                physical = str(resource["physical"])
                if physical.startswith("http"):
                    return physical  # CloudFormation reports the queue URL itself
                try:
                    return sqs.get_queue_url(QueueName=physical)["QueueUrl"]
                except Exception:  # noqa: BLE001 -- try the next queue
                    continue
        return ""

    queue = find_queue(resources)
    stack = os.environ.get("M2S_AWS_STACK", "")
    if not queue and stack:
        # a partial/empty resource map from the caller: read the stack directly
        _, fresh = e2e_aws.stack_info(stack)
        queue = find_queue(fresh)
    if not queue:
        kinds = Counter(str(r.get("type")) for r in resources.values())
        raise SystemExit(f"aws_load.py: no SQS queue in M2S_AWS_RESOURCES (stack={stack or '-'}, types={dict(kinds)})")
    records = (bench.get("sqs") or {}).get("records") or []
    if not records:
        raise SystemExit("aws_load.py: the benchmark has no sqs.records")
    rounds = max(1, int(os.environ.get("M2S_AWS_LOAD_REQUESTS", "30")))
    run_nonce = uuid.uuid4().hex[:8]  # reused baseline stacks keep their idempotency rows
    samples: list[float] = []
    sent = 0
    errors: Counter[str] = Counter()
    for round_no in range(rounds):
        entries = []
        for index, record in enumerate(records):
            body = json.loads(json.dumps(record.get("body") or {}))
            if isinstance(body, dict) and body.get("orderId"):
                body["orderId"] = f"{body['orderId']}-{run_nonce}-r{round_no}"
            entries.append({"Id": f"m{round_no}-{index}", "MessageBody": json.dumps(body)})
        started = time.perf_counter()
        try:
            # one batch per round: the event source then receives a full batch
            # of 10, which is what the benchmark (and the R4 change) assumes
            result = sqs.send_message_batch(QueueUrl=queue, Entries=entries[:10])
            samples.append(round((time.perf_counter() - started) * 1000, 3))
            failed = result.get("Failed") or []
            sent += len(entries[:10]) - len(failed)
            for failure in failed:
                errors[str(failure.get("Code") or "Failed")] += 1
        except Exception as exc:  # noqa: BLE001 -- report the count, not a stack
            errors[type(exc).__name__] += len(entries[:10])
        time.sleep(0.2)  # let the event source collect the next batch
    if not samples:
        raise SystemExit(f"aws_load.py: no SQS message could be sent ({dict(errors)})")
    return {"samples": samples, "statuses": {"sent": sent, **dict(errors)}, "concurrency": 1,
            "mode": "sqs", "queue": queue, "rounds": rounds}


def _load(ws: Path, bench: dict[str, Any], target: str, outputs: dict[str, str],
          resources: dict[str, dict[str, str]]) -> dict[str, Any]:
    if bench.get("sqs"):
        return _load_sqs(bench, resources)
    sub_old = str(((bench.get("request") or {}).get("identity") or {}).get("sub") or "")
    auth, sub_new = ("", "")
    if sub_old:
        auth, sub_new = _session(ws, outputs)
        if not sub_new:
            raise SystemExit("aws_load.py: Cognito session has no sub claim")
    req = _request(bench, target, auth, sub_old, sub_new)
    count = max(1, int(os.environ.get("M2S_AWS_LOAD_REQUESTS", "30")))
    concurrency = max(1, int(os.environ.get("M2S_AWS_LOAD_CONCURRENCY", "1")))
    timeout = float(os.environ.get("M2S_AWS_LOAD_TIMEOUT", "30"))
    import requests

    local = threading.local()

    def call(_: int) -> tuple[float, Any]:
        session = getattr(local, "session", None)
        if session is None:
            session = requests.Session()
            session.trust_env = False  # direct: the local proxy must not hijack AWS
            local.session = session
        started = time.perf_counter()
        try:
            resp = session.request(req["method"], req["url"], params=req["params"], json=req["body"],
                                   headers=req["headers"], timeout=timeout)
            return (time.perf_counter() - started) * 1000, resp.status_code
        except requests.RequestException as exc:
            return (time.perf_counter() - started) * 1000, type(exc).__name__

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        results = list(pool.map(call, range(count)))
    samples = [round(ms, 3) for ms, status in results if isinstance(status, int)]
    statuses = Counter(str(status) for _, status in results)
    if not samples or all(int(status) >= 400 for _, status in results if isinstance(status, int)):
        raise SystemExit(f"aws_load.py: every request failed ({dict(statuses)})")
    return {"samples": samples, "statuses": dict(statuses), "concurrency": concurrency,
            "method": req["method"], "url": req["url"], "identity": bool(auth)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", action="store_true", help="seed the benchmark data and exit")
    args = parser.parse_args(argv)

    ws = Path(os.environ.get("M2S_AWS_WS", "")).resolve()
    if not ws.is_dir():
        raise SystemExit("aws_load.py: M2S_AWS_WS is not set")
    bench_path = ws / os.environ.get("M2S_AWS_BENCHMARK", "")
    if not bench_path.is_file():
        raise SystemExit(f"aws_load.py: benchmark {bench_path} does not exist")
    bench = json.loads(bench_path.read_text(encoding="utf-8"))
    outputs = _json_env("M2S_AWS_OUTPUTS", {})
    resources = _json_env("M2S_AWS_RESOURCES", {})
    if not resources and os.environ.get("M2S_AWS_STACK"):
        # the caller's resource lookup failed or an older caller did not send it
        outputs, resources = e2e_aws.stack_info(os.environ["M2S_AWS_STACK"])

    sub_old = str(((bench.get("request") or {}).get("identity") or {}).get("sub") or "")
    auth, sub_new = _session(ws, outputs) if sub_old else ("", "")
    if args.seed:
        result = _seed(ws, bench, resources, sub_old, sub_new)
        print(json.dumps(result, ensure_ascii=False))
        return 0

    target = os.environ.get("M2S_AWS_TARGET", "")
    if not target:
        raise SystemExit("aws_load.py: M2S_AWS_TARGET is not set")
    out = os.environ.get("M2S_AWS_OUT", "")
    if not out:
        raise SystemExit("aws_load.py: M2S_AWS_OUT is not set")
    result = _load(ws, bench, target, outputs, resources)
    Path(out).write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: result.get(k) for k in ("statuses", "concurrency", "method", "url", "mode", "queue")},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
