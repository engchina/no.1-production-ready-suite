"""検索 API の業務アシスタント(Business View)解決テスト。

業務アシスタント指定時に参照 KB 群を検索対象へ展開し、その query 設定・persona を
pipeline / diagnostics へ反映することを検証する。
"""

from datetime import UTC, datetime

import pytest
from pytest import MonkeyPatch

from app.api.routes import search as search_route
from app.config import Settings
from app.main import app
from app.rag.business_view_config import BusinessViewConfig
from app.rag.diagnostics import build_search_diagnostics
from app.rag.kb_adapter_config import KnowledgeBaseQueryConfig
from app.schemas.business_view import BusinessViewDetail, BusinessViewStatus
from app.schemas.search import SearchRequest, SearchResponse
from tests.support import AsgiTestClient

client = AsgiTestClient(app)


class RecordingPipeline:
    """構築時 settings と実行時 request を捕捉するテスト用 pipeline。"""

    captured_settings: Settings | None = None
    captured_request: SearchRequest | None = None

    def __init__(self, *, settings: Settings | None = None, **_kwargs: object) -> None:
        RecordingPipeline.captured_settings = settings
        self._settings = settings

    async def run(
        self,
        request: SearchRequest,
        trace_id: str | None = None,
        progress_callback: object | None = None,
        token_callback: object | None = None,
    ) -> SearchResponse:
        _ = progress_callback, token_callback
        RecordingPipeline.captured_request = request
        settings = self._settings
        assert trace_id
        return SearchResponse(
            answer="ok",
            citations=[],
            trace_id=trace_id,
            elapsed_ms=1.0,
            diagnostics=build_search_diagnostics(
                request,
                settings=settings,
                generation_profile=(settings.rag_generation_profile if settings else None),
            ),
        )


class FakeViewOracle:
    """業務アシスタントを返すテスト用 Oracle。"""

    def __init__(self, views: dict[str, BusinessViewConfig]) -> None:
        self._views = views

    async def get_business_view(self, business_view_id: str) -> BusinessViewDetail | None:
        config = self._views.get(business_view_id)
        if config is None:
            return None
        return BusinessViewDetail(
            id=business_view_id,
            name=f"view {business_view_id}",
            status=BusinessViewStatus.ACTIVE,
            config=config,
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
            updated_at=datetime(2026, 1, 1, tzinfo=UTC),
        )

    async def get_knowledge_base(self, knowledge_base_id: str) -> None:
        return None


@pytest.fixture(autouse=True)
def _reset() -> None:
    RecordingPipeline.captured_settings = None
    RecordingPipeline.captured_request = None


def _install(monkeypatch: MonkeyPatch, views: dict[str, BusinessViewConfig]) -> None:
    monkeypatch.setattr(search_route, "RagPipeline", RecordingPipeline)
    monkeypatch.setattr(search_route, "OracleClient", lambda: FakeViewOracle(views))


def test_business_view_expands_kbs_and_applies_query_config(monkeypatch: MonkeyPatch) -> None:
    """参照 KB 群が検索対象へ展開され、query 設定が pipeline と diagnostics に効く。"""
    config = BusinessViewConfig(
        knowledge_base_ids=["kb-1", "kb-2"],
        query=KnowledgeBaseQueryConfig(generation_profile="detailed_cited"),
    )
    _install(monkeypatch, {"bv-1": config})

    response = client.post(
        "/api/search",
        json={"query": "経費精算の上限", "business_view_id": "bv-1"},
    )

    assert response.status_code == 200
    diagnostics = response.json()["data"]["diagnostics"]
    assert diagnostics["generation_profile"] == "detailed_cited"
    assert diagnostics["business_view_applied"] == "bv-1"
    # 参照 KB が検索対象へ展開されている。
    assert RecordingPipeline.captured_request is not None
    assert RecordingPipeline.captured_request.knowledge_base_ids == ["kb-1", "kb-2"]
    assert RecordingPipeline.captured_request.filters["knowledge_base_id"] == "kb-1,kb-2"


def test_business_view_persona_overrides_system_prompt(monkeypatch: MonkeyPatch) -> None:
    """persona は generation system prompt 上書きとして pipeline settings へ渡る。"""
    config = BusinessViewConfig(
        knowledge_base_ids=["kb-1"],
        system_prompt="あなたは経理規程アシスタントです。",
    )
    _install(monkeypatch, {"bv-1": config})

    response = client.post(
        "/api/search",
        json={"query": "上限額", "business_view_id": "bv-1"},
    )

    assert response.status_code == 200
    settings = RecordingPipeline.captured_settings
    assert settings is not None
    assert settings.rag_generation_system_prompt_override is not None
    assert "経理規程アシスタント" in settings.rag_generation_system_prompt_override


def test_request_kb_ids_take_precedence_over_view(monkeypatch: MonkeyPatch) -> None:
    """request 明示の KB は業務アシスタントの参照 KB より優先する。"""
    config = BusinessViewConfig(knowledge_base_ids=["kb-1", "kb-2"])
    _install(monkeypatch, {"bv-1": config})

    response = client.post(
        "/api/search",
        json={
            "query": "上限額",
            "business_view_id": "bv-1",
            "knowledge_base_ids": ["kb-9"],
        },
    )

    assert response.status_code == 200
    assert RecordingPipeline.captured_request is not None
    assert RecordingPipeline.captured_request.knowledge_base_ids == ["kb-9"]


def test_missing_business_view_falls_back_to_global(monkeypatch: MonkeyPatch) -> None:
    """存在しない業務アシスタント ID はグローバル既定へ安全縮退する。"""
    _install(monkeypatch, {})

    response = client.post(
        "/api/search",
        json={"query": "上限額", "business_view_id": "missing"},
    )

    assert response.status_code == 200
    diagnostics = response.json()["data"]["diagnostics"]
    assert diagnostics["business_view_applied"] is None
    assert diagnostics["generation_profile"] == "grounded_concise"
