"""3製品共通の `.env` と、環境変数名の規則（#211）。

- 3製品共通の設定（システム設定画面の OCI 認証・アップロード保存先・モデル・データベースと、
  共通認証の構成管理者・認証ポリシー）は platform の共通 `.env` に置き、`PLATFORM_` で始める。
- 製品固有の設定は製品の `backend/.env` に置き、製品の接頭辞（`RAG_` / `NL2SQL_` / `AGENT_`）で
  始める。
- Settings の属性名は変えず、環境変数名だけを validation alias で決める。旧名は読まない。
"""

from __future__ import annotations

import os
from functools import partial
from pathlib import Path

from pydantic import AliasGenerator
from pydantic_settings import (
    BaseSettings,
    DotEnvSettingsSource,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

# 共通 `.env` の場所を上書きする環境変数（コンテナなど、既定の場所にないとき）。
PLATFORM_ENV_FILE_ENV = "PLATFORM_ENV_FILE"
PLATFORM_ENV_PREFIX = "PLATFORM_"

# 共通 `.env` に置く Settings の属性。製品が持たない属性は無視される。
PLATFORM_SETTING_FIELDS = frozenset(
    {
        # OCI 認証
        "oci_config_file",
        "oci_config_profile",
        "oci_region",
        "oci_compartment_id",
        "oci_auth_mode",
        "oci_user_ocid",
        "oci_fingerprint",
        "oci_tenancy_ocid",
        "oci_key_file",
        # アップロード保存先
        "upload_storage_backend",
        "local_storage_dir",
        "object_storage_region",
        "object_storage_namespace",
        "object_storage_bucket",
        # モデル
        "model_settings_file",
        "oci_enterprise_ai_endpoint",
        "oci_enterprise_ai_project_ocid",
        "oci_enterprise_ai_api_key",
        "oci_enterprise_ai_models",
        "oci_enterprise_ai_default_model",
        "oci_enterprise_ai_llm_model",
        "oci_enterprise_ai_vlm_model",
        "oci_enterprise_ai_llm_path",
        "oci_enterprise_ai_vlm_path",
        "oci_enterprise_ai_vlm_input_mode",
        "oci_enterprise_ai_llm_payload_template",
        "oci_enterprise_ai_vlm_payload_template",
        "oci_enterprise_ai_llm_response_path",
        "oci_enterprise_ai_vlm_response_path",
        "oci_enterprise_ai_timeout_seconds",
        "oci_enterprise_ai_max_retries",
        "oci_enterprise_ai_llm_max_output_tokens",
        "oci_enterprise_ai_vlm_max_output_tokens",
        "oci_genai_endpoint",
        "oci_genai_embedding_model",
        "oci_genai_embedding_dim",
        "oci_genai_rerank_model",
        "oci_genai_embed_model_id",
        "oci_genai_rerank_model_id",
        # データベース
        "oracle_user",
        "oracle_password",
        "oracle_dsn",
        "oracle_driver_mode",
        "oracle_connection_security",
        "oracle_client_lib_dir",
        "oracle_wallet_dir",
        "oracle_wallet_password",
        "oracle_adb_ocid",
        "oracle_adb_region",
        "oracle_tcp_connect_timeout_seconds",
        "oracle_db_test_timeout_seconds",
        # 共通認証（構成管理者と認証ポリシー。Cookie 名と認証の有効 / 無効は製品ごと）
        "app_admin_login_user_id",
        "app_admin_login_user_password",
        "app_auth_cookie_secure",
        "app_auth_idle_timeout_minutes",
        "app_auth_absolute_timeout_hours",
        "app_auth_failed_login_limit",
        "app_auth_lockout_minutes",
        "app_auth_password_min_length",
        "app_auth_password_max_length",
        "app_auth_argon2_time_cost",
        "app_auth_argon2_memory_kib",
        "app_auth_argon2_parallelism",
    }
)


def platform_env_name(field: str) -> str:
    """共通の属性の環境変数名。`app_` は付けない（`app_auth_*` → `PLATFORM_AUTH_*`）。"""
    return PLATFORM_ENV_PREFIX + field.upper().removeprefix("APP_")


def product_env_name(prefix: str, field: str) -> str:
    """製品の属性の環境変数名。属性名が接頭辞で始まっていれば重ねない。"""
    name = field.upper()
    return name if name.startswith(prefix) else prefix + name


def settings_env_name(prefix: str, field: str) -> str:
    if field in PLATFORM_SETTING_FIELDS:
        return platform_env_name(field)
    return product_env_name(prefix, field)


def platform_env_file(backend_dir: Path) -> Path:
    """共通 `.env`。`PLATFORM_ENV_FILE` がなければ `<repo>/platform/.env`。"""
    configured = os.environ.get(PLATFORM_ENV_FILE_ENV, "").strip()
    if configured:
        return Path(configured).expanduser()
    return backend_dir.resolve().parents[1] / "platform" / ".env"


def product_settings_config(*, prefix: str, backend_dir: Path) -> SettingsConfigDict:
    """製品の Settings の model_config。共通 `.env` → 製品の `backend/.env` の順に読む。

    環境変数は `.env` より優先する。属性名での生成（テストの `Settings(oracle_dsn=...)`）も許す。
    旧名を読まないよう、Settings は `PlatformEnvSourcesMixin` を使う
    （`BaseServiceSettings` は継承済み）。
    """
    return SettingsConfigDict(
        env_file=(platform_env_file(backend_dir), backend_dir / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        populate_by_name=True,
        alias_generator=AliasGenerator(validation_alias=partial(settings_env_name, prefix)),
    )


def _alias_only(source: PydanticBaseSettingsSource) -> PydanticBaseSettingsSource:
    """環境変数 / `.env` では validation alias（新名）だけで探す。属性名（旧名）では探さない。"""
    source.config = {**source.config, "populate_by_name": False, "validate_by_name": False}
    if isinstance(source, DotEnvSettingsSource):
        # `.env` の未使用の行は extra として渡され、属性名と同じ旧名（`LOG_LEVEL` など）が
        # populate_by_name で属性に入ってしまう。一致した項目だけを返す。
        source.dotenv_filtering = "only_existing"
    return source


class PlatformEnvSourcesMixin:
    """`product_settings_config` と組み合わせる。旧名（属性名と同じ環境変数名）を読まない。

    `populate_by_name=True` は `Settings(oracle_dsn=...)` のような属性名での生成のためだけに使う。
    """

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            _alias_only(env_settings),
            _alias_only(dotenv_settings),
            file_secret_settings,
        )


def settings_env_names(settings_cls: type[BaseSettings]) -> dict[str, str]:
    """Settings の属性名 → 環境変数名（`.env.example` や設定監査の照合に使う）。"""
    names: dict[str, str] = {}
    for field, info in settings_cls.model_fields.items():
        alias = info.validation_alias
        names[field] = alias if isinstance(alias, str) else field.upper()
    return names
