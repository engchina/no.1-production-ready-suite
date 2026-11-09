"""一覧の「作り直しが必要」の印と、文書詳細の /chunk-sets の判定が一致する（#4185）。

レシピを持たない配信中の chunk_set は、詳細では既定のレシピの設定と KB の計画で層を判定する。
一覧も同じ規則で判定し、文書の行の旧列（processing_config）を読まない。
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.api.routes import documents as documents_route
from app.api.routes.documents import _mark_layers_rebuild_required
from app.config import Settings
from app.rag.extraction_field_adapter import FIELD_SCHEMA_FILE_ENV, FieldDefinition
from app.rag.kb_adapter_config import KnowledgeBaseAdapterConfig
from app.rag.variant_keys import compute_chunk_set_id, compute_metadata_layer_id
from app.schemas.document import DocumentSummary, FileStatus

DOCUMENT_ID = "doc-a"
CONTENT_SHA256 = "a" * 64
DEFAULT_RECIPE_ID = "recipe-a-1"


def _global_settings() -> Settings:
    """全体の既定では派生層を使わない(レシピの上書きで使う)。"""
    return Settings(
        rag_graph_profile="off",
        rag_field_extraction_enabled=False,
        rag_navigation_summary_enabled=False,
    )


class _LegacyServingOracle:
    """レシピを持たない配信中の chunk_set を 1 つ持つ文書の fake(一覧と詳細の両方)。"""

    def __init__(
        self,
        *,
        default_recipe_config: dict[str, object],
        document_processing_config: dict[str, object],
        chunk_set_id: str,
        layer_id: str,
    ) -> None:
        self.default_recipe_config = default_recipe_config
        self.document_processing_config = document_processing_config
        self.chunk_set_id = chunk_set_id
        self.layer_id = layer_id
        self.fingerprint = {"field_schema_hash": "older-definitions"}

    # --- 一覧 ---
    async def list_serving_artifact_layer_fingerprints(
        self, document_ids: Collection[str]
    ) -> list[dict[str, object]]:
        assert list(document_ids) == [DOCUMENT_ID]
        return [
            {
                "document_id": DOCUMENT_ID,
                "layer_id": self.layer_id,
                "layer_kind": "metadata",
                "parent_chunk_set_id": self.chunk_set_id,
                "recipe_id": None,
                "recipe_processing_config": None,
                # 変更前の一覧が読んでいた文書の行の旧列(変更後の一覧は返さない)。
                "document_processing_config": self.document_processing_config,
                "input_fingerprint": self.fingerprint,
            }
        ]

    async def list_documents_extraction_field_sets(
        self, document_ids: Collection[str]
    ) -> dict[str, list[list[FieldDefinition] | None]]:
        return {document_id: [None] for document_id in document_ids}

    # --- 詳細(/chunk-sets) ---
    async def get_document_summary(self, document_id: str) -> DocumentSummary | None:
        return _summary() if document_id == DOCUMENT_ID else None

    async def list_document_chunk_sets(self, document_id: str) -> list[dict[str, object]]:
        return [
            {
                "chunk_set_id": self.chunk_set_id,
                "recipe_id": None,
                "status": "INDEXED",
                "is_serving": True,
            }
        ]

    async def list_document_knowledge_base_configs(
        self, document_id: str
    ) -> list[tuple[str, KnowledgeBaseAdapterConfig]]:
        return [("kb-a", KnowledgeBaseAdapterConfig())]

    async def ensure_default_document_recipe(self, document_id: str) -> dict[str, object]:
        return {"recipe_id": DEFAULT_RECIPE_ID, "slot_no": 1}

    async def get_document_recipe(
        self, document_id: str, recipe_id: str
    ) -> dict[str, object] | None:
        if recipe_id != DEFAULT_RECIPE_ID:
            return None
        return {
            "recipe_id": DEFAULT_RECIPE_ID,
            "slot_no": 1,
            "processing_config": self.default_recipe_config,
        }

    async def list_artifact_layers_for_chunk_sets(
        self, chunk_set_ids: Sequence[str]
    ) -> dict[str, dict[str, object]]:
        return {
            self.layer_id: {
                "layer_id": self.layer_id,
                "layer_kind": "metadata",
                "parent_chunk_set_id": self.chunk_set_id,
                "requested": True,
                "status": "materialized",
                "reason": None,
                "input_fingerprint": self.fingerprint,
            }
        }

    async def list_document_extraction_field_sets(
        self, document_id: str
    ) -> list[list[FieldDefinition] | None]:
        return [None]

    async def graph_entity_counts_for_chunk_sets(
        self, chunk_set_ids: Sequence[str]
    ) -> dict[str, int]:
        return {}


def _summary() -> DocumentSummary:
    return DocumentSummary(
        id=DOCUMENT_ID,
        file_name="a.pdf",
        status=FileStatus.INDEXED,
        uploaded_at=datetime(2026, 1, 1, tzinfo=UTC),
        content_sha256=CONTENT_SHA256,
    )


@pytest.mark.parametrize(
    ("default_recipe_config", "document_processing_config"),
    [
        # 既定のレシピは項目抽出を使う。文書の行の旧列は空(開発の ADB の旧列はすべて NULL)。
        ({"field_extraction_enabled": True}, {}),
        # 既定のレシピは項目抽出を使わない。文書の行の旧列には使う設定が残っている。
        ({"field_extraction_enabled": False}, {"field_extraction_enabled": True}),
    ],
    ids=["recipe_requests_layer", "only_document_column_requests_layer"],
)
async def test_list_flag_matches_detail_for_serving_chunk_set_without_recipe(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    default_recipe_config: dict[str, object],
    document_processing_config: dict[str, object],
) -> None:
    monkeypatch.setenv(FIELD_SCHEMA_FILE_ENV, str(tmp_path / "extraction-fields.json"))
    global_settings = _global_settings()
    monkeypatch.setattr(documents_route, "get_settings", lambda: global_settings)
    # 層を作ったとき(作り直しの判定の対象)の設定: 項目抽出を使う設定の計画の chunk_set と層。
    layer_settings = global_settings.model_copy(update={"rag_field_extraction_enabled": True})
    chunk_set_id = compute_chunk_set_id(CONTENT_SHA256, layer_settings)
    oracle = _LegacyServingOracle(
        default_recipe_config=default_recipe_config,
        document_processing_config=document_processing_config,
        chunk_set_id=chunk_set_id,
        layer_id=compute_metadata_layer_id(chunk_set_id, layer_settings),
    )
    monkeypatch.setattr(documents_route, "OracleClient", lambda: oracle)

    detail = await documents_route.list_document_chunk_sets(DOCUMENT_ID)
    documents = [_summary()]
    await _mark_layers_rebuild_required(oracle, documents, global_settings)  # type: ignore[arg-type]

    assert detail.data is not None
    detail_flag = any(
        status.rebuild_required
        for chunk_set in detail.data
        for status in (
            chunk_set.layer_statuses.metadata,
            chunk_set.layer_statuses.graph,
            chunk_set.layer_statuses.navigation,
        )
    )
    assert documents[0].layers_rebuild_required is detail_flag
    # 判定の正本は既定のレシピの設定: 項目抽出を使うときだけ作り直しの対象になる。
    assert detail_flag is bool(default_recipe_config["field_extraction_enabled"])
