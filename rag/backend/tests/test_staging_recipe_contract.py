"""staging の正式job/版境界の固定合成契約。外部サービスは使わない。"""

from importlib import import_module
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.schemas.document import FileStatus


def module() -> Any:
    return import_module("app.rag.staging_recipes")


@pytest.mark.parametrize("key", ["secret", "oci_region", "model"])
def test_runtime_options_reject_other_fields(key: Any) -> None:
    with pytest.raises(ValidationError):
        module().StagingRuntimeOptions.model_validate({key: True})


async def test_collect_reads_active_version_not_document_legacy() -> None:
    class Oracle:
        async def list_document_recipes(self, document_id: Any) -> Any:
            return [{"recipe_id": "r1", "slot_no": 1}]

        async def get_document_recipe(self, document_id: Any, recipe_id: Any) -> Any:
            return {
                "recipe_id": "r1",
                "status": "INDEXED",
                "active_chunk_set_id": "cs2",
                "active_extraction_recipe_id": "wrong-current",
            }

        async def get_chunk_set(self, chunk_set_id: Any) -> Any:
            return {
                "document_id": "d1",
                "recipe_id": "r1",
                "status": "INDEXED",
                "extraction_recipe_id": "e2",
            }

        async def get_document_extraction_artifact(
            self, *, document_id: Any, extraction_recipe_id: Any
        ) -> Any:
            assert extraction_recipe_id == "e2"
            return {"extraction_json": {"value": "version2"}}

        async def list_chunk_set_chunks(self, chunk_set_id: Any) -> Any:
            assert chunk_set_id == "cs2"
            return ["new"]

        async def list_ingestion_segments(self, document_id: Any) -> Any:
            return [SimpleNamespace(recipe_id="r1"), SimpleNamespace(recipe_id="r2")]

    state = await module().read_staging_recipe(Oracle(), "d1")
    assert state.extraction == {"value": "version2"}
    assert state.chunks == ("new",)
    assert state.segments == ()  # receiptの無いcheckpointを版の根拠にしない。


async def test_missing_version_never_reads_document_fallback() -> None:
    class Oracle:
        async def list_document_recipes(self, document_id: Any) -> Any:
            return [{"recipe_id": "r1", "slot_no": 1}]

        async def get_document_recipe(self, document_id: Any, recipe_id: Any) -> Any:
            return {"recipe_id": "r1", "status": "INDEXED"}

        async def get_document(self, document_id: Any) -> Any:
            raise AssertionError("旧列を読んだ")

    with pytest.raises(RuntimeError, match="出力"):
        await module().read_staging_recipe(Oracle(), "d1")


def test_runtime_none_preserves_settings_identity() -> None:
    settings = Settings.model_construct()
    assert module().apply_staging_runtime(settings, {}) is settings


def test_runtime_options_roundtrip_is_bounded() -> None:
    options = module().StagingRuntimeOptions(parser_readiness_probe_enabled=True)
    settings = Settings.model_construct(rag_parser_readiness_probe_enabled=False)
    updated = module().apply_staging_runtime(
        settings, {"staging_runtime": options.model_dump(exclude_none=True)}
    )
    assert updated.rag_parser_readiness_probe_enabled is True
    assert settings.rag_parser_readiness_probe_enabled is False


async def test_review_state_is_not_promoted() -> None:
    class Oracle:
        async def list_document_recipes(self, document_id: Any) -> Any:
            return [{"recipe_id": "r1", "slot_no": 1}]

        async def get_document_recipe(self, document_id: Any, recipe_id: Any) -> Any:
            return {"recipe_id": "r1", "status": "REVIEW", "active_extraction_recipe_id": "e2"}

        async def get_document_extraction_artifact(self, **kwargs: Any) -> Any:
            return {"extraction_json": {"value": "pending"}}

        async def list_ingestion_segments(self, document_id: Any) -> Any:
            return []

    state = await module().read_staging_recipe(Oracle(), "d1")
    assert state.status == FileStatus.REVIEW
    assert state.chunk_set_id is None and state.chunks == ()


@pytest.mark.parametrize("terminal", ["SUCCEEDED", "FAILED", "CANCELLED", "SKIPPED"])
async def test_adapter_uses_formal_job_and_does_not_mask_failure(
    monkeypatch: Any, terminal: Any
) -> None:
    from datetime import UTC, datetime

    from app.api.routes import documents
    from app.schemas.document import DocumentDetail, IngestionJobStatus

    m = module()
    settings = Settings.model_construct()
    monkeypatch.setattr(m, "get_settings", lambda: settings)
    row = {"recipe_id": "r1", "status": "UPLOADED"}
    job = SimpleNamespace(id="j1", recipe_id="r1", status=IngestionJobStatus.QUEUED)
    detail = DocumentDetail(
        id="d1",
        file_name="synthetic.txt",
        status=FileStatus.UPLOADED,
        uploaded_at=datetime.now(UTC),
    )
    calls: list[Any] = []

    class Oracle:
        async def ensure_default_document_recipe(self, document_id: Any) -> Any:
            return row

        async def get_ingestion_job(self, job_id: Any) -> Any:
            return job

        async def list_document_ingestion_jobs(self, document_id: Any) -> Any:
            return [job]

        async def get_document(self, document_id: Any) -> Any:
            return detail

    async def enqueue(document_id: Any, recipe_id: Any, **options: Any) -> Any:
        calls.append(("enqueue", document_id, recipe_id, options))
        return job

    async def run(job_id: Any, **options: Any) -> Any:
        calls.append(("run", job_id, options))
        job.status = IngestionJobStatus(terminal)

    async def state(oracle: Any, document_id: Any) -> Any:
        return m.StagingRecipeState("r1", FileStatus.REVIEW, None, {}, (), ())

    monkeypatch.setattr(documents, "_enqueue_ingestion_job_for_recipe", enqueue)
    monkeypatch.setattr(documents, "_run_ingestion_job", run)
    monkeypatch.setattr(m, "read_staging_recipe", state)
    adapter = m.RecipeStagingIngestion(oracle=Oracle(), settings=settings)
    if terminal == "SUCCEEDED":
        result = await adapter.ingest("d1", b"source", "synthetic")
        assert result.status == FileStatus.REVIEW
        assert detail.status == FileStatus.UPLOADED
    else:
        with pytest.raises(RuntimeError, match=terminal):
            await adapter.ingest("d1", b"source", "synthetic")
    assert calls[0][1:3] == ("d1", "r1")
    assert calls[1] == ("run", "j1", {"propagate_errors": True})
    assert len(calls) == 2


async def test_worker_claim_race_has_bounded_wait(monkeypatch: Any) -> None:
    from app.api.routes import documents
    from app.schemas.document import IngestionJobStatus

    m = module()
    settings = Settings.model_construct(ingestion_job_subprocess_timeout_seconds=1)
    monkeypatch.setattr(m, "get_settings", lambda: settings)
    ticks = iter([0, 2])
    monkeypatch.setattr(m, "monotonic", lambda: next(ticks))
    job = SimpleNamespace(id="j1", recipe_id="r1", status=IngestionJobStatus.RUNNING)

    class Oracle:
        async def get_document(self, document_id: Any) -> Any:
            return SimpleNamespace(file_name="synthetic.pdf", content_type="application/pdf")

        async def ensure_default_document_recipe(self, document_id: Any) -> Any:
            return {"recipe_id": "r1"}

        async def get_ingestion_job(self, job_id: Any) -> Any:
            return job

    async def enqueue(*args: Any, **kwargs: Any) -> Any:
        return job

    async def run(*args: Any, **kwargs: Any) -> Any:
        return None

    monkeypatch.setattr(documents, "_enqueue_ingestion_job_for_recipe", enqueue)
    monkeypatch.setattr(documents, "_run_ingestion_job", run)
    with pytest.raises(TimeoutError):
        await m.RecipeStagingIngestion(oracle=Oracle(), settings=settings).ingest(
            "d1", b"a", "synthetic"
        )


@pytest.mark.parametrize("with_options", [False, True])
async def test_actual_enqueue_persists_only_typed_options(
    monkeypatch: Any, with_options: bool
) -> None:
    from app.api.routes import documents
    from app.schemas.document import IngestionJobPhase

    captured: dict[str, Any] = {}

    class Oracle:
        async def get_document_recipe(self, *args: Any) -> Any:
            return {"processing_config": {}, "config_revision": 1}

        async def get_document(self, *args: Any) -> Any:
            return SimpleNamespace(file_name="synthetic.pdf", content_type="application/pdf")

    settings = Settings.model_construct(rag_parser_readiness_probe_enabled=False)
    monkeypatch.setattr(documents, "OracleClient", Oracle)
    monkeypatch.setattr(
        documents,
        "_merge_document_processing_config",
        lambda config, **_kwargs: (settings, None),
    )
    monkeypatch.setattr(
        documents,
        "_source_profile_for_detail",
        lambda detail: SimpleNamespace(parser_profile="text", quality_warnings=[]),
    )

    async def allowed(*args: Any) -> None:
        return None

    async def create(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return SimpleNamespace(id="job")

    monkeypatch.setattr(documents, "_raise_if_parser_source_blocked", allowed)
    monkeypatch.setattr(documents, "_create_ingestion_job_record", create)
    monkeypatch.setattr(documents, "_dispatch_ingestion_job", lambda *args: None)
    options = (
        module().StagingRuntimeOptions(parser_readiness_probe_enabled=True)
        if with_options
        else None
    )
    await documents._enqueue_ingestion_job_for_recipe(
        "d1", "r1", phase=IngestionJobPhase.PREPROCESS, staging_runtime=options
    )
    overrides = captured["settings_overrides"]
    assert captured["recipe_id"] == "r1"
    if with_options:
        assert overrides["staging_runtime"] == {"parser_readiness_probe_enabled": True}
    else:
        assert set(overrides) == {
            "processing_config",
            "rag_preprocess_profile",
            "rag_parser_adapter_backend",
        }
    assert settings.rag_parser_readiness_probe_enabled is False


async def test_actual_job_keeps_runtime_options_for_auto_next_phase(monkeypatch: Any) -> None:
    from app.api.routes import documents
    from app.clients.object_storage import ObjectStorageClient
    from app.schemas.document import IngestionJobPhase, IngestionJobStatus
    from tests.language_release_support import install_language_release_stub
    from tests.test_document_recipes import (
        _extract_job,
        _FakeRecipeJobOracle,
        _FakeRecipeJobPipeline,
    )

    install_language_release_stub(monkeypatch)
    fake = _FakeRecipeJobOracle()
    job = _extract_job().model_copy(
        update={
            "settings_overrides": {
                "processing_config": {},
                "staging_runtime": {"parser_readiness_probe_enabled": True},
            }
        }
    )
    fake.jobs[job.id] = job
    await ObjectStorageClient().put("prepared/policy.pdf", b"prepared pdf bytes", "application/pdf")
    seen = []

    class Pipeline(_FakeRecipeJobPipeline):
        def __init__(self, *, oracle: Any, **kwargs: Any) -> None:
            seen.append(kwargs["settings"].rag_parser_readiness_probe_enabled)
            super().__init__(oracle=oracle, **kwargs)

    monkeypatch.setattr(documents, "OracleClient", lambda: fake)
    monkeypatch.setattr(documents, "IngestionPipeline", Pipeline)
    monkeypatch.setattr(
        documents,
        "get_settings",
        lambda: Settings(
            rag_auto_chunk_after_extract_enabled=True, rag_parser_readiness_probe_enabled=False
        ),
    )
    monkeypatch.setattr(documents, "_dispatch_ingestion_job", lambda *a, **k: None)
    await documents._run_ingestion_job(job.id, propagate_errors=True)
    assert fake.jobs[job.id].status == IngestionJobStatus.SUCCEEDED
    following = [j for j in fake.jobs.values() if j.phase == IngestionJobPhase.CHUNK]
    assert len(following) == 1
    assert following[0].settings_overrides is not None
    assert following[0].settings_overrides["staging_runtime"] == {
        "parser_readiness_probe_enabled": True
    }
    assert seen == [True]
