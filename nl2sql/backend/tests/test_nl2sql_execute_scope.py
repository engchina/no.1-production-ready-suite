"""POST /nl2sql/execute の実行スコープと row_limit 正規化の回帰テスト。

- 非 system admin の principal は、許可された業務プロファイル群の許可オブジェクトの
  和集合を越えて SELECT できない(Issue: /execute がプロファイルスコープを強制しない)
- `row_limit: null` は既定値へ倒し、無制限 fetchall にしない(Issue: row_limit null)
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.features.nl2sql import router as nl2sql_router
from app.features.nl2sql.incremental_store import MemoryIncrementalNl2SqlRepository
from app.features.nl2sql.models import (
    DIRECT_SQL_DEFAULT_ROW_LIMIT,
    AllowedObjects,
    ExecuteRequest,
    Nl2SqlProfile,
    SchemaCatalog,
    SchemaColumn,
    SchemaTable,
)
from app.features.nl2sql.service import Nl2SqlService
from app.features.nl2sql.store import MemoryNl2SqlStore
from app.security.domain import SYSTEM_ADMIN_ROLE_CODE, Principal


def _table(name: str) -> SchemaTable:
    # deterministic runtime の mock 実行は 4 列以上を前提にしているため列を揃える。
    return SchemaTable(
        owner="APP",
        table_name=name,
        logical_name=name,
        columns=[
            SchemaColumn(column_name="ID", logical_name="ID", data_type="NUMBER", nullable=False),
            SchemaColumn(
                column_name="NAME", logical_name="名称", data_type="VARCHAR2", nullable=True
            ),
            SchemaColumn(column_name="AMOUNT", logical_name="金額", data_type="NUMBER"),
            SchemaColumn(column_name="CREATED_AT", logical_name="作成日", data_type="DATE"),
        ],
    )


def _repository() -> MemoryIncrementalNl2SqlRepository:
    repository = MemoryIncrementalNl2SqlRepository(seed_default=False)
    catalog = SchemaCatalog(
        refreshed_at="2026-09-01T00:00:00+00:00",
        schema_fingerprint="execute-scope-v1",
        current_owner="APP",
        tables=[_table("ORDERS"), _table("INVOICES"), _table("SALARY")],
    )
    manifest = {
        (table.owner.upper(), table.table_name.upper()): catalog.refreshed_at
        for table in catalog.tables
    }
    repository.apply_schema_refresh(
        catalog=catalog,
        manifest=manifest,
        changed_keys=set(manifest),
        deleted_keys=set(),
    )
    repository.save_profile(
        Nl2SqlProfile(id="sales", name="販売", allowed_tables=["APP.ORDERS"]),
        expected_etag=None,
    )
    repository.save_profile(
        Nl2SqlProfile(id="finance", name="経理", allowed_tables=["APP.INVOICES"]),
        expected_etag=None,
    )
    return repository


def _service(repository: MemoryIncrementalNl2SqlRepository) -> Nl2SqlService:
    service = Nl2SqlService(store=MemoryNl2SqlStore())
    service._incremental_repository = repository  # noqa: SLF001 - white-box contract test
    service._refresh_job_repository = repository  # noqa: SLF001
    service._persistence_ready = True  # noqa: SLF001
    service._persistence_writable = True  # noqa: SLF001
    service._cache_token_poll_seconds = 0.0  # noqa: SLF001
    service._catalog = repository.load_catalog()  # noqa: SLF001
    return service


def _principal(allowed_profile_ids: set[str], *, admin: bool = False) -> Principal:
    return Principal(
        user_uuid="user-1",
        login_user_id="user1",
        display_name="利用者",
        status="ACTIVE",
        force_password_change=False,
        role_codes=[SYSTEM_ADMIN_ROLE_CODE] if admin else ["ANALYST"],
        permissions={"menu.query", "nl2sql.sql.execute"},
        data_entitlements=[],
        allowed_profile_ids=set(allowed_profile_ids),
        session_id="session-1",
        csrf_token_hash="csrf",
    )


def _request(principal: Principal | None) -> SimpleNamespace:
    return SimpleNamespace(state=SimpleNamespace(principal=principal))


def test_execute_request_null_row_limit_falls_back_to_default() -> None:
    assert (
        ExecuteRequest.model_validate({"sql": "SELECT 1 FROM DUAL", "row_limit": None}).row_limit
        == DIRECT_SQL_DEFAULT_ROW_LIMIT
    )
    assert ExecuteRequest(sql="SELECT 1 FROM DUAL").row_limit == DIRECT_SQL_DEFAULT_ROW_LIMIT
    assert ExecuteRequest(sql="SELECT 1 FROM DUAL", row_limit=5000).row_limit == 5000


@pytest.mark.parametrize("row_limit", [0, -1, 100001])
def test_execute_request_rejects_out_of_range_row_limit(row_limit: int) -> None:
    with pytest.raises(ValidationError):
        ExecuteRequest(sql="SELECT 1 FROM DUAL", row_limit=row_limit)


def test_direct_sql_scope_without_profile_restriction_keeps_request_scope() -> None:
    service = _service(_repository())

    allowed = service.resolve_direct_sql_allowed_objects(AllowedObjects())

    assert allowed.table_names == []
    assert allowed.enforce_table_scope is False


def test_direct_sql_scope_is_limited_to_allowed_profiles() -> None:
    service = _service(_repository())

    sales_only = service.resolve_direct_sql_allowed_objects(AllowedObjects(), profile_ids={"sales"})
    assert sales_only.table_names == ["APP.ORDERS"]
    assert sales_only.enforce_table_scope is True

    both = service.resolve_direct_sql_allowed_objects(
        AllowedObjects(), profile_ids={"sales", "finance"}
    )
    assert both.table_names == ["APP.INVOICES", "APP.ORDERS"]

    intersected = service.resolve_direct_sql_allowed_objects(
        AllowedObjects(table_names=["APP.ORDERS", "APP.INVOICES", "APP.SALARY"]),
        profile_ids={"sales"},
    )
    assert intersected.table_names == ["APP.ORDERS"]

    outside = service.resolve_direct_sql_allowed_objects(
        AllowedObjects(table_names=["APP.INVOICES"]), profile_ids={"sales"}
    )
    assert outside.table_names == []
    assert outside.enforce_table_scope is True

    unknown_only = service.resolve_direct_sql_allowed_objects(
        AllowedObjects(), profile_ids={"missing-profile"}
    )
    assert unknown_only.table_names == []
    assert unknown_only.enforce_table_scope is True

    empty = service.resolve_direct_sql_allowed_objects(AllowedObjects(), profile_ids=set())
    assert empty.table_names == []
    assert empty.enforce_table_scope is True


def test_execute_sql_blocks_tables_outside_profile_scope() -> None:
    service = _service(_repository())
    allowed = service.resolve_direct_sql_allowed_objects(AllowedObjects(), profile_ids={"sales"})

    blocked, _, blocked_results = service.execute_sql("SELECT ID FROM APP.INVOICES", allowed, 10)
    assert blocked.is_safe is False
    assert "許可されていない表" in blocked.blocked_reason
    assert blocked_results.total == 0

    permitted, _, _ = service.execute_sql("SELECT ID FROM APP.ORDERS", allowed, 10)
    assert permitted.is_safe is True


def test_execute_route_scopes_non_admin_principal_to_allowed_profiles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(_repository())
    monkeypatch.setattr(nl2sql_router, "nl2sql_service", service)

    with pytest.raises(HTTPException) as denied:
        nl2sql_router.execute(
            ExecuteRequest(sql="SELECT ID FROM APP.INVOICES"),
            _request(_principal({"sales"})),  # type: ignore[arg-type]
        )
    assert denied.value.status_code == 400
    assert "許可されていない表" in str(denied.value.detail)

    with pytest.raises(HTTPException) as no_profiles:
        nl2sql_router.execute(
            ExecuteRequest(sql="SELECT ID FROM APP.ORDERS"),
            _request(_principal(set())),  # type: ignore[arg-type]
        )
    assert no_profiles.value.status_code == 400

    permitted = nl2sql_router.execute(
        ExecuteRequest(sql="SELECT ID FROM APP.ORDERS"),
        _request(_principal({"sales"})),  # type: ignore[arg-type]
    )
    assert permitted.data is not None and permitted.data.columns


def test_execute_route_keeps_request_scope_for_admin_and_unauthenticated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(_repository())
    monkeypatch.setattr(nl2sql_router, "nl2sql_service", service)

    admin = nl2sql_router.execute(
        ExecuteRequest(sql="SELECT ID FROM APP.SALARY"),
        _request(_principal(set(), admin=True)),  # type: ignore[arg-type]
    )
    assert admin.data is not None and admin.data.columns

    unauthenticated = nl2sql_router.execute(
        ExecuteRequest(sql="SELECT ID FROM APP.SALARY"),
        _request(None),  # type: ignore[arg-type]
    )
    assert unauthenticated.data is not None and unauthenticated.data.columns
