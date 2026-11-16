"""Starts a Python (WSGI) monolith for run_monolith (PIPELINE_V2_PLAN.md §4.3,
N3.1), the counterpart of monolith-preload.js for Node.

    python m2s_monolith.py <monolith_root> <app> <port> [<debug>]

<app> is "module:attr" (the WSGI application object) or "module:factory()".
The monolith runs in its own environment (a venv from its requirements.txt);
boto3 reaches the recording proxy through AWS_ENDPOINT_URL_DYNAMODB / _SQS,
set by monolith-run.js.

1. With M2S_COV_PORT set, coverage.py starts before the application is
   imported (import-time lines are the "load" coverage) and serves POST /take
   on that port: it switches to a fresh dynamic context and answers the lines
   run in the previous one, per file relative to the monolith root:
   {"files": {rel: {"ran": [...], "zero": [...]}}} -- "zero" being the file's
   other executable lines -- so each case gets its own lines.
2. botocore keeps its retry count (DynamoDB: 10 in legacy mode), but each
   retry waits at most 20 ms, like the Node preload: whether a throttled call
   ends in success or in an error is the monolith's behaviour; the backoff is
   timing only, and a long one would leak into the next case's record.
3. The application is served by werkzeug's run_simple, threaded, without the
   reloader (it would fork a second process) and without the interactive
   debugger. app.debug is <debug> ("1"/"0", default "0").
"""

from __future__ import annotations

import http.server
import importlib
import json
import os
import re
import sys
import threading

ROOT = os.path.abspath(sys.argv[1])
APP_SPEC = sys.argv[2]
PORT = int(sys.argv[3])
DEBUG = len(sys.argv) > 4 and sys.argv[4] == "1"
MAX_DELAY_S = 0.02

sys.path.insert(0, ROOT)
os.chdir(ROOT)

cov = None
cov_lock = threading.Lock()
context = {"name": "", "n": 0}
statements: dict[str, set[int]] = {}

COV_PORT = int(os.environ.get("M2S_COV_PORT") or 0)  # monolith-run.js sets "0" without coverage
if COV_PORT:
    import coverage

    cov = coverage.Coverage(data_file=None, source=[ROOT], omit=["*/site-packages/*", "*/.venv/*", "*/venv/*"])
    cov.start()


def _cap_retry_backoff() -> None:
    try:
        import botocore.retryhandler as legacy

        orig = legacy.delay_exponential

        def capped(base, growth_factor, attempts):
            return min(orig(base, growth_factor, attempts), MAX_DELAY_S)

        legacy.delay_exponential = capped
    except Exception:  # noqa: BLE001 -- an app without botocore
        pass
    try:
        from botocore.retries import standard

        orig_delay = standard.ExponentialBackoff.delay_amount

        def capped_amount(self, context):
            return min(orig_delay(self, context), MAX_DELAY_S)

        standard.ExponentialBackoff.delay_amount = capped_amount
    except Exception:  # noqa: BLE001
        pass


def _take() -> dict:
    """Lines of the context that ends now; a fresh context starts."""
    with cov_lock:
        prev = context["name"]
        context["n"] += 1
        context["name"] = f"m2s-{context['n']}"
        cov.switch_context(context["name"])
        data = cov.get_data()
        # Files the process ever ran (like V8 for Node): coverage.py with `source`
        # also lists files never imported (helper scripts next to the app).
        loaded = {path for path in data.measured_files() if data.lines(path)}
        data.set_query_contexts(["^" + re.escape(prev) + "$"])
        files = {}
        for path in sorted(loaded):
            ran = set(data.lines(path) or [])
            if path not in statements:
                try:
                    statements[path] = set(cov.analysis2(path)[1])
                except Exception:  # noqa: BLE001 -- not Python source
                    statements[path] = set()
            rel = os.path.relpath(path, ROOT).replace("\\", "/")
            if rel.startswith(".."):
                continue
            files[rel] = {"ran": sorted(ran), "zero": sorted(statements[path] - ran)}
        data.set_query_contexts(None)
        return {"files": files}


class _CovHandler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        try:
            body = json.dumps(_take()).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
        except Exception as exc:  # noqa: BLE001
            body = repr(exc).encode()
            self.send_response(500)
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # quiet
        pass


def _load_app():
    module_name, _, attr = APP_SPEC.partition(":")
    module = importlib.import_module(module_name)
    attr = attr or "app"
    if attr.endswith("()"):
        return getattr(module, attr[:-2])()
    return getattr(module, attr)


def main() -> None:
    _cap_retry_backoff()
    app = _load_app()
    if hasattr(app, "debug"):
        app.debug = DEBUG
    if cov is not None:
        server = http.server.ThreadingHTTPServer(("127.0.0.1", COV_PORT), _CovHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
    from werkzeug.serving import run_simple

    run_simple("127.0.0.1", PORT, app, threaded=True, use_reloader=False, use_debugger=False)


if __name__ == "__main__":
    main()
