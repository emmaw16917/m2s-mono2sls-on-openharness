"""Starts the Lambda Python runtime with the perf shim loaded.

The RIE container runs the runtime through AWS_LAMBDA_EXEC_WRAPPER
(/opt/m2s-shim/wrapper, written by perf.py), which executes
    /var/lang/bin/python3.12 /opt/m2s-shim/m2s_boot.py /var/runtime/bootstrap.py
This file patches botocore, takes its own directory off sys.path again and
runs the unmodified bootstrap. PYTHONPATH is left unset on purpose: the
bootstrap only adds the layer paths (/opt/python...) when it is not set.
"""

import runpy
import sys

_SHIM_DIR = "/opt/m2s-shim"
sys.path.insert(0, _SHIM_DIR)
import m2s_perf_shim  # noqa: E402,F401  (patches botocore on import)

sys.path.remove(_SHIM_DIR)
sys.argv = sys.argv[1:]
runpy.run_path(sys.argv[0], run_name="__main__")
