import pytest
from pydantic import ValidationError

from backend.app.config import Settings


def test_reasoning_effort_defaults_to_omitted_provider_setting(monkeypatch):
    monkeypatch.delenv("LLM_REASONING_EFFORT", raising=False)
    assert Settings(_env_file=None).llm_reasoning_effort == "auto"


@pytest.mark.parametrize("effort", ["auto", "none", "low", "medium", "high"])
def test_reasoning_effort_environment_is_explicit(monkeypatch, effort):
    monkeypatch.setenv("LLM_REASONING_EFFORT", effort)
    assert Settings(_env_file=None).llm_reasoning_effort == effort


@pytest.mark.parametrize("effort", ["", "off", "invalid", "null"])
def test_invalid_reasoning_effort_environment_is_rejected(monkeypatch, effort):
    monkeypatch.setenv("LLM_REASONING_EFFORT", effort)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_yandex_profile_is_opt_in_and_never_fabricates_credentials(monkeypatch):
    for key in ("LLM_API_KEY", "LLM_MODEL", "LLM_API_BASE_URL", "LLM_PROVIDER_PROFILE", "LLM_PROJECT"):
        monkeypatch.delenv(key, raising=False)
    generic = Settings(_env_file=None)
    assert generic.llm_provider_profile == "openai_compatible"
    monkeypatch.setenv("LLM_PROVIDER_PROFILE", "yandex_ai_studio")
    settings = Settings(_env_file=None)
    assert settings.llm_provider_profile == "yandex_ai_studio"
    assert settings.llm_model == settings.llm_api_key.get_secret_value() == settings.llm_api_base_url == ""
    assert settings.llm_auth_scheme == "auto"


@pytest.mark.parametrize("key,value", [
    ("LLM_PROVIDER_PROFILE", "unknown"), ("LLM_AUTH_SCHEME", "custom"),
    ("LLM_PROJECT", "folder\r\nInjected: yes"), ("LLM_PROJECT", "x" * 201),
])
def test_transport_profile_configuration_rejects_unknown_modes_and_header_injection(monkeypatch, key, value):
    monkeypatch.setenv(key, value)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)
