"""利用者フィードバック API のテスト。"""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.api.routes import feedback as feedback_route
from app.main import app
from app.rag.request_context import current_audit_request_context
from app.schemas.feedback import FeedbackRequest
from tests.support import AsgiTestClient

client = AsgiTestClient(app)


def test_feedback_request_validates_target_and_reason() -> None:
    """対象ごとの ID と低評価理由を必須にする。"""
    with pytest.raises(ValidationError, match="文書 ID とチャンク ID"):
        FeedbackRequest(
            trace_id="trace-1",
            business_view_id="bv-1",
            target_type="citation",
            source_surface="search",
            rating="not_helpful",
            reason="not_relevant",
        )
    with pytest.raises(ValidationError, match="理由を選択"):
        FeedbackRequest(
            trace_id="trace-1",
            business_view_id="bv-1",
            target_type="answer",
            source_surface="chat",
            rating="not_helpful",
        )
    with pytest.raises(ValidationError, match="対応していない理由"):
        FeedbackRequest(
            trace_id="trace-1",
            business_view_id="bv-1",
            target_type="answer",
            source_surface="chat",
            rating="not_helpful",
            reason="missing_evidence",
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("trace_id", "t" * 65),
        ("business_view_id", "b" * 65),
        ("document_id", "d" * 65),
        ("chunk_id", "c" * 129),
    ],
)
def test_feedback_request_rejects_ids_longer_than_oracle_columns(
    field: str,
    value: str,
) -> None:
    """ID は保存先の Oracle 列幅を超えて受け付けない。"""
    payload = {
        "trace_id": "trace-1",
        "business_view_id": "bv-1",
        "target_type": "citation",
        "source_surface": "search",
        "document_id": "doc-1",
        "chunk_id": "chunk-1",
        "rating": "helpful",
        field: value,
    }

    with pytest.raises(ValidationError, match="at most"):
        FeedbackRequest.model_validate(payload)


def test_submit_feedback_saves_only_categorical_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    """新 API は本文や自由記述を受け取らず、分類 metadata だけ保存する。"""
    fake = FakeFeedbackClient()
    monkeypatch.setattr(feedback_route, "OracleClient", lambda: fake)

    response = client.post(
        "/api/feedback",
        json={
            "trace_id": "trace-1",
            "business_view_id": "bv-1",
            "target_type": "answer",
            "source_surface": "chat",
            "rating": "not_helpful",
            "reason": "incorrect",
        },
    )

    assert response.status_code == 200
    assert response.json()["data"]["feedback_id"] == "feedback-1"
    assert fake.saved == [
        {
            "trace_id": "trace-1",
            "business_view_id": "bv-1",
            "target_type": "answer",
            "source_surface": "chat",
            "document_id": None,
            "chunk_id": None,
            "rating": "not_helpful",
            "reason": "incorrect",
        }
    ]
    assert "comment" not in str(fake.saved)
    assert fake.user_id_hash is not None


def test_submit_feedback_rejects_unknown_business_view(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeFeedbackClient(view_exists=False)
    monkeypatch.setattr(feedback_route, "OracleClient", lambda: fake)

    response = client.post(
        "/api/feedback",
        json={
            "trace_id": "trace-1",
            "business_view_id": "missing",
            "target_type": "answer",
            "source_surface": "search",
            "rating": "helpful",
        },
    )

    assert response.status_code == 404
    assert not fake.saved


def test_current_feedback_returns_latest_items(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeFeedbackClient()
    fake.current = [
        {
            "feedback_id": "feedback-2",
            "trace_id": "trace-1",
            "business_view_id": "bv-1",
            "target_type": "citation",
            "source_surface": "chat",
            "document_id": "doc-1",
            "chunk_id": "doc-1:0",
            "rating": "not_helpful",
            "reason": "not_relevant",
            "created_at": datetime(2026, 7, 1, tzinfo=UTC),
        }
    ]
    monkeypatch.setattr(feedback_route, "OracleClient", lambda: fake)

    response = client.get("/api/feedback/current?trace_id=trace-1")

    assert response.status_code == 200
    assert response.json()["data"][0]["feedback_id"] == "feedback-2"
    assert fake.current_trace == "trace-1"


def test_feedback_dashboard_builds_latest_vote_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeFeedbackClient()
    fake.dashboard = (
        [
            {
                "feedback_id": "feedback-2",
                "trace_id": "trace-1",
                "business_view_id": "bv-1",
                "business_view_name": "経理",
                "target_type": "answer",
                "source_surface": "chat",
                "document_id": None,
                "chunk_id": None,
                "rating": "not_helpful",
                "reason": "incorrect",
                "created_at": datetime(2026, 7, 1, tzinfo=UTC),
                "conversation_id": "conversation-1",
                "conversation_title": "経費精算",
                "message_id": "message-1",
                "model": "model-a",
                "file_name": None,
            }
        ],
        3,
        [
            {"target_type": "answer", "rating": "helpful", "reason": None, "item_count": 1},
            {
                "target_type": "answer",
                "rating": "not_helpful",
                "reason": "incorrect",
                "item_count": 1,
            },
            {
                "target_type": "citation",
                "rating": "helpful",
                "reason": None,
                "item_count": 1,
            },
        ],
    )
    monkeypatch.setattr(feedback_route, "OracleClient", lambda: fake)

    response = client.get("/api/feedback?period_days=30&limit=20&offset=0")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["summary"] == {
        "total": 3,
        "helpful_count": 2,
        "not_helpful_count": 1,
        "helpful_rate": 0.6667,
        "answer_total": 2,
        "answer_helpful_rate": 0.5,
        "citation_total": 1,
        "citation_helpful_rate": 1.0,
        "reason_counts": [{"reason": "incorrect", "count": 1}],
    }
    assert data["items"]["total"] == 3


def test_feedback_dashboard_rejects_non_admin() -> None:
    request = SimpleNamespace(state=SimpleNamespace(auth_session=SimpleNamespace(role="USER")))

    with pytest.raises(HTTPException) as exc_info:
        feedback_route._require_feedback_admin(request)  # type: ignore[arg-type]

    assert exc_info.value.status_code == 403


class FakeFeedbackClient:
    def __init__(self, *, view_exists: bool = True) -> None:
        self.view_exists = view_exists
        self.saved: list[dict[str, object]] = []
        self.current: list[dict[str, object]] = []
        self.current_trace: str | None = None
        self.user_id_hash: str | None = None
        self.dashboard: tuple[list[dict[str, object]], int, list[dict[str, object]]] = ([], 0, [])

    async def get_business_view(self, business_view_id: str) -> object | None:
        return object() if self.view_exists else None

    async def save_feedback(self, payload: dict[str, object]) -> str:
        self.user_id_hash = current_audit_request_context().user_id_hash
        self.saved.append(payload)
        return "feedback-1"

    async def list_current_feedback(self, trace_id: str) -> list[dict[str, object]]:
        self.current_trace = trace_id
        return self.current

    async def list_feedback_dashboard_rows(
        self, **_: object
    ) -> tuple[list[dict[str, object]], int, list[dict[str, object]]]:
        return self.dashboard
