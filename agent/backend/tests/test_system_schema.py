"""Agent のシステムテーブル（#751）の決定論テスト。

Oracle の辞書（USER_OBJECTS / USER_TABLES / USER_CONSTRAINTS）・台帳・操作の lease を
fake で再現する。manager の状態の分類・作成・更新・全再作成、API の権限と契約、CLI、
DB ゲートの schema の確認を確かめる。
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pr_system_settings.auth.store import PLATFORM_AUTH_TABLES
from pr_system_settings.system_schema import (
    DESTRUCTIVE_MIGRATIONS_CONFIRMATION_REQUIRED,
    ForeignKeySpec,
    foreign_keys_from_create_table,
)
from pytest import MonkeyPatch
from security_support import ProductionAuth, client, enable_production_auth, login

import app.features.settings.system_tables as system_tables_api
from app.cli import agent_system_schema
from app.security.permissions import ALL_PERMISSION_CODES, permission_for_route
from app.security.service import set_security_service
from app.system_schema import (
    CONTROL_PLANE_STATEMENTS,
    CONTROL_TABLE,
    DOMAIN_TABLES,
    MANAGED_FOREIGN_KEYS,
    MANAGED_OBJECTS,
    MANAGED_TABLES,
    MIGRATION_TABLE,
    MIGRATIONS,
    RECREATE_CONFIRMATION,
    RETIRED_MANAGED_OBJECTS,
    RETIRED_TABLES_APPROVAL,
    RUN_FACTS_STATEMENTS,
    RUNTIME_STATEMENTS,
    SystemSchemaError,
    SystemSchemaManager,
    classify_system_schema_status,
    managed_manifest_from_schema,
)


class _FakeDatabase:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], datetime | None] = {}
        self.migrations: dict[str, str] = {}
        self.foreign_keys: dict[str, tuple[ForeignKeySpec, bool]] = {}
        self.operation: dict[str, Any] | None = None
        self.executed: list[str] = []
        # AGENT_ROLE_PERMISSIONS の行（ROLE_ID, PERMISSION_CODE）。
        self.role_permissions: set[tuple[str, str]] = set()

    def table(self, name: str) -> None:
        self.objects[(name, "TABLE")] = datetime.now(UTC)

    @staticmethod
    def idle_operation() -> dict[str, Any]:
        return {
            "status": "IDLE",
            "operation_kind": None,
            "lease_owner": None,
            "lease_expires_at": None,
            "last_error_code": None,
            "schema_epoch": 0,
            "updated_at": datetime.now(UTC),
        }


class _FakeCursor:
    def __init__(self, database: _FakeDatabase) -> None:
        self.database = database
        self.rows: list[tuple[Any, ...]] = []
        self.rowcount = 0

    def __enter__(self) -> _FakeCursor:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self.rows

    def fetchone(self) -> tuple[Any, ...] | None:
        return self.rows[0] if self.rows else None

    def execute(self, statement: str, parameters: dict[str, Any] | None = None) -> None:
        sql = re.sub(r"\s+", " ", statement).strip()
        upper = sql.upper()
        params = parameters or {}
        db = self.database
        self.rows = []
        self.rowcount = 0
        db.executed.append(upper)
        names = {str(value).upper() for value in params.values()}

        if upper.startswith("SELECT OBJECT_NAME, OBJECT_TYPE, CREATED FROM USER_OBJECTS"):
            self.rows = [
                (name, kind, created)
                for (name, kind), created in sorted(db.objects.items())
                if name in names
            ]
        elif upper.startswith(f"SELECT MIGRATION_NAME, CHECKSUM FROM {MIGRATION_TABLE}"):
            self.rows = sorted(db.migrations.items())
        elif upper.startswith("SELECT TABLE_NAME, NUM_ROWS, LAST_ANALYZED FROM USER_TABLES"):
            self.rows = [(n, 0, None) for n in sorted(names) if (n, "TABLE") in db.objects]
        elif upper.startswith("SELECT TABLE_NAME FROM USER_TABLES"):
            self.rows = [(n,) for n in sorted(names) if (n, "TABLE") in db.objects]
        elif upper.startswith("SELECT STATUS, OPERATION_KIND"):
            op = db.operation
            if op is not None:
                self.rows = [
                    (
                        op["status"],
                        op["operation_kind"],
                        op["lease_expires_at"],
                        op["last_error_code"],
                        op["schema_epoch"],
                        op["updated_at"],
                    )
                ]
        elif upper.startswith("SELECT CHILD.CONSTRAINT_NAME"):
            self.rows = [
                (
                    fk.name,
                    fk.table_name,
                    fk.referenced_table_name,
                    fk.delete_rule,
                    "ENABLED",
                    "VALIDATED" if validated else "NOT VALIDATED",
                    column,
                    referenced,
                )
                for fk, validated in sorted(db.foreign_keys.values(), key=lambda i: i[0].name)
                if fk.table_name in names
                for column, referenced in zip(fk.columns, fk.referenced_columns, strict=True)
            ]
        elif upper.startswith("CREATE TABLE"):
            name = upper.split()[2]
            if (name, "TABLE") in db.objects:
                raise RuntimeError("ORA-00955: name is already used by an existing object")
            db.table(name)
            for foreign_key in foreign_keys_from_create_table(statement):
                db.foreign_keys[foreign_key.name] = (foreign_key, True)
        elif (
            index := re.match(r"CREATE (?:UNIQUE )?INDEX ([A-Z][A-Z0-9_$#]*)", upper)
        ) is not None:
            name = index.group(1)
            if (name, "INDEX") in db.objects:
                raise RuntimeError("ORA-00955: name is already used by an existing object")
            db.objects[(name, "INDEX")] = datetime.now(UTC)
        elif upper.startswith("DROP INDEX"):
            db.objects.pop((upper.split()[2], "INDEX"), None)
        elif upper.startswith("DROP TABLE"):
            name = upper.split()[2]
            if (name, "TABLE") not in db.objects:
                raise RuntimeError("ORA-00942: table or view does not exist")
            db.objects.pop((name, "TABLE"))
            db.foreign_keys = {k: v for k, v in db.foreign_keys.items() if v[0].table_name != name}
            if name == MIGRATION_TABLE:
                db.migrations.clear()
        elif upper.startswith(f"INSERT INTO {CONTROL_TABLE}"):
            if db.operation is not None:
                raise RuntimeError("ORA-00001: unique constraint violated")
            db.operation = db.idle_operation()
        elif upper.startswith(f"MERGE INTO {MIGRATION_TABLE}"):
            db.migrations[str(params["migration_name"])] = str(params["checksum"])
        elif upper.startswith("DELETE FROM AGENT_ROLE_PERMISSIONS WHERE PERMISSION_CODE IN"):
            codes = set(re.findall(r"'([^']+)'", sql))
            matched = {row for row in db.role_permissions if row[1] in codes}
            db.role_permissions -= matched
            self.rowcount = len(matched)
        elif upper.startswith(f"UPDATE {CONTROL_TABLE}"):
            self._update_operation(upper, params)
        elif upper.startswith("ALTER TABLE") and " FOREIGN KEY " in upper:
            match = re.match(
                r"ALTER TABLE (\S+) ADD CONSTRAINT (\S+) FOREIGN KEY \(([^)]*)\) "
                r"REFERENCES (\S+) \(([^)]*)\)(?: ON DELETE (CASCADE|SET NULL))?",
                upper,
            )
            assert match is not None, upper
            table, name, columns, referenced, referenced_columns, rule = match.groups()
            spec = ForeignKeySpec(
                name=name,
                table_name=table,
                columns=tuple(item.strip() for item in columns.split(",")),
                referenced_table_name=referenced,
                referenced_columns=tuple(item.strip() for item in referenced_columns.split(",")),
                delete_rule=rule or "NO ACTION",
            )
            if any(fk.signature == spec.signature for fk, _ in db.foreign_keys.values()):
                raise RuntimeError("ORA-02275: such a referential constraint already exists")
            db.foreign_keys[name] = (spec, True)

    def _update_operation(self, upper: str, params: dict[str, Any]) -> None:
        op = self.database.operation
        if op is None:
            return
        now = datetime.now(UTC)
        owner = str(params.get("lease_owner") or "")
        if "SET STATUS = 'RUNNING'" in upper:
            if (
                op["status"] == "RUNNING"
                and op["lease_expires_at"]
                and op["lease_expires_at"] >= now
            ):
                return
            op.update(
                status="RUNNING",
                operation_kind=params["operation_kind"],
                lease_owner=owner,
                lease_expires_at=now + timedelta(seconds=int(params["lease_seconds"])),
                last_error_code=None,
            )
            self.rowcount = 1
            return
        if op.get("lease_owner") != owner:
            return
        if "SET STATUS = 'IDLE'" in upper:
            if "SCHEMA_EPOCH = SCHEMA_EPOCH + 1" in upper:
                op["schema_epoch"] = int(op["schema_epoch"]) + 1
            op.update(status="IDLE", operation_kind=None, lease_owner=None, lease_expires_at=None)
        elif "SET STATUS = 'FAILED'" in upper:
            op.update(
                status="FAILED",
                operation_kind=None,
                lease_owner=None,
                lease_expires_at=None,
                last_error_code=params["error_code"],
            )
        else:
            op["lease_expires_at"] = now + timedelta(seconds=int(params["lease_seconds"]))
        self.rowcount = 1


class _FakeConnection:
    def __init__(self, database: _FakeDatabase) -> None:
        self.database = database

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self.database)

    def commit(self) -> None:
        return None

    def rollback(self) -> None:
        return None


def _manager(database: _FakeDatabase) -> SystemSchemaManager:
    @contextmanager
    def factory() -> Iterator[_FakeConnection]:
        yield _FakeConnection(database)

    return SystemSchemaManager(factory)


def _legacy_database() -> _FakeDatabase:
    """#751 より前に `agent_security_migrate` で作った DB（台帳と制御テーブルが無い）。"""
    database = _FakeDatabase()
    for name in (
        *PLATFORM_AUTH_TABLES,
        "AGENT_ROLE_PERMISSIONS",
        "AGENT_ROLE_AGENTS",
        "AGENT_ROLE_BUSINESS_VIEWS",
    ):
        database.table(name)
    # 旧 CLI の CREATE TABLE も同じ FK（ON DELETE CASCADE）を持っていた。
    for foreign_key in MANAGED_FOREIGN_KEYS:
        database.foreign_keys[foreign_key.name] = (foreign_key, True)
    database.role_permissions = {("role-a", "menu.dashboard"), ("role-a", "menu.runs")}
    return database


# ---- manifest -----------------------------------------------------------------


def test_manifest_matches_ddl_and_excludes_shared_auth_tables() -> None:
    assert managed_manifest_from_schema() == set(MANAGED_OBJECTS)
    assert MANAGED_TABLES[:2] == (CONTROL_TABLE, MIGRATION_TABLE)
    assert {
        "AGENT_ROLE_PERMISSIONS",
        "AGENT_ROLE_AGENTS",
        # Run・業務 Agent の保存先と、画面で変えた定義（#764）。
        "AGENT_RUNTIME_CHECKPOINTS",
        "AGENT_RUNTIME_RUNS",
        "AGENT_RUNTIME_EVENTS",
        "AGENT_RUNTIME_STEPS",
        "AGENT_RUNTIME_APPROVALS",
        "AGENT_RUNTIME_ARTIFACTS",
        "AGENT_CONTROL_PLANE_ITEMS",
        # 利用状況・フィードバックの集計に使う Run の事実（#794）。
        "AGENT_RUN_FACTS",
    } == DOMAIN_TABLES
    # 監査の検索に使う projection の索引（エラーコードは JSON_VALUE の関数索引）。
    assert any(
        "AGENT_RUNTIME_STEPS_ERROR_CODE_IX" in statement
        and "JSON_VALUE(TOOL_RESULT_JSON" in statement
        for statement in RUNTIME_STATEMENTS
    )
    # 既存の DB では Runtime repository が作っていたテーブルを、migration 006 で管理対象にする。
    migration_006 = next(item for item in MIGRATIONS if item.name.startswith("20261002_006"))
    assert migration_006.statements == (*RUNTIME_STATEMENTS, *CONTROL_PLANE_STATEMENTS)
    # 既存の DB には migration 007 で Run の事実のテーブルと索引を足す（#794）。
    migration_007 = next(item for item in MIGRATIONS if item.name.startswith("20261003_007"))
    assert migration_007.statements == RUN_FACTS_STATEMENTS
    assert not migration_007.destructive
    assert {
        "AGENT_RUN_FACTS_CREATED_IX",
        "AGENT_RUN_FACTS_AGENT_CREATED_IX",
        "AGENT_RUN_FACTS_RATED_IX",
    } <= {name for name, kind in MANAGED_OBJECTS if kind == "INDEX"}
    # 共通認証の表は管理対象にしない（全再作成でも RAG / NL2SQL のユーザー・ロールを消さない）。
    assert not set(PLATFORM_AUTH_TABLES) & set(MANAGED_TABLES)
    assert ("AGENT_ROLE_BUSINESS_VIEWS", "TABLE") in RETIRED_MANAGED_OBJECTS
    assert {fk.name for fk in MANAGED_FOREIGN_KEYS} == {
        "FK_AGENT_ROLE_PERMISSIONS_ROLE",
        "FK_AGENT_ROLE_AGENTS_ROLE",
    }
    assert all(fk.delete_rule == "CASCADE" for fk in MANAGED_FOREIGN_KEYS)
    # 名前は AGENT_ で始める（AGENTS.md「データベース object の命名」）。
    assert all(name.startswith("AGENT_") for name, _ in MANAGED_OBJECTS)
    assert len({migration.name for migration in MIGRATIONS}) == len(MIGRATIONS)


def test_classify_status() -> None:
    applied = {migration.name: migration.checksum for migration in MIGRATIONS}
    everything = set(MANAGED_OBJECTS)
    assert classify_system_schema_status(set(), {}) == "missing"
    assert classify_system_schema_status({("AGENT_ROLE_AGENTS", "TABLE")}, applied) == "partial"
    assert classify_system_schema_status(everything, applied) == "ready"
    assert classify_system_schema_status(everything, {}) == "outdated"
    assert (
        classify_system_schema_status(everything | {RETIRED_MANAGED_OBJECTS[0]}, applied)
        == "outdated"
    )


# ---- manager ------------------------------------------------------------------


def test_initialize_fresh_database_creates_everything_and_is_idempotent() -> None:
    database = _FakeDatabase()
    manager = _manager(database)
    assert manager.status()["status"] == "missing"

    result = manager.initialize()

    assert result["operation"] == "initialized"
    assert result["status"] == "ready"
    assert result["pending_versions"] == []
    assert set(PLATFORM_AUTH_TABLES) <= {name for name, kind in database.objects if kind == "TABLE"}
    # 組み込み SYSTEM_ADMIN ロールを MERGE で用意する（ユーザーは作らない）。
    assert any(item.startswith("MERGE INTO PLATFORM_ROLES") for item in database.executed)
    assert not any("INSERT INTO PLATFORM_USERS" in item for item in database.executed)
    # PLATFORM_ROLES を先に作り、AGENT_ROLE_* はその後（FK の参照先）。
    creates = [item.split()[2] for item in database.executed if item.startswith("CREATE TABLE")]
    assert creates.index("PLATFORM_ROLES") < creates.index("AGENT_ROLE_PERMISSIONS")
    assert result["operation_state"]["schema_epoch"] == 1
    assert {item["name"] for item in result["objects"]} == {name for name, _ in MANAGED_OBJECTS}

    again = manager.initialize()
    assert again["operation"] == "no_op"
    assert again["operation_state"]["schema_epoch"] == 1


def test_legacy_database_requires_approval_then_migrates() -> None:
    """旧 CLI で作った DB は、検索・回答プロファイルの
    表の削除（#750 から未使用）を承認してから更新する。"""
    database = _legacy_database()
    manager = _manager(database)
    status = manager.status()
    assert status["status"] == "partial"
    # 台帳が無いので、廃止した表の削除も別に承認を求める（#1648）。
    assert [item["name"] for item in status["pending_destructive_migrations"]] == [
        "20261002_003_retire_role_business_views",
        RETIRED_TABLES_APPROVAL,
    ]
    assert status["retired_objects"] == [
        {"name": "AGENT_ROLE_BUSINESS_VIEWS", "object_type": "TABLE"}
    ]

    with pytest.raises(SystemSchemaError) as refused:
        manager.initialize()
    assert refused.value.code == DESTRUCTIVE_MIGRATIONS_CONFIRMATION_REQUIRED
    assert ("AGENT_ROLE_BUSINESS_VIEWS", "TABLE") in database.objects

    result = manager.initialize(allow_destructive=True)
    assert result["operation"] == "migrated"
    assert result["status"] == "ready"
    assert ("AGENT_ROLE_BUSINESS_VIEWS", "TABLE") not in database.objects
    assert ("AGENT_ROLE_AGENTS_AGENT_IDX", "INDEX") in database.objects
    # 廃止した権限コードだけを消す。
    assert database.role_permissions == {("role-a", "menu.runs")}
    assert set(database.migrations) == {migration.name for migration in MIGRATIONS}


def test_retired_table_without_ledger_is_not_dropped_without_approval() -> None:
    """台帳の無い schema の廃止した表は、missing でも承認が無ければ消さない（#1648）。"""
    database = _FakeDatabase()
    database.table("AGENT_ROLE_BUSINESS_VIEWS")
    manager = _manager(database)

    status = manager.status()
    assert status["status"] == "missing"
    approvals = status["pending_destructive_migrations"]
    assert [item["name"] for item in approvals] == [RETIRED_TABLES_APPROVAL]
    assert "AGENT_ROLE_BUSINESS_VIEWS" in approvals[0]["description"]

    with pytest.raises(SystemSchemaError) as refused:
        manager.initialize()
    assert refused.value.code == DESTRUCTIVE_MIGRATIONS_CONFIRMATION_REQUIRED
    assert ("AGENT_ROLE_BUSINESS_VIEWS", "TABLE") in database.objects
    assert not any(item.startswith("DROP") for item in database.executed)

    result = manager.initialize(allow_destructive=True)
    assert result["status"] == "ready"
    assert ("AGENT_ROLE_BUSINESS_VIEWS", "TABLE") not in database.objects


def test_retired_table_with_ledger_is_dropped_as_agent_owned() -> None:
    """台帳に記録があれば Agent が作った表として、承認なしに消す（#1648）。"""
    database = _FakeDatabase()
    manager = _manager(database)
    manager.initialize()
    database.table("AGENT_ROLE_BUSINESS_VIEWS")

    status = manager.status()
    assert status["status"] == "outdated"
    assert status["pending_destructive_migrations"] == []
    assert manager.initialize()["status"] == "ready"
    assert ("AGENT_ROLE_BUSINESS_VIEWS", "TABLE") not in database.objects


def test_recreate_requires_confirmation_and_keeps_shared_auth_tables() -> None:
    database = _FakeDatabase()
    manager = _manager(database)
    manager.initialize()
    with pytest.raises(SystemSchemaError):
        manager.initialize(recreate=True, confirmation="wrong")

    result = manager.initialize(recreate=True, confirmation=RECREATE_CONFIRMATION)

    assert result["operation"] == "recreated"
    assert result["status"] == "ready"
    dropped = [item.split()[2] for item in database.executed if item.startswith("DROP TABLE")]
    # 全再作成は Run の保存先・定義の表も消す（共通認証の表は残す）。
    assert set(dropped) == (set(DOMAIN_TABLES) | {MIGRATION_TABLE})
    assert not set(dropped) & set(PLATFORM_AUTH_TABLES)


# ---- API・DB ゲート・CLI --------------------------------------------------------


@pytest.fixture
def fake_manager(monkeypatch: MonkeyPatch) -> SystemSchemaManager:
    manager = _manager(_legacy_database())
    monkeypatch.setattr(system_tables_api, "system_schema_manager", manager)
    import app.features.agent.router as agent_router

    monkeypatch.setattr(agent_router, "system_schema_manager", manager)
    return manager


def test_system_tables_routes_require_menu_permission() -> None:
    assert permission_for_route("GET", "/settings/database/system-tables") == frozenset(
        {"menu.settings_system_tables"}
    )
    assert permission_for_route("POST", "/settings/database/system-tables/initialize") == (
        frozenset({"menu.settings_system_tables"})
    )
    assert "menu.settings_system_tables" in ALL_PERMISSION_CODES


def test_system_tables_api_status_and_initialize(fake_manager: SystemSchemaManager) -> None:
    status = client.get("/api/settings/database/system-tables")
    assert status.status_code == 200, status.text
    assert status.json()["data"]["status"] == "partial"

    refused = client.post("/api/settings/database/system-tables/initialize", json={})
    assert refused.status_code == 409
    assert refused.json()["error_code"] == DESTRUCTIVE_MIGRATIONS_CONFIRMATION_REQUIRED

    done = client.post(
        "/api/settings/database/system-tables/initialize", json={"allow_destructive": True}
    )
    assert done.status_code == 200, done.text
    assert done.json()["data"]["status"] == "ready"
    assert done.json()["data"]["operation"] == "migrated"


def test_system_tables_api_is_denied_without_permission(
    monkeypatch: MonkeyPatch, fake_manager: SystemSchemaManager
) -> None:
    auth: ProductionAuth = enable_production_auth(monkeypatch)
    try:
        auth.user_with_permissions("runs-only", ["agent.runs.view"])
        headers = login("runs-only")
        response = client.get("/api/settings/database/system-tables", headers=headers)
        assert response.status_code == 403
        auth.user_with_permissions("tables-admin", ["menu.settings_system_tables"])
        allowed = client.get("/api/settings/database/system-tables", headers=login("tables-admin"))
        assert allowed.status_code == 200, allowed.text
    finally:
        set_security_service(None)


def test_database_gate_reports_setup_required_until_ready(
    monkeypatch: MonkeyPatch, fake_manager: SystemSchemaManager
) -> None:
    import pr_system_settings.database_status as database_status

    import app.features.agent.router as agent_router

    # 接続設定の判定と接続確認は通ったことにして、schema の確認だけを見る。
    monkeypatch.setattr(database_status, "database_readiness", lambda *_args: "ok")

    async def connected(_settings: object) -> None:
        return None

    monkeypatch.setattr(agent_router, "_test_database_status_connection", connected)

    # local でも短絡せず、システムテーブルの状態を確かめる（RAG と同じ。#751）。
    before = client.get("/api/ready/database").json()["data"]
    assert before["status"] == "setup_required"
    fake_manager.initialize(allow_destructive=True)
    after = client.get("/api/ready/database").json()["data"]
    assert after["status"] == "ok"


def test_cli_status_and_initialize(
    monkeypatch: MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    manager = _manager(_legacy_database())
    monkeypatch.setattr(agent_system_schema, "system_schema_manager", manager)

    assert agent_system_schema.main(["--status"]) == 0
    assert json.loads(capsys.readouterr().out)["data"]["status"] == "partial"
    assert agent_system_schema.main(["--initialize"]) == 2
    refused = json.loads(capsys.readouterr().out)
    assert refused["error"]["code"] == DESTRUCTIVE_MIGRATIONS_CONFIRMATION_REQUIRED
    assert agent_system_schema.main(["--initialize", "--allow-destructive"]) == 0
    assert json.loads(capsys.readouterr().out)["data"]["status"] == "ready"

    class _Broken:
        def status(self) -> dict[str, Any]:
            raise RuntimeError("DPY-6005: cannot connect password=secret")

    monkeypatch.setattr(agent_system_schema, "system_schema_manager", _Broken())
    assert agent_system_schema.main(["--status"]) == 1
    output = capsys.readouterr().out
    assert "secret" not in output
    # DB に接続できない失敗は、本当のコードと、理由・対処を日本語で出す（#1744）。
    error = json.loads(output)["error"]
    assert error["code"] == "DPY-6005"
    assert error["cause"] == "unreachable"
    assert error["hint"].startswith("ADB が停止しているか、この接続元から DB に届きません。")
    assert error["detail"] == "DPY-6005: cannot connect password=***"


def test_cli_check_returns_zero_only_when_ready(
    monkeypatch: MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """配備の確認（#1648）: ready なら 0、それ以外（読めないときも）は 1 と 1 行の要約。"""
    database = _FakeDatabase()
    manager = _manager(database)
    monkeypatch.setattr(agent_system_schema, "system_schema_manager", manager)

    assert agent_system_schema.main(["--check"]) == 1
    assert capsys.readouterr().out.strip() == "status=missing"
    manager.initialize()
    assert agent_system_schema.main(["--check"]) == 0
    assert capsys.readouterr().out.strip() == "status=ready"

    class _Denied:
        def status(self) -> dict[str, Any]:
            raise RuntimeError("ORA-01031: insufficient privileges password=secret")

        def initialize(self, **_kwargs: Any) -> dict[str, Any]:
            raise RuntimeError("ORA-01031: insufficient privileges password=secret")

    monkeypatch.setattr(agent_system_schema, "system_schema_manager", _Denied())
    assert agent_system_schema.main(["--check"]) == 1
    output = capsys.readouterr().out
    assert "ORA-01031" in output
    assert "secret" not in output
    assert agent_system_schema.main(["--initialize"]) == 1
    output = capsys.readouterr().out
    assert json.loads(output)["error"]["code"] == "ORA-01031"
    assert "secret" not in output


def test_cli_reports_real_cause_of_pool_timeout_without_secrets(
    monkeypatch: MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """pool の時間切れ（DPY-4005）は、直接 1 回だけ接続して本当の原因を出す（#1744）。"""
    from pr_backend_core import oracle_connect_diagnosis

    calls: list[dict[str, Any]] = []

    class _Oracledb:
        def connect(self, **kwargs: Any) -> Any:
            calls.append(kwargs)
            raise RuntimeError("ORA-01017: invalid credential or not authorized; logon denied")

    class _Timeout:
        def status(self) -> dict[str, Any]:
            raise RuntimeError("DPY-4005: timed out waiting for the connection pool")

        def initialize(self, **_kwargs: Any) -> dict[str, Any]:
            return self.status()

    monkeypatch.setattr(oracle_connect_diagnosis, "_default_oracledb_loader", lambda: _Oracledb())
    monkeypatch.setattr(
        agent_system_schema,
        "_connect_kwargs",
        lambda: {"user": "APP", "password": "Pa55-word-x", "dsn": "db_high"},
    )
    monkeypatch.setattr(agent_system_schema, "system_schema_manager", _Timeout())

    assert agent_system_schema.main(["--initialize"]) == 1
    output = capsys.readouterr().out
    error = json.loads(output)["error"]
    assert error["code"] == "ORA-01017"
    assert error["cause"] == "invalid_credentials"
    assert "password が違います" in error["hint"]
    assert "Pa55-word-x" not in output
    assert len(calls) == 1 and calls[0]["retry_count"] == 0

    assert agent_system_schema.main(["--check"]) == 1
    output = capsys.readouterr().out
    assert output.startswith("状態を読めません（ORA-01017: DB のユーザー名か password が違います。")
    assert "Pa55-word-x" not in output
