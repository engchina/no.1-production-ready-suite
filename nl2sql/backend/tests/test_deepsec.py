"""DeepSec V001 registry と connection context lifecycle。"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.clients.oracle_runtime import OraclePoolManager
from app.features.nl2sql.oracle_adapter import OracleAdapterError
from app.security.deepsec import (
    DEEPSEC_APPLY_CONFIRMATION,
    PASSWORD_PLACEHOLDER,
    DeepSecService,
    build_v001_plan,
)
from app.security.domain import Principal
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
        app_admin_username="system_admin",
        app_admin_password="AppAdminPass123",
        oracle_dsn="test",
        oracle_driver_mode=driver_mode,
        oracle_connection_security=connection_security,
        oracle_client_lib_dir="/opt/oracle/instantclient",
        oracle_wallet_dir=wallet_dir,
        oracle_wallet_password="",
        oracle_deepsec_enabled=deepsec_enabled,
        oracle_deepsec_data_user="NL2SQL_DEEPSEC_DATA_USER",
        oracle_deepsec_data_user_password=data_user_password,
        nl2sql_persistence_mode="memory",
        app_auth_password_min_length=12,
        app_auth_password_max_length=128,
    )


def _principal() -> Principal:
    return Principal(
        user_id="actor",
        login_name="actor",
        display_name="actor",
        status="ACTIVE",
        force_password_change=False,
        role_codes=["SYSTEM_ADMIN"],
        permissions=set(),
        data_entitlements=[],
        session_id="session",
        csrf_token_hash="csrf",
    )


def test_v001_registry_is_stable_and_preview_never_contains_secret() -> None:
    settings = _settings()
    first = build_v001_plan(settings)
    second = build_v001_plan(settings)
    assert [step.checksum for step in first] == [step.checksum for step in second]
    preview = "\n".join(statement for step in first for statement in step.statements)
    assert PASSWORD_PLACEHOLDER in preview
    assert settings.oracle_deepsec_data_user_password not in preview


def test_v001_verification_object_plsql_block_has_terminator() -> None:
    verification_step = build_v001_plan(_settings())[2]

    assert verification_step.key == "verification_object"
    assert verification_step.statements[0].strip().endswith("END;")


def test_v001_application_context_clears_only_app_user_attribute() -> None:
    context_step = build_v001_plan(_settings())[1]
    package_spec = context_step.statements[0]
    package_body = context_step.statements[1]
    compile_check = context_step.statements[2]

    assert context_step.key == "application_context"
    assert package_spec.strip().endswith("END NL2SQL_DEEPSEC_CTX_PKG;")
    assert package_body.strip().endswith("END NL2SQL_DEEPSEC_CTX_PKG;")
    assert "DBMS_SESSION.CLEAR_CONTEXT" not in package_body
    assert "DBMS_SESSION.SET_CONTEXT('NL2SQL_APP_USER_CTX', 'APP_USER_ID', NULL)" in package_body
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
        executed_by="actor",
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
        executed_by="actor",
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
        executed_by="actor",
    )
    store.set_deepsec_state(
        version="V001",
        step_no=context_step.step_no,
        step_key=context_step.key,
        checksum="legacy-clear-context-checksum",
        status="APPLIED",
        error_message="",
        executed_by="actor",
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
        "DBMS_SESSION.SET_CONTEXT('NL2SQL_APP_USER_CTX', 'APP_USER_ID', NULL)" in statement
        for statement in executed[0]
    )
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
        executed_by="actor",
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
    assert status["data_user"] == "NL2SQL_DEEPSEC_DATA_USER"
    assert status["has_data_user_password"] is True
    assert settings.oracle_deepsec_enabled is True
    assert settings.oracle_deepsec_data_user == "NL2SQL_DEEPSEC_DATA_USER"
    assert settings.oracle_deepsec_data_user_password == "DeepSecret!456"
    assert closed == [True]
    env_text = env_file.read_text(encoding="utf-8")
    assert "ORACLE_USER=APP_OWNER" in env_text
    assert "ORACLE_DEEPSEC_ENABLED=true" in env_text
    assert "ORACLE_DEEPSEC_DATA_USER=NL2SQL_DEEPSEC_DATA_USER" in env_text
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
            "user": "NL2SQL_DEEPSEC_DATA_USER",
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
            "user": "NL2SQL_DEEPSEC_DATA_USER",
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
        ("NL2SQL_DEEPSEC_CTX_PKG.SET_APP_USER", ["user-a"]),
        ("NL2SQL_DEEPSEC_CTX_PKG.CLEAR_APP_USER", []),
        ("NL2SQL_DEEPSEC_CTX_PKG.SET_APP_USER", ["user-b"]),
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

    with pytest.raises(OracleAdapterError, match="DeepSec context"), manager.data_connection(
        "user-a"
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
        ("NL2SQL_DEEPSEC_CTX_PKG.SET_APP_USER", ["user-a"]),
        ("NL2SQL_DEEPSEC_CTX_PKG.CLEAR_APP_USER", []),
    ]
    assert pool.dropped == [connection]
    assert connection.closed == 1
