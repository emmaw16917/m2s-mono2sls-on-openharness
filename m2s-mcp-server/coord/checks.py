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
    report = _read_json(ws / "output" / "perf_report.json")
    if not isinstance(report, dict) or not (ws / "storage" / "stage_reports" / "S5.json").is_file():
        return CheckResult("s5_report", False, "perf_report.json or S5.json missing",
                           relay="output/perf_report.json 或 storage/stage_reports/S5.json 不存在。")
    kept = [c.get("id") for c in report.get("changes") or [] if isinstance(c, dict)
            and c.get("verdict") in ("kept", "kept_inconclusive")]
    proc = subprocess.run(["git", "-C", str(ws), "rev-list", "--count", "v0..HEAD"], capture_output=True, text=True)
    commits = int(proc.stdout.strip() or 0) if proc.returncode == 0 else 0
    ok = commits >= len(kept)
    return CheckResult("s5_report", ok, f"{len(kept)} kept changes, {commits} commits since v0",
                       relay="" if ok else f"perf_report.json 里有 {len(kept)} 个保留的改动（{kept}），但 v0 之后只有 {commits} 个提交；每个保留的改动都要单独提交。")


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


def reconcile_classified(ws: Path) -> CheckResult:
    """J3: every difference of judge/reconcile.json is classified (v2 §4.5)."""
    import judge

    result = judge.check_reconcile_classified(ws)
    ok = result["status"] == "PASS"
    return CheckResult("reconcile_classified", ok,
                       f"{result['differences']} differences" + (f", classes {result['classes']}" if ok else
                                                                  f", {len(result['errors'])} problems"),
                       detail=result, relay="" if ok else "judge/reconcile_classes.json 的问题：\n" + _clip("\n".join(result["errors"])))


def judge_unchanged(ws: Path) -> CheckResult:
    """The frozen judge still has the hashes of its manifest (v2 §4.7)."""
    import judge

    changes = judge.judge_changes(ws)
    bad = [p for kind in ("changed", "added", "removed") for p in (changes or {}).get(kind, [])]
    return CheckResult("judge_unchanged", not bad, "judge not frozen" if changes is None else
                       ("frozen judge unchanged" if not bad else f"frozen judge changed: {bad[:10]}"),
                       relay="" if not bad else f"冻结后的裁判文件被改动了：{bad[:20]}。裁判冻结之后谁都不能改 judge/。")


CHECKS: dict[str, Callable[[Path], CheckResult]] = {
    "judge_assets": judge_assets,
    "judge_unchanged": judge_unchanged,
    "reconcile_classified": reconcile_classified,
    "analysis_report": analysis_report,
    "check_provenance": check_provenance,
    "blueprint_keys": blueprint_keys,
    "gate_blueprint": _gate("blueprint", require_pass=False),
    "gate_code": _gate("code", require_pass=False),
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
            if rel and rel not in IGNORED and "__pycache__" not in rel:
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
