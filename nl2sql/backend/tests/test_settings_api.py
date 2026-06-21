from __future__ import annotations

import stat
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from zipfile import ZipFile

from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from app.features.settings import router as settings_router
from app.main import app
from app.settings import get_settings

client = TestClient(app)


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

    monkeypatch.setattr("app.features.settings.router.importlib.import_module", fake_import_module)
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

    monkeypatch.setattr("app.features.settings.router.importlib.import_module", fake_import_module)

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

    monkeypatch.setattr("app.features.settings.router.importlib.import_module", fake_import_module)

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
            "local_storage_dir": "/u01/production-ready-rag",
            "object_storage_bucket": "rag-uploads",
        },
    )

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["object_storage_namespace"] == "existingnamespace"
    env_text = env_file.read_text(encoding="utf-8")
    assert "UPLOAD_STORAGE_BACKEND=oci" in env_text
    assert "OBJECT_STORAGE_REGION=ap-osaka-1" in env_text
    assert "OBJECT_STORAGE_NAMESPACE=existingnamespace" in env_text
    assert "OBJECT_STORAGE_BUCKET=rag-uploads" in env_text


def test_update_model_settings_persists_json_with_resolved_secret(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = get_settings()
    model_settings_file = tmp_path / "model-settings.json"
    monkeypatch.setattr(settings, "model_settings_file", str(model_settings_file))
    monkeypatch.setattr(settings, "oci_enterprise_ai_api_key", "saved-secret")

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
                    }
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
    document = model_settings_file.read_text(encoding="utf-8")
    assert '"api_key": "saved-secret"' in document
    assert '"embedding_dim": 1536' in document
    assert stat.S_IMODE(model_settings_file.stat().st_mode) == 0o600


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
