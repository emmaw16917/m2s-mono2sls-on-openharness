"""v3 M2 (W2.8): resource locks, the worker cap and scope conflicts."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from coord_fakes import make_dispatcher, ok_report  # noqa: E402

from coord.checks import CheckResult  # noqa: E402
from coord.dispatcher import EXIT_DONE  # noqa: E402
from coord.locks import Locks, hold_count  # noqa: E402
from coord.template import load_template, scopes_conflict  # noqa: E402


def test_exclusive_lock_cannot_be_taken_twice(tmp_path):
    locks = Locks(tmp_path / "locks")
    first = locks.acquire("ws-git:ws1", "dispatcher")
    assert first is not None
    assert locks.acquire("ws-git:ws1", "other", wait_s=0) is None
    first.release()
    again = locks.acquire("ws-git:ws1", "other", wait_s=0)
    assert again is not None
    again.release()


def test_dead_holder_is_reclaimed(tmp_path):
    locks = Locks(tmp_path / "locks")
    path = locks._path("ws-git:ws1")
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"pid": 999999, "owner": "gone", "expires": 9e18}), encoding="utf-8")
    handle = locks.acquire("ws-git:ws1", "dispatcher", wait_s=0)
    assert handle is not None
    handle.release()


def test_expired_lock_is_reclaimed(tmp_path):
    locks = Locks(tmp_path / "locks")
    path = locks._path("ws-git:ws1")
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"pid": 1, "owner": "old", "expires": 1}), encoding="utf-8")
    handle = locks.acquire("ws-git:ws1", "dispatcher", wait_s=0)
    assert handle is not None
    handle.release()


def test_count_lock_limits_slots(tmp_path):
    locks = Locks(tmp_path / "locks")
    first = locks.acquire_count("workers", 2, "a", wait_s=0)
    second = locks.acquire_count("workers", 2, "b", wait_s=0)
    assert first and second
    assert locks.acquire_count("workers", 2, "c", wait_s=0) is None
    first.release()
    third = locks.acquire_count("workers", 2, "c", wait_s=0)
    assert third is not None
    second.release()
    third.release()


def test_hold_count_is_reentrant(tmp_path, monkeypatch):
    monkeypatch.setenv("M2S_LOCK_DIR", str(tmp_path / "locks"))
    with hold_count("docker-rie", 1, wait_s=0) as outer:
        assert outer is not None
        with hold_count("docker-rie", 1, wait_s=0) as inner:
            assert inner is None  # the same process already holds it


def test_scopes_conflict():
    assert scopes_conflict(["output/lambdas/books/**"], ["output/lambdas/books/handler.js"])
    assert scopes_conflict(["output/**"], ["output/lambdas/books/**"])
    assert scopes_conflict(["**"], ["storage/x.json"])
    assert not scopes_conflict(["output/lambdas/books/**"], ["output/lambdas/cart/**"])
    assert not scopes_conflict(["storage/analysis_report.json"], ["storage/stage_reports/S0-books.json"])


def test_scopes_conflict_honours_shared_write():
    """F2 parallel annotation: one file is shared when *both* tasks lock it."""
    report = ["storage/analysis_report.json"]
    assert not scopes_conflict(report, report, report, report)
    assert scopes_conflict(report, report, report, [])  # the other side does not lock
    assert scopes_conflict(report, report, ["storage/other.json"], report)
    # an unrelated overlap is still a conflict whatever is declared shared
    assert scopes_conflict(["storage/**"], ["storage/gap_inventory.json"], report, report)


TEMPLATE = """\
name: lock-test
tasks:
  - id: A
    role: fake
    scope: [storage/shared.json, storage/stage_reports/A.json]
    check: [ok]
  - id: B
    role: fake
    scope: [storage/shared.json, storage/stage_reports/B.json]
    check: [ok]
"""

TEMPLATE_DISJOINT = """\
name: lock-test-2
tasks:
  - id: A
    role: fake
    scope: [storage/a.json, storage/stage_reports/A.json]
    check: [ok]
  - id: B
    role: fake
    scope: [storage/b.json, storage/stage_reports/B.json]
    check: [ok]
"""


def _ok(ws: Path, spec: dict) -> CheckResult:
    return CheckResult("ok", True, "ok")


def _order(d) -> list[tuple[str, str]]:
    return [(e["type"], e.get("task")) for e in d.events.all() if e["type"] in ("spawn", "worker_exit")]


TEMPLATE_SHARED_WRITE = """\
name: shared-write-test
tasks:
  - id: A
    role: fake
    shared_write: [storage/analysis_report.json]
    scope: [storage/analysis_report.json, storage/stage_reports/A.json]
    check: [ok]
  - id: B
    role: fake
    shared_write: [storage/analysis_report.json]
    scope: [storage/analysis_report.json, storage/stage_reports/B.json]
    check: [ok]
"""


def _slow_script() -> dict:
    """Both workers stay up long enough for their runs to overlap."""
    return {"A": [{**ok_report("A"), "sleep": 0.6}], "B": [{**ok_report("B"), "sleep": 0.6}]}


def test_overlapping_scopes_do_not_run_together(tmp_path, monkeypatch):
    monkeypatch.setenv("M2S_LOCK_DIR", str(tmp_path / "locks"))
    path = tmp_path / "scope.yaml"
    path.write_text(TEMPLATE, encoding="utf-8")
    template = load_template(path=path)
    d = make_dispatcher(tmp_path, {}, template=template, checks={"ok": _ok})
    d.setup()
    assert d.run() == EXIT_DONE

    order = _order(d)
    spawns = [i for i, (k, _t) in enumerate(order) if k == "spawn"]
    exits = [i for i, (k, _t) in enumerate(order) if k == "worker_exit"]
    assert len(spawns) == len(exits) == 2
    # one task finishes before the other starts
    assert spawns[0] < exits[0] < spawns[1]


def test_worker_slots_serialize_disjoint_tasks(tmp_path, monkeypatch):
    monkeypatch.setenv("M2S_WORKER_SLOTS", "1")
    monkeypatch.setenv("M2S_LOCK_DIR", str(tmp_path / "locks"))
    path = tmp_path / "disjoint.yaml"
    path.write_text(TEMPLATE_DISJOINT, encoding="utf-8")
    template = load_template(path=path)
    d = make_dispatcher(tmp_path, {}, template=template, checks={"ok": _ok})
    d.setup()
    assert d.run() == EXIT_DONE

    order = _order(d)
    spawns = [i for i, (k, _t) in enumerate(order) if k == "spawn"]
    exits = [i for i, (k, _t) in enumerate(order) if k == "worker_exit"]
    assert len(spawns) == len(exits) == 2
    assert spawns[1] > exits[0]  # the second worker waits for the single slot


def test_write_scope_ignores_files_owned_by_another_task(tmp_path):
    path = tmp_path / "scopes.yaml"
    path.write_text(TEMPLATE_DISJOINT, encoding="utf-8")
    template = load_template(path=path)
    d = make_dispatcher(tmp_path, {}, template=template, checks={"ok": _ok})
    d.setup()
    spec_a = d._spec("A")
    # storage/b.json belongs to B's scope: A's window must not revert it
    assert d._write_scope_outside(spec_a, [("??", "storage/b.json")]) == []
    assert d._write_scope_outside(spec_a, [("??", "storage/not-owned.json")]) == [("??", "storage/not-owned.json")]


def test_shared_write_overlap_runs_together(tmp_path, monkeypatch):
    """F2 parallel annotation: both tasks declare the same file shared_write,
    so the scheduler starts them together instead of serializing on scope."""
    monkeypatch.setenv("M2S_LOCK_DIR", str(tmp_path / "locks"))
    path = tmp_path / "shared-write.yaml"
    path.write_text(TEMPLATE_SHARED_WRITE, encoding="utf-8")
    template = load_template(path=path)
    d = make_dispatcher(tmp_path, _slow_script(), template=template, checks={"ok": _ok}, max_workers=2)
    d.setup()
    assert d.run() == EXIT_DONE

    order = _order(d)
    spawns = [i for i, (k, _t) in enumerate(order) if k == "spawn"]
    exits = [i for i, (k, _t) in enumerate(order) if k == "worker_exit"]
    assert len(spawns) == len(exits) == 2
    assert spawns[1] < exits[0]  # both workers were running at the same time


def test_without_shared_write_two_workers_still_serialize_the_file(tmp_path, monkeypatch):
    """The control: max_workers=2 alone must not let two overlapping scopes
    run together when only one side (or neither) declares the lock."""
    monkeypatch.setenv("M2S_LOCK_DIR", str(tmp_path / "locks"))
    path = tmp_path / "overlap.yaml"
    path.write_text(TEMPLATE, encoding="utf-8")
    template = load_template(path=path)
    d = make_dispatcher(tmp_path, _slow_script(), template=template, checks={"ok": _ok}, max_workers=2)
    d.setup()
    assert d.run() == EXIT_DONE

    order = _order(d)
    spawns = [i for i, (k, _t) in enumerate(order) if k == "spawn"]
    exits = [i for i, (k, _t) in enumerate(order) if k == "worker_exit"]
    assert len(spawns) == len(exits) == 2
    assert spawns[1] > exits[0]  # the scope conflict still serializes them


def test_report_lock_files_are_not_scope_violations(tmp_path):
    """The annotate tools create and remove storage/.analysis_report.lock and
    a PID-unique temp inside one call. A concurrent task's snapshot must not
    blame or revert them (reverting would steal the other task's lock)."""
    path = tmp_path / "disjoint.yaml"
    path.write_text(TEMPLATE_DISJOINT, encoding="utf-8")
    template = load_template(path=path)
    d = make_dispatcher(tmp_path, {}, template=template, checks={"ok": _ok})
    d.setup()
    spec_a = d._spec("A")
    assert d._write_scope_outside(spec_a, [("A", "storage/.analysis_report.lock")]) == []
    assert d._write_scope_outside(spec_a, [("A", "storage/analysis_report.json.4321.tmp")]) == []
    assert d._write_scope_outside(spec_a, [("A", "storage/not-owned.json")]) == [("A", "storage/not-owned.json")]
