"""合成2段PDFを正式adapter/job/候補構築/読戻しまで通す。I/Oは決定論代役。"""

from __future__ import annotations

import hashlib
import json
from typing import Any, cast

import pytest

from app.api.routes import documents
from app.rag.ingestion import IngestionPipeline
from app.rag.ingestion_attempt_receipts import ATTEMPT_RECEIPT_KEY, read_attempt_receipt
from app.rag.staging_recipes import RecipeStagingIngestion, read_staging_recipe
from app.schemas.document import FileStatus, IngestionJobStatus
from tests import test_ingestion_pdf_segments as pdf
from tests.language_release_support import install_language_release_stub
from tests.test_document_recipes import _CompletingRecipeOracle
from tests.test_ingestion_strategy import FakeEmbeddingClient, FakeObjectStorage
from tests.test_ingestion_strategy import FakeOracle as ParserOracle


class FormalOracle(_CompletingRecipeOracle, ParserOracle):
    def __init__(self, source: bytes) -> None:
        _CompletingRecipeOracle.__init__(self)
        ParserOracle.__init__(self)
        self.source = source
        self.recipe_status = FileStatus.UPLOADED
        self.recipe_values: dict[str, Any] = {}
        self.chunks: dict[str, list[Any]] = {}
        self.progress: dict[str, dict[str, Any]] = {}
        self.events: list[str] = []

    async def update_document_status(self, *args: Any, **kwargs: Any) -> Any:
        return await ParserOracle.update_document_status(self, *args, **kwargs)

    async def list_document_recipes(self, document_id: str) -> list[dict[str, Any]]:
        return [{**await self.get_document_recipe(document_id, "recipe-1"), "slot_no": 1}]

    async def ensure_default_document_recipe(self, document_id: str) -> dict[str, Any]:
        return await self.get_document_recipe(document_id, "recipe-1")

    async def get_document(self, document_id: str) -> Any:
        value = await super().get_document(document_id)
        return value.model_copy(
            update={
                "content_sha256": hashlib.sha256(self.source).hexdigest(),
                "file_size_bytes": len(self.source),
                "status": FileStatus.UPLOADED,
                "object_storage_path": "local://original.pdf",
            }
        )

    async def get_document_recipe(self, document_id: str, recipe_id: str) -> dict[str, Any]:
        row = await super().get_document_recipe(document_id, recipe_id)
        # 旧activeを持つ完成用fakeの既定値は使用しない。
        row.pop("active_extraction_recipe_id", None)
        row.update(self.recipe_values)
        row["active_chunk_set_id"] = self.activated[-1] if self.activated else None
        return row

    async def update_document_recipe_status(
        self, *, recipe_id: str, status: Any, **kwargs: Any
    ) -> None:
        self.recipe_status = FileStatus(status)
        self.recipe_values.update(
            {
                key: value.model_dump(mode="json") if hasattr(value, "model_dump") else value
                for key, value in kwargs.items()
                if value is not None
            }
        )

    async def upsert_document_extraction_artifact(self, **kwargs: Any) -> None:
        await ParserOracle.upsert_document_extraction_artifact(self, **kwargs)

    async def get_document_extraction_artifact(self, **kwargs: Any) -> Any:
        return await ParserOracle.get_document_extraction_artifact(self, **kwargs)

    async def save_index(
        self,
        document_id: str,
        extraction: Any,
        chunks: Any,
        vectors: Any,
        chunk_set_id: str | None = None,
    ) -> None:
        assert chunk_set_id is not None
        await ParserOracle.save_index(self, document_id, extraction, chunks, vectors, chunk_set_id)
        self.chunks[chunk_set_id] = list(chunks)

    async def count_chunk_set_chunks(self, chunk_set_id: str) -> int:
        return len(self.chunks.get(chunk_set_id, []))

    async def mark_chunk_set_indexed(self, **kwargs: object) -> None:
        self.chunk_set_rows[str(kwargs["chunk_set_id"])].update(status="INDEXED", **kwargs)

    async def activate_recipe_chunk_set(self, *, chunk_set_id: str, **kwargs: object) -> None:
        job_fence = cast(Any, kwargs["job_fence"])
        expected_base_chunk_set_id = kwargs["expected_base_chunk_set_id"]
        job = self.jobs[job_fence.job_id]
        assert (
            job.status == IngestionJobStatus.RUNNING
            and job.attempt_count == job_fence.attempt_count
        )
        assert expected_base_chunk_set_id == (self.activated[-1] if self.activated else None)
        self.activated.append(chunk_set_id)
        self.recipe_status = FileStatus.INDEXED
        self.events.append("activate")

    async def get_chunk_set(self, chunk_set_id: str) -> Any:
        return self.chunk_set_rows.get(chunk_set_id)

    async def list_chunk_set_chunks(self, chunk_set_id: str) -> list[Any]:
        return self.chunks.get(chunk_set_id, [])

    async def update_ingestion_job_progress(
        self, job_id: str, progress: Any, **kwargs: Any
    ) -> bool:
        # DB RMWは別の実Oracle試験の責務。この代役は予約記録を保持する。
        return True

    async def save_ingestion_attempt_receipt(
        self, job_id: str, receipt: Any, *, attempt_count: int, lease_owner: Any
    ) -> bool:
        job = self.jobs[job_id]
        assert job.status == IngestionJobStatus.RUNNING
        assert attempt_count == job.attempt_count and lease_owner is None
        self.progress[job_id] = {ATTEMPT_RECEIPT_KEY: receipt.model_dump(mode="json")}
        self.events.append("receipt")
        return True

    async def read_ingestion_attempt_receipt(self, job: Any) -> Any:
        return read_attempt_receipt(
            self.progress.get(job.id, {}),
            job_id=job.id,
            attempt_count=job.attempt_count,
            document_id=job.document_id,
            recipe_id=job.recipe_id,
            recipe_revision=job.recipe_revision,
        )

    async def read_staging_recipe(self, document_id: str) -> Any:
        return await read_staging_recipe(self, document_id)


async def test_formal_adapter_retry_preserves_failed_attempt_and_reads_activated_version(
    monkeypatch: Any,
) -> None:
    from dataclasses import replace

    from app.rag.file_processing_staging import (
        _collect_staging_evidence,
        _segment_artifact_reuse_passed,
    )
    from app.rag.ingestion import IngestionUserError

    install_language_release_stub(monkeypatch)
    source = pdf._pdf(2)
    storage = FakeObjectStorage()
    storage.objects["local://original.pdf"] = source
    oracle = FormalOracle(source)
    settings = pdf._settings(
        rag_pdf_max_pages_per_segment=1, rag_parser_readiness_probe_enabled=False
    )
    runner = pdf._Runner(fail_on_call=2)
    pdf._install(monkeypatch, runner)
    monkeypatch.setattr(documents, "OracleClient", lambda: oracle)
    monkeypatch.setattr(documents, "ObjectStorageClient", lambda: storage)
    monkeypatch.setattr(documents, "get_settings", lambda: settings)
    monkeypatch.setattr("app.rag.staging_recipes.get_settings", lambda: settings)
    monkeypatch.setattr(documents, "_dispatch_ingestion_job", lambda *args: None)

    def factory(**kwargs: Any) -> IngestionPipeline:
        return IngestionPipeline(
            genai=FakeEmbeddingClient(), object_storage=cast(Any, storage), **kwargs
        )

    monkeypatch.setattr(documents, "IngestionPipeline", factory)
    adapter = RecipeStagingIngestion(oracle=cast(Any, oracle), settings=settings)
    with pytest.raises(IngestionUserError):
        await adapter.ingest("doc-1", source, "合成の取込", content_type="application/pdf")
    before = await _collect_staging_evidence(cast(Any, oracle), cast(Any, storage), "doc-1")
    frozen_before = json.dumps([r.model_dump(mode="json") for r in before.segments], sort_keys=True)
    assert before.status == "ERROR"
    assert [r.status for r in before.segments] == ["SUCCEEDED", "FAILED"]
    assert oracle.activated == []
    assert oracle.events == ["receipt"]
    runner.fail_on_call = None
    result = await adapter.ingest("doc-1", source, "合成の取込", content_type="application/pdf")
    after = await _collect_staging_evidence(cast(Any, oracle), cast(Any, storage), "doc-1")
    assert result.status == FileStatus.INDEXED
    assert runner.calls == [[1], [2], [2]]
    assert (
        json.dumps([r.model_dump(mode="json") for r in before.segments], sort_keys=True)
        == frozen_before
    )
    assert len(oracle.activated) == 1
    active = oracle.chunk_set_rows[oracle.activated[0]]
    assert active["document_id"] == "doc-1" and active["recipe_id"] == "recipe-1"
    assert active["extraction_recipe_id"] in oracle.extraction_artifacts
    assert (
        after.extraction
        == oracle.extraction_artifacts[active["extraction_recipe_id"]]["extraction"]
    )
    assert after.status == "INDEXED" and after.chunks
    assert _segment_artifact_reuse_passed(
        replace(after, initial_retry_segments=before.segments, retry_segments=after.segments)
    ) == (True, None)
