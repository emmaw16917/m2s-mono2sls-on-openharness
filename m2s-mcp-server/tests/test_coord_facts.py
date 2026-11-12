"""v3 M4/W4.1: every fact-base change gets a version and an artifact notice."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from coord_fakes import make_dispatcher  # noqa: E402

from coord import facts  # noqa: E402
from coord.checks import CheckResult  # noqa: E402
from coord.dispatcher import EXIT_DONE  # noqa: E402
from coord.template import load_template  # noqa: E402

TEMPLATE = """\
name: facts-test
tasks:
  - id: F1
    runner: dispatcher
    action: analyze_project
    scope: [storage/analysis_report.json]
    check: [ok]
  - id: F2a
    role: m2s-spec-annotator
    deps: [F1]
    scope: [storage/analysis_report.json, storage/stage_reports/F2a.json]
    check: [ok]
  - id: F2b
    role: m2s-spec-annotator
    deps: [F2a]
    scope: [storage/analysis_report.json, storage/stage_reports/F2b.json]
    check: [ok]
  - id: I1
    role: m2s-code-developer
    deps: [F2b]
    scope: [output/lambdas/**, storage/stage_reports/I1.json]
    check: [ok]
"""


def test_fact_changes_get_versions_and_notices(tmp_path, monkeypatch):
    monkeypatch.setenv("M2S_LOCK_DIR", str(tmp_path / "locks"))
    template_path = tmp_path / "template.yaml"
    template_path.write_text(TEMPLATE, encoding="utf-8")
    template = load_template(path=template_path)
    ws = tmp_path / "ws"

    def analyze(spec):
        path = ws / "storage" / "analysis_report.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"facts": 1}), encoding="utf-8")
        return CheckResult("analyze_project", True, "ok")

    script = {
        "F2a": [{"write": {"storage/analysis_report.json": {"facts": 2},
                           "storage/stage_reports/F2a.json": {"ok": True}}, "submit": "done"}],
        "F2b": [{"write": {"storage/analysis_report.json": {"facts": 3},
                           "storage/stage_reports/F2b.json": {"ok": True}}, "submit": "done"}],
    }
    d = make_dispatcher(tmp_path, script, template=template, actions={"analyze_project": analyze})
    d.setup()
    assert d.run() == EXIT_DONE

    record = facts.read(ws)
    assert record["version"] == 2
    assert len(record["sha"]) == 64
    assert json.loads((ws / "storage" / "facts_version.json").read_text(encoding="utf-8"))["version"] == 2

    notices = [json.loads(line) for line in
               (ws / "coord" / "mail" / "notices.jsonl").read_text(encoding="utf-8").splitlines()]
    artifacts = [n for n in notices if n.get("topic") == "artifact"]
    assert [n["body"]["facts_version"] for n in artifacts] == [1, 2]
    assert [n["body"]["by"] for n in artifacts] == ["F2a", "F2b"]

    spawns = {e["task"]: e for e in d.events.all() if e["type"] == "spawn"}
    assert spawns["F2a"].get("facts_version") is None  # nothing recorded before it ran
    assert spawns["F2b"].get("facts_version") == 1
    assert spawns["I1"].get("facts_version") == 2
