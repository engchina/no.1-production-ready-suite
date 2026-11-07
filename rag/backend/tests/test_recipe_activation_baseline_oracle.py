"""active の切替の基線の照合と履歴（#4167。実 Oracle）。"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.clients.chunk_set_retention_store import ChunkSetRetentionStore
from app.clients.oracle import (
    IngestionJobFence,
    OracleClient,
    RecipeActivationConflictError,
    RecipeActivationFencedError,
)
from app.rag.chunking import Chunk
from app.schemas.document import FileStatus, IngestionJob, IngestionJobPhase, IngestionJobStatus
from app.schemas.extraction import StructuredExtraction


async def _new_document(client: OracleClient, name: str) -> tuple[str, str]:
    document = await client.create_document(
        file_name=f"synthetic-{name}.txt",
        object_storage_path=f"synthetic-{name}-{uuid4().hex[:8]}.txt",
        content_type="text/plain",
    )
    recipe = await client.ensure_default_document_recipe(document.id)
    return document.id, str(recipe["recipe_id"])


async def _chunk_set(
    client: OracleClient, document_id: str, recipe_id: str, chunk_set_id: str, extraction_id: str
) -> None:
    await client.upsert_document_extraction_artifact(
        document_id=document_id,
        extraction_recipe_id=extraction_id,
        source_sha256="a" * 64,
        extraction=StructuredExtraction(raw_text=chunk_set_id).to_document_payload(),
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


async def _running_job(
    client: OracleClient, document_id: str, recipe_id: str, *, lease_owner: str
) -> IngestionJob:
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
    claimed = await client.claim_ingestion_job(
        job.id, started_at=datetime.now(UTC), lease_owner=lease_owner
    )
    assert claimed is not None
    return claimed


async def _active_chunk_set_id(client: OracleClient, document_id: str, recipe_id: str) -> object:
    recipe = await client.get_document_recipe(document_id, recipe_id)
    assert recipe is not None
    return recipe["active_chunk_set_id"]


@pytest.mark.usefixtures("oracle_db")
async def test_switch_is_rejected_when_base_changed_and_active_stays() -> None:
    client = OracleClient()
    document_id, recipe_id = await _new_document(client, "baseline-conflict")
    suffix = uuid4().hex[:12]
    base, newer, stale = f"base_{suffix}", f"newer_{suffix}", f"stale_{suffix}"
    await _chunk_set(client, document_id, recipe_id, base, f"ex_b_{suffix}")
    await client.activate_recipe_chunk_set(
        recipe_id=recipe_id,
        chunk_set_id=base,
        expected_base_chunk_set_id=None,
        extraction_recipe_id=f"ex_b_{suffix}",
    )
    # 先に切り替わる新しい版。
    await _chunk_set(client, document_id, recipe_id, newer, f"ex_n_{suffix}")
    await client.activate_recipe_chunk_set(
        recipe_id=recipe_id,
        chunk_set_id=newer,
        expected_base_chunk_set_id=base,
        extraction_recipe_id=f"ex_n_{suffix}",
    )
    log_before = await client.list_recipe_version_log(document_id, recipe_id)
    # 基線を古い版のまま持つ job の候補。
    await _chunk_set(client, document_id, recipe_id, stale, f"ex_s_{suffix}")

    with pytest.raises(RecipeActivationConflictError) as caught:
        await client.activate_recipe_chunk_set(
            recipe_id=recipe_id,
            chunk_set_id=stale,
            expected_base_chunk_set_id=base,
            extraction_recipe_id=f"ex_s_{suffix}",
        )

    assert caught.value.current == newer
    assert await _active_chunk_set_id(client, document_id, recipe_id) == newer
    assert await client.list_recipe_version_log(document_id, recipe_id) == log_before


@pytest.mark.usefixtures("oracle_db")
async def test_log_keeps_one_row_per_switch_after_old_version_is_gone() -> None:
    client = OracleClient()
    document_id, recipe_id = await _new_document(client, "version-log")
    suffix = uuid4().hex[:12]
    first, second = f"first_{suffix}", f"second_{suffix}"
    await _chunk_set(client, document_id, recipe_id, first, f"ex_f_{suffix}")
    first_job = await _running_job(client, document_id, recipe_id, lease_owner="worker-a")
    await client.activate_recipe_chunk_set(
        recipe_id=recipe_id,
        chunk_set_id=first,
        expected_base_chunk_set_id=None,
        extraction_recipe_id=f"ex_f_{suffix}",
        materialized_revision=1,
        job_fence=IngestionJobFence(
            job_id=first_job.id, attempt_count=first_job.attempt_count, lease_owner="worker-a"
        ),
        chunk_set_input_id=f"input_{suffix}",
    )
    await client.transition_ingestion_job(
        first_job.id,
        from_statuses=(IngestionJobStatus.RUNNING,),
        to_status=IngestionJobStatus.SUCCEEDED,
        finished_at=datetime.now(UTC),
    )
    [initial] = await client.list_recipe_version_log(document_id, recipe_id)
    assert initial["operation"] == "INITIAL"
    assert initial["chunk_set_id"] == first
    assert initial["parent_chunk_set_id"] is None
    assert initial["job_id"] == first_job.id
    assert initial["extraction_recipe_id"] == f"ex_f_{suffix}"
    assert initial["chunk_set_input_id"] == f"input_{suffix}"
    assert initial["recipe_revision"] == 1
    assert initial["chunk_count"] == 1

    await _chunk_set(client, document_id, recipe_id, second, f"ex_s_{suffix}")
    await client.activate_recipe_chunk_set(
        recipe_id=recipe_id,
        chunk_set_id=second,
        expected_base_chunk_set_id=first,
        extraction_recipe_id=f"ex_s_{suffix}",
    )

    # 旧版は清掃で消えても、履歴は残る（#4168）。
    assert await client.get_chunk_set(first) is not None
    await ChunkSetRetentionStore(client).expire_recipe(
        recipe_id, retention_days=0, retained_versions=0, canonical_prefix="artifacts/canonical"
    )
    assert await client.get_chunk_set(first) is None
    latest, oldest = await client.list_recipe_version_log(document_id, recipe_id)
    assert (latest["operation"], latest["chunk_set_id"], latest["parent_chunk_set_id"]) == (
        "REPROCESS",
        second,
        first,
    )
    assert oldest["log_id"] == initial["log_id"]

    # 同じ版をもう一度切り替えても行を増やさない。
    await client.activate_recipe_chunk_set(
        recipe_id=recipe_id, chunk_set_id=second, expected_base_chunk_set_id=second
    )
    assert len(await client.list_recipe_version_log(document_id, recipe_id)) == 2


@pytest.mark.usefixtures("oracle_db")
async def test_switch_is_rejected_for_a_run_that_lost_the_job() -> None:
    client = OracleClient()
    document_id, recipe_id = await _new_document(client, "activation-fence")
    suffix = uuid4().hex[:12]
    candidate = f"candidate_{suffix}"
    await _chunk_set(client, document_id, recipe_id, candidate, f"ex_c_{suffix}")
    job = await _running_job(client, document_id, recipe_id, lease_owner="worker-b")

    for fence in (
        IngestionJobFence(job_id=job.id, attempt_count=job.attempt_count, lease_owner="worker-a"),
        IngestionJobFence(
            job_id=job.id, attempt_count=job.attempt_count + 1, lease_owner="worker-b"
        ),
        IngestionJobFence(job_id="job-missing", attempt_count=1, lease_owner="worker-b"),
    ):
        with pytest.raises(RecipeActivationFencedError):
            await client.activate_recipe_chunk_set(
                recipe_id=recipe_id,
                chunk_set_id=candidate,
                expected_base_chunk_set_id=None,
                job_fence=fence,
            )
    assert await _active_chunk_set_id(client, document_id, recipe_id) is None
    assert await client.list_recipe_version_log(document_id, recipe_id) == []

    # 取り消された job の実行も切り替えない。
    await client.transition_ingestion_job(
        job.id,
        from_statuses=(IngestionJobStatus.RUNNING,),
        to_status=IngestionJobStatus.CANCELLED,
        finished_at=datetime.now(UTC),
    )
    with pytest.raises(RecipeActivationFencedError):
        await client.activate_recipe_chunk_set(
            recipe_id=recipe_id,
            chunk_set_id=candidate,
            expected_base_chunk_set_id=None,
            job_fence=IngestionJobFence(
                job_id=job.id, attempt_count=job.attempt_count, lease_owner="worker-b"
            ),
        )
    assert await _active_chunk_set_id(client, document_id, recipe_id) is None


@pytest.mark.usefixtures("oracle_db")
async def test_discard_removes_candidate_and_restores_extraction_pointer() -> None:
    client = OracleClient()
    document_id, recipe_id = await _new_document(client, "discard-candidate")
    suffix = uuid4().hex[:12]
    active, candidate = f"active_{suffix}", f"candidate_{suffix}"
    active_extraction, candidate_extraction = f"ex_a_{suffix}", f"ex_c_{suffix}"
    await _chunk_set(client, document_id, recipe_id, active, active_extraction)
    await client.activate_recipe_chunk_set(
        recipe_id=recipe_id,
        chunk_set_id=active,
        expected_base_chunk_set_id=None,
        extraction_recipe_id=active_extraction,
    )
    await _chunk_set(client, document_id, recipe_id, candidate, candidate_extraction)
    # 候補の解析がレシピの抽出の指す先を進めた状態。
    await client.update_document_recipe_status(
        recipe_id=recipe_id,
        status=FileStatus.INGESTING,
        active_extraction_recipe_id=candidate_extraction,
    )

    assert (
        await client.discard_recipe_candidate_chunk_set(recipe_id=recipe_id, chunk_set_id=active)
        is False
    )
    assert await client.discard_recipe_candidate_chunk_set(
        recipe_id=recipe_id, chunk_set_id=candidate
    )

    assert await client.get_chunk_set(candidate) is None
    assert await client.list_chunk_set_chunks(candidate) == []
    assert (
        await client.get_document_extraction_artifact(
            document_id=document_id, extraction_recipe_id=candidate_extraction
        )
        is None
    )
    recipe = await client.get_document_recipe(document_id, recipe_id)
    assert recipe is not None
    assert recipe["active_chunk_set_id"] == active
    assert recipe["active_extraction_recipe_id"] == active_extraction
    assert len(await client.list_chunk_set_chunks(active)) == 1
    assert (
        await client.get_document_extraction_artifact(
            document_id=document_id, extraction_recipe_id=active_extraction
        )
        is not None
    )
    # 繰り返しても何も変えない。
    assert (
        await client.discard_recipe_candidate_chunk_set(recipe_id=recipe_id, chunk_set_id=candidate)
        is False
    )


@pytest.mark.usefixtures("oracle_db")
async def test_superseded_job_keeps_skip_reason() -> None:
    client = OracleClient()
    document_id, recipe_id = await _new_document(client, "superseded-job")
    job = await _running_job(client, document_id, recipe_id, lease_owner="worker-a")

    skipped = await client.transition_ingestion_job(
        job.id,
        from_statuses=(IngestionJobStatus.RUNNING,),
        to_status=IngestionJobStatus.SKIPPED,
        finished_at=datetime.now(UTC),
        fence=IngestionJobFence(
            job_id=job.id, attempt_count=job.attempt_count, lease_owner="worker-a"
        ),
        skip_reason="superseded_by_newer_version",
    )

    assert skipped is not None
    assert skipped.status == IngestionJobStatus.SKIPPED
    assert skipped.skip_reason == "superseded_by_newer_version"
    stored = await client.get_ingestion_job(job.id)
    assert stored is not None and stored.skip_reason == "superseded_by_newer_version"


@pytest.mark.usefixtures("oracle_db")
async def test_document_delete_removes_its_log() -> None:
    client = OracleClient()
    document_id, recipe_id = await _new_document(client, "log-delete")
    suffix = uuid4().hex[:12]
    await _chunk_set(client, document_id, recipe_id, f"only_{suffix}", f"ex_{suffix}")
    await client.activate_recipe_chunk_set(
        recipe_id=recipe_id, chunk_set_id=f"only_{suffix}", expected_base_chunk_set_id=None
    )
    assert len(await client.list_recipe_version_log(document_id, recipe_id)) == 1

    await client.delete_document(document_id)

    assert await client.list_recipe_version_log(document_id, recipe_id) == []
