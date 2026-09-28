"""Coverage wrapper for a Python handler in an RIE container (the Python
twin of cov-flush.js).

controller.js copies this file to /var/task/m2s_cov_handler.py, the
coverage.py package to /opt/m2s-cov, and starts the container with
CMD m2s_cov_handler.handler and M2S_REAL_HANDLER=<the real module.function>.

Measurement starts before the real module is imported, so module-level code
is measured too. The data file is saved after every invocation, in the
invoking thread: the runtime process is never shut down cleanly before the
container is removed.
"""

import importlib
import os
import sys

sys.path.insert(0, "/opt/m2s-cov")
import coverage  # noqa: E402

_TASK_ROOT = os.environ.get("LAMBDA_TASK_ROOT", "/var/task")
os.makedirs("/tmp/m2s-cov", exist_ok=True)
_cov = coverage.Coverage(
    data_file="/tmp/m2s-cov/.coverage",
    branch=True,
    source=[_TASK_ROOT, "/opt/python"],
    omit=["*/m2s_cov_handler.py", "*/site-packages/*", "*/dist-packages/*"],
)
_cov.start()
_mod, _, _fn = os.environ["M2S_REAL_HANDLER"].rpartition(".")
try:
    _real = getattr(importlib.import_module(_mod.replace("/", ".")), _fn)
finally:
    _cov.save()


def handler(event, context):
    try:
        return _real(event, context)
    finally:
        _cov.save()
