"""設定 API のテスト。"""

import json
import stat
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from zipfile import ZipFile

from pytest import MonkeyPatch

from app.api.routes import settings as settings_routes
from app.clients.oracle import OracleConnectionTimeoutError, OracleWalletPasswordRequiredError
from app.config import Settings, get_settings, load_persisted_model_settings
from app.main import app
from app.schemas.settings import EnterpriseAiModelSettings
from tests.support import AsgiTestClient

client = AsgiTestClient(app)
LLM_TEMPLATE = '{"input":"${user_message}"}'
VLM_TEMPLATE = '{"input":"${data_base64}"}'


def test_model_settings_vision_test_image_is_valid_jpeg() -> None:
    """Vision モデルの接続テストには provider が受理できる JPEG を使う。"""
    data = settings_routes.MODEL_TEST_IMAGE_BYTES

    assert data.startswith(b"\xff\xd8")
    assert len(data) > 1024


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
    monkeypatch.setattr(settings, "oci_enterprise_ai_models", [])
    monkeypatch.setattr(settings, "oci_enterprise_ai_default_model", "")
    monkeypatch.setattr(settings, "oci_enterprise_ai_llm_path", "/responses")
    monkeypatch.setattr(settings, "oci_enterprise_ai_vlm_path", "/responses")
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
    assert body["settings"]["enterprise_ai"]["models"] == [
        {
            "model_id": "enterprise-llm",
            "display_name": "enterprise-llm",
            "vision_enabled": False,
        },
        {
            "model_id": "enterprise-vlm",
            "display_name": "enterprise-vlm",
            "vision_enabled": True,
        },
    ]
    assert body["settings"]["enterprise_ai"]["default_model_id"] == "enterprise-llm"
    assert body["settings"]["enterprise_ai"]["api_path"] == "/responses"
    assert body["settings"]["enterprise_ai"]["text_payload_template"] == LLM_TEMPLATE
    assert body["settings"]["enterprise_ai"]["vision_payload_template"] == VLM_TEMPLATE
    assert body["settings"]["enterprise_ai"]["text_response_path"] == "/data/text"
    assert body["settings"]["enterprise_ai"]["vision_response_path"] == "/data/document"
    assert body["settings"]["enterprise_ai"]["llm_max_output_tokens"] == 1200
    assert body["settings"]["enterprise_ai"]["vlm_max_output_tokens"] == 65536
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
    assert body["model_settings_file"] == get_settings().model_settings_file
    settings = get_settings()
    assert settings.oci_enterprise_ai_endpoint == "https://enterprise-ai.example"
    assert settings.oci_enterprise_ai_project_ocid == "ocid1.generativeaiproject.oc1..example"
    assert settings.oci_enterprise_ai_api_key == "sk-update-secret"
    assert settings.oci_enterprise_ai_llm_model == "enterprise-llm"
    assert settings.oci_enterprise_ai_vlm_model == "enterprise-vlm"
    assert [model.model_id for model in settings.oci_enterprise_ai_models] == [
        "enterprise-llm",
        "enterprise-vlm",
    ]
    assert settings.oci_enterprise_ai_default_model == "enterprise-llm"
    assert settings.oci_enterprise_ai_llm_path == "/responses"
    assert settings.oci_enterprise_ai_vlm_path == "/responses"
    assert settings.oci_enterprise_ai_llm_payload_template == LLM_TEMPLATE
    assert settings.oci_enterprise_ai_vlm_payload_template == VLM_TEMPLATE
    assert settings.oci_enterprise_ai_llm_response_path == "/data/text"
    assert settings.oci_enterprise_ai_vlm_response_path == "/data/document"
    assert settings.oci_enterprise_ai_llm_max_output_tokens == 1600
    assert settings.oci_enterprise_ai_vlm_max_output_tokens == 64000
    assert settings.oci_genai_embedding_model == "cohere.embed-v4.0"
    assert settings.oci_genai_embedding_dim == 1536
    assert settings.oci_genai_rerank_model == "cohere.rerank-v4.0-fast"
    assert "sk-update-secret" not in resp.text


def test_update_model_settings_persists_private_json(tmp_path: Path) -> None:
    settings = get_settings()
    settings.model_settings_file = str(tmp_path / "config" / "model-settings.json")
    payload = _payload()

    resp = client.patch("/api/settings/model", json=payload)

    assert resp.status_code == 200
    settings_file = Path(settings.model_settings_file)
    assert settings_file.is_file()
    assert stat.S_IMODE(settings_file.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(settings_file.stat().st_mode) == 0o600
    persisted = json.loads(settings_file.read_text(encoding="utf-8"))
    assert persisted["version"] == 1
    assert persisted["enterprise_ai"]["api_key"] == "sk-update-secret"
    assert persisted["enterprise_ai"]["models"] == [
        {
            "model_id": "enterprise-llm",
            "display_name": "標準 LLM",
            "vision_enabled": False,
        },
        {
            "model_id": "enterprise-vlm",
            "display_name": "Vision LLM",
            "vision_enabled": True,
        },
    ]
    assert persisted["enterprise_ai"]["default_model_id"] == "enterprise-llm"
    assert persisted["enterprise_ai"]["llm_max_output_tokens"] == 1600
    assert persisted["enterprise_ai"]["vlm_max_output_tokens"] == 64000
    assert persisted["generative_ai"]["embedding_dim"] == 1536
    assert "sk-update-secret" not in resp.text


def test_load_persisted_model_settings_applies_saved_model_catalog(tmp_path: Path) -> None:
    settings_file = tmp_path / "model-settings.json"
    settings_file.write_text(
        json.dumps(
            {
                "version": 1,
                "enterprise_ai": {
                    "endpoint": "https://persisted-enterprise.example",
                    "project_ocid": "ocid1.generativeaiproject.oc1..persisted",
                    "api_key": "sk-persisted-secret",
                    "models": [
                        {
                            "model_id": "persisted-text",
                            "display_name": "永続 Text",
                            "vision_enabled": False,
                        },
                        {
                            "model_id": "persisted-vision",
                            "display_name": "永続 Vision",
                            "vision_enabled": True,
                        },
                    ],
                    "default_model_id": "persisted-text",
                    "api_path": "/responses",
                    "text_payload_template": LLM_TEMPLATE,
                    "vision_payload_template": VLM_TEMPLATE,
                    "text_response_path": "/payload/text",
                    "vision_response_path": "/payload/document",
                    "timeout_seconds": 42,
                    "max_retries": 1,
                    "llm_max_output_tokens": 1700,
                    "vlm_max_output_tokens": 63000,
                },
                "generative_ai": {
                    "embedding_model": "cohere.embed-v4.0",
                    "embedding_dim": 1536,
                    "rerank_model": "cohere.rerank-v4.0-fast",
                },
            }
        ),
        encoding="utf-8",
    )
    settings = Settings(model_settings_file=str(settings_file))
    load_persisted_model_settings(settings)

    assert settings.oci_enterprise_ai_endpoint == "https://persisted-enterprise.example"
    assert settings.oci_enterprise_ai_project_ocid == "ocid1.generativeaiproject.oc1..persisted"
    assert settings.oci_enterprise_ai_api_key == "sk-persisted-secret"
    assert [model.model_id for model in settings.oci_enterprise_ai_models] == [
        "persisted-text",
        "persisted-vision",
    ]
    assert settings.oci_enterprise_ai_default_model == "persisted-text"
    assert settings.oci_enterprise_ai_llm_model == "persisted-text"
    assert settings.oci_enterprise_ai_vlm_model == "persisted-vision"
    assert settings.oci_enterprise_ai_llm_response_path == "/payload/text"
    assert settings.oci_enterprise_ai_vlm_response_path == "/payload/document"
    assert settings.oci_enterprise_ai_timeout_seconds == 42
    assert settings.oci_enterprise_ai_max_retries == 1
    assert settings.oci_enterprise_ai_llm_max_output_tokens == 1700
    assert settings.oci_enterprise_ai_vlm_max_output_tokens == 63000


def test_update_model_settings_does_not_mutate_runtime_when_persist_fails(
    tmp_path: Path,
) -> None:
    settings = get_settings()
    settings.model_settings_file = str(tmp_path)
    settings.oci_enterprise_ai_endpoint = "https://old-enterprise.example"
    settings.oci_enterprise_ai_api_key = "sk-old-secret"

    resp = client.patch("/api/settings/model", json=_payload())

    assert resp.status_code == 500
    assert settings.oci_enterprise_ai_endpoint == "https://old-enterprise.example"
    assert settings.oci_enterprise_ai_api_key == "sk-old-secret"


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


def test_model_settings_test_enterprise_text_uses_candidate_without_mutating_runtime(
    monkeypatch: MonkeyPatch,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "oci_enterprise_ai_endpoint", "https://runtime.example")
    observed_settings: list[Settings] = []

    class FakeEnterpriseAiClient:
        def __init__(self, settings: Settings) -> None:
            observed_settings.append(settings)

        async def generate(self, prompt: str, context: str) -> str:
            assert prompt
            assert context
            return "接続テスト応答"

    monkeypatch.setattr(settings_routes, "OciEnterpriseAiClient", FakeEnterpriseAiClient)
    payload = _payload()

    resp = client.post(
        "/api/settings/model/test",
        json={
            "settings": payload,
            "target_type": "enterprise_text",
            "model_id": "enterprise-llm",
            "vision_enabled": False,
        },
    )

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["status"] == "success"
    assert body["target_type"] == "enterprise_text"
    assert body["details"]["surface"] == "llm"
    assert observed_settings[0].oci_enterprise_ai_llm_model == "enterprise-llm"
    assert observed_settings[0].oci_enterprise_ai_api_key == "sk-update-secret"
    assert settings.oci_enterprise_ai_endpoint == "https://runtime.example"
    assert "sk-update-secret" not in resp.text


def test_model_settings_test_enterprise_vision_uses_smoke_image_payload(
    monkeypatch: MonkeyPatch,
) -> None:
    observed: list[tuple[Settings, bytes, str, str]] = []

    class FakeEnterpriseAiClient:
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

    monkeypatch.setattr(settings_routes, "OciEnterpriseAiClient", FakeEnterpriseAiClient)
    payload = _payload()

    resp = client.post(
        "/api/settings/model/test",
        json={
            "settings": payload,
            "target_type": "enterprise_vision",
            "model_id": "google.gemini-2.5-flash",
            "vision_enabled": True,
        },
    )

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["status"] == "success"
    assert body["details"]["surface"] == "vision"
    assert body["details"]["response_chars"] == len("画像を確認しました。")
    assert observed[0][0].oci_enterprise_ai_vlm_model == "google.gemini-2.5-flash"
    assert observed[0][1] == settings_routes.MODEL_TEST_IMAGE_BYTES
    assert observed[0][2]
    assert observed[0][3] == "image/jpeg"


def test_model_settings_test_embedding_uses_candidate_model(
    monkeypatch: MonkeyPatch,
) -> None:
    observed_settings: list[Settings] = []

    class FakeGenAiClient:
        def __init__(self, settings: Settings) -> None:
            observed_settings.append(settings)

        async def embed(self, texts: list[str], *, input_type: str) -> list[list[float]]:
            assert texts == ["モデル接続テスト"]
            assert input_type == "SEARCH_QUERY"
            return [[0.0] * 1536]

    monkeypatch.setattr(settings_routes, "OciGenAiClient", FakeGenAiClient)
    payload = _payload()
    payload["generative_ai"]["embedding_model"] = "cohere.embed-custom"

    resp = client.post(
        "/api/settings/model/test",
        json={
            "settings": payload,
            "target_type": "embedding",
            "model_id": "cohere.embed-custom",
            "vision_enabled": False,
        },
    )

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["status"] == "success"
    assert body["details"]["vector_dim"] == 1536
    assert observed_settings[0].oci_genai_embedding_model == "cohere.embed-custom"


def test_model_settings_test_returns_real_error_with_troubleshooting_and_masks_secret(
    monkeypatch: MonkeyPatch,
) -> None:
    class FailingEnterpriseAiClient:
        def __init__(self, settings: Settings) -> None:
            self.settings = settings

        async def generate(self, prompt: str, context: str) -> str:
            raise RuntimeError(
                f"401 Unauthorized: bearer {self.settings.oci_enterprise_ai_api_key}"
            )

    monkeypatch.setattr(settings_routes, "OciEnterpriseAiClient", FailingEnterpriseAiClient)
    payload = _payload()

    resp = client.post(
        "/api/settings/model/test",
        json={
            "settings": payload,
            "target_type": "enterprise_text",
            "model_id": "enterprise-llm",
            "vision_enabled": False,
        },
    )

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["status"] == "failed"
    assert body["error_type"] == "RuntimeError"
    assert "401 Unauthorized" in body["raw_error"]
    assert "<secret>" in body["raw_error"]
    assert body["troubleshooting"]
    assert "sk-update-secret" not in resp.text


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


def test_update_model_settings_allows_invalid_readiness_fields() -> None:
    payload = _payload()
    payload["enterprise_ai"]["endpoint"] = "enterprise-ai.example"
    payload["enterprise_ai"]["project_ocid"] = "not-an-ocid"
    payload["enterprise_ai"]["api_path"] = "responses"

    resp = client.patch("/api/settings/model", json=payload)

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["checks"]["enterprise_ai"] == "invalid"
    settings = get_settings()
    assert settings.oci_enterprise_ai_endpoint == "enterprise-ai.example"
    assert settings.oci_enterprise_ai_project_ocid == "not-an-ocid"
    assert settings.oci_enterprise_ai_llm_path == "responses"


def test_model_settings_requires_enterprise_ai_api_key() -> None:
    payload = _payload()
    payload["enterprise_ai"]["api_key"] = ""
    payload["enterprise_ai"]["has_api_key"] = False

    resp = client.post("/api/settings/model/check", json=payload)

    assert resp.status_code == 200
    assert resp.json()["data"]["checks"]["enterprise_ai"] == "missing"


def test_model_settings_requires_enterprise_ai_model_catalog() -> None:
    payload = _payload()
    payload["enterprise_ai"]["models"] = []
    payload["enterprise_ai"]["default_model_id"] = ""

    resp = client.post("/api/settings/model/check", json=payload)

    assert resp.status_code == 200
    assert resp.json()["data"]["checks"]["enterprise_ai"] == "missing"


def test_enterprise_ai_model_settings_defaults_max_retries_to_three() -> None:
    """設定 API スキーマの最大リトライ回数既定値は 3。"""
    assert EnterpriseAiModelSettings().max_retries == 3
    assert EnterpriseAiModelSettings().llm_max_output_tokens == 1200
    assert EnterpriseAiModelSettings().vlm_max_output_tokens == 65536


def test_model_settings_reports_invalid_default_model() -> None:
    payload = _payload()
    payload["enterprise_ai"]["default_model_id"] = "missing-model"

    resp = client.post("/api/settings/model/check", json=payload)

    assert resp.status_code == 200
    assert resp.json()["data"]["checks"]["enterprise_ai"] == "invalid"


def test_model_settings_rejects_invalid_payload_template() -> None:
    payload = _payload()
    payload["enterprise_ai"]["text_payload_template"] = "[1, 2, 3]"

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


def test_get_oci_settings_returns_runtime_and_config_values(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    key_file = tmp_path / ".oci" / "oci_api_key.pem"
    key_file.parent.mkdir()
    key_file.write_text(
        "-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----\n",
        encoding="utf-8",
    )
    config_file = tmp_path / ".oci" / "config"
    config_file.write_text(
        "\n".join(
            [
                "[DEFAULT]",
                "user=ocid1.user.oc1..runtime",
                "fingerprint=12:34:56:78",
                "tenancy=ocid1.tenancy.oc1..runtime",
                "region=ap-tokyo-1",
                "key_file=/tmp/ignored.pem",
            ]
        ),
        encoding="utf-8",
    )
    settings = get_settings()
    monkeypatch.setattr(settings, "oci_config_file", str(config_file))
    monkeypatch.setattr(settings, "oci_config_profile", "DEFAULT")
    monkeypatch.setattr(settings, "oci_region", "us-chicago-1")

    resp = client.get("/api/settings/oci")

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body == {
        "config_file": str(config_file),
        "profile": "DEFAULT",
        "user": "ocid1.user.oc1..runtime",
        "fingerprint": "12:34:56:78",
        "tenancy": "ocid1.tenancy.oc1..runtime",
        "region": "us-chicago-1",
        "key_file": "~/.oci/oci_api_key.pem",
        "key_file_exists": True,
        "config_file_exists": True,
        "config_source": "runtime",
    }


def test_get_oci_settings_reports_missing_private_key_without_failing(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    settings = get_settings()
    monkeypatch.setattr(settings, "oci_config_file", str(tmp_path / ".oci" / "missing-config"))
    monkeypatch.setattr(settings, "oci_config_profile", "DEFAULT")
    monkeypatch.setattr(settings, "oci_region", "ap-osaka-1")

    resp = client.get("/api/settings/oci")

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["user"] == ""
    assert body["fingerprint"] == ""
    assert body["tenancy"] == ""
    assert body["region"] == "ap-osaka-1"
    assert body["key_file"] == "~/.oci/oci_api_key.pem"
    assert body["key_file_exists"] is False
    assert body["config_file_exists"] is False


def test_update_oci_settings_creates_config_dir_and_file_with_private_permissions(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    settings = get_settings()
    monkeypatch.setattr(settings, "oci_config_file", "~/.oci/config")
    monkeypatch.setattr(settings, "oci_config_profile", "DEFAULT")
    monkeypatch.setattr(settings, "oci_region", "us-chicago-1")
    env_file = _settings_env_file(
        monkeypatch,
        tmp_path,
        "\n".join(
            [
                "# 既存設定",
                "OCI_REGION=us-chicago-1",
                "",
            ]
        ),
    )

    resp = client.patch(
        "/api/settings/oci",
        json={
            "user": "ocid1.user.oc1..new",
            "fingerprint": "12:34:56:78:90:ab:cd:ef",
            "tenancy": "ocid1.tenancy.oc1..new",
            "region": "ap-osaka-1",
        },
    )

    assert resp.status_code == 200
    body = resp.json()["data"]
    config_dir = tmp_path / ".oci"
    config_file = config_dir / "config"
    assert config_dir.is_dir()
    assert config_file.is_file()
    assert stat.S_IMODE(config_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(config_file.stat().st_mode) == 0o600
    content = config_file.read_text(encoding="utf-8")
    assert "[DEFAULT]" in content
    assert "user=ocid1.user.oc1..new" in content
    assert "fingerprint=12:34:56:78:90:ab:cd:ef" in content
    assert "tenancy=ocid1.tenancy.oc1..new" in content
    assert "region=ap-osaka-1" in content
    assert "key_file=~/.oci/oci_api_key.pem" in content
    assert settings.oci_region == "ap-osaka-1"
    assert body["config_file_exists"] is True
    assert body["key_file_exists"] is False
    persisted = env_file.read_text(encoding="utf-8")
    assert "# 既存設定" in persisted
    assert "OCI_CONFIG_FILE=~/.oci/config" in persisted
    assert "OCI_CONFIG_PROFILE=DEFAULT" in persisted
    assert "OCI_REGION=ap-osaka-1" in persisted


def test_update_oci_settings_allows_incomplete_profile(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    settings = get_settings()
    monkeypatch.setattr(settings, "oci_config_file", "~/.oci/config")
    monkeypatch.setattr(settings, "oci_config_profile", "DEFAULT")
    monkeypatch.setattr(settings, "oci_region", "us-chicago-1")
    env_file = _settings_env_file(monkeypatch, tmp_path)

    resp = client.patch(
        "/api/settings/oci",
        json={
            "user": "",
            "fingerprint": "",
            "tenancy": "",
            "region": "",
        },
    )

    assert resp.status_code == 200
    content = (tmp_path / ".oci" / "config").read_text(encoding="utf-8")
    assert "user=" in content
    assert "fingerprint=" in content
    assert "tenancy=" in content
    assert "region=" in content
    assert settings.oci_region == ""
    assert "OCI_REGION=" in env_file.read_text(encoding="utf-8")


def test_update_oci_settings_preserves_existing_non_default_profile(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    config_dir = tmp_path / ".oci"
    config_dir.mkdir()
    config_file = config_dir / "config"
    config_file.write_text(
        "\n".join(
            [
                "[DEFAULT]",
                "user=ocid1.user.oc1..old",
                "fingerprint=aa:bb:cc:dd",
                "[ADMIN_USER]",
                "user=ocid1.user.oc1..admin",
                "fingerprint=11:22:33:44",
                "tenancy=ocid1.tenancy.oc1..admin",
                "region=us-chicago-1",
                "key_file=keys/admin.pem",
            ]
        ),
        encoding="utf-8",
    )
    settings = get_settings()
    monkeypatch.setattr(settings, "oci_config_file", str(config_file))
    monkeypatch.setattr(settings, "oci_config_profile", "DEFAULT")
    _settings_env_file(monkeypatch, tmp_path)

    resp = client.patch(
        "/api/settings/oci",
        json={
            "user": "ocid1.user.oc1..new",
            "fingerprint": "12:34:56:78:90:ab:cd:ef",
            "tenancy": "ocid1.tenancy.oc1..new",
            "region": "ap-osaka-1",
        },
    )

    assert resp.status_code == 200
    content = config_file.read_text(encoding="utf-8")
    assert "[ADMIN_USER]" in content
    assert "user=ocid1.user.oc1..admin" in content
    assert "key_file=keys/admin.pem" in content
    assert "user=ocid1.user.oc1..new" in content


def test_update_oci_settings_does_not_mutate_runtime_when_env_write_fails(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    settings = get_settings()
    monkeypatch.setattr(settings, "oci_config_file", "~/.oci/config")
    monkeypatch.setattr(settings, "oci_config_profile", "DEFAULT")
    monkeypatch.setattr(settings, "oci_region", "us-chicago-1")
    monkeypatch.setattr(settings_routes, "BACKEND_ENV_FILE", tmp_path)

    resp = client.patch(
        "/api/settings/oci",
        json={
            "user": "ocid1.user.oc1..new",
            "fingerprint": "12:34:56:78:90:ab:cd:ef",
            "tenancy": "ocid1.tenancy.oc1..new",
            "region": "ap-osaka-1",
        },
    )

    assert resp.status_code == 500
    assert settings.oci_region == "us-chicago-1"


def test_update_oci_object_storage_settings_persists_env_and_mutates_runtime(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "object_storage_region", "ap-osaka-1")
    monkeypatch.setattr(settings, "object_storage_namespace", "old-namespace")
    env_file = _settings_env_file(
        monkeypatch,
        tmp_path,
        "\n".join(
            [
                "# OCI Object Storage",
                "OBJECT_STORAGE_REGION=ap-osaka-1",
                "OBJECT_STORAGE_NAMESPACE=old-namespace",
                "OBJECT_STORAGE_BUCKET=rag-originals",
                "",
            ]
        ),
    )

    resp = client.patch(
        "/api/settings/oci/object-storage",
        json={
            "object_storage_region": "us-chicago-1",
            "object_storage_namespace": "mytenancynamespace",
        },
    )

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["object_storage_region"] == "us-chicago-1"
    assert body["object_storage_namespace"] == "mytenancynamespace"
    assert settings.object_storage_region == "us-chicago-1"
    assert settings.object_storage_namespace == "mytenancynamespace"
    persisted = env_file.read_text(encoding="utf-8")
    assert "OBJECT_STORAGE_REGION=us-chicago-1" in persisted
    assert "OBJECT_STORAGE_NAMESPACE=mytenancynamespace" in persisted
    assert "OBJECT_STORAGE_BUCKET=rag-originals" in persisted


def test_update_oci_object_storage_settings_does_not_mutate_runtime_when_env_write_fails(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "object_storage_region", "ap-osaka-1")
    monkeypatch.setattr(settings, "object_storage_namespace", "old-namespace")
    monkeypatch.setattr(settings_routes, "BACKEND_ENV_FILE", tmp_path)

    resp = client.patch(
        "/api/settings/oci/object-storage",
        json={
            "object_storage_region": "us-chicago-1",
            "object_storage_namespace": "mytenancynamespace",
        },
    )

    assert resp.status_code == 500
    assert settings.object_storage_region == "ap-osaka-1"
    assert settings.object_storage_namespace == "old-namespace"


def test_update_oci_object_storage_settings_rejects_invalid_values() -> None:
    resp = client.patch(
        "/api/settings/oci/object-storage",
        json={
            "object_storage_region": "US_CHICAGO_1",
            "object_storage_namespace": "invalid namespace",
        },
    )

    assert resp.status_code == 422
    assert resp.json()["error_messages"]


def test_test_oci_config_reports_missing_private_key_after_save(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    settings = get_settings()
    monkeypatch.setattr(settings, "oci_config_file", "~/.oci/config")
    monkeypatch.setattr(settings, "oci_config_profile", "DEFAULT")
    _settings_env_file(monkeypatch, tmp_path)
    client.patch(
        "/api/settings/oci",
        json={
            "user": "ocid1.user.oc1..new",
            "fingerprint": "12:34:56:78:90:ab:cd:ef",
            "tenancy": "ocid1.tenancy.oc1..new",
            "region": "ap-osaka-1",
        },
    )

    resp = client.post("/api/settings/oci/config/test")

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["status"] == "failed"
    assert body["config_file_exists"] is True
    assert body["key_file_exists"] is False
    assert body["missing_fields"] == []
    assert body["oci_directory_mode"] == "0700"
    assert body["config_file_mode"] == "0600"
    assert body["key_file_mode"] is None
    assert "秘密鍵ファイルが見つかりません" in body["message"]


def test_test_oci_config_succeeds_with_private_key_and_permissions(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    settings = get_settings()
    monkeypatch.setattr(settings, "oci_config_file", "~/.oci/config")
    monkeypatch.setattr(settings, "oci_config_profile", "DEFAULT")
    _settings_env_file(monkeypatch, tmp_path)
    client.patch(
        "/api/settings/oci",
        json={
            "user": "ocid1.user.oc1..new",
            "fingerprint": "12:34:56:78:90:ab:cd:ef",
            "tenancy": "ocid1.tenancy.oc1..new",
            "region": "ap-osaka-1",
        },
    )
    key_file = tmp_path / ".oci" / "oci_api_key.pem"
    key_file.write_text(
        "-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----\n",
        encoding="utf-8",
    )
    key_file.chmod(0o600)

    resp = client.post("/api/settings/oci/config/test")

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["status"] == "success"
    assert body["key_file_exists"] is True
    assert body["permission_issues"] == []
    assert body["key_file_mode"] == "0600"


def test_test_oci_config_reports_encrypted_private_key_without_pass_phrase(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    settings = get_settings()
    monkeypatch.setattr(settings, "oci_config_file", "~/.oci/config")
    monkeypatch.setattr(settings, "oci_config_profile", "DEFAULT")
    _settings_env_file(monkeypatch, tmp_path)
    client.patch(
        "/api/settings/oci",
        json={
            "user": "ocid1.user.oc1..new",
            "fingerprint": "12:34:56:78:90:ab:cd:ef",
            "tenancy": "ocid1.tenancy.oc1..new",
            "region": "ap-osaka-1",
        },
    )
    key_file = tmp_path / ".oci" / "oci_api_key.pem"
    key_file.write_text(
        "-----BEGIN ENCRYPTED PRIVATE KEY-----\nabc\n-----END ENCRYPTED PRIVATE KEY-----\n",
        encoding="utf-8",
    )
    key_file.chmod(0o600)

    resp = client.post("/api/settings/oci/config/test")

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["status"] == "failed"
    assert body["key_file_exists"] is True
    assert body["error_type"] == "OciPrivateKeyPassPhraseRequiredError"
    assert "暗号化されています" in body["message"]


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
    assert "暗号化されています" in resp.text
    assert initialized is False


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


def test_upload_oci_private_key_rejects_encrypted_content(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    pem = (
        b"-----BEGIN ENCRYPTED PRIVATE KEY-----\n" b"abc\n" b"-----END ENCRYPTED PRIVATE KEY-----\n"
    )

    resp = client.post(
        "/api/settings/oci/key-file",
        files={"file": ("encrypted.pem", pem, "application/x-pem-file")},
    )

    assert resp.status_code == 400
    assert "pass phrase" in resp.text
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
    tmp_path: Path,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "oracle_user", "old_user")
    monkeypatch.setattr(settings, "oracle_password", "old-secret")
    monkeypatch.setattr(settings, "oracle_dsn", "old-dsn")
    monkeypatch.setattr(settings, "oracle_client_lib_dir", "/opt/oracle/instantclient_23_26")
    monkeypatch.setattr(settings, "oracle_wallet_dir", "")
    monkeypatch.setattr(settings, "oracle_wallet_password", "")
    env_file = _database_env_file(
        monkeypatch,
        tmp_path,
        "\n".join(
            [
                "# 既存設定",
                "ORACLE_USER=old_user",
                "ORACLE_DSN=old-dsn",
                "ORACLE_USER=duplicate",
                "",
            ]
        ),
    )

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
    persisted = env_file.read_text(encoding="utf-8")
    assert "# 既存設定" in persisted
    assert persisted.count("ORACLE_USER=") == 1
    assert "ORACLE_USER=rag_app" in persisted
    assert "ORACLE_PASSWORD=old-secret" in persisted
    assert "ORACLE_DSN=adb.example.com/rag" in persisted
    assert "ORACLE_CLIENT_LIB_DIR=/opt/oracle/instantclient_23_26" in persisted
    assert "ORACLE_WALLET_PASSWORD=" in persisted


def test_update_database_settings_does_not_mutate_runtime_when_env_write_fails(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "oracle_user", "old_user")
    monkeypatch.setattr(settings, "oracle_password", "old-secret")
    monkeypatch.setattr(settings, "oracle_dsn", "old-dsn")
    monkeypatch.setattr(settings, "oracle_wallet_dir", "")
    monkeypatch.setattr(settings, "oracle_wallet_password", "old-wallet-secret")
    monkeypatch.setattr(settings_routes, "BACKEND_ENV_FILE", tmp_path)

    resp = client.patch(
        "/api/settings/database",
        json={
            "user": "rag_app",
            "dsn": "adb.example.com/rag",
            "wallet_dir": "/opt/oracle/wallet",
            "password": "new-secret",
            "wallet_password": "new-wallet-secret",
        },
    )

    assert resp.status_code == 500
    assert settings.oracle_user == "old_user"
    assert settings.oracle_password == "old-secret"
    assert settings.oracle_dsn == "old-dsn"
    assert settings.oracle_wallet_password == "old-wallet-secret"


def test_update_database_settings_clears_saved_secrets(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "oracle_user", "rag_app")
    monkeypatch.setattr(settings, "oracle_password", "old-secret")
    monkeypatch.setattr(settings, "oracle_dsn", "ragdb_high")
    monkeypatch.setattr(settings, "oracle_wallet_dir", "")
    monkeypatch.setattr(settings, "oracle_wallet_password", "old-wallet-secret")
    env_file = _database_env_file(
        monkeypatch,
        tmp_path,
        "\n".join(
            [
                "ORACLE_USER=rag_app",
                "ORACLE_PASSWORD=old-secret",
                "ORACLE_DSN=ragdb_high",
                "ORACLE_WALLET_PASSWORD=old-wallet-secret",
                "",
            ]
        ),
    )

    resp = client.patch(
        "/api/settings/database",
        json={
            "user": "rag_app",
            "dsn": "ragdb_high",
            "wallet_dir": settings.resolved_oracle_wallet_dir,
            "clear_password": True,
            "clear_wallet_password": True,
        },
    )

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["has_password"] is False
    assert body["has_wallet_password"] is False
    assert settings.oracle_password == ""
    assert settings.oracle_wallet_password == ""
    persisted = env_file.read_text(encoding="utf-8")
    assert "ORACLE_PASSWORD=" in persisted
    assert "ORACLE_PASSWORD=old-secret" not in persisted
    assert "ORACLE_WALLET_PASSWORD=" in persisted
    assert "ORACLE_WALLET_PASSWORD=old-wallet-secret" not in persisted


def test_database_connection_test_uses_candidate_without_mutating_runtime(
    monkeypatch: MonkeyPatch,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "oracle_user", "")
    monkeypatch.setattr(settings, "oracle_password", "")
    monkeypatch.setattr(settings, "oracle_dsn", "")
    monkeypatch.setattr(settings, "oracle_wallet_dir", "")
    monkeypatch.setattr(settings, "oracle_wallet_password", "")

    async def fake_test_oracle_connection(candidate: Settings) -> None:
        assert candidate.oracle_user == "rag_app"
        assert candidate.oracle_password == "candidate-secret"
        assert candidate.oracle_dsn == "adb.example.com/rag"

    monkeypatch.setattr(settings_routes, "test_oracle_connection", fake_test_oracle_connection)

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
    assert body["status"] == "success"
    assert body["readiness"] == "ok"
    assert body["elapsed_ms"] >= 0
    assert body["details"]["timeout_seconds"] == settings.oracle_db_test_timeout_seconds
    assert settings.oracle_user == ""
    assert settings.oracle_password == ""
    assert "candidate-secret" not in resp.text


def test_database_connection_test_returns_wallet_password_guidance(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "oracle_user", "")
    monkeypatch.setattr(settings, "oracle_password", "")
    monkeypatch.setattr(settings, "oracle_dsn", "")
    monkeypatch.setattr(settings, "oracle_client_lib_dir", str(tmp_path / "instantclient_23_26"))
    monkeypatch.setattr(settings, "oracle_wallet_dir", "")
    monkeypatch.setattr(settings, "oracle_wallet_password", "")
    wallet_dir = Path(settings.resolved_oracle_wallet_dir)
    wallet_dir.mkdir(parents=True)

    async def fake_test_oracle_connection(candidate: Settings) -> None:
        raise OracleWalletPasswordRequiredError("Wallet パスワードを入力してください。")

    monkeypatch.setattr(settings_routes, "test_oracle_connection", fake_test_oracle_connection)

    resp = client.post(
        "/api/settings/database/test",
        json={
            "user": "rag_app",
            "dsn": "ragdb_high",
            "wallet_dir": "",
        },
    )

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["status"] == "failed"
    assert body["message"] == "Wallet パスワードを入力してください。"
    assert body["error_type"] == "OracleWalletPasswordRequiredError"
    assert body["elapsed_ms"] >= 0
    assert body["troubleshooting"]


def test_database_connection_test_returns_timeout_guidance(
    monkeypatch: MonkeyPatch,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "oracle_user", "")
    monkeypatch.setattr(settings, "oracle_password", "")
    monkeypatch.setattr(settings, "oracle_dsn", "")
    monkeypatch.setattr(settings, "oracle_wallet_dir", "")
    monkeypatch.setattr(settings, "oracle_wallet_password", "")

    async def fake_test_oracle_connection(candidate: Settings) -> None:
        raise OracleConnectionTimeoutError("Oracle 26ai 接続テストが 15 秒でタイムアウトしました。")

    monkeypatch.setattr(settings_routes, "test_oracle_connection", fake_test_oracle_connection)

    resp = client.post(
        "/api/settings/database/test",
        json={
            "user": "rag_app",
            "dsn": "ragdb_high",
            "wallet_dir": "",
            "password": "candidate-secret",
        },
    )

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["status"] == "failed"
    assert body["error_type"] == "OracleConnectionTimeoutError"
    assert "タイムアウト" in body["message"]
    assert any("TCPS 1522" in tip for tip in body["troubleshooting"])
    assert (
        body["details"]["tcp_connect_timeout_seconds"]
        == settings.oracle_tcp_connect_timeout_seconds
    )


def test_database_connection_test_classifies_oracle_operational_error(
    monkeypatch: MonkeyPatch,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "oracle_user", "")
    monkeypatch.setattr(settings, "oracle_password", "")
    monkeypatch.setattr(settings, "oracle_dsn", "")
    monkeypatch.setattr(settings, "oracle_wallet_dir", "")
    monkeypatch.setattr(settings, "oracle_wallet_password", "")

    class FakeOperationalError(Exception):
        """python-oracledb OperationalError 相当のテスト用例外。"""

    async def fake_test_oracle_connection(candidate: Settings) -> None:
        raise FakeOperationalError(
            "ORA-01017: invalid username/password; logon denied candidate-secret"
        )

    monkeypatch.setattr(settings_routes, "test_oracle_connection", fake_test_oracle_connection)

    resp = client.post(
        "/api/settings/database/test",
        json={
            "user": "rag_app",
            "dsn": "ragdb_high",
            "wallet_dir": "",
            "password": "candidate-secret",
        },
    )

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["status"] == "failed"
    assert body["error_type"] == "FakeOperationalError"
    assert "ORA-01017" in body["message"]
    assert "ユーザー名または DB パスワード" in body["message"]
    assert body["details"]["oracle_error_codes"] == "ORA-01017"
    assert any("DB パスワード" in tip for tip in body["troubleshooting"])
    assert "candidate-secret" not in resp.text


def test_database_connection_test_classifies_adb_acl_rejection(
    monkeypatch: MonkeyPatch,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "oracle_user", "")
    monkeypatch.setattr(settings, "oracle_password", "")
    monkeypatch.setattr(settings, "oracle_dsn", "")
    monkeypatch.setattr(settings, "oracle_wallet_dir", "")
    monkeypatch.setattr(settings, "oracle_wallet_password", "")

    class FakeOperationalError(Exception):
        """python-oracledb OperationalError 相当のテスト用例外。"""

    async def fake_test_oracle_connection(candidate: Settings) -> None:
        raise FakeOperationalError(
            "DPY-6005: cannot connect to database. DPY-6000: listener refused. "
            "ORA-12506: listener rejected connection based on service ACL filtering"
        )

    monkeypatch.setattr(settings_routes, "test_oracle_connection", fake_test_oracle_connection)

    resp = client.post(
        "/api/settings/database/test",
        json={
            "user": "rag_app",
            "dsn": "ragdb_high",
            "wallet_dir": "",
            "password": "candidate-secret",
        },
    )

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert "ORA-12506" in body["message"]
    assert "アクセス制御リスト" in body["message"]
    assert body["details"]["oracle_error_codes"] == "DPY-6005, DPY-6000, ORA-12506"
    assert any("Network Access / ACL" in tip for tip in body["troubleshooting"])


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
    assert (wallet_dir / "ewallet.pem").is_file()
    assert not (wallet_dir / "ewallet.p12").exists()
    assert not (wallet_dir / "keystore.jks").exists()
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
    (wallet_dir / "ewallet.p12").write_text("legacy-password-wallet", encoding="utf-8")
    (wallet_dir / "keystore.jks").write_text("legacy-java-keystore", encoding="utf-8")
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
    assert not (wallet_dir / "ewallet.p12").exists()
    assert not (wallet_dir / "keystore.jks").exists()


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
                _wallet_zip(
                    {
                        "../tnsnames.ora": "bad",
                        "sqlnet.ora": "...",
                        "cwallet.sso": "...",
                        "ewallet.pem": "...",
                    }
                ),
                "application/zip",
            )
        },
    )

    assert resp.status_code == 400
    assert sentinel.read_text(encoding="utf-8") == "keep"
    assert not (wallet_dir / "tnsnames.ora").exists()


class _FakeAdbInfo:
    """OCI SDK の AutonomousDatabase model 代替。"""

    def __init__(
        self,
        lifecycle_state: str,
        *,
        display_name: str = "RAG ADB",
        adb_id: str = "ocid1.autonomousdatabase.oc1..fake",
    ) -> None:
        self.id = adb_id
        self.display_name = display_name
        self.lifecycle_state = lifecycle_state
        self.db_name = "ragdb"
        self.cpu_core_count = 2
        self.data_storage_size_in_tbs = 1.0


class _FakeAdbResponse:
    def __init__(self, data: _FakeAdbInfo) -> None:
        self.data = data


def _make_fake_database_client(
    lifecycle_state: str,
    calls: list[str],
) -> type:
    """指定 lifecycle を返し、start/stop 呼び出しを記録する Fake DatabaseClient。"""

    class _FakeDatabaseClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            calls.append("init")

        def get_autonomous_database(self, autonomous_database_id: str) -> _FakeAdbResponse:
            calls.append(f"get:{autonomous_database_id}")
            return _FakeAdbResponse(_FakeAdbInfo(lifecycle_state))

        def start_autonomous_database(self, autonomous_database_id: str) -> None:
            calls.append(f"start:{autonomous_database_id}")

        def stop_autonomous_database(self, autonomous_database_id: str) -> None:
            calls.append(f"stop:{autonomous_database_id}")

    return _FakeDatabaseClient


def _patch_adb_client(
    monkeypatch: MonkeyPatch,
    lifecycle_state: str,
    calls: list[str],
) -> None:
    from app.clients.oci_database import OciDatabaseClient

    fake_client = _make_fake_database_client(lifecycle_state, calls)()

    def _factory(settings: Settings | None = None) -> OciDatabaseClient:
        return OciDatabaseClient(settings=settings, database_client=fake_client)

    monkeypatch.setattr(settings_routes, "OciDatabaseClient", _factory)


def test_get_adb_info_returns_not_configured_without_ocid(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "oracle_adb_ocid", "")

    resp = client.get("/api/settings/database/adb")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["status"] == "not_configured"
    assert data["lifecycle_state"] is None


def test_get_adb_info_returns_lifecycle_from_oci(monkeypatch: MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "oracle_adb_ocid", "ocid1.autonomousdatabase.oc1..fake")
    calls: list[str] = []
    _patch_adb_client(monkeypatch, "AVAILABLE", calls)

    resp = client.get("/api/settings/database/adb")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["status"] == "success"
    assert data["lifecycle_state"] == "AVAILABLE"
    assert data["display_name"] == "RAG ADB"
    assert any(call.startswith("get:") for call in calls)


def test_update_adb_settings_persists_ocid_and_region(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "oracle_adb_ocid", "")
    env_file = _database_env_file(monkeypatch, tmp_path)
    calls: list[str] = []
    _patch_adb_client(monkeypatch, "STOPPED", calls)

    resp = client.post(
        "/api/settings/database/adb/settings",
        json={"adb_ocid": "ocid1.autonomousdatabase.oc1..saved", "region": "ap-tokyo-1"},
    )

    assert resp.status_code == 200
    assert settings.oracle_adb_ocid == "ocid1.autonomousdatabase.oc1..saved"
    assert settings.oci_region == "ap-tokyo-1"
    persisted = env_file.read_text(encoding="utf-8")
    assert "ORACLE_ADB_OCID=ocid1.autonomousdatabase.oc1..saved" in persisted
    assert "OCI_REGION=ap-tokyo-1" in persisted


def test_start_adb_sends_start_when_stopped(monkeypatch: MonkeyPatch) -> None:
    settings = get_settings()
    ocid = "ocid1.autonomousdatabase.oc1..fake"
    monkeypatch.setattr(settings, "oracle_adb_ocid", ocid)
    calls: list[str] = []
    _patch_adb_client(monkeypatch, "STOPPED", calls)

    resp = client.post("/api/settings/database/adb/start")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["status"] == "accepted"
    assert data["lifecycle_state"] == "STARTING"
    assert f"start:{ocid}" in calls


def test_start_adb_reports_already_available(monkeypatch: MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "oracle_adb_ocid", "ocid1.autonomousdatabase.oc1..fake")
    calls: list[str] = []
    _patch_adb_client(monkeypatch, "AVAILABLE", calls)

    resp = client.post("/api/settings/database/adb/start")

    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "already_available"
    assert not any(call.startswith("start:") for call in calls)


def test_stop_adb_sends_stop_when_available(monkeypatch: MonkeyPatch) -> None:
    settings = get_settings()
    ocid = "ocid1.autonomousdatabase.oc1..fake"
    monkeypatch.setattr(settings, "oracle_adb_ocid", ocid)
    calls: list[str] = []
    _patch_adb_client(monkeypatch, "AVAILABLE", calls)

    resp = client.post("/api/settings/database/adb/stop")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["status"] == "accepted"
    assert data["lifecycle_state"] == "STOPPING"
    assert f"stop:{ocid}" in calls


def test_stop_adb_reports_already_stopped(monkeypatch: MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "oracle_adb_ocid", "ocid1.autonomousdatabase.oc1..fake")
    calls: list[str] = []
    _patch_adb_client(monkeypatch, "STOPPED", calls)

    resp = client.post("/api/settings/database/adb/stop")

    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "already_stopped"
    assert not any(call.startswith("stop:") for call in calls)


def test_start_adb_without_ocid_returns_not_configured(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "oracle_adb_ocid", "")

    resp = client.post("/api/settings/database/adb/start")

    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "not_configured"


def test_get_upload_storage_settings_returns_runtime_values(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = get_settings()
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
    assert body["local_storage_dir"] == str(tmp_path / "uploads")
    assert body["object_storage_region"] == "us-chicago-1"
    assert body["object_storage_namespace"] == "example-namespace"
    assert body["object_storage_bucket"] == "rag-originals"
    assert body["readiness"] == "ok"
    assert body["max_upload_bytes"] == 12345
    assert body["config_source"] == "runtime"


def test_update_upload_storage_settings_persists_env_and_mutates_runtime(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "object_storage_region", "us-chicago-1")
    monkeypatch.setattr(settings, "object_storage_namespace", "global-namespace")
    env_file = _upload_storage_env_file(
        monkeypatch,
        tmp_path,
        "\n".join(
            [
                "# 既存設定",
                "UPLOAD_STORAGE_BACKEND=local",
                "LOCAL_STORAGE_DIR=/old/uploads",
                "UPLOAD_STORAGE_BACKEND=duplicate",
                "",
            ]
        ),
    )

    resp = client.patch(
        "/api/settings/upload-storage",
        json={
            "backend": "oci",
            "local_storage_dir": "/u01/production-ready-rag",
            "object_storage_bucket": "rag-originals",
        },
    )

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["backend"] == "oci"
    assert body["readiness"] == "ok"
    assert settings.upload_storage_backend == "oci"
    assert settings.local_storage_dir == "/u01/production-ready-rag"
    assert settings.object_storage_namespace == "global-namespace"
    assert settings.object_storage_bucket == "rag-originals"
    persisted = env_file.read_text(encoding="utf-8")
    assert "# 既存設定" in persisted
    assert persisted.count("UPLOAD_STORAGE_BACKEND=") == 1
    assert "UPLOAD_STORAGE_BACKEND=oci" in persisted
    assert "LOCAL_STORAGE_DIR=/u01/production-ready-rag" in persisted
    assert "OBJECT_STORAGE_REGION=us-chicago-1" in persisted
    assert "OBJECT_STORAGE_NAMESPACE=global-namespace" in persisted
    assert "OBJECT_STORAGE_BUCKET=rag-originals" in persisted


def test_update_upload_storage_settings_can_apply_namespace_from_oci_settings_draft(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "object_storage_namespace", "")
    env_file = _upload_storage_env_file(monkeypatch, tmp_path)

    resp = client.patch(
        "/api/settings/upload-storage",
        json={
            "backend": "oci",
            "local_storage_dir": "/u01/production-ready-rag",
            "object_storage_namespace": "oci-page-namespace",
            "object_storage_bucket": "rag-originals",
        },
    )

    assert resp.status_code == 200
    assert settings.object_storage_namespace == "oci-page-namespace"
    assert settings.object_storage_bucket == "rag-originals"
    persisted = env_file.read_text(encoding="utf-8")
    assert "OBJECT_STORAGE_NAMESPACE=oci-page-namespace" in persisted
    assert "OBJECT_STORAGE_BUCKET=rag-originals" in persisted


def test_update_upload_storage_settings_does_not_mutate_runtime_when_env_write_fails(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "upload_storage_backend", "local")
    monkeypatch.setattr(settings, "local_storage_dir", "/old/uploads")
    monkeypatch.setattr(settings, "object_storage_namespace", "global-namespace")
    monkeypatch.setattr(settings, "object_storage_bucket", "old-bucket")
    monkeypatch.setattr(settings_routes, "BACKEND_ENV_FILE", tmp_path)

    resp = client.patch(
        "/api/settings/upload-storage",
        json={
            "backend": "oci",
            "local_storage_dir": "/u01/production-ready-rag",
            "object_storage_bucket": "rag-originals",
        },
    )

    assert resp.status_code == 500
    assert settings.upload_storage_backend == "local"
    assert settings.local_storage_dir == "/old/uploads"
    assert settings.object_storage_namespace == "global-namespace"
    assert settings.object_storage_bucket == "old-bucket"


def test_update_upload_storage_settings_allows_missing_selected_backend_fields(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "object_storage_namespace", "global-namespace")
    env_file = _upload_storage_env_file(monkeypatch, tmp_path)

    resp = client.patch(
        "/api/settings/upload-storage",
        json={
            "backend": "oci",
            "local_storage_dir": "/u01/production-ready-rag",
            "object_storage_bucket": "",
        },
    )

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["backend"] == "oci"
    assert body["readiness"] == "missing"
    assert settings.upload_storage_backend == "oci"
    assert settings.object_storage_namespace == "global-namespace"
    assert settings.object_storage_bucket == ""
    persisted = env_file.read_text(encoding="utf-8")
    assert "OBJECT_STORAGE_NAMESPACE=global-namespace" in persisted
    assert "OBJECT_STORAGE_BUCKET=" in persisted


def test_update_upload_storage_settings_allows_missing_global_namespace(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "object_storage_namespace", "")
    env_file = _upload_storage_env_file(monkeypatch, tmp_path)

    resp = client.patch(
        "/api/settings/upload-storage",
        json={
            "backend": "oci",
            "local_storage_dir": "/u01/production-ready-rag",
            "object_storage_bucket": "rag-originals",
        },
    )

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["backend"] == "oci"
    assert body["readiness"] == "missing"
    assert settings.object_storage_namespace == ""
    assert settings.object_storage_bucket == "rag-originals"
    persisted = env_file.read_text(encoding="utf-8")
    assert "OBJECT_STORAGE_NAMESPACE=" in persisted
    assert "OBJECT_STORAGE_BUCKET=rag-originals" in persisted


def _database_env_file(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
    content: str = "",
) -> Path:
    return _settings_env_file(monkeypatch, tmp_path, content)


def _upload_storage_env_file(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
    content: str = "",
) -> Path:
    return _settings_env_file(monkeypatch, tmp_path, content)


def _settings_env_file(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
    content: str = "",
) -> Path:
    env_file = tmp_path / ".env"
    if content:
        env_file.write_text(content, encoding="utf-8")
    monkeypatch.setattr(settings_routes, "BACKEND_ENV_FILE", env_file)
    return env_file


def _payload() -> dict[str, Any]:
    return {
        "enterprise_ai": {
            "endpoint": "https://enterprise-ai.example",
            "project_ocid": "ocid1.generativeaiproject.oc1..example",
            "api_key": "sk-update-secret",
            "has_api_key": False,
            "clear_api_key": False,
            "models": [
                {
                    "model_id": "enterprise-llm",
                    "display_name": "標準 LLM",
                    "vision_enabled": False,
                },
                {
                    "model_id": "enterprise-vlm",
                    "display_name": "Vision LLM",
                    "vision_enabled": True,
                },
            ],
            "default_model_id": "enterprise-llm",
            "api_path": "/responses",
            "text_payload_template": LLM_TEMPLATE,
            "vision_payload_template": VLM_TEMPLATE,
            "text_response_path": "/data/text",
            "vision_response_path": "/data/document",
            "timeout_seconds": 60.0,
            "max_retries": 2,
            "llm_max_output_tokens": 1600,
            "vlm_max_output_tokens": 64000,
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
        "ewallet.pem": "-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----\n",
        "ewallet.p12": "password-wallet",
        "keystore.jks": "java-keystore",
    }
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        for name, content in wallet_entries.items():
            archive.writestr(name, content)
    return buffer.getvalue()
