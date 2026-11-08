import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.app.config import Settings
from backend.app.domain import PipelineError
from backend.app.service import GenerationService
from backend.app.storage import Storage
from worker.interpreter import interpret

CASES = json.loads(Path("examples/cases.json").read_text(encoding="utf-8"))["cases"]


class TrustedFixtureRunner:
    """Test double only. This deliberately does not prove container isolation."""

    calls = 0

    async def run(self, code, source_text):
        self.calls += 1
        return interpret(code, source_text)


class SequenceProvider:
    provider, model = "test_transport", "test_model"

    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    async def generate(self, description, feedback=None):
        self.calls.append(feedback)
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return SimpleNamespace(
            envelope=SimpleNamespace(mode="build", python_code=response, assumptions=[], summary="Тест"),
            usage=None,
        )


def service(tmp_path, provider, runner=None):
    return GenerationService(
        Settings(_env_file=None, data_dir=tmp_path),
        provider,
        runner or TrustedFixtureRunner(),
        Storage(tmp_path),
    )


async def test_repairs_stop_after_three_total_attempts(tmp_path):
    provider = SequenceProvider(["import os"] * 3)
    result = await service(tmp_path, provider).generate(CASES[0]["text"])
    assert result["status"] == "error"
    assert result["attempt_count"] == len(provider.calls) == 3
    assert result["checks"]["code"] == "failed"
    metadata = json.loads((tmp_path / result["id"] / "metadata.json").read_text(encoding="utf-8"))
    assert len(metadata["attempts"]) == 3
    assert all(attempt["error_codes"] == ["CODE_POLICY"] for attempt in metadata["attempts"])


async def test_repair_uses_new_python_and_preserves_actual_attempts(tmp_path):
    code = Path("examples/programs/P01.py").read_text(encoding="utf-8")
    provider = SequenceProvider(["import os", code])
    runner = TrustedFixtureRunner()
    result = await service(tmp_path, provider, runner).generate(CASES[0]["text"])
    assert result["status"] == "ready"
    assert result["attempt_count"] == 2 and runner.calls == 1
    assert result["python_code"] == code
    assert result["checks"]["import"] == "not_run"
    assert provider.calls[1][0]["issues"][0]["code"] == "CODE_POLICY"


async def test_unavailable_runner_is_not_repaired_or_bypassed(tmp_path):
    class Unavailable:
        async def run(self, code, source_text):
            raise PipelineError("RUNNER_UNAVAILABLE", "Worker недоступен")

    code = Path("examples/programs/P01.py").read_text(encoding="utf-8")
    provider = SequenceProvider([code])
    result = await service(tmp_path, provider, Unavailable()).generate(CASES[0]["text"])
    assert result["status"] == "error" and result["attempt_count"] == 1
    assert result["ir"] is None and result["bpmn_xml"] is None
    assert result["issues"][0]["code"] == "RUNNER_UNAVAILABLE"


async def test_serializer_failure_does_not_ask_model_to_change_graph(tmp_path, monkeypatch):
    code = Path("examples/programs/P01.py").read_text(encoding="utf-8")
    provider = SequenceProvider([code])

    def fail(ir):
        raise PipelineError("LAYOUT_FAILURE", "Ошибка компоновки")

    monkeypatch.setattr("backend.app.service.serialize", fail)
    result = await service(tmp_path, provider).generate(CASES[0]["text"])
    assert result["status"] == "error" and len(provider.calls) == 1


async def test_graph_error_can_be_repaired(tmp_path):
    code = Path("examples/programs/P01.py").read_text(encoding="utf-8")
    provider = SequenceProvider(['bpmn.process(id="p", name="p")', code])
    result = await service(tmp_path, provider).generate(CASES[0]["text"])
    assert result["status"] == "ready" and result["attempt_count"] == 2


async def test_clarification_after_repair_has_no_stale_technical_error(tmp_path):
    from backend.app.llm import ProviderResult, parse_envelope

    class ClarifyingProvider:
        provider, model = "test_transport", "test_model"
        count = 0

        async def generate(self, description, feedback=None):
            self.count += 1
            if self.count == 1:
                raise PipelineError("LLM_ENVELOPE", "Некорректный ответ")
            return ProviderResult(
                parse_envelope(
                    {
                        "mode": "clarify",
                        "questions": [
                            {"question": "Кто проверяет заявку?", "source_quote": "проверяет заявку"}
                        ],
                    },
                    description,
                ),
                self.provider,
                self.model,
            )

    result = await service(tmp_path, ClarifyingProvider()).generate(CASES[0]["text"])
    assert result["status"] == "needs_clarification" and result["attempt_count"] == 2
    assert result["issues"] == [] and result["ir"] is None and result["python_code"] is None
    assert set(result["checks"].values()) == {"not_run"}
    metadata = json.loads((tmp_path / result["id"] / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["attempts"][0]["error_codes"] == ["LLM_ENVELOPE"]
    assert metadata["attempts"][1]["error_codes"] == []


@pytest.mark.parametrize(
    "change,expected",
    [
        (('source_quote="менеджер проверяет заявку"', "source_quote=None"), "ACTION_WITHOUT_SOURCE"),
        (('name="Менеджер"', 'name="Роль не указана"'), "UNASSIGNED_ROLE"),
    ],
)
async def test_unconfirmed_action_or_role_never_becomes_ready(tmp_path, change, expected):
    original = Path("examples/programs/P01.py").read_text(encoding="utf-8")
    code = original.replace(*change)
    assert code != original
    result = await service(tmp_path, SequenceProvider([code])).generate(CASES[0]["text"])
    assert result["status"] == "needs_clarification"
    assert result["bpmn_xml"] is None and result["ir"] is not None
    assert result["checks"]["graph"] == "passed" and result["checks"]["xsd"] == "not_run"
    assert result["attempt_count"] == 1 and result["questions"]
    assert any(issue["code"] == expected for issue in result["issues"])
    assert all(question["source_quote"] in CASES[0]["text"] for question in result["questions"])
