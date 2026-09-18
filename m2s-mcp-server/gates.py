"""Deterministic consistency gates for the Mono2Sls -> OpenHarness migration.

Reimplements the mechanical (non-judgment) checks from M2S's own
`tasks.yaml` consistency-check task (C1-C12) as plain Python instead of an
LLM-driven CrewAI task, so they can run instantly and deterministically as
an MCP tool (`run_gate`).

Workspace layout this module expects (mirrors M2S's own storage/-output/
convention, rooted at $M2S_WORKSPACE instead of the M2S repo root):

    <workspace>/
    |-- storage/
    |   |-- analysis_report.json
    |   |-- symbol_table.json
    |   |-- blueprint_sections/*.json   (architect's incremental output)
    |   |-- blueprint.json              (merged, via m2s_merge.py)
    |   `-- stage_reports/<stage_id>.json
    `-- output/
        |-- lambdas/<domain>/<name>/{handler.js|handler.py, package.json|requirements.txt}
        |-- layers/shared/...
        |-- template.part1.yaml / template.part2.yaml  (sam-engineer partial writes)
        |-- template.yaml                                (merged, via m2s_merge.py)
        `-- consistency_report.json                       (validator output)

Only the mechanical checks (B0, C1, C2, C3, C10, C11, C12, C7) produce a
pass/fail verdict here. C4-C9 are "suspects": mechanically extracted data
handed to the m2s-validator agent for semantic judgment, per the plan's
hybrid grading design -- this module does not decide whether they're
actually wrong, only surfaces candidates.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml

# ---------------------------------------------------------------------------
# CFN-tag-aware YAML loading (ported from M2S's file_tools.py _CFNLoader,
# without importing crewai).
# ---------------------------------------------------------------------------


class _CFNLoader(yaml.SafeLoader):
    """SafeLoader extended with CloudFormation/SAM intrinsic function tags."""


def _cfn_multi_ctor(loader: yaml.SafeLoader, tag_suffix: str, node: yaml.Node) -> Any:
    del tag_suffix
    if isinstance(node, yaml.ScalarNode):
        return node.value
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node)
    return loader.construct_mapping(node)


_CFNLoader.add_multi_constructor("!", _cfn_multi_ctor)


def load_template(template_path: Path) -> dict[str, Any]:
    text = template_path.read_text(encoding="utf-8")
    return yaml.load(text, Loader=_CFNLoader) or {}


# ---------------------------------------------------------------------------
# Finding helpers
# ---------------------------------------------------------------------------


def _finding(check_id: str, severity: str, description: str, affected_resource: str) -> dict[str, Any]:
    return {
        "check_id": check_id,
        "severity": severity,
        "description": description,
        "affected_resource": affected_resource,
    }


# ---------------------------------------------------------------------------
# B0 - blueprint completeness (gate(blueprint) + gate(final))
# ---------------------------------------------------------------------------

_B0_REQUIRED_KEYS: dict[str, type] = {
    "metadata": dict,
    "lambda_functions": list,
    "dynamodb_tables": list,
    "s3_buckets": list,
    "sqs_queues": list,
    "eventbridge_rules": list,
    "lambda_invoke_permissions": list,
    "dropped_functions": list,
    "cognito": dict,
    "api_gateway": dict,
}


def _endpoint_from_entry_point(entry_point: str) -> str | None:
    """"GET /item" -> "GET /item". Kept as-is; comparison is exact-string."""
    return entry_point.strip() if entry_point else None


def check_b0(workspace: Path) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    storage_dir = workspace / "storage"
    blueprint_path = storage_dir / "blueprint.json"

    # 5. Residue detection: sibling blueprint2/3/4.json files from a botched
    # split-write (the historical crash this plan's B0 gate exists to catch).
    for sibling in sorted(storage_dir.glob("blueprint[0-9]*.json")):
        findings.append(
            _finding(
                "B0",
                "ERROR",
                f"Residue file found: {sibling.name}. A previous run likely wrote "
                "the blueprint across numbered siblings instead of merging them "
                "into storage/blueprint.json. Re-run the merge (m2s_merge.py) "
                "before trusting blueprint.json.",
                str(sibling),
            )
        )

    if not blueprint_path.exists():
        findings.append(_finding("B0", "ERROR", "storage/blueprint.json does not exist.", str(blueprint_path)))
        return findings

    # 1. JSON legality
    try:
        blueprint = json.loads(blueprint_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        findings.append(
            _finding("B0", "ERROR", f"storage/blueprint.json is not valid JSON: {exc}", str(blueprint_path))
        )
        return findings

    if not isinstance(blueprint, dict):
        findings.append(_finding("B0", "ERROR", "blueprint.json root is not a JSON object.", str(blueprint_path)))
        return findings

    # 2. Required keys + types (+ lambda_functions non-empty)
    for key, expected_type in _B0_REQUIRED_KEYS.items():
        if key not in blueprint:
            findings.append(_finding("B0", "ERROR", f"blueprint.json missing required key '{key}'.", "blueprint.json"))
            continue
        if not isinstance(blueprint[key], expected_type):
            findings.append(
                _finding(
                    "B0",
                    "ERROR",
                    f"blueprint.json key '{key}' has wrong type: expected {expected_type.__name__}.",
                    "blueprint.json",
                )
            )
    if isinstance(blueprint.get("lambda_functions"), list) and not blueprint["lambda_functions"]:
        findings.append(_finding("B0", "ERROR", "blueprint.json 'lambda_functions' is empty.", "blueprint.json"))

    # 3. Per-function required fields
    for i, fn in enumerate(blueprint.get("lambda_functions", []) or []):
        label = fn.get("name", f"lambda_functions[{i}]") if isinstance(fn, dict) else f"lambda_functions[{i}]"
        if not isinstance(fn, dict):
            findings.append(_finding("B0", "ERROR", f"{label} is not a JSON object.", label))
            continue
        if not fn.get("name"):
            findings.append(_finding("B0", "ERROR", f"{label} missing non-empty 'name'.", label))
        if not fn.get("entry_points"):
            findings.append(_finding("B0", "ERROR", f"{label} missing non-empty 'entry_points'.", label))
        if fn.get("auth") not in {"required", "none"}:
            findings.append(
                _finding("B0", "ERROR", f"{label} 'auth' must be 'required' or 'none', got {fn.get('auth')!r}.", label)
            )
        if not fn.get("runtime"):
            findings.append(_finding("B0", "ERROR", f"{label} missing 'runtime'.", label))

    # 4. Coverage cross-check against analysis_report.json entry_points
    analysis_path = storage_dir / "analysis_report.json"
    if analysis_path.exists():
        try:
            analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            analysis = None
        if isinstance(analysis, dict):
            analyzed_endpoints = {
                f"{ep.get('method', '').upper()} {ep.get('path', '')}".strip()
                for ep in analysis.get("entry_points", [])
                if isinstance(ep, dict)
            }
            blueprint_endpoints: set[str] = set()
            for fn in blueprint.get("lambda_functions", []) or []:
                if isinstance(fn, dict):
                    blueprint_endpoints.update(
                        ep for ep in (fn.get("entry_points") or []) if isinstance(ep, str)
                    )
            dropped_endpoints = {
                item.get("item", "")
                for item in blueprint.get("dropped_functions", []) or []
                if isinstance(item, dict) and item.get("type") == "endpoint"
            }
            covered = blueprint_endpoints | dropped_endpoints
            uncovered = analyzed_endpoints - covered
            # Endpoint spelling differs between analyzer ("GET /item/:id") and
            # blueprint ("GET /item/{id}") -- normalize :param and {param} the
            # same way before flagging, to avoid false positives.
            def _normalize(ep: str) -> str:
                return re.sub(r"(:([A-Za-z0-9_]+)|\{([A-Za-z0-9_]+)\})", "{param}", ep)

            normalized_covered = {_normalize(ep) for ep in covered}
            uncovered = {ep for ep in uncovered if _normalize(ep) not in normalized_covered}
            for ep in sorted(uncovered):
                findings.append(
                    _finding(
                        "B0",
                        "ERROR",
                        f"Analyzed endpoint '{ep}' is not covered by any lambda_functions.entry_points "
                        "nor dropped_functions.",
                        ep,
                    )
                )

    return findings


# ---------------------------------------------------------------------------
# C1 - Directory <-> Function coverage (bidirectional)
# ---------------------------------------------------------------------------


def _iter_lambda_dirs(lambdas_root: Path) -> list[Path]:
    if not lambdas_root.exists():
        return []
    dirs = []
    for domain_dir in lambdas_root.iterdir():
        if not domain_dir.is_dir():
            continue
        for lambda_dir in domain_dir.iterdir():
            if lambda_dir.is_dir():
                dirs.append(lambda_dir)
    return dirs


def check_c1_code(workspace: Path) -> list[dict[str, Any]]:
    """C1, "code" direction: blueprint function names <-> handler directory names."""
    findings: list[dict[str, Any]] = []
    blueprint_path = workspace / "storage" / "blueprint.json"
    lambdas_root = workspace / "output" / "lambdas"
    if not blueprint_path.exists():
        return [_finding("C1", "ERROR", "storage/blueprint.json does not exist.", str(blueprint_path))]

    blueprint = json.loads(blueprint_path.read_text(encoding="utf-8"))
    expected_names = {fn.get("name") for fn in blueprint.get("lambda_functions", []) if isinstance(fn, dict) and fn.get("name")}
    actual_names = {d.name for d in _iter_lambda_dirs(lambdas_root)}

    for missing in sorted(expected_names - actual_names):
        findings.append(_finding("C1", "ERROR", f"No handler directory found for blueprint function '{missing}'.", missing))
    for extra in sorted(actual_names - expected_names):
        findings.append(_finding("C1", "ERROR", f"Handler directory '{extra}' has no matching blueprint function.", extra))
    return findings


def check_c1_template(workspace: Path, template: dict[str, Any]) -> list[dict[str, Any]]:
    """C1, "template" direction: CodeUri <-> on-disk directory."""
    findings: list[dict[str, Any]] = []
    resources = template.get("Resources", {}) or {}
    for logical_name, resource in resources.items():
        if not isinstance(resource, dict) or resource.get("Type") != "AWS::Serverless::Function":
            continue
        code_uri = (resource.get("Properties", {}) or {}).get("CodeUri")
        if not code_uri:
            findings.append(_finding("C1", "ERROR", f"{logical_name} has no CodeUri.", logical_name))
            continue
        dir_path = workspace / "output" / code_uri
        if not dir_path.is_dir():
            findings.append(
                _finding("C1", "ERROR", f"{logical_name}.CodeUri '{code_uri}' does not exist on disk.", logical_name)
            )
    return findings


# ---------------------------------------------------------------------------
# C2 - CodeUri accuracy
# ---------------------------------------------------------------------------


def check_c2(workspace: Path, template: dict[str, Any]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    resources = template.get("Resources", {}) or {}
    for logical_name, resource in resources.items():
        if not isinstance(resource, dict) or resource.get("Type") != "AWS::Serverless::Function":
            continue
        code_uri = (resource.get("Properties", {}) or {}).get("CodeUri")
        if not code_uri:
            continue
        if not code_uri.endswith("/"):
            findings.append(
                _finding("C2", "ERROR", f"{logical_name}.CodeUri '{code_uri}' must end with '/'.", logical_name)
            )
        if not (workspace / "output" / code_uri).is_dir():
            findings.append(
                _finding("C2", "ERROR", f"{logical_name}.CodeUri '{code_uri}' does not match an actual directory.", logical_name)
            )
    return findings


# ---------------------------------------------------------------------------
# C3 - Handler validity
# ---------------------------------------------------------------------------

_PY_HANDLER_RE = re.compile(r"^def\s+(\w+)\s*\(", re.MULTILINE)
_NODE_HANDLER_RE = re.compile(r"(?:exports\.(\w+)\s*=|module\.exports\.(\w+)\s*=|module\.exports\s*=\s*\{[^}]*?(\w+)\s*:)")


def check_c3(workspace: Path, template: dict[str, Any]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    resources = template.get("Resources", {}) or {}
    for logical_name, resource in resources.items():
        if not isinstance(resource, dict) or resource.get("Type") != "AWS::Serverless::Function":
            continue
        props = resource.get("Properties", {}) or {}
        code_uri = props.get("CodeUri")
        handler = props.get("Handler")
        if not code_uri or not handler or "." not in handler:
            findings.append(_finding("C3", "ERROR", f"{logical_name}.Handler '{handler}' is malformed.", logical_name))
            continue
        module_name, _, func_name = handler.rpartition(".")
        lambda_dir = workspace / "output" / code_uri

        py_file = lambda_dir / f"{module_name}.py"
        js_file = lambda_dir / f"{module_name}.js"
        if py_file.exists():
            content = py_file.read_text(encoding="utf-8", errors="replace")
            names = set(_PY_HANDLER_RE.findall(content))
            if func_name not in names:
                findings.append(
                    _finding("C3", "ERROR", f"{logical_name}: function '{func_name}' not found in {py_file.name}.", logical_name)
                )
        elif js_file.exists():
            content = js_file.read_text(encoding="utf-8", errors="replace")
            has_exports_handler = bool(re.search(r"exports\.\s*" + re.escape(func_name) + r"\s*=", content))
            has_module_exports = bool(re.search(r"module\.exports\.\s*" + re.escape(func_name) + r"\s*=", content))
            if not (has_exports_handler or has_module_exports):
                findings.append(
                    _finding("C3", "ERROR", f"{logical_name}: 'exports.{func_name}' not found in {js_file.name}.", logical_name)
                )
        else:
            findings.append(
                _finding("C3", "ERROR", f"{logical_name}: handler file '{module_name}.py/.js' not found under {code_uri}.", logical_name)
            )
    return findings


# ---------------------------------------------------------------------------
# C7 - API event matching (bidirectional, exact-string with :param/{param} normalization)
# ---------------------------------------------------------------------------


def _normalize_path(path: str) -> str:
    return re.sub(r"(:([A-Za-z0-9_]+)|\{([A-Za-z0-9_]+)\})", "{param}", path)


def check_c7(workspace: Path, template: dict[str, Any]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    blueprint_path = workspace / "storage" / "blueprint.json"
    if not blueprint_path.exists():
        return [_finding("C7", "ERROR", "storage/blueprint.json does not exist.", str(blueprint_path))]
    blueprint = json.loads(blueprint_path.read_text(encoding="utf-8"))

    blueprint_endpoints: dict[str, str] = {}
    for fn in blueprint.get("lambda_functions", []) or []:
        if not isinstance(fn, dict):
            continue
        for ep in fn.get("entry_points", []) or []:
            method, _, path = ep.partition(" ")
            key = f"{method.upper()} {_normalize_path(path)}"
            blueprint_endpoints[key] = fn.get("name", "")

    template_endpoints: dict[str, list[str]] = {}
    resources = template.get("Resources", {}) or {}
    for logical_name, resource in resources.items():
        if not isinstance(resource, dict) or resource.get("Type") != "AWS::Serverless::Function":
            continue
        for event in ((resource.get("Properties", {}) or {}).get("Events", {}) or {}).values():
            if not isinstance(event, dict) or event.get("Type") != "Api":
                continue
            props = event.get("Properties", {}) or {}
            method = str(props.get("Method", "")).upper()
            path = str(props.get("Path", ""))
            key = f"{method} {_normalize_path(path)}"
            template_endpoints.setdefault(key, []).append(logical_name)

    for key, fn_name in blueprint_endpoints.items():
        matches = template_endpoints.get(key, [])
        if len(matches) == 0:
            findings.append(_finding("C7", "ERROR", f"Blueprint entry_point '{key}' ({fn_name}) has no matching Api Event.", fn_name))
        elif len(matches) > 1:
            findings.append(
                _finding("C7", "ERROR", f"Blueprint entry_point '{key}' matched by multiple Functions: {matches}.", key)
            )
    for key, logical_names in template_endpoints.items():
        if key not in blueprint_endpoints:
            findings.append(_finding("C7", "ERROR", f"Api Event '{key}' has no corresponding blueprint entry_point.", str(logical_names)))
    return findings


# ---------------------------------------------------------------------------
# C10 - Dependency file hygiene
# ---------------------------------------------------------------------------

_PY_BUILTIN_DEPS = {"boto3", "botocore", "s3transfer", "jmespath", "urllib3"}


def check_c10(workspace: Path) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    output_root = workspace / "output"
    if not output_root.exists():
        return findings

    for req_file in output_root.rglob("requirements.txt"):
        lines = [l.strip() for l in req_file.read_text(encoding="utf-8").splitlines() if l.strip()]
        deps = {re.split(r"[<>=!~\[]", l, 1)[0].strip().lower() for l in lines}
        if deps and deps <= _PY_BUILTIN_DEPS:
            findings.append(
                _finding("C10", "ERROR", f"{req_file} contains only Python runtime built-ins {sorted(deps)}; should be deleted.", str(req_file))
            )

    for pkg_file in output_root.rglob("package.json"):
        try:
            pkg = json.loads(pkg_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            findings.append(_finding("C10", "ERROR", f"{pkg_file} is not valid JSON.", str(pkg_file)))
            continue
        deps = pkg.get("dependencies", {}) or {}
        if isinstance(deps, dict) and len(deps) == 0 and "dependencies" in pkg:
            findings.append(
                _finding("C10", "ERROR", f"{pkg_file} declares an empty dependencies object; should be removed if unused.", str(pkg_file))
            )
    return findings


# ---------------------------------------------------------------------------
# C11 - Node.js module system (CJS only, no ESM)
# ---------------------------------------------------------------------------

_ESM_PATTERNS = [
    re.compile(r"^\s*import\s.+\sfrom\s", re.MULTILINE),
    re.compile(r"^\s*export\s+const\s", re.MULTILINE),
    re.compile(r"^\s*export\s+default\b", re.MULTILINE),
    re.compile(r"^\s*export\s+function\s", re.MULTILINE),
    re.compile(r"^\s*export\s*\{", re.MULTILINE),
]


def check_c11(workspace: Path, template: dict[str, Any]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    if not _is_node_runtime(template):
        return findings
    output_root = workspace / "output"
    if not output_root.exists():
        return findings

    for js_file in output_root.rglob("*.js"):
        content = js_file.read_text(encoding="utf-8", errors="replace")
        if any(pattern.search(content) for pattern in _ESM_PATTERNS):
            findings.append(_finding("C11", "ERROR", f"{js_file} uses ESM syntax; must use CommonJS (require/exports).", str(js_file)))

    for pkg_file in output_root.rglob("package.json"):
        try:
            pkg = json.loads(pkg_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if pkg.get("type") == "module":
            findings.append(_finding("C11", "ERROR", f"{pkg_file} declares \"type\": \"module\"; must be removed for CJS.", str(pkg_file)))
    return findings


def _is_node_runtime(template: dict[str, Any]) -> bool:
    globals_runtime = ((template.get("Globals", {}) or {}).get("Function", {}) or {}).get("Runtime", "")
    if str(globals_runtime).startswith("nodejs"):
        return True
    for resource in (template.get("Resources", {}) or {}).values():
        if isinstance(resource, dict) and resource.get("Type") == "AWS::Serverless::Function":
            runtime = (resource.get("Properties", {}) or {}).get("Runtime", "")
            if str(runtime).startswith("nodejs"):
                return True
    return False


# ---------------------------------------------------------------------------
# C12 - Node.js runtime import / dependency resolution
# ---------------------------------------------------------------------------

_NODE_BUILTINS = {
    "assert", "buffer", "child_process", "cluster", "crypto", "dgram", "dns",
    "events", "fs", "http", "https", "net", "os", "path", "querystring",
    "readline", "stream", "string_decoder", "timers", "tls", "url", "util",
    "v8", "vm", "zlib", "process", "console",
}
_REQUIRE_RE = re.compile(r"require\(\s*['\"]([^'\"]+)['\"]\s*\)")


def _package_name(spec: str) -> str:
    if spec.startswith("@"):
        parts = spec.split("/")
        return "/".join(parts[:2]) if len(parts) >= 2 else spec
    return spec.split("/")[0]


def check_c12(workspace: Path, template: dict[str, Any]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    if not _is_node_runtime(template):
        return findings
    output_root = workspace / "output"
    if not output_root.exists():
        return findings

    shared_pkg = output_root / "layers" / "shared" / "nodejs" / "package.json"
    shared_deps = _read_dep_names(shared_pkg)

    for js_file in output_root.rglob("*.js"):
        content = js_file.read_text(encoding="utf-8", errors="replace")
        is_shared = "layers" in js_file.parts and "shared" in js_file.parts
        scope_pkg = shared_pkg if is_shared else (js_file.parent / "package.json")
        scope_deps = shared_deps if is_shared else _read_dep_names(scope_pkg)

        for spec in _REQUIRE_RE.findall(content):
            if spec.startswith(".") or spec.startswith("/"):
                continue
            pkg = _package_name(spec)
            if pkg in _NODE_BUILTINS:
                continue
            if pkg == "aws-sdk":
                findings.append(
                    _finding("C12", "ERROR", f"{js_file}: bare require('aws-sdk') is AWS SDK v2; use modular @aws-sdk/* (v3) on nodejs22.x.", str(js_file))
                )
                continue
            if pkg not in scope_deps:
                scope_desc = "shared layer package.json" if is_shared else f"{js_file.parent.name}/package.json"
                findings.append(
                    _finding("C12", "ERROR", f"{js_file}: require('{spec}') not declared in {scope_desc}.", str(js_file))
                )
    return findings


def _read_dep_names(pkg_file: Path) -> set[str]:
    if not pkg_file.exists():
        return set()
    try:
        pkg = json.loads(pkg_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return set()
    names = set()
    for section in ("dependencies", "devDependencies"):
        names.update((pkg.get(section) or {}).keys())
    return names


# ---------------------------------------------------------------------------
# Suspects (C4-C9): mechanical extraction only, no verdict.
# ---------------------------------------------------------------------------

_ENV_PY_RE = re.compile(r"os\.environ(?:\.get)?\(\s*['\"](\w+)['\"]|os\.getenv\(\s*['\"](\w+)['\"]")
_ENV_NODE_RE = re.compile(r"process\.env\.(\w+)")
_BOTO3_RE = re.compile(r"boto3\.(?:client|resource)\(\s*['\"](\w+)['\"]")


def suspects_c4_env(workspace: Path, template: dict[str, Any]) -> dict[str, Any]:
    """C4 prescreen: env keys used in code vs declared in template, per Function."""
    result: dict[str, list[str]] = {}
    resources = template.get("Resources", {}) or {}
    globals_env = set(((template.get("Globals", {}) or {}).get("Function", {}) or {}).get("Environment", {}).get("Variables", {}) or {})
    for logical_name, resource in resources.items():
        if not isinstance(resource, dict) or resource.get("Type") != "AWS::Serverless::Function":
            continue
        props = resource.get("Properties", {}) or {}
        code_uri = props.get("CodeUri")
        if not code_uri:
            continue
        lambda_dir = workspace / "output" / code_uri
        used_keys: set[str] = set()
        for src in list(lambda_dir.glob("*.py")) + list(lambda_dir.glob("*.js")):
            content = src.read_text(encoding="utf-8", errors="replace")
            for m in _ENV_PY_RE.findall(content):
                used_keys.update(k for k in m if k)
            used_keys.update(_ENV_NODE_RE.findall(content))
        declared_keys = set((props.get("Environment", {}) or {}).get("Variables", {}) or {}) | globals_env
        missing = sorted(used_keys - declared_keys)
        unused = sorted(declared_keys - used_keys - globals_env)
        if missing or unused:
            result[logical_name] = {"used_but_not_declared": missing, "declared_but_unused": unused}
    return result


def suspects_c5_iam(workspace: Path) -> dict[str, list[str]]:
    """C5 prescreen: boto3 client/resource service names referenced per lambda dir."""
    result: dict[str, list[str]] = {}
    output_root = workspace / "output" / "lambdas"
    for lambda_dir in _iter_lambda_dirs(output_root):
        services: set[str] = set()
        for py_file in lambda_dir.glob("*.py"):
            services.update(_BOTO3_RE.findall(py_file.read_text(encoding="utf-8", errors="replace")))
        if services:
            result[lambda_dir.name] = sorted(services)
    return result


def suspects_c6_layers(workspace: Path, template: dict[str, Any]) -> dict[str, Any]:
    """C6 prescreen: which Functions reference a shared layer vs which import from one."""
    resources = template.get("Resources", {}) or {}
    referencing: list[str] = []
    for logical_name, resource in resources.items():
        if not isinstance(resource, dict) or resource.get("Type") != "AWS::Serverless::Function":
            continue
        layers = (resource.get("Properties", {}) or {}).get("Layers", []) or []
        if any("SharedUtilsLayer" in str(layer) for layer in layers):
            referencing.append(logical_name)
    shared_layer_exists = (workspace / "output" / "layers" / "shared").exists()
    return {"functions_referencing_shared_layer": referencing, "shared_layer_dir_exists": shared_layer_exists}


def suspects_c8_sqs(workspace: Path, template: dict[str, Any]) -> dict[str, Any]:
    blueprint_path = workspace / "storage" / "blueprint.json"
    blueprint = json.loads(blueprint_path.read_text(encoding="utf-8")) if blueprint_path.exists() else {}
    queue_resources = [
        name for name, r in (template.get("Resources", {}) or {}).items()
        if isinstance(r, dict) and r.get("Type") == "AWS::SQS::Queue"
    ]
    return {"blueprint_sqs_queues": blueprint.get("sqs_queues", []), "template_sqs_resources": queue_resources}


def suspects_c9_eventbridge(workspace: Path, template: dict[str, Any]) -> dict[str, Any]:
    blueprint_path = workspace / "storage" / "blueprint.json"
    blueprint = json.loads(blueprint_path.read_text(encoding="utf-8")) if blueprint_path.exists() else {}
    rule_resources = [
        name for name, r in (template.get("Resources", {}) or {}).items()
        if isinstance(r, dict) and r.get("Type") == "AWS::Events::Rule"
    ]
    return {"blueprint_eventbridge_rules": blueprint.get("eventbridge_rules", []), "template_rule_resources": rule_resources}


# ---------------------------------------------------------------------------
# run_gate: phase dispatch
# ---------------------------------------------------------------------------

_MECHANICAL_LIMIT_FOR_SEVERE = 2  # per plan: "template" phase treats >2 mechanical failures as severe


def _stage_report_status(workspace: Path, phase: str) -> dict[str, Any]:
    reports_dir = workspace / "storage" / "stage_reports"
    candidates = sorted(reports_dir.glob("*.json")) if reports_dir.exists() else []
    return {"dir": str(reports_dir), "exists": reports_dir.exists(), "files": [p.name for p in candidates]}


def run_gate(phase: str, workspace: str) -> dict[str, Any]:
    ws = Path(workspace).resolve()
    template_path = ws / "output" / "template.yaml"
    template: dict[str, Any] = {}
    template_error: str | None = None
    if template_path.exists():
        try:
            template = load_template(template_path)
        except yaml.YAMLError as exc:
            template_error = str(exc)

    checks: list[dict[str, Any]] = []
    suspects: dict[str, Any] = {}
    severe = False

    if phase == "blueprint":
        checks = check_b0(ws)
        severe = any(f["severity"] == "ERROR" for f in checks)

    elif phase == "code":
        checks = check_c1_code(ws)
        if template_error is None and template:
            checks += check_c3(ws, template)
            checks += check_c10(ws)
            checks += check_c11(ws, template)
            checks += check_c12(ws, template)
        c1_code_failed = any(f["check_id"] == "C1" for f in checks)
        severe = c1_code_failed

    elif phase == "template":
        if template_error is not None:
            checks = [_finding("TEMPLATE", "ERROR", f"output/template.yaml failed to parse: {template_error}", str(template_path))]
            severe = True
        elif not template:
            checks = [_finding("TEMPLATE", "ERROR", "output/template.yaml does not exist.", str(template_path))]
            severe = True
        else:
            checks = (
                check_c1_code(ws)
                + check_c1_template(ws, template)
                + check_c2(ws, template)
                + check_c3(ws, template)
                + check_c7(ws, template)
                + check_c10(ws)
                + check_c11(ws, template)
                + check_c12(ws, template)
            )
            suspects = {
                "C4": suspects_c4_env(ws, template),
                "C5": suspects_c5_iam(ws),
                "C6": suspects_c6_layers(ws, template),
                "C8": suspects_c8_sqs(ws, template),
                "C9": suspects_c9_eventbridge(ws, template),
            }
            mechanical_errors = sum(1 for f in checks if f["severity"] == "ERROR")
            severe = mechanical_errors > _MECHANICAL_LIMIT_FOR_SEVERE

    elif phase == "final":
        checks = check_b0(ws)
        if template_error is not None:
            checks.append(_finding("TEMPLATE", "ERROR", f"output/template.yaml failed to parse: {template_error}", str(template_path)))
        elif template:
            checks += (
                check_c1_code(ws)
                + check_c1_template(ws, template)
                + check_c2(ws, template)
                + check_c3(ws, template)
                + check_c7(ws, template)
                + check_c10(ws)
                + check_c11(ws, template)
                + check_c12(ws, template)
            )
        report_path = ws / "output" / "consistency_report.json"
        report_ok = False
        if report_path.exists():
            try:
                report = json.loads(report_path.read_text(encoding="utf-8"))
                report_ok = isinstance(report, dict) and "status" in report
            except json.JSONDecodeError:
                report_ok = False
        if not report_ok:
            checks.append(_finding("REPORT", "ERROR", "output/consistency_report.json missing or unreadable.", str(report_path)))
        severe = any(f["severity"] == "ERROR" for f in checks)

    else:
        return {
            "phase": phase,
            "status": "error",
            "mechanical_failures": [],
            "checks": [],
            "suspects": {},
            "stage_report": _stage_report_status(ws, phase),
            "error": f"unknown phase '{phase}', expected one of blueprint/code/template/final",
        }

    mechanical_failures = [f for f in checks if f["severity"] == "ERROR"]
    return {
        "phase": phase,
        "status": "FAIL" if severe else ("WARN" if mechanical_failures else "PASS"),
        "mechanical_failures": mechanical_failures,
        "checks": checks,
        "suspects": suspects,
        "stage_report": _stage_report_status(ws, phase),
    }
