"""Demo LLM stub: a deterministic OpenAI-compatible server for the README walkthrough.

**Demo only.** It knows a handful of requests against the demo ``ecommerce`` warehouse
and answers them with the cohort spec a correct interpreter would produce; every other
request gets a clarification pointing to the README. It is opt-in (``cohortsplit
demo-llm-stub``, compose profile ``demo``) and never the default provider. Point
``COHORTSPLIT_LLM_BASE_URL`` at it to try the campaign flow without a model; use a real
provider (cloud, or Qwen via Ollama) for anything else.

The specs reference the demo schema and the business-context term ``paid_order`` the
README asks you to define. Nothing here is security relevant: every spec still goes
through deterministic resolution, compilation and SQL validation.
"""

import argparse
import json
import logging
from collections.abc import Mapping, Sequence
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from cohortsplit.llm.client import ChatMessage

logger = logging.getLogger(__name__)

DEMO_MODEL = "cohortsplit-demo-stub"
PREFIX = "/v1"
_SCHEMA = "public"
_MAX_BODY = 1_000_000


def _hop(source: str, target: str) -> dict[str, str]:
    return {"from_column": f"{_SCHEMA}.{source}", "to_column": f"{_SCHEMA}.{target}"}


def _related(table: str, column: str, value: str) -> dict[str, Any]:
    return {
        "type": "related",
        "table": f"{_SCHEMA}.{table}",
        "path": [_hop("users.user_id", f"{table}.user_id")],
        "filters": [{"column": f"{_SCHEMA}.{table}.{column}", "operator": "=", "value": value}],
    }


def _country_abandoned_never_paid(country: str) -> dict[str, Any]:
    return {
        "spec_version": "1",
        "entity": "user",
        "where": {
            "type": "all_of",
            "conditions": [
                _related("addresses", "country_code", country),
                _related("carts", "status", "abandoned"),
                {"type": "not", "condition": {"type": "metric", "term": "paid_order"}},
            ],
        },
    }


# Request text (as documented in the README) -> the tagged result the stub returns.
DEMO_REQUESTS: Mapping[str, Mapping[str, Any]] = {
    "Users from Kazakhstan who abandoned a cart and never paid for an order": {
        "kind": "interpretation",
        "spec": _country_abandoned_never_paid("KZ"),
    },
    "Users from Germany who abandoned a cart and never paid for an order": {
        "kind": "interpretation",
        "spec": _country_abandoned_never_paid("DE"),
    },
    "Users who paid for an order": {
        "kind": "interpretation",
        "spec": {
            "spec_version": "1",
            "entity": "user",
            "where": {"type": "metric", "term": "paid_order"},
        },
    },
}
UNKNOWN = {
    "kind": "clarification",
    "question": (
        "This is the CohortSplit demo LLM stub: it only understands the example requests "
        "listed in the README. Use one of them, or configure a real LLM provider."
    ),
}


def _normalize(text: str) -> str:
    return " ".join(text.split()).rstrip(".").strip().casefold()


_BY_TEXT = {_normalize(request): reply for request, reply in DEMO_REQUESTS.items()}


def demo_reply(messages: Sequence[ChatMessage]) -> str:
    """The stub's answer (a tagged-result JSON document) for the last user message."""
    request = next((m.content for m in reversed(messages) if m.role == "user"), "")
    return json.dumps(_BY_TEXT.get(_normalize(request), UNKNOWN))


def _messages(body: Mapping[str, Any]) -> list[ChatMessage]:
    out: list[ChatMessage] = []
    for item in body.get("messages") or []:
        if isinstance(item, Mapping) and item.get("role") in ("system", "user", "assistant"):
            out.append(ChatMessage(item["role"], str(item.get("content") or "")))
    return out


class _Handler(BaseHTTPRequestHandler):
    server_version = "cohortsplit-demo-stub"
    sys_version = ""

    def _send(self, status: int, payload: Mapping[str, Any] | None = None) -> None:
        data = json.dumps(payload or {}).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802 (http.server API)
        if self.path == f"{PREFIX}/models":
            self._send(200, {"object": "list", "data": [{"id": DEMO_MODEL, "object": "model"}]})
        else:
            self._send(404, {"error": {"message": "not found"}})

    def do_POST(self) -> None:  # noqa: N802 (http.server API)
        if self.path != f"{PREFIX}/chat/completions":
            self._send(404, {"error": {"message": "not found"}})
            return
        length = min(int(self.headers.get("Content-Length") or 0), _MAX_BODY)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(body, dict):
                raise ValueError("body must be an object")
        except ValueError:
            self._send(400, {"error": {"message": "invalid JSON body"}})
            return
        content = demo_reply(_messages(body))
        self._send(
            200,
            {
                "id": "demo-stub",
                "object": "chat.completion",
                "model": body.get("model") or DEMO_MODEL,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": content},
                        "finish_reason": "stop",
                    }
                ],
            },
        )

    def log_message(self, format: str, *args: Any) -> None:
        # Never log request bodies; the access line is enough for a demo.
        logger.info("demo-llm-stub %s", format % args)


def create_server(host: str, port: int) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), _Handler)


def register(subparsers: "argparse._SubParsersAction[Any]") -> None:
    parser = subparsers.add_parser(
        "demo-llm-stub",
        help="serve the deterministic demo LLM stub (README walkthrough only)",
        description=(
            "Serve an OpenAI-compatible endpoint that answers only the README demo requests. "
            "Demo only: configure a real LLM provider for actual use."
        ),
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8090)
    parser.set_defaults(handler=_run)


def _run(args: argparse.Namespace) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    server = create_server(args.host, args.port)
    logger.info("demo-llm-stub listening on http://%s:%d%s", args.host, args.port, PREFIX)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
