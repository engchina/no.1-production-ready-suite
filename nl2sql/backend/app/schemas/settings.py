"""設定 API のスキーマ。secret はレスポンスに含めない。"""

import json
import re
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

EnterpriseAiVlmInputMode = Literal["auto", "files_api", "inline_image"]
UploadStorageBackend = Literal["local", "oci"]
ModelSettingsCheckStatus = Literal["ok", "missing", "invalid"]
ModelSettingsSecretSource = Literal["environment", "legacy_json", "missing"]
ModelSettingsTestStatus = Literal["success", "failed"]
ModelSettingsTestTargetType = Literal["enterprise_text", "enterprise_vision", "embedding", "rerank"]
DatabaseConnectionTestStatus = Literal["success", "failed"]
DatabaseWalletDownloadStatus = Literal["downloaded", "already_configured"]
OciConfigTestStatus = Literal["success", "failed"]
OciConfigField = Literal["user", "fingerprint", "tenancy", "region", "key_file"]
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
    checks: dict[str, ModelSettingsCheckStatus]
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
    object_type: Literal["TABLE", "INDEX", "SEQUENCE"]


class SystemTableMetadata(BaseModel):
    """USER_TABLES / USER_OBJECTS から取得する概算 metadata。"""

    name: str
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
    """OCI config file の profile 読み取り request。"""

    config_file: str = Field(default="~/.oci/config", max_length=1024)
    profile: str = Field(default="DEFAULT", max_length=128)

    @field_validator("config_file", "profile")
    @classmethod
    def strip_text(cls, value: str) -> str:
        """前後空白を設定値へ混入させない。"""
        return value.strip()


class OciConfigReadData(BaseModel):
    """OCI config profile から読み取った UI 反映値。"""

    profile: str
    user: str = ""
    fingerprint: str = ""
    tenancy: str = ""
    region: str = ""
    key_file: str = ""
    applied_fields: list[OciConfigField] = Field(default_factory=list)


class OciSettingsUpdate(BaseModel):
    """OCI config / profile の更新 payload。"""

    user: str = Field(default="", max_length=512)
    fingerprint: str = Field(default="", max_length=256)
    tenancy: str = Field(default="", max_length=512)
    region: str = Field(default="", max_length=128)

    @field_validator("user", "fingerprint", "tenancy", "region")
    @classmethod
    def strip_text(cls, value: str) -> str:
        """前後空白を設定値へ混入させない。"""
        return value.strip()


class OciSettingsData(BaseModel):
    """OCI 認証設定画面の表示用データ。"""

    config_file: str
    profile: str
    user: str
    fingerprint: str
    tenancy: str
    region: str
    key_file: str
    key_file_exists: bool
    config_file_exists: bool
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

    @field_validator("object_storage_namespace")
    @classmethod
    def validate_namespace(cls, value: str) -> str:
        """OCI Object Storage namespace で危険な文字を拒否する。"""
        if value and not re.fullmatch(r"[A-Za-z0-9._-]+", value):
            raise ValueError(
                "Object Storage namespace は英数字、ハイフン、アンダースコア、"
                "ドットで入力してください。"
            )
        return value


class OciConfigTestResult(BaseModel):
    """OCI config / 秘密鍵の検証結果。"""

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
    """Object Storage namespace 取得 request。"""

    config_file: str = Field(default="~/.oci/config", max_length=1024)
    profile: str = Field(default="DEFAULT", max_length=128)
    region: str = Field(default="", max_length=128)

    @field_validator("config_file", "profile", "region")
    @classmethod
    def strip_text(cls, value: str) -> str:
        """前後空白を設定値へ混入させない。"""
        return value.strip()


class OciObjectStorageNamespaceData(BaseModel):
    """Object Storage namespace 取得結果。"""

    namespace: str


class OciPrivateKeyUploadData(BaseModel):
    """OCI API 秘密鍵アップロード結果。"""

    key_file: str
    saved: bool
