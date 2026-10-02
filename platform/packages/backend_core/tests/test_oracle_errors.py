"""Oracle のエラーの分類（#820）。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from pr_backend_core.oracle_errors import (
    is_oracle_connection_error,
    is_oracle_pool_wait_timeout,
    oracle_error_codes,
)


@pytest.mark.parametrize(
    "message",
    [
        "DPY-4005: timed out waiting for the connection pool to return a connection",
        "DPY-6005: cannot connect to database. [Errno 111] Connection refused",
        "DPY-4011: the database or network closed the connection",
        "ORA-12541: TNS:no listener",
        "ORA-12170: TNS:Connect timeout occurred",
        "ORA-03113: end-of-file on communication channel",
        "ORA-03114: not connected to ORACLE",
        "ORA-01033: ORACLE initialization or shutdown in progress",
        "ORA-01017: invalid credential or not authorized; logon denied",
    ],
)
def test_connection_errors(message: str) -> None:
    assert is_oracle_connection_error(RuntimeError(message))


@pytest.mark.parametrize(
    "message",
    [
        "ORA-00942: table or view does not exist",
        "ORA-01031: insufficient privileges",
        "ORA-00904: invalid identifier",
        "KeyError: status",
    ],
)
def test_schema_errors_are_not_connection_errors(message: str) -> None:
    assert not is_oracle_connection_error(RuntimeError(message))


def test_timeout_without_code_is_connection_error() -> None:
    class OracleConnectionTimeoutError(RuntimeError):
        pass

    assert is_oracle_connection_error(TimeoutError())
    assert is_oracle_connection_error(OracleConnectionTimeoutError("probe"))


def test_codes_follow_the_cause_chain() -> None:
    """製品が包んだ例外（`raise ... from exc`）も、原因のコードで分類する。"""
    try:
        try:
            raise RuntimeError("DPY-4005: timed out waiting for the connection pool")
        except RuntimeError as inner:
            raise ValueError("システムテーブルの状態を取得できません") from inner
    except ValueError as outer:
        assert oracle_error_codes(outer) == ["DPY-4005"]
        assert is_oracle_pool_wait_timeout(outer)
        assert is_oracle_connection_error(outer)


def test_full_code_of_driver_error_object() -> None:
    error = SimpleNamespace(full_code="DPY-6005")
    assert oracle_error_codes(Exception(error)) == ["DPY-6005"]
