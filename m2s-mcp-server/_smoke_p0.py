"""P0 smoke test: run wrap_m2s.analyze against the todo monolith and diff
entry_point_count against the existing M2S reference analysis_report.json.
Not part of the pytest suite (that's P1's tests/test_gates.py) - throwaway.
"""
import json
from pathlib import Path

import wrap_m2s

MONOLITH = r"D:\Emma\MyLab\Mono2SLs\Mono2Sls-artifacts\mono_benchmark\todo"
WORKSPACE = r"D:\Emma\MyLab\Mono2SLs\m2s-mcp-server\runs\todo-oh"
REFERENCE = Path(r"D:\Emma\MyLab\Mono2SLs\Mono2Sls-artifacts\M2S\storage\analysis_report.json")

result = wrap_m2s.analyze(MONOLITH, WORKSPACE)
print("entry_point_count:", result["entry_point_count"])
print("entry_points:", json.dumps(result["entry_points"], ensure_ascii=False, indent=2))

ref = json.loads(REFERENCE.read_text(encoding="utf-8"))
ref_entry_points = ref.get("entry_points", [])
print("\nreference entry_point_count:", len(ref_entry_points))
print("match:", len(ref_entry_points) == result["entry_point_count"])
