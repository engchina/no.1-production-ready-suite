"""切替と旧版の清掃の分離（#4168。実 Oracle）。"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pr_backend_core.mcp import McpToolError

from app.api.errors import SourceVersionStaleError
from app.clients.chunk_set_retention_store import ChunkSetRetentionStore, ExpiredRecipeVersions
from app.clients.document_cleanup_store import DocumentCleanupStore
from app.clients.oracle import OracleClient, _execute
from app.clients.section_map_store import SectionMapStore
from app.mcp import tools as mcp_tools
from app.rag.chunk_set_retention import expire_retired_chunk_sets
from app.rag.chunking import Chunk
from app.rag.request_context import (
    AuditRequestContext,
    reset_audit_request_context,
    set_audit_request_context,
)
from app.rag.section_map_service import rebuild_document_section_map
from app.schemas.document import IngestionJob, IngestionJobPhase, IngestionJobStatus
from app.schemas.extraction import StructuredExtraction
from app.services.source_reader import resolve_source_context

pytestmark = pytest.mark.usefixtures("oracle_db")

PREFIX = "artifacts/canonical"


async def _new_document(client: OracleClient, name: str) -> tuple[str, str]:
    document = await client.create_document(
        file_name=f"synthetic-{name}.txt",
        object_storage_path=f"synthetic-{name}-{uuid4().hex[:8]}.txt",
        content_type="text/plain",
    )
    recipe = await client.ensure_default_document_recipe(document.id)
    return document.id, str(recipe["recipe_id"])


async def _version(
    client: OracleClient,
    document_id: str,
    recipe_id: str,
    chunk_set_id: str,
    extraction_id: str,
    *,
    derived_path: str | None = None,
) -> str:
    payload = StructuredExtraction(raw_text=chunk_set_id).to_document_payload()
    if derived_path is not None:
        payload["parser_artifacts"] = {"source_derivation": {"derived_object_path": derived_path}}
    await client.upsert_document_extraction_artifact(
        document_id=document_id,
        extraction_recipe_id=extraction_id,
        source_sha256="a" * 64,
        extraction=payload,
        status="materialized",
    )
    await client.upsert_chunk_set(
        chunk_set_id=chunk_set_id,
        document_id=document_id,
        recipe_id=recipe_id,
        extraction_recipe_id=extraction_id,
    )
    await client.save_chunk_preview(
        document_id,
        StructuredExtraction(raw_text=chunk_set_id),
        [Chunk(index=0, text=chunk_set_id, start_offset=0, end_offset=len(chunk_set_id))],
        chunk_set_id=chunk_set_id,
    )
    await client.mark_chunk_set_indexed(chunk_set_id=chunk_set_id, chunk_count=1, vector_count=0)
    [chunk] = await client.list_chunk_set_chunks(chunk_set_id)
    return chunk.chunk_id


async def _switch(
    client: OracleClient,
    recipe_id: str,
    chunk_set_id: str,
    base: str | None,
    extraction_id: str | None = None,
) -> None:
    await client.activate_recipe_chunk_set(
        recipe_id=recipe_id,
        chunk_set_id=chunk_set_id,
        expected_base_chunk_set_id=base,
        extraction_recipe_id=extraction_id,
    )


async def _retired_ago(client: OracleClient, chunk_set_id: str, days: float) -> None:
    def operation(connection: object) -> None:
        _execute(
            connection,  # type: ignore[arg-type]
            """
            UPDATE rag_chunk_sets
            SET retired_at = SYSTIMESTAMP - NUMTODSINTERVAL(:days, 'DAY')
            WHERE chunk_set_id = :chunk_set_id
            """,
            {"days": days, "chunk_set_id": chunk_set_id},
        )

    await client._run_transaction(operation)


async def _expire(
    client: OracleClient, recipe_id: str, *, days: int = 7, versions: int = 1
) -> ExpiredRecipeVersions:
    return await ChunkSetRetentionStore(client).expire_recipe(
        recipe_id, retention_days=days, retained_versions=versions, canonical_prefix=PREFIX
    )


async def _two_versions(client: OracleClient, name: str) -> tuple[str, str, str, str, str, str]:
    document_id, recipe_id = await _new_document(client, name)
    suffix = uuid4().hex[:12]
    old_set, new_set = f"old_{suffix}", f"new_{suffix}"
    old_chunk = await _version(client, document_id, recipe_id, old_set, f"ex_o_{suffix}")
    await _switch(client, recipe_id, old_set, None, f"ex_o_{suffix}")
    new_chunk = await _version(client, document_id, recipe_id, new_set, f"ex_n_{suffix}")
    await _switch(client, recipe_id, new_set, old_set, f"ex_n_{suffix}")
    return document_id, recipe_id, old_set, new_set, old_chunk, new_chunk


async def test_switch_keeps_old_version_readable_but_hidden_from_search() -> None:
    client = OracleClient()
    document_id, recipe_id, old_set, new_set, old_chunk, new_chunk = await _two_versions(
        client, "keep-readable"
    )

    # 根拠を ID で開ける。
    readable = await client.readable_version_chunk(document_id, old_chunk)
    assert readable is not None and readable.metadata["chunk_set_id"] == old_set
    assert (await mcp_tools.readable_chunk(client, document_id, old_chunk)).chunk_id == old_chunk
    chunk, revision, _, payload = await resolve_source_context(client, document_id, old_chunk)
    assert chunk.chunk_id == old_chunk and revision == old_set
    assert payload.get("raw_text") == old_set
    # 版の指定でも読める。指定が無ければ active だけ。
    store = SectionMapStore(client)
    assert (await store.readable_chunk_set(document_id, old_set)) is not None
    assert (await store.readable_chunk_set(document_id)).chunk_set_id == new_set  # type: ignore[union-attr]
    assert await client.readable_document_chunks(
        document_id, chunk_set_id=old_set, from_index=0, limit=5
    )

    # 検索・回答の監査・件数には出ない。
    assert await client.retrievable_chunk(document_id, old_chunk) is None
    assert await client.retrievable_chunk(document_id, new_chunk) is not None
    filters = {"document_id": document_id}
    assert await client.retrieval_scope_state(filters) == await client.retrieval_scope_state(
        {"document_id": document_id, "chunk_set_id": new_set}
    )
    corpus = await client.evaluation_corpus_rows([])
    assert {row["chunk_set_id"] for row in corpus if row["document_id"] == document_id} == {new_set}
    recipe = await client.get_document_recipe(document_id, recipe_id)
    assert recipe is not None
    assert recipe["active_chunk_set_id"] == new_set
    assert recipe["chunk_count"] == 1
    assert recipe["pending_chunk_set_id"] is None
    rows = await client._fetch_all(
        "SELECT COUNT(*) AS cnt FROM rag_chunks c JOIN rag_documents d "
        "ON d.document_id = c.document_id WHERE c.document_id = :d",
        {"d": document_id},
    )
    assert int(str(rows[0]["cnt"])) == 2  # 行は残っている


async def test_old_version_is_invisible_without_permission() -> None:
    client = OracleClient()
    document_id, _, old_set, _, old_chunk, _ = await _two_versions(client, "no-permission")
    token = set_audit_request_context(
        AuditRequestContext(allowed_document_ids=frozenset({"other-document"}))
    )
    try:
        assert await client.readable_version_chunk(document_id, old_chunk) is None
        assert await SectionMapStore(client).readable_chunk_set(document_id, old_set) is None
        with pytest.raises(McpToolError) as tool_error:
            await mcp_tools.readable_chunk(client, document_id, old_chunk)
        assert tool_error.value.code == mcp_tools.SOURCE_NOT_FOUND_CODE
        with pytest.raises(HTTPException) as http_error:
            await resolve_source_context(client, document_id, old_chunk)
        assert http_error.value.status_code == 404
    finally:
        reset_audit_request_context(token)


async def test_cleanup_waits_for_retention_and_is_idempotent() -> None:
    client = OracleClient()
    document_id, recipe_id, old_set, new_set, old_chunk, _ = await _two_versions(
        client, "retention"
    )

    # 保持の間は消さない。
    kept = await _expire(client, recipe_id)
    assert kept.chunk_set_ids == () and kept.skipped is False
    assert await client.get_chunk_set(old_set) is not None

    await _retired_ago(client, old_set, 8)
    expired = await _expire(client, recipe_id)
    assert expired.chunk_set_ids == (old_set,)
    assert await client.get_chunk_set(old_set) is None
    assert await client.get_chunk_set(new_set) is not None

    # 繰り返しても同じ。
    again = await _expire(client, recipe_id)
    assert again.chunk_set_ids == ()
    assert await client.get_chunk_set(new_set) is not None

    # 清掃の後は版の失効の案内になる。
    assert await client.readable_version_chunk(document_id, old_chunk) is None
    with pytest.raises(McpToolError) as tool_error:
        await mcp_tools.readable_chunk(client, document_id, old_chunk)
    assert tool_error.value.code == mcp_tools.SOURCE_STALE_CODE
    with pytest.raises(SourceVersionStaleError):
        await resolve_source_context(client, document_id, old_chunk)


async def test_cleanup_keeps_only_the_configured_number_of_versions() -> None:
    client = OracleClient()
    document_id, recipe_id, first, second, first_chunk, _ = await _two_versions(client, "count")
    suffix = uuid4().hex[:12]
    third = f"third_{suffix}"
    await _version(client, document_id, recipe_id, third, f"ex_t_{suffix}")
    await _switch(client, recipe_id, third, second, f"ex_t_{suffix}")
    # 切替は日数の内でも古い版を消さない。清掃が版の数を超えた分だけを消す。
    assert await client.get_chunk_set(first) is not None
    assert await client.readable_version_chunk(document_id, first_chunk) is not None

    expired = await _expire(client, recipe_id)
    assert expired.chunk_set_ids == (first,)
    assert await client.get_chunk_set(second) is not None


async def test_cleanup_skips_recipe_with_running_job() -> None:
    client = OracleClient()
    document_id, recipe_id, old_set, _, _, _ = await _two_versions(client, "running-job")
    await _retired_ago(client, old_set, 30)
    job = await client.create_ingestion_job(
        IngestionJob(
            id=f"job-{uuid4().hex[:16]}",
            document_id=document_id,
            recipe_id=recipe_id,
            recipe_revision=1,
            parser_profile="generic",
            status=IngestionJobStatus.QUEUED,
            phase=IngestionJobPhase.INDEX,
            queued_at=datetime.now(UTC),
        )
    )
    store = ChunkSetRetentionStore(client)
    assert recipe_id not in await store.due_recipe_ids(
        retention_days=7, retained_versions=1, limit=100
    )
    skipped = await _expire(client, recipe_id)
    assert skipped.skipped is True and skipped.chunk_set_ids == ()
    assert await client.get_chunk_set(old_set) is not None

    await client.transition_ingestion_job(
        job.id,
        from_statuses=(IngestionJobStatus.QUEUED,),
        to_status=IngestionJobStatus.CANCELLED,
        finished_at=datetime.now(UTC),
    )
    assert recipe_id in await store.due_recipe_ids(retention_days=7, retained_versions=1, limit=100)
    assert (await _expire(client, recipe_id)).chunk_set_ids == (old_set,)


async def test_cleanup_removes_derived_rows_and_keeps_shared_extraction() -> None:
    client = OracleClient()
    document_id, recipe_id = await _new_document(client, "derived-rows")
    suffix = uuid4().hex[:12]
    old_set, new_set = f"old_{suffix}", f"new_{suffix}"
    shared = f"ex_shared_{suffix}"
    await _version(client, document_id, recipe_id, old_set, shared)
    await _switch(client, recipe_id, old_set, None, shared)
    await rebuild_document_section_map(document_id, oracle=client)
    # 分割だけのやり直し: 新版も同じ抽出を読む。
    await _version(client, document_id, recipe_id, new_set, shared)
    await _switch(client, recipe_id, new_set, old_set, shared)
    await rebuild_document_section_map(document_id, oracle=client)

    async def section_sets() -> set[str]:
        rows = await client._fetch_all(
            "SELECT DISTINCT chunk_set_id FROM rag_chunk_set_sections WHERE document_id = :d",
            {"d": document_id},
        )
        return {str(row["chunk_set_id"]) for row in rows}

    # 切替は章節の行を消さない（旧版の根拠の章節を読める）。
    assert await section_sets() == {old_set, new_set}
    await _retired_ago(client, old_set, 8)
    await _expire(client, recipe_id)
    assert await section_sets() == {new_set}
    # 新版が読む抽出は残る。
    assert await client.get_document_extraction_artifact(
        document_id=document_id, extraction_recipe_id=shared
    )


async def test_cleanup_records_only_unreferenced_prepared_files() -> None:
    client = OracleClient()
    document_id, recipe_id = await _new_document(client, "files")
    suffix = uuid4().hex[:12]
    only_old = f"{PREFIX}/{document_id}/trace_old_{suffix}/canonical.pdf"
    shared = f"{PREFIX}/{document_id}/trace_shared_{suffix}/canonical.pdf"
    foreign = f"other-place/{document_id}/canonical.pdf"
    sets = [f"a_{suffix}", f"b_{suffix}", f"c_{suffix}", f"d_{suffix}"]
    paths = [only_old, shared, foreign, shared]
    base: str | None = None
    for index, (chunk_set_id, path) in enumerate(zip(sets, paths, strict=True)):
        extraction_id = f"ex_{index}_{suffix}"
        await _version(
            client, document_id, recipe_id, chunk_set_id, extraction_id, derived_path=path
        )
        await _switch(client, recipe_id, chunk_set_id, base, extraction_id)
        base = chunk_set_id

    result = await _expire(client, recipe_id, days=0, versions=0)
    assert set(result.chunk_set_ids) == {sets[0], sets[1], sets[2]}
    # 新版が同じファイルを読む・正規化原本の置き場の外のファイルは消さない。
    assert result.orphan_paths == (only_old,)
    assert result.cleanup is not None
    task = await DocumentCleanupStore(client).find_for_document(document_id)
    assert task is not None and task.targets.artifact_uris == (only_old,)


async def test_reactivating_a_retired_version_clears_retired_time() -> None:
    client = OracleClient()
    document_id, recipe_id, old_set, new_set, old_chunk, _ = await _two_versions(
        client, "reactivate"
    )
    await _retired_ago(client, old_set, 30)
    await _switch(client, recipe_id, old_set, new_set)
    assert await client.retrievable_chunk(document_id, old_chunk) is not None
    # 戻した版は清掃の対象にならない。切替で退役した版は保持の間は残る。
    assert (await _expire(client, recipe_id)).chunk_set_ids == ()
    assert await client.get_chunk_set(old_set) is not None
    assert await client.get_chunk_set(new_set) is not None


async def test_switch_removes_only_never_served_candidates() -> None:
    client = OracleClient()
    document_id, recipe_id, old_set, new_set, _, _ = await _two_versions(client, "candidates")
    suffix = uuid4().hex[:12]
    abandoned, next_set = f"abandoned_{suffix}", f"next_{suffix}"
    await _version(client, document_id, recipe_id, abandoned, f"ex_a_{suffix}")
    await _version(client, document_id, recipe_id, next_set, f"ex_x_{suffix}")
    await _switch(client, recipe_id, next_set, new_set, f"ex_x_{suffix}")
    assert await client.get_chunk_set(abandoned) is None
    assert await client.get_chunk_set(old_set) is not None
    assert await client.get_chunk_set(new_set) is not None


async def test_discard_never_removes_a_retired_version() -> None:
    client = OracleClient()
    document_id, recipe_id, old_set, new_set, old_chunk, _ = await _two_versions(client, "discard")
    assert (
        await client.discard_recipe_candidate_chunk_set(recipe_id=recipe_id, chunk_set_id=old_set)
        is False
    )
    assert await client.readable_version_chunk(document_id, old_chunk) is not None
    # 候補を捨てても、退役した版と抽出の指す先はそのまま。
    suffix = uuid4().hex[:12]
    candidate = f"cand_{suffix}"
    await _version(client, document_id, recipe_id, candidate, f"ex_c_{suffix}")
    assert await client.discard_recipe_candidate_chunk_set(
        recipe_id=recipe_id, chunk_set_id=candidate
    )
    assert await client.get_chunk_set(old_set) is not None
    assert await client.get_chunk_set(new_set) is not None


async def test_retired_version_extraction_is_protected_from_overwrite() -> None:
    client = OracleClient()
    document_id, recipe_id, old_set, new_set, _, _ = await _two_versions(client, "extraction")
    old = await client.get_chunk_set(old_set)
    new = await client.get_chunk_set(new_set)
    assert old is not None and new is not None
    assert await client.served_extraction_ids(document_id) == {
        str(old["extraction_recipe_id"]),
        str(new["extraction_recipe_id"]),
    }


async def test_recipe_version_exists_only_for_active_and_retired() -> None:
    client = OracleClient()
    document_id, recipe_id, old_set, new_set, _, _ = await _two_versions(client, "version-exists")
    suffix = uuid4().hex[:12]
    candidate = f"cand_{suffix}"
    await _version(client, document_id, recipe_id, candidate, f"ex_c_{suffix}")

    async def exists(chunk_set_id: str) -> bool:
        return await client.recipe_version_exists(
            document_id=document_id, recipe_id=recipe_id, chunk_set_id=chunk_set_id
        )

    assert await exists(new_set) is True
    assert await exists(old_set) is True
    assert await exists(candidate) is False
    assert await exists(f"missing_{suffix}") is False


async def test_sweep_uses_settings_and_returns_expired_count() -> None:
    client = OracleClient()
    _, recipe_id, old_set, _, _, _ = await _two_versions(client, "sweep")
    await _retired_ago(client, old_set, 8)
    assert recipe_id in await ChunkSetRetentionStore(client).due_recipe_ids(
        retention_days=7, retained_versions=1, limit=1000
    )
    expired = await expire_retired_chunk_sets(store=ChunkSetRetentionStore(client), limit=1000)
    assert expired >= 1
    assert await client.get_chunk_set(old_set) is None
