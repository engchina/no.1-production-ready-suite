"""正式レシピ欠落時に旧文書の成果物から仮のレシピを作らない。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.api.routes import documents as routes
from app.clients.oracle import OracleClient, _default_document_recipe_values
from app.config import Settings
from app.rag.staging_recipes import RecipeStagingIngestion
from app.schemas.document import FileStatus, IngestionJobPhase
from tests.test_oracle_adapter import FakeOraclePool, _oci_settings, _run_inline
from tests.test_oracle_db_paths import _recipe_row, _statements, _write_statements


@pytest.mark.parametrize(
    ("legacy_status", "artifact"),
    [
        ("INDEXED", {"object_storage_path": "synthetic/old-prepared"}),
        ("INDEXED", None),
        ("UPLOADED", None),
        (None, None),
    ],
)
async def test_missing_formal_recipe_never_reads_or_copies_legacy_document(
    legacy_status: str | None, artifact: dict[str, str] | None
) -> None:
    legacy: dict[str, object] = {
        "document_id": "doc-1",
        "status": legacy_status,
        "preprocess_artifact": artifact,
    }
    pool = FakeOraclePool(execute_results=[[], [legacy], [_recipe_row()]])
    client = OracleClient(settings=_oci_settings(), pool=pool, db_call_runner=_run_inline)

    with pytest.raises(KeyError, match="既定の処理レシピが見つかりません"):
        await client.ensure_default_document_recipe("doc-1")

    assert len(_statements(pool)) == 1
    assert _write_statements(pool) == []
    assert pool.connection.commits == 0 and pool.connection.rollbacks == 1
    assert "FROM rag_document_recipes" in _statements(pool)[0]
    assert "JOIN rag_documents" in _statements(pool)[0]
    assert "slot_no = 1" in _statements(pool)[0]


async def test_formal_recipe_does_not_read_old_document_or_write() -> None:
    formal = _recipe_row(preprocess_artifact={"object_storage_path": "synthetic/formal"})
    pool = FakeOraclePool(execute_results=[[formal]])
    client = OracleClient(settings=_oci_settings(), pool=pool, db_call_runner=_run_inline)
    result = await client.ensure_default_document_recipe("doc-1")
    assert result["preprocess_artifact"] == formal["preprocess_artifact"]
    assert len(_statements(pool)) == 1 and _write_statements(pool) == []
    assert pool.connection.commits == 1 and pool.connection.rollbacks == 0


def test_new_upload_seed_remains_fresh_without_legacy_outputs() -> None:
    result = _default_document_recipe_values({"document_id": "synthetic-new", "status": "UPLOADED"})
    assert result["status"] == "UPLOADED"
    assert "preprocess_artifact" not in result
    assert result["materialized_revision"] is None
    assert result["active_extraction_recipe_id"] is None


@pytest.mark.parametrize("caller", ["recipe-create", "staging"])
async def test_missing_recipe_stops_real_creation_or_staging_before_later_writes(
    caller: str,
) -> None:
    pool = FakeOraclePool(execute_results=[[]])
    client = OracleClient(settings=_oci_settings(), pool=pool, db_call_runner=_run_inline)
    with pytest.raises(KeyError, match="既定の処理レシピが見つかりません"):
        if caller == "recipe-create":
            await client.create_document_recipe("doc-1")
        else:
            await RecipeStagingIngestion(oracle=client, settings=Settings.model_construct()).ingest(
                "doc-1", b"synthetic"
            )
    assert len(_statements(pool)) == 1 and _write_statements(pool) == []
    assert pool.connection.rollbacks == 1 and pool.connection.commits == 0


@pytest.mark.parametrize("phase", [IngestionJobPhase.PREPROCESS, IngestionJobPhase.EXTRACT])
async def test_real_enqueue_stops_missing_recipe_before_reset_or_dispatch(
    monkeypatch: pytest.MonkeyPatch, phase: IngestionJobPhase
) -> None:
    pool = FakeOraclePool(execute_results=[[]])
    client = OracleClient(settings=_oci_settings(), pool=pool, db_call_runner=_run_inline)
    monkeypatch.setattr(
        client,
        "get_document",
        AsyncMock(
            return_value=SimpleNamespace(
                object_storage_path="synthetic",
                status=FileStatus.UPLOADED,
                duplicate_of_document_id=None,
            )
        ),
    )
    monkeypatch.setattr(routes, "OracleClient", lambda: client)
    monkeypatch.setattr(routes, "_raise_if_default_recipe_job_active", AsyncMock())
    monkeypatch.setattr(
        routes,
        "_source_profile_for_detail",
        lambda _: SimpleNamespace(
            unsupported_reason=None,
            parser_profile="synthetic",
            quality_warnings=[],
        ),
    )
    # 設定解決は別の責務。実ensureを通るEXTRACT/served分岐までの前提だけ固定する。
    monkeypatch.setattr(routes, "_default_recipe_settings", AsyncMock(return_value=object()))
    monkeypatch.setattr(routes, "_raise_if_parser_source_blocked", AsyncMock())
    monkeypatch.setattr(routes, "_has_served_output", AsyncMock(return_value=True))
    reset, create, dispatch = AsyncMock(), AsyncMock(), AsyncMock()
    monkeypatch.setattr(routes, "_reset_document_outputs_for_extract", reset)
    monkeypatch.setattr(routes, "_create_ingestion_job_record", create)
    monkeypatch.setattr(routes, "_dispatch_ingestion_job", dispatch)
    with pytest.raises(KeyError, match="既定の処理レシピが見つかりません"):
        await routes._enqueue_ingestion_job_for_document("doc-1", force=True, phase=phase)
    reset.assert_not_called()
    create.assert_not_called()
    dispatch.assert_not_called()
    assert len(_statements(pool)) == 1 and _write_statements(pool) == []
