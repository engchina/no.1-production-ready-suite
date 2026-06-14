"""設定 API のテスト。"""

import stat
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from zipfile import ZipFile

from pytest import MonkeyPatch

from app.config import get_settings
from app.main import app
from app.schemas.settings import EnterpriseAiModelSettings
from tests.support import AsgiTestClient

client = AsgiTestClient(app)
LLM_TEMPLATE = '{"input":"${user_message}"}'
VLM_TEMPLATE = '{"input":"${data_base64}"}'


def test_get_model_settings_returns_runtime_values(monkeypatch: MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "oci_enterprise_ai_endpoint", "https://enterprise-ai.example")
    monkeypatch.setattr(
        settings,
        "oci_enterprise_ai_project_ocid",
        "ocid1.generativeaiproject.oc1..example",
    )
    monkeypatch.setattr(settings, "oci_enterprise_ai_api_key", "sk-runtime-secret")
    monkeypatch.setattr(settings, "oci_enterprise_ai_llm_model", "enterprise-llm")
    monkeypatch.setattr(settings, "oci_enterprise_ai_vlm_model", "enterprise-vlm")
    monkeypatch.setattr(settings, "oci_enterprise_ai_llm_payload_template", LLM_TEMPLATE)
    monkeypatch.setattr(settings, "oci_enterprise_ai_vlm_payload_template", VLM_TEMPLATE)
    monkeypatch.setattr(settings, "oci_enterprise_ai_llm_response_path", "/data/text")
    monkeypatch.setattr(settings, "oci_enterprise_ai_vlm_response_path", "/data/document")
    monkeypatch.setattr(settings, "oci_genai_embedding_model", "cohere.embed-v4.0")
    monkeypatch.setattr(settings, "oci_genai_embedding_dim", 1536)
    monkeypatch.setattr(settings, "oci_genai_rerank_model", "cohere.rerank-v4.0-fast")

    resp = client.get("/api/settings/model")

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["settings"]["enterprise_ai"]["endpoint"] == "https://enterprise-ai.example"
    assert (
        body["settings"]["enterprise_ai"]["project_ocid"]
        == "ocid1.generativeaiproject.oc1..example"
    )
    assert body["settings"]["enterprise_ai"]["api_key"] == ""
    assert body["settings"]["enterprise_ai"]["has_api_key"] is True
    assert body["settings"]["enterprise_ai"]["llm_model"] == "enterprise-llm"
    assert body["settings"]["enterprise_ai"]["vlm_model"] == "enterprise-vlm"
    assert body["settings"]["enterprise_ai"]["llm_payload_template"] == LLM_TEMPLATE
    assert body["settings"]["enterprise_ai"]["vlm_payload_template"] == VLM_TEMPLATE
    assert body["settings"]["enterprise_ai"]["llm_response_path"] == "/data/text"
    assert body["settings"]["enterprise_ai"]["vlm_response_path"] == "/data/document"
    assert body["settings"]["generative_ai"]["embedding_dim"] == 1536
    assert "sk-runtime-secret" not in resp.text
    assert body["checks"] == {
        "enterprise_ai": "ok",
        "generative_ai": "ok",
        "embedding_dim": "ok",
    }


def test_update_model_settings_mutates_runtime_settings() -> None:
    payload = _payload()

    resp = client.patch("/api/settings/model", json=payload)

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["checks"]["enterprise_ai"] == "ok"
    settings = get_settings()
    assert settings.oci_enterprise_ai_endpoint == "https://enterprise-ai.example"
    assert settings.oci_enterprise_ai_project_ocid == "ocid1.generativeaiproject.oc1..example"
    assert settings.oci_enterprise_ai_api_key == "sk-update-secret"
    assert settings.oci_enterprise_ai_llm_model == "enterprise-llm"
    assert settings.oci_enterprise_ai_vlm_model == "enterprise-vlm"
    assert settings.oci_enterprise_ai_llm_payload_template == LLM_TEMPLATE
    assert settings.oci_enterprise_ai_vlm_payload_template == VLM_TEMPLATE
    assert settings.oci_enterprise_ai_llm_response_path == "/data/text"
    assert settings.oci_enterprise_ai_vlm_response_path == "/data/document"
    assert settings.oci_genai_embedding_model == "cohere.embed-v4.0"
    assert settings.oci_genai_embedding_dim == 1536
    assert settings.oci_genai_rerank_model == "cohere.rerank-v4.0-fast"
    assert "sk-update-secret" not in resp.text


def test_check_model_settings_does_not_mutate_runtime_settings(monkeypatch: MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "oci_enterprise_ai_endpoint", "")

    resp = client.post("/api/settings/model/check", json=_payload())

    assert resp.status_code == 200
    assert resp.json()["data"]["checks"]["enterprise_ai"] == "ok"
    assert settings.oci_enterprise_ai_endpoint == ""


def test_check_model_settings_masks_candidate_api_key() -> None:
    payload = _payload()
    payload["enterprise_ai"]["api_key"] = "sk-check-secret"
    payload["enterprise_ai"]["has_api_key"] = False

    resp = client.post("/api/settings/model/check", json=payload)

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["checks"]["enterprise_ai"] == "ok"
    assert body["settings"]["enterprise_ai"]["api_key"] == ""
    assert body["settings"]["enterprise_ai"]["has_api_key"] is True
    assert "sk-check-secret" not in resp.text


def test_model_settings_missing_values_are_reported() -> None:
    payload = _payload()
    payload["enterprise_ai"]["endpoint"] = ""
    payload["generative_ai"]["rerank_model"] = ""

    resp = client.post("/api/settings/model/check", json=payload)

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["checks"]["enterprise_ai"] == "missing"
    assert body["checks"]["generative_ai"] == "missing"
    assert body["checks"]["embedding_dim"] == "ok"


def test_model_settings_requires_enterprise_ai_api_key() -> None:
    payload = _payload()
    payload["enterprise_ai"]["api_key"] = ""
    payload["enterprise_ai"]["has_api_key"] = False

    resp = client.post("/api/settings/model/check", json=payload)

    assert resp.status_code == 200
    assert resp.json()["data"]["checks"]["enterprise_ai"] == "missing"


def test_model_settings_allows_model_id_omitted_when_template_does_not_reference_model() -> None:
    payload = _payload()
    payload["enterprise_ai"]["llm_model"] = ""
    payload["enterprise_ai"]["vlm_model"] = ""
    payload["enterprise_ai"]["llm_payload_template"] = LLM_TEMPLATE
    payload["enterprise_ai"]["vlm_payload_template"] = VLM_TEMPLATE

    resp = client.post("/api/settings/model/check", json=payload)

    assert resp.status_code == 200
    assert resp.json()["data"]["checks"]["enterprise_ai"] == "ok"


def test_enterprise_ai_model_settings_defaults_max_retries_to_three() -> None:
    """設定 API スキーマの最大リトライ回数既定値は 3。"""
    assert EnterpriseAiModelSettings().max_retries == 3


def test_model_settings_requires_model_id_when_template_references_model() -> None:
    payload = _payload()
    payload["enterprise_ai"]["llm_model"] = ""
    payload["enterprise_ai"]["llm_payload_template"] = '{"model":"${model}"}'

    resp = client.post("/api/settings/model/check", json=payload)

    assert resp.status_code == 200
    assert resp.json()["data"]["checks"]["enterprise_ai"] == "missing"


def test_model_settings_rejects_invalid_payload_template() -> None:
    payload = _payload()
    payload["enterprise_ai"]["llm_payload_template"] = "[1, 2, 3]"

    resp = client.patch("/api/settings/model", json=payload)

    assert resp.status_code == 422
    assert resp.json()["error_messages"]


def test_model_settings_keeps_existing_api_key_when_secret_input_is_blank(
    monkeypatch: MonkeyPatch,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "oci_enterprise_ai_api_key", "sk-existing-secret")
    payload = _payload()
    payload["enterprise_ai"]["api_key"] = ""
    payload["enterprise_ai"]["has_api_key"] = True

    resp = client.patch("/api/settings/model", json=payload)

    assert resp.status_code == 200
    assert settings.oci_enterprise_ai_api_key == "sk-existing-secret"
    assert resp.json()["data"]["settings"]["enterprise_ai"]["has_api_key"] is True
    assert "sk-existing-secret" not in resp.text


def test_model_settings_clears_existing_api_key(monkeypatch: MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "oci_enterprise_ai_api_key", "sk-existing-secret")
    payload = _payload()
    payload["enterprise_ai"]["api_key"] = ""
    payload["enterprise_ai"]["has_api_key"] = True
    payload["enterprise_ai"]["clear_api_key"] = True

    resp = client.patch("/api/settings/model", json=payload)

    assert resp.status_code == 200
    assert settings.oci_enterprise_ai_api_key == ""
    assert resp.json()["data"]["settings"]["enterprise_ai"]["has_api_key"] is False
    assert "sk-existing-secret" not in resp.text


def test_model_settings_rejects_non_1536_embedding_dim() -> None:
    payload = _payload()
    payload["generative_ai"]["embedding_dim"] = 1024

    resp = client.patch("/api/settings/model", json=payload)

    assert resp.status_code == 422
    body = resp.json()
    assert body["data"] is None
    assert body["error_messages"]


def test_read_oci_config_uses_requested_profile_from_backend_path(tmp_path: Path) -> None:
    config_file = tmp_path / "config"
    config_file.write_text(
        "\n".join(
            [
                "[DEFAULT]",
                "tenancy=ocid1.tenancy.oc1..shared",
                "region=ap-tokyo-1",
                "key_file=/home/app/.oci/default.pem",
                "[RAG_PROD]",
                "user=ocid1.user.oc1..prod",
                "fingerprint=12:34:56:78",
                "region=ap-osaka-1",
                "compartment=ocid1.compartment.oc1..prod",
            ]
        ),
        encoding="utf-8",
    )

    resp = client.post(
        "/api/settings/oci/config/read",
        json={"config_file": str(config_file), "profile": "RAG_PROD"},
    )

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body == {
        "profile": "RAG_PROD",
        "user": "ocid1.user.oc1..prod",
        "fingerprint": "12:34:56:78",
        "tenancy": "ocid1.tenancy.oc1..shared",
        "region": "ap-osaka-1",
        "key_file": "/home/app/.oci/default.pem",
        "applied_fields": [
            "user",
            "fingerprint",
            "tenancy",
            "region",
            "key_file",
        ],
    }
    assert "compartment" not in body


def test_read_oci_config_rejects_missing_requested_profile(tmp_path: Path) -> None:
    config_file = tmp_path / "config"
    config_file.write_text(
        "[DEFAULT]\nuser=ocid1.user.oc1..default\n",
        encoding="utf-8",
    )

    resp = client.post(
        "/api/settings/oci/config/read",
        json={"config_file": str(config_file), "profile": "RAG_PROD"},
    )

    assert resp.status_code == 404
    assert "指定した OCI config profile が見つかりません。" in resp.text


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

    monkeypatch.setattr("app.api.routes.settings.importlib.import_module", fake_import_module)
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

    monkeypatch.setattr("app.api.routes.settings.importlib.import_module", fake_import_module)

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


def test_upload_oci_private_key_overwrites_fixed_path(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    pem = b"-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----\n"

    resp = client.post(
        "/api/settings/oci/key-file",
        files={"file": ("new-key.pem", pem, "application/x-pem-file")},
    )

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body == {"key_file": "~/.oci/oci_api_key.pem", "saved": True}
    target = tmp_path / ".oci" / "oci_api_key.pem"
    assert target.read_bytes() == pem
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert "PRIVATE KEY" not in resp.text


def test_upload_oci_private_key_rejects_invalid_content(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))

    resp = client.post(
        "/api/settings/oci/key-file",
        files={"file": ("new-key.pem", b"not a pem", "application/x-pem-file")},
    )

    assert resp.status_code == 400
    assert not (tmp_path / ".oci" / "oci_api_key.pem").exists()


def test_get_database_settings_masks_secrets(monkeypatch: MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "oracle_user", "rag_app")
    monkeypatch.setattr(settings, "oracle_password", "super-secret-password")
    monkeypatch.setattr(settings, "oracle_dsn", "adb.example.com/rag")
    monkeypatch.setattr(settings, "oracle_wallet_dir", "")
    monkeypatch.setattr(settings, "oracle_wallet_password", "wallet-secret")

    resp = client.get("/api/settings/database")

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["user"] == "rag_app"
    assert body["dsn"] == "adb.example.com/rag"
    assert body["wallet_dir"] == settings.resolved_oracle_wallet_dir
    assert body["has_password"] is True
    assert body["has_wallet_password"] is True
    assert body["wallet_uploaded"] is False
    assert body["available_services"] == []
    assert body["readiness"] == "ok"
    assert body["vector_column"] == "VECTOR(1536, FLOAT32)"
    assert "super-secret-password" not in resp.text
    assert "wallet-secret" not in resp.text


def test_update_database_settings_mutates_runtime_without_echoing_secret(
    monkeypatch: MonkeyPatch,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "oracle_user", "old_user")
    monkeypatch.setattr(settings, "oracle_password", "old-secret")
    monkeypatch.setattr(settings, "oracle_dsn", "old-dsn")
    monkeypatch.setattr(settings, "oracle_wallet_dir", "")
    monkeypatch.setattr(settings, "oracle_wallet_password", "")

    resp = client.patch(
        "/api/settings/database",
        json={
            "user": "rag_app",
            "dsn": "adb.example.com/rag",
            "wallet_dir": "/opt/oracle/wallet",
        },
    )

    assert resp.status_code == 200
    assert settings.oracle_user == "rag_app"
    assert settings.oracle_dsn == "adb.example.com/rag"
    assert settings.oracle_wallet_dir == settings.resolved_oracle_wallet_dir
    assert settings.oracle_password == "old-secret"
    assert resp.json()["data"]["wallet_dir"] == settings.resolved_oracle_wallet_dir
    assert resp.json()["data"]["has_password"] is True
    assert "old-secret" not in resp.text


def test_database_connection_test_local_adapter_does_not_mutate_runtime(
    monkeypatch: MonkeyPatch,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "ai_service_adapter", "local")
    monkeypatch.setattr(settings, "oracle_user", "")
    monkeypatch.setattr(settings, "oracle_password", "")
    monkeypatch.setattr(settings, "oracle_dsn", "")
    monkeypatch.setattr(settings, "oracle_wallet_dir", "")
    monkeypatch.setattr(settings, "oracle_wallet_password", "")

    resp = client.post(
        "/api/settings/database/test",
        json={
            "user": "rag_app",
            "dsn": "adb.example.com/rag",
            "wallet_dir": "",
            "password": "candidate-secret",
        },
    )

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["status"] == "skipped"
    assert body["readiness"] == "ok"
    assert settings.oracle_user == ""
    assert settings.oracle_password == ""
    assert "candidate-secret" not in resp.text


def test_upload_database_wallet_zip_updates_runtime(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "local_storage_dir", str(tmp_path))
    monkeypatch.setattr(settings, "oracle_user", "rag_app")
    monkeypatch.setattr(settings, "oracle_password", "")
    monkeypatch.setattr(settings, "oracle_dsn", "ragdb_high")
    monkeypatch.setattr(settings, "oracle_wallet_dir", "")
    wallet_dir = Path(settings.resolved_oracle_wallet_dir)
    wallet_dir.mkdir(parents=True)
    (wallet_dir / "old-wallet-file").write_text("old", encoding="utf-8")

    resp = client.post(
        "/api/settings/database/wallet",
        files={"file": ("Wallet_RAGDB.zip", _wallet_zip(), "application/zip")},
    )

    assert resp.status_code == 200
    body = resp.json()["data"]
    wallet_dir = Path(settings.oracle_wallet_dir)
    assert wallet_dir.is_dir()
    assert (wallet_dir / "tnsnames.ora").read_text(encoding="utf-8") == "ragdb_high = ..."
    assert not (wallet_dir / "old-wallet-file").exists()
    assert body["wallet_dir"] == str(wallet_dir)
    assert body["wallet_uploaded"] is True
    assert body["available_services"] == ["ragdb_high"]
    assert body["readiness"] == "ok"
    assert "ewallet-secret" not in resp.text


def test_get_database_settings_extracts_available_services_from_wallet_dir(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = get_settings()
    wallet_dir = Path(settings.resolved_oracle_wallet_dir)
    wallet_dir.mkdir(parents=True)
    (wallet_dir / "tnsnames.ora").write_text(
        "\n".join(
            [
                "ragdb_high = (DESCRIPTION = ...)",
                "  (ADDRESS = (PROTOCOL = tcps)(HOST = example.oraclecloud.com)(PORT = 1522))",
                "ragdb_low = (DESCRIPTION = ...)",
                "ragdb_high = (DESCRIPTION = duplicate)",
            ]
        ),
        encoding="utf-8",
    )

    resp = client.get("/api/settings/database")

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["wallet_uploaded"] is True
    assert body["available_services"] == ["ragdb_high", "ragdb_low"]


def test_upload_database_wallet_zip_rejects_unsafe_member_path(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = get_settings()
    wallet_dir = Path(settings.resolved_oracle_wallet_dir)
    wallet_dir.mkdir(parents=True)
    sentinel = wallet_dir / "keep.txt"
    sentinel.write_text("keep", encoding="utf-8")

    resp = client.post(
        "/api/settings/database/wallet",
        files={
            "file": (
                "Wallet_BAD.zip",
                _wallet_zip({"../tnsnames.ora": "bad", "sqlnet.ora": "...", "cwallet.sso": "..."}),
                "application/zip",
            )
        },
    )

    assert resp.status_code == 400
    assert sentinel.read_text(encoding="utf-8") == "keep"
    assert not (wallet_dir / "tnsnames.ora").exists()


def test_get_upload_storage_settings_returns_runtime_values(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "ai_service_adapter", "local")
    monkeypatch.setattr(settings, "upload_storage_backend", "local")
    monkeypatch.setattr(settings, "local_storage_dir", str(tmp_path / "uploads"))
    monkeypatch.setattr(settings, "object_storage_region", "us-chicago-1")
    monkeypatch.setattr(settings, "object_storage_namespace", "example-namespace")
    monkeypatch.setattr(settings, "object_storage_bucket", "rag-originals")
    monkeypatch.setattr(settings, "max_upload_bytes", 12345)

    resp = client.get("/api/settings/upload-storage")

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["backend"] == "local"
    assert body["ai_service_adapter"] == "local"
    assert body["local_storage_dir"] == str(tmp_path / "uploads")
    assert body["object_storage_region"] == "us-chicago-1"
    assert body["object_storage_namespace"] == "example-namespace"
    assert body["object_storage_bucket"] == "rag-originals"
    assert body["readiness"] == "ok"
    assert body["max_upload_bytes"] == 12345
    assert body["config_source"] == "runtime"


def test_update_upload_storage_settings_mutates_runtime(monkeypatch: MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "object_storage_namespace", "global-namespace")

    resp = client.patch(
        "/api/settings/upload-storage",
        json={
            "backend": "oci",
            "local_storage_dir": "/tmp/production-ready-rag",
            "object_storage_bucket": "rag-originals",
        },
    )

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["backend"] == "oci"
    assert body["readiness"] == "ok"
    assert settings.upload_storage_backend == "oci"
    assert settings.local_storage_dir == "/tmp/production-ready-rag"
    assert settings.object_storage_namespace == "global-namespace"
    assert settings.object_storage_bucket == "rag-originals"


def test_update_upload_storage_settings_can_apply_namespace_from_oci_settings_draft(
    monkeypatch: MonkeyPatch,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "object_storage_namespace", "")

    resp = client.patch(
        "/api/settings/upload-storage",
        json={
            "backend": "oci",
            "local_storage_dir": "/tmp/production-ready-rag",
            "object_storage_namespace": "oci-page-namespace",
            "object_storage_bucket": "rag-originals",
        },
    )

    assert resp.status_code == 200
    assert settings.object_storage_namespace == "oci-page-namespace"
    assert settings.object_storage_bucket == "rag-originals"


def test_update_upload_storage_settings_rejects_missing_selected_backend_fields(
    monkeypatch: MonkeyPatch,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "object_storage_namespace", "global-namespace")

    resp = client.patch(
        "/api/settings/upload-storage",
        json={
            "backend": "oci",
            "local_storage_dir": "/tmp/production-ready-rag",
            "object_storage_bucket": "",
        },
    )

    assert resp.status_code == 422
    assert resp.json()["error_messages"]


def test_update_upload_storage_settings_requires_global_namespace(
    monkeypatch: MonkeyPatch,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "object_storage_namespace", "")

    resp = client.patch(
        "/api/settings/upload-storage",
        json={
            "backend": "oci",
            "local_storage_dir": "/tmp/production-ready-rag",
            "object_storage_bucket": "rag-originals",
        },
    )

    assert resp.status_code == 422
    assert "OCI 認証設定で Object Storage ネームスペース" in resp.text


def _payload() -> dict[str, Any]:
    return {
        "enterprise_ai": {
            "endpoint": "https://enterprise-ai.example",
            "project_ocid": "ocid1.generativeaiproject.oc1..example",
            "api_key": "sk-update-secret",
            "has_api_key": False,
            "clear_api_key": False,
            "llm_model": "enterprise-llm",
            "vlm_model": "enterprise-vlm",
            "llm_path": "/v1/llm/generate",
            "vlm_path": "/v1/vlm/extract",
            "llm_payload_template": LLM_TEMPLATE,
            "vlm_payload_template": VLM_TEMPLATE,
            "llm_response_path": "/data/text",
            "vlm_response_path": "/data/document",
            "timeout_seconds": 60.0,
            "max_retries": 2,
        },
        "generative_ai": {
            "embedding_model": "cohere.embed-v4.0",
            "embedding_dim": 1536,
            "rerank_model": "cohere.rerank-v4.0-fast",
        },
    }


def _wallet_zip(entries: dict[str, str] | None = None) -> bytes:
    wallet_entries = entries or {
        "tnsnames.ora": "ragdb_high = ...",
        "sqlnet.ora": "WALLET_LOCATION = ...",
        "cwallet.sso": "ewallet-secret",
    }
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        for name, content in wallet_entries.items():
            archive.writestr(name, content)
    return buffer.getvalue()
