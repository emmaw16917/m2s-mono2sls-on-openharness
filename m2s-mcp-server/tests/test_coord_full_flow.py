"""The whole v2 template with scripted fake workers (PIPELINE_V2_PLAN.md N5.1), a judge copied
from an earlier run, C1 and C2 (N5.4), and the mechanical route for unawaited writes (V2P-22)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from coord_fakes import make_dispatcher, ok_action, ok_report  # noqa: E402

import judge  # noqa: E402
from coord import closing  # noqa: E402
from coord.checks import CheckResult  # noqa: E402
from coord.dispatcher import EXIT_DONE, EXIT_HUMAN  # noqa: E402
from coord.template import load_template  # noqa: E402

slow = pytest.mark.skipif(not os.environ.get("M2S_RUN_SLOW"), reason="slow; set M2S_RUN_SLOW=1")
V2 = load_template("v2")
ACTIONS = ("run_monolith", "reconcile_spec", "run_mutants", "freeze_judge", "gap_scan", "check_adapter", "run_judge",
           "tag_b0", "final_v2", "archive_run")


def fake_j2(ws: Path):
    """As the real run_monolith does, it records the inputs it ran; otherwise J4 sees new cases for ever."""
    def run(spec) -> CheckResult:
        judge._write(ws / "judge" / "oracle_expected.json", {"meta": {"inputs_sha": judge.inputs_sha(ws)}, "cases": {}})
        return CheckResult("run_monolith", True, "fake run_monolith")
    return run


def full_dispatcher(tmp_path: Path, script: dict, *, keep: tuple[str, ...] = (), **overrides):
    actions = {name: ok_action(name) for name in ACTIONS if name not in keep}
    if "run_monolith" not in keep:
        actions["run_monolith"] = fake_j2(tmp_path / "ws")
    actions.update(overrides)
    return make_dispatcher(tmp_path, script, template=V2, actions=actions)


def _spawns(d, task: str) -> int:
    return d.events.count("spawn", task)


def _prompt(d, task: str, attempt: int = 1) -> str:
    return (d.coord / "run" / f"{task}-a{attempt}" / "prompt.md").read_text(encoding="utf-8")


def test_the_template_follows_appendix_a():
    assert [t.id for t in V2.tasks] == ["F1", "F2", "G1", "G2", "J1", "J2", "RC", "J3", "J4", "J5", "J5C", "J6", "A1", "D1",
                                        "I1", "I2a", "I2b", "I2c", "I3", "V1", "V2", "V3", "B0", "O", "C1", "C2"]
    # the two lines meet before I1
    upstream = V2.upstream("I1")
    assert "J6" in upstream and "G2" in upstream and "D1" in upstream
    assert "J6" not in V2.upstream("A1") and "J1" in V2.upstream("A1")
    assert V2.get("D1").deny_read == ("monolith", "output/**")
    for task in ("A1", "I1", "I2a", "I2b", "I2c", "I3"):
        assert "verification/**" in V2.get(task).deny_read, task  # the delta tests are closed to the implementation line
    assert {V2.get(t).start for t in ("F2", "J1", "J3", "J4", "J5C")} == {"recheck"}


@slow
def test_fake_workers_run_the_whole_template(tmp_path: Path):
    d = full_dispatcher(tmp_path, {})

    assert d.run() == EXIT_DONE

    assert [t.id for t in V2.tasks if d.board.status(t.id) != "closed"] == []
    order = [e["task"] for e in d.events.all() if e["type"] in ("spawn", "action_start")]
    for early, late in [("J1", "A1"), ("G2", "A1"), ("A1", "D1"), ("J2", "D1"), ("J6", "I1"), ("D1", "I1"), ("I1", "I2a"), ("I2c", "I3"), ("I3", "V1"), ("V1", "V2"),
                        ("V2", "V3"), ("V3", "B0"), ("B0", "O"), ("O", "C1"), ("C1", "C2")]:
        assert order.index(early) < order.index(late), (early, late)
    assert "适配映射" in _prompt(d, "A1") and "缺口决定" in _prompt(d, "A1") and "迁移带来的对外变化" in _prompt(d, "A1")
    assert "run_judge_self_test" in _prompt(d, "I1")
    assert "S3a" in _prompt(d, "I2a") and "MERGE" in _prompt(d, "I2c") and "MONOLITH" in _prompt(d, "I2c")
    assert "MODE: D1" in _prompt(d, "D1") and "MODE: V3" in _prompt(d, "V3") and "B0" in _prompt(d, "O")


@slow
def test_a_loop_over_actions_stops_and_asks_a_person(tmp_path: Path, monkeypatch):
    """J4 sends J2 again while the inputs J2 ran differ. Workers have a backstop against a rule
    loop; a loop over the dispatcher's own actions spawns nothing and needs its own."""
    from coord import dispatcher

    monkeypatch.setattr(dispatcher, "MAX_ACTION_RUNS", 3)
    d = full_dispatcher(tmp_path, {}, run_monolith=ok_action("run_monolith"))  # never records its inputs

    assert d.run() == EXIT_HUMAN

    assert d.events.count("action_start", "J2") == 3 and d.board.status("J2") == "needs_human"
    assert any("可能在循环" in e["reason"] for e in d.events.of_task("J2", "escalate"))


@slow
def test_a_settled_judge_costs_no_worker_and_is_not_run_again(tmp_path: Path, monkeypatch):
    """A workspace that starts from a frozen judge: no judge line worker, and J2, RC and J5 leave
    the judge alone (running the monolith again would change frozen files)."""
    monkeypatch.setattr(judge, "judge_changes", lambda ws: {"changed": [], "added": [], "removed": []})
    monkeypatch.setattr(judge, "run_monolith", lambda ws: pytest.fail("the frozen judge's monolith ran again"))
    monkeypatch.setattr(judge, "reconcile", lambda ws: pytest.fail("the frozen judge was reconciled again"))
    d = full_dispatcher(tmp_path, {}, keep=("run_monolith", "reconcile_spec", "run_mutants"))
    reports = d.ws / "storage" / "stage_reports"
    reports.mkdir(parents=True)
    for task in ("F2", "J1", "J3", "J4", "J5C"):
        (reports / f"{task}.json").write_text(json.dumps({"ok": True}), encoding="utf-8")
    jd = d.ws / "judge"
    jd.mkdir()
    (jd / "judge_manifest.json").write_text("{}", encoding="utf-8")
    # what J4 found was settled before the freeze: it must not go back to F2
    (jd / "spec_issues.json").write_text(json.dumps([{"id": "J4-1", "endpoint": "POST /x", "kind": "missing", "rule": "r", "cases": []}]), encoding="utf-8")

    assert d.run() == EXIT_DONE

    assert all(_spawns(d, t) == 0 for t in ("F2", "J1", "J3", "J4", "J5C"))
    assert d.events.of_task("J2", "checks")[-1]["results"][0]["summary"].startswith("the judge is frozen")
    assert _spawns(d, "A1") == 1


def test_f1_keeps_a_fact_base(tmp_path: Path, monkeypatch):
    import wrap_m2s

    monkeypatch.setattr(wrap_m2s, "analyze", lambda *a: pytest.fail("analyzed an annotated fact base again"))
    d = make_dispatcher(tmp_path, {}, template=V2)
    (d.ws / "storage").mkdir(parents=True)
    (d.ws / "storage" / "analysis_report.json").write_text(json.dumps({"spec_meta": {"monolith_root": "x"}}), encoding="utf-8")
    assert d._act_analyze(V2.get("F1")).summary.endswith("kept")


@slow
def test_unawaited_writes_go_to_the_code(tmp_path: Path):
    results = [{"status": "FAIL", "failures": [{"case": "c1", "steps": {}, "unawaited": {
        "calls": [{"op": "SendMessage", "queue": "Q", "function": "orders-create"}], "why": "x"}}]}, {"status": "PASS"}]
    calls = []

    def run_judge(spec) -> CheckResult:
        detail = results[min(len(calls), 1)]
        calls.append(1)
        return CheckResult("run_judge", detail["status"] == "PASS", detail["status"], detail=detail)

    d = full_dispatcher(tmp_path, {}, run_judge=run_judge)

    assert d.run() == EXIT_DONE

    assert _spawns(d, "I1") == 2 and "SendMessage" in _prompt(d, "I1", 2) and "orders-create" in _prompt(d, "I1", 2)
    assert d.events.count("handler_spawn", "V2") == 0 and len(calls) == 2


# --- B0: what a decision says the judge verifies (V2P-23) ----------------------------


def _b0_ws(d, verdicts: dict[str, str], named: list[str]) -> None:
    v = d.ws / "verification"
    v.mkdir(exist_ok=True)
    (v / "verdict_judge.json").write_text(json.dumps({"status": "PASS", "cases": {c: {"verdict": x} for c, x in verdicts.items()}}), encoding="utf-8")
    (v / "verdict_delta.json").write_text(json.dumps({"status": "PASS"}), encoding="utf-8")
    (d.ws / "storage").mkdir(exist_ok=True)
    (d.ws / "storage" / "gap_inventory.json").write_text(json.dumps({"gaps": [
        {"id": "GAP-1", "decision": {"test": "judge", "judge_cases": named}}]}), encoding="utf-8")
    for name in ("output", "judge"):
        (d.ws / name).mkdir(exist_ok=True)
        (d.ws / name / "x.txt").write_text(name, encoding="utf-8")


@pytest.mark.parametrize("verdicts,named,ok", [
    ({"c1": "pass"}, ["c1"], True),
    ({"c1": "migration-change", "c2": "dropped"}, ["c1", "c2"], True),
    ({"c1": "dropped"}, ["c1"], False),  # a case that did not run verifies nothing
    ({"c1": "pass"}, ["c1", "c9"], False),  # not run at all
])
def test_b0_checks_the_cases_decisions_name(tmp_path: Path, monkeypatch, verdicts, named, ok):
    monkeypatch.setattr(judge, "judge_changes", lambda ws: {"changed": [], "added": [], "removed": []})
    d = make_dispatcher(tmp_path, {}, template=V2)
    _b0_ws(d, verdicts, named)
    r = d._act_tag_b0(V2.get("B0"))
    assert r.ok is ok, r.summary
    assert ok or "the judge does not verify GAP-1" in r.summary


# --- C1 ---------------------------------------------------------------------------


def _git(ws: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(ws), "-c", "user.name=t", "-c", "user.email=t@t", *args], capture_output=True, text=True).stdout.strip()


def _delivered_ws(d) -> None:
    d.setup()
    for name, text in (("output/code.js", "b0"), ("verification/verdict_judge.json", "{}"), ("storage/x.json", "{}")):
        path = d.ws / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    _git(d.ws, "init", "-q")
    _git(d.ws, "add", "-A", "output", "verification", "storage")
    _git(d.ws, "commit", "-q", "-m", "b0")
    _git(d.ws, "tag", "B0")
    (d.ws / "output" / "code.js").write_text("optimized", encoding="utf-8")
    _git(d.ws, "add", "-A", "output")
    _git(d.ws, "commit", "-q", "-m", "perf")


def _final_checks(d, outcomes: list[bool]) -> None:
    runs = []

    def make(name: str):
        def check(ws):
            ok = outcomes[min(len(runs) // 3, len(outcomes) - 1)]
            runs.append(name)
            return CheckResult(name, ok, f"{name} {'ok' if ok else 'failed'}")
        return check

    for name in ("gate_final", "judge_full", "delta_full"):
        d.checks[name] = make(name)


def test_c1_writes_the_report_on_the_code_it_checked(tmp_path: Path):
    d = make_dispatcher(tmp_path, {}, template=V2)
    _delivered_ws(d)
    _final_checks(d, [True])
    r = d._act_final_v2(V2.get("C1"))
    assert r.ok, r.summary
    text = (d.ws / "output" / "final_report.md").read_text(encoding="utf-8")
    assert "judge_full：通过" in text and "每一层在什么环境下检验" in text and "裁定" in text
    assert (d.ws / "output" / "code.js").read_text(encoding="utf-8") == "optimized"
    assert "output/final_report.md" in _git(d.ws, "show", "--name-only", "HEAD")


def test_c1_goes_back_to_b0_once_when_the_optimizer_broke_it(tmp_path: Path):
    d = make_dispatcher(tmp_path, {}, template=V2)
    _delivered_ws(d)
    _final_checks(d, [False, True])
    r = d._act_final_v2(V2.get("C1"))
    assert r.ok and "reverted" in r.summary
    assert (d.ws / "output" / "code.js").read_text(encoding="utf-8") == "b0"
    assert d.events.count("o_reverted", "C1") == 1
    assert "退回 B0" in (d.ws / "output" / "final_report.md").read_text(encoding="utf-8")


def test_c1_goes_to_a_person_when_b0_fails_too(tmp_path: Path):
    d = make_dispatcher(tmp_path, {}, template=V2)
    _delivered_ws(d)
    _final_checks(d, [False])
    r = d._act_final_v2(V2.get("C1"))
    assert not r.ok and r.detail.get("escalate") and not (d.ws / "output" / "final_report.md").exists()


def test_c1_stops_for_a_person_on_blocked_changes(tmp_path: Path):
    d = make_dispatcher(tmp_path, {}, template=V2)
    (d.ws / "output").mkdir()
    (d.ws / "output" / "perf_report.json").write_text(json.dumps({"changes": [{"id": "REQ-1", "verdict": "pending_spec"}]}), encoding="utf-8")
    r = d._act_final_v2(V2.get("C1"))
    assert not r.ok and r.detail.get("escalate") and "REQ-1" in r.summary


def test_final_report_has_what_the_acceptance_asks_for(tmp_path: Path):
    ws = tmp_path / "ws"
    (ws / "judge").mkdir(parents=True)
    (ws / "output").mkdir()
    (ws / "judge" / "judge_manifest.json").write_text(json.dumps({
        "version": 2, "frozen_at": "t", "cases": 74, "layers": {"P": 74, "R": 0}, "fault_scenarios": 24, "required_responses": "63/66",
        "business_rules": "114/126", "coverage": {"lines": "584/611", "percent": 95.6},
        "mutation_score": {"mutants": 93, "killed": 86, "equivalent": 7, "score": 1.0, "reverse": {"serial": "survived"}}}), encoding="utf-8")
    (ws / "output" / "perf_report.json").write_text(json.dumps({"changes": [
        {"id": "REQ-1", "verdict": "kept", "summary": "parallel entries"}, {"id": "REQ-2", "verdict": "revert"}]}), encoding="utf-8")
    rulings = [{"id": "R-1", "decider": "m2s-architect", "status": "decided", "category": "migration-change", "question": "q", "conclusion": "c"},
               {"id": "R-2", "decider": "m2s-architect", "status": "confirmed", "category": "contract", "question": "q", "conclusion": "c"},
               {"id": "R-3", "decider": "human", "status": "decided", "category": "contract", "question": "q", "conclusion": "c"}]
    text = closing.final_report(ws, results=[CheckResult("gate_final", True, "ok")], rulings=rulings, open_items=["- 还有一件事"], reverted=False)
    assert "裁判 v2" in text and "584/611" in text and "检出率 1.0" in text and "serial" in text
    assert "保留 REQ-1" in text and "parallel entries" in text and "REQ-2" not in text.split("## 裁定")[0].split("保留 REQ-1")[1]
    assert "共 3 条，其中人工裁定或确认 2 条" in text and "还有一件事" in text and "没有部署到 AWS" in text


# --- C2 ---------------------------------------------------------------------------


def _finished_ws(tmp_path: Path) -> tuple[Path, Path]:
    ws, mono = tmp_path / "bookstore-full", tmp_path / "bookstore"
    mono.mkdir()
    for rel, text in {"storage/rulings.json": "{}", "storage/gap_inventory.json": "{}", "storage/DECISIONS.md": "# d",
                      "storage/stage_reports/A1.json": json.dumps({"status": "success", "summary": "designed"}),
                      "judge/judge_manifest.json": "{}", "output/final_report.md": "# r", "verification/verdict_judge.json": json.dumps({"status": "PASS"}),
                      "coord/events.jsonl": "{}\n", "coord/report.md": "# r", "coord/run/A1-a1/stream.jsonl": "{}\n", "coord/run/A1-a1/prompt.md": "p",
                      "coord/run/A1-a1/settings.json": '{"secret": "x"}', "coord/run/A1-a1/mcp.json": "{}", "coord/shadow.git/HEAD": "ref"}.items():
        path = ws / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return ws, mono


def test_archive_holds_the_evidence_and_leaves_out_the_rest(tmp_path: Path):
    ws, mono = _finished_ws(tmp_path)
    r = closing.archive_run(ws, mono, tmp_path / "evidence")
    assert r["status"] == "PASS"
    out = tmp_path / "evidence" / "bookstore" / "bookstore-full"
    assert Path(r["path"]) == out
    for rel in ("storage/rulings.json", "judge/judge_manifest.json", "output/final_report.md", "coord/events.jsonl",
                "coord/run/A1-a1/stream.jsonl", "verdict_summary.json", "stage_reports_index.json", "ARCHIVE.json"):
        assert (out / rel).is_file(), rel
    for rel in ("coord/run/A1-a1/settings.json", "coord/run/A1-a1/mcp.json", "coord/shadow.git"):
        assert not (out / rel).exists(), rel
    index = json.loads((out / "stage_reports_index.json").read_text(encoding="utf-8"))
    assert index == [{"task": "A1", "file": "storage/stage_reports/A1.json", "status": "success", "summary": "designed"}]
    assert "storage/adapter_map.json" in json.loads((out / "ARCHIVE.json").read_text(encoding="utf-8"))["missing"]


def test_archive_only_appends(tmp_path: Path):
    ws, mono = _finished_ws(tmp_path)
    first = closing.archive_run(ws, mono, tmp_path / "evidence")
    (Path(first["path"]) / "marker").write_text("first", encoding="utf-8")
    second = closing.archive_run(ws, mono, tmp_path / "evidence")
    assert second["path"] != first["path"] and second["path"].endswith("bookstore-full.2")
    assert (Path(first["path"]) / "marker").read_text(encoding="utf-8") == "first"


def test_archive_refuses_what_looks_like_a_secret(tmp_path: Path):
    ws, mono = _finished_ws(tmp_path)
    (ws / "coord" / "run" / "A1-a1" / "stream.jsonl").write_text('{"text": "key sk-abcdefghijklmnopqrstuvwx"}', encoding="utf-8")
    r = closing.archive_run(ws, mono, tmp_path / "evidence")
    assert r["status"] == "FAIL" and r["files"] == ["coord/run/A1-a1/stream.jsonl"]
    assert not list((tmp_path / "evidence" / "bookstore").iterdir())


@slow
def test_c2_runs_after_c1_in_the_template(tmp_path: Path, monkeypatch):
    seen = []
    monkeypatch.setattr(closing, "archive_run", lambda ws, mono: seen.append(ws.name) or {"status": "PASS", "path": "p", "files": [], "missing": []})
    d = full_dispatcher(tmp_path, {}, keep=("archive_run",))

    assert d.run() == EXIT_DONE

    assert seen == ["ws"] and d.events.last("C2", "archived")["path"] == "p"


@slow
def test_c2_failure_goes_to_a_person(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(closing, "archive_run", lambda ws, mono: {"status": "FAIL", "error": "secret", "files": ["x"]})
    d = full_dispatcher(tmp_path, {}, keep=("archive_run",))

    assert d.run() == EXIT_HUMAN

    assert d.board.status("C2") == "needs_human"
