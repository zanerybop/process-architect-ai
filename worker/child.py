"""Only launched inside the hardened Linux worker container."""

import json
import os
import resource
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Set before loading pydantic/builder and before parsing any untrusted input.
resource.setrlimit(resource.RLIMIT_AS, (128 * 1024**2, 128 * 1024**2))
resource.setrlimit(resource.RLIMIT_CPU, (3, 3))
resource.setrlimit(resource.RLIMIT_FSIZE, (1024**2, 1024**2))
resource.setrlimit(resource.RLIMIT_NOFILE, (32, 32))
os.umask(0o077)

from backend.app.domain import PipelineError  # noqa: E402
from worker.interpreter import interpret  # noqa: E402

try:
    payload = json.loads(sys.stdin.buffer.read(262145))
    result = {"ir": interpret(payload["code"], payload["source_text"]).model_dump()}
except PipelineError as exc:
    result = {"error": exc.issue.model_dump()}
except Exception:
    result = {"error": {"code": "WORKER_FAILURE", "message": "Worker отклонил программу."}}
output = json.dumps(result, ensure_ascii=False).encode("utf-8")
if len(output) > 1024**2:
    output = b'{"error":{"code":"OUTPUT_LIMIT","message":"Worker output limit"}}'
sys.stdout.buffer.write(output)
