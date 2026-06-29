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
    _candidate_experiment_settings,
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
        self.chunk_sets: dict[str, dict[str, object]] = {
            _SERVING_ID: {"chunk_set_id": _SERVING_ID, "status": "INDEXED"}
        }
        self.processing_config = DocumentProcessingConfig()

    async def get_document(self, document_id: str) -> DocumentDetail | None:
        return self.detail if document_id == self.detail.id else None

    async def get_document_serving_chunk_set_id(self, document_id: str) -> str | None:
        _ = document_id
        return _SERVING_ID

    async def count_chunk_set_chunks(self, chunk_set_id: str) -> int:
        _ = chunk_set_id
        return 5

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

    def __init__(self, *, oracle: object, settings: object) -> None:
        self._oracle = oracle
        self._settings = settings

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
    """候補は別 chunk_set に materialize し、現 serving 再アサートで is_serving=0 に落とす。"""
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
    # 配信は候補に切り替えず、現 serving を再アサートして候補を demote する。
    assert oracle.serving_calls == [_SERVING_ID]


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
    """昇格は候補を serving に切替え、敗者 chunk_set を keep=候補のみで GC する。"""
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

    response = await promote_chunk_set_experiment("doc-1", candidate_id)

    assert response.data is not None
    assert response.data.chunk_set_id == candidate_id
    assert oracle.serving_calls == [candidate_id]
    assert oracle.deleted_keep == [[candidate_id]]
    assert oracle.processing_config.chunking_strategy == "page_level"
    assert oracle.processing_config.chunk_size == 512
    # 敗者(旧 serving)は GC 済み。
    assert _SERVING_ID not in oracle.chunk_sets


async def test_promote_rejects_chunk_set_not_on_document(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    oracle = FakeExperimentOracle()
    _patch(monkeypatch, oracle)
    with pytest.raises(HTTPException) as exc:
        await promote_chunk_set_experiment("doc-1", "cs_not_here_00000000")
    assert exc.value.status_code == 404


def test_experiment_request_requires_at_least_one_override() -> None:
    with pytest.raises(ValidationError):
        ChunkSetExperimentRequest()


def test_candidate_settings_rejects_overlap_not_smaller_than_size() -> None:
    request = ChunkSetExperimentRequest(chunk_size=300, chunk_overlap=300)
    with pytest.raises(HTTPException) as exc:
        _candidate_experiment_settings(get_settings(), request)
    assert exc.value.status_code == 422
