"""系统对象不进入用户可见目录的回归测试。"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import pytest

from app.features.nl2sql.incremental_store import MemoryIncrementalNl2SqlRepository
from app.features.nl2sql.models import (
    Nl2SqlProfile,
    SchemaCatalog,
    SchemaColumn,
    SchemaTable,
    SchemaViewDependency,
)
from app.features.nl2sql.object_visibility import (
    filter_user_visible_catalog,
    is_user_visible_object_name,
)
from app.features.nl2sql.oracle_adapter import OracleNl2SqlAdapter
from app.features.nl2sql.service import Nl2SqlService
from app.features.nl2sql.store import MemoryNl2SqlStore
from app.settings import get_settings


def _table(name: str, *, table_type: str = "table") -> SchemaTable:
    return SchemaTable(
        owner="APP",
        table_name=name,
        table_type=table_type,
        logical_name=name,
        columns=[
            SchemaColumn(
                column_name="ID",
                logical_name="ID",
                data_type="NUMBER",
                nullable=False,
            )
        ],
    )


def test_object_name_visibility_rejects_dollar_and_hash_markers() -> None:
    assert is_user_visible_object_name("ORDERS") is True
    assert is_user_visible_object_name("DBTOOLS$EXECUTION_HISTORY") is False
    assert is_user_visible_object_name("SYS#AUDIT") is False


def test_catalog_filter_removes_system_objects_and_dependencies() -> None:
    catalog = SchemaCatalog(
        refreshed_at="2026-07-22T00:00:00+00:00",
        tables=[
            _table("ORDERS"),
            _table("DBTOOLS$EXECUTION_HISTORY"),
            _table("SYS#AUDIT", table_type="view"),
        ],
        view_dependencies=[
            SchemaViewDependency(
                owner="APP",
                view_name="ORDER_VIEW",
                referenced_owner="APP",
                referenced_name="ORDERS",
                referenced_type="TABLE",
            ),
            SchemaViewDependency(
                owner="APP",
                view_name="ORDER_VIEW",
                referenced_owner="APP",
                referenced_name="DBTOOLS$EXECUTION_HISTORY",
                referenced_type="TABLE",
            ),
        ],
    )

    visible = filter_user_visible_catalog(catalog)

    assert [table.table_name for table in visible.tables] == ["ORDERS"]
    assert [item.referenced_name for item in visible.view_dependencies] == ["ORDERS"]


def test_memory_schema_pages_filter_before_counts_and_detail_lookup() -> None:
    repository = MemoryIncrementalNl2SqlRepository(seed_default=False)
    catalog = SchemaCatalog(
        refreshed_at="2026-07-22T00:00:00+00:00",
        tables=[
            _table("ORDERS"),
            _table("DBTOOLS$EXECUTION_HISTORY"),
            _table("SYS#AUDIT", table_type="view"),
        ],
    )
    repository.apply_schema_refresh(
        catalog=catalog,
        manifest={
            ("APP", "ORDERS"): "v1",
            ("APP", "DBTOOLS$EXECUTION_HISTORY"): "v1",
            ("APP", "SYS#AUDIT"): "v1",
        },
        changed_keys={
            ("APP", "ORDERS"),
            ("APP", "DBTOOLS$EXECUTION_HISTORY"),
            ("APP", "SYS#AUDIT"),
        },
        deleted_keys=set(),
    )

    page = repository.search_schema_objects(
        cursor=None,
        limit=100,
        query="",
        owner="APP",
        object_type="",
        allowed_names=None,
        row_state="all",
    )

    assert [item.object_name for item in page.items] == ["ORDERS"]
    assert (page.total, page.table_count, page.view_count) == (1, 1, 0)
    assert repository.get_catalog_head().object_count == 1
    assert repository.get_schema_object("APP", "SYS#AUDIT") is None


def test_oracle_admin_list_filters_both_markers_in_sql_and_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Cursor:
        executed = ""

        def __enter__(self) -> Cursor:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def execute(self, sql: str) -> None:
            self.executed = sql

        def fetchall(self) -> list[tuple[object, ...]]:
            return [
                ("ORDERS", "APP", 1, "受注"),
                ("DBTOOLS$EXECUTION_HISTORY", "APP", 4, "内部履歴"),
                ("SYS#AUDIT", "APP", 1, "内部監査"),
            ]

    class Connection:
        def __init__(self, cursor: Cursor) -> None:
            self._cursor = cursor

        def cursor(self) -> Cursor:
            return self._cursor

    cursor = Cursor()

    @contextmanager
    def connection() -> Iterator[Connection]:
        yield Connection(cursor)

    adapter = OracleNl2SqlAdapter(get_settings())
    monkeypatch.setattr(adapter, "connection", connection)

    items = adapter.list_db_admin_objects("table")

    assert [item["name"] for item in items] == ["ORDERS"]
    assert "NOT LIKE '%$%'" in cursor.executed
    assert "NOT LIKE '%#%'" in cursor.executed


def test_saved_profile_scope_does_not_expose_system_objects() -> None:
    service = Nl2SqlService(store=MemoryNl2SqlStore())
    service._profiles["business"] = Nl2SqlProfile(  # noqa: SLF001
        id="business",
        name="业务",
        object_scope_version=2,
        allowed_tables=["APP.ORDERS", "APP.DBTOOLS$EXECUTION_HISTORY"],
        allowed_views=["APP.SYS#AUDIT_VIEW"],
    )

    profile = service.get_profile("business")

    assert profile.allowed_tables == ["APP.ORDERS"]
    assert profile.allowed_views == []
    assert service.profile_allowed_object_names(profile) == ["APP.ORDERS"]
