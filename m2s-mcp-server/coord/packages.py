"""Work packages: the deterministic split of a monolith's endpoints (v3 M2/W2.4).

PIPELINE_V3_PLAN.md §4.9 and PIPELINE_V3_EXPLORATION.md §3.6 define the rules.
Grouping only affects efficiency and annotation consistency; correctness is
checked per endpoint and by the global judge, so a heuristic is fine as long as
it is deterministic and every decision is recorded in `reasons` / `merge_log`.

Rules implemented here (1-6, 8):
 1. operational endpoints (status/health/check/metrics/config/loader/admin/
    stream, no login, no business writes) become one package;
 2. login/registration/session endpoints become one identity package;
 3. remaining business endpoints start as URL-first-segment groups and merge
    through affinity: a shared non-hub table, helper, or file, or a request /
    path field that names another group's entity;
 4. packages are sized by an estimated line count (handler + reachable
    helpers); oversize groups are split by weakest affinity, unconnected small
    groups are folded into the smallest neighbour;
 5. code used by 2+ packages is listed as shared_code (one F2:shared task);
 6. an application with fewer than 10 endpoints gets at most two packages;
 8. `impl_packages` stays null here; S1 fills it from the blueprint's domains.

Rule 7 (regroup after J3, using per-case coverage) needs J2 coverage data and
is not part of W2.4.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

RULES_VERSION = "v3-2026-10-30"
LOC_BUDGET = 1200          # one worker's slice of the monolith
LOC_MIN = 120              # below this a group is folded into its neighbour
SMALL_APP_ENDPOINTS = 10   # rule 6

OPS_WORDS = ("status", "health", "check", "metrics", "config", "loader", "admin", "stream")
IDENTITY_SEGMENTS = {"login", "logout", "register", "auth", "session", "sessions",
                     "token", "tokenid", "me", "users"}
AUTH_FILE_RE = re.compile(r"(?:^|/)(?:auth|authservice|sessions?|tokens?)(?:[._/-]|$)", re.I)
WRITE_OPS = re.compile(r"^(put|update|delete|insert|remove|save|create|replace|batch_writer|transact)", re.I)
# SDK / data-access method names are shared by nearly every handler; they say
# nothing about business affinity and are not a function to annotate once.
GENERIC_HELPERS = {
    "get", "put", "update", "delete", "query", "scan", "send", "getitem", "putitem",
    "updateitem", "deleteitem", "batchgetitem", "batchget", "batchwrite",
    "transactwriteitems", "transactgetitems", "find", "findone", "insertone", "insertmany",
    "updateone", "updatemany", "deleteone", "deletemany", "aggregate", "count", "distinct",
    "replaceone", "findoneandupdate", "findoneanddelete", "save", "remove", "create",
    "destroy", "bulkwrite", "insert", "updatebyid", "deletebyid", "execute", "fetchall",
    "fetchone", "commit", "rollback",
}
PREFIX_SEGMENTS = {"api", "rest", "v1", "v2"}


def _generic(helper: tuple[str, str]) -> bool:
    return helper[1].lower() in GENERIC_HELPERS


def _segments(path: str) -> list[str]:
    return [s for s in re.split(r"[/<>]", path) if s and not s.startswith(":") and not s.startswith("<")]


def _singular(word: str) -> str:
    w = word.lower()
    if w.endswith("ies") and len(w) > 3:
        return w[:-3] + "y"
    if w.endswith("s") and not w.endswith("ss"):
        return w[:-1]
    return w


def _tokens(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


@dataclass
class Endpoint:
    method: str
    path: str
    file: str
    handler_start: int
    handler_end: int
    auth: str
    tables: set[str] = field(default_factory=set)
    # (resolved file or module spec, function name)
    helpers: set[tuple[str, str]] = field(default_factory=set)
    fields: set[str] = field(default_factory=set)
    loc: int = 0

    @property
    def key(self) -> tuple[str, str]:
        return (self.method, self.path)

    @property
    def handler_loc(self) -> int:
        return max(1, self.handler_end - self.handler_start + 1)


def _table_name(item: dict[str, Any]) -> str | None:
    table = item.get("table") or {}
    return table.get("default_name") or table.get("config_key") or None


def _writes(raw: dict[str, Any]) -> bool:
    for item in raw.get("data_access") or []:
        if WRITE_OPS.match(str(item.get("operation") or "")):
            return True
    return False


def _resolve_module(handler_file: str, spec: str) -> str | None:
    """`../utils/dynamodb` from bookstore/routes/auth.js -> bookstore/utils/dynamodb.js"""
    if not spec.startswith("."):
        return None
    parts = handler_file.split("/")
    base = parts[:-1] + spec.split("/")
    out: list[str] = []
    for part in base:
        if part == "..":
            if out:
                out.pop()
        elif part not in ("", "."):
            out.append(part)
    stem = "/".join(out)
    for cand in (f"{stem}.js", f"{stem}.ts", f"{stem}/index.js", f"{stem}/index.ts"):
        return cand
    return stem


def _endpoints(report: dict[str, Any], symbol_index: dict[tuple[str, str], int]) -> list[Endpoint]:
    deps: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for dep in report.get("entry_point_dependencies") or []:
        ep = dep.get("entry_point") or {}
        deps[(ep.get("method"), ep.get("path"))] = dep.get("cross_file_calls") or []
    symbol_files: dict[str, list[str]] = {}
    for (file, name) in symbol_index:
        symbol_files.setdefault(name, []).append(file)
    out = []
    for raw in report.get("entry_points") or []:
        hl = raw.get("handler_location") or {}
        if not hl.get("file"):
            continue
        request = raw.get("request") or {}
        fields = {f.get("name") for f in request.get("body") or [] if f.get("name")}
        fields |= {p.get("name") for p in request.get("path_params") or [] if p.get("name")}
        files = {hl["file"]}
        helpers: set[tuple[str, str]] = set()
        for call in deps.get((raw.get("method"), raw.get("path")), []):
            symbol = call.get("callee_symbol") or ""
            name = symbol.rsplit(".", 1)[-1]
            module = call.get("callee_module") or ""
            resolved = _resolve_module(hl["file"], module) or module
            if name:
                helpers.add((resolved, name))
            if resolved:
                files.add(resolved)
        auth = raw.get("auth") or {}
        auth_files = {s.get("symbol"): s.get("file") for s in auth.get("source") or [] if isinstance(s, dict)}
        for name in auth.get("middleware") or []:
            short = name.rsplit(".", 1)[-1]
            candidates = sorted(symbol_files.get(short) or [])
            file = next((f for f in candidates if "middleware" in f.lower()),
                        candidates[0] if candidates else "")
            file = file or auth_files.get(name) or auth_files.get(short) or ""
            helpers.add((file, short))
        ep = Endpoint(
            method=raw.get("method") or "", path=raw.get("path") or "", file=hl["file"],
            handler_start=int(hl.get("start_line") or 0), handler_end=int(hl.get("end_line") or 0),
            auth=((raw.get("auth") or {}).get("classification") or "none"),
            tables={n for n in (_table_name(d) for d in raw.get("data_access") or []) if n},
            helpers=helpers, fields={str(f) for f in fields},
        )
        ep.loc = ep.handler_loc + sum(
            size for (file, name), size in symbol_index.items()
            if file in files and (file, name) in ep.helpers)
        out.append(ep)
    return out


def _is_ops(ep: Endpoint, raw: dict[str, Any]) -> bool:
    segments = [s for s in _segments(ep.path) if s.lower() not in PREFIX_SEGMENTS]
    first = segments[0].lower() if segments else ""
    word = any(word in first for word in OPS_WORDS)
    return bool(word) and ep.auth != "required" and not _writes(raw)


def _is_identity(ep: Endpoint) -> bool:
    if any(seg.lower() in IDENTITY_SEGMENTS for seg in _segments(ep.path)):
        return True
    return bool(AUTH_FILE_RE.search(ep.file))


def _common_prefix(paths: list[list[str]]) -> list[str]:
    if not paths:
        return []
    prefix = list(paths[0])
    for segs in paths[1:]:
        i = 0
        while i < len(prefix) and i < len(segs) and prefix[i] == segs[i]:
            i += 1
        prefix = prefix[:i]
        if not prefix:
            break
    return prefix


def _affinities(eps: list[Endpoint]):
    """Non-hub tables, helpers and files, with the endpoints that use each."""
    n = len(eps)
    tables: dict[str, set[tuple[str, str]]] = {}
    helpers: dict[tuple[str, str], set[tuple[str, str]]] = {}
    files: dict[str, set[tuple[str, str]]] = {}
    for ep in eps:
        for table in ep.tables:
            tables.setdefault(table, set()).add(ep.key)
        for helper in ep.helpers:
            if _generic(helper):
                continue
            helpers.setdefault(helper, set()).add(ep.key)
        files.setdefault(ep.file, set()).add(ep.key)
    half = n / 2
    hubs = {
        "tables": sorted(t for t, users in tables.items() if len(users) > half),
        "files": sorted(f for f, users in files.items() if len(users) > half),
        "functions": sorted(f"{fn} ({f})" for (f, fn), users in helpers.items() if len(users) > half),
    }
    return tables, helpers, files, hubs


def _group_affinity(a: str, b: str, groups: dict[str, list[Endpoint]], members: dict[str, list[str]],
                    tables: dict[str, set[tuple[str, str]]], helpers: dict[tuple[str, str], set[tuple[str, str]]],
                    files: dict[str, set[tuple[str, str]]], hubs: dict[str, list[str]]) -> str | None:
    """Why two groups should merge, or None. Rule 3."""
    keys_a = {e.key for e in groups[a]}
    keys_b = {e.key for e in groups[b]}
    for table, users in sorted(tables.items()):
        if table in hubs["tables"]:
            continue
        if users & keys_a and users & keys_b:
            return f"shares table {table} with {b}"
    for helper, users in sorted(helpers.items()):
        if f"{helper[1]} ({helper[0]})" in hubs["functions"]:
            continue
        if users & keys_a and users & keys_b:
            return f"shares helper {helper[1]} with {b}"
    for file, users in sorted(files.items()):
        if file in hubs["files"]:
            continue
        if users & keys_a and users & keys_b:
            return f"shares file {file} with {b}"
    tokens_a = {_singular(part) for name in members[a] for part in name.split("-") if part}
    tokens_b = {_singular(part) for name in members[b] for part in name.split("-") if part}
    for group, tokens, other, other_name in ((a, tokens_a, b, b), (b, tokens_b, a, a)):
        for ep in groups[other]:
            field_text = " ".join(_tokens(f) for f in ep.fields)
            for token in sorted(tokens):
                if len(token) >= 3 and token in field_text:
                    return f"{other_name} field references {token} of {group}"
    return None


def _merge(packages: dict[str, list[Endpoint]], members: dict[str, list[str]],
           tables, helpers, files, hubs):
    log: list[dict[str, Any]] = []
    changed = True
    while changed:
        changed = False
        ids = sorted(packages)
        for i, a in enumerate(ids):
            for b in ids[i + 1:]:
                if b not in packages or a not in packages:
                    continue
                why = _group_affinity(a, b, packages, members, tables, helpers, files, hubs)
                if not why:
                    continue
                packages[a] = packages[a] + packages.pop(b)
                members[a] = sorted(members[a] + members.pop(b))
                log.append({"rule": 3, "merged": [a, b], "why": why})
                changed = True
                break
            if changed:
                break
    return packages, members, log


def _package_id(keys: list[str]) -> str:
    return "-".join(sorted(k.lower() for k in keys))


def _split_oversize(name: str, eps: list[Endpoint], budget: int):
    """Rule 4: peel the least-connected endpoint off until each group fits."""
    total = sum(e.loc for e in eps)
    if total <= budget or len(eps) <= 1:
        return [(name, eps, [])]
    groups = [list(eps)]
    log: list[dict[str, Any]] = []
    while any(sum(e.loc for e in g) > budget and len(g) > 1 for g in groups):
        for g in groups:
            if sum(e.loc for e in g) <= budget or len(g) <= 1:
                continue
            shared: dict[tuple[str, str], int] = {e.key: 0 for e in g}
            for e in g:
                for other in g:
                    if other is e:
                        continue
                    shared_helpers = (e.helpers & other.helpers) - {h for h in e.helpers if _generic(h)}
                    if e.tables & other.tables or shared_helpers or e.file == other.file:
                        shared[e.key] += 1
            victim = sorted(g, key=lambda e: (shared[e.key], -e.loc, e.path))[0]
            g.remove(victim)
            groups.append([victim])
            log.append({"rule": 4, "split": name, "endpoint": f"{victim.method} {victim.path}", "why": "least connected"})
            break
    groups.sort(key=lambda g: (-sum(e.loc for e in g), sorted(e.path for e in g)))
    if len(groups) == 1:
        return [(name, groups[0], log)]
    return [(f"{name}-{i + 1}", g, log if i == 0 else []) for i, g in enumerate(groups)]


def plan(report: dict[str, Any], symbol_table: list[dict[str, Any]] | None = None,
         report_sha256: str | None = None) -> dict[str, Any]:
    """The deterministic work-package plan for one analysis report."""
    symbol_index: dict[tuple[str, str], int] = {}
    for sym in symbol_table or []:
        file = sym.get("file_path")
        name = (sym.get("id") or "").rsplit(".", 1)[-1]
        if file and name:
            symbol_index[(file, name)] = max(1, int(sym.get("end_line", 0)) - int(sym.get("start_line", 0)) + 1)

    raw_by_key = {(e.get("method"), e.get("path")): e for e in report.get("entry_points") or []}
    endpoints = _endpoints(report, symbol_index)
    ops = [e for e in endpoints if _is_ops(e, raw_by_key.get(e.key, {}))]
    ops_keys = {e.key for e in ops}
    identity = [e for e in endpoints if e.key not in ops_keys and _is_identity(e)]
    identity_keys = {e.key for e in identity}
    business = [e for e in endpoints if e.key not in ops_keys and e.key not in identity_keys]

    packages: list[dict[str, Any]] = []
    merge_log: list[dict[str, Any]] = []

    def add(name: str, kind: str, eps: list[Endpoint], why: list[str]) -> None:
        if not eps:
            return
        packages.append({
            "id": name, "kind": kind,
            "endpoints": [{"method": e.method, "path": e.path} for e in sorted(eps, key=lambda x: (x.path, x.method))],
            "files": sorted({e.file for e in eps}),
            "tables": sorted({t for e in eps for t in e.tables}),
            "loc_estimate": sum(e.loc for e in eps),
            "reasons": why,
        })

    add("ops", "ops", ops, ["rule1: operational endpoints, no login, no business writes"])
    add("identity", "identity", identity, ["rule2: login/registration/session endpoints"])

    hubs: dict[str, list[str]] = {"tables": [], "files": [], "functions": []}
    if business:
        prefixes = _common_prefix([_segments(e.path) for e in business])
        groups: dict[str, list[Endpoint]] = {}
        for e in business:
            segs = _segments(e.path)
            rest = segs[len(prefixes):] if segs[:len(prefixes)] == prefixes else segs
            key = (rest[0] if rest else "root")
            groups.setdefault(key, []).append(e)
        members = {key: [key] for key in groups}

        if len(endpoints) < SMALL_APP_ENDPOINTS:
            add("app", "app", business, ["rule6: fewer than 10 endpoints, no further split"])
        else:
            tables, helpers, files, hubs = _affinities(business)
            groups, members, log = _merge(groups, members, tables, helpers, files, hubs)
            merge_log.extend(log)
            for key in sorted(groups):
                eps = groups[key]
                if key not in groups or len(groups) == 1:
                    continue
                if len(eps) != 1 or sum(e.loc for e in eps) >= LOC_MIN:
                    continue
                if any(_group_affinity(key, other, groups, members, tables, helpers, files, hubs)
                       for other in groups if other != key):
                    continue
                others = [(k, sum(e.loc for e in v)) for k, v in groups.items() if k != key]
                if not others:
                    continue
                target = sorted(others, key=lambda kv: (kv[1], kv[0]))[0][0]
                groups[target] = groups[target] + groups.pop(key)
                members[target] = sorted(members[target] + members.pop(key))
                merge_log.append({"rule": 4, "merged": [key, target],
                                  "why": f"a single endpoint with no affinity and below {LOC_MIN} lines"})
            for key in sorted(groups):
                for name, eps, split_log in _split_oversize(key, groups[key], LOC_BUDGET):
                    merge_log.extend(split_log)
                    add(_package_id(members[key] if len(groups[key]) == len(eps) else [name]),
                        "domain", eps, [f"rule3: URL group {key}"])

    # rule 5: code shared by two or more packages
    by_key: dict[tuple[str, str], str] = {}
    for pkg in packages:
        for ep in pkg["endpoints"]:
            by_key[(ep["method"], ep["path"])] = pkg["id"]
    shared: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for ep in endpoints:
        pkg = by_key.get(ep.key)
        if not pkg:
            continue
        for helper in sorted(ep.helpers):
            if _generic(helper):
                continue
            if (pkg, f"{helper[0]}:{helper[1]}") in seen:
                continue
            users = sorted({by_key[e.key] for e in endpoints
                            if helper in e.helpers and e.key in by_key})
            if len(users) >= 2:
                shared.append({"file": helper[0], "function": helper[1], "used_by": users})
                seen.update((u, f"{helper[0]}:{helper[1]}") for u in users)
    shared = _dedupe_shared(shared)

    all_ids = [p["id"] for p in packages]
    self_check = {
        "every_endpoint_once": sorted(f"{e.method} {e.path}" for e in endpoints)
        == sorted(f"{ep['method']} {ep['path']}" for p in packages for ep in p["endpoints"]),
        "package_count": len(packages),
        "over_budget": [p["id"] for p in packages if p["loc_estimate"] > LOC_BUDGET],
        "duplicate_ids": sorted({i for i in all_ids if all_ids.count(i) > 1}),
    }
    return {
        "version": 1,
        "rules_version": RULES_VERSION,
        "source": {"analysis_report_sha256": report_sha256, "footprint": None},
        "packages": packages,
        "shared_code": shared,
        "hubs": hubs,
        "impl_packages": None,
        "merge_log": merge_log,
        "self_check": self_check,
    }


def _dedupe_shared(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[tuple[str, str], set[str]] = {}
    for item in items:
        key = (item["file"], item["function"])
        merged.setdefault(key, set()).update(item["used_by"])
    return [{"file": f, "function": fn, "used_by": sorted(u)} for (f, fn), u in sorted(merged.items())]


def write(ws: str | Path) -> dict[str, Any]:
    """Plan from <ws>/storage/analysis_report.json; write storage/work_packages.json."""
    ws = Path(ws).resolve()
    report_path = ws / "storage" / "analysis_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    symbol_path = ws / "storage" / "symbol_table.json"
    symbols = json.loads(symbol_path.read_text(encoding="utf-8")) if symbol_path.exists() else []
    result = plan(report, symbols, hashlib.sha256(report_path.read_bytes()).hexdigest())
    out = ws / "storage" / "work_packages.json"
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Plan work packages for a workspace")
    parser.add_argument("--ws", required=True)
    args = parser.parse_args(argv)
    result = write(args.ws)
    print(json.dumps({"packages": [(p["id"], len(p["endpoints"]), p["loc_estimate"]) for p in result["packages"]],
                      "self_check": result["self_check"]}, ensure_ascii=False, indent=2))
    return 0 if result["self_check"]["every_endpoint_once"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
