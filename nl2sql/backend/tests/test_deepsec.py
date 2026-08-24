"""DeepSec V001 registry と connection context lifecycle。"""

from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path

import pytest

from app.clients.oracle_runtime import OraclePoolManager
from app.features.nl2sql.oracle_adapter import OracleAdapterError
from app.security.deepsec import (
    DEEPSEC_APPLY_CONFIRMATION,
    DEEPSEC_RESET_CONFIRMATION,
    PASSWORD_PLACEHOLDER,
    DeepSecService,
    OracleManagedDataGrant,
    build_data_entitlement_statements,
    build_v001_plan,
    build_v001_reset_statements,
)
from app.security.domain import (
    DataEntitlementRecord,
    DataEntitlementScopeFilter,
    Principal,
    RoleRecord,
    scope_filters_scope_code,
)
from app.security.service import SecurityApiError, SecurityService
from app.security.store import InMemorySecurityStore
from app.settings import Settings


def _settings(
    *,
    driver_mode: str = "thin",
    connection_security: str = "walletless_tls",
    deepsec_enabled: bool = True,
    data_user_password: str = "DeepSecret!123",
    wallet_dir: str = "",
) -> Settings:
    return Settings.model_construct(
        oracle_user="APP_OWNER",
        oracle_password="ControlPass!123",
        app_admin_login_user_id="system_admin",
        app_admin_password="AppAdminPass123",
        oracle_dsn="test",
        oracle_driver_mode=driver_mode,
        oracle_connection_security=connection_security,
        oracle_client_lib_dir="/opt/oracle/instantclient",
        oracle_wallet_dir=wallet_dir,
        oracle_wallet_password="",
        oracle_deepsec_enabled=deepsec_enabled,
        oracle_deepsec_data_user="DEEPSEC_DATA_USER",
        oracle_deepsec_data_user_password=data_user_password,
        nl2sql_persistence_mode="memory",
        app_auth_password_min_length=12,
        app_auth_password_max_length=128,
    )


def _principal() -> Principal:
    return Principal(
        user_uuid="actor",
        login_user_id="actor",
        display_name="actor",
        status="ACTIVE",
        force_password_change=False,
        role_codes=["SYSTEM_ADMIN"],
        permissions=set(),
        data_entitlements=[],
        session_id="session",
        csrf_token_hash="csrf",
    )


def _data_grant_checksum_for_test(statements: tuple[str, ...]) -> str:
    payload = "\n-- statement --\n".join(statement.strip() for statement in statements)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _mark_deepsec_steps_applied(store: InMemorySecurityStore, settings: Settings) -> None:
    for step in build_v001_plan(settings):
        store.set_deepsec_state(
            version="V001",
            step_no=step.step_no,
            step_key=step.key,
            checksum=step.checksum,
            status="APPLIED",
            error_message="",
            executed_by_user_uuid="actor",
        )
    for step_no, step_key in ((3, "legacy_verification_object"), (4, "legacy_data_grants")):
        store.set_deepsec_state(
            version="V001",
            step_no=step_no,
            step_key=step_key,
            checksum=f"legacy-{step_no}",
            status="APPLIED",
            error_message="",
            executed_by_user_uuid="actor",
        )


def _insert_real_entitlement_role(
    store: InMemorySecurityStore,
    *,
    apply_status: str = "PENDING",
    data_grant_name: str = "",
) -> RoleRecord:
    role = RoleRecord(
        role_id="role-sales",
        role_code="SALES_ANALYST",
        display_name="営業分析",
        description="営業テーブルを参照するロール",
        is_built_in=False,
        archived=False,
        version=1,
        permissions=set(),
        entitlements=[
            DataEntitlementRecord(
                entitlement_id="entitlement-sales",
                role_id="role-sales",
                resource_code="HR.EMPLOYEES",
                scope_code="SALES",
                capability="SELECT",
                target_owner="HR",
                target_object="EMPLOYEES",
                target_type="TABLE",
                column_names=["EMPLOYEE_ID", "DISPLAY_NAME"],
                scope_mode="COLUMN_EQUALS",
                scope_column="DEPARTMENT_CODE",
                data_grant_name=data_grant_name,
                apply_status=apply_status,
            )
        ],
    )
    store.roles[role.role_id] = role
    return role


def test_v001_registry_is_stable_and_preview_never_contains_secret() -> None:
    settings = _settings()
    first = build_v001_plan(settings)
    second = build_v001_plan(settings)
    assert [step.checksum for step in first] == [step.checksum for step in second]
    preview = "\n".join(statement for step in first for statement in step.statements)
    assert PASSWORD_PLACEHOLDER in preview
    assert settings.oracle_deepsec_data_user_password not in preview


def test_v001_plan_contains_foundation_only_without_probe_flow() -> None:
    plan = build_v001_plan(_settings())
    preview = "\n".join(statement for step in plan for statement in step.statements)

    assert [step.step_no for step in plan] == [1, 2]
    assert "NL2SQL_DEEPSEC_PROBE" not in preview
    assert "CREATE TABLE" not in preview
    assert "CREATE DATA ROLE IF NOT EXISTS NL2SQL_APP_DATA_ROLE" in preview
    assert "GRANT SELECT ON APP_OWNER.NL2SQL_APP_USER_ROLES TO NL2SQL_APP_DB_ROLE" in preview
    assert "GRANT SELECT ON APP_OWNER.NL2SQL_APP_ROLES TO NL2SQL_APP_DB_ROLE" in preview
    assert "GRANT SELECT ON APP_OWNER.NL2SQL_APP_DATA_ENTITLEMENTS TO NL2SQL_APP_DB_ROLE" in preview
    assert "GRANT NL2SQL_APP_DB_ROLE TO NL2SQL_APP_DATA_ROLE" in preview
    assert "GRANT DATA ROLE NL2SQL_APP_DATA_ROLE TO DEEPSEC_DATA_USER" in preview


def test_verify_fails_when_predicate_table_grants_are_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class StatusCursor:
        def __init__(self) -> None:
            self.sql = ""
            self.params: dict[str, object] = {}

        def __enter__(self) -> StatusCursor:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def execute(self, sql: str, params: dict[str, object] | None = None) -> None:
            self.sql = sql
            self.params = dict(params or {})

        def fetchone(self) -> tuple[int]:
            if "DBA_TAB_PRIVS" not in self.sql:
                return (1,)
            table_name = str(self.params.get("table_name") or "")
            return (0,) if table_name == "NL2SQL_APP_USER_ROLES" else (1,)

        def fetchall(self) -> list[tuple[object, ...]]:
            return []

    class StatusConnection:
        def cursor(self) -> StatusCursor:
            return StatusCursor()

        def close(self) -> None:
            return None

    class StatusPool:
        def acquire(self) -> StatusConnection:
            return StatusConnection()

    settings = _settings()
    store = InMemorySecurityStore()
    security = SecurityService(store, settings)
    security.bootstrap()
    manager = OraclePoolManager(settings)
    monkeypatch.setattr(manager, "_get_pool", lambda *, data_plane: StatusPool())
    service = DeepSecService(settings, security, manager)

    status = service.status()
    result = service.verify(_principal())

    assert status["configured"] is False
    assert status["objects"]["predicate_user_roles_grant"] == 0
    assert result["passed"] is False
    predicate_check = next(
        item for item in result["checks"] if item["key"] == "predicate_table_grants"
    )
    assert predicate_check["passed"] is False
    assert "NL2SQL_APP_USER_ROLES" in predicate_check["detail"]


@pytest.mark.parametrize(
    ("grantee", "grantee_type", "expected_passed"),
    [
        ("NL2SQL_APP_DATA_ROLE", "DATA ROLE", True),
        ("DEEPSEC_DATA_USER", "END USER", False),
    ],
)
def test_verify_requires_managed_data_grant_on_data_role(
    monkeypatch: pytest.MonkeyPatch,
    grantee: str,
    grantee_type: str,
    expected_passed: bool,
) -> None:
    class VerifyCursor:
        def __init__(self) -> None:
            self.sql = ""

        def __enter__(self) -> VerifyCursor:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def execute(self, sql: str, _params: dict[str, object] | None = None) -> None:
            self.sql = sql

        def fetchall(self) -> list[tuple[object, ...]]:
            if "DBA_POLICIES" in self.sql:
                return []
            predicate = "ORA_END_USER_CONTEXT.CLIENT_IDENTIFIER = 'actor'"
            return [
                (
                    "NL2SQL_DG_VERIFY",
                    "HR",
                    "EMPLOYEES",
                    grantee,
                    grantee_type,
                    True,
                    predicate,
                )
            ]

    class VerifyConnection:
        def cursor(self) -> VerifyCursor:
            return VerifyCursor()

        def close(self) -> None:
            return None

    class VerifyPool:
        def acquire(self) -> VerifyConnection:
            return VerifyConnection()

    settings = _settings()
    store = InMemorySecurityStore()
    security = SecurityService(store, settings)
    security.bootstrap()
    _insert_real_entitlement_role(
        store,
        apply_status="APPLIED",
        data_grant_name="NL2SQL_DG_VERIFY",
    )
    manager = OraclePoolManager(settings)
    monkeypatch.setattr(manager, "_get_pool", lambda *, data_plane: VerifyPool())
    service = DeepSecService(settings, security, manager)
    monkeypatch.setattr(
        service,
        "status",
        lambda: {
            "configured": True,
            "message": "ok",
            "objects": {
                "predicate_user_roles_grant": 1,
                "predicate_roles_grant": 1,
                "predicate_data_entitlements_grant": 1,
            },
        },
    )

    result = service.verify(_principal())

    assert result["passed"] is expected_passed
    data_grant_check = next(
        item for item in result["checks"] if item["key"] == "data_grant:entitlement-sales"
    )
    assert data_grant_check["passed"] is expected_passed
    assert f"data_role_rows={1 if expected_passed else 0}" in data_grant_check["detail"]
    assert f"direct_end_user_rows={0 if expected_passed else 1}" in data_grant_check["detail"]


def test_verify_fails_when_target_has_enabled_vpd_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class VerifyCursor:
        def __init__(self) -> None:
            self.sql = ""

        def __enter__(self) -> VerifyCursor:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def execute(self, sql: str, _params: dict[str, object] | None = None) -> None:
            self.sql = sql

        def fetchall(self) -> list[tuple[object, ...]]:
            if "DBA_POLICIES" in self.sql:
                return [
                    (
                        "SQL_ASSIST_VPD_LEGACY_POL",
                        "HR",
                        "SQL_ASSIST_VPD_LEGACY_FN",
                        "CONTEXT_SENSITIVE",
                    )
                ]
            return [
                (
                    "NL2SQL_DG_VERIFY",
                    "HR",
                    "EMPLOYEES",
                    "NL2SQL_APP_DATA_ROLE",
                    "DATA ROLE",
                    True,
                    "ORA_END_USER_CONTEXT.CLIENT_IDENTIFIER = 'actor'",
                )
            ]

    class VerifyConnection:
        def cursor(self) -> VerifyCursor:
            return VerifyCursor()

        def close(self) -> None:
            return None

    class VerifyPool:
        def acquire(self) -> VerifyConnection:
            return VerifyConnection()

    settings = _settings()
    store = InMemorySecurityStore()
    security = SecurityService(store, settings)
    security.bootstrap()
    _insert_real_entitlement_role(
        store,
        apply_status="APPLIED",
        data_grant_name="NL2SQL_DG_VERIFY",
    )
    manager = OraclePoolManager(settings)
    monkeypatch.setattr(manager, "_get_pool", lambda *, data_plane: VerifyPool())
    service = DeepSecService(settings, security, manager)
    monkeypatch.setattr(
        service,
        "status",
        lambda: {
            "configured": True,
            "message": "ok",
            "objects": {
                "predicate_user_roles_grant": 1,
                "predicate_roles_grant": 1,
                "predicate_data_entitlements_grant": 1,
            },
        },
    )

    result = service.verify(_principal())

    assert result["passed"] is False
    vpd_check = next(
        item for item in result["checks"] if item["key"] == "vpd_policy:entitlement-sales"
    )
    assert vpd_check["passed"] is False
    assert "SQL_ASSIST_VPD_LEGACY_POL" in vpd_check["detail"]
    data_grant_check = next(
        item for item in result["checks"] if item["key"] == "data_grant:entitlement-sales"
    )
    assert data_grant_check["passed"] is True


def test_data_entitlement_sql_targets_real_object_with_role_predicate() -> None:
    settings = _settings()
    entitlement = DataEntitlementRecord(
        entitlement_id="entitlement-sales",
        role_id="role-sales",
        resource_code="HR.EMPLOYEES",
        scope_code="SALES",
        capability="SELECT",
        target_owner="HR",
        target_object="EMPLOYEES",
        target_type="TABLE",
        column_names=["EMPLOYEE_ID", "DISPLAY_NAME"],
        scope_mode="COLUMN_EQUALS",
        scope_column="DEPARTMENT_CODE",
    )

    statements = build_data_entitlement_statements(settings, entitlement)
    sql = "\n".join(statements)

    assert statements[0] == "GRANT SELECT ON HR.EMPLOYEES TO NL2SQL_APP_DB_ROLE"
    assert "CREATE OR REPLACE DATA GRANT APP_OWNER.NL2SQL_DG_" in sql
    assert "AS SELECT (EMPLOYEE_ID, DISPLAY_NAME)" in sql
    assert "ON HR.EMPLOYEES" in sql
    assert "TO NL2SQL_APP_DATA_ROLE" in sql
    assert "TO DEEPSEC_DATA_USER" not in sql
    assert "ORA_END_USER_CONTEXT.CLIENT_IDENTIFIER" in sql
    assert "SYS_CONTEXT('NL2SQL_APP_USER_CTX', 'APP_USER_ID')" not in sql
    assert "APP_OWNER.NL2SQL_APP_USER_ROLES" in sql
    assert "APP_OWNER.NL2SQL_APP_DATA_ENTITLEMENTS" in sql
    assert "e.ENTITLEMENT_ID = 'entitlement-sales'" in sql
    assert "e.ROLE_ID = 'role-sales'" in sql
    assert "e.CAPABILITY = 'SELECT'" in sql
    assert "e.APPLY_STATUS = 'APPLIED'" in sql
    assert "HR.EMPLOYEES.DEPARTMENT_CODE = e.SCOPE_CODE" in sql
    assert statements[-1] == "SET USE DATA GRANTS ONLY ON HR.EMPLOYEES ENABLED"


def test_data_entitlement_sql_builds_structured_scope_filters() -> None:
    settings = _settings()
    filters = [
        DataEntitlementScopeFilter(
            column_name="REGION_CODE",
            operator="CONTAINS",
            value_type="TEXT",
            value="A_%'",
        ),
        DataEntitlementScopeFilter(
            column_name="TOTAL_AMOUNT",
            operator="BETWEEN",
            value_type="NUMBER",
            value="10",
            value_to="20.5",
        ),
        DataEntitlementScopeFilter(
            column_name="ORDERED_AT",
            operator="ON_OR_AFTER",
            value_type="TEMPORAL",
            value="2026-08-23",
        ),
    ]
    entitlement = DataEntitlementRecord(
        entitlement_id="entitlement-filtered",
        role_id="role-sales",
        resource_code="HR.ORDERS",
        scope_code=scope_filters_scope_code(filters),
        capability="SELECT",
        target_owner="HR",
        target_object="ORDERS",
        target_type="TABLE",
        column_names=["REGION_CODE", "TOTAL_AMOUNT", "ORDERED_AT"],
        scope_mode="FILTERS",
        scope_filters=filters,
    )

    statements = build_data_entitlement_statements(
        settings,
        entitlement,
        column_types={
            "REGION_CODE": "VARCHAR2",
            "TOTAL_AMOUNT": "NUMBER",
            "ORDERED_AT": "DATE",
        },
    )
    sql = "\n".join(statements)

    assert "HR.ORDERS.REGION_CODE LIKE '%A\\_\\%''%' ESCAPE '\\'" in sql
    assert "HR.ORDERS.TOTAL_AMOUNT BETWEEN 10 AND 20.5" in sql
    assert "HR.ORDERS.ORDERED_AT >= DATE '2026-08-23'" in sql
    assert "HR.ORDERS.REGION_CODE = e.SCOPE_CODE" not in sql


def test_data_entitlement_sql_builds_login_user_id_scope_filter() -> None:
    settings = _settings()
    filters = [
        DataEntitlementScopeFilter(
            column_name="APP_OWNER_USER_ID",
            operator="EQ",
            value_type="TEXT",
            value_source="LOGIN_USER_ID",
            value="ignored",
        )
    ]
    entitlement = DataEntitlementRecord(
        entitlement_id="entitlement-own-rows",
        role_id="role-sales",
        resource_code="HR.ORDERS",
        scope_code=scope_filters_scope_code(filters),
        capability="SELECT",
        target_owner="HR",
        target_object="ORDERS",
        target_type="TABLE",
        column_names=["ORDER_ID", "APP_OWNER_USER_ID"],
        scope_mode="FILTERS",
        scope_filters=filters,
    )

    statements = build_data_entitlement_statements(
        settings,
        entitlement,
        column_types={"APP_OWNER_USER_ID": "VARCHAR2"},
    )
    sql = "\n".join(statements)

    assert (
        "HR.ORDERS.APP_OWNER_USER_ID = "
        "SYS_CONTEXT('NL2SQL_APP_USER_CTX', 'LOGIN_USER_ID')"
    ) in sql
    assert "HR.ORDERS.APP_OWNER_USER_ID = 'ignored'" not in sql


def test_data_entitlement_sql_builds_number_login_user_id_scope_filter() -> None:
    settings = _settings()
    filters = [
        DataEntitlementScopeFilter(
            column_name="APP_OWNER_USER_ID",
            operator="EQ",
            value_type="NUMBER",
            value_source="LOGIN_USER_ID",
            value="ignored",
        )
    ]
    entitlement = DataEntitlementRecord(
        entitlement_id="entitlement-own-rows",
        role_id="role-sales",
        resource_code="HR.ORDERS",
        scope_code=scope_filters_scope_code(filters),
        capability="SELECT",
        target_owner="HR",
        target_object="ORDERS",
        target_type="TABLE",
        column_names=["ORDER_ID", "APP_OWNER_USER_ID"],
        scope_mode="FILTERS",
        scope_filters=filters,
    )

    statements = build_data_entitlement_statements(
        settings,
        entitlement,
        column_types={"APP_OWNER_USER_ID": "NUMBER"},
    )
    sql = "\n".join(statements)

    assert "HR.ORDERS.APP_OWNER_USER_ID = CASE WHEN REGEXP_LIKE(" in sql
    assert "SYS_CONTEXT('NL2SQL_APP_USER_CTX', 'LOGIN_USER_ID'), '^[0-9]+$')" in sql
    assert (
        "THEN TO_NUMBER(SYS_CONTEXT('NL2SQL_APP_USER_CTX', 'LOGIN_USER_ID')) END"
    ) in sql
    assert "NULLIF" not in sql
    assert "HR.ORDERS.APP_OWNER_USER_ID = ignored" not in sql


def test_data_entitlement_sql_accepts_number_eq_positive_integer_literal() -> None:
    settings = _settings()
    filters = [
        DataEntitlementScopeFilter(
            column_name="APP_OWNER_USER_ID",
            operator="EQ",
            value_type="NUMBER",
            value="123",
        )
    ]
    entitlement = DataEntitlementRecord(
        entitlement_id="entitlement-owner-id",
        role_id="role-sales",
        resource_code="HR.ORDERS",
        scope_code=scope_filters_scope_code(filters),
        capability="SELECT",
        target_owner="HR",
        target_object="ORDERS",
        target_type="TABLE",
        column_names=["APP_OWNER_USER_ID"],
        scope_mode="FILTERS",
        scope_filters=filters,
    )

    statements = build_data_entitlement_statements(
        settings,
        entitlement,
        column_types={"APP_OWNER_USER_ID": "NUMBER"},
    )

    assert "HR.ORDERS.APP_OWNER_USER_ID = 123" in "\n".join(statements)


@pytest.mark.parametrize("value", ["1.5", "-1", "0", ""])
def test_data_entitlement_sql_rejects_number_eq_non_positive_integer_literal(
    value: str,
) -> None:
    settings = _settings()
    filters = [
        DataEntitlementScopeFilter(
            column_name="APP_OWNER_USER_ID",
            operator="EQ",
            value_type="NUMBER",
            value=value,
        )
    ]
    entitlement = DataEntitlementRecord(
        entitlement_id="entitlement-owner-id",
        role_id="role-sales",
        resource_code="HR.ORDERS",
        scope_code=scope_filters_scope_code(filters),
        capability="SELECT",
        target_owner="HR",
        target_object="ORDERS",
        target_type="TABLE",
        column_names=["APP_OWNER_USER_ID"],
        scope_mode="FILTERS",
        scope_filters=filters,
    )

    with pytest.raises(SecurityApiError, match="正整数"):
        build_data_entitlement_statements(
            settings,
            entitlement,
            column_types={"APP_OWNER_USER_ID": "NUMBER"},
        )


def test_data_entitlement_sql_accepts_materialized_view_target() -> None:
    settings = _settings()
    entitlement = DataEntitlementRecord(
        entitlement_id="entitlement-summary",
        role_id="role-analytics",
        resource_code="DW.SALES_SUMMARY_MV",
        scope_code="*",
        capability="SELECT",
        target_owner="DW",
        target_object="SALES_SUMMARY_MV",
        target_type="MATERIALIZED VIEW",
        column_names=["REGION_CODE", "TOTAL_AMOUNT"],
        scope_mode="ALL",
        scope_column="",
    )

    statements = build_data_entitlement_statements(settings, entitlement)
    sql = "\n".join(statements)

    assert "CREATE OR REPLACE DATA GRANT APP_OWNER.NL2SQL_DG_" in sql
    assert "AS SELECT (REGION_CODE, TOTAL_AMOUNT)" in sql
    assert "ON DW.SALES_SUMMARY_MV" in sql
    assert statements[-1] == "SET USE DATA GRANTS ONLY ON DW.SALES_SUMMARY_MV ENABLED"


def test_data_entitlement_validation_accepts_mview_with_table_dictionary_row() -> None:
    class FakeCursor:
        def __init__(self) -> None:
            self.sql = ""

        def execute(self, sql: str, _params: dict[str, str]) -> None:
            self.sql = sql

        def fetchall(self) -> list[tuple[str, ...]]:
            if "OBJECT_TYPE" in self.sql:
                return [("TABLE",), ("MATERIALIZED VIEW",)]
            return [
                ("REGION_CODE", "VARCHAR2"),
                ("TOTAL_AMOUNT", "NUMBER"),
            ]

    service = DeepSecService(
        _settings(),
        SecurityService(InMemorySecurityStore(), _settings()),
        OraclePoolManager(_settings()),
    )
    entitlement = DataEntitlementRecord(
        entitlement_id="entitlement-summary",
        role_id="role-analytics",
        resource_code="DW.SALES_SUMMARY_MV",
        scope_code="*",
        capability="SELECT",
        target_owner="DW",
        target_object="SALES_SUMMARY_MV",
        target_type="MATERIALIZED VIEW",
        column_names=["REGION_CODE", "TOTAL_AMOUNT"],
        scope_mode="ALL",
        scope_column="",
    )

    service._validate_data_entitlement(FakeCursor(), entitlement)  # noqa: SLF001


def test_data_entitlement_validation_accepts_structured_filters() -> None:
    class FakeCursor:
        def __init__(self) -> None:
            self.sql = ""

        def execute(self, sql: str, _params: dict[str, str]) -> None:
            self.sql = sql

        def fetchall(self) -> list[tuple[str, ...]]:
            if "OBJECT_TYPE" in self.sql:
                return [("TABLE",)]
            return [
                ("REGION_CODE", "VARCHAR2"),
                ("TOTAL_AMOUNT", "NUMBER"),
                ("ORDERED_AT", "DATE"),
            ]

    filters = [
        DataEntitlementScopeFilter(
            column_name="REGION_CODE",
            operator="IN",
            value_type="TEXT",
            values=["SALES", "HR"],
        ),
        DataEntitlementScopeFilter(
            column_name="TOTAL_AMOUNT",
            operator="GTE",
            value_type="NUMBER",
            value="100",
        ),
        DataEntitlementScopeFilter(
            column_name="ORDERED_AT",
            operator="BEFORE",
            value_type="TEMPORAL",
            value="2026-09-01",
        ),
    ]
    entitlement = DataEntitlementRecord(
        entitlement_id="entitlement-filtered",
        role_id="role-sales",
        resource_code="HR.ORDERS",
        scope_code=scope_filters_scope_code(filters),
        capability="SELECT",
        target_owner="HR",
        target_object="ORDERS",
        target_type="TABLE",
        column_names=["REGION_CODE", "TOTAL_AMOUNT"],
        scope_mode="FILTERS",
        scope_filters=filters,
    )
    service = DeepSecService(
        _settings(),
        SecurityService(InMemorySecurityStore(), _settings()),
        OraclePoolManager(_settings()),
    )

    columns = service._validate_data_entitlement(FakeCursor(), entitlement)  # noqa: SLF001

    assert columns["TOTAL_AMOUNT"] == "NUMBER"


def test_data_entitlement_validation_rejects_filter_type_mismatch() -> None:
    class FakeCursor:
        def __init__(self) -> None:
            self.sql = ""

        def execute(self, sql: str, _params: dict[str, str]) -> None:
            self.sql = sql

        def fetchall(self) -> list[tuple[str, ...]]:
            if "OBJECT_TYPE" in self.sql:
                return [("TABLE",)]
            return [("TOTAL_AMOUNT", "NUMBER")]

    filters = [
        DataEntitlementScopeFilter(
            column_name="TOTAL_AMOUNT",
            operator="CONTAINS",
            value_type="TEXT",
            value="100",
        )
    ]
    entitlement = DataEntitlementRecord(
        entitlement_id="entitlement-filtered",
        role_id="role-sales",
        resource_code="HR.ORDERS",
        scope_code=scope_filters_scope_code(filters),
        capability="SELECT",
        target_owner="HR",
        target_object="ORDERS",
        target_type="TABLE",
        column_names=["TOTAL_AMOUNT"],
        scope_mode="FILTERS",
        scope_filters=filters,
    )
    service = DeepSecService(
        _settings(),
        SecurityService(InMemorySecurityStore(), _settings()),
        OraclePoolManager(_settings()),
    )

    with pytest.raises(SecurityApiError, match="value_type"):
        service._validate_data_entitlement(FakeCursor(), entitlement)  # noqa: SLF001


def test_data_entitlement_validation_rejects_app_user_id_without_eq() -> None:
    class FakeCursor:
        def __init__(self) -> None:
            self.sql = ""

        def execute(self, sql: str, _params: dict[str, str]) -> None:
            self.sql = sql

        def fetchall(self) -> list[tuple[str, ...]]:
            if "OBJECT_TYPE" in self.sql:
                return [("TABLE",)]
            return [("APP_OWNER_USER_ID", "VARCHAR2")]

    filters = [
        DataEntitlementScopeFilter(
            column_name="APP_OWNER_USER_ID",
            operator="NE",
            value_type="TEXT",
            value_source="LOGIN_USER_ID",
        )
    ]
    entitlement = DataEntitlementRecord(
        entitlement_id="entitlement-filtered",
        role_id="role-sales",
        resource_code="HR.ORDERS",
        scope_code=scope_filters_scope_code(filters),
        capability="SELECT",
        target_owner="HR",
        target_object="ORDERS",
        target_type="TABLE",
        column_names=["APP_OWNER_USER_ID"],
        scope_mode="FILTERS",
        scope_filters=filters,
    )
    service = DeepSecService(
        _settings(),
        SecurityService(InMemorySecurityStore(), _settings()),
        OraclePoolManager(_settings()),
    )

    with pytest.raises(SecurityApiError, match="ログインユーザーID"):
        service._validate_data_entitlement(FakeCursor(), entitlement)  # noqa: SLF001


def test_data_entitlement_apply_rejects_predicate_over_4000_before_oracle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    long_role_id = "role-" + "x" * 4000
    settings = _settings()
    store = InMemorySecurityStore()
    security = SecurityService(store, settings)
    security.bootstrap()
    store.roles[long_role_id] = RoleRecord(
        role_id=long_role_id,
        role_code="LONG_ROLE",
        display_name="長いロール",
        description="predicate length guard",
        is_built_in=False,
        archived=False,
        version=1,
        permissions=set(),
        entitlements=[
            DataEntitlementRecord(
                entitlement_id="entitlement-long",
                role_id=long_role_id,
                resource_code="HR.EMPLOYEES",
                scope_code="*",
                capability="SELECT",
                target_owner="HR",
                target_object="EMPLOYEES",
                target_type="TABLE",
                column_names=["EMPLOYEE_ID"],
                scope_mode="ALL",
            )
        ],
    )
    executed: list[bool] = []
    monkeypatch.setattr(DeepSecService, "_validate_data_entitlement", lambda *_args: None)
    monkeypatch.setattr(
        "app.security.deepsec.oracle_statement_executor.execute",
        lambda *_args, **_kwargs: executed.append(True),
    )
    manager = OraclePoolManager(settings)
    monkeypatch.setattr(manager, "_get_pool", lambda *, data_plane: _FakePool(_FakeConnection([])))
    service = DeepSecService(settings, security, manager)

    with pytest.raises(SecurityApiError, match="4000"):
        service.apply_data_entitlements(
            long_role_id,
            confirmation=DEEPSEC_APPLY_CONFIRMATION,
            entitlement_ids=["entitlement-long"],
            actor=_principal(),
        )

    assert executed == []


def test_data_entitlements_reports_missing_scope_filters_migration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings()
    security = SecurityService(InMemorySecurityStore(), settings)
    service = DeepSecService(settings, security, OraclePoolManager(settings))

    def raise_missing_column(*_args: object, **_kwargs: object) -> list[RoleRecord]:
        raise RuntimeError('ORA-00904: "SCOPE_FILTERS": invalid identifier')

    monkeypatch.setattr(security, "list_roles", raise_missing_column)

    with pytest.raises(SecurityApiError) as exc_info:
        service.data_entitlements()

    assert exc_info.value.status_code == 409
    assert "SCOPE_FILTERS" in exc_info.value.public_message
    assert "migration" in exc_info.value.public_message


def test_v001_application_context_sets_login_user_id_and_clears_context() -> None:
    context_step = build_v001_plan(_settings())[1]
    package_spec = context_step.statements[0]
    package_body = context_step.statements[1]
    compile_check = context_step.statements[2]

    assert context_step.key == "application_context"
    assert package_spec.strip().endswith("END NL2SQL_DEEPSEC_CTX_PKG;")
    assert package_body.strip().endswith("END NL2SQL_DEEPSEC_CTX_PKG;")
    assert "PROCEDURE SET_APP_USER_UUID(p_user_uuid IN VARCHAR2)" in package_spec
    assert "WHERE USER_UUID = p_user_uuid AND STATUS = 'ACTIVE'" in package_body
    assert "DBMS_SESSION.CLEAR_CONTEXT" not in package_body
    assert "SELECT LOGIN_USER_ID INTO v_login_user_id" in package_body
    assert (
        "DBMS_SESSION.SET_CONTEXT('NL2SQL_APP_USER_CTX', 'LOGIN_USER_ID', v_login_user_id)"
        in package_body
    )
    assert "DBMS_SESSION.SET_IDENTIFIER(p_user_uuid)" in package_body
    assert "DBMS_SESSION.SET_CONTEXT('NL2SQL_APP_USER_CTX', 'LOGIN_USER_ID', NULL)" in package_body
    assert "DBMS_SESSION.SET_CONTEXT('NL2SQL_APP_USER_CTX', 'APP_USER_ID', NULL)" in package_body
    assert "DBMS_SESSION.CLEAR_IDENTIFIER" in package_body
    assert "ALL_ERRORS" in compile_check
    assert "NL2SQL_DEEPSEC_CTX_PKG compile error" in compile_check


def test_apply_rejects_unknown_checksum_before_oracle_execution() -> None:
    settings = _settings()
    security = SecurityService(InMemorySecurityStore(), settings)
    security.bootstrap()
    service = DeepSecService(settings, security, OraclePoolManager(settings))
    with pytest.raises(SecurityApiError, match="チェックサム"):
        service.apply_step(1, "0" * 64, DEEPSEC_APPLY_CONFIRMATION, _principal())


@pytest.mark.parametrize("confirmation", ["", "ADMIN", "admin_execute"])
def test_apply_requires_confirmation_before_oracle_or_state(
    monkeypatch: pytest.MonkeyPatch,
    confirmation: str,
) -> None:
    settings = _settings()
    store = InMemorySecurityStore()
    security = SecurityService(store, settings)
    security.bootstrap()
    service = DeepSecService(settings, security, OraclePoolManager(settings))
    step = build_v001_plan(settings)[0]
    executed: list[bool] = []

    def fail_if_executed(*_args: object, **_kwargs: object) -> list[dict[str, object]]:
        executed.append(True)
        raise AssertionError("Oracle executor must not run without ADMIN_EXECUTE confirmation")

    monkeypatch.setattr(
        "app.security.deepsec.oracle_statement_executor.execute",
        fail_if_executed,
    )

    with pytest.raises(SecurityApiError, match="confirmation=ADMIN_EXECUTE"):
        service.apply_step(step.step_no, step.checksum, confirmation, _principal())

    assert executed == []
    assert store.get_deepsec_states() == {}


@pytest.mark.parametrize("confirmation", ["", "ADMIN", "admin_execute"])
def test_data_entitlement_apply_requires_confirmation_before_oracle(
    monkeypatch: pytest.MonkeyPatch,
    confirmation: str,
) -> None:
    settings = _settings()
    store = InMemorySecurityStore()
    security = SecurityService(store, settings)
    security.bootstrap()
    _insert_real_entitlement_role(store)
    service = DeepSecService(settings, security, OraclePoolManager(settings))
    executed: list[bool] = []

    def fail_if_validated(*_args: object, **_kwargs: object) -> None:
        executed.append(True)
        raise AssertionError("metadata validation must not run without ADMIN_EXECUTE")

    monkeypatch.setattr(service, "_validate_data_entitlement", fail_if_validated)
    monkeypatch.setattr(
        "app.security.deepsec.oracle_statement_executor.execute",
        lambda *_args, **_kwargs: executed.append(True),
    )

    with pytest.raises(SecurityApiError, match="confirmation=ADMIN_EXECUTE"):
        service.apply_data_entitlements(
            "role-sales",
            confirmation=confirmation,
            entitlement_ids=["entitlement-sales"],
            actor=_principal(),
        )

    assert executed == []
    assert store.get_role("role-sales").entitlements[0].apply_status == "PENDING"


def test_data_entitlement_apply_executes_generated_sql_and_marks_applied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(data_user_password="")
    store = InMemorySecurityStore()
    security = SecurityService(store, settings)
    security.bootstrap()
    _insert_real_entitlement_role(store)
    executed: list[str] = []
    monkeypatch.setattr(DeepSecService, "_validate_data_entitlement", lambda *_args: None)
    monkeypatch.setattr(
        "app.security.deepsec.oracle_statement_executor.execute",
        lambda _conn, statements, **_kwargs: (
            executed.extend(list(statements))
            or [
                {"status": "success", "index": index}
                for index, _statement in enumerate(statements, start=1)
            ]
        ),
    )
    manager = OraclePoolManager(settings)
    monkeypatch.setattr(manager, "_get_pool", lambda *, data_plane: _FakePool(_FakeConnection([])))
    service = DeepSecService(settings, security, manager)
    monkeypatch.setattr(service, "_managed_oracle_data_grants", lambda _cursor: [])

    result = service.apply_data_entitlements(
        "role-sales",
        confirmation=DEEPSEC_APPLY_CONFIRMATION,
        entitlement_ids=["entitlement-sales"],
        actor=_principal(),
    )

    stored = store.get_role("role-sales").entitlements[0]
    assert result["status"] == "APPLIED"
    assert stored.apply_status == "APPLIED"
    assert stored.data_grant_name.startswith("NL2SQL_DG_")
    assert stored.sql_checksum
    assert executed[0] == "GRANT SELECT ON HR.EMPLOYEES TO NL2SQL_APP_DB_ROLE"
    assert executed[1] == f"DROP DATA GRANT IF EXISTS APP_OWNER.{stored.data_grant_name}"
    assert "CREATE OR REPLACE DATA GRANT APP_OWNER.NL2SQL_DG_" in executed[2]
    assert "TO NL2SQL_APP_DATA_ROLE" in executed[2]
    assert "TO DEEPSEC_DATA_USER" not in executed[2]
    assert "e.ROLE_ID = 'role-sales'" in executed[2]
    assert executed[3] == "SET USE DATA GRANTS ONLY ON HR.EMPLOYEES ENABLED"


def test_data_entitlement_apply_allows_empty_policy_sync_without_sql(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(data_user_password="")
    store = InMemorySecurityStore()
    security = SecurityService(store, settings)
    security.bootstrap()
    store.roles["role-sales"] = RoleRecord(
        role_id="role-sales",
        role_code="SALES_ANALYST",
        display_name="営業分析",
        description="営業テーブルを参照するロール",
        is_built_in=False,
        archived=False,
        version=1,
        permissions=set(),
        entitlements=[],
    )
    executed: list[str] = []
    monkeypatch.setattr(
        "app.security.deepsec.oracle_statement_executor.execute",
        lambda _conn, statements, **_kwargs: executed.extend(list(statements)),
    )
    manager = OraclePoolManager(settings)
    monkeypatch.setattr(manager, "_get_pool", lambda *, data_plane: _FakePool(_FakeConnection([])))
    service = DeepSecService(settings, security, manager)
    monkeypatch.setattr(service, "_managed_oracle_data_grants", lambda _cursor: [])

    result = service.apply_data_entitlements(
        "role-sales",
        confirmation=DEEPSEC_APPLY_CONFIRMATION,
        entitlement_ids=[],
        actor=_principal(),
    )

    assert result["status"] == "APPLIED"
    assert result["entitlement_ids"] == []
    assert result["cleanup_count"] == 0
    assert executed == []


def test_data_entitlement_apply_all_deleted_drops_stale_grants_and_disables_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(data_user_password="")
    store = InMemorySecurityStore()
    security = SecurityService(store, settings)
    security.bootstrap()
    store.roles["role-sales"] = RoleRecord(
        role_id="role-sales",
        role_code="SALES_ANALYST",
        display_name="営業分析",
        description="営業テーブルを参照するロール",
        is_built_in=False,
        archived=False,
        version=1,
        permissions=set(),
        entitlements=[],
    )
    executed: list[str] = []
    monkeypatch.setattr(
        "app.security.deepsec.oracle_statement_executor.execute",
        lambda _conn, statements, **_kwargs: (
            executed.extend(list(statements))
            or [
                {"status": "success", "index": index}
                for index, _statement in enumerate(statements, start=1)
            ]
        ),
    )
    manager = OraclePoolManager(settings)
    monkeypatch.setattr(manager, "_get_pool", lambda *, data_plane: _FakePool(_FakeConnection([])))
    service = DeepSecService(settings, security, manager)
    monkeypatch.setattr(
        service,
        "_managed_oracle_data_grants",
        lambda _cursor: [OracleManagedDataGrant("NL2SQL_DG_OLD", "HR", "EMPLOYEES")],
    )

    result = service.apply_data_entitlements(
        "role-sales",
        confirmation=DEEPSEC_APPLY_CONFIRMATION,
        entitlement_ids=[],
        actor=_principal(),
    )

    assert result["status"] == "APPLIED"
    assert result["entitlement_ids"] == []
    assert result["cleanup_count"] == 2
    assert "SET USE DATA GRANTS ONLY ON HR.EMPLOYEES DISABLED" in executed[0]
    assert executed[1] == "DROP DATA GRANT IF EXISTS APP_OWNER.NL2SQL_DG_OLD"


def test_data_entitlement_apply_replaces_deleted_grant_with_new_grant_on_same_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(data_user_password="")
    store = InMemorySecurityStore()
    security = SecurityService(store, settings)
    security.bootstrap()
    role = _insert_real_entitlement_role(store, data_grant_name="NL2SQL_DG_NEW")
    role.entitlements[0] = replace(role.entitlements[0], entitlement_id="entitlement-new")
    executed: list[str] = []
    monkeypatch.setattr(DeepSecService, "_validate_data_entitlement", lambda *_args: None)
    monkeypatch.setattr(
        "app.security.deepsec.oracle_statement_executor.execute",
        lambda _conn, statements, **_kwargs: (
            executed.extend(list(statements))
            or [
                {"status": "success", "index": index}
                for index, _statement in enumerate(statements, start=1)
            ]
        ),
    )
    manager = OraclePoolManager(settings)
    monkeypatch.setattr(manager, "_get_pool", lambda *, data_plane: _FakePool(_FakeConnection([])))
    service = DeepSecService(settings, security, manager)
    monkeypatch.setattr(
        service,
        "_managed_oracle_data_grants",
        lambda _cursor: [OracleManagedDataGrant("NL2SQL_DG_OLD", "HR", "EMPLOYEES")],
    )

    result = service.apply_data_entitlements(
        "role-sales",
        confirmation=DEEPSEC_APPLY_CONFIRMATION,
        entitlement_ids=["entitlement-new"],
        actor=_principal(),
    )

    assert result["status"] == "APPLIED"
    assert result["entitlement_ids"] == ["entitlement-new"]
    assert result["cleanup_count"] == 1
    assert executed[0] == "DROP DATA GRANT IF EXISTS APP_OWNER.NL2SQL_DG_OLD"
    assert executed[1] == "GRANT SELECT ON HR.EMPLOYEES TO NL2SQL_APP_DB_ROLE"
    assert executed[2] == "DROP DATA GRANT IF EXISTS APP_OWNER.NL2SQL_DG_NEW"
    assert "CREATE OR REPLACE DATA GRANT APP_OWNER.NL2SQL_DG_NEW" in executed[3]
    assert executed[4] == "SET USE DATA GRANTS ONLY ON HR.EMPLOYEES ENABLED"


def test_data_entitlement_preview_generates_sql_without_store_or_oracle_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings()
    store = InMemorySecurityStore()
    role = RoleRecord(
        role_id="role-sales",
        role_code="SALES_ANALYST",
        display_name="営業分析",
        description="営業テーブルを参照するロール",
        is_built_in=False,
        archived=False,
        version=1,
        permissions=set(),
        entitlements=[],
    )
    store.roles[role.role_id] = role
    security = SecurityService(store, settings)
    entitlement = DataEntitlementRecord(
        entitlement_id="",
        role_id="role-sales",
        resource_code="HR.EMPLOYEES",
        scope_code="SALES",
        capability="SELECT",
        target_owner="HR",
        target_object="EMPLOYEES",
        target_type="TABLE",
        column_names=["EMPLOYEE_ID", "DISPLAY_NAME"],
        scope_mode="COLUMN_EQUALS",
        scope_column="DEPARTMENT_CODE",
    )
    monkeypatch.setattr(DeepSecService, "_validate_data_entitlement", lambda *_args: None)
    monkeypatch.setattr(
        "app.security.deepsec.oracle_statement_executor.execute",
        lambda *_args, **_kwargs: pytest.fail("preview must not execute Oracle DDL"),
    )
    monkeypatch.setattr(
        store,
        "set_deepsec_entitlement_apply_state",
        lambda *_args, **_kwargs: pytest.fail("preview must not update apply state"),
    )
    manager = OraclePoolManager(settings)
    monkeypatch.setattr(manager, "_get_pool", lambda *, data_plane: _FakePool(_FakeConnection([])))
    service = DeepSecService(settings, security, manager)

    result = service.preview_data_entitlements(
        "role-sales",
        entitlements=[entitlement],
        actor=_principal(),
    )

    preview = result["data_entitlements"][0]
    assert result["role_id"] == "role-sales"
    assert preview["entitlement_id"]
    assert preview["data_grant_name"].startswith("NL2SQL_DG_")
    assert preview["checksum"]
    assert "CREATE OR REPLACE DATA GRANT APP_OWNER.NL2SQL_DG_" in "\n".join(preview["sql"])
    assert store.get_role("role-sales").entitlements == []


def test_data_entitlement_preview_id_keeps_saved_apply_sql_stable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings()
    store = InMemorySecurityStore()
    role = RoleRecord(
        role_id="role-sales",
        role_code="SALES_ANALYST",
        display_name="営業分析",
        description="営業テーブルを参照するロール",
        is_built_in=False,
        archived=False,
        version=1,
        permissions=set(),
        entitlements=[],
    )
    store.roles[role.role_id] = role
    security = SecurityService(store, settings)
    draft = DataEntitlementRecord(
        entitlement_id="",
        role_id="role-sales",
        resource_code="HR.EMPLOYEES",
        scope_code="SALES",
        capability="SELECT",
        target_owner="HR",
        target_object="EMPLOYEES",
        target_type="TABLE",
        column_names=["EMPLOYEE_ID", "DISPLAY_NAME"],
        scope_mode="COLUMN_EQUALS",
        scope_column="DEPARTMENT_CODE",
    )
    monkeypatch.setattr(DeepSecService, "_validate_data_entitlement", lambda *_args: None)
    manager = OraclePoolManager(settings)
    monkeypatch.setattr(manager, "_get_pool", lambda *, data_plane: _FakePool(_FakeConnection([])))
    service = DeepSecService(settings, security, manager)

    preview = service.preview_data_entitlements(
        "role-sales",
        entitlements=[draft],
        actor=_principal(),
    )["data_entitlements"][0]
    saved = replace(draft, entitlement_id=str(preview["entitlement_id"]))
    security.update_role_data_entitlements(
        "role-sales",
        expected_version=1,
        entitlements=[saved],
        actor=_principal(),
    )
    stored = store.get_role("role-sales").entitlements[0]
    apply_sql = build_data_entitlement_statements(settings, stored)

    assert str(preview["data_grant_name"]) in "\n".join(apply_sql)
    assert preview["checksum"] == _data_grant_checksum_for_test(apply_sql)


def test_data_entitlement_preview_rejects_invalid_columns() -> None:
    class PreviewCursor:
        def __init__(self) -> None:
            self.sql = ""

        def __enter__(self) -> PreviewCursor:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def execute(self, sql: str, _params: dict[str, str]) -> None:
            self.sql = sql

        def fetchall(self) -> list[tuple[str, ...]]:
            if "OBJECT_TYPE" in self.sql:
                return [("TABLE",)]
            return [("EMPLOYEE_ID", "NUMBER")]

    class PreviewConnection:
        def cursor(self) -> PreviewCursor:
            return PreviewCursor()

        def close(self) -> None:
            return None

    settings = _settings()
    store = InMemorySecurityStore()
    store.roles["role-sales"] = RoleRecord(
        role_id="role-sales",
        role_code="SALES_ANALYST",
        display_name="営業分析",
        description="営業テーブルを参照するロール",
        is_built_in=False,
        archived=False,
        version=1,
        permissions=set(),
        entitlements=[],
    )
    manager = OraclePoolManager(settings)
    manager._control_pool = _FakePool(PreviewConnection())  # noqa: SLF001
    service = DeepSecService(settings, SecurityService(store, settings), manager)

    with pytest.raises(SecurityApiError, match="対象 object に存在しない列"):
        service.preview_data_entitlements(
            "role-sales",
            entitlements=[
                DataEntitlementRecord(
                    entitlement_id="",
                    role_id="role-sales",
                    resource_code="HR.EMPLOYEES",
                    scope_code="*",
                    capability="SELECT",
                    target_owner="HR",
                    target_object="EMPLOYEES",
                    target_type="TABLE",
                    column_names=["MISSING_COLUMN"],
                    scope_mode="ALL",
                    scope_column="",
                )
            ],
            actor=_principal(),
        )


def test_plan_ignores_stale_checksum_state() -> None:
    settings = _settings()
    store = InMemorySecurityStore()
    security = SecurityService(store, settings)
    security.bootstrap()
    current_step = build_v001_plan(settings)[0]
    store.set_deepsec_state(
        version="V001",
        step_no=current_step.step_no,
        step_key=current_step.key,
        checksum="0" * 64,
        status="APPLIED",
        error_message="",
        executed_by_user_uuid="actor",
    )
    service = DeepSecService(settings, security, OraclePoolManager(settings))

    plan = service.plan()

    assert plan["has_data_user_password"] is True
    assert plan["steps"][0]["status"] == "PENDING"


def test_plan_marks_stale_application_context_for_reapply() -> None:
    settings = _settings()
    store = InMemorySecurityStore()
    security = SecurityService(store, settings)
    security.bootstrap()
    context_step = build_v001_plan(settings)[1]
    store.set_deepsec_state(
        version="V001",
        step_no=context_step.step_no,
        step_key=context_step.key,
        checksum="legacy-clear-context-checksum",
        status="APPLIED",
        error_message="",
        executed_by_user_uuid="actor",
    )
    service = DeepSecService(settings, security, OraclePoolManager(settings))

    plan = service.plan()

    assert plan["steps"][1]["key"] == "application_context"
    assert plan["steps"][1]["status"] == "PENDING"


def test_apply_application_context_after_stale_checksum_closes_pools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings()
    store = InMemorySecurityStore()
    security = SecurityService(store, settings)
    security.bootstrap()
    role_step, context_step = build_v001_plan(settings)[:2]
    store.set_deepsec_state(
        version="V001",
        step_no=role_step.step_no,
        step_key=role_step.key,
        checksum=role_step.checksum,
        status="APPLIED",
        error_message="",
        executed_by_user_uuid="actor",
    )
    store.set_deepsec_state(
        version="V001",
        step_no=context_step.step_no,
        step_key=context_step.key,
        checksum="legacy-clear-context-checksum",
        status="APPLIED",
        error_message="",
        executed_by_user_uuid="actor",
    )
    closed: list[bool] = []
    executed: list[list[str]] = []
    monkeypatch.setattr("app.security.deepsec.close_oracle_pools", lambda: closed.append(True))
    monkeypatch.setattr(
        "app.security.deepsec.oracle_statement_executor.execute",
        lambda _conn, statements, **_kwargs: (
            executed.append(list(statements))
            or [
                {"status": "success", "index": index}
                for index, _statement in enumerate(statements, start=1)
            ]
        ),
    )
    manager = OraclePoolManager(settings)
    monkeypatch.setattr(manager, "_get_pool", lambda *, data_plane: _FakePool(_FakeConnection([])))
    service = DeepSecService(settings, security, manager)

    result = service.apply_step(
        context_step.step_no,
        context_step.checksum,
        DEEPSEC_APPLY_CONFIRMATION,
        _principal(),
    )

    assert result["status"] == "APPLIED"
    assert closed == [True]
    assert len(executed) == 1
    assert any(
        "DBMS_SESSION.SET_CONTEXT('NL2SQL_APP_USER_CTX', 'LOGIN_USER_ID', NULL)" in statement
        for statement in executed[0]
    )
    assert any(
        "DBMS_SESSION.SET_CONTEXT('NL2SQL_APP_USER_CTX', 'APP_USER_ID', NULL)" in statement
        for statement in executed[0]
    )
    assert any("DBMS_SESSION.CLEAR_IDENTIFIER" in statement for statement in executed[0])
    assert any("NL2SQL_DEEPSEC_CTX_PKG compile error" in statement for statement in executed[0])
    plan = service.plan()
    assert plan["steps"][1]["status"] == "APPLIED"


def test_apply_application_context_compile_error_marks_step_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings()
    store = InMemorySecurityStore()
    security = SecurityService(store, settings)
    security.bootstrap()
    role_step, context_step = build_v001_plan(settings)[:2]
    store.set_deepsec_state(
        version="V001",
        step_no=role_step.step_no,
        step_key=role_step.key,
        checksum=role_step.checksum,
        status="APPLIED",
        error_message="",
        executed_by_user_uuid="actor",
    )
    closed: list[bool] = []
    monkeypatch.setattr("app.security.deepsec.close_oracle_pools", lambda: closed.append(True))
    monkeypatch.setattr(
        "app.security.deepsec.oracle_statement_executor.execute",
        lambda _conn, _statements, **_kwargs: [
            {"status": "success", "index": 1},
            {"status": "success", "index": 2},
            {
                "status": "error",
                "index": 3,
                "error_message": "ORA-20002: NL2SQL_DEEPSEC_CTX_PKG compile error",
            },
        ],
    )
    manager = OraclePoolManager(settings)
    monkeypatch.setattr(manager, "_get_pool", lambda *, data_plane: _FakePool(_FakeConnection([])))
    service = DeepSecService(settings, security, manager)

    with pytest.raises(SecurityApiError, match="compile error") as exc_info:
        service.apply_step(
            context_step.step_no,
            context_step.checksum,
            DEEPSEC_APPLY_CONFIRMATION,
            _principal(),
        )

    assert exc_info.value.status_code == 409
    assert closed == []
    plan = service.plan()
    assert plan["steps"][1]["status"] == "FAILED"
    assert "compile error" in plan["steps"][1]["error_message"]


@pytest.mark.parametrize("confirmation", ["", "ADMIN_EXECUTE", "admin_reset"])
def test_reset_requires_confirmation_before_oracle_or_state(
    monkeypatch: pytest.MonkeyPatch,
    confirmation: str,
) -> None:
    settings = _settings()
    store = InMemorySecurityStore()
    security = SecurityService(store, settings)
    security.bootstrap()
    _mark_deepsec_steps_applied(store, settings)
    _insert_real_entitlement_role(
        store,
        apply_status="APPLIED",
        data_grant_name="NL2SQL_DG_MANAGED",
    )
    service = DeepSecService(settings, security, OraclePoolManager(settings))
    executed: list[bool] = []

    def fail_if_executed(*_args: object, **_kwargs: object) -> list[dict[str, object]]:
        executed.append(True)
        raise AssertionError("Oracle executor must not run without ADMIN_RESET confirmation")

    monkeypatch.setattr(
        "app.security.deepsec.oracle_statement_executor.execute",
        fail_if_executed,
    )

    with pytest.raises(SecurityApiError, match="confirmation=ADMIN_RESET"):
        service.reset("V001", confirmation, _principal())

    assert executed == []
    assert len(store.get_deepsec_states()) == 4


def test_reset_executes_fixed_teardown_and_clears_states_without_data_password(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env_file = tmp_path / ".env"
    env_text = (
        "ORACLE_DEEPSEC_ENABLED=true\n"
        "ORACLE_DEEPSEC_DATA_USER=DEEPSEC_DATA_USER\n"
        "ORACLE_DEEPSEC_DATA_USER_PASSWORD=DeepSecret!123\n"
    )
    env_file.write_text(env_text, encoding="utf-8")
    monkeypatch.setattr("app.security.deepsec._BACKEND_ENV_FILE", env_file)
    settings = _settings(data_user_password="")
    store = InMemorySecurityStore()
    security = SecurityService(store, settings)
    security.bootstrap()
    _mark_deepsec_steps_applied(store, settings)
    _insert_real_entitlement_role(
        store,
        apply_status="APPLIED",
        data_grant_name="NL2SQL_DG_MANAGED",
    )
    closed: list[bool] = []
    executed: list[str] = []
    monkeypatch.setattr("app.security.deepsec.close_oracle_pools", lambda: closed.append(True))
    monkeypatch.setattr(
        "app.security.deepsec.oracle_statement_executor.execute",
        lambda _conn, statements, **_kwargs: (
            executed.extend(list(statements))
            or [
                {"status": "success", "index": index}
                for index, _statement in enumerate(statements, start=1)
            ]
        ),
    )
    manager = OraclePoolManager(settings)
    monkeypatch.setattr(manager, "_get_pool", lambda *, data_plane: _FakePool(_FakeConnection([])))
    service = DeepSecService(settings, security, manager)

    result = service.reset("V001", DEEPSEC_RESET_CONFIRMATION, _principal())

    assert result["status"] == "RESET"
    assert result["step_numbers"] == [1, 2, 3, 4]
    assert closed == [True]
    assert len(executed) == len(
        build_v001_reset_statements(settings, store.get_role("role-sales").entitlements)
    )
    assert "SET USE DATA GRANTS ONLY ON HR.EMPLOYEES DISABLED" in executed[0]
    assert executed[1] == "DROP DATA GRANT IF EXISTS APP_OWNER.NL2SQL_DG_MANAGED"
    assert "SET USE DATA GRANTS ONLY ON APP_OWNER.NL2SQL_DEEPSEC_PROBE DISABLED" in executed[2]
    assert executed[3] == "DROP DATA GRANT IF EXISTS APP_OWNER.NL2SQL_DEEPSEC_PROBE_SENSITIVE"
    assert executed[4] == "DROP DATA GRANT IF EXISTS APP_OWNER.NL2SQL_DEEPSEC_PROBE_ROWS"
    assert "DROP TABLE APP_OWNER.NL2SQL_DEEPSEC_PROBE CASCADE CONSTRAINTS PURGE" in executed[5]
    assert "DROP CONTEXT NL2SQL_APP_USER_CTX" in executed[6]
    assert "DROP PACKAGE APP_OWNER.NL2SQL_DEEPSEC_CTX_PKG" in executed[7]
    assert executed[8] == "DROP END USER IF EXISTS DEEPSEC_DATA_USER"
    assert executed[9] == "DROP DATA ROLE IF EXISTS NL2SQL_APP_DATA_ROLE"
    assert "DROP ROLE NL2SQL_APP_DB_ROLE" in executed[10]
    assert store.get_deepsec_states() == {}
    assert store.get_role("role-sales").entitlements[0].apply_status == "PENDING"
    assert settings.oracle_deepsec_enabled is True
    assert settings.oracle_deepsec_data_user_password == ""
    assert env_file.read_text(encoding="utf-8") == env_text


def test_reset_failure_keeps_states_and_does_not_close_pools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings()
    store = InMemorySecurityStore()
    security = SecurityService(store, settings)
    security.bootstrap()
    _mark_deepsec_steps_applied(store, settings)
    closed: list[bool] = []
    monkeypatch.setattr("app.security.deepsec.close_oracle_pools", lambda: closed.append(True))
    monkeypatch.setattr(
        "app.security.deepsec.oracle_statement_executor.execute",
        lambda _conn, _statements, **_kwargs: [
            {"status": "success", "index": 1},
            {
                "status": "error",
                "index": 2,
                "error_message": "ORA-01031: insufficient privileges",
            },
        ],
    )
    manager = OraclePoolManager(settings)
    monkeypatch.setattr(manager, "_get_pool", lambda *, data_plane: _FakePool(_FakeConnection([])))
    service = DeepSecService(settings, security, manager)

    with pytest.raises(SecurityApiError, match="ORA-01031") as exc_info:
        service.reset("V001", DEEPSEC_RESET_CONFIRMATION, _principal())

    assert exc_info.value.status_code == 409
    assert closed == []
    assert len(store.get_deepsec_states()) == 4


def test_update_config_persists_runtime_settings_and_closes_pools(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "ORACLE_USER=APP_OWNER",
                "ORACLE_DEEPSEC_END_USER=NL2SQL_APP_END_USER",
                "ORACLE_DEEPSEC_END_USER_PASSWORD=OldSecret123",
                "ORACLE_ADB_OCID=ocid1.autonomousdatabase.oc1..example",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    env_file.chmod(0o600)
    closed: list[bool] = []
    monkeypatch.setattr("app.security.deepsec._BACKEND_ENV_FILE", env_file)
    monkeypatch.setattr("app.security.deepsec.close_oracle_pools", lambda: closed.append(True))
    settings = _settings(deepsec_enabled=False, data_user_password="")
    settings.oracle_dsn = ""
    security = SecurityService(InMemorySecurityStore(), settings)
    security.bootstrap()
    service = DeepSecService(settings, security, OraclePoolManager(settings))

    status = service.update_config("DeepSecret!456")

    assert status["deepsec_enabled"] is True
    assert status["data_user"] == "DEEPSEC_DATA_USER"
    assert status["has_data_user_password"] is True
    assert settings.oracle_deepsec_enabled is True
    assert settings.oracle_deepsec_data_user == "DEEPSEC_DATA_USER"
    assert settings.oracle_deepsec_data_user_password == "DeepSecret!456"
    assert closed == [True]
    env_text = env_file.read_text(encoding="utf-8")
    assert "ORACLE_USER=APP_OWNER" in env_text
    assert "ORACLE_DEEPSEC_ENABLED=true" in env_text
    assert "ORACLE_DEEPSEC_DATA_USER=DEEPSEC_DATA_USER" in env_text
    assert "ORACLE_DEEPSEC_DATA_USER_PASSWORD=DeepSecret!456" in env_text
    assert "ORACLE_ADB_OCID=ocid1.autonomousdatabase.oc1..example" in env_text
    assert "ORACLE_DEEPSEC_END_USER" not in env_text
    assert env_file.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize(
    "password",
    ["short", "Invalid\nPass123", "Invalid\x7fPass123", 'Invalid"Pass123'],
)
def test_update_config_rejects_invalid_data_user_password(password: str) -> None:
    settings = _settings()
    security = SecurityService(InMemorySecurityStore(), settings)
    security.bootstrap()
    service = DeepSecService(settings, security, OraclePoolManager(settings))

    with pytest.raises(SecurityApiError, match="ORACLE_DEEPSEC_DATA_USER_PASSWORD"):
        service.update_config(password)


def test_update_config_rejects_thick_driver_before_persisting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    persisted: list[bool] = []
    monkeypatch.setattr(
        "app.security.deepsec._write_deepsec_config_env",
        lambda _settings: persisted.append(True),
    )
    settings = _settings(driver_mode="thick", deepsec_enabled=False, data_user_password="")
    security = SecurityService(InMemorySecurityStore(), settings)
    security.bootstrap()
    service = DeepSecService(settings, security, OraclePoolManager(settings))

    with pytest.raises(SecurityApiError, match="Thin mode"):
        service.update_config("DeepSecret!456")

    assert persisted == []
    assert settings.oracle_deepsec_enabled is False
    assert settings.oracle_deepsec_data_user_password == ""


class _FakeCursor:
    def __init__(self, calls: list[tuple[str, list[str]]], *, fail_clear: bool = False) -> None:
        self.calls = calls
        self.fail_clear = fail_clear

    def __enter__(self) -> _FakeCursor:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def callproc(self, name: str, values: list[str] | None = None) -> None:
        self.calls.append((name, list(values or [])))
        if self.fail_clear and name.endswith("CLEAR_APP_USER"):
            raise RuntimeError("clear failed")


class _FakeConnection:
    def __init__(
        self,
        calls: list[tuple[str, list[str]]],
        *,
        fail_clear: bool = False,
        fail_close: bool = False,
    ) -> None:
        self.calls = calls
        self.fail_clear = fail_clear
        self.fail_close = fail_close
        self.closed = 0

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self.calls, fail_clear=self.fail_clear)

    def close(self) -> None:
        self.closed += 1
        if self.fail_close:
            raise RuntimeError("DPY-1001: not connected to database")


class _FakePool:
    def __init__(self, connection: _FakeConnection) -> None:
        self.connection = connection
        self.dropped: list[_FakeConnection] = []

    def acquire(self) -> _FakeConnection:
        return self.connection

    def drop(self, connection: _FakeConnection) -> None:
        self.dropped.append(connection)


class _FakeOracleDb:
    def __init__(self) -> None:
        self.thin_mode = True
        self.init_calls: list[str] = []
        self.pool_kwargs: list[dict[str, object]] = []

    def is_thin_mode(self) -> bool:
        return self.thin_mode

    def init_oracle_client(self, *, lib_dir: str) -> None:
        self.init_calls.append(lib_dir)
        self.thin_mode = False

    def create_pool(self, **kwargs: object) -> _FakePool:
        self.pool_kwargs.append(kwargs)
        return _FakePool(_FakeConnection([]))


def test_deepsec_configuration_accepts_thin() -> None:
    OraclePoolManager(_settings(driver_mode="thin")).validate_deepsec_configuration()


def test_deepsec_configuration_rejects_thick() -> None:
    manager = OraclePoolManager(_settings(driver_mode="thick"))

    with pytest.raises(OracleAdapterError, match="Thin mode"):
        manager.validate_deepsec_configuration()


def test_settings_validation_rejects_deepsec_thick_driver_mode() -> None:
    with pytest.raises(ValueError, match="ORACLE_DRIVER_MODE=thin"):
        Settings(oracle_deepsec_enabled=True, oracle_driver_mode="thick")


def test_deepsec_configuration_requires_data_user_password() -> None:
    manager = OraclePoolManager(_settings(driver_mode="thin", data_user_password=""))

    with pytest.raises(OracleAdapterError, match="ORACLE_DEEPSEC_DATA_USER_PASSWORD"):
        manager.validate_deepsec_configuration()


def test_data_pool_uses_thin_driver_and_data_user_credentials() -> None:
    manager = OraclePoolManager(_settings(driver_mode="thin"))
    fake_oracledb = _FakeOracleDb()
    manager._oracledb = fake_oracledb

    manager._get_pool(data_plane=True)

    assert fake_oracledb.init_calls == []
    assert fake_oracledb.pool_kwargs == [
        {
            "user": "DEEPSEC_DATA_USER",
            "password": "DeepSecret!123",
            "dsn": "test",
            "tcp_connect_timeout": 5,
            "min": 1,
            "max": 4,
            "increment": 1,
        }
    ]


def test_deepsec_pool_rejects_thick_before_driver_initialization() -> None:
    manager = OraclePoolManager(_settings(driver_mode="thick"))
    fake_oracledb = _FakeOracleDb()
    manager._oracledb = fake_oracledb

    with pytest.raises(OracleAdapterError, match="Thin mode"):
        manager._get_pool(data_plane=True)

    assert fake_oracledb.init_calls == []
    assert fake_oracledb.pool_kwargs == []


def test_thick_control_pool_initializes_oracle_client_when_deepsec_disabled() -> None:
    manager = OraclePoolManager(_settings(driver_mode="thick", deepsec_enabled=False))
    fake_oracledb = _FakeOracleDb()
    manager._oracledb = fake_oracledb

    manager._get_pool(data_plane=False)

    assert fake_oracledb.init_calls == ["/opt/oracle/instantclient"]
    assert fake_oracledb.pool_kwargs == [
        {
            "user": "APP_OWNER",
            "password": "ControlPass!123",
            "dsn": "test",
            "tcp_connect_timeout": 5,
            "min": 1,
            "max": 4,
            "increment": 1,
        }
    ]


def test_control_and_data_pools_share_wallet_mtls_network_settings(tmp_path: Path) -> None:
    wallet_dir = tmp_path / "wallet"
    wallet_dir.mkdir()
    for file_name in ("tnsnames.ora", "sqlnet.ora", "cwallet.sso", "ewallet.pem"):
        (wallet_dir / file_name).write_text("dummy", encoding="utf-8")
    manager = OraclePoolManager(
        _settings(connection_security="wallet_mtls", wallet_dir=str(wallet_dir))
    )
    fake_oracledb = _FakeOracleDb()
    manager._oracledb = fake_oracledb

    manager._get_pool(data_plane=False)
    manager._get_pool(data_plane=True)

    assert fake_oracledb.pool_kwargs == [
        {
            "user": "APP_OWNER",
            "dsn": "test",
            "tcp_connect_timeout": 5,
            "password": "ControlPass!123",
            "config_dir": str(wallet_dir),
            "wallet_location": str(wallet_dir),
            "wallet_password": "ControlPass!123",
            "min": 1,
            "max": 4,
            "increment": 1,
        },
        {
            "user": "DEEPSEC_DATA_USER",
            "dsn": "test",
            "tcp_connect_timeout": 5,
            "password": "DeepSecret!123",
            "config_dir": str(wallet_dir),
            "wallet_location": str(wallet_dir),
            "wallet_password": "ControlPass!123",
            "min": 1,
            "max": 4,
            "increment": 1,
        },
    ]


def test_data_pool_sets_and_clears_each_actor_without_cross_user_leak(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, list[str]]] = []
    connection = _FakeConnection(calls)
    pool = _FakePool(connection)
    manager = OraclePoolManager(_settings())
    monkeypatch.setattr(manager, "_get_pool", lambda *, data_plane: pool)

    with manager.data_connection("user-a"):
        pass
    with manager.data_connection("user-b"):
        pass

    assert calls == [
        ("NL2SQL_DEEPSEC_CTX_PKG.SET_APP_USER_UUID", ["user-a"]),
        ("NL2SQL_DEEPSEC_CTX_PKG.CLEAR_APP_USER", []),
        ("NL2SQL_DEEPSEC_CTX_PKG.SET_APP_USER_UUID", ["user-b"]),
        ("NL2SQL_DEEPSEC_CTX_PKG.CLEAR_APP_USER", []),
    ]


def test_context_clear_failure_drops_connection_and_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, list[str]]] = []
    connection = _FakeConnection(calls, fail_clear=True)
    pool = _FakePool(connection)
    manager = OraclePoolManager(_settings())
    manager._data_pool = pool
    monkeypatch.setattr(manager, "_get_pool", lambda *, data_plane: pool)

    with pytest.raises(Exception, match="context"), manager.data_connection("user-a"):
        pass
    assert pool.dropped == [connection]


def test_context_clear_failure_is_not_masked_by_close_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, list[str]]] = []
    connection = _FakeConnection(calls, fail_clear=True, fail_close=True)
    pool = _FakePool(connection)
    manager = OraclePoolManager(_settings())
    manager._data_pool = pool
    monkeypatch.setattr(manager, "_get_pool", lambda *, data_plane: pool)

    with (
        pytest.raises(OracleAdapterError, match="DeepSec context"),
        manager.data_connection("user-a"),
    ):
        pass

    assert pool.dropped == [connection]
    assert connection.closed == 1


def test_data_connection_close_failure_after_success_drops_without_failing_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, list[str]]] = []
    connection = _FakeConnection(calls, fail_close=True)
    pool = _FakePool(connection)
    manager = OraclePoolManager(_settings())
    manager._data_pool = pool
    monkeypatch.setattr(manager, "_get_pool", lambda *, data_plane: pool)

    with manager.data_connection("user-a"):
        pass

    assert calls == [
        ("NL2SQL_DEEPSEC_CTX_PKG.SET_APP_USER_UUID", ["user-a"]),
        ("NL2SQL_DEEPSEC_CTX_PKG.CLEAR_APP_USER", []),
    ]
    assert pool.dropped == [connection]
    assert connection.closed == 1
