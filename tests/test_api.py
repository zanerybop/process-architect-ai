"""HTTP transport/storage tests; injected fixture runner is NOT sandbox evidence."""

import asyncio
import json
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient
from lxml import etree

from backend.app.config import ROOT, Settings
from backend.app.main import MAX_REQUEST_BYTES, create_app
from backend.app.runner import ContainerRunner
from backend.app.serialization import NS
from tests.auth_helpers import authorize, authorize_async
from worker.interpreter import interpret

CASES = json.loads((ROOT / "examples/cases.json").read_text(encoding="utf-8"))["cases"]
TEXT = CASES[0]["text"]
SECRET = "test-transport-secret-never-forward"


class TrustedFixtureRunner:
    """Explicit in-process unit transport, unavailable in application configuration."""

    def __init__(self):
        self.calls = []

    async def health(self):
        return True

    async def run(self, code, source_text):
        self.calls.append((code, source_text))
        return interpret(code, source_text)


def settings_for(tmp_path, **overrides):
    return Settings(
        _env_file=None,
        data_dir=tmp_path,
        app_mode=overrides.pop("app_mode", "mock"),
        llm_api_key=overrides.pop("llm_api_key", SECRET),
        llm_model=overrides.pop("llm_model", ""),
        llm_api_base_url=overrides.pop("llm_api_base_url", ""),
        **overrides,
    )


@pytest.fixture
def client(tmp_path):
    runner = TrustedFixtureRunner()
    app = create_app(settings_for(tmp_path), runner=runner)
    with TestClient(app) as connection:
        authorize(connection)
        yield connection, runner


def generated(client):
    response = client.post("/api/generate", json={"description": TEXT})
    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "ready", result["issues"]
    return result


def test_all_generation_artifact_and_validation_paths(client):
    connection, runner = client
    examples = connection.get("/api/examples").json()
    assert examples["origin"] == "synthetic_project_test_cases"
    assert len(examples["cases"]) == 21
    assert {item["id"] for item in examples["cases"]} >= {"P01", "F01_nested_timer", "F06_conversations"}
    result = generated(connection)
    assert result["mode"] == "mock"
    assert result["checks"]["import"] == "not_run"
    assert len(runner.calls) == 1
    assert runner.calls[0] == (result["python_code"], TEXT)
    assert result["ir"]["source_text"] == TEXT
    assert connection.get(f"/api/generations/{result['id']}").json() == result
    validation = connection.post("/api/validate", json={"xml": result["bpmn_xml"]}).json()
    assert all(validation["checks"][key] == "passed" for key in ("graph", "xsd", "layout"))
    for kind in ("source", "python", "ir", "xml", "checks", "metadata"):
        artifact = connection.get(f"/api/generations/{result['id']}/artifacts/{kind}")
        assert artifact.status_code == 200
        assert artifact.headers["x-content-type-options"] == "nosniff"
        assert SECRET not in artifact.text
    assert SECRET not in json.dumps(result)
    assert SECRET not in str(runner.calls)
    metadata = connection.get(f"/api/generations/{result['id']}/artifacts/metadata").json()
    assert metadata["mode"] == "mock" and metadata["provider"] == "fixture"


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_all_fifteen_synthetic_cases_through_api_unit_transport(client, case):
    connection, runner = client
    response = connection.post("/api/generate", json={"description": case["text"]})
    assert response.status_code == 200
    result = response.json()
    assert result["mode"] == "mock"
    assert result["status"] == case["expected_status"], result["issues"]
    if case["expected_status"] == "ready":
        assert result["python_code"] and result["ir"] and result["bpmn_xml"]
        assert all(result["checks"][gate] == "passed" for gate in ("code", "graph", "xsd", "layout"))
        assert result["checks"]["import"] == "not_run"
        assert len(runner.calls) == 1
    else:
        assert result["bpmn_xml"] is None and runner.calls == []
    assert connection.get(f"/api/generations/{result['id']}").json()["status"] == case["expected_status"]


def test_revision_stores_submitted_name_and_coordinates_without_relayout(client):
    connection, _ = client
    original = generated(connection)
    root = etree.fromstring(original["bpmn_xml"].encode())
    task = root.find("bpmn:process/bpmn:task", NS)
    if task is None:
        task = root.find("bpmn:process/bpmn:userTask", NS)
    assert task is not None
    task.set("name", "Проверка после ручной правки")
    shape = root.xpath("//bpmndi:BPMNShape[@bpmnElement=$id]/dc:Bounds", namespaces=NS, id=task.get("id"))[0]
    moved_x = float(shape.get("x")) + 27
    shape.set("x", str(moved_x))
    current_xml = etree.tostring(root, encoding="unicode")
    payload = {"xml": current_xml, "base_revision_id": original["revision_id"]}
    endpoint = f"/api/generations/{original['id']}/revisions"
    response = connection.put(endpoint, json=payload)
    assert response.status_code == 200, response.text
    head = response.json()
    assert head["revision_id"] != original["revision_id"] and head["audit_stale"] is True
    current = connection.get(f"/api/generations/{original['id']}").json()
    assert current["bpmn_xml"] == current_xml
    assert current["python_code"] == original["python_code"]
    assert current["ir"] == original["ir"]
    assert connection.put(endpoint, json=payload).status_code == 409
    assert connection.get(f"/api/generations/{original['id']}/artifacts/xml").text == original["bpmn_xml"]
    assert (
        connection.post("/api/validate", json={"xml": current["bpmn_xml"]}).json()["checks"]["xsd"]
        == "passed"
    )


def test_invalid_revision_preserves_last_success(client):
    connection, _ = client
    original = generated(connection)
    response = connection.put(
        f"/api/generations/{original['id']}/revisions",
        json={
            "xml": "<broken/>",
            "base_revision_id": original["revision_id"],
        },
    )
    assert response.status_code == 422
    assert connection.get(f"/api/generations/{original['id']}").json()["bpmn_xml"] == original["bpmn_xml"]


def test_unknown_mock_description_does_not_run_demo(client):
    connection, runner = client
    response = connection.post("/api/generate", json={"description": "Совсем новый процесс"})
    result = response.json()
    assert result["status"] == "error" and result["mode"] == "mock"
    assert result["issues"][0]["code"] == "MOCK_UNKNOWN_TEXT"
    assert result["bpmn_xml"] is None and runner.calls == []


@pytest.mark.parametrize("case_id,status", [("P07", "needs_clarification"), ("P14", "needs_clarification")])
def test_missing_business_facts_are_clarified_even_when_notation_is_supported(client, case_id, status):
    connection, runner = client
    text = next(case["text"] for case in CASES if case["id"] == case_id)
    result = connection.post("/api/generate", json={"description": text}).json()
    assert result["status"] == status and result["bpmn_xml"] is None
    assert runner.calls == []


def test_resource_unsupported_state_is_explicit_without_executing_python(tmp_path):
    from backend.app.llm import ProviderResult, parse_envelope

    class ResourceLimitedProvider:
        provider, model = "test_transport", "resource_limit_fixture"

        async def generate(self, description, feedback=None):
            return ProviderResult(parse_envelope({
                "mode": "unsupported", "reasons": ["Превышен ресурсный лимит размера программы."],
            }, description), self.provider, self.model)

    runner = TrustedFixtureRunner()
    app = create_app(settings_for(tmp_path), runner=runner, provider=ResourceLimitedProvider())
    with TestClient(app) as connection:
        authorize(connection)
        result = connection.post("/api/generate", json={"description": TEXT}).json()
    assert result["status"] == "unsupported" and result["bpmn_xml"] is None
    assert runner.calls == []


def test_missing_worker_is_error_without_local_interpreter_fallback(tmp_path):
    app = create_app(settings_for(tmp_path))
    assert isinstance(app.state.runner, ContainerRunner)
    with patch("httpx.AsyncClient.post", side_effect=httpx.ConnectError("unavailable")):
        connection = TestClient(app)
        authorize(connection)
        result = connection.post("/api/generate", json={"description": TEXT}).json()
    assert result["status"] == "error" and result["bpmn_xml"] is None
    assert result["issues"][0]["code"] == "RUNNER_UNAVAILABLE"
    assert result["attempt_count"] == 1


def test_missing_live_key_is_error_not_mock_fallback(tmp_path):
    runner = TrustedFixtureRunner()
    app = create_app(settings_for(tmp_path, app_mode="live", llm_api_key=""), runner=runner)
    connection = TestClient(app)
    authorize(connection)
    result = connection.post("/api/generate", json={"description": TEXT}).json()
    assert result["mode"] == "live" and result["status"] == "error"
    assert result["issues"][0]["code"] == "LLM_NOT_CONFIGURED"
    assert runner.calls == []


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"description": ""},
        {"description": " \n"},
        {"description": "x" * 20_001},
        {"description": 123},
        {"description": {"secret": SECRET}},
        {"description": TEXT, "api_key": SECRET},
    ],
)
def test_request_boundaries_without_input_echo(client, payload):
    connection, runner = client
    response = connection.post("/api/generate", json=payload)
    assert response.status_code == 422
    assert SECRET not in response.text
    assert response.json()["issues"][0]["code"] == "INVALID_REQUEST"
    assert runner.calls == []


def test_xml_byte_limit_and_total_request_limit(client):
    connection, _ = client
    response = connection.post("/api/validate", json={"xml": "я" * 1_000_001})
    assert response.status_code == 422
    oversized = b"x" * (MAX_REQUEST_BYTES + 1)
    assert connection.post("/api/generate", content=oversized).status_code == 413
    assert (
        connection.post("/api/generate", content=oversized, headers={"content-length": "1"}).status_code
        == 413
    )


def test_invalid_xml_and_external_entities_are_controlled(client):
    connection, _ = client
    for xml in ("<broken", '<!DOCTYPE x [<!ENTITY e SYSTEM "file:///private">]><x>&e;</x>'):
        result = connection.post("/api/validate", json={"xml": xml}).json()
        assert result["checks"]["xsd"] == "failed"


def test_unknown_ids_and_artifact_traversal_return_404(client):
    connection, _ = client
    for path in (
        "/api/generations/not-an-id",
        "/api/generations/" + "0" * 32,
        "/api/generations/" + "0" * 32 + "/artifacts/python",
    ):
        assert connection.get(path).status_code == 404
    result = generated(connection)
    for kind in ("invalid", "%2e%2e%2f.env"):
        assert connection.get(f"/api/generations/{result['id']}/artifacts/{kind}").status_code == 404


def test_generate_rate_limit(client):
    connection, _ = client
    for _ in range(12):
        assert connection.post("/api/generate", json={"description": "Unknown fixture"}).status_code == 200
    blocked = connection.post("/api/generate", json={"description": "Unknown fixture"})
    assert blocked.status_code == 429 and blocked.json()["issues"][0]["code"] == "RATE_LIMIT"


@pytest.mark.asyncio
async def test_generation_concurrency_limit(tmp_path):
    ready, release = asyncio.Event(), asyncio.Event()

    class BlockingRunner(TrustedFixtureRunner):
        count = 0

        async def run(self, code, source_text):
            self.count += 1
            if self.count == 2:
                ready.set()
            await release.wait()
            return await super().run(code, source_text)

    app = create_app(settings_for(tmp_path), runner=BlockingRunner())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as connection:
        await authorize_async(connection, app)
        active = [
            asyncio.create_task(connection.post("/api/generate", json={"description": TEXT}))
            for _ in range(2)
        ]
        try:
            await asyncio.wait_for(ready.wait(), timeout=2)
            blocked = await connection.post("/api/generate", json={"description": TEXT})
            assert blocked.status_code == 429
            assert blocked.json()["issues"][0]["code"] == "GENERATION_BUSY"
        finally:
            release.set()
            responses = await asyncio.gather(*active)
        assert all(response.json()["status"] == "ready" for response in responses)


@pytest.mark.parametrize("response_format", ["json_object", "json_schema"])
@pytest.mark.parametrize("reasoning_effort", ["auto", "none", "low", "medium", "high"])
def test_live_provider_settings_are_passed_without_exposing_secret(
    tmp_path, response_format, reasoning_effort,
):
    app = create_app(
        settings_for(
            tmp_path,
            app_mode="live",
            llm_max_tokens=321,
            llm_token_parameter="max_tokens",
            llm_response_format=response_format,
            llm_reasoning_effort=reasoning_effort,
        ),
        runner=TrustedFixtureRunner(),
    )
    assert app.state.service.provider.max_tokens == 321
    assert app.state.service.provider.token_parameter == "max_tokens"
    assert app.state.service.provider.response_format == response_format
    assert app.state.service.provider.reasoning_effort == reasoning_effort


def test_request_id_is_correlation_only(client):
    connection, _ = client
    result = connection.post("/api/generate", json={"description": TEXT, "request_id": "request-1"}).json()
    assert result["status"] == "ready" and result["request_id"] == "request-1"
