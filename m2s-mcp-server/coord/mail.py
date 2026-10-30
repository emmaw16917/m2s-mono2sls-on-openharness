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

    def start(self, timeout_s: float = 30.0) -> None:
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
        self.process = subprocess.Popen(
            [str(exe_path("agent_mail")), "serve", "--host", _LOCALHOST, "--port", str(self.port), "--no-tui"],
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
