"""検索 API の KB 単位 query 上書き(クエリ時 overlay)テスト。"""

from datetime import UTC, datetime

import pytest
from pytest import MonkeyPatch

from app.api.routes import search as search_route
from app.config import Settings
from app.main import app
from app.rag.diagnostics import build_search_diagnostics
from app.rag.kb_adapter_config import KnowledgeBaseAdapterConfig
from app.schemas.knowledge_base import KnowledgeBaseDetail, KnowledgeBaseStatus
from app.schemas.search import SearchRequest, SearchResponse
from tests.support import AsgiTestClient

client = AsgiTestClient(app)


class RecordingPipeline:
    """構築時に渡された settings を診断へ反映するテスト用 pipeline。"""

    captured_settings: Settings | None = None

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
        assert trace_id
        # 実 pipeline は adapter を self._settings から解決して diagnostics へ渡す。
        settings = self._settings
        return SearchResponse(
            answer="ok",
            citations=[],
            trace_id=trace_id,
            elapsed_ms=1.0,
            diagnostics=build_search_diagnostics(
                request,
                settings=settings,
                generation_profile=(settings.rag_generation_profile if settings else None),
                vector_index_profile=(settings.rag_vector_index_profile if settings else None),
            ),
        )


class FakeSearchOracle:
    """KB query 上書きを返すテスト用 Oracle。"""

    def __init__(self, configs: dict[str, KnowledgeBaseAdapterConfig]) -> None:
        self._configs = configs

    async def get_knowledge_base(self, knowledge_base_id: str) -> KnowledgeBaseDetail | None:
        config = self._configs.get(knowledge_base_id)
        if config is None:
            return None
        return KnowledgeBaseDetail(
            id=knowledge_base_id,
            name=f"KB {knowledge_base_id}",
            status=KnowledgeBaseStatus.ACTIVE,
            adapter_config=config,
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
            updated_at=datetime(2026, 1, 1, tzinfo=UTC),
        )


@pytest.fixture(autouse=True)
def _reset_recording() -> None:
    RecordingPipeline.captured_settings = None


def _install(monkeypatch: MonkeyPatch, configs: dict[str, KnowledgeBaseAdapterConfig]) -> None:
    monkeypatch.setattr(search_route, "RagPipeline", RecordingPipeline)
    monkeypatch.setattr(search_route, "OracleClient", lambda: FakeSearchOracle(configs))


def test_single_kb_query_overrides_apply_to_pipeline(monkeypatch: MonkeyPatch) -> None:
    """単一 KB 指定時、その KB の query 上書きが pipeline と diagnostics に効く。"""
    config = KnowledgeBaseAdapterConfig.model_validate(
        {"query": {"generation_profile": "detailed_cited", "vector_index_profile": "accurate"}}
    )
    _install(monkeypatch, {"kb-1": config})

    response = client.post(
        "/api/search",
        json={"query": "承認条件", "knowledge_base_ids": ["kb-1"]},
    )

    assert response.status_code == 200
    diagnostics = response.json()["data"]["diagnostics"]
    assert diagnostics["generation_profile"] == "detailed_cited"
    assert diagnostics["vector_index_profile"] == "accurate"
    assert diagnostics["kb_adapter_config_applied"] == "kb-1"
    # pipeline へ overlay 済み settings が渡っている。
    assert RecordingPipeline.captured_settings is not None
    assert RecordingPipeline.captured_settings.rag_generation_profile == "detailed_cited"


def test_multiple_kb_ids_use_global_defaults(monkeypatch: MonkeyPatch) -> None:
    """複数 KB 指定は設定競合を避けてグローバル既定を使う。"""
    config = KnowledgeBaseAdapterConfig.model_validate(
        {"query": {"generation_profile": "detailed_cited"}}
    )
    _install(monkeypatch, {"kb-1": config, "kb-2": config})

    response = client.post(
        "/api/search",
        json={"query": "承認条件", "knowledge_base_ids": ["kb-1", "kb-2"]},
    )

    assert response.status_code == 200
    diagnostics = response.json()["data"]["diagnostics"]
    assert diagnostics["generation_profile"] == "grounded_concise"
    assert diagnostics["kb_adapter_config_applied"] is None


def test_empty_kb_config_does_not_mark_applied(monkeypatch: MonkeyPatch) -> None:
    """KB に query 上書きが無ければ applied は立たない。"""
    _install(monkeypatch, {"kb-1": KnowledgeBaseAdapterConfig()})

    response = client.post(
        "/api/search",
        json={"query": "承認条件", "knowledge_base_ids": ["kb-1"]},
    )

    assert response.status_code == 200
    diagnostics = response.json()["data"]["diagnostics"]
    assert diagnostics["generation_profile"] == "grounded_concise"
    assert diagnostics["kb_adapter_config_applied"] is None
