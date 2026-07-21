from __future__ import annotations

import asyncio
import json
import stat
import string
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from zipfile import ZipFile, ZipInfo

import httpx
import pytest
from pytest import MonkeyPatch

from app.clients.oci_database import (
    AutonomousDatabaseInfo,
    OciDatabaseClient,
    WalletDownloadTooLargeError,
)
from app.features.nl2sql.oracle_adapter import OracleNl2SqlAdapter
from app.features.settings import router as settings_router
from app.main import app
from app.settings import Settings, get_settings, load_persisted_model_settings


class _AsgiTestClient:
    """httpx 0.x と Starlette 1.x の同期 TestClient 非互換を避ける薄い test client。"""

    @staticmethod
    def _request(method: str, path: str, **kwargs: Any) -> httpx.Response:
        async def send() -> httpx.Response:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://test",
            ) as async_client:
                return await async_client.request(method, path, **kwargs)

        return asyncio.run(send())

    def post(self, path: str, **kwargs: Any) -> httpx.Response:
        return self._request("POST", path, **kwargs)

    def patch(self, path: str, **kwargs: Any) -> httpx.Response:
        return self._request("PATCH", path, **kwargs)


client = _AsgiTestClient()


def test_model_settings_vision_test_image_is_valid_jpeg() -> None:
    data = settings_router.MODEL_TEST_IMAGE_BYTES

    assert data.startswith(b"\xff\xd8")
    assert len(data) > 1024


def test_read_object_storage_namespace_uses_oci_sdk(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, Any] = {}

    class FakeObjectStorageClient:
        def __init__(self, config: dict[str, Any]) -> None:
            captured["config"] = config

        def get_namespace(self) -> object:
            captured["get_namespace_called"] = True
            return SimpleNamespace(data="mytenancynamespace")

    def fake_from_file(path: str, profile: str) -> dict[str, Any]:
        captured["config_path"] = path
        captured["profile"] = profile
        return {"region": "ap-tokyo-1"}

    def fake_import_module(name: str) -> object:
        if name == "oci.config":
            return SimpleNamespace(from_file=fake_from_file)
        if name == "oci.object_storage":
            return SimpleNamespace(ObjectStorageClient=FakeObjectStorageClient)
        raise AssertionError(f"unexpected module import: {name}")

    # importlib.import_module 自体を書き換えると、TestClient/AnyIO の遅延 import まで
    # 偽装されて request が待ち続けるため、router が参照する module だけを差し替える。
    monkeypatch.setattr(
        settings_router,
        "importlib",
        SimpleNamespace(import_module=fake_import_module),
    )
    config_file = tmp_path / "config"

    resp = client.post(
        "/api/settings/oci/object-storage/namespace",
        json={
            "config_file": str(config_file),
            "profile": "DEFAULT",
            "region": "ap-osaka-1",
        },
    )

    assert resp.status_code == 200
    assert resp.json()["data"] == {"namespace": "mytenancynamespace"}
    assert captured["config_path"] == str(config_file)
    assert captured["profile"] == "DEFAULT"
    assert captured["config"] == {"region": "ap-osaka-1"}
    assert captured["get_namespace_called"] is True


def test_read_object_storage_namespace_reports_oci_errors(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    def fake_import_module(name: str) -> object:
        if name == "oci.config":
            return SimpleNamespace(from_file=lambda path, profile: {"region": "ap-tokyo-1"})
        if name == "oci.object_storage":
            raise RuntimeError("sdk unavailable")
        raise AssertionError(f"unexpected module import: {name}")

    monkeypatch.setattr(
        settings_router,
        "importlib",
        SimpleNamespace(import_module=fake_import_module),
    )

    resp = client.post(
        "/api/settings/oci/object-storage/namespace",
        json={
            "config_file": str(tmp_path / "config"),
            "profile": "DEFAULT",
            "region": "ap-osaka-1",
        },
    )

    assert resp.status_code == 502
    assert "namespace を取得できませんでした" in resp.text


def test_read_object_storage_namespace_refuses_encrypted_private_key_without_prompt(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    key_file = tmp_path / "encrypted.pem"
    key_file.write_text(
        "-----BEGIN ENCRYPTED PRIVATE KEY-----\nabc\n-----END ENCRYPTED PRIVATE KEY-----\n",
        encoding="utf-8",
    )
    initialized = False

    class FakeObjectStorageClient:
        def __init__(self, config: dict[str, Any]) -> None:
            nonlocal initialized
            initialized = True

        def get_namespace(self) -> object:
            return SimpleNamespace(data="mytenancynamespace")

    def fake_import_module(name: str) -> object:
        if name == "oci.config":
            return SimpleNamespace(
                from_file=lambda path, profile: {"key_file": str(key_file), "region": "ap-tokyo-1"}
            )
        if name == "oci.object_storage":
            return SimpleNamespace(ObjectStorageClient=FakeObjectStorageClient)
        raise AssertionError(f"unexpected module import: {name}")

    monkeypatch.setattr(
        settings_router,
        "importlib",
        SimpleNamespace(import_module=fake_import_module),
    )

    resp = client.post(
        "/api/settings/oci/object-storage/namespace",
        json={
            "config_file": str(tmp_path / "config"),
            "profile": "DEFAULT",
            "region": "ap-osaka-1",
        },
    )

    assert resp.status_code == 502
    assert "暗号化されています" in resp.text
    assert initialized is False


def test_update_oci_settings_writes_config_and_env(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    env_file = tmp_path / ".env"
    env_file.write_text("KEEP_ME=1\nOCI_REGION=old-region\n", encoding="utf-8")
    settings = get_settings()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(settings_router, "BACKEND_ENV_FILE", env_file)
    monkeypatch.setattr(settings, "oci_config_file", "~/.oci/config")
    monkeypatch.setattr(settings, "oci_config_profile", "DEFAULT")

    resp = client.patch(
        "/api/settings/oci",
        json={
            "user": "ocid1.user.oc1..example",
            "fingerprint": "aa:bb:cc",
            "tenancy": "ocid1.tenancy.oc1..example",
            "region": "ap-osaka-1",
        },
    )

    assert resp.status_code == 200
    config_file = home / ".oci" / "config"
    config_text = config_file.read_text(encoding="utf-8")
    assert "user=ocid1.user.oc1..example" in config_text
    assert "fingerprint=aa:bb:cc" in config_text
    assert "tenancy=ocid1.tenancy.oc1..example" in config_text
    assert "region=ap-osaka-1" in config_text
    assert "key_file=~/.oci/oci_api_key.pem" in config_text
    assert stat.S_IMODE(config_file.stat().st_mode) == 0o600
    assert stat.S_IMODE(config_file.parent.stat().st_mode) == 0o700

    env_text = env_file.read_text(encoding="utf-8")
    assert "KEEP_ME=1" in env_text
    assert "OCI_CONFIG_FILE=~/.oci/config" in env_text
    assert "OCI_CONFIG_PROFILE=DEFAULT" in env_text
    assert "OCI_REGION=ap-osaka-1" in env_text


def test_update_oci_settings_does_not_write_empty_config_defaults(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    env_file = tmp_path / ".env"
    env_file.write_text("KEEP_ME=1\nOCI_REGION=old-region\n", encoding="utf-8")
    settings = get_settings()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(settings_router, "BACKEND_ENV_FILE", env_file)
    monkeypatch.setattr(settings, "oci_config_file", "~/.oci/config")
    monkeypatch.setattr(settings, "oci_config_profile", "DEFAULT")
    monkeypatch.setattr(settings, "oci_region", "us-chicago-1")

    resp = client.patch(
        "/api/settings/oci",
        json={"user": "", "fingerprint": "", "tenancy": "", "region": ""},
    )

    assert resp.status_code == 200
    config_text = (home / ".oci" / "config").read_text(encoding="utf-8")
    assert "user=" not in config_text
    assert "fingerprint=" not in config_text
    assert "tenancy=" not in config_text
    assert "region=" not in config_text
    assert "key_file=" not in config_text
    assert settings.oci_region == ""
    env_text = env_file.read_text(encoding="utf-8")
    assert "KEEP_ME=1" in env_text
    assert "OCI_REGION" not in env_text


def test_read_oci_config_reports_missing_profile(tmp_path: Path) -> None:
    config_file = tmp_path / "config"
    config_file.write_text(
        "[DEFAULT]\n"
        "user=ocid1.user.oc1..example\n"
        "fingerprint=aa:bb:cc\n"
        "tenancy=ocid1.tenancy.oc1..example\n"
        "region=ap-osaka-1\n",
        encoding="utf-8",
    )

    resp = client.post(
        "/api/settings/oci/config/read",
        json={"config_file": str(config_file), "profile": "MISSING"},
    )

    assert resp.status_code == 404
    assert "profile が見つかりません" in resp.text


def test_upload_oci_private_key_saves_fixed_secure_path(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))

    resp = client.post(
        "/api/settings/oci/key-file",
        files={
            "file": (
                "oci_api_key.pem",
                b"-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----\n",
                "application/x-pem-file",
            )
        },
    )

    assert resp.status_code == 200
    assert resp.json()["data"] == {"key_file": "~/.oci/oci_api_key.pem", "saved": True}
    key_file = home / ".oci" / "oci_api_key.pem"
    assert key_file.read_text(encoding="utf-8").startswith("-----BEGIN PRIVATE KEY-----")
    assert stat.S_IMODE(key_file.stat().st_mode) == 0o600
    assert stat.S_IMODE(key_file.parent.stat().st_mode) == 0o700


def test_upload_database_wallet_extracts_to_resolved_wallet_dir(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = get_settings()
    client_lib_dir = tmp_path / "instantclient_23_26"
    monkeypatch.setattr(settings, "oracle_client_lib_dir", str(client_lib_dir))
    monkeypatch.setattr(settings, "oracle_wallet_dir", "")

    wallet_zip = _wallet_zip_bytes()
    resp = client.post(
        "/api/settings/database/wallet",
        files={"file": ("Wallet_MYDB.zip", wallet_zip, "application/zip")},
    )

    assert resp.status_code == 200
    data = resp.json()["data"]
    wallet_dir = client_lib_dir / "network" / "admin"
    assert data["wallet_dir"] == str(wallet_dir)
    assert data["wallet_uploaded"] is True
    assert "mydb_high" in data["available_services"]
    assert (wallet_dir / "tnsnames.ora").is_file()
    assert not (wallet_dir / "readme").exists()
    assert stat.S_IMODE(wallet_dir.stat().st_mode) == 0o700
    for file_name in settings_router.ORACLE_WALLET_REQUIRED_FILES:
        assert stat.S_IMODE((wallet_dir / file_name).stat().st_mode) == 0o600


def test_database_wallet_state_requires_all_four_files(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = get_settings()
    client_lib_dir = tmp_path / "instantclient"
    wallet_dir = client_lib_dir / "network" / "admin"
    wallet_dir.mkdir(parents=True)
    (wallet_dir / "tnsnames.ora").write_text("mydb_high = (...)\n", encoding="utf-8")
    monkeypatch.setattr(settings, "oracle_client_lib_dir", str(client_lib_dir))
    monkeypatch.setattr(settings, "oracle_wallet_dir", "")

    data = settings_router._database_settings_data(settings)

    assert data.wallet_uploaded is False
    assert data.available_services == []


def test_download_database_wallet_repairs_partial_serverless_wallet(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = _configure_wallet_download(monkeypatch, tmp_path)
    wallet_dir = Path(settings.resolved_oracle_wallet_dir)
    wallet_dir.mkdir(parents=True)
    (wallet_dir / "tnsnames.ora").write_text("partial", encoding="utf-8")
    monkeypatch.setattr(settings, "oracle_password", "database-secret")
    monkeypatch.setattr(settings, "oracle_wallet_password", "saved-wallet-secret")
    captured: dict[str, Any] = {}

    class FakeDatabaseClient:
        def __init__(self, settings: Settings) -> None:
            self.settings = settings

        async def get_autonomous_database(self, adb_ocid: str) -> AutonomousDatabaseInfo:
            captured["get_ocid"] = adb_ocid
            return _adb_info(adb_ocid, is_dedicated=False)

        async def download_autonomous_database_wallet(
            self,
            adb_ocid: str,
            password: str,
            generate_type: str | None,
            max_bytes: int,
        ) -> bytes:
            captured.update(
                adb_ocid=adb_ocid,
                password=password,
                generate_type=generate_type,
                max_bytes=max_bytes,
            )
            return _wallet_zip_bytes()

    pool_closed: list[bool] = []
    monkeypatch.setattr(settings_router, "OciDatabaseClient", FakeDatabaseClient)
    monkeypatch.setattr(settings_router, "close_oracle_pool", lambda: pool_closed.append(True))

    resp = client.post("/api/settings/database/wallet/download")

    assert resp.status_code == 200
    body = resp.json()
    assert body["data"]["status"] == "downloaded"
    assert body["data"]["settings"]["wallet_uploaded"] is True
    assert body["data"]["settings"]["available_services"] == ["mydb_high"]
    assert captured["get_ocid"] == settings.oracle_adb_ocid
    assert captured["generate_type"] == "SINGLE"
    assert captured["max_bytes"] == settings_router.ORACLE_WALLET_MAX_BYTES
    password = str(captured["password"])
    assert len(password) == settings_router.ORACLE_WALLET_GENERATED_PASSWORD_LENGTH
    assert any(char in string.ascii_lowercase for char in password)
    assert any(char in string.ascii_uppercase for char in password)
    assert any(char in string.digits for char in password)
    assert any(char in settings_router.ORACLE_WALLET_PASSWORD_SPECIALS for char in password)
    assert password not in {settings.oracle_password, settings.oracle_wallet_password}
    assert password not in resp.text
    assert pool_closed == [True]
    assert stat.S_IMODE(wallet_dir.stat().st_mode) == 0o700
    for file_name in settings_router.ORACLE_WALLET_REQUIRED_FILES:
        assert stat.S_IMODE((wallet_dir / file_name).stat().st_mode) == 0o600


def test_download_database_wallet_omits_generate_type_for_dedicated(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    _configure_wallet_download(monkeypatch, tmp_path)
    captured: dict[str, Any] = {}

    class FakeDatabaseClient:
        def __init__(self, settings: Settings) -> None:
            self.settings = settings

        async def get_autonomous_database(self, adb_ocid: str) -> AutonomousDatabaseInfo:
            return _adb_info(adb_ocid, is_dedicated=True)

        async def download_autonomous_database_wallet(
            self,
            adb_ocid: str,
            password: str,
            generate_type: str | None,
            max_bytes: int,
        ) -> bytes:
            captured["generate_type"] = generate_type
            return _wallet_zip_bytes()

    monkeypatch.setattr(settings_router, "OciDatabaseClient", FakeDatabaseClient)

    resp = client.post("/api/settings/database/wallet/download")

    assert resp.status_code == 200
    assert captured["generate_type"] is None


def test_download_database_wallet_is_idempotent_when_complete(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = _configure_wallet_download(monkeypatch, tmp_path)
    settings_router._install_database_wallet(settings, _wallet_zip_bytes(), "wallet.zip")

    class UnexpectedDatabaseClient:
        def __init__(self, settings: Settings) -> None:
            raise AssertionError("complete Wallet must not call OCI")

    monkeypatch.setattr(settings_router, "OciDatabaseClient", UnexpectedDatabaseClient)

    resp = client.post("/api/settings/database/wallet/download")

    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "already_configured"


def test_download_database_wallet_requires_adb_ocid(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = _configure_wallet_download(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "oracle_adb_ocid", "")

    resp = client.post("/api/settings/database/wallet/download")

    assert resp.status_code == 422
    assert "ADB OCID" in resp.text
    assert "手動アップロード" in resp.text


def test_download_database_wallet_requires_complete_oci_configuration(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = _configure_wallet_download(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "oci_config_file", str(tmp_path / "missing-config"))

    resp = client.post("/api/settings/database/wallet/download")

    assert resp.status_code == 422
    assert "OCI 認証設定" in resp.text
    assert "手動アップロード" in resp.text


def test_download_database_wallet_maps_oci_error_without_leaking_details(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    _configure_wallet_download(monkeypatch, tmp_path)

    class FailingDatabaseClient:
        def __init__(self, settings: Settings) -> None:
            self.settings = settings

        async def get_autonomous_database(self, adb_ocid: str) -> AutonomousDatabaseInfo:
            raise RuntimeError("SDK secret detail must not leak")

    monkeypatch.setattr(settings_router, "OciDatabaseClient", FailingDatabaseClient)

    resp = client.post("/api/settings/database/wallet/download")

    assert resp.status_code == 502
    assert "IAM 権限" in resp.text
    assert "SDK secret detail" not in resp.text


def test_download_database_wallet_rejects_invalid_upstream_zip(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    _configure_wallet_download(monkeypatch, tmp_path)

    class InvalidWalletClient:
        def __init__(self, settings: Settings) -> None:
            self.settings = settings

        async def get_autonomous_database(self, adb_ocid: str) -> AutonomousDatabaseInfo:
            return _adb_info(adb_ocid, is_dedicated=False)

        async def download_autonomous_database_wallet(
            self,
            adb_ocid: str,
            password: str,
            generate_type: str | None,
            max_bytes: int,
        ) -> bytes:
            return b"not-a-wallet-zip"

    monkeypatch.setattr(settings_router, "OciDatabaseClient", InvalidWalletClient)

    resp = client.post("/api/settings/database/wallet/download")

    assert resp.status_code == 502
    assert "内容を検証できませんでした" in resp.text


def test_download_database_wallet_maps_stream_limit_to_413(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    _configure_wallet_download(monkeypatch, tmp_path)

    class OversizedWalletClient:
        def __init__(self, settings: Settings) -> None:
            self.settings = settings

        async def get_autonomous_database(self, adb_ocid: str) -> AutonomousDatabaseInfo:
            return _adb_info(adb_ocid, is_dedicated=False)

        async def download_autonomous_database_wallet(
            self,
            adb_ocid: str,
            password: str,
            generate_type: str | None,
            max_bytes: int,
        ) -> bytes:
            raise WalletDownloadTooLargeError

    monkeypatch.setattr(settings_router, "OciDatabaseClient", OversizedWalletClient)

    resp = client.post("/api/settings/database/wallet/download")

    assert resp.status_code == 413
    assert "20 MB" in resp.text


def test_database_wallet_install_lock_returns_conflict(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = _configure_wallet_download(monkeypatch, tmp_path)

    with settings_router._database_wallet_install_lock(settings):
        resp = client.post(
            "/api/settings/database/wallet",
            files={"file": ("wallet.zip", _wallet_zip_bytes(), "application/zip")},
        )

    assert resp.status_code == 409
    assert "処理中" in resp.text


def test_database_wallet_install_failure_restores_existing_wallet(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = _configure_wallet_download(monkeypatch, tmp_path)
    wallet_dir = settings_router._install_database_wallet(
        settings,
        _wallet_zip_bytes(),
        "old-wallet.zip",
    )
    old_tnsnames = (wallet_dir / "tnsnames.ora").read_bytes()

    def fail_permissions(path: Path) -> None:
        raise OSError("simulated chmod failure")

    monkeypatch.setattr(settings_router, "_secure_database_wallet", fail_permissions)

    resp = client.post(
        "/api/settings/database/wallet",
        files={"file": ("new-wallet.zip", _wallet_zip_bytes(), "application/zip")},
    )

    assert resp.status_code == 500
    assert (wallet_dir / "tnsnames.ora").read_bytes() == old_tnsnames
    assert not list(wallet_dir.parent.glob(f".{wallet_dir.name}.backup-*"))


def test_upload_database_wallet_rejects_zip_slip_without_overwriting(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = _configure_wallet_download(monkeypatch, tmp_path)
    wallet_dir = settings_router._install_database_wallet(
        settings,
        _wallet_zip_bytes(),
        "old-wallet.zip",
    )
    old_tnsnames = (wallet_dir / "tnsnames.ora").read_bytes()
    unsafe_zip = BytesIO()
    with ZipFile(unsafe_zip, "w") as archive:
        archive.writestr("../tnsnames.ora", "unsafe")

    resp = client.post(
        "/api/settings/database/wallet",
        files={"file": ("unsafe.zip", unsafe_zip.getvalue(), "application/zip")},
    )

    assert resp.status_code == 400
    assert "安全でないファイルパス" in resp.text
    assert (wallet_dir / "tnsnames.ora").read_bytes() == old_tnsnames


def test_upload_database_wallet_rejects_symlink_member(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    _configure_wallet_download(monkeypatch, tmp_path)
    unsafe_zip = BytesIO()
    with ZipFile(unsafe_zip, "w") as archive:
        symlink = ZipInfo("Wallet_MYDB/cwallet.sso")
        symlink.create_system = 3
        symlink.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(symlink, "tnsnames.ora")

    resp = client.post(
        "/api/settings/database/wallet",
        files={"file": ("symlink.zip", unsafe_zip.getvalue(), "application/zip")},
    )

    assert resp.status_code == 400
    assert "シンボリックリンク" in resp.text


def test_upload_database_wallet_enforces_extracted_size_limit(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    _configure_wallet_download(monkeypatch, tmp_path)
    monkeypatch.setattr(settings_router, "ORACLE_WALLET_MAX_EXTRACTED_BYTES", 8)

    resp = client.post(
        "/api/settings/database/wallet",
        files={"file": ("wallet.zip", _wallet_zip_bytes(), "application/zip")},
    )

    assert resp.status_code == 413
    assert "展開後サイズ" in resp.text


def test_oci_database_wallet_streaming_applies_limit_and_model_parameters() -> None:
    captured: list[dict[str, str]] = []

    class FakeRaw:
        def __init__(self, chunks: list[bytes]) -> None:
            self.chunks = chunks
            self.closed = False

        def stream(self, chunk_size: int, *, decode_content: bool) -> list[bytes]:
            assert chunk_size == 1024 * 1024
            assert decode_content is True
            return self.chunks

        def close(self) -> None:
            self.closed = True

    class FakeSdkClient:
        def __init__(self, raw: FakeRaw) -> None:
            self.raw = raw

        def generate_autonomous_database_wallet(self, adb_ocid: str, details: Any) -> object:
            assert adb_ocid == "ocid1.autonomousdatabase.oc1..example"
            captured.append(details)
            return SimpleNamespace(data=SimpleNamespace(raw=self.raw))

    async def inline_runner(operation: Any) -> Any:
        return operation()

    factory = lambda **kwargs: kwargs  # noqa: E731 - SDK model factory の最小 fixture
    raw = FakeRaw([b"abc", b"def"])
    sdk_client = FakeSdkClient(raw)
    database_client = OciDatabaseClient(
        settings=Settings(_env_file=None),
        database_client=sdk_client,  # type: ignore[arg-type]
        sdk_call_runner=inline_runner,
        wallet_details_factory=factory,
    )

    result = asyncio.run(
        database_client.download_autonomous_database_wallet(
            "ocid1.autonomousdatabase.oc1..example",
            "temporary-secret",
            "SINGLE",
            6,
        )
    )

    assert result == b"abcdef"
    assert captured == [{"password": "temporary-secret", "generate_type": "SINGLE"}]
    assert raw.closed is True

    dedicated_raw = FakeRaw([b"1234", b"5"])
    dedicated_client = OciDatabaseClient(
        settings=Settings(_env_file=None),
        database_client=FakeSdkClient(dedicated_raw),  # type: ignore[arg-type]
        sdk_call_runner=inline_runner,
        wallet_details_factory=factory,
    )
    with pytest.raises(WalletDownloadTooLargeError):
        asyncio.run(
            dedicated_client.download_autonomous_database_wallet(
                "ocid1.autonomousdatabase.oc1..example",
                "another-secret",
                None,
                4,
            )
        )
    assert captured[-1] == {"password": "another-secret"}
    assert dedicated_raw.closed is True


def test_update_database_settings_preserves_client_lib_dir_in_env(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = get_settings()
    env_file = tmp_path / ".env"
    client_lib_dir = tmp_path / "instantclient_23_26"
    monkeypatch.setattr(settings_router, "BACKEND_ENV_FILE", env_file)
    monkeypatch.setattr(settings, "oracle_client_lib_dir", str(client_lib_dir))
    monkeypatch.setattr(settings, "oracle_wallet_dir", "")
    monkeypatch.setattr(settings, "oracle_password", "old-password")
    monkeypatch.setattr(settings, "oracle_wallet_password", "")

    resp = client.patch(
        "/api/settings/database",
        json={
            "user": "ADMIN",
            "dsn": "mydb_high",
            "wallet_dir": str(client_lib_dir / "network" / "admin"),
        },
    )

    assert resp.status_code == 200
    env_text = env_file.read_text(encoding="utf-8")
    assert f"ORACLE_CLIENT_LIB_DIR={client_lib_dir}" in env_text
    assert f"ORACLE_CLIENT_LIB_DIR={client_lib_dir / 'network' / 'admin'}" not in env_text
    assert "ORACLE_PASSWORD=old-password" in env_text


def test_update_upload_storage_persists_env_and_keeps_namespace(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = get_settings()
    env_file = tmp_path / ".env"
    monkeypatch.setattr(settings_router, "BACKEND_ENV_FILE", env_file)
    monkeypatch.setattr(settings, "object_storage_region", "ap-osaka-1")
    monkeypatch.setattr(settings, "object_storage_namespace", "existingnamespace")

    resp = client.patch(
        "/api/settings/upload-storage",
        json={
            "backend": "oci",
            "local_storage_dir": "/u01/production-ready-nl2sql",
            "object_storage_bucket": "rag-uploads",
        },
    )

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["object_storage_namespace"] == "existingnamespace"
    env_text = env_file.read_text(encoding="utf-8")
    assert "UPLOAD_STORAGE_BACKEND=oci" in env_text
    assert "LOCAL_STORAGE_DIR=/u01/production-ready-nl2sql" in env_text
    assert "OBJECT_STORAGE_REGION=ap-osaka-1" in env_text
    assert "OBJECT_STORAGE_NAMESPACE=existingnamespace" in env_text
    assert "OBJECT_STORAGE_BUCKET=rag-uploads" in env_text


def test_upload_storage_defaults_use_nl2sql_names(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.delenv("LOCAL_STORAGE_DIR", raising=False)
    monkeypatch.delenv("OBJECT_STORAGE_BUCKET", raising=False)

    settings = Settings(_env_file=None)

    assert settings.local_storage_dir == "/u01/production-ready-nl2sql"
    assert settings.object_storage_bucket == "nl2sql-originals"


def test_update_model_settings_persists_v2_json_and_env_secret(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = get_settings()
    model_settings_file = tmp_path / "model-settings.json"
    env_file = tmp_path / ".env"
    env_file.write_text("OCI_ENTERPRISE_AI_API_KEY=saved-secret\n", encoding="utf-8")
    env_file.chmod(0o600)
    monkeypatch.setattr(settings, "model_settings_file", str(model_settings_file))
    monkeypatch.setattr(settings_router, "BACKEND_ENV_FILE", env_file)
    settings.set_runtime_enterprise_ai_api_key("saved-secret")

    resp = client.patch(
        "/api/settings/model",
        json={
            "enterprise_ai": {
                "endpoint": "https://enterprise-ai.example.com",
                "project_ocid": "ocid1.project.oc1..example",
                "api_key": "",
                "has_api_key": True,
                "clear_api_key": False,
                "models": [
                    {
                        "model_id": "cohere.command-r-plus",
                        "display_name": "回答生成",
                        "vision_enabled": False,
                    },
                    {
                        "model_id": "mistral.vision-model",
                        "display_name": "画像解析",
                        "vision_enabled": True,
                    },
                ],
                "default_model_id": "cohere.command-r-plus",
                "api_path": "/responses",
                "vlm_input_mode": "auto",
                "text_payload_template": "",
                "vision_payload_template": "",
                "text_response_path": "",
                "vision_response_path": "",
                "timeout_seconds": 600.0,
                "max_retries": 3,
            },
            "generative_ai": {
                "embedding_model": "cohere.embed-v4.0",
                "embedding_dim": 1536,
                "rerank_model": "cohere.rerank-v4.0-fast",
            },
        },
    )

    assert resp.status_code == 200
    assert resp.json()["data"]["settings"]["enterprise_ai"]["api_key"] == ""
    models = resp.json()["data"]["settings"]["enterprise_ai"]["models"]
    assert [model["model_id"] for model in models] == [
        "cohere.command-r-plus",
        "mistral.vision-model",
    ]
    assert models[1]["vision_enabled"] is True
    document = json.loads(model_settings_file.read_text(encoding="utf-8"))
    assert document["version"] == 2
    assert "api_key" not in document["enterprise_ai"]
    assert document["enterprise_ai"]["models"][1]["model_id"] == "mistral.vision-model"
    assert document["generative_ai"]["embedding_dim"] == 1536
    assert env_file.read_text(encoding="utf-8") == "OCI_ENTERPRISE_AI_API_KEY=saved-secret\n"
    assert resp.json()["data"]["secret_source"] == "environment"
    assert resp.json()["data"]["legacy_secret_detected"] is False
    assert stat.S_IMODE(model_settings_file.stat().st_mode) == 0o600
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600


def test_enterprise_ai_api_key_env_update_and_clear_are_atomic(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# keep this comment\nOCI_ENTERPRISE_AI_API_KEY=old-secret\nDEBUG=true\n",
        encoding="utf-8",
    )
    env_file.chmod(0o600)
    monkeypatch.setattr(settings_router, "BACKEND_ENV_FILE", env_file)

    settings_router._persist_enterprise_ai_api_key("new-secret")

    updated = env_file.read_text(encoding="utf-8")
    assert "old-secret" not in updated
    assert "OCI_ENTERPRISE_AI_API_KEY=new-secret" in updated
    assert "# keep this comment" in updated
    assert "DEBUG=true" in updated
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600

    settings_router._persist_enterprise_ai_api_key("")

    cleared = env_file.read_text(encoding="utf-8")
    assert "OCI_ENTERPRISE_AI_API_KEY" not in cleared
    assert "# keep this comment" in cleared
    assert "DEBUG=true" in cleared
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600


def test_load_persisted_model_settings_applies_runtime_fields(tmp_path: Path) -> None:
    model_settings_file = tmp_path / "model-settings.json"
    model_settings_file.write_text(
        """
        {
          "version": 1,
          "enterprise_ai": {
            "endpoint": "https://enterprise-ai.example.com",
            "project_ocid": "ocid1.project.oc1..example",
            "api_key": "persisted-secret",
            "models": [
              {
                "model_id": "cohere.command-r-plus",
                "display_name": "回答生成",
                "vision_enabled": false
              },
              {
                "model_id": "mistral.vision-model",
                "display_name": "画像解析",
                "vision_enabled": true
              }
            ],
            "default_model_id": "cohere.command-r-plus",
            "api_path": "/responses",
            "vlm_input_mode": "inline_image",
            "timeout_seconds": 120,
            "max_retries": 4
          },
          "generative_ai": {
            "embedding_model": "cohere.embed-v4.0",
            "embedding_dim": 1536,
            "rerank_model": "cohere.rerank-v4.0-fast"
          }
        }
        """,
        encoding="utf-8",
    )
    settings = Settings(_env_file=None, model_settings_file=str(model_settings_file))

    load_persisted_model_settings(settings)

    assert settings.oci_enterprise_ai_endpoint == "https://enterprise-ai.example.com"
    assert settings.oci_enterprise_ai_api_key == "persisted-secret"
    assert settings.model_secret_source == "legacy_json"
    assert settings.legacy_model_secret_detected is True
    assert settings.oci_enterprise_ai_default_model == "cohere.command-r-plus"
    assert settings.oci_enterprise_ai_vlm_model == "mistral.vision-model"
    assert [model.model_id for model in settings.oci_enterprise_ai_models] == [
        "cohere.command-r-plus",
        "mistral.vision-model",
    ]
    assert settings.oci_enterprise_ai_vlm_input_mode == "inline_image"
    assert settings.oci_enterprise_ai_max_retries == 4
    assert settings.oci_genai_embed_model_id == "cohere.embed-v4.0"
    assert settings.oci_genai_rerank_model_id == "cohere.rerank-v4.0-fast"


def test_environment_model_secret_takes_precedence_over_v1_json(tmp_path: Path) -> None:
    model_settings_file = tmp_path / "model-settings.json"
    model_settings_file.write_text(
        '{"version":1,"enterprise_ai":{"api_key":"legacy-secret"}}',
        encoding="utf-8",
    )
    settings = Settings(
        _env_file=None,
        model_settings_file=str(model_settings_file),
        oci_enterprise_ai_api_key="environment-secret",
    )

    load_persisted_model_settings(settings)

    assert settings.oci_enterprise_ai_api_key == "environment-secret"
    assert settings.model_secret_source == "environment"
    assert settings.legacy_model_secret_detected is True


def test_v2_model_settings_never_reads_api_key_field(tmp_path: Path) -> None:
    model_settings_file = tmp_path / "model-settings.json"
    model_settings_file.write_text(
        '{"version":2,"enterprise_ai":{"api_key":"must-not-load"}}',
        encoding="utf-8",
    )
    settings = Settings(_env_file=None, model_settings_file=str(model_settings_file))

    load_persisted_model_settings(settings)

    assert settings.oci_enterprise_ai_api_key == ""
    assert settings.model_secret_source == "missing"
    assert settings.legacy_model_secret_detected is True


def test_oci_config_profile_prefers_canonical_and_falls_back_to_legacy() -> None:
    canonical = Settings(
        _env_file=None,
        oci_config_profile="CANONICAL",
        oci_profile="LEGACY",
    )
    legacy = Settings(_env_file=None, oci_config_profile="", oci_profile="LEGACY")

    assert canonical.resolved_oci_config_profile == "CANONICAL"
    assert legacy.resolved_oci_config_profile == "LEGACY"


def test_nonlocal_security_boundaries_fail_closed() -> None:
    with pytest.raises(ValueError, match="DEBUG=true"):
        Settings(
            _env_file=None,
            environment="production",
            debug=True,
            app_auth_enabled=False,
        )
    with pytest.raises(ValueError, match="APP_AUTH_COOKIE_SECURE=true"):
        Settings(
            _env_file=None,
            environment="production",
            debug=False,
            app_auth_enabled=True,
            app_auth_cookie_secure=False,
        )

    settings = Settings(
        _env_file=None,
        environment="production",
        debug=False,
        app_auth_enabled=True,
        app_auth_cookie_secure=True,
    )
    assert settings.local_debug_enabled is False


def test_model_settings_test_calls_enterprise_client(monkeypatch: MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class FakeEnterpriseClient:
        def __init__(self, settings: Settings) -> None:
            self.settings = settings

        async def generate(self, prompt: str, context: str = "") -> str:
            captured["prompt"] = prompt
            captured["context"] = context
            captured["endpoint"] = self.settings.oci_enterprise_ai_endpoint
            captured["model_id"] = self.settings.oci_enterprise_ai_llm_model
            captured["api_key"] = self.settings.oci_enterprise_ai_api_key
            return "テスト応答"

    monkeypatch.setattr(settings_router, "OciEnterpriseAiClient", FakeEnterpriseClient)

    resp = client.post(
        "/api/settings/model/test",
        json={
            "target_type": "enterprise_text",
            "model_id": "cohere.command-r-plus",
            "settings": {
                "enterprise_ai": {
                    "endpoint": "https://enterprise-ai.example.com",
                    "project_ocid": "ocid1.project.oc1..example",
                    "api_key": "request-secret",
                    "models": [
                        {
                            "model_id": "cohere.command-r-plus",
                            "display_name": "回答生成",
                            "vision_enabled": False,
                        }
                    ],
                    "default_model_id": "cohere.command-r-plus",
                    "api_path": "/responses",
                    "vlm_input_mode": "auto",
                    "timeout_seconds": 10,
                    "max_retries": 0,
                },
                "generative_ai": {
                    "embedding_model": "cohere.embed-v4.0",
                    "embedding_dim": 1536,
                    "rerank_model": "cohere.rerank-v4.0-fast",
                },
            },
        },
    )

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["status"] == "success"
    assert data["details"]["response_chars"] == 5
    assert captured["endpoint"] == "https://enterprise-ai.example.com"
    assert captured["model_id"] == "cohere.command-r-plus"
    assert captured["api_key"] == "request-secret"


def test_model_settings_error_sanitizer_never_returns_secret() -> None:
    sanitized = settings_router._sanitize_model_test_error(
        "gateway rejected Bearer request-secret with status 401",
        ["request-secret"],
    )

    assert "request-secret" not in sanitized
    assert sanitized == "gateway rejected Bearer <secret> with status 401"


def test_model_settings_test_enterprise_vision_uses_smoke_image_payload(
    monkeypatch: MonkeyPatch,
) -> None:
    observed: list[tuple[Settings, bytes, str, str]] = []

    class FakeEnterpriseClient:
        def __init__(self, settings: Settings) -> None:
            self.settings = settings

        async def generate_from_image(
            self,
            image_bytes: bytes,
            prompt: str,
            *,
            mime_type: str,
        ) -> str:
            observed.append((self.settings, image_bytes, prompt, mime_type))
            return "画像を確認しました。"

    monkeypatch.setattr(settings_router, "OciEnterpriseAiClient", FakeEnterpriseClient)

    resp = client.post(
        "/api/settings/model/test",
        json={
            "target_type": "enterprise_vision",
            "model_id": "google.gemini-2.5-flash",
            "settings": {
                "enterprise_ai": {
                    "endpoint": "https://enterprise-ai.example.com",
                    "project_ocid": "ocid1.project.oc1..example",
                    "api_key": "request-secret",
                    "models": [
                        {
                            "model_id": "cohere.command-r-plus",
                            "display_name": "回答生成",
                            "vision_enabled": False,
                        },
                        {
                            "model_id": "google.gemini-2.5-flash",
                            "display_name": "Vision",
                            "vision_enabled": True,
                        },
                    ],
                    "default_model_id": "cohere.command-r-plus",
                    "api_path": "/responses",
                    "vlm_input_mode": "auto",
                    "timeout_seconds": 10,
                    "max_retries": 0,
                },
                "generative_ai": {
                    "embedding_model": "cohere.embed-v4.0",
                    "embedding_dim": 1536,
                    "rerank_model": "cohere.rerank-v4.0-fast",
                },
            },
        },
    )

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["status"] == "success"
    assert data["details"]["surface"] == "vision"
    assert data["details"]["response_chars"] == len("画像を確認しました。")
    assert observed[0][0].oci_enterprise_ai_vlm_model == "google.gemini-2.5-flash"
    assert observed[0][1] == settings_router.MODEL_TEST_IMAGE_BYTES
    assert observed[0][2]
    assert observed[0][3] == "image/jpeg"


def test_adb_start_uses_oci_database_client(monkeypatch: MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "oracle_adb_ocid", "ocid1.autonomousdatabase.oc1..example")
    monkeypatch.setattr(settings, "oracle_adb_region", "ap-osaka-1")
    calls: list[tuple[str, str]] = []

    class FakeDatabaseClient:
        def __init__(self, settings: Settings) -> None:
            self.settings = settings

        async def get_autonomous_database(self, adb_ocid: str) -> AutonomousDatabaseInfo:
            calls.append(("get", adb_ocid))
            return AutonomousDatabaseInfo(
                id=adb_ocid,
                display_name="NL2SQLADB",
                lifecycle_state="STOPPED",
                db_name="NL2SQL",
                cpu_core_count=1,
                data_storage_size_in_tbs=1,
            )

        async def start_autonomous_database(self, adb_ocid: str) -> None:
            calls.append(("start", adb_ocid))

    monkeypatch.setattr(settings_router, "OciDatabaseClient", FakeDatabaseClient)

    resp = client.post("/api/settings/database/adb/start")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["status"] == "accepted"
    assert data["lifecycle_state"] == "STARTING"
    assert data["region"] == "ap-osaka-1"
    assert calls == [
        ("get", "ocid1.autonomousdatabase.oc1..example"),
        ("start", "ocid1.autonomousdatabase.oc1..example"),
    ]


def test_update_adb_settings_persists_dedicated_region(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = get_settings()
    env_file = tmp_path / ".env"
    monkeypatch.setattr(settings_router, "BACKEND_ENV_FILE", env_file)
    monkeypatch.setattr(settings, "oracle_adb_ocid", "")
    monkeypatch.setattr(settings, "oracle_adb_region", "")
    calls: list[str] = []

    class FakeDatabaseClient:
        def __init__(self, settings: Settings) -> None:
            self.settings = settings

        async def get_autonomous_database(self, adb_ocid: str) -> AutonomousDatabaseInfo:
            calls.append(adb_ocid)
            return AutonomousDatabaseInfo(
                id=adb_ocid,
                display_name="NL2SQLADB",
                lifecycle_state="STOPPED",
                db_name="NL2SQL",
                cpu_core_count=1,
                data_storage_size_in_tbs=1,
            )

    monkeypatch.setattr(settings_router, "OciDatabaseClient", FakeDatabaseClient)

    resp = client.post(
        "/api/settings/database/adb/settings",
        json={"adb_ocid": "ocid1.autonomousdatabase.oc1..saved", "region": "ap-tokyo-1"},
    )

    assert resp.status_code == 200
    assert resp.json()["data"]["region"] == "ap-tokyo-1"
    assert settings.oracle_adb_ocid == "ocid1.autonomousdatabase.oc1..saved"
    assert settings.oracle_adb_region == "ap-tokyo-1"
    env_text = env_file.read_text(encoding="utf-8")
    assert "ORACLE_ADB_OCID=ocid1.autonomousdatabase.oc1..saved" in env_text
    assert "ORACLE_ADB_REGION=ap-tokyo-1" in env_text
    assert "OCI_REGION=ap-tokyo-1" not in env_text
    assert calls == ["ocid1.autonomousdatabase.oc1..saved"]


def test_oracle_adapter_wallet_only_connection_uses_wallet_kwargs(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    wallet_dir = tmp_path / "wallet"
    wallet_dir.mkdir()
    (wallet_dir / "tnsnames.ora").write_text(
        "mydb_high = "
        "(DESCRIPTION=(retry_count=20)(retry_delay=3)"
        "(ADDRESS=(PROTOCOL=tcps)(HOST=db.example.com)(PORT=1522))"
        "(CONNECT_DATA=(SERVICE_NAME=mydb_high)))\n",
        encoding="utf-8",
    )
    (wallet_dir / "cwallet.sso").write_text("dummy", encoding="utf-8")
    captured: dict[str, Any] = {}

    class FakeOracleCursor:
        def __enter__(self) -> FakeOracleCursor:
            return self

        def __exit__(self, *_exc: object) -> None:
            return None

        def execute(self, sql: str) -> None:
            captured["sql"] = sql

        def fetchone(self) -> tuple[int]:
            return (1,)

    class FakeOracleConnection:
        def cursor(self) -> FakeOracleCursor:
            return FakeOracleCursor()

        def close(self) -> None:
            captured["closed"] = True

    class FakeOracleDbModule:
        def connect(self, **kwargs: object) -> FakeOracleConnection:
            captured["connect_kwargs"] = kwargs
            return FakeOracleConnection()

    def fake_import_module(name: str) -> object:
        if name == "oracledb":
            return FakeOracleDbModule()
        raise AssertionError(f"unexpected module import: {name}")

    monkeypatch.setattr(
        "app.features.nl2sql.oracle_adapter.importlib.import_module",
        fake_import_module,
    )
    settings = Settings(
        oracle_user="ADMIN",
        oracle_password="",
        oracle_dsn="mydb_high",
        oracle_client_lib_dir="",
        oracle_wallet_dir=str(wallet_dir),
        nl2sql_oracle_connect_timeout_seconds=3,
    )

    ok, message = OracleNl2SqlAdapter(settings).test_connection()

    assert ok is True, message
    kwargs = captured["connect_kwargs"]
    assert kwargs["user"] == "ADMIN"
    assert "password" not in kwargs
    assert kwargs["config_dir"] == str(wallet_dir)
    assert kwargs["wallet_location"] == str(wallet_dir)
    assert kwargs["tcp_connect_timeout"] == 3
    assert "retry_count" not in str(kwargs["dsn"]).lower()
    assert captured["sql"] == "SELECT 1 FROM DUAL"
    assert captured["closed"] is True


def _configure_wallet_download(monkeypatch: MonkeyPatch, tmp_path: Path) -> Settings:
    settings = get_settings()
    client_lib_dir = tmp_path / "instantclient"
    key_file = tmp_path / "oci_api_key.pem"
    key_file.write_text(
        "-----BEGIN PRIVATE KEY-----\nfixture\n-----END PRIVATE KEY-----\n",
        encoding="utf-8",
    )
    config_file = tmp_path / "oci_config"
    config_file.write_text(
        "[DEFAULT]\n"
        "user=ocid1.user.oc1..example\n"
        "fingerprint=aa:bb:cc\n"
        "tenancy=ocid1.tenancy.oc1..example\n"
        "region=ap-osaka-1\n"
        f"key_file={key_file}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "oracle_client_lib_dir", str(client_lib_dir))
    monkeypatch.setattr(settings, "oracle_wallet_dir", "")
    monkeypatch.setattr(settings, "oracle_adb_ocid", "ocid1.autonomousdatabase.oc1..example")
    monkeypatch.setattr(settings, "oracle_adb_region", "ap-osaka-1")
    monkeypatch.setattr(settings, "oci_config_file", str(config_file))
    monkeypatch.setattr(settings, "oci_config_profile", "DEFAULT")
    return settings


def _adb_info(adb_ocid: str, *, is_dedicated: bool) -> AutonomousDatabaseInfo:
    return AutonomousDatabaseInfo(
        id=adb_ocid,
        display_name="NL2SQLADB",
        lifecycle_state="AVAILABLE",
        db_name="NL2SQL",
        cpu_core_count=1,
        data_storage_size_in_tbs=1,
        is_dedicated=is_dedicated,
    )


def _wallet_zip_bytes() -> bytes:
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr(
            "Wallet_MYDB/tnsnames.ora",
            "mydb_high = (DESCRIPTION=(ADDRESS=(PROTOCOL=tcps)(HOST=db.example.com)))\n",
        )
        archive.writestr("Wallet_MYDB/sqlnet.ora", "WALLET_LOCATION=(SOURCE=(METHOD=file))\n")
        archive.writestr("Wallet_MYDB/cwallet.sso", "dummy")
        archive.writestr("Wallet_MYDB/ewallet.pem", "dummy")
        archive.writestr("Wallet_MYDB/readme", "skip me")
    return buffer.getvalue()
