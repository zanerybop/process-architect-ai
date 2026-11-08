"""Strict mock/live envelope providers; no execution, XML creation, or hidden retries.

Explicit Chat Completions JSON object/schema modes share local discriminated
validation because endpoints vary in schema support. Credentials enter only as an
explicit constructor argument and never appear in returned metadata or errors.
"""

import asyncio
import json
import math
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Annotated, Literal
from urllib.parse import urlsplit

import httpx
from pydantic import Field, StringConstraints, TypeAdapter, ValidationError, field_validator

from backend.app.domain import Model, PipelineError
from backend.app.examples import fixture_program, load_examples
from backend.app.prompts import build_messages
from backend.app.providers import PROFILES, AuthScheme, ProfileName

MAX_ENVELOPE_BYTES = 262144
MAX_RESPONSE_BYTES = 1_000_000
MAX_CODE_BYTES = 65536
ROOT = Path(__file__).resolve().parents[2]
NonEmptyText = Annotated[str, StringConstraints(min_length=1, max_length=2000, pattern=r"\S")]
Assumption = Annotated[str, StringConstraints(min_length=1, max_length=1000, pattern=r"\S")]
Quote = Annotated[str, StringConstraints(min_length=1, max_length=4000, pattern=r"\S")]


class Question(Model):
    question: NonEmptyText
    source_quote: Quote


class BuildEnvelope(Model):
    mode: Literal["build"]
    python_code: str = Field(min_length=1, max_length=MAX_CODE_BYTES)
    summary: NonEmptyText
    assumptions: list[Assumption] = Field(max_length=10)

    @field_validator("python_code")
    @classmethod
    def bounded_code(cls, value):
        if not value.strip() or len(value.encode("utf-8")) > MAX_CODE_BYTES:
            raise ValueError("Код пуст или превышает ограничение UTF-8.")
        return value


class ClarifyEnvelope(Model):
    mode: Literal["clarify"]
    questions: list[Question] = Field(min_length=1, max_length=10)


class UnsupportedEnvelope(Model):
    mode: Literal["unsupported"]
    reasons: list[NonEmptyText] = Field(min_length=1, max_length=10)


Envelope = Annotated[BuildEnvelope | ClarifyEnvelope | UnsupportedEnvelope, Field(discriminator="mode")]
ENVELOPE_ADAPTER = TypeAdapter(Envelope)


def _wire_envelope_schema() -> dict:
    """Inline the fixed envelope schema for grammar engines, retaining local checks.

    llama.cpp-based endpoints have limited nested reference and regex support.
    Their wire schema need not duplicate the non-whitespace pattern: the unchanged
    local envelope validator still enforces it and every other acceptance rule.
    """
    schema = ENVELOPE_ADAPTER.json_schema()
    definitions = schema.get("$defs", {})

    def inline(value):
        if isinstance(value, list):
            return [inline(item) for item in value]
        if isinstance(value, dict):
            if "$ref" in value:
                return inline(definitions[value["$ref"].removeprefix("#/$defs/")])
            return {
                key: inline(item)
                for key, item in value.items()
                if key not in {"$defs", "discriminator", "title", "pattern"}
            }
        return value

    return {"type": "object", **inline(schema)}


@dataclass(frozen=True)
class ProviderResult:
    envelope: Envelope
    provider: str
    model: str | None
    usage: dict[str, int] | None = None
    elapsed_seconds: float = 0.0


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Повторный ключ JSON.")
        result[key] = value
    return result


def _invalid_constant(_value):
    raise ValueError("Нечисловая константа JSON запрещена.")


def _strict_json(value):
    return json.loads(value, object_pairs_hook=_unique_object, parse_constant=_invalid_constant)


def _reject_secret_echo(value, secret):
    """Check decoded JSON strings too: Unicode escapes must not bypass redaction."""
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, str) and secret in item:
            raise PipelineError(
                "LLM_SECRET_ECHO", "Ответ провайдера отклонён из-за небезопасного содержимого."
            )
        if isinstance(item, dict):
            pending.extend(item.keys())
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)


def parse_envelope(value: dict | str, source_text: str | None = None) -> Envelope:
    """Reject malformed output without repair heuristics or echoing untrusted data."""
    try:
        encoded = value.encode("utf-8") if isinstance(value, str) else json.dumps(value).encode("utf-8")
        if len(encoded) > MAX_ENVELOPE_BYTES:
            raise ValueError("Envelope exceeds limit")
        data = _strict_json(value) if isinstance(value, str) else value
        json.dumps(data, ensure_ascii=False).encode("utf-8")
        envelope = ENVELOPE_ADAPTER.validate_python(data)
        if isinstance(envelope, ClarifyEnvelope) and source_text is not None:
            if any(question.source_quote not in source_text for question in envelope.questions):
                raise ValueError("Question quote absent from source")
        return envelope
    except (ValidationError, ValueError, TypeError, RecursionError, UnicodeError):
        raise PipelineError(
            "LLM_ENVELOPE", "Ответ LLM не соответствует контракту JSON или содержит неверную цитату."
        ) from None


def _check_input(description, feedback):
    if not isinstance(description, str) or not description.strip():
        raise PipelineError("EMPTY_DESCRIPTION", "Введите описание процесса.")
    if len(description) > 20000:
        raise PipelineError("INPUT_LIMIT", "Описание превышает 20000 символов.")
    if feedback is not None:
        try:
            valid = (isinstance(feedback, list) and len(feedback) <= 10
                     and all(isinstance(item, dict) for item in feedback)
                     and len(json.dumps(feedback, ensure_ascii=False).encode("utf-8")) <= 100000)
        except (TypeError, ValueError, RecursionError, UnicodeError):
            valid = False
        if not valid:
            raise PipelineError("LLM_FEEDBACK_LIMIT", "Список ошибок для исправления превышает лимит.")


class MockProvider:
    """Only an exact known synthetic description selects a labelled fixture."""

    mode = "mock"
    provider = "fixture"
    model = None

    def __init__(self):
        cases = load_examples()["cases"]
        self._cases = {case["text"]: case for case in cases}
        self._envelopes = json.loads((ROOT / "examples/envelopes.json").read_text(encoding="utf-8"))

    async def generate(self, description: str, feedback: list[dict] | None = None) -> ProviderResult:
        started = perf_counter()
        _check_input(description, feedback)
        case = self._cases.get(description)
        if case is None:
            raise PipelineError(
                "MOCK_UNKNOWN_TEXT", "Mock работает только с точными текстами известных примеров."
            )
        if case["expected_status"] == "ready":
            data = {
                "mode": "build",
                "python_code": fixture_program(case["id"]).read_text(encoding="utf-8"),
                "summary": f"Сохранённая ручная синтетическая fixture {case['id']}: {case['title']}.",
                "assumptions": [],
            }
        else:
            data = self._envelopes[case["id"]]
        envelope = parse_envelope(data, description)
        return ProviderResult(envelope, self.provider, self.model, elapsed_seconds=perf_counter() - started)


class LiveProvider:
    """A single compatible Chat Completions request per generate call, fail closed."""

    mode = "live"
    provider = "openai_compatible"

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str,
        timeout: float = 45,
        max_tokens: int = 8000,
        transport: httpx.AsyncBaseTransport | None = None,
        token_parameter: Literal["max_completion_tokens", "max_tokens"] = "max_completion_tokens",
        response_format: Literal["json_object", "json_schema"] = "json_object",
        reasoning_effort: Literal["auto", "none", "low", "medium", "high"] = "auto",
        profile: ProfileName = "openai_compatible",
        auth_scheme: AuthScheme = "auto",
        project: str = "",
    ):
        self.profile = profile
        selected = PROFILES.get(profile)
        self.base_url = base_url or (selected.default_base_url if selected else "")
        self.provider = selected.name if selected else "openai_compatible"
        self.model = model
        self._api_key = api_key
        self.timeout = timeout
        self.max_tokens = max_tokens
        self._transport = transport
        self.token_parameter = token_parameter
        self.response_format = response_format
        self.reasoning_effort = reasoning_effort
        self.auth_scheme = auth_scheme
        self.project = project

    @property
    def profile_metadata(self) -> dict:
        """No credential, project ID or user-supplied URL enters public metadata."""
        selected = PROFILES.get(self.profile)
        return {
            "name": selected.name if selected else "unknown",
            "protocol": "chat_completions",
            "profile_live_acceptance": selected.live_acceptance if selected else "not_live_verified",
            "token_parameter": self.token_parameter,
            "response_format": self.response_format,
            "reasoning_effort": self.reasoning_effort,
            "project_header_configured": bool(self.project),
        }

    def _check_config(self):
        config_values = (self.base_url, self.model, self._api_key)
        if not all(isinstance(value, str) and value.strip() for value in config_values):
            raise PipelineError(
                "LLM_NOT_CONFIGURED", "Для live задайте LLM_API_BASE_URL, LLM_MODEL и LLM_API_KEY."
            )
        try:
            url = urlsplit(self.base_url)
            valid = (url.scheme in {"http", "https"} and bool(url.hostname) and not url.username
                     and not url.password and not url.query and not url.fragment and len(self.model) <= 200
                     and not any(character in self._api_key for character in "\r\n")
                     and type(self.timeout) in (int, float) and math.isfinite(self.timeout)
                     and 0 < self.timeout <= 300 and type(self.max_tokens) is int
                     and 1 <= self.max_tokens <= 32768
                     and self.token_parameter in {"max_completion_tokens", "max_tokens"}
                     and self.response_format in {"json_object", "json_schema"}
                     and self.reasoning_effort in {"auto", "none", "low", "medium", "high"}
                     and self.profile in PROFILES and self.auth_scheme in {"auto", "bearer", "api_key"}
                     and isinstance(self.project, str) and len(self.project) <= 200
                     and all(" " <= character <= "~" for character in self.project))
            # Validate malformed ports without exposing the supplied URL in errors.
            url.port
        except (ValueError, TypeError):
            valid = False
        if not valid:
            raise PipelineError(
                "LLM_CONFIG_INVALID", "Проверьте URL, модель, timeout, лимит токенов и формат ответа LLM."
            )

    async def generate(self, description: str, feedback: list[dict] | None = None) -> ProviderResult:
        started = perf_counter()
        self._check_config()
        _check_input(description, feedback)
        request = {
            "model": self.model,
            "messages": build_messages(description, feedback),
            "response_format": {"type": "json_object"} if self.response_format == "json_object" else {
                "type": "json_schema",
                "json_schema": {
                    "name": "bpmn_generation",
                    "strict": True,
                    "schema": _wire_envelope_schema(),
                },
            },
            self.token_parameter: self.max_tokens,
            "stream": False,
        }
        if self.reasoning_effort != "auto":
            request["reasoning_effort"] = self.reasoning_effort
        scheme = PROFILES[self.profile].auth_scheme if self.auth_scheme == "auto" else self.auth_scheme
        headers = {
            "Authorization": ("Api-Key " if scheme == "api_key" else "Bearer ") + self._api_key,
            "Accept": "application/json",
        }
        if self.project:
            headers["OpenAI-Project"] = self.project
        try:
            # Both read/connect timeouts and an overall wall-time budget are required:
            # a peer sending one byte per read interval cannot extend the request forever.
            async with asyncio.timeout(self.timeout):
                async with httpx.AsyncClient(
                    timeout=self.timeout, transport=self._transport, follow_redirects=False, trust_env=False,
                ) as client:
                    async with client.stream(
                        "POST", self.base_url.rstrip("/") + "/chat/completions",
                        headers=headers,
                        json=request,
                    ) as response:
                        if response.status_code in {401, 403}:
                            raise PipelineError("LLM_AUTH", "Провайдер LLM отклонил авторизацию.")
                        if response.status_code == 429:
                            raise PipelineError("LLM_RATE_LIMIT", "Провайдер LLM сообщил о лимите запросов.")
                        if not 200 <= response.status_code < 300:
                            raise PipelineError("LLM_HTTP_ERROR", "Провайдер LLM вернул ошибку HTTP.")
                        body = bytearray()
                        async for chunk in response.aiter_bytes():
                            if len(body) + len(chunk) > MAX_RESPONSE_BYTES:
                                raise PipelineError(
                                    "LLM_RESPONSE_TOO_LARGE", "Ответ LLM превышает допустимый размер."
                                )
                            body.extend(chunk)
        except (TimeoutError, httpx.TimeoutException):
            raise PipelineError("LLM_TIMEOUT", "Превышено время ожидания ответа LLM.") from None
        except (httpx.HTTPError, ValueError):
            raise PipelineError("LLM_CONNECTION", "Не удалось обратиться к провайдеру LLM.") from None

        try:
            decoded = body.decode("utf-8")
            _reject_secret_echo(decoded, self._api_key)
            payload = _strict_json(decoded)
            _reject_secret_echo(payload, self._api_key)
            choices = payload["choices"]
            if not isinstance(choices, list) or len(choices) != 1:
                raise ValueError("Expected one choice")
            choice = choices[0]
            message = choice["message"]
            if message.get("refusal") or choice.get("finish_reason") == "content_filter":
                raise PipelineError("LLM_REFUSAL", "Провайдер не выполнил запрос; схема не создана.")
            if choice.get("finish_reason") == "length":
                raise PipelineError(
                    "LLM_TRUNCATED", "Ответ LLM обрезан лимитом токенов; полный код не получен."
                )
            if choice.get("finish_reason") != "stop" or not isinstance(message.get("content"), str):
                raise ValueError("Invalid completion")
            envelope = parse_envelope(message["content"], description)
            _reject_secret_echo(envelope.model_dump(), self._api_key)
            usage_data = payload.get("usage")
            usage = {key: usage_data[key] for key in ("prompt_tokens", "completion_tokens", "total_tokens")
                     if key in usage_data and type(usage_data[key]) is int and usage_data[key] >= 0
                     } if isinstance(usage_data, dict) else None
        except (ValueError, KeyError, TypeError, AttributeError, RecursionError, UnicodeError):
            raise PipelineError(
                "LLM_RESPONSE_INVALID", "Провайдер вернул некорректный формат Chat Completions."
            ) from None
        return ProviderResult(envelope, self.provider, self.model, usage or None, perf_counter() - started)
