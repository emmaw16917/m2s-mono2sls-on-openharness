"""Turn the coverage.py data of one RIE container into the istanbul-shaped
coverage-final.json that verify.py reads for Node functions (statementMap/s,
branchMap/b, fnMap/f), so run_coverage and changed-line coverage work the
same for both runtimes.

Runs inside the container (the Lambda Python and the coverage.py it
measured with):
    python3 m2s_cov_report.py <data_file> <out.json>

- statements: every statement coverage.py knows, spanning its lines (a
  multi-line call is one statement; a compound statement spans only its
  header, not its body), count 1 if executed else 0;
- branches: coverage.py arcs grouped by source line, one arm per target;
- functions: coverage.py's function regions, hit if any line ran.
Paths stay container paths (/var/task/..., /opt/python/...); controller.js
maps them to the build directory.
"""

import ast
import json
import sys

sys.path.insert(0, "/opt/m2s-cov")
import coverage  # noqa: E402


def statement_spans(source):
    """first line of a statement -> (start, end) of the lines it occupies."""
    spans = {}
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return spans
    for node in ast.walk(tree):
        if not isinstance(node, ast.stmt):
            continue
        start = node.lineno
        body = getattr(node, "body", None)
        if isinstance(body, list) and body and isinstance(body[0], ast.stmt):
            end = max(start, body[0].lineno - 1)
        else:
            end = node.end_lineno or start
        spans.setdefault(start, (start, end))
        for dec in getattr(node, "decorator_list", []) or []:
            spans.setdefault(dec.lineno, (dec.lineno, dec.end_lineno or dec.lineno))
    return spans


def loc(start, end=None):
    return {"start": {"line": start, "column": 0}, "end": {"line": end or start, "column": 0}}


def convert(path, fr):
    try:
        with open(path, encoding="utf-8") as fh:
            spans = statement_spans(fh.read())
    except OSError:
        spans = {}
    out = {"path": path, "statementMap": {}, "s": {}, "branchMap": {}, "b": {}, "fnMap": {}, "f": {}}
    executed = set(fr.get("executed_lines", []))
    for i, line in enumerate(sorted(executed | set(fr.get("missing_lines", [])))):
        start, end = spans.get(line, (line, line))
        out["statementMap"][str(i)] = loc(start, end)
        out["s"][str(i)] = 1 if line in executed else 0
    arcs = {}
    for src, dst in fr.get("executed_branches", []):
        arcs.setdefault(src, []).append((dst, 1))
    for src, dst in fr.get("missing_branches", []):
        arcs.setdefault(src, []).append((dst, 0))
    for i, (src, arms) in enumerate(sorted(arcs.items())):
        arms.sort()
        out["branchMap"][str(i)] = {"type": "branch", "loc": loc(src),
                                    "locations": [loc(dst if dst > 0 else src) for dst, _ in arms]}
        out["b"][str(i)] = [hit for _, hit in arms]
    i = 0
    for name, region in (fr.get("functions") or {}).items():
        lines = region.get("executed_lines", []) + region.get("missing_lines", [])
        if not name or not lines:  # "" is the module-level region
            continue
        out["fnMap"][str(i)] = {"name": name, "loc": loc(min(lines))}
        out["f"][str(i)] = 1 if region.get("executed_lines") else 0
        i += 1
    return out


def main():
    data_file, out_file = sys.argv[1:3]
    cov = coverage.Coverage(data_file=data_file, branch=True)
    cov.load()
    tmp = out_file + ".cov.json"
    cov.json_report(outfile=tmp, ignore_errors=True)
    with open(tmp, encoding="utf-8") as fh:
        report = json.load(fh)
    result = {path: convert(path, fr) for path, fr in report.get("files", {}).items()}
    with open(out_file, "w", encoding="utf-8") as fh:
        json.dump(result, fh)


if __name__ == "__main__":
    main()
