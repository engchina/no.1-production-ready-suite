"""provider の stream の event の順に左右されず、完了の event の応答だけを使うことを確かめる（#4197）。

HTTP は httpx.MockTransport（決定論。合成の SSE）で受け、実際の OpenAI SDK の client と event の型を通す。OCI へは接続しない。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from pr_backend_core.llm_request_params import reset_dropped_parameters
from pydantic import ValidationError

from rag_engine.adapters import oci
from rag_engine.config import get_settings
from rag_engine.generation.answering import _invalid_structured_output
from rag_engine.generation.grounded import generation_failure_detail
from rag_engine.models.llm import TextOutput
from rag_engine.resources.deadline import Deadline, bind_deadline

PROVIDER = SimpleNamespace(model="synthetic-model", label="synthetic", reasoning_effort="", base_url="https://stub")
OUTPUT = TextOutput(text="合成の本文").model_dump_json()
USAGE = {
    "input_tokens": 7,
    "input_tokens_details": {"cached_tokens": 0},
    "output_tokens": 11,
    "output_tokens_details": {"reasoning_tokens": 3},
    "total_tokens": 18,
}
_RESPONSE: dict[str, Any] = {
    "id": "resp-1",
    "object": "response",
    "created_at": 0,
    "model": "synthetic-model",
    "parallel_tool_calls": False,
    "tool_choice": "auto",
    "tools": [],
}


def _message(text: str = OUTPUT) -> dict[str, Any]:
    return {
        "id": "msg-1",
        "type": "message",
        "role": "assistant",
        "status": "completed",
        "content": [{"type": "output_text", "text": text, "annotations": []}],
    }


REASONING = {"id": "rs-1", "type": "reasoning", "summary": []}
PART = {"type": "output_text", "text": "", "annotations": []}


def _created() -> dict[str, Any]:
    return {"type": "response.created", "response": {**_RESPONSE, "status": "in_progress", "output": []}}


def _completed(*output: dict[str, Any]) -> dict[str, Any]:
    return {"type": "response.completed", "response": {**_RESPONSE, "status": "completed", "output": list(output), "usage": USAGE}}


def _item_added(index: int, item: dict[str, Any]) -> dict[str, Any]:
    return {"type": "response.output_item.added", "output_index": index, "item": item}


def _part_added(index: int) -> dict[str, Any]:
    return {"type": "response.content_part.added", "output_index": index, "content_index": 0, "item_id": "msg-1", "part": PART}


def _delta(index: int, text: str = OUTPUT) -> dict[str, Any]:
    return {
        "type": "response.output_text.delta",
        "output_index": index,
        "content_index": 0,
        "item_id": "msg-1",
        "delta": text,
        "logprobs": [],
    }


def _text_done(index: int, text: str = OUTPUT) -> dict[str, Any]:
    return {
        "type": "response.output_text.done",
        "output_index": index,
        "content_index": 0,
        "item_id": "msg-1",
        "text": text,
        "logprobs": [],
    }


def test_completed_snapshot_reuses_clean_done_without_resending(send, caplog: pytest.LogCaptureFixture) -> None:
    broken = OUTPUT.replace("本", "\ufffd" * 3)
    events = [_created(), _delta(0), _text_done(0), _completed(_message(broken))]
    bodies: list[dict[str, Any]] = []
    with caplog.at_level(logging.INFO, logger=oci.__name__):
        result = send([events], bodies)
    assert result.text == "合成の本文"
    assert len(bodies) == 1
    usage = next(record for record in caplog.records if record.__dict__.get("event") == "llm_call_usage")
    assert usage.__dict__["output_tokens"] == 11
    assert "合成の本文" not in caplog.text


@pytest.mark.parametrize(
    "variant", ["missing", "delta_only", "item", "output", "content", "response", "conflict", "meaning", "broken", "created"]
)
def test_untrusted_done_does_not_replace_completed(send, variant: str) -> None:
    broken = OUTPUT.replace("本", "\ufffd" * 3)
    done = _text_done(0)
    middle = [done]
    start = [_created()]
    if variant == "missing":
        middle = []
    elif variant == "delta_only":
        middle = [_delta(0)]
    elif variant in {"item", "output", "content"}:
        field, value = {"item": ("item_id", "msg-other"), "output": ("output_index", 1), "content": ("content_index", 1)}[variant]
        done[field] = value
    elif variant == "response":
        other = _created()
        other["response"]["id"] = "resp-other"
        middle.append(other)
    elif variant == "conflict":
        middle.extend([_text_done(0, OUTPUT.replace("合成", "別の合成")), _text_done(0)])
    elif variant == "meaning":
        done["text"] = OUTPUT.replace("合成", "別の合成")
    elif variant == "broken":
        done["text"] = broken
    elif variant == "created":
        start = []
    bodies: list[dict[str, Any]] = []
    with pytest.raises(oci.InvalidStructuredOutput):
        send([start + middle + [_completed(_message(broken))]], bodies)
    assert len(bodies) == 1


def test_clean_final_wins_over_different_done(send) -> None:
    bodies: list[dict[str, Any]] = []
    result = send([[_created(), _text_done(0, OUTPUT.replace("合成", "別の合成")), _completed(_message())]], bodies)
    assert result.text == "合成の本文"


def test_recovered_text_still_requires_valid_schema(send) -> None:
    clean = '{"text":123,"label":"本"}'
    broken = clean.replace("本", "\ufffd" * 3)
    bodies: list[dict[str, Any]] = []
    with pytest.raises(ValidationError):
        send([[_created(), _text_done(0, clean), _completed(_message(broken))]], bodies)
    assert len(bodies) == 1


def test_multiple_content_parts_bind_to_final_indices(send) -> None:
    parts = ['{"text":"合成の', '本文"}']
    message = _message()
    message["content"] = [{"type": "output_text", "text": text.replace("本", "\ufffd" * 3), "annotations": []} for text in parts]
    done = [{**_text_done(1, text), "content_index": index} for index, text in enumerate(parts)]
    bodies: list[dict[str, Any]] = []
    result = send([[_created(), *done, _completed(REASONING, message)]], bodies)
    assert result.text == "合成の本文"
    assert len(bodies) == 1


def test_refusal_does_not_use_received_clean_done(send) -> None:
    refusal = {**_message(), "content": [{"type": "refusal", "refusal": "合成の拒否"}]}
    with pytest.raises(RuntimeError, match="拒否"):
        send([[_created(), _text_done(0), _completed(refusal)]], [])


def _sse(events: list[dict[str, Any]]) -> httpx.Response:
    numbered = [{**event, "sequence_number": number} for number, event in enumerate(events)]
    body = "".join(f"event: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n" for event in numbered)
    return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body.encode())


@pytest.fixture(autouse=True)
def _fresh_dropped_parameters() -> Iterator[None]:
    reset_dropped_parameters()
    yield
    reset_dropped_parameters()


@pytest.fixture
def send(monkeypatch: pytest.MonkeyPatch):
    """event の列を順に返す実際の client で ``_openai_parsed`` を呼ぶ。戻り値は (結果, 送信の本文の一覧)。"""

    def run(scripts: list[list[dict[str, Any]]], bodies: list[dict[str, Any]]) -> Any:
        remaining = list(scripts)

        def handler(request: httpx.Request) -> httpx.Response:
            bodies.append(json.loads(request.content))
            return _sse(remaining.pop(0))

        recording_client = oci._RecordingHttpClient
        monkeypatch.setattr(
            oci, "_RecordingHttpClient", lambda **kwargs: recording_client(transport=httpx.MockTransport(handler), **kwargs)
        )
        client = oci._openai_client.__wrapped__("provider-a", "https://enterprise-ai.example/openai/v1", "test-key", "", 30)
        monkeypatch.setattr(oci, "_openai_client_for_provider", lambda *_: client)
        settings = replace(get_settings(environ={}, dotenv_path=None), llm_stall_detection=True, answer_max_tokens=1000)
        with bind_deadline(Deadline(600)):
            return oci._openai_parsed([{"role": "user", "content": "合成の質問"}], settings, TextOutput, PROVIDER)

    return run


UNORDERED = {
    # 検証環境で 16 / 16 回 SDK の `_responses.py:344` の IndexError になった種類の順。
    "content_part_before_item": [_created(), _part_added(0), _delta(0), _item_added(0, _message("")), _completed(_message())],
    "output_index_gap": [_created(), _item_added(1, _message("")), _part_added(1), _delta(1), _completed(_message())],
    # 検証環境で `_responses.py:254` の AssertionError になった種類（delta が message でない項目を指す）。
    "delta_on_reasoning_item": [_created(), _item_added(0, REASONING), _delta(0), _completed(REASONING, _message())],
}


@pytest.mark.parametrize("events", UNORDERED.values(), ids=UNORDERED.keys())
def test_event_order_does_not_decide_the_result(send, caplog: pytest.LogCaptureFixture, events) -> None:
    bodies: list[dict[str, Any]] = []
    with caplog.at_level(logging.INFO, logger=oci.__name__):
        result = send([events], bodies)
    assert result.text == "合成の本文"
    assert len(bodies) == 1
    assert bodies[0]["stream"] is True
    assert bodies[0]["text"]["format"]["strict"] is True
    assert "text_format" not in bodies[0]
    usage = next(record for record in caplog.records if record.__dict__.get("event") == "llm_call_usage")
    assert (usage.__dict__["llm_status"], usage.__dict__["output_tokens"]) == ("completed", 11)


def test_unordered_events_are_recorded_without_text(send, caplog: pytest.LogCaptureFixture) -> None:
    bodies: list[dict[str, Any]] = []
    with caplog.at_level(logging.INFO, logger=oci.__name__):
        send([UNORDERED["content_part_before_item"]], bodies)
    usage = next(record for record in caplog.records if record.__dict__.get("event") == "llm_call_usage")
    assert usage.__dict__["llm_unordered_events"] == 2
    warning = next(record.getMessage() for record in caplog.records if "out of order" in record.getMessage())
    assert "response.content_part.added" in warning
    assert "合成の本文" not in warning


def test_sdk_assembly_cannot_read_the_same_events() -> None:
    """同じ順を SDK の組み立てに渡すと失敗する（`responses.stream` を使わない理由）。"""
    from openai.lib.streaming.responses._responses import ResponseStreamState
    from openai.types.responses import ResponseStreamEvent
    from pydantic import TypeAdapter

    adapter: TypeAdapter[Any] = TypeAdapter(ResponseStreamEvent)
    state: ResponseStreamState[TextOutput] = ResponseStreamState(input_tools=[], text_format=TextOutput)
    events = [
        adapter.validate_python({**event, "sequence_number": number}) for number, event in enumerate(UNORDERED["content_part_before_item"])
    ]
    state.handle_event(events[0])
    with pytest.raises(IndexError):
        state.handle_event(events[1])


def test_completed_event_without_output_is_resent_then_fails_with_trace(send, caplog: pytest.LogCaptureFixture) -> None:
    """event で本文が届いても、完了の event に本文が無ければ失敗にする（delta から組み立て直さない）。"""
    missing = [_created(), _delta(0), _completed(REASONING)]
    bodies: list[dict[str, Any]] = []
    with caplog.at_level(logging.INFO, logger=oci.__name__), pytest.raises(oci.LlmStreamOutputMissing) as raised:
        send([missing for _ in range(1 + oci.LLM_CALL_MAX_RETRIES)], bodies)
    assert len(bodies) == 1 + oci.LLM_CALL_MAX_RETRIES
    assert _invalid_structured_output(raised.value)
    detail = generation_failure_detail(raised.value)
    assert detail["category"] == "stream_output_missing"
    trace = detail["stream_trace"]
    assert trace["final_output_types"] == ["reasoning"]
    assert trace["delta_events"] == 1
    assert [row["type"] for row in trace["events"]] == ["response.created", "response.completed"]
    assert "合成の本文" not in json.dumps(detail, ensure_ascii=False)
    assert "合成の本文" not in caplog.text


def test_resend_recovers_after_completed_event_without_output(send) -> None:
    bodies: list[dict[str, Any]] = []
    result = send([[_created(), _completed()], [_created(), _completed(_message())]], bodies)
    assert (result.text, len(bodies)) == ("合成の本文", 2)


def test_invalid_output_raises_validation_error_and_keeps_tokens(send, caplog: pytest.LogCaptureFixture) -> None:
    """本文の不合格は `ValidationError`（既存の再実行の対象）のまま。token の数と類別を記録に残す。"""
    events = [_created(), _item_added(0, _message("")), _completed(_message('{"text": 1}'))]
    bodies: list[dict[str, Any]] = []
    with caplog.at_level(logging.INFO, logger=oci.__name__), pytest.raises(ValidationError):
        send([events], bodies)
    assert len(bodies) == 1
    usage = next(record for record in caplog.records if record.__dict__.get("event") == "llm_call_usage")
    assert usage.__dict__["llm_status"] == "failed"
    assert usage.__dict__["llm_incomplete_reason"] == "ValidationError"
    assert usage.__dict__["llm_failure"]["category"] == "schema_field"
    assert usage.__dict__["output_tokens"] == 11


def test_truncated_output_is_treated_as_incomplete_not_invalid(send) -> None:
    """上限で切れた応答の本文の done を検証の失敗にせず、未完了の応答として上限を広げて送り直す（#4145 と同じ）。"""
    truncated = '{"text": "合成'
    incomplete = [
        _created(),
        _item_added(0, _message("")),
        _delta(0, truncated),
        {"type": "response.output_text.done", "output_index": 0, "content_index": 0, "item_id": "msg-1", "text": truncated, "logprobs": []},
        {
            "type": "response.incomplete",
            "response": {
                **_RESPONSE,
                "status": "incomplete",
                "incomplete_details": {"reason": "max_output_tokens"},
                "output": [_message(truncated)],
                "usage": USAGE,
            },
        },
    ]
    bodies: list[dict[str, Any]] = []
    with pytest.raises(oci.IncompleteResponse):
        send([incomplete, [_created(), _completed(_message())]], bodies)
    assert [body["max_output_tokens"] for body in bodies] == [32_768]


def test_refusal_in_completed_event_is_not_reported_as_missing_output(send) -> None:
    refusal = {**_message(), "content": [{"type": "refusal", "refusal": "合成の拒否"}]}
    bodies: list[dict[str, Any]] = []
    with pytest.raises(RuntimeError, match="拒否") as raised:
        send([[_created(), _completed(refusal)]], bodies)
    assert not isinstance(raised.value, oci.LlmStreamOutputMissing)
    assert len(bodies) == 1
