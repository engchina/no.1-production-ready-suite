"""サービス設定。共通基底 BaseServiceSettings を継承し、ドメイン設定を足す。"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pr_backend_core.config import (
    BaseServiceSettings,
    platform_env_file,
    product_settings_config,
)
from pr_system_settings.auth.domain import DEFAULT_CSRF_COOKIE_NAME, DEFAULT_SESSION_COOKIE_NAME
from pr_system_settings.model import (
    EnterpriseAiConfiguredModel,
    ModelSecretStateMixin,
    ModelSettingsStore,
)
from pydantic import Field

BACKEND_DIR = Path(__file__).resolve().parents[1]
# 製品固有の設定（`AGENT_*`）は `backend/.env`（画面からは書かない）。
# 3製品共通の設定（`PLATFORM_*`）を置く共通 `.env`（既定は `<repo>/platform/.env`。#211）。
PLATFORM_ENV_FILE = platform_env_file(BACKEND_DIR)


class Settings(ModelSecretStateMixin, BaseServiceSettings):
    """サービス固有設定。

    環境変数名は属性名から決まる（#211）。3製品共通の属性（OCI 認証・アップロード保存先・
    モデル・データベース）は共通 `.env` の `PLATFORM_*`、それ以外は `backend/.env` の `AGENT_*`。
    """

    model_config = product_settings_config(prefix="AGENT_", backend_dir=BACKEND_DIR)

    service_name: str = "production-ready-agent"
    # --- 認証（#215）---
    # RAG / NL2SQL と同じ共通認証（#750）。ユーザー・ロールはどちらも Oracle の PLATFORM_*。
    # local: ログインを省略し、全権限のローカル利用者として使う（開発用）。
    # production: 共通認証（PLATFORM_* のユーザー・ロール）のログインを必須にする。Cookie の
    #   ないリクエストは 401（Runtime からの呼出しは Binding token の /api/mcp/{binding_id}）。
    auth_mode: Literal["local", "production"] = "local"
    # 構成管理者・認証ポリシー・ログインの Cookie 名は共通 `.env` の PLATFORM_ADMIN_* /
    # PLATFORM_AUTH_*（#211）。Cookie は 3 製品で共有し、1 回のログインで全製品を使える（#1565）。
    app_admin_login_user_id: str = ""
    app_admin_login_user_password: str = ""
    app_auth_cookie_secure: bool = False
    app_auth_session_cookie_name: str = DEFAULT_SESSION_COOKIE_NAME
    app_auth_csrf_cookie_name: str = DEFAULT_CSRF_COOKIE_NAME
    app_auth_idle_timeout_minutes: int = 60
    app_auth_absolute_timeout_hours: int = 12
    app_auth_failed_login_limit: int = 5
    app_auth_lockout_minutes: int = 15
    # ログインの試行の回数の制限（ログイン ID と送信元 IP の組・送信元 IP ごと。0 で無効。#1087）。
    app_auth_login_attempt_limit: int = 5
    app_auth_login_ip_attempt_limit: int = 20
    app_auth_login_attempt_window_minutes: int = 15
    app_auth_password_min_length: int = 12
    app_auth_password_max_length: int = 128
    app_auth_argon2_time_cost: int = 3
    app_auth_argon2_memory_kib: int = 65536
    app_auth_argon2_parallelism: int = 4
    # サービス間の token の署名鍵（共通 `.env` の PLATFORM_SERVICE_TOKEN_SECRET。#230 / #233）。
    app_service_token_secret: str = ""
    # システム設定（3製品共通）。
    oci_config_file: str = "~/.oci/config"
    oci_config_profile: str = "DEFAULT"
    oci_compartment_id: str = ""
    oci_region: str = ""
    object_storage_region: str = ""
    object_storage_namespace: str = ""
    object_storage_bucket: str = "production-ready"
    upload_storage_backend: str = "local"
    local_storage_dir: str = "/u01/data/production-ready-agent"
    max_upload_bytes: int = 100 * 1024 * 1024
    model_settings_file: str = "model-settings.json"
    oci_enterprise_ai_endpoint: str = ""
    oci_enterprise_ai_project_ocid: str = ""
    oci_enterprise_ai_api_key: str = ""
    # OCI Enterprise AI のセカンダリ接続（#533）。モデルを呼ぶ接続は
    # enterprise_ai_connection_for_model で引く。
    oci_enterprise_ai_secondary_endpoint: str = ""
    oci_enterprise_ai_secondary_project_ocid: str = ""
    oci_enterprise_ai_secondary_api_key: str = ""
    # ターシャリ接続（#786。OpenAI / OpenAI 互換 API 向け。Project OCID は任意）。
    oci_enterprise_ai_tertiary_endpoint: str = ""
    oci_enterprise_ai_tertiary_project_ocid: str = ""
    oci_enterprise_ai_tertiary_api_key: str = ""
    oci_enterprise_ai_models: list[EnterpriseAiConfiguredModel] = Field(default_factory=list)
    oci_enterprise_ai_default_text_model: str = ""
    oci_enterprise_ai_default_vision_model: str = ""
    oci_enterprise_ai_llm_path: str = "/responses"
    oci_enterprise_ai_vlm_path: str = "/responses"
    oci_enterprise_ai_vlm_input_mode: str = "auto"
    oci_enterprise_ai_llm_payload_template: str = ""
    oci_enterprise_ai_vlm_payload_template: str = ""
    oci_enterprise_ai_llm_response_path: str = ""
    oci_enterprise_ai_vlm_response_path: str = ""
    oci_enterprise_ai_timeout_seconds: float = 600.0
    oci_enterprise_ai_max_retries: int = 3
    # 出力の上限は登録モデルごとの `max_output_tokens`（#1719。platform の
    # `enterprise_ai_output_token_budget`）。共通の LLM / VLM の出力の上限は持たない。
    # LLM / VLM の呼び出しの seed（#1434。共通のモデル設定）。
    # 解釈は pr_system_settings の enterprise_ai_seed。
    oci_enterprise_ai_seed: int = Field(default=42, ge=0, le=2_147_483_647)
    oci_genai_embedding_model: str = "cohere.embed-v4.0"
    oci_genai_embedding_dim: int = 1536
    oci_genai_rerank_model: str = "cohere.rerank-v4.0-fast"
    oracle_dsn: str | None = None
    oracle_user: str | None = None
    oracle_password: str | None = None
    oracle_client_lib_dir: str = "/u01/aipoc/instantclient_23_26"
    oracle_wallet_dir: str | None = None
    oracle_wallet_password: str | None = None
    oracle_adb_ocid: str | None = None
    oracle_adb_region: str | None = None
    oracle_tcp_connect_timeout_seconds: float = 10.0
    oracle_db_test_timeout_seconds: float = 15.0
    # PLATFORM_ORACLE_* の接続 pool の大きさ（Agent 固有。`app.oracle_connection`。#793）。
    oracle_pool_min_connections: int = 1
    oracle_pool_max_connections: int = 8
    # MCP 接続（#757）。RAG / NL2SQL は各製品の MCP（`POST /api/mcp`）で、接続 `rag` / `nl2sql`
    # の URL の初期値になる（例: http://rag-host/api/mcp）。Run の利用者のサービストークン
    # （共通 `.env` の PLATFORM_SERVICE_TOKEN_SECRET で署名）で呼ぶ（#233）。タイムアウト（秒）は
    # 1 回の MCP の呼び出しの待ち時間で、画面の「MCP 接続」の上限（900 秒）以下にする。
    agent_external_rag_mcp_url: str | None = None
    # RAG の `rag_search` は RAG の回答生成（`RAG_ANSWER_TIMEOUT_SECONDS`。既定 600 秒）を通しで待つ
    # ため、RAG の回答の上限より長くする（RAG の画面の timeout と同じ 630 秒。#1425）。
    agent_external_rag_timeout_seconds: float = 630.0
    # 画面（ブラウザ）から RAG を開く起点（例: http://rag-host）。RAG の図の根拠を開く短命の URL
    # （#1311）の path をこの起点に付ける。空なら RAG が MCP の呼び出しを受けた起点（MCP の URL の
    # host。private IP のことがある）のまま使う。1 台の Compute（#1316）では同じ origin の `/rag`。
    agent_external_rag_public_url: str | None = None
    agent_external_nl2sql_mcp_url: str | None = None
    # NL2SQL の `nl2sql_query` / `nl2sql_get_job` は SQL の生成・実行をジョブにし、1 回の
    # 呼び出しでは結果を最大 45 秒（`wait_seconds`）だけ待って、終わらなければ job_id を返す。
    # `nl2sql_query` は SQL を作る前に AI要件確認（OCI Enterprise AI の LLM。下書きと回答の後の
    # 再検査で最大 2 回。#1441）を呼び、その分だけジョブを待つ秒数を減らす。待ちの上限 45 秒と
    # AI要件確認の 180 秒に、HTTP の往復・ジョブの登録・DB の負荷の余裕 75 秒を足した 300 秒にする
    # （#1432 の 120 秒に AI要件確認の分を足した）。続きの取得（`nl2sql_get_job`）は 1 回 20 秒
    # （`tools._JOB_POLL_WAIT_SECONDS`）で、ジョブの完了は `agent_nl2sql_job_wait_seconds` まで
    # 続きを取って待つ（#848 / #1430）。
    agent_external_nl2sql_timeout_seconds: float = 300.0
    # NL2SQL のジョブが pending / running のまま返ったとき、ツールの中で完了を待つ合計の上限
    # （秒。#848）。既定の SQL の生成（Select AI の 1 回）と実行は、それぞれ NL2SQL の DB の
    # 呼び出しの上限（`NL2SQL_ORACLE_CALL_TIMEOUT_SECONDS`、既定 120 秒）で打ち切られるため、
    # 300 秒で足りる。
    # nl2sql_get_job を wait_seconds 付きで繰り返し呼ぶ。0 なら待たずにそのまま返す。
    agent_nl2sql_job_wait_seconds: float = 300.0
    # Run の利用者がいない呼び出し（画面からのツール一覧の取得など、利用者のいない経路）で使う
    # 共通認証のログインユーザー ID。空ならその呼び出しは失敗する。
    agent_mcp_service_user_login_id: str = ""
    # MCP の 1 メッセージの再試行の回数（#854）。送信前の失敗（接続できない・接続の timeout）と
    # 429 / 503 は全メッセージ、502 / 504 は手順（initialize・tools/list）と読み取り専用の
    # ツールだけ。待ちは指数 backoff + jitter（Retry-After に従う）で、呼び出し全体は接続の
    # timeout に収める。
    agent_external_mcp_max_retries: int = 3
    # MCP 接続の宣言。JSON list か {"servers": [...]}。各項目は server_id/id 必須、
    # base_url・auth_mode・資格情報・timeout_seconds を任意で持つ。
    agent_external_mcp_servers_json: str | None = None
    # Skill 外部定義: 中立ディレクトリ(skills/<id>/SKILL.md)と JSON 宣言。
    agent_skills_dir: str | None = None
    agent_skills_definitions_json: str | None = None
    # Plugin 宣言: install 済み plugin manifest と marketplace ソース。
    agent_plugins_json: str | None = None
    agent_plugin_marketplaces_json: str | None = None
    # 組み込み Runtime の実行（#754）。in_process は API のプロセスで実行し、
    # それ以外は runtime_dispatcher（別プロセス）が claim して実行する。
    agent_runtime_dispatch_mode: str = "in_process"
    agent_runtime_dispatch_poll_seconds: float = 1.0
    agent_runtime_dispatch_lease_seconds: int = 120
    agent_permission_default_mode: str = "approval"
    # Run・業務 Agent と画面で変えた定義の保存先（auto / memory / file / oracle_checkpoint /
    # oracle_normalized）。Oracle は共通の PLATFORM_ORACLE_* で接続し、テーブルはシステムテーブルが
    # 作る（#764）。oracle_normalized は監査用の projection（AGENT_RUNTIME_RUNS など）も書く。
    # auto（既定。#839）は DB の設定がそろっていれば oracle_checkpoint、無ければ memory
    # （起動時に判定）。DB に接続できないあいだも起動し、接続できた時点で読み込む（#1212）。
    agent_runtime_repository_backend: str = "auto"
    agent_runtime_snapshot_path: str | None = None
    agent_runtime_projection_retention_days: int = 0
    agent_runtime_projection_write_mode: str = "replace"
    agent_max_tool_calls_per_run: int = 20
    # 支援タスクの予算（#1243）。超える呼び出しは実行せず、ツールの結果（budget_exceeded）でモデルに
    # 知らせる（Run は失敗にしない）。0 以下は上限なし。
    # Run ごとの RAG の呼び出し（rag_search・rag_retrieve_evidence。1 回が重い検索と回答の生成）。
    # 既定 6 は 5 段の多段の質問（段ごとに 1 回）と 1 回の言い換え（#1345）。
    # 本文を読むツール（rag_read_source・rag_outline・rag_read_document・rag_read_section）は
    # 数えない（ツールの呼び出しの上限には数える）。
    agent_max_rag_calls_per_run: int = 6
    # 同じ会話（thread）の通しのツールの呼び出し。前の Run の支援タスクの状態に積む。
    agent_max_tool_calls_per_task: int = 60
    # 品質評価の 1 ケースの上限時間（秒。Run の実行。判定は別。#1432）。0 以下（既定）は自動で、
    # 接続 `rag` の待ち時間 ×（Run の RAG の予算〔agent_max_rag_calls_per_run。0 以下なら
    # agent_max_tool_calls_per_run〕+ 1）にする。+1 は回答の最終の検証（rag_validate_answer）と
    # モデルの推論の分。既定では 630 ×（6 + 1）= 4410 秒。300 秒〜86400 秒に収める。
    agent_evaluation_case_timeout_seconds: float = 0.0
    # 回答の最終の検証（#1246。#1277 で既定 on）。on のとき、RAG の根拠を使った Run の回答を、
    # 保存する前に Control Plane が根拠を返した RAG の接続ごとに MCP rag_validate_answer で検証する
    # （RAG がモデルを 1 回呼ぶため、接続ごとに 10〜20 秒増える）。根拠で確かめられない段落は回答に
    # 載せず「確かめられていない点」に示す。予算（上の 2 つ）には数えない。
    agent_final_validation_enabled: bool = True
    agent_metrics_enabled: bool = True
    agent_trace_events_enabled: bool = True
    agent_trace_events_buffer_size: int = 500
    agent_trace_events_retention_seconds: int = 86_400
    agent_trace_exporter_url: str | None = None
    agent_trace_exporter_api_key: str | None = None
    agent_trace_exporter_timeout_seconds: float = 2.0
    agent_trace_exporter_retry_queue_size: int = 100
    agent_trace_exporter_retry_max_attempts: int = 3
    agent_trace_exporter_retry_base_delay_seconds: float = 1.0
    agent_trace_exporter_retry_max_delay_seconds: float = 60.0
    agent_trace_exporter_retry_worker_enabled: bool = True
    agent_trace_exporter_retry_worker_interval_seconds: float = 5.0
    agent_trace_exporter_retry_worker_batch_size: int = 100
    agent_trace_sample_rate: float = 1.0
    agent_langfuse_host: str | None = None
    agent_langfuse_public_key: str | None = None
    agent_langfuse_secret_key: str | None = None
    agent_opentelemetry_endpoint: str | None = None
    agent_artifact_storage_backend: str = "inline"
    agent_artifact_storage_path: str = ".agent-artifacts"

    @property
    def local_debug_enabled(self) -> bool:
        """local mode: 画面は全権限のローカル利用者（DB セッションを作らない）。"""
        return self.auth_mode == "local"

    @property
    def app_auth_enabled(self) -> bool:
        """production mode: 画面は共通認証のログインを必須にする。"""
        return self.auth_mode == "production"

    @property
    def oracle_driver_mode(self) -> str:
        """Agent は python-oracledb の Thin mode だけで接続する（Wallet の判定用）。"""
        return "thin"

    @property
    def oracle_connection_security(self) -> str:
        """Agent は Wallet mTLS だけに対応する。"""
        return "wallet_mtls"

    @property
    def resolved_oracle_adb_region(self) -> str:
        """ADB 管理用の region。未設定なら PLATFORM_OCI_REGION を使う。"""
        return (self.oracle_adb_region or self.oci_region or "").strip()

    @property
    def resolved_oracle_wallet_dir(self) -> str:
        """参照実装と同じく PLATFORM_ORACLE_CLIENT_LIB_DIR/network/admin を Wallet 配置先にする。"""
        client_lib_dir = self.oracle_client_lib_dir.strip()
        if client_lib_dir:
            return str(Path(client_lib_dir).expanduser() / "network" / "admin")
        return (self.oracle_wallet_dir or "").strip()


def resolve_model_settings_file(path_value: str) -> Path:
    """PLATFORM_MODEL_SETTINGS_FILE を共通 `.env` と同じディレクトリ基準で解決する。"""
    path = Path(path_value.strip() or "model-settings.json").expanduser()
    return path if path.is_absolute() else (PLATFORM_ENV_FILE.parent / path).resolve()


# モデル設定の読み書きは3製品共通（platform の pr_system_settings。#103）。
# model-settings.json と API key は3製品で共有する（共通 `.env`。#211）。
# テストで PLATFORM_ENV_FILE を差し替えられるよう、呼出時に module の値を参照する。
MODEL_SETTINGS_STORE = ModelSettingsStore(
    resolve_path=lambda settings: resolve_model_settings_file(settings.model_settings_file),
    env_file=lambda _settings: PLATFORM_ENV_FILE,
)


@lru_cache
def _settings_singleton() -> Settings:
    settings = Settings()
    MODEL_SETTINGS_STORE.load(settings)
    return settings


def get_settings() -> Settings:
    """設定シングルトン。保存済みのモデル設定が更新されていれば再読込する。"""
    settings = _settings_singleton()
    MODEL_SETTINGS_STORE.reload_if_changed(settings)
    return settings


def reset_settings_cache() -> None:
    """テストや明示的な再初期化のため Settings singleton を破棄する。"""
    _settings_singleton.cache_clear()
    MODEL_SETTINGS_STORE.reset()
