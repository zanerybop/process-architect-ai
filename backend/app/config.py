from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from backend.app.providers import AuthScheme, ProfileName

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")
    app_mode: Literal["mock", "live"] = "mock"
    llm_provider_profile: ProfileName = "openai_compatible"
    llm_auth_scheme: AuthScheme = "auto"
    llm_project: str = Field(default="", max_length=200, pattern=r"^[\x20-\x7e]*$")
    llm_api_base_url: str = ""
    llm_api_key: SecretStr = SecretStr("")
    llm_model: str = ""
    llm_timeout_seconds: float = Field(default=60, gt=0, le=120)
    llm_max_repair_attempts: int = Field(default=2, ge=0, le=2)
    llm_max_tokens: int = Field(default=8000, ge=1, le=32768)
    llm_token_parameter: Literal["max_completion_tokens", "max_tokens"] = "max_completion_tokens"
    llm_response_format: Literal["json_object", "json_schema"] = "json_object"
    llm_reasoning_effort: Literal["auto", "none", "low", "medium", "high"] = "auto"
    worker_url: str = "http://worker:8081"
    data_dir: Path = ROOT / "local_data"
    auth_cookie_secure: bool = False
