"""Smoke test for the pinned coordination tools (PIPELINE_V3_PLAN.md W0.2).

    python -m coord.smoke_tools [--keep]

Checks the behaviour the v3 design relies on, on this machine:
- br: the task-board policy (dependencies, the done_check gate, no bypass,
  re-blocking through `stale`, the needs_human transitions), plus concurrent
  writers;
- agent_mail: one shared HTTP server, registration, a message thread, and
  the advisory build slot / refusing file reservation semantics.

Runs in a fresh directory under <tools root>/_smoke and removes it unless
--keep is given. Prints a JSON report; exit status 0 only if every check
passed.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import shutil
import stat
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path

from coord.board import Br, issues_of
from coord.mail import AgentMailServer, connect
from coord.policy import DISPATCHER_ACTOR, DONE_GATE
from coord.toolchain import PINNED, tools_root


def remove_tree(path: Path) -> None:
    """rmtree that also removes read-only files (agent_mail's git objects on Windows)."""

    def _retry_writable(func, target, _exc):
        os.chmod(target, stat.S_IWRITE)
        func(target)

    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=_retry_writable)
    else:
        shutil.rmtree(path, onerror=_retry_writable)


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""


class _Checks(list):
    def add(self, name: str, ok: bool, detail: str = "") -> bool:
        self.append(Check(name, bool(ok), detail))
        return bool(ok)


def smoke_br(workdir: Path) -> list[Check]:
    checks = _Checks()
    br = Br(workdir)
    br.init()

    a, b = br.create("F2:flights"), br.create("J1:flights")
    br.run("dep", "add", b, a)
    checks.add("dependency blocks downstream", br.ready_ids() == [a], f"ready={br.ready_ids()}")

    br.run("update", a, "--claim")
    br.run("update", a, "-s", "checking")
    refused = br.run("close", a, check=False)
    checks.add("close without done_check is refused", not refused.ok and DONE_GATE in refused.stderr, refused.stderr.strip()[:160])
    bypass = br.run("close", a, "--bypass-policy", "--bypass-reason", "smoke", check=False)
    checks.add("bypass is disabled", not bypass.ok and "allow_bypass" in bypass.stderr, bypass.stderr.strip()[:160])

    br.run("gate", "report", a, "--gate", DONE_GATE, "--provider", DISPATCHER_ACTOR, "--status", "fail", "--to", "closed")
    failed_gate = br.run("close", a, check=False)
    checks.add("a failing gate still blocks close", not failed_gate.ok)
    br.run("gate", "report", a, "--gate", DONE_GATE, "--provider", DISPATCHER_ACTOR, "--status", "pass", "--to", "closed")
    closed = br.run("close", a, check=False)
    checks.add("passing gate allows close", closed.ok and br.status(a) == "closed", closed.stderr.strip()[:160])
    checks.add("closing unblocks downstream", br.ready_ids() == [b], f"ready={br.ready_ids()}")

    br.run("update", a, "-s", "stale")
    checks.add("stale upstream re-blocks downstream", b not in br.ready_ids() and br.status(a) == "stale")

    c = br.create("S2:flights")
    br.run("update", c, "--claim")
    br.run("update", c, "-s", "checking")
    br.run("update", c, "-s", "needs_human")
    bare = br.run("close", c, check=False)
    checks.add("needs_human -> closed needs a comment", not bare.ok, bare.stderr.strip()[:160])
    with_comment = br.run("close", c, "--transition-comment", "human: accepted after review", check=False)
    checks.add("needs_human -> closed with a comment", with_comment.ok and br.status(c) == "closed", with_comment.stderr.strip()[:160])

    d = br.create("S3a")
    br.run("update", d, "--claim")
    br.run("update", d, "-s", "checking")
    br.run("update", d, "-s", "needs_human")
    reopen_bare = br.run("update", d, "-s", "open", check=False)
    reopen = br.run("update", d, "-s", "open", "--transition-comment", "human: retry with new hint", check=False)
    checks.add("needs_human -> open needs a comment", not reopen_bare.ok and reopen.ok and br.status(d) == "open")

    before = len(issues_of(br.run("list", "--json", "--limit", "1000").json()))
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda i: br.create(f"concurrent-{i}"), range(12)))
    after = len(issues_of(br.run("list", "--json", "--limit", "1000").json()))
    checks.add("concurrent writers lose nothing", after - before == 12, f"{before} -> {after}")

    warning = br.run("ready", "--json")
    checks.add("policy has no unknown keys", "unknown key" not in warning.stderr, warning.stderr.strip()[:160])
    return checks


async def _smoke_mail(server: AgentMailServer, project_dir: Path) -> list[Check]:
    checks = _Checks()
    async with connect(server) as mail:
        await mail.call("ensure_project", human_key=str(project_dir))
        a = await mail.call("register_agent", project_key=str(project_dir), program="smoke", model="none", task_description="architect")
        b = await mail.call("register_agent", project_key=str(project_dir), program="smoke", model="none", task_description="verifier")
        checks.add("agents register", bool(a.get("name")) and bool(b.get("name")) and a["name"] != b["name"])

        sent = await mail.call(
            "send_message",
            project_key=str(project_dir),
            sender_name=a["name"],
            sender_token=a.get("registration_token"),
            to=[b["name"]],
            subject="SI-1: ordering of bookings",
            body_md="Does the monolith guarantee ordering?",
            thread_id="room-spec-SI-1",
            auto_contact_if_blocked=True,
        )
        inbox = await mail.call("fetch_inbox", project_key=str(project_dir), agent_name=b["name"], include_bodies=True)
        messages = inbox if isinstance(inbox, list) else inbox.get("messages", inbox.get("items", []))
        got = [m for m in messages if isinstance(m, dict) and "SI-1" in str(m.get("subject", ""))]
        checks.add("message reaches the recipient", bool(got), json.dumps(sent)[:160])
        if got:
            await mail.call(
                "reply_message",
                project_key=str(project_dir),
                message_id=got[0]["id"],
                sender_name=b["name"],
                sender_token=b.get("registration_token"),
                body_md="Yes, by booking time.",
            )
            thread = await mail.call("search_messages", project_key=str(project_dir), query="ordering", thread_id="room-spec-SI-1")
            checks.add("reply lands in the same thread", "room-spec-SI-1" in json.dumps(thread))

        slot = {"project_key": str(project_dir), "slot": "aws-load-client", "ttl_seconds": 60, "exclusive": True}
        await mail.call("acquire_build_slot", agent_name=a["name"], **slot)
        second = await mail.call("acquire_build_slot", agent_name=b["name"], **slot)
        checks.add(
            "build slots are advisory (conflict reported, still granted)",
            bool(second.get("granted")) and bool(second.get("conflicts")),
            "the dispatcher must hold resource locks itself",
        )

        reserve = {"project_key": str(project_dir), "paths": ["output/lambdas/flights/**"], "ttl_seconds": 60, "exclusive": True}
        await mail.call("file_reservation_paths", agent_name=a["name"], reason="S2:flights", **reserve)
        clash = await mail.call("file_reservation_paths", agent_name=b["name"], reason="S2:orders", **reserve)
        checks.add("conflicting exclusive reservation is refused", not clash.get("granted") and bool(clash.get("conflicts")))
    return checks


def smoke_agent_mail(workdir: Path) -> list[Check]:
    server = AgentMailServer(workdir / "store")
    project_dir = workdir / "proj"
    project_dir.mkdir(parents=True, exist_ok=True)
    checks = _Checks()
    started = time.monotonic()
    server.start()
    checks.add("HTTP server becomes healthy", True, f"{time.monotonic() - started:.1f}s on port {server.port}")
    try:
        checks.extend(asyncio.run(_smoke_mail(server, project_dir)))
    finally:
        server.stop()
    return checks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m coord.smoke_tools")
    parser.add_argument("--keep", action="store_true", help="keep the scratch directory")
    args = parser.parse_args(argv)

    workdir = tools_root() / "_smoke" / time.strftime("%Y%m%d-%H%M%S")
    report: dict[str, object] = {"workdir": str(workdir), "versions": {n: t.version for n, t in PINNED.items()}}
    all_checks: list[Check] = []
    for name, fn in (("br", smoke_br), ("agent_mail", smoke_agent_mail)):
        try:
            checks = fn(workdir / name)
        except Exception as exc:  # report and keep going so one tool's failure does not hide the other
            checks = [Check(f"{name} smoke ran", False, f"{type(exc).__name__}: {exc}")]
        report[name] = [asdict(check) for check in checks]
        all_checks.extend(checks)
    report["passed"] = all(check.ok for check in all_checks)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not args.keep:
        remove_tree(workdir)
        with contextlib.suppress(OSError):
            workdir.parent.rmdir()  # only succeeds when no other smoke run is kept
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
