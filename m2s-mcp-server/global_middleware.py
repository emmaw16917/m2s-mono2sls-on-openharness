"""Application-wide middleware that changes every response (P0).

Route-level extraction never sees these: a monolith's `CORS(app)`,
`app.use(cors())`, `app.use(helmet())`, an `@app.after_request` hook or an
`app.use((req, res, next) => res.set(...))` lives outside every route handler.
On API Gateway + Lambda proxy integration there is no app-wide layer any more:
each function's response must carry these headers itself, and the responses
API Gateway generates on its own (401/403/429/5xx) need GatewayResponses. If
the spec does not record them they are silently lost in migration (airline:
2026-10-04 E2E test ROB-01b, no Access-Control-Allow-Origin on Lambda
responses although the template had a Cors block, which only serves
preflight OPTIONS).

detect() returns report["global_middleware"]: one item per call site,
  {"kind": "cors" | "security_headers" | "response_header_hook",
   "framework": "flask-cors" | "express-cors" | "helmet" | "flask-talisman" | "flask" | "express",
   "call": "<the call as written>", "options": <bool: called with options>,
   "headers": {name: value} | null,     # null = depends on options/code -> S0 fills it
   "applies_to": "all_routes", "source": [{file, line, symbol}], "origin": "script"}
Header values are filled only where the library default is unambiguous; a
configured call is recorded with headers null for the spec annotator.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

SKIP_DIRS = {"node_modules", ".git", "venv", ".venv", "__pycache__", "tests", "test", "dist", "build"}

# Library defaults (no options): what every response carries.
DEFAULT_HEADERS = {
    "flask-cors": {"Access-Control-Allow-Origin": "*"},    # always_send=True, origins='*'
    "express-cors": {"Access-Control-Allow-Origin": "*"},  # origin: '*' by default
}

_PY_PATTERNS = [
    ("cors", "flask-cors", re.compile(r"^\s*CORS\s*\(\s*(?P<args>[^)]*)\)", re.M)),
    ("security_headers", "flask-talisman", re.compile(r"^\s*Talisman\s*\(\s*(?P<args>[^)]*)\)", re.M)),
    ("response_header_hook", "flask", re.compile(r"^\s*@\s*\w+\.after_request\b(?P<args>)", re.M)),
]
_JS_PATTERNS = [
    ("cors", "express-cors", re.compile(r"\b\w+\.use\(\s*cors\s*\((?P<args>[^)]*)\)", re.S)),
    ("security_headers", "helmet", re.compile(r"\b\w+\.use\(\s*helmet\s*\((?P<args>[^)]*)\)", re.S)),
    # app.use((req, res, next) => { res.set/setHeader/header(...) ... })
    ("response_header_hook", "express",
     re.compile(r"\b\w+\.use\(\s*(?:function\s*\w*)?\s*\(\s*\w+\s*,\s*\w+\s*,\s*\w+\s*\)\s*(?:=>)?\s*\{(?P<args>[^}]*?\.(?:set|setHeader|header)\s*\([^}]*)\}", re.S)),
]


def _files(root: Path, suffixes: tuple[str, ...]):
    for p in sorted(root.rglob("*")):
        if p.suffix in suffixes and p.is_file() and not (set(p.relative_to(root).parts) & SKIP_DIRS):
            yield p


def detect(monolith_root: str | Path) -> list[dict[str, Any]]:
    root = Path(monolith_root).resolve()
    app = root.name
    found: list[dict[str, Any]] = []
    for patterns, suffixes in ((_PY_PATTERNS, (".py",)), (_JS_PATTERNS, (".js", ".mjs", ".cjs", ".ts"))):
        for path in _files(root, suffixes):
            text = path.read_text(encoding="utf-8", errors="replace")
            for kind, framework, rx in patterns:
                for m in rx.finditer(text):
                    line = text.count("\n", 0, m.start()) + 1
                    call = " ".join(m.group(0).split())[:200]
                    args = (m.group("args") or "").strip()
                    # flask-cors: CORS(app) has the app as its only argument
                    options = bool(re.sub(r"^\s*\w+\s*,?\s*", "", args)) if framework == "flask-cors" else bool(args)
                    if kind == "response_header_hook":
                        options = True
                    symbol = next((s for s in ("CORS(", "cors(", "Talisman(", "helmet(", "after_request", ".use(") if s in m.group(0)), m.group(0)[:40])
                    found.append({
                        "kind": kind, "framework": framework, "call": call, "options": options,
                        "headers": None if options else dict(DEFAULT_HEADERS.get(framework, {})) or None,
                        "applies_to": "all_routes",
                        "source": [{"file": f"{app}/{path.relative_to(root).as_posix()}", "line": line, "symbol": symbol}],
                        "origin": "script",
                    })
    return found
