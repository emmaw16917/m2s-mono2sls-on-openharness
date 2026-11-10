#!/bin/bash
# v2 N5 launcher: the whole pipeline (coord/templates/v2.yaml) under the v3 dispatcher of the
# v2 worktree, real workers, --max-workers 1. Usage: launch.sh [--stop-after TASK]; log: WS/coord/dispatcher.log
WS="D:/Emma/MyLab/Mono2SLs/Mono2Sls-artifacts/M2S/runs/bookstore-full"
MONO="D:/Emma/MyLab/Mono2SLs/Mono2Sls-artifacts/mono_benchmark/bookstore"
CONDA="C:/Users/36029/miniconda3/Scripts/conda.exe"
cd /d/Emma/MyLab/Mono2SLs-v2/m2s-mcp-server || exit 1
{
  echo "=== $(date '+%F %T') dispatcher run $* (pid $$; HTTP_PROXY=${HTTP_PROXY:-unset} HTTPS_PROXY=${HTTPS_PROXY:-unset})"
  "$CONDA" run --no-capture-output -n mono2esls python -u -m coord.dispatcher run --ws "$WS" --monolith "$MONO" --template v2 --max-workers 1 "$@"
  rc=$?  # 0 done, 3 needs_human, 4 stalled, 5 stopped (--stop-after)
  echo "=== $(date '+%F %T') dispatcher exit $rc"
} >> "$WS/coord/dispatcher.log" 2>&1
