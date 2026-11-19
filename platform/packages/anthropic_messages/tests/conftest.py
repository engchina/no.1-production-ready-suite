"""Messages API の録画の応答（SSE）と、SDK の HTTP を差し替える transport。

実の API は呼ばない。応答は Messages API の streaming の event の形（message_start →
content_block_* → message_delta → message_stop）をそのまま並べる。
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

import anthropic
import httpx2
import pytest

MODEL_ID = "claude-opus-5-5"
API_KEY = "sk-ant-test-0000000000000000"


def sse_body(events: Iterable[Mapping[str, Any]]) -> bytes:
    lines: list[str] = []
    for event in events:
        lines.append(f"event: {event['type']}")
        lines.append(f"data: {json.dumps(event, ensure_ascii=False)}")
        lines.append("")
    return ("\n".join(lines) + "\n").encode()


def recorded_stream(
    *,
    blocks: Iterable[Mapping[str, Any]] = ({"type": "text", "text": "こんにちは"},),
    stop_reason: str = "end_turn",
    stop_details: Mapping[str, Any] | None = None,
    input_tokens: int = 120,
    output_tokens: int = 40,
    thinking_tokens: int | None = 25,
    cache_read_input_tokens: int = 0,
    cache_creation_input_tokens: int = 0,
) -> list[dict[str, Any]]:
    """1 回の応答の SSE の event の並び（本文は delta を 2 つに分けて送る）。"""
    events: list[dict[str, Any]] = [
        {
            "type": "message_start",
            "message": {
                "id": "msg_test_1",
                "type": "message",
                "role": "assistant",
                "model": MODEL_ID,
                "content": [],
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {
                    "input_tokens": input_tokens,
                    "output_tokens": 1,
                    "cache_read_input_tokens": cache_read_input_tokens,
                    "cache_creation_input_tokens": cache_creation_input_tokens,
                },
            },
        }
    ]
    for index, block in enumerate(blocks):
        if block["type"] == "thinking":
            events.append(
                {
                    "type": "content_block_start",
                    "index": index,
                    "content_block": {"type": "thinking", "thinking": "", "signature": ""},
                }
            )
            events.append(
                {
                    "type": "content_block_delta",
                    "index": index,
                    "delta": {"type": "signature_delta", "signature": "sig-test"},
                }
            )
        else:
            text = str(block["text"])
            half = len(text) // 2
            events.append(
                {
                    "type": "content_block_start",
                    "index": index,
                    "content_block": {"type": "text", "text": ""},
                }
            )
            for part in (text[:half], text[half:]):
                if part:
                    events.append(
                        {
                            "type": "content_block_delta",
                            "index": index,
                            "delta": {"type": "text_delta", "text": part},
                        }
                    )
        events.append({"type": "content_block_stop", "index": index})
    usage: dict[str, Any] = {"output_tokens": output_tokens}
    if thinking_tokens is not None:
        usage["output_tokens_details"] = {"thinking_tokens": thinking_tokens}
    events.append(
        {
            "type": "message_delta",
            "delta": {
                "stop_reason": stop_reason,
                "stop_sequence": None,
                "stop_details": dict(stop_details) if stop_details is not None else None,
            },
            "usage": usage,
        }
    )
    events.append({"type": "message_stop"})
    return events


def error_body(error_type: str, message: str) -> dict[str, Any]:
    return {"type": "error", "error": {"type": error_type, "message": message}}


@dataclass
class FakeMessagesApi:
    """POST /v1/messages の応答を順に返し、届いた要求を残す。"""

    responses: list[httpx2.Response | Exception] = field(default_factory=list)
    requests: list[httpx2.Request] = field(default_factory=list)

    def reply_stream(self, events: list[dict[str, Any]], *, request_id: str = "req_test") -> None:
        self.responses.append(
            httpx2.Response(
                200,
                headers={"content-type": "text/event-stream", "request-id": request_id},
                content=sse_body(events),
            )
        )

    def reply_error(self, status: int, body: Mapping[str, Any]) -> None:
        self.responses.append(
            httpx2.Response(status, headers={"request-id": "req_err"}, json=dict(body))
        )

    def raise_error(self, exc: Exception) -> None:
        self.responses.append(exc)

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    async def ahandle(self, request: httpx2.Request) -> httpx2.Response:
        return self.handle(request)

    def body(self, index: int = -1) -> dict[str, Any]:
        loaded: dict[str, Any] = json.loads(self.requests[index].content)
        return loaded

    def client(self) -> anthropic.DefaultHttpxClient:
        return anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(self.handle))

    def async_client(self) -> anthropic.DefaultAsyncHttpxClient:
        return anthropic.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(self.ahandle))


@pytest.fixture
def fake_api() -> FakeMessagesApi:
    return FakeMessagesApi()


@pytest.fixture(autouse=True)
def _no_ambient_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    # 環境の credential・base URL を読まないことを確かめるため、テストの間は外す。
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
