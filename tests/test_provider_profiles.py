"""Offline HTTP contracts only: no test here invokes or certifies a Yandex model."""

import json

import httpx
import pytest

from backend.app.domain import PipelineError
from backend.app.llm import BuildEnvelope, LiveProvider
from tests.test_llm import BUILD, CANARY, SOURCE, completion


def yandex(handler, **overrides):
    config = {
        "base_url": "",
        "model": "gpt://synthetic-folder/model-provided-by-organizer",
        "api_key": CANARY,
        "profile": "yandex_ai_studio",
        "project": "synthetic-folder",
        "response_format": "json_schema",
    }
    return LiveProvider(**(config | overrides), transport=httpx.MockTransport(handler))


@pytest.mark.parametrize(
    "envelope",
    [
        BUILD,
        {"mode": "clarify", "questions": [{"question": "Кто исполнитель?", "source_quote": "Менеджер"}]},
        {"mode": "unsupported", "reasons": ["Таймер вне профиля."]},
    ],
)
async def test_yandex_profile_uses_documented_http_contract_and_common_envelope(envelope):
    calls = []

    def handler(request):
        calls.append(request)
        assert str(request.url) == "https://ai.api.cloud.yandex.net/v1/chat/completions"
        assert request.headers["Authorization"] == "Api-Key " + CANARY
        assert request.headers["OpenAI-Project"] == "synthetic-folder"
        body = json.loads(request.content)
        assert body["model"] == "gpt://synthetic-folder/model-provided-by-organizer"
        assert body["max_completion_tokens"] == 8000
        assert "max_tokens" not in body and "reasoning_effort" not in body and "tools" not in body
        assert body["response_format"]["type"] == "json_schema"
        assert body["stream"] is False
        assert CANARY not in request.content.decode()
        return httpx.Response(200, json=completion(envelope))

    profile = yandex(handler)
    result = await profile.generate(SOURCE)
    assert result.envelope.model_dump() == envelope
    assert result.provider == "yandex_ai_studio"
    assert len(calls) == 1
    assert profile.profile_metadata["profile_live_acceptance"] == "not_live_verified"
    assert CANARY not in str(profile.profile_metadata)
    assert "synthetic-folder" not in str(profile.profile_metadata)


async def test_provider_switch_keeps_prompt_and_pipeline_contract_identical():
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=completion())

    generic = LiveProvider(
        "https://local-model.invalid/v1",
        "local-model",
        CANARY,
        response_format="json_schema",
        transport=httpx.MockTransport(handler),
    )
    result_a = await generic.generate(SOURCE)
    result_b = await yandex(handler).generate(SOURCE)
    assert isinstance(result_a.envelope, BuildEnvelope) and isinstance(result_b.envelope, BuildEnvelope)
    assert result_a.envelope.model_dump() == result_b.envelope.model_dump()
    del requests[0]["model"], requests[1]["model"]
    assert requests[0] == requests[1]


async def test_profile_preserves_explicit_endpoint_auth_and_model_parameter_overrides():
    def handler(request):
        assert str(request.url) == "https://organizer-gateway.invalid/v1/chat/completions"
        assert request.headers["Authorization"] == "Bearer " + CANARY
        assert "OpenAI-Project" not in request.headers
        body = json.loads(request.content)
        assert body["max_tokens"] == 3100 and "max_completion_tokens" not in body
        assert body["response_format"] == {"type": "json_object"}
        assert body["reasoning_effort"] == "low"
        assert body["model"] == "organizer-configured-alias"
        return httpx.Response(200, json=completion())

    await yandex(
        handler,
        base_url="https://organizer-gateway.invalid/v1",
        auth_scheme="bearer",
        project="",
        model="organizer-configured-alias",
        max_tokens=3100,
        token_parameter="max_tokens",
        response_format="json_object",
        reasoning_effort="low",
    ).generate(SOURCE)


@pytest.mark.parametrize("status,code", [(401, "LLM_AUTH"), (429, "LLM_RATE_LIMIT"), (400, "LLM_HTTP_ERROR")])
async def test_yandex_errors_remain_bounded_and_never_trigger_hidden_model_fallback(status, code):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, text=CANARY)

    with pytest.raises(PipelineError) as caught:
        await yandex(handler).generate(SOURCE)
    assert caught.value.issue.code == code
    assert CANARY not in str(caught.value)
    assert len(calls) == 1


@pytest.mark.parametrize(
    "overrides,code",
    [
        ({"api_key": ""}, "LLM_NOT_CONFIGURED"),
        ({"model": ""}, "LLM_NOT_CONFIGURED"),
        ({"profile": "unknown"}, "LLM_NOT_CONFIGURED"),
        ({"auth_scheme": "made-up"}, "LLM_CONFIG_INVALID"),
        ({"project": "folder\nInjected: yes"}, "LLM_CONFIG_INVALID"),
    ],
)
async def test_missing_yandex_access_never_sends_a_request(overrides, code):
    def handler(_request):
        pytest.fail("Missing or invalid access must fail before any network request")

    with pytest.raises(PipelineError) as caught:
        await yandex(handler, **overrides).generate(SOURCE)
    assert caught.value.issue.code == code
