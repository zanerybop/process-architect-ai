"""Explicit transport profiles; selecting one does not claim live model acceptance.

Yandex reference checked 2026-10-03:
https://aistudio.yandex.ru/ru/docs/ai-studio/operations/generation/completions-structured
https://aistudio.yandex.ru/ru/docs/ai-studio/api/Chat-Completions/createChatCompletion
https://aistudio.yandex.ru/en/docs/ai-studio/quickstart/

Models, credentials and model-specific parameter support remain operator supplied.
The shared Python/IR/XML pipeline is independent of these HTTP transport choices.
"""

from dataclasses import dataclass
from typing import Literal

ProfileName = Literal["openai_compatible", "yandex_ai_studio"]
AuthScheme = Literal["auto", "bearer", "api_key"]


@dataclass(frozen=True)
class ProviderProfile:
    name: ProfileName
    default_base_url: str
    auth_scheme: Literal["bearer", "api_key"]
    documentation: str | None = None
    live_acceptance: Literal["not_live_verified"] = "not_live_verified"


PROFILES: dict[str, ProviderProfile] = {
    "openai_compatible": ProviderProfile("openai_compatible", "", "bearer"),
    "yandex_ai_studio": ProviderProfile(
        "yandex_ai_studio",
        "https://ai.api.cloud.yandex.net/v1",
        "api_key",
        "https://aistudio.yandex.ru/ru/docs/ai-studio/operations/generation/completions-structured",
    ),
}
