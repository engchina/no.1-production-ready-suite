"""正式レシピ欠測を旧設定preflightへ渡さず、mixedの正常文書を受付する。"""

from datetime import UTC, datetime
from typing import Any

import pytest

from app.clients.oracle import OracleClient
from app.clients.oracle_bulk_ingestion_store import OracleBulkIngestionStore
from app.config import Settings
from app.rag.bulk_ingestion import accept_bulk_ingestion
from app.rag.kb_adapter_config import (
    KbAdapterConfigError,
    KnowledgeBaseAdapterConfig,
    resolve_effective_settings,
)
from app.rag.parser_source_guard import parser_source_block
from app.schemas.document import (
    DocumentProcessingConfig,
    IngestionJob,
    IngestionJobPhase,
    IngestionJobStatus,
    SourceProfile,
)
from tests.test_oracle_adapter import FakeOraclePool, _oci_settings, _run_inline

NOW = datetime(2026, 1, 2, tzinfo=UTC)


def document(
    did: str,
    *,
    formal: bool = True,
    config: dict[str, Any] | None = None,
    legacy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "document_id": did,
        "file_name": did + ".txt",
        "document_status": "UPLOADED",
        "tenant_id_hash": None,
        "content_type": "text/plain",
        "file_size_bytes": 10,
        "content_sha256": "a" * 64,
        "duplicate_of_document_id": None,
        "processing_config": legacy or {"parser_adapter_backend": "docling"},
        "uploaded_at": NOW,
        "has_source": 1,
        "has_outputs": 0,
        "list_status": "UPLOADED" if formal else None,
        "default_recipe_id": "formal-" + did if formal else None,
        "recipe_processing_config": config,
    }


@pytest.mark.parametrize(
    "scenario",
    [
        "formal",
        "formal-empty",
        "missing-supported",
        "missing-blocked",
        "mixed-invalid",
        "invisible",
        "active",
        "history",
        "mixed-invalid-reversed",
        "formal-invalid",
    ],
)
async def test_bulk_preflights_only_formal_recipes(scenario: str) -> None:
    good = document(
        "good",
        config={"parser_adapter_backend": "unstructured", "preprocess_profile": "passthrough"},
    )
    rows = [good]
    requested = ["good"]
    jobs: list[dict[str, object]] = []
    lock = [{"document_id": "good", "recipe_id": "formal-good", "config_revision": 3}]
    expected: dict[str, tuple[str, str | None]] = {"good": ("accepted", None)}
    expected_resolved = 1
    expected_insert = {"good"}
    expected_single: list[str] = []
    if scenario == "formal-empty":
        good["recipe_processing_config"] = None
    if scenario.startswith("missing-"):
        good.update(default_recipe_id=None, list_status=None, recipe_processing_config=None)
        good["processing_config"] = {
            "parser_adapter_backend": "unstructured"
            if scenario == "missing-supported"
            else "docling",
            "preprocess_profile": "passthrough",
        }
        lock = []
        expected = {"good": ("rejected", "not_found")}
        expected_resolved = 0
        expected_insert = set()
    if scenario in {"mixed-invalid", "mixed-invalid-reversed"}:
        rows.append(
            document("missing", formal=False, legacy={"chunk_size": 256, "chunk_overlap": 300})
        )
        requested.append("missing")
        expected["missing"] = ("rejected", "not_found")
        if scenario == "mixed-invalid-reversed":
            requested.reverse()
            rows.reverse()
    if scenario == "invisible":
        rows = []
        lock = []
        expected = {"good": ("rejected", "not_found")}
        expected_resolved = 0
        expected_insert = set()
    if scenario == "active":
        jobs = [{"document_id": "good", "recipe_id": None, "active": 1}]
        expected = {"good": ("already_active", "job_active")}
        expected_resolved = 0
        expected_insert = set()
    if scenario == "history":
        jobs = [{"document_id": "good", "recipe_id": "formal-good", "active": 0}]
        expected_insert = set()
        expected_single = ["good"]
    if scenario == "formal-invalid":
        good["recipe_processing_config"] = {"chunk_size": 256, "chunk_overlap": 300}
    pool = FakeOraclePool(execute_results=[rows, jobs, lock, []])
    client = OracleClient(settings=_oci_settings(), pool=pool, db_call_runner=_run_inline)
    resolved: list[dict[str, Any]] = []
    parser_checks: list[str] = []
    single: list[str] = []
    base = Settings.model_construct(
        rag_parser_adapter_backend="unstructured", rag_preprocess_profile="passthrough"
    )

    def resolve(config: DocumentProcessingConfig) -> Settings:
        resolved.append(config.model_dump(exclude_none=True))
        return resolve_effective_settings(
            base, KnowledgeBaseAdapterConfig(ingestion=config), scope="ingestion"
        )

    async def parser(settings: Settings, profile: SourceProfile) -> str | None:
        parser_checks.append(profile.original_file_name)
        block = parser_source_block(settings, profile)
        return block.message if block else None

    async def enqueue(did: str) -> IngestionJob:
        single.append(did)
        return IngestionJob(
            id="single-job",
            document_id=did,
            recipe_id="formal-" + did,
            recipe_revision=3,
            status=IngestionJobStatus.QUEUED,
            phase=IngestionJobPhase.PREPROCESS,
            parser_profile="local_text_structure",
            queued_at=NOW,
        )

    if scenario == "formal-invalid":
        with pytest.raises(KbAdapterConfigError, match="chunk_overlap"):
            await accept_bulk_ingestion(
                requested,
                store=OracleBulkIngestionStore(client),
                resolve_settings=resolve,
                enqueue_single=enqueue,
                max_attempts=3,
                parser_message=parser,
                clock=lambda: NOW,
            )
        assert resolved == [{"chunk_size": 256, "chunk_overlap": 300}]
        assert parser_checks == single == []
        assert pool.connection.many_calls == []
        return
    result = await accept_bulk_ingestion(
        requested,
        store=OracleBulkIngestionStore(client),
        resolve_settings=resolve,
        enqueue_single=enqueue,
        max_attempts=3,
        parser_message=parser,
        clock=lambda: NOW,
    )
    assert {x.document_id: (x.outcome.value, x.reason) for x in result.items} == expected
    assert len(resolved) == len(parser_checks) == expected_resolved
    assert single == expected_single
    inserted = [row for call in pool.connection.many_calls for row in call.rows]
    assert {row["document_id"] for row in inserted} == expected_insert
    assert all(not call.statement.startswith("MERGE") for call in pool.connection.many_calls)
    assert all(
        row["recipe_id"] == "formal-" + str(row["document_id"]) and row["recipe_revision"] == 3
        for row in inserted
    )
    if scenario == "formal-empty":
        assert resolved == [{}]
    if scenario in {"mixed-invalid", "mixed-invalid-reversed"}:
        assert all("chunk_overlap" not in c for c in resolved)
