"""DeepSec V001 registry と connection context lifecycle。"""

from __future__ import annotations

from typing import Any

import pytest

from app.clients.oracle_runtime import OraclePoolManager
from app.features.nl2sql.oracle_adapter import OracleAdapterError
from app.security.deepsec import PASSWORD_PLACEHOLDER, DeepSecService, build_v001_plan
from app.security.domain import Principal
from app.security.service import SecurityApiError, SecurityService
from app.security.store import InMemorySecurityStore
from app.settings import Settings


def _settings(
    *,
    driver_mode: str = "thin",
    deepsec_enabled: bool = True,
    end_user_password: str = "DeepSecret!123",
) -> Settings:
    return Settings.model_construct(
        oracle_user="APP_OWNER",
        oracle_password="ControlPass!123",
        oracle_dsn="test",
        oracle_driver_mode=driver_mode,
        oracle_client_lib_dir="/opt/oracle/instantclient",
        oracle_deepsec_enabled=deepsec_enabled,
        oracle_deepsec_end_user="NL2SQL_APP_END_USER",
        oracle_deepsec_end_user_password=end_user_password,
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
    assert settings.oracle_deepsec_end_user_password not in preview


def test_apply_rejects_unknown_checksum_before_oracle_execution() -> None:
    settings = _settings()
    security = SecurityService(InMemorySecurityStore(), settings)
    security.bootstrap()
    service = DeepSecService(settings, security, OraclePoolManager(settings))
    with pytest.raises(SecurityApiError, match="チェックサム"):
        service.apply_step(1, "0" * 64, _principal())


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
    def __init__(self, calls: list[tuple[str, list[str]]], *, fail_clear: bool = False) -> None:
        self.calls = calls
        self.fail_clear = fail_clear
        self.closed = 0

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self.calls, fail_clear=self.fail_clear)

    def close(self) -> None:
        self.closed += 1


class _FakePool:
    def __init__(self, connection: _FakeConnection) -> None:
        self.connection = connection
        self.dropped: list[Any] = []

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


@pytest.mark.parametrize("driver_mode", ["thin", "thick"])
def test_deepsec_configuration_accepts_thin_and_thick(driver_mode: str) -> None:
    OraclePoolManager(_settings(driver_mode=driver_mode)).validate_deepsec_configuration()


@pytest.mark.parametrize("driver_mode", ["thin", "thick"])
def test_deepsec_configuration_requires_end_user_password(driver_mode: str) -> None:
    manager = OraclePoolManager(_settings(driver_mode=driver_mode, end_user_password=""))

    with pytest.raises(OracleAdapterError, match="ORACLE_DEEPSEC_END_USER_PASSWORD"):
        manager.validate_deepsec_configuration()


@pytest.mark.parametrize(
    ("driver_mode", "expected_init_calls"),
    [("thin", []), ("thick", ["/opt/oracle/instantclient"])],
)
def test_data_pool_uses_selected_driver_and_end_user_credentials(
    driver_mode: str,
    expected_init_calls: list[str],
) -> None:
    manager = OraclePoolManager(_settings(driver_mode=driver_mode))
    fake_oracledb = _FakeOracleDb()
    manager._oracledb = fake_oracledb

    manager._get_pool(data_plane=True)

    assert fake_oracledb.init_calls == expected_init_calls
    assert fake_oracledb.pool_kwargs == [
        {
            "user": "NL2SQL_APP_END_USER",
            "password": "DeepSecret!123",
            "dsn": "test",
            "tcp_connect_timeout": 5,
            "min": 1,
            "max": 4,
            "increment": 1,
        }
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
