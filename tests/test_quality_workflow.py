"""API integration of progress, answers and revision review with explicit test doubles.

The bounded interpreter only executes repository-owned fixtures here; these tests
neither call a real model nor claim worker/container isolation.
"""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from lxml import etree

from backend.app.llm import parse_envelope
from backend.app.main import create_app
from backend.app.serialization import NS
from backend.app.service import GenerationService
from tests.auth_helpers import AUTH_HEADERS, authorize
from tests.test_api import TEXT, TrustedFixtureRunner, generated, settings_for


class GatedProvider:
    provider, model = "workflow_test", "fixture"

    def __init__(self):
        self.entered, self.release = asyncio.Event(), asyncio.Event()

    async def generate(self, description, feedback=None):
        self.entered.set()
        await self.release.wait()
        return SimpleNamespace(
            envelope=parse_envelope(
                {
                    "mode": "build",
                    "summary": "Тестовый процесс",
                    "assumptions": [],
                    "python_code": Path("examples/programs/P01.py").read_text(encoding="utf-8"),
                },
                description,
            ),
            usage=None,
        )


class GatedRunner(TrustedFixtureRunner):
    def __init__(self):
        super().__init__()
        self.entered, self.release = asyncio.Event(), asyncio.Event()

    async def run(self, code, source_text):
        self.entered.set()
        await self.release.wait()
        return await super().run(code, source_text)


async def _authorize_async(client, app, username):
    credentials = {"username": username, "password": "synthetic-workflow-password"}
    app.state.auth.create_user(**credentials)
    login = await client.post("/api/auth/login", json=credentials, headers=AUTH_HEADERS)
    assert login.status_code == 200
    client.headers["X-CSRF-Token"] = login.json()["csrf_token"]


async def test_progress_reports_real_waiting_phases_and_is_scoped_to_account(tmp_path, monkeypatch):
    provider, runner = GatedProvider(), GatedRunner()
    app = create_app(settings_for(tmp_path), provider=provider, runner=runner)
    phases = []
    original_progress = GenerationService._progress

    def observe(self, phase, attempt=0):
        phases.append((phase, attempt))
        original_progress(self, phase, attempt)

    monkeypatch.setattr(GenerationService, "_progress", observe)
    async with (
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as owner,
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as other,
    ):
        endpoint = "/api/generation-progress/same-request-id"
        assert (await owner.get(endpoint)).status_code == 401
        await _authorize_async(owner, app, "progress-owner")
        await _authorize_async(other, app, "progress-other")
        assert (await owner.get(endpoint)).status_code == 404
        task = asyncio.create_task(
            owner.post(
                "/api/generate",
                json={
                    "description": TEXT,
                    "request_id": "same-request-id",
                },
            )
        )
        try:
            await asyncio.wait_for(provider.entered.wait(), 2)
            model_progress = (await owner.get(endpoint)).json()
            assert model_progress["phase"] == "model" and model_progress["attempt"] == 1
            assert set(model_progress) == {"phase", "attempt", "elapsed_seconds"}
            assert model_progress["elapsed_seconds"] >= 0
            assert (await other.get(endpoint)).status_code == 404
            provider.release.set()
            await asyncio.wait_for(runner.entered.wait(), 2)
            worker_progress = (await owner.get(endpoint)).json()
            assert worker_progress["phase"] == "worker" and worker_progress["attempt"] == 1
            assert (await other.get(endpoint)).status_code == 404
            runner.release.set()
            result = await asyncio.wait_for(task, 5)
            assert result.status_code == 200 and result.json()["status"] == "ready", result.text
            assert (await owner.get(endpoint)).json()["phase"] == "ready"
            assert (await other.get(endpoint)).status_code == 404
            assert phases == [(phase, 1) for phase in ("model", "code", "worker", "review", "xml", "ready")]
            # Identical request IDs in another account create a separate progress record.
            other_result = await other.post(
                "/api/generate",
                json={
                    "description": TEXT,
                    "request_id": "same-request-id",
                },
            )
            assert other_result.json()["status"] == "ready"
            assert other_result.json()["id"] != result.json()["id"]
            assert (await other.get(endpoint)).json()["phase"] == "ready"
            assert (await owner.get(endpoint)).json()["phase"] == "ready"
        finally:
            provider.release.set()
            runner.release.set()
            if not task.done():
                task.cancel()


SOURCE = "Аналитик проверяет документ. Что делать после проверки, пока не определено."
QUOTE = "Что делать после проверки, пока не определено."
QUESTION = "Что делает аналитик после проверки?"
ANSWER = "После проверки аналитик регистрирует документ и завершает процесс."
ANSWER_CODE = """bpmn.process(id="process", name="Документ")
bpmn.pool(id="pool", name="Компания", process_id="process")
bpmn.lane(id="lane", name="Аналитик", process_id="process")
bpmn.start(id="start", process_id="process", lane_id="lane")
bpmn.task(id="check", name="Проверить документ", process_id="process", lane_id="lane",
          source_quote="Аналитик проверяет документ")
bpmn.task(id="register", name="Зарегистрировать документ", process_id="process", lane_id="lane",
          source_quote="аналитик регистрирует документ")
bpmn.end(id="end", process_id="process", lane_id="lane")
bpmn.flow(id="f1", source="start", target="check")
bpmn.flow(id="f2", source="check", target="register")
bpmn.flow(id="f3", source="register", target="end")
"""


class ClarifyingProvider:
    provider, model = "workflow_test", "fixture"

    def __init__(self):
        self.descriptions = []

    async def generate(self, description, feedback=None):
        self.descriptions.append(description)
        envelope = (
            {"mode": "clarify", "questions": [{"question": QUESTION, "source_quote": QUOTE}]}
            if len(self.descriptions) == 1
            else {
                "mode": "build",
                "python_code": ANSWER_CODE,
                "summary": "Регистрация после проверки",
                "assumptions": [],
            }
        )
        return SimpleNamespace(envelope=parse_envelope(envelope, description), usage=None)


def test_clarification_answers_build_and_preserve_original_and_effective_source(tmp_path):
    provider, runner = ClarifyingProvider(), TrustedFixtureRunner()
    app = create_app(settings_for(tmp_path), provider=provider, runner=runner)
    with TestClient(app) as client:
        authorize(client)
        first = client.post("/api/generate", json={"description": SOURCE}).json()
        assert first["status"] == "needs_clarification" and not runner.calls
        answers = [{"question": QUESTION, "answer": ANSWER, "source_quote": QUOTE}]
        answer_response = client.post(
            "/api/generate",
            json={
                "description": SOURCE,
                "clarification_answers": answers,
            },
        )
        assert answer_response.status_code == 200, answer_response.text
        result = answer_response.json()
        assert result["status"] == "ready", result["issues"]
        assert result["source_context"] == {"description": SOURCE, "clarification_answers": answers}
        assert len(provider.descriptions) == 2
        effective = provider.descriptions[1]
        assert effective.startswith(SOURCE) and QUESTION in effective and ANSWER in effective
        assert result["ir"]["source_text"] == effective
        assert runner.calls == [(ANSWER_CODE, effective)]
        assert not any(issue["code"] == "SOURCE_UNCERTAINTY" for issue in result["issues"])
        stored = client.get(f"/api/generations/{result['id']}").json()
        assert stored["source_context"] == result["source_context"]
        assert client.get(f"/api/generations/{result['id']}/artifacts/source").text == effective
        initial = client.get(f"/api/generations/{first['id']}").json()
        assert initial["status"] == "needs_clarification" and initial["bpmn_xml"] is None
        metadata = client.get(f"/api/generations/{result['id']}/artifacts/metadata").json()
        assert metadata["attempts"][0]["ir"] == result["ir"]
        assert metadata["prompt_sha256"] and metadata["semantic_rules_version"]


@pytest.mark.parametrize(
    "answer",
    [
        {"question": QUESTION, "answer": "   ", "source_quote": QUOTE},
        {"question": QUESTION, "answer": ANSWER, "source_quote": "Несуществующая цитата"},
    ],
)
def test_invalid_answers_are_rejected_before_provider(tmp_path, answer):
    provider = ClarifyingProvider()
    with TestClient(
        create_app(settings_for(tmp_path), provider=provider, runner=TrustedFixtureRunner())
    ) as client:
        authorize(client)
        rejected = client.post(
            "/api/generate", json={"description": SOURCE, "clarification_answers": [answer]}
        )
        assert rejected.status_code == 422
        assert not provider.descriptions


def test_geometry_only_revision_keeps_audit_valid_and_preserves_exact_coordinates(tmp_path):
    with TestClient(create_app(settings_for(tmp_path), runner=TrustedFixtureRunner())) as client:
        authorize(client)
        original = generated(client)
        root = etree.fromstring(original["bpmn_xml"].encode())
        bounds = root.find(".//bpmndi:BPMNShape/dc:Bounds", NS)
        changed_x = float(bounds.get("x")) + 41.75
        bounds.set("x", str(changed_x))
        xml = etree.tostring(root, encoding="unicode")
        review = client.post("/api/review", json={"xml": xml, "generation_id": original["id"]}).json()
        assert review["semantic_diff"]["geometry_only"] is True
        assert review["semantic_diff"]["status"] == "unchanged"
        assert review["semantic_diff"]["changes"] == []
        revision = client.put(
            f"/api/generations/{original['id']}/revisions",
            json={
                "xml": xml,
                "base_revision_id": original["revision_id"],
            },
        )
        assert revision.status_code == 200, revision.text
        assert revision.json()["audit_stale"] is False
        current = client.get(f"/api/generations/{original['id']}").json()
        assert current["bpmn_xml"] == xml and current["audit_stale"] is False
        assert current["ir"] == original["ir"] and current["python_code"] == original["python_code"]
        assert current["semantic_diff"]["geometry_only"] is True
        assert (
            json.loads(client.get(f"/api/generations/{original['id']}/artifacts/ir").text) == original["ir"]
        )


def test_content_change_keeps_geometry_only_flag_false_even_after_moving_again(tmp_path):
    with TestClient(create_app(settings_for(tmp_path), runner=TrustedFixtureRunner())) as client:
        authorize(client)
        original = generated(client)
        root = etree.fromstring(original["bpmn_xml"].encode())
        task = root.find(".//bpmn:userTask", NS)
        if task is None:
            task = root.find(".//bpmn:task", NS)
        task.set("name", "Совершенно другое действие")
        for index in range(2):
            bounds = root.find(".//bpmndi:BPMNShape/dc:Bounds", NS)
            bounds.set("x", str(float(bounds.get("x")) + index + 10))
            xml = etree.tostring(root, encoding="unicode")
            review = client.post("/api/review", json={"xml": xml, "generation_id": original["id"]}).json()
            assert review["semantic_diff"]["geometry_only"] is False
            assert review["semantic_diff"]["status"] == "changed"
            assert any(
                change["element_id"] == task.get("id") and "name" in change["fields"]
                for change in review["semantic_diff"]["changes"]
            )
