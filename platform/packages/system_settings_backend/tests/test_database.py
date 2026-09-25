from __future__ import annotations

import io
import stat
from pathlib import Path
from typing import Any
from zipfile import ZipFile, ZipInfo

import pytest
from dotenv import dotenv_values
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from pr_system_settings import database as shared_db
from pr_system_settings.database import build_database_router
from pr_system_settings.oci_database import AutonomousDatabaseInfo

PEM = "-----BEGIN PRIVATE KEY-----\nMIIB\n-----END PRIVATE KEY-----\n"
TNSNAMES = "ragdb_high = (description=(address=(protocol=tcps)(port=1522)(host=adb.example)))\n"


class FakeSettings(BaseModel):
    """製品の Settings のうちデータベース設定 API が読む属性だけを持つ。"""

    oracle_user: str = ""
    oracle_password: str = ""
    oracle_dsn: str = ""
    oracle_driver_mode: str = "thin"
    oracle_connection_security: str = "wallet_mtls"
    oracle_client_lib_dir: str = ""
    oracle_wallet_dir: str = ""
    oracle_wallet_password: str = ""
    oracle_adb_ocid: str = ""
    oracle_adb_region: str = ""
    oci_genai_embedding_dim: int = 1536
    oracle_db_test_timeout_seconds: float = 15.0

    @property
    def resolved_oracle_wallet_dir(self) -> str:
        return self.oracle_wallet_dir

    @property
    def resolved_oracle_adb_region(self) -> str:
        return self.oracle_adb_region


def wallet_zip(files: dict[str, str] | None = None, *, prefix: str = "") -> bytes:
    buffer = io.BytesIO()
    content = files or {"tnsnames.ora": TNSNAMES, "ewallet.pem": PEM, "README": "x"}
    with ZipFile(buffer, "w") as archive:
        for name, text in content.items():
            archive.writestr(prefix + name, text)
    return buffer.getvalue()


class Harness:
    def __init__(self, tmp_path: Path, **kwargs: Any) -> None:
        self.settings = FakeSettings(oracle_wallet_dir=str(tmp_path / "wallet"))
        self.env_file = tmp_path / ".env"
        self.saved: list[str] = []
        self.connect_error: Exception | None = None
        self.connected: list[FakeSettings] = []

        async def test_connection(candidate: Any) -> None:
            self.connected.append(candidate)
            if self.connect_error is not None:
                raise self.connect_error

        app = FastAPI()
        app.include_router(
            build_database_router(
                get_settings=lambda: self.settings,
                env_file=lambda: self.env_file,
                test_connection=test_connection,
                on_saved=lambda settings: self.saved.append(settings.oracle_user),
                **kwargs,
            ),
            prefix="/api/settings",
        )
        self.client = TestClient(app)

    def upload(self, data: bytes, name: str = "wallet.zip") -> Any:
        return self.client.post(
            "/api/settings/database/wallet",
            files={"file": (name, data, "application/zip")},
        )


def test_patch_persists_then_applies_and_keeps_secret_out_of_response(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    resp = h.client.patch(
        "/api/settings/database",
        json={"user": "app", "dsn": "ragdb_high", "password": "db-secret"},
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["has_password"] is True
    assert "db-secret" not in resp.text
    env = dotenv_values(h.env_file)
    assert env["ORACLE_USER"] == "app"
    assert env["ORACLE_PASSWORD"] == "db-secret"
    assert env["ORACLE_DRIVER_MODE"] == "thin"
    assert env["ORACLE_CONNECTION_SECURITY"] == "wallet_mtls"
    assert h.settings.oracle_password == "db-secret"
    assert h.saved == ["app"]

    # 空欄は保持、clear は削除。
    h.client.patch("/api/settings/database", json={"user": "app", "dsn": "ragdb_high"})
    assert h.settings.oracle_password == "db-secret"
    h.client.patch(
        "/api/settings/database",
        json={"user": "app", "dsn": "ragdb_high", "clear_password": True},
    )
    assert h.settings.oracle_password == ""


def test_patch_failure_keeps_runtime(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    h.env_file = tmp_path  # directory なので書き込みに失敗する
    resp = h.client.patch("/api/settings/database", json={"user": "app", "dsn": "x"})
    assert resp.status_code == 500
    assert h.settings.oracle_user == ""
    assert h.saved == []


def test_connection_security_is_ignored_unless_enabled(tmp_path: Path) -> None:
    disabled = Harness(tmp_path / "a")
    disabled.client.patch(
        "/api/settings/database",
        json={"user": "u", "dsn": "d", "connection_security": "walletless_tls"},
    )
    assert disabled.settings.oracle_connection_security == "wallet_mtls"

    enabled = Harness(tmp_path / "b", connection_security_enabled=True)
    enabled.client.patch(
        "/api/settings/database",
        json={"user": "u", "dsn": "d", "connection_security": "walletless_tls"},
    )
    assert enabled.settings.oracle_connection_security == "walletless_tls"


def test_wallet_upload_installs_securely_and_lists_services(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    resp = h.upload(wallet_zip(prefix="Wallet_ragdb/"))
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["wallet_uploaded"] is True
    assert data["available_services"] == ["ragdb_high"]
    wallet = tmp_path / "wallet"
    assert stat.S_IMODE(wallet.stat().st_mode) == 0o700
    assert stat.S_IMODE((wallet / "ewallet.pem").stat().st_mode) == 0o600
    assert not (wallet / "README").exists()
    assert h.saved == [""]


@pytest.mark.parametrize(
    ("data", "name", "status"),
    [
        (wallet_zip(), "wallet.txt", 415),
        (b"", "wallet.zip", 400),
        (b"not a zip", "wallet.zip", 400),
        (wallet_zip({"tnsnames.ora": TNSNAMES}), "wallet.zip", 400),
        (wallet_zip({"../evil.ora": "x"}), "wallet.zip", 400),
    ],
)
def test_wallet_upload_rejects_unsafe_files_and_keeps_previous_wallet(
    tmp_path: Path, data: bytes, name: str, status: int
) -> None:
    h = Harness(tmp_path)
    assert h.upload(wallet_zip()).status_code == 200
    resp = h.upload(data, name)
    assert resp.status_code == status
    assert (tmp_path / "wallet" / "ewallet.pem").read_text() == PEM
    assert not (tmp_path / "evil.ora").exists()


def test_wallet_upload_rejects_symlink(tmp_path: Path) -> None:
    buffer = io.BytesIO()
    with ZipFile(buffer, "w") as archive:
        info = ZipInfo("link")
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(info, "/etc/passwd")
    assert Harness(tmp_path).upload(buffer.getvalue()).status_code == 400


def test_wallet_upload_returns_409_while_another_install_runs(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    with shared_db.database_wallet_install_lock(h.settings):
        assert h.upload(wallet_zip()).status_code == 409


def test_readiness_values(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    settings = h.settings
    assert shared_db.database_readiness(settings) == "missing"
    settings.oracle_user, settings.oracle_dsn = "app", "ragdb_high"
    assert shared_db.database_readiness(settings) == "wallet_not_found"
    h.upload(wallet_zip())
    assert shared_db.database_readiness(settings) == "ok"
    settings.oracle_dsn = "other_high"
    assert shared_db.database_readiness(settings) == "invalid"
    settings.oracle_connection_security = "walletless_tls"
    assert shared_db.database_readiness(settings) == "walletless_tls_dsn_required"
    assert shared_db.database_readiness(settings, lambda _s: "invalid_configuration") == (
        "invalid_configuration"
    )


def test_connection_test_uses_unsaved_values_and_explains_oracle_errors(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    h.upload(wallet_zip())
    body = {"user": "app", "dsn": "ragdb_high", "password": "unsaved"}

    ok = h.client.post("/api/settings/database/test", json=body).json()["data"]
    assert ok["status"] == "success"
    assert h.connected[-1].oracle_password == "unsaved"
    assert h.settings.oracle_user == ""  # 保存していない

    h.connect_error = RuntimeError("ORA-01017: invalid username/password")
    failed = h.client.post("/api/settings/database/test", json=body).json()["data"]
    assert failed["status"] == "failed"
    assert "ORA-01017" in failed["message"]
    assert failed["details"]["oracle_error_codes"] == "ORA-01017"
    assert any("DB パスワード" in tip for tip in failed["troubleshooting"])

    not_ready = h.client.post("/api/settings/database/test", json={"user": "", "dsn": ""}).json()[
        "data"
    ]
    assert not_ready["readiness"] == "missing"


def test_password_reveal_only_when_enabled(tmp_path: Path) -> None:
    disabled = Harness(tmp_path / "a")
    assert disabled.client.post("/api/settings/database/password/reveal").status_code in {404, 405}

    enabled = Harness(tmp_path / "b", password_reveal_enabled=True)
    assert enabled.client.post("/api/settings/database/password/reveal").status_code == 404
    enabled.settings.oracle_password = "db-secret"
    resp = enabled.client.post("/api/settings/database/password/reveal")
    assert resp.json()["data"]["password"] == "db-secret"
    assert resp.headers["cache-control"] == "no-store"


class FakeOciDatabaseClient:
    state = "STOPPED"
    fail = False
    started: list[str] = []

    def __init__(self, settings: Any) -> None:
        self.settings = settings

    async def get_autonomous_database(self, adb_ocid: str) -> AutonomousDatabaseInfo:
        if self.fail:
            raise RuntimeError("ServiceError: secret-looking OCI detail")
        return AutonomousDatabaseInfo(
            id=adb_ocid,
            display_name="ragdb",
            lifecycle_state=self.state,
            db_name="RAGDB",
            cpu_core_count=2,
            data_storage_size_in_tbs=1.0,
        )

    async def start_autonomous_database(self, adb_ocid: str) -> None:
        self.started.append(adb_ocid)

    async def stop_autonomous_database(self, adb_ocid: str) -> None:
        self.started.append("stop:" + adb_ocid)


def test_adb_settings_start_stop_and_hidden_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(shared_db, "OciDatabaseClient", FakeOciDatabaseClient)
    h = Harness(tmp_path)
    assert h.client.get("/api/settings/database/adb").json()["data"]["error_code"] == (
        "ADB_NOT_CONFIGURED"
    )

    saved = h.client.post(
        "/api/settings/database/adb/settings",
        json={"adb_ocid": "ocid1.autonomousdatabase.oc1..x", "region": "ap-osaka-1"},
    ).json()["data"]
    assert saved["lifecycle_state"] == "STOPPED"
    assert dotenv_values(h.env_file)["ORACLE_ADB_REGION"] == "ap-osaka-1"

    started = h.client.post("/api/settings/database/adb/start").json()["data"]
    assert started["status"] == "accepted"
    assert started["lifecycle_state"] == "STARTING"
    stop = h.client.post("/api/settings/database/adb/stop").json()["data"]
    assert stop["status"] == "already_stopped"

    FakeOciDatabaseClient.fail = True
    try:
        failed = h.client.get("/api/settings/database/adb").json()["data"]
    finally:
        FakeOciDatabaseClient.fail = False
    assert failed["error_code"] == "ADB_INFO_UNAVAILABLE"
    assert "secret-looking" not in failed["message"]
