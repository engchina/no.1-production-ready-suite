"""アプリケーション設定。

環境変数 → 3製品共通の `platform/.env`（`PLATFORM_*`）→ RAG の `backend/.env`（`RAG_*`）から
読み込む（#211）。シークレットはコードにハードコードしない。
"""

import contextlib
import logging
import os
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, Self

from pr_backend_core.chat_history import (
    CHAT_HISTORY_DEFAULT_MAX_CHARS,
    CHAT_HISTORY_DEFAULT_MAX_CHARS_PER_TURN,
    CHAT_HISTORY_DEFAULT_MAX_TURNS,
    CHAT_HISTORY_MAX_CHARS_LIMIT,
    CHAT_HISTORY_MAX_CHARS_PER_TURN_LIMIT,
    CHAT_HISTORY_MAX_TURNS_LIMIT,
    CHAT_HISTORY_MIN_CHARS,
    CHAT_HISTORY_MIN_CHARS_PER_TURN,
)
from pr_backend_core.config import (
    PlatformEnvSourcesMixin,
    platform_env_file,
    product_env_file,
    product_settings_config,
    settings_env_name,
)
from pr_system_settings.auth.domain import DEFAULT_CSRF_COOKIE_NAME, DEFAULT_SESSION_COOKIE_NAME
from pr_system_settings.model import EnterpriseAiConfiguredModel as EnterpriseAiConfiguredModel
from pr_system_settings.model import EnterpriseAiConnection as EnterpriseAiConnection
from pr_system_settings.model import ModelSecretStateMixin, ModelSettingsStore
from pr_system_settings.model import (
    enterprise_ai_connection_for_model as enterprise_ai_connection_for_model,
)
from pr_system_settings.model import (
    enterprise_ai_default_model_id as enterprise_ai_default_model_id,
)
from pr_system_settings.model import (
    enterprise_ai_max_input_tokens as enterprise_ai_max_input_tokens,
)
from pr_system_settings.model import enterprise_ai_model_catalog as enterprise_ai_model_catalog
from pr_system_settings.model import (
    enterprise_ai_output_token_budget as enterprise_ai_output_token_budget,
)
from pr_system_settings.model import (
    enterprise_ai_reasoning_effort_to_send as enterprise_ai_reasoning_effort_to_send,
)
from pr_system_settings.model import enterprise_ai_seed as enterprise_ai_seed
from pr_system_settings.model import enterprise_ai_vision_model_id as enterprise_ai_vision_model_id
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource
from rag_parser_core.sheet_records import ExcelOptions
from rag_pipeline_core.chunking import (
    CHUNK_OVERLAP_MAX_CHARS as CHUNK_OVERLAP_MAX_CHARS,
)
from rag_pipeline_core.chunking import (
    CHUNK_SIZE_MAX_CHARS as CHUNK_SIZE_MAX_CHARS,
)
from rag_pipeline_core.chunking import (
    CHUNK_SIZE_MIN_CHARS as CHUNK_SIZE_MIN_CHARS,
)
from rag_pipeline_core.evaluation import LEGACY_EVALUATION_SUITES

from app.parser_settings_store import ParserSettingsStore
from app.rag.language_pack_store import LANGUAGE_PACK_STORE

AuthMode = Literal["local", "production"]
UploadStorageBackend = Literal["local", "oci"]
AuditPersistence = Literal["log", "oracle", "both"]
ParserAdapterBackend = Literal[
    # 廃止済みの in-process baseline。validator は local を正規化せず保持する(advanced
    # diagnostics の scorecard/staging golden gate が「常時利用可能な baseline」概念として
    # 参照するため)。runtime では ingestion._partition_source が local を既定エンジン
    # (DEFAULT_PARSER_ADAPTER_BACKEND = docling)のサービスへマップし、in-process 解析は実行しない。
    "local",
    "docling",
    "unstructured",
    # XML ツリー JSON(tag / attr / children の木の JSON)を要素の階層のまま解析する(#2024)。
    # Docling・Unstructured と同じく、使用エンジンで選んだときだけ使う(#2042)。
    "xml_tree_json",
    "mineru",
    "dots_ocr",
    # service 系 backend。oci_document_understanding は OCI Document Understanding の
    # 非同期 processor job で OCR/表抽出する(parser-oci-document-understanding へ委譲する)。
    "oci_document_understanding",
]
# 削除した文書解析エンジン(#270: Marker / Unlimited-OCR / GLM-OCR、#2042: OCI Generative AI
# (Vision))。保存済みの設定(parser-settings.json・文書レシピ・KB 構築設定・取込ジョブ)に
# 残っていても取込・画面を壊さないよう、読み込み時に既定エンジンへ寄せる(旧 ``auto`` と同じ扱い)。
# 旧値は再保存時に消えるため、既存データを書き換える migration は持たない。
REMOVED_PARSER_ADAPTER_BACKENDS = frozenset(
    {"marker", "unlimited_ocr", "glm_ocr", "oci_genai_vision", "enterprise_ai_vlm"}
)
# 既定の文書解析エンジン(#286: Unstructured から Docling へ変更)。Unstructured は明示選択で使える。
DEFAULT_PARSER_ADAPTER_BACKEND: Literal["docling"] = "docling"


def normalize_parser_adapter_backend_value(value: object) -> object:
    """旧値・削除済みエンジンを既定エンジンへ正規化する(未知値はそのまま検証へ回す)。

    - ``auto``(旧既定)と削除済みエンジン → 既定 ``docling``(DEFAULT_PARSER_ADAPTER_BACKEND)。
    - ``local_partition``(結果タグ別名)→ baseline 値 ``local``。
    - ``local`` は正規化しない(advanced diagnostics が常時利用可能な baseline として扱う)。
    """
    normalized = str(value).strip().casefold()
    if normalized == "auto" or normalized in REMOVED_PARSER_ADAPTER_BACKENDS:
        return DEFAULT_PARSER_ADAPTER_BACKEND
    if normalized == "local_partition":
        return "local"
    return value


PreprocessProfile = Literal[
    "passthrough",
    "office_to_pdf",
    "pdf_to_page_images",
    "csv_to_json",
    "excel_to_json",
    "pii_redact",
]
# 削除したファイル準備の変換方式(text_normalize、#2042: スキャン画像の補正・URL→Markdown)。
# 保存済みの設定(backend/.env・文書レシピ・KB 構築設定・取込ジョブ)に残っていても起動・取込を
# 壊さないよう、読み込み時に「変換なし」(passthrough)へ寄せる。旧値は再保存時に消える。
REMOVED_PREPROCESS_PROFILES = frozenset({"text_normalize", "url_to_markdown", "image_enhance"})


def normalize_removed_preprocess_profile(value: object) -> object:
    """削除した変換方式を「変換なし」へ寄せる(未知値はそのまま検証へ回す)。"""
    if isinstance(value, str) and value.strip().casefold() in REMOVED_PREPROCESS_PROFILES:
        logging.getLogger(__name__).warning(
            "removed_preprocess_profile_normalized",
            extra={"preprocess_profile": value.strip().casefold()},
        )
        return "passthrough"
    return value


ChunkingStrategy = Literal[
    "structure_aware",
    "recursive_character",
    "small_to_big",
    "markdown_heading",
    "page_level",
    "fixed_size",
    "fixed_delimiter",
]
CHUNKING_STRATEGIES_WITH_MIN_CHARS: set[ChunkingStrategy] = {
    "structure_aware",
    "recursive_character",
    "markdown_heading",
    "page_level",
}
# 削除した分割方式の保存値を後継の方式へ読み替える(.env / 文書レシピ / KB の保存値)。
# 親子階層(hierarchical_parent_child)は親子階層（small-to-big）へ置き換えた(#271)。
LEGACY_CHUNKING_STRATEGY_ALIASES: dict[str, ChunkingStrategy] = {
    "sentence_window": "recursive_character",
    "hierarchical_parent_child": "small_to_big",
}


def normalize_legacy_chunking_strategy_value(value: object) -> object:
    """削除した分割方式の保存値を後継へ読み替える(pydantic の before validator 用)。"""
    if isinstance(value, str):
        return LEGACY_CHUNKING_STRATEGY_ALIASES.get(value.strip().casefold(), value)
    return value


# 親子階層（small-to-big。`small_to_big`）の分割パラメータ。既定値と範囲は rag_poc の
# rag_engine.chunking.constants(DEFAULT_* / *_RANGE)と同じ(テストで一致を確認する)。
# rag_engine.chunking は import すると分割実装一式を読み込むため、ここでは値を複製して持つ。
# OCI Enterprise AI の LLM 1 回の timeout（`oci_enterprise_ai_timeout_seconds`）の上限（秒）。
# 保存済みの回答の評価の時間の上限もこの値から決める（#304）。
OCI_ENTERPRISE_AI_TIMEOUT_MAX_SECONDS = 600.0
# 品質評価の job の全体の時間の上限の最大（秒。1 日）。job の上限は、回答の回数 × 回答の上限を
# この値で抑える（#1430。`app.rag.evaluation_jobs.evaluation_job_time_limit_seconds`）。
RAG_EVALUATION_JOB_TIMEOUT_MAX_SECONDS = 86400
# 取込 job の全体の上限（秒。#2003）。既定は 1 文書 1,000 図を 1 件 60 秒で順に読んでも足りる
# 24 時間、設定できる上限は 7 日。ふだんは停滞の時間（進捗が変わらない秒数）で止める。
INGESTION_JOB_SUBPROCESS_TIMEOUT_DEFAULT_SECONDS = 86400.0
INGESTION_JOB_SUBPROCESS_TIMEOUT_MAX_SECONDS = 604800.0
# 取込 job の停滞の時間の既定（秒。#2003）。進捗を出さない 1 回の呼び出し（再試行を含む）の最長
# より長くする: 解析サービスは配備の unit の 1230 秒 × 5 回 + 待ち = 約 6170 秒（timeout も
# 再試行する）、Vision の LLM は共通の設定（PLATFORM_OCI_ENTERPRISE_AI_*）で選べる最長が
# 600 秒 × 6 回 = 3600 秒、MinerU の job は既定 1800 秒。これらより長い 2 時間にする。
INGESTION_JOB_STALL_TIMEOUT_DEFAULT_SECONDS = 7200.0
CHUNK_CHILD_TARGET_CHARS_DEFAULT = 1000
CHUNK_CHILD_TARGET_CHARS_MIN = 300
CHUNK_CHILD_TARGET_CHARS_MAX = 1600
CHUNK_TABLE_CHILD_TARGET_CHARS_DEFAULT = 3000
CHUNK_TABLE_CHILD_TARGET_CHARS_MIN = 300
CHUNK_TABLE_CHILD_TARGET_CHARS_MAX = 8000
CHUNK_PARENT_TARGET_CHARS_DEFAULT = 6000
CHUNK_PARENT_TARGET_CHARS_MIN = 1200
CHUNK_PARENT_TARGET_CHARS_MAX = 10000
CHUNK_PARENT_MAX_PAGES_DEFAULT = 3
CHUNK_PARENT_MAX_PAGES_MIN = 1
CHUNK_PARENT_MAX_PAGES_MAX = 5
CHUNK_PARENT_MAX_CHILDREN_DEFAULT = 12
CHUNK_PARENT_MAX_CHILDREN_MIN = 3
CHUNK_PARENT_MAX_CHILDREN_MAX = 20
# 親子階層（small-to-big）の分割パラメータの Settings 属性名
# (保存・受け渡し・chunk_set の hash で使う)。
SMALL_TO_BIG_SETTING_FIELDS: tuple[str, ...] = (
    "rag_chunk_child_target_chars",
    "rag_chunk_table_child_target_chars",
    "rag_chunk_parent_target_chars",
    "rag_chunk_parent_max_pages",
    "rag_chunk_parent_max_children",
)
# 回答フローの選択肢(rag_engine.generation.answer_models の ID と一致させる)。
QueryStrategy = Literal[
    "auto_routing",
    "simple_retrieval",
    "rag_fusion",
    "query_decomposition",
    "step_back_prompting",
    "hyde",
]
AnswerFlow = Literal["crag", "standard_rag"]
# 回答の LLM の用途ごとの推論(思考)の量(#1499)。model はモデルの登録値(共通のモデル設定)のまま、
# none は送らない、low / medium / high はその値を送る(platform の推論の量の選択肢と同じ)。
ReasoningEffortOverride = Literal["model", "none", "low", "medium", "high"]
MineruTier = Literal["flash", "basic", "standard", "advanced"]
GuardrailPolicyName = Literal[
    "standard",
    "strict",
    "lenient",
    "regulated",
]
# Guardrail のバックエンド。local(既定)は in-process 決定論ヒューリスティック。
# oci_guardrails は OCI Generative AI Guardrails(ApplyGuardrails、検出専用 API)を併用し、
# 未設定/失敗時は local へ安全に縮退する。
GuardrailBackend = Literal[
    "local",
    "oci_guardrails",
]
VectorIndexProfile = Literal[
    "balanced",
    "accurate",
    "fast",
]
# 評価の基準(閾値のプリセット。#591)。
EvaluationSuite = Literal[
    "standard",
    "strict",
]
# 関係情報の構築(#621)。off = 構築しない、entities = 文書と章・節の見出しのつながりを構築する。
GraphProfile = Literal[
    "off",
    "entities",
]
# #621 で削除した関係情報の構築の値。backend/.env に残っていると起動を止めて書き換えを促す。
REMOVED_GRAPH_PROFILES = frozenset({"full"})
EnterpriseAiVlmInputMode = Literal["files_api", "inline_image"]
BACKEND_ROOT = Path(__file__).resolve().parents[1]
# RAG 固有の設定（`RAG_*`）を置く `backend/.env`。
BACKEND_ENV_FILE = product_env_file("RAG_", BACKEND_ROOT)
# 3製品共通の設定（`PLATFORM_*`）を置く `platform/.env`。`PLATFORM_ENV_FILE` で上書きできる。
PLATFORM_ENV_FILE = platform_env_file(BACKEND_ROOT)
DEFAULT_MODEL_SETTINGS_FILE = "model-settings.json"
# 文書解析の設定（RAG 専用。#1701）。相対 path は backend/ 基準。
DEFAULT_PARSER_SETTINGS_FILE = "parser-settings.json"
# 言語資源の全体の上書き(#2181)。
DEFAULT_LANGUAGE_PACK_FILE = "language-pack.json"
DEFAULT_LOCAL_STORAGE_DIR = "/u01/data/production-ready-rag"


# 検索・回答プロファイル（と品質評価の rag_overrides）の値だけで決める Settings の項目（#1388）。
# プロファイルの値は Settings に重ねて回答の検索へ渡す（``compose_query_settings``）ため Settings の
# 項目として持つが、環境変数 / .env からは読まない（既定はコードの Settings の既定。拡張は on。
# #1402）。止めるときはプロファイルの開閉を off にする（再起動は要らない）。値の範囲は Settings の
# 検証で持つ。
PROFILE_ONLY_SETTING_FIELDS = frozenset(
    {
        "rag_entity_expansion_enabled",
        "rag_entity_expansion_max_chunks",
        # 章節の拡張（#1436）。既定 on。上限もプロファイルで選ぶ。
        "rag_section_expansion_enabled",
        "rag_section_expansion_max_levels",
        "rag_section_expansion_input_token_percent",
        "rag_section_expansion_time_limit_seconds",
        # 抽出してから統合（#1437）の抽出の呼び出しの上限。既定 8。
        "rag_section_extraction_max_calls",
        # rerank の後、起点に採る同じ文書の chunk の上限（#2167）。既定 5。
        "rag_max_anchors_per_document",
    }
)


def _without_profile_only_fields(
    source: PydanticBaseSettingsSource,
) -> Callable[[], dict[str, Any]]:
    """環境変数 / .env の source から、プロファイルだけで決める項目を除く。"""
    excluded = {
        name.casefold()
        for field in PROFILE_ONLY_SETTING_FIELDS
        for name in (field, settings_env_name("RAG_", field))
    }

    def read() -> dict[str, Any]:
        return {key: value for key, value in source().items() if key.casefold() not in excluded}

    # pydantic-settings は source の名前ごとに読んだ値を持つ（debug の表示）。元の名前を保つ。
    read.__name__ = type(source).__name__
    return read


class _PersistedParserAdapterSettings(BaseModel):
    """UI から保存された文書解析 backend と外部接続設定（`parser-settings.json`。#1701）。

    API key は持たない（RAG の backend/.env の `RAG_PARSER_*_API_KEY`）。
    """

    # 既定エンジンは Docling(#286)。選択中の engine の flag だけを既定で有効にする。
    adapter_backend: ParserAdapterBackend = DEFAULT_PARSER_ADAPTER_BACKEND
    docling_enabled: bool = True
    unstructured_enabled: bool = False
    mineru_enabled: bool = False
    dots_ocr_enabled: bool = False
    xml_tree_json_enabled: bool = False
    mineru_api_host: str = Field(default="", max_length=2048)
    dots_ocr_api_host: str = Field(default="", max_length=2048)
    dots_ocr_model: str = Field(default="rednote-hilab/dots.mocr", max_length=512)

    # 削除済みエンジンの項目(marker_enabled 等)と、画面から外した docling_vision_enabled(#497。
    # Vision の既定は backend/.env の RAG_VISION_ENABLED で決める。#528)は
    # 既定の extra=ignore で読み捨てる。
    # adapter_backend に削除済みエンジンが残っていれば既定エンジンへ寄せる(#270)。
    @field_validator("adapter_backend", mode="before")
    @classmethod
    def normalize_adapter_backend(cls, value: object) -> object:
        return normalize_parser_adapter_backend_value(value)


# 取込の worker の同時実行数に書ける上限(全体・重い段とも)。
INGESTION_QUEUE_MAX_CONCURRENCY = 16


def detect_vcpu_count() -> int:
    """この process が使える vCPU の数(OCI の Flex の Compute では OCPU × 2)。

    cgroup / taskset で絞られた CPU を数えるため ``os.sched_getaffinity`` を先に使う(#2027)。
    """
    sched_getaffinity = getattr(os, "sched_getaffinity", None)
    if sched_getaffinity is not None:
        with contextlib.suppress(OSError):
            return max(1, len(sched_getaffinity(0)))
    return max(1, os.cpu_count() or 1)


@dataclass(frozen=True)
class IngestionQueueConcurrency:
    """取込の worker の同時実行数の決まった値と、その元(起動時のログに出す。#2027)。"""

    total: int
    heavy_phase: int
    vcpu: int
    total_auto: bool
    heavy_phase_auto: bool

    def log_fields(self) -> dict[str, object]:
        return {
            "worker_concurrency": self.total,
            "heavy_phase_concurrency": self.heavy_phase,
            "vcpu": self.vcpu,
            "worker_concurrency_auto": self.total_auto,
            "heavy_phase_concurrency_auto": self.heavy_phase_auto,
        }


def resolve_ingestion_queue_concurrency(
    total: int | Literal["auto"],
    heavy_phase: int | Literal["auto"],
    *,
    vcpu: int,
) -> IngestionQueueConcurrency:
    """同時実行数の設定(数か auto)から、worker の全体の枠と重い段の上限を決める(#2027)。

    auto の全体は max(2, vCPU − 2)、重い段は max(1, (vCPU − 2) // 3)。3 製品の backend・Nginx・
    解析サービスに CPU を残す。解析(重い段)は 1 回で複数のスレッドの CPU を使うので CPU の数で
    絞り、LLM の応答待ちが多い段は全体の枠で並べる。重い段は全体を超えない。
    """
    reserved = max(vcpu - 2, 0)
    resolved_total = (
        min(INGESTION_QUEUE_MAX_CONCURRENCY, max(2, reserved)) if total == "auto" else total
    )
    resolved_heavy = max(1, reserved // 3) if heavy_phase == "auto" else heavy_phase
    return IngestionQueueConcurrency(
        total=resolved_total,
        heavy_phase=min(resolved_heavy, resolved_total),
        vcpu=vcpu,
        total_auto=total == "auto",
        heavy_phase_auto=heavy_phase == "auto",
    )


class Settings(PlatformEnvSourcesMixin, ModelSecretStateMixin, BaseSettings):
    """環境変数ベースの設定。旧名（属性名と同じ環境変数名）は読まない（#211）。"""

    # 環境変数名は属性名から決める（共通の属性は `PLATFORM_*`、それ以外は `RAG_*`。#211）。
    model_config = product_settings_config(prefix="RAG_", backend_dir=BACKEND_ROOT)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[Any, ...]:
        """共通の source（旧名を読まない）から、プロファイルだけで決める項目を除く（#1388）。"""
        init, env, dotenv, secrets = super().settings_customise_sources(
            settings_cls, init_settings, env_settings, dotenv_settings, file_secret_settings
        )
        return (
            init,
            _without_profile_only_fields(env),
            _without_profile_only_fields(dotenv),
            secrets,
        )

    # --- アプリ ---
    app_name: str = "production-ready-rag"
    environment: str = Field(default="dev")
    log_level: str = Field(default="INFO")
    app_version: str = Field(default="0.1.0")
    auth_mode: AuthMode = Field(
        default="local",
        description=(
            "local は全権限・対象範囲の制限なしのローカル利用者として動かす（ログイン不要）。"
            "production は共通認証（PLATFORM_* のユーザー・ロール）のログインを必須にする。"
        ),
    )
    # --- 共通認証（platform の pr_system_settings.auth。#214）---
    # 構成管理者・認証ポリシー・ログインの Cookie 名は共通 `.env` の PLATFORM_ADMIN_* /
    # PLATFORM_AUTH_*（#211）。Cookie は 3 製品で共有し、1 回のログインで全製品を使える（#1565）。
    service_name: str = Field(default="production-ready-rag")
    app_admin_login_user_id: str = Field(default="")
    app_admin_login_user_password: str = Field(default="")
    app_auth_cookie_secure: bool = Field(default=False)
    app_auth_session_cookie_name: str = Field(default=DEFAULT_SESSION_COOKIE_NAME)
    app_auth_csrf_cookie_name: str = Field(default=DEFAULT_CSRF_COOKIE_NAME)
    app_auth_idle_timeout_minutes: int = Field(default=60)
    app_auth_absolute_timeout_hours: int = Field(default=12)
    app_auth_failed_login_limit: int = Field(default=5)
    app_auth_lockout_minutes: int = Field(default=15)
    # ログインの試行の回数の制限（ログイン ID と送信元 IP の組・送信元 IP ごと。0 で無効。#1087）。
    app_auth_login_attempt_limit: int = Field(default=5)
    app_auth_login_ip_attempt_limit: int = Field(default=20)
    app_auth_login_attempt_window_minutes: int = Field(default=15)
    app_auth_password_min_length: int = Field(default=12)
    app_auth_password_max_length: int = Field(default=128)
    app_auth_argon2_time_cost: int = Field(default=3)
    app_auth_argon2_memory_kib: int = Field(default=65536)
    app_auth_argon2_parallelism: int = Field(default=4)
    # Agent が MCP（`POST /api/mcp`）を利用者として呼ぶサービストークンの署名鍵（#230 / #232）。
    # 共通 `.env` の PLATFORM_SERVICE_TOKEN_SECRET。空なら MCP は 503 で拒否する。
    app_service_token_secret: str = Field(default="", repr=False)
    # 公開の基底の URL（共通 `.env` の PLATFORM_PUBLIC_BASE_URL。例 `https://<公開 IP>`）。配備の
    # suite-init.sh が書き、API キーの画面の接続先に出す（#1870）。
    app_public_base_url: str = ""
    # 外部の MCP クライアントの API キーの 1 分あたりの呼び出しの回数（#1874。0 は上限なし）。
    app_api_key_calls_per_minute: int = Field(default=60, ge=0, le=100_000)
    model_settings_file: str = Field(
        default=DEFAULT_MODEL_SETTINGS_FILE,
        description="UI から保存した共有ランタイム設定 JSON。存在する場合は .env より優先する。",
    )
    rag_parser_settings_file: str = Field(
        default=DEFAULT_PARSER_SETTINGS_FILE,
        description=(
            "「文書解析」画面から保存した解析エンジンと外部接続の JSON（RAG 専用。#1701）。"
            "存在する場合は .env より優先する。相対 path は backend/ 基準。"
        ),
    )

    rag_language_pack_file: str = Field(
        default=DEFAULT_LANGUAGE_PACK_FILE,
        description=(
            "「言語資源」画面から保存した、組み込みの言語資源との差の JSON(#2181)。"
            "相対 path は backend/ 基準。"
        ),
    )

    # --- HuggingFace モデルダウンロード ---
    # RAG_HUGGINGFACE_TOKEN / RAG_HUGGINGFACE_ENDPOINT。huggingface_hub が読む HF_TOKEN /
    # HF_ENDPOINT は、サービス管理が起動/再起動の前にサービス実行用の env ファイルへ書き、
    # parser の systemd の unit が EnvironmentFile で読む(#286)。
    huggingface_token: str = Field(default="")
    huggingface_endpoint: str = Field(default="")

    # CORS 許可オリジン（フロントエンド）
    cors_origins: list[str] = Field(default=["http://localhost:3000"])

    # --- OCI 共通 ---
    oci_config_file: str = Field(default="~/.oci/config")
    oci_config_profile: str = Field(default="DEFAULT")
    oci_region: str = Field(default="")
    oci_compartment_id: str = Field(default="")

    # --- OCI Enterprise AI（LLM / Vision-capable LLM）---
    # 注意: OCI Generative AI の chat 推論 API ではなく Enterprise AI を使う
    oci_enterprise_ai_endpoint: str = Field(default="")
    oci_enterprise_ai_project_ocid: str = Field(default="")
    oci_enterprise_ai_api_key: str = Field(default="")
    # OCI Enterprise AI のセカンダリ接続（#533）。モデルを呼ぶ接続は
    # enterprise_ai_connection_for_model で引く。
    oci_enterprise_ai_secondary_endpoint: str = Field(default="")
    oci_enterprise_ai_secondary_project_ocid: str = Field(default="")
    oci_enterprise_ai_secondary_api_key: str = Field(default="")
    # ターシャリ接続（#786。OpenAI / OpenAI 互換 API 向け。Project OCID は任意）。
    oci_enterprise_ai_tertiary_endpoint: str = Field(default="")
    oci_enterprise_ai_tertiary_project_ocid: str = Field(default="")
    oci_enterprise_ai_tertiary_api_key: str = Field(default="")
    oci_enterprise_ai_models: list[EnterpriseAiConfiguredModel] = Field(default_factory=list)
    # 既定のテキストモデルと既定の Vision モデル（#499。画面・API では 2 つとも必須。#566）。
    # 呼び出しに使う ID は enterprise_ai_default_model_id / enterprise_ai_vision_model_id
    # で解決する。
    oci_enterprise_ai_default_text_model: str = Field(default="")
    oci_enterprise_ai_default_vision_model: str = Field(default="")
    oci_enterprise_ai_llm_path: str = Field(default="/responses")
    oci_enterprise_ai_vlm_path: str = Field(default="/responses")
    oci_enterprise_ai_vlm_input_mode: EnterpriseAiVlmInputMode = Field(
        default="files_api",
        description=(
            "Enterprise AI VLM への入力搬送方式。files_api は VLM 入力を明示的に /files "
            "経由へ送る。inline_image は画像のみ inline。"
        ),
    )
    oci_enterprise_ai_llm_payload_template: str = Field(
        default="",
        description=(
            "Enterprise AI LLM endpoint の request JSON template。空なら標準 RAG payload。"
        ),
    )
    oci_enterprise_ai_vlm_payload_template: str = Field(
        default="",
        description=(
            "Enterprise AI VLM endpoint の request JSON template。空なら標準 OCR payload。"
        ),
    )
    oci_enterprise_ai_llm_response_path: str = Field(
        default="",
        description=(
            "Enterprise AI LLM response から回答候補を取り出す JSON Pointer。"
            "空なら既知 envelope を順番に照合する。"
        ),
    )
    oci_enterprise_ai_vlm_response_path: str = Field(
        default="",
        description=(
            "Enterprise AI VLM response から StructuredExtraction 候補を取り出す JSON Pointer。"
            "空なら既知 envelope を順番に照合する。"
        ),
    )
    oci_enterprise_ai_timeout_seconds: float = Field(
        default=OCI_ENTERPRISE_AI_TIMEOUT_MAX_SECONDS,
        gt=0.0,
        le=OCI_ENTERPRISE_AI_TIMEOUT_MAX_SECONDS,
    )
    oci_enterprise_ai_max_retries: int = Field(default=3, ge=0, le=5)
    # 出力の上限は登録モデルごとの `max_output_tokens`(#1719。platform の
    # `enterprise_ai_output_token_budget`)。共通の LLM / VLM の出力の上限は持たない。
    # LLM / VLM の呼び出しの seed（#1434。共通のモデル設定）。
    # 解釈は pr_system_settings の enterprise_ai_seed。
    oci_enterprise_ai_seed: int = Field(default=42, ge=0, le=2_147_483_647)
    # チャットで同じ会話の前のターンを LLM に渡す予算（共通の設定。#1818）。
    # 解釈は pr_backend_core.chat_history（新しいターンから詰め、古いターンを落とす）。
    chat_history_max_turns: int = Field(
        default=CHAT_HISTORY_DEFAULT_MAX_TURNS, ge=0, le=CHAT_HISTORY_MAX_TURNS_LIMIT
    )
    chat_history_max_chars_per_turn: int = Field(
        default=CHAT_HISTORY_DEFAULT_MAX_CHARS_PER_TURN,
        ge=CHAT_HISTORY_MIN_CHARS_PER_TURN,
        le=CHAT_HISTORY_MAX_CHARS_PER_TURN_LIMIT,
    )
    chat_history_max_chars: int = Field(
        default=CHAT_HISTORY_DEFAULT_MAX_CHARS,
        ge=CHAT_HISTORY_MIN_CHARS,
        le=CHAT_HISTORY_MAX_CHARS_LIMIT,
    )

    # --- OCI Generative AI（埋め込み / リランク）---
    oci_genai_embedding_model: str = Field(default="cohere.embed-v4.0")
    oci_genai_embedding_dim: int = Field(
        default=1536,
        ge=1536,
        le=1536,
        description="Cohere Embed v4 と Oracle VECTOR(1536, FLOAT32) に合わせる。",
    )
    oci_genai_rerank_model: str = Field(default="cohere.rerank-v4.0-fast")

    # --- Oracle AI Database ---
    oracle_user: str = Field(default="")
    oracle_password: str = Field(default="")
    oracle_dsn: str = Field(default="")
    oracle_client_lib_dir: str = Field(
        default="",
        description=(
            "指定したときだけ Thick mode（Instant Client）で接続する。既定は空で Thin mode。"
        ),
    )
    oracle_wallet_dir: str = Field(
        default="/u01/aipoc/wallet",
        description=(
            "Thin mode の Wallet 配置先。"
            "Thick mode では PLATFORM_ORACLE_CLIENT_LIB_DIR/network/admin を使う。"
        ),
    )
    oracle_wallet_password: str = Field(default="")
    oracle_adb_ocid: str = Field(
        default="",
        description=(
            "Autonomous Database 操作対象の OCID。起動 / 停止 / 情報取得に使う。"
            "ベクトル検索とは別経路の OCI Database 制御プレーン操作用。"
        ),
    )
    oracle_adb_region: str = Field(
        default="",
        description=(
            "Autonomous Database 管理専用の OCI region。未設定なら PLATFORM_OCI_REGION を使う。"
        ),
    )
    oracle_tcp_connect_timeout_seconds: float = Field(
        default=10.0,
        gt=0.0,
        le=120.0,
        description="Oracle TCP 接続の待機秒数。ADB/Wallet 疎通確認を長時間ブロックしない。",
    )
    oracle_db_test_timeout_seconds: float = Field(
        default=15.0,
        gt=0.0,
        le=180.0,
        description="Oracle 接続テスト API 全体の待機秒数。",
    )
    oracle_pool_max: int = Field(
        default=16,
        ge=1,
        le=128,
        description=(
            "backend の Oracle の接続 pool の上限(接続の数)。回答の検索は 1 問の中で"
            "検索文をまとめた vector / keyword の SQL を並行に読み(#1498)、1 問で 2 本程度の"
            "接続を使うため、同時に答える問の数に合わせる。"
        ),
    )
    oracle_pool_min: int = Field(
        default=4,
        ge=1,
        le=128,
        description=(
            "backend(API)の起動時に開いておく Oracle の接続の数(接続 pool の min。#1498)。"
            "起動直後の問で接続の作成を待たない。取込の job のプロセスは 1 本から始める。"
            "RAG_ORACLE_POOL_MAX より大きければ RAG_ORACLE_POOL_MAX にする。"
        ),
    )
    oracle_vector_target_accuracy: int = Field(
        default=95,
        ge=1,
        le=100,
        description="Oracle AI Vector Search の FETCH APPROX target accuracy。",
    )

    # --- OCI Object Storage ---
    object_storage_region: str = Field(default="")
    object_storage_namespace: str = Field(default="")
    object_storage_bucket: str = Field(default="production-ready")
    upload_storage_backend: UploadStorageBackend = Field(
        default="local",
        description=(
            "アップロード原本の保存先。local は PLATFORM_LOCAL_STORAGE_DIR、"
            "oci は OCI Object Storage。"
        ),
    )

    # --- ローカルアップロード保存先 ---
    local_storage_dir: str = Field(default=DEFAULT_LOCAL_STORAGE_DIR)
    max_upload_bytes: int = Field(default=200 * 1024 * 1024, ge=1)
    allowed_upload_content_types: list[str] = Field(
        default=[
            "application/pdf",
            "image/gif",
            "image/jpeg",
            "image/jpg",
            "image/png",
            "image/tif",
            "image/webp",
            "image/tiff",
            "text/plain",
            "text/markdown",
            "text/csv",
            "text/tab-separated-values",
            "text/html",
            "application/xhtml+xml",
            "application/json",
            "application/jsonl",
            "application/jsonlines",
            "application/ndjson",
            "application/xml",
            "application/csv",
            "application/x-ndjson",
            "message/rfc822",
            "application/eml",
            "application/vnd.ms-outlook",
            "application/x-msg",
            "application/msword",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/vnd.ms-powerpoint",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            "application/vnd.ms-excel",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "audio/aac",
            "audio/flac",
            "audio/mp3",
            "audio/mpeg",
            "audio/mp4",
            "audio/ogg",
            "audio/wave",
            "audio/wav",
            "audio/x-flac",
            "audio/x-m4a",
            "audio/x-wav",
            "application/ogg",
            "application/octet-stream",
        ]
    )

    # --- 取込 queue ---
    ingestion_queue_startup_recovery_enabled: bool = Field(
        default=True,
        description="起動時に永続化済み QUEUED job と stale RUNNING job を自動回復する。",
    )
    ingestion_queue_startup_drain_limit: int = Field(default=50, ge=1, le=500)
    ingestion_queue_stale_running_seconds: float = Field(
        default=300.0,
        gt=0.0,
        le=86400.0,
        description=(
            "heartbeat の無い RUNNING job(lease 導入前の行・lease を持たない実行)を"
            "stale とみなすまでの秒数。heartbeat のある job は"
            "RAG_INGESTION_QUEUE_LEASE_TTL_SECONDS で判定する(#357)。"
        ),
    )
    ingestion_queue_heartbeat_interval_seconds: float = Field(
        default=15.0,
        gt=0.0,
        le=600.0,
        description="取込 worker が自分の lease の RUNNING job に heartbeat を打つ間隔(秒)。#357",
    )
    ingestion_queue_lease_ttl_seconds: float = Field(
        default=90.0,
        gt=0.0,
        le=3600.0,
        description=(
            "heartbeat がこの秒数途絶えた RUNNING job を、worker が止まったとみなして回復する。"
            "heartbeat 間隔の 3 倍以上にする。job の長さの上限は job の timeout だけが持つ(#357)。"
        ),
    )
    ingestion_queue_shutdown_grace_seconds: float = Field(
        default=60.0,
        ge=0.0,
        le=3600.0,
        description=(
            "取込 worker が停止(SIGTERM)を受けてから、実行中の job の完了を待つ上限(秒)。"
            "超えたら子プロセスを止め、自分の lease の job を QUEUED に戻す(attempt は増やさない)。"
            "systemd の TimeoutStopSec はこれより長くする(#357)。"
        ),
    )
    ingestion_queue_recovery_interval_seconds: float = Field(
        default=60.0,
        gt=0.0,
        le=3600.0,
        description=(
            "専用ワーカーが起動後も stale/固着文書の復旧を再実行する最短間隔（秒）。"
            "クラッシュで INGESTING のまま取り残された文書を再起動なしで回復させる。"
        ),
    )
    ingestion_queue_worker_concurrency: int | Literal["auto"] = Field(
        default="auto",
        description=(
            "取込の worker が同時に処理する job(文書)の数(1〜16)。既定 auto は vCPU の数から"
            "max(2, vCPU − 2)(#2027)。3 製品の backend・Nginx・解析サービスに CPU を残す。"
            "軽い段(Chunk 作成・索引)は LLM / Embedding の応答待ちが多いので、全体の枠で並べる。"
            "Enterprise AI の 429 が増えるときは数を書いて下げる。"
        ),
    )
    ingestion_queue_heavy_phase_concurrency: int | Literal["auto"] = Field(
        default="auto",
        description=(
            "全体の枠のうち、重い段(ファイル準備・抽出。解析サービスの CPU を使う)を同時に処理する"
            "上限(1〜16。全体を超えない)。既定 auto は max(1, (vCPU − 2) // 3)(#2027)。解析は"
            "1 回で複数のスレッドの CPU を使うため、重い段だけを CPU の数で絞る。"
        ),
    )
    ingestion_queue_pipeline_priority_window_seconds: int = Field(
        default=3600,
        ge=0,
        le=86400,
        description=(
            "続きの段(Chunk 作成・索引)の job を、その文書の取込の最初の段の投入の時刻まで"
            "繰り上げて取る上限(秒。#2027)。始めた文書を先に終わらせ(パイプライン)、"
            "この秒数より前から待っている job は追い越さない(飢餓を防ぐ)。0 なら投入の順(FIFO)。"
        ),
    )
    ingestion_job_max_attempts: int = Field(default=3, ge=1, le=20)
    ingestion_queue_dedicated_worker_enabled: bool = Field(
        default=True,
        description=(
            "True にすると取込はキュー投入のみとし、専用ワーカー（in-process または別プロセス）"
            "がジョブを消費する。HTTP リクエスト内では取込を実行しない。"
        ),
    )
    ingestion_queue_poll_interval_seconds: float = Field(
        default=2.0,
        gt=0.0,
        le=60.0,
        description="専用ワーカーが QUEUED ジョブをポーリングする間隔（秒）。",
    )
    ingestion_queue_inprocess_worker_enabled: bool = Field(
        default=True,
        description=(
            "専用ワーカーモード時に API プロセス内（lifespan）でもワーカーを起動するか。"
            "別プロセスのワーカーへ完全に切り出す場合は False にする。"
        ),
    )
    ingestion_queue_process_isolation_enabled: bool = Field(
        default=True,
        description=(
            "in-process ワーカーが job 本体を subprocess で実行し、Docling/OCR/CUDA 初期化を "
            "API プロセスから隔離する。専用の取込 worker(別プロセス)では False にして"
            "直接実行できる。"
        ),
    )
    ingestion_job_subprocess_timeout_seconds: float = Field(
        default=INGESTION_JOB_SUBPROCESS_TIMEOUT_DEFAULT_SECONDS,
        gt=0.0,
        le=INGESTION_JOB_SUBPROCESS_TIMEOUT_MAX_SECONDS,
        description=(
            "process isolation 時の 1 取込 job の全体の上限(秒。保険)。進んでいる job も"
            "これを超えたら止める。1 文書 1,000 図を 1 件 60 秒で順に読んでも足りる 24 時間を"
            "既定にする(#2003)。ふだんの打ち切りは RAG_INGESTION_JOB_STALL_TIMEOUT_SECONDS。"
        ),
    )
    ingestion_job_stall_timeout_seconds: float = Field(
        default=INGESTION_JOB_STALL_TIMEOUT_DEFAULT_SECONDS,
        gt=0.0,
        le=INGESTION_JOB_SUBPROCESS_TIMEOUT_MAX_SECONDS,
        description=(
            "process isolation 時、進捗を出す段(解析のページ・図・表の説明)の途中で job の"
            "進捗(rag_ingestion_jobs.progress_json)がこの秒数変わらなければ止める(#2003)。"
            "進捗を出さない 1 回の呼び出し(解析サービス・MinerU・Document Understanding・"
            "AI Speech・Vision の LLM。再試行を含む)の最長より長くする(起動時に検証する)。"
        ),
    )

    # --- RAG ---
    rag_chunk_size: int = Field(
        default=800,
        ge=CHUNK_SIZE_MIN_CHARS,
        le=CHUNK_SIZE_MAX_CHARS,
    )
    rag_chunk_overlap: int = Field(default=120, ge=0, le=CHUNK_OVERLAP_MAX_CHARS)
    rag_chunking_strategy: ChunkingStrategy = Field(
        default="small_to_big",
        description=(
            "chunks 段階の分割戦略(Chunking アダプター)。"
            "small_to_big(既定)は親子階層(small-to-big。Docling の解析結果を使う。"
            "解析結果が Docling でない文書は structure_aware で分割する)、"
            "structure_aware は element/section/table 認識、recursive_character は固定長、"
            "markdown_heading は章節単位、page_level はページ単位、"
            "fixed_size は章節・文境界を無視した純粋な固定長分割、"
            "fixed_delimiter は指定文字列での固定分割。"
        ),
    )
    rag_chunk_delimiter: str = Field(
        default="\\n\\n",
        min_length=1,
        max_length=256,
        description="fixed_delimiter 戦略で使う分割符。\\n / \\t / \\\\ の escape 表現を許可する。",
    )
    # 親子階層（small-to-big。`small_to_big`）の分割パラメータ。
    # rag_poc の「チャンキング」tab と同じ 5 項目。
    rag_chunk_child_target_chars: int = Field(
        default=CHUNK_CHILD_TARGET_CHARS_DEFAULT,
        ge=CHUNK_CHILD_TARGET_CHARS_MIN,
        le=CHUNK_CHILD_TARGET_CHARS_MAX,
        description=(
            "親子階層(small-to-big)で、検索に使う子 chunk の目標文字数。"
            "超える Text / List-item は文末で複数の子へ分ける。"
        ),
    )
    rag_chunk_table_child_target_chars: int = Field(
        default=CHUNK_TABLE_CHILD_TARGET_CHARS_DEFAULT,
        ge=CHUNK_TABLE_CHILD_TARGET_CHARS_MIN,
        le=CHUNK_TABLE_CHILD_TARGET_CHARS_MAX,
        description=(
            "親子階層(small-to-big)で、表を行グループへ分ける閾値と各グループの目標文字数。"
            "各グループには列見出しと関連見出しを繰り返し付ける。"
        ),
    )
    rag_chunk_parent_target_chars: int = Field(
        default=CHUNK_PARENT_TARGET_CHARS_DEFAULT,
        ge=CHUNK_PARENT_TARGET_CHARS_MIN,
        le=CHUNK_PARENT_TARGET_CHARS_MAX,
        description="親子階層(small-to-big)で、回答文脈に使う親 chunk の目標文字数。",
    )
    rag_chunk_parent_max_pages: int = Field(
        default=CHUNK_PARENT_MAX_PAGES_DEFAULT,
        ge=CHUNK_PARENT_MAX_PAGES_MIN,
        le=CHUNK_PARENT_MAX_PAGES_MAX,
        description="親子階層(small-to-big)で、1 つの親 chunk がまたげる最大ページ数。",
    )
    rag_chunk_parent_max_children: int = Field(
        default=CHUNK_PARENT_MAX_CHILDREN_DEFAULT,
        ge=CHUNK_PARENT_MAX_CHILDREN_MIN,
        le=CHUNK_PARENT_MAX_CHILDREN_MAX,
        description="親子階層(small-to-big)で、1 つの親 chunk に入れる子 chunk の最大数。",
    )
    rag_chunk_min_chars: int = Field(
        default=120,
        ge=0,
        le=2000,
        description=(
            "この文字数未満の微小 chunk を隣接 chunk へ吸収する下限。0 で無効。"
            "rag_chunk_size より小さくする。"
        ),
    )
    rag_chunk_context_header_enabled: bool = Field(
        default=True,
        description=(
            "embedding 入力の先頭へ「文書名 > section_path」の文脈ヘッダを前置する"
            "(Anthropic Contextual Retrieval の決定論版)。保存 chunk 本文・引用表示は変えない。"
        ),
    )

    rag_context_group_max_chunks: int = Field(
        default=4,
        ge=1,
        le=20,
        description="同一 chunk group から anchor ごとに追加する sibling chunk 数の上限。",
    )
    rag_reference_expansion_enabled: bool = Field(
        default=True,
        description=(
            "回答の検索で、上位の候補の chunk が本文で参照する節(「第3章を参照」など。取込時に"
            "解決して chunk の metadata に保存)の chunk を rerank の候補に足す(#1280)。"
            "LLM の呼び出しは増えない。参照の無い候補だけなら SQL も増えない。"
        ),
    )
    rag_reference_expansion_max_chunks: int = Field(
        default=4,
        ge=1,
        le=20,
        description="交差参照で 1 回の検索に足す参照先の chunk 数の上限(#1280)。",
    )
    rag_entity_index_enabled: bool = Field(
        default=True,
        description=(
            "取込の索引の保存の後に、chunk から実体（表の行の名前・属性の値、本文の定義の形・"
            "言及）と「実体と chunk の関連」を決定的に抜き出し、Oracle の rag_entities / "
            "rag_entity_aliases / rag_entity_chunks に保存する（#1362。既定 ON（#1388）。文書"
            "レシピの「実体の索引」で文書ごとに無効にできる。LLM は使わない）。回答の検索は、"
            "この表との SQL の join で関連する chunk を 1 段だけ足す（検索・回答プロファイルの"
            " entity_expansion_enabled。#1388）。"
        ),
    )
    rag_entity_name_columns: list[str] = Field(
        default_factory=list,
        description=(
            "実体の抽出で、表の行の名前（同じ実体の別名）にする列名（#1362）。空なら列名で"
            "決める（ID・番号・コード・正式名・名称・〜名・略称・別表記・通称）。"
        ),
    )
    rag_entity_attribute_columns: list[str] = Field(
        default_factory=list,
        description=(
            "実体の抽出で、表の行の属性の値を実体にする列名（#1362。担当部署・重要度など）。"
            "空なら名前の列・長い本文の列（説明・備考など）・日付や数値の列を除いた列。"
        ),
    )
    # 次の 2 つは、検索・回答プロファイル（と品質評価の rag_overrides）の値を回答の検索へ渡すための
    # 項目で、環境変数 / .env からは読まない（PROFILE_ONLY_SETTING_FIELDS。#1388）。
    rag_entity_expansion_enabled: bool = Field(
        default=True,
        description=(
            "回答の検索で、質問と上位の候補の chunk の実体から、実体の表との SQL の join で"
            "関連する chunk を 1 段だけ足す（#1362）。検索・回答プロファイルの"
            " entity_expansion_enabled で選ぶ（既定 ON。#1402）。実体は文書レシピで実体の抽出"
            "（rag_entity_index_enabled）を選んだ文書にだけある。LLM の呼び出しは増えない。"
        ),
    )
    rag_entity_expansion_max_chunks: int = Field(
        default=6,
        ge=1,
        le=20,
        description=(
            "実体の 1 段の拡張で 1 回の検索に足す chunk 数の上限（#1362）。検索・回答プロファイルの"
            " entity_expansion_max_chunks で選ぶ（#1388）。"
        ),
    )
    rag_graph_expansion_enabled: bool = Field(
        default=False,
        description=(
            "回答の検索で、関係情報の構築（rag_graph_profile=entities）で作った章節・表 / 図の"
            "見出しの entity と chunk の対応から候補を足す（#2055。既定 OFF。LLM の呼び出しは"
            "増えない）。検索の上位の chunk と同じ狭い章節・表の chunk と、質問に見出しの語が"
            "含まれる章節の chunk を足す。検索方法の画面と検索・回答プロファイルの"
            " graph_expansion_enabled で選ぶ。関係情報の無い文書では何もしない。"
        ),
    )
    rag_graph_expansion_max_chunks: int = Field(
        default=6,
        ge=1,
        le=20,
        description=(
            "関係情報で 1 回の検索に足す chunk 数の上限（#2055）。検索・回答プロファイルの"
            " graph_expansion_max_chunks で上書きできる。"
        ),
    )
    # 次の 4 つも、検索・回答プロファイル（と品質評価の rag_overrides）の値を回答へ渡すための
    # 項目で、環境変数 / .env からは読まない（PROFILE_ONLY_SETTING_FIELDS。#1436）。
    rag_section_expansion_enabled: bool = Field(
        default=True,
        description=(
            "回答で、当たった chunk の章節（章節ナビゲーション）から親の章節へ段階的に広げて"
            "文脈に入れる（#1436。既定 ON）。1 段上げる前に CRAG の十分性の判定で足りるかを"
            "確かめ、足りれば止める（段ごとに LLM の呼び出しが 1 回増える）。回答の生成方式が"
            " CRAG のときだけ効く。検索・回答プロファイルの section_expansion_enabled で選ぶ。"
        ),
    )
    rag_section_expansion_max_levels: int = Field(
        default=4,
        ge=1,
        le=8,
        description="章節の拡張で上がる段の上限（#1436。既定 4。所有の章節を 1 段目に数える）。",
    )
    rag_section_expansion_input_token_percent: int = Field(
        default=50,
        ge=10,
        le=90,
        description=(
            "章節の拡張で、1 回の回答の根拠の入力 token の上限にする、回答のモデルの入力の上限"
            "（システム設定 › モデル の登録モデルの「入力の上限（tokens）」。#1437）に対する"
            "割合（%。#1436。既定 50）。"
        ),
    )
    rag_section_extraction_max_calls: int = Field(
        default=8,
        ge=1,
        le=16,
        description=(
            "章節の拡張で根拠の入力の上限に入らない章節から、質問に関係する原文を抜き出す LLM の"
            "呼び出しの、1 回の回答の上限（#1437。既定 8）。上限で読めなかった範囲は「確かめられて"
            "いない点」に示し、条件つきの回答にする。検索・回答プロファイルの "
            "section_extraction_max_calls で選ぶ。"
        ),
    )
    rag_max_anchors_per_document: int = Field(
        default=5,
        ge=0,
        le=20,
        description=(
            "rerank の後、回答の根拠の起点(上位 top_k 件)に採る同じ文書の chunk の上限(#2167。"
            "既定 5、0 は上限なし)。超えた chunk は落とさず後ろへ回し、別の文書の関連する chunk を"
            "起点に入れる。検索・回答プロファイルの max_anchors_per_document で選ぶ。"
        ),
    )
    rag_section_expansion_time_limit_seconds: float = Field(
        default=600.0,
        ge=60.0,
        le=OCI_ENTERPRISE_AI_TIMEOUT_MAX_SECONDS,
        description=(
            "章節の拡張を終える、回答の開始からの秒数（#1436。既定 600）。回答の通しの上限"
            "（rag_answer_timeout_seconds）の早いほうから、生成の分（270 秒。"
            "生成と監査の 1 組の見積もりと回答の確定の分。#1516）を残して止める。"
        ),
    )
    rag_navigation_summary_enabled: bool = Field(
        default=False,
        description=(
            "取込時に navigation tree の各章節 node を OCI Enterprise AI LLM で要約し、"
            "progressive disclosure / Navigate retrieval に使う（既定 OFF）。"
        ),
    )
    rag_navigation_summary_max_nodes: int = Field(
        default=24,
        ge=1,
        le=200,
        description="navigation node 要約を生成する node 数の上限（LLM 呼び出し回数の bound）。",
    )
    rag_field_extraction_enabled: bool = Field(
        default=True,
        description=(
            "取込時に field schema 定義に従い OCI Enterprise AI structured output で named "
            "field/entity を抽出する（PoweRAG/LangExtract 由来。既定 ON。項目の定義が 0 件の"
            "ときは何もしない。#537）。"
        ),
    )
    rag_min_similarity: float = Field(default=0.05, ge=0.0, le=1.0)
    rag_rrf_k: int = Field(
        default=60,
        ge=1,
        le=1000,
        description="Hybrid retrieval の Reciprocal Rank Fusion 定数。",
    )
    rag_approved_faq_semantic_enabled: bool = Field(
        default=True,
        description=(
            "Approved FAQ(類似問)の照合に embedding の意味類似度を加える(rag_poc と同じ既定 ON)。"
        ),
    )
    rag_approved_faq_chat_min_score: float = Field(
        default=0.75,
        ge=0.0,
        le=1.0,
        description=(
            "チャットで類似問を提示する一致度の下限(0〜1。#684 / #709)。低いと弱い候補が毎回出て、"
            "利用者に毎回「どれでもない」を選ばせることになる。"
        ),
    )
    rag_approved_faq_chat_max_gap: float = Field(
        default=0.1,
        ge=0.0,
        le=1.0,
        description=(
            "チャットで類似問を提示するとき、1 位の一致度からこの差より離れた候補を出さない(#709)。"
            "1 位とほぼ同じくらい近い候補だけを並べ、遠い 2・3 位で迷わせない。"
        ),
    )
    # 回答は rag_poc の根拠付き回答(質問ルーティング / CRAG / 生成 + 監査ラウンド)だけ
    # にした(#594)。回答エンジンの選択(旧 RAG_ANSWER_ENGINE)は読まない。
    rag_answer_vision_enabled: bool = Field(
        default=True,
        description=(
            "画像を見て答える必要がある質問では、根拠の図を切り出して既定の Vision モデルへ添付して"
            "回答する(それ以外は既定のテキストモデル。Vision モデルが未設定なら添付しない。#649)。"
        ),
    )
    rag_history_rewrite_enabled: bool = Field(
        default=True,
        description=(
            "チャットで回答するとき、会話履歴から最新の質問を"
            "単独の質問へ書き換える(履歴がある場合だけ LLM 呼び出しが 1 回増える)。"
        ),
    )
    rag_query_strategy: QueryStrategy = Field(
        default="auto_routing",
        description="質問の拡張(query rewriting / expa"
        "nsion)の方式。検索・回答プロファイルで上書きできる。",
    )
    rag_answer_flow: AnswerFlow = Field(
        default="crag",
        description=(
            "回答の生成方式。crag(CRAG)は検索結果を評価して必要なら補正検索し、"
            "standard_rag(標準 RAG)は補正しない。"
        ),
    )
    rag_neighbor_child_count: int = Field(
        default=3,
        ge=0,
        le=20,
        description="回答で、根拠の child の前後から context へ足す近傍 child 数。",
    )
    rag_rerank_enabled: bool = Field(
        default=True,
        description="回答で、検索候補を OCI Generative AI の rerank で並べ替える。",
    )
    rag_screen_linking_enabled: bool = Field(
        default=False,
        description=(
            "回答で、検索範囲の画面目録(文書ごとの番号付きの見出し)から質問を解決する"
            "操作画面を LLM で選び、その画面の根拠を検索候補に加える"
            "(LLM の呼び出しが 1 回増える。#554)。"
            "検索・回答プロファイルで上書きできる。"
        ),
    )
    rag_request_coverage_retrieval_enabled: bool = Field(
        default=True,
        description=(
            "回答の生成の前に、質問の要求ごとに根拠があるかを語の一致で確かめ(LLM は呼ばない)、"
            "根拠の無い要求だけを要求の文で 1 回ずつ検索し直して回答の材料に足す(#1279)。"
            "検索・回答プロファイルで上書きできる。"
        ),
    )
    rag_request_coverage_max_queries: int = Field(
        default=2,
        ge=0,
        le=5,
        description="根拠の無い要求の再検索の回数の上限(1 要求 1 回。#1279)。",
    )
    rag_request_coverage_max_chunks: int = Field(
        default=4,
        ge=0,
        le=12,
        description="根拠の無い要求の再検索で回答の材料に足す根拠(親 chunk)の数の上限(#1279)。",
    )
    rag_request_coverage_deadline_seconds: float = Field(
        default=10.0,
        ge=0,
        le=60,
        description=("根拠の無い要求の再検索の期限(秒)。過ぎたら次の要求の検索を始めない(#1279)。"),
    )
    rag_strategy_reasoning_effort: ReasoningEffortOverride = Field(
        default="model",
        description=(
            "質問拡張戦略の判定と検索文の生成の LLM の呼び出しの推論の量(#1499。既定 model = "
            "モデルの登録値。low では戦略の選択がぶれ、検索文 0 本の単純検索に倒れることがあった)。"
        ),
    )
    rag_judge_reasoning_effort: ReasoningEffortOverride = Field(
        default="medium",
        description=(
            "根拠の評価(CRAG の評価と章節の拡張の十分性の判定)の LLM の呼び出しの推論の量"
            "(#1499。既定 medium。#1550)。model はモデルの登録値のまま。モデルの登録値が none の"
            "モデルには送らない。"
        ),
    )
    rag_generation_reasoning_effort: ReasoningEffortOverride = Field(
        default="model",
        description=(
            "回答文の生成の LLM の呼び出しの推論の量(#1499。既定 model = モデルの登録値)。"
            "監査は RAG_AUDIT_REASONING_EFFORT(#1528)。"
        ),
    )
    rag_audit_reasoning_effort: ReasoningEffortOverride = Field(
        default="model",
        description=(
            "生成後の監査(主張と引用の適用性の確認)の LLM の呼び出しの推論の量(#1528。"
            "既定 model = モデルの登録値)。"
        ),
    )
    rag_crag_skip_on_strong_signals: bool = Field(
        default=False,
        description=(
            "規則の信号が強いとき CRAG の根拠の評価(1 回目の LLM の呼び出し)を飛ばし、"
            "曖昧な帯だけで呼ぶ(#1499・#1528。既定 OFF)。rerank の最上位の score が閾値以上で、"
            "質問の要求に語の一致で根拠の無いもの(missing)が無く、定義を問う語の定義本文が"
            "あるときだけ飛ばす。"
            "エラー文・列挙・網羅・件数を問う質問(#1503)と、候補の除外が要る根拠(旧版の文書・"
            "別の文書と同じ本文)があるときは必ず呼ぶ。生成後の監査は飛ばさない。"
        ),
    )
    rag_crag_compact_input: bool = Field(
        default=True,
        description=(
            "CRAG の根拠の評価の入力を詰める(#1528。既定 ON。A/B で入力 tokens が"
            " 1/4〜1/9)。候補を生成に渡す親の数にそろえ、JSON を"
            "空白なしで書き、候補の ID を chunk_uid だけにする。章節の拡張の判定は候補の数を"
            "そろえない(広げた章節を見せる)。"
        ),
    )
    rag_crag_short_candidate_ids: bool = Field(
        default=True,
        description=(
            "(既定 ON。#1864)CRAG の根拠の評価の詰めた入力(rag_crag_compact_input)で、候補の"
            " chunk_uid を短い ID(C1・C1-1)にする。評価器は候補ごとの判定・観点の根拠・回復の起点に"
            "ID を書き写すので、長い chunk_uid(文書 ID の 32 桁と chunk の名前)が出力の token と"
            "時間を増やしていた。返った ID は chunk_uid に戻すので、判定の働きは変わらない。"
        ),
    )
    rag_answer_audit_compact_reasons: bool = Field(
        default=False,
        description=(
            "(既定 OFF。#1912)生成後の監査の reason を、判定が通った説明"
            "(supported かつ matched)と答えている要求(addressed)では空文字にさせる。"
            "監査の見える出力は約 5 割減るが、同じ入力の対の比較で監査が甘くなった"
            "(通らなかった判定が 5 件 → 1 件)ため既定では使わない。品質評価の比較用。"
        ),
    )
    rag_answer_max_correction_rounds: int = Field(
        default=2,
        ge=0,
        le=2,
        description=(
            "回答文の生成と根拠確認の是正の組(作り直し)の回数の上限(#1912。0〜2。"
            "既定 2 は従来と同じ)。是正 1 組は生成と監査の LLM の呼び出しを 1 回ずつ足す"
            "(#1864 の記録で 1 組 5〜63 秒)。"
            "0 は作り直さず、1 組目の検査と監査の結果で確定する。"
        ),
    )
    rag_llm_stall_detection_enabled: bool = Field(
        default=True,
        description=(
            "(#1926)回答(検索の回答・チャット)の LLM の呼び出しを streaming で受け、"
            "停滞した呼び出しを打ち切って同じ要求を送り直す。OCI Enterprise AI の呼び出しは"
            "まれ(記録の約 1%)に通常の 1/10 以下の速さ(13〜23 token/秒。中央値 224)に落ち、"
            "同じ token 数で 36 秒 → 183 秒かかった。"
            "判定の基準は同じ接続・モデルの直近の完了した呼び出し(3 回以上)から学ぶ。"
            "回答の期限の無い呼び出し(取込など)は従来どおり streaming しない。"
        ),
    )
    rag_llm_stall_idle_seconds: float = Field(
        default=20.0,
        ge=5.0,
        le=120.0,
        description=(
            "(#1926)応答の event(最初の event を含む)がこの秒だけ来なければ停滞として打ち切る。"
            "直近の呼び出しの最大の間隔の 3 倍が長ければそちら(推論を stream しないモデル)。"
            "記録では最初の event まで 0.1〜0.2 秒、event の間隔は 1 秒未満。"
        ),
    )
    rag_llm_stall_min_rate_ratio: float = Field(
        default=0.25,
        gt=0.0,
        le=0.9,
        description=(
            "(#1926)直近 10 秒の出力の速さ(delta の event/秒)が、直近の完了した呼び出しの"
            "速さの中央値のこの割合を下回れば停滞として打ち切る。"
            "記録の 10 パーセンタイルは中央値の約 0.65、遅い呼び出しは約 0.06〜0.1。"
        ),
    )
    rag_llm_stall_resends: int = Field(
        default=1,
        ge=0,
        le=2,
        description=(
            "(#1926)停滞で打ち切って送り直す回数の上限(LLM の呼び出し 1 回あたり)。"
            "最後の送信は停滞を検出せずに回答の期限まで待つ。"
            "0 は打ち切らない(streaming の時刻の記録だけ)。"
        ),
    )
    rag_section_skip_regrade_at_scope: bool = Field(
        default=True,
        description=(
            "(既定 ON。#1528 の A/B で列挙の問が 484 → 293 秒、品質は同じ)"
            "読む範囲が文書全体の列挙の問(#1503)で、章節の拡張が文書全体まで読んだら、"
            "CRAG の評価し直しを省いて生成へ進む(#1528)。"
        ),
    )
    rag_crag_skip_min_rerank_score: float = Field(
        default=0.8,
        ge=0.0,
        le=1.0,
        description="CRAG の評価を飛ばす rerank の最上位の score の閾値(#1499。0〜1)。",
    )
    rag_query_expansion_max_queries: int = Field(
        default=5,
        ge=1,
        le=5,
        description=(
            "質問拡張で LLM が作る検索文の本数の上限(#1499。既定は従来と同じ 5)。"
            "原質問・用語の別名・"
            "語彙の補助の検索文は数えない。"
        ),
    )
    rag_auto_field_filter_enabled: bool = Field(
        default=False,
        description=(
            "質問に書かれた条件(「2025 年以降」「10 万円以"
            "上」など)を、検索・回答プロファイルの KB で定義した"
            "抽出項目の条件として LLM で読み取り、検索を絞り込む(self-query。LLM の呼び出しが"
            " 1 回増える。読み取った条件で 0 件なら条件を外して検索し直す。#652)。"
            "検索・回答プロファイルで上書きできる。"
        ),
    )
    rag_answer_record_retention_days: int = Field(
        default=90,
        ge=0,
        le=3650,
        description=(
            "回答の記録の保持日数。0 は無期限。回答保存時と設定変更時に期限切れを削除する。"
        ),
    )
    rag_query_history_enabled: bool = Field(
        default=False,
        description=(
            "回答に成功した質問を検索・回答プロファイル単位で保存し、よく聞かれる質問を候補に出す"
            "(rag_poc の QUERY_HISTORY_ENABLED と同じく既定は無効)。"
        ),
    )
    rag_query_history_retention_days: int = Field(default=90, ge=0, le=3650)
    rag_query_history_min_count: int = Field(default=3, ge=1, le=1000)
    rag_query_history_suggestion_limit: int = Field(default=5, ge=1, le=20)
    rag_query_history_blocklist: list[str] = Field(
        default_factory=list,
        description="質問履歴に記録・提示しない語(部分一致)。env は JSON 配列で指定する。",
    )
    rag_answer_profile: Literal["generic", "legacy"] = Field(
        default="legacy",
        description=(
            "回答フローの業務 profile。回答フローは rag_engine の current_profile()"
            "(runtime なしの既定 = legacy: 日本語問い合わせ規則を有効、業務分類・別名は"
            " RAG_ENGINE_DOMAIN_PROFILE_FILE の JSON)で動き、rag_poc と同じ挙動になる。"
            "既定はこの実際の挙動に合わせて legacy(#300)。generic は既存の .env との互換のため"
            "受け付けるが、回答フローには反映されない。"
        ),
    )
    rag_runtime_knowledge: dict[str, object] = Field(
        default_factory=dict,
        description=(
            "リクエスト単位で検索・回答プロファイルから解決する用語・ルール"
            "(rag_poc runtime knowledge payload)。"
            "回答フローだけが使う。"
        ),
    )
    rag_support_guide: dict[str, object] = Field(
        default_factory=dict,
        description=(
            "リクエスト単位で検索・回答プロファイルの公開した業務ガイドから選んだ 1 つの要約"
            "（guide_id・版・判断・既知 / 不明の条件・確認の質問。#1238）。回答フローだけが使う。"
        ),
    )
    rag_guide_preview: dict[str, object] = Field(
        default_factory=dict,
        description=(
            "リクエスト単位で、業務ガイドの下書きで試す回答の印（guide_id・下書きの版。#1288）。"
            "回答の記録に残し、利用者の回答の履歴・質問の履歴に入れない。"
        ),
    )
    rag_language_pack_overrides: dict[str, dict[str, object]] = Field(
        default_factory=dict,
        description=(
            "リクエスト単位で検索・回答プロファイルから解決する言語資源の上書き（検索・回答の項目だけ。"
            "#2181）。回答の要求の間だけ全体の言語資源に重ねて束ねる。"
        ),
    )
    rag_search_answer_profile_revision: dict[str, object] = Field(
        default_factory=dict,
        description=(
            "リクエスト単位で解決した検索・回答プロファイルの版（id・更新時刻・設定の sha256。"
            "#1276）。回答の記録の provenance に残すだけで、検索・回答の挙動は変えない。"
        ),
    )
    rag_domain_keywords: list[str] = Field(
        default_factory=list,
        description=(
            "リクエスト単位で検索・回答プロファイルから解決するドメインキーワード。"
            "全文検索で分割せず 1 語として優先する。"
        ),
    )
    rag_embedding_cache_enabled: bool = Field(
        default=True,
        description=(
            "同一 process 内で OCI Generative AI embedding 結果を LRU cache する。"
            "cache key は本文 hash と model/input_type/dimension だけで構成する。"
        ),
    )
    rag_embedding_cache_max_entries: int = Field(default=4096, ge=0, le=200000)
    rag_embedding_batch_size: int = Field(
        default=96,
        ge=1,
        le=96,
        description=(
            "OCI Generative AI embedding へ 1 回に送る text 数(最大 96)。"
            "大きな文書取込や query expansion で API payload を過大化しない。"
        ),
    )
    rag_rerank_cache_enabled: bool = Field(
        default=True,
        description=(
            "同一 process 内で OCI Generative AI rerank 結果を LRU cache する。"
            "cache key は query/document hash と model/top_n だけで構成する。"
        ),
    )
    rag_rerank_cache_max_entries: int = Field(default=1024, ge=0, le=100000)
    rag_answer_timeout_seconds: float = Field(
        default=600.0,
        gt=0.0,
        le=OCI_ENTERPRISE_AI_TIMEOUT_MAX_SECONDS,
        description=(
            "LLM を呼ぶ回答生成（チャット・RAG 検索の回答。ストリーム・非ストリーム・MCP。"
            "品質評価の 1 ケース）の通しの上限（秒。#375 / #383）。検索の計画・追加の検索の再分解・"
            "回答の生成で LLM を複数回呼ぶ。上限は LLM 1 回の timeout の設定の上限と同じ。"
            "既定は上限と同じ 600 秒（推論の量の既定 medium で、推論型のモデルの 1 回の呼び出しが"
            "数十秒になるため。#1425）。画面（630 秒）と Nginx（660 秒）の待ち時間は"
            "これより長くする。"
        ),
    )
    db_read_timeout_seconds: float = Field(
        default=8.0,
        gt=0.0,
        le=60.0,
        description=(
            "閲覧系一覧/集計 API（ドキュメント・取込ジョブ・ナレッジベース）の DB 待機秒数。"
            "DB 停止時に 500 ではなく空データ + warning で縮退応答するための上限。"
        ),
    )
    rag_pdf_segmentation_enabled: bool = Field(
        default=True,
        description=(
            "PDF をページの段（rag_pdf_max_pages_per_segment ページ）に分け、段ごとに"
            "解析サービス（Docling など）を呼ぶ。段ごとに状態・進捗・抽出結果を残し、"
            "失敗・中断の後は成功した段を解析し直さない（#2001）。"
        ),
    )
    rag_pdf_max_pages_per_segment: int = Field(default=10, ge=1, le=50)
    rag_pdf_max_segments: int = Field(default=300, ge=1, le=2000)
    rag_pdf_segment_parse_concurrency: int = Field(
        default=1,
        ge=1,
        le=4,
        description=(
            "PDF の段を同時に解析する数（#2001）。既定は 1（順番）。解析サービスは段ごとにページを"
            "描画して解析するため、上げるときは解析サービスの CPU・メモリと worker の数を確かめる。"
        ),
    )
    # 取込の AI の呼び出しの並行と再試行（#2002。共通の部品 pr_backend_core.bounded_calls）。
    rag_vision_concurrency: int = Field(
        default=4,
        ge=1,
        le=16,
        description=(
            "図・表の説明（Vision）の VLM を同時に呼ぶ数の上限（#2002）。429 を受けると自動で"
            "減らし（最小 1）、成功が続くとこの上限まで戻す。同じページの図は順番に読む。"
        ),
    )
    rag_navigation_summary_concurrency: int = Field(
        default=4,
        ge=1,
        le=16,
        description="ナビゲーション要約の LLM を同時に呼ぶ数の上限（#2002）。",
    )
    rag_embedding_concurrency: int = Field(
        default=2,
        ge=1,
        le=8,
        description=(
            "Embedding の要求（最大 96 件の batch）を同時に送る数の上限（#2002）。1 つの要求が"
            "大きいため、LLM・VLM より小さい既定にする。"
        ),
    )
    rag_evaluation_concurrency: int = Field(
        default=2,
        ge=1,
        le=8,
        description=(
            "品質評価で同時に実行するケースの数の上限（#2004）。1 ケースの往復と判定は順に行い、"
            "結果はケースの順のまま。回答・判定が 429 で失敗したら評価の全体で待って並行の数を"
            "下げ（最小 1）、送り直す。1 は 1 件ずつ（#2004 より前と同じ）。"
        ),
    )
    rag_ai_call_backoff_max_seconds: float = Field(
        default=60.0,
        ge=1.0,
        le=600.0,
        description=(
            "AI の呼び出しの再試行の待ち（指数の backoff と full jitter）の上限（秒。#2002）。"
        ),
    )
    rag_ai_call_final_retry_delay_seconds: float = Field(
        default=30.0,
        ge=0.0,
        le=600.0,
        description=(
            "再試行を使い切った件を、段の最後にまとめてもう一度呼ぶ前の待ちの秒数（#2002）。"
        ),
    )
    rag_ai_call_fail_job_on_item_error: bool = Field(
        default=False,
        description=(
            "最後の再試行でも失敗した件があれば取込を失敗にする（#2002）。既定は偽で、"
            "その件を「説明できなかった」などとして記録し、取込を続ける。"
        ),
    )
    rag_guardrail_policy: GuardrailPolicyName = Field(
        default="standard",
        description=(
            "安全の Guardrail アダプター。standard(既定)は現行フラグ、"
            "strict/regulated は groundedness 厳格化、lenient は warning 抑制。"
        ),
    )
    rag_guardrail_backend: GuardrailBackend = Field(
        default="local",
        description=(
            "Guardrail のバックエンド。local(既定)は in-process 決定論ヒューリスティック"
            "(現行挙動)。oci_guardrails は OCI Generative AI Guardrails(ApplyGuardrails、"
            "content moderation / PII / prompt injection の検出専用 API)を併用する。障害時は"
            "regulated が拒否し、その他は warning 付きで local へ縮退する。確定スタックは不変"
            "(別 LLM provider・外部 DB は不採用)。"
        ),
    )
    oci_guardrails_compartment_id: str = Field(
        default="",
        description="OCI Guardrails の compartment OCID。空欄時は oci_compartment_id を使う。",
    )
    oci_guardrails_endpoint: str = Field(
        default="",
        description="OCI Generative AI Inference のサービスエンドポイント上書き(空欄は SDK 既定)。",
    )
    oci_guardrails_prompt_injection_threshold: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="prompt injection の risk score をブロック扱いにする閾値(0.0–1.0)。",
    )
    oci_guardrails_timeout_seconds: float = Field(
        default=5.0,
        gt=0.0,
        le=30.0,
        description="OCI Guardrails 検査の接続・読取 timeout 秒。",
    )
    rag_evaluation_job_timeout_seconds: int = Field(
        default=3600,
        ge=60,
        le=RAG_EVALUATION_JOB_TIMEOUT_MAX_SECONDS,
        description=(
            "品質評価の job（golden set の評価。#390）の全体の時間の上限の最小値（秒）。"
            "job の上限は、投入時の回答の回数（ケース × 往復）× "
            "rag_answer_timeout_seconds とこの値の大きいほうで、86400 秒を超えない（#1430）。"
            "上限に達したら実行中のケースを打ち切り、残りのケースは実行せずに失敗として記録して結果を返す。"
            "1 回の回答の上限は rag_answer_timeout_seconds。"
        ),
    )
    rag_evaluation_suite: EvaluationSuite = Field(
        default="standard",
        description=(
            "品質評価の基準(閾値のプリセット。#591)。standard(標準。既定)/ strict(厳格)。"
            "request の thresholds が最優先。閾値は、そのケースの集合で測れた指標だけに適用する。"
        ),
    )
    rag_graph_profile: GraphProfile = Field(
        default="off",
        description=(
            "関係情報の構築。off(既定)は構築しない、entities は文書と章・節の見出しのつながりを"
            "構築する(LLM は使わない)。ナレッジベースの関係情報グラフで見られ、"
            "rag_graph_expansion_enabled を ON にすると回答の検索の候補を広げるのにも使う(#2055)。"
        ),
    )
    rag_vector_index_profile: VectorIndexProfile = Field(
        default="accurate",
        description=(
            "索引/検索精度の Vector Index アダプター。balanced は"
            "RAG_ORACLE_VECTOR_TARGET_ACCURACY をそのまま使い、accurate(既定。#272)は高再現(98)、"
            "fast は低レイテンシ(85)へ検索時 target accuracy を上書きする。"
            "推奨 HNSW ビルドパラメータは設定画面に表示し、適用には索引再作成が必要。"
        ),
    )
    # --- 前処理(Preprocess)ステージ(parse の前の原本変換)---
    rag_preprocess_profile: PreprocessProfile = Field(
        default="passthrough",
        description=(
            "parse の前に原本を一度だけ canonical な中間物へ変換する前処理プリセット。"
            "passthrough(既定)は変換せず現行挙動と一致。office_to_pdf は Office→PDF、"
            "pdf_to_page_images は PDF→ページ画像、csv_to_json は CSV→構造化 JSON、"
            "excel_to_json は Excel(.xls/.xlsx)→構造化 JSON、pii_redact は取込時の PII マスク"
            "(Presidio + 日本語 NER、外部 SaaS 非使用)"
            "(いずれも各々独立した前処理マイクロサービス)。"
        ),
    )
    rag_preprocess_enabled: bool = Field(
        default=False,
        description=(
            "前処理マイクロサービスへの HTTP 委譲を有効化する。OFF(既定)は in-process で "
            "扱える profile(passthrough)のみ実行し、サービス必須の変換は "
            "passthrough へ安全に縮退する。"
        ),
    )
    rag_preprocess_office_to_pdf_service_url: str = Field(
        default="http://127.0.0.1:18010",
        description="Office→PDF 前処理マイクロサービスの base URL。",
    )
    rag_preprocess_pdf_to_page_images_service_url: str = Field(
        default="http://127.0.0.1:18011",
        description="PDF→ページ画像PDF 前処理マイクロサービスの base URL。",
    )
    rag_preprocess_csv_to_json_service_url: str = Field(
        default="http://127.0.0.1:18012",
        description="CSV→構造化 JSON 前処理マイクロサービスの base URL。",
    )
    rag_preprocess_excel_options: ExcelOptions = Field(
        default_factory=ExcelOptions,
        description=(
            "前処理 excel_to_json の選択肢(#1221。読み方 auto/table/procedure・表頭の行と行数・"
            "読むシート・読まないシート・非表示のシート・読まない列・列の役割の判定と指定(#1281))。"
            "文書レシピの excel_options で"
            '上書きできる。環境変数は JSON(例: {"mode": "table", "header_row": 2})。'
        ),
    )
    rag_preprocess_excel_to_json_service_url: str = Field(
        default="http://127.0.0.1:18013",
        description="Excel(.xls/.xlsx)→行の記録 JSON 前処理マイクロサービスの base URL。",
    )
    rag_preprocess_pii_redact_service_url: str = Field(
        default="http://127.0.0.1:18016",
        description="PII マスク(取込時)前処理マイクロサービスの base URL。",
    )
    rag_preprocess_service_timeout_seconds: float = Field(
        default=300.0,
        gt=0,
        description=(
            "前処理マイクロサービス呼び出しの HTTP timeout(秒)。"
            "超過・接続失敗時は warning を付けて passthrough(原本そのまま parse)へ縮退する。"
        ),
    )
    rag_canonical_artifact_prefix: str = Field(
        default="artifacts/canonical",
        max_length=256,
        description="前処理で生成した正規化原本(canonical source)の Object Storage key prefix。",
    )
    rag_parser_adapter_backend: ParserAdapterBackend = Field(
        default=DEFAULT_PARSER_ADAPTER_BACKEND,
        description=(
            "文書解析 backend の明示選択。既定は Docling(#286)。Unstructured は明示選択で使う。"
            "Docling/Unstructured/XML ツリー JSON/各 OCR/Document Understanding は対応 parser "
            "マイクロサービスまたは API へ HTTP 委譲する。"
            "in-process 解析・local fallback は持たない。"
        ),
    )
    rag_parser_docling_enabled: bool = Field(
        default=True,
        description=(
            "Docling adapter を有効化する。既定 backend のため既定で True。"
            "取込時は parser-docling マイクロサービスの常時起動が前提。"
        ),
    )
    rag_vision_enabled: bool = Field(
        default=True,
        description=(
            "文書解析の後に図・画像を OCI Enterprise AI の VLM で読み取り、図の要素の本文を"
            "説明文にする(全ての解析エンジン。Docling は解析サービスの中で読み取る)。"
            "全体の既定は backend/.env で決め(文書解析の画面の「解析後の処理」から"
            "保存できる。#528)、文書のレシピで上書きする。既定 ON(#537)。"
            "画像 1 枚ごとに VLM を呼ぶ。"
        ),
    )
    rag_parser_unstructured_enabled: bool = Field(
        default=False,
        description=(
            "Unstructured adapter を有効化する。既定 backend ではないため既定で False"
            "(画面で Unstructured を選ぶと有効になる)。選択時は parser-unstructured の起動が前提。"
        ),
    )
    rag_parser_mineru_enabled: bool = Field(
        default=False,
        description="外部 MinerU native API adapter を feature flag で有効化する。",
    )
    rag_parser_dots_ocr_enabled: bool = Field(
        default=False,
        description="外部 Dots.OCR OpenAI 互換 API adapter を feature flag で有効化する。",
    )
    rag_parser_xml_tree_json_enabled: bool = Field(
        default=False,
        description=(
            "XML ツリー JSON adapter を有効化する(#2024・#2042)。既定 backend ではないため既定で"
            " False(画面で XML ツリー JSON を選ぶと有効になる)。選択時は parser-xml-tree-json の"
            "起動が前提。"
        ),
    )
    rag_parser_docling_service_url: str = Field(
        default="http://127.0.0.1:18020",
        description="Docling parser マイクロサービスの base URL。",
    )
    rag_xml_tree_json_source_url_template: str = Field(
        default="",
        description=(
            "XML ツリー JSON の文書の原典の URL のひな形(#2024)。``{document_id}`` を文書 ID に"
            "置き換え、引用カードの「原典を開く」と根拠の metadata(xml_tree_source_url)に使う。"
            "空なら原典の URL を付けない。"
        ),
    )
    rag_parser_xml_tree_json_service_url: str = Field(
        default="http://127.0.0.1:18021",
        description="XML ツリー JSON の parser マイクロサービスの base URL(#2024)。",
    )
    rag_parser_unstructured_service_url: str = Field(
        default="http://127.0.0.1:18022",
        description="Unstructured parser マイクロサービスの base URL。",
    )
    # GPU OCR は外部で運用済みの native API を呼ぶ。旧 *_SERVICE_URL(/parse wrapper)
    # は読まず、誤った protocol への自動移行を避ける。
    rag_parser_mineru_api_host: str = Field(
        default="", description="MinerU 4.x の V1 API（/v1/parse/jobs）の base URL。"
    )
    rag_parser_mineru_api_key: str = Field(default="", repr=False)
    # MinerU 4.0 は解析の方式（pipeline / vlm / hybrid）を品質の tier にまとめた（#1329）。
    # OCR の言語は MinerU の API サーバーの起動時の設定で、V1 の request には無い。
    rag_parser_mineru_tier: MineruTier = Field(
        default="basic",
        description=(
            "MinerU の解析の品質（flash / basic / standard / advanced）。basic は小さな"
            "モデルで CPU でも動く（3.x の pipeline 相当）。"
            "PDF・画像以外は MinerU が flash だけを持つ。"
        ),
    )
    rag_parser_mineru_job_timeout_seconds: float = Field(
        default=1800.0,
        gt=0,
        le=86400,
        description=(
            "MinerU の parse job を終了まで待つ上限（秒）。超えたら job を取り消して縮退する。"
        ),
    )
    rag_parser_dots_ocr_api_host: str = Field(
        default="", description="Dots.OCR OpenAI 互換 API の base URL。"
    )
    rag_parser_dots_ocr_model: str = Field(
        default="rednote-hilab/dots.mocr", description="Dots.OCR の model ID。"
    )
    rag_parser_dots_ocr_api_key: str = Field(default="", repr=False)
    rag_parser_dots_ocr_dpi: int = Field(default=200, ge=72, le=600)
    rag_parser_dots_ocr_pdf_workers: int = Field(default=4, ge=1, le=16)
    rag_parser_asr_enabled: bool = Field(
        default=True,
        description=(
            "音声/動画の文字起こし(OCI AI Speech)を有効化する。OFF にすると音声は未対応として扱う。"
        ),
    )
    rag_parser_oci_document_understanding_service_url: str = Field(
        default="http://127.0.0.1:18028",
        description=(
            "OCI Document Understanding parser マイクロサービスの base URL。"
            "OCI を呼ぶ薄いプロキシで、認証はメイン設定(OCI env)を継承する。"
        ),
    )
    rag_parser_service_timeout_seconds: float = Field(
        default=300.0,
        gt=0,
        description=(
            "parser マイクロサービス呼び出しの HTTP timeout(秒)。"
            "超過・接続失敗時は warning を付けて local/Enterprise AI fallback へ縮退する。"
        ),
    )
    rag_http_service_retry_attempts: int = Field(
        default=5,
        ge=1,
        le=10,
        description="HTTP マイクロサービス呼び出しの最大試行回数。",
    )
    rag_http_service_retry_initial_delay_seconds: float = Field(
        default=0.5,
        ge=0.0,
        le=60.0,
        description="HTTP マイクロサービス retry の初期待機秒数。",
    )
    rag_http_service_retry_max_delay_seconds: float = Field(
        default=4.0,
        ge=0.0,
        le=300.0,
        description="HTTP マイクロサービス retry の最大待機秒数。",
    )
    # --- pipeline ステージのプラグイン(マイクロサービス)化 ---
    # 各 stage は remote サービスが使える場合だけ委譲し、未起動・未到達なら同一
    # rag_pipeline_core 実装を backend in-process で実行する。応答済みサービスの HTTP error /
    # 不正応答は壊れた remote として停止し、静かに隠さない。
    rag_pipeline_stage_timeout_seconds: float = Field(
        default=120.0,
        gt=0,
        description=(
            "pipeline ステージサービス呼び出しの HTTP timeout(秒)。"
            "サービス未起動・未到達時は backend in-process の同一実装へ縮退する。"
            "応答済みサービスの HTTP error / 不正応答は停止する。"
        ),
    )
    rag_chunking_service_enabled: bool = Field(
        default=True,
        description=(
            "chunking ステージの remote 委譲を許可する。ON でもサービス未起動・未到達時は "
            "backend in-process の同一実装へ縮退する。OFF は常に in-process。"
        ),
    )
    rag_chunking_service_url: str = Field(
        default="http://127.0.0.1:18030",
        description="chunking ステージマイクロサービスの base URL。",
    )
    rag_vector_index_service_enabled: bool = Field(
        default=True,
        description=(
            "vector_index プロファイル解決の remote 委譲を許可する。サービス未起動・未到達時は "
            "backend in-process の同一実装へ縮退する。OFF は常に in-process。"
        ),
    )
    rag_vector_index_service_url: str = Field(
        default="http://127.0.0.1:18031",
        description="vector_index ステージマイクロサービスの base URL。",
    )
    rag_graph_service_enabled: bool = Field(
        default=True,
        description=(
            "graphrag プロファイル解決の remote 委譲を許可する。サービス未起動・未到達時は "
            "backend in-process の同一実装へ縮退する。OFF は常に in-process。"
        ),
    )
    rag_graph_service_url: str = Field(
        default="http://127.0.0.1:18032",
        description="graphrag ステージマイクロサービスの base URL。",
    )
    rag_guardrail_service_enabled: bool = Field(
        default=True,
        description=(
            "guardrail の policy 解決(groundedness 閾値 + 監査強調)の remote 委譲を許可する。"
            "サービス未起動・未到達時は backend in-process の同一実装へ縮退する。"
            "OFF は常に in-process。"
            "OCI Generative AI Guardrails backend(rag_guardrail_backend)とは別レイヤーで共存。"
        ),
    )
    rag_guardrail_service_url: str = Field(
        default="http://127.0.0.1:18034",
        description="guardrail ステージマイクロサービスの base URL。",
    )
    rag_evaluation_service_enabled: bool = Field(
        default=True,
        description=(
            "evaluation の suite→閾値解決の remote 委譲を許可する。サービス未起動・未到達時は "
            "backend in-process の同一実装へ縮退する。OFF は常に in-process。"
        ),
    )
    rag_evaluation_service_url: str = Field(
        default="http://127.0.0.1:18037",
        description="evaluation ステージマイクロサービスの base URL。",
    )
    rag_raptor_enabled: bool = Field(
        default=False,
        description=(
            "RAPTOR 再帰要約索引: chunking 後に leaf chunk を再帰 cluster + OCI Enterprise AI で"
            "要約し、多層级 summary node を leaf と一緒に索引する。OFF(既定)は leaf のみ。"
            "追加 LLM 呼び出しを伴う opt-in。再試行のあとも要約できない cluster は飛ばす(leaf は"
            "残る)。権限・モデルの設定の誤り・割り当ての使い切りは取込を止める(#2067)。"
        ),
    )
    rag_raptor_cluster_size: int = Field(
        default=5,
        ge=2,
        le=50,
        description="RAPTOR の 1 cluster あたり chunk 数(要約単位)。",
    )
    rag_raptor_max_levels: int = Field(
        default=2,
        ge=1,
        le=5,
        description="RAPTOR 要約 tree の最大階層数。",
    )
    rag_raptor_concurrency: int = Field(
        default=4,
        ge=1,
        le=16,
        description=(
            "RAPTOR 要約の LLM を同時に呼ぶ数の上限（#2067。1 つの階層の cluster を並べ、階層は"
            "順に作る）。429 を受けると自動で減らし（最小 1）、成功が続くとこの上限まで戻す。"
        ),
    )
    rag_parser_readiness_probe_enabled: bool = Field(
        default=False,
        description=(
            "readiness 画面の adapter version/可用性を parser サービスの /health 問い合わせで "
            "解決する。OFF(既定)は backend プロセス内の import 検出にフォールバック(開発/テスト "
            "用)。本番(systemd の unit)では true にしてサービスの導入状況を表示する。"
        ),
    )
    rag_parser_readiness_probe_timeout_seconds: float = Field(
        default=2.0,
        gt=0,
        description="readiness の /health 問い合わせ timeout(秒)。",
    )
    # --- サービス管理（前処理 / Parser マイクロサービスの稼働可視化・起動/停止。#286）---
    rag_service_control_enabled: bool = Field(
        default=False,
        description=(
            "サービス管理画面からの起動/停止(systemd の unit の操作)を有効化する。"
            "OFF(既定)は稼働状態の可視化のみで、制御 API は 409(control_disabled)で拒否する。"
            "dev(RAG_ENVIRONMENT が prod 以外)は自動で有効。操作は sudoers で許可した "
            "allowlist の unit の `systemctl enable --now / disable --now / restart` と "
            "`journalctl -u <unit>` だけを `sudo -n` で実行する(rag/docs/deployment.md)。"
        ),
    )
    rag_service_control_timeout_seconds: float = Field(
        default=60.0,
        gt=0,
        description=(
            "systemctl / journalctl の subprocess の timeout(秒)。超過は失敗として構造化返却する。"
        ),
    )
    rag_service_runtime_env_file: str = Field(
        default=str(BACKEND_ROOT / "service-runtime.env"),
        description=(
            "マイクロサービスの unit が EnvironmentFile で読む実行用の env ファイル。"
            "backend が起動/再起動の前に HuggingFace 設定と実効 OCI Enterprise AI 設定"
            "(model-settings.json 由来を含む)を書く(0600)。unit 側の path と一致させる。"
        ),
    )
    rag_service_status_probe_timeout_seconds: float = Field(
        default=5.0,
        gt=0,
        description=(
            "サービス管理画面が各マイクロサービスの /health を問い合わせる timeout(秒)。"
            "接続拒否/timeout は stopped、到達したが status!=ok は degraded として表示する。"
        ),
    )
    # --- OCI Document Understanding（service 系 parser backend）---
    # 別 OCI サービス(oci.ai_document)。非同期 processor job で日本語 OCR/表抽出する。
    # 入出力は Object Storage 経由。未設定/失敗時は安全に既存フローへ縮退する。
    oci_document_understanding_compartment_id: str = Field(
        default="",
        description=(
            "OCI Document Understanding の compartment OCID。空のときは oci_compartment_id を使う。"
        ),
    )
    oci_document_understanding_namespace: str = Field(
        default="",
        description=(
            "DU 入出力 Object Storage の namespace。空のときは object_storage_namespace を使う。"
        ),
    )
    oci_document_understanding_object_storage_region: str = Field(
        default="",
        description=(
            "DU 入出力 Object Storage の region。空のときは PLATFORM_OCI_REGION、"
            "さらに空なら object_storage_region を使う。"
        ),
    )
    oci_document_understanding_input_bucket: str = Field(
        default="",
        description=(
            "DU 入力ファイルを置く Object Storage bucket。空のときは object_storage_bucket を使う。"
        ),
    )
    oci_document_understanding_output_bucket: str = Field(
        default="",
        description="DU 結果 JSON の出力先 bucket。空のときは入力 bucket を使う。",
    )
    oci_document_understanding_input_prefix: str = Field(
        default="document-understanding/input",
        max_length=256,
        description="DU 入力ファイルの Object Storage key prefix。",
    )
    oci_document_understanding_output_prefix: str = Field(
        default="document-understanding/output",
        max_length=256,
        description="DU 結果 JSON の Object Storage key prefix。",
    )
    oci_document_understanding_language: str = Field(
        default="ja",
        max_length=8,
        description="DU の言語ヒント(BCP 47。日本語は ja / ja-JP)。",
    )
    oci_document_understanding_features: list[str] = Field(
        default_factory=lambda: ["DOCUMENT_TEXT_EXTRACTION", "TABLE_EXTRACTION"],
        description="DU で要求する analysis feature 種別。",
    )
    oci_document_understanding_poll_interval_seconds: float = Field(
        default=5.0,
        gt=0,
        le=60.0,
        description="DU processor job の状態 poll 間隔(秒)。",
    )
    oci_document_understanding_timeout_seconds: float = Field(
        default=600.0,
        gt=0,
        le=3600.0,
        description="DU processor job 完了待ちの上限(秒)。超過時は安全に縮退する。",
    )
    # --- OCI AI Speech(音声文字起こし。空欄はローカル faster-whisper へ縮退)---
    oci_speech_compartment_id: str = Field(
        default="",
        description="OCI AI Speech の compartment OCID。空欄時は oci_compartment_id を使う。",
    )
    oci_speech_namespace: str = Field(
        default="",
        description="Speech 入出力 Object Storage の namespace。空欄は object_storage_namespace。",
    )
    oci_speech_input_bucket: str = Field(
        default="",
        description="Speech 入力 bucket。空欄は object_storage_bucket。",
    )
    oci_speech_output_bucket: str = Field(
        default="",
        description="Speech 出力 bucket。空欄は入力 bucket と同じ。",
    )
    oci_speech_input_prefix: str = Field(
        default="speech/input",
        description="Speech 入力 object の key prefix。",
    )
    oci_speech_output_prefix: str = Field(
        default="speech/output",
        description="Speech 出力 JSON の key prefix。",
    )
    oci_speech_language: str = Field(
        default="ja",
        description="文字起こしの言語コード(既定 日本語)。",
    )
    oci_speech_poll_interval_seconds: float = Field(
        default=5.0,
        gt=0,
        description="Speech transcription job の状態 poll 間隔(秒)。",
    )
    oci_speech_timeout_seconds: float = Field(
        default=900.0,
        gt=0,
        le=7200.0,
        description="Speech job 完了待ちの上限(秒)。超過時はローカル faster-whisper へ縮退。",
    )
    rag_segment_checkpoint_enabled: bool = Field(
        default=True,
        description="取込 segment checkpoint を Oracle に永続化し、失敗 segment の再試行に使う。",
    )
    rag_extraction_artifact_cache_enabled: bool = Field(
        default=True,
        description="構造化抽出 JSON artifact を chunk/embedding 前に Object Storage へ保存する。",
    )
    rag_extraction_artifact_prefix: str = Field(
        default="artifacts/extractions",
        max_length=256,
        description="構造化抽出 artifact の Object Storage key prefix。",
    )
    rag_review_gate_enabled: bool = Field(
        default=True,
        description=(
            "True のときファイル処理を段階レビュー式(EXTRACT→CHUNK→INDEX)にする。"
            "False は従来互換で EXTRACT job 内から最後まで進める。"
        ),
    )
    rag_auto_parse_after_preprocess_enabled: bool = Field(
        default=True,
        description="PREPROCESS 完了後に PREPROCESSED で止めず、EXTRACT を自動で続行する。",
    )
    rag_auto_chunk_after_extract_enabled: bool = Field(
        default=True,
        description="EXTRACT 完了後に REVIEW で止めず、CHUNK job を自動投入する。",
    )
    rag_auto_index_after_chunk_enabled: bool = Field(
        default=True,
        description="CHUNK 完了後に CHUNKED で止めず、INDEX job を自動投入する。",
    )

    # --- チャット（会話 / マルチモデル比較）---
    rag_chat_enabled: bool = Field(
        default=True,
        description=(
            "チャット(会話)機能の有効化。True で会話 API を提供し UI に項目を出す。"
            "False のとき会話 API は 404 を返す(運用キルスイッチ)。"
        ),
    )
    rag_chat_max_compare_models: int = Field(
        default=3,
        ge=1,
        le=5,
        description="マルチモデル比較で同時に回答生成する OCI モデルの最大数。",
    )
    rag_chat_max_active_answers_per_user: int = Field(
        default=3,
        ge=1,
        le=20,
        description=(
            "同じ利用者が同時に作成できるチャットの回答の数（backend のプロセスごと。#1175）。"
            "回答の作成は SSE の接続が切れても続くので、送信を重ねて LLM を使い過ぎないよう抑える。"
        ),
    )

    # --- レート制限（高コスト API の保護）---
    rate_limit_enabled: bool = Field(default=True)
    rate_limit_window_seconds: float = Field(default=60.0, gt=0.0, le=3600.0)
    rate_limit_search_requests: int = Field(default=60, ge=1, le=10000)
    rate_limit_evaluation_runs: int = Field(default=10, ge=1, le=1000)
    rate_limit_uploads: int = Field(default=30, ge=1, le=1000)
    rate_limit_ingest_requests: int = Field(default=20, ge=1, le=1000)

    # --- ガードレール ---
    guardrail_max_query_chars: int = Field(default=2000, ge=100, le=20000)
    guardrail_block_prompt_injection: bool = Field(default=True)
    guardrail_mask_sensitive_identifiers: bool = Field(default=True)

    # --- 監査 ---
    audit_context_hash_salt: str = Field(
        default="",
        description="tenant/user id を監査ログへ hash 化するときの任意 salt。.env から注入する。",
    )
    audit_persistence: AuditPersistence = Field(
        default="log",
        description="RAG 監査イベントの保存先。log / oracle / both。",
    )

    # --- Trace export（OpenTelemetry / Langfuse gateway 連携用）---
    trace_export_http_endpoint: str = Field(default="")
    trace_export_http_bearer_token: str = Field(default="")
    trace_export_timeout_seconds: float = Field(default=2.0, gt=0.0, le=30.0)
    trace_export_queue_size: int = Field(default=1024, ge=1, le=100000)

    @field_validator("model_settings_file")
    @classmethod
    def normalize_model_settings_file(cls, value: str) -> str:
        """空指定は共通 `.env` と同じ階層の既定ファイルへ戻す。"""
        return value.strip() or DEFAULT_MODEL_SETTINGS_FILE

    @field_validator("huggingface_endpoint")
    @classmethod
    def normalize_huggingface_endpoint(cls, value: str) -> str:
        """ミラー URL に scheme を補う。

        ``hf-mirror.com`` のように scheme なしで設定されると、HF hub の DL が
        ``httpx.UnsupportedProtocol``(URL missing 'http(s)://')で失敗する。
        scheme 省略時は ``https://`` を補う。空は空のまま(公式 hub 既定)。
        """
        endpoint = value.strip()
        if endpoint and "://" not in endpoint:
            return f"https://{endpoint}"
        return endpoint

    @field_validator("rag_parser_adapter_backend", mode="before")
    @classmethod
    def normalize_legacy_parser_adapter_backend(cls, value: object) -> object:
        """旧値・削除済みエンジン(#270)の正規化。

        ``local`` は **正規化しない**。advanced diagnostics(scorecard/staging golden gate)が
        「常時利用可能な baseline」概念として扱う。runtime では ingestion._partition_source が
        ``local`` を既定エンジン(``docling``)のサービスへマップし、in-process 解析は実行しない。
        """
        return normalize_parser_adapter_backend_value(value)

    @field_validator("rag_preprocess_profile", mode="before")
    @classmethod
    def normalize_legacy_preprocess_profile(cls, value: object) -> object:
        """削除した変換方式(text_normalize・#2042 の 2 つ)は起動互換のため passthrough へ寄せる。"""
        return normalize_removed_preprocess_profile(value)

    @field_validator("rag_chunking_strategy", mode="before")
    @classmethod
    def normalize_legacy_chunking_strategy(cls, value: object) -> object:
        """削除した分割方式(sentence_window / hierarchical_parent_child)を後継へ寄せる。"""
        return normalize_legacy_chunking_strategy_value(value)

    @field_validator("rag_evaluation_suite", mode="before")
    @classmethod
    def normalize_legacy_evaluation_suite(cls, value: object) -> object:
        """削除した評価の基準(#591)を後継へ寄せる(起動互換。未知の値は拒否する)。"""
        normalized = str(value).strip().casefold()
        return LEGACY_EVALUATION_SUITES.get(normalized, value)

    @field_validator("oci_enterprise_ai_vlm_input_mode", mode="before")
    @classmethod
    def normalize_legacy_settings_vlm_input_mode(cls, value: object) -> object:
        """廃止済み旧値は起動互換のため files_api へ寄せる。"""
        if str(value).strip().casefold() == "auto":
            return "files_api"
        return value

    @field_validator("rag_graph_profile", mode="before")
    @classmethod
    def reject_removed_graph_profile(cls, value: object) -> object:
        """削除した full(#621)は読み替えず、書き換え先を示して起動を止める。"""
        if str(value).strip().casefold() in REMOVED_GRAPH_PROFILES:
            raise ValueError(
                "RAG_GRAPH_PROFILE=full は廃止しました(#621)。backend/.env の値を entities"
                "(構築する)か off(構築しない)に書き換えてください。"
            )
        return value

    @field_validator(
        "ingestion_queue_worker_concurrency",
        "ingestion_queue_heavy_phase_concurrency",
        mode="before",
    )
    @classmethod
    def normalize_ingestion_queue_concurrency(cls, value: object) -> object:
        """未設定(空)と大文字小文字の違いを auto に寄せる(#2027)。"""
        if value is None:
            return "auto"
        if isinstance(value, str):
            text = value.strip().lower()
            return "auto" if text in {"", "auto"} else text
        return value

    @field_validator(
        "ingestion_queue_worker_concurrency",
        "ingestion_queue_heavy_phase_concurrency",
    )
    @classmethod
    def validate_ingestion_queue_concurrency_range(
        cls, value: int | Literal["auto"]
    ) -> int | Literal["auto"]:
        if value != "auto" and not 1 <= value <= INGESTION_QUEUE_MAX_CONCURRENCY:
            raise ValueError(
                f"1〜{INGESTION_QUEUE_MAX_CONCURRENCY} の数か auto を指定してください。"
            )
        return value

    @model_validator(mode="after")
    def validate_ingestion_queue_heavy_phase_concurrency(self) -> Self:
        """数を書いた重い段の上限は、数を書いた全体の枠を超えない(#2027)。

        どちらかが auto なら起動時に全体の枠で頭打ちにする(vCPU で決まるため、ここでは決めない)。
        """
        total = self.ingestion_queue_worker_concurrency
        heavy = self.ingestion_queue_heavy_phase_concurrency
        if total != "auto" and heavy != "auto" and heavy > total:
            raise ValueError(
                "RAG_INGESTION_QUEUE_HEAVY_PHASE_CONCURRENCY は "
                "RAG_INGESTION_QUEUE_WORKER_CONCURRENCY 以下にしてください。"
            )
        return self

    def ingestion_queue_concurrency(self, *, vcpu: int | None = None) -> IngestionQueueConcurrency:
        """取込の worker の全体の枠と重い段の上限(auto は vCPU から決める。#2027)。"""
        return resolve_ingestion_queue_concurrency(
            self.ingestion_queue_worker_concurrency,
            self.ingestion_queue_heavy_phase_concurrency,
            vcpu=detect_vcpu_count() if vcpu is None else vcpu,
        )

    @model_validator(mode="after")
    def validate_ingestion_queue_lease(self) -> Self:
        """lease の TTL は heartbeat 間隔の 3 倍以上にする。

        heartbeat が 1〜2 回失敗しただけで、他の worker に実行中の job を回復させないため(#357)。
        """
        if (
            self.ingestion_queue_lease_ttl_seconds
            < self.ingestion_queue_heartbeat_interval_seconds * 3
        ):
            raise ValueError(
                "RAG_INGESTION_QUEUE_LEASE_TTL_SECONDS は "
                "RAG_INGESTION_QUEUE_HEARTBEAT_INTERVAL_SECONDS の 3 倍以上にしてください。"
            )
        return self

    def ingestion_silent_call_timeouts(self) -> dict[str, float]:
        """進捗を出す段の中で、進捗を変えずに続きうる 1 回の呼び出しの最長(秒。再試行を含む)。

        停滞の時間(``ingestion_job_stall_timeout_seconds``)はこれより長くする(#2003)。
        値の key は直す設定(環境変数)の名前。
        """
        timeouts = {
            "rag_parser_service_timeout_seconds": (
                self.rag_parser_service_timeout_seconds * self.rag_http_service_retry_attempts
                + self.rag_http_service_retry_max_delay_seconds
                * max(self.rag_http_service_retry_attempts - 1, 0)
            ),
            "rag_parser_mineru_job_timeout_seconds": self.rag_parser_mineru_job_timeout_seconds,
            "oci_document_understanding_timeout_seconds": (
                self.oci_document_understanding_timeout_seconds
            ),
            "oci_speech_timeout_seconds": self.oci_speech_timeout_seconds,
            "oci_enterprise_ai_timeout_seconds": (
                self.oci_enterprise_ai_timeout_seconds * (self.oci_enterprise_ai_max_retries + 1)
            ),
        }
        return {settings_env_name("RAG_", field): value for field, value in timeouts.items()}

    @model_validator(mode="after")
    def validate_ingestion_job_stall_timeout(self) -> Self:
        """停滞の時間は、進捗を出さない 1 回の呼び出しの最長より長くする(#2003)。

        短いと、解析サービスの 1 回の呼び出しや Vision の再試行の間に、進んでいる job を止める。
        """
        name, longest = max(self.ingestion_silent_call_timeouts().items(), key=lambda item: item[1])
        if self.ingestion_job_stall_timeout_seconds <= longest:
            raise ValueError(
                "RAG_INGESTION_JOB_STALL_TIMEOUT_SECONDS は、進捗を出さない 1 回の呼び出しの最長"
                f"（{name} から {longest:g} 秒。再試行を含む）より長くしてください。"
            )
        return self

    @model_validator(mode="after")
    def validate_rag_chunk_settings(self) -> Self:
        """chunk size と各 chunking 戦略パラメータの整合性を起動時に検証する。"""
        self.rag_chunk_delimiter = self.rag_chunk_delimiter.strip()
        if not self.rag_chunk_delimiter:
            raise ValueError("RAG_CHUNK_DELIMITER を入力してください。")
        if self.rag_chunking_strategy == "fixed_delimiter":
            return self
        if self.rag_chunk_overlap >= self.rag_chunk_size:
            raise ValueError("RAG_CHUNK_OVERLAP は RAG_CHUNK_SIZE より小さくしてください。")
        if (
            self.rag_chunking_strategy in CHUNKING_STRATEGIES_WITH_MIN_CHARS
            and self.rag_chunk_min_chars >= self.rag_chunk_size
        ):
            raise ValueError("RAG_CHUNK_MIN_CHARS は RAG_CHUNK_SIZE より小さくしてください。")
        return self

    @property
    def local_debug_enabled(self) -> bool:
        """local mode: 全権限・対象範囲の制限なしのローカル利用者（DB セッションを作らない）。"""
        return self.auth_mode == "local"

    @property
    def app_auth_enabled(self) -> bool:
        """production mode: 共通認証のログインを必須にする。"""
        return self.auth_mode == "production"

    @property
    def oracle_driver_mode(self) -> str:
        """既定は Thin mode。PLATFORM_ORACLE_CLIENT_LIB_DIR を指定したときだけ Thick mode。

        Wallet の判定にも使う。
        """
        return "thick" if self.oracle_client_lib_dir.strip() else "thin"

    @property
    def oracle_connection_security(self) -> str:
        """RAG は Wallet mTLS だけに対応する（Walletless TLS は接続処理が未対応）。"""
        return "wallet_mtls"

    @property
    def resolved_oracle_wallet_dir(self) -> str:
        """Wallet 配置先。

        Thin は PLATFORM_ORACLE_WALLET_DIR、Thick は <CLIENT_LIB_DIR>/network/admin。
        """
        client_lib_dir = self.oracle_client_lib_dir.strip()
        if client_lib_dir:
            return str(Path(client_lib_dir).expanduser() / "network" / "admin")
        return self.oracle_wallet_dir.strip()

    @property
    def resolved_oracle_adb_region(self) -> str:
        """ADB 管理専用 region。旧設定互換のため PLATFORM_OCI_REGION へ fallback する。"""
        return self.oracle_adb_region.strip() or self.oci_region.strip()


@lru_cache
def _settings_singleton() -> Settings:
    """環境変数/.env と永続化ファイルから初期 Settings を作る。

    `.env` は module の `PLATFORM_ENV_FILE` / `BACKEND_ENV_FILE` から読む
    （本番は model_config と同じパス）。テストはこの 2 つを差し替えて、
    開発者の手元の `.env` を読まないようにする（#483）。
    """
    settings = Settings(_env_file=(PLATFORM_ENV_FILE, BACKEND_ENV_FILE))
    load_persisted_model_settings(settings)
    load_persisted_parser_settings(settings)
    # 以後の .env の変更を差分で取り込むため、今の .env の内容を基準として覚える。
    reload_env_settings_if_changed(settings)
    return settings


def get_settings() -> Settings:
    """設定のシングルトンを返す。.env・永続化ファイルの更新があれば再読込する。"""
    settings = _settings_singleton()
    reload_env_settings_if_changed(settings)
    reload_persisted_model_settings_if_changed(settings)
    PARSER_SETTINGS_STORE.reload_if_changed(settings)
    LANGUAGE_PACK_STORE.reload_if_changed(resolve_language_pack_file(settings))
    return settings


def reset_settings_cache() -> None:
    """テストや明示的な再初期化のため Settings singleton を破棄する。"""
    _settings_singleton.cache_clear()
    MODEL_SETTINGS_STORE.reset()
    PARSER_SETTINGS_STORE.reset()
    LANGUAGE_PACK_STORE.reset()
    _ENV_RELOAD_STATE.key = None
    _ENV_RELOAD_STATE.snapshot = None


@dataclass
class _EnvReloadState:
    """前回読んだ .env の (パス, 更新時刻) と、その内容から作った Settings。"""

    key: tuple[tuple[str, int | None], ...] | None = None
    snapshot: Settings | None = None


_ENV_RELOAD_STATE = _EnvReloadState()
_ENV_RELOAD_LOCK = threading.Lock()


def _env_file_mtime_ns(path: Path) -> int | None:
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return None


def reload_env_settings_if_changed(settings: Settings) -> None:
    """別プロセス（もう一方の worker・取込 worker）が画面から保存した `.env` を取り込む（#465）。

    設定画面の保存は `.env` に書き、保存を受けたプロセスの Settings だけを書き換える。
    ほかのプロセスは、`.env` の更新時刻が変わったら読み直し、前回読んだ内容から
    **変わった項目だけ** を反映する（プロセスの中で変えた値や、`.env` にない値を既定値へ戻さない）。
    反映後は model-settings.json と parser-settings.json の値をかけ直す。`.env` のパスが
    変わったとき（テストの tmp など）は基準を取り直すだけにする。
    """
    files = (PLATFORM_ENV_FILE, BACKEND_ENV_FILE)
    key = tuple((str(path), _env_file_mtime_ns(path)) for path in files)
    state = _ENV_RELOAD_STATE
    if key == state.key:
        return
    with _ENV_RELOAD_LOCK:
        if key == state.key:
            return
        previous, previous_key = state.snapshot, state.key
        try:
            snapshot = Settings(_env_file=files)
        except (ValidationError, OSError) as exc:
            # 読み直しに失敗しても（手で書いた不正な値など）リクエストは止めず、今の値のまま動く。
            # 同じ内容で何度も読み直さないよう時刻は覚え、基準は最後に読めた内容のままにする。
            state.key = key
            logging.getLogger(__name__).warning(
                "rag_env_settings_reload_failed", extra={"error_type": type(exc).__name__}
            )
            return
        state.key, state.snapshot = key, snapshot
        same_files = previous_key is not None and [path for path, _ in previous_key] == [
            path for path, _ in key
        ]
        if previous is None or not same_files:
            return
        changed = [
            name
            for name in Settings.model_fields
            if getattr(snapshot, name) != getattr(previous, name)
        ]
        for name in changed:
            setattr(settings, name, getattr(snapshot, name))
        if changed:
            load_persisted_model_settings(settings)
            load_persisted_parser_settings(settings)


def resolve_model_settings_file(path_value: str) -> Path:
    """PLATFORM_MODEL_SETTINGS_FILE を共通 `.env` と同じディレクトリ基準で解決する。

    model-settings.json は3製品で共有する（#211）。
    """
    raw_path = path_value.strip() or DEFAULT_MODEL_SETTINGS_FILE
    path = Path(raw_path).expanduser()
    if path.is_absolute():
        return path
    return (PLATFORM_ENV_FILE.parent / path).resolve()


_PARSER_ADAPTER_FIELDS = tuple(_PersistedParserAdapterSettings.model_fields)
# parser の API key（JSON に書かず、RAG の backend/.env に保存する。#106 / #211）。
PARSER_SECRET_ATTRS = ("rag_parser_mineru_api_key", "rag_parser_dots_ocr_api_key")


def resolve_language_pack_file(settings: Settings) -> Path:
    """RAG_LANGUAGE_PACK_FILE を RAG の backend/.env と同じディレクトリ基準で解決する(#2181)。"""
    path = Path(settings.rag_language_pack_file.strip() or DEFAULT_LANGUAGE_PACK_FILE).expanduser()
    return path if path.is_absolute() else (BACKEND_ENV_FILE.parent / path).resolve()


def resolve_parser_settings_file(path_value: str) -> Path:
    """RAG_PARSER_SETTINGS_FILE を RAG の backend/.env と同じディレクトリ基準で解決する。"""
    path = Path(path_value.strip() or DEFAULT_PARSER_SETTINGS_FILE).expanduser()
    if path.is_absolute():
        return path
    return (BACKEND_ENV_FILE.parent / path).resolve()


def _load_parser_adapters(settings: Settings, raw: Mapping[str, Any]) -> None:
    """parser-settings.json の内容を Settings へ反映する（無い項目は既定値）。"""
    parser = _PersistedParserAdapterSettings.model_validate(raw)
    for name in _PARSER_ADAPTER_FIELDS:
        setattr(settings, f"rag_parser_{name}", getattr(parser, name))


def _dump_parser_adapters(settings: Settings) -> dict[str, Any]:
    return {name: getattr(settings, f"rag_parser_{name}") for name in _PARSER_ADAPTER_FIELDS}


# モデル設定の読み書きは3製品共通（platform の pr_system_settings。#103）。
MODEL_SETTINGS_STORE = ModelSettingsStore(
    resolve_path=lambda settings: resolve_model_settings_file(settings.model_settings_file),
    # モデルの API key（PLATFORM_OCI_ENTERPRISE_AI_API_KEY）は共通 `.env` に保存する（#211）。
    # テストで差し替えられるよう、呼出時に module の値を参照する。
    env_file=lambda _settings: PLATFORM_ENV_FILE,
)
# 文書解析の設定は RAG 専用のファイルに置き、共有の model-settings.json には書かない（#1701）。
PARSER_SETTINGS_STORE = ParserSettingsStore(
    resolve_path=lambda settings: resolve_parser_settings_file(settings.rag_parser_settings_file),
    env_file=lambda _settings: BACKEND_ENV_FILE,
    load=_load_parser_adapters,
    dump=_dump_parser_adapters,
    secrets=tuple((attr, settings_env_name("RAG_", attr)) for attr in PARSER_SECRET_ATTRS),
)


def load_persisted_parser_settings(settings: Settings) -> None:
    """「文書解析」画面で保存した parser-settings.json があれば Settings へ上書き適用する。"""
    PARSER_SETTINGS_STORE.load(settings)


def load_persisted_model_settings(settings: Settings) -> None:
    """UI 保存済みの共有ランタイム設定 JSON があれば Settings へ上書き適用する。"""
    MODEL_SETTINGS_STORE.load(settings)


def reload_persisted_model_settings_if_changed(settings: Settings) -> None:
    """別 worker が保存した共有ランタイム設定を次回リクエストで取り込む。"""
    MODEL_SETTINGS_STORE.reload_if_changed(settings)
