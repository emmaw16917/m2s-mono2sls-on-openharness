"""mcp_agent_mail_rust as one shared HTTP server (PIPELINE_V3_PLAN.md §4.7).

A storage root may be owned by a single agent_mail process (a second one is
refused), so all clients share one HTTP server instead of each spawning a
stdio server. Windows gotchas, verified 2026-10-30:
- the storage directory must exist before the server starts;
- the bearer token must be at least 8 characters;
- deep paths break agent_mail's git archive, so keep the root short;
- localhost must bypass any HTTP proxy (NO_PROXY).
"""
from __future__ import annotations

import json
import os
import secrets
import socket
import subprocess
import time
import urllib.request
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

from coord.toolchain import exe_path

_LOCALHOST = "127.0.0.1"
# §4.7: one agent_mail server per machine; every workspace uses a project on it.
DEFAULT_MAIL_ROOT = Path(os.environ.get("M2S_MAIL_ROOT") or ("D:/m2s-am" if os.name == "nt" else "/tmp/m2s-am"))
DEFAULT_MAIL_PORT = int(os.environ.get("M2S_MAIL_PORT") or "8765")


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind((_LOCALHOST, 0))
        return sock.getsockname()[1]


def _bypass_proxy_for_localhost() -> None:
    for key in ("NO_PROXY", "no_proxy"):
        hosts = [h for h in os.environ.get(key, "").split(",") if h]
        for host in (_LOCALHOST, "localhost"):
            if host not in hosts:
                hosts.append(host)
        os.environ[key] = ",".join(hosts)


class AgentMailServer:
    """Start (or stop) an agent_mail HTTP server on a storage root."""

    def __init__(self, storage_root: Path, port: int | None = None, token: str | None = None):
        self.storage_root = Path(storage_root)
        self.port = port or _free_port()
        self.token = token or secrets.token_urlsafe(24)
        self.process: subprocess.Popen | None = None
        self.log_path = self.storage_root / "server.log"
        self._log = None

    @property
    def url(self) -> str:
        return f"http://{_LOCALHOST}:{self.port}/mcp/"

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    def start(self, timeout_s: float = 30.0, reuse: bool = False) -> None:
        _bypass_proxy_for_localhost()
        self.storage_root.mkdir(parents=True, exist_ok=True)
        env = {
            **os.environ,
            "STORAGE_ROOT": str(self.storage_root),
            "HTTP_BEARER_TOKEN": self.token,
            "WORKTREES_ENABLED": "true",
            "LOG_LEVEL": "warn",
            "TUI_ENABLED": "false",
        }
        self._log = self.log_path.open("w", encoding="utf-8")
        argv = [str(exe_path("agent_mail")), "serve", "--host", _LOCALHOST, "--port", str(self.port), "--no-tui"]
        if reuse:
            argv.append("--reuse-running")
        self.process = subprocess.Popen(
            argv,
            env=env,
            stdout=self._log,
            stderr=subprocess.STDOUT,
            cwd=self.storage_root,
        )
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError(f"agent_mail exited early (rc={self.process.returncode}); see {self.log_path}")
            try:
                urllib.request.urlopen(f"http://{_LOCALHOST}:{self.port}/healthz", timeout=2)
                return
            except OSError:
                time.sleep(0.5)
        self.stop()
        raise RuntimeError(f"agent_mail did not become healthy within {timeout_s}s; see {self.log_path}")

    def stop(self) -> None:
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(10)
        self.process = None
        if self._log is not None:
            self._log.close()
            self._log = None


class MailClient:
    """Call agent_mail tools over streamable HTTP; tool results are decoded from JSON."""

    def __init__(self, session: Any):
        self._session = session

    async def call(self, tool: str, **arguments: Any) -> Any:
        result = await self._session.call_tool(tool, arguments)
        text = "".join(getattr(block, "text", "") for block in result.content)
        if getattr(result, "isError", False):
            raise RuntimeError(f"agent_mail {tool} failed: {text}")
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text


@asynccontextmanager
async def connect(server: AgentMailServer) -> AsyncIterator[MailClient]:
    import httpx
    from mcp import ClientSession
    from mcp.client import streamable_http

    # mcp >= 1.30 deprecates streamablehttp_client(headers=...) in favour of
    # streamable_http_client(http_client=...); mono2sls still has 1.28.
    if hasattr(streamable_http, "streamable_http_client"):
        async with httpx.AsyncClient(headers=server.headers) as http_client:
            async with streamable_http.streamable_http_client(server.url, http_client=http_client) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    yield MailClient(session)
    else:
        async with streamable_http.streamablehttp_client(server.url, headers=server.headers) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield MailClient(session)


# ---------------------------------------------------------------------------
# Shared server (W3.1): one process per machine, reused across workspaces
# ---------------------------------------------------------------------------

def _read_token(root: Path) -> str:
    root.mkdir(parents=True, exist_ok=True)
    path = root / "token"
    if path.is_file():
        token = path.read_text(encoding="utf-8").strip()
        if len(token) >= 8:
            return token
    token = secrets.token_urlsafe(24)
    path.write_text(token, encoding="utf-8")  # agent_mail wants >= 8 characters
    return token


def _healthy(port: int) -> bool:
    try:
        urllib.request.urlopen(f"http://{_LOCALHOST}:{port}/healthz", timeout=1)
        return True
    except OSError:
        return False


def ensure_server(root: Path | None = None, port: int | None = None) -> AgentMailServer:
    """Return the machine's agent_mail server, starting it if it is not running."""
    root = Path(root or DEFAULT_MAIL_ROOT)
    port = int(port or DEFAULT_MAIL_PORT)
    server = AgentMailServer(root, port, _read_token(root))
    if _healthy(port):
        return server
    server.start(reuse=True)
    return server


def mail_env(server: AgentMailServer, ws: Path | str) -> dict[str, str]:
    """Environment for the worker's m2s-server: where the proxy posts messages."""
    return {"M2S_MAIL_URL": server.url, "M2S_MAIL_TOKEN": server.token,
            "M2S_MAIL_PROJECT": str(Path(ws).resolve())}


async def _call(server: AgentMailServer, tool: str, **arguments: Any) -> Any:
    async with connect(server) as client:
        return await client.call(tool, **arguments)


def call_sync(server: AgentMailServer, tool: str, **arguments: Any) -> Any:
    import asyncio
    import concurrent.futures

    def run() -> Any:
        return asyncio.run(_call(server, tool, **arguments))

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return run()
    # v3 M3: m2s-server runs its sync tools on the event loop thread, and the
    # room/notice tools mirror from there. asyncio.run inside that loop raises
    # (and the mirror used to fail silently); give the call its own thread.
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(run).result(timeout=60)


def ensure_project(server: AgentMailServer, ws: Path | str) -> None:
    call_sync(server, "ensure_project", human_key=str(Path(ws).resolve()))
