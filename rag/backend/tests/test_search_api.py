"""検索 API の HTTP 境界テスト。"""

import asyncio
import logging
from typing import Any, cast

import pytest
from pydantic import ValidationError
from pytest import LogCaptureFixture, MonkeyPatch

from app.api.routes import search as search_route
from app.config import get_settings
from app.main import app
from app.rag.audit import record_rag_search_audit
from app.rag.diagnostics import build_search_diagnostics
from app.rag.pipeline import SearchTokenDelta
from app.schemas.search import (
    SearchRequest,
    SearchResponse,
    SearchRetrievalBreakdown,
    SearchRetrievalCandidate,
)
from tests.support import AsgiTestClient

client = AsgiTestClient(app)


def test_search_api_returns_504_when_pipeline_times_out(
    monkeypatch: MonkeyPatch,
    caplog: LogCaptureFixture,
) -> None:
    """通常検索は pipeline timeout を ApiResponse 形式の 504 にして監査へ残す。"""
    _force_search_timeout(monkeypatch)

    with caplog.at_level(logging.INFO, logger="app.audit"):
        response = client.post("/api/search", json={"query": "INV-SECRET の承認条件"})

    assert response.status_code == 504
    body = response.json()
    assert body["data"] is None
    assert body["error_messages"] == [search_route.SEARCH_TIMEOUT_MESSAGE]

    audit_record = next(record for record in caplog.records if record.message == "rag_search_audit")
    audit_event = cast(Any, audit_record).audit_event
    assert audit_event["outcome"] == "error"
    assert audit_event["error_stage"] == "timeout"
    assert audit_event["error_type"] == "TimeoutError"
    assert audit_event["retrieved_count"] == 0
    assert audit_event["citation_count"] == 0
    assert audit_event["top_k"] == 20
    assert audit_event["rerank_top_n"] == 5
    assert audit_event["context_window_chars"]
    assert audit_event["config_fingerprint"]
    assert audit_event["trace_id"]
    assert "INV-SECRET" not in str(audit_event)


def test_stream_search_api_emits_error_event_when_pipeline_times_out(
    monkeypatch: MonkeyPatch,
) -> None:
    """SSE 検索は stream 開始後の timeout を error event として返す。"""
    _force_search_timeout(monkeypatch)

    response = client.post("/api/search/stream", json={"query": "承認条件"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "event: error" in response.text
    assert search_route.SEARCH_TIMEOUT_MESSAGE in response.text


def test_stream_search_api_uses_realtime_deltas_without_duplicate_answer(
    monkeypatch: MonkeyPatch,
) -> None:
    """Enterprise AI token stream を使う場合、最終 answer を二重 delta 化しない。"""
    settings = get_settings()
    monkeypatch.setattr(settings, "rag_stream_realtime_enabled", True)
    monkeypatch.setattr(search_route, "RagPipeline", RealtimeStreamingPipeline)

    response = client.post("/api/search/stream", json={"query": "承認条件"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.text.count("event: delta") == 2
    assert '{"text": "承認条件は "}' in response.text
    assert '{"text": "120000 円です。"}' in response.text
    assert '{"text": "承認条件は 120000 円です。"}' not in response.text
    assert "event: metadata" in response.text
    assert '"keyword_terms": ["承認条件"]' in response.text
    assert '"retrieval_breakdown":' in response.text
    assert '"vector_count": 1' in response.text
    assert '"retrieval_candidates":' in response.text
    assert "候補本文" not in response.text
    assert "event: citations" in response.text
    assert "event: done" in response.text


def test_search_api_hashes_tenant_and_user_headers_into_audit(
    monkeypatch: MonkeyPatch,
    caplog: LogCaptureFixture,
) -> None:
    """HTTP header の tenant/user id は hash として RAG 監査へ相関される。"""
    monkeypatch.setattr(search_route, "RagPipeline", AuditingPipeline)

    with caplog.at_level(logging.INFO, logger="app.audit"):
        response = client.post(
            "/api/search",
            json={"query": "存在しない社内規程"},
            headers={
                "X-Tenant-ID": "tenant-a",
                "X-User-ID": "user@example.com",
            },
        )

    assert response.status_code == 200
    audit_record = next(record for record in caplog.records if record.message == "rag_search_audit")
    audit_event = cast(Any, audit_record).audit_event
    assert audit_event["request_id"] == response.headers["x-request-id"]
    assert len(audit_event["tenant_id_hash"]) == 64
    assert len(audit_event["user_id_hash"]) == 64
    assert "tenant-a" not in str(audit_event)
    assert "user@example.com" not in str(audit_event)


def test_search_api_records_knowledge_base_scope_in_audit(
    monkeypatch: MonkeyPatch,
    caplog: LogCaptureFixture,
) -> None:
    """検索 API の KB スコープは diagnostics と audit に残る。"""
    monkeypatch.setattr(search_route, "RagPipeline", AuditingPipeline)

    with caplog.at_level(logging.INFO, logger="app.audit"):
        response = client.post(
            "/api/search",
            json={
                "query": "存在しない社内規程",
                "knowledge_base_ids": ["kb-1", "kb-2"],
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["data"]["diagnostics"]["knowledge_base_count"] == 2
    audit_record = next(record for record in caplog.records if record.message == "rag_search_audit")
    audit_event = cast(Any, audit_record).audit_event
    assert audit_event["knowledge_base_ids"] == ["kb-1", "kb-2"]


def test_citation_feedback_api_saves_low_sensitivity_payload(
    monkeypatch: MonkeyPatch,
) -> None:
    """引用 feedback は comment 明文を落とさず hash と文字数だけを保存する。"""
    fake = CapturingFeedbackClient()
    monkeypatch.setattr(search_route, "OracleClient", lambda: fake)

    response = client.post(
        "/api/search/citation-feedback",
        json={
            "trace_id": "trace-1",
            "document_id": "doc-1",
            "chunk_id": "doc-1:0",
            "rating": "not_helpful",
            "reason": "missing_evidence",
            "comment": "根拠のページが違います",
        },
        headers={"X-Tenant-ID": "tenant-a", "X-User-ID": "user@example.com"},
    )

    assert response.status_code == 200
    body = response.json()["data"]
    assert body["feedback_id"] == "feedback-1"
    assert body["trace_id"] == "trace-1"
    assert body["rating"] == "not_helpful"
    assert fake.saved_payloads == [
        {
            "trace_id": "trace-1",
            "document_id": "doc-1",
            "chunk_id": "doc-1:0",
            "rating": "not_helpful",
            "reason": "missing_evidence",
            "comment_hash": fake.saved_payloads[0]["comment_hash"],
            "comment_chars": 11,
        }
    ]
    comment_hash = fake.saved_payloads[0]["comment_hash"]
    assert isinstance(comment_hash, str)
    assert len(comment_hash) == 64
    assert "根拠" not in str(fake.saved_payloads)
    assert "tenant-a" not in str(fake.saved_payloads)
    assert "user@example.com" not in str(fake.saved_payloads)


def test_search_request_accepts_chunk_metadata_filters() -> None:
    """構造化 chunk metadata filter は検索リクエストとして受け付ける。"""
    request = SearchRequest(
        query="料金表",
        filters={
            "content_kind": "figure",
            "section_title": "料金",
            "section_path": "経費申請",
            "source_acl": "support",
            "document_version": "2024.05",
        },
    )

    assert request.filters == {
        "content_kind": "figure",
        "section_title": "料金",
        "section_path": "経費申請",
        "source_acl": "support",
        "document_version": "2024.05",
    }


def test_search_request_normalizes_content_kind_filter_case() -> None:
    """content_kind filter は API 利用者の大小文字揺れを低 cardinality 値へ寄せる。"""
    request = SearchRequest(query="料金表", filters={"content_kind": " Table "})

    assert request.filters == {"content_kind": "table"}


def test_search_request_rejects_unknown_content_kind_filter() -> None:
    """未知の content_kind は空振りではなく 422 相当の検証エラーにする。"""
    with pytest.raises(ValidationError, match="未対応の内容種別フィルターです"):
        SearchRequest(query="料金表", filters={"content_kind": "chart"})


def test_search_request_normalizes_knowledge_base_ids() -> None:
    """knowledge_base_ids は重複排除され、既存 filters 経路にも同期される。"""
    request = SearchRequest(
        query="料金表",
        knowledge_base_ids=[" kb-1 ", "kb-2", "kb-1"],
    )

    assert request.knowledge_base_ids == ["kb-1", "kb-2"]
    assert request.filters["knowledge_base_id"] == "kb-1,kb-2"


def test_search_request_accepts_legacy_knowledge_base_filter() -> None:
    """既存 filters.knowledge_base_id 指定も新しい配列 field へ反映する。"""
    request = SearchRequest(
        query="料金表",
        filters={"knowledge_base_id": "kb-1, kb-2"},
    )

    assert request.knowledge_base_ids == ["kb-1", "kb-2"]
    assert request.filters["knowledge_base_id"] == "kb-1,kb-2"


def test_search_request_rejects_conflicting_knowledge_base_scope() -> None:
    """配列 field と legacy filter が食い違う場合は曖昧に検索しない。"""
    with pytest.raises(ValidationError, match="knowledge_base_ids"):
        SearchRequest(
            query="料金表",
            filters={"knowledge_base_id": "kb-1"},
            knowledge_base_ids=["kb-2"],
        )


def _force_search_timeout(monkeypatch: MonkeyPatch) -> None:
    """検索 route を低 timeout + 遅い pipeline に差し替える。"""
    settings = get_settings()
    monkeypatch.setattr(settings, "rag_search_timeout_seconds", 0.001)
    monkeypatch.setattr(search_route, "RagPipeline", SlowPipeline)


class SlowPipeline:
    """timeout を再現するテスト用 pipeline。"""

    def __init__(self, *, settings: object | None = None, **_kwargs: object) -> None:
        self._settings = settings

    async def run(
        self,
        _request: SearchRequest,
        trace_id: str | None = None,
        progress_callback: object | None = None,
        token_callback: object | None = None,
    ) -> SearchResponse:
        _ = progress_callback, token_callback
        assert trace_id
        await asyncio.sleep(1)
        raise AssertionError("timeout 前に完了しない")


class RealtimeStreamingPipeline:
    """token_callback へ回答 delta を先に流すテスト用 pipeline。"""

    def __init__(self, *, settings: object | None = None, **_kwargs: object) -> None:
        self._settings = settings

    async def run(
        self,
        request: SearchRequest,
        trace_id: str | None = None,
        progress_callback: object | None = None,
        token_callback: Any | None = None,
    ) -> SearchResponse:
        _ = progress_callback
        assert trace_id
        assert token_callback is not None
        await token_callback(SearchTokenDelta(trace_id=trace_id, text="承認条件は "))
        await token_callback(SearchTokenDelta(trace_id=trace_id, text="120000 円です。"))
        return SearchResponse(
            answer="承認条件は 120000 円です。",
            citations=[],
            trace_id=trace_id,
            elapsed_ms=1.0,
            diagnostics=build_search_diagnostics(
                request,
                settings=get_settings(),
                keyword_terms=["承認条件"],
                retrieval_breakdown=SearchRetrievalBreakdown(
                    vector_count=1,
                    keyword_count=1,
                    overlap_count=1,
                    fused_count=1,
                    rerank_input_count=1,
                    rerank_kept_count=1,
                    evidence_count=1,
                    citation_count=1,
                ),
                retrieval_candidates=[
                    SearchRetrievalCandidate(
                        chunk_id="doc-1:0",
                        document_id="doc-1",
                        file_name="policy.txt",
                        sources=["vector", "keyword"],
                        vector_rank=1,
                        vector_score=0.91,
                        keyword_rank=1,
                        keyword_score=0.82,
                        rrf_score=0.032,
                        rerank_rank=1,
                        rerank_score=0.96,
                        status="citation",
                    )
                ],
            ),
        )


class AuditingPipeline:
    """監査ログだけを記録して空検索結果を返すテスト用 pipeline。"""

    def __init__(self, *, settings: object | None = None, **_kwargs: object) -> None:
        self._settings = settings

    async def run(
        self,
        request: SearchRequest,
        trace_id: str | None = None,
        progress_callback: object | None = None,
        token_callback: object | None = None,
    ) -> SearchResponse:
        _ = progress_callback, token_callback
        assert trace_id
        diagnostics = build_search_diagnostics(
            request,
            settings=get_settings(),
        )
        record_rag_search_audit(
            trace_id=trace_id,
            outcome="no_results",
            mode=request.mode,
            sanitized_query=request.query,
            filters=request.filters,
            findings=[],
            retrieved_count=0,
            citations=[],
            elapsed_ms=1.0,
            diagnostics=diagnostics,
        )
        return SearchResponse(
            answer="該当する文書は見つかりませんでした。",
            citations=[],
            trace_id=trace_id,
            elapsed_ms=1.0,
            diagnostics=diagnostics,
        )


class CapturingFeedbackClient:
    """引用 feedback API テスト用の fake Oracle client。"""

    def __init__(self) -> None:
        self.saved_payloads: list[dict[str, object]] = []

    async def save_citation_feedback(self, payload: dict[str, object]) -> str:
        self.saved_payloads.append(payload)
        return "feedback-1"
