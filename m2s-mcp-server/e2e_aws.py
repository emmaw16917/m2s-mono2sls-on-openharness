"""E2E comparison on real AWS: the monolith (run locally on DynamoDB Local),
the accepted v0 and the optimized HEAD of an m2s workspace (each deployed
with SAM, tested, measured, torn down -- one stack at a time), all driven by
the same suite under Mono2Sls-artifacts/tests/e2e-test-suite-<app>.

    python e2e_aws.py bookstore                 # monolith + v0 + opt
    python e2e_aws.py bookstore --only opt      # one variant
    python e2e_aws.py bookstore --keep          # do not delete the stack (debugging)

Outputs under Mono2Sls-artifacts/tests/results-e2e/<app>/:
  <variant>.jest.json        per-test results (jest --json)
  <variant>.metrics.json     Lambda REPORT lines (duration, billed, memory,
                             init) and DynamoDB consumed capacity during the run
  report.json / report.md    behaviour diff (per test, vs the monolith) and
                             latency / cost comparison

Costs real money (small: a few hundred invocations per variant) and deploys
into the default AWS profile. Stacks are named m2s-e2e-<app>-<variant> and
deleted after each variant unless --keep.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import boto3

sys.path.insert(0, str(Path(__file__).resolve().parent))
import verify  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
ART = ROOT / "Mono2Sls-artifacts"
RUNS = ART / "M2S" / "runs"
TESTS = ART / "tests"
MONO = ART / "mono_benchmark"
WORK = Path("D:/tmp/m2s-e2e")  # short path: sam build breaks on Windows MAX_PATH
REGION = "us-east-1"
PRICES = json.loads((Path(__file__).resolve().parent / "perf-rules" / "prices.json").read_text(encoding="utf-8"))
API_GW_REST_PER_REQUEST = 3.5e-06  # REST API, first tier (us-east-1)
API_GW_HTTP_PER_REQUEST = 1.0e-06  # HTTP API, first tier (us-east-1)
SAM = shutil.which("sam") or "sam"
NPX = shutil.which("npx") or "npx"
NODE = shutil.which("node") or "node"
NPM = shutil.which("npm") or "npm"
DDB_LOCAL_PORT = 8765

# Per app: the workspace, how the suite's target config is built from stack
# outputs / table logical ids, and how the monolith runs locally.
APPS: dict[str, dict[str, Any]] = {
    "bookstore": {
        "workspace": "bookstore-v2",
        "suite": "e2e-test-suite-bookstore",
        "outputs": {"baseUrl": "BookstoreApiUrl", "userPoolId": "CognitoUserPoolId", "clientId": "CognitoUserPoolClientId"},
        "tables": {"booksTable": "BooksTable", "cartTable": "CartTable", "ordersTable": "OrdersTable",
                   "bestsellersTable": "BestsellersTable"},
        "base": {"pathPrefix": "api", "capabilities": {"supportsAuthCompatibility": False, "asyncBestsellersAfterOrder": True},
                 "timeouts": {"requestTimeoutMs": 30000, "eventualConsistencyTimeoutMs": 30000, "pollIntervalMs": 2000}},
        "monolith": {
            "dir": "bookstore", "kind": "node", "start": ["server.js"], "init": ["scripts/init-db.js"], "port": 3301,
            "env": {"PORT": "3301", "JWT_SECRET": "e2e-secret", "AWS_REGION": REGION},
            "tables": {"booksTable": "Bookstore-Books", "cartTable": "Bookstore-Cart", "ordersTable": "Bookstore-Orders",
                       "bestsellersTable": "Bookstore-Bestsellers", "usersTable": "Bookstore-Users"},
            "auth": {"registerEndpoint": "/auth/register", "loginEndpoint": "/auth/login", "registerUserIdField": "name"},
        },
        # authenticated workload for latency / cost (the suite treats the
        # catalog as public, so its calls stop at the gateway on both sides)
        "workload": {
            "setup": [("POST", "/orders", {"books": [{"bookId": "book-001", "quantity": 1, "price": 99},
                                                     {"bookId": "book-004", "quantity": 2, "price": 89}]})] * 5,
            "settle_s": 20,
            "rounds": 30,
            "calls": [("GET", "/books", None), ("GET", "/books/book-001", None), ("GET", "/search?q=javascript", None),
                      ("GET", "/bestsellers", None), ("GET", "/cart", None), ("GET", "/orders", None)],
        },
    },
    "airline": {
        "workspace": "airline-booking-e0",
        "suite": "e2e-test-suite-airline-booking",
        "outputs": {"baseUrl": "ApiUrl", "userPoolId": "UserPoolId", "clientId": "UserPoolClientId"},
        "tables": {"flightTable": "FlightTable", "bookingTable": "BookingTable", "loyaltyTable": "LoyaltyTable",
                   "paymentTable": "PaymentTable"},
        "base": {"pathPrefix": "", "paymentMode": "simulation",
                 "capabilities": {"supportsAuthCompatibility": False, "supportsAdminBootstrap": False,
                                  "asyncLoyaltyAfterBooking": True, "asyncSeatReleaseAfterCancel": False},
                 "timeouts": {"requestTimeoutMs": 30000, "eventualConsistencyTimeoutMs": 30000, "pollIntervalMs": 2000}},
        "seed": [{"file": "seed-flights.js", "array": "FLIGHTS", "table_field": "flightTable"}],
        "monolith": {
            "dir": "airline-booking", "kind": "python", "port": 5301,
            "start": ["-c", "import app; app.app.run(host='127.0.0.1', port=5301, debug=False, use_reloader=False)"],
            "init": ["init_dynamodb_tables.py"],
            "env": {"AWS_REGION": REGION, "STAGE": "dev", "JWT_SECRET_KEY": "e2e-secret", "PYTHONIOENCODING": "utf-8"},
            "tables": {"flightTable": "Airline-Flight-dev", "bookingTable": "Airline-Booking-dev",
                       "loyaltyTable": "Airline-Loyalty-dev", "usersTable": "Airline-Users-dev"},
            "auth": {"registerEndpoint": "/auth/register", "loginEndpoint": "/auth/login", "registerUserIdField": "userId"},
        },
        "workload": {
            "setup": [("POST", "/bookings", {"outboundFlightId": "FL001", "chargeId": "ch_test_e2e_perf", "name": "perf"})] * 5,
            "settle_s": 20,
            "rounds": 30,
            "calls": [("GET", "/flights/search?departureCode=LAX&arrivalCode=SFO&departureDate=2025-11-10", None),
                      ("GET", "/flights/FL001", None), ("GET", "/customers/{sub}/bookings", None), ("GET", "/loyalty/{sub}", None)],
        },
    },
}


def sh(cmd: list[str], cwd: Path | None = None, env: dict | None = None, check: bool = True, log: Path | None = None,
       timeout: int | None = None) -> subprocess.CompletedProcess:
    full_env = {**os.environ, **(env or {})}
    if log:
        with log.open("a", encoding="utf-8") as f:
            f.write(f"\n$ {' '.join(cmd)}\n")
            p = subprocess.run(cmd, cwd=cwd, env=full_env, stdout=f, stderr=subprocess.STDOUT, text=True,
                               stdin=subprocess.DEVNULL, timeout=timeout)
    else:
        p = subprocess.run(cmd, cwd=cwd, env=full_env, capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, timeout=timeout)
    if check and p.returncode != 0:
        raise RuntimeError(f"command failed ({p.returncode}): {' '.join(cmd)}" + (f" -- see {log}" if log else f"\n{p.stdout}\n{p.stderr}"))
    return p


# ---------------------------------------------------------------------------
# Suite
# ---------------------------------------------------------------------------

def run_suite_repeated(app: dict, target: str, config: dict, out: Path, env: dict | None = None, runs: int = 2) -> dict:
    """The suite `runs` times against one deployment: a test whose outcome
    differs between runs is reported as "flaky" (seen on this client's
    network path to AWS), not as a behaviour difference."""
    results = [run_suite(app, target, config, out.with_name(f"{out.name}.run{i + 1}"), env) for i in range(runs)]
    tests = {}
    for name in sorted({t for r in results for t in r.get("tests", {})}):
        seen = {r.get("tests", {}).get(name) for r in results} - {None}
        tests[name] = seen.pop() if len(seen) == 1 else "flaky"
    summary = {"totalTests": len(tests), "passed": sum(v == "passed" for v in tests.values()),
               "failed": sum(v == "failed" for v in tests.values()), "flaky": sum(v == "flaky" for v in tests.values()),
               "runs": [r.get("summary") for r in results]}
    return {"started": results[0]["started"], "ended": results[-1]["ended"], "summary": summary, "tests": tests,
            "categories": results[-1].get("categories")}


def run_suite(app: dict, target: str, config: dict, out: Path, env: dict | None = None) -> dict:
    suite = TESTS / app["suite"]
    if not (suite / "node_modules").exists():
        sh([NPM, "ci"], cwd=suite, log=out.with_suffix(".log"))
    cfg_path = suite / "config" / f"{target}.json"
    cfg_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
    started = time.time()
    sh([NPX, "jest", "--runInBand", "--forceExit"], cwd=suite, env={"TARGET": target, **(env or {})}, check=False,
       log=out.with_suffix(".log"), timeout=30 * 60)
    ended = time.time()
    # the suite's own reporter writes results/<target>_<timestamp>.json
    files = sorted((suite / "results").glob(f"{target}_*.json"), key=lambda f: f.stat().st_mtime)
    files = [f for f in files if f.stat().st_mtime >= started - 1]
    if not files:
        return {"started": started, "ended": ended, "summary": None, "tests": {}, "error": "suite wrote no results file"}
    shutil.copy2(files[-1], out.with_suffix(".results.json"))
    results = json.loads(files[-1].read_text(encoding="utf-8"))
    return {"started": started, "ended": ended,
            "summary": {k: results.get(k) for k in ("totalTests", "passed", "failed", "pending", "passRate")},
            "categories": {k: results[k] for k in ("core", "authCompatibility", "asyncSemantics", "robustness") if k in results},
            "tests": {f"{t.get('suite')} > {t.get('name')}": t.get("status") for t in results.get("tests", [])}}


def _prefix(app: dict) -> str:
    p = str(app["base"].get("pathPrefix") or "").strip("/")
    return f"/{p}" if p else ""


def run_workload(app: dict, base_url: str, auth_header: str, sub: str = "") -> dict:
    """Fixed authenticated workload: setup calls, a pause for async work,
    then `rounds` passes over the read calls; client-side latency per call.
    "{sub}" in a path is the caller's user id."""
    import requests
    w = app["workload"]
    http = requests.Session()
    http.trust_env = False  # direct: the local proxy breaks HTTPS to AWS, and localhost must not be proxied
    url = lambda path: base_url.rstrip("/") + _prefix(app) + path.replace("{sub}", sub)
    headers = {"Authorization": auth_header, "Content-Type": "application/json"}
    def call(m: str, p: str, b: Any) -> tuple[Any, float]:
        for attempt in (1, 2):  # one retry: a stalled connection on this client's network is not the service's fault
            t0 = time.perf_counter()
            try:
                return http.request(m, url(p), json=b, headers=headers, timeout=30).status_code, (time.perf_counter() - t0) * 1000
            except requests.RequestException as exc:
                err = type(exc).__name__
        return err, (time.perf_counter() - t0) * 1000

    setup = [call(m, p, b)[0] for m, p, b in w.get("setup", [])]
    time.sleep(w.get("settle_s", 0))
    started = time.time()
    per = {f"{m} {p}": {"ms": [], "status": []} for m, p, _ in w["calls"]}
    for _ in range(w["rounds"]):
        for m, p, b in w["calls"]:
            status, ms = call(m, p, b)
            if isinstance(status, int):
                per[f"{m} {p}"]["ms"].append(ms)
            per[f"{m} {p}"]["status"].append(status)
    ended = time.time()
    summary = {k: {"p50_ms": round(statistics.median(v["ms"]), 1) if v["ms"] else None,
                   "p90_ms": round(sorted(v["ms"])[int(0.9 * (len(v["ms"]) - 1))], 1) if v["ms"] else None,
                   "statuses": sorted(set(map(str, v["status"]))), "errors": sum(1 for x in v["status"] if not isinstance(x, int))}
               for k, v in per.items()}
    return {"started": started, "ended": ended, "setup_statuses": setup, "calls": summary}


def cognito_session(app: dict, cfg: dict) -> tuple[str, str]:
    suite = TESTS / app["suite"]
    env = {"USER_POOL_ID": cfg["authConfig"]["userPoolId"], "USER_POOL_CLIENT_ID": cfg["authConfig"]["clientId"],
           "AWS_REGION": REGION, "USER_EMAIL_DOMAIN": "example.com", "DEFAULT_PASSWORD": "TestPass123!"}
    out = sh([NODE, "auth/cognito-cli.js", "create-session", "perf"], cwd=suite, env=env).stdout
    session = json.loads(out)
    return session["authHeader"], session.get("sub") or _jwt_claims(session["authHeader"]).get("sub", "")


def _jwt_claims(auth_header: str) -> dict:
    import base64
    payload = auth_header.split()[-1].split(".")[1]
    return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))


def jwt_session(app: dict, base_url: str) -> tuple[str, str]:
    import requests
    a = app["monolith"]["auth"]
    http = requests.Session()
    http.trust_env = False
    email, pw = f"perf-{int(time.time())}@example.com", "TestPass123!"
    user_id = f"perf-{int(time.time())}"
    http.post(base_url + _prefix(app) + a["registerEndpoint"],
              json={"email": email, "password": pw, a.get("registerUserIdField", "userId"): user_id}, timeout=30)
    data = http.post(base_url + _prefix(app) + a["loginEndpoint"], json={"email": email, "password": pw}, timeout=30).json()
    token = next(data[k] for k in ("accessToken", "token", "access_token", "idToken") if k in data)
    claims = _jwt_claims(f"Bearer {token}")
    return f"Bearer {token}", str(claims.get("sub") or claims.get("userId") or user_id)


def seed(app: dict, table_of, env: dict, log: Path) -> None:
    """Items a suite expects but does not write itself (e.g. airline flights)."""
    suite = TESTS / app["suite"]
    if app.get("seed") and not (suite / "node_modules").exists():
        sh([NPM, "ci"], cwd=suite, log=log, timeout=1200)
    for s in app.get("seed", []):
        sh([NODE, str(Path(__file__).resolve().parent / "e2e-tools" / "seed-from-suite.js"), str(suite), s["file"], s["array"],
            table_of(s["table_field"]), REGION], env=env, log=log, timeout=120)


# ---------------------------------------------------------------------------
# Serverless variants
# ---------------------------------------------------------------------------

def export_variant(ws: Path, ref: str, dest: Path) -> Path:
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    archive = dest.parent / f"{dest.name}.tar"
    sh(["git", "-C", str(ws), "archive", "--format=tar", f"--output={archive}", ref, "output"])
    sh(["tar", "-xf", str(archive), "-C", str(dest)])
    archive.unlink()
    return dest / "output"


def environment_value(template: Path, variant: str) -> str:
    """A value for the template's Environment parameter that is allowed and
    differs between the variants (their table names embed it)."""
    t = verify.gates.load_template(template)
    param = (t.get("Parameters") or {}).get("Environment") or {}
    allowed = param.get("AllowedValues") or []
    if not allowed:
        return f"e2e{variant}"
    order = ["v0", "opt", "v0-http"]  # distinct table names for every variant
    return allowed[min(order.index(variant) if variant in order else 0, len(allowed) - 1)]


def _retry(fn, attempts: int = 6, wait: float = 10):
    """Control-plane calls from this machine sometimes die with SSL EOFs on the
    proxy path; retry them instead of treating them as answers."""
    from botocore.exceptions import EndpointConnectionError, SSLError, ConnectionClosedError, ReadTimeoutError
    for i in range(attempts):
        try:
            return fn()
        except (EndpointConnectionError, SSLError, ConnectionClosedError, ReadTimeoutError):
            if i == attempts - 1:
                raise
            time.sleep(wait)


def stack_status(stack: str) -> str | None:
    cf = boto3.client("cloudformation", region_name=REGION)

    def get():
        try:
            return cf.describe_stacks(StackName=stack)["Stacks"][0]["StackStatus"]
        except cf.exceptions.ClientError as exc:
            if "does not exist" in str(exc):
                return None
            raise
    return _retry(get)


def stack_exists(stack: str) -> bool:
    return stack_status(stack) not in (None, "DELETE_COMPLETE")


def stack_info(stack: str) -> tuple[dict, dict]:
    cf = boto3.client("cloudformation", region_name=REGION)
    outputs = {o["OutputKey"]: o["OutputValue"] for o in _retry(lambda: cf.describe_stacks(StackName=stack))["Stacks"][0].get("Outputs", [])}
    resources = {}
    for page in _retry(lambda: list(cf.get_paginator("list_stack_resources").paginate(StackName=stack))):
        for r in page["StackResourceSummaries"]:
            resources[r["LogicalResourceId"]] = {"type": r["ResourceType"], "physical": r.get("PhysicalResourceId")}
    return outputs, resources


def deploy(app_name: str, variant: str, src: Path, log: Path, reuse: bool = False) -> tuple[str, dict, dict]:
    stack = f"m2s-e2e-{app_name}-{variant}"
    if reuse and stack_status(stack) in ("CREATE_COMPLETE", "UPDATE_COMPLETE"):
        with log.open("a", encoding="utf-8") as f:
            f.write(f"reusing deployed stack {stack}\n")
        return (stack, *stack_info(stack))
    env_tag = environment_value(src / "template.yaml", variant)
    # sam build needs an interpreter matching the python runtime (verify.py keeps a 3.12 one)
    py312 = ({"PATH": os.pathsep.join([str(verify.PY312_DIR), str(verify.PY312_DIR / "Scripts"), os.environ.get("PATH", "")])}
             if (verify.PY312_DIR / "python.exe").exists() else {})
    sh([SAM, "build"], cwd=src, env=py312, log=log, timeout=30 * 60)
    p = sh([SAM, "deploy", "--stack-name", stack, "--region", REGION, "--resolve-s3", "--no-confirm-changeset",
            "--no-fail-on-empty-changeset", "--capabilities", "CAPABILITY_IAM", "CAPABILITY_NAMED_IAM", "CAPABILITY_AUTO_EXPAND",
            "--parameter-overrides", f"Environment={env_tag}"], cwd=src, log=log, timeout=45 * 60, check=False)
    if p.returncode != 0:
        # sam may lose its connection while CloudFormation keeps going: wait for the real outcome
        for _ in range(90):
            st = stack_status(stack)
            if st is None or not st.endswith("_IN_PROGRESS"):
                break
            time.sleep(20)
        if st not in ("CREATE_COMPLETE", "UPDATE_COMPLETE"):
            raise RuntimeError(f"sam deploy failed and the stack ended in {st} -- see {log}")
        with log.open("a", encoding="utf-8") as f:
            f.write(f"sam deploy reported failure but the stack reached {st}; continuing\n")
    return (stack, *stack_info(stack))


def delete(stack: str, log: Path) -> None:
    """sam delete, then the resources the templates mark DeletionPolicy:
    Retain (DynamoDB tables, log groups): left behind they collide with the
    next deploy's fixed names and keep costing."""
    cf = boto3.client("cloudformation", region_name=REGION)
    retained, apis = [], []
    try:
        for page in cf.get_paginator("list_stack_resources").paginate(StackName=stack):
            retained += [(r["ResourceType"], r.get("PhysicalResourceId")) for r in page["StackResourceSummaries"]
                         if r["ResourceType"] in ("AWS::DynamoDB::Table", "AWS::Logs::LogGroup") and r.get("PhysicalResourceId")]
            apis += [r["PhysicalResourceId"] for r in page["StackResourceSummaries"]
                     if r["ResourceType"] == "AWS::ApiGateway::RestApi" and r.get("PhysicalResourceId")]
    except cf.exceptions.ClientError:
        pass
    sh([SAM, "delete", "--stack-name", stack, "--region", REGION, "--no-prompts"], log=log, check=False, timeout=30 * 60)
    ddb = boto3.client("dynamodb", region_name=REGION)
    logs = boto3.client("logs", region_name=REGION)
    # log groups Lambda / API Gateway create on their own (not stack resources);
    # their contents were downloaded before the delete
    for prefix in [f"/aws/lambda/{stack}-", *[f"API-Gateway-Execution-Logs_{api}/" for api in apis]]:
        for page in logs.get_paginator("describe_log_groups").paginate(logGroupNamePrefix=prefix):
            for g in page["logGroups"]:
                retained.append(("AWS::Logs::LogGroup", g["logGroupName"]))
    for kind, name in retained:
        try:
            if kind == "AWS::DynamoDB::Table":
                ddb.delete_table(TableName=name)
            else:
                logs.delete_log_group(logGroupName=name)
            with log.open("a", encoding="utf-8") as f:
                f.write(f"deleted retained {kind} {name}\n")
        except Exception:  # noqa: BLE001 -- already gone
            pass


def serverless_config(app: dict, target: str, outputs: dict, resources: dict) -> dict:
    o = app["outputs"]
    cfg = {"configType": "target", "name": target, "shortName": target, "region": REGION, "authType": "cognito",
           "baseUrl": outputs[o["baseUrl"]].rstrip("/"), **app["base"],
           "authConfig": {"userPoolId": outputs[o["userPoolId"]], "clientId": outputs[o["clientId"]], "region": REGION,
                          "userEmailDomain": "example.com", "defaultPassword": "TestPass123!"}}
    for field, logical in app["tables"].items():
        # a workspace may drop a table the suite knows about (airline-booking-c3:
        # no Payment table); tests that read it then fail and show in the report
        if logical in resources:
            cfg[field] = resources[logical]["physical"]
        else:
            print(f"  note: stack has no {logical}; {field} left unset", flush=True)
    # stage already in the URL from the stack output; the suite adds pathPrefix
    if cfg["baseUrl"].endswith("/" + app["base"].get("pathPrefix", "")):
        cfg["baseUrl"] = cfg["baseUrl"][: -(len(app["base"]["pathPrefix"]) + 1)]
    return cfg


def collect_metrics(resources: dict, started: float, ended: float, raw_dir: Path | None = None,
                    until: float | None = None, delay: float = 90) -> dict:
    """Lambda REPORT lines from each function's log group, and DynamoDB
    consumed capacity per table and per GSI, for the test window.

    REPORT lines count only when written in [started, until): `until`
    defaults to ended + 60 s and must not reach into the next pass, or that
    pass's invocations get counted twice. DynamoDB metrics only exist per
    minute, so capacity is the sum over the whole minutes [floor(started),
    floor(ended)]: the caller has to keep other traffic (loader, count
    checks, the next pass) out of those minutes -- see e2e_ec2.minute_gap.

    delay: wait before querying (REPORT lines and CloudWatch metrics arrive
    late); a caller collecting several passes at once can do one wait and
    pass delay=0 for the rest."""
    until = ended + 60 if until is None else until
    logs = boto3.client("logs", region_name=REGION)
    cw = boto3.client("cloudwatch", region_name=REGION)
    if delay:
        time.sleep(delay)
    fns = {}
    rx = re.compile(r"Duration: ([\d.]+) ms\s+Billed Duration: (\d+) ms\s+Memory Size: (\d+) MB\s+Max Memory Used: (\d+) MB(?:\s+Init Duration: ([\d.]+) ms)?")
    for logical, r in resources.items():
        if r["type"] != "AWS::Lambda::Function":
            continue
        group = f"/aws/lambda/{r['physical']}"
        reports, raw, seen = [], [], set()
        kwargs = {"logGroupName": group, "startTime": int(started * 1000), "endTime": int(until * 1000)}
        try:
            for page in logs.get_paginator("filter_log_events").paginate(**kwargs):
                for e in page["events"]:
                    raw.append(f"{e['timestamp']}\t{e.get('logStreamName', '')}\t{e['message'].rstrip()}")
                    if not e["message"].startswith("REPORT"):
                        continue
                    rid = e["message"].split()[2] if len(e["message"].split()) > 2 else e["eventId"]
                    if rid in seen:
                        continue
                    seen.add(rid)
                    m = rx.search(e["message"])
                    if m:
                        reports.append({"duration": float(m[1]), "billed": int(m[2]), "memory": int(m[3]), "max_memory": int(m[4]),
                                        "init": float(m[5]) if m[5] else None})
        except logs.exceptions.ResourceNotFoundException:
            pass
        fns[logical] = reports
        if raw_dir:
            (raw_dir / "logs").mkdir(parents=True, exist_ok=True)
            (raw_dir / "logs" / f"{logical}.log").write_text("\n".join(raw) + "\n", encoding="utf-8")
    if raw_dir:
        for api in [r["physical"] for r in resources.values() if r["type"] == "AWS::ApiGateway::RestApi"]:
            for page in logs.get_paginator("describe_log_groups").paginate(logGroupNamePrefix=f"API-Gateway-Execution-Logs_{api}/"):
                for g in page["logGroups"]:
                    lines = [f"{e['timestamp']}\t{e['message'].rstrip()}" for pg in logs.get_paginator("filter_log_events").paginate(
                        logGroupName=g["logGroupName"], startTime=int(started * 1000), endTime=int(until * 1000)) for e in pg["events"]]
                    (raw_dir / "logs").mkdir(parents=True, exist_ok=True)
                    (raw_dir / "logs" / ("apigw-execution-" + g["logGroupName"].split("/")[-1] + ".log")).write_text("\n".join(lines) + "\n", encoding="utf-8")
    # API Gateway access logs and any other log groups the stack owns
    if raw_dir:
        for logical, r in resources.items():
            if r["type"] != "AWS::Logs::LogGroup":
                continue
            lines = []
            try:
                for page in logs.get_paginator("filter_log_events").paginate(
                        logGroupName=r["physical"], startTime=int(started * 1000), endTime=int(until * 1000)):
                    lines += [f"{e['timestamp']}\t{e['message'].rstrip()}" for e in page["events"]]
            except logs.exceptions.ResourceNotFoundException:
                pass
            (raw_dir / "logs").mkdir(parents=True, exist_ok=True)
            (raw_dir / "logs" / f"{logical}.log").write_text("\n".join(lines) + "\n", encoding="utf-8")
    tables = {}
    m0, m1 = int(started // 60 * 60), int(ended // 60 * 60) + 60  # whole minutes the pass touches
    for logical, r in resources.items():
        if r["type"] != "AWS::DynamoDB::Table":
            continue
        base = [{"Name": "TableName", "Value": r["physical"]}]
        # GSI capacity is published under its own dimension, not in the table's figure
        gsis = sorted({d["Value"] for mt in cw.list_metrics(Namespace="AWS/DynamoDB", Dimensions=base)["Metrics"]
                       for d in mt["Dimensions"] if d["Name"] == "GlobalSecondaryIndexName"})
        for target, dims in [(logical, base)] + [(f"{logical}/{g}", base + [{"Name": "GlobalSecondaryIndexName", "Value": g}]) for g in gsis]:
            t = {}
            for metric in ("ConsumedReadCapacityUnits", "ConsumedWriteCapacityUnits"):
                pts = cw.get_metric_statistics(Namespace="AWS/DynamoDB", MetricName=metric, Dimensions=dims,
                                               StartTime=m0, EndTime=m1, Period=60, Statistics=["Sum"])["Datapoints"]
                t[metric] = sum(p["Sum"] for p in pts)
                t[metric + "_datapoints"] = sorted(({"time": p["Timestamp"].isoformat(), "sum": p["Sum"]} for p in pts), key=lambda x: x["time"])
            tables[target] = t
    return {"functions": fns, "tables": tables}


def summarize_metrics(m: dict) -> dict:
    per_fn = {}
    gb_s = invocations = 0.0
    for fn, reps in m["functions"].items():
        if not reps:
            continue
        warm = [r["duration"] for r in reps if r["init"] is None]
        cold = [r for r in reps if r["init"] is not None]
        per_fn[fn] = {"invocations": len(reps), "cold": len(cold),
                      # nearest rank for both: a median of two would average them and could exceed this p90
                      "warm_p50_ms": round(sorted(warm)[int(0.5 * (len(warm) - 1))], 1) if warm else None,
                      "warm_p90_ms": round(sorted(warm)[int(0.9 * (len(warm) - 1))], 1) if warm else None,
                      "warm_n": len(warm),
                      "init_p50_ms": round(statistics.median(r["init"] for r in cold), 1) if cold else None,
                      "max_memory_mb": max(r["max_memory"] for r in reps)}
        gb_s += sum(r["billed"] / 1000 * r["memory"] / 1024 for r in reps)
        invocations += len(reps)
    rru = sum(t["ConsumedReadCapacityUnits"] for t in m["tables"].values())
    wru = sum(t["ConsumedWriteCapacityUnits"] for t in m["tables"].values())
    return {"functions": per_fn, "invocations": int(invocations), "lambda_gb_seconds": round(gb_s, 3), "rru": rru, "wru": wru}


def cost(summary: dict, arch: str, api_requests: int | None = None, api_price: float = API_GW_REST_PER_REQUEST) -> dict:
    """api_requests: client requests that went through API Gateway; defaults
    to the Lambda invocation count (which also counts non-HTTP invokes)."""
    gb = PRICES["lambda_gb_second_arm64"] if arch == "arm64" else PRICES["lambda_gb_second"]
    c = {"lambda_duration": summary["lambda_gb_seconds"] * gb, "lambda_requests": summary["invocations"] * PRICES["lambda_request"],
         "dynamodb": summary["rru"] * PRICES["dynamodb_read_request_unit"] + summary["wru"] * PRICES["dynamodb_write_request_unit"],
         "api_gateway": (summary["invocations"] if api_requests is None else api_requests) * api_price}
    c["total"] = sum(c.values())
    return {k: round(v, 8) for k, v in c.items()}


def run_serverless(app_name: str, app: dict, variant: str, ref: str, out_dir: Path, keep: bool, reuse: bool = False) -> dict:
    log = out_dir / f"{variant}.log"
    log.write_text("", encoding="utf-8")
    src = export_variant(RUNS / app["workspace"], ref, WORK / f"{app_name}-{variant}")
    stack = f"m2s-e2e-{app_name}-{variant}"
    try:
        stack, outputs, resources = deploy(app_name, variant, src, log, reuse)
        target = f"e2e_{app_name}_{variant}"
        # axios mishandles HTTPS through the local HTTP proxy (answers 503);
        # AWS endpoints are reachable directly from this machine.
        no_proxy = {"NO_PROXY": ".amazonaws.com,127.0.0.1,localhost", "no_proxy": ".amazonaws.com,127.0.0.1,localhost"}
        # Suites that call the AWS SDK inside jest (airline's Cognito provider):
        # the SDK's default chain dynamic-imports the shared-file provider, which
        # jest rejects without --experimental-vm-modules; credentials in the
        # environment resolve first and avoid that import.
        creds = boto3.Session().get_credentials().get_frozen_credentials()
        no_proxy.update({"AWS_ACCESS_KEY_ID": creds.access_key, "AWS_SECRET_ACCESS_KEY": creds.secret_key, "AWS_REGION": REGION,
                         **({"AWS_SESSION_TOKEN": creds.token} if creds.token else {})})
        cfg = serverless_config(app, target, outputs, resources)
        seed(app, lambda field: cfg[field], {}, log)
        res = run_suite_repeated(app, target, cfg, out_dir / variant, env=no_proxy)
        if app.get("workload"):
            try:
                auth_header, sub = cognito_session(app, cfg)
                res["workload"] = run_workload(app, cfg["baseUrl"], auth_header, sub)
                res["ended"] = res["workload"]["ended"]
            except Exception as exc:  # noqa: BLE001
                res["workload"] = {"error": str(exc)[:500]}
        raw_dir = out_dir / f"{variant}-aws"
        raw_dir.mkdir(exist_ok=True)
        (raw_dir / "stack.json").write_text(json.dumps({"stack": stack, "outputs": outputs, "resources": resources}, indent=2), encoding="utf-8")
        metrics = collect_metrics(resources, res["started"], res["ended"], raw_dir)
        (out_dir / f"{variant}.metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
        summary = summarize_metrics(metrics)
        arch = "arm64" if re.search(r"Architectures:\s*\n\s*-\s*arm64", (src / "template.yaml").read_text(encoding="utf-8")) else "x86_64"
        return {"variant": variant, "ref": ref, "commit": sh(["git", "-C", str(RUNS / app["workspace"]), "rev-parse", "--short", ref]).stdout.strip(),
                "architecture": arch, **res, "metrics": summary, "cost_usd": cost(summary, arch)}
    finally:
        if not keep and stack_exists(stack):
            delete(stack, log)


# ---------------------------------------------------------------------------
# Monolith (local, DynamoDB Local)
# ---------------------------------------------------------------------------

PRELOAD = """// Points the monolith's aws-sdk v2 at DynamoDB Local without editing it.
const AWS = require(require.resolve('aws-sdk', { paths: [process.cwd()] }));
AWS.config.update({ endpoint: process.env.DDB_ENDPOINT, region: process.env.AWS_REGION || 'us-east-1',
                    accessKeyId: 'local', secretAccessKey: 'local' });
"""


def python_venv(app_name: str, src: Path, log: Path) -> str:
    """An isolated venv with the monolith's pinned requirements (kept under
    WORK, reused across runs; nothing is installed into the caller's env)."""
    venv = WORK / f"venv-{app_name}"
    py = venv / "Scripts" / "python.exe" if os.name == "nt" else venv / "bin" / "python"
    if not py.exists():
        sh([sys.executable, "-m", "venv", str(venv)], log=log)
        sh([str(py), "-m", "pip", "install", "-q", "-r", str(src / "requirements.txt")], log=log, timeout=1200)
    return str(py)


def run_monolith(app_name: str, app: dict, out_dir: Path) -> dict:
    mono = app["monolith"]
    src = MONO / mono["dir"]
    log = out_dir / "monolith.log"
    log.write_text("", encoding="utf-8")
    if mono["kind"] == "node" and not (src / "node_modules").exists():
        sh([NPM, "ci" if (src / "package-lock.json").exists() else "install"], cwd=src, log=log)
    container = f"m2s-e2e-ddb-{app_name}"
    sh(["docker", "rm", "-f", container], check=False)
    spec = verify.ddb_backend().get("ddb_local")
    if not spec:
        raise RuntimeError("no DynamoDB Local image available (see verify.ddb_backend)")
    sh(["docker", "run", "-d", "--name", container, "-p", f"127.0.0.1:{DDB_LOCAL_PORT}:8000",
        *(["--entrypoint", spec["entrypoint"]] if spec.get("entrypoint") else []), spec["image"], *spec["args"]],
       env={"MSYS_NO_PATHCONV": "1"})
    preload = WORK / "ddb-local-preload.js"
    WORK.mkdir(parents=True, exist_ok=True)
    preload.write_text(PRELOAD, encoding="utf-8")
    endpoint = f"http://127.0.0.1:{DDB_LOCAL_PORT}"
    env = {**mono["env"], "DDB_ENDPOINT": endpoint, "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost",
           "AWS_ACCESS_KEY_ID": "local", "AWS_SECRET_ACCESS_KEY": "local",
           # boto3 >= 1.28 and the JS SDK v3 honour this; aws-sdk v2 (Node monoliths) gets the preload instead
           "AWS_ENDPOINT_URL_DYNAMODB": endpoint}
    runner = [python_venv(app_name, src, log)] if mono["kind"] == "python" else [NODE]
    if mono["kind"] == "node":
        env["NODE_OPTIONS"] = f"--require {preload.as_posix()}"
    server = None
    try:
        time.sleep(12)  # the localstack-bundled DynamoDB Local JVM starts slowly
        for script in mono.get("init", []):
            sh([*runner, script], cwd=src, env=env, log=log, timeout=300)
        server = subprocess.Popen([*runner, *mono["start"]], cwd=src, env={**os.environ, **env},
                                  stdout=log.open("a", encoding="utf-8"), stderr=subprocess.STDOUT)
        time.sleep(6)
        seed(app, lambda field: mono["tables"][field], {"AWS_ENDPOINT_URL_DYNAMODB": endpoint, "AWS_ACCESS_KEY_ID": "local",
                                                         "AWS_SECRET_ACCESS_KEY": "local"}, log)
        target = f"e2e_{app_name}_monolith"
        cfg = {"configType": "target", "name": target, "shortName": target, "region": REGION, "authType": "custom_jwt",
               "baseUrl": f"http://127.0.0.1:{mono['port']}", **app["base"], **mono["tables"],
               "authConfig": {"userEmailDomain": "example.com", "defaultPassword": "TestPass123!", **mono["auth"]}}
        suite_env = {"NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost",
                     "AWS_ENDPOINT_URL_DYNAMODB": endpoint, "AWS_ACCESS_KEY_ID": "local", "AWS_SECRET_ACCESS_KEY": "local",
                     "AWS_REGION": REGION}
        res = run_suite_repeated(app, target, cfg, out_dir / "monolith", env=suite_env)
        if app.get("workload"):
            auth_header, sub = jwt_session(app, cfg["baseUrl"])
            res["workload"] = run_workload(app, cfg["baseUrl"], auth_header, sub)
        return {"variant": "monolith", **res}
    finally:
        if server:
            server.terminate()
        sh(["docker", "rm", "-f", container], check=False)


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def report(app_name: str, results: dict[str, dict], out_dir: Path) -> None:
    names = sorted({t for r in results.values() for t in r.get("tests", {})})
    rows = []
    for t in names:
        st = {v: results[v].get("tests", {}).get(t, "-") for v in results}
        mono = st.get("monolith")
        rows.append({"test": t, **st,
                     "diff_vs_monolith": [v for v in st if v != "monolith" and mono not in (None, "-", "flaky")
                                          and st[v] not in ("-", "flaky") and st[v] != mono],
                     "flaky": [v for v in st if st[v] == "flaky"]})
    doc = {"app": app_name, "generated_at": time.strftime("%Y-%m-%d %H:%M"), "variants": {v: {k: r.get(k) for k in ("ref", "commit", "architecture", "summary", "metrics", "cost_usd")}
                                                                                       for v, r in results.items()},
           "tests": rows}
    (out_dir / "report.json").write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
    md = [f"# {app_name}: monolith vs v0 vs optimized (AWS {REGION})", "",
          "| variant | commit | arch | tests passed | invocations | Lambda GB-s | RRU | WRU | est. cost USD |", "|---|---|---|---|---|---|---|---|---|"]
    for v, r in results.items():
        s, m, c = r.get("summary") or {}, r.get("metrics") or {}, r.get("cost_usd") or {}
        md.append(f"| {v} | {r.get('commit', '-')} | {r.get('architecture', '-')} | {s.get('passed')}/{s.get('totalTests')}{(' (' + str(s.get('flaky')) + ' flaky)') if s.get('flaky') else ''} | "
                  f"{m.get('invocations', '-')} | {m.get('lambda_gb_seconds', '-')} | {m.get('rru', '-')} | {m.get('wru', '-')} | {c.get('total', '-')} |")
    fns = sorted({f for r in results.values() for f in (r.get("metrics") or {}).get("functions", {})})
    if fns:
        vs = [v for v in results if v != "monolith"]
        md += ["", "## Per function (warm p50 / p90 ms, cold starts)", "", "| function | " + " | ".join(vs) + " |", "|---|" + "---|" * len(vs)]
        for f in fns:
            cells = []
            for v in vs:
                x = (results[v].get("metrics") or {}).get("functions", {}).get(f)
                cells.append(f"{x['warm_p50_ms']} / {x['warm_p90_ms']} ({x['cold']} cold)" if x else "-")
            md.append(f"| {f} | " + " | ".join(cells) + " |")
    calls = sorted({c for r in results.values() for c in ((r.get("workload") or {}).get("calls") or {})})
    if calls:
        vs = list(results)
        md += ["", "## Authenticated workload, client-side latency (p50 / p90 ms, statuses)", "",
               "Monolith runs locally (no network hop); the AWS variants include the client-to-us-east-1 round trip.", "",
               "| call | " + " | ".join(vs) + " |", "|---|" + "---|" * len(vs)]
        for c in calls:
            cells = []
            for v in vs:
                x = ((results[v].get("workload") or {}).get("calls") or {}).get(c)
                cells.append(f"{x['p50_ms']} / {x['p90_ms']} {x['statuses']}" if x else "-")
            md.append(f"| {c} | " + " | ".join(cells) + " |")
    diffs = [r for r in rows if r["diff_vs_monolith"]]
    md += ["", f"## Behaviour differences vs the monolith ({len(diffs)})", ""]
    md += [f"- {r['test']}: " + ", ".join(f"{v}={r[v]}" for v in results) for r in diffs] or ["None."]
    flaky = [r for r in rows if r["flaky"]]
    md += ["", f"## Flaky (outcome differed between the two suite runs on one deployment) ({len(flaky)})", ""]
    md += [f"- {r['test']}: " + ", ".join(f"{v}={r[v]}" for v in results) for r in flaky] or ["None."]
    md += ["", "## Tests failing everywhere (suite contract vs the monolith, not a migration difference)", ""]
    md += [f"- {r['test']}" for r in rows if all(r[v] == "failed" for v in results)] or ["None."]
    (out_dir / "report.md").write_text("\n".join(md) + "\n", encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("app", choices=sorted(APPS))
    ap.add_argument("--only", choices=["monolith", "v0", "opt"], action="append")
    ap.add_argument("--keep", action="store_true", help="keep the stacks (debugging); delete them yourself with sam delete")
    ap.add_argument("--reuse", action="store_true", help="test an already deployed m2s-e2e-<app>-<variant> stack instead of deploying")
    ap.add_argument("--workspace", help="test this runs/<workspace> instead of the app's default one")
    ap.add_argument("--out", help="results-e2e/<out> (default: the app name)")
    ap.add_argument("--v0-ref", default="v0", help="git ref deployed as v0 (default: the v0 tag)")
    args = ap.parse_args()
    app = dict(APPS[args.app])
    if args.workspace:
        app["workspace"] = args.workspace
    out_dir = TESTS / "results-e2e" / (args.out or args.app)
    out_dir.mkdir(parents=True, exist_ok=True)
    results_path = out_dir / "results.json"
    results = json.loads(results_path.read_text(encoding="utf-8")) if results_path.exists() else {}
    for variant in args.only or ["monolith", "v0", "opt"]:
        print(f"== {args.app} {variant}", flush=True)
        try:
            results[variant] = run_monolith(args.app, app, out_dir) if variant == "monolith" else \
                run_serverless(args.app, app, variant, args.v0_ref if variant == "v0" else "HEAD", out_dir, args.keep, args.reuse)
        except Exception as exc:  # noqa: BLE001 -- keep going; the report shows the failure
            results[variant] = {"variant": variant, "error": str(exc)[:2000]}
        results_path.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
        print(json.dumps({k: results[variant].get(k) for k in ("summary", "error", "cost_usd")}, ensure_ascii=False), flush=True)
    report(args.app, {v: results[v] for v in ("monolith", "v0", "opt") if v in results}, out_dir)


if __name__ == "__main__":
    main()
