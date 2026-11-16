"""Done checks and the tools-unchanged guard (PIPELINE_V3_PLAN.md §4.5).

A done check looks only at files and tool results, never at what the worker
says. Each returns a CheckResult whose `relay` is the text handed to the next
attempt as feedback. For implementation-line tasks the relay must stay in
specification / gate language (v1 SKILL §2); the checks used on those tasks
only produce gate findings, so that holds by construction.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

REPO = Path(__file__).resolve().parents[2]
M2S_DIR = REPO / "m2s-mcp-server"
if str(M2S_DIR) not in sys.path:
    sys.path.insert(0, str(M2S_DIR))

RELAY_LIMIT = 6000


@dataclass
class CheckResult:
    name: str
    ok: bool
    summary: str
    detail: dict[str, Any] = field(default_factory=dict)
    relay: str = ""

    def to_event(self) -> dict[str, Any]:
        return {"name": self.name, "ok": self.ok, "summary": self.summary}


def _clip(text: str, limit: int = RELAY_LIMIT) -> str:
    return text if len(text) <= limit else text[:limit] + f"\n…（截断，原文 {len(text)} 字符）"


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# --- individual checks ---------------------------------------------------------


def analysis_report(ws: Path) -> CheckResult:
    report = _read_json(ws / "storage" / "analysis_report.json")
    ok = isinstance(report, dict) and bool((report.get("spec_meta") or {}).get("monolith_root")) \
        and bool(report.get("entry_points")) and (ws / "storage" / "symbol_table.json").is_file()
    n = len(report.get("entry_points") or []) if isinstance(report, dict) else 0
    return CheckResult("analysis_report", ok, f"{n} entry points" if ok else "analysis_report.json / symbol_table.json missing or incomplete")


def check_provenance(ws: Path) -> CheckResult:
    import provenance

    result = provenance.check_provenance(str(ws))
    ok = result.get("status") == "PASS"
    problems = {"errors": result.get("errors", [])[:40], "incomplete": result.get("incomplete", [])[:40]}
    return CheckResult("check_provenance", ok,
                       f"provenance {result.get('status')}: {len(result.get('errors', []))} errors, {len(result.get('incomplete', []))} incomplete",
                       detail=result, relay="" if ok else "check_provenance 报告：\n" + _clip(json.dumps(problems, ensure_ascii=False, indent=1)))


def blueprint_keys(ws: Path) -> CheckResult:
    import m2s_merge

    blueprint = _read_json(ws / "storage" / "blueprint.json")
    if not isinstance(blueprint, dict):
        return CheckResult("blueprint_keys", False, "storage/blueprint.json missing or not JSON",
                           relay="storage/blueprint.json 不存在或不是合法 JSON。")
    missing = sorted(m2s_merge.BLUEPRINT_KEYS - set(blueprint))
    return CheckResult("blueprint_keys", not missing, "all top-level keys present" if not missing else f"missing {missing}",
                       relay="" if not missing else f"blueprint.json 缺少顶层字段：{missing}")


def _gate(phase: str, require_pass: bool) -> Callable[[Path], CheckResult]:
    def check(ws: Path) -> CheckResult:
        import gates

        result = gates.run_gate(phase, str(ws))
        status = result.get("status")
        ok = status == "PASS" or (status == "WARN" and not require_pass)
        findings = [c for c in result.get("checks", []) if c.get("severity") == "ERROR"]
        summary = f"gate({phase}) {status}" + (f": {sorted({f['check_id'] for f in findings})}" if findings else "")
        relay = "" if ok else f"run_gate(phase=\"{phase}\") 报告：\n" + _clip(json.dumps(
            {"status": status, "errors": findings, "mechanical_failures": result.get("mechanical_failures")},
            ensure_ascii=False, indent=1))
        return CheckResult(f"gate_{phase}", ok, summary, detail=result, relay=relay)

    return check


def check_provenance_pkg(ws: Path, spec: dict[str, Any]) -> CheckResult:
    """S0:<pkg> (v3 M2): check_provenance for the endpoints in this package."""
    import provenance

    endpoints = spec.get("endpoints") or []
    if not endpoints:
        return CheckResult("check_provenance_pkg", True, "no endpoints in this package")
    errors: list[str] = []
    incomplete: list[str] = []
    for endpoint in endpoints:
        result = provenance.check_provenance(str(ws), endpoint)
        errors.extend(result.get("errors") or [])
        incomplete.extend(result.get("incomplete") or [])
    ok = not errors and not incomplete
    summary = f"{len(endpoints)} endpoints checked" if ok else f"{len(errors)} errors, {len(incomplete)} incomplete"
    relay = "" if ok else _clip("check_provenance 报告：\n" + json.dumps(
        {"errors": errors, "incomplete": incomplete}, ensure_ascii=False, indent=1))
    return CheckResult("check_provenance_pkg", ok, summary, detail={"errors": errors, "incomplete": incomplete},
                       relay=relay)


def room_turn(ws: Path, spec: dict[str, Any]) -> CheckResult:
    """M3: this turn's participant posted its contribution into the room."""
    from coord import notices

    room_id = spec.get("room_id") or ""
    role = spec.get("room_role") or ""
    kind = spec.get("room_kind") or "spec"
    if not room_id or not role:
        return CheckResult("room_turn", False, "task spec has no room_id/room_role")
    try:
        entries = notices.read_room(ws, role, room_id, kind)
    except notices.RelayError as exc:
        return CheckResult("room_turn", False, str(exc))
    if entries and entries[-1].get("sender_role") == role:
        return CheckResult("room_turn", True, f"room {room_id}: {role} posted #{len(entries)}")
    return CheckResult("room_turn", False, f"room {room_id}: {role} has not posted yet",
                       relay="轮到你发言：用 mcp__m2s-server__room_post 提交你的意见，然后调用 submit_task。")


def global_middleware_determined(ws: Path, spec: dict[str, Any]) -> CheckResult:
    """S0-shared (v3 M2): app-wide middleware headers are filled in."""
    import provenance

    result = provenance.check_global_middleware(str(ws))
    ok = result["status"] == "PASS"
    summary = "global middleware determined" if ok else "global middleware incomplete"
    relay = "" if ok else _clip("check_global_middleware 报告：\n" + json.dumps(result, ensure_ascii=False, indent=1))
    return CheckResult("global_middleware", ok, summary, detail=result, relay=relay)


def gate_code_pkg(ws: Path, spec: dict[str, Any]) -> CheckResult:
    """S2:<pkg> / S2-shared (v3 M2): gate(code) scoped to the package's functions."""
    import gates

    functions = spec.get("functions")
    package = spec.get("package")
    result = gates.run_gate("code", str(ws), functions=functions,
                            package=None if package in (None, "shared") else package)
    status = result.get("status")
    ok = status in ("PASS", "WARN")
    findings = [c for c in result.get("checks", []) if c.get("severity") == "ERROR"]
    summary = f"gate(code) {status}" + (f": {sorted({f['check_id'] for f in findings})}" if findings else "")
    relay = "" if ok else "run_gate(phase=\"code\") 报告：\n" + _clip(json.dumps(
        {"status": status, "errors": findings, "mechanical_failures": result.get("mechanical_failures")},
        ensure_ascii=False, indent=1))
    return CheckResult("gate_code_pkg", ok, summary, detail=result, relay=relay)


def consistency_pass(ws: Path) -> CheckResult:
    report = _read_json(ws / "output" / "consistency_report.json")
    status = report.get("status") if isinstance(report, dict) else None
    return CheckResult("consistency_pass", status == "PASS", f"consistency_report status {status!r}",
                       relay="" if status == "PASS" else f"output/consistency_report.json 的 status 是 {status!r}，不是 PASS。")


def _validate(dry_run: bool) -> Callable[[Path], CheckResult]:
    def check(ws: Path) -> CheckResult:
        import verify

        name = "validate_dry_run" if dry_run else "validate_full"
        try:
            result = verify.run_validate(str(ws), dry_run=dry_run)
        except verify.VerifyError as exc:
            return CheckResult(name, False, f"run_validate error: {exc}", relay=f"run_validate 报错：{exc}")
        ok = result.get("status") == "PASS"
        summary = f"run_validate{'(dry_run)' if dry_run else ''} {result.get('status')}"
        if not dry_run:
            summary += f": {result.get('passed')}/{result.get('total')} passed"
        relay = "" if ok else "run_validate 结果摘要：\n" + _clip(json.dumps(
            {k: result.get(k) for k in ("status", "total", "passed", "failed", "failures", "errors", "schema_errors")
             if k in result}, ensure_ascii=False, indent=1))
        return CheckResult(name, ok, summary, detail={k: v for k, v in result.items() if k != "results"}, relay=relay)

    return check


def _template_part(n: int) -> Callable[[Path], CheckResult]:
    def check(ws: Path) -> CheckResult:
        path = ws / "output" / f"template.part{n}.yaml"
        if not path.is_file() or not path.read_text(encoding="utf-8", errors="replace").strip():
            return CheckResult(f"template_part{n}", False, f"{path.name} missing or empty",
                               relay=f"output/{path.name} 不存在或为空。")
        if n == 1 and "# --- END METADATA ---" not in path.read_text(encoding="utf-8", errors="replace"):
            return CheckResult("template_part1", False, "part1 lacks '# --- END METADATA ---'",
                               relay="output/template.part1.yaml 缺少 '# --- END METADATA ---' 结束标记。")
        return CheckResult(f"template_part{n}", True, f"{path.name} present")

    return check


def s5_report(ws: Path) -> CheckResult:
    """The optimizer's report (v1 S5, v2 O): every kept change has its own commit since the baseline."""
    report = _read_json(ws / "output" / "perf_report.json")
    reports = ws / "storage" / "stage_reports"
    if not isinstance(report, dict) or not ((reports / "S5.json").is_file() or (reports / "O.json").is_file()):
        return CheckResult("s5_report", False, "perf_report.json or the stage report missing",
                           relay="output/perf_report.json 或 storage/stage_reports/ 下这个任务的工作报告不存在。")
    kept = [c.get("id") for c in report.get("changes") or [] if isinstance(c, dict)
            and c.get("verdict") in ("kept", "kept_inconclusive")]
    has_b0 = subprocess.run(["git", "-C", str(ws), "tag", "--list", "B0"], capture_output=True, text=True).stdout.strip()
    base = "B0" if has_b0 else "v0"
    proc = subprocess.run(["git", "-C", str(ws), "rev-list", "--count", f"{base}..HEAD"], capture_output=True, text=True)
    commits = int(proc.stdout.strip() or 0) if proc.returncode == 0 else 0
    ok = commits >= len(kept)
    return CheckResult("s5_report", ok, f"{len(kept)} kept changes, {commits} commits since {base}",
                       relay="" if ok else f"perf_report.json 里有 {len(kept)} 个保留的改动（{kept}），但 {base} 之后只有 {commits} 个提交；每个保留的改动都要单独提交。")


def judge_assets(ws: Path) -> CheckResult:
    """J1's completion check (v2 §4.1)."""
    import judge

    try:
        result = judge.check_judge_assets(ws)
    except judge.JudgeError as exc:
        return CheckResult("judge_assets", False, f"check_judge_assets error: {exc}", relay=f"check_judge_assets 报错：{exc}")
    ok = result["status"] == "PASS"
    s = result.get("summary") or {}
    summary = (f"check_judge_assets {result['status']}: {s.get('cases', 0)} cases, required responses "
               f"{s.get('required_responses', '?')}" + ("" if ok else f", {len(result['errors'])} errors"))
    return CheckResult("judge_assets", ok, summary, detail={"summary": s},
                       relay="" if ok else "check_judge_assets 报告的问题：\n" + _clip("\n".join(result["errors"])))


def judge_cases_pkg(ws: Path, spec: dict[str, Any]) -> CheckResult:
    """J1:<pkg> (v3 M4/W4.2): one package's cases; J1-shared runs judge_assets."""
    import judge

    package = str(spec.get("package") or "")
    try:
        result = judge.check_cases_package(ws, package, spec.get("functions") or [])
    except judge.JudgeError as exc:
        return CheckResult("judge_cases_pkg", False, f"check_cases_package error: {exc}",
                           relay=f"check_cases_package 报错：{exc}")
    ok = result["status"] == "PASS"
    return CheckResult("judge_cases_pkg", ok, f"{result['cases']} cases for package {package}"
                       + ("" if ok else f", {len(result['errors'])} problems"), detail=result,
                       relay="" if ok else f"包 {package} 的裁判用例问题：\n" + _clip("\n".join(result["errors"])))


def reconcile_classified(ws: Path) -> CheckResult:
    """J3: every difference of judge/reconcile.json is classified (v2 §4.5)."""
    import judge

    result = judge.check_reconcile_classified(ws)
    ok = result["status"] == "PASS"
    return CheckResult("reconcile_classified", ok,
                       f"{result['differences']} differences" + (f", classes {result['classes']}" if ok else
                                                                  f", {len(result['errors'])} problems"),
                       detail=result, relay="" if ok else "judge/reconcile_classes.json 的问题：\n" + _clip("\n".join(result["errors"])))


def adapter_map(ws: Path) -> CheckResult:
    """A1's adapter map passes its static check (v2 §4.8; also part of gate(blueprint), AM)."""
    import judge

    try:
        result = judge.check_adapter_map(ws)
    except judge.JudgeError as exc:
        return CheckResult("adapter_map", False, f"check_adapter_map error: {exc}", relay=f"check_adapter_map 报错：{exc}")
    ok = result["status"] == "PASS"
    return CheckResult("adapter_map", ok, "adapter map PASS" if ok else f"adapter map: {len(result['errors'])} errors",
                       detail=result, relay="" if ok else "check_adapter_map 报告的问题：\n" + _clip("\n".join(result["errors"])))


def mutant_classes(ws: Path) -> CheckResult:
    """J5: every mutant the judge did not catch is classified (equivalent / gap)."""
    import mutants

    result = mutants.check_mutant_classes(ws)
    ok = result["status"] == "PASS"
    return CheckResult("mutant_classes", ok, f"{result.get('survivors', 0)} uncaught mutants"
                       + (f", classes {result.get('classes')}" if ok else f", {len(result['errors'])} problems"),
                       detail=result, relay="" if ok else "judge/mutant_classes.json 的问题：\n" + _clip("\n".join(result["errors"])))


def gap_inventory(ws: Path) -> CheckResult:
    """G2: the inventory's format and evidence hold (each cited line really is there)."""
    import gaps

    try:
        result = gaps.check_inventory(ws)
    except gaps.GapError as exc:
        return CheckResult("gap_inventory", False, f"gap inventory: {exc}", relay=f"storage/gap_inventory.json 的问题：{exc}")
    ok = result["status"] == "PASS"
    return CheckResult("gap_inventory", ok, f"{result['gaps']} gaps, {len(result['open'])} undecided"
                       + ("" if ok else f", {len(result['errors'])} problems"), detail=result,
                       relay="" if ok else "storage/gap_inventory.json 的问题：\n" + _clip("\n".join(result["errors"])))


def gap_package(ws: Path, spec: dict[str, Any]) -> CheckResult:
    """G2:<pkg> (v3 M4/W4.2): one package's share of the gap inventory."""
    import gaps

    package = str(spec.get("package") or "")
    try:
        result = gaps.check_package(ws, package, spec.get("endpoints") or [])
    except gaps.GapError as exc:
        return CheckResult("gap_package", False, f"gap package: {exc}",
                           relay=f"storage/gaps/{package}.json 的问题：{exc}")
    ok = result["status"] == "PASS"
    return CheckResult("gap_package", ok, f"{result['gaps']} gaps for package {package}"
                       + ("" if ok else f", {len(result['errors'])} problems"), detail=result,
                       relay="" if ok else f"storage/gaps/{package}.json 的问题：\n" + _clip("\n".join(result["errors"])))


def gap_decisions(ws: Path) -> CheckResult:
    """A1/A2: every gap has a decision, and storage/DECISIONS.md mentions it."""
    import gaps

    try:
        result = gaps.check_inventory(ws, require_decided=True)
    except gaps.GapError as exc:
        return CheckResult("gap_decisions", False, f"gap inventory: {exc}", relay=f"storage/gap_inventory.json 的问题：{exc}")
    ok = result["status"] == "PASS"
    return CheckResult("gap_decisions", ok, f"{result['gaps']} gaps decided" if ok else f"{len(result['errors'])} problems, {len(result['open'])} undecided",
                       detail=result, relay="" if ok else "缺口决定的问题：\n" + _clip("\n".join(result["errors"])))


def delta_assets(ws: Path) -> CheckResult:
    """D1: the delta tests pass the dry run (format, names, sources, where expectations come from)."""
    import verify

    try:
        result = verify.run_validate(str(ws), dry_run=True, assets=verify.DELTA_ASSETS)
    except verify.VerifyError as exc:
        return CheckResult("delta_assets", False, f"delta tests: {exc}", relay=f"run_validate(dry_run, assets) 报错：{exc}")
    ok = result.get("status") == "PASS"
    return CheckResult("delta_assets", ok, f"{result.get('cases')} delta cases, {result.get('fault_scenarios')} fault scenarios"
                       + ("" if ok else f", {len(result.get('schema_errors') or [])} problems"),
                       detail={k: v for k, v in result.items() if k != "note"},
                       relay="" if ok else "增量测试的问题：\n" + _clip("\n".join(result.get("schema_errors") or [])))


def delta_assets_domain(ws: Path, spec: dict[str, Any]) -> CheckResult:
    """D1:<domain> (v3 M4/W4.2): this domain's delta tests pass the dry run.
    The other domains write theirs in parallel, so the "every delta gap is
    cited" check waits for V3's full run."""
    import verify

    functions = spec.get("functions") or None
    try:
        result = verify.run_validate(str(ws), functions=functions, dry_run=True,
                                     assets=verify.DELTA_ASSETS, partial=True)
    except verify.VerifyError as exc:
        return CheckResult("delta_assets_domain", False, f"delta tests: {exc}",
                           relay=f"run_validate(dry_run, assets) 报错：{exc}")
    ok = result.get("status") == "PASS"
    return CheckResult("delta_assets_domain", ok,
                       f"{result.get('cases')} delta cases, {result.get('fault_scenarios')} fault scenarios"
                       + ("" if ok else f", {len(result.get('schema_errors') or [])} problems"),
                       detail={k: v for k, v in result.items() if k != "note"},
                       relay="" if ok else "增量测试的问题：\n" + _clip("\n".join(result.get("schema_errors") or [])))


def verdict_delta(ws: Path) -> CheckResult:
    """V3: the delta tests pass on the code as it is, and coverage and the fault baseline are there."""
    import verify

    verdict = _read_json(ws / "verification" / "verdict_delta.json")
    problems = []
    if not isinstance(verdict, dict):
        problems.append("verification/verdict_delta.json 不存在：要跑不带过滤条件的 run_validate(assets=\"verification/delta\")")
    else:
        if verdict.get("status") != "PASS":
            problems.append(f"verdict_delta 的 status 是 {verdict.get('status')!r}，失败 {[f.get('id') for f in verdict.get('failed') or []]}")
        if verdict.get("assets_digest") != verify.assets_digest(ws, verify.DELTA_ASSETS):
            problems.append("verdict_delta 之后增量测试又改过：要重新跑 run_validate(assets=\"verification/delta\")")
    if not (ws / "verification" / "coverage" / "summary.json").is_file():
        problems.append("verification/coverage/summary.json 不存在：要跑 run_coverage(assets=\"verification/delta\")")
    if not (ws / "verification" / "fault_baseline_B0.json").is_file():
        problems.append("verification/fault_baseline_B0.json 不存在：要跑 run_equivalence(mode=\"record\", label=\"B0\", assets=\"verification/delta\")")
    summary = (f"verdict_delta PASS: {verdict.get('passed')}/{verdict.get('total')}" if not problems else f"{len(problems)} problems: {problems[0][:120]}")
    return CheckResult("verdict_delta", not problems, summary, relay="" if not problems else "V3 的问题：\n" + "\n".join(problems))


def j4_report(ws: Path) -> CheckResult:
    """J4 has been done: judge_assets alone already holds after J1."""
    ok = (ws / "storage" / "stage_reports" / "J4.json").is_file()
    return CheckResult("j4_report", ok, "J4 report present" if ok else "J4 has not run",
                       relay="" if ok else "白盒补测（J4）还没有做过。")


def judge_full(ws: Path) -> CheckResult:
    """C1: the whole judge on the working tree; writes verification/verdict_judge.json."""
    import judge

    result = judge.run_judge(ws)
    ok = result.get("status") == "PASS"
    return CheckResult("judge_full", ok, f"run_judge {result.get('status')}: {result.get('summary') or result.get('error')}", detail=result,
                       relay="" if ok else "裁判没有通过：\n" + _clip(json.dumps(result.get("failures") or result.get("error"), ensure_ascii=False)))


def delta_full(ws: Path) -> CheckResult:
    """C1: all delta tests on the working tree; writes verification/verdict_delta.json."""
    import verify

    try:
        result = verify.run_validate(str(ws), assets=verify.DELTA_ASSETS)
    except verify.VerifyError as exc:
        return CheckResult("delta_full", False, f"run_validate: {exc}", relay=f"增量测试运行出错：{exc}")
    ok = result.get("status") == "PASS"
    return CheckResult("delta_full", ok, f"delta tests {result.get('status')}: {result.get('passed')}/{result.get('total')}", detail={"failed": result.get("failed")},
                       relay="" if ok else "增量测试没有通过：\n" + _clip(json.dumps(result.get("failures") or result.get("schema_errors"), ensure_ascii=False)))


def judge_unchanged(ws: Path) -> CheckResult:
    """The frozen judge still has the hashes of its manifest (v2 §4.7)."""
    import judge

    changes = judge.judge_changes(ws)
    bad = [p for kind in ("changed", "added", "removed") for p in (changes or {}).get(kind, [])]
    return CheckResult("judge_unchanged", not bad, "judge not frozen" if changes is None else
                       ("frozen judge unchanged" if not bad else f"frozen judge changed: {bad[:10]}"),
                       relay="" if not bad else f"冻结后的裁判文件被改动了：{bad[:20]}。裁判冻结之后谁都不能改 judge/。")


CHECKS: dict[str, Callable[[Path], CheckResult]] = {
    "gap_inventory": gap_inventory,
    "gap_package": gap_package,
    "gap_decisions": gap_decisions,
    "delta_assets": delta_assets,
    "delta_assets_domain": delta_assets_domain,
    "verdict_delta": verdict_delta,
    "j4_report": j4_report,
    "judge_full": judge_full,
    "delta_full": delta_full,
    "judge_assets": judge_assets,
    "judge_cases_pkg": judge_cases_pkg,
    "judge_unchanged": judge_unchanged,
    "reconcile_classified": reconcile_classified,
    "adapter_map": adapter_map,
    "mutant_classes": mutant_classes,
    "analysis_report": analysis_report,
    "check_provenance": check_provenance,
    "check_provenance_pkg": check_provenance_pkg,
    "global_middleware": global_middleware_determined,
    "room_turn": room_turn,
    "blueprint_keys": blueprint_keys,
    "gate_blueprint": _gate("blueprint", require_pass=False),
    "gate_code": _gate("code", require_pass=False),
    "gate_code_pkg": gate_code_pkg,
    "gate_template": _gate("template", require_pass=False),
    "gate_final": _gate("final", require_pass=True),
    "consistency_pass": consistency_pass,
    "validate_dry_run": _validate(dry_run=True),
    "validate_full": _validate(dry_run=False),
    "template_part1": _template_part(1),
    "template_part2": _template_part(2),
    "s5_report": s5_report,
}


# --- tools-unchanged guard ---------------------------------------------------------

HARNESS_PATHS = ("m2s-mcp-server", ".openharness")
IGNORED = {"m2s-mcp-server/perf-rules/ledger.json"}
IGNORED_PREFIXES = ("m2s-mcp-server/evidence/",)  # C2 appends a run's archive there


def _sha(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


class ToolGuard:
    """Detect (and undo) worker edits to the pipeline's own tools.

    The baseline is taken when the run starts, so a tool tree that was already
    dirty is not blamed on a worker. ~/.openharness/agents is hashed too; it is
    not under git, so changes there can only be reported.
    """

    def __init__(self, repo: Path = REPO, agents_dir: Path | None = None):
        self.repo = repo
        self.agents_dir = agents_dir or Path.home() / ".openharness" / "agents"
        self.baseline: dict[str, Any] = {}

    def _status(self) -> dict[str, tuple[str, str | None]]:
        proc = subprocess.run(["git", "-C", str(self.repo), "status", "--porcelain", "--untracked-files=all", "--", *HARNESS_PATHS],
                              capture_output=True, text=True, encoding="utf-8", errors="replace")
        out = {}
        for line in proc.stdout.splitlines():
            rel = line[3:].strip().strip('"').replace("\\", "/")
            if rel and rel not in IGNORED and not rel.startswith(IGNORED_PREFIXES) and "__pycache__" not in rel:
                out[rel] = (line[:2], _sha(self.repo / rel))
        return out

    def _agents(self) -> dict[str, str | None]:
        return {p.name: _sha(p) for p in sorted(self.agents_dir.glob("*.md"))} if self.agents_dir.is_dir() else {}

    def capture(self) -> dict[str, Any]:
        self.baseline = {"repo": {k: list(v) for k, v in self._status().items()}, "agents": self._agents()}
        return self.baseline

    def load(self, baseline: dict[str, Any]) -> None:
        self.baseline = baseline

    def violations(self) -> dict[str, list[str]]:
        """repo_new: clean at baseline, changed now (safe to revert);
        repo_changed: already dirty at baseline and changed again (reverting
        would also drop the person's own edits, so only report);
        agents: agent definitions whose content changed."""
        before = {k: tuple(v) for k, v in self.baseline.get("repo", {}).items()}
        now = self._status()
        changed = sorted(rel for rel, state in now.items() if before.get(rel) != state)
        agents_before = self.baseline.get("agents", {})
        agents = sorted(name for name, sha in self._agents().items() if agents_before.get(name) != sha)
        return {
            "repo_new": [rel for rel in changed if rel not in before],
            "repo_changed": [rel for rel in changed if rel in before],
            "agents": agents,
        }

    def revert(self, paths: list[str]) -> None:
        """Undo edits to files that were clean when the run started."""
        for rel in paths:
            path = self.repo / rel
            tracked = subprocess.run(["git", "-C", str(self.repo), "ls-files", "--error-unmatch", "--", rel],
                                     capture_output=True).returncode == 0
            if tracked:
                subprocess.run(["git", "-C", str(self.repo), "checkout", "--", rel], capture_output=True)
            elif path.is_file():
                path.unlink()
