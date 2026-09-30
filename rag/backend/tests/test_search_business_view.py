"""検索 API の Business View 解決テスト。

Business View 指定時に参照 KB 群を検索対象へ展開し、その回答の設定を
検索 runtime / diagnostics へ反映することを検証する。
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from pytest import MonkeyPatch

from app.api.routes import search as search_route
from app.config import Settings
from app.main import app
from app.rag import extraction_field_adapter as fields_mod
from app.rag.business_view_config import BusinessViewConfig
from app.rag.diagnostics import build_search_diagnostics
from app.rag.extraction_field_adapter import FieldDefinition
from app.rag.kb_adapter_config import KnowledgeBaseAdapterConfig, KnowledgeBaseQueryConfig
from app.schemas.business_view import BusinessViewDetail, BusinessViewStatus
from app.schemas.knowledge_base import KnowledgeBaseDetail, KnowledgeBaseStatus
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
                request, settings=settings, retrieval_strategy_adapter="grounded"
            ),
        )


class FakeViewOracle:
    """業務ビューを返すテスト用 Oracle。"""

    def __init__(self, views: dict[str, BusinessViewConfig]) -> None:
        self._views = views

    async def get_business_view_knowledge(
        self, business_view_id: str, kind: str
    ) -> dict[str, object] | None:
        return None

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
    monkeypatch.setattr(
        search_route,
        "OracleClient",
        lambda *_args, **_kwargs: FakeViewOracle(views),
    )


def test_business_view_expands_kbs_and_applies_query_config(monkeypatch: MonkeyPatch) -> None:
    """参照 KB 群が検索対象へ展開され、query 設定が pipeline と diagnostics に効く。"""
    config = BusinessViewConfig(
        knowledge_base_ids=["kb-1", "kb-2"],
        query=KnowledgeBaseQueryConfig(
            query_strategy="rag_fusion",
            answer_flow="standard_rag",
        ),
    )
    _install(monkeypatch, {"bv-1": config})

    response = client.post(
        "/api/search",
        json={"query": "経費精算の上限", "business_view_id": "bv-1"},
    )

    assert response.status_code == 200
    diagnostics = response.json()["data"]["diagnostics"]
    assert RecordingPipeline.captured_settings is not None
    assert RecordingPipeline.captured_settings.rag_query_strategy == "rag_fusion"
    assert RecordingPipeline.captured_settings.rag_answer_flow == "standard_rag"
    assert diagnostics["business_view_applied"] == "bv-1"
    # 参照 KB が検索対象へ展開されている。
    assert RecordingPipeline.captured_request is not None
    assert RecordingPipeline.captured_request.knowledge_base_ids == ["kb-1", "kb-2"]
    assert RecordingPipeline.captured_request.filters["knowledge_base_id"] == "kb-1,kb-2"


def test_multiple_business_views_expand_union_and_use_first_config(
    monkeypatch: MonkeyPatch,
) -> None:
    """複数業務ビューでは参照 KB を union し、query 設定は先頭 View を代表にする。"""
    first = BusinessViewConfig(
        knowledge_base_ids=["kb-1", "kb-2"],
        query=KnowledgeBaseQueryConfig(query_strategy="rag_fusion"),
    )
    second = BusinessViewConfig(
        knowledge_base_ids=["kb-2", "kb-3"],
        query=KnowledgeBaseQueryConfig(query_strategy="hyde"),
    )
    _install(monkeypatch, {"bv-1": first, "bv-2": second})

    response = client.post(
        "/api/search",
        json={"query": "経費精算の上限", "business_view_ids": ["bv-1", "bv-2"]},
    )

    assert response.status_code == 200
    diagnostics = response.json()["data"]["diagnostics"]
    assert RecordingPipeline.captured_settings is not None
    assert RecordingPipeline.captured_settings.rag_query_strategy == "rag_fusion"
    assert diagnostics["business_view_applied"] == "bv-1,bv-2"
    assert RecordingPipeline.captured_request is not None
    assert RecordingPipeline.captured_request.knowledge_base_ids == ["kb-1", "kb-2", "kb-3"]
    assert RecordingPipeline.captured_request.filters["knowledge_base_id"] == "kb-1,kb-2,kb-3"


def test_saved_standard_options_of_business_view_are_ignored(monkeypatch: MonkeyPatch) -> None:
    """保存済みの旧 standard の値(回答スタイル・persona など。#595 で削除)は読み捨てて検索する。"""
    config = BusinessViewConfig.model_validate(
        {
            "knowledge_base_ids": ["kb-1"],
            "system_prompt": "あなたは経理規程アシスタントです。",
            "default_language": "en",
            "query": {
                "retrieval_strategy": "keyword",
                "generation_profile": "structured_json",
                "post_retrieval_pipeline": "lean",
                "rerank_enabled": False,
            },
        }
    )
    _install(monkeypatch, {"bv-1": config})

    response = client.post(
        "/api/search",
        json={"query": "上限額", "business_view_id": "bv-1"},
    )

    assert response.status_code == 200
    settings = RecordingPipeline.captured_settings
    assert settings is not None
    assert settings.rag_rerank_enabled is False
    for removed in ("rag_generation_profile", "rag_generation_system_prompt_override"):
        assert not hasattr(settings, removed)


def test_business_view_guardrail_policy_reaches_pipeline_settings(
    monkeypatch: MonkeyPatch,
) -> None:
    """業務ビューの regulated 設定が実 pipeline 構築に使う Settings へ届く。"""
    config = BusinessViewConfig(
        knowledge_base_ids=["kb-1"],
        query=KnowledgeBaseQueryConfig(guardrail_policy="regulated"),
    )
    _install(monkeypatch, {"bv-1": config})

    response = client.post(
        "/api/search",
        json={"query": "上限額", "business_view_id": "bv-1"},
    )

    assert response.status_code == 200
    assert RecordingPipeline.captured_settings is not None
    assert RecordingPipeline.captured_settings.rag_guardrail_policy == "regulated"


def test_request_kb_ids_take_precedence_over_view(monkeypatch: MonkeyPatch) -> None:
    """request 明示の KB は業務ビューの参照 KB より優先する。"""
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


@pytest.mark.parametrize("path", ["/api/search", "/api/search/stream"])
def test_business_view_without_knowledge_bases_is_rejected(
    monkeypatch: MonkeyPatch, path: str
) -> None:
    """参照 KB が 0 件の業務ビューでは利用者の全 KB を検索せず、理由を 409 で返す（#304）。"""
    _install(
        monkeypatch,
        {"bv-empty": BusinessViewConfig(), "bv-empty-2": BusinessViewConfig()},
    )

    response = client.post(
        path, json={"query": "上限額", "business_view_ids": ["bv-empty", "bv-empty-2"]}
    )

    assert response.status_code == 409
    assert response.json()["error_messages"] == [
        search_route.BUSINESS_VIEW_NO_KNOWLEDGE_BASES_MESSAGE
    ]
    assert RecordingPipeline.captured_request is None


def test_empty_business_view_is_searched_with_other_views_knowledge_bases(
    monkeypatch: MonkeyPatch,
) -> None:
    """KB のない業務ビューを、KB のある業務ビューと一緒に選んだときは、その KB だけを検索する。"""
    _install(
        monkeypatch,
        {"bv-empty": BusinessViewConfig(), "bv-1": BusinessViewConfig(knowledge_base_ids=["kb-1"])},
    )

    response = client.post(
        "/api/search", json={"query": "上限額", "business_view_ids": ["bv-empty", "bv-1"]}
    )

    assert response.status_code == 200
    assert RecordingPipeline.captured_request is not None
    assert RecordingPipeline.captured_request.knowledge_base_ids == ["kb-1"]


def test_missing_business_view_is_rejected(monkeypatch: MonkeyPatch) -> None:
    """明示した業務ビューが無い場合は別 scope へ縮退せず 404 にする。"""
    _install(monkeypatch, {})

    response = client.post(
        "/api/search",
        json={"query": "上限額", "business_view_id": "missing"},
    )

    assert response.status_code == 404
    assert response.json()["error_messages"] == ["指定した業務ビューが見つかりません: missing"]


def test_archived_business_view_is_rejected(monkeypatch: MonkeyPatch) -> None:
    class ArchivedOracle(FakeViewOracle):
        async def get_business_view(self, business_view_id: str) -> BusinessViewDetail | None:
            detail = await super().get_business_view(business_view_id)
            return (
                detail.model_copy(update={"status": BusinessViewStatus.ARCHIVED})
                if detail
                else None
            )

    monkeypatch.setattr(search_route, "RagPipeline", RecordingPipeline)
    monkeypatch.setattr(
        search_route,
        "OracleClient",
        lambda *_args, **_kwargs: ArchivedOracle({"bv-1": BusinessViewConfig()}),
    )

    response = client.post(
        "/api/search",
        json={"query": "上限額", "business_view_id": "bv-1"},
    )

    assert response.status_code == 409
    assert "アーカイブ済み" in response.json()["error_messages"][0]


def test_business_view_serving_mode_flows_to_settings_and_diagnostics(
    monkeypatch: MonkeyPatch,
) -> None:
    """業務ビューの serving_mode=fused が pipeline settings へ流れる。"""
    config = BusinessViewConfig(knowledge_base_ids=["kb-1"], serving_mode="fused")
    _install(monkeypatch, {"bv-1": config})

    response = client.post(
        "/api/search",
        json={"query": "上限額", "business_view_id": "bv-1"},
    )

    assert response.status_code == 200
    settings = RecordingPipeline.captured_settings
    assert settings is not None
    assert settings.rag_serving_mode == "fused"
    diagnostics = response.json()["data"]["diagnostics"]
    assert diagnostics["business_view_applied"] == "bv-1"


class FakeViewAndKbOracle:
    """Business View と、その参照 KB(legacy query 付き)を返すテスト用 Oracle。"""

    def __init__(
        self,
        views: dict[str, BusinessViewConfig],
        kb_configs: dict[str, KnowledgeBaseAdapterConfig],
    ) -> None:
        self._views = views
        self._kb_configs = kb_configs

    async def get_business_view_knowledge(
        self, business_view_id: str, kind: str
    ) -> dict[str, object] | None:
        return None

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

    async def get_knowledge_base(self, knowledge_base_id: str) -> KnowledgeBaseDetail | None:
        config = self._kb_configs.get(knowledge_base_id)
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


def test_business_view_ignores_single_kb_legacy_query(monkeypatch: MonkeyPatch) -> None:
    """Business View は単一 KB に解決しても KB legacy query を下層に重ねない。"""
    kb_config = KnowledgeBaseAdapterConfig.model_validate(
        {"query": {"vector_index_profile": "fast", "neighbor_child_count": 9}}
    )
    view_config = BusinessViewConfig(
        knowledge_base_ids=["kb-1"],
        query=KnowledgeBaseQueryConfig(answer_flow="standard_rag"),
    )
    monkeypatch.setattr(search_route, "RagPipeline", RecordingPipeline)
    monkeypatch.setattr(
        search_route,
        "OracleClient",
        lambda *_args, **_kwargs: FakeViewAndKbOracle(
            {"bv-1": view_config},
            {"kb-1": kb_config},
        ),
    )

    response = client.post(
        "/api/search",
        json={"query": "上限額", "business_view_id": "bv-1"},
    )

    assert response.status_code == 200
    settings = RecordingPipeline.captured_settings
    assert settings is not None
    # Business View が設定した回答の設定は Business View 値が効く。
    assert settings.rag_answer_flow == "standard_rag"
    # Business View が触れていない項目は KB legacy 値ではなく global 既定。
    assert settings.rag_neighbor_child_count == 3
    assert settings.rag_vector_index_profile == "accurate"
    diagnostics = response.json()["data"]["diagnostics"]
    assert diagnostics["business_view_applied"] == "bv-1"
    assert diagnostics["kb_adapter_config_applied"] is None


class FakeFieldSetOracle(FakeViewOracle):
    """業務ビューと、KB ごとの項目抽出の定義(#549)を返すテスト用 Oracle。"""

    def __init__(
        self,
        views: dict[str, BusinessViewConfig],
        field_sets: dict[str, list[FieldDefinition] | None],
    ) -> None:
        super().__init__(views)
        self._field_sets = field_sets
        self.requested_kb_ids: list[str] = []

    async def list_knowledge_base_extraction_field_sets(
        self, knowledge_base_ids: list[str]
    ) -> list[list[FieldDefinition] | None]:
        self.requested_kb_ids = list(knowledge_base_ids)
        return [
            self._field_sets[kb_id] for kb_id in knowledge_base_ids if kb_id in self._field_sets
        ]


def test_search_extraction_fields_unions_business_view_knowledge_bases(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    """検索の絞り込みの項目は、選んだ業務ビューの KB の定義(無ければ既定)の和集合(#549)。"""
    monkeypatch.setenv(fields_mod.FIELD_SCHEMA_FILE_ENV, str(tmp_path / "fields.json"))
    fields_mod.save_field_schema([FieldDefinition(name="請求書番号")])
    oracle = FakeFieldSetOracle(
        {
            "bv-1": BusinessViewConfig(knowledge_base_ids=["kb-contract"]),
            "bv-2": BusinessViewConfig(knowledge_base_ids=["kb-contract", "kb-default"]),
        },
        {
            "kb-contract": [
                FieldDefinition(name="契約日", value_type="date"),
                FieldDefinition(name="金額", value_type="number"),
            ],
            "kb-default": None,
        },
    )
    monkeypatch.setattr(search_route, "OracleClient", lambda *_args, **_kwargs: oracle)

    response = client.get("/api/search/extraction-fields?business_view_ids=bv-1,bv-2")

    assert response.status_code == 200
    assert oracle.requested_kb_ids == ["kb-contract", "kb-default"]
    assert [
        (field["name"], field["value_type"]) for field in response.json()["data"]["fields"]
    ] == [("契約日", "date"), ("金額", "number"), ("請求書番号", "string")]


def test_search_extraction_fields_without_knowledge_bases_and_missing_view(
    monkeypatch: MonkeyPatch,
) -> None:
    oracle = FakeFieldSetOracle({"bv-empty": BusinessViewConfig()}, {})
    monkeypatch.setattr(search_route, "OracleClient", lambda *_args, **_kwargs: oracle)

    empty = client.get("/api/search/extraction-fields?business_view_ids=bv-empty")
    assert empty.status_code == 200
    assert empty.json()["data"]["fields"] == []
    missing = client.get("/api/search/extraction-fields?business_view_ids=bv-missing")
    assert missing.status_code == 404
