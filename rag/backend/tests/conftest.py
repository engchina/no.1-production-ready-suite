"""pytest 共通 fixture。

アプリ（`app`）と、アプリを import するテスト補助（`tests._ai_stubs` など）は、fixture の中で
import する。conftest は xdist（`-n`）の controller も読み込むが、controller はテストを収集・
実行しない。module の先頭で import すると、worker の起動の前に約 2 秒の import を直列に待つ
（#401）。
"""

from __future__ import annotations

import atexit
import fcntl
import gc
import warnings
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from app.config import Settings
    from app.rag.request_context import AuditRequestContext

# プロセスの終了時（pytest の後始末と、ほかの atexit の処理の後）に、残っているオブジェクトを GC の
# 対象から外す。Python の終了処理の GC が、アプリとテストで作った大量のオブジェクトをたどって
# 1 プロセス 1〜2 秒かかり、xdist の controller は全 worker の終了をこれだけ待っていた（#401）。
# 循環参照のごみの `__del__` が終了時に呼ばれなくなるだけで、テストの結果には影響しない。
atexit.register(gc.freeze)


@pytest.fixture(scope="session", autouse=True)
def _shared_httpx_default_tls_context() -> Iterator[None]:
    """httpx の既定の TLS 設定（CA 証明書の読み込み）を worker ごとに 1 回だけ作り、使い回す。

    `httpx.Client()` は作るたびに certifi の CA 証明書を読み込む（約 20ms）。検索・取込のステージ
    （chunking・retrieval 等）の remote 委譲は既定で ON（`http://127.0.0.1:1803x`）で、
    `Settings.model_construct(...)` などで作ったテストの設定では、パイプラインを 1 回流すごとに
    数回 `httpx.Client()` を作り、接続を拒否されて in-process へ縮退する。この読み込みだけで
    全体の約 6 秒を占めていた（#401）。既定（`verify=True`・`cert=None`・`trust_env=True`、
    `SSL_CERT_FILE` / `SSL_CERT_DIR` なし）のときだけ同じ設定を返し、それ以外は httpx に任せる。
    """
    import os
    import ssl

    import httpx
    from httpx._transports import default as httpx_transports

    original = httpx_transports.create_ssl_context  # type: ignore[attr-defined]
    shared: list[ssl.SSLContext] = []

    def create_ssl_context(
        verify: ssl.SSLContext | str | bool = True,
        cert: object = None,
        trust_env: bool = True,
    ) -> ssl.SSLContext:
        uses_default = verify is True and cert is None and trust_env
        if not uses_default or os.environ.get("SSL_CERT_FILE") or os.environ.get("SSL_CERT_DIR"):
            return original(verify=verify, cert=cert, trust_env=trust_env)  # type: ignore[arg-type]
        if not shared:
            shared.append(httpx.create_ssl_context())
        return shared[0]

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(httpx_transports, "create_ssl_context", create_ssl_context)
        yield


# xdist の worker が nodeid に group 名を付ける hook より先に marker を付ける。
@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """実 Oracle を使うテスト（`oracle_db`）は、xdist でも 1 つの worker で直列に実行する（#344）。

    `cleanup_to_baseline` は baseline にない行をすべて消すため、別の worker の実 Oracle の
    テストと同時に動くと互いの行を消す。`--dist loadgroup`（pyproject の addopts）で効く。
    """
    for item in items:
        if "oracle_db" in getattr(item, "fixturenames", ()):
            item.add_marker(pytest.mark.xdist_group("oracle"))


@pytest.fixture(scope="session", autouse=True)
def _hermetic_settings(tmp_path_factory: pytest.TempPathFactory) -> None:
    """アプリの設定に、開発者の手元の `.env`（共通・RAG）を読ませない（#483）。

    手元の `.env` の値（ファイル準備の有効化など）が入ると、CI と違う経路を通って
    テストが失敗する。実 Oracle の接続先は `tests._oracle_test_db` が実際の `.env` から
    別に読み、接続の項目だけを入れる。
    """
    from app import config as app_config
    from tests import _oracle_test_db  # noqa: F401 - 実際の `.env` の接続先を先に読む

    empty = tmp_path_factory.mktemp("hermetic-env")
    app_config.PLATFORM_ENV_FILE = empty / "platform.env"
    app_config.BACKEND_ENV_FILE = empty / ".env"
    # テストが直接作る `Settings()` も、手元の `.env` を読まない。
    app_config.Settings.model_config["env_file"] = (
        app_config.PLATFORM_ENV_FILE,
        app_config.BACKEND_ENV_FILE,
    )
    app_config.reset_settings_cache()


@pytest.fixture(scope="session", autouse=True)
def _oracle_db_session(_hermetic_settings: None, tmp_path_factory: pytest.TempPathFactory) -> None:
    """実 Oracle が使えるならスキーマを保証し baseline を記録する。"""
    from app.config import get_settings
    from tests import _oracle_test_db

    if not _oracle_test_db.db_available():
        return
    _oracle_test_db.apply_real_oracle_settings(get_settings())
    # xdist の worker は同時に起動するため、スキーマの作成（DDL）は 1 つずつにする（#344）。
    lock_path = tmp_path_factory.getbasetemp().parent / "rag-oracle-schema.lock"
    with lock_path.open("w") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        skip_reason = _oracle_test_db.ensure_schema()
    if skip_reason is not None:
        # データを消す未適用の migration は当てない。実 Oracle のテストは `oracle_db` で
        # skip する（#619）。
        warnings.warn(skip_reason, stacklevel=1)
        return
    _oracle_test_db.capture_baseline()


@pytest.fixture(autouse=True)
def isolated_local_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """各テストで保存先と runtime の状態を分離する。"""
    from app import config as app_config
    from app.api.routes import settings as settings_routes
    from app.config import get_settings
    from app.rag.guardrail_adapter import reset_guardrail_static_cache
    from app.rag.rate_limit import reset_rate_limiter
    from app.rag.vector_index_adapter import reset_vector_index_static_cache
    from app.security.service import set_security_service
    from app.services import control as service_control

    monkeypatch.setenv("PLATFORM_MODEL_SETTINGS_FILE", str(tmp_path / "model-settings.json"))
    # 共通 .env（モデルの API key・OCI / DB / 保存先の画面保存先）と RAG の backend/.env
    # （parser の API key・RAG 固有の画面保存先）は tmp へ分離する（#211）。
    monkeypatch.setattr(app_config, "PLATFORM_ENV_FILE", tmp_path / "platform.env")
    monkeypatch.setattr(app_config, "BACKEND_ENV_FILE", tmp_path / ".env")
    monkeypatch.setattr(settings_routes, "BACKEND_ENV_FILE", tmp_path / ".env")
    monkeypatch.delenv("PLATFORM_OCI_ENTERPRISE_AI_API_KEY", raising=False)
    monkeypatch.delenv("PLATFORM_OCI_ENTERPRISE_AI_SECONDARY_API_KEY", raising=False)
    set_security_service(None)
    reset_rate_limiter()
    reset_guardrail_static_cache()
    reset_vector_index_static_cache()
    _reset_runtime_settings(get_settings(), tmp_path)
    # サービス管理の systemctl / journalctl / sudo は実行しない(手元の systemd を触らない。#286)。
    # 既定は「systemd を使えない」扱いにし、必要なテストだけ fake を差し込む。
    monkeypatch.setattr(service_control, "run_command", _systemd_unavailable)
    monkeypatch.setattr(
        get_settings(), "rag_service_runtime_env_file", str(tmp_path / "service-runtime.env")
    )


async def _systemd_unavailable(argv: list[str], timeout: float) -> object:
    from app.services.systemd import CommandUnavailableError

    raise CommandUnavailableError(f"{argv[0]} はテストでは実行しません。")


@pytest.fixture
def oracle_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """実 Oracle AI Database を使う統合テスト用。未到達なら skip し、作成行を後始末する。

    Oracle は実 DB を使うが、VLM/embedding/rerank/LLM は決定論スタブへ差し替える。
    `isolated_local_state` が Oracle 接続設定を初期化した後に実値を再適用するため、
    autouse より後に動く本 fixture で上書きしている。
    """
    from app.config import get_settings
    from app.rag.request_context import (
        audit_request_context_from_headers,
        current_audit_request_context,
        reset_audit_request_context,
        set_audit_request_context,
    )
    from tests import _ai_stubs, _oracle_test_db
    from tests.support import TEST_REQUEST_HEADERS

    if not _oracle_test_db.db_available():
        pytest.skip("実 Oracle AI Database に未到達のため統合テストをスキップします。")
    skip_reason = _oracle_test_db.schema_skip_reason()
    if skip_reason is not None:
        pytest.skip(skip_reason)
    settings = get_settings()
    # 実 Oracle の接続の項目だけを入れる。手元の model-settings.json（文書解析の既定など）は読まない
    # （テストの既定値を上書きして CI と違う経路を通るため。AI は決定論スタブに差し替える。#483）。
    _oracle_test_db.apply_real_oracle_settings(settings)
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
    settings.oracle_adb_region = ""
    settings.local_storage_dir = str(tmp_path / "storage")
    settings.max_upload_bytes = 200 * 1024 * 1024
    settings.rate_limit_enabled = True
    settings.auth_mode = "local"
    settings.app_admin_login_user_id = ""
    settings.app_admin_login_user_password = ""
    settings.app_auth_cookie_secure = False
    settings.model_settings_file = str(tmp_path / "model-settings.json")
    settings.oci_enterprise_ai_endpoint = ""
    settings.oci_enterprise_ai_project_ocid = ""
    settings.oci_enterprise_ai_secondary_endpoint = ""
    settings.oci_enterprise_ai_secondary_project_ocid = ""
    settings.oci_enterprise_ai_tertiary_endpoint = ""
    settings.oci_enterprise_ai_tertiary_project_ocid = ""
    # プライマリ接続・セカンダリ接続・ターシャリ接続（#533 / #786）の API key の基準値を空にする。
    settings.prepare_model_secret_state("", "", "")
    settings.oci_enterprise_ai_models = []
    settings.oci_enterprise_ai_default_text_model = ""
    settings.oci_enterprise_ai_default_vision_model = ""
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
    settings.oci_enterprise_ai_vlm_input_mode = "files_api"
    settings.rag_parser_adapter_backend = "unstructured"
    settings.rag_parser_docling_enabled = False
    settings.rag_parser_unstructured_enabled = True
    settings.rag_parser_mineru_enabled = False
    settings.rag_parser_dots_ocr_enabled = False
    settings.oci_genai_embedding_model = "cohere.embed-v4.0"
    settings.oci_genai_embedding_dim = 1536
    settings.oci_genai_rerank_model = "cohere.rerank-v4.0-fast"
    settings.rag_chunking_service_enabled = False
    settings.rag_vector_index_service_enabled = False
    settings.rag_graph_service_enabled = False
    settings.rag_guardrail_service_enabled = False
    settings.rag_evaluation_service_enabled = False
    settings.rag_pdf_segmentation_enabled = True
    settings.rag_pdf_max_pages_per_segment = 10
    settings.rag_pdf_max_segments = 300
    # 本番既定は False(PREPROCESSED で停止)。既存テストは preprocess を越えて
    # REVIEW まで進む前提なのでテスト既定は自動進行とし、停止挙動は専用テストで検証する。
    settings.rag_auto_parse_after_preprocess_enabled = True


@pytest.fixture(autouse=True)
def _fresh_database_status_cache() -> Iterator[None]:
    """DB の状態 API の `ok` の cache（#793）をテストごとに捨てる（テストの順序に依らない）。"""
    from pr_system_settings.database_status import clear_database_status_cache

    clear_database_status_cache()
    yield
    clear_database_status_cache()
