"""設定 API のスキーマ。secret はレスポンスに含めない。"""

from datetime import UTC, datetime
from typing import Literal

# モデル設定の schema は3製品共通（pr_system_settings。#103）。互換のため re-export する。
from pr_system_settings.model import (
    EnterpriseAiModelEntrySettings as EnterpriseAiModelEntrySettings,
)
from pr_system_settings.model import EnterpriseAiModelSettings as EnterpriseAiModelSettings
from pr_system_settings.model import EnterpriseAiVlmInputMode as EnterpriseAiVlmInputMode
from pr_system_settings.model import GenerativeAiModelSettings as GenerativeAiModelSettings
from pr_system_settings.model import ModelSettingsData as ModelSettingsData
from pr_system_settings.model import ModelSettingsPayload as ModelSettingsPayload
from pr_system_settings.model import ModelSettingsSecretSource as ModelSettingsSecretSource
from pr_system_settings.model import ModelSettingsTestRequest as ModelSettingsTestRequest
from pr_system_settings.model import ModelSettingsTestResult as ModelSettingsTestResult
from pr_system_settings.model import ModelSettingsTestStatus as ModelSettingsTestStatus
from pr_system_settings.model import ModelSettingsTestTargetType as ModelSettingsTestTargetType

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
