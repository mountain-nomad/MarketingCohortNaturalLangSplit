"""Deterministic fake LLM and an in-process OpenAI-compatible HTTP stub (no network in CI)."""

import json
import threading
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from cohortsplit.llm.client import ChatMessage

Reply = str | Exception | Callable[[Sequence[ChatMessage]], str]


def interpretation(spec: dict[str, Any]) -> str:
    return json.dumps({"kind": "interpretation", "spec": spec})


def clarification(question: str) -> str:
    return json.dumps({"kind": "clarification", "question": question})


def rejection(reason: str, message: str = "") -> str:
    return json.dumps({"kind": "rejection", "reason": reason, "message": message})


@dataclass
class FakeChatClient:
    """Answers with the queued replies in order (the last one repeats); records prompts."""

    replies: list[Reply]
    calls: list[list[ChatMessage]] = field(default_factory=list)

    def complete(self, messages: Sequence[ChatMessage]) -> str:
        self.calls.append(list(messages))
        reply = self.replies[min(len(self.calls), len(self.replies)) - 1]
        if isinstance(reply, Exception):
            raise reply
        if callable(reply):
            return reply(messages)
        return reply

    def prompt_text(self) -> str:
        return "\n".join(m.content for call in self.calls for m in call)


@dataclass
class StubRequest:
    path: str
    headers: dict[str, str]
    body: dict[str, Any]


@dataclass
class OpenAIStub:
    """A tiny OpenAI-compatible chat-completions server on 127.0.0.1."""

    base_url: str
    requests: list[StubRequest]
    reply: list[str]


@contextmanager
def openai_stub(content: str, *, prefix: str = "/v1") -> Iterator[OpenAIStub]:
    requests: list[StubRequest] = []
    reply = [content]

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 (http.server API)
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length) or b"{}")
            requests.append(
                StubRequest(self.path, {k.lower(): v for k, v in self.headers.items()}, body)
            )
            if self.path != f"{prefix}/chat/completions":
                self.send_response(404)
                self.end_headers()
                return
            payload = json.dumps(
                {
                    "id": "stub",
                    "object": "chat.completion",
                    "model": body.get("model"),
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": reply[0]},
                            "finish_reason": "stop",
                        }
                    ],
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: Any) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        yield OpenAIStub(f"http://{host!s}:{port}{prefix}", requests, reply)
    finally:
        server.shutdown()
        server.server_close()
