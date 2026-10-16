"""Run m2s orchestrator sessions (`oh -p`) for several workspaces, one after
another, and resume a workspace until it is really finished.

    python oh_chain.py --log-dir ../temp/chain ecommerce-e0=../temp/gsi/ecommerce-e0.md bookstore-v2=...

"Finished" is decided by the tools, not by commit messages: gate(final)
must PASS (its SR1/SR2 checks already reject a pending_spec request and an
accepted relaxation nobody re-judged) and output/perf_report.json may hold no
pending_human change from this chain run. A session that ends early (network
error, a text-only reply, the orchestrator stopping at a failed gate) is
started again with a resume preamble, up to --attempts times; only then does
the chain move to the next workspace.

Runs on this machine's setup: `oh` on PATH, the m2s-server MCP entry in
~/.openharness/settings.json, workspaces under Mono2Sls-artifacts/M2S/runs.
The M2S_WORKSPACE setting is switched per workspace (a backup of the file is
kept as settings.json.bak-oh-chain). Do not edit the tool repo while a chain
runs: gate(final)'s TOOLS check fails on a dirty tool tree.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gates  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
RUNS = REPO / "Mono2Sls-artifacts" / "M2S" / "runs"
SETTINGS = Path.home() / ".openharness" / "settings.json"

RESUME = (
    "续跑（上一次 oh 运行没有把这个工作区跑完：可能是网络错误中断、只回复了文字，或者停在了没通过的 gate 上）。"
    "先读 WS/storage/TODO.md、output/perf_report.json、perf/requests/、blueprint 的 spec_resolutions 和 git log，"
    "判断流程走到了哪一步、为什么没完成，从那里继续；不重做已经提交的步骤；output/ 里没提交的临时量测改动可以丢弃。"
    "原始说明里\"WS 是空目录、第一步一定是 P0\"只适用于首次启动；续跑时 WS 里已有的产出就是本次运行之前的进度，不要移走、不要重做。"
    "gate(final) 没通过时，按它报的检查项处理（例如 SR2：被接受的申请还没有重判；裁定本身有问题就交回 architect 修订）。"
    "下面是原始任务说明：\n\n"
)


def _git(workspace: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(workspace), *args], capture_output=True, text=True).stdout.strip()


def pipeline_complete(workspace: Path) -> str | None:
    """None when the run went past S4: gate(final) also passes right after S4,
    so on its own it cannot tell a finished run from one that stopped at V2.
    Requires the workspace's own repo tagged v0, the S5 report and perf_report.json."""
    top = _git(workspace, "rev-parse", "--show-toplevel")
    if not top or Path(top).resolve() != workspace.resolve():
        return "workspace has no git repo of its own (v0 not tagged yet)"
    if not _git(workspace, "tag", "--list", "v0"):
        return "tag v0 missing"
    for f in ("storage/stage_reports/S5.json", "output/perf_report.json"):
        if not (workspace / f).is_file():
            return f"{f} missing (S5 not run)"
    return None


def has_progress(workspace: Path) -> bool:
    return any(p.name not in (".gitkeep",) for p in workspace.iterdir()) if workspace.is_dir() else False


def finished(workspace: Path, since: float) -> tuple[bool, str]:
    """(done, why): pipeline_complete, gate(final) PASS and no pending_human
    change recorded by commits made after `since` (epoch seconds)."""
    missing = pipeline_complete(workspace)
    if missing:
        return False, f"pipeline not complete: {missing}"
    gate = gates.run_gate("final", str(workspace))
    if gate.get("status") != "PASS":
        failed = [c.get("check_id") for c in gate.get("checks", []) if c.get("severity") == "ERROR"]
        return False, f"gate(final) {gate.get('status')}: {failed or gate.get('mechanical_failures')}"
    rp = workspace / "output" / "perf_report.json"
    try:
        changes = json.loads(rp.read_text(encoding="utf-8")).get("changes") or []
    except (OSError, ValueError):
        changes = []
    pending = [c.get("id") for c in changes if isinstance(c, dict) and c.get("verdict") == "pending_human"]
    if pending:
        new = subprocess.run(["git", "-C", str(workspace), "log", f"--since=@{int(since)}", "--format=%H"],
                             capture_output=True, text=True).stdout.split()
        if new:
            return False, f"gate(final) PASS but pending_human changes remain: {pending}"
    return True, "gate(final) PASS"


def set_workspace(name: str) -> None:
    text = SETTINGS.read_text(encoding="utf-8")
    new, n = re.subn(r'("M2S_WORKSPACE"\s*:\s*")runs/[^"]*(")', rf'\g<1>runs/{name}\g<2>', text)
    if n != 1:
        raise SystemExit(f"expected exactly one M2S_WORKSPACE entry in {SETTINGS}, found {n}")
    SETTINGS.write_text(new, encoding="utf-8")


def run_chain(apps: list[tuple[str, Path]], log_dir: Path, attempts: int, max_turns: int) -> list[dict[str, Any]]:
    log_dir.mkdir(parents=True, exist_ok=True)
    chain_log = log_dir / "chain.log"
    backup = SETTINGS.with_name("settings.json.bak-oh-chain")
    if not backup.exists():
        shutil.copy2(SETTINGS, backup)
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    results = []

    def note(line: str) -> None:
        with chain_log.open("a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M')} {line}\n")

    for name, prompt_file in apps:
        ws = RUNS / name
        task = prompt_file.read_text(encoding="utf-8")
        set_workspace(name)
        start = time.time()
        done, why = False, "not started"
        resuming = has_progress(ws)  # a relaunched chain must not hand the fresh-start prompt to a workspace with progress
        first_log = 1 + sum(1 for _ in log_dir.glob(f"{name}.run*.log"))
        for attempt in range(1, attempts + 1):
            prompt = RESUME + task if resuming or attempt > 1 else task
            log = log_dir / f"{name}.run{first_log + attempt - 1}.log"
            note(f"start {name} attempt {attempt}{' (resume)' if prompt is not task else ''} log={log.name}")
            with log.open("w", encoding="utf-8") as out:
                rc = subprocess.run(["oh", "-p", prompt, "--max-turns", str(max_turns)], cwd=REPO, env=env,
                                    stdout=out, stderr=subprocess.STDOUT).returncode
            done, why = finished(ws, start)
            note(f"end {name} attempt {attempt} exit={rc}: {'FINISHED' if done else 'NOT FINISHED'} - {why}")
            if done:
                break
        results.append({"workspace": name, "finished": done, "why": why})
    note("CHAIN_DONE " + json.dumps(results, ensure_ascii=False))
    return results


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("apps", nargs="+", help="workspace=prompt_file, in run order")
    ap.add_argument("--log-dir", default=str(REPO / "temp" / "oh-chain"))
    ap.add_argument("--attempts", type=int, default=4, help="sessions per workspace before moving on (first run + resumes)")
    ap.add_argument("--max-turns", type=int, default=400)
    args = ap.parse_args()
    apps = []
    for a in args.apps:
        name, _, prompt = a.partition("=")
        if not prompt or not (RUNS / name).is_dir() or not Path(prompt).is_file():
            raise SystemExit(f"bad app spec {a!r}: need <workspace under {RUNS}>=<existing prompt file>")
        apps.append((name, Path(prompt)))
    print(json.dumps(run_chain(apps, Path(args.log_dir), args.attempts, args.max_turns), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
