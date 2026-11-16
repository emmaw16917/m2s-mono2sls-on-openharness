#!/bin/bash
# v3 M4/W4.8: airline-booking, v2-split template, 3 workers, notice board and rooms on.
WS="D:/Emma/MyLab/Mono2SLs/Mono2Sls-artifacts/M2S/runs/airline-m4"
MONO="D:/Emma/MyLab/Mono2SLs/Mono2Sls-artifacts/mono_benchmark/airline-booking"
CONDA="C:/Users/36029/miniconda3/Scripts/conda.exe"
export M2S_MAIL=1
export M2S_ROOMS=1
export M2S_MAIL_ROOT="D:/m2s-am"
export M2S_MAIL_PORT=8765
mkdir -p "$WS/coord"
cd /d/Emma/MyLab/Mono2SLs-v3/m2s-mcp-server || exit 1
{
  echo "=== $(date '+%F %T') dispatcher run $* (pid $$; template v2-split; max-workers 3; mail+rooms)"
  "$CONDA" run --no-capture-output -n mono2esls python -u -m coord.dispatcher run \
    --ws "$WS" --monolith "$MONO" --template v2-split --max-workers 3 "$@"
  rc=$?
  echo "=== $(date '+%F %T') dispatcher exit $rc"
} >> "$WS/coord/dispatcher.log" 2>&1
