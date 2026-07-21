"""Incremental NL2SQL state / lazy startup regression tests。"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from app.cli.nl2sql_migrate_state import (
    _decode_snapshot_value,
    _load_snapshot_cut,
    _migration_summary,
    _split_ddl,
    migrate_snapshot,
    validate_migrated_snapshot,
)
from app.features.nl2sql.incremental_store import (
    IncrementalVersionConflict,
    MemoryIncrementalNl2SqlRepository,
    OracleIncrementalNl2SqlRepository,
    VersionedTtlCache,
    _read_lob,
)
from app.features.nl2sql.models import (
    Nl2SqlProfile,
    SchemaCatalog,
    SchemaColumn,
    SchemaRefreshJob,
    SchemaRefreshJobStatus,
    SchemaTable,
    SchemaViewDependency,
)
from app.features.nl2sql.oracle_adapter import OracleNl2SqlAdapter
from app.features.nl2sql.service import Nl2SqlPersistenceUnavailable, Nl2SqlService
from app.features.nl2sql.store import MemoryNl2SqlStore
from app.settings import Settings


def _profile(index: int) -> Nl2SqlProfile:
    return Nl2SqlProfile(
        id=f"profile-{index:04d}",
        name=f"業務プロファイル {index:04d}",
        category="sales" if index % 2 else "finance",
        allowed_tables=[f"APP.TABLE_{index:04d}"],
    )


def _table(name: str, *, comment: str = "") -> SchemaTable:
    return SchemaTable(
        owner="APP",
        table_name=name,
        logical_name=name,
        comment=comment,
        columns=[
            SchemaColumn(
                column_name="ID",
                logical_name="ID",
                data_type="NUMBER",
                nullable=False,
            )
        ],
    )


class _LobPayload:
    """Scripted cursor が connection-bound LOB に変換する値。"""

    def __init__(self, value: str) -> None:
        self.value = value


class _ConnectionBoundLob:
    def __init__(self, connection: _ScriptedOracleConnection, value: str) -> None:
        self._connection = connection
        self._value = value

    def read(self) -> str:
        if self._connection.closed:
            raise RuntimeError("LOB locator is no longer valid")
        return self._value


class _ScriptedOracleCursor:
    def __init__(
        self,
        connection: _ScriptedOracleConnection,
        result_sets: list[list[tuple[Any, ...]]],
    ) -> None:
        self._connection = connection
        self._result_sets = result_sets
        self._current: list[tuple[Any, ...]] = []

    def __enter__(self) -> _ScriptedOracleCursor:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(self, _sql: str, _binds: Any = None) -> None:
        if not self._result_sets:
            raise AssertionError("scripted result set is missing")
        self._current = list(self._result_sets.pop(0))

    def fetchone(self) -> tuple[Any, ...] | None:
        if not self._current:
            return None
        return self._materialize(self._current.pop(0))

    def fetchall(self) -> list[tuple[Any, ...]]:
        rows = [self._materialize(row) for row in self._current]
        self._current = []
        return rows

    def _materialize(self, row: tuple[Any, ...]) -> tuple[Any, ...]:
        return tuple(
            _ConnectionBoundLob(self._connection, value.value)
            if isinstance(value, _LobPayload)
            else value
            for value in row
        )


class _ScriptedOracleConnection:
    def __init__(self, result_sets: list[list[tuple[Any, ...]]]) -> None:
        self.closed = False
        self._cursor = _ScriptedOracleCursor(self, result_sets)

    def cursor(self) -> _ScriptedOracleCursor:
        return self._cursor

    def commit(self) -> None:
        return None

    def rollback(self) -> None:
        return None


def _oracle_repository(
    *connection_results: list[list[tuple[Any, ...]]],
) -> tuple[OracleIncrementalNl2SqlRepository, list[_ScriptedOracleConnection]]:
    pending = list(connection_results)
    connections: list[_ScriptedOracleConnection] = []

    @contextmanager
    def connection_factory() -> Iterator[_ScriptedOracleConnection]:
        if not pending:
            raise AssertionError("scripted connection is missing")
        connection = _ScriptedOracleConnection(pending.pop(0))
        connections.append(connection)
        try:
            yield connection
        finally:
            connection.closed = True

    return OracleIncrementalNl2SqlRepository(connection_factory=connection_factory), connections


def _incremental_service(
    repository: Any,
) -> Nl2SqlService:
    service = Nl2SqlService(store=MemoryNl2SqlStore())
    service._incremental_repository = repository  # noqa: SLF001 - white-box contract test
    service._refresh_job_repository = repository  # noqa: SLF001
    service._persistence_ready = True  # noqa: SLF001
    service._persistence_writable = True  # noqa: SLF001
    service._cache_token_poll_seconds = 0.0  # noqa: SLF001
    return service


def test_incremental_migrations_backfill_before_not_null_constraints() -> None:
    migration_root = Path(__file__).resolve().parents[1] / "migrations"
    state_ddl = (migration_root / "003_incremental_nl2sql_state.sql").read_text(
        encoding="utf-8"
    )
    lease_ddl = (migration_root / "006_incremental_job_leases.sql").read_text(
        encoding="utf-8"
    )

    proposal_add = "ADD (PROFILE_ID VARCHAR2(128));"
    proposal_backfill = "UPDATE NL2SQL_ONTOLOGY_PROPOSALS proposal"
    proposal_constraint = "MODIFY (PROFILE_ID NOT NULL);"
    assert state_ddl.index(proposal_add) < state_ddl.index(proposal_backfill)
    assert state_ddl.index(proposal_backfill) < state_ddl.index(proposal_constraint)
    assert "PROFILE_ID VARCHAR2(128) DEFAULT '' NOT NULL" not in state_ddl

    lease_add = "ATTEMPT_NO NUMBER(10)"
    lease_backfill = "UPDATE NL2SQL_SCHEMA_REFRESH_JOBS"
    lease_constraint = "MODIFY (ATTEMPT_NO DEFAULT 0 NOT NULL);"
    assert lease_ddl.index(lease_add) < lease_ddl.index(lease_backfill)
    assert lease_ddl.index(lease_backfill) < lease_ddl.index(lease_constraint)
    assert "WORKER_ID VARCHAR2(256) DEFAULT '' NOT NULL" not in lease_ddl


def test_snapshot_cut_materializes_oracle_lob_before_connection_closes() -> None:
    class Connection:
        closed = False

        def cursor(self) -> Cursor:
            return Cursor(self)

    class ConnectionBoundLob:
        def __init__(self, connection: Connection) -> None:
            self.connection = connection

        def read(self) -> str:
            if self.connection.closed:
                raise RuntimeError("LOB locator is no longer valid")
            return '{"profiles":[]}'

    class Cursor:
        def __init__(self, connection: Connection) -> None:
            self.rows: list[tuple[Any, ...]] = [
                (ConnectionBoundLob(connection),),
                (7,),
            ]

        def __enter__(self) -> Cursor:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def execute(self, _sql: str, _binds: object | None = None) -> None:
            return None

        def fetchone(self) -> tuple[Any, ...]:
            return self.rows.pop(0)

    connection = Connection()

    @contextmanager
    def connect() -> Iterator[Connection]:
        try:
            yield connection
        finally:
            connection.closed = True

    class Adapter:
        connection = staticmethod(connect)

    snapshot, high_water = _load_snapshot_cut(
        Adapter(),  # type: ignore[arg-type]
        table_name="NL2SQL_STATE",
    )

    assert snapshot == {"profiles": []}
    assert high_water == 7
    assert connection.closed is True


def test_snapshot_decoder_accepts_oracle_native_json_mapping() -> None:
    snapshot = _decode_snapshot_value({"profiles": [{"id": "profile-1"}]})

    assert snapshot == {"profiles": [{"id": "profile-1"}]}


def test_oracle_profile_timestamp_bind_is_nls_independent() -> None:
    profile = _profile(1)
    updated_at = "2026-07-19T12:34:56+00:00"

    binds = OracleIncrementalNl2SqlRepository._profile_binds(  # noqa: SLF001
        profile,
        profile.model_dump(mode="json"),
        1,
        "etag",
        updated_at,
    )

    assert binds["updated_at"] == datetime.fromisoformat(updated_at)
    assert isinstance(binds["updated_at"], datetime)


def test_incremental_repository_accepts_oracle_native_json_values() -> None:
    assert json.loads(_read_lob({"items": ["値"]})) == {"items": ["値"]}
    assert json.loads(_read_lob([{"id": "one"}])) == [{"id": "one"}]


def test_oracle_profile_lobs_are_materialized_before_connection_closes() -> None:
    profile = _profile(1)
    payload = json.dumps(profile.model_dump(mode="json"), ensure_ascii=False)
    detail_repository, detail_connections = _oracle_repository(
        [[(_LobPayload(payload), 1, "etag-1", "2026-07-20T00:00:00+00:00")]]
    )
    list_repository, list_connections = _oracle_repository(
        [[(_LobPayload(payload), 1, "etag-1", "2026-07-20T00:00:00+00:00")]]
    )

    detail = detail_repository.get_profile(profile.id)
    profiles = list_repository.list_profiles(include_archived=True)

    assert detail is not None
    assert detail.id == profile.id
    assert [item.id for item in profiles] == [profile.id]
    assert all(connection.closed for connection in detail_connections + list_connections)


def test_oracle_state_document_lobs_are_materialized_before_connection_closes() -> None:
    payload = '{"id":"history-1","question":"部署名"}'
    detail_repository, detail_connections = _oracle_repository(
        [[(_LobPayload(payload),)]]
    )
    list_repository, list_connections = _oracle_repository(
        [[(_LobPayload(payload),)]]
    )
    page_repository, page_connections = _oracle_repository(
        [
            [(1,)],
            [
                (
                    _LobPayload(payload),
                    datetime(2026, 7, 20, tzinfo=UTC),
                    "history-1",
                )
            ],
        ]
    )

    detail = detail_repository.get_document("history", "history-1")
    documents = list_repository.list_documents("history", limit=10)
    page, next_cursor, total = page_repository.list_documents_page(
        "history", cursor=None, limit=10
    )

    assert detail == {"id": "history-1", "question": "部署名"}
    assert documents == [detail]
    assert page == [detail]
    assert next_cursor is None
    assert total == 1
    assert all(
        connection.closed
        for connection in detail_connections + list_connections + page_connections
    )


@pytest.mark.asyncio
async def test_similar_history_lob_read_does_not_block_following_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.features.nl2sql import router as nl2sql_router

    history_payload = json.dumps(
        {
            "id": "history-1",
            "question": "部署名を検索",
            "engine": "select_ai",
            "generated_sql": "SELECT DEPARTMENT_NAME FROM ADMIN.DEPARTMENT",
            "created_at": "2026-07-20T00:00:00+00:00",
            "feedback_rating": "good",
            "profile_id": "default",
            "profile_name": "標準プロファイル",
            "safety_is_safe": True,
        },
        ensure_ascii=False,
    )
    repository, connections = _oracle_repository(
        [
            [(1,)],
            [
                (
                    _LobPayload(history_payload),
                    datetime(2026, 7, 20, tzinfo=UTC),
                    "history-1",
                )
            ],
        ]
    )
    service = _incremental_service(repository)
    profile = Nl2SqlProfile(
        id="default",
        name="標準プロファイル",
        object_scope_version=2,
    )
    service._profile_cache.put(profile.id, profile)  # noqa: SLF001
    service._cache_token_checked_at["profiles"] = time.monotonic()  # noqa: SLF001
    service._cache_token_poll_seconds = 60.0  # noqa: SLF001
    service._deepsec_enabled = False  # noqa: SLF001
    monkeypatch.setattr(service._embedding_client, "is_configured", lambda: False)
    monkeypatch.setattr(service, "list_profiles", lambda include_archived=False: [profile])
    monkeypatch.setattr(service, "_persist_job", lambda _job_id: None)
    monkeypatch.setattr(service, "_run_job_safely", lambda _job_id: None)
    monkeypatch.setattr(nl2sql_router, "nl2sql_service", service)
    application = FastAPI()
    application.include_router(nl2sql_router.router, prefix="/api")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://test"
    ) as client:
        similar_response = await client.post(
            "/api/nl2sql/similar-history",
            json={"question": "部署名を検索", "profile_id": "default", "limit": 3},
        )
        persistence_after_similar = service.persistence_status()
        job_response = await client.post(
            "/api/nl2sql/jobs",
            json={"question": "部署名を検索", "engine": "select_ai", "profile_id": "default"},
        )

    assert similar_response.status_code == 200
    assert len(similar_response.json()["data"]["items"]) == 1
    assert persistence_after_similar.ready is True
    assert job_response.status_code == 200
    assert job_response.json()["data"]["status"] == "pending"
    assert connections[0].closed is True


def test_oracle_schema_lobs_are_materialized_before_connection_closes() -> None:
    constraint_payload = json.dumps(
        {
            "constraint_name": "PK_DEPARTMENT",
            "constraint_type": "P",
            "owner": "ADMIN",
            "table_name": "DEPARTMENT",
            "columns": ["DEPARTMENT_ID"],
        }
    )
    object_row = ("ADMIN", "DEPARTMENT", "TABLE", "部署", "部署情報", 10)
    column_row = (
        "ADMIN",
        "DEPARTMENT",
        "DEPARTMENT_ID",
        "部署ID",
        "NUMBER",
        False,
        "主キー",
        _LobPayload('["10"]'),
    )
    constraint_row = (
        "ADMIN",
        "DEPARTMENT",
        "PRIMARY KEY (DEPARTMENT_ID)",
        _LobPayload(constraint_payload),
    )
    head_row = (1, "fingerprint", "2026-07-20T00:00:00+00:00", 1, 1, "etag", 1)
    catalog_repository, catalog_connections = _oracle_repository(
        [[head_row]],
        [[object_row], [column_row], [constraint_row], []],
    )
    detail_repository, detail_connections = _oracle_repository(
        [
            [object_row],
            [(*column_row[2:7], column_row[7])],
            [(constraint_row[2], constraint_row[3])],
            [],
        ],
        [[head_row]],
    )

    catalog = catalog_repository.load_catalog()
    detail = detail_repository.get_schema_object("ADMIN", "DEPARTMENT")

    assert catalog.tables[0].columns[0].sample_values == ["10"]
    assert catalog.tables[0].constraint_details[0].constraint_name == "PK_DEPARTMENT"
    assert detail is not None
    assert detail.table.columns[0].sample_values == ["10"]
    assert detail.table.constraint_details[0].constraint_name == "PK_DEPARTMENT"
    assert all(connection.closed for connection in catalog_connections + detail_connections)


def test_oracle_refresh_job_lob_is_materialized_before_connection_closes() -> None:
    job = SchemaRefreshJob(
        job_id="refresh-1",
        created_at="2026-07-20T00:00:00+00:00",
    )
    repository, connections = _oracle_repository(
        [[(_LobPayload(job.model_dump_json()),)]]
    )

    restored = repository.get_refresh_job(job.job_id)

    assert restored == job
    assert connections[0].closed is True


def test_profile_repository_uses_cursor_summary_and_etag_conflict() -> None:
    repository = MemoryIncrementalNl2SqlRepository(seed_default=False)
    for index in range(120):
        repository.save_profile(_profile(index), expected_etag=None)

    first = repository.search_profiles(
        cursor=None,
        limit=50,
        query="業務",
        include_archived=False,
    )
    second = repository.search_profiles(
        cursor=first.next_cursor,
        limit=50,
        query="業務",
        include_archived=False,
    )

    assert len(first.items) == 50
    assert len(second.items) == 50
    assert first.total == 120
    assert set(item.id for item in first.items).isdisjoint(item.id for item in second.items)
    current = repository.get_profile(first.items[0].id)
    assert current is not None
    stored = repository.save_profile(
        current.model_copy(update={"description": "updated"}),
        expected_etag=current.etag,
    )
    assert stored.version == current.version + 1
    with pytest.raises(IncrementalVersionConflict):
        repository.save_profile(current, expected_etag=current.etag)


def test_state_document_page_uses_stable_keyset_cursor() -> None:
    repository = MemoryIncrementalNl2SqlRepository(seed_default=False)
    for index in range(5):
        repository.put_document("history", f"history-{index}", {"id": f"history-{index}"})

    first, cursor, first_total = repository.list_documents_page(
        "history", cursor=None, limit=2
    )
    assert [item["id"] for item in first] == ["history-4", "history-3"]
    assert cursor is not None
    assert first_total == 5

    repository.put_document("history", "history-5", {"id": "history-5"})
    second, _next_cursor, second_total = repository.list_documents_page(
        "history", cursor=cursor, limit=2
    )
    assert [item["id"] for item in second] == ["history-2", "history-1"]
    assert second_total == 6


def test_two_services_converge_through_change_token() -> None:
    repository = MemoryIncrementalNl2SqlRepository(seed_default=False)
    original = repository.save_profile(_profile(1), expected_etag=None)
    first = _incremental_service(repository)
    second = _incremental_service(repository)

    assert second.get_profile(original.id).description == ""
    updated = first.update_profile(
        original.id,
        lambda profile: profile.model_copy(update={"description": "new value"}),
        expected_etag=original.etag,
    )

    assert updated.description == "new value"
    assert second.get_profile(original.id).description == "new value"


def test_cache_is_used_only_inside_ttl_when_change_token_store_is_unreachable() -> None:
    class FailingTokenRepository(MemoryIncrementalNl2SqlRepository):
        fail_token = False

        def get_change_token(self, namespace: str) -> int:
            if self.fail_token:
                raise RuntimeError("database unavailable")
            return super().get_change_token(namespace)

    repository = FailingTokenRepository(seed_default=False)
    stored = repository.save_profile(_profile(2), expected_etag=None)
    service = _incremental_service(repository)
    service._profile_cache = VersionedTtlCache(  # noqa: SLF001
        max_entries=10,
        ttl_seconds=0.01,
        name="profile-test",
    )

    assert service.get_profile(stored.id).id == stored.id
    repository.fail_token = True
    assert service.get_profile(stored.id).id == stored.id
    time.sleep(0.02)
    with pytest.raises(Nl2SqlPersistenceUnavailable):
        service.get_profile(stored.id)


async def test_profile_api_supports_summary_detail_etag_and_conflict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.features.nl2sql import router as profile_router

    repository = MemoryIncrementalNl2SqlRepository(seed_default=False)
    stored = repository.save_profile(_profile(7), expected_etag=None)
    service = _incremental_service(repository)
    monkeypatch.setattr(profile_router, "nl2sql_service", service)
    monkeypatch.setattr(
        profile_router,
        "_materialize_incremental_profile_view",
        lambda _profile_id: None,
    )
    app = FastAPI()
    app.include_router(profile_router.router, prefix="/api")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        page_response = await client.get("/api/nl2sql/profiles/search?limit=10&q=0007")
        page_not_modified = await client.get(
            "/api/nl2sql/profiles/search?limit=10&q=0007",
            headers={"If-None-Match": page_response.headers["etag"]},
        )
        detail_response = await client.get(f"/api/nl2sql/profiles/{stored.id}")
        not_modified = await client.get(
            f"/api/nl2sql/profiles/{stored.id}",
            headers={"If-None-Match": detail_response.headers["etag"]},
        )
        missing_precondition = await client.patch(
            f"/api/nl2sql/profiles/{stored.id}", json={"name": "updated"}
        )
        conflict = await client.patch(
            f"/api/nl2sql/profiles/{stored.id}",
            headers={"If-Match": '"stale"'},
            json={"name": "updated"},
        )

    assert page_response.status_code == 200
    assert page_not_modified.status_code == 304
    assert page_response.json()["data"]["items"][0]["id"] == stored.id
    assert detail_response.status_code == 200
    assert detail_response.headers["etag"] == f'"{stored.etag}"'
    assert not_modified.status_code == 304
    assert missing_precondition.status_code == 428
    assert conflict.status_code == 409
    assert conflict.headers["etag"] == f'"{stored.etag}"'


async def test_schema_api_supports_page_and_detail_etag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.features.schema import router as schema_router

    repository = MemoryIncrementalNl2SqlRepository(seed_default=False)
    catalog = SchemaCatalog(refreshed_at="now", tables=[_table("ORDERS")])
    repository.apply_schema_refresh(
        catalog=catalog,
        manifest={("APP", "ORDERS"): "v1"},
        changed_keys={("APP", "ORDERS")},
        deleted_keys=set(),
    )
    service = _incremental_service(repository)
    monkeypatch.setattr(schema_router, "nl2sql_service", service)
    app = FastAPI()
    app.include_router(schema_router.router, prefix="/api")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        page = await client.get("/api/schema/objects?limit=10")
        page_304 = await client.get(
            "/api/schema/objects?limit=10",
            headers={"If-None-Match": page.headers["etag"]},
        )
        detail = await client.get("/api/schema/objects/APP/ORDERS")
        detail_304 = await client.get(
            "/api/schema/objects/APP/ORDERS",
            headers={"If-None-Match": detail.headers["etag"]},
        )

    assert page.status_code == 200
    assert page_304.status_code == 304
    assert detail.status_code == 200
    assert detail_304.status_code == 304


class _RefreshAdapter:
    def __init__(
        self,
        manifest: dict[tuple[str, str], str],
        changed_catalog: SchemaCatalog,
        *,
        fail: bool = False,
    ) -> None:
        self.manifest = manifest
        self.changed_catalog = changed_catalog
        self.fail = fail
        self.requested_keys: set[tuple[str, str]] = set()

    def fetch_schema_manifest(self) -> dict[tuple[str, str], str]:
        if self.fail:
            raise RuntimeError("manifest failed")
        return dict(self.manifest)

    def fetch_catalog_objects(self, keys: set[tuple[str, str]]) -> SchemaCatalog:
        self.requested_keys = set(keys)
        return self.changed_catalog.model_copy(deep=True)

    def catalog_fingerprint(self, catalog: SchemaCatalog) -> str:
        return "fingerprint:" + ",".join(sorted(table.table_name for table in catalog.tables))


def test_schema_refresh_is_incremental_and_deletes_missing_objects() -> None:
    repository = MemoryIncrementalNl2SqlRepository(seed_default=False)
    initial = SchemaCatalog(
        refreshed_at="before",
        tables=[_table("A"), _table("B"), _table("D")],
        view_dependencies=[
            SchemaViewDependency(
                owner="APP",
                view_name="D",
                referenced_owner="APP",
                referenced_name="A",
                referenced_type="TABLE",
            )
        ],
    )
    repository.apply_schema_refresh(
        catalog=initial,
        manifest={("APP", "A"): "v1", ("APP", "B"): "v1", ("APP", "D"): "v1"},
        changed_keys={("APP", "A"), ("APP", "B"), ("APP", "D")},
        deleted_keys=set(),
    )
    adapter = _RefreshAdapter(
        {("APP", "A"): "v2", ("APP", "C"): "v1", ("APP", "D"): "v1"},
        SchemaCatalog(
            refreshed_at="next",
            tables=[_table("A", comment="changed"), _table("C")],
        ),
    )
    service = _incremental_service(repository)
    service._oracle_adapter = adapter  # type: ignore[assignment]  # noqa: SLF001
    service._use_oracle_runtime = lambda: True  # type: ignore[method-assign]  # noqa: SLF001

    job = service.start_schema_refresh_job(dispatch=False)
    service._run_schema_refresh_job(job.job_id)  # noqa: SLF001

    completed = service.get_schema_refresh_job(job.job_id)
    assert completed is not None
    assert completed.status == SchemaRefreshJobStatus.DONE
    assert completed.changed_objects == 2
    assert completed.deleted_objects == 1
    assert adapter.requested_keys == {("APP", "A"), ("APP", "C")}
    catalog = repository.load_catalog()
    table_by_name = {table.table_name: table for table in catalog.tables}
    assert set(table_by_name) == {"A", "C", "D"}
    assert table_by_name["A"].comment == "changed"
    assert [(item.view_name, item.referenced_name) for item in catalog.view_dependencies] == [
        ("D", "A")
    ]


def test_failed_schema_refresh_keeps_previous_catalog() -> None:
    repository = MemoryIncrementalNl2SqlRepository(seed_default=False)
    initial = SchemaCatalog(refreshed_at="before", tables=[_table("A")])
    repository.apply_schema_refresh(
        catalog=initial,
        manifest={("APP", "A"): "v1"},
        changed_keys={("APP", "A")},
        deleted_keys=set(),
    )
    service = _incremental_service(repository)
    service._oracle_adapter = _RefreshAdapter(  # type: ignore[assignment]  # noqa: SLF001
        {}, SchemaCatalog(refreshed_at="", tables=[]), fail=True
    )
    service._use_oracle_runtime = lambda: True  # type: ignore[method-assign]  # noqa: SLF001

    job = service.start_schema_refresh_job(dispatch=False)
    service._run_schema_refresh_job(job.job_id)  # noqa: SLF001

    failed = service.get_schema_refresh_job(job.job_id)
    assert failed is not None
    assert failed.status == SchemaRefreshJobStatus.ERROR
    assert [table.table_name for table in repository.load_catalog().tables] == ["A"]
    assert repository.get_catalog_head().catalog_version == 1


def test_schema_refresh_job_reclaims_only_expired_lease() -> None:
    repository = MemoryIncrementalNl2SqlRepository(seed_default=False)
    pending = SchemaRefreshJob(job_id="refresh-1", created_at=datetime.now(UTC).isoformat())
    repository.save_refresh_job(pending)

    claimed = repository.claim_refresh_job(
        worker_id="worker-a", lease_seconds=300, job_id=pending.job_id
    )
    assert claimed is not None
    assert claimed.status == SchemaRefreshJobStatus.RUNNING
    assert claimed.attempt == 1
    assert (
        repository.claim_refresh_job(
            worker_id="worker-b", lease_seconds=300, job_id=pending.job_id
        )
        is None
    )

    repository.save_refresh_job(
        claimed.model_copy(
            update={
                "lease_expires_at": (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
            }
        )
    )
    reclaimed = repository.claim_refresh_job(
        worker_id="worker-b", lease_seconds=300, job_id=pending.job_id
    )
    assert reclaimed is not None
    assert reclaimed.worker_id == "worker-b"
    assert reclaimed.attempt == 2


def test_incremental_service_construction_does_not_open_oracle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.features.nl2sql import service as service_module

    settings = Settings(
        nl2sql_runtime_mode="oracle",
        nl2sql_persistence_mode="oracle",
        nl2sql_state_backend="incremental",
    )
    opened = 0

    def forbidden_connection(_self: Any) -> Any:
        nonlocal opened
        opened += 1
        raise AssertionError("constructor must not connect")

    monkeypatch.setattr(service_module, "get_settings", lambda: settings)
    monkeypatch.setattr(OracleNl2SqlAdapter, "connection", forbidden_connection)

    service = service_module.Nl2SqlService()

    assert service.uses_incremental_store is True
    assert opened == 0
    assert service.persistence_status().model_dump()["snapshot_loaded"] is False


async def test_fastapi_lifespan_does_not_bootstrap_security_or_load_business_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.main import lifespan
    from app.security.service import SecurityService

    calls = 0

    def forbidden_bootstrap(_self: SecurityService) -> None:
        nonlocal calls
        calls += 1
        raise AssertionError("security bootstrap belongs to the first login request")

    monkeypatch.setattr(SecurityService, "ensure_bootstrapped", forbidden_bootstrap)
    application = FastAPI()

    async with lifespan(application):
        assert application.state.services.nl2sql is not None

    assert calls == 0


def test_incremental_readiness_is_one_scalar_query_and_reads_no_clob() -> None:
    class Cursor:
        def __init__(self) -> None:
            self.executed: list[str] = []

        def __enter__(self) -> Cursor:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def execute(self, sql: str, _binds: Any = None) -> None:
            self.executed.append(sql)

        def fetchall(self) -> list[tuple[int]]:
            return [(3,), (5,), (6,)]

    class Connection:
        def __init__(self, cursor: Cursor) -> None:
            self._cursor = cursor

        def cursor(self) -> Cursor:
            return self._cursor

    cursor = Cursor()

    @contextmanager
    def connection_factory() -> Iterator[Connection]:
        yield Connection(cursor)

    repository = OracleIncrementalNl2SqlRepository(connection_factory=connection_factory)
    ready, _detail = repository.check()

    assert ready is True
    assert len(cursor.executed) == 1
    assert "NL2SQL_SCHEMA_MIGRATIONS" in cursor.executed[0]
    assert "CLOB" not in cursor.executed[0].upper()


def test_legacy_snapshot_migration_validates_aggregate_ids_and_payloads() -> None:
    repository = MemoryIncrementalNl2SqlRepository(seed_default=False)
    snapshot = {
        "profiles": [_profile(1).model_dump(mode="json")],
        "catalog": SchemaCatalog(
            refreshed_at="2026-07-19T00:00:00Z",
            tables=[_table("ORDERS")],
        ).model_dump(mode="json"),
        "history": [
            {
                "id": "history-1",
                "question": "受注件数は？",
                "generated_sql": "SELECT COUNT(*) FROM APP.ORDERS",
                "profile_id": "profile-0001",
            }
        ],
    }

    summary = migrate_snapshot(repository, snapshot)  # type: ignore[arg-type]
    validated = validate_migrated_snapshot(repository, snapshot)  # type: ignore[arg-type]

    assert summary["profiles"] == 1
    assert summary["schema_objects"] == 1
    assert validated["validated"] is True


def test_migration_dry_run_accepts_empty_snapshot_and_ddl_is_versioned() -> None:
    summary = _migration_summary({})
    assert summary["profiles"] == 0
    assert summary["schema_objects"] == 0
    statements = _split_ddl(
        "CREATE TABLE A (ID NUMBER);\nCREATE INDEX IX_A ON A (ID);"
    )
    assert len(statements) == 2
