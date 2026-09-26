"""フィードバックの rag_poc 分類・修正した回答と、Approved FAQ / 品質評価のケースへの昇格。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from app.api.routes import feedback as feedback_route
from app.main import app
from app.schemas.feedback import FeedbackRequest
from tests.support import AsgiTestClient

client = AsgiTestClient(app)

BASE_REQUEST: dict[str, Any] = {
    "trace_id": "trace-1",
    "business_view_id": "bv-1",
    "target_type": "answer",
    "source_surface": "search",
}


@pytest.mark.parametrize("reason", ["missing_knowledge", "outdated_source", "ambiguous_question"])
def test_answer_feedback_accepts_rag_poc_reasons(reason: str) -> None:
    request = FeedbackRequest.model_validate(
        {**BASE_REQUEST, "rating": "not_helpful", "reason": reason}
    )

    assert request.reason == reason


def test_rag_poc_reasons_are_answer_only() -> None:
    with pytest.raises(ValidationError, match="対応していない理由"):
        FeedbackRequest.model_validate(
            {
                **BASE_REQUEST,
                "target_type": "citation",
                "document_id": "doc-1",
                "chunk_id": "doc-1:c1",
                "rating": "not_helpful",
                "reason": "missing_knowledge",
            }
        )


def test_corrected_answer_is_answer_only_and_cleared_for_helpful() -> None:
    helpful = FeedbackRequest.model_validate(
        {**BASE_REQUEST, "rating": "helpful", "corrected_answer": "修正"}
    )
    with pytest.raises(ValidationError, match="修正した回答"):
        FeedbackRequest.model_validate(
            {
                **BASE_REQUEST,
                "target_type": "citation",
                "document_id": "doc-1",
                "chunk_id": "doc-1:c1",
                "rating": "not_helpful",
                "reason": "not_relevant",
                "corrected_answer": "修正",
            }
        )

    assert helpful.corrected_answer is None


def test_submit_feedback_saves_corrected_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakePromotionClient()
    monkeypatch.setattr(feedback_route, "OracleClient", lambda: fake)

    response = client.post(
        "/api/feedback",
        json={
            **BASE_REQUEST,
            "rating": "not_helpful",
            "reason": "outdated_source",
            "corrected_answer": "  2026年版の規程では部長承認です。  ",
        },
    )

    assert response.status_code == 200
    assert response.json()["data"]["corrected_answer"] == "2026年版の規程では部長承認です。"
    assert "corrected_answer" not in fake.saved[0]
    assert fake.saved_details[0] is not None
    assert fake.saved_details[0]["corrected_answer_text"] == "2026年版の規程では部長承認です。"


def _detail(**overrides: Any) -> dict[str, Any]:
    return {
        "feedback_id": "feedback-1",
        "trace_id": "trace-1",
        "business_view_id": "bv-1",
        "target_type": "answer",
        "source_surface": "search",
        "document_id": None,
        "chunk_id": None,
        "message_id": None,
        "rating": "not_helpful",
        "reason": "outdated_source",
        "comment": "規程が改定されています。",
        "corrected_answer": "2026年版の規程では部長の承認が必要です。",
        "created_at": datetime(2026, 9, 26, tzinfo=UTC),
        "content_source": "search_snapshot",
        "question": "経費の承認者は？",
        "answer": "課長の承認が必要です。",
        "citations": [
            {"document_id": "doc-1", "chunk_id": "doc-1:c1"},
            {"document_id": "doc-1", "chunk_id": "doc-1:c2"},
            {"document_id": "doc-2", "chunk_id": "doc-2:c1"},
        ],
        **overrides,
    }


def test_promote_feedback_registers_corrected_answer_as_faq(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakePromotionClient(detail=_detail())
    monkeypatch.setattr(feedback_route, "OracleClient", lambda: fake)

    first = client.post("/api/feedback/feedback-1/approved-faq")
    # 同じ質問をもう一度登録すると、既存の FAQ を置き換える。
    second = client.post("/api/feedback/feedback-1/approved-faq")

    assert first.status_code == 200, first.text
    assert first.json()["data"] == {
        "business_view_id": "bv-1",
        "question": "経費の承認者は？",
        "inserted_count": 1,
        "deleted_count": 0,
    }
    assert second.json()["data"]["deleted_count"] == 1
    records = _faq_records(fake.knowledge["bv-1"])
    assert [(record["question"], record["approved_answer"]) for record in records] == [
        ("経費の承認者は？", "2026年版の規程では部長の承認が必要です。")
    ]
    assert "outdated_source" in records[0]["tags"]


def test_promote_helpful_feedback_uses_saved_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakePromotionClient(
        detail=_detail(rating="helpful", reason=None, comment=None, corrected_answer=None)
    )
    monkeypatch.setattr(feedback_route, "OracleClient", lambda: fake)

    response = client.post("/api/feedback/feedback-1/approved-faq")

    assert response.status_code == 200
    assert _faq_records(fake.knowledge["bv-1"])[0]["approved_answer"] == "課長の承認が必要です。"


@pytest.mark.parametrize(
    ("detail", "answer_record", "status", "message"),
    [
        (_detail(corrected_answer=None), None, 409, "FAQへ反映する回答がありません"),
        (
            _detail(rating="helpful", reason=None, comment=None, corrected_answer=None),
            {"diagnostics_json": {"confidence": "low", "insufficient_reason": "根拠がない"}},
            409,
            "根拠不足の回答は FAQ 化しません",
        ),
        (
            _detail(target_type="citation", document_id="doc-1", chunk_id="doc-1:c1"),
            None,
            409,
            "回答のフィードバックだけ",
        ),
    ],
)
def test_promote_feedback_follows_rag_poc_skip_rules(
    monkeypatch: pytest.MonkeyPatch,
    detail: dict[str, Any],
    answer_record: dict[str, Any] | None,
    status: int,
    message: str,
) -> None:
    fake = FakePromotionClient(detail=detail, answer_record=answer_record)
    monkeypatch.setattr(feedback_route, "OracleClient", lambda: fake)

    response = client.post("/api/feedback/feedback-1/approved-faq")

    assert response.status_code == status
    assert message in response.json()["error_messages"][0]
    assert fake.knowledge == {}


def test_promote_unknown_feedback_returns_404(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(feedback_route, "OracleClient", lambda: FakePromotionClient())

    assert client.post("/api/feedback/missing/approved-faq").status_code == 404
    assert client.get("/api/feedback/missing/evaluation-case").status_code == 404


def test_evaluation_case_uses_corrected_answer_terms(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakePromotionClient(detail=_detail())
    monkeypatch.setattr(feedback_route, "OracleClient", lambda: fake)

    response = client.get("/api/feedback/feedback-1/evaluation-case")

    assert response.status_code == 200
    case = response.json()["data"]
    assert case["id"] == "feedback-feedback-1"
    assert case["query"] == "経費の承認者は？"
    # 低評価の引用は正解の文書にしない。
    assert case["relevant_document_ids"] == []
    assert "部長" in "".join(case["expected_answer_keywords"])
    assert "課長" not in "".join(case["expected_answer_keywords"])


def test_evaluation_case_for_helpful_feedback_uses_cited_documents(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakePromotionClient(
        detail=_detail(rating="helpful", reason=None, comment=None, corrected_answer=None)
    )
    monkeypatch.setattr(feedback_route, "OracleClient", lambda: fake)

    case = client.get("/api/feedback/feedback-1/evaluation-case").json()["data"]

    assert case["relevant_document_ids"] == ["doc-1", "doc-2"]
    assert "課長" in "".join(case["expected_answer_keywords"])


def test_evaluation_case_requires_expected_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakePromotionClient(detail=_detail(corrected_answer=None))
    monkeypatch.setattr(feedback_route, "OracleClient", lambda: fake)

    response = client.get("/api/feedback/feedback-1/evaluation-case")

    assert response.status_code == 409


def _faq_records(payload: dict[str, object]) -> list[dict[str, Any]]:
    records = payload.get("records")
    assert isinstance(records, list)
    return records


class FakePromotionClient:
    def __init__(
        self,
        *,
        detail: dict[str, Any] | None = None,
        answer_record: dict[str, Any] | None = None,
    ) -> None:
        self.detail = detail
        self.answer_record = answer_record
        self.knowledge: dict[str, dict[str, object]] = {}
        self.saved: list[dict[str, object]] = []
        self.saved_details: list[dict[str, object] | None] = []

    async def get_business_view(self, business_view_id: str) -> object | None:
        return object()

    async def save_feedback(
        self, payload: dict[str, object], *, details: dict[str, object] | None = None
    ) -> str:
        self.saved.append(payload)
        self.saved_details.append(details)
        return "feedback-1"

    async def get_feedback_detail(self, feedback_id: str) -> dict[str, Any] | None:
        return self.detail if feedback_id == "feedback-1" else None

    async def get_answer_record(self, trace_id: str) -> dict[str, Any] | None:
        return self.answer_record

    async def get_business_view_knowledge(
        self, business_view_id: str, kind: str
    ) -> dict[str, object] | None:
        return self.knowledge.get(business_view_id)

    async def save_business_view_knowledge(
        self, business_view_id: str, kind: str, payload: dict[str, object]
    ) -> None:
        self.knowledge[business_view_id] = payload


@pytest.mark.usefixtures("oracle_db")
async def test_new_reason_and_corrected_answer_round_trip_on_real_oracle() -> None:
    """実 Oracle 26ai で、追加した理由(CHECK 制約)と修正した回答の列を保存・取得できる。"""
    from app.clients.oracle import OracleClient

    oracle = OracleClient()
    request = FeedbackRequest.model_validate(
        {
            **BASE_REQUEST,
            "trace_id": "pytest-feedback-promotion",
            "rating": "not_helpful",
            "reason": "missing_knowledge",
            "corrected_answer": "登録文書にない手順のため、窓口へ確認します。",
        }
    )
    payload = request.model_dump(
        mode="json", exclude={"message_id", "content_snapshot", "comment", "corrected_answer"}
    )
    payload["comment_hash"] = request.comment_hash
    payload["comment_chars"] = request.comment_chars
    feedback_id = await oracle.save_feedback(
        payload,
        details={
            "message_id": None,
            "content_source": "search_snapshot",
            "question_text": "申請の窓口は？",
            "answer_text": "分かりません。",
            "citations": [],
            "comment_text": None,
            "corrected_answer_text": request.corrected_answer,
        },
    )

    row = await oracle.get_feedback_detail(feedback_id)

    assert row is not None
    assert row["reason"] == "missing_knowledge"
    assert row["corrected_answer"] == "登録文書にない手順のため、窓口へ確認します。"
