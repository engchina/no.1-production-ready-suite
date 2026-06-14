"""設定 API のスキーマ。secret はレスポンスに含めない。"""

import json
import re
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.config import AiServiceAdapter, UploadStorageBackend

ModelSettingsCheckStatus = Literal["ok", "missing", "invalid"]
DatabaseConnectionTestStatus = Literal["success", "failed", "skipped"]
OciConfigField = Literal["user", "fingerprint", "tenancy", "region", "key_file"]


class EnterpriseAiModelSettings(BaseModel):
    """OCI Enterprise AI（LLM/VLM）モデル設定。"""

    endpoint: str = Field(default="", max_length=2048)
    project_ocid: str = Field(default="", max_length=512)
    api_key: str = Field(default="", max_length=4096)
    has_api_key: bool = False
    clear_api_key: bool = False
    llm_model: str = Field(default="", max_length=256)
    vlm_model: str = Field(default="", max_length=256)
    llm_path: str = Field(default="/responses", max_length=512)
    vlm_path: str = Field(default="/responses", max_length=512)
    llm_payload_template: str = Field(default="", max_length=20000)
    vlm_payload_template: str = Field(default="", max_length=20000)
    llm_response_path: str = Field(default="", max_length=1024)
    vlm_response_path: str = Field(default="", max_length=1024)
    timeout_seconds: float = Field(default=60.0, gt=0.0, le=600.0)
    max_retries: int = Field(default=3, ge=0, le=5)

    @field_validator(
        "endpoint",
        "project_ocid",
        "api_key",
        "llm_model",
        "vlm_model",
        "llm_path",
        "vlm_path",
        "llm_payload_template",
        "vlm_payload_template",
        "llm_response_path",
        "vlm_response_path",
    )
    @classmethod
    def strip_text(cls, value: str) -> str:
        """前後空白を設定値へ混入させない。"""
        return value.strip()

    @field_validator("endpoint")
    @classmethod
    def validate_endpoint(cls, value: str) -> str:
        """endpoint は未設定または HTTP(S) URL に限定する。"""
        if value and not value.startswith(("http://", "https://")):
            raise ValueError("endpoint は http:// または https:// で始めてください。")
        return value

    @field_validator("project_ocid")
    @classmethod
    def validate_project_ocid(cls, value: str) -> str:
        """OpenAI-compatible API の project は Generative AI project OCID を使う。"""
        if value and not value.startswith("ocid1.generativeaiproject."):
            raise ValueError("project OCID は ocid1.generativeaiproject. で始めてください。")
        return value

    @field_validator("llm_path", "vlm_path")
    @classmethod
    def validate_api_path(cls, value: str) -> str:
        """Enterprise AI の呼び出し先は相対 path または HTTP(S) URL に限定する。"""
        if not value:
            raise ValueError("API パスを入力してください。")
        if not value.startswith(("/", "http://", "https://")):
            raise ValueError("API パスは / または http(s):// で始めてください。")
        return value

    @field_validator("llm_payload_template", "vlm_payload_template")
    @classmethod
    def validate_payload_template(cls, value: str) -> str:
        """payload template は空または JSON object 文字列だけを許可する。"""
        if not value:
            return value
        try:
            parsed = json.loads(value)
        except ValueError as exc:
            raise ValueError("payload template は JSON object で入力してください。") from exc
        if not isinstance(parsed, dict):
            raise ValueError("payload template は JSON object で入力してください。")
        return value

    @field_validator("llm_response_path", "vlm_response_path")
    @classmethod
    def validate_response_path(cls, value: str) -> str:
        """response path は空または JSON Pointer 形式だけを許可する。"""
        if value and not value.startswith("/"):
            raise ValueError("response path は / で始まる JSON Pointer で入力してください。")
        return value


class GenerativeAiModelSettings(BaseModel):
    """OCI Generative AI（embedding/rerank）モデル設定。"""

    embedding_model: str = Field(default="cohere.embed-v4.0", max_length=256)
    embedding_dim: int = Field(
        default=1536,
        ge=1536,
        le=1536,
        description="Oracle VECTOR(1536, FLOAT32) と互換にするため 1536 固定。",
    )
    rerank_model: str = Field(default="cohere.rerank-v4.0-fast", max_length=256)

    @field_validator("embedding_model", "rerank_model")
    @classmethod
    def strip_text(cls, value: str) -> str:
        """前後空白を設定値へ混入させない。"""
        return value.strip()


class ModelSettingsPayload(BaseModel):
    """モデル設定の読み書き payload。"""

    enterprise_ai: EnterpriseAiModelSettings
    generative_ai: GenerativeAiModelSettings


class ModelSettingsData(BaseModel):
    """モデル設定 API のレスポンス data。"""

    settings: ModelSettingsPayload
    checks: dict[str, ModelSettingsCheckStatus]
    source: Literal["runtime"]


class DatabaseSettingsData(BaseModel):
    """Oracle 26ai 接続設定の表示用データ。"""

    adapter: AiServiceAdapter
    user: str
    dsn: str
    wallet_dir: str
    wallet_uploaded: bool
    available_services: list[str]
    has_password: bool
    has_wallet_password: bool
    readiness: str
    embedding_dimension: int
    vector_column: str
    config_source: Literal["runtime"]


class DatabaseSettingsUpdate(BaseModel):
    """Oracle 26ai 接続設定の更新 payload。

    password / wallet_password は未指定または空文字なら既存値を保持する。
    """

    user: str = Field(default="", max_length=256)
    dsn: str = Field(default="", max_length=1024)
    wallet_dir: str = Field(default="", max_length=1024)
    password: str | None = Field(default=None, max_length=4096)
    wallet_password: str | None = Field(default=None, max_length=4096)

    @field_validator("user", "dsn", "wallet_dir")
    @classmethod
    def strip_text(cls, value: str) -> str:
        """前後空白を設定値へ混入させない。"""
        return value.strip()


class UploadStorageSettingsData(BaseModel):
    """アップロード原本保存先の表示用データ。"""

    backend: UploadStorageBackend
    ai_service_adapter: AiServiceAdapter
    local_storage_dir: str
    object_storage_region: str
    object_storage_namespace: str
    object_storage_bucket: str
    readiness: str
    max_upload_bytes: int
    config_source: Literal["runtime"]


class UploadStorageSettingsUpdate(BaseModel):
    """アップロード原本保存先の更新 payload。"""

    backend: UploadStorageBackend
    local_storage_dir: str = Field(default="", max_length=1024)
    object_storage_namespace: str | None = Field(default=None, max_length=256)
    object_storage_bucket: str = Field(default="", max_length=256)

    @field_validator("local_storage_dir", "object_storage_bucket")
    @classmethod
    def strip_text(cls, value: str) -> str:
        """前後空白を設定値へ混入させない。"""
        return value.strip()

    @field_validator("object_storage_namespace")
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str | None:
        """省略時は既存の OCI 認証設定 namespace を保持する。"""
        return value.strip() if value is not None else None

    @field_validator("object_storage_namespace", "object_storage_bucket")
    @classmethod
    def validate_object_storage_name(cls, value: str | None) -> str | None:
        """OCI Object Storage の namespace / bucket 名で危険な文字を拒否する。"""
        if value and not re.fullmatch(r"[A-Za-z0-9._-]+", value):
            raise ValueError(
                "Object Storage の値は英数字、ハイフン、アンダースコア、ドットで入力してください。"
            )
        return value

    @model_validator(mode="after")
    def validate_selected_backend(self) -> "UploadStorageSettingsUpdate":
        """選択した保存先に必要な値を確認する。"""
        if self.backend == "local" and not self.local_storage_dir:
            raise ValueError("local_storage_dir を入力してください。")
        if self.backend == "oci" and not self.object_storage_bucket:
            raise ValueError("OCI Object Storage の bucket を入力してください。")
        return self


class OciConfigReadRequest(BaseModel):
    """バックエンドから OCI config file の指定 profile を読み取る request。"""

    config_file: str = Field(default="", max_length=1024)
    profile: str = Field(default="DEFAULT", max_length=128)

    @field_validator("config_file", "profile")
    @classmethod
    def strip_text(cls, value: str) -> str:
        """前後空白を設定値へ混入させない。"""
        return value.strip()

    @field_validator("config_file")
    @classmethod
    def require_config_file(cls, value: str) -> str:
        """読み取り対象 path は必須。"""
        if not value:
            raise ValueError("OCI config ファイルの path を入力してください。")
        return value

    @field_validator("profile")
    @classmethod
    def validate_profile(cls, value: str) -> str:
        """profile 名は INI section として安全な文字列に限定する。"""
        profile = value or "DEFAULT"
        if any(char in profile for char in "[]\r\n"):
            raise ValueError("プロファイル名に [ ] や改行は使用できません。")
        return profile


class OciConfigReadData(BaseModel):
    """OCI config profile から取り込んだ表示用データ。"""

    profile: str
    user: str = ""
    fingerprint: str = ""
    tenancy: str = ""
    region: str = ""
    key_file: str = ""
    applied_fields: list[OciConfigField] = Field(default_factory=list)


class OciObjectStorageNamespaceRequest(BaseModel):
    """OCI Object Storage namespace 取得 request。"""

    config_file: str = Field(default="", max_length=1024)
    profile: str = Field(default="DEFAULT", max_length=128)
    region: str = Field(default="", max_length=128)

    @field_validator("config_file", "profile", "region")
    @classmethod
    def strip_text(cls, value: str) -> str:
        """前後空白を設定値へ混入させない。"""
        return value.strip()

    @field_validator("config_file")
    @classmethod
    def require_config_file(cls, value: str) -> str:
        """OCI SDK が読む config path は必須。"""
        if not value:
            raise ValueError("OCI config ファイルの path を入力してください。")
        return value

    @field_validator("profile")
    @classmethod
    def validate_profile(cls, value: str) -> str:
        """profile 名は INI section として安全な文字列に限定する。"""
        profile = value or "DEFAULT"
        if any(char in profile for char in "[]\r\n"):
            raise ValueError("プロファイル名に [ ] や改行は使用できません。")
        return profile

    @field_validator("region")
    @classmethod
    def require_region(cls, value: str) -> str:
        """Object Storage namespace 取得に使う region は必須。"""
        if not value:
            raise ValueError("Object Storage リージョンを入力してください。")
        return value


class OciObjectStorageNamespaceData(BaseModel):
    """OCI Object Storage namespace 取得結果。"""

    namespace: str


class OciPrivateKeyUploadData(BaseModel):
    """OCI API 秘密鍵アップロード結果。secret 内容は含めない。"""

    key_file: str
    saved: bool


class DatabaseConnectionTestResult(BaseModel):
    """Oracle 接続検証の結果。"""

    status: DatabaseConnectionTestStatus
    readiness: str
    message: str
    checked_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    error_type: str | None = None
