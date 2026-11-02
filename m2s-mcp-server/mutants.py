"""J5 `run_mutants` (PIPELINE_V2_PLAN.md §4.10, N3.3): how strong is the judge?

Mutants of the monolith, each a small change the judge should notice, located
by the fact base's citations (the lines its responses, business rules and
authentication cite):

    status        a status code changed            res.status(400) -> 422; return ..., 400 -> 422
    negate        a condition negated              if (c) -> if (!(c)); if c: -> if not (c):
    branch_off    a branch never taken             if (c) -> if (false); if c: -> if False:
    field         a response field renamed         message: -> message_m2s: ; 'error': -> 'error_m2s':
    auth          an authentication check removed  app.use('/x', authMiddleware, r) -> app.use('/x', r);
                                                   @login_required -> (removed)
    drop_write    a write of the fact base's data access never applied (the recording
                  proxy answers it as done; language-independent)

and one reverse check the judge must NOT notice:

    serial        "parallel -> serial": the proxy takes one SDK request at a time, so
                  the calls the monolith makes concurrently take effect one after the
                  other. External effects are the same; a judge that notices compares
                  an implementation trace.

Source mutants keep the number of lines. Each runs on a copy of the monolith
(node_modules linked, not copied); a mutant runs only the judge's units that can
see it: the units whose coverage includes the mutated line (a line that only runs
at load time, e.g. mounting a middleware: the units calling the endpoints whose
facts cite it), for drop_write the units whose monolith calls include that write,
for serial every unit. One pass each, compared with the frozen expectations
(oracle_expected.json, fault_baseline_monolith.json) the way pass 3 of
run_monolith is checked.

A mutant is killed when some unit differs. Differences only in an error's stack
trace are counted apart ("trace_only", not a kill): the stack is the monolith's
own file paths and lines (§11 V2P-18). Mutants that do not start are "invalid";
mutants no unit can see are "not_covered" (survived without running).

Output: judge/judge_validation.json (mutants, outcome, killing unit and step,
score). judge/mutant_classes.json, written by the J5 verifier, marks survivors
as equivalent (excluded from the score) or gap (J4 adds a case).
"""

from __future__ import annotations

import json
import re
import shutil
import time
from pathlib import Path
from typing import Any

import judge
import verify

VALIDATION = "judge_validation.json"
CLASSES = "mutant_classes.json"
SOURCE_OPERATORS = ("status", "negate", "branch_off", "field", "auth")
STATUS_SWAP = {200: 201, 201: 200, 204: 200, 400: 422, 401: 403, 403: 401, 404: 410, 409: 400, 422: 400, 500: 503}
WRITE_OPS = {"put": "PutItem", "update": "UpdateItem", "delete": "DeleteItem", "batch_write": "BatchWriteItem",
             "transact_write": "TransactWriteItems"}
SKIP_DIRS = {"node_modules", ".git", "__pycache__", "venv", ".venv", "env", ".pytest_cache"}


def _swap(code: int) -> int:
    return STATUS_SWAP.get(code, 400 if code < 400 else 503 if code >= 500 else 422)


def _rel(root: Path, file: str) -> str | None:
    """A citation's file relative to the monolith root (citations start with the root's name)."""
    parts = file.replace("\\", "/").split("/")
    rel = "/".join(parts[1:]) if parts and parts[0] == root.name else "/".join(parts)
    if not rel or any(p in SKIP_DIRS or p == "site-packages" for p in rel.split("/")):
        return None
    return rel if (root / rel).is_file() else None


def _paren_span(line: str, start: int) -> int | None:
    """Index of the ')' matching the '(' at start, on this line."""
    depth, quote = 0, None
    for i in range(start, len(line)):
        ch = line[i]
        if quote:
            if ch == "\\":
                continue
            if ch == quote:
                quote = None
            continue
        if ch in "'\"`":
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return i
    return None


def mutate_line(line: str, operator: str, lang: str, fields: list[str] | None = None,
                middleware: list[str] | None = None) -> str | None:
    """The line with one operator applied, or None when it does not apply."""
    if operator == "status":
        if lang == "js":
            m = re.search(r"\.status\(\s*(\d{3})\s*\)", line) or re.search(r"\.sendStatus\(\s*(\d{3})\s*\)", line)
        else:
            m = re.search(r"^\s*return\s+.+,\s*(\d{3})\s*$", line) or re.search(r"\babort\(\s*(\d{3})", line) \
                or re.search(r"\bstatus(?:_code)?\s*=\s*(\d{3})", line)
        if not m:
            return None
        return line[:m.start(1)] + str(_swap(int(m.group(1)))) + line[m.end(1):]
    if operator in ("negate", "branch_off"):
        if lang == "js":
            m = re.search(r"\bif\s*\(", line)
            if not m:
                return None
            end = _paren_span(line, m.end() - 1)
            if end is None:
                return None
            cond = line[m.end():end]
            new = f"!({cond})" if operator == "negate" else "false"
            return line[:m.end()] + new + line[end:]
        m = re.match(r"^(\s*)(el)?if\s+(.+?):\s*(#.*)?$", line)
        if not m:
            return None
        cond = f"not ({m.group(3)})" if operator == "negate" else "False"
        return f"{m.group(1)}{m.group(2) or ''}if {cond}:"
    if operator == "field":
        for f in fields or []:
            if lang == "js":
                m = re.search(rf"(?<![.\w?]){re.escape(f)}\s*:", line)
                if m and "?" not in line[max(0, m.start() - 2):m.start()]:
                    return line[:m.start()] + f + "_m2s" + line[m.start() + len(f):]
            else:
                m = re.search(rf"(['\"]){re.escape(f)}\1\s*:", line)
                if m:
                    return line[:m.start() + 1] + f + "_m2s" + line[m.start() + 1 + len(f):]
        return None
    if operator == "auth":
        for mw in middleware or []:
            if lang == "js":
                new = re.sub(rf"\b{re.escape(mw)}\b\s*,\s*", "", line, count=1)
                if new == line:
                    new = re.sub(rf",\s*\b{re.escape(mw)}\b", "", line, count=1)
                if new != line:
                    return new
            elif re.match(rf"^\s*@{re.escape(mw)}\b", line):
                return ""
        if lang == "py" and re.match(r"^\s*@\w*(login|auth|admin|owner)\w*required\w*\s*(\(.*\))?\s*$", line):
            return ""
        return None
    raise ValueError(operator)


def generate(facts: judge.Facts) -> list[dict[str, Any]]:
    """Every mutant of the monolith the fact base locates."""
    root = Path(facts.monolith_root)
    sites: dict[tuple[str, int], dict[str, Any]] = {}

    def site(file: str, line: int) -> dict[str, Any] | None:
        rel = _rel(root, file)
        if rel is None or not isinstance(line, int):
            return None
        return sites.setdefault((rel, line), {"file": rel, "line": line, "refs": [], "fields": [], "middleware": [],
                                              "endpoints": set(), "auth": False})

    for name, ep in facts.entry_points.items():
        for part in ("responses", "business_rules"):
            for i, x in enumerate(ep.get(part) or []):
                if not isinstance(x, dict):
                    continue
                for s in x.get("source") or []:
                    st = site(s.get("file", ""), s.get("line"))
                    if st is None:
                        continue
                    st["refs"].append(f"{name}#{part}[{i}]")
                    st["endpoints"].add(name)
                    if part == "responses":
                        st["fields"] += [f for f in ((x.get("body") or {}).get("fields") or []) if isinstance(f, str)]
        auth = ep.get("auth") or {}
        for s in auth.get("source") or []:
            st = site(s.get("file", ""), s.get("line"))
            if st is None:
                continue
            st["refs"].append(f"{name}#auth")
            st["endpoints"].add(name)
            st["auth"] = True
            st["middleware"] += [m for m in auth.get("middleware") or [] if isinstance(m, str)]
    out, seen = [], set()
    lines_of: dict[str, list[str]] = {}
    for (rel, line), st in sorted(sites.items()):
        text = lines_of.setdefault(rel, (root / rel).read_text(encoding="utf-8", errors="replace").split("\n"))
        if not 1 <= line <= len(text):
            continue
        before = text[line - 1].rstrip("\r")
        lang = "py" if rel.endswith(".py") else "js"
        ops = [op for op in SOURCE_OPERATORS if op != "auth" or st["auth"]]
        for op in ops:
            after = mutate_line(before, op, lang, sorted(set(st["fields"])), sorted(set(st["middleware"])))
            if after is None or after == before or (rel, line, after) in seen:
                continue
            seen.add((rel, line, after))
            out.append({"id": f"{op}:{rel}:{line}", "kind": "source", "operator": op, "file": rel, "line": line,
                        "before": before.strip(), "after": after.strip(), "refs": sorted(set(st["refs"])),
                        "endpoints": sorted(st["endpoints"])})
    writes: dict[tuple[str, str], list[str]] = {}
    for name, ep in facts.entry_points.items():
        for i, d in enumerate(ep.get("data_access") or []):
            if not isinstance(d, dict):
                continue
            op = re.sub(r"_items?$", "", str(d.get("operation") or "").lower())
            table = (d.get("table") or {}).get("default_name")
            if op in WRITE_OPS and table in facts.tables:
                writes.setdefault((table, WRITE_OPS[op]), []).append(f"{name}#data_access[{i}]")
    for (table, op), refs in sorted(writes.items()):
        out.append({"id": f"drop_write:{table}:{op}", "kind": "drop_write", "operator": "drop_write", "table": table,
                    "op": op, "refs": refs})
    out.append({"id": "serial", "kind": "serial", "operator": "serial", "reverse": True,
                "note": "the proxy takes one SDK request at a time: must NOT be noticed"})
    return out


def _unit_coverage(jd: Path) -> dict[str, dict[str, set[int]]]:
    out = {}
    for p in sorted((jd / "coverage_monolith" / "units").glob("*.json")):
        uid = p.stem.replace("__", "/")
        out[uid] = {f: set(lines) for f, lines in json.loads(p.read_text(encoding="utf-8")).items()}
    return out


def affected_units(m: dict[str, Any], units: list[dict[str, Any]], coverage: dict[str, dict[str, set[int]]],
                   calls: dict[str, list[dict[str, Any]]], facts: judge.Facts) -> list[str]:
    ids = [u["id"] for u in units]
    if m["kind"] == "serial":
        return ids
    if m["kind"] == "drop_write":
        return [uid for uid in ids if any(c.get("op") == m["op"] and (c.get("table") == m["table"]
                                                                      or m["table"] in (c.get("tables") or []))
                                          for c in calls.get(uid) or [])]
    hit = [uid for uid in ids if m["line"] in (coverage.get(uid) or {}).get(m["file"], set())]
    if hit:
        return hit
    # A line no case runs: module-level code (e.g. mounting a middleware) runs at load
    # time for every case; the cases that call the endpoints citing it can see it.
    endpoints = set(m.get("endpoints") or [])
    eps = list(facts.entry_points)
    return [u["id"] for u in units
            if any(judge._step_endpoint(s, eps) in endpoints for s in u["steps"])] if endpoints else []


def _remove_copy(dst: Path) -> None:
    """Delete a mutant's copy: its node_modules link first (os.rmdir removes a
    junction itself, never the monolith's node_modules it points to)."""
    nm = dst / "node_modules"
    if nm.is_symlink() or getattr(nm, "is_junction", lambda: False)() or (nm.exists() and _is_junction(nm)):
        import os
        os.rmdir(nm)
    if dst.exists():
        shutil.rmtree(dst)


def _is_junction(p: Path) -> bool:
    import os
    import stat
    try:
        return bool(os.lstat(p).st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)
    except (OSError, AttributeError):
        return False


def _copy_root(src: Path, dst: Path) -> None:
    def ignore(d: str, names: list[str]) -> set[str]:
        return {n for n in names if n in SKIP_DIRS}
    _remove_copy(dst)
    shutil.copytree(src, dst, ignore=ignore)
    if (src / "node_modules").is_dir():
        import _winapi  # noqa: PLC0415 -- Windows: a junction needs no privilege
        _winapi.CreateJunction(str(src / "node_modules"), str(dst / "node_modules"))


def _apply(root: Path, m: dict[str, Any]) -> None:
    p = root / m["file"]
    text = p.read_text(encoding="utf-8", errors="replace")
    nl = "\r\n" if "\r\n" in text else "\n"
    lines = text.split(nl)
    old = lines[m["line"] - 1]
    indent = re.match(r"^\s*", old).group(0)
    lines[m["line"] - 1] = indent + m["after"] if m["after"] else ""
    p.write_text(nl.join(lines), encoding="utf-8")


def _trace_only(mism: list[dict[str, Any]]) -> bool:
    return bool(mism) and all(re.search(r"\.stack$", str(x.get("path", ""))) for x in mism)


def run_mutants(ws: Path | str, operators: list[str] | None = None, limit: int | None = None,
                timeout_s: int = 7200) -> dict[str, Any]:
    """J5 on the judge of `ws` (the monolith's expectations must exist: J2 first)."""
    ws = Path(ws).resolve()
    jd = judge.judge_dir(ws)
    facts = judge.Facts(ws)
    for need in ("oracle_expected.json", "fault_baseline_monolith.json", "masks.json", "monolith_calls.json",
                 "coverage_monolith/summary.json"):
        if not (jd / need).is_file():
            raise judge.JudgeError(f"judge/{need} not found: run_monolith (J2, with coverage) comes first")
    assets = judge.load_assets(ws)
    units = judge.build_units(assets)
    by_id = {u["id"]: u for u in units}
    oracle = judge._read(jd / "oracle_expected.json")
    faults = (judge._read(jd / "fault_baseline_monolith.json") or {}).get("scenarios") or {}
    masks = (judge._read(jd / "masks.json") or {}).get("units") or {}
    calls = (judge._read(jd / "monolith_calls.json") or {}).get("units") or {}
    coverage = _unit_coverage(jd)
    if (oracle.get("meta") or {}).get("backend") != verify.ddb_backend()["backend"]:
        raise judge.JudgeError("the expectations were recorded on another database backend (V2P-11)")
    rc = judge.effective_run_config(facts, assets["run_config"], prepare=True)
    names = judge.monolith_table_names(facts, rc.get("table_names"))
    actual_of = {k: v for k, v in names.items() if k != v}
    fact_of = {v: k for k, v in actual_of.items()}
    root = Path(facts.monolith_root)

    mutants = [m for m in generate(facts) if not operators or m["operator"] in operators]
    if limit:
        mutants = mutants[:limit]
    work = verify.TMP_ROOT / "mutants" / re.sub(r"[^A-Za-z0-9_-]", "_", ws.name)
    job_mutants, plan = [], {}
    for n, m in enumerate(mutants):
        m["units"] = affected_units(m, units, coverage, calls, facts)
        if not m["units"]:
            plan[m["id"]] = {"outcome": "not_covered"}
            continue
        entry = {"id": m["id"], "units": m["units"]}
        if m["kind"] == "source":
            mroot = work / f"m{n:04d}" / root.name
            _copy_root(root, mroot)
            _apply(mroot, m)
            entry["root"] = str(mroot)
            m["root"] = str(mroot)
        elif m["kind"] == "drop_write":
            entry["rules"] = [{"match": {"table": actual_of.get(m["table"], m["table"]), "op": m["op"]}, "nth": "all",
                               "kind": "drop"}]
        elif m["kind"] == "serial":
            entry["serial"] = True
        job_mutants.append(entry)
    run_units = [{**u, "fixtures": judge._rename_tables(u["fixtures"], actual_of),
                  "rules": judge._rename_tables(u["rules"], actual_of)} for u in units]
    out_dir = work / "runs" / time.strftime("%Y%m%d-%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)
    backend = verify.ddb_backend()
    identities = {alias: spec["monolith"] for alias, spec in (assets["identities"] or {}).items()}
    job = {"monolith_root": str(root), "run": rc, **backend, "tables": judge._monolith_tables(facts, names),
           "identities": identities, "units": run_units, "passes": 1, "coverage": False, "out_dir": str(out_dir),
           "mutants": job_mutants}
    (out_dir / "job.json").write_text(json.dumps(job, ensure_ascii=False, indent=1), encoding="utf-8")
    t0 = time.time()
    proc = verify._run(["node", str(verify.VERIFY_TOOLS / "monolith-run.js"), str(out_dir / "job.json")],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", env=verify._env(),
                       timeout=timeout_s)
    raw_path = out_dir / "monolith_raw.json"
    if proc.returncode != 0 or not raw_path.is_file():
        raise judge.JudgeError(f"monolith-run (mutants) failed (exit {proc.returncode}): {proc.stderr[-2000:]}")
    raw = json.loads(raw_path.read_text(encoding="utf-8"))

    def template_of(uid: str) -> dict[str, Any] | None:
        if by_id[uid]["rules"]:
            return (faults.get(uid.split("/", 1)[0]) or {}).get(by_id[uid]["case"])
        return (oracle.get("cases") or {}).get(uid)

    for res in raw.get("mutants") or []:
        m = next(x for x in mutants if x["id"] == res["id"])
        if res.get("start_error"):
            plan[m["id"]] = {"outcome": "invalid", "why": res["start_error"][-600:]}
            continue
        kills, traces = [], []
        for r in res["units"]:
            text = json.dumps(r, ensure_ascii=False)
            if m.get("root"):  # the copy's paths (stack traces) as the original's
                for a, b in ((m["root"], str(root)), (m["root"].replace("\\", "/"), str(root).replace("\\", "/"))):
                    text = text.replace(json.dumps(a)[1:-1], json.dumps(b)[1:-1])
            r = json.loads(text)
            r["tables"] = {fact_of.get(t, t): items for t, items in (r.get("tables") or {}).items()}
            r["rules_fired"] = (r.get("rules_fired") or [])[:len(by_id[r["id"]]["rules"])]
            tmpl = template_of(r["id"])
            if tmpl is None:
                continue
            bindings = judge._identity_bindings(r)
            obs = judge._observed(facts, r, {judge._jkey(v): k for k, v in bindings.items()})
            mism = judge.unify(tmpl, obs, bindings, unordered=frozenset((masks.get(r["id"]) or {}).get("unordered") or []))
            if not mism:
                continue
            entry = {"unit": r["id"], "mismatches": mism[:3]}
            (traces if _trace_only(mism) else kills).append(entry)
        if kills:
            plan[m["id"]] = {"outcome": "killed", "by": kills[0], "killing_units": len(kills)}
        elif traces:
            plan[m["id"]] = {"outcome": "trace_only", "by": traces[0]}
        else:
            plan[m["id"]] = {"outcome": "survived", "units_run": len(res["units"]), **({"error": res["error"]} if res.get("error") else {})}
    for d in sorted(work.glob("m[0-9]*")):
        _remove_copy(d / root.name)
        shutil.rmtree(d, ignore_errors=True)

    classes = judge._read(jd / CLASSES) if (jd / CLASSES).is_file() else {}
    classes = classes if isinstance(classes, dict) and "_load_error" not in classes else {}
    exempt = {u["ref"]: u.get("reason") for u in assets.get("uncovered") or [] if isinstance(u, dict) and u.get("ref")}
    rows = []
    for m in mutants:
        row = {k: v for k, v in m.items() if k not in ("root",)}
        row.update(plan.get(m["id"], {"outcome": "not_run"}))
        if row["outcome"] in UNCAUGHT and not m.get("reverse"):
            auto = _explained_by_uncovered(m, exempt)
            if m["id"] in classes:
                row["class"] = classes[m["id"]]
            elif auto:
                row["class"] = auto
        rows.append(row)
    forward = [r for r in rows if not r.get("reverse")]
    count = {o: sum(1 for r in forward if r["outcome"] == o) for o in ("killed", "survived", "not_covered", "trace_only", "invalid")}
    equivalent = sum(1 for r in forward if (r.get("class") or {}).get("class") == "equivalent")
    valid = len(forward) - count["invalid"] - equivalent
    reverse = [r for r in rows if r.get("reverse")]
    summary = {"mutants": len(forward), **count, "equivalent": equivalent,
               "score": round(count["killed"] / valid, 3) if valid else None,
               "by_operator": {op: {o: sum(1 for r in forward if r["operator"] == op and r["outcome"] == o)
                                    for o in ("killed", "survived", "not_covered", "trace_only", "invalid")}
                               for op in sorted({r["operator"] for r in forward})},
               "reverse": {r["id"]: r["outcome"] for r in reverse},
               "reverse_ok": all(r["outcome"] in ("survived", "trace_only") for r in reverse),
               "seconds": round(time.time() - t0, 1)}
    judge._write(jd / VALIDATION, {"meta": {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                            "backend": raw.get("backend"), "oracle_generated_at": (oracle.get("meta") or {}).get("generated_at")},
                                   "summary": summary, "mutants": rows})
    left = [{"id": r["id"], "outcome": r["outcome"], "refs": r.get("refs"), "before": r.get("before"),
             "after": r.get("after")} for r in forward if _to_classify(r)]
    return {"status": "PASS" if summary["reverse_ok"] else "FAIL", "summary": summary, "survivors": left[:30],
            "survivors_total": len(left), "output": f"judge/{VALIDATION}"}


MUTANT_CLASSES = ("equivalent", "gap")
UNCAUGHT = ("survived", "not_covered", "trace_only")


def _explained_by_uncovered(m: dict[str, Any], exempt: dict[str, Any]) -> dict[str, Any] | None:
    """A mutant only on responses judge/uncovered.json says no request can reach (with the
    reason): equivalent under the judge's conditions, and nobody has to read that code again."""
    responses = [r for r in m.get("refs") or [] if "#responses[" in r]
    if not responses or not all(r in exempt for r in responses):
        return None
    return {"class": "equivalent", "source": "uncovered",
            "reason": "judge/uncovered.json: " + "; ".join(f"{r}: {exempt[r]}" for r in responses)}


def _to_classify(m: dict[str, Any]) -> bool:
    return (not m.get("reverse") and m.get("outcome") in UNCAUGHT
            and (m.get("class") or {}).get("source") != "uncovered")


def survivors(ws: Path | str) -> list[dict[str, Any]]:
    """The forward mutants of judge/judge_validation.json the judge did not catch,
    but for those judge/uncovered.json already explains."""
    v = judge._read(judge.judge_dir(ws) / VALIDATION) if (judge.judge_dir(ws) / VALIDATION).is_file() else {}
    return [m for m in (v.get("mutants") or []) if _to_classify(m)]


def check_mutant_classes(ws: Path | str) -> dict[str, Any]:
    """J5 classification: every mutant the judge did not catch is equivalent or a gap."""
    jd = judge.judge_dir(ws)
    if not (jd / VALIDATION).is_file():
        return {"status": "FAIL", "errors": ["judge/judge_validation.json not found: run_mutants (J5) comes first"]}
    left = survivors(ws)
    classes = judge._read(jd / CLASSES) if (jd / CLASSES).is_file() else {}
    if not isinstance(classes, dict) or "_load_error" in classes:
        return {"status": "FAIL", "errors": [f"judge/{CLASSES}: must be an object {{mutant id: {{class, reason}}}}"]}
    ids = {m["id"] for m in left}
    errs = []
    for m in left:
        c = classes.get(m["id"])
        if not isinstance(c, dict):
            errs.append(f"{m['id']}: not classified")
        elif c.get("class") not in MUTANT_CLASSES:
            errs.append(f"{m['id']}: class must be one of {MUTANT_CLASSES}")
        elif not str(c.get("reason") or "").strip():
            errs.append(f"{m['id']}: reason is required")
    # entries for mutants caught since (an earlier round) are left alone
    counts = {k: sum(1 for m in left if (classes.get(m["id"]) or {}).get("class") == k) for k in MUTANT_CLASSES}
    return {"status": "PASS" if not errs else "FAIL", "survivors": len(left), "classes": counts, "errors": errs[:60]}


def gaps(ws: Path | str) -> list[dict[str, Any]]:
    """Survivors classified as gaps, with the classification (for J4)."""
    classes = judge._read(judge.judge_dir(ws) / CLASSES) if (judge.judge_dir(ws) / CLASSES).is_file() else {}
    out = []
    for m in survivors(ws):
        c = (classes or {}).get(m["id"]) or {}
        if c.get("class") == "gap":
            out.append({k: m.get(k) for k in ("id", "operator", "file", "line", "before", "after", "refs", "outcome")}
                       | {"reason": c.get("reason"), "suggest": c.get("suggest")})
    return out
