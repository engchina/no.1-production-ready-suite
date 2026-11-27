"""同じ回答版の判定・再回答の保存・問題単位の集計を実 DB で確かめる。"""

from datetime import UTC, datetime
from hashlib import sha256
from uuid import uuid4

import pytest
from pr_backend_core.file_review import StaleReview

from app.api.routes.feedback import _feedback_summary
from app.clients.oracle import OracleClient, StoredMessage
from app.rag.request_context import (
    AuditRequestContext,
    reset_audit_request_context,
    set_audit_request_context,
)
from app.schemas.feedback import FeedbackSummary


@pytest.mark.usefixtures("oracle_db")
@pytest.mark.parametrize("surface", ["chat", "search"])
async def test_review_versions_and_latest_resolution_in_oracle(surface: str) -> None:
    client = OracleClient()
    owner = sha256(uuid4().bytes).hexdigest()
    first, second = uuid4().hex, uuid4().hex
    token = set_audit_request_context(
        AuditRequestContext(request_id="review-integration", user_id_hash=owner)
    )
    record = {
        "trace_id": first,
        "surface": surface,
        "answer_engine": "grounded",
        "question": "合成の質問",
        "answer": "最初の回答",
        "review_request": {"request": {"query": "合成の質問"}},
    }
    vote = {"trace_id": first, "source_surface": surface, "target_type": "answer"}
    details = {
        "content_source": "search_snapshot",
        "question_text": "合成の質問",
        "answer_text": "最初の回答",
        "citations": [],
    }

    async def summary() -> FeedbackSummary:
        _, _, groups, _ = await client.list_feedback_dashboard_rows(
            search_answer_profile_id=None,
            target_type="answer",
            rating=None,
            reason=None,
            period_days=None,
            search_query=None,
            sort_order="newest",
            limit=20,
            offset=0,
        )
        return _feedback_summary(groups)

    try:
        await client.save_answer_record(record)
        first_id = await client.save_feedback({**vote, "rating": "helpful"}, details=details)
        assert (
            await client.save_feedback({**vote, "rating": "not_helpful"}, details=details)
            == first_id
        )
        assert (await summary()).unretried_unresolved == 1
        assert await client.file_review_ratings([first], owner) == {first: "not_helpful"}
        pending = {"revision": 1, "versions": [{"id": first}]}
        await client.save_file_review(first, 0, pending, {})
        assert (await summary()).regenerated_questions == 0
        revised = {"revision": 2, "versions": [{"id": first}, {"id": second}]}
        new_record: dict[str, object] = {
            **record,
            "trace_id": second,
            "answer": "選んだ資料からの回答",
        }
        with pytest.raises(StaleReview):
            await client.save_file_review(first, 0, revised, new_record)
        assert await client.get_answer_record(second) is None
        await client.save_file_review(first, 1, revised, new_record)
        assert (await summary()).regenerated_questions == 1
        assert (await summary()).unretried_unresolved == 0
        await client.save_feedback(
            {**vote, "trace_id": second, "rating": "helpful"}, details=details
        )
        result = await summary()
        assert (result.total, result.question_total, result.resolved_after_retry) == (2, 1, 1)
        assert await client.file_review_ratings([first, second], owner) == {
            first: "not_helpful",
            second: "helpful",
        }
    finally:
        reset_audit_request_context(token)


@pytest.mark.usefixtures("oracle_db")
async def test_chat_message_reads_latest_reviewed_answer_in_oracle() -> None:
    """再回答の後の会話のメッセージは最新の版の回答も返し、実行を結び付けるメッセージを引ける。

    会話の履歴（#5914）と、再回答の実行履歴の会話・メッセージ（#5913）が使う読み取り。
    """
    client = OracleClient()
    profile = await client.ensure_default_search_answer_profile()
    # 版の保存は回答の持ち主だけなので、利用者の分かる context で会話と回答を作る。
    owner = sha256(uuid4().bytes).hexdigest()
    token = set_audit_request_context(
        AuditRequestContext(request_id="review-integration", user_id_hash=owner)
    )
    conversation = await client.create_conversation(search_answer_profile_id=profile.id)
    user_id, answer_id, other_id = uuid4().hex, uuid4().hex, uuid4().hex
    first, second, untouched = uuid4().hex, uuid4().hex, uuid4().hex
    now = datetime.now(UTC)
    record: dict[str, object] = {
        "surface": "chat",
        "answer_engine": "grounded",
        "question": "合成の質問",
        "answer": "最初の回答",
    }

    def answer(message_id: str, trace_id: str) -> StoredMessage:
        return StoredMessage(
            id=message_id,
            conversation_id=conversation.id,
            role="ASSISTANT",
            content="最初の回答",
            reply_to_message_id=user_id,
            trace_id=trace_id,
            created_at=now,
        )

    async def reviewed() -> dict[str, str | None]:
        messages = await client.list_messages(conversation.id)
        return {message.id: message.reviewed_answer for message in messages}

    try:
        await client.append_message(
            StoredMessage(
                id=user_id,
                conversation_id=conversation.id,
                role="USER",
                content="合成の質問",
                created_at=now,
            )
        )
        await client.append_message(answer(answer_id, first))
        await client.append_message(answer(other_id, untouched))
        await client.save_answer_record({**record, "trace_id": first})
        await client.save_answer_record({**record, "trace_id": untouched})
        assert await client.get_chat_answer_message_id(conversation.id, first) == answer_id
        assert await client.get_chat_answer_message_id(conversation.id, second) is None
        assert await reviewed() == {user_id: None, answer_id: None, other_id: None}
        # 候補の確認だけ（版は 1 つ）では、元の回答のまま。
        versions = [{"id": first, "answer": "最初の回答"}]
        await client.save_file_review(first, 0, {"revision": 1, "versions": versions}, {})
        assert await reviewed() == {user_id: None, answer_id: None, other_id: None}
        versions.append({"id": second, "answer": "選んだ資料からの回答"})
        await client.save_file_review(
            first,
            1,
            {"revision": 2, "versions": versions},
            {**record, "trace_id": second, "answer": "選んだ資料からの回答"},
        )
        assert await reviewed() == {
            user_id: None,
            answer_id: "選んだ資料からの回答",
            other_id: None,
        }
    finally:
        await client.delete_conversation(conversation.id)
        reset_audit_request_context(token)
