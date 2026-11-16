"""Machine-wide resource locks (PIPELINE_V3_PLAN.md §4.11, v3 M2/W2.8).

The lock directory is shared by every workspace on this machine
(`M2S_LOCK_DIR`, default `D:/m2s-locks` on Windows and `/tmp/m2s-locks`
elsewhere). An exclusive lock is a file created atomically with O_EXCL; it
records the holder's pid, task and expiry, so a lock whose process is gone or
whose TTL passed can be reclaimed. A counting lock is N numbered exclusive
locks (`docker-rie-1..N`).

Resources and who takes them:

    workers          count (default 3)   the dispatcher, per live worker
    docker-rie       count (default 1)   run_validate / run_rie_verdict tools
    ws-git:<ws>      exclusive           dispatcher commits and tags
    aws-load-client  exclusive           AWS load generator (M4)
    aws-infra-stack  exclusive           shared AWS infrastructure (M4)
    aws-concurrency  count (quota)       stacks measured at the same time (M4)

agent_mail's build slots are advisory only and are not locks (§6.4).
"""
from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

DEFAULT_ROOT = Path("D:/m2s-locks") if os.name == "nt" else Path("/tmp/m2s-locks")
DEFAULT_TTL_S = 3600.0


def lock_root() -> Path:
    return Path(os.environ.get("M2S_LOCK_DIR") or DEFAULT_ROOT)


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


@dataclass
class LockHandle:
    path: Path
    name: str
    owner: str

    def release(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if data.get("owner") != self.owner:
            return  # someone reclaimed it; not ours to remove
        try:
            self.path.unlink()
        except OSError:
            pass


class Locks:
    def __init__(self, root: Path | str | None = None):
        self.root = Path(root) if root else lock_root()

    def _path(self, name: str) -> Path:
        safe = name.replace("\\", "_").replace("/", "_")
        return self.root / f"{safe}.lock"

    def _free(self, path: Path, ttl_s: float) -> bool:
        """True when the lock file is absent or can be reclaimed."""
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return True
        except OSError:
            return False
        try:
            data = json.loads(raw)
        except ValueError:
            data = {}
        pid = int(data.get("pid") or 0)
        expires = float(data.get("expires") or 0)
        if expires and time.time() > expires:
            pass  # expired
        elif _pid_alive(pid):
            return False
        try:
            path.unlink()
        except OSError:
            return False
        return True

    def acquire(self, name: str, owner: str, *, ttl_s: float = DEFAULT_TTL_S,
                wait_s: float = 0.0) -> LockHandle | None:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self._path(name)
        deadline = time.time() + max(0.0, wait_s)
        while True:
            if self._free(path, ttl_s):
                payload = json.dumps({"pid": os.getpid(), "owner": owner, "task": owner,
                                      "expires": time.time() + ttl_s}, ensure_ascii=False)
                try:
                    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                except FileExistsError:
                    pass
                else:
                    with os.fdopen(fd, "w", encoding="utf-8") as f:
                        f.write(payload)
                    return LockHandle(path, name, owner)
            if time.time() >= deadline:
                return None
            time.sleep(0.2)

    def acquire_count(self, name: str, slots: int, owner: str, *, ttl_s: float = DEFAULT_TTL_S,
                      wait_s: float = 0.0) -> LockHandle | None:
        deadline = time.time() + max(0.0, wait_s)
        while True:
            for i in range(1, max(1, slots) + 1):
                handle = self.acquire(f"{name}-{i}", owner, ttl_s=ttl_s, wait_s=0)
                if handle:
                    return handle
            if time.time() >= deadline:
                return None
            time.sleep(0.2)

    def holder(self, name: str) -> dict[str, Any] | None:
        try:
            return json.loads(self._path(name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None


# Resources already held by this process. A nested request (run_judge calling
# run_validate inside the same process) must not take a second slot: with one
# docker-rie slot that would deadlock.
_HELD: set[str] = set()


@contextmanager
def hold_count(name: str, slots: int | None = None, owner: str = "runner",
               *, ttl_s: float = 7200.0, wait_s: float = 7200.0) -> Iterator[LockHandle | None]:
    """Context manager for a counting lock; no-op when this process already holds it."""
    if name in _HELD:
        yield None
        return
    slots = slots if slots is not None else int(os.environ.get(f"M2S_{name.upper().replace('-', '_')}_SLOTS", "1"))
    lock = Locks().acquire_count(name, slots, owner, ttl_s=ttl_s, wait_s=wait_s)
    _HELD.add(name)
    try:
        yield lock
    finally:
        _HELD.discard(name)
        if lock:
            lock.release()
