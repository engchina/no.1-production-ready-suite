"""会話のメッセージの段落の印（#2738）。会話を開き直したときの読み元。"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from app.api.routes import chat as chat_route
from app.clients.oracle import StoredMessage, _message_passage_record

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
