import json
import socket
import subprocess
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from backend.app.domain import PipelineError
from backend.app.runner import ContainerRunner
from worker.server import BoundedHTTPServer, Handler, run_child


def test_timeout_kills_child_via_subprocess_run():
    with patch("worker.server.subprocess.run", side_effect=subprocess.TimeoutExpired("python", 5)) as call:
        result = run_child(b"{}")
    assert result["error"]["code"] == "WORKER_TIMEOUT"
    assert call.call_args.kwargs["timeout"] == 5
    assert "LLM_API_KEY" not in call.call_args.kwargs["env"]


def test_output_and_protocol_limits():
    for stdout, expected in [(b"x" * (1024**2 + 1), "OUTPUT_LIMIT"), (b"not json", "WORKER_PROTOCOL")]:
        with patch("worker.server.subprocess.run", return_value=SimpleNamespace(returncode=0, stdout=stdout)):
            assert run_child(b"{}")["error"]["code"] == expected


@pytest.mark.asyncio
async def test_unavailable_worker_never_falls_back():
    with patch("httpx.AsyncClient.post", side_effect=httpx.ConnectError("unreachable")):
        with pytest.raises(PipelineError) as error:
            await ContainerRunner().run('bpmn.process(id="p", name="p")', "")
    assert error.value.issue.code == "RUNNER_UNAVAILABLE"


def test_compose_boundary_has_no_worker_secrets_mounts_or_ports():
    from pathlib import Path

    compose = Path("compose.yaml").read_text(encoding="utf-8")
    worker = compose.split("  worker:", 1)[1].split("  api:", 1)[0]
    for required in ["read_only: true", "cap_drop: [ALL]", "no-new-privileges:true", "pids_limit: 32"]:
        assert required in worker
    for forbidden in ["LLM_", "volumes:", "ports:", "docker.sock", "privileged: true"]:
        assert forbidden not in worker
    assert "internal: true" in compose


def test_controlled_child_error_preserved():
    body = {"error": {"code": "CODE_POLICY", "message": "Запрещённый вызов"}}
    with patch(
        "worker.server.subprocess.run",
        return_value=SimpleNamespace(returncode=0, stdout=json.dumps(body).encode()),
    ):
        assert run_child(b"{}") == body


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [[], None, "ready", {}, {"ready": "true"}])
async def test_health_rejects_malformed_envelope(payload):
    response = httpx.Response(
        200,
        content=json.dumps(payload),
        request=httpx.Request("GET", "http://worker/health"),
    )
    with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=response)):
        assert await ContainerRunner().health() is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        [],
        None,
        "ir",
        {},
        {"error": None},
        {"error": []},
        {"error": {"code": 42}},
        {"error": {"message": []}},
        {"error": {"element_ids": "node"}},
        {"error": {"element_ids": [None]}},
        {"ir": None},
        {"ir": {}, "error": {}},
    ],
)
async def test_runner_malformed_envelope_is_controlled(payload):
    response = httpx.Response(
        200,
        content=json.dumps(payload),
        request=httpx.Request("POST", "http://worker/run"),
    )
    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=response)):
        with pytest.raises(PipelineError) as caught:
            await ContainerRunner().run("", "")
    assert caught.value.issue.code == "WORKER_PROTOCOL"


@pytest.mark.asyncio
async def test_runner_preserves_controlled_error_and_element_reference():
    response = httpx.Response(
        200,
        json={"error": {"code": "UNKNOWN_LANE", "message": "Неизвестная дорожка", "element_ids": ["n"]}},
        request=httpx.Request("POST", "http://worker/run"),
    )
    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=response)):
        with pytest.raises(PipelineError) as caught:
            await ContainerRunner().run("", "")
    assert caught.value.issue.code == "UNKNOWN_LANE"
    assert caught.value.issue.element_ids == ["n"]


@pytest.mark.parametrize("stdout", [b"[]", b"null", b'"text"', b"{}"])
def test_supervisor_rejects_non_envelope_json(stdout):
    with patch("worker.server.subprocess.run", return_value=SimpleNamespace(returncode=0, stdout=stdout)):
        assert run_child(b"{}")["error"]["code"] == "WORKER_PROTOCOL"


def test_stalled_headers_timeout_and_connection_capacity_is_released():
    started, finished = threading.Event(), threading.Event()

    class ObservedHandler(Handler):
        def setup(self):
            super().setup()
            started.set()

        def finish(self):
            try:
                super().finish()
            finally:
                finished.set()

    with BoundedHTTPServer(
        ("127.0.0.1", 0),
        ObservedHandler,
        max_connections=1,
        connection_timeout=0.3,
    ) as server:
        server_thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
        server_thread.start()
        try:
            with socket.create_connection(server.server_address, timeout=2) as stalled:
                stalled.sendall(b"GET /health HTTP/1.1\r\nHost:")
                assert started.wait(2)
                # A second socket is closed without starting another handler.
                with socket.create_connection(server.server_address, timeout=2) as excess:
                    try:
                        assert excess.recv(1) == b""
                    except ConnectionResetError:
                        pass  # Windows may reset instead of FIN on an unread socket.
                assert stalled.recv(1) == b""
                assert finished.wait(2)
            assert server._connections.acquire(timeout=2)
            server._connections.release()
            # The timed-out connection releases its slot; a real GET now works.
            with socket.create_connection(server.server_address, timeout=2) as next_request:
                next_request.sendall(b"GET /health HTTP/1.1\r\nHost: localhost\r\n\r\n")
                assert next_request.recv(4096).startswith(b"HTTP/1.0 ")
        finally:
            server.shutdown()
            server_thread.join(timeout=2)
