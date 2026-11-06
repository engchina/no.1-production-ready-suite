"""job投入は選択した正式行だけを使い、未指定でも既存の正式行を必須にする。"""

from collections.abc import Mapping
from copy import deepcopy
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.clients.oracle import OracleClient
from app.schemas.document import IngestionJob, IngestionJobStatus
from tests.test_oracle_adapter import (
    FakeOracleConnection,
    FakeOracleCursor,
    FakeOraclePool,
    _oci_settings,
    _oracle_document_row,
    _run_inline,
)
from tests.test_oracle_db_paths import _default_recipe_id, _recipe_row


class JobConnection(FakeOracleConnection):
    """正式行の有無と旧seedを分けて観測する合成transaction。"""

    def __init__(
        self,
        row: dict[str, object] | None = None,
        *,
        visible: bool = True,
        active: bool = False,
        release: str = "synthetic-release",
    ) -> None:
        super().__init__([])
        self.row = deepcopy(row)
        self.initial = deepcopy(row)
        self.visible = visible
        self.active = active
        self.release = release

    def cursor(self) -> "JobCursor":
        return JobCursor(self)

    def rollback(self) -> None:
        super().rollback()
        self.row = deepcopy(self.initial)


class JobCursor(FakeOracleCursor):
    def __init__(self, connection: JobConnection) -> None:
        super().__init__(connection)
        self.state = connection

    def execute(self, statement: str, parameters: Mapping[str, object] | None = None) -> None:
        sql = " ".join(statement.split())
        binds = dict(parameters or {})
        if sql.startswith("MERGE INTO rag_document_recipes"):
            self.state.row = _recipe_row(status="UPLOADED", active_extraction_recipe_id=None)
        elif not sql.startswith(("SELECT", "INSERT INTO rag_ingestion_jobs")):
            raise AssertionError(f"想定外のSQL: {sql}")
        rows: list[dict[str, object]] = []
        if sql.startswith("SELECT"):
            if "FROM rag_document_recipes" in sql:
                row = self.state.row
                if (
                    self.state.visible
                    and row is not None
                    and row["document_id"] == binds["document_id"]
                    and (
                        row["recipe_id"] == binds["recipe_id"]
                        if "recipe_id" in binds
                        else row["slot_no"] == 1
                    )
                ):
                    rows = [dict(row)]
            elif "FROM rag_documents" in sql:
                rows = [_oracle_document_row(status="UPLOADED")] if self.state.visible else []
            elif "FROM rag_ingestion_jobs" in sql:
                rows = [{"job_id": "synthetic-active"}] if self.state.active else []
            elif "FROM rag_language_pack_state" in sql:
                rows = [{"current_release_id": self.state.release}]
            else:
                raise AssertionError(f"想定外のSELECT: {sql}")
            self.state._execute_results.append(rows)
        super().execute(statement, parameters)


def client_for(
    row: dict[str, object] | None = None,
    *,
    visible: bool = True,
    active: bool = False,
    release: str = "synthetic-release",
) -> tuple[OracleClient, JobConnection]:
    connection = JobConnection(row, visible=visible, active=active, release=release)
    pool = FakeOraclePool()
    pool.connection = connection
    return OracleClient(settings=_oci_settings(), pool=pool, db_call_runner=_run_inline), connection


def job_for(recipe_id: str | None, revision: int = 1) -> IngestionJob:
    return IngestionJob(
        id="synthetic-job",
        document_id="doc-1",
        status=IngestionJobStatus.QUEUED,
        parser_profile="local_text_structure",
        queued_at=datetime(2026, 1, 2, tzinfo=UTC),
        recipe_id=recipe_id,
        recipe_revision=revision if recipe_id else None,
    )


def assert_read_only(connection: JobConnection) -> None:
    assert all(call.statement.lstrip().startswith("SELECT") for call in connection.calls)
    assert not any("d.processing_config" in call.statement for call in connection.calls)
    assert connection.commits == 0 and connection.rollbacks == 1


@pytest.mark.parametrize(
    "case,recipe_id,visible,active,revision",
    [
        ("missing-opaque", "opaque", True, False, 1),
        ("missing-default", _default_recipe_id(), True, False, 1),
        ("missing-default-stale", _default_recipe_id(), True, False, 2),
        ("missing-default-active", _default_recipe_id(), True, True, 1),
        ("invisible-default", _default_recipe_id(), False, False, 1),
    ],
)
async def test_missing_selected_recipe_never_seeds(
    case: str, recipe_id: str, visible: bool, active: bool, revision: int
) -> None:
    client, connection = client_for(visible=visible, active=active)
    with pytest.raises(KeyError, match="レシピが見つかりません"):
        await client.create_ingestion_job(job_for(recipe_id, revision))
    assert connection.row is None
    assert_read_only(connection)


@pytest.mark.parametrize(
    "recipe_id,slot", [(_default_recipe_id(), 1), ("formal-slot-one", 1), ("formal-slot-two", 2)]
)
async def test_formal_selected_recipe_creates_only_job(recipe_id: str, slot: int) -> None:
    row = _recipe_row(recipe_id=recipe_id, slot_no=slot)
    client, connection = client_for(row)
    job = job_for(recipe_id)
    assert await client.create_ingestion_job(job) == job
    assert connection.row == row
    assert connection.commits == 1 and connection.rollbacks == 0
    writes = [call for call in connection.calls if not call.statement.lstrip().startswith("SELECT")]
    assert len(writes) == 1 and writes[0].statement.startswith("INSERT INTO rag_ingestion_jobs")
    assert writes[0].parameters["recipe_id"] == recipe_id
    lock = next(call for call in connection.calls if "FOR UPDATE OF r.recipe_id" in call.statement)
    assert "JOIN rag_documents d" in lock.statement
    assert lock.parameters["document_id"] == "doc-1" and lock.parameters["recipe_id"] == recipe_id
    assert not any("d.processing_config" in call.statement for call in connection.calls)


async def test_low_level_none_recipe_keeps_existing_compatibility() -> None:
    client, connection = client_for()
    job = job_for(None)
    assert await client.create_ingestion_job(job) == job
    assert connection.row is None
    assert connection.commits == 1
    assert len(connection.calls) == 2
    assert "FROM rag_documents" in connection.calls[0].statement
    assert connection.calls[1].statement.startswith("INSERT INTO rag_ingestion_jobs")
    assert connection.calls[1].parameters["recipe_id"] is None


async def test_unspecified_record_uses_existing_formal_recipe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api.routes import documents

    row = _recipe_row(recipe_id="formal-slot-one", slot_no=1, config_revision=4)
    client, connection = client_for(row)
    monkeypatch.setattr(
        documents, "active_release", lambda: SimpleNamespace(id="synthetic-release")
    )
    monkeypatch.setattr(
        documents, "get_settings", lambda: SimpleNamespace(ingestion_job_max_attempts=3)
    )
    job = await documents._create_ingestion_job_record(
        oracle=client,
        document_id="doc-1",
        parser_profile="local_text_structure",
        quality_warnings=[],
    )
    assert job.recipe_id == "formal-slot-one" and job.recipe_revision == 4
    assert connection.row == row
    writes = [call for call in connection.calls if not call.statement.lstrip().startswith("SELECT")]
    assert len(writes) == 1 and writes[0].statement.startswith("INSERT INTO rag_ingestion_jobs")
    assert writes[0].parameters["recipe_id"] == "formal-slot-one"
    assert writes[0].parameters["recipe_revision"] == 4
    assert not any("MERGE INTO rag_document_recipes" in call.statement for call in connection.calls)
    assert connection.commits == 2 and connection.rollbacks == 0


@pytest.mark.parametrize("visible,formal_exists", [(True, False), (False, False), (False, True)])
async def test_unspecified_record_requires_visible_formal_recipe(
    monkeypatch: pytest.MonkeyPatch, visible: bool, formal_exists: bool
) -> None:
    from app.api.routes import documents

    row = _recipe_row() if formal_exists else None
    client, connection = client_for(row, visible=visible)

    def unexpected_after_recipe() -> None:
        raise AssertionError("正式行を取得できない場合は後段へ進まない")

    monkeypatch.setattr(documents, "active_release", unexpected_after_recipe)
    monkeypatch.setattr(documents, "get_settings", unexpected_after_recipe)
    with pytest.raises(KeyError, match="既定の処理レシピが見つかりません"):
        await documents._create_ingestion_job_record(
            oracle=client,
            document_id="doc-1",
            parser_profile="local_text_structure",
            quality_warnings=[],
        )
    assert connection.row == row
    assert_read_only(connection)
    assert len(connection.calls) == 1
    assert "FROM rag_document_recipes" in connection.calls[0].statement
    assert "JOIN rag_documents d" in connection.calls[0].statement
    assert connection.calls[0].parameters["document_id"] == "doc-1"


@pytest.mark.parametrize("active,revision", [(True, 1), (False, 2)])
async def test_formal_job_active_and_revision_fences_remain(active: bool, revision: int) -> None:
    row = _recipe_row()
    client, connection = client_for(row, active=active)
    with pytest.raises(ValueError):
        await client.create_ingestion_job(job_for(_default_recipe_id(), revision))
    assert connection.row == row
    assert_read_only(connection)


@pytest.mark.parametrize("matches", [True, False])
async def test_language_current_release_fence_remains_before_recipe_lock(matches: bool) -> None:
    row = _recipe_row()
    client, connection = client_for(
        row, release="synthetic-release" if matches else "changed-release"
    )
    if matches:
        await client.create_ingestion_job(
            job_for(_default_recipe_id()), expected_current_release_id="synthetic-release"
        )
        assert connection.commits == 1
    else:
        with pytest.raises(ValueError, match="言語資源"):
            await client.create_ingestion_job(
                job_for(_default_recipe_id()), expected_current_release_id="synthetic-release"
            )
        assert len(connection.calls) == 1
        assert_read_only(connection)
    assert "rag_language_pack_state" in connection.calls[0].statement
    assert "FOR UPDATE" in connection.calls[0].statement


async def test_recipe_http_missing_maps_to_404_without_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi import HTTPException

    from app.api.routes import documents

    async def missing(*args: object, **kwargs: object) -> IngestionJob:
        raise KeyError("レシピが見つかりません。")

    monkeypatch.setattr(documents, "_enqueue_ingestion_job_for_recipe", missing)
    with pytest.raises(HTTPException) as caught:
        await documents.enqueue_document_recipe_job("doc-1", _default_recipe_id())
    assert caught.value.status_code == 404
