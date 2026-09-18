"""P1 smoke test: run gates.py's run_gate("template") against the REAL M2S
reference output (Mono2Sls-artifacts/M2S/storage + output), which M2S's own
consistency_report.json already says is a clean PASS. Confirms our
reimplementation agrees with the ground truth on real data, not just
synthetic fixtures."""
import json
from pathlib import Path

import gates

# M2S's own storage/ and output/ sit directly under M2S/, not under a
# workspace subdirectory -- pass M2S/ itself as the "workspace" root since
# gates.py expects <workspace>/storage and <workspace>/output.
M2S_ROOT = Path(r"D:\Emma\MyLab\Mono2SLs\Mono2Sls-artifacts\M2S")

result = gates.run_gate("template", str(M2S_ROOT))
print(json.dumps({k: v for k, v in result.items() if k != "suspects"}, ensure_ascii=False, indent=2))
print("\nsuspects summary:")
for check_id, data in result["suspects"].items():
    print(f"  {check_id}: {data}")

print("\n=== verdict ===")
print("status:", result["status"])
print("mechanical_failures:", len(result["mechanical_failures"]))
assert result["status"] == "PASS", "gates.py disagrees with M2S's own consistency_report.json (PASS)"
print("OK: gates.py agrees with the real M2S reference output.")
