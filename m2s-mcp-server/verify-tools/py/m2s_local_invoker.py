"""Local-Python stand-in for the Lambda runtime (no-Docker fallback of
controller.js, the Python twin of local-invoker.js).

    python m2s_local_invoker.py <functionDir> <module.function> [layerPythonDir...]

Reads one JSON event per stdin line, invokes the handler, writes one
"@@M2S@@<json>" line per event to stdout: {result} or {error: {type,
message}}, plus ms. The host Python is not the Lambda runtime (version,
boto3), which verify.py states in its disclaimer.
"""

import importlib
import json
import os
import sys
import time
import uuid


def emit(obj):
    sys.stdout.write("@@M2S@@" + json.dumps(obj, default=str) + "\n")
    sys.stdout.flush()


class Context:
    def __init__(self, n):
        self.aws_request_id = f"req-{n}-{uuid.uuid4().hex[:8]}"
        self.function_name = os.environ.get("AWS_LAMBDA_FUNCTION_NAME", "fn")
        self.function_version = "$LATEST"
        self.memory_limit_in_mb = os.environ.get("AWS_LAMBDA_FUNCTION_MEMORY_SIZE", "512")
        self.invoked_function_arn = f"arn:aws:lambda:us-east-1:000000000000:function:{self.function_name}"
        self.log_group_name = f"/aws/lambda/{self.function_name}"
        self.log_stream_name = "local"

    def get_remaining_time_in_millis(self):
        return 30000


def main():
    fn_dir, spec, *layer_dirs = sys.argv[1:]
    # Layers first would shadow the function's own modules; Lambda puts the
    # task root first, then /opt/python.
    sys.path[:0] = [fn_dir, *layer_dirs]
    os.chdir(fn_dir)
    mod_name, _, fn_name = spec.rpartition(".")
    handler, load_error = None, None
    try:
        handler = getattr(importlib.import_module(mod_name.replace("/", ".")), fn_name, None)
        if not callable(handler):
            load_error = {"type": "Runtime.HandlerNotFound", "message": f"{spec} is undefined or not callable"}
    except SyntaxError as e:
        load_error = {"type": "Runtime.UserCodeSyntaxError", "message": str(e)}
    except Exception as e:  # noqa: BLE001 -- reported like the runtime does
        load_error = {"type": "Runtime.ImportModuleError", "message": f"{type(e).__name__}: {e}"}
    n = 0
    for line in sys.stdin:
        if not line.strip():
            continue
        event = json.loads(line)
        if load_error:
            emit({"error": load_error, "ms": 0})
            continue
        n += 1
        t0 = time.perf_counter()
        try:
            result = handler(event, Context(n))
            emit({"result": result, "ms": round((time.perf_counter() - t0) * 1000, 3)})
        except Exception as e:  # noqa: BLE001
            emit({"error": {"type": type(e).__name__, "message": str(e)}, "ms": round((time.perf_counter() - t0) * 1000, 3)})


if __name__ == "__main__":
    main()
