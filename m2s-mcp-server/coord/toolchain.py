"""Pinned third-party coordination tools: br (beads_rust) and mcp_agent_mail_rust.

    python -m coord.toolchain install     # download, verify sha256, unpack
    python -m coord.toolchain status      # what is installed where

Versions and archive checksums are pinned here (PIPELINE_V3_PLAN.md W0.2);
an archive whose sha256 differs is rejected before it is unpacked. Tools are
unpacked to <root>/<name>-<version>/, root defaulting to D:\\m2s-tools
(override with M2S_TOOLS_ROOT). The root is kept short on purpose: agent_mail's
git archive fails on long Windows paths.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

DEFAULT_ROOT = Path(r"D:\m2s-tools")


@dataclass(frozen=True)
class PinnedTool:
    name: str
    version: str
    url: str
    sha256: str
    executables: tuple[str, ...]

    @property
    def archive_name(self) -> str:
        return self.url.rsplit("/", 1)[-1]


# Windows x86_64 release archives, checked 2026-10-30.
PINNED: dict[str, PinnedTool] = {
    "br": PinnedTool(
        name="br",
        version="0.7.6",
        url="https://github.com/Dicklesworthstone/beads_rust/releases/download/v0.7.6/br-0.7.6-windows_amd64.zip",
        sha256="ae1a33be770d535ee509cff5e8e735ab43fa315f70f391eb25c1ed0bd7fc2484",
        executables=("br.exe",),
    ),
    "agent_mail": PinnedTool(
        name="agent_mail",
        version="0.3.39",
        url=(
            "https://github.com/Dicklesworthstone/mcp_agent_mail_rust/releases/download/v0.3.39/"
            "mcp-agent-mail-x86_64-pc-windows-msvc.zip"
        ),
        sha256="8486db661b7e098eaddc61de812468bf819dd01b1825018e4c420f61e7a8b414",
        executables=("mcp-agent-mail.exe", "am.exe"),
    ),
}


class ToolchainError(RuntimeError):
    pass


def tools_root() -> Path:
    return Path(os.environ.get("M2S_TOOLS_ROOT") or DEFAULT_ROOT)


def install_dir(tool: PinnedTool, root: Path | None = None) -> Path:
    return (root or tools_root()) / f"{tool.name}-{tool.version}"


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def exe_path(name: str, executable: str | None = None, root: Path | None = None) -> Path:
    """Absolute path of an installed executable; raises if the tool is not installed."""
    tool = PINNED[name]
    exe = executable or tool.executables[0]
    path = install_dir(tool, root) / exe
    if not path.is_file():
        raise ToolchainError(f"{name} {tool.version} is not installed ({path} missing); run `python -m coord.toolchain install`")
    return path


def _download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(url, timeout=120) as response, partial.open("wb") as fh:
        shutil.copyfileobj(response, fh)
    partial.replace(dest)


def install(name: str, root: Path | None = None, archive: Path | None = None) -> Path:
    """Install one pinned tool and return its directory. Idempotent.

    ``archive`` uses an already downloaded file instead of the URL; it is
    checked against the pinned sha256 like a download.
    """
    tool = PINNED[name]
    root = root or tools_root()
    target = install_dir(tool, root)
    marker = target / ".installed.json"
    if marker.is_file() and all((target / exe).is_file() for exe in tool.executables):
        return target

    if archive is None:
        archive = root / "_downloads" / tool.archive_name
        if not archive.is_file():
            _download(tool.url, archive)
    actual = sha256_of(archive)
    if actual != tool.sha256:
        raise ToolchainError(f"{archive}: sha256 {actual} does not match pinned {tool.sha256} for {name} {tool.version}")

    staging = target.with_name(target.name + ".staging")
    shutil.rmtree(staging, ignore_errors=True)
    with zipfile.ZipFile(archive) as zf:
        for member in zf.namelist():
            # Refuse absolute paths and ".." entries before extracting anything.
            resolved = (staging / member).resolve()
            if staging.resolve() not in resolved.parents and resolved != staging.resolve():
                raise ToolchainError(f"{archive}: unsafe member path {member!r}")
        zf.extractall(staging)
    missing = [exe for exe in tool.executables if not (staging / exe).is_file()]
    if missing:
        shutil.rmtree(staging, ignore_errors=True)
        raise ToolchainError(f"{archive}: archive lacks {missing}")
    (staging / ".installed.json").write_text(
        json.dumps({"name": tool.name, "version": tool.version, "sha256": tool.sha256, "url": tool.url}, indent=2),
        encoding="utf-8",
    )
    shutil.rmtree(target, ignore_errors=True)
    staging.replace(target)
    return target


def installed_versions(root: Path | None = None) -> dict[str, dict[str, str | None]]:
    """Pinned version and install state of every tool (used by the harness stamp)."""
    out: dict[str, dict[str, str | None]] = {}
    for name, tool in PINNED.items():
        target = install_dir(tool, root)
        marker = target / ".installed.json"
        out[name] = {
            "version": tool.version,
            "sha256": tool.sha256,
            "path": str(target) if marker.is_file() else None,
        }
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m coord.toolchain")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_install = sub.add_parser("install", help="download, verify and unpack the pinned tools")
    p_install.add_argument("names", nargs="*", default=sorted(PINNED))
    p_install.add_argument("--archive", action="append", default=[], metavar="NAME=PATH",
                           help="use a local archive for NAME instead of downloading")
    sub.add_parser("status", help="show pinned versions and install paths")
    args = parser.parse_args(argv)

    if args.cmd == "status":
        print(json.dumps({"root": str(tools_root()), "tools": installed_versions()}, indent=2))
        return 0

    archives = dict(item.split("=", 1) for item in args.archive)
    for name in args.names:
        if name not in PINNED:
            parser.error(f"unknown tool {name!r}; pinned: {sorted(PINNED)}")
        local = Path(archives[name]) if name in archives else None
        print(f"{name} {PINNED[name].version} -> {install(name, archive=local)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
