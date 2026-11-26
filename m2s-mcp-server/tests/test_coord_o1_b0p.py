"""v3 O1/P (B0 -> O1/P -> B0P -> O2 -> C1): candidate expansion, accept/revert,
B0P, and C1's fine rollback."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from coord_fakes import make_dispatcher  # noqa: E402

from coord.checks import CheckResult  # noqa: E402
from coord.template import TaskSpec, load_template  # noqa: E402

CANDIDATE_TEMPLATE = """\
name: o1-test
tasks:
  - id: O1
    runner: dispatcher
    action: analyze_project
    scope: [perf/**, storage/stage_reports/O1.json]
  - id: O1C
    runner: dispatcher
    action: candidate_plan
    split: candidate
    expand_after: [O1]
    deps: [O1]
  - id: O1D
    runner: dispatcher
    action: barrier
    deps: [O1, "O1C:*"]
"""


def _git(ws: Path, *args: str) -> str:
    cp = subprocess.run(["git", "-C", str(ws), *args], capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert cp.returncode == 0, cp.stderr
    return cp.stdout.strip()


def _repo(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    if not (ws / ".git").is_dir():
        _git(ws, "init", "-q")
    _git(ws, "config", "user.email", "test@example.com")
    _git(ws, "config", "user.name", "test")
    return ws


def _commit(ws: Path, rel: str, text: str, message: str) -> str:
    path = ws / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    _git(ws, "add", "-A", rel)
    _git(ws, "commit", "-q", "-m", message)
    return _git(ws, "rev-parse", "HEAD")


def _dispatcher(tmp_path: Path, template_text: str = CANDIDATE_TEMPLATE, checks=None):
    path = tmp_path / "template.yaml"
    path.write_text(template_text, encoding="utf-8")
    from coord.template import load_template as load
    template = load(path=path)
    return make_dispatcher(tmp_path, {}, template=template, checks=checks)


def test_v2_split_has_the_b0_o1_b0p_o2_dag():
    template = load_template("v2-split")
    assert template.get("O1").role == "m2s-perf-triage"
    assert "output/**" not in template.get("O1").scope
    assert "verification/**" not in template.get("O1").scope
    assert template.get("O1C").split == "candidate"
    assert template.get("O1D").deps == ("O1", "O1C:*")
    assert template.get("V2P").deps == ("O1D",)
    assert template.get("V3P").deps == ("V2P",)
    assert template.get("B0P").deps == ("V3P",)
    assert template.get("O2").role == "m2s-perf-optimizer"
    assert template.get("O2").deps == ("B0P",)
    assert template.get("C1").deps == ("O2",)
    # --stop-after O2 must keep the candidate family, hence its dynamic children.
    assert "O1C" in template.upstream("O2")


def test_route_target_ignores_dynamic_candidate_tasks(tmp_path):
    d = _dispatcher(tmp_path)
    d.setup()
    assert d._route_target("code", "O1C-C1-owner") is None


def test_candidate_expansion_creates_owner_measure_accept_chains(tmp_path):
    d = _dispatcher(tmp_path)
    d.setup()
    (d.ws / "perf").mkdir(parents=True, exist_ok=True)
    (d.ws / "perf" / "candidates.json").write_text(json.dumps({"candidates": [
        {"id": "C1", "owner": "I1", "target": {"kind": "code"}, "benchmark": "b1"},
        {"id": "C2", "owner": "A1", "target": {"kind": "blueprint"}, "benchmark": "b2"},
    ]}), encoding="utf-8")
    template = d.template
    d._expand_candidates(template.get("O1C"))
    tasks = d.board.tasks()
    owner1, owner2 = tasks["O1C-C1-owner"]["spec"], tasks["O1C-C2-owner"]["spec"]
    measure1, accept1 = tasks["O1C-C1-measure"]["spec"], tasks["O1C-C1-accept"]["spec"]
    assert owner1["role"] == "m2s-code-developer" and owner1["mode"] == "candidate"
    assert owner2["role"] == "m2s-architect"
    assert measure1["role"] == "m2s-perf-triage" and measure1["mode"] == "O1M"
    assert accept1["runner"] == "dispatcher" and accept1["action"] == "perf_accept"
    assert owner1["deps"] == ["O1"]
    assert measure1["deps"] == ["O1C-C1-owner"]
    assert accept1["deps"] == ["O1C-C1-measure"]
    assert owner2["deps"] == ["O1C-C1-accept"]
    assert "output/**" not in owner2["scope"]  # A1 never writes code
    assert "verification/**" not in owner1["scope"]  # I1 never writes tests
    show = d.board.br.run("show", tasks["O1D"]["issue"], "--json").json()
    row = show[0] if isinstance(show, list) else show
    assert "O1C-C1-owner" in {x["title"] for x in row.get("dependencies") or []}


def test_perf_accept_keeps_a_kept_candidate_and_reverts_a_rejected_one(tmp_path):
    d = _dispatcher(tmp_path)
    ws = _repo(tmp_path)
    _commit(ws, "output/code.txt", "base", "B0")
    _git(ws, "tag", "B0")
    kept = _commit(ws, "output/code.txt", "kept", "perf(cand-C1): parallelize")
    (ws / "storage" / "stage_reports").mkdir(parents=True)
    (ws / "storage" / "stage_reports" / "O1C-C1-owner.json").write_text(
        json.dumps({"commit": kept}), encoding="utf-8")
    (ws / "perf" / "verdicts").mkdir(parents=True)
    (ws / "perf" / "verdicts" / "C1.json").write_text(json.dumps({"candidate": "C1", "verdict": "kept"}), encoding="utf-8")
    result = d._act_perf_accept(TaskSpec(id="O1C-C1-accept", runner="dispatcher", action="perf_accept",
                                        extra={"candidate_id": "C1", "candidate_owner": "I1"}))
    assert result.ok and (ws / "output" / "code.txt").read_text(encoding="utf-8") == "kept"

    rejected = _commit(ws, "output/code.txt", "rejected", "perf(cand-C2): bad")
    (ws / "storage" / "stage_reports" / "O1C-C2-owner.json").write_text(
        json.dumps({"commit": rejected}), encoding="utf-8")
    (ws / "perf" / "verdicts" / "C2.json").write_text(
        json.dumps({"candidate": "C2", "verdict": "revert"}), encoding="utf-8")
    result = d._act_perf_accept(TaskSpec(id="O1C-C2-accept", runner="dispatcher", action="perf_accept",
                                        extra={"candidate_id": "C2", "candidate_owner": "I1"}))
    assert result.ok and (ws / "output" / "code.txt").read_text(encoding="utf-8") == "kept"
    assert "Revert" in _git(ws, "log", "-1", "--format=%s")


def test_b0p_without_candidates_is_the_b0_commit(tmp_path):
    d = _dispatcher(tmp_path)
    ws = _repo(tmp_path)
    _commit(ws, "output/code.txt", "base", "B0")
    _git(ws, "tag", "B0")
    result = d._act_tag_b0p(TaskSpec(id="B0P", runner="dispatcher", action="tag_b0p"))
    assert result.ok
    assert _git(ws, "rev-parse", "B0P") == _git(ws, "rev-parse", "B0")


def test_s5_report_counts_o2_from_b0p(tmp_path):
    from coord import checks

    ws = _repo(tmp_path)
    base = _commit(ws, "output/code.txt", "base", "B0")
    _git(ws, "tag", "B0")
    _git(ws, "tag", "B0P", base)
    sha = _commit(ws, "output/code.txt", "o2", "perf(REQ-1): o2 [kept]")
    (ws / "storage" / "stage_reports").mkdir(parents=True)
    (ws / "storage" / "stage_reports" / "O2.json").write_text(json.dumps({"status": "success"}), encoding="utf-8")
    (ws / "output" / "perf_report.json").write_text(json.dumps({"changes": [
        {"id": "C1", "commit": sha, "verdict": "kept"},
    ]}), encoding="utf-8")
    result = checks.s5_report(ws)
    assert result.ok and "since B0P" in result.summary


def test_candidate_scope_rejects_a_commit_outside_the_owner_scope(tmp_path):
    from coord import checks

    ws = _repo(tmp_path)
    _commit(ws, "output/code.txt", "base", "B0")
    bad = _commit(ws, "verification/delta/evil.json", "{}", "perf(cand-C1): touches tests")
    (ws / "storage" / "stage_reports").mkdir(parents=True)
    report = ws / "storage" / "stage_reports" / "O1C-C1-owner.json"
    report.write_text(json.dumps({"commit": bad}), encoding="utf-8")
    spec = {"id": "O1C-C1-owner", "candidate_id": "C1",
            "scope": ["output/**", "storage/stage_reports/O1C-C1-owner.json"]}
    assert not checks.candidate_scope(ws, spec).ok

    good = _commit(ws, "output/code.txt", "ok", "perf(cand-C1): code only")
    report.write_text(json.dumps({"commit": good}), encoding="utf-8")
    assert checks.candidate_scope(ws, spec).ok


def test_b0p_with_an_accepted_candidate_contains_its_commit(tmp_path):
    d = _dispatcher(tmp_path)
    ws = _repo(tmp_path)
    base = _commit(ws, "output/code.txt", "base", "B0")
    _git(ws, "tag", "B0")
    cand = _commit(ws, "output/code.txt", "candidate", "perf(cand-C1): accepted")
    (ws / "perf").mkdir(parents=True, exist_ok=True)
    (ws / "perf" / "candidate_results.json").write_text(json.dumps({"candidates": [
        {"id": "C1", "owner": "I1", "commit": cand, "verdict": "kept", "accepted": True},
    ]}), encoding="utf-8")
    for rel, body in (("verification/verdict_judge.json", {"status": "PASS"}),
                      ("verification/verdict_delta.json", {"status": "PASS"}),
                      ("verification/fault_baseline_B0P.json", {})):
        path = ws / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(body), encoding="utf-8")
    d.checks = {name: (lambda ws_path, _n=name: CheckResult(_n, True, "ok"))
                for name in ("gate_final", "gate_blueprint", "gate_template", "judge_unchanged")}
    result = d._act_tag_b0p(TaskSpec(id="B0P", runner="dispatcher", action="tag_b0p"))
    assert result.ok, result.summary
    assert _git(ws, "rev-parse", "B0P") != _git(ws, "rev-parse", "B0")
    _git(ws, "merge-base", "--is-ancestor", cand, "B0P")


def test_c1_fine_rollback_keeps_the_earlier_o2_commits(tmp_path):
    d = _dispatcher(tmp_path)
    ws = _repo(tmp_path)
    base = _commit(ws, "output/code.txt", "base", "B0")
    _git(ws, "tag", "B0")
    _git(ws, "tag", "B0P", base)
    c1 = _commit(ws, "output/code.txt", "one", "perf(REQ-1): one [kept]")
    c2 = _commit(ws, "output/code.txt", "two", "perf(REQ-2): two [kept]")
    c3 = _commit(ws, "output/code.txt", "bad", "perf(REQ-3): bad [kept]")
    (ws / "storage" / "stage_reports").mkdir(parents=True)
    (ws / "storage" / "stage_reports" / "O2.json").write_text(
        json.dumps({"stage_id": "O2", "status": "success", "kept": 3, "blocked": []}), encoding="utf-8")
    (ws / "output" / "perf_report.json").write_text(json.dumps({"changes": [
        {"id": "C1", "commit": c1, "verdict": "kept"},
        {"id": "C2", "commit": c2, "verdict": "kept"},
        {"id": "C3", "commit": c3, "verdict": "kept"},
    ]}), encoding="utf-8")

    def final(ws_path: Path) -> CheckResult:
        text = (ws_path / "output" / "code.txt").read_text(encoding="utf-8")
        return CheckResult("gate_final", text != "bad", "code is %r" % text)

    d.checks = {"gate_final": final, "judge_full": lambda ws_path: CheckResult("judge_full", True, "ok"),
                "delta_full": lambda ws_path: CheckResult("delta_full", True, "ok")}
    d.setup()
    result = d._act_final_v2(TaskSpec(id="C1", runner="dispatcher", action="final_v2"))
    assert result.ok, result.summary
    assert (ws / "output" / "code.txt").read_text(encoding="utf-8") == "two"
    perf = json.loads((ws / "output" / "perf_report.json").read_text(encoding="utf-8"))
    verdicts = {c["id"]: c["verdict"] for c in perf["changes"]}
    assert verdicts == {"C1": "kept", "C2": "kept", "C3": "reverted"}
    o2 = json.loads((ws / "storage" / "stage_reports" / "O2.json").read_text(encoding="utf-8"))
    assert o2["c1_rollback"]["mode"] == "fine" and o2["c1_rollback"]["reverted"] == ["C3"]
    assert o2["kept"] == 2
    report = (ws / "output" / "final_report.md").read_text(encoding="utf-8")
    assert "O1 架构级改动" in report and "O2 局部优化" in report
