"""検索 API の HTTP 境界テスト。"""

import asyncio
import logging
from typing import Any, cast

from pytest import LogCaptureFixture, MonkeyPatch

from app.api.routes import search as search_route
from app.config import get_settings
from app.main import app
from app.schemas.search import SearchRequest, SearchResponse
from tests.support import AsgiTestClient

client = AsgiTestClient(app)


def test_search_api_returns_504_when_pipeline_times_out(
    monkeypatch: MonkeyPatch,
    caplog: LogCaptureFixture,
) -> None:
    """通常検索は pipeline timeout を ApiResponse 形式の 504 にして監査へ残す。"""
    _force_search_timeout(monkeypatch)

    with caplog.at_level(logging.INFO, logger="app.audit"):
        response = client.post("/api/search", json={"query": "INV-SECRET の請求金額"})

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


def test_stream_search_api_returns_504_when_pipeline_times_out(monkeypatch: MonkeyPatch) -> None:
    """SSE 検索も pipeline timeout 時は stream 開始前に 504 を返す。"""
    _force_search_timeout(monkeypatch)

    response = client.post("/api/search/stream", json={"query": "請求金額"})

    assert response.status_code == 504
    body = response.json()
    assert body["data"] is None
    assert body["error_messages"] == [search_route.SEARCH_TIMEOUT_MESSAGE]


def test_search_api_hashes_tenant_and_user_headers_into_audit(
    caplog: LogCaptureFixture,
) -> None:
    """HTTP header の tenant/user id は hash として RAG 監査へ相関される。"""
    with caplog.at_level(logging.INFO, logger="app.audit"):
        response = client.post(
            "/api/search",
            json={"query": "存在しない請求書"},
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


def _force_search_timeout(monkeypatch: MonkeyPatch) -> None:
    """検索 route を低 timeout + 遅い pipeline に差し替える。"""
    settings = get_settings()
    monkeypatch.setattr(settings, "rag_search_timeout_seconds", 0.001)
    monkeypatch.setattr(search_route, "RagPipeline", SlowPipeline)


class SlowPipeline:
    """timeout を再現するテスト用 pipeline。"""

    async def run(
        self,
        _request: SearchRequest,
        trace_id: str | None = None,
    ) -> SearchResponse:
        assert trace_id
        await asyncio.sleep(1)
        raise AssertionError("timeout 前に完了しない")
