"""会話の履歴とツールの往復の部品（録画の応答で確かめる。実の API は呼ばない）。"""

from __future__ import annotations

import base64
from typing import Any

import anyio
import pytest
from conftest import API_KEY, MODEL_ID, FakeMessagesApi, error_body, recorded_stream

from pr_anthropic_messages import (
    THINKING_BINDING_BETA,
    AnthropicConversationRequest,
    AnthropicImage,
    AnthropicMessagesError,
    AnthropicTool,
    AnthropicTurnResult,
    acreate_anthropic_turn,
    anthropic_messages,
    anthropic_text_block,
    anthropic_tool_result_block,
    anthropic_tool_use_block,
)

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8
SCHEMA = {
    "type": "object",
    "properties": {"query": {"type": "string", "minLength": 2}},
    "required": ["query"],
    "additionalProperties": False,
}


def _request(**overrides: Any) -> AnthropicConversationRequest:
    values: dict[str, Any] = {
        "model_id": MODEL_ID,
        "messages": anthropic_messages([("user", [anthropic_text_block("合成の質問 A")])]),
        "max_output_tokens": 2048,
    }
    values.update(overrides)
    return AnthropicConversationRequest(**values)


def _call(fake_api: FakeMessagesApi, request: AnthropicConversationRequest) -> AnthropicTurnResult:
    async def run() -> AnthropicTurnResult:
        return await acreate_anthropic_turn(
            request, api_key=API_KEY, http_client=fake_api.async_client()
        )

    return anyio.run(run)


def test_tool_use_turn_returns_blocks_to_send_back(fake_api: FakeMessagesApi) -> None:
    """ツールの呼び出し（並列）: 応答の block を順のまま返し、呼ぶツールを取り出す。"""
    fake_api.reply_stream(
        recorded_stream(
            blocks=[
                {"type": "thinking", "thinking": "合成の思考", "signature": "sig-a"},
                {"type": "text", "text": "調べます。"},
                {"type": "tool_use", "id": "toolu_1", "name": "search", "input": {"query": "A"}},
                {"type": "tool_use", "id": "toolu_2", "name": "search", "input": {"query": "B"}},
            ],
            stop_reason="tool_use",
        )
    )
    tool = AnthropicTool(name="search", input_schema=SCHEMA, description="合成の検索", strict=True)

    turn = _call(
        fake_api,
        _request(system="Step 1: 合成の指示", tools=(tool,), reasoning_effort="medium"),
    )

    body = fake_api.body()
    assert body["system"] == "Step 1: 合成の指示"
    # strict のツールは構造化出力と同じ変換（minLength などを外し、説明に写す）。
    [sent_tool] = body["tools"]
    assert (sent_tool["name"], sent_tool["description"], sent_tool["strict"]) == (
        "search",
        "合成の検索",
        True,
    )
    assert "minLength" not in sent_tool["input_schema"]["properties"]["query"]
    assert body["tool_choice"] == {"type": "auto"}
    # 推論の量を送る呼び出しは、検証に通らない思考の block を外して続ける beta を付ける。
    assert body["thinking"] == {
        "type": "adaptive",
        "block_binding": {"prefix_mismatch_behavior": "drop_block"},
    }
    assert body["output_config"] == {"effort": "medium"}
    assert fake_api.requests[0].headers["anthropic-beta"] == THINKING_BINDING_BETA
    assert turn.stop_reason == "tool_use"
    assert turn.text == "調べます。"
    assert [(use.id, use.name, dict(use.input)) for use in turn.tool_uses] == [
        ("toolu_1", "search", {"query": "A"}),
        ("toolu_2", "search", {"query": "B"}),
    ]
    assert [block["type"] for block in turn.content] == [
        "thinking",
        "text",
        "tool_use",
        "tool_use",
    ]
    assert turn.content[0] == {"type": "thinking", "thinking": "合成の思考", "signature": "sig-a"}
    assert turn.dropped_thinking_blocks == 0


def test_tool_results_round_trip_in_one_user_message(fake_api: FakeMessagesApi) -> None:
    """並列のツールの結果は 1 つの user の message（tool_result が先頭）。思考は変えない。"""
    fake_api.reply_stream(recorded_stream(blocks=[{"type": "text", "text": "合成の回答"}]))
    thinking = {"type": "thinking", "thinking": "合成の思考", "signature": "sig-a"}
    image = AnthropicImage.from_bytes("image/png", PNG_BYTES)
    messages = anthropic_messages(
        [
            ("user", [anthropic_text_block("合成の質問 A")]),
            ("assistant", [thinking]),
            ("assistant", [anthropic_tool_use_block("toolu_1", "search", {"query": "A"})]),
            ("assistant", [anthropic_tool_use_block("toolu_2", "search", {"query": "B"})]),
            ("user", [anthropic_text_block("合成の補足")]),
            ("user", [anthropic_tool_result_block("toolu_1", "結果 A")]),
            (
                "user",
                [
                    anthropic_tool_result_block(
                        "toolu_2", [anthropic_text_block("結果 B"), image], is_error=True
                    )
                ],
            ),
        ]
    )

    turn = _call(fake_api, _request(messages=messages, reasoning_effort="high"))

    sent = fake_api.body()["messages"]
    assert [message["role"] for message in sent] == ["user", "assistant", "user"]
    assert sent[1]["content"][0] == thinking
    assert [block["type"] for block in sent[2]["content"]] == [
        "tool_result",
        "tool_result",
        "text",
    ]
    assert sent[2]["content"][0] == {
        "type": "tool_result",
        "tool_use_id": "toolu_1",
        "content": "結果 A",
    }
    second = sent[2]["content"][1]
    assert second["is_error"] is True
    assert second["content"][1]["source"] == {
        "type": "base64",
        "media_type": "image/png",
        "data": base64.standard_b64encode(PNG_BYTES).decode(),
    }
    assert (turn.text, turn.tool_uses) == ("合成の回答", ())


def test_reasoning_none_does_not_send_back_thinking(fake_api: FakeMessagesApi) -> None:
    """推論の量 none は思考・beta を送らず、モデルの既定の思考の block を戻さない。"""
    fake_api.reply_stream(recorded_stream())
    messages = anthropic_messages(
        [
            ("user", [anthropic_text_block("合成の質問 A")]),
            (
                "assistant",
                [
                    {"type": "redacted_thinking", "data": "opaque"},
                    anthropic_tool_use_block("toolu_1", "search", {"query": "A"}),
                ],
            ),
            ("user", [anthropic_tool_result_block("toolu_1", "結果 A")]),
        ]
    )

    _call(fake_api, _request(messages=messages))

    body = fake_api.body()
    assert "thinking" not in body and "output_config" not in body
    assert "anthropic-beta" not in fake_api.requests[0].headers
    assert body["messages"][1]["content"] == [
        {"type": "tool_use", "id": "toolu_1", "name": "search", "input": {"query": "A"}}
    ]


@pytest.mark.parametrize(
    ("choice", "name", "parallel", "expected"),
    [
        ("any", "", True, {"type": "any"}),
        (
            "tool",
            "search",
            False,
            {"type": "tool", "name": "search", "disable_parallel_tool_use": True},
        ),
        ("auto", "", False, {"type": "auto", "disable_parallel_tool_use": True}),
        ("none", "", False, {"type": "none"}),
    ],
    ids=["any", "named", "auto-serial", "none"],
)
def test_tool_choice_is_sent_as_given(
    fake_api: FakeMessagesApi, choice: str, name: str, parallel: bool, expected: dict[str, Any]
) -> None:
    """tool_choice は指定どおり送る（受け付けないモデルの 400 を別の指定へ読み替えない）。"""
    fake_api.reply_stream(recorded_stream())
    tool = AnthropicTool(name="search", input_schema=SCHEMA)

    _call(
        fake_api,
        _request(tools=(tool,), tool_choice=choice, tool_name=name, parallel_tool_calls=parallel),
    )

    body = fake_api.body()
    assert body["tool_choice"] == expected
    # strict でないツールは schema をそのまま送る。
    assert body["tools"][0] == {"name": "search", "input_schema": SCHEMA}


def test_forced_tool_choice_rejection_is_a_request_failure(fake_api: FakeMessagesApi) -> None:
    fake_api.reply_error(
        400,
        error_body(
            "invalid_request_error",
            'tool_choice: type "tool" and "any" are not supported for this model.',
        ),
    )

    with pytest.raises(AnthropicMessagesError) as raised:
        _call(
            fake_api,
            _request(tools=(AnthropicTool(name="search", input_schema=SCHEMA),), tool_choice="any"),
        )

    error = raised.value
    assert (error.kind, error.status_code, error.error_type) == (
        "request",
        400,
        "invalid_request_error",
    )
    assert "not supported for this model" in error.provider_message


def test_dropped_thinking_blocks_are_counted(fake_api: FakeMessagesApi) -> None:
    fake_api.reply_stream(
        recorded_stream(
            input_transformations=[
                {
                    "type": "thinking_dropped",
                    "path": "messages.1.content.0",
                    "reason": "prefix_binding_mismatch",
                },
                {
                    "type": "thinking_dropped",
                    "path": "messages.3.content.0",
                    "reason": "prefix_binding_mismatch",
                },
            ]
        )
    )

    turn = _call(fake_api, _request(reasoning_effort="low"))

    assert turn.dropped_thinking_blocks == 2


@pytest.mark.parametrize(
    ("stop_reason", "blocks", "kind"),
    [
        ("max_tokens", [{"type": "text", "text": "途中"}], "incomplete"),
        ("pause_turn", [{"type": "text", "text": "途中"}], "incomplete"),
        ("tool_use", [{"type": "text", "text": "呼び出しなし"}], "response_format"),
        ("end_turn", [{"type": "thinking", "signature": "sig"}], "response_format"),
        ("refusal", [{"type": "text", "text": "x"}], "refusal"),
    ],
    ids=["max-tokens", "pause-turn", "tool-use-without-call", "empty", "refusal"],
)
def test_unfinished_turns_fail_with_the_shared_kinds(
    fake_api: FakeMessagesApi, stop_reason: str, blocks: list[dict[str, Any]], kind: str
) -> None:
    fake_api.reply_stream(recorded_stream(blocks=blocks, stop_reason=stop_reason))

    with pytest.raises(AnthropicMessagesError) as raised:
        _call(fake_api, _request())

    assert raised.value.kind == kind
    if kind == "incomplete":
        assert raised.value.incomplete is not None
        assert raised.value.incomplete.max_output_tokens == 2048
        assert raised.value.usage is not None


def test_request_validation() -> None:
    with pytest.raises(ValueError, match="message"):
        _request(messages=[])
    with pytest.raises(ValueError, match="ツールの名前"):
        _request(tool_choice="tool")
    with pytest.raises(ValueError, match="tool_choice"):
        _request(tool_choice="required")
    with pytest.raises(ValueError, match="ツールの名前が空"):
        AnthropicTool(name=" ", input_schema=SCHEMA)
