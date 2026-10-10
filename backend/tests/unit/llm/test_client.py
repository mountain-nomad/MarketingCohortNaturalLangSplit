"""FR-10 OpenAI-compatible LLM client: configuration only, no secrets in logs (AC-28, AC-30)."""

import json
import logging
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest
from pydantic import SecretStr

from cohortsplit.config import ConfigError
from cohortsplit.llm.client import (
    ChatMessage,
    LlmNotConfiguredError,
    LlmUnavailableError,
    OpenAICompatibleClient,
    create_chat_client,
    safe_base_url,
)
from cohortsplit.llm.settings import LlmSettings, load_llm_settings
from tests.llm_fakes import openai_stub

API_KEY = "sk-test-secret-9f8e7d"
MESSAGES = [ChatMessage("system", "You interpret cohorts."), ChatMessage("user", "Users from DE")]


def settings(**overrides: object) -> LlmSettings:
    values: dict[str, object] = {
        "base_url": "https://llm.example.com/v1",
        "model": "gpt-test",
        "api_key": SecretStr(API_KEY),
        "timeout_seconds": 5,
    }
    values.update(overrides)
    return LlmSettings(**values)  # type: ignore[arg-type]


def completion(content: str) -> dict[str, object]:
    return {"choices": [{"index": 0, "message": {"role": "assistant", "content": content}}]}


def test_request_shape_is_openai_chat_completions() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=completion('{"kind": "clarification", "question": "?"}'))

    client = OpenAICompatibleClient(settings(), transport=httpx.MockTransport(handler))

    assert client.complete(MESSAGES) == '{"kind": "clarification", "question": "?"}'
    [request] = seen
    assert str(request.url) == "https://llm.example.com/v1/chat/completions"
    assert request.headers["authorization"] == f"Bearer {API_KEY}"
    body = json.loads(request.content)
    assert body["model"] == "gpt-test"
    assert body["temperature"] == 0
    assert body["response_format"] == {"type": "json_object"}
    assert body["messages"] == [
        {"role": "system", "content": "You interpret cohorts."},
        {"role": "user", "content": "Users from DE"},
    ]


def test_no_authorization_header_without_api_key_and_json_mode_optional() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=completion("{}"))

    client = OpenAICompatibleClient(
        settings(api_key=None, json_mode=False), transport=httpx.MockTransport(handler)
    )
    client.complete(MESSAGES)

    assert "authorization" not in seen[0].headers
    assert "response_format" not in json.loads(seen[0].content)


def test_switching_cloud_and_local_is_configuration_only() -> None:
    """AC-30: the same code talks to a 'cloud' (key, /v1) and a 'local Qwen' endpoint."""
    with (
        openai_stub('{"kind": "clarification", "question": "cloud"}', prefix="/v1") as cloud,
        openai_stub('{"kind": "clarification", "question": "local"}', prefix="/api/v1") as local,
    ):
        cloud_client = create_chat_client(
            LlmSettings(base_url=cloud.base_url, model="gpt-4o-mini", api_key=SecretStr(API_KEY))
        )
        local_client = create_chat_client(
            LlmSettings(base_url=local.base_url, model="qwen2.5:7b-instruct")
        )

        assert "cloud" in cloud_client.complete(MESSAGES)
        assert "local" in local_client.complete(MESSAGES)

    [cloud_request] = cloud.requests
    [local_request] = local.requests
    assert cloud_request.path == "/v1/chat/completions"
    assert local_request.path == "/api/v1/chat/completions"
    assert cloud_request.body["model"] == "gpt-4o-mini"
    assert local_request.body["model"] == "qwen2.5:7b-instruct"
    assert cloud_request.headers["authorization"] == f"Bearer {API_KEY}"
    assert "authorization" not in local_request.headers
    assert cloud_request.body["messages"] == local_request.body["messages"]


def test_settings_come_from_the_environment(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("COHORTSPLIT_LLM_BASE_URL", "http://ollama:11434/v1")
    clean_env.setenv("COHORTSPLIT_LLM_MODEL", "qwen2.5:7b-instruct")
    clean_env.setenv("COHORTSPLIT_LLM_API_KEY", API_KEY)
    clean_env.setenv("COHORTSPLIT_LLM_TIMEOUT_SECONDS", "12.5")
    clean_env.setenv("COHORTSPLIT_LLM_MAX_ATTEMPTS", "2")
    clean_env.setenv("COHORTSPLIT_LLM_JSON_MODE", "false")

    loaded = load_llm_settings()

    assert loaded.base_url == "http://ollama:11434/v1"
    assert loaded.model == "qwen2.5:7b-instruct"
    assert loaded.api_key is not None and loaded.api_key.get_secret_value() == API_KEY
    assert (loaded.timeout_seconds, loaded.max_attempts, loaded.json_mode) == (12.5, 2, False)
    assert API_KEY not in repr(loaded)


def test_empty_api_key_is_unset(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("COHORTSPLIT_LLM_API_KEY", "")
    assert load_llm_settings().api_key is None


def test_invalid_settings_name_the_variable(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("COHORTSPLIT_LLM_MAX_ATTEMPTS", "0")
    with pytest.raises(ConfigError, match="COHORTSPLIT_LLM_MAX_ATTEMPTS"):
        load_llm_settings()


@pytest.mark.parametrize("missing", ["base_url", "model"])
def test_not_configured_names_the_variables(missing: str) -> None:
    with pytest.raises(LlmNotConfiguredError, match="COHORTSPLIT_LLM_BASE_URL"):
        create_chat_client(settings(**{missing: None}))


def test_safe_base_url_strips_credentials_query_and_fragment() -> None:
    assert (
        safe_base_url("https://user:pa55@llm.example.com:8443/v1?key=abc#x")
        == "https://llm.example.com:8443/v1"
    )


def test_unreachable_provider_is_actionable_and_secret_free(
    closed_port: int, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    client = create_chat_client(
        settings(base_url=f"http://admin:pw-in-url@127.0.0.1:{closed_port}/v1")
    )

    with pytest.raises(LlmUnavailableError) as info:
        client.complete(MESSAGES)

    message = str(info.value)
    assert f"LLM provider unavailable at http://127.0.0.1:{closed_port}/v1" in message
    for secret in (API_KEY, "pw-in-url", "admin:"):
        assert secret not in message
        assert secret not in repr(info.value)
        assert secret not in caplog.text


@pytest.fixture
def slow_server() -> Iterator[str]:
    release = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            release.wait(5)
            self.send_response(200)
            self.end_headers()

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        yield f"http://{host!s}:{port}/v1"
    finally:
        release.set()
        server.shutdown()
        server.server_close()


def test_timeout_is_reported_with_the_base_url(slow_server: str) -> None:
    client = create_chat_client(settings(base_url=slow_server, timeout_seconds=0.2))

    with pytest.raises(LlmUnavailableError, match="timed out"):
        client.complete(MESSAGES)


@pytest.mark.parametrize("status", [401, 429, 500, 503])
def test_http_errors_are_unavailable_without_secrets(
    status: int, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"error": {"message": f"bad key {API_KEY}"}})

    client = OpenAICompatibleClient(settings(), transport=httpx.MockTransport(handler))

    with pytest.raises(LlmUnavailableError) as info:
        client.complete(MESSAGES)

    assert str(status) in str(info.value)
    assert API_KEY not in str(info.value)
    assert API_KEY not in caplog.text


@pytest.mark.parametrize(
    "payload",
    [{"choices": []}, {"nope": 1}, {"choices": [{"message": {"content": None}}]}],
)
def test_malformed_responses_are_reported(payload: dict[str, object]) -> None:
    client = OpenAICompatibleClient(
        settings(), transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))
    )

    with pytest.raises(LlmUnavailableError, match="unexpected response"):
        client.complete(MESSAGES)


def test_api_key_never_logged_or_in_errors(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    client = OpenAICompatibleClient(
        settings(),
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=completion("{}"))),
    )

    client.complete(MESSAGES)

    assert API_KEY not in caplog.text
    assert API_KEY not in repr(client)
    assert API_KEY not in repr(settings())
