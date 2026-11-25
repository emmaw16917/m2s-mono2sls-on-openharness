"""J1 cannot read the monolith (PIPELINE_V2_PLAN.md §2, V2P-06, N1.4).

The worker settings the dispatcher writes for a task with deny_read:
[monolith] are evaluated by OpenHarness's own PermissionChecker -- the same
code a real `oh -p` worker runs (the end-to-end probe with a real worker is
test_coord_perm.py, behind M2S_RUN_LLM_TESTS).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

checker_mod = pytest.importorskip("openharness.permissions.checker")
settings_mod = pytest.importorskip("openharness.config.settings")

from coord_fakes import make_dispatcher  # noqa: E402

from coord.dispatcher import closed_to  # noqa: E402
from coord.prompts import worker_prompt  # noqa: E402
from coord.template import TaskSpec, load_template  # noqa: E402
from coord.workers import WorkerJob, worker_settings  # noqa: E402

J1 = TaskSpec(id="J1", role="m2s-verifier", mode="J1", deny_read=("monolith", "judge_outputs"),
              scope=("judge/**", "storage/stage_reports/J1.json"))


@pytest.fixture
def layout():
    # The checker matches path strings only; paths outside the system temp
    # directory, which workers may always write to.
    root = Path("D:/perm-probe/artifacts")
    return root / "M2S" / "runs" / "bookstore-j1", root / "mono_benchmark" / "bookstore"


def _checker(spec: TaskSpec, ws: Path, mono: Path):
    dirs, cmds = closed_to(spec, ws, mono)
    job = WorkerJob(spec.id, 1, spec.role, ws, ws / "coord", "ROLE", "do it", spec.scope, 100, 60.0,
                    deny_read=dirs, deny_commands=cmds)
    perm = settings_mod.PermissionSettings(**worker_settings(job, [])["permission"])
    return checker_mod.PermissionChecker(perm)


def _allowed(checker, tool: str, *, read_only: bool, path: Path | None = None, command: str | None = None) -> bool:
    return checker.evaluate(tool, is_read_only=read_only, file_path=str(path) if path else None, command=command).allowed


def test_j1_cannot_read_the_monolith(layout):
    ws, mono = layout
    c = _checker(J1, ws, mono)
    for tool in ("read_file", "grep", "glob"):
        assert not _allowed(c, tool, read_only=True, path=mono / "routes" / "cart.js")
        assert not _allowed(c, tool, read_only=True, path=mono)
        assert not _allowed(c, tool, read_only=True, path=mono.parent)  # a grep from mono_benchmark/ would reach it
    for cmd in (f"cat {mono / 'routes' / 'cart.js'}", f"cat {mono.as_posix()}/routes/cart.js",
                "grep -rn price ../../../mono_benchmark/bookstore", "ls ..\\..\\..\\mono_benchmark"):
        assert not _allowed(c, "bash", read_only=False, command=cmd), cmd
    # Nor what the monolith did: J1's predictions come from the fact base alone.
    for rel in ("judge/oracle_expected.json", "judge/masks.json", "judge/coverage_monolith/summary.json",
                "judge/reconcile.json"):
        assert not _allowed(c, "read_file", read_only=True, path=ws / rel), rel
    assert not _allowed(c, "bash", read_only=False, command="cat judge/oracle_expected.json")
    assert _allowed(c, "read_file", read_only=True, path=ws / "judge" / "cases" / "get-api-cart" / "c.json")
    # What J1 does need stays open.
    assert _allowed(c, "read_file", read_only=True, path=ws / "storage" / "analysis_report.json")
    assert _allowed(c, "write_file", read_only=False, path=ws / "judge" / "cases" / "get-api-cart" / "c.json")
    assert not _allowed(c, "write_file", read_only=False, path=ws / "storage" / "analysis_report.json")
    assert _allowed(c, "bash", read_only=False, command="python -c \"print(1)\"")


def test_other_tasks_still_read_the_monolith(layout):
    ws, mono = layout
    c = _checker(TaskSpec(id="J4", role="m2s-verifier", mode="J4", scope=("judge/**",)), ws, mono)
    assert _allowed(c, "read_file", read_only=True, path=mono / "routes" / "cart.js")
    assert _allowed(c, "bash", read_only=False, command=f"cat {mono / 'routes' / 'cart.js'}")


def test_the_parent_stays_open_when_it_holds_the_workspace(tmp_path: Path):
    """A monolith next to the workspace: only the monolith itself is closed."""
    ws = tmp_path / "proj" / "ws"
    mono = tmp_path / "proj" / "app"
    dirs, cmds = closed_to(TaskSpec(id="J1", role="m2s-verifier", deny_read=("monolith",)), ws, mono)
    assert dirs == (str(mono),) and cmds == ("*proj/app*", "*proj\\app*")
    ws_glob = TaskSpec(id="D1", role="m2s-verifier", deny_read=("output/lambdas/**",))
    assert closed_to(ws_glob, ws, mono)[0] == (str(ws / "output" / "lambdas"),)


def test_d1_cannot_read_the_code_or_the_monolith(layout):
    """N4.4's acceptance: delta tests are written from the decisions, with the implementation
    and the monolith's source closed; what the monolith answered stays open (V2-08)."""
    ws, mono = layout
    d1 = load_template("v2-gaps").get("D1")
    checker = _checker(d1, ws, mono)
    for path in (ws / "output" / "lambdas" / "orders" / "handler.js", ws / "output" / "template.yaml", mono / "routes" / "orders.js"):
        assert not _allowed(checker, "read_file", read_only=True, path=path), path
        assert not _allowed(checker, "write_file", read_only=False, path=path), path
    for tool in ("grep", "glob"):
        assert not _allowed(checker, tool, read_only=True, path=ws / "output" / "lambdas")
    for command in (f"cat {ws / 'output' / 'lambdas' / 'orders' / 'handler.js'}", "cat output/lambdas/orders/handler.js",
                    "type output\\lambdas\\orders\\handler.js", f"ls {mono}"):
        assert not _allowed(checker, "bash", read_only=False, command=command), command
    for path in (ws / "judge" / "oracle_expected.json", ws / "storage" / "gap_inventory.json", ws / "storage" / "blueprint.json"):
        assert _allowed(checker, "read_file", read_only=True, path=path), path
    assert _allowed(checker, "write_file", read_only=False, path=ws / "verification" / "delta" / "events" / "f" / "c.json")
    text = worker_prompt(d1, ws, mono, 1, [])
    assert "MODE: D1" in text and "verification/delta" in text and str(mono) not in text and "MONOLITH =" not in text


def test_gap_template_is_in_order():
    t = load_template("v2-gaps")
    assert [s.id for s in t.tasks] == ["G1", "G2", "A1", "D1", "I1", "I2", "V3"]
    assert t.get("I1").start == t.get("I2").start == "recheck" and t.get("V3").on_stale == "rerun"
    assert "gate_blueprint" in t.get("A1").check and "gap_decisions" in t.get("A1").check
    assert t.upstream("V3").index("D1") < t.upstream("V3").index("I1")


def test_j1_prompt_names_the_mode_and_not_the_monolith(layout):
    ws, mono = layout
    text = worker_prompt(J1, ws, mono, 1, [])
    assert "MODE: J1" in text and "check_judge_assets" in text
    assert str(mono) not in text and "MONOLITH =" not in text
    other = worker_prompt(TaskSpec(id="S1", role="m2s-architect"), ws, mono, 1, [])
    assert f"MONOLITH = {mono}" in other


def test_template_fields_round_trip(tmp_path: Path):
    path = tmp_path / "t.yaml"
    path.write_text("name: t\ntasks:\n  - id: J1\n    role: m2s-verifier\n    mode: J1\n    deny_read: [monolith]\n",
                    encoding="utf-8")
    spec = load_template("t", path).get("J1")
    assert (spec.mode, spec.deny_read) == ("J1", ("monolith",))
    assert TaskSpec.from_json(spec.to_json()) == spec


def test_the_frozen_judge_is_closed_to_writes(layout):
    ws, mono = layout
    job = WorkerJob("A1", 1, "m2s-architect", ws, ws / "coord", "ROLE", "do it", ("judge/**",), 100, 60.0,
                    deny_write=(str(ws / "judge"),))
    c = checker_mod.PermissionChecker(settings_mod.PermissionSettings(**worker_settings(job, [])["permission"]))
    assert not _allowed(c, "write_file", read_only=False, path=ws / "judge" / "cases" / "x" / "c.json")
    assert _allowed(c, "read_file", read_only=True, path=ws / "judge" / "cases" / "x" / "c.json")


def test_workers_use_this_repositorys_server_and_role_definitions():
    from coord import workers
    assert workers.this_repo_server(["D:\Emma\MyLab\Mono2SLs\m2s-mcp-server\server.py"]) == [str(workers.SERVER_PY)]
    assert workers.this_repo_server(["-u", "other.py"]) == ["-u", "other.py"]
    assert workers.AGENTS_DIR == workers.REPO / "m2s-mcp-server" / "agents"
    assert "## J1" in workers.role_prompt("m2s-verifier") and "MODE: ruling" in workers.role_prompt("m2s-architect")


def test_verifier_role_split_keeps_v1_out_of_the_v2_prompt():
    """2026-11-25: m2s-verifier carries only the v2 modes; the V-model content
    (V1/V2/VR/VS + its test plan) lives in m2s-verifier-v1, and the v1
    templates use that role."""
    from coord import workers
    from coord.template import load_template

    v2 = workers.role_prompt("m2s-verifier")
    v1 = workers.role_prompt("m2s-verifier-v1")
    for heading in ("## V1", "## V2", "## VR", "## VS"):
        assert heading not in v2, heading
        assert heading in v1, heading
    for heading in ("## J1", "## J3", "## J4", "## J5", "## D1", "## V3"):
        assert heading in v2, heading
        assert heading not in v1, heading
    # D1 still has the asset format and expectations rules it references.
    assert "增量测试资产格式" in v2 and "expected_from" in v2 and "怎么写预期" in v2

    # What the dispatcher actually writes into a worker's settings.json.
    fake_ws = Path("C:/nonexistent-ws")
    for task_id, role in (("J1", "m2s-verifier"), ("D1", "m2s-verifier"), ("V3", "m2s-verifier"),
                          ("V1", "m2s-verifier-v1")):
        job = WorkerJob(task_id, 1, role, fake_ws, fake_ws / "coord", workers.role_prompt(role),
                        "do it", (), 10, 60.0, mode=task_id)
        system_prompt = worker_settings(job, [])["system_prompt"]
        if role == "m2s-verifier":
            for heading in ("## V1", "## V2", "## VR", "## VS"):
                assert heading not in system_prompt, (task_id, heading)
        else:
            for heading in ("## V1", "## V2", "## VR", "## VS"):
                assert heading in system_prompt, (task_id, heading)

    v1_template = load_template("v1")
    v1_split = load_template("v1-split")
    for template in (v1_template, v1_split):
        assert template.get("V1").role == template.get("V2").role == "m2s-verifier-v1"
    for name in ("v2", "v2-split", "v2-judge"):
        template = load_template(name)
        roles = {s.role for s in template.tasks if s.id in ("J1", "J3", "J4", "D1", "V3")}
        assert roles == {"m2s-verifier"}, (name, roles)


def test_architect_role_split_keeps_v1_out_of_the_v2_prompt():
    """2026-11-25: m2s-architect carries the v2/A1 content only; the v1 S1
    sections and completion live in m2s-architect-v1, which the v1 templates
    use (including the spec/blueprint routing target)."""
    from coord import workers

    v2 = workers.role_prompt("m2s-architect")
    v1 = workers.role_prompt("m2s-architect-v1")
    for heading in ("## 回炉：裁定验证线提出的 spec_issues", "## 裁定 S5 的 spec 放宽申请"):
        assert heading not in v2, heading
        assert heading in v1, heading
    assert "STAGE_COMPLETE" not in v2 and "STAGE_BLOCKED" not in v2
    assert "stage_reports/S1.json" not in v2
    for heading in ("## MODE: ruling", "## MODE: adapter", "## MODE: gaps"):
        assert heading in v2, heading
        assert heading not in v1, heading
    assert "## 迁移带来的对外变化" in v2 and "adapter_map" in v2 and "gap_inventory" in v2
    assert "stage_reports/S1.json" in v1 and "STAGE_COMPLETE" in v1

    # What the dispatcher actually writes into each worker's settings.json.
    fake_ws = Path("C:/nonexistent-ws")
    for task_id, role in (("A1", "m2s-architect"), ("S1", "m2s-architect-v1")):
        job = WorkerJob(task_id, 1, role, fake_ws, fake_ws / "coord", workers.role_prompt(role),
                        "do it", (), 10, 60.0)
        system_prompt = worker_settings(job, [])["system_prompt"]
        if role == "m2s-architect":
            assert "STAGE_COMPLETE" not in system_prompt and "stage_reports/S1.json" not in system_prompt
            assert "## MODE: ruling" in system_prompt
        else:
            assert "STAGE_COMPLETE" in system_prompt and "stage_reports/S1.json" in system_prompt
            assert "## MODE: ruling" not in system_prompt

    v1_template = load_template("v1")
    v1_split = load_template("v1-split")
    for template in (v1_template, v1_split):
        assert template.get("S1").role == "m2s-architect-v1"
    for name in ("v2-split", "v2-gaps", "v2-adapter"):
        template = load_template(name)
        roles = {t.role for t in template.tasks if t.role and t.role.startswith("m2s-architect")}
        assert roles == {"m2s-architect"}, (name, roles)


def test_code_developer_role_split_keeps_s2_out_of_the_v2_prompt():
    """2026-11-26: m2s-code-developer carries the v2/I1 content (submit_task,
    report_blocked, the judge self-test); the S2 STAGE protocol and report
    path live in m2s-code-developer-v1, which the v1 templates use."""
    from coord import workers

    v2 = workers.role_prompt("m2s-code-developer")
    v1 = workers.role_prompt("m2s-code-developer-v1")
    for token in ("STAGE_COMPLETE", "STAGE_BLOCKED", "stage_reports/S2.json", "主 Agent"):
        assert token not in v2, token
        assert token in v1, token
    for token in ("submit_task", "report_blocked", "run_judge_self_test"):
        assert token in v2, token
    assert "## 用裁判自测" in v2 and "## 用裁判自测" not in v1

    fake_ws = Path("C:/nonexistent-ws")
    for task_id, role in (("I1", "m2s-code-developer"), ("S2", "m2s-code-developer-v1")):
        job = WorkerJob(task_id, 1, role, fake_ws, fake_ws / "coord", workers.role_prompt(role),
                        "do it", (), 10, 60.0)
        system_prompt = worker_settings(job, [])["system_prompt"]
        assert ("STAGE_COMPLETE" in system_prompt) == role.endswith("-v1")
        assert ("run_judge_self_test" in system_prompt) == (role == "m2s-code-developer")

    for template in (load_template("v1"), load_template("v1-split")):
        assert template.get("S2").role == "m2s-code-developer-v1"
    for name in ("v2", "v2-split", "v2-gaps"):
        template = load_template(name)
        roles = {t.role for t in template.tasks if t.role and "code-developer" in t.role}
        assert roles == {"m2s-code-developer"}, (name, roles)


def test_sam_engineer_role_split_keeps_s3_and_sg_out_of_the_v2_prompt():
    """2026-11-26: m2s-sam-engineer carries the v2/I2 content (I2a/I2b/I2c,
    submit_task/report_blocked, MERGE, m2s-sam-kb); the S3a/S3b/S3c and SG
    protocol lives in m2s-sam-engineer-v1, which the v1 templates use."""
    from coord import workers

    v2 = workers.role_prompt("m2s-sam-engineer")
    v1 = workers.role_prompt("m2s-sam-engineer-v1")
    for token in ("S3a", "S3b", "S3c", "STAGE_COMPLETE", "STAGE_BLOCKED",
                  "stage_reports/S3a.json", "STAGE_ID = SG", "不调用任何 MCP 工具"):
        assert token not in v2, token
    assert "S3a" in v1 and "S3b" in v1 and "S3c" in v1
    assert "STAGE_ID = SG" in v1 and "STAGE_COMPLETE" in v1
    for token in ("I2a", "I2b", "I2c", "submit_task", "report_blocked", "MERGE", "m2s-sam-kb"):
        assert token in v2, token
    assert "D:\\Emma\\MyLab\\Mono2SLs" not in v2 and "envs\\mono2sls" not in v2

    fake_ws = Path("C:/nonexistent-ws")
    for task_id, role in (("I2a", "m2s-sam-engineer"), ("S3a", "m2s-sam-engineer-v1")):
        job = WorkerJob(task_id, 1, role, fake_ws, fake_ws / "coord", workers.role_prompt(role),
                        "do it", (), 10, 60.0)
        system_prompt = worker_settings(job, [])["system_prompt"]
        assert ("STAGE_COMPLETE" in system_prompt) == role.endswith("-v1")
        if role == "m2s-sam-engineer":
            assert "submit_task" in system_prompt and "report_blocked" in system_prompt
        else:
            assert "submit_task" not in system_prompt

    for template in (load_template("v1"), load_template("v1-split")):
        assert template.get("S3a").role == template.get("S3b").role == template.get("S3c").role == "m2s-sam-engineer-v1"
    for name in ("v2", "v2-split", "v2-gaps"):
        template = load_template(name)
        roles = {t.role for t in template.tasks if t.role and "sam-engineer" in t.role}
        assert roles == {"m2s-sam-engineer"}, (name, roles)


def test_v1_spec_routing_resolves_the_v1_architect(tmp_path):
    """A v1 verification failure routed to "spec" finds S1 even though the v1
    template now uses m2s-architect-v1; the code/template routes find the v1
    developer/engineer tasks too."""
    d = make_dispatcher(tmp_path, {})
    d.setup()
    assert d._route_target("spec", "V2") == "S1"
    assert d._route_target("blueprint", "V2") == "S1"
    assert d._route_target("code", "V2") == "S2"
    assert d._route_target("template", "V2") == "S3c"


def test_the_v2_judge_template():
    t = load_template("v2-judge")
    assert [s.id for s in t.tasks] == ["F2", "J1", "J2", "RC", "J3", "J4", "J5", "J5C", "J6"]
    assert t.get("J1").deny_read == ("monolith", "judge_outputs") and t.get("F2").start == "recheck" and t.get("J3").mode == "J3"
    assert t.get("J1").start == "recheck" and t.get("J4").deny_read == () and t.get("J4").on_stale == "recheck"
    assert (t.get("J5").action, t.get("J5C").mode, t.get("J6").deps) == ("run_mutants", "J5", ("J5C",))
    assert t.max_reconcile_rounds == 2 and t.max_strength_rounds == 2


def test_the_mode_reaches_the_m2s_server(layout):
    ws, _ = layout
    job = WorkerJob("J1", 1, "m2s-verifier", ws, ws / "coord", "ROLE", "do it", (), 100, 60.0, mode="J1")
    assert job.env()["M2S_MODE"] == "J1"
