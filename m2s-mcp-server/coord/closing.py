"""C1 and C2 (PIPELINE_V2_PLAN.md §4.15, N5.4): the final report and the run's archive.

The report is written from files and events, never from what a worker said. The archive
goes to m2s-mcp-server/evidence/<application>/<run>/ and is append-only: a run archived
again gets a new directory, and nothing there is overwritten.
"""
from __future__ import annotations

import json
import re
import shutil
import time
from pathlib import Path
from typing import Any

from coord.checks import M2S_DIR

EVIDENCE_ROOT = M2S_DIR / "evidence"
# Never archived: a worker's generated settings, the internal snapshots of the workspace.
LEFT_OUT = {"settings.json", "mcp.json"}
LEFT_OUT_DIRS = {"shadow.git", "__pycache__", "node_modules", ".beads"}
SECRET_RE = re.compile(r"\bsk-[A-Za-z0-9_-]{16,}|\bAKIA[0-9A-Z]{16}\b|-----BEGIN [A-Z ]*PRIVATE KEY-----")
SCANNED_SUFFIXES = {".json", ".jsonl", ".md", ".log", ".txt", ".yaml", ".yml", ".sh", ".ps1"}


def _read(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _judge_lines(ws: Path) -> list[str]:
    m = _read(ws / "judge" / "judge_manifest.json")
    if not isinstance(m, dict):
        return ["- 没有冻结的裁判。"]
    score = m.get("mutation_score") or {}
    coverage = (m.get("coverage") or {})
    return [f"- 裁判 v{m.get('version')}，{m.get('frozen_at')} 冻结：{m.get('cases')} 个用例（{m.get('layers')}），"
            f"{m.get('fault_scenarios')} 个故障场景。",
            f"- 单体的行覆盖率：{coverage.get('lines')}（{coverage.get('percent')}%）。必须的响应 {m.get('required_responses')}，"
            f"业务规则 {m.get('business_rules')}。",
            f"- 变异检验：{score.get('mutants')} 个变异体，抓住 {score.get('killed')} 个，等价 {score.get('equivalent')} 个，"
            f"检出率 {score.get('score')}；并行改串行的反向检查：{(score.get('reverse') or {})}。"]


def _environment_lines(ws: Path) -> list[str]:
    oracle = (_read(ws / "judge" / "oracle_expected.json") or {}).get("meta") or {}
    judged = (_read(ws / "verification" / "verdict_judge.json") or {})
    meta = judged.get("meta") or {}
    delta = _read(ws / "verification" / "verdict_delta.json") or {}
    baseline = _read(ws / "verification" / "fault_baseline_B0.json") or {}
    perf = _read(ws / "output" / "perf_report.json") or {}
    aws = [c for c in perf.get("changes") or [] if isinstance(c, dict)
           and ((c.get("evidence") or {}).get("aws") or {}).get("verdict")]
    lines = [f"- 裁判的预期（单体）：本机进程，DynamoDB 后端 {oracle.get('backend')}。",
             f"- 裁判（目标）：{meta.get('mode')}，后端 {meta.get('backend')}，判定 {judged.get('status')}，代码 {meta.get('code')}。",
             f"- 增量测试：{delta.get('mode')}，后端 {delta.get('backend')}，判定 {delta.get('status')}（{delta.get('passed')}/{delta.get('total')}），"
             f"代码 {delta.get('code_state')}。",
             f"- 故障行为基准 B0：{baseline.get('runtime')}，{len(baseline.get('observations') or {})} 次运行。",
             "- 正确性检查是本机检验：RIE 容器和 DynamoDB Local 或 dynalite。"]
    lines.append(f"- 性能判决：{len(aws)} 个改动有 AWS 实测（同一区域部署、B0 与候选交替测量，候选栈测完拆除；"
                 "正确性检查不在 AWS 上做）。" if aws else "- 性能判决也是本机（RIE / 代理指标），没有部署到 AWS。")
    return lines


def _gap_lines(ws: Path) -> list[str]:
    data = _read(ws / "storage" / "gap_inventory.json") or {}
    entries = [g for g in data.get("gaps") or [] if isinstance(g, dict)]
    if not entries:
        return ["- 没有缺口清单。"]
    tests: dict[str, int] = {}
    for g in entries:
        test = (g.get("decision") or {}).get("test") or "未决定"
        tests[test] = tests.get(test, 0) + 1
    return [f"- {len(entries)} 条：" + "，".join(f"{k} {v}" for k, v in sorted(tests.items()))]


def _perf_lines(ws: Path) -> list[str]:
    perf = _read(ws / "output" / "perf_report.json")
    if not isinstance(perf, dict):
        return ["- 没有性能报告。"]
    changes = [c for c in perf.get("changes") or [] if isinstance(c, dict)]
    counts: dict[str, int] = {}
    for c in changes:
        counts[str(c.get("verdict"))] = counts.get(str(c.get("verdict")), 0) + 1
    lines = ["- 改动请求 " + (f"{len(changes)} 个：" + "，".join(f"{k} {v}" for k, v in sorted(counts.items())) if changes else "0 个。")]

    def aws_suffix(change: dict[str, Any]) -> str:
        aws = (change.get("evidence") or {}).get("aws") or {}
        if not aws.get("verdict"):
            return ""
        dims, guards = aws.get("dimensions") or {}, aws.get("guards") or {}
        return (f"；AWS 实测 {aws['verdict']}：Duration {dims.get('latency', '?')}，边际成本 {dims.get('cost', '?')}"
                f"，guard p50 {guards.get('p50', '?')} / p95 {guards.get('p95', '?')} / 冷启动 {guards.get('cold_start', '?')}")

    lines += [f"- 保留 {c.get('id')}（{c.get('verdict')}）：{str(c.get('summary') or c.get('proposal') or c.get('source') or '')[:200]}"
              + aws_suffix(c)
              for c in changes if c.get("verdict") in ("kept", "kept_inconclusive")]
    advisories = [a for a in perf.get("advisories") or [] if isinstance(a, dict)]
    if advisories:
        lines.append(f"- 建议（advisories，未实施）{len(advisories)} 条：")
        lines += [f"  - {str(a.get('diagnosis') or a.get('summary') or a.get('proposal') or '')[:200]}" for a in advisories]
    return lines


def _o1_lines(ws: Path) -> list[str]:
    results = _read(ws / "perf" / "candidate_results.json") or {}
    candidates = [c for c in results.get("candidates") or [] if isinstance(c, dict)]
    if not candidates:
        return ["- 没有 O1 候选（B0P = B0）。"]
    accepted = [c for c in candidates if c.get("accepted")]
    lines = [f"- O1 候选 {len(candidates)} 个，接受 {len(accepted)} 个："]
    lines += [f"  - {c.get('id')}（{c.get('owner')}，{c.get('verdict')}）commit {str(c.get('commit') or '')[:12]}"
              for c in candidates]
    return lines


def ruling_counts(rulings: list[dict[str, Any]]) -> dict[str, int]:
    """Rulings in the end, and how many a person made or confirmed."""
    human = [r for r in rulings if r.get("decider") == "human" or r.get("status") in ("confirmed", "rejected")]
    return {"total": len(rulings), "human": len(human),
            "waiting": sum(1 for r in rulings if r.get("status") == "proposed")}


def final_report(ws: Path, *, results: list[Any], rulings: list[dict[str, Any]], open_items: list[str],
                 reverted: bool, rollback: dict[str, Any] | None = None) -> str:
    counts = ruling_counts(rulings)
    lines = [f"# 最终报告：{ws.name}", "", f"生成时间：{time.strftime('%Y-%m-%d %H:%M')}。由调度器按文件和事件生成。", "",
             "## 最后一次检查", ""]
    lines += [f"- {r.name}：{'通过' if r.ok else '未通过'}。{r.summary}" for r in results]
    if reverted:
        if rollback:
            commits = rollback.get("commits") or []
            what = "逐个回退到通过为止" if rollback.get("mode") == "fine" else f"整体退回 {rollback.get('baseline')}"
            lines.append(f"- 优化阶段的改动没有通过最后检查；{what}（{len(commits)} 个 O2 提交），"
                         f"回退的改动：{', '.join(rollback.get('reverted') or []) or '没有'}。")
        else:
            lines.append("- 优化阶段的改动没有通过最后检查，已经全部退回 B0。")
    lines += ["", "## 每一层在什么环境下检验", "", *_environment_lines(ws), "", "## 裁判和强度", "", *_judge_lines(ws),
              "", "## 缺口", "", *_gap_lines(ws), "", "## O1 架构级改动（B0 → B0P）", "", *_o1_lines(ws),
              "", "## O2 局部优化（B0P → final）", "", *_perf_lines(ws), "", "## 裁定", "",
              f"- 共 {counts['total']} 条，其中人工裁定或确认 {counts['human']} 条，还在等人 {counts['waiting']} 条。"]
    lines += [f"- {r.get('id')}（{r.get('category')}，{r.get('status')}）：{str(r.get('question'))[:160]} → {str(r.get('conclusion'))[:160]}"
              for r in rulings]
    lines += ["", "## 仍未解决", ""]
    lines += open_items or ["- 没有。"]
    return "\n".join(lines) + "\n"


def _stage_index(ws: Path) -> list[dict[str, Any]]:
    index = []
    for path in sorted((ws / "storage" / "stage_reports").glob("*.json")):
        report = _read(path)
        report = report if isinstance(report, dict) else {}
        index.append({"task": path.stem, "file": f"storage/stage_reports/{path.name}", "status": report.get("status"),
                      "summary": str(report.get("summary") or "")[:400]})
    return index


def _verdict_summary(ws: Path) -> dict[str, Any]:
    judged = _read(ws / "verification" / "verdict_judge.json") or {}
    delta = _read(ws / "verification" / "verdict_delta.json") or {}
    return {"judge": {"status": judged.get("status"), "summary": judged.get("summary"), "meta": judged.get("meta")},
            "delta": {k: delta.get(k) for k in ("status", "total", "passed", "mode", "backend", "code_state", "timestamp")}}


def archive_run(ws: Path, monolith: Path, root: Path | None = None) -> dict[str, Any]:
    """C2: the run's evidence, built aside and moved in only when it is complete and clean."""
    root = root or EVIDENCE_ROOT
    base = root / monolith.name / ws.name
    target, n = base, 1
    while target.exists():
        n += 1
        target = base.with_name(f"{base.name}.{n}")
    build = target.with_name(target.name + ".building")
    if build.exists():
        shutil.rmtree(build)
    build.mkdir(parents=True)
    wanted = ["storage/harness_version.json", "storage/rulings.json", "storage/gap_inventory.json", "storage/DECISIONS.md",
              "storage/adapter_map.json", "judge/judge_manifest.json", "judge/judge_validation.json", "output/perf_report.json",
              "output/final_report.md", "verification/verdict_judge.json", "verification/verdict_delta.json"]
    copied, missing = [], []
    for rel in wanted:
        source = ws / rel
        if source.is_file():
            (build / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, build / rel)
            copied.append(rel)
        else:
            missing.append(rel)
    (build / "stage_reports_index.json").write_text(json.dumps(_stage_index(ws), ensure_ascii=False, indent=1), encoding="utf-8")
    (build / "verdict_summary.json").write_text(json.dumps(_verdict_summary(ws), ensure_ascii=False, indent=1), encoding="utf-8")

    def ignore(folder: str, names: list[str]) -> set[str]:
        return {x for x in names if x in LEFT_OUT or x in LEFT_OUT_DIRS}

    shutil.copytree(ws / "coord", build / "coord", ignore=ignore)
    secrets = []
    for path in build.rglob("*"):
        if path.is_file() and path.suffix in SCANNED_SUFFIXES and SECRET_RE.search(path.read_text(encoding="utf-8", errors="replace")):
            secrets.append(path.relative_to(build).as_posix())
    if secrets:
        shutil.rmtree(build)
        return {"status": "FAIL", "error": "these files look like they hold a secret; nothing was archived", "files": secrets}
    (build / "ARCHIVE.json").write_text(json.dumps(
        {"application": monolith.name, "run": ws.name, "workspace": str(ws), "archived_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
         "files": copied, "missing": missing}, ensure_ascii=False, indent=1), encoding="utf-8")
    build.rename(target)
    return {"status": "PASS", "path": str(target), "files": copied, "missing": missing}
