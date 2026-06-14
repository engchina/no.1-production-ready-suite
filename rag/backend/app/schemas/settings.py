"""設定 API のスキーマ。secret はレスポンスに含めない。"""

import json
import re
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.config import UploadStorageBackend

ModelSettingsCheckStatus = Literal["ok", "missing", "invalid"]
ModelSettingsTestStatus = Literal["success", "failed"]
ModelSettingsTestTargetType = Literal["enterprise_text", "enterprise_vision", "embedding", "rerank"]
DatabaseConnectionTestStatus = Literal["success", "failed"]
OciConfigTestStatus = Literal["success", "failed"]
OciConfigField = Literal["user", "fingerprint", "tenancy", "region", "key_file"]


class EnterpriseAiModelEntrySettings(BaseModel):
    """OCI Enterprise AI provider に登録する LLM。"""

    model_id: str = Field(default="", max_length=256)
    display_name: str = Field(default="", max_length=256)
    vision_enabled: bool = False

    @field_validator("model_id", "display_name")
    @classmethod
    def strip_text(cls, value: str) -> str:
        """前後空白を設定値へ混入させない。"""
        return value.strip()


class EnterpriseAiModelSettings(BaseModel):
    """OCI Enterprise AI モデル provider 設定。"""

    endpoint: str = Field(default="", max_length=2048)
    project_ocid: str = Field(default="", max_length=512)
    api_key: str = Field(default="", max_length=4096)
    has_api_key: bool = False
    clear_api_key: bool = False
    models: list[EnterpriseAiModelEntrySettings] = Field(default_factory=list, max_length=20)
    default_model_id: str = Field(default="", max_length=256)
    api_path: str = Field(default="/responses", max_length=512)
    text_payload_template: str = Field(default="", max_length=20000)
    vision_payload_template: str = Field(default="", max_length=20000)
    text_response_path: str = Field(default="", max_length=1024)
    vision_response_path: str = Field(default="", max_length=1024)
    timeout_seconds: float = Field(default=60.0, gt=0.0, le=600.0)
    max_retries: int = Field(default=3, ge=0, le=5)

    @field_validator(
        "endpoint",
        "project_ocid",
        "api_key",
        "default_model_id",
        "api_path",
        "text_payload_template",
        "vision_payload_template",
        "text_response_path",
        "vision_response_path",
    )
    @classmethod
    def strip_text(cls, value: str) -> str:
        """前後空白を設定値へ混入させない。"""
        return value.strip()

    @field_validator("endpoint")
    @classmethod
    def validate_endpoint(cls, value: str) -> str:
        """endpoint の readiness 判定は保存後のチェックへ委譲する。"""
        return value

    @field_validator("project_ocid")
    @classmethod
    def validate_project_ocid(cls, value: str) -> str:
        """project OCID の readiness 判定は保存後のチェックへ委譲する。"""
        return value

    @field_validator("api_path")
    @classmethod
    def validate_api_path(cls, value: str) -> str:
        """API path の readiness 判定は保存後のチェックへ委譲する。"""
        return value

    @field_validator("text_payload_template", "vision_payload_template")
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

    @field_validator("text_response_path", "vision_response_path")
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
    model_settings_file: str
    source: Literal["runtime"]


class ModelSettingsTestRequest(BaseModel):
    """保存前のモデル設定で特定モデルを実 API に対してテストする request。"""

    settings: ModelSettingsPayload
    target_type: ModelSettingsTestTargetType
    model_id: str = Field(default="", max_length=256)
    vision_enabled: bool = False

    @field_validator("model_id")
    @classmethod
    def strip_model_id(cls, value: str) -> str:
        """前後空白を設定値へ混入させない。"""
        return value.strip()


class ModelSettingsTestResult(BaseModel):
    """モデル単位の実接続テスト結果。"""

    status: ModelSettingsTestStatus
    target_type: ModelSettingsTestTargetType
    model_id: str
    message: str
    troubleshooting: list[str] = Field(default_factory=list)
    raw_error: str | None = None
    error_type: str | None = None
    elapsed_ms: int
    checked_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    details: dict[str, str | int | float | bool | None] = Field(default_factory=dict)


class DatabaseSettingsData(BaseModel):
    """Oracle 26ai 接続設定の表示用データ。"""

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
    clear_* が true の場合だけ保存済み secret を削除する。
    """

    user: str = Field(default="", max_length=256)
    dsn: str = Field(default="", max_length=1024)
    wallet_dir: str = Field(default="", max_length=1024)
    password: str | None = Field(default=None, max_length=4096)
    wallet_password: str | None = Field(default=None, max_length=4096)
    clear_password: bool = False
    clear_wallet_password: bool = False

    @field_validator("user", "dsn", "wallet_dir")
    @classmethod
    def strip_text(cls, value: str) -> str:
        """前後空白を設定値へ混入させない。"""
        return value.strip()


class UploadStorageSettingsData(BaseModel):
    """アップロード原本保存先の表示用データ。"""

    backend: UploadStorageBackend
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


class OciSettingsUpdate(BaseModel):
    """OCI SDK config の DEFAULT profile へ保存する認証設定。"""

    user: str = Field(default="", max_length=512)
    fingerprint: str = Field(default="", max_length=128)
    tenancy: str = Field(default="", max_length=512)
    region: str = Field(default="", max_length=128)

    @field_validator("user", "fingerprint", "tenancy", "region")
    @classmethod
    def strip_text(cls, value: str) -> str:
        """前後空白を設定値へ混入させない。"""
        return value.strip()

    @field_validator("user")
    @classmethod
    def validate_user_ocid(cls, value: str) -> str:
        """OCI user OCID は入力時だけ形式を確認する。"""
        if value and not value.startswith("ocid1.user."):
            raise ValueError("ユーザー OCID は ocid1.user. で始めてください。")
        return value

    @field_validator("fingerprint")
    @classmethod
    def validate_fingerprint(cls, value: str) -> str:
        """API key fingerprint は入力時だけ OCI 形式を確認する。"""
        if value and not re.fullmatch(r"[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2})+", value):
            raise ValueError("fingerprint は 16 進数をコロン区切りで入力してください。")
        return value

    @field_validator("tenancy")
    @classmethod
    def validate_tenancy_ocid(cls, value: str) -> str:
        """OCI tenancy OCID は入力時だけ形式を確認する。"""
        if value and not value.startswith("ocid1.tenancy."):
            raise ValueError("テナンシ OCID は ocid1.tenancy. で始めてください。")
        return value

    @field_validator("region")
    @classmethod
    def validate_region(cls, value: str) -> str:
        """リージョン名は入力時だけ OCI region identifier として確認する。"""
        if value and not re.fullmatch(r"[a-z0-9-]+", value):
            raise ValueError("リージョンは英小文字、数字、ハイフンで入力してください。")
        return value


class OciSettingsData(BaseModel):
    """OCI 認証設定画面の初期表示用 runtime データ。"""

    config_file: str
    profile: str
    user: str = ""
    fingerprint: str = ""
    tenancy: str = ""
    region: str = ""
    key_file: str = ""
    key_file_exists: bool = False
    config_file_exists: bool = False
    config_source: Literal["runtime"]


class OciObjectStorageSettingsUpdate(BaseModel):
    """OCI Object Storage 共通設定の更新 payload。"""

    object_storage_region: str = Field(default="", max_length=128)
    object_storage_namespace: str = Field(default="", max_length=256)

    @field_validator("object_storage_region", "object_storage_namespace")
    @classmethod
    def strip_text(cls, value: str) -> str:
        """前後空白を設定値へ混入させない。"""
        return value.strip()

    @field_validator("object_storage_region")
    @classmethod
    def validate_region(cls, value: str) -> str:
        """Object Storage region は入力時だけ OCI region identifier として確認する。"""
        if value and not re.fullmatch(r"[a-z0-9-]+", value):
            raise ValueError("リージョンは英小文字、数字、ハイフンで入力してください。")
        return value

    @field_validator("object_storage_namespace")
    @classmethod
    def validate_namespace(cls, value: str) -> str:
        """Object Storage namespace は入力時だけ危険な文字を拒否する。"""
        if value and not re.fullmatch(r"[A-Za-z0-9._-]+", value):
            raise ValueError(
                "Object Storage の値は英数字、ハイフン、アンダースコア、ドットで入力してください。"
            )
        return value


class OciConfigTestResult(BaseModel):
    """保存済み OCI SDK config の検証結果。"""

    status: OciConfigTestStatus
    profile: str
    config_file: str
    key_file: str
    config_file_exists: bool
    key_file_exists: bool
    missing_fields: list[OciConfigField] = Field(default_factory=list)
    permission_issues: list[str] = Field(default_factory=list)
    oci_directory_mode: str | None = None
    config_file_mode: str | None = None
    key_file_mode: str | None = None
    message: str
    checked_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    error_type: str | None = None


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
    elapsed_ms: int = 0
    troubleshooting: list[str] = Field(default_factory=list)
    details: dict[str, str | int | float | bool | None] = Field(default_factory=dict)
    checked_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    error_type: str | None = None
