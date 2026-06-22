"""pytest 共通 fixture。"""

from collections.abc import Iterator
from pathlib import Path

import pytest

from app.clients.oracle import reset_local_store
from app.config import (
    DEFAULT_MODEL_SETTINGS_FILE,
    Settings,
    get_settings,
    load_persisted_model_settings,
)
from app.rag.rate_limit import reset_rate_limiter
from app.rag.request_context import (
    AuditRequestContext,
    audit_request_context_from_headers,
    current_audit_request_context,
    reset_audit_request_context,
    set_audit_request_context,
)
from tests import _ai_stubs, _oracle_test_db
from tests.support import TEST_REQUEST_HEADERS


@pytest.fixture(scope="session", autouse=True)
def _oracle_db_session() -> None:
    """実 Oracle が使えるならスキーマを保証し baseline を記録する。"""
    if not _oracle_test_db.db_available():
        return
    _oracle_test_db.apply_real_oracle_settings(get_settings())
    _oracle_test_db.ensure_schema()
    _oracle_test_db.capture_baseline()


@pytest.fixture(autouse=True)
def isolated_local_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """各テストで保存先とテスト補助 store を分離する。"""
    monkeypatch.setenv("MODEL_SETTINGS_FILE", str(tmp_path / "model-settings.json"))
    reset_local_store()
    reset_rate_limiter()
    _reset_runtime_settings(get_settings(), tmp_path)


@pytest.fixture
def oracle_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """実 Oracle 26ai を使う統合テスト用。未到達なら skip し、作成行を後始末する。

    Oracle は実 DB を使うが、VLM/embedding/rerank/LLM は決定論スタブへ差し替える。
    `isolated_local_state` が Oracle 接続設定を初期化した後に実値を再適用するため、
    autouse より後に動く本 fixture で上書きしている。
    """
    if not _oracle_test_db.db_available():
        pytest.skip("実 Oracle 26ai に未到達のため統合テストをスキップします。")
    settings = get_settings()
    _oracle_test_db.apply_real_oracle_settings(settings)
    settings.model_settings_file = DEFAULT_MODEL_SETTINGS_FILE
    load_persisted_model_settings(settings)
    _ai_stubs.patch_ai_clients(monkeypatch)
    context = audit_request_context_from_headers(
        TEST_REQUEST_HEADERS,
        request_id="pytest-oracle-integration",
        settings=settings,
    )

    def current_or_default_context() -> AuditRequestContext:
        current = current_audit_request_context()
        if (
            current.request_id is None
            and current.tenant_id_hash is None
            and current.user_id_hash is None
            and current.allowed_document_ids is None
            and current.allowed_category_names is None
            and current.allowed_knowledge_base_ids is None
        ):
            return context
        return current

    monkeypatch.setattr(
        "app.clients.oracle.current_audit_request_context",
        current_or_default_context,
    )
    monkeypatch.setattr(
        "app.rag.audit.current_audit_request_context",
        current_or_default_context,
    )
    token = set_audit_request_context(context)
    _oracle_test_db.cleanup_to_baseline()
    try:
        yield
    finally:
        _oracle_test_db.cleanup_to_baseline()
        reset_audit_request_context(token)


def _reset_runtime_settings(settings: Settings, tmp_path: Path) -> None:
    """mutable runtime settings をテスト既定値へ戻す。"""
    settings.upload_storage_backend = "local"
    settings.object_storage_namespace = ""
    settings.object_storage_bucket = ""
    settings.oracle_client_lib_dir = str(tmp_path / "instantclient_23_26")
    settings.oracle_wallet_dir = ""
    settings.oracle_adb_ocid = ""
    settings.local_storage_dir = str(tmp_path / "storage")
    settings.max_upload_bytes = 200 * 1024 * 1024
    settings.rate_limit_enabled = True
    settings.auth_mode = "local"
    settings.auth_username = ""
    settings.auth_password = ""
    settings.auth_session_secret = ""
    settings.auth_session_timeout_seconds = 24 * 60 * 60
    settings.auth_cookie_secure = False
    settings.model_settings_file = str(tmp_path / "model-settings.json")
    settings.oci_enterprise_ai_endpoint = ""
    settings.oci_enterprise_ai_project_ocid = ""
    settings.oci_enterprise_ai_api_key = ""
    settings.oci_enterprise_ai_models = []
    settings.oci_enterprise_ai_default_model = ""
    settings.oci_enterprise_ai_llm_model = ""
    settings.oci_enterprise_ai_vlm_model = ""
    settings.oci_enterprise_ai_llm_path = "/responses"
    settings.oci_enterprise_ai_vlm_path = "/responses"
    settings.oci_enterprise_ai_llm_payload_template = ""
    settings.oci_enterprise_ai_vlm_payload_template = ""
    settings.oci_enterprise_ai_llm_response_path = ""
    settings.oci_enterprise_ai_vlm_response_path = ""
    settings.oci_enterprise_ai_timeout_seconds = 600.0
    settings.oci_enterprise_ai_max_retries = 3
    settings.oci_enterprise_ai_llm_max_output_tokens = 1200
    settings.oci_enterprise_ai_vlm_max_output_tokens = 65536
    settings.oci_genai_embedding_model = "cohere.embed-v4.0"
    settings.oci_genai_embedding_dim = 1536
    settings.oci_genai_rerank_model = "cohere.rerank-v4.0-fast"
    settings.rag_agentic_profile = "off"
    settings.rag_agentic_max_subqueries = 3
    settings.rag_pdf_segmentation_enabled = True
    settings.rag_pdf_max_pages_per_segment = 10
    settings.rag_pdf_max_segments = 300
