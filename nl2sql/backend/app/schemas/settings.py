"""設定 API のスキーマ。secret はレスポンスに含めない。"""

import json
from datetime import UTC, datetime
from typing import Literal

# OCI 認証の schema は3製品共通（platform の pr_system_settings。#100）。互換のため re-export する。
from pr_system_settings.oci import OciConfigField as OciConfigField
from pr_system_settings.oci import OciConfigReadData as OciConfigReadData
from pr_system_settings.oci import OciConfigReadRequest as OciConfigReadRequest
from pr_system_settings.oci import OciConfigTestResult as OciConfigTestResult
from pr_system_settings.oci import OciConfigTestStage as OciConfigTestStage
from pr_system_settings.oci import OciConfigTestStageKey as OciConfigTestStageKey
from pr_system_settings.oci import OciConfigTestStageStatus as OciConfigTestStageStatus
from pr_system_settings.oci import OciConfigTestStatus as OciConfigTestStatus
from pr_system_settings.oci import OciObjectStorageNamespaceData as OciObjectStorageNamespaceData
from pr_system_settings.oci import (
    OciObjectStorageNamespaceRequest as OciObjectStorageNamespaceRequest,
)
from pr_system_settings.oci import OciObjectStorageSettingsUpdate as OciObjectStorageSettingsUpdate
from pr_system_settings.oci import OciPrivateKeyUploadData as OciPrivateKeyUploadData
from pr_system_settings.oci import OciSettingsData as OciSettingsData
from pr_system_settings.oci import OciSettingsUpdate as OciSettingsUpdate

# アップロード保存先の schema は3製品共通（platform の pr_system_settings。#97）。
# 互換のため re-export する。
from pr_system_settings.upload_storage import (
    UploadStorageBackend as UploadStorageBackend,
)
from pr_system_settings.upload_storage import (
    UploadStorageSettingsData as UploadStorageSettingsData,
)
from pr_system_settings.upload_storage import (
    UploadStorageSettingsUpdate as UploadStorageSettingsUpdate,
)
from pydantic import BaseModel, Field, field_validator

EnterpriseAiVlmInputMode = Literal["auto", "files_api", "inline_image"]
ModelSettingsSecretSource = Literal["environment", "legacy_json", "missing"]
ModelSettingsTestStatus = Literal["success", "failed"]
ModelSettingsTestTargetType = Literal["enterprise_text", "enterprise_vision", "embedding", "rerank"]
DatabaseConnectionTestStatus = Literal["success", "failed"]
DatabaseConnectionSecurity = Literal["wallet_mtls", "walletless_tls"]
DatabaseWalletDownloadStatus = Literal["downloaded", "already_configured"]
SelectAiCredentialRegion = Literal["ap-osaka-1", "us-chicago-1"]
SelectAiCredentialOperation = Literal["created", "recreated", "already_exists"]
AdbOperationStatus = Literal[
    "success",
    "not_configured",
    "error",
    "accepted",
    "already_available",
    "already_stopped",
    "cannot_start",
    "cannot_stop",
]
SystemTableSchemaStatus = Literal["missing", "partial", "outdated", "ready"]
SystemTableOperationStatus = Literal["idle", "running", "failed"]
SystemTableOperationKind = Literal["initialize", "recreate"]
SystemTableOperationResult = Literal["no_op", "initialized", "migrated", "recreated"]


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
    vlm_input_mode: EnterpriseAiVlmInputMode = "auto"
    text_payload_template: str = Field(default="", max_length=20000)
    vision_payload_template: str = Field(default="", max_length=20000)
    text_response_path: str = Field(default="", max_length=1024)
    vision_response_path: str = Field(default="", max_length=1024)
    timeout_seconds: float = Field(default=600.0, gt=0.0, le=600.0)
    max_retries: int = Field(default=3, ge=0, le=5)
    llm_max_output_tokens: int = Field(default=1200, ge=1, le=65536)
    vlm_max_output_tokens: int = Field(default=65536, ge=1, le=65536)

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
    model_settings_file: str
    source: Literal["runtime"]
    secret_source: ModelSettingsSecretSource
    legacy_secret_detected: bool = False


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
    driver_mode: Literal["thin", "thick"]
    connection_security: DatabaseConnectionSecurity
    client_lib_dir: str
    wallet_dir: str
    wallet_uploaded: bool
    available_services: list[str]
    has_password: bool
    has_wallet_password: bool
    readiness: str
    embedding_dimension: int
    vector_column: str
    adb_ocid: str
    region: str
    config_source: Literal["runtime"]


class DatabasePasswordRevealData(BaseModel):
    """明示操作でのみ返す Oracle DB password。通常の設定取得には含めない。"""

    password: str = Field(default="", max_length=4096)


class SelectAiCredentialData(BaseModel):
    """Select AI Credential の安全な表示用状態。秘密鍵本文は含めない。"""

    credential_name: Literal["OCI_CRED"] = "OCI_CRED"
    schema_name: str
    exists: bool
    region: SelectAiCredentialRegion
    oci_auth_ready: bool
    missing_fields: list[str] = Field(default_factory=list)
    operation: SelectAiCredentialOperation | None = None


class SelectAiCredentialCreateRequest(BaseModel):
    """Credential 作成・再作成の明示操作 payload。"""

    region: SelectAiCredentialRegion = "us-chicago-1"
    confirmation: str = Field(default="", max_length=64)
    recreate: bool = False

    @field_validator("confirmation")
    @classmethod
    def strip_confirmation(cls, value: str) -> str:
        return value.strip()


class DatabaseWalletDownloadData(BaseModel):
    """OCI からの Wallet 取得結果。ZIP や生成 password は含めない。"""

    status: DatabaseWalletDownloadStatus
    settings: DatabaseSettingsData


class AdbSettingsUpdate(BaseModel):
    """Autonomous Database 操作対象の OCID と region の更新 payload。"""

    adb_ocid: str = Field(default="", max_length=512)
    region: str = Field(default="", max_length=128)

    @field_validator("adb_ocid", "region")
    @classmethod
    def strip_text(cls, value: str) -> str:
        """前後空白を設定値へ混入させない。"""
        return value.strip()


class AdbInfoData(BaseModel):
    """Autonomous Database の情報 / 操作結果の表示用データ。"""

    status: AdbOperationStatus
    message: str
    error_code: str | None = None
    id: str | None = None
    display_name: str | None = None
    lifecycle_state: str | None = None
    db_name: str | None = None
    cpu_core_count: int | None = None
    data_storage_size_in_tbs: float | None = None
    region: str | None = None


class DatabaseSettingsUpdate(BaseModel):
    """Oracle 26ai 接続設定の更新 payload。

    password / wallet_password は未指定または空文字なら既存値を保持する。
    clear_* が true の場合だけ保存済み secret を削除する。
    """

    user: str = Field(default="", max_length=256)
    dsn: str = Field(default="", max_length=1024)
    connection_security: DatabaseConnectionSecurity | None = None
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


class DatabaseConnectionTestResult(BaseModel):
    """Oracle 26ai 接続テスト結果。"""

    status: DatabaseConnectionTestStatus
    readiness: str
    message: str
    elapsed_ms: int
    troubleshooting: list[str] = Field(default_factory=list)
    details: dict[str, str | int | float | bool | None] = Field(default_factory=dict)
    checked_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    error_type: str | None = None


class SystemTableMissingObject(BaseModel):
    """不足している manifest object。"""

    name: str
    object_type: Literal["TABLE", "INDEX", "SEQUENCE", "PACKAGE", "PACKAGE BODY"]


class SystemTableMetadata(BaseModel):
    """USER_TABLES / USER_OBJECTS から取得する概算 metadata。"""

    name: str
    # 接続ユーザーの schema と所有者付きの名前（取得できない場合は空文字）。
    owner: str = ""
    qualified_name: str = ""
    exists: bool
    estimated_rows: int | None = None
    created_at: str | None = None
    last_analyzed_at: str | None = None


class SystemObjectMetadata(BaseModel):
    """必須の table / index / sequence を統一表示する metadata。"""

    name: str
    owner: str = ""
    qualified_name: str = ""
    object_type: Literal["TABLE", "INDEX", "SEQUENCE", "PACKAGE", "PACKAGE BODY"]
    exists: bool
    estimated_rows: int | None = None
    created_at: str | None = None
    last_analyzed_at: str | None = None


class SystemTableOperationState(BaseModel):
    """複数 replica が共有する schema operation lease 状態。"""

    status: SystemTableOperationStatus
    operation_kind: SystemTableOperationKind | None = None
    lease_expires_at: str | None = None
    last_error_code: str | None = None
    schema_epoch: int = 0
    updated_at: str | None = None


class SystemTablesStatusData(BaseModel):
    """NL2SQL system table の read-only status。"""

    status: SystemTableSchemaStatus
    schema_head: int
    applied_versions: list[int]
    pending_versions: list[int]
    expected_object_count: int
    existing_object_count: int
    expected_table_count: int
    existing_table_count: int
    missing_objects: list[SystemTableMissingObject]
    tables: list[SystemTableMetadata]
    objects: list[SystemObjectMetadata]
    operation_state: SystemTableOperationState


class SystemTablesInitializeRequest(BaseModel):
    """初期化または全再作成の request。"""

    recreate: bool = False
    confirmation: str | None = Field(default=None, max_length=128)


class SystemTablesOperationData(SystemTablesStatusData):
    """DDL operation 後の状態と件数。"""

    operation: SystemTableOperationResult
    dropped_object_count: int
    created_object_count: int
