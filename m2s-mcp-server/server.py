"""M2S MCP server: exposes deterministic Mono2Sls tools (static analysis,
SAM template validation, consistency gates) to OpenHarness agents over stdio.

Registered in OpenHarness as `m2s-server`, so tool names resolve to:
    mcp__m2s-server__analyze_project
    mcp__m2s-server__validate_sam
    mcp__m2s-server__run_gate
    mcp__m2s-server__merge_blueprint_key
    mcp__m2s-server__annotate_entry_point
    mcp__m2s-server__annotate_shared_entry
    mcp__m2s-server__check_provenance
    mcp__m2s-server__run_validate
    mcp__m2s-server__run_coverage
    mcp__m2s-server__run_equivalence
    mcp__m2s-server__run_proxy_metrics
    mcp__m2s-server__run_rie_verdict
    mcp__m2s-server__update_ledger

Workspace resolution: all relative paths are resolved against $M2S_WORKSPACE
(default: ./runs/todo-oh relative to this file's parent dir) so tool output
never pollutes M2S's own reference `storage/`/`output/` directories.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP

import wrap_m2s
import m2s_merge
import provenance
import verify
import perf
from coord import tools as coord_tools

# Only import gates lazily / guard for P0: gates.py is a P1 deliverable.
try:
    import gates as _gates
except ImportError:
    _gates = None

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MONOLITH_ROOT = REPO_ROOT / "Mono2Sls-artifacts" / "mono_benchmark" / "todo"
# Matches the registered mcp_servers.m2s-server config: cwd=M2S,
# M2S_WORKSPACE=runs/todo-oh (relative to that cwd) -> M2S/runs/todo-oh.
# Kept separate from M2S/storage and M2S/output so it never overwrites the
# reference pipeline artifacts already sitting there.
DEFAULT_WORKSPACE = wrap_m2s.M2S_ROOT / "runs" / "todo-oh"


def _workspace() -> Path:
    ws = os.environ.get("M2S_WORKSPACE")
    path = Path(ws).resolve() if ws else DEFAULT_WORKSPACE
    path.mkdir(parents=True, exist_ok=True)
    return path


mcp = FastMCP("m2s-server")


@mcp.tool()
def analyze_project(monolith_root: str | None = None) -> str:
    """Run static analysis over the monolith and write
    analysis_report.json + symbol_table.json into the workspace.

    Args:
        monolith_root: absolute path to the monolith source root. Defaults
            to Mono2Sls-artifacts/mono_benchmark/todo if omitted.

    Returns: JSON string with report_path, symbol_table_path,
        entry_point_count, entry_points, config_info, dynamodb_info.
    """
    root = monolith_root or str(DEFAULT_MONOLITH_ROOT)
    result = wrap_m2s.analyze(root, str(_workspace()))
    return json.dumps(result, ensure_ascii=False)


@mcp.tool()
def validate_sam(template_path: str) -> str:
    """Run cfn-lint against a SAM template.

    Args:
        template_path: absolute path to the template.yaml to validate.

    Returns: JSON string {ok, errors, warnings, raw}.
    """
    result = wrap_m2s.validate_sam(template_path)
    return json.dumps(result, ensure_ascii=False)


@mcp.tool()
def merge_blueprint_key(key: str, value_json: str, mode: str = "append") -> str:
    """Merge one section into storage/blueprint.json, one top-level key at a time.

    Build the blueprint incrementally instead of writing one giant JSON
    document in a single shot: call this once per top-level key (e.g. once
    for "metadata", once for "cognito", once or more for "lambda_functions"
    if you want to add functions in a few batches). The file is validated as
    JSON on every call, so a malformed value_json is rejected immediately
    instead of surfacing later in run_gate.

    Args:
        key: one of metadata, lambda_functions, dynamodb_tables, s3_buckets,
            sqs_queues, eventbridge_rules, lambda_invoke_permissions,
            cognito, api_gateway, dropped_functions; plus data_model when the
            monolith's data store is not DynamoDB (analysis_report has
            source_data_store). Any other key is rejected -- do not invent
            new top-level keys.
        value_json: a JSON string holding the value for that key (an object
            for metadata/cognito/api_gateway, an array for the rest).
        mode: "append" (default) -- for array values, concatenates onto
            whatever is already there, and rejects the call if an item with
            the same "name" already exists (use this for genuinely NEW
            items). "replace" -- overwrites the key wholesale regardless of
            type, no duplicate check. Use "replace" when you're
            self-correcting something you already submitted (e.g. after
            run_gate flagged a problem with one function you already wrote):
            pass the FULL corrected array, not just the changed item.

    Returns: JSON string {action, key, output_path, current_keys, missing_keys}.
    """
    try:
        result = m2s_merge.merge_blueprint_key(str(_workspace()), key, value_json, mode=mode)
    except ValueError as exc:
        return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)
    return json.dumps({"ok": True, **result}, ensure_ascii=False)


@mcp.tool()
def annotate_entry_point(method: str, path: str, field: str, value_json: str) -> str:
    """Replace one spec field of one endpoint in storage/analysis_report.json.

    This is how m2s-spec-annotator writes its annotations: one (endpoint,
    field) at a time. The value is schema-checked and every `source`
    ({file, line, symbol}) in it is provenance-checked before anything is
    written; on any error nothing is written and the errors say exactly
    which item and citation failed.

    Args:
        method: HTTP method of the entry point, e.g. "POST".
        path: the entry point's path exactly as in analysis_report.json,
            e.g. "/api/orders" or "/api/books/:id".
        field: one of handler_location, auth, request, responses,
            data_access, business_rules.
        value_json: the FULL new value for that field (not a diff). Keep
            script items you agree with unchanged (origin "script");
            anything you add or modify must have origin "llm".

    Returns: JSON string {ok, errors} on rejection, or {ok, entry_point,
        field, annotated_fields, unknown_items}.
    """
    try:
        result = provenance.annotate_entry_point(str(_workspace()), method, path, field, value_json)
    except provenance.SpecError as exc:
        return json.dumps({"ok": False, "errors": [str(exc)]}, ensure_ascii=False)
    return json.dumps(result, ensure_ascii=False)


@mcp.tool()
def annotate_global_middleware(index: int, headers_json: str, note: str = "", source_json: str = "") -> str:
    """Set the response headers of one app-wide middleware item
    (analysis_report.global_middleware[index], found by P0: CORS(app),
    app.use(cors({...})), helmet, after_request hooks ...). P0 fills the
    headers only for library defaults; a configured call has headers null
    until you read its options and record what EVERY response gets.

    Args:
        index: position in global_middleware.
        headers_json: {"Header-Name": "value"}; where the value depends on the
            request, describe it, e.g. "<request Origin when in CORS_ORIGINS>".
        note: why (what the options mean).
        source_json: optional extra [{file, line, symbol}] citations (e.g. the
            config value the options read).

    Returns: JSON {ok, index, headers} or {ok: false, errors}.
    """
    try:
        result = provenance.annotate_global_middleware(str(_workspace()), index, headers_json, note, source_json)
    except provenance.SpecError as exc:
        return json.dumps({"ok": False, "errors": [str(exc)]}, ensure_ascii=False)
    return json.dumps(result, ensure_ascii=False)


@mcp.tool()
def annotate_shared_entry(entry_json: str) -> str:
    """F2:shared: write one shared-code rule into analysis_report.shared_rules.

    Shared code (middleware, data-access helpers) is annotated once, here,
    instead of once per endpoint. Endpoint business_rules cite it with
    {"rule": "ref: shared/<id>", "source": [...], "origin": "llm"}.

    Args:
        entry_json: {"id": "shared/<slug>", "file": "...", "function": "...",
            "rule": "...", "kind": "validation|authorization|data_scope|...",
            "source": [{file, line, symbol}], "origin": "llm",
            "used_by": ["METHOD /path", ...] (optional)}

    Returns: JSON {ok, id, shared_entries, replaced} or {ok: false, errors}.
    """
    try:
        result = provenance.annotate_shared_entry(str(_workspace()), entry_json)
    except provenance.SpecError as exc:
        return json.dumps({"ok": False, "errors": [str(exc)]}, ensure_ascii=False)
    return json.dumps(result, ensure_ascii=False)


@mcp.tool()
def check_provenance(endpoint: str = "") -> str:
    """Stage-1 gate for the spec fields in storage/analysis_report.json.

    Checks that every cited source line exists and mentions its symbol
    (within 2 lines), that every spec field matches its schema, and that
    every endpoint is complete (request, responses incl. a success
    response, auth, handler_location, at least one business rule).

    Args:
        endpoint: optional "METHOD /path" (for example "POST /api/orders") to
            check only that endpoint (v3 M2/W2.6). Shared rules are still
            validated as a whole.

    Returns: JSON string {status: PASS|FAIL, errors, incomplete,
        checked_sources, unknown_items, endpoints}.
    """
    return json.dumps(provenance.check_provenance(str(_workspace()), endpoint or None), ensure_ascii=False)


@mcp.tool()
def check_judge_assets() -> str:
    """Check the judge assets under judge/ (J1's completion check).

    Layout: judge/cases/<endpoint slug>/<case>.json (slug of "GET /api/books/:id"
    is "get-api-books-id"), judge/fixtures/<name>.json ({monolith table:
    [items]}), judge/faults/<scenario>.json ({description, cases, rules}),
    judge/identities.json, optional judge/run_config.json and
    judge/uncovered.json ([{ref, reason}]).

    Checks: the format of every file; every fact_ref exists in the fact base
    and its endpoint is called by a step; fixtures use the monolith's tables
    with their key attributes; identities, fixtures and ${vars} used are
    defined; every endpoint has a case; every response below 500 that is not
    a caught exception is cited by some case; every endpoint that writes has
    a case that reads the effect back (a later GET step with read_back: true).
    Coverage J1 cannot provide goes in judge/uncovered.json with the reason.

    Returns: JSON {status: PASS|FAIL, errors, warnings, summary}.
    """
    import judge
    try:
        return json.dumps(judge.check_judge_assets(_workspace()), ensure_ascii=False)
    except judge.JudgeError as exc:
        return json.dumps({"status": "ERROR", "error": str(exc)}, ensure_ascii=False)


@mcp.tool()
def judge_facts(endpoint: str = "") -> str:
    """The fact base as J1 needs it, without source citations: per endpoint the
    auth classification, request fields, every response (its fact ref such as
    "POST /api/cart#responses[2]", status, kind, when, body fields), data
    access and business rules (with their refs); plus the monolith's tables
    (keys) and the global middleware. Read this instead of printing
    storage/analysis_report.json.

    Args:
        endpoint: e.g. "POST /api/cart"; empty for every endpoint.

    Returns: JSON {endpoints, tables?, global_middleware?}.
    """
    import judge
    try:
        return json.dumps(judge.fact_digest(_workspace(), endpoint or None), ensure_ascii=False)
    except judge.JudgeError as exc:
        return json.dumps({"status": "ERROR", "error": str(exc)}, ensure_ascii=False)


@mcp.tool()
def check_adapter_map() -> str:
    """Check storage/adapter_map.json (A1's adapter map, PIPELINE_V2_PLAN.md
    §4.8) without running anything: every monolith table of the fact base has
    an entry (a target table, or target null with replaced_by and reason);
    target tables exist in the blueprint; the target's key attributes are
    produced; field specs are declarative (from / as identity / const /
    format / split / encode|decode json, drop); identity claims use only
    variables the aliases define; dropped endpoints exist; every judge
    fixture item maps to the target and back to itself.

    Returns: JSON {status: PASS|FAIL, errors}.
    """
    import judge
    try:
        return json.dumps(judge.check_adapter_map(_workspace()), ensure_ascii=False)
    except judge.JudgeError as exc:
        return json.dumps({"status": "ERROR", "error": str(exc)}, ensure_ascii=False)


@mcp.tool()
def check_adapter(ref: str = "", mode: str = "auto") -> str:
    """V1: run the frozen judge's read-only cases over fixtures on the target,
    the fixtures written through the adapter map: the target's reads must
    answer what the monolith answered on the same data, or the map (or the
    read) is wrong. Writes verification/check_adapter.json.

    Args:
        ref: a git ref of the workspace repo to judge (e.g. "v0"); empty: the working tree.
        mode: "auto" (RIE with Docker, else local Node), "rie" or "local".

    Returns: JSON {status: PASS|FAIL|ERROR, cases, failed, failures}.
    """
    import judge
    return json.dumps(judge.check_adapter(_workspace(), ref or None, mode), ensure_ascii=False)


@mcp.tool()
def run_judge_self_test(functions: list[str] | None = None) -> str:
    """I1 self test: run the frozen judge's cases on the code written so far, before any
    template exists. Routes and environment come from the blueprint; the functions run on
    this machine. Only the cases whose every step reaches a function with code. Advisory:
    nothing is written, V2 gives the verdict on the built template.

    Args:
        functions: blueprint function names to look at; empty: every function with code.

    Returns: JSON {status: PASS|FAIL|ERROR, cases_run, functions_with_code, summary, failures,
        failures_total}. Each failure gives the case, the step, what the monolith answered
        (expected) and what the function answered (actual); judge/cases/ has the case.
    """
    import judge
    return json.dumps(judge.run_judge_self_test(_workspace(), functions or None), ensure_ascii=False)


@mcp.tool()
def run_judge(ref: str = "", mode: str = "auto", cases: list[str] | None = None,
              functions: list[str] | None = None) -> str:
    """V2: run the frozen judge on the target's whole API (every case and every
    fault scenario; steps routed to their functions, SQS messages delivered
    between steps) and compare each step with the monolith's record: status,
    declared headers, body, symbol by symbol. Writes
    verification/verdict_judge.json when all cases run.

    Args:
        ref: a git ref of the workspace repo to judge; empty: the working tree.
        mode: "auto", "rie" or "local".
        cases: only these judge case ids (and their fault runs); no verdict file.
        functions: only the cases with a step these functions answer; no verdict file.

    Returns: JSON {status: PASS|FAIL|ERROR, summary, failures, verdict}.
    """
    import judge
    return json.dumps(judge.run_judge(_workspace(), ref or None, mode, cases or None, functions or None), ensure_ascii=False)


@mcp.tool()
def check_gap_inventory(require_decided: bool = False) -> str:
    """Check storage/gap_inventory.json: format, categories, evidence (each
    {file, line, symbol} really is in the monolith), endpoints in the fact base; a
    decided gap has decision {choice, why, test: delta | judge | none}. With
    require_decided (architect): every gap decided and mentioned in storage/DECISIONS.md.

    Returns: JSON {status: PASS|FAIL, errors, gaps, open}.
    """
    import gaps
    try:
        return json.dumps(gaps.check_inventory(_workspace(), require_decided), ensure_ascii=False)
    except gaps.GapError as exc:
        return json.dumps({"status": "ERROR", "error": str(exc)}, ensure_ascii=False)


@mcp.tool()
def check_mutant_classes() -> str:
    """J5 self-check: every mutant judge/judge_validation.json lists as not caught
    (survived, not_covered, trace_only) has a class in judge/mutant_classes.json
    ({mutant id: {class: equivalent | gap, reason, suggest?}}).

    Returns: JSON {status: PASS|FAIL, survivors, classes (counts), errors}.
    """
    import mutants
    return json.dumps(mutants.check_mutant_classes(_workspace()), ensure_ascii=False)


@mcp.tool()
def check_reconcile_classes() -> str:
    """J3 self-check: every difference in judge/reconcile.json has a class in
    judge/reconcile_classes.json ({difference id: {class, reason, fact_ref?,
    fix?}}), class one of fact_error / needs_ruling / case_error / mask_gap,
    with a reason; mask_gap needs rules in judge/mask_rules.json.

    Returns: JSON {status: PASS|FAIL, differences, classes (counts), errors}.
    """
    import judge
    return json.dumps(judge.check_reconcile_classified(_workspace()), ensure_ascii=False)


@mcp.tool()
def run_monolith(passes: int = 3, coverage: bool = True) -> str:
    """J2: run every judge case, and every case of every fault scenario, on the
    monolith itself, `passes` times (at least 3), and write the judge's
    expectations. Needs check_judge_assets to pass.

    Each pass starts the monolith afresh on a local DynamoDB (DynamoDB Local
    with Docker, else dynalite) behind the recording proxy; before each case
    the tables are emptied and seeded with its fixtures and its identities
    are prepared (not recorded). Values that differ between passes 1 and 2
    become symbols (<uuid#1>, <time#2>, <id:u1>, ...); pass 3 is checked
    against them.

    Writes judge/oracle_expected.json, judge/fault_baseline_monolith.json,
    judge/masks.json, judge/monolith_calls.json, judge/coverage_monolith/.

    Returns: JSON {status: PASS|FAIL|ERROR, problems (failed requests,
    identity preparation, monolith exits), unstable (values that cannot be
    masked), coverage, ...}.
    """
    import judge
    if os.environ.get("M2S_MODE") == "J1":
        # J1's predictions come from the fact base alone; the monolith's output is what they are checked against.
        return json.dumps({"status": "REFUSED", "error": "J1 writes predictions from the fact base only; run_monolith "
                           "is run by the dispatcher after J1 (J2)"}, ensure_ascii=False)
    try:
        return json.dumps(judge.run_monolith(_workspace(), passes=passes, coverage=coverage), ensure_ascii=False,
                          default=str)
    except judge.JudgeError as exc:
        return json.dumps({"status": "ERROR", "error": str(exc)}, ensure_ascii=False)


def _verify_call(fn, *args, **kwargs) -> str:
    try:
        return json.dumps(fn(str(_workspace()), *args, **kwargs), ensure_ascii=False)
    except verify.VerifyError as exc:
        return json.dumps({"status": "ERROR", "error": str(exc)}, ensure_ascii=False)


@mcp.tool()
def run_validate(functions: list[str] | None = None, cases: list[str] | None = None,
                 dry_run: bool = False, mode: str = "auto", ref: str | None = None,
                 assets: str | None = None) -> str:
    """Run the verification test cases (verification/events/<function>/*.json)
    and judge each one against its expectations.

    Args:
        functions: only run cases of these blueprint function names.
        cases: only run these case ids.
        dry_run: only check the test assets (format, names, spec_refs,
            fault scenarios) against the blueprint -- no execution, no
            template needed. This is what V1 uses before any code exists.
        mode: "auto" (RIE containers if Docker is available, else local
            Node), "rie" or "local".
        ref: run the current test assets against the code of this git ref
            of the workspace repo (e.g. "v0" after test cases were revised)
            instead of the working tree's code.
        assets: the delta tests, "verification/delta" (v2 D1, V3). Their cases also
            need source and expected_from, every gap decided with test: "delta"
            needs a case, and a full run writes verification/verdict_delta.json.

    A full run (no functions/cases filter, not dry_run) also writes
    verification/verdict_A.json, the stage-A verdict. The first run does a
    `sam build` (minutes); later runs reuse it until output/ changes. The
    verdict records which code it describes (code_state: the ref, or the
    working tree with its uncommitted output/ changes).

    Returns: JSON string {status: PASS|FAIL, total, passed, failed,
        failures: [{id, mismatches, triage_hint, logs_tail, sdk_calls}],
        cases_with_conflicts (cases where the proxy rejected a request that
        touched an item of an ongoing transaction, as real DynamoDB does;
        sdk_calls mark them "!conflict"), mode, disclaimer} or {status, dry_run, schema_errors, ...}.
    """
    return _verify_call(verify.run_validate, functions, cases, dry_run, mode, ref, assets=assets)


@mcp.tool()
def run_coverage(functions: list[str] | None = None, mode: str = "auto", changed_since: str | None = None,
                 assets: str | None = None, with_judge: bool = False) -> str:
    """Run the test cases with V8 coverage and report branch/statement
    coverage per function. Every uncovered branch and statement (file, line,
    code) is written to verification/coverage/<function>.json -- use it to
    add white-box cases.

    Args:
        functions: only these blueprint function names.
        mode: "auto", "rie" or "local".
        changed_since: a git ref of the workspace repo (e.g. "HEAD"); also
            report whether every executable line changed since that ref was
            executed by at least one test (changed_lines.all_covered).
        assets: run the delta tests, "verification/delta" (v2 V3).
        with_judge: (v2 O2, needs functions and changed_since) a changed line counts as
            covered when the judge's cases or the delta tests executed it. Returns
            {status, functions: {name: {covered, uncovered, ...}}, all_covered}.

    Returns: JSON string {mode, functions: {name: {statements_pct,
        branches_pct, functions_pct, files, uncovered_count}},
        changed_lines?}.
    """
    if with_judge:
        import judge
        if not functions or not changed_since:
            return json.dumps({"status": "ERROR", "error": "with_judge needs functions and changed_since"})
        return json.dumps(judge.changed_lines_covered(_workspace(), functions, changed_since, assets), ensure_ascii=False)
    return _verify_call(verify.run_coverage, functions, mode, changed_since, assets=assets)


@mcp.tool()
def run_equivalence(mode: str = "record", label: str = "v0", functions: list[str] | None = None,
                    runtime: str = "auto", speculative_reads: list[str] | None = None,
                    assets: str | None = None) -> str:
    """Fault-injection runs (verification/faults/*.json).

    Args:
        mode: "record" stores every scenario's observations (SDK call
            multiset, return value / thrown error / batchItemFailures,
            messages, table state) as verification/fault_baseline_<label>.json.
            "diff" reruns them, plus every test case of the functions
            without scenario faults, on the baseline (git ref v0) and the
            working tree and compares: H1 (a) same calls, (b) same
            externally visible outcome, (c) no emulated TransactionConflict
            (concurrent requests on one item) where the baseline had none.
        label: baseline label, "v0" by default.
        functions: only scenarios of these functions. A filtered record
            replaces just these functions' scenarios and observations in an
            existing baseline file (same runtime required) and keeps the rest.
        runtime: "auto", "rie" or "local".
        speculative_reads: (diff, rule R1b) functions whose reads were moved
            ahead of an error-propagation early return. H1 (a) then accepts
            extra reads on runs the baseline answered with an error other
            than 401/403, provided writes, messages and every baseline read
            are unchanged; the extra reads are reported, not hidden.
        assets: use the delta tests, "verification/delta" (v2 V3 records
            label "B0" on them).

    Returns: JSON string {status: RECORDED|EQUIVALENT|DIFFERENT|..., runs,
        differences, infra_errors[, speculative_reads, extra_reads_total]}.
    """
    return _verify_call(verify.run_equivalence, mode, label, functions, runtime, speculative_reads=speculative_reads, assets=assets)


@mcp.tool()
def run_proxy_metrics(functions: list[str] | None = None, benchmarks: list[str] | None = None, ref: str | None = None) -> str:
    """Proxy metrics for the performance benchmarks (perf/benchmarks/<function>/*.json):
    SDK call count, sequential depth (at a fixed 10ms per call), emulated
    TransactionConflict rejections (at 10ms, concurrent requests on one
    item), warm CPU
    (wall time at zero latency, median of 300 in local Node), estimated
    DynamoDB request units / SQS requests and cost. Proxies only locate and
    rank problems; they never decide a verdict on their own.

    Args:
        functions / benchmarks: filters.
        ref: measure this git ref of the workspace repo instead of the
            working tree (e.g. "v0").

    Returns: JSON string {status, version, benchmarks: {id: {...}}}.
    """
    try:
        return json.dumps(perf.run_proxy_metrics(str(_workspace()), functions, benchmarks, ref), ensure_ascii=False)
    except verify.VerifyError as exc:
        return json.dumps({"status": "ERROR", "error": str(exc)}, ensure_ascii=False)


@mcp.tool()
def run_config_verdict(baseline: str = "HEAD") -> str:
    """Tier-A verdict for a template-only Architectures change (rule R5):
    checks that only output/template.yaml changed and only in Architectures,
    gate(template), sam build, and compiled modules per function; cost from
    the per-architecture duration price. Runs no validation, equivalence or
    RIE rounds (the local x86_64 run cannot see an architecture change).
    Takes about a minute.

    Returns: JSON string {status, tier, verdict, reason, architectures,
        duration_price_ratio, native_binaries, dimensions, saved_to}.
    """
    try:
        return json.dumps(perf.run_config_verdict(str(_workspace()), baseline), ensure_ascii=False)
    except verify.VerifyError as exc:
        return json.dumps({"status": "ERROR", "error": str(exc)}, ensure_ascii=False)


@mcp.tool()
def run_rie_verdict(benchmark: str, baseline: str = "HEAD", latencies: list[int] | None = None,
                    guard_benchmarks: list[str] | None = None) -> str:
    """Judge the working tree against <baseline> (a git ref, default HEAD =
    v0 plus the changes kept so far) on one benchmark, following
    PERF_OPTIMIZATION_DESIGN.md §3: validity check, proxies for both
    versions, alternating RIE measurements (5 rounds of 1 cold + 30 warm
    invocations, CPU capped at MemorySize/1769) at 10ms simulated latency,
    and at 0/5/15ms too when a proxy or the 10ms run gets worse; then the
    latency / init / cost dimensions ("clearly" = ranges do not overlap)
    and the RSS guard give the verdict: kept | kept_inconclusive |
    pending_human | revert. A change that adds emulated TransactionConflict
    rejections (concurrent requests on one item) is reverted outright.
    guard_benchmarks: further benchmark ids the change must not make worse
    in any dimension nor add conflicts to (required by rule R4: the hot-key
    benchmark); each costs another measurement. Takes several minutes. Run
    the veto checks (gate(final), run_validate, run_equivalence diff,
    run_coverage changed_since) first -- this tool does not.

    Returns: JSON string {status, verdict, reason, veto, dimensions, proxies,
        rie, init_ms, cost_usd_per_invocation, rss_guard, conflicts, guards,
        saved_to}.
    """
    try:
        return json.dumps(perf.run_rie_verdict(str(_workspace()), benchmark, baseline, latencies, guard_benchmarks), ensure_ascii=False)
    except verify.VerifyError as exc:
        return json.dumps({"status": "ERROR", "error": str(exc)}, ensure_ascii=False)


@mcp.tool()
def update_ledger() -> str:
    """Fold output/perf_report.json (its rule_scan and changes) into the
    cross-application rule ledger m2s-mcp-server/perf-rules/ledger.json.
    Re-running for the same workspace replaces its earlier entry.

    Returns: JSON string {status, app, rules, candidates, ledger}.
    """
    try:
        return json.dumps(perf.update_ledger(str(_workspace())), ensure_ascii=False)
    except verify.VerifyError as exc:
        return json.dumps({"status": "ERROR", "error": str(exc)}, ensure_ascii=False)


@mcp.tool()
def run_gate(phase: str, package: str = "", functions_json: str = "") -> str:
    """Run the deterministic consistency gate for a migration phase.

    Args:
        phase: one of "blueprint", "code", "template", "final".
        package: optional work-package id (M2). For phase "code", C1 is scoped
            to the functions listed for the package in
            storage/work_packages.json (impl_packages, filled after S1).
        functions_json: optional JSON list of function names; an explicit scope
            that overrides `package` for the C1 code-direction check.

    Returns: JSON string {phase, status, mechanical_failures, checks,
        suspects, stage_report}.
    """
    functions = None
    if functions_json:
        try:
            functions = json.loads(functions_json)
        except json.JSONDecodeError as exc:
            return json.dumps({"phase": phase, "status": "ERROR", "error": f"functions_json is not valid JSON: {exc}"},
                              ensure_ascii=False)
        if not isinstance(functions, list) or not all(isinstance(f, str) for f in functions):
            return json.dumps({"phase": phase, "status": "ERROR",
                               "error": "functions_json must be a JSON list of function names"}, ensure_ascii=False)
    if _gates is None:
        return json.dumps(
            {
                "phase": phase,
                "status": "not_implemented",
                "mechanical_failures": [],
                "checks": [],
                "suspects": {},
                "stage_report": {"path": "", "exists": False},
                "note": "gates.py not yet implemented (P1 deliverable)",
            },
            ensure_ascii=False,
        )
    result = _gates.run_gate(phase, str(_workspace()), functions=functions, package=package or None)
    return json.dumps(result, ensure_ascii=False)


# --- v3 dispatcher coordination (PIPELINE_V3_PLAN.md §4.6) -------------------
# These only work in a session started by coord.dispatcher (it sets
# M2S_COORD_DIR / M2S_TASK_ID / M2S_ATTEMPT / M2S_ROLE on this process).


@mcp.tool()
def submit_task(summary: str, notes: str = "") -> str:
    """Hand your finished task to the dispatcher. Call this once, after you have
    written your stage report, then end the session. The dispatcher runs the
    task's checks itself; your own success claim is not the verdict.

    Args:
        summary: one or two sentences on what you produced.
        notes: anything the next attempt or a reviewer should know.
    """
    return json.dumps(coord_tools.safe(coord_tools.submit_task, summary, notes), ensure_ascii=False)


@mcp.tool()
def report_blocked(reason: str, evidence: str = "", suspected_module: str = "") -> str:
    """Tell the dispatcher you cannot finish this task, then end the session.

    Args:
        reason: what stops you.
        evidence: gate output, error text, file and line.
        suspected_module: the earlier stage you believe caused it (e.g. "S2"
            when the template is right but a handler is wrong); empty if it is
            this task's own problem or you do not know.
    """
    return json.dumps(coord_tools.safe(coord_tools.report_blocked, reason, evidence, suspected_module), ensure_ascii=False)


@mcp.tool()
def get_task_context() -> str:
    """Return the full instructions and feedback for your current task."""
    return json.dumps(coord_tools.safe(coord_tools.get_task_context), ensure_ascii=False)


@mcp.tool()
def handler_decide(case: str, action: str, target: str = "", feedback: str = "", rationale: str = "") -> str:
    """Exception handler only: record your decision for a case, then end the session.

    Args:
        case: the case id from your instructions.
        action: "retry" (same task once more, with your feedback), "reroute"
            (rework an earlier task, named in target), "escalate" (hand to a
            person), or "ok" (phase review: nothing to do).
        target: task id for reroute (e.g. "S2").
        feedback: what the worker should do differently, in specification
            language (no test cases or test file paths for implementation tasks).
        rationale: why, citing the evidence in the case file.
    """
    return json.dumps(coord_tools.safe(coord_tools.handler_decide, case, action, target, feedback, rationale), ensure_ascii=False)


@mcp.tool()
def record_ruling(question: str, conclusion: str, category: str, basis: str, raised_by: str = "",
                  options: list[str] | None = None, relaxation: str = "", affected_cases: list[str] | None = None,
                  layers: list[str] | None = None, affected_tasks: list[str] | None = None,
                  deviates_from_monolith: bool = False) -> str:
    """Architect or exception handler only: record a ruling on a question the facts do
    not settle (PIPELINE_V2_PLAN.md §4.6). It becomes part of storage/rulings.json when
    your task passes its checks; one that departs from the monolith also needs a person.
    Do not edit storage/rulings.json yourself.

    Args:
        question: the question, as raised (cite the case, difference or issue id).
        conclusion: what is decided, precise enough to compare against.
        category: "contract" (observable behaviour the target must keep),
            "implementation-trace" (how the monolith happens to do it; not compared),
            "migration-change" (changed on purpose by the migration, e.g. Cognito
            replaces the login endpoints), or "spec-gap" (the facts say nothing).
        basis: the evidence: fact refs, monolith output, design rules.
        raised_by: the module that raised it (e.g. "J3"); default: this task.
        options: the options that were considered.
        relaxation: for a behaviour that may vary: "order", "freshness" or
            "alternatives" (several acceptable results); compared on the R layer.
        affected_cases: judge case ids the ruling changes.
        layers: observation layers it concerns: "P", "R", "T".
        affected_tasks: closed tasks that must be checked again because of it.
        deviates_from_monolith: true when the target will NOT copy the monolith's
            behaviour (e.g. fixing a monolith defect); a person must confirm it.
    """
    return json.dumps(coord_tools.safe(coord_tools.record_ruling, question, conclusion, category, basis, raised_by,
                                       options, relaxation, affected_cases, layers, affected_tasks,
                                       deviates_from_monolith), ensure_ascii=False)


if __name__ == "__main__":
    mcp.run(transport="stdio")
