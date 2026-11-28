"""Oracle AI Data Platform の連携の情報（`GET /settings/database/aidp`。#5931）。"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from pr_system_settings.aidp import adb_name_from_dsn, database_user_exists
from pr_system_settings.database import build_database_router

# 合成の値。secret scan が鍵と見なさないよう、組み立てて作る。
SYNTHETIC_SECRET = "-".join(("Synthetic", "Value", "12"))
AIDP_OCID = "ocid1.aidataplatform.oc1..synthetic"
AIDP_URL = "https://aidp.example.invalid/?ocid=" + AIDP_OCID
# Wallet を使わない接続（TLS）の接続記述子。設定の判定（readiness）が Wallet のファイルを要らない。
DSN = (
    "(description=(address=(protocol=tcps)(port=1522)(host=adb.example.invalid))"
    "(connect_data=(service_name=prefix1_appdb_tp.adb.example.invalid)))"
)


class FakeSettings(BaseModel):
    """製品の Settings のうち、データベース設定 API が読む属性だけを持つ。"""

    oracle_user: str = "APP_USER"
    oracle_password: str = SYNTHETIC_SECRET
    oracle_dsn: str = DSN
    oracle_driver_mode: str = "thin"
    oracle_connection_security: str = "walletless_tls"
    oracle_client_lib_dir: str = ""
    oracle_wallet_dir: str = ""
    oracle_wallet_password: str = SYNTHETIC_SECRET
    oracle_adb_ocid: str = ""
    oracle_adb_region: str = ""
    oci_genai_embedding_dim: int = 1536
    oracle_db_test_timeout_seconds: float = 15.0
    aidp_ocid: str = AIDP_OCID
    aidp_region: str = "ap-osaka-1"
    aidp_url: str = AIDP_URL
    aidp_db_user: str = "adb_aidp_user"

    @property
    def resolved_oracle_wallet_dir(self) -> str:
        return self.oracle_wallet_dir

    @property
    def resolved_oracle_adb_region(self) -> str:
        return self.oracle_adb_region


class FakeCursor:
    def __init__(self, users: set[str]) -> None:
        self.users = users
        self.executed: list[tuple[str, dict[str, object]]] = []
        self.row: tuple[int] | None = None
        self.closed = False

    def execute(self, statement: str, parameters: dict[str, object]) -> None:
        self.executed.append((statement, parameters))
        self.row = (1,) if parameters["user_name"] in self.users else None

    def fetchone(self) -> tuple[int] | None:
        return self.row

    def close(self) -> None:
        self.closed = True


class FakeConnection:
    def __init__(self, users: set[str]) -> None:
        self.cursors: list[FakeCursor] = []
        self.users = users

    def cursor(self) -> FakeCursor:
        cursor = FakeCursor(self.users)
        self.cursors.append(cursor)
        return cursor


class Harness:
    """共有 router と、製品が渡す接続の hook の代役。"""

    def __init__(self, tmp_path: Path, *, with_hook: bool = True, **settings: Any) -> None:
        self.settings = FakeSettings(**settings)
        self.connection = FakeConnection({"ADB_AIDP_USER"})
        self.connect_error: Exception | None = None
        self.borrowed = 0

        async def test_connection(_candidate: Any) -> None:
            return None

        async def run_on_connection(_settings: Any, operation: Callable[[Any], Any]) -> Any:
            self.borrowed += 1
            if self.connect_error is not None:
                raise self.connect_error
            return operation(self.connection)

        app = FastAPI()
        app.include_router(
            build_database_router(
                get_settings=lambda: self.settings,
                env_file=lambda: tmp_path / ".env",
                test_connection=test_connection,
                connection_security_enabled=True,
                run_on_connection=run_on_connection if with_hook else None,
            ),
            prefix="/api/settings",
        )
        self.client = TestClient(app)

    def aidp(self) -> dict[str, Any]:
        response = self.client.get("/api/settings/database/aidp")
        assert response.status_code == 200
        self.text = response.text
        data: dict[str, Any] = response.json()["data"]
        return data


def test_not_configured_returns_only_the_flag_and_does_not_touch_the_database(
    tmp_path: Path,
) -> None:
    h = Harness(tmp_path, aidp_ocid="  ")
    assert h.aidp() == {
        "configured": False,
        "url": "",
        "ocid": "",
        "region": "",
        "adb_db_name": "",
        "adb_service_name": "",
        "db_user": "",
        "db_user_status": "unknown",
    }
    assert h.borrowed == 0
    # 画面が区画を出すかを決める値（データベース設定の応答）。
    assert h.client.get("/api/settings/database").json()["data"]["aidp_configured"] is False


def test_configured_returns_the_values_to_register_the_external_catalog(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    assert h.aidp() == {
        "configured": True,
        "url": AIDP_URL,
        "ocid": AIDP_OCID,
        "region": "ap-osaka-1",
        "adb_db_name": "appdb",
        # 3 製品の接続先（DSN）ではなく、分析の読み取りの HIGH のサービスの別名（#6018）。
        "adb_service_name": "appdb_high",
        # 引用符なしで作る DB ユーザーの名前は大文字で保存される。
        "db_user": "ADB_AIDP_USER",
        "db_user_status": "exists",
    }
    assert h.borrowed == 1
    (cursor,) = h.connection.cursors
    assert cursor.executed == [
        ("SELECT 1 FROM ALL_USERS WHERE USERNAME = :user_name", {"user_name": "ADB_AIDP_USER"})
    ]
    assert cursor.closed is True
    assert h.client.get("/api/settings/database").json()["data"]["aidp_configured"] is True


def test_response_has_no_password(tmp_path: Path) -> None:
    """応答に、3 製品の接続・Wallet のパスワードも、パスワードの項目も含めない。"""
    h = Harness(tmp_path)
    data = h.aidp()
    assert SYNTHETIC_SECRET not in h.text
    assert not [key for key in data if "password" in key.lower() or "secret" in key.lower()]


def test_missing_user_is_reported(tmp_path: Path) -> None:
    h = Harness(tmp_path, aidp_db_user="OTHER_USER")
    data = h.aidp()
    assert (data["db_user"], data["db_user_status"]) == ("OTHER_USER", "missing")


def test_unreachable_database_reports_unknown_without_failing(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    h = Harness(tmp_path)
    h.connect_error = RuntimeError("DPY-6005: cannot connect to database host.example.invalid")
    with caplog.at_level(logging.WARNING, logger="pr_system_settings.aidp"):
        data = h.aidp()
    # 設定の値は出したまま、ユーザーの状態だけ「確かめられない」にする。
    assert data["configured"] is True
    assert data["url"] == AIDP_URL
    assert data["db_user_status"] == "unknown"
    # 例外の文（接続先が入り得る）は応答にも log にも出さず、種類とコードだけを残す。
    assert "host.example.invalid" not in h.text
    (record,) = [item for item in caplog.records if item.message == "aidp_db_user_check_failed"]
    assert record.__dict__["exception_type"] == "RuntimeError"
    assert record.__dict__["oracle_error_codes"] == "DPY-6005"
    assert "host.example.invalid" not in caplog.text


def test_check_timeout_reports_unknown(tmp_path: Path) -> None:
    h = Harness(tmp_path, oracle_db_test_timeout_seconds=0.01)

    async def never(_settings: Any, _operation: Callable[[Any], Any]) -> Any:
        await asyncio.sleep(30)

    app = FastAPI()
    app.include_router(
        build_database_router(
            get_settings=lambda: h.settings,
            env_file=lambda: tmp_path / ".env",
            test_connection=never,  # type: ignore[arg-type]
            connection_security_enabled=True,
            run_on_connection=never,
        ),
        prefix="/api/settings",
    )
    data = TestClient(app).get("/api/settings/database/aidp").json()["data"]
    assert data["db_user_status"] == "unknown"


@pytest.mark.parametrize(
    ("changes", "with_hook"),
    [
        ({"aidp_db_user": ""}, True),  # ユーザーの名前が未設定
        ({"oracle_dsn": ""}, True),  # DB の接続が未設定（接続を試さない）
        ({}, False),  # 製品が接続の hook を渡していない
    ],
    ids=["no-user-name", "database-not-configured", "no-hook"],
)
def test_user_is_not_checked_when_it_cannot_be(
    tmp_path: Path, changes: dict[str, Any], with_hook: bool
) -> None:
    h = Harness(tmp_path, with_hook=with_hook, **changes)
    assert h.aidp()["db_user_status"] == "unknown"
    assert h.borrowed == 0


def test_url_is_returned_only_when_it_is_an_https_url(tmp_path: Path) -> None:
    """画面がリンクにするため、https の絶対 URL だけを返す。"""
    for value in ("javascript:alert(1)", "http://aidp.example.invalid/", "aidp.example.invalid"):
        assert Harness(tmp_path, aidp_url=value).aidp()["url"] == ""


@pytest.mark.parametrize(
    ("dsn", "expected"),
    [
        ("appdb_high", "appdb"),
        ("APPDB_TPURGENT", "APPDB"),
        (
            "(description=(retry_count=20)(address=(protocol=tcps)(port=1522)(host=adb.example.invalid))"
            "(connect_data=(service_name=prefix1_appdb_medium.adb.example.invalid))"
            "(security=(ssl_server_dn_match=yes)))",
            "appdb",
        ),
        ("adb.example.invalid:1522/prefix1_appdb_low.adb.example.invalid", "appdb"),
        # 読めない形は空（推測で埋めない）。
        ("", ""),
        ("appdb", ""),
        ("localhost:1521/FREEPDB1", ""),
        ("(description=(address=(host=adb.example.invalid)))", ""),
    ],
    ids=[
        "alias",
        "alias-upper",
        "descriptor",
        "easy-connect",
        "empty",
        "no-suffix",
        "not-adb",
        "no-service-name",
    ],
)
def test_adb_name_from_dsn(dsn: str, expected: str) -> None:
    assert adb_name_from_dsn(dsn) == expected


def test_database_user_exists_closes_the_cursor_on_error() -> None:
    class FailingCursor(FakeCursor):
        def execute(self, statement: str, parameters: dict[str, object]) -> None:
            raise RuntimeError("ORA-00942")

    cursor = FailingCursor(set())

    class Connection:
        def cursor(self) -> FakeCursor:
            return cursor

    with pytest.raises(RuntimeError):
        database_user_exists(Connection(), "ADB_AIDP_USER")
    assert cursor.closed is True
