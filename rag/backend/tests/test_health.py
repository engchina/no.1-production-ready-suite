"""ヘルスチェックの疎通テスト。"""

import asyncio
import logging
from pathlib import Path

from pytest import LogCaptureFixture, MonkeyPatch

from app.api.routes import health as health_route
from app.config import EnterpriseAiConfiguredModel, get_settings
from app.main import UNHANDLED_ERROR_MESSAGE, app, create_app
from tests.support import AsgiTestClient

client = AsgiTestClient(app)
LLM_TEMPLATE = '{"input":"${user_message}"}'
VLM_TEMPLATE = '{"input":"${data_base64}"}'


def test_health() -> None:
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["data"]["status"] == "ok"
    assert body["data"]["version"] == "0.1.0"


def test_request_id_header_is_generated_and_preserved() -> None:
    generated = client.get("/api/health")
    assert generated.headers["x-request-id"]

    preserved = client.get("/api/health", headers={"X-Request-ID": "client-request-id"})
    assert preserved.headers["x-request-id"] == "client-request-id"


def test_unsafe_request_id_header_is_not_reflected() -> None:
    """安全でない request id はレスポンスへ反射せず、新しく採番する。"""
    resp = client.get("/api/health", headers={"X-Request-ID": "bad request id"})

    assert resp.status_code == 200
    assert resp.headers["x-request-id"]
    assert resp.headers["x-request-id"] != "bad request id"
    assert " " not in resp.headers["x-request-id"]


def test_readiness_oci_missing_config_is_degraded(monkeypatch: MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "upload_storage_backend", "oci")
    for field in (
        "oci_region",
        "oci_compartment_id",
        "oci_enterprise_ai_endpoint",
        "oci_enterprise_ai_project_ocid",
        "oci_enterprise_ai_api_key",
        "oci_enterprise_ai_default_model",
        "oci_enterprise_ai_llm_model",
        "oci_enterprise_ai_vlm_model",
        "oci_genai_embedding_model",
        "oci_genai_rerank_model",
        "oracle_user",
        "oracle_password",
        "oracle_dsn",
        "oracle_wallet_dir",
        "object_storage_namespace",
        "object_storage_bucket",
    ):
        monkeypatch.setattr(settings, field, "")
    monkeypatch.setattr(settings, "oci_enterprise_ai_models", [])

    resp = client.get("/api/ready")

    assert resp.status_code == 503
    body = resp.json()
    assert body["data"]["status"] == "degraded"
    assert body["data"]["checks"] == {
        "oci_common": "missing",
        "enterprise_ai": "missing",
        "genai": "missing",
        "oracle": "missing",
        "object_storage": "missing",
    }


def test_readiness_oci_complete_config_is_ok(monkeypatch: MonkeyPatch) -> None:
    _configure_oci_readiness(monkeypatch, oracle_password="super-secret-password")

    resp = client.get("/api/ready")

    assert resp.status_code == 200
    body = resp.json()
    assert body["data"]["status"] == "ok"
    assert body["data"]["checks"] == {
        "oci_common": "ok",
        "enterprise_ai": "ok",
        "genai": "ok",
        "oracle": "ok",
        "object_storage": "ok",
    }
    assert "super-secret-password" not in str(body)


def test_readiness_with_local_upload_storage_checks_local_storage(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    _configure_oci_readiness(monkeypatch)
    settings = get_settings()
    monkeypatch.setattr(settings, "upload_storage_backend", "local")
    monkeypatch.setattr(settings, "local_storage_dir", str(tmp_path / "upload-storage"))

    resp = client.get("/api/ready")

    assert resp.status_code == 200
    body = resp.json()
    assert body["data"]["checks"] == {
        "oci_common": "ok",
        "enterprise_ai": "ok",
        "genai": "ok",
        "oracle": "ok",
        "local_storage": "ok",
    }


def test_readiness_oci_requires_enterprise_ai_model_catalog(
    monkeypatch: MonkeyPatch,
) -> None:
    _configure_oci_readiness(monkeypatch)
    settings = get_settings()
    monkeypatch.setattr(settings, "oci_enterprise_ai_models", [])
    monkeypatch.setattr(settings, "oci_enterprise_ai_default_model", "")
    monkeypatch.setattr(settings, "oci_enterprise_ai_llm_model", "")
    monkeypatch.setattr(settings, "oci_enterprise_ai_vlm_model", "")
    monkeypatch.setattr(settings, "oci_enterprise_ai_llm_payload_template", LLM_TEMPLATE)
    monkeypatch.setattr(settings, "oci_enterprise_ai_vlm_payload_template", VLM_TEMPLATE)

    resp = client.get("/api/ready")

    assert resp.status_code == 503
    assert resp.json()["data"]["checks"]["enterprise_ai"] == "missing"


def test_readiness_production_oci_requires_audit_salt(monkeypatch: MonkeyPatch) -> None:
    _configure_oci_readiness(monkeypatch, environment="production", audit_context_hash_salt="")

    resp = client.get("/api/ready")

    assert resp.status_code == 503
    body = resp.json()
    assert body["data"]["status"] == "degraded"
    assert body["data"]["checks"] == {
        "oci_common": "ok",
        "enterprise_ai": "ok",
        "genai": "ok",
        "oracle": "ok",
        "object_storage": "ok",
        "audit_context_salt": "missing",
    }


def test_readiness_production_oci_complete_config_is_ok(monkeypatch: MonkeyPatch) -> None:
    _configure_oci_readiness(
        monkeypatch,
        environment="production",
        audit_context_hash_salt="production-audit-salt",
    )

    resp = client.get("/api/ready")

    assert resp.status_code == 200
    body = resp.json()
    assert body["data"]["status"] == "ok"
    assert body["data"]["checks"] == {
        "oci_common": "ok",
        "enterprise_ai": "ok",
        "genai": "ok",
        "oracle": "ok",
        "object_storage": "ok",
        "audit_context_salt": "ok",
    }
    assert "production-audit-salt" not in str(body)


def test_readiness_oci_missing_oracle_credentials_is_degraded(
    monkeypatch: MonkeyPatch,
) -> None:
    _configure_oci_readiness(
        monkeypatch,
        oracle_password="",
        oracle_client_lib_dir="",
        oracle_wallet_dir="",
    )

    resp = client.get("/api/ready")

    assert resp.status_code == 503
    body = resp.json()
    assert body["data"]["status"] == "degraded"
    assert body["data"]["checks"]["oracle"] == "missing_credentials"


def test_readiness_oci_missing_wallet_dir_is_degraded(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    _configure_oci_readiness(
        monkeypatch,
        oracle_password="",
        oracle_wallet_dir=str(tmp_path / "missing-wallet"),
    )

    resp = client.get("/api/ready")

    assert resp.status_code == 503
    body = resp.json()
    assert body["data"]["status"] == "degraded"
    assert body["data"]["checks"]["oracle"] == "wallet_not_found"


def test_readiness_oci_invalid_embedding_dim_is_degraded(monkeypatch: MonkeyPatch) -> None:
    _configure_oci_readiness(monkeypatch, oci_genai_embedding_dim=1024)

    resp = client.get("/api/ready")

    assert resp.status_code == 503
    body = resp.json()
    assert body["data"]["status"] == "degraded"
    assert body["data"]["checks"]["genai"] == "invalid"


def _configure_oracle_only(monkeypatch: MonkeyPatch, *, password: str = "oracle-password") -> None:
    """DB ステータス API 用に Oracle 接続情報だけ設定する。"""
    settings = get_settings()
    monkeypatch.setattr(settings, "oracle_user", "rag_app")
    monkeypatch.setattr(settings, "oracle_dsn", "adb.example.com/rag")
    monkeypatch.setattr(settings, "oracle_password", password)
    monkeypatch.setattr(settings, "oracle_wallet_dir", "")
    monkeypatch.setattr(settings, "oracle_client_lib_dir", "")


def test_database_status_not_configured_skips_probe(monkeypatch: MonkeyPatch) -> None:
    """接続情報未設定なら実接続を試さず not_configured を返す。"""
    settings = get_settings()
    monkeypatch.setattr(settings, "oracle_user", "")
    monkeypatch.setattr(settings, "oracle_dsn", "")

    async def _must_not_probe(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("未設定時は実接続を試さない")

    monkeypatch.setattr(health_route, "test_oracle_connection", _must_not_probe)

    resp = client.get("/api/ready/database")

    assert resp.status_code == 200
    body = resp.json()
    assert body["data"]["status"] == "not_configured"
    assert body["data"]["check"] == "missing"


def test_database_status_ok_when_probe_succeeds(monkeypatch: MonkeyPatch) -> None:
    """設定済み + 実接続成功なら ok を返す。"""
    _configure_oracle_only(monkeypatch)

    async def _probe_ok(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(health_route, "test_oracle_connection", _probe_ok)

    resp = client.get("/api/ready/database")

    body = resp.json()
    assert body["data"]["status"] == "ok"
    assert body["data"]["check"] == "ok"


def test_database_status_uses_oracle_probe_timeout(monkeypatch: MonkeyPatch) -> None:
    """閲覧 API 用 timeout ではなく Oracle 接続テスト側の timeout に委ねる。"""
    _configure_oracle_only(monkeypatch)
    settings = get_settings()
    monkeypatch.setattr(settings, "db_read_timeout_seconds", 0.001)

    async def _probe_ok(*_args: object, **_kwargs: object) -> None:
        await asyncio.sleep(0.01)

    monkeypatch.setattr(health_route, "test_oracle_connection", _probe_ok)

    resp = client.get("/api/ready/database")

    body = resp.json()
    assert body["data"]["status"] == "ok"


def test_database_status_unreachable_when_probe_fails(monkeypatch: MonkeyPatch) -> None:
    """設定済みでも起動していなければ unreachable を返す。"""
    _configure_oracle_only(monkeypatch)

    async def _probe_fail(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("listener does not currently know of service")

    monkeypatch.setattr(health_route, "test_oracle_connection", _probe_fail)

    resp = client.get("/api/ready/database")

    body = resp.json()
    assert body["data"]["status"] == "unreachable"
    assert body["data"]["check"] == "ok"
    assert body["data"]["detail"]


def test_not_found_uses_api_response_shape() -> None:
    resp = client.get("/api/missing", headers={"X-Request-ID": "not-found-request"})

    assert resp.status_code == 404
    assert resp.headers["x-request-id"] == "not-found-request"
    body = resp.json()
    assert body["data"] is None
    assert body["error_messages"] == ["リソースが見つかりません。"]
    assert body["warning_messages"] == []


def test_method_not_allowed_uses_api_response_shape_and_allow_header() -> None:
    resp = client.post("/api/health", headers={"X-Request-ID": "method-request"})

    assert resp.status_code == 405
    assert resp.headers["x-request-id"] == "method-request"
    assert "GET" in resp.headers["allow"]
    body = resp.json()
    assert body["data"] is None
    assert body["error_messages"] == ["許可されていない HTTP メソッドです。"]


def test_validation_error_preserves_request_id() -> None:
    """検証エラーでもクライアント指定の request id を返す。"""
    resp = client.post(
        "/api/search",
        json={"query": "社内規程", "top_k": 0},
        headers={"X-Request-ID": "validation-request"},
    )

    assert resp.status_code == 422
    assert resp.headers["x-request-id"] == "validation-request"
    assert resp.json()["data"] is None


def test_unhandled_exception_uses_api_response_shape_and_logs_request_id(
    caplog: LogCaptureFixture,
) -> None:
    """未処理例外でも内部詳細を隠し、request id 付きでログに残す。"""
    failing_app = create_app()

    @failing_app.get("/api/fail")
    async def fail() -> None:
        raise RuntimeError("secret boom")

    failing_client = AsgiTestClient(failing_app, raise_app_exceptions=False)

    with caplog.at_level(logging.ERROR, logger="app.main"):
        resp = failing_client.get("/api/fail", headers={"X-Request-ID": "failure-request"})

    assert resp.status_code == 500
    assert resp.headers["x-request-id"] == "failure-request"
    body = resp.json()
    assert body["data"] is None
    assert body["error_messages"] == [UNHANDLED_ERROR_MESSAGE]
    assert "secret boom" not in str(body)

    record = next(item for item in caplog.records if item.message == "unhandled_api_error")
    record_data = record.__dict__
    assert record_data["request_id"] == "failure-request"
    assert record_data["method"] == "GET"
    assert record_data["path"] == "/api/fail"
    assert record_data["exception_type"] == "RuntimeError"


def _configure_oci_readiness(
    monkeypatch: MonkeyPatch,
    *,
    oracle_password: str = "oracle-password",
    oracle_client_lib_dir: str = "",
    oracle_wallet_dir: str = "",
    oci_genai_embedding_dim: int = 1536,
    environment: str = "development",
    audit_context_hash_salt: str = "",
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "environment", environment)
    monkeypatch.setattr(settings, "upload_storage_backend", "oci")
    monkeypatch.setattr(settings, "oci_region", "ap-osaka-1")
    monkeypatch.setattr(settings, "oci_compartment_id", "ocid1.compartment.oc1..example")
    monkeypatch.setattr(settings, "oci_enterprise_ai_endpoint", "https://enterprise-ai.example")
    monkeypatch.setattr(
        settings,
        "oci_enterprise_ai_project_ocid",
        "ocid1.generativeaiproject.oc1..example",
    )
    monkeypatch.setattr(settings, "oci_enterprise_ai_llm_model", "enterprise-llm")
    monkeypatch.setattr(settings, "oci_enterprise_ai_vlm_model", "enterprise-vlm")
    monkeypatch.setattr(
        settings,
        "oci_enterprise_ai_models",
        [
            EnterpriseAiConfiguredModel(model_id="enterprise-llm", display_name="標準 LLM"),
            EnterpriseAiConfiguredModel(
                model_id="enterprise-vlm",
                display_name="Vision LLM",
                vision_enabled=True,
            ),
        ],
    )
    monkeypatch.setattr(settings, "oci_enterprise_ai_default_model", "enterprise-llm")
    monkeypatch.setattr(settings, "oci_enterprise_ai_api_key", "sk-test-secret")
    monkeypatch.setattr(settings, "oci_enterprise_ai_llm_path", "/v1/llm/generate")
    monkeypatch.setattr(settings, "oci_enterprise_ai_vlm_path", "/v1/vlm/extract")
    monkeypatch.setattr(settings, "oci_enterprise_ai_llm_payload_template", "")
    monkeypatch.setattr(settings, "oci_enterprise_ai_vlm_payload_template", "")
    monkeypatch.setattr(settings, "oci_genai_embedding_model", "cohere.embed-v4.0")
    monkeypatch.setattr(settings, "oci_genai_embedding_dim", oci_genai_embedding_dim)
    monkeypatch.setattr(settings, "oci_genai_rerank_model", "cohere.rerank-v4.0-fast")
    monkeypatch.setattr(settings, "oracle_user", "rag_app")
    monkeypatch.setattr(settings, "oracle_password", oracle_password)
    monkeypatch.setattr(settings, "oracle_dsn", "adb.example.com/rag")
    monkeypatch.setattr(settings, "oracle_client_lib_dir", oracle_client_lib_dir)
    monkeypatch.setattr(settings, "oracle_wallet_dir", oracle_wallet_dir)
    monkeypatch.setattr(settings, "object_storage_namespace", "example-namespace")
    monkeypatch.setattr(settings, "object_storage_bucket", "rag-originals")
    monkeypatch.setattr(settings, "audit_context_hash_salt", audit_context_hash_salt)
