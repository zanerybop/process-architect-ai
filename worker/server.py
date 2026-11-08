"""Internal HTTP supervisor; isolated children never inherit application secrets."""

import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

MAX_REQUEST = 262144
MAX_OUTPUT = 1024**2
MAX_CONNECTIONS = 8
CONNECTION_TIMEOUT = 6
CAPACITY = threading.BoundedSemaphore(2)
CHILD_ENV = {"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8", "PYTHONIOENCODING": "utf-8"}


def sandbox_ready() -> bool:
    if sys.platform != "linux" or os.geteuid() == 0:
        return False
    status = Path("/proc/self/status").read_text()
    flags = dict(line.split(":", 1) for line in status.splitlines() if ":" in line)
    root_ro = any(
        fields[1] == "/" and "ro" in fields[3].split(",")
        for fields in (line.split() for line in Path("/proc/mounts").read_text().splitlines())
    )
    return root_ro and flags.get("NoNewPrivs", "").strip() == "1" and int(flags.get("CapEff", "1"), 16) == 0


def run_child(payload: bytes) -> dict:
    try:
        result = subprocess.run(
            [sys.executable, "-I", str(Path(__file__).with_name("child.py"))],
            input=payload,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=5,
            env=CHILD_ENV,
            cwd="/tmp",
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"error": {"code": "WORKER_TIMEOUT", "message": "Превышено время выполнения worker."}}
    if result.returncode != 0:
        return {"error": {"code": "WORKER_FAILURE", "message": "Worker завершён по ошибке или лимиту."}}
    if len(result.stdout) > MAX_OUTPUT:
        return {"error": {"code": "OUTPUT_LIMIT", "message": "Превышен лимит ответа worker."}}
    try:
        data = json.loads(result.stdout)
        if not isinstance(data, dict) or ("error" in data) == ("ir" in data):
            raise ValueError("Invalid child envelope")
        return data
    except (ValueError, UnicodeError, RecursionError):
        return {"error": {"code": "WORKER_PROTOCOL", "message": "Недопустимый ответ worker."}}


class BoundedHTTPServer(ThreadingHTTPServer):
    """Bound sockets before parsing headers, separately from child capacity."""

    request_queue_size = MAX_CONNECTIONS

    def __init__(
        self,
        *args,
        max_connections: int = MAX_CONNECTIONS,
        connection_timeout: float = CONNECTION_TIMEOUT,
        **kwargs,
    ):
        self._connections = threading.BoundedSemaphore(max_connections)
        self.connection_timeout = connection_timeout
        super().__init__(*args, **kwargs)

    def get_request(self):
        connection, address = super().get_request()
        # This applies to request line and headers, before Handler.do_POST.
        connection.settimeout(self.connection_timeout)
        return connection, address

    def process_request(self, request, client_address):
        if not self._connections.acquire(blocking=False):
            # No extra thread and no blocking write for an excess connection.
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self._connections.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._connections.release()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # Neither source nor generated program belongs in infrastructure logs.

    def reply(self, status: int, payload: dict):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        ready = sandbox_ready()
        self.reply(200 if ready else 503, {"ready": ready, "runtime": "restricted-python-container-v1"})

    def do_POST(self):
        if self.path != "/run" or not sandbox_ready():
            return self.reply(503, {"error": {"code": "RUNNER_UNAVAILABLE"}})
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_REQUEST:
            return self.reply(413, {"error": {"code": "INPUT_LIMIT"}})
        if not CAPACITY.acquire(blocking=False):
            return self.reply(429, {"error": {"code": "WORKER_BUSY"}})
        try:
            body = self.rfile.read(length)
            self.reply(200, run_child(body))
        except (OSError, ValueError):
            self.reply(400, {"error": {"code": "WORKER_PROTOCOL"}})
        finally:
            CAPACITY.release()


if __name__ == "__main__":
    BoundedHTTPServer(("0.0.0.0", 8081), Handler).serve_forever()
