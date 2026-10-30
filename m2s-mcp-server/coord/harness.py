"""Harness version stamp (PIPELINE_V3_PLAN.md W0.6).

    python -m coord.harness            # print the stamp as JSON

The stamp is the first event of every v3 run's event log, so a result can
be traced to the exact harness that produced it:
- the commits of this repository and of OpenHarness, with any uncommitted
  changes to the harness paths;
- the pinned br / agent_mail versions;
- the model each agent role will use;
- sha256 of the agent definitions and project skills (the prompts).

No secrets are recorded: from the OpenHarness settings only the model,
profile, provider and base URL are read.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import yaml

from coord.toolchain import installed_versions

REPO = Path(__file__).resolve().parents[2]
OPENHARNESS_REPO = REPO / "OpenHarness"
# Paths whose uncommitted edits change the harness (same scope as the v1 TOOLS gate).
HARNESS_PATHS = ("m2s-mcp-server", ".openharness")
# Written by S5's update_ledger during runs; not a harness change.
IGNORED_DIRTY = {"m2s-mcp-server/perf-rules/ledger.json"}


def _git(repo: Path, *args: str, strip: bool = True) -> str | None:
    try:
        proc = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() if strip else proc.stdout


def _repo_state(repo: Path, paths: tuple[str, ...] = ()) -> dict[str, Any]:
    # Porcelain lines are "XY <path>"; X may be a space, so the output must not be stripped.
    status = _git(repo, "status", "--porcelain", "--", *paths, strip=False)
    dirty = []
    for line in (status or "").splitlines():
        path = line[3:].strip().strip('"').replace("\\", "/")
        if path and path not in IGNORED_DIRTY:
            dirty.append(path)
    return {
        "commit": _git(repo, "rev-parse", "HEAD"),
        "branch": _git(repo, "rev-parse", "--abbrev-ref", "HEAD"),
        "dirty": sorted(dirty),
    }


def _config_dir() -> Path:
    return Path(os.environ.get("OPENHARNESS_CONFIG_DIR") or Path.home() / ".openharness")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _tree_sha256(root: Path) -> str | None:
    """One hash over every file under root (relative path + content), order-independent."""
    if not root.is_dir():
        return None
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file() and "__pycache__" not in p.parts):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _frontmatter(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8", errors="replace")
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    try:
        data = yaml.safe_load(text[3:end])
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


def _model_settings(config_dir: Path) -> dict[str, Any]:
    path = config_dir / "settings.json"
    try:
        settings = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"model": None, "active_profile": None}
    profile_name = settings.get("active_profile")
    profile = (settings.get("profiles") or {}).get(profile_name) or {}
    return {
        "model": settings.get("model"),
        "active_profile": profile_name,
        "provider": profile.get("provider") or settings.get("provider"),
        "base_url": profile.get("base_url") or settings.get("base_url"),
    }


def _agents(config_dir: Path, default_model: str | None) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for path in sorted((config_dir / "agents").glob("*.md")):
        meta = _frontmatter(path)
        declared = meta.get("model")
        out[str(meta.get("name") or path.stem)] = {
            "file": path.name,
            "sha256": _sha256(path),
            "model": declared,
            "effective_model": default_model if declared in (None, "", "inherit") else declared,
        }
    return out


def stamp() -> dict[str, Any]:
    config_dir = _config_dir()
    models = _model_settings(config_dir)
    return {
        "type": "harness",
        "time": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "m2s": _repo_state(REPO, HARNESS_PATHS),
        "openharness": _repo_state(OPENHARNESS_REPO),
        "tools": {name: {"version": info["version"], "installed": info["path"] is not None}
                  for name, info in installed_versions().items()},
        "models": models,
        "agents": _agents(config_dir, models.get("model")),
        "prompts": {
            "project_skills_sha256": _tree_sha256(REPO / ".openharness" / "skills"),
            "user_skills_sha256": _tree_sha256(config_dir / "skills"),
        },
        "python": {"coord": sys.executable, "version": sys.version.split()[0]},
    }


def is_clean(harness: dict[str, Any]) -> bool:
    """True when no harness path has uncommitted changes in either repository."""
    return not harness["m2s"]["dirty"] and not harness["openharness"]["dirty"]


def main() -> int:
    print(json.dumps(stamp(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
