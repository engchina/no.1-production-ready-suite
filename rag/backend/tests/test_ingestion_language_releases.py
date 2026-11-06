"""公開版をjob・各レシピの成果物・外部parserへ固定する。"""

import json
from datetime import UTC, datetime
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from rag_parser_core.language import builtin_pack, current_pack, pack_from_payload, resolve

from app.api.routes import documents
from app.clients import oracle as oracle_module
from app.rag.ingestion_language import (
    artifact_language_compatible,
    ingestion_release_job_id,
    ingestion_release_scope,
)
from app.rag.language_pack_releases import active_release
from app.schemas.document import IngestionJob, IngestionJobStatus
from app.schemas.language_pack_release import LanguagePackIndexRelation, LanguagePackRelease


def release(identity: str = "release-a", version: int = 1) -> LanguagePackRelease:
    pack = resolve(builtin_pack(), {"labels": {"default_document_type": "合成資料"}})
    return LanguagePackRelease(
        id=identity,
        version=version,
        effective=pack.to_dict(),
        hash=pack.hash,
        ingest_hash=pack.ingest_hash,
        published_at=datetime.now(UTC),
    )


def job(release_id: str | None = "release-a") -> IngestionJob:
    return IngestionJob(
        id="job-a",
        document_id="document-a",
        recipe_id="recipe-a",
        recipe_revision=1,
        status=IngestionJobStatus.RUNNING,
        parser_profile="generic",
        queued_at=datetime.now(UTC),
        language_pack_release_id=release_id,
    )


def test_parser_snapshot_uses_complete_values_and_rejects_missing_fields() -> None:
    pinned = release()
    parsed = pack_from_payload(json.dumps({"snapshot": pinned.effective}))
    assert parsed is not None and parsed.hash == pinned.hash
    with pytest.raises(ValueError):
        pack_from_payload({"snapshot": {}})


def test_persisted_job_release_roundtrips_without_inventing_legacy_version() -> None:
    row = oracle_module._ingestion_job_binds(job())
    assert row["language_pack_release_id"] == "release-a"
    assert oracle_module._ingestion_job_from_row(row).language_pack_release_id == "release-a"
    del row["language_pack_release_id"]
    assert oracle_module._ingestion_job_from_row(row).language_pack_release_id is None


@pytest.mark.parametrize("legacy", [False, True])
async def test_worker_and_followup_job_keep_pinned_release(
    monkeypatch: pytest.MonkeyPatch,
    legacy: bool,
) -> None:
    pinned = release()
    recorded: list[IngestionJob] = []

    class Oracle:
        async def claim_ingestion_job(self, *_args: Any, **_kwargs: Any) -> IngestionJob:
            return job(None if legacy else pinned.id)

        async def pin_ingestion_job_language_pack(self, _job: str, identity: str) -> str:
            assert identity == pinned.id
            return identity

        async def create_ingestion_job(self, item: IngestionJob) -> IngestionJob:
            recorded.append(item)
            return item

    class Store:
        def __init__(self, _oracle: Any) -> None:
            pass

        async def current(self) -> LanguagePackRelease:
            return pinned if legacy else release("new-current", 2)

        async def get(self, identity: str) -> LanguagePackRelease:
            assert identity == pinned.id
            return pinned

    async def run(oracle: Any, item: IngestionJob, **_kwargs: Any) -> None:
        assert active_release() == pinned and ingestion_release_job_id() == item.id
        assert current_pack().hash == pinned.hash
        await documents._create_ingestion_job_record(
            oracle=oracle,
            document_id=item.document_id,
            recipe_id=item.recipe_id,
            recipe_revision=1,
            parser_profile="generic",
            quality_warnings=[],
        )

    monkeypatch.setattr(documents, "OracleClient", Oracle)

    async def initialized(_oracle: Any = None) -> Store:
        return Store(_oracle)

    monkeypatch.setattr(documents, "initialized_release_store", initialized)
    monkeypatch.setattr(documents, "_run_pinned_ingestion_job", run)
    await documents._run_ingestion_job("job-a")
    assert len(recorded) == 1 and recorded[0].language_pack_release_id == pinned.id
    assert active_release() is None and ingestion_release_job_id() is None


async def test_manual_reprocess_rejects_changed_current_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Store:
        def __init__(self, _oracle: Any) -> None:
            pass

        async def current(self) -> LanguagePackRelease:
            return release("new-current", 2)

    async def initialized(_oracle: Any = None) -> Store:
        return Store(_oracle)

    monkeypatch.setattr(documents, "initialized_release_store", initialized)
    with pytest.raises(HTTPException) as raised:
        await documents._create_ingestion_job_record(
            oracle=cast(Any, AnyOracle()),
            document_id="document-a",
            recipe_id="recipe-a",
            recipe_revision=1,
            parser_profile="generic",
            quality_warnings=[],
            expected_language_pack_release_id="release-a",
        )
    assert raised.value.status_code == 409


class AnyOracle:
    pass


@pytest.mark.parametrize("relation", list(LanguagePackIndexRelation))
def test_relation_filter_keeps_access_and_one_shared_document_predicate(
    relation: LanguagePackIndexRelation,
) -> None:
    sql, binds = oracle_module._oracle_document_where(
        language_pack_relation=relation, current_release_id="release-a"
    )
    assert "rag_chunk_sets" in sql and "cs.is_active = 1" in sql
    assert "superseded_by_document_id IS NULL" in sql
    assert binds["current_language_release_id"] == "release-a"
    baseline, _ = oracle_module._oracle_document_where()
    assert sql.startswith(baseline + " AND ")


def test_release_scope_restores_after_failure() -> None:
    with pytest.raises(RuntimeError), ingestion_release_scope(release(), "job-a"):
        assert active_release() is not None
        raise RuntimeError("合成の失敗")
    assert active_release() is None and ingestion_release_job_id() is None


async def test_worker_release_resolution_failure_finishes_running_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failures: list[str] = []

    class Oracle:
        async def claim_ingestion_job(self, *_args: Any, **_kwargs: Any) -> IngestionJob:
            return job()

    async def unavailable(_oracle: Any = None) -> Any:
        raise RuntimeError("synthetic database failure")

    async def fail(_oracle: Any, item: IngestionJob, _message: str, **_kwargs: Any) -> bool:
        failures.append(item.id)
        return True

    monkeypatch.setattr(documents, "OracleClient", Oracle)
    monkeypatch.setattr(documents, "initialized_release_store", unavailable)
    monkeypatch.setattr(documents, "_fail_ingestion_job", fail)
    await documents._run_ingestion_job("job-a")
    assert failures == ["job-a"]
    assert active_release() is None


async def test_recipe_enqueue_forwards_expected_release(monkeypatch: pytest.MonkeyPatch) -> None:
    expected: list[str | None] = []

    async def enqueue(*_args: Any, **kwargs: Any) -> IngestionJob:
        expected.append(kwargs.get("expected_language_pack_release_id"))
        return job()

    monkeypatch.setattr(documents, "_enqueue_ingestion_job_for_recipe", enqueue)
    await documents.enqueue_document_recipe_job(
        "document-a", "recipe-a", expected_language_pack_release_id="release-a"
    )
    assert expected == ["release-a"]


@pytest.mark.parametrize(
    "section,key,phase,allowed",
    [
        ("entity_cues", "alias_column_words", "INDEX", True),
        ("inquiry_cues", "operation_trigger_terms", "INDEX", True),
        ("inquiry_cues", "operation_trigger_terms", "CHUNK", True),
        ("markers", "bullet_chars", "CHUNK", False),
    ],
)
async def test_partial_restart_compares_actual_artifact_release(
    monkeypatch: pytest.MonkeyPatch, section: str, key: str, phase: str, allowed: bool
) -> None:
    from app.clients import language_pack_release_store

    before = release()
    pack = resolve(builtin_pack(), {section: {key: ["合成の語"]}})
    # 基底も同じ完全設定にそろえ、差は指定した工程の語だけにする。
    base = builtin_pack()
    before = before.model_copy(
        update={"effective": base.to_dict(), "hash": base.hash, "ingest_hash": base.ingest_hash}
    )
    after = before.model_copy(
        update={
            "id": "release-b",
            "version": 2,
            "effective": pack.to_dict(),
            "hash": pack.hash,
            "ingest_hash": pack.ingest_hash,
        }
    )
    get = AsyncMock(return_value=before)
    monkeypatch.setattr(
        language_pack_release_store,
        "LanguagePackReleaseStore",
        lambda _oracle: type("Store", (), {"get": get})(),
    )
    provenance = {
        "language_pack_release_id": before.id,
        "language_pack_ingest_hash": before.ingest_hash,
    }
    with ingestion_release_scope(after, "job-b"):
        assert await artifact_language_compatible(cast(Any, object()), provenance, phase) is allowed
        assert not await artifact_language_compatible(
            cast(Any, object()), {"language_pack_ingest_hash": before.ingest_hash}, phase
        )
        assert not await artifact_language_compatible(
            cast(Any, object()), {**provenance, "language_pack_ingest_hash": "tampered"}, phase
        )


async def test_queued_only_recipe_reports_target_without_inventing_active_artifact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """未構築のレシピは公開版の推定ではなく、待機中ジョブの対象版だけを返す。"""
    current = release()
    client = oracle_module.OracleClient()
    fetch = AsyncMock(
        side_effect=[
            [],
            [
                {
                    "document_id": "document-a",
                    "recipe_id": "recipe-a",
                    "language_pack_release_id": "release-b",
                    "target_version": 7,
                },
                {
                    "document_id": "document-a",
                    "recipe_id": "recipe-a",
                    "language_pack_release_id": "release-older",
                    "target_version": 6,
                },
            ],
        ]
    )
    monkeypatch.setattr(client, "_fetch_all", fetch)
    monkeypatch.setattr(
        oracle_module,
        "LanguagePackReleaseStore",
        lambda _client: type("Store", (), {"get": AsyncMock(return_value=current)})(),
    )
    uses = await client.document_language_pack_uses(["document-a"], current_release_id=current.id)
    [used] = uses["document-a"].recipes
    assert used.chunk_set_id is None and used.release_id is None and used.ingest_hash is None
    assert used.version is None and used.built_at is None and used.job_id is None
    assert used.relation == LanguagePackIndexRelation.UNRECORDED
    assert used.target_release_id == "release-b"
    assert used.target_version == 7
    assert uses["document-a"].current_release_id == current.id
    assert "cs.is_active = 1" in fetch.call_args_list[0].args[0]
    assert "j.status IN ('QUEUED', 'RUNNING')" in fetch.call_args_list[1].args[0]
    assert "LEFT JOIN rag_language_pack_releases target" in fetch.call_args_list[1].args[0]


def _active_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "document_id": "document-a",
        "recipe_id": "recipe-a",
        "chunk_set_id": "chunks-a",
        "language_pack_release_id": "release-a",
        "language_pack_ingest_hash": None,
        "language_pack_job_id": "job-a",
        "language_pack_built_at": None,
        "version": 1,
        "target_release_id": None,
    }
    return {**row, **overrides}


def _mock_release_store(monkeypatch: pytest.MonkeyPatch, current: LanguagePackRelease) -> None:
    monkeypatch.setattr(
        oracle_module,
        "LanguagePackReleaseStore",
        lambda _client: type("Store", (), {"get": AsyncMock(return_value=current)})(),
    )


async def test_active_recipe_reports_target_version_from_pinned_job_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """既存の索引を持つレシピにも、構築中の job が固定した版の番号を付ける。"""
    current = release("release-b", 2)
    client = oracle_module.OracleClient()
    active = _active_row(language_pack_ingest_hash=current.ingest_hash + "-old")
    fetch = AsyncMock(
        side_effect=[
            [{**active, "target_release_id": "release-b"}],
            [
                {
                    "document_id": "document-a",
                    "recipe_id": "recipe-a",
                    "language_pack_release_id": "release-b",
                    "target_version": 2,
                }
            ],
        ]
    )
    monkeypatch.setattr(client, "_fetch_all", fetch)
    _mock_release_store(monkeypatch, current)
    uses = await client.document_language_pack_uses(["document-a"], current_release_id=current.id)
    [used] = uses["document-a"].recipes
    assert used.chunk_set_id == "chunks-a" and used.release_id == "release-a"
    assert used.version == 1
    assert used.relation == LanguagePackIndexRelation.DIFFERENT
    assert used.target_release_id == "release-b" and used.target_version == 2


async def test_active_recipe_without_ingest_hash_is_unrecorded_and_target_row_missing_is_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """取込設定の hash が無い索引は判定不可のまま返し、版の行が無い対象版の番号を推定しない。"""
    current = release("release-b", 2)
    client = oracle_module.OracleClient()
    fetch = AsyncMock(
        side_effect=[
            [_active_row(language_pack_ingest_hash=None, target_release_id="release-gone")],
            [
                {
                    "document_id": "document-a",
                    "recipe_id": "recipe-a",
                    "language_pack_release_id": "release-gone",
                    "target_version": None,
                }
            ],
        ]
    )
    monkeypatch.setattr(client, "_fetch_all", fetch)
    _mock_release_store(monkeypatch, current)
    uses = await client.document_language_pack_uses(["document-a"], current_release_id=current.id)
    [used] = uses["document-a"].recipes
    assert used.ingest_hash is None
    assert used.relation == LanguagePackIndexRelation.UNRECORDED
    assert used.target_release_id == "release-gone" and used.target_version is None


def _unreadable_store(monkeypatch: pytest.MonkeyPatch, *, raise_on_get: bool) -> None:
    """現在の契約で読めない旧版を返す保存先（読むときに失敗するか、返した版の照合に失敗する）。"""
    from app.clients import language_pack_release_store
    from app.rag.language_pack_contract import LanguagePackSnapshotError

    legacy = release("release-legacy", version=1)
    # 旧い版は項目が欠けている（合成）。hash はそのまま残し、照合の失敗を再現する。
    effective = dict(legacy.effective)
    effective.pop(next(iter(effective)))
    legacy = legacy.model_copy(update={"effective": effective})

    async def get(_identity: str) -> LanguagePackRelease:
        if raise_on_get:
            raise LanguagePackSnapshotError("保存済み言語資源の項目が現在の契約に合いません。")
        return legacy

    monkeypatch.setattr(
        language_pack_release_store,
        "LanguagePackReleaseStore",
        lambda _oracle: type("Store", (), {"get": staticmethod(get)})(),
    )


@pytest.mark.parametrize("raise_on_get", [True, False], ids=["store-rejects", "pack-rejects"])
@pytest.mark.parametrize("phase", ["CHUNK", "INDEX"])
async def test_unreadable_artifact_release_is_not_reusable(
    monkeypatch: pytest.MonkeyPatch, raise_on_get: bool, phase: str
) -> None:
    """現在の契約で読めない版の成果物は、例外にせず再利用できないと判定する。"""
    _unreadable_store(monkeypatch, raise_on_get=raise_on_get)
    legacy = release("release-legacy", version=1)
    provenance = {
        "language_pack_release_id": legacy.id,
        "language_pack_ingest_hash": legacy.ingest_hash,
    }
    with ingestion_release_scope(release("release-b", version=2), "job-b"):
        assert not await artifact_language_compatible(cast(Any, object()), provenance, phase)


async def test_chunk_reuse_of_unreadable_release_asks_to_reprocess_from_extraction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CHUNK の再開で抽出の版が読めないとき、汎用の失敗ではなく文書解析からの再処理を示す。"""
    from app.rag.ingestion import IngestionUserError
    from app.schemas.document import DocumentDetail, FileStatus, IngestionJobPhase

    _unreadable_store(monkeypatch, raise_on_get=True)
    legacy = release("release-legacy", version=1)

    class Oracle:
        async def get_document(self, document_id: str) -> DocumentDetail:
            return DocumentDetail(
                id=document_id,
                file_name="synthetic.pdf",
                status=FileStatus.INDEXED,
                content_sha256="a" * 64,
                content_type="application/pdf",
                object_storage_path="local://synthetic.pdf",
                uploaded_at=datetime.now(UTC),
            )

        async def get_document_recipe(self, document_id: str, recipe_id: str) -> dict[str, Any]:
            return {
                "document_id": document_id,
                "recipe_id": recipe_id,
                "active_chunk_set_id": "chunk-set-legacy",
                "active_extraction_recipe_id": "extraction-legacy",
            }

        async def update_document_recipe_status(self, **_kwargs: Any) -> None:
            return None

        async def upsert_chunk_set(self, **_kwargs: Any) -> None:
            return None

        async def get_document_extraction_artifact(self, **_kwargs: Any) -> dict[str, Any]:
            return {
                "extraction_json": {
                    "parser_artifacts": {
                        "language_pack_release_id": legacy.id,
                        "language_pack_ingest_hash": legacy.ingest_hash,
                    }
                }
            }

    monkeypatch.setattr(documents, "IngestionPipeline", lambda **_kwargs: object())
    chunk_job = job().model_copy(
        update={"phase": IngestionJobPhase.CHUNK, "settings_overrides": {"processing_config": {}}}
    )
    with (
        ingestion_release_scope(release("release-b", version=2), chunk_job.id),
        pytest.raises(IngestionUserError) as caught,
    ):
        await documents._materialize_experiment_candidate(cast(Any, Oracle()), chunk_job)
    message = documents._safe_ingestion_job_error_message(caught.value)
    assert "文書解析から再処理してください" in message
