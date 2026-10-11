"""LLM provider settings (FR-10): ``COHORTSPLIT_LLM_*``.

Switching between a cloud OpenAI-compatible provider and a local model (Ollama, vLLM,
LM Studio) is configuration only (AC-30). The API key is a ``SecretStr`` and is never
logged.
"""

from pydantic import Field, SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from cohortsplit.config import ConfigError

LLM_ENV_PREFIX = "COHORTSPLIT_LLM_"


class LlmSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix=LLM_ENV_PREFIX, extra="ignore")

    # e.g. https://api.openai.com/v1, http://ollama:11434/v1, http://localhost:1234/v1
    base_url: str | None = None
    model: str | None = None
    api_key: SecretStr | None = None
    timeout_seconds: float = Field(default=60.0, gt=0, le=600)
    # Attempts per interpretation when the output violates the schema (bounded retry).
    max_attempts: int = Field(default=3, ge=1, le=10)
    # Ask for a JSON object response (OpenAI ``response_format``); disable for servers
    # that do not support it.
    json_mode: bool = True

    @field_validator("base_url", "model", "api_key", mode="before")
    @classmethod
    def _empty_is_unset(cls, value: object) -> object:
        return None if value == "" else value


def load_llm_settings() -> LlmSettings:
    try:
        return LlmSettings()
    except ValidationError as exc:
        problems = "; ".join(
            f"{LLM_ENV_PREFIX}{str(err['loc'][0]).upper() if err['loc'] else '?'}: {err['msg']}"
            for err in exc.errors(include_input=False, include_url=False)
        )
        raise ConfigError(f"Invalid LLM configuration: {problems}") from None
