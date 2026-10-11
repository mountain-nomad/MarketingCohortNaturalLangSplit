"""Demo LLM stub (README walkthrough, AC-31): a deterministic OpenAI-compatible server that
answers the documented demo requests with the spec a correct interpreter would produce,
so the campaign flow can be tried without a model. Opt-in; never the default provider."""

import socket
import threading
from collections.abc import Iterator
from typing import Any

import httpx
import pytest

from cohortsplit.cli import build_parser
from cohortsplit.compiler.resolve import ResolvedCohort, resolve
from cohortsplit.llm.client import ChatMessage, create_chat_client
from cohortsplit.llm.demo_stub import (
    DEMO_MODEL,
    DEMO_REQUESTS,
    create_server,
    demo_reply,
)
from cohortsplit.llm.interpreter import Clarification, Interpretation, interpret_request
from cohortsplit.llm.settings import LlmSettings
from tests.unit.compiler.fixtures import shop_snapshot

AC2 = "Users from Kazakhstan who abandoned a cart and never paid for an order"
PAID = "Users who paid for an order"


@pytest.fixture
def stub_url() -> Iterator[str]:
    server = create_server("127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        yield f"http://{host!s}:{port}/v1"
    finally:
        server.shutdown()
        server.server_close()


def ask(text: str) -> list[ChatMessage]:
    return [
        ChatMessage("system", "You turn requests into cohort specs."),
        ChatMessage("user", "An earlier example request"),
        ChatMessage("assistant", '{"kind": "clarification", "question": "?"}'),
        ChatMessage("user", text),
    ]


def test_documented_requests_include_ac2_and_a_non_empty_demo_cohort() -> None:
    assert AC2 in DEMO_REQUESTS
    assert PAID in DEMO_REQUESTS


@pytest.mark.parametrize("request_text", [AC2, PAID])
def test_demo_requests_resolve_against_the_shop_schema(request_text: str) -> None:
    result = interpret_request(_Direct(), ask(request_text), max_attempts=1)

    assert isinstance(result, Interpretation)
    assert isinstance(resolve(result.spec, shop_snapshot()), ResolvedCohort)


def test_ac2_spec_has_country_cart_and_not_paid() -> None:
    result = interpret_request(_Direct(), ask(AC2), max_attempts=1)

    assert isinstance(result, Interpretation)
    dumped = result.spec.model_dump(mode="json", exclude_none=True)
    text = str(dumped)
    assert "'KZ'" in text
    assert "'abandoned'" in text
    assert "paid_order" in text
    assert dumped["where"]["type"] == "all_of"


@pytest.mark.parametrize(
    "variant",
    [
        "  users who PAID for an order.  ",
        "Users  who paid for\nan order",
    ],
)
def test_matching_ignores_case_whitespace_and_final_period(variant: str) -> None:
    assert demo_reply(ask(variant)) == demo_reply(ask(PAID))


def test_unknown_requests_get_a_clarification_pointing_to_the_readme() -> None:
    result = interpret_request(_Direct(), ask("Our best customers"), max_attempts=1)

    assert isinstance(result, Clarification)
    assert "demo" in result.question.lower()
    assert "README" in result.question


def test_only_the_last_user_message_counts() -> None:
    messages = [ChatMessage("user", PAID), ChatMessage("user", "Something else")]

    assert demo_reply(messages) == demo_reply(ask("Something else"))


def test_served_over_the_openai_compatible_api(stub_url: str) -> None:
    client = create_chat_client(LlmSettings(base_url=stub_url, model=DEMO_MODEL))

    result = interpret_request(client, ask(PAID), max_attempts=1)

    assert isinstance(result, Interpretation)


def test_models_endpoint_and_unknown_paths(stub_url: str) -> None:
    models = httpx.get(f"{stub_url}/models").json()
    assert [m["id"] for m in models["data"]] == [DEMO_MODEL]
    assert httpx.post(f"{stub_url}/elsewhere", json={}).status_code == 404
    assert httpx.post(f"{stub_url}/chat/completions", content=b"not json").status_code == 400


def test_cli_subcommand_is_registered() -> None:
    args = build_parser().parse_args(["demo-llm-stub", "--host", "127.0.0.1", "--port", "8090"])

    assert args.host == "127.0.0.1"
    assert args.port == 8090
    assert callable(args.handler)


class _Direct:
    """Calls the stub's reply function directly (no HTTP)."""

    def complete(self, messages: Any) -> str:
        return demo_reply(list(messages))


@pytest.mark.parametrize("length", ["-5", "abc", "99999999999"])
def test_bad_content_length_is_refused(stub_url: str, length: str) -> None:
    host_port = stub_url.removeprefix("http://").removesuffix("/v1")
    host, port = host_port.split(":")
    request = (
        f"POST /v1/chat/completions HTTP/1.1\r\nHost: {host_port}\r\n"
        f"Content-Length: {length}\r\nConnection: close\r\n\r\n{{}}"
    ).encode()
    with socket.create_connection((host, int(port)), timeout=5) as conn:
        conn.sendall(request)
        conn.shutdown(socket.SHUT_WR)
        status_line = conn.makefile("rb").readline().decode()

    assert " 400 " in status_line
