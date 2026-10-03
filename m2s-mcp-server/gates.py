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

Only checks that cross-reference structured data (blueprint.json vs
template.yaml vs the on-disk directory layout) produce a pass/fail verdict
here: B0, C1, C2, C3, C7, C7b, C8, C9, C10, C11, C12, C13. C4/C5/C6 are
"suspects": mechanically extracted data handed to the m2s-validator agent
for semantic judgment, because they require interpreting arbitrary
generated code (env var usage, IAM-relevant SDK calls, layer imports)
rather than comparing two structured documents -- regex-based extraction
of that is inherently lossier, and a false ERROR here would burn a scarce
retry (N=2 cap) on a misfire instead of a validator noticing it with more
context.
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


def check_spec_relaxations(workspace: Path) -> list[dict[str, Any]]:
    """S5 -> S1 spec relaxation (CONTRACT_TESTING_PLAN.md section 7) must be
    finished before delivery: no request still waiting for a ruling (SR1),
    and every ruling that came from a perf request has its re-judged change
    in perf_report.json (SR2) -- an accepted relaxation nobody re-judged would
    leave the spec and the tests changed for nothing."""
    findings: list[dict[str, Any]] = []
    try:
        report = json.loads((workspace / "output" / "perf_report.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        report = {}
    changes = [c for c in (report.get("changes") or []) if isinstance(c, dict)] if isinstance(report, dict) else []
    for c in changes:
        if c.get("verdict") == "pending_spec":
            findings.append(_finding("SR1", "ERROR", f"perf change {c.get('id')} ({c.get('request')}) is still pending_spec: the spec "
                                     "relaxation it asked for was never ruled on and re-judged.", "output/perf_report.json"))
    try:
        meta = json.loads((workspace / "storage" / "blueprint.json").read_text(encoding="utf-8")).get("metadata") or {}
    except (OSError, json.JSONDecodeError, AttributeError):
        return findings
    req = lambda v: (m.group(0) if (m := re.search(r"REQ-\d+", str(v or ""))) else None)
    judged = {req(c.get("request")) for c in changes if c.get("verdict") in ("kept", "kept_inconclusive", "reverted", "revert")}
    for r in meta.get("spec_resolutions") or [] if isinstance(meta, dict) else []:
        if not isinstance(r, dict) or not str(r.get("origin", "")).startswith("perf-request"):
            continue
        if r.get("ruling", "accepted") == "accepted" and req(r.get("origin")) not in judged:
            findings.append(_finding("SR2", "ERROR", f"spec_resolutions[{r.get('issue_id')}] accepted {r.get('origin')} but perf_report.json "
                                     "has no kept / reverted verdict for that request (re-judge it).", "blueprint.json"))
    return findings


# What a spec_resolutions entry rules on (PIPELINE_REDESIGN.md section 3.6).
SI_CLASSES = {
    "contract": "keep an externally observable behaviour of the monolith (confirm, correct a classification, pin a result)",
    "implementation-trace": "a monolith-internal step the migration may change",
    "migration-change": "the migration deliberately changes external behaviour (auth platform, seed data, external dependency); states the new behaviour",
    "spec-gap": "behaviour the spec left open; the ruling fixes a concrete result",
}


def check_contract(workspace: Path) -> list[dict[str, Any]]:
    """Contract testing (PIPELINE_REDESIGN.md section 3.6). A spec whose
    business rules carry a class is a contract-2 spec: the blueprint must say
    so, and every response arm the annotator marked trace_arm must be ruled on
    (voided, or given its allowed outcomes) by an implementation-trace SI that
    lists it in `targets`. Implementation-trace SIs, wherever they come from
    (S1, or an S5 spec relaxation request), must name their targets."""
    findings: list[dict[str, Any]] = []
    try:
        blueprint = json.loads((workspace / "storage" / "blueprint.json").read_text(encoding="utf-8"))
        report = json.loads((workspace / "storage" / "analysis_report.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return findings  # B0 reports a missing / broken blueprint; no report yet means nothing to check
    meta = blueprint.get("metadata") if isinstance(blueprint, dict) else None
    meta = meta if isinstance(meta, dict) else {}
    eps = report.get("entry_points", []) if isinstance(report, dict) else []
    classified = any(isinstance(r, dict) and "class" in r for ep in eps for r in ep.get("business_rules") or [])
    if classified and meta.get("verification_contract") != 2:
        findings.append(_finding("CT1", "ERROR", "analysis_report.json classifies business rules (contract / implementation-trace) "
                                 "but blueprint metadata.verification_contract is not 2.", "blueprint.json"))
    if meta.get("verification_contract") not in (None, 1, 2):
        findings.append(_finding("CT1", "ERROR", "metadata.verification_contract must be 1 or 2.", "blueprint.json"))
    targets: set[str] = set()
    for r in meta.get("spec_resolutions") or []:
        if not isinstance(r, dict):
            continue
        cls = r.get("class")
        if cls is not None and cls not in SI_CLASSES:
            findings.append(_finding("CT4", "ERROR", f"spec_resolutions[{r.get('issue_id')}].class {cls!r} is not one of "
                                     f"{sorted(SI_CLASSES)}.", "blueprint.json"))
        elif cls is None and meta.get("verification_contract") == 2:
            findings.append(_finding("CT4", "ERROR", f"spec_resolutions[{r.get('issue_id')}] has no 'class'; under "
                                     f"verification_contract 2 every ruling needs one of {sorted(SI_CLASSES)}.", "blueprint.json"))
        dmc = r.get("data_model_change")
        if dmc is not None:
            idx = (dmc or {}).get("index") if isinstance(dmc, dict) else None
            bad = not isinstance(dmc, dict) or not dmc.get("table") or not isinstance(idx, dict) or not idx.get("name")                 or not isinstance(idx.get("keys"), list) or not idx["keys"]                 or any(not isinstance(a, dict) or not a.get("name") or not a.get("backfill") for a in dmc.get("new_attributes") or [])
            if bad:
                findings.append(_finding("CT5", "ERROR", f"spec_resolutions[{r.get('issue_id')}].data_model_change needs table, index "
                                         "{name, keys: [{attribute, type: HASH|RANGE}], projection}, and for each new_attributes item "
                                         "a name and a backfill note.", "blueprint.json"))
        if cls != "implementation-trace":
            continue
        t = r.get("targets")
        if not (isinstance(t, list) and t and all(isinstance(x, str) for x in t)):
            findings.append(_finding("CT2", "ERROR", f"spec_resolutions[{r.get('issue_id')}] is class implementation-trace but has no "
                                     "'targets' (the spec items it rules on, e.g. 'POST /x#responses[6]').", "blueprint.json"))
        else:
            targets.update(t)
    for ep in eps:
        for i, resp in enumerate(ep.get("responses") or []):
            ref = f"{ep.get('method')} {ep.get('path')}#responses[{i}]"
            if isinstance(resp, dict) and resp.get("trace_arm") and ref not in targets:
                findings.append(_finding("CT3", "ERROR", f"{ref} is marked trace_arm but no implementation-trace spec_resolution "
                                         "targets it (void it, or give its allowed outcomes).", "blueprint.json"))
    return findings


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
        # `entry_points`/`auth` are an api-trigger-only contract (see
        # m2s-architect.md: "entry_points 仅 api 类型需要...sqs/eventbridge 消费者
        # 没有 entry_points"). Requiring them unconditionally false-positives on
        # every non-api lambda_functions entry -- never caught by the todo
        # benchmark because it has zero sqs/eventbridge functions.
        if fn.get("trigger_type") == "api":
            if not fn.get("entry_points"):
                findings.append(
                    _finding("B0", "ERROR", f"{label} missing non-empty 'entry_points' (required for trigger_type='api').", label)
                )
            if fn.get("auth") not in {"required", "none"}:
                findings.append(
                    _finding(
                        "B0", "ERROR",
                        f"{label} 'auth' must be 'required' or 'none' for trigger_type='api', got {fn.get('auth')!r}.",
                        label,
                    )
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
            # Endpoint spelling differs between analyzer ("GET /item/:id", Flask
            # "GET /item/<id>") and blueprint ("GET /item/{id}") -- normalize
            # :param, {param} and Flask's <param> / <converter:param> the same
            # way before comparing, to avoid false positives.
            def _normalize(ep: str) -> str:
                return re.sub(r"(:([A-Za-z0-9_]+)|\{([A-Za-z0-9_]+)\}|<(?:[A-Za-z0-9_]+:)?([A-Za-z0-9_]+)>)", "{param}", ep)

            # Track WHO claims each normalized endpoint, not just whether it's
            # claimed, so a double assignment (the same endpoint given to two
            # different lambda_functions entries, e.g. from an accidental
            # array-append duplicate) is caught as an ERROR rather than
            # silently passing coverage. Caught once in practice: architect
            # resubmitted its whole lambda_functions list via
            # merge_blueprint_key after renaming one function, producing 12
            # entries where "GET /item" was claimed by two different names.
            claimants: dict[str, list[str]] = {}
            for fn in blueprint.get("lambda_functions", []) or []:
                if not isinstance(fn, dict):
                    continue
                fn_name = fn.get("name", "<unnamed>")
                for ep in fn.get("entry_points") or []:
                    if isinstance(ep, str):
                        claimants.setdefault(_normalize(ep), []).append(fn_name)

            dropped_endpoints_raw = {
                item.get("item", "")
                for item in blueprint.get("dropped_functions", []) or []
                if isinstance(item, dict) and item.get("type") == "endpoint"
            }
            for ep in dropped_endpoints_raw:
                claimants.setdefault(_normalize(ep), []).append("dropped_functions")

            for normalized_ep, owners in claimants.items():
                if len(owners) > 1:
                    findings.append(
                        _finding(
                            "B0",
                            "ERROR",
                            f"Endpoint '{normalized_ep}' is claimed by more than one place: {owners}. "
                            "Each endpoint must map to exactly one lambda_functions entry OR be dropped, not both/multiple.",
                            normalized_ep,
                        )
                    )

            normalized_covered = set(claimants.keys())
            for ep in sorted(analyzed_endpoints):
                if _normalize(ep) not in normalized_covered:
                    findings.append(
                        _finding(
                            "B0",
                            "ERROR",
                            f"Analyzed endpoint '{ep}' is not covered by any lambda_functions.entry_points "
                            "nor dropped_functions.",
                            ep,
                        )
                    )

            # 5. Auth vs the spec's endpoint-level classification (PIPELINE_REDESIGN
            # §2.1/§2.2): the architect adopts it instead of re-inferring from
            # file tags. required -> none would expose an endpoint the monolith
            # protected, so that is an ERROR; other differences are warnings.
            spec_auth = {
                _normalize(f"{ep.get('method', '').upper()} {ep.get('path', '')}"): (ep.get("auth") or {}).get("classification")
                for ep in analysis.get("entry_points", [])
                if isinstance(ep, dict) and isinstance(ep.get("auth"), dict)
            }
            for fn in blueprint.get("lambda_functions", []) or []:
                if not isinstance(fn, dict) or fn.get("trigger_type") != "api":
                    continue
                for ep in fn.get("entry_points") or []:
                    if not isinstance(ep, str):
                        continue
                    want = spec_auth.get(_normalize(ep))
                    got = fn.get("auth")
                    if want == "required" and got == "none":
                        findings.append(_finding(
                            "B0", "ERROR",
                            f"'{ep}' requires authentication in the monolith (spec auth.classification=required) "
                            f"but {fn.get('name')} has auth='none'.", fn.get("name", "<unnamed>")))
                    elif want in ("required", "none") and got in ("required", "none") and want != got:
                        findings.append(_finding(
                            "B0", "WARNING",
                            f"'{ep}': spec auth.classification={want!r} but {fn.get('name')} has auth={got!r}; "
                            "record the reason in DECISIONS.md.", fn.get("name", "<unnamed>")))

    # 6. Decision record (PIPELINE_REDESIGN §2.2): storage/DECISIONS.md must
    # exist and mention every lambda function.
    decisions_path = storage_dir / "DECISIONS.md"
    decisions = decisions_path.read_text(encoding="utf-8", errors="replace") if decisions_path.exists() else ""
    fn_names = [fn.get("name") for fn in blueprint.get("lambda_functions", []) or [] if isinstance(fn, dict) and fn.get("name")]
    if not decisions.strip():
        findings.append(_finding("B0", "ERROR", "storage/DECISIONS.md is missing or empty; every design decision needs a recorded rationale.", "storage/DECISIONS.md"))
    else:
        missing = [n for n in fn_names if n not in decisions]
        if missing:
            findings.append(_finding("B0", "ERROR", f"storage/DECISIONS.md does not mention these lambda functions: {missing}.", "storage/DECISIONS.md"))
    # 7. Async consumers must be idempotent (§2.4) -- keyword heuristic, warning only.
    for fn in blueprint.get("lambda_functions", []) or []:
        if isinstance(fn, dict) and fn.get("trigger_type") in ("sqs", "eventbridge"):
            text = (json.dumps(fn, ensure_ascii=False) + decisions).lower()
            if "幂等" not in text and "idempot" not in text:
                findings.append(_finding(
                    "B0", "WARNING",
                    f"Consumer {fn.get('name')} has no recorded idempotency design (notes / DECISIONS.md); "
                    "consumers must be idempotent under redelivery (m2s-async-patterns-kb).", fn.get("name", "<unnamed>")))

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


def _lambda_name_to_logical(resources: dict[str, Any]) -> dict[str, str]:
    """Map a blueprint lambda_functions[].name (e.g. "todo-create-item") to its
    template.yaml logical resource name (e.g. "TodoCreateItemFunction"), via
    the CodeUri directory's basename. Used by C8/C9 to cross-reference
    blueprint-level facts (publishes_to, trigger_type) against the Function's
    template properties (Policies, Environment, Events)."""
    mapping: dict[str, str] = {}
    for logical_name, resource in resources.items():
        if not isinstance(resource, dict) or resource.get("Type") != "AWS::Serverless::Function":
            continue
        code_uri = (resource.get("Properties", {}) or {}).get("CodeUri", "")
        dir_name = Path(str(code_uri).rstrip("/")).name if code_uri else None
        if dir_name:
            mapping[dir_name] = logical_name
    return mapping


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


def _handler_problem(lambda_dir: Path, handler: Any, label: str) -> str | None:
    """Return a description of why `handler` does not resolve under lambda_dir, or None if it does."""
    if not handler or not isinstance(handler, str) or "." not in handler:
        return f"{label}.Handler '{handler}' is malformed."
    module_name, _, func_name = handler.rpartition(".")
    py_file = lambda_dir / f"{module_name}.py"
    js_file = lambda_dir / f"{module_name}.js"
    if py_file.exists():
        names = set(_PY_HANDLER_RE.findall(py_file.read_text(encoding="utf-8", errors="replace")))
        if func_name not in names:
            return f"{label}: function '{func_name}' not found in {py_file.name}."
        return None
    if js_file.exists():
        content = js_file.read_text(encoding="utf-8", errors="replace")
        has_exports_handler = bool(re.search(r"exports\.\s*" + re.escape(func_name) + r"\s*=", content))
        has_module_exports = bool(re.search(r"module\.exports\.\s*" + re.escape(func_name) + r"\s*=", content))
        if not (has_exports_handler or has_module_exports):
            return f"{label}: 'exports.{func_name}' not found in {js_file.name}."
        return None
    return f"{label}: handler file '{module_name}.py/.js' not found under {lambda_dir.name}."


def check_c3(workspace: Path, template: dict[str, Any]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    resources = template.get("Resources", {}) or {}
    globals_handler = ((template.get("Globals", {}) or {}).get("Function", {}) or {}).get("Handler")

    lambda_dirs: list[Path] = []
    any_function_inherits = False
    for logical_name, resource in resources.items():
        if not isinstance(resource, dict) or resource.get("Type") != "AWS::Serverless::Function":
            continue
        props = resource.get("Properties", {}) or {}
        code_uri = props.get("CodeUri")
        own_handler = props.get("Handler")
        handler = own_handler or globals_handler
        if own_handler is None and globals_handler is not None:
            any_function_inherits = True
        if not code_uri:
            findings.append(_finding("C3", "ERROR", f"{logical_name}.CodeUri is missing.", logical_name))
            continue
        lambda_dir = workspace / "output" / code_uri
        lambda_dirs.append(lambda_dir)
        problem = _handler_problem(lambda_dir, handler, logical_name)
        if problem:
            findings.append(_finding("C3", "ERROR", problem, logical_name))

    # A Globals handler that no function inherits and that resolves nowhere is
    # dead-but-wrong config: harmless today, a landmine for the next function added.
    if globals_handler is not None and not any_function_inherits and lambda_dirs:
        if all(_handler_problem(d, globals_handler, "Globals.Function") for d in lambda_dirs):
            findings.append(
                _finding(
                    "C3",
                    "ERROR",
                    f"Globals.Function.Handler '{globals_handler}' resolves to no real handler in any function's "
                    "CodeUri (every function overrides it). Remove it, or set it to the real handler.",
                    "Globals.Function.Handler",
                )
            )
    return findings


# ---------------------------------------------------------------------------
# C7 - API event matching (bidirectional, exact-string with :param/{param} normalization)
# ---------------------------------------------------------------------------


def _normalize_path(path: str) -> str:
    """Fold Express (":id", "{id}") and Flask ("<id>", "<int:id>") resource
    variables to one canonical "{param}" form so blueprint paths (written in
    the API Gateway "{id}" shape) can be compared against analyzer paths
    (which keep the framework's own spelling, e.g. Flask "/<product_id>") and
    against template Paths."""
    return re.sub(r"(:([A-Za-z0-9_]+)|\{([A-Za-z0-9_]+)\}|<([A-Za-z0-9_]+:)?[A-Za-z0-9_]+>)", "{param}", path)


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
# C7b - Per-endpoint auth override
# ---------------------------------------------------------------------------


def _has_authorizer_none(event_properties: dict[str, Any]) -> bool:
    auth = event_properties.get("Auth")
    if not isinstance(auth, dict):
        return False
    return str(auth.get("Authorizer", "")).upper() == "NONE"


def check_c7b(workspace: Path, template: dict[str, Any]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    blueprint_path = workspace / "storage" / "blueprint.json"
    if not blueprint_path.exists():
        return [_finding("C7b", "ERROR", "storage/blueprint.json does not exist.", str(blueprint_path))]
    blueprint = json.loads(blueprint_path.read_text(encoding="utf-8"))

    # normalized "METHOD path" -> (fn_name, auth)
    blueprint_auth: dict[str, tuple[str, str]] = {}
    for fn in blueprint.get("lambda_functions", []) or []:
        if not isinstance(fn, dict):
            continue
        auth = fn.get("auth")
        for ep in fn.get("entry_points", []) or []:
            method, _, path = ep.partition(" ")
            key = f"{method.upper()} {_normalize_path(path)}"
            blueprint_auth[key] = (fn.get("name", ""), auth)

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
            match = blueprint_auth.get(key)
            if match is None:
                continue  # unmatched events are already flagged by C7
            _fn_name, auth = match
            none_override = _has_authorizer_none(props)
            if auth == "none" and not none_override:
                findings.append(
                    _finding(
                        "C7b",
                        "ERROR",
                        f"{logical_name} ({key}): blueprint auth='none' but Api Event is missing "
                        "'Auth: Authorizer: NONE' -- this endpoint will incorrectly require Cognito auth.",
                        logical_name,
                    )
                )
            elif auth == "required" and none_override:
                findings.append(
                    _finding(
                        "C7b",
                        "ERROR",
                        f"{logical_name} ({key}): blueprint auth='required' but Api Event has "
                        "'Auth: Authorizer: NONE' -- this endpoint will incorrectly bypass Cognito auth.",
                        logical_name,
                    )
                )
    return findings


# ---------------------------------------------------------------------------
# C8 - SQS resource consistency (structural: blueprint <-> template only,
# no handler code reading -- tasks.yaml groups this in "Phase D: Async
# resource checks", separate from the code-reading Phase B checks).
# ---------------------------------------------------------------------------


def check_c8(workspace: Path, template: dict[str, Any]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    blueprint_path = workspace / "storage" / "blueprint.json"
    if not blueprint_path.exists():
        return [_finding("C8", "ERROR", "storage/blueprint.json does not exist.", str(blueprint_path))]
    blueprint = json.loads(blueprint_path.read_text(encoding="utf-8"))
    resources = template.get("Resources", {}) or {}

    queue_resource_names = {
        name for name, r in resources.items() if isinstance(r, dict) and r.get("Type") == "AWS::SQS::Queue"
    }
    for q in blueprint.get("sqs_queues", []) or []:
        logical_name = q.get("logical_name") if isinstance(q, dict) else None
        if not logical_name or logical_name not in queue_resource_names:
            findings.append(
                _finding("C8", "ERROR", f"blueprint sqs_queues entry '{logical_name}' has no matching AWS::SQS::Queue resource.", str(logical_name))
            )

    name_to_logical = _lambda_name_to_logical(resources)
    for fn in blueprint.get("lambda_functions", []) or []:
        if not isinstance(fn, dict):
            continue
        name = fn.get("name", "")
        publishes_to = [str(p).lower() for p in (fn.get("publishes_to") or [])]
        logical = name_to_logical.get(name)

        if any("sqs" in p for p in publishes_to):
            if logical is None:
                findings.append(_finding("C8", "ERROR", f"Lambda '{name}' publishes_to SQS but has no matching template Function.", name))
            else:
                props = (resources.get(logical, {}) or {}).get("Properties", {}) or {}
                policies = props.get("Policies", []) or []
                has_sqs_policy = any(isinstance(p, dict) and "SQSSendMessagePolicy" in p for p in policies)
                env_vars = (props.get("Environment", {}) or {}).get("Variables", {}) or {}
                has_queue_url = any("QUEUE_URL" in str(k).upper() for k in env_vars)
                if not has_sqs_policy:
                    findings.append(_finding("C8", "ERROR", f"{logical}: publishes to SQS but missing SQSSendMessagePolicy.", logical))
                if not has_queue_url:
                    findings.append(_finding("C8", "ERROR", f"{logical}: publishes to SQS but missing a QUEUE_URL environment variable.", logical))

        if fn.get("trigger_type") == "sqs":
            if logical is None:
                findings.append(_finding("C8", "ERROR", f"Lambda '{name}' has trigger_type=sqs but has no matching template Function.", name))
            else:
                events = ((resources.get(logical, {}) or {}).get("Properties", {}) or {}).get("Events", {}) or {}
                has_sqs_event = any(isinstance(e, dict) and e.get("Type") == "SQS" for e in events.values())
                if not has_sqs_event:
                    findings.append(_finding("C8", "ERROR", f"{logical}: trigger_type=sqs but no SQS Event source mapping found.", logical))
    return findings


# ---------------------------------------------------------------------------
# C9 - EventBridge resource consistency (structural, same rationale as C8)
# ---------------------------------------------------------------------------


def check_c9(workspace: Path, template: dict[str, Any]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    blueprint_path = workspace / "storage" / "blueprint.json"
    if not blueprint_path.exists():
        return [_finding("C9", "ERROR", "storage/blueprint.json does not exist.", str(blueprint_path))]
    blueprint = json.loads(blueprint_path.read_text(encoding="utf-8"))
    resources = template.get("Resources", {}) or {}

    rule_resources = {name: r for name, r in resources.items() if isinstance(r, dict) and r.get("Type") == "AWS::Events::Rule"}
    permission_resources = [r for r in resources.values() if isinstance(r, dict) and r.get("Type") == "AWS::Lambda::Permission"]

    for rule in blueprint.get("eventbridge_rules", []) or []:
        rule_name = rule.get("logical_name") if isinstance(rule, dict) else None
        if not rule_name or rule_name not in rule_resources:
            findings.append(
                _finding("C9", "ERROR", f"blueprint eventbridge_rules entry '{rule_name}' has no matching AWS::Events::Rule resource.", str(rule_name))
            )
            continue
        targets = (rule_resources[rule_name].get("Properties", {}) or {}).get("Targets", []) or []
        if not targets:
            findings.append(_finding("C9", "ERROR", f"AWS::Events::Rule '{rule_name}' has no Targets.", rule_name))
        has_events_permission = any(
            (isinstance(p, dict) and (p.get("Properties", {}) or {}).get("Principal") == "events.amazonaws.com")
            for p in permission_resources
        )
        if not has_events_permission:
            findings.append(
                _finding("C9", "ERROR", f"No AWS::Lambda::Permission grants events.amazonaws.com invoke for rule '{rule_name}'.", rule_name)
            )

    name_to_logical = _lambda_name_to_logical(resources)
    all_target_text = " ".join(
        json.dumps(t) for rule in rule_resources.values() for t in (rule.get("Properties", {}) or {}).get("Targets", []) or []
    )
    for fn in blueprint.get("lambda_functions", []) or []:
        if not isinstance(fn, dict):
            continue
        name = fn.get("name", "")
        logical = name_to_logical.get(name)
        publishes_to = [str(p).lower() for p in (fn.get("publishes_to") or [])]

        if fn.get("trigger_type") == "eventbridge":
            if logical is None or logical not in all_target_text:
                findings.append(
                    _finding("C9", "ERROR", f"Lambda '{name}' has trigger_type=eventbridge but does not appear as a Target in any Rule.", name)
                )

        if any("eventbridge" in p for p in publishes_to):
            if logical is None:
                findings.append(_finding("C9", "ERROR", f"Lambda '{name}' publishes_to EventBridge but has no matching template Function.", name))
            else:
                props = (resources.get(logical, {}) or {}).get("Properties", {}) or {}
                policies = props.get("Policies", []) or []
                has_policy = any(
                    (isinstance(p, dict) and "EventBridgePutEventsPolicy" in p) or p == "EventBridgePutEventsPolicy"
                    for p in policies
                )
                if not has_policy:
                    findings.append(_finding("C9", "ERROR", f"{logical}: publishes to EventBridge but missing EventBridgePutEventsPolicy.", logical))
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


# NOTE: C8 (SQS) and C9 (EventBridge) were originally planned as suspects
# prescreens like C4/C5/C6, but on review they don't belong in that group:
# they only cross-reference blueprint.json against template.yaml (structured
# data), with no free-form handler-code parsing involved -- the same
# category as C1/C2/C7. tasks.yaml itself puts them in a separate "Phase D:
# Async resource checks" precisely because they need no handler reading.
# They're implemented as hard checks above (check_c8, check_c9) instead.


# ---------------------------------------------------------------------------
# run_gate: phase dispatch
# ---------------------------------------------------------------------------

_MECHANICAL_LIMIT_FOR_SEVERE = 2  # per plan: "template" phase treats >2 mechanical failures as severe


def _stage_report_status(workspace: Path, phase: str) -> dict[str, Any]:
    reports_dir = workspace / "storage" / "stage_reports"
    candidates = sorted(reports_dir.glob("*.json")) if reports_dir.exists() else []
    return {"dir": str(reports_dir), "exists": reports_dir.exists(), "files": [p.name for p in candidates]}


def check_c13(workspace: Path, template: dict[str, Any]) -> list[dict[str, Any]]:
    """C13: shared layer layout. Lambda adds /opt/python (Python) and
    /opt/nodejs/node_modules (Node) to the module path, and M2S code requires
    /opt/nodejs/... directly. `sam build` with Metadata.BuildMethod puts the
    ContentUri's contents under python/ or nodejs/ itself, so the source must
    NOT already have that directory (python/python/x.py is not importable);
    without BuildMethod it copies ContentUri as-is, so the source MUST have it."""
    findings: list[dict[str, Any]] = []
    output_root = workspace / "output"
    for logical, res in (template.get("Resources") or {}).items():
        if not isinstance(res, dict) or res.get("Type") not in ("AWS::Serverless::LayerVersion", "AWS::Lambda::LayerVersion"):
            continue
        props = res.get("Properties") or {}
        content_uri = str(props.get("ContentUri") or "").strip()
        if not content_uri or content_uri.startswith("s3://"):
            continue
        root = (output_root / content_uri).resolve()
        if not root.is_dir():
            findings.append(_finding("C13", "ERROR", f"{logical}.ContentUri '{content_uri}' does not exist on disk.", logical))
            continue
        runtimes = [str(r) for r in (props.get("CompatibleRuntimes") or [])]
        build_method = str(((res.get("Metadata") or {}).get("BuildMethod")) or "")
        if build_method.startswith("python") or any(r.startswith("python") for r in runtimes):
            kind, ext = "python", ".py"
        elif build_method.startswith("nodejs") or any(r.startswith("nodejs") for r in runtimes):
            kind, ext = "nodejs", ".js"
        else:
            continue
        nested = root / kind
        has_nested_code = nested.is_dir() and any(nested.rglob(f"*{ext}"))
        has_root_code = any(root.glob(f"*{ext}"))
        if build_method and has_nested_code:
            findings.append(_finding(
                "C13", "ERROR",
                f"{logical}: with Metadata.BuildMethod {build_method}, sam build puts the contents of ContentUri '{content_uri}' "
                f"under {kind}/ itself, so the code in '{content_uri.rstrip('/')}/{kind}/' ends up in /opt/{kind}/{kind}/ and "
                f"cannot be {'imported' if kind == 'python' else 'required'}. Put the {ext} files at the ContentUri root "
                f"(or point ContentUri at the {kind}/ directory).", logical))
        elif not build_method and has_root_code and not has_nested_code:
            findings.append(_finding(
                "C13", "ERROR",
                f"{logical}: without Metadata.BuildMethod, sam build copies ContentUri '{content_uri}' as-is, so its {ext} files "
                f"land directly in /opt/ where Lambda does not look. Put them under '{content_uri.rstrip('/')}/{kind}/' "
                f"or add Metadata.BuildMethod.", logical))
    return findings


# Pipeline tools an agent must never modify during a run (TOOLS check). The
# rule ledger is written by update_ledger during S5, so it is exempt.
TOOL_DIRS = ("m2s-mcp-server", ".openharness")
TOOL_EXEMPT = {"m2s-mcp-server/perf-rules/ledger.json"}


def check_tools_unmodified(repo: Path | None = None, home_agents: Path | None = None) -> list[dict[str, Any]]:
    """TOOLS: the pipeline's own code, skills and agent definitions are the
    committed versions. shopping-cart's architect edited gates.py/verify.py
    to get past a gate it judged wrong; agents must report such a gate with
    evidence (STAGE_BLOCKED) instead. Checks uncommitted changes (and new
    files) under TOOL_DIRS of the repo, and that ~/.openharness/agents/m2s-*
    equal the repo's agents/ copies."""
    import subprocess
    repo = repo or Path(__file__).resolve().parent.parent
    home_agents = home_agents or Path.home() / ".openharness" / "agents"
    findings: list[dict[str, Any]] = []
    if (repo / ".git").exists():
        p = subprocess.run(["git", "-C", str(repo), "status", "--porcelain", "--untracked-files=all", "--", *TOOL_DIRS],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL)
        if p.returncode != 0:
            findings.append(_finding("TOOLS", "WARNING", f"could not check the pipeline tools with git: {p.stderr.strip()[:200]}", str(repo)))
        else:
            for line in p.stdout.splitlines():
                path = line[3:].strip().strip('"').split(" -> ")[-1]
                if line.strip() and path not in TOOL_EXEMPT:
                    findings.append(_finding("TOOLS", "ERROR",
                                             f"pipeline tool file changed and not committed: {line[:2].strip()} {path} "
                                             "(agents must not modify the pipeline's tools; report a suspected tool bug instead)", path))
    agents_dir = repo / "m2s-mcp-server" / "agents"
    for src in sorted(agents_dir.glob("m2s-*.md")) if agents_dir.is_dir() else []:
        installed = home_agents / src.name
        if installed.exists() and installed.read_bytes() != src.read_bytes():
            findings.append(_finding("TOOLS", "ERROR", f"{installed} differs from the committed {src.relative_to(repo)} "
                                     "(agent definitions were edited, or not synced after a change)", str(installed)))
    return findings


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
        checks = check_b0(ws) + check_contract(ws)
        severe = any(f["severity"] == "ERROR" for f in checks)

    elif phase == "code":
        # check_c10 doesn't take `template` at all (it's a plain filesystem
        # scan of requirements.txt/package.json), so it must not be gated
        # behind template.yaml existing -- S2 (code-developer) runs before
        # S3 ever produces a template, so gate(phase="code") would otherwise
        # never be able to catch a C10 problem at the point it's most
        # useful (before the code-developer's own self-check loop exits).
        checks = check_c1_code(ws) + check_c10(ws)
        if template_error is None and template:
            checks += check_c3(ws, template)
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
                + check_c7b(ws, template)
                + check_c8(ws, template)
                + check_c9(ws, template)
                + check_c10(ws)
                + check_c11(ws, template)
                + check_c12(ws, template)
                + check_c13(ws, template)
            )
            suspects = {
                "C4": suspects_c4_env(ws, template),
                "C5": suspects_c5_iam(ws),
                "C6": suspects_c6_layers(ws, template),
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
                + check_c7b(ws, template)
                + check_c8(ws, template)
                + check_c9(ws, template)
                + check_c10(ws)
                + check_c11(ws, template)
                + check_c12(ws, template)
                + check_c13(ws, template)
            )
        checks += check_tools_unmodified() + check_spec_relaxations(ws)
        report_path = ws / "output" / "consistency_report.json"
        report = None
        if report_path.exists():
            try:
                report = json.loads(report_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                report = None
        if not isinstance(report, dict) or "status" not in report:
            checks.append(_finding("REPORT", "ERROR", "output/consistency_report.json missing or unreadable.", str(report_path)))
        elif report["status"] != "PASS":
            # Agents may write a provisional report first ("IN_PROGRESS");
            # only a finished, passing report counts.
            checks.append(_finding("REPORT", "ERROR", f"output/consistency_report.json status is {report['status']!r}, not 'PASS'.", str(report_path)))
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
