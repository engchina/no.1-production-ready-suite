"""会話のメッセージの段落の印（#2738）。会話を開き直したときの読み元。"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from app.api.routes import chat as chat_route
from app.clients.oracle import StoredMessage, _message_passage_record, _stored_message_from_row

MARKS = [
    {
        "text": "・合成の説明",
        "state": "warning",
        "reasons": [{"code": "item_check", "label": "システムの検査で注意が必要", "detail": "値"}],
    }
]


def test_rows_read_the_marks_of_the_answer_record() -> None:
    """回答の記録の JSON（文字列か値）を読み、無い・壊れた値は捨てる。"""
    row = {
        "passage_marks_json": json.dumps(MARKS, ensure_ascii=False),
        "removed_passages_json": [],
        "answer_warnings_json": "{壊れた",
    }
    assert _message_passage_record(row) == {"passage_marks": MARKS, "removed_passages": []}
    assert _message_passage_record({}) is None


def _message(status: str, record: dict[str, object] | None) -> StoredMessage:
    return StoredMessage(
        id="m1",
        conversation_id="c1",
        role="ASSISTANT",
        content="合成の回答",
        status=status,
        created_at=datetime.now(UTC),
        passage_record=record,
    )


def test_chat_message_carries_the_marks_of_a_finished_answer() -> None:
    message = chat_route._to_chat_message(_message("COMPLETE", {"passage_marks": MARKS}))
    assert message.passage_marks == MARKS
    assert (message.removed_passages, message.answer_warnings) == ([], [])
    # 作成中の回答・記録の無い回答（この機能より前の回答）は空。
    streaming = chat_route._to_chat_message(_message("STREAMING", {"passage_marks": MARKS}))
    assert streaming.passage_marks == []
    assert chat_route._to_chat_message(_message("COMPLETE", None)).passage_marks == []


def test_chat_message_carries_the_vision_model_that_wrote_the_answer() -> None:
    """画像を添付して Vision モデルで作った回答は、そのモデルを見出し用に返す（#3439）。"""
    vision = {"model_id": "vision-model-a", "label": "Vision モデル A"}
    row = {
        "message_id": "m1",
        "conversation_id": "c1",
        "role": "ASSISTANT",
        "status": "COMPLETE",
        "created_at": datetime.now(UTC),
        "answer_vision_model_json": json.dumps(vision, ensure_ascii=False),
    }
    stored = _stored_message_from_row(row)
    assert stored.answer_vision_model == vision
    message = chat_route._to_chat_message(stored)
    assert message.answer_vision_model is not None
    assert message.answer_vision_model.model_dump() == vision
    # 名前の無い記録は model_id を名前にする。
    # 画像を添付しなかった回答（null）・壊れた値・記録の無い回答は None。
    unnamed = _stored_message_from_row(
        {**row, "answer_vision_model_json": {"model_id": "vision-model-a"}}
    )
    assert unnamed.answer_vision_model == {"model_id": "vision-model-a", "label": "vision-model-a"}
    for value in (None, "null", "{壊れた", {"label": "名前だけ"}):
        assert (
            _stored_message_from_row({**row, "answer_vision_model_json": value}).answer_vision_model
            is None
        )
    # 作成中の回答には出さない。
    streaming = chat_route._to_chat_message(
        _stored_message_from_row({**row, "status": "STREAMING"})
    )
    assert streaming.answer_vision_model is None
