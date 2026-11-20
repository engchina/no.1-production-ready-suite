"""Messages API の録画の応答（SSE）を返す HTTP の client（#5580。実の API は呼ばない）。

`FakeMessagesApi.install` が `anthropic_model.anthropic_http_client` を差し替え、組み込み Runtime の
`AnthropicMessagesModel`・画像の読み取りが SDK（`anthropic`）と部品（`pr_anthropic_messages`）を
通って、この client の録画の応答を受け取る。応答は Messages API の streaming の event の形
（message_start → content_block_* → message_delta → message_stop）。
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

import anthropic
import httpx2
import pytest

import app.features.agent.anthropic_model as anthropic_model

CLAUDE_MODEL = "claude-model-a"


def text(value: str) -> dict[str, Any]:
    return {"type": "text", "text": value}


def thinking(value: str, signature: str) -> dict[str, Any]:
    return {"type": "thinking", "thinking": value, "signature": signature}


def tool_use(tool_use_id: str, name: str, input: Mapping[str, Any]) -> dict[str, Any]:
    return {"type": "tool_use", "id": tool_use_id, "name": name, "input": dict(input)}


def _block_events(index: int, block: Mapping[str, Any]) -> list[dict[str, Any]]:
    kind = block["type"]
    start: dict[str, Any]
    if kind == "thinking":
        start = {"type": "thinking", "thinking": "", "signature": ""}
        deltas = [
            {"type": "thinking_delta", "thinking": block["thinking"]},
            {"type": "signature_delta", "signature": block["signature"]},
        ]
    elif kind == "tool_use":
        start = {"type": "tool_use", "id": block["id"], "name": block["name"], "input": {}}
        deltas = [
            {
                "type": "input_json_delta",
                "partial_json": json.dumps(block["input"], ensure_ascii=False),
            }
        ]
    else:
        start = {"type": "text", "text": ""}
        deltas = [{"type": "text_delta", "text": block["text"]}]
    return [
        {"type": "content_block_start", "index": index, "content_block": start},
        *({"type": "content_block_delta", "index": index, "delta": delta} for delta in deltas),
        {"type": "content_block_stop", "index": index},
    ]


def recorded_turn(
    *blocks: Mapping[str, Any],
    stop_reason: str = "end_turn",
    input_tokens: int = 100,
    output_tokens: int = 20,
    thinking_tokens: int | None = None,
) -> list[dict[str, Any]]:
    """1 回の応答の SSE の event の並び。"""
    usage: dict[str, Any] = {"output_tokens": output_tokens}
    if thinking_tokens is not None:
        usage["output_tokens_details"] = {"thinking_tokens": thinking_tokens}
    events: list[dict[str, Any]] = [
        {
            "type": "message_start",
            "message": {
                "id": "msg_synthetic",
                "type": "message",
                "role": "assistant",
                "model": CLAUDE_MODEL,
                "content": [],
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {"input_tokens": input_tokens, "output_tokens": 1},
            },
        }
    ]
    for index, block in enumerate(blocks):
        events.extend(_block_events(index, block))
    events.append(
        {
            "type": "message_delta",
            "delta": {"stop_reason": stop_reason, "stop_sequence": None},
            "usage": usage,
        }
    )
    events.append({"type": "message_stop"})
    return events


def _sse(events: Iterable[Mapping[str, Any]]) -> bytes:
    lines: list[str] = []
    for event in events:
        lines += [f"event: {event['type']}", f"data: {json.dumps(event, ensure_ascii=False)}", ""]
    return ("\n".join(lines) + "\n").encode()


@dataclass
class FakeMessagesApi:
    """POST /v1/messages の録画の応答を順に返し、届いた要求を残す。"""

    responses: list[httpx2.Response] = field(default_factory=list)
    requests: list[httpx2.Request] = field(default_factory=list)

    def reply(self, events: list[dict[str, Any]], *, request_id: str = "req_synthetic") -> None:
        self.responses.append(
            httpx2.Response(
                200,
                headers={"content-type": "text/event-stream", "request-id": request_id},
                content=_sse(events),
            )
        )

    def reply_error(self, status: int, error_type: str, message: str) -> None:
        self.responses.append(
            httpx2.Response(
                status,
                headers={"request-id": "req_error"},
                json={"type": "error", "error": {"type": error_type, "message": message}},
            )
        )

    async def _handle(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        if not self.responses:
            pytest.fail("録画の応答が足りません（想定より多く Messages API を呼んだ）。")
        return self.responses.pop(0)

    def body(self, index: int = -1) -> dict[str, Any]:
        loaded: dict[str, Any] = json.loads(self.requests[index].content)
        return loaded

    def install(self, monkeypatch: pytest.MonkeyPatch) -> FakeMessagesApi:
        monkeypatch.setattr(
            anthropic_model,
            "anthropic_http_client",
            lambda: anthropic.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(self._handle)),
        )
        return self
