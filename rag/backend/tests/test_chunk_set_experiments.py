"""レシピ実験エンドポイント(候補 materialize / 昇格)のオーケストレーション単体テスト。

実 Oracle / AI を使わず、fake で「候補は配信に載せない(現 serving 再アサートで demote)」
「昇格は serving 切替 + 敗者 GC」の呼び出し順序を固定する。SQL 述語自体は
test_search_filters.py(chunk_set_id フィルタ)で検証する。
"""

from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.api.routes import documents
from app.api.routes.documents import (
    _candidate_chunking_settings,
    create_chunk_set_experiment,
    promote_chunk_set_experiment,
)
from app.config import get_settings
from app.schemas.document import (
    ChunkSetExperimentRequest,
    DocumentDetail,
    DocumentProcessingConfig,
    FileStatus,
)
from app.schemas.extraction import StructuredExtraction

_SERVING_ID = "cs_serving_existing0"


def _detail(status: FileStatus = FileStatus.INDEXED) -> DocumentDetail:
    return DocumentDetail(
        id="doc-1",
        file_name="policy.txt",
        status=status,
        content_type="text/plain",
        file_size_bytes=24,
        content_sha256="a" * 64,
        uploaded_at=datetime.now(UTC),
        indexed_at=datetime.now(UTC) if status == FileStatus.INDEXED else None,
    )


class FakeExperimentOracle:
    """実験エンドポイントが触る Oracle subset の fake。呼び出しを記録する。"""

    def __init__(self, *, detail: DocumentDetail | None = None) -> None:
        self.detail = detail if detail is not None else _detail()
        self.serving_calls: list[str] = []
        self.deleted_keep: list[list[str]] = []
        self.indexed_marks: list[str] = []
        self.activated: list[tuple[str, str]] = []
        self.chunk_sets: dict[str, dict[str, object]] = {
            _SERVING_ID: {"chunk_set_id": _SERVING_ID, "status": "INDEXED"}
        }
        self.processing_config = DocumentProcessingConfig()
        self.recipes = [
            {
                "recipe_id": "recipe-1",
                "document_id": "doc-1",
                "slot_no": 1,
                "config_revision": 1,
                "active_extraction_recipe_id": "er-shared",
            }
        ]
        self.extraction_artifacts: dict[str, dict[str, object]] = {
            "er-shared": {
                "extraction_json": StructuredExtraction(
                    raw_text="共有抽出本文"
                ).to_document_payload(),
                "recipe_subset": {},
                "status": "materialized",
            }
        }

    async def get_document(self, document_id: str) -> DocumentDetail | None:
        return self.detail if document_id == self.detail.id else None

    async def get_document_serving_chunk_set_id(self, document_id: str) -> str | None:
        _ = document_id
        return _SERVING_ID

    async def count_chunk_set_chunks(self, chunk_set_id: str) -> int:
        _ = chunk_set_id
        return 5

    async def list_document_recipes(self, document_id: str) -> list[dict[str, object]]:
        _ = document_id
        return self.recipes

    async def create_document_recipe(
        self, document_id: str, *, copy_from_recipe_id: str | None = None
    ) -> dict[str, object]:
        _ = document_id, copy_from_recipe_id
        created: dict[str, object] = {
            "recipe_id": "recipe-2",
            "document_id": "doc-1",
            "slot_no": 2,
            "config_revision": 1,
        }
        self.recipes.append(created)
        return created

    async def update_document_recipe_config(
        self,
        document_id: str,
        recipe_id: str,
        config: DocumentProcessingConfig,
    ) -> dict[str, object]:
        _ = document_id
        self.processing_config = config
        row = next(recipe for recipe in self.recipes if recipe["recipe_id"] == recipe_id)
        row["config_revision"] = 2
        return row

    async def get_document_extraction_artifact(
        self, *, document_id: str, extraction_recipe_id: str
    ) -> dict[str, object] | None:
        _ = document_id
        return self.extraction_artifacts.get(extraction_recipe_id)

    async def upsert_document_extraction_artifact(
        self, *, extraction_recipe_id: str, extraction: dict[str, object], **kwargs: Any
    ) -> None:
        self.extraction_artifacts[extraction_recipe_id] = {
            "extraction_json": extraction,
            **kwargs,
        }

    async def update_document_recipe_status(
        self,
        *,
        recipe_id: str,
        active_extraction_recipe_id: str | None = None,
        **_kwargs: object,
    ) -> None:
        row = next(recipe for recipe in self.recipes if recipe["recipe_id"] == recipe_id)
        row["active_extraction_recipe_id"] = active_extraction_recipe_id

    async def activate_recipe_chunk_set(
        self, *, recipe_id: str, chunk_set_id: str, **_kwargs: object
    ) -> None:
        self.activated.append((recipe_id, chunk_set_id))

    async def upsert_chunk_set(self, *, chunk_set_id: str, **kwargs: Any) -> None:
        self.chunk_sets.setdefault(chunk_set_id, {"chunk_set_id": chunk_set_id})
        self.chunk_sets[chunk_set_id]["status"] = "INDEXED"
        self.chunk_sets[chunk_set_id]["recipe_subset"] = kwargs.get("recipe_subset")

    async def get_document_processing_config(self, document_id: str) -> DocumentProcessingConfig:
        _ = document_id
        return self.processing_config

    async def update_document_processing_config(
        self, document_id: str, config: DocumentProcessingConfig
    ) -> DocumentProcessingConfig:
        _ = document_id
        self.processing_config = config
        return config

    async def mark_chunk_set_indexed(self, *, chunk_set_id: str, **_kwargs: Any) -> None:
        self.indexed_marks.append(chunk_set_id)

    async def set_document_serving_chunk_set(self, *, document_id: str, chunk_set_id: str) -> None:
        _ = document_id
        self.serving_calls.append(chunk_set_id)

    async def list_document_chunk_set_ids(self, document_id: str) -> list[str]:
        _ = document_id
        return list(self.chunk_sets)

    async def get_chunk_set(self, chunk_set_id: str) -> dict[str, object] | None:
        return self.chunk_sets.get(chunk_set_id)

    async def delete_document_chunk_sets_except(
        self, *, document_id: str, keep_chunk_set_ids: list[str]
    ) -> list[str]:
        _ = document_id
        keep = set(keep_chunk_set_ids)
        self.deleted_keep.append(list(keep_chunk_set_ids))
        removed = [cs for cs in self.chunk_sets if cs not in keep]
        for cs in removed:
            del self.chunk_sets[cs]
        return removed

    async def list_document_chunk_sets(self, document_id: str) -> list[dict[str, object]]:
        _ = document_id
        return [
            {
                "chunk_set_id": cs_id,
                "status": str(row.get("status") or "INDEXED"),
                "chunk_count": 5,
                "vector_count": 5,
                "knowledge_base_ids": ["kb-1"],
                "serving_knowledge_base_ids": (
                    ["kb-1"] if cs_id in self.serving_calls[-1:] else []
                ),
            }
            for cs_id, row in self.chunk_sets.items()
        ]


class FakeExperimentPipeline:
    """index_reviewed の呼び出し(chunk_set_id)を記録するだけの fake。"""

    last_chunk_set_id: str | None = None

    def __init__(
        self,
        *,
        oracle: object,
        settings: object,
        recipe_id: str | None = None,
        recipe_revision: int | None = None,
    ) -> None:
        self._oracle = oracle
        self._settings = settings
        self._recipe_id = recipe_id
        self._recipe_revision = recipe_revision

    async def index_reviewed(
        self, document_id: str, *, chunk_set_id: str, record_outcome: bool = True
    ) -> DocumentDetail:
        _ = document_id, record_outcome
        FakeExperimentPipeline.last_chunk_set_id = chunk_set_id
        return _detail()


def _patch(monkeypatch: pytest.MonkeyPatch, oracle: FakeExperimentOracle) -> None:
    monkeypatch.setattr(documents, "OracleClient", lambda: oracle)
    monkeypatch.setattr(documents, "IngestionPipeline", FakeExperimentPipeline)
    FakeExperimentPipeline.last_chunk_set_id = None


async def test_create_experiment_materializes_candidate_without_promoting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """旧実験作成は新しいレシピを作り、その active chunk_set として公開する。"""
    oracle = FakeExperimentOracle()
    _patch(monkeypatch, oracle)

    response = await create_chunk_set_experiment("doc-1", ChunkSetExperimentRequest(chunk_size=512))

    assert response.data is not None
    candidate_id = response.data.chunk_set_id
    # 候補 = serving とは別 chunk_set。
    assert candidate_id != _SERVING_ID
    # 候補レシピで re-chunk→index した(index_reviewed が候補 id で呼ばれた)。
    assert FakeExperimentPipeline.last_chunk_set_id == candidate_id
    assert oracle.indexed_marks == [candidate_id]
    assert oracle.activated == [("recipe-2", candidate_id)]
    assert oracle.serving_calls == []


async def test_create_experiment_rejects_non_indexed_document(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    oracle = FakeExperimentOracle(detail=_detail(status=FileStatus.REVIEW))
    _patch(monkeypatch, oracle)
    with pytest.raises(HTTPException) as exc:
        await create_chunk_set_experiment("doc-1", ChunkSetExperimentRequest(chunk_size=512))
    assert exc.value.status_code == 409


async def test_promote_switches_serving_and_gcs_losers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """全レシピ融合モードでは旧昇格 API は操作せず 409 を返す。"""
    oracle = FakeExperimentOracle()
    candidate_id = "cs_candidate_aaaaaa0"
    oracle.chunk_sets[candidate_id] = {
        "chunk_set_id": candidate_id,
        "status": "INDEXED",
        "recipe_subset": {
            "processing_config": {"chunking_strategy": "page_level", "chunk_size": 512},
        },
    }
    _patch(monkeypatch, oracle)

    with pytest.raises(HTTPException) as exc:
        await promote_chunk_set_experiment("doc-1", candidate_id)
    assert exc.value.status_code == 409
    assert oracle.serving_calls == []
    assert oracle.deleted_keep == []


async def test_promote_rejects_chunk_set_not_on_document(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    oracle = FakeExperimentOracle()
    _patch(monkeypatch, oracle)
    with pytest.raises(HTTPException) as exc:
        await promote_chunk_set_experiment("doc-1", "cs_not_here_00000000")
    assert exc.value.status_code == 409


def test_experiment_request_requires_at_least_one_override() -> None:
    with pytest.raises(ValidationError):
        ChunkSetExperimentRequest()


def test_candidate_settings_rejects_overlap_not_smaller_than_size() -> None:
    request = ChunkSetExperimentRequest(chunk_size=300, chunk_overlap=300)
    with pytest.raises(HTTPException) as exc:
        _candidate_chunking_settings(get_settings(), request.settings_overrides())
    assert exc.value.status_code == 422
