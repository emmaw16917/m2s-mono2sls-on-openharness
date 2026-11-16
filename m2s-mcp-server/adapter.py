"""The adapter map (PIPELINE_V2_PLAN.md §4.8, V2-16, V2P-10): how the judge,
written against the monolith, is applied to the target.

The architect writes it in A1 as storage/adapter_map.json. It is declarative
JSON -- no code -- so it can itself be checked (check_adapter_map here, A2 in
gates.py, V1 check_adapter in judge.py):

    {"tables": [
       {"monolith": "Bookstore-Books", "target": "BooksTable"},
       {"monolith": "Bookstore-Cart", "target": "CartTable",
        "fields": {"customerId": {"from": "customerId", "as": "identity"}}},
       {"monolith": "Bookstore-Bestsellers", "target": "BestsellersTable",
        "fields": {"gsiPk": {"const": "ALL"}}},
       {"monolith": "Bookstore-Users", "target": null, "replaced_by": "cognito",
        "reason": "..."}],
     "identities": {
       "claims": {"sub": "${id}", "email": "${email}", "name": "${name}"},
       "user_id": "${id}",
       "seed": [{"table": "UserProfiles", "item": {"userId": "${id}", "name": "${name}"}}]},
     "dropped_endpoints": [
       {"endpoint": "POST /api/auth/register", "replaced_by": "cognito", "reason": "..."}],
     "target_only_tables": [{"target": "IdempotencyTable", "reason": "..."}],
     "note": "..."}

Data mapping, per monolith table (every table of the fact base has an entry):
  target         the target table (blueprint logical name), or null with
                 replaced_by and reason when the table has no counterpart
  fields         target attribute -> one of
                   {"from": m}                       the monolith attribute m, renamed
                   {"from": m, "as": "identity"}     m holds a user id; the target holds
                                                     that user's id on the target side
                   {"const": v}                      a constant (e.g. a GSI partition key)
                   {"format": "${a}#${b}"}           a composite of monolith attributes
                   {"from": m, "split": "#", "part": i}   part i of the monolith attribute m
                   {"from": m, "encode": "json"}     JSON text of the monolith value
                   {"from": m, "decode": "json"}     the monolith holds JSON text, the
                                                     target the parsed value
  drop           {monolith attribute: reason}: not carried to the target
  copy_rest      (default true) every other monolith attribute is copied under its
                 own name; attributes some spec takes "from" are not copied as well

Identity mapping: how an identity alias of judge/identities.json looks on the
target. Templates may use ${alias}, ${id} (a fresh id per alias, the user's
id on the target -- what a Cognito sub would be) and the fields the alias's
monolith preparation sends (e.g. ${email}, ${name} of its register body).
  claims    the authorizer claims the gateway hands the function (sub required)
  user_id   what the monolith's user id (the symbol <id:alias>) is on the target,
            default "${id}"
  seed      items written to target tables for each alias before a case
            (profile data kept in a table)

Endpoints the target does not serve (replaced by Cognito, say) are listed in
dropped_endpoints; the judge does not run their steps.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

MAP_PATH = ("storage", "adapter_map.json")
VAR_RE = re.compile(r"\$\{([^}]*)\}")
SPEC_KINDS = ("from", "const", "format")
ENDPOINT_RE = re.compile(r"^(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS) /\S*$")


class AdapterError(ValueError):
    pass


def map_path(ws: Path | str) -> Path:
    return Path(ws).joinpath(*MAP_PATH)


def load_map(ws: Path | str) -> dict[str, Any] | None:
    p = map_path(ws)
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {"_load_error": f"invalid JSON: {exc}"}
    return data if isinstance(data, dict) else {"_load_error": "must be a JSON object"}


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------

def render(template: Any, env: dict[str, Any]) -> Any:
    """${var} in strings; a string that is exactly one ${var} takes the value's type."""
    if isinstance(template, str):
        m = re.fullmatch(r"\$\{([^}]*)\}", template)
        if m and m.group(1) in env:
            return env[m.group(1)]
        return VAR_RE.sub(lambda mm: str(env[mm.group(1)]) if mm.group(1) in env else mm.group(0), template)
    if isinstance(template, list):
        return [render(v, env) for v in template]
    if isinstance(template, dict):
        return {k: render(v, env) for k, v in template.items()}
    return template


def _format_regex(fmt: str) -> tuple[re.Pattern[str], list[str]]:
    names = VAR_RE.findall(fmt)
    parts = VAR_RE.split(fmt)
    rx = ""
    for i, part in enumerate(parts):
        if i % 2 == 0:
            rx += re.escape(part)
        else:
            rx += f"(?P<g{i // 2}>.*?)"
    return re.compile(f"^{rx}$", re.S), names


# ---------------------------------------------------------------------------
# Item mapping
# ---------------------------------------------------------------------------

def _table_entry(amap: dict[str, Any], monolith_table: str) -> dict[str, Any] | None:
    return next((t for t in amap.get("tables") or [] if isinstance(t, dict) and t.get("monolith") == monolith_table), None)


def _sources(fields: dict[str, Any]) -> set[str]:
    return {s["from"] for s in fields.values() if isinstance(s, dict) and "from" in s}


def to_target(entry: dict[str, Any], item: dict[str, Any], ids: dict[str, Any] | None = None) -> dict[str, Any]:
    """One monolith item as the target stores it. `ids` maps monolith user ids
    to target user ids (fields marked as identity)."""
    fields = entry.get("fields") or {}
    drop = set((entry.get("drop") or {}).keys())
    out: dict[str, Any] = {}
    if entry.get("copy_rest", True):
        taken = _sources(fields)
        out = {k: v for k, v in item.items() if k not in taken and k not in drop}
    for t, spec in fields.items():
        if "const" in spec:
            out[t] = spec["const"]
        elif "format" in spec:
            if all(n in item for n in VAR_RE.findall(spec["format"])):
                out[t] = VAR_RE.sub(lambda mm: str(item[mm.group(1)]), spec["format"])
        elif spec.get("from") in item:
            v = item[spec["from"]]
            if spec.get("as") == "identity" and ids and isinstance(v, str) and v in ids:
                v = ids[v]
            if "split" in spec:
                parts = str(v).split(spec["split"])
                if spec.get("part", 0) >= len(parts):
                    continue
                v = parts[spec.get("part", 0)]
            elif spec.get("encode") == "json":
                v = json.dumps(v, ensure_ascii=False, separators=(",", ":"))
            elif spec.get("decode") == "json":
                try:
                    v = json.loads(v)
                except (TypeError, json.JSONDecodeError):
                    pass
            out[t] = v
    return out


def to_monolith(entry: dict[str, Any], item: dict[str, Any], ids: dict[str, Any] | None = None) -> dict[str, Any]:
    """A target item in the monolith's structure (the inverse of to_target).
    `ids` maps target user ids back to monolith user ids."""
    fields = entry.get("fields") or {}
    produced = set(fields)
    out: dict[str, Any] = {}
    if entry.get("copy_rest", True):
        out = {k: v for k, v in item.items() if k not in produced}
    splits: dict[str, dict[int, Any]] = {}
    for t, spec in fields.items():
        if t not in item or "const" in spec:
            continue
        v = item[t]
        if "format" in spec:
            rx, names = _format_regex(spec["format"])
            m = rx.match(str(v))
            if m:
                for i, n in enumerate(names):
                    out.setdefault(n, m.group(f"g{i}"))
            continue
        if "split" in spec:
            splits.setdefault(spec["from"], {})[spec.get("part", 0)] = v
            continue
        if spec.get("encode") == "json":
            try:
                v = json.loads(v)
            except (TypeError, json.JSONDecodeError):
                pass
        elif spec.get("decode") == "json":
            v = json.dumps(v, ensure_ascii=False, separators=(",", ":"))
        if spec.get("as") == "identity" and ids and isinstance(v, str) and v in ids:
            v = ids[v]
        out[spec["from"]] = v
    for m_attr, parts in splits.items():
        sep = next(s["split"] for s in fields.values() if s.get("from") == m_attr and "split" in s)
        out[m_attr] = sep.join(str(parts[i]) for i in sorted(parts))
    return out


def fixtures_to_target(amap: dict[str, Any], fixtures: dict[str, list[Any]], ids: dict[str, Any] | None = None
                       ) -> dict[str, list[Any]]:
    """{monolith table: [items]} -> {target table: [items]}; tables replaced on
    the target (target null) are left out."""
    out: dict[str, list[Any]] = {}
    for table, items in fixtures.items():
        entry = _table_entry(amap, table)
        if entry is None:
            raise AdapterError(f"the adapter map has no entry for the monolith table {table!r}")
        if not entry.get("target"):
            continue
        out.setdefault(entry["target"], []).extend(to_target(entry, i, ids) for i in items)
    return out


def tables_to_monolith(amap: dict[str, Any], tables: dict[str, list[Any]], ids: dict[str, Any] | None = None
                       ) -> dict[str, list[Any]]:
    """Target tables after a case, in the monolith's structure: {monolith table: [items]}.
    Target-only tables are left out; a replaced table is absent."""
    out: dict[str, list[Any]] = {}
    for entry in amap.get("tables") or []:
        if isinstance(entry, dict) and entry.get("target") and entry["target"] in tables:
            out[entry["monolith"]] = [to_monolith(entry, i, ids) for i in tables[entry["target"]]]
    return out


def dropped_fields(amap: dict[str, Any], monolith_table: str) -> set[str]:
    entry = _table_entry(amap, monolith_table) or {}
    return set((entry.get("drop") or {}).keys())


# ---------------------------------------------------------------------------
# Identities on the target
# ---------------------------------------------------------------------------

def identity_env(alias: str, monolith_spec: dict[str, Any], target_id: str) -> dict[str, Any]:
    """The variables an identity template may use: alias, id, and the top-level
    fields the alias's monolith preparation sends."""
    env: dict[str, Any] = {}
    for s in (monolith_spec or {}).get("steps") or []:
        for part in ("body", "form"):
            if isinstance(s.get(part), dict):
                for k, v in s[part].items():
                    if isinstance(v, (str, int, float, bool)):
                        env.setdefault(k, v)
    env.update({"alias": alias, "id": target_id})
    return env


def target_identity(amap: dict[str, Any], alias: str, monolith_spec: dict[str, Any], target_id: str) -> dict[str, Any]:
    """{claims, user_id, seed} of one alias on the target."""
    ident = amap.get("identities") or {}
    env = identity_env(alias, monolith_spec, target_id)
    claims = {k: (v if isinstance(v, (str, list)) else str(v)) for k, v in render(ident.get("claims") or {"sub": "${id}"}, env).items()}
    return {"claims": claims, "user_id": render(ident.get("user_id", "${id}"), env),
            "seed": render(ident.get("seed") or [], env)}


# ---------------------------------------------------------------------------
# check_adapter_map (static)
# ---------------------------------------------------------------------------

def _check_spec(where: str, t: str, spec: Any) -> list[str]:
    if not isinstance(spec, dict):
        return [f"{where}.fields.{t}: must be an object (from / const / format)"]
    kinds = [k for k in SPEC_KINDS if k in spec]
    if len(kinds) != 1:
        return [f"{where}.fields.{t}: exactly one of from / const / format, got {kinds or 'none'}"]
    allowed = {"from": {"from", "as", "split", "part", "encode", "decode", "note"},
               "const": {"const", "note"}, "format": {"format", "note"}}[kinds[0]]
    errs = []
    if set(spec) - allowed:
        errs.append(f"{where}.fields.{t}: unknown keys {sorted(set(spec) - allowed)} for a {kinds[0]} field "
                    "(transformations are declarative: from + as/split/part/encode/decode, const, format)")
    if "from" in spec:
        if not isinstance(spec["from"], str) or not spec["from"]:
            errs.append(f"{where}.fields.{t}.from: a monolith attribute name")
        if "as" in spec and spec["as"] != "identity":
            errs.append(f"{where}.fields.{t}.as: only \"identity\"")
        if len([k for k in ("split", "encode", "decode") if k in spec]) > 1:
            errs.append(f"{where}.fields.{t}: at most one of split / encode / decode")
        if "split" in spec and (not isinstance(spec["split"], str) or not spec["split"]
                                or not isinstance(spec.get("part", 0), int) or spec.get("part", 0) < 0):
            errs.append(f"{where}.fields.{t}: split is a separator string, part a non-negative index")
        for k in ("encode", "decode"):
            if k in spec and spec[k] != "json":
                errs.append(f"{where}.fields.{t}.{k}: only \"json\"")
    if "format" in spec:
        if not isinstance(spec["format"], str) or not VAR_RE.search(spec["format"]):
            errs.append(f"{where}.fields.{t}.format: a string with ${{attribute}} parts, e.g. \"${{a}}#${{b}}\"")
        else:
            rx, names = _format_regex(spec["format"])
            if any(not n for n in names):
                errs.append(f"{where}.fields.{t}.format: empty ${{}}")
            if re.search(r"\}\$\{", spec["format"]):
                errs.append(f"{where}.fields.{t}.format: two ${{...}} parts need a separator between them, "
                            "or the target value cannot be split back")
    return errs


def check_adapter_map(amap: Any, *, monolith_tables: dict[str, dict[str, Any]], endpoints: list[str],
                      target_tables: dict[str, list[str]] | None = None,
                      identities: dict[str, Any] | None = None,
                      fixtures: dict[str, Any] | None = None,
                      auth_replaced: bool = False) -> list[str]:
    """What is wrong with an adapter map.

    monolith_tables  {table: {"keys": {...}, "index_keys": {...}}} from the fact base
    endpoints        the fact base's endpoints ("POST /api/cart")
    target_tables    {target table: [key attributes]} from the blueprint / template (None: unknown)
    identities       judge/identities.json (aliases and their monolith preparation)
    fixtures         judge/fixtures ({name: {monolith table: [items]}}) for the round-trip check
    auth_replaced    the blueprint replaces the monolith's authentication (identity mapping required)
    """
    where = "storage/adapter_map.json"
    if not isinstance(amap, dict):
        return [f"{where}: not found -- the architect writes it in A1 (see the m2s-architect role doc)"]
    if "_load_error" in amap:
        return [f"{where}: {amap['_load_error']}"]
    allowed = {"version", "tables", "identities", "dropped_endpoints", "target_only_tables", "note"}
    errs = []
    if set(amap) - allowed:
        errs.append(f"{where}: unknown keys {sorted(set(amap) - allowed)} (allowed: {sorted(allowed)})")
    tables = amap.get("tables")
    if not isinstance(tables, list):
        return errs + [f"{where}: tables must be a list, one entry per monolith table"]
    seen: set[str] = set()
    for i, e in enumerate(tables):
        w = f"{where}: tables[{i}]"
        if not isinstance(e, dict):
            errs.append(f"{w}: must be an object")
            continue
        w = f"{where}: tables[{i}] ({e.get('monolith')})"
        if set(e) - {"monolith", "target", "fields", "drop", "copy_rest", "replaced_by", "reason", "note"}:
            errs.append(f"{w}: unknown keys {sorted(set(e) - {'monolith', 'target', 'fields', 'drop', 'copy_rest', 'replaced_by', 'reason', 'note'})}")
        m = e.get("monolith")
        if m not in monolith_tables:
            errs.append(f"{w}: monolith must be a table of the fact base {sorted(monolith_tables)}")
        elif m in seen:
            errs.append(f"{w}: {m} has a second entry")
        seen.add(str(m))
        tgt = e.get("target")
        if tgt is None:
            if not str(e.get("replaced_by") or "").strip() or not str(e.get("reason") or "").strip():
                errs.append(f"{w}: a table with no target needs replaced_by (e.g. \"cognito\") and reason")
            if e.get("fields") or e.get("drop"):
                errs.append(f"{w}: a replaced table has no fields / drop")
            continue
        if not isinstance(tgt, str) or not tgt:
            errs.append(f"{w}: target must be a target table name or null")
            continue
        if target_tables is not None and tgt not in target_tables:
            errs.append(f"{w}: target {tgt!r} is not a table of the target {sorted(target_tables)}")
        fields = e.get("fields") or {}
        if not isinstance(fields, dict):
            errs.append(f"{w}: fields must map target attributes to specs")
            continue
        for t, spec in fields.items():
            errs += _check_spec(w, t, spec)
        drop = e.get("drop") or {}
        if not isinstance(drop, dict) or not all(isinstance(v, str) and v.strip() for v in drop.values()):
            errs.append(f"{w}: drop maps monolith attributes to the reason they are not carried over")
            drop = {}
        if "copy_rest" in e and not isinstance(e["copy_rest"], bool):
            errs.append(f"{w}: copy_rest is true or false")
        splits: dict[str, set[int]] = {}
        for t, spec in fields.items():
            if isinstance(spec, dict) and "split" in spec and isinstance(spec.get("from"), str):
                splits.setdefault(spec["from"], set()).add(spec.get("part", 0))
        for src, parts in splits.items():
            if parts != set(range(len(parts))):
                errs.append(f"{w}: the parts of {src} must all be mapped (0..n-1), got {sorted(parts)}")
        if m in monolith_tables and target_tables is not None and tgt in target_tables:
            mono_attrs = set(monolith_tables[m]["keys"]) | set(monolith_tables[m].get("index_keys") or {})
            produced = set(fields)
            if e.get("copy_rest", True):
                produced |= mono_attrs - _sources(fields) - set(drop)
            missing = [k for k in target_tables[tgt] if k not in produced]
            if missing:
                errs.append(f"{w}: the target key attribute(s) {missing} of {tgt} are not produced by the mapping "
                            f"(map them in fields)")
        if m in monolith_tables:
            gone = [k for k in monolith_tables[m]["keys"] if k in drop]
            if gone:
                errs.append(f"{w}: a key attribute of the monolith table cannot be dropped: {gone}")
    for m in monolith_tables:
        if m not in seen:
            errs.append(f"{where}: no entry for the monolith table {m} (every table needs one: a target, or "
                        "target null with replaced_by and reason)")
    for f_name, data in (fixtures or {}).items():
        for table in (data or {}) if isinstance(data, dict) else {}:
            if table not in seen and table in monolith_tables:
                errs.append(f"{where}: the judge fixture {f_name} seeds {table}, which has no entry")
    # identities
    ident = amap.get("identities")
    aliases = identities or {}
    if ident is None:
        if auth_replaced and aliases:
            errs.append(f"{where}: identities is required -- the blueprint replaces the monolith's authentication, "
                        "so the judge needs each alias's claims on the target")
    elif not isinstance(ident, dict) or set(ident) - {"claims", "user_id", "seed", "note"}:
        errs.append(f"{where}: identities is {{claims, user_id?, seed?, note?}}")
    else:
        claims = ident.get("claims")
        if not isinstance(claims, dict) or "sub" not in claims:
            errs.append(f"{where}: identities.claims must be an object with at least sub")
        for alias, spec in aliases.items():
            env = identity_env(alias, (spec or {}).get("monolith") or {}, "x")
            used = set(VAR_RE.findall(json.dumps({k: ident.get(k) for k in ("claims", "user_id", "seed")})))
            unknown = sorted(used - set(env))
            if unknown:
                errs.append(f"{where}: identities use {unknown}, which alias {alias} does not define "
                            f"(available: {sorted(env)})")
        seed = ident.get("seed") or []
        if not isinstance(seed, list) or not all(isinstance(s, dict) and set(s) <= {"table", "item"} and
                                                 isinstance(s.get("item"), dict) for s in seed):
            errs.append(f"{where}: identities.seed is a list of {{table, item}}")
        else:
            for s in seed:
                if target_tables is not None and s.get("table") not in target_tables:
                    errs.append(f"{where}: identities.seed table {s.get('table')!r} is not a table of the target")
    # dropped endpoints
    drops = amap.get("dropped_endpoints") or []
    if not isinstance(drops, list):
        errs.append(f"{where}: dropped_endpoints is a list of {{endpoint, replaced_by?, reason}}")
        drops = []
    for i, d in enumerate(drops):
        w = f"{where}: dropped_endpoints[{i}]"
        if not isinstance(d, dict) or not isinstance(d.get("endpoint"), str):
            errs.append(f"{w}: {{endpoint, replaced_by?, reason}}")
            continue
        if d["endpoint"] not in endpoints:
            errs.append(f"{w}: {d['endpoint']!r} is not an endpoint of the fact base")
        if not str(d.get("reason") or "").strip():
            errs.append(f"{w}: reason is required")
    for i, t in enumerate(amap.get("target_only_tables") or []):
        if not isinstance(t, dict) or not t.get("target") or not str(t.get("reason") or "").strip():
            errs.append(f"{where}: target_only_tables[{i}]: {{target, reason}}")
    if errs:
        return errs
    # round trip: every fixture item mapped to the target and back is itself
    for f_name, data in (fixtures or {}).items():
        for table, items in (data or {}).items() if isinstance(data, dict) else []:
            entry = _table_entry(amap, table)
            if not entry or not entry.get("target"):
                continue
            drop = set((entry.get("drop") or {}).keys())
            for n, item in enumerate(items if isinstance(items, list) else []):
                if not isinstance(item, dict):
                    continue
                want = {k: v for k, v in item.items() if k not in drop}
                back = to_monolith(entry, to_target(entry, item))
                if json.dumps(back, sort_keys=True) != json.dumps(want, sort_keys=True):
                    errs.append(f"{where}: {table}: fixture {f_name}[{n}] does not map back to itself "
                                f"(monolith {want} -> target {to_target(entry, item)} -> {back})")
                    break
    return errs
