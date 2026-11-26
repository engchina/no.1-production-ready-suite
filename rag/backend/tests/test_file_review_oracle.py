"""同じ回答版の判定・再回答の保存・問題単位の集計を実 DB で確かめる。"""

from hashlib import sha256
from uuid import uuid4

import pytest
from pr_backend_core.file_review import StaleReview

from app.api.routes.feedback import _feedback_summary
from app.clients.oracle import OracleClient
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
