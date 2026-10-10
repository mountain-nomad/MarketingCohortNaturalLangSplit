"""One OpenAI-compatible chat-completions client (FR-10).

Covers cloud providers and locally hosted models (Qwen via Ollama / vLLM / LM Studio):
only ``base_url``, ``model`` and the optional API key differ (AC-30). No vendor SDK.

Errors are actionable and secret-free: they name the base URL without credentials,
query or fragment, never the API key or the provider's error body (AC-28).
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, Protocol
from urllib.parse import urlsplit, urlunsplit

import httpx

from cohortsplit.llm.settings import LLM_ENV_PREFIX, LlmSettings

logger = logging.getLogger(__name__)

Role = Literal["system", "user", "assistant"]


@dataclass(frozen=True)
class ChatMessage:
    role: Role
    content: str


class ChatClient(Protocol):
    def complete(self, messages: Sequence[ChatMessage]) -> str:
        """The assistant message content for ``messages``."""
        ...


class LlmError(Exception):
    """Base class for LLM provider failures."""


class LlmNotConfiguredError(LlmError):
    def __init__(self) -> None:
        super().__init__(
            "No LLM provider is configured. Set "
            f"{LLM_ENV_PREFIX}BASE_URL and {LLM_ENV_PREFIX}MODEL "
            f"(and {LLM_ENV_PREFIX}API_KEY for cloud providers)."
        )


class LlmUnavailableError(LlmError):
    """The provider could not be reached, timed out, refused or answered unusably."""


def safe_base_url(url: str) -> str:
    """``scheme://host[:port]/path`` without credentials, query or fragment."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    if parts.port is not None:
        host = f"{host}:{parts.port}"
    return urlunsplit((parts.scheme, host, parts.path.rstrip("/"), "", ""))


class OpenAICompatibleClient:
    def __init__(
        self, settings: LlmSettings, *, transport: httpx.BaseTransport | None = None
    ) -> None:
        if not settings.base_url or not settings.model:
            raise LlmNotConfiguredError()
        self._url = settings.base_url.rstrip("/") + "/chat/completions"
        self._safe_url = safe_base_url(settings.base_url)
        self._model = settings.model
        self._timeout = settings.timeout_seconds
        self._json_mode = settings.json_mode
        self.__api_key = settings.api_key
        self._transport = transport

    def __repr__(self) -> str:
        return f"OpenAICompatibleClient(base_url={self._safe_url!r}, model={self._model!r})"

    @property
    def base_url(self) -> str:
        return self._safe_url

    def _unavailable(self, reason: str) -> LlmUnavailableError:
        logger.warning("llm request failed base_url=%s reason=%s", self._safe_url, reason)
        return LlmUnavailableError(
            f"LLM provider unavailable at {self._safe_url} ({reason}). Check that it is "
            f"running and that {LLM_ENV_PREFIX}BASE_URL, {LLM_ENV_PREFIX}MODEL and "
            f"{LLM_ENV_PREFIX}API_KEY are correct."
        )

    def complete(self, messages: Sequence[ChatMessage]) -> str:
        body: dict[str, object] = {
            "model": self._model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": 0,
        }
        if self._json_mode:
            body["response_format"] = {"type": "json_object"}
        headers = {"Accept": "application/json"}
        if self.__api_key is not None:
            headers["Authorization"] = f"Bearer {self.__api_key.get_secret_value()}"
        logger.info(
            "llm request base_url=%s model=%s messages=%d",
            self._safe_url,
            self._model,
            len(messages),
        )
        try:
            with httpx.Client(
                timeout=self._timeout, transport=self._transport, follow_redirects=False
            ) as http:
                response = http.post(self._url, json=body, headers=headers)
        except httpx.TimeoutException:
            raise self._unavailable(f"timed out after {self._timeout:g}s") from None
        except httpx.HTTPError as exc:
            raise self._unavailable(type(exc).__name__) from None
        if response.status_code >= 400:
            # The provider's error body may echo the key or the prompt: never surfaced.
            raise self._unavailable(f"HTTP {response.status_code}")
        try:
            payload = response.json()
            content = payload["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError):
            raise self._unavailable("unexpected response shape") from None
        if not isinstance(content, str):
            raise self._unavailable("unexpected response shape")
        return content


def create_chat_client(settings: LlmSettings) -> OpenAICompatibleClient:
    return OpenAICompatibleClient(settings)
