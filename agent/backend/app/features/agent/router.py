"""Agent Runtime API。

業務 RAG / NL2SQL は外部ツールとして扱い、このサービスは実行管理・
権限・監査・表示用の標準契約を提供する。
"""

from __future__ import annotations

import base64
import configparser
import hashlib
import hmac
import io
import json
import re
import shutil
import stat
from asyncio import sleep, wait_for
from collections.abc import Iterable, Mapping
from csv import DictWriter
from datetime import UTC, datetime
from email import policy
from email.parser import BytesParser
from importlib import import_module
from io import StringIO
from pathlib import Path, PurePosixPath
from time import monotonic
from types import SimpleNamespace
from typing import Any, Literal, cast
from uuid import uuid4
from zipfile import BadZipFile, ZipFile

import httpx
from anyio import fail_after
from anyio import to_thread as anyio_to_thread
from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import Response, StreamingResponse
from pr_backend_core import ApiResponse
from pydantic import BaseModel, Field, field_validator

from app.features.agent.config import runtime_config_store
from app.features.agent.runtime import (
    AgentProfile,
    AgentProfilePatch,
    AgentRuntimeSnapshot,
    AgentRuntimeSnapshotValidation,
    AgentsData,
    ApprovalDecisionRequest,
    Artifact,
    ArtifactsData,
    MemoryData,
    MemoryEntry,
    MemoryKind,
    MemorySearchRequest,
    RunCreateRequest,
    RunEvent,
    RunsData,
    RunState,
    RuntimeToolCallAuditData,
    runtime_repository,
)
from app.features.agent.skills import (
    AgentSkillListOutput,
    AgentSkillPlanOutput,
    AgentSkillRunInput,
    skill_registry,
)
from app.features.agent.tools import (
    ExternalMcpToolsData,
    ExternalServiceSettings,
    ExternalToolError,
    ToolCall,
    ToolDefinitionsData,
    ToolPolicy,
    ToolResult,
    ToolsData,
    list_external_mcp_tools,
    tool_registry,
)
from app.observability import (
    ObservabilityStatus,
    TraceEventsData,
    TraceExportRetryData,
    TracePolicyPatch,
    TracePolicySettings,
    flush_trace_export_retry_queue,
    get_trace_policy,
    list_trace_events,
    patch_trace_policy,
    trace_exporter_status,
)
from app.settings import get_settings

router = APIRouter(tags=["agent-runtime"])

BACKEND_ROOT = Path(__file__).resolve().parents[3]
BACKEND_ENV_FILE = BACKEND_ROOT / ".env"
OCI_CONFIG_MAX_BYTES = 64 * 1024
OCI_PRIVATE_KEY_FILE = "~/.oci/oci_api_key.pem"
OCI_CONFIG_KEYS = ("user", "fingerprint", "tenancy", "region", "key_file")
OCI_PRIVATE_KEY_PASSPHRASE_REQUIRED_ERROR = (  # nosec B105 - エラーメッセージ定数
    "OCI API 秘密鍵 PEM が暗号化されています。"
    " pass_phrase を OCI config に設定するか、パスフレーズなしの秘密鍵 PEM を使用してください。"
)
PASSPHRASE_CONFIG_KEYS = frozenset({"pass_phrase", "passphrase", "key_password"})
ENV_ASSIGNMENT_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")
ENV_FILE_MODE = 0o600
MODEL_SETTINGS_FILE_MODE = 0o600
OCI_DIRECTORY_MODE = 0o700
OCI_CONFIG_FILE_MODE = 0o600
OCI_PRIVATE_KEY_FILE_MODE = 0o600
OCI_PRIVATE_KEY_MAX_BYTES = 128 * 1024
ORACLE_WALLET_MAX_BYTES = 64 * 1024 * 1024
ORACLE_WALLET_MAX_EXTRACTED_BYTES = 256 * 1024 * 1024
ORACLE_WALLET_DIR_NAME = "wallet"
ORACLE_WALLET_SKIPPED_FILES = frozenset({"ojdbc.properties", "README"})
ORACLE_ERROR_CODE_RE = re.compile(r"\b(?:ORA|DPY|DPI)-\d{4,5}\b", re.IGNORECASE)
ModelSettingsCheckStatus = Literal["ok", "missing", "invalid"]
ModelSettingsTestStatus = Literal["success", "failed"]
ModelSettingsTestTargetType = Literal[
    "enterprise_text",
    "enterprise_vision",
    "embedding",
    "rerank",
]
UploadStorageBackend = Literal["local", "oci"]
OciConfigTestStatus = Literal["success", "failed"]
OciConfigField = Literal["user", "fingerprint", "tenancy", "region", "key_file"]
_WEBSOCKET_COMMAND_DEDUPE_TTL_SECONDS = 300.0
_WEBSOCKET_COMMAND_DEDUPE_MAX_ENTRIES = 2000
_websocket_command_dedupe: dict[tuple[str, str], tuple[str, float]] = {}
_rbac_policy_cache: dict[str, tuple[ActorPolicy, float]] = {}
_jwt_jwks_cache: dict[str, tuple[dict[str, object], float]] = {}


class SettingsPatch(BaseModel):
    base_url: str | None = None
    timeout_seconds: float | None = None
    default_limit: int | None = None
    session_id: str | None = None


class ToolPolicySettings(BaseModel):
    default_mode: str = "approval"
    allow: list[str] = Field(default_factory=list)
    ask: list[str] = Field(default_factory=list)
    deny: list[str] = Field(default_factory=list)


class ToolPolicySettingsPatch(BaseModel):
    default_mode: str | None = None
    allow: list[str] | None = None
    ask: list[str] | None = None
    deny: list[str] | None = None


class RuntimeSafetySettings(BaseModel):
    max_tool_calls_per_run: int
    max_pending_approvals_per_run: int


class RuntimeSafetySettingsPatch(BaseModel):
    max_tool_calls_per_run: int | None = None
    max_pending_approvals_per_run: int | None = None


class CommandPolicySettings(BaseModel):
    enabled: bool
    workspace_root: str
    allowed_prefixes: list[str]
    default_timeout_seconds: float
    max_timeout_seconds: float
    output_limit_bytes: int
    artifact_storage_backend: str
    artifact_storage_path: str


class CommandPolicySettingsPatch(BaseModel):
    enabled: bool | None = None
    workspace_root: str | None = None
    allowed_prefixes: list[str] | None = None
    default_timeout_seconds: float | None = None
    max_timeout_seconds: float | None = None
    output_limit_bytes: int | None = None
    artifact_storage_backend: str | None = None
    artifact_storage_path: str | None = None


class PlannerSettings(BaseModel):
    provider: str
    oci_responses_base_url: str | None = None
    oci_responses_base_url_configured: bool
    oci_responses_api_key_configured: bool
    oci_responses_model: str | None = None
    oci_responses_model_configured: bool
    oci_responses_project: str | None = None
    oci_responses_project_configured: bool
    oci_agent_endpoint: str | None = None
    oci_agent_endpoint_configured: bool
    oci_agent_api_key_configured: bool
    timeout_seconds: float
    max_retries: int
    fallback_to_heuristic: bool
    allowed_tool_names: list[str]
    allow_command_generation: bool


class PlannerSettingsPatch(BaseModel):
    provider: str | None = None
    oci_responses_base_url: str | None = None
    oci_responses_model: str | None = None
    oci_responses_project: str | None = None
    oci_agent_endpoint: str | None = None
    enterprise_ai_endpoint: str | None = None
    timeout_seconds: float | None = None
    max_retries: int | None = None
    fallback_to_heuristic: bool | None = None
    allowed_tool_names: list[str] | None = None
    allow_command_generation: bool | None = None


class EnterpriseAiConfiguredModel(BaseModel):
    model_id: str = Field(default="", max_length=256)
    display_name: str = Field(default="", max_length=256)
    vision_enabled: bool = False

    @field_validator("model_id", "display_name")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()


class EnterpriseAiModelSettings(BaseModel):
    endpoint: str = Field(default="", max_length=2048)
    project_ocid: str = Field(default="", max_length=512)
    api_key: str = Field(default="", max_length=4096)
    has_api_key: bool = False
    clear_api_key: bool = False
    models: list[EnterpriseAiConfiguredModel] = Field(default_factory=list, max_length=20)
    default_model_id: str = Field(default="", max_length=256)
    api_path: str = Field(default="/responses", max_length=512)
    vlm_input_mode: str = "auto"
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
        return value.strip()

    @field_validator("text_payload_template", "vision_payload_template")
    @classmethod
    def validate_payload_template(cls, value: str) -> str:
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
        if value and not value.startswith("/"):
            raise ValueError("response path は / で始まる JSON Pointer で入力してください。")
        return value


class GenerativeAiModelSettings(BaseModel):
    embedding_model: str = Field(default="cohere.embed-v4.0", max_length=256)
    embedding_dim: int = Field(default=1536, ge=1536, le=1536)
    rerank_model: str = Field(default="cohere.rerank-v4.0-fast", max_length=256)

    @field_validator("embedding_model", "rerank_model")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()


class ModelSettingsPayload(BaseModel):
    enterprise_ai: EnterpriseAiModelSettings
    generative_ai: GenerativeAiModelSettings


class ModelSettingsData(BaseModel):
    settings: ModelSettingsPayload
    checks: dict[str, ModelSettingsCheckStatus]
    model_settings_file: str
    source: Literal["runtime"]


class ModelSettingsTestRequest(BaseModel):
    settings: ModelSettingsPayload
    target_type: ModelSettingsTestTargetType
    model_id: str = Field(default="", max_length=256)
    vision_enabled: bool = False

    @field_validator("model_id")
    @classmethod
    def strip_model_id(cls, value: str) -> str:
        return value.strip()


class ModelSettingsTestResult(BaseModel):
    status: ModelSettingsTestStatus
    target_type: ModelSettingsTestTargetType
    model_id: str
    message: str
    troubleshooting: list[str] = Field(default_factory=list)
    raw_error: str | None = None
    error_type: str | None = None
    elapsed_ms: int = 0
    checked_at: str
    details: dict[str, str | int | bool | None] = Field(default_factory=dict)


class DatabaseSettingsData(BaseModel):
    user: str = ""
    dsn: str = ""
    wallet_dir: str = ""
    wallet_uploaded: bool = False
    available_services: list[str] = Field(default_factory=list)
    has_password: bool = False
    has_wallet_password: bool = False
    readiness: str = "missing"
    embedding_dimension: int = 1536
    vector_column: str = "VECTOR(1536, FLOAT32)"
    adb_ocid: str = ""
    region: str = ""
    config_source: str = "runtime"


class DatabaseSettingsUpdate(BaseModel):
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
        return value.strip()


class DatabaseConnectionTestResult(BaseModel):
    status: str
    readiness: str
    message: str
    elapsed_ms: int = 0
    troubleshooting: list[str] = Field(default_factory=list)
    details: dict[str, str | int | float | bool | None] = Field(default_factory=dict)
    checked_at: str
    error_type: str | None = None


class AdbInfoData(BaseModel):
    status: str = "not_configured"
    message: str = ""
    id: str | None = None
    display_name: str | None = None
    lifecycle_state: str | None = None
    db_name: str | None = None
    cpu_core_count: int | None = None
    data_storage_size_in_tbs: int | None = None
    region: str | None = None


class AdbSettingsUpdate(BaseModel):
    adb_ocid: str = Field(default="", max_length=512)
    region: str = Field(default="", max_length=128)

    @field_validator("adb_ocid", "region")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()


class UploadStorageSettingsData(BaseModel):
    backend: UploadStorageBackend = "local"
    local_storage_dir: str = "/u01/production-ready-rag"
    object_storage_region: str = "ap-osaka-1"
    object_storage_namespace: str = ""
    object_storage_bucket: str = ""
    readiness: str = "missing"
    max_upload_bytes: int = 100 * 1024 * 1024
    config_source: str = "runtime"


class UploadStorageSettingsUpdate(BaseModel):
    backend: UploadStorageBackend = "local"
    local_storage_dir: str = Field(default="", max_length=1024)
    object_storage_namespace: str | None = Field(default=None, max_length=256)
    object_storage_bucket: str = Field(default="", max_length=256)

    @field_validator("local_storage_dir", "object_storage_bucket")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("object_storage_namespace")
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @field_validator("object_storage_namespace", "object_storage_bucket")
    @classmethod
    def validate_object_storage_name(cls, value: str | None) -> str | None:
        if value and not re.fullmatch(r"[A-Za-z0-9._-]+", value):
            raise ValueError(
                "Object Storage の値は英数字、ハイフン、アンダースコア、ドットで入力してください。"
            )
        return value


class OciConfigReadRequest(BaseModel):
    config_file: str = Field(default="~/.oci/config", max_length=1024)
    profile: str = Field(default="DEFAULT", max_length=128)

    @field_validator("config_file", "profile")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("config_file")
    @classmethod
    def require_config_file(cls, value: str) -> str:
        if not value:
            raise ValueError("OCI config ファイルの path を入力してください。")
        return value

    @field_validator("profile")
    @classmethod
    def validate_profile(cls, value: str) -> str:
        profile = value or "DEFAULT"
        if any(char in profile for char in "[]\r\n"):
            raise ValueError("プロファイル名に [ ] や改行は使用できません。")
        return profile


class OciConfigReadData(BaseModel):
    profile: str = "DEFAULT"
    user: str = ""
    fingerprint: str = ""
    tenancy: str = ""
    region: str = ""
    key_file: str = "~/.oci/oci_api_key.pem"
    applied_fields: list[OciConfigField] = Field(default_factory=list)


class OciSettingsUpdate(BaseModel):
    user: str = Field(default="", max_length=512)
    fingerprint: str = Field(default="", max_length=128)
    tenancy: str = Field(default="", max_length=512)
    region: str = Field(default="", max_length=128)

    @field_validator("user", "fingerprint", "tenancy", "region")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("user")
    @classmethod
    def validate_user_ocid(cls, value: str) -> str:
        if value and not value.startswith("ocid1.user."):
            raise ValueError("ユーザー OCID は ocid1.user. で始めてください。")
        return value

    @field_validator("fingerprint")
    @classmethod
    def validate_fingerprint(cls, value: str) -> str:
        if value and not re.fullmatch(r"[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2})+", value):
            raise ValueError("fingerprint は 16 進数をコロン区切りで入力してください。")
        return value

    @field_validator("tenancy")
    @classmethod
    def validate_tenancy_ocid(cls, value: str) -> str:
        if value and not value.startswith("ocid1.tenancy."):
            raise ValueError("テナンシ OCID は ocid1.tenancy. で始めてください。")
        return value

    @field_validator("region")
    @classmethod
    def validate_region(cls, value: str) -> str:
        if value and not re.fullmatch(r"[a-z0-9-]+", value):
            raise ValueError("リージョンは英小文字、数字、ハイフンで入力してください。")
        return value


class OciSettingsData(BaseModel):
    config_file: str = "~/.oci/config"
    profile: str = "DEFAULT"
    user: str = ""
    fingerprint: str = ""
    tenancy: str = ""
    region: str = ""
    key_file: str = "~/.oci/oci_api_key.pem"
    key_file_exists: bool = False
    config_file_exists: bool = False
    config_source: str = "runtime"


class OciObjectStorageSettingsUpdate(BaseModel):
    object_storage_region: str = Field(default="ap-osaka-1", max_length=128)
    object_storage_namespace: str = Field(default="", max_length=256)

    @field_validator("object_storage_region", "object_storage_namespace")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("object_storage_region")
    @classmethod
    def validate_region(cls, value: str) -> str:
        if value and not re.fullmatch(r"[a-z0-9-]+", value):
            raise ValueError("リージョンは英小文字、数字、ハイフンで入力してください。")
        return value

    @field_validator("object_storage_namespace")
    @classmethod
    def validate_namespace(cls, value: str) -> str:
        if value and not re.fullmatch(r"[A-Za-z0-9._-]+", value):
            raise ValueError(
                "Object Storage の値は英数字、ハイフン、アンダースコア、ドットで入力してください。"
            )
        return value


class OciConfigTestResult(BaseModel):
    status: OciConfigTestStatus
    profile: str = "DEFAULT"
    config_file: str = "~/.oci/config"
    key_file: str = "~/.oci/oci_api_key.pem"
    config_file_exists: bool = False
    key_file_exists: bool = False
    missing_fields: list[str] = Field(default_factory=list)
    permission_issues: list[str] = Field(default_factory=list)
    oci_directory_mode: str | None = None
    config_file_mode: str | None = None
    key_file_mode: str | None = None
    message: str
    checked_at: str
    error_type: str | None = None


class OciObjectStorageNamespaceRequest(BaseModel):
    config_file: str = Field(default="~/.oci/config", max_length=1024)
    profile: str = Field(default="DEFAULT", max_length=128)
    region: str = Field(default="ap-osaka-1", max_length=128)

    @field_validator("config_file", "profile", "region")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("config_file")
    @classmethod
    def require_config_file(cls, value: str) -> str:
        if not value:
            raise ValueError("OCI config ファイルの path を入力してください。")
        return value

    @field_validator("profile")
    @classmethod
    def validate_profile(cls, value: str) -> str:
        profile = value or "DEFAULT"
        if any(char in profile for char in "[]\r\n"):
            raise ValueError("プロファイル名に [ ] や改行は使用できません。")
        return profile

    @field_validator("region")
    @classmethod
    def require_region(cls, value: str) -> str:
        if not value:
            raise ValueError("Object Storage リージョンを入力してください。")
        return value


class OciObjectStorageNamespaceData(BaseModel):
    namespace: str


class OciPrivateKeyUploadData(BaseModel):
    key_file: str
    saved: bool


class ToolAuditRecord(BaseModel):
    step_id: str
    tool_name: str
    status: str
    approval_id: str | None = None
    approval_status: str | None = None
    policy_decision: str | None = None
    permission_level: str | None = None
    side_effects: bool | None = None
    started_at: str | None = None
    completed_at: str | None = None
    duration_ms: int | None = None
    success: bool | None = None
    error: str | None = None
    error_code: str | None = None
    guardrail_warnings: list[str] = Field(default_factory=list)
    trace_id: str | None = None
    artifact_ids: list[str] = Field(default_factory=list)
    audit_metadata: dict[str, object] = Field(default_factory=dict)


class RunAuditData(BaseModel):
    run_id: str
    goal: str
    status: str
    records: list[ToolAuditRecord]


class ToolCallAuditRecord(ToolAuditRecord):
    run_id: str
    run_goal: str
    run_status: str
    agent_id: str
    run_created_at: str
    run_updated_at: str


class ToolCallAuditData(BaseModel):
    total: int
    offset: int
    limit: int
    filters: dict[str, object] = Field(default_factory=dict)
    records: list[ToolCallAuditRecord]


class RuntimeSnapshotImportRequest(BaseModel):
    snapshot: AgentRuntimeSnapshot
    dry_run: bool = True
    confirm_replace: bool = False
    reason: str | None = None


class RuntimeSnapshotImportResult(BaseModel):
    imported: bool
    dry_run: bool
    validation: AgentRuntimeSnapshotValidation
    reason: str | None = None


class ActorPolicy(BaseModel):
    roles: set[str] = Field(default_factory=set)
    business_view_ids: set[str] | None = None
    agent_ids: set[str] | None = None


async def require_viewer(request: Request) -> None:
    _require_actor_roles(request, {"viewer", "operator", "approver", "auditor"})


async def require_operator(request: Request) -> None:
    _require_actor_roles(request, {"operator"})


async def require_approver(request: Request) -> None:
    _require_actor_roles(request, {"approver"})


async def require_auditor(request: Request) -> None:
    _require_actor_roles(request, {"auditor"})


async def require_admin(request: Request) -> None:
    _require_actor_roles(request, {"admin"})


_model_settings_state: ModelSettingsPayload | None = None
_database_settings_state: DatabaseSettingsData | None = None
_upload_storage_settings_state: UploadStorageSettingsData | None = None
_oci_settings_state: OciSettingsData | None = None
_adb_info_state: AdbInfoData | None = None


def _settings_attr(name: str, default: object) -> object:
    return getattr(get_settings(), name, default)


def _settings_str_from(settings: object, name: str, default: str = "") -> str:
    value = getattr(settings, name, default)
    return str(default if value is None else value)


def _settings_str(name: str, default: str = "") -> str:
    return _settings_str_from(get_settings(), name, default)


def _settings_first_from(settings: object, names: tuple[str, ...], default: object = "") -> object:
    for name in names:
        value = getattr(settings, name, None)
        if value is None:
            continue
        if isinstance(value, str) and not value:
            continue
        return value
    return default


def _settings_int_from(settings: object, name: str, default: int) -> int:
    value = getattr(settings, name, default)
    return _coerce_int(value, default)


def _coerce_int(value: object, default: int) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, float | str):
        try:
            return int(value)
        except ValueError:
            return default
    return default


def _settings_int(name: str, default: int) -> int:
    return _settings_int_from(get_settings(), name, default)


def _settings_float_from(settings: object, name: str, default: float) -> float:
    value = getattr(settings, name, default)
    return _coerce_float(value, default)


def _coerce_float(value: object, default: float) -> float:
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return default
    return default


def _settings_float(name: str, default: float) -> float:
    return _settings_float_from(get_settings(), name, default)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _secret_value(*, current: str, update: str | None, clear: bool) -> str:
    if clear:
        return ""
    if update is not None and update != "":
        return update
    return current


def _is_present(value: str) -> bool:
    return bool(value.strip())


def _secret_is_available(settings: EnterpriseAiModelSettings) -> bool:
    if settings.clear_api_key:
        return False
    return _is_present(settings.api_key) or settings.has_api_key


def _json_pointer_or_empty(value: str) -> str:
    normalized = value.strip()
    return normalized if not normalized or normalized.startswith("/") else ""


def _write_env_values(
    path: Path,
    values: dict[str, str],
    *,
    section_comment: str,
    error_detail: str,
) -> None:
    try:
        lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
        next_lines: list[str] = []
        written: set[str] = set()
        for line in lines:
            key = _env_assignment_key(line)
            if key not in values:
                next_lines.append(line)
                continue
            if key in written:
                continue
            next_lines.append(f"{key}={_format_env_value(values[key])}")
            written.add(key)

        missing = [key for key in values if key not in written]
        if missing:
            if next_lines and next_lines[-1].strip():
                next_lines.append("")
            next_lines.append(section_comment)
            for key in missing:
                next_lines.append(f"{key}={_format_env_value(values[key])}")

        _replace_env_file(path, "\n".join(next_lines).rstrip() + "\n")
    except OSError as exc:
        raise HTTPException(status_code=500, detail=error_detail) from exc


def _env_assignment_key(line: str) -> str | None:
    if line.lstrip().startswith("#"):
        return None
    match = ENV_ASSIGNMENT_RE.match(line)
    return match.group(1) if match else None


def _format_env_value(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        return ""
    if re.search(r"[\s#\"']", normalized):
        return '"' + normalized.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return normalized


def _replace_env_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else ENV_FILE_MODE
    tmp_path = path.with_name(f".{path.name}.tmp-{uuid4().hex}")
    try:
        tmp_path.write_text(content, encoding="utf-8")
        tmp_path.chmod(mode)
        tmp_path.replace(path)
        path.chmod(mode)
    finally:
        tmp_path.unlink(missing_ok=True)


def _model_settings_path(settings: object) -> Path:
    raw_path = _settings_str_from(settings, "model_settings_file", "model-settings.json")
    path = Path(raw_path).expanduser()
    return path if path.is_absolute() else BACKEND_ROOT / path


def _load_persisted_model_settings(settings: object) -> ModelSettingsPayload | None:
    path = _model_settings_path(settings)
    if not path.is_file():
        return None
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        payload = ModelSettingsPayload.model_validate(
            {
                "enterprise_ai": document.get("enterprise_ai", {}),
                "generative_ai": document.get("generative_ai", {}),
            }
        )
    except (OSError, ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=500,
            detail="モデル設定ファイルを読み取れませんでした。",
        ) from exc
    _apply_model_settings(settings, payload)
    return payload


def _persist_model_settings(settings: object, payload: ModelSettingsPayload) -> None:
    path = _model_settings_path(settings)
    document = _model_settings_document(payload)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_name(f".{path.name}.tmp-{uuid4().hex}")
        try:
            tmp_path.write_text(
                json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            tmp_path.chmod(MODEL_SETTINGS_FILE_MODE)
            tmp_path.replace(path)
            path.chmod(MODEL_SETTINGS_FILE_MODE)
        finally:
            tmp_path.unlink(missing_ok=True)
    except OSError as exc:
        raise HTTPException(
            status_code=500,
            detail="モデル設定を永続化ファイルへ保存できませんでした。",
        ) from exc


def _model_settings_document(payload: ModelSettingsPayload) -> dict[str, object]:
    enterprise_ai = payload.enterprise_ai
    generative_ai = payload.generative_ai
    return {
        "version": 1,
        "enterprise_ai": {
            "endpoint": enterprise_ai.endpoint,
            "project_ocid": enterprise_ai.project_ocid,
            "api_key": enterprise_ai.api_key,
            "models": [
                {
                    "model_id": model.model_id,
                    "display_name": model.display_name,
                    "vision_enabled": model.vision_enabled,
                }
                for model in enterprise_ai.models
                if model.model_id
            ],
            "default_model_id": enterprise_ai.default_model_id,
            "api_path": enterprise_ai.api_path,
            "vlm_input_mode": enterprise_ai.vlm_input_mode,
            "text_payload_template": enterprise_ai.text_payload_template,
            "vision_payload_template": enterprise_ai.vision_payload_template,
            "text_response_path": enterprise_ai.text_response_path,
            "vision_response_path": enterprise_ai.vision_response_path,
            "timeout_seconds": enterprise_ai.timeout_seconds,
            "max_retries": enterprise_ai.max_retries,
            "llm_max_output_tokens": enterprise_ai.llm_max_output_tokens,
            "vlm_max_output_tokens": enterprise_ai.vlm_max_output_tokens,
        },
        "generative_ai": {
            "embedding_model": generative_ai.embedding_model,
            "embedding_dim": generative_ai.embedding_dim,
            "rerank_model": generative_ai.rerank_model,
        },
    }


def _model_settings_with_resolved_secret(
    settings: object,
    request: ModelSettingsPayload,
) -> ModelSettingsPayload:
    current_api_key = str(
        _settings_first_from(
            settings,
            ("oci_enterprise_ai_api_key", "enterprise_ai_api_key"),
        )
    )
    enterprise_ai = request.enterprise_ai
    resolved_api_key = _secret_value(
        current=current_api_key,
        update=enterprise_ai.api_key,
        clear=enterprise_ai.clear_api_key,
    )
    resolved_enterprise_ai = enterprise_ai.model_copy(
        update={
            "api_key": resolved_api_key,
            "has_api_key": bool(resolved_api_key.strip()),
            "clear_api_key": False,
        }
    )
    return request.model_copy(update={"enterprise_ai": resolved_enterprise_ai})


def _apply_model_settings(settings: object, payload: ModelSettingsPayload) -> None:
    enterprise_ai = payload.enterprise_ai
    generative_ai = payload.generative_ai
    models = [model for model in enterprise_ai.models if model.model_id]
    default_model = enterprise_ai.default_model_id or (models[0].model_id if models else "")
    vision_model = (
        next((model.model_id for model in models if model.vision_enabled), default_model)
        if models
        else ""
    )
    updates: dict[str, object] = {
        "enterprise_ai_endpoint": enterprise_ai.endpoint,
        "enterprise_ai_project_ocid": enterprise_ai.project_ocid,
        "enterprise_ai_api_key": enterprise_ai.api_key,
        "enterprise_ai_default_model_id": default_model,
        "enterprise_ai_api_path": enterprise_ai.api_path,
        "enterprise_ai_vlm_input_mode": enterprise_ai.vlm_input_mode,
        "enterprise_ai_text_payload_template": enterprise_ai.text_payload_template,
        "enterprise_ai_vision_payload_template": enterprise_ai.vision_payload_template,
        "enterprise_ai_text_response_path": enterprise_ai.text_response_path,
        "enterprise_ai_vision_response_path": enterprise_ai.vision_response_path,
        "enterprise_ai_timeout_seconds": enterprise_ai.timeout_seconds,
        "enterprise_ai_max_retries": enterprise_ai.max_retries,
        "enterprise_ai_llm_max_output_tokens": enterprise_ai.llm_max_output_tokens,
        "enterprise_ai_vlm_max_output_tokens": enterprise_ai.vlm_max_output_tokens,
        "oci_enterprise_ai_endpoint": enterprise_ai.endpoint,
        "oci_enterprise_ai_project_ocid": enterprise_ai.project_ocid,
        "oci_enterprise_ai_api_key": enterprise_ai.api_key,
        "oci_enterprise_ai_models": models,
        "oci_enterprise_ai_default_model": default_model,
        "oci_enterprise_ai_llm_model": default_model,
        "oci_enterprise_ai_vlm_model": vision_model,
        "oci_enterprise_ai_llm_path": enterprise_ai.api_path,
        "oci_enterprise_ai_vlm_path": enterprise_ai.api_path,
        "oci_enterprise_ai_vlm_input_mode": enterprise_ai.vlm_input_mode,
        "oci_enterprise_ai_llm_payload_template": enterprise_ai.text_payload_template,
        "oci_enterprise_ai_vlm_payload_template": enterprise_ai.vision_payload_template,
        "oci_enterprise_ai_llm_response_path": enterprise_ai.text_response_path,
        "oci_enterprise_ai_vlm_response_path": enterprise_ai.vision_response_path,
        "oci_enterprise_ai_timeout_seconds": enterprise_ai.timeout_seconds,
        "oci_enterprise_ai_max_retries": enterprise_ai.max_retries,
        "oci_enterprise_ai_llm_max_output_tokens": enterprise_ai.llm_max_output_tokens,
        "oci_enterprise_ai_vlm_max_output_tokens": enterprise_ai.vlm_max_output_tokens,
        "embedding_model": generative_ai.embedding_model,
        "embedding_dim": generative_ai.embedding_dim,
        "rerank_model": generative_ai.rerank_model,
        "oci_genai_embedding_model": generative_ai.embedding_model,
        "oci_genai_embedding_dim": generative_ai.embedding_dim,
        "oci_genai_rerank_model": generative_ai.rerank_model,
    }
    for key, value in updates.items():
        try:
            setattr(settings, key, value)
        except (AttributeError, ValueError):
            continue


def _get_model_settings_state() -> ModelSettingsPayload:
    global _model_settings_state
    if _model_settings_state is None:
        settings = get_settings()
        persisted = _load_persisted_model_settings(settings)
        if persisted is not None:
            public_payload = _public_model_settings_payload(persisted)
            _model_settings_state = public_payload.model_copy(deep=True)
            return _model_settings_state.model_copy(deep=True)
        model_id = str(
            _settings_first_from(
                settings,
                (
                    "oci_enterprise_ai_default_model",
                    "enterprise_ai_default_model_id",
                    "agent_planner_oci_responses_model",
                ),
            )
            or "enterprise-llm"
        )
        api_key = str(
            _settings_first_from(
                settings,
                (
                    "oci_enterprise_ai_api_key",
                    "enterprise_ai_api_key",
                    "agent_planner_oci_responses_api_key",
                ),
            )
        )
        _model_settings_state = ModelSettingsPayload(
            enterprise_ai=EnterpriseAiModelSettings(
                endpoint=str(
                    _settings_first_from(
                        settings,
                        (
                            "oci_enterprise_ai_endpoint",
                            "enterprise_ai_endpoint",
                            "agent_planner_oci_responses_base_url",
                        ),
                    )
                ),
                project_ocid=str(
                    _settings_first_from(
                        settings,
                        (
                            "oci_enterprise_ai_project_ocid",
                            "enterprise_ai_project_ocid",
                            "agent_planner_oci_responses_project",
                        ),
                    )
                ),
                api_key="",
                has_api_key=bool(api_key),
                models=_enterprise_model_catalog(settings, model_id),
                default_model_id=model_id,
                api_path=str(
                    _settings_first_from(
                        settings,
                        ("oci_enterprise_ai_llm_path", "enterprise_ai_api_path"),
                        "/responses",
                    )
                ),
                vlm_input_mode=str(
                    _settings_first_from(
                        settings,
                        ("oci_enterprise_ai_vlm_input_mode", "enterprise_ai_vlm_input_mode"),
                        "auto",
                    )
                ),
                text_payload_template=str(
                    _settings_first_from(
                        settings,
                        (
                            "oci_enterprise_ai_llm_payload_template",
                            "enterprise_ai_text_payload_template",
                        ),
                    )
                ),
                vision_payload_template=str(
                    _settings_first_from(
                        settings,
                        (
                            "oci_enterprise_ai_vlm_payload_template",
                            "enterprise_ai_vision_payload_template",
                        ),
                    )
                ),
                text_response_path=_json_pointer_or_empty(
                    str(
                        _settings_first_from(
                            settings,
                            (
                                "oci_enterprise_ai_llm_response_path",
                                "enterprise_ai_text_response_path",
                            ),
                        )
                    )
                ),
                vision_response_path=_json_pointer_or_empty(
                    str(
                        _settings_first_from(
                            settings,
                            (
                                "oci_enterprise_ai_vlm_response_path",
                                "enterprise_ai_vision_response_path",
                            ),
                        )
                    )
                ),
                timeout_seconds=_coerce_float(
                    _settings_first_from(
                        settings,
                        ("oci_enterprise_ai_timeout_seconds", "enterprise_ai_timeout_seconds"),
                        600.0,
                    ),
                    600.0,
                ),
                max_retries=_coerce_int(
                    _settings_first_from(
                        settings,
                        ("oci_enterprise_ai_max_retries", "enterprise_ai_max_retries"),
                        3,
                    ),
                    3,
                ),
                llm_max_output_tokens=_coerce_int(
                    _settings_first_from(
                        settings,
                        (
                            "oci_enterprise_ai_llm_max_output_tokens",
                            "enterprise_ai_llm_max_output_tokens",
                        ),
                        1200,
                    ),
                    1200,
                ),
                vlm_max_output_tokens=_coerce_int(
                    _settings_first_from(
                        settings,
                        (
                            "oci_enterprise_ai_vlm_max_output_tokens",
                            "enterprise_ai_vlm_max_output_tokens",
                        ),
                        65536,
                    ),
                    65536,
                ),
            ),
            generative_ai=GenerativeAiModelSettings(
                embedding_model=str(
                    _settings_first_from(
                        settings,
                        ("oci_genai_embedding_model", "embedding_model"),
                        "cohere.embed-v4.0",
                    )
                ),
                embedding_dim=_coerce_int(
                    _settings_first_from(
                        settings,
                        ("oci_genai_embedding_dim", "embedding_dim"),
                        1536,
                    ),
                    1536,
                ),
                rerank_model=str(
                    _settings_first_from(
                        settings,
                        ("oci_genai_rerank_model", "rerank_model"),
                        "cohere.rerank-v4.0-fast",
                    )
                ),
            ),
        )
    return _model_settings_state.model_copy(deep=True)


def _enterprise_model_catalog(
    settings: object,
    fallback_model_id: str,
) -> list[EnterpriseAiConfiguredModel]:
    raw_models = _settings_first_from(settings, ("oci_enterprise_ai_models",), [])
    models: list[EnterpriseAiConfiguredModel] = []
    if isinstance(raw_models, list):
        for item in raw_models:
            try:
                if isinstance(item, EnterpriseAiConfiguredModel):
                    model = item
                elif isinstance(item, Mapping):
                    model = EnterpriseAiConfiguredModel.model_validate(item)
                else:
                    continue
            except ValueError:
                continue
            if model.model_id:
                models.append(model)
    if models:
        return models
    if not fallback_model_id:
        return []
    return [
        EnterpriseAiConfiguredModel(
            model_id=fallback_model_id,
            display_name="業務 RAG 標準",
            vision_enabled=True,
        )
    ]


def _set_model_settings_state(payload: ModelSettingsPayload) -> ModelSettingsPayload:
    global _model_settings_state
    current = _get_model_settings_state()
    stored = payload.model_copy(deep=True)
    resolved_api_key = _secret_value(
        current="__saved__" if current.enterprise_ai.has_api_key else "",
        update=stored.enterprise_ai.api_key,
        clear=stored.enterprise_ai.clear_api_key,
    )
    stored.enterprise_ai.has_api_key = bool(resolved_api_key.strip())
    stored.enterprise_ai.api_key = ""
    stored.enterprise_ai.clear_api_key = False
    _model_settings_state = stored
    return stored.model_copy(deep=True)


def _model_settings_checks(payload: ModelSettingsPayload) -> dict[str, ModelSettingsCheckStatus]:
    return {
        "enterprise_ai": _enterprise_ai_status(payload.enterprise_ai),
        "generative_ai": _generative_ai_status(payload.generative_ai),
        "embedding_dim": _embedding_dim_status(payload.generative_ai),
    }


def _enterprise_ai_status(settings: EnterpriseAiModelSettings) -> ModelSettingsCheckStatus:
    required = (settings.endpoint, settings.project_ocid, settings.api_path)
    if not all(_is_present(value) for value in required):
        return "missing"
    if not settings.endpoint.startswith(("http://", "https://")):
        return "invalid"
    if not settings.project_ocid.startswith("ocid1.generativeaiproject."):
        return "invalid"
    if not settings.api_path.startswith(("/", "http://", "https://")):
        return "invalid"
    if not _secret_is_available(settings):
        return "missing"
    model_ids = [model.model_id for model in settings.models if _is_present(model.model_id)]
    if len(model_ids) != len(settings.models):
        return "missing"
    if len(model_ids) != len(set(model_ids)):
        return "invalid"
    if not model_ids or not _is_present(settings.default_model_id):
        return "missing"
    if settings.default_model_id not in model_ids:
        return "invalid"
    if not any(model.vision_enabled for model in settings.models if _is_present(model.model_id)):
        return "missing"
    return "ok"


def _generative_ai_status(settings: GenerativeAiModelSettings) -> ModelSettingsCheckStatus:
    if _embedding_dim_status(settings) == "invalid":
        return "invalid"
    required = (settings.embedding_model, settings.rerank_model)
    return "ok" if all(_is_present(value) for value in required) else "missing"


def _embedding_dim_status(settings: GenerativeAiModelSettings) -> ModelSettingsCheckStatus:
    return "ok" if settings.embedding_dim == 1536 else "invalid"


def _model_settings_data(
    payload: ModelSettingsPayload | None = None,
    settings: object | None = None,
) -> ModelSettingsData:
    settings_payload = payload or _get_model_settings_state()
    runtime_settings = settings or get_settings()
    return ModelSettingsData(
        settings=_public_model_settings_payload(settings_payload),
        checks=_model_settings_checks(settings_payload),
        model_settings_file=_settings_str_from(
            runtime_settings,
            "model_settings_file",
            "model-settings.json",
        ),
        source="runtime",
    )


def _public_model_settings_payload(payload: ModelSettingsPayload) -> ModelSettingsPayload:
    enterprise_ai = payload.enterprise_ai.model_copy(
        update={
            "api_key": "",
            "has_api_key": (
                not payload.enterprise_ai.clear_api_key
                and (
                    payload.enterprise_ai.has_api_key
                    or _is_present(payload.enterprise_ai.api_key)
                )
            ),
            "clear_api_key": False,
        }
    )
    return ModelSettingsPayload(
        enterprise_ai=enterprise_ai,
        generative_ai=payload.generative_ai,
    )


def _get_database_settings_state() -> DatabaseSettingsData:
    global _database_settings_state
    if _database_settings_state is None:
        _database_settings_state = _database_settings_data(get_settings())
    return _database_settings_state.model_copy(deep=True)


def _set_database_settings_state(patch: DatabaseSettingsUpdate) -> DatabaseSettingsData:
    global _database_settings_state
    candidate = _database_settings_candidate(_get_database_settings_state(), patch)
    _database_settings_state = candidate
    return candidate.model_copy(deep=True)


def _database_settings_data(settings: object) -> DatabaseSettingsData:
    dsn = str(_settings_first_from(settings, ("oracle_dsn", "agent_runtime_oracle_dsn")))
    user = str(_settings_first_from(settings, ("oracle_user", "agent_runtime_oracle_user")))
    wallet_dir = _settings_str_from(settings, "oracle_wallet_dir")
    wallet_path = Path(wallet_dir).expanduser() if wallet_dir else None
    wallet_uploaded = bool(_settings_first_from(settings, ("oracle_wallet_uploaded",), False))
    available_services: list[str] = [dsn] if dsn else []
    if wallet_path is not None and wallet_path.is_dir():
        wallet_uploaded = True
        available_services = _extract_wallet_services(wallet_path) or available_services
    has_password = bool(
        _settings_first_from(settings, ("oracle_password", "agent_runtime_oracle_password"))
    )
    embedding_dim = _coerce_int(
        _settings_first_from(settings, ("oci_genai_embedding_dim", "embedding_dim"), 1536),
        1536,
    )
    data = DatabaseSettingsData(
        user=user,
        dsn=dsn,
        wallet_dir=wallet_dir,
        wallet_uploaded=wallet_uploaded,
        available_services=available_services,
        has_password=has_password,
        has_wallet_password=bool(_settings_str_from(settings, "oracle_wallet_password")),
        embedding_dimension=embedding_dim,
        vector_column=f"VECTOR({embedding_dim}, FLOAT32)",
        adb_ocid=_settings_str_from(settings, "oracle_adb_ocid")
        or _settings_str_from(settings, "adb_ocid"),
        region=_settings_str_from(settings, "oci_region")
        or _settings_str_from(settings, "oracle_region"),
        config_source="runtime",
    )
    data.readiness = _database_readiness(data)
    return data


def _extract_wallet_services(wallet_path: Path) -> list[str]:
    tnsnames = wallet_path / "tnsnames.ora"
    if not tnsnames.is_file():
        nested = list(wallet_path.rglob("tnsnames.ora"))
        tnsnames = nested[0] if nested else tnsnames
    try:
        content = tnsnames.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []
    services: list[str] = []
    for line in content.splitlines():
        match = re.match(r"^\s*([A-Za-z0-9_.-]+)\s*=", line)
        if match:
            services.append(match.group(1))
    return services


def _database_settings_candidate(
    base: DatabaseSettingsData,
    payload: DatabaseSettingsUpdate,
) -> DatabaseSettingsData:
    candidate = base.model_copy(
        update={
            "user": payload.user,
            "dsn": payload.dsn,
            "wallet_dir": payload.wallet_dir or base.wallet_dir,
            "available_services": [payload.dsn] if payload.dsn else [],
            "has_password": bool(
                _secret_value(
                    current="__saved__" if base.has_password else "",
                    update=payload.password,
                    clear=payload.clear_password,
                )
            ),
            "has_wallet_password": bool(
                _secret_value(
                    current="__saved__" if base.has_wallet_password else "",
                    update=payload.wallet_password,
                    clear=payload.clear_wallet_password,
                )
            ),
        }
    )
    candidate.readiness = _database_readiness(candidate)
    return candidate


def _database_readiness(data: DatabaseSettingsData) -> str:
    if not data.user or not data.dsn:
        return "missing"
    if not (data.has_password or data.wallet_uploaded or data.has_wallet_password):
        return "missing_credentials"
    return "ok"


def _upload_storage_settings_data(settings: object) -> UploadStorageSettingsData:
    data = UploadStorageSettingsData(
        backend=_settings_str_from(settings, "upload_storage_backend", "local"),
        local_storage_dir=_settings_str_from(
            settings,
            "local_storage_dir",
            "/u01/production-ready-rag",
        ),
        object_storage_region=_settings_str_from(
            settings,
            "object_storage_region",
            "ap-osaka-1",
        ),
        object_storage_namespace=_settings_str_from(settings, "object_storage_namespace"),
        object_storage_bucket=_settings_str_from(settings, "object_storage_bucket"),
        max_upload_bytes=_settings_int_from(settings, "max_upload_bytes", 100 * 1024 * 1024),
        config_source="runtime",
    )
    data.readiness = _upload_storage_readiness(data)
    return data


def _get_upload_storage_settings_state() -> UploadStorageSettingsData:
    global _upload_storage_settings_state
    if _upload_storage_settings_state is None:
        _upload_storage_settings_state = _upload_storage_settings_data(get_settings())
    return _upload_storage_settings_state.model_copy(deep=True)


def _set_upload_storage_settings_state(
    patch: UploadStorageSettingsUpdate,
) -> UploadStorageSettingsData:
    global _upload_storage_settings_state
    candidate = _upload_storage_settings_candidate(_get_upload_storage_settings_state(), patch)
    _upload_storage_settings_state = candidate
    return candidate.model_copy(deep=True)


def _upload_storage_settings_candidate(
    base: UploadStorageSettingsData,
    payload: UploadStorageSettingsUpdate,
) -> UploadStorageSettingsData:
    candidate = base.model_copy(
        update={
            "backend": payload.backend,
            "local_storage_dir": payload.local_storage_dir,
            "object_storage_namespace": (
                payload.object_storage_namespace
                if payload.object_storage_namespace is not None
                else base.object_storage_namespace
            ),
            "object_storage_bucket": payload.object_storage_bucket,
        }
    )
    candidate.readiness = _upload_storage_readiness(candidate)
    return candidate


def _upload_storage_readiness(data: UploadStorageSettingsData) -> str:
    if data.backend == "oci":
        if not data.object_storage_namespace or not data.object_storage_bucket:
            return "missing"
        return "ok"
    return "ok" if data.local_storage_dir else "missing"


def _oci_settings_data(settings: object) -> OciSettingsData:
    parsed = _read_runtime_oci_config(settings)
    config_file = _settings_str_from(settings, "oci_config_file", "~/.oci/config")
    profile = _settings_str_from(settings, "oci_config_profile", "DEFAULT")
    region = _settings_str_from(settings, "oci_region", "ap-osaka-1")
    config_path = Path(config_file).expanduser()
    key_path = Path(OCI_PRIVATE_KEY_FILE).expanduser()

    return OciSettingsData(
        config_file=config_file,
        profile=profile,
        user=parsed.user if parsed is not None else "",
        fingerprint=parsed.fingerprint if parsed is not None else "",
        tenancy=parsed.tenancy if parsed is not None else "",
        region=region.strip() or (parsed.region if parsed is not None else ""),
        key_file=OCI_PRIVATE_KEY_FILE,
        key_file_exists=key_path.is_file(),
        config_file_exists=config_path.is_file(),
        config_source="runtime",
    )


def _read_runtime_oci_config(settings: object) -> OciConfigReadData | None:
    try:
        content = _read_oci_config_text(
            _settings_str_from(settings, "oci_config_file", "~/.oci/config")
        )
        return _parse_oci_config(
            content,
            _settings_str_from(settings, "oci_config_profile", "DEFAULT"),
        )
    except HTTPException:
        return None


def _get_oci_settings_state() -> OciSettingsData:
    global _oci_settings_state
    if _oci_settings_state is None:
        _oci_settings_state = _oci_settings_data(get_settings())
    return _oci_settings_state.model_copy(deep=True)


def _set_oci_settings_state(patch: OciSettingsUpdate) -> OciSettingsData:
    global _oci_settings_state
    current = _get_oci_settings_state()
    current.user = patch.user.strip()
    current.fingerprint = patch.fingerprint.strip()
    current.tenancy = patch.tenancy.strip()
    current.region = patch.region.strip()
    current.config_file_exists = bool(
        current.user and current.fingerprint and current.tenancy and current.region
    )
    _oci_settings_state = current
    return current.model_copy(deep=True)


def _read_oci_config_text(config_file: str) -> str:
    path = Path(config_file).expanduser()
    try:
        if not path.is_file():
            raise HTTPException(
                status_code=404,
                detail=(
                    "OCI config ファイルを読み取れません。"
                    "バックエンドから参照できる path を指定してください。"
                ),
            )
        if path.stat().st_size > OCI_CONFIG_MAX_BYTES:
            raise HTTPException(status_code=413, detail="OCI config ファイルが大きすぎます。")
        return path.read_text(encoding="utf-8")
    except HTTPException:
        raise
    except UnicodeDecodeError as exc:
        raise HTTPException(
            status_code=400,
            detail="OCI config ファイルは UTF-8 テキストとして読み取れる必要があります。",
        ) from exc
    except OSError as exc:
        raise HTTPException(
            status_code=404,
            detail=(
                "OCI config ファイルを読み取れません。"
                "バックエンドから参照できる path を指定してください。"
            ),
        ) from exc


def _parse_oci_config(content: str, profile: str) -> OciConfigReadData:
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read_string(content)
    except configparser.Error as exc:
        raise HTTPException(
            status_code=400,
            detail="OCI config ファイルの形式を確認してください。",
        ) from exc

    selected_profile = profile.strip() or "DEFAULT"
    if selected_profile.upper() == "DEFAULT":
        entries = parser.defaults()
    elif parser.has_section(selected_profile):
        entries = parser[selected_profile]
    else:
        raise HTTPException(
            status_code=404,
            detail="指定した OCI config profile が見つかりません。",
        )

    values = {key: str(entries.get(key, "")).strip() for key in OCI_CONFIG_KEYS}
    applied_fields = [key for key in OCI_CONFIG_KEYS if values[key]]
    if not applied_fields:
        raise HTTPException(
            status_code=422,
            detail="指定した profile から OCI config 項目を読み取れませんでした。",
        )

    return OciConfigReadData(
        profile=selected_profile,
        user=values["user"],
        fingerprint=values["fingerprint"],
        tenancy=values["tenancy"],
        region=values["region"],
        key_file=values["key_file"],
        applied_fields=applied_fields,
    )


def _safe_oci_profile_name(profile: str) -> str:
    selected = profile.strip() or "DEFAULT"
    if any(char in selected for char in "[]\r\n"):
        raise HTTPException(status_code=422, detail="プロファイル名に [ ] や改行は使用できません。")
    return selected


def _write_oci_config(settings: object, payload: OciSettingsUpdate) -> Path:
    target = Path(_settings_str_from(settings, "oci_config_file", "~/.oci/config")).expanduser()
    profile = _safe_oci_profile_name(_settings_str_from(settings, "oci_config_profile", "DEFAULT"))
    parser = _load_oci_config_for_write(target)
    values = {
        "user": payload.user,
        "fingerprint": payload.fingerprint,
        "tenancy": payload.tenancy,
        "region": payload.region,
        "key_file": OCI_PRIVATE_KEY_FILE,
    }
    _set_oci_config_profile(parser, profile, values)
    _atomic_write_oci_config(target, parser)
    return target


def _load_oci_config_for_write(path: Path) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(interpolation=None)
    if not path.exists():
        return parser
    if path.is_dir():
        raise HTTPException(
            status_code=400,
            detail="OCI config ファイル path がディレクトリを指しています。",
        )
    try:
        if path.stat().st_size > OCI_CONFIG_MAX_BYTES:
            raise HTTPException(status_code=413, detail="OCI config ファイルが大きすぎます。")
        content = path.read_text(encoding="utf-8")
    except HTTPException:
        raise
    except UnicodeDecodeError as exc:
        raise HTTPException(
            status_code=400,
            detail="OCI config ファイルは UTF-8 テキストとして読み取れる必要があります。",
        ) from exc
    except OSError as exc:
        raise HTTPException(
            status_code=500,
            detail="OCI config ファイルを更新前に読み取れませんでした。",
        ) from exc
    if not content.strip():
        return parser
    try:
        parser.read_string(content)
    except configparser.Error as exc:
        raise HTTPException(
            status_code=400,
            detail="OCI config ファイルの形式を確認してください。",
        ) from exc
    return parser


def _set_oci_config_profile(
    parser: configparser.ConfigParser,
    profile: str,
    values: dict[str, str],
) -> None:
    if profile.upper() == "DEFAULT":
        for key, value in values.items():
            parser["DEFAULT"][key] = value
        return
    if not parser.has_section(profile):
        parser.add_section(profile)
    for key, value in values.items():
        parser[profile][key] = value


def _atomic_write_oci_config(path: Path, parser: configparser.ConfigParser) -> None:
    tmp_path = path.with_name(f".{path.name}.tmp-{uuid4().hex}")
    try:
        _ensure_private_directory(path.parent)
        buffer = StringIO()
        parser.write(buffer, space_around_delimiters=False)
        tmp_path.write_text(buffer.getvalue(), encoding="utf-8")
        tmp_path.chmod(OCI_CONFIG_FILE_MODE)
        tmp_path.replace(path)
        path.chmod(OCI_CONFIG_FILE_MODE)
    except OSError as exc:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=500,
            detail="OCI config ファイルをバックエンドの固定 path へ保存できませんでした。",
        ) from exc


def _ensure_private_directory(path: Path) -> None:
    path.mkdir(mode=OCI_DIRECTORY_MODE, parents=True, exist_ok=True)
    path.chmod(OCI_DIRECTORY_MODE)


def _persist_oci_settings(settings: object, payload: OciSettingsUpdate) -> None:
    _write_env_values(
        BACKEND_ENV_FILE,
        {
            "OCI_CONFIG_FILE": _settings_str_from(settings, "oci_config_file", "~/.oci/config"),
            "OCI_CONFIG_PROFILE": _settings_str_from(settings, "oci_config_profile", "DEFAULT"),
            "OCI_REGION": payload.region,
        },
        section_comment="# OCI 共通",
        error_detail="OCI 認証設定を backend/.env へ保存できませんでした。",
    )


def _persist_oci_object_storage_settings(settings: object) -> None:
    _write_env_values(
        BACKEND_ENV_FILE,
        {
            "OBJECT_STORAGE_REGION": _settings_str_from(
                settings,
                "object_storage_region",
                "ap-osaka-1",
            ),
            "OBJECT_STORAGE_NAMESPACE": _settings_str_from(settings, "object_storage_namespace"),
        },
        section_comment="# OCI Object Storage",
        error_detail="OCI Object Storage 設定を backend/.env へ保存できませんでした。",
    )


def _persist_upload_storage_settings(data: UploadStorageSettingsData) -> None:
    values = {
        "UPLOAD_STORAGE_BACKEND": data.backend,
        "LOCAL_STORAGE_DIR": data.local_storage_dir,
    }
    if data.backend == "oci":
        values["OBJECT_STORAGE_REGION"] = data.object_storage_region
        values["OBJECT_STORAGE_NAMESPACE"] = data.object_storage_namespace
        values["OBJECT_STORAGE_BUCKET"] = data.object_storage_bucket
    _write_env_values(
        BACKEND_ENV_FILE,
        values,
        section_comment="# アップロード保存先",
        error_detail="アップロード保存先設定を backend/.env へ保存できませんでした。",
    )


def _persist_database_settings(
    settings: object,
    data: DatabaseSettingsData,
    payload: DatabaseSettingsUpdate,
) -> None:
    oracle_password = _secret_value(
        current=_settings_str_from(settings, "oracle_password")
        or _settings_str_from(settings, "agent_runtime_oracle_password"),
        update=payload.password,
        clear=payload.clear_password,
    )
    wallet_password = _secret_value(
        current=_settings_str_from(settings, "oracle_wallet_password"),
        update=payload.wallet_password,
        clear=payload.clear_wallet_password,
    )
    _write_env_values(
        BACKEND_ENV_FILE,
        {
            "ORACLE_USER": data.user,
            "ORACLE_PASSWORD": oracle_password,
            "ORACLE_DSN": data.dsn,
            "ORACLE_WALLET_DIR": data.wallet_dir,
            "ORACLE_WALLET_PASSWORD": wallet_password,
        },
        section_comment="# Oracle 26ai",
        error_detail="Oracle 26ai 接続設定を backend/.env へ保存できませんでした。",
    )
    _set_if_possible(settings, "oracle_user", data.user)
    _set_if_possible(settings, "oracle_password", oracle_password)
    _set_if_possible(settings, "oracle_dsn", data.dsn)
    _set_if_possible(settings, "oracle_wallet_dir", data.wallet_dir)
    _set_if_possible(settings, "oracle_wallet_password", wallet_password)


def _database_connection_test_candidate(
    settings: object,
    payload: DatabaseSettingsUpdate,
) -> SimpleNamespace:
    base = _database_settings_data(settings)
    candidate = _database_settings_candidate(base, payload)
    oracle_password = _secret_value(
        current=_settings_str_from(settings, "oracle_password")
        or _settings_str_from(settings, "agent_runtime_oracle_password"),
        update=payload.password,
        clear=payload.clear_password,
    )
    wallet_password = _secret_value(
        current=_settings_str_from(settings, "oracle_wallet_password"),
        update=payload.wallet_password,
        clear=payload.clear_wallet_password,
    )
    return SimpleNamespace(
        oracle_user=candidate.user,
        oracle_dsn=candidate.dsn,
        oracle_password=oracle_password,
        oracle_wallet_dir=candidate.wallet_dir,
        oracle_wallet_password=wallet_password,
        oracle_tcp_connect_timeout_seconds=_coerce_float(
            _settings_first_from(settings, ("oracle_tcp_connect_timeout_seconds",), 10.0),
            10.0,
        ),
        oracle_db_test_timeout_seconds=_coerce_float(
            _settings_first_from(settings, ("oracle_db_test_timeout_seconds",), 15.0),
            15.0,
        ),
        readiness=_database_readiness(candidate),
        display_dsn=candidate.dsn,
    )


async def _test_oracle_connection(settings: SimpleNamespace) -> None:
    timeout_seconds = float(settings.oracle_db_test_timeout_seconds)
    try:
        with fail_after(timeout_seconds):
            await anyio_to_thread.run_sync(_test_oracle_connection_sync, settings)
    except TimeoutError as exc:
        raise OracleConnectionTimeoutError(
            f"Oracle 26ai 接続テストが {timeout_seconds:g} 秒でタイムアウトしました。"
            "データベースの起動状態、Wallet サービス名、ネットワーク到達性を確認してください。"
        ) from exc


def _test_oracle_connection_sync(settings: SimpleNamespace) -> None:
    oracledb = import_module("oracledb")
    connection = oracledb.connect(**_oracle_connect_kwargs(settings))
    try:
        cursor = connection.cursor()
        try:
            cursor.execute("SELECT 1 FROM DUAL")
            cursor.fetchone()
        finally:
            cursor.close()
    finally:
        connection.close()


def _oracle_connect_kwargs(settings: SimpleNamespace) -> dict[str, object]:
    kwargs: dict[str, object] = {
        "user": settings.oracle_user,
        "dsn": settings.oracle_dsn,
        "retry_count": 0,
        "retry_delay": 0,
    }
    tcp_connect_timeout = float(settings.oracle_tcp_connect_timeout_seconds)
    if tcp_connect_timeout > 0:
        kwargs["tcp_connect_timeout"] = tcp_connect_timeout
    if str(settings.oracle_password).strip():
        kwargs["password"] = settings.oracle_password
    wallet_dir = str(settings.oracle_wallet_dir or "").strip()
    if wallet_dir:
        kwargs["config_dir"] = str(Path(wallet_dir).expanduser())
        kwargs["wallet_location"] = str(Path(wallet_dir).expanduser())
    if str(settings.oracle_wallet_password).strip():
        kwargs["wallet_password"] = settings.oracle_wallet_password
    return kwargs


def _oracle_error_codes(error_text: str) -> list[str]:
    return list(dict.fromkeys(match.upper() for match in ORACLE_ERROR_CODE_RE.findall(error_text)))


def _database_connection_error_message(exc: Exception, oracle_error_codes: list[str]) -> str:
    if getattr(exc, "safe_for_user", False):
        return str(exc)

    code_label = f"（{', '.join(oracle_error_codes)}）" if oracle_error_codes else ""
    code_set = set(oracle_error_codes)
    if isinstance(exc, ModuleNotFoundError):
        return "python-oracledb がインストールされていないため、Oracle 26ai へ接続できません。"
    if "ORA-01017" in code_set:
        return (
            f"Oracle 26ai へ接続できませんでした{code_label}。"
            "ユーザー名または DB パスワードを確認してください。"
        )
    if "ORA-12154" in code_set:
        return (
            f"Oracle 26ai へ接続できませんでした{code_label}。"
            "Wallet サービス名が tnsnames.ora に存在するか確認してください。"
        )
    if "ORA-12506" in code_set:
        return (
            f"Oracle 26ai へ接続できませんでした{code_label}。"
            "ADB のアクセス制御リストまたは network ACL が"
            "この接続元を許可しているか確認してください。"
        )
    if code_set & {"ORA-12514", "ORA-12505"}:
        return (
            f"Oracle 26ai へ接続できませんでした{code_label}。"
            "Wallet サービス名と ADB の稼働状態を確認してください。"
        )
    if code_set & {"ORA-12541", "DPY-6005", "DPY-6000"}:
        return (
            f"Oracle 26ai へ接続できませんでした{code_label}。"
            "ADB の listener と TCPS 1522 への到達性を確認してください。"
        )
    if "DPY-4011" in code_set:
        return (
            f"Oracle 26ai へ接続できませんでした{code_label}。"
            "Wallet ZIP と Wallet パスワードを確認してください。"
        )
    if code_set & {"DPI-1047", "DPI-1072"}:
        return (
            f"Oracle 26ai へ接続できませんでした{code_label}。"
            "Oracle Instant Client の配置を確認してください。"
        )
    if oracle_error_codes:
        return (
            f"Oracle 26ai へ接続できませんでした{code_label}。"
            "下の確認ポイントと backend ログを確認してください。"
        )
    return "Oracle 26ai へ接続できませんでした。下の確認ポイントと backend ログを確認してください。"


def _database_connection_troubleshooting(
    *,
    readiness: str,
    error_text: str = "",
    error_type: str = "",
) -> list[str]:
    tips: list[str] = []
    if readiness == "missing":
        tips.append("ユーザー名、DSN、Wallet ZIP が入力・アップロード済みか確認してください。")
    if readiness == "missing_credentials":
        tips.append("DB パスワードまたは Wallet パスワードが保存済みか確認してください。")

    combined = f"{error_text} {error_type}".lower()
    if "modulenotfounderror" in combined or "oracledb" in combined:
        tips.append("backend の依存関係に python-oracledb が含まれているか確認してください。")
    if any(token in combined for token in ("timeout", "timed out", "oracleconnectiontimeouterror")):
        tips.append(
            "接続テストがタイムアウトしました。ADB が起動中か、ネットワーク経路から "
            "TCPS 1522 に到達できるか確認してください。"
        )
    if "ora-01017" in combined:
        tips.append("ユーザー名または DB パスワードが正しいか確認してください。")
    if "ora-12154" in combined or "tns" in combined:
        tips.append("Wallet サービス名が tnsnames.ora に存在するか確認してください。")
    if "ora-12506" in combined:
        tips.append("ADB のアクセス制御リストまたは network ACL を確認してください。")
    if "dpy-4011" in combined:
        tips.append("Wallet ZIP と Wallet パスワードを確認してください。")
    if "dpi-1047" in combined:
        tips.append(
            "Oracle Instant Client が必要な実行環境では"
            "配置とライブラリパスを確認してください。"
        )
    if not tips:
        tips.append("backend ログの Oracle エラーコードと設定値を確認してください。")
    return list(dict.fromkeys(tips))


def _elapsed_ms(started: float) -> int:
    return max(0, round((monotonic() - started) * 1000))


def _set_if_possible(target: object, name: str, value: object) -> None:
    try:
        setattr(target, name, value)
    except (AttributeError, ValueError):
        return


class OciPrivateKeyPassPhraseRequiredError(RuntimeError):
    safe_for_user = True


class OracleConnectionTimeoutError(RuntimeError):
    safe_for_user = True


def _load_oci_config_without_prompt(
    oci_config_module: object,
    config_file: str,
    profile: str,
    *,
    region: str | None = None,
) -> dict[str, object]:
    config_path = Path(config_file).expanduser()
    from_file = cast(Any, oci_config_module).from_file
    config = dict(from_file(str(config_path), profile))
    if region:
        config["region"] = region
    _assert_oci_private_key_can_load_without_prompt(config, config_path)
    return config


def _assert_oci_private_key_can_load_without_prompt(
    config: Mapping[str, object],
    config_file: str | Path,
) -> None:
    key_file = str(config.get("key_file", "") or "").strip()
    if not key_file or _has_pass_phrase(config):
        return
    key_path = _resolve_oci_key_file(key_file, config_file)
    if _pem_file_is_encrypted(key_path):
        raise OciPrivateKeyPassPhraseRequiredError(OCI_PRIVATE_KEY_PASSPHRASE_REQUIRED_ERROR)


def _resolve_oci_key_file(key_file: str, config_file: str | Path) -> Path:
    path = Path(key_file).expanduser()
    if path.is_absolute():
        return path
    return Path(config_file).expanduser().parent / path


def _pem_file_is_encrypted(path: Path) -> bool:
    try:
        head = path.read_bytes()[:4096]
    except OSError:
        return False
    text = head.decode("utf-8", errors="ignore").upper()
    return "BEGIN ENCRYPTED PRIVATE KEY" in text or "PROC-TYPE: 4,ENCRYPTED" in text


def _has_pass_phrase(config: Mapping[str, object]) -> bool:
    return any(str(config.get(key, "") or "").strip() for key in PASSPHRASE_CONFIG_KEYS)


def _test_oci_config(settings: object) -> OciConfigTestResult:
    config_file = _settings_str_from(settings, "oci_config_file", "~/.oci/config")
    profile = _safe_oci_profile_name(_settings_str_from(settings, "oci_config_profile", "DEFAULT"))
    config_path = Path(config_file).expanduser()
    default_key_path = Path(OCI_PRIVATE_KEY_FILE).expanduser()
    try:
        content = _read_oci_config_text(config_file)
        parsed = _parse_oci_config(content, profile)
    except HTTPException as exc:
        return OciConfigTestResult(
            status="failed",
            profile=profile,
            config_file=config_file,
            key_file=OCI_PRIVATE_KEY_FILE,
            config_file_exists=config_path.is_file(),
            key_file_exists=default_key_path.is_file(),
            message=str(exc.detail),
            checked_at=_now_iso(),
            error_type=type(exc).__name__,
            oci_directory_mode=_mode_string(config_path.parent),
            config_file_mode=_mode_string(config_path),
            key_file_mode=_mode_string(default_key_path),
        )

    parsed_values = {
        "user": parsed.user,
        "fingerprint": parsed.fingerprint,
        "tenancy": parsed.tenancy,
        "region": parsed.region,
        "key_file": parsed.key_file,
    }
    missing_fields = [field for field in OCI_CONFIG_KEYS if not parsed_values[field].strip()]
    key_path = _resolve_oci_key_file(parsed.key_file or OCI_PRIVATE_KEY_FILE, config_path)
    key_file_exists = key_path.is_file()
    permission_issues = _oci_permission_issues(config_path, key_path)
    pass_phrase_required = (
        key_file_exists
        and _pem_file_is_encrypted(key_path)
        and not _oci_config_has_private_key_pass_phrase(content, profile)
    )
    can_use_config = (
        not missing_fields
        and key_file_exists
        and not permission_issues
        and not pass_phrase_required
    )

    if missing_fields:
        message = "OCI config の必須項目が不足しています。"
    elif not key_file_exists:
        message = "OCI config の key_file が指す秘密鍵ファイルが見つかりません。"
    elif pass_phrase_required:
        message = OCI_PRIVATE_KEY_PASSPHRASE_REQUIRED_ERROR
    elif permission_issues:
        message = "OCI 認証ファイルの権限を確認してください。"
    else:
        message = "OCI config と秘密鍵ファイルを確認できました。"

    return OciConfigTestResult(
        status="success" if can_use_config else "failed",
        profile=parsed.profile,
        config_file=config_file,
        key_file=parsed.key_file or OCI_PRIVATE_KEY_FILE,
        config_file_exists=config_path.is_file(),
        key_file_exists=key_file_exists,
        missing_fields=missing_fields,
        permission_issues=permission_issues,
        oci_directory_mode=_mode_string(config_path.parent),
        config_file_mode=_mode_string(config_path),
        key_file_mode=_mode_string(key_path),
        message=message,
        checked_at=_now_iso(),
        error_type="OciPrivateKeyPassPhraseRequiredError" if pass_phrase_required else None,
    )


def _oci_permission_issues(config_path: Path, key_path: Path) -> list[str]:
    issues: list[str] = []
    directory_mode = _path_mode(config_path.parent)
    config_mode = _path_mode(config_path)
    key_mode = _path_mode(key_path)
    if directory_mode is not None and directory_mode != OCI_DIRECTORY_MODE:
        issues.append("~/.oci ディレクトリは 0700 にしてください。")
    if config_mode is not None and config_mode & 0o077:
        issues.append("OCI config ファイルは 0600 にしてください。")
    if key_mode is not None and key_mode & 0o077:
        issues.append("秘密鍵ファイルは 0600 にしてください。")
    return issues


def _mode_string(path: Path) -> str | None:
    mode = _path_mode(path)
    return f"{mode:04o}" if mode is not None else None


def _path_mode(path: Path) -> int | None:
    try:
        return stat.S_IMODE(path.stat().st_mode)
    except OSError:
        return None


def _oci_config_has_private_key_pass_phrase(content: str, profile: str) -> bool:
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read_string(content)
    except configparser.Error:
        return False

    selected_profile = profile.strip() or "DEFAULT"
    if selected_profile.upper() == "DEFAULT":
        entries = parser.defaults()
    elif parser.has_section(selected_profile):
        entries = parser[selected_profile]
    else:
        return False
    return any(str(entries.get(key, "")).strip() for key in PASSPHRASE_CONFIG_KEYS)


def _read_object_storage_namespace(payload: OciObjectStorageNamespaceRequest) -> str:
    try:
        oci_config = import_module("oci.config")
        object_storage = import_module("oci.object_storage")
        config = _load_oci_config_without_prompt(
            oci_config,
            payload.config_file,
            payload.profile,
            region=payload.region,
        )
        response = object_storage.ObjectStorageClient(config).get_namespace()
    except Exception as exc:
        detail = (
            str(exc)
            if getattr(exc, "safe_for_user", False)
            else (
                "OCI Object Storage namespace を取得できませんでした。"
                "OCI config / profile / region を確認してください。"
            )
        )
        raise HTTPException(status_code=502, detail=detail) from exc

    namespace = getattr(response, "data", "")
    if not isinstance(namespace, str):
        namespace = str(namespace) if namespace is not None else ""
    namespace = namespace.strip()
    if not namespace:
        raise HTTPException(
            status_code=502,
            detail="OCI Object Storage namespace が空で返されました。",
        )
    return namespace


def _get_adb_info_state() -> AdbInfoData:
    global _adb_info_state
    if _adb_info_state is None:
        database = _get_database_settings_state()
        _adb_info_state = AdbInfoData(
            status="success" if database.adb_ocid else "not_configured",
            message=(
                "ADB OCID が設定されています。"
                if database.adb_ocid
                else "ADB OCID が未設定です。"
            ),
            id=database.adb_ocid or None,
            display_name=None,
            lifecycle_state="AVAILABLE" if database.adb_ocid else None,
            db_name=None,
            region=database.region or None,
        )
    return _adb_info_state.model_copy(deep=True)


def _set_adb_info_state(patch: AdbSettingsUpdate) -> AdbInfoData:
    global _adb_info_state, _database_settings_state
    database = _get_database_settings_state()
    database.adb_ocid = patch.adb_ocid.strip()
    database.region = patch.region.strip()
    _database_settings_state = database
    _adb_info_state = AdbInfoData(
        status="success" if database.adb_ocid else "not_configured",
        message="ADB OCID を保存しました。" if database.adb_ocid else "ADB OCID が未設定です。",
        id=database.adb_ocid or None,
        lifecycle_state="AVAILABLE" if database.adb_ocid else None,
        region=database.region or None,
    )
    return _adb_info_state.model_copy(deep=True)


def _oci_missing_fields(data: OciSettingsData) -> list[str]:
    missing = []
    if not data.user:
        missing.append("user")
    if not data.fingerprint:
        missing.append("fingerprint")
    if not data.tenancy:
        missing.append("tenancy")
    if not data.region:
        missing.append("region")
    if not data.key_file_exists:
        missing.append("key_file")
    return missing


async def _uploaded_filename(request: Request) -> str:
    body = await request.body()
    if not body:
        return ""
    preview = body[:4096].decode("latin-1", errors="ignore")
    marker = 'filename="'
    start = preview.find(marker)
    if start < 0:
        return ""
    start += len(marker)
    end = preview.find('"', start)
    return preview[start:end] if end >= start else ""


async def _uploaded_file_from_request(request: Request) -> tuple[str, bytes]:
    body = await request.body()
    if not body:
        return "", b""
    content_type = request.headers.get("content-type", "")
    if "multipart/form-data" not in content_type:
        return "", body

    message = BytesParser(policy=policy.default).parsebytes(
        b"Content-Type: "
        + content_type.encode("utf-8", errors="ignore")
        + b"\r\nMIME-Version: 1.0\r\n\r\n"
        + body
    )
    for part in message.iter_parts():
        filename = part.get_filename()
        if not filename:
            continue
        payload = part.get_payload(decode=True)
        return filename, payload if isinstance(payload, bytes) else b""
    return "", b""


def _install_oci_private_key(data: bytes, file_name: str | None) -> Path:
    safe_name = PurePosixPath((file_name or "oci_api_key.pem").replace("\\", "/")).name
    if Path(safe_name).suffix.lower() not in {".pem", ".key"}:
        raise HTTPException(
            status_code=415,
            detail="秘密鍵は .pem または .key ファイルを選択してください。",
        )
    if not data:
        raise HTTPException(status_code=400, detail="空の秘密鍵ファイルはアップロードできません。")
    if len(data) > OCI_PRIVATE_KEY_MAX_BYTES:
        raise HTTPException(status_code=413, detail="秘密鍵ファイルのサイズが上限を超えています。")
    _validate_private_key_pem(data)

    target = Path(OCI_PRIVATE_KEY_FILE).expanduser()
    tmp_path = target.with_name(f".{target.name}.tmp-{uuid4().hex}")
    try:
        _ensure_private_directory(target.parent)
        tmp_path.write_bytes(data)
        tmp_path.chmod(OCI_PRIVATE_KEY_FILE_MODE)
        tmp_path.replace(target)
        target.chmod(OCI_PRIVATE_KEY_FILE_MODE)
    except OSError as exc:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=500,
            detail="秘密鍵ファイルをバックエンドの固定 path へ保存できませんでした。",
        ) from exc
    return target


def _validate_private_key_pem(data: bytes) -> None:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(
            status_code=400,
            detail="秘密鍵ファイルは UTF-8 の PEM テキストとして読み取れる必要があります。",
        ) from exc
    if "\x00" in text or "-----BEGIN " not in text or "PRIVATE KEY-----" not in text:
        raise HTTPException(
            status_code=400,
            detail="秘密鍵 PEM ファイルの形式を確認してください。",
        )
    upper_text = text.upper()
    if "BEGIN ENCRYPTED PRIVATE KEY" in upper_text or "PROC-TYPE: 4,ENCRYPTED" in upper_text:
        raise HTTPException(
            status_code=400,
            detail=(
                "暗号化された OCI API 秘密鍵は pass phrase 入力が必要です。"
                "パスフレーズなしの秘密鍵 PEM を使用してください。"
            ),
        )


def _install_database_wallet(settings: object, data: bytes, file_name: str | None) -> Path:
    safe_name = PurePosixPath((file_name or "wallet.zip").replace("\\", "/")).name
    if not safe_name.lower().endswith(".zip"):
        raise HTTPException(
            status_code=415,
            detail="Oracle Wallet は ZIP ファイルを選択してください。",
        )
    if not data:
        raise HTTPException(status_code=400, detail="空の Wallet ZIP はアップロードできません。")
    if len(data) > ORACLE_WALLET_MAX_BYTES:
        raise HTTPException(status_code=413, detail="Wallet ZIP のサイズが上限を超えています。")

    target = _wallet_storage_root(settings)
    tmp_dir = target.parent / f".{target.name}.tmp-{uuid4().hex}"
    try:
        wallet_dir = _extract_wallet_zip(data, tmp_dir)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
        shutil.move(str(wallet_dir), str(target))
        return target
    except HTTPException:
        raise
    except OSError as exc:
        raise HTTPException(
            status_code=500,
            detail="Wallet ZIP をバックエンドの保存先へ展開できませんでした。",
        ) from exc
    finally:
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir, ignore_errors=True)


def _wallet_storage_root(settings: object) -> Path:
    configured = _settings_str_from(settings, "oracle_wallet_dir")
    if configured:
        return Path(configured).expanduser()
    return BACKEND_ROOT / ".oracle" / ORACLE_WALLET_DIR_NAME


def _extract_wallet_zip(data: bytes, target_dir: Path) -> Path:
    extracted_files: list[Path] = []
    total_uncompressed = 0
    try:
        with ZipFile(io.BytesIO(data)) as archive:
            members = [member for member in archive.infolist() if not member.is_dir()]
            if not members:
                raise HTTPException(
                    status_code=400,
                    detail="Wallet ZIP にファイルが含まれていません。",
                )
            for member in members:
                total_uncompressed += member.file_size
                if total_uncompressed > ORACLE_WALLET_MAX_EXTRACTED_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail="Wallet ZIP 展開後サイズが上限を超えています。",
                    )
                member_path = target_dir / member.filename
                if not _is_relative_to(member_path.resolve(), target_dir.resolve()):
                    raise HTTPException(status_code=400, detail="Wallet ZIP の path が不正です。")
                archive.extract(member, target_dir)
                extracted_files.append(member_path)
    except BadZipFile as exc:
        raise HTTPException(
            status_code=400,
            detail="Wallet ZIP の形式を確認してください。",
        ) from exc

    if not any(path.name.lower() == "tnsnames.ora" for path in extracted_files):
        raise HTTPException(
            status_code=400,
            detail="Wallet ZIP に tnsnames.ora が含まれていません。",
        )
    return target_dir


def _is_relative_to(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
        return True
    except ValueError:
        return False


@router.get("/settings/model", response_model=ApiResponse[ModelSettingsData])
async def get_model_settings() -> ApiResponse[ModelSettingsData]:
    return ApiResponse(data=_model_settings_data())


@router.patch("/settings/model", response_model=ApiResponse[ModelSettingsData])
async def patch_model_settings(
    patch: ModelSettingsPayload,
    _: None = Depends(require_admin),
) -> ApiResponse[ModelSettingsData]:
    settings = get_settings()
    resolved = _model_settings_with_resolved_secret(settings, patch)
    _persist_model_settings(settings, resolved)
    _apply_model_settings(settings, resolved)
    return ApiResponse(data=_model_settings_data(_set_model_settings_state(resolved), settings))


@router.post("/settings/model/check", response_model=ApiResponse[ModelSettingsData])
async def check_model_settings(patch: ModelSettingsPayload) -> ApiResponse[ModelSettingsData]:
    return ApiResponse(data=_model_settings_data(patch))


@router.post("/settings/model/test", response_model=ApiResponse[ModelSettingsTestResult])
async def test_model_settings(
    request: ModelSettingsTestRequest,
) -> ApiResponse[ModelSettingsTestResult]:
    checks = _model_settings_checks(request.settings)
    target_ok = (
        checks["enterprise_ai"] == "ok"
        if request.target_type in {"enterprise_text", "enterprise_vision"}
        else checks["generative_ai"] == "ok" and checks["embedding_dim"] == "ok"
    )
    return ApiResponse(
        data=ModelSettingsTestResult(
            status="success" if target_ok else "failed",
            target_type=request.target_type,
            model_id=request.model_id,
            message=(
                "設定形式を確認しました。"
                if target_ok
                else "必要な設定値が不足しているためテストできません。"
            ),
            troubleshooting=(
                []
                if target_ok
                else ["Endpoint / Project / API key / model ID を確認してください。"]
            ),
            error_type=None if target_ok else "missing_settings",
            checked_at=_now_iso(),
            details={"dry_run": True, "vision_enabled": request.vision_enabled},
        )
    )


@router.get("/settings/database", response_model=ApiResponse[DatabaseSettingsData])
async def get_database_settings() -> ApiResponse[DatabaseSettingsData]:
    return ApiResponse(data=_get_database_settings_state())


@router.patch("/settings/database", response_model=ApiResponse[DatabaseSettingsData])
async def patch_database_settings(
    patch: DatabaseSettingsUpdate,
    _: None = Depends(require_admin),
) -> ApiResponse[DatabaseSettingsData]:
    settings = get_settings()
    data = _set_database_settings_state(patch)
    _persist_database_settings(settings, data, patch)
    return ApiResponse(data=_database_settings_data(settings))


@router.post("/settings/database/wallet", response_model=ApiResponse[DatabaseSettingsData])
async def upload_database_wallet(
    request: Request,
    _: None = Depends(require_admin),
) -> ApiResponse[DatabaseSettingsData]:
    global _database_settings_state
    settings = get_settings()
    filename, content = await _uploaded_file_from_request(request)
    wallet_dir = _install_database_wallet(settings, content, filename)
    _set_if_possible(settings, "oracle_wallet_dir", str(wallet_dir))
    data = _database_settings_data(settings)
    _database_settings_state = data
    _write_env_values(
        BACKEND_ENV_FILE,
        {"ORACLE_WALLET_DIR": str(wallet_dir)},
        section_comment="# Oracle 26ai",
        error_detail="Oracle Wallet 設定を backend/.env へ保存できませんでした。",
    )
    return ApiResponse(data=data)


@router.post("/settings/database/test", response_model=ApiResponse[DatabaseConnectionTestResult])
async def test_database_settings(
    patch: DatabaseSettingsUpdate,
) -> ApiResponse[DatabaseConnectionTestResult]:
    started = monotonic()
    candidate = _database_connection_test_candidate(get_settings(), patch)
    readiness = str(candidate.readiness)
    if readiness != "ok":
        return ApiResponse(
            data=DatabaseConnectionTestResult(
                status="failed",
                readiness=readiness,
                message="Oracle 26ai 接続に必要な設定が不足しています。",
                elapsed_ms=_elapsed_ms(started),
                troubleshooting=_database_connection_troubleshooting(readiness=readiness),
                checked_at=_now_iso(),
                error_type=readiness,
                details={"dsn": str(candidate.display_dsn) or None},
            )
        )

    try:
        await _test_oracle_connection(candidate)
    except Exception as exc:  # noqa: BLE001 - Oracle SDK の多様な例外を表示用に握る
        oracle_error_codes = _oracle_error_codes(str(exc))
        return ApiResponse(
            data=DatabaseConnectionTestResult(
                status="failed",
                readiness=readiness,
                message=_database_connection_error_message(exc, oracle_error_codes),
                elapsed_ms=_elapsed_ms(started),
                troubleshooting=_database_connection_troubleshooting(
                    readiness=readiness,
                    error_text=str(exc),
                    error_type=type(exc).__name__,
                ),
                checked_at=_now_iso(),
                error_type=type(exc).__name__,
                details={
                    "timeout_seconds": float(candidate.oracle_db_test_timeout_seconds),
                    "tcp_connect_timeout_seconds": float(
                        candidate.oracle_tcp_connect_timeout_seconds
                    ),
                    "oracle_error_codes": ", ".join(oracle_error_codes) or None,
                    "dsn": str(candidate.display_dsn) or None,
                },
            )
        )

    return ApiResponse(
        data=DatabaseConnectionTestResult(
            status="success",
            readiness=readiness,
            message="Oracle 26ai への接続に成功しました。",
            elapsed_ms=_elapsed_ms(started),
            troubleshooting=[],
            checked_at=_now_iso(),
            details={
                "timeout_seconds": float(candidate.oracle_db_test_timeout_seconds),
                "tcp_connect_timeout_seconds": float(candidate.oracle_tcp_connect_timeout_seconds),
                "dsn": str(candidate.display_dsn) or None,
            },
        )
    )


@router.get("/settings/database/adb", response_model=ApiResponse[AdbInfoData])
async def get_adb_info() -> ApiResponse[AdbInfoData]:
    return ApiResponse(data=_get_adb_info_state())


@router.post("/settings/database/adb/settings", response_model=ApiResponse[AdbInfoData])
async def patch_adb_settings(
    patch: AdbSettingsUpdate,
    _: None = Depends(require_admin),
) -> ApiResponse[AdbInfoData]:
    return ApiResponse(data=_set_adb_info_state(patch))


@router.post("/settings/database/adb/start", response_model=ApiResponse[AdbInfoData])
async def start_adb(_: None = Depends(require_admin)) -> ApiResponse[AdbInfoData]:
    global _adb_info_state
    info = _get_adb_info_state()
    if not info.id:
        return ApiResponse(data=info)
    info.status = "accepted"
    info.message = "ADB 起動要求を受け付けました。"
    info.lifecycle_state = "AVAILABLE"
    _adb_info_state = info
    return ApiResponse(data=info)


@router.post("/settings/database/adb/stop", response_model=ApiResponse[AdbInfoData])
async def stop_adb(_: None = Depends(require_admin)) -> ApiResponse[AdbInfoData]:
    global _adb_info_state
    info = _get_adb_info_state()
    if not info.id:
        return ApiResponse(data=info)
    info.status = "accepted"
    info.message = "ADB 停止要求を受け付けました。"
    info.lifecycle_state = "STOPPED"
    _adb_info_state = info
    return ApiResponse(data=info)


@router.get("/settings/upload-storage", response_model=ApiResponse[UploadStorageSettingsData])
async def get_upload_storage_settings() -> ApiResponse[UploadStorageSettingsData]:
    return ApiResponse(data=_get_upload_storage_settings_state())


@router.patch("/settings/upload-storage", response_model=ApiResponse[UploadStorageSettingsData])
async def patch_upload_storage_settings(
    patch: UploadStorageSettingsUpdate,
    _: None = Depends(require_admin),
) -> ApiResponse[UploadStorageSettingsData]:
    settings = get_settings()
    data = _set_upload_storage_settings_state(patch)
    _persist_upload_storage_settings(data)
    _set_if_possible(settings, "upload_storage_backend", data.backend)
    _set_if_possible(settings, "local_storage_dir", data.local_storage_dir)
    _set_if_possible(settings, "object_storage_namespace", data.object_storage_namespace)
    _set_if_possible(settings, "object_storage_bucket", data.object_storage_bucket)
    return ApiResponse(data=_upload_storage_settings_data(settings))


@router.get("/settings/oci", response_model=ApiResponse[OciSettingsData])
async def get_oci_settings() -> ApiResponse[OciSettingsData]:
    return ApiResponse(data=_get_oci_settings_state())


@router.patch("/settings/oci", response_model=ApiResponse[OciSettingsData])
async def patch_oci_settings(
    patch: OciSettingsUpdate,
    _: None = Depends(require_admin),
) -> ApiResponse[OciSettingsData]:
    global _oci_settings_state
    settings = get_settings()
    _write_oci_config(settings, patch)
    _persist_oci_settings(settings, patch)
    _set_if_possible(settings, "oci_region", patch.region)
    _set_if_possible(settings, "oci_user_ocid", patch.user)
    _set_if_possible(settings, "oci_fingerprint", patch.fingerprint)
    _set_if_possible(settings, "oci_tenancy_ocid", patch.tenancy)
    _oci_settings_state = _oci_settings_data(settings)
    return ApiResponse(data=_oci_settings_state.model_copy(deep=True))


@router.patch("/settings/oci/object-storage", response_model=ApiResponse[UploadStorageSettingsData])
async def patch_oci_object_storage_settings(
    patch: OciObjectStorageSettingsUpdate,
    _: None = Depends(require_admin),
) -> ApiResponse[UploadStorageSettingsData]:
    global _upload_storage_settings_state
    settings = get_settings()
    current = _get_upload_storage_settings_state()
    updated = _set_upload_storage_settings_state(
        UploadStorageSettingsUpdate(
            backend=current.backend,
            local_storage_dir=current.local_storage_dir,
            object_storage_namespace=patch.object_storage_namespace,
            object_storage_bucket=current.object_storage_bucket,
        )
    )
    updated.object_storage_region = patch.object_storage_region.strip()
    updated.readiness = _upload_storage_readiness(updated)
    _upload_storage_settings_state = updated
    _set_if_possible(settings, "object_storage_region", updated.object_storage_region)
    _set_if_possible(settings, "object_storage_namespace", updated.object_storage_namespace)
    _persist_oci_object_storage_settings(settings)
    return ApiResponse(data=_upload_storage_settings_data(settings))


@router.post("/settings/oci/config/read", response_model=ApiResponse[OciConfigReadData])
async def read_oci_config(payload: OciConfigReadRequest) -> ApiResponse[OciConfigReadData]:
    content = _read_oci_config_text(payload.config_file)
    return ApiResponse(data=_parse_oci_config(content, payload.profile))


@router.post("/settings/oci/config/test", response_model=ApiResponse[OciConfigTestResult])
async def test_oci_config() -> ApiResponse[OciConfigTestResult]:
    return ApiResponse(data=_test_oci_config(get_settings()))


@router.post(
    "/settings/oci/object-storage/namespace",
    response_model=ApiResponse[OciObjectStorageNamespaceData],
)
async def read_oci_object_storage_namespace(
    payload: OciObjectStorageNamespaceRequest,
) -> ApiResponse[OciObjectStorageNamespaceData]:
    namespace = _read_object_storage_namespace(payload)
    return ApiResponse(data=OciObjectStorageNamespaceData(namespace=namespace))


@router.post("/settings/oci/key-file", response_model=ApiResponse[OciPrivateKeyUploadData])
async def upload_oci_private_key(
    request: Request,
    _: None = Depends(require_admin),
) -> ApiResponse[OciPrivateKeyUploadData]:
    global _oci_settings_state
    filename, content = await _uploaded_file_from_request(request)
    _install_oci_private_key(content, filename)
    _oci_settings_state = _oci_settings_data(get_settings())
    return ApiResponse(data=OciPrivateKeyUploadData(key_file=OCI_PRIVATE_KEY_FILE, saved=True))


@router.get("/runtime/snapshot", response_model=ApiResponse[AgentRuntimeSnapshot])
async def export_runtime_snapshot(
    _: None = Depends(require_admin),
) -> ApiResponse[AgentRuntimeSnapshot]:
    return ApiResponse(data=runtime_repository.export_snapshot())


@router.post(
    "/runtime/snapshot/import",
    response_model=ApiResponse[RuntimeSnapshotImportResult],
)
async def import_runtime_snapshot(
    request: RuntimeSnapshotImportRequest,
    _: None = Depends(require_admin),
) -> ApiResponse[RuntimeSnapshotImportResult]:
    validation = runtime_repository.validate_snapshot(request.snapshot)
    if request.dry_run:
        return ApiResponse(
            data=RuntimeSnapshotImportResult(
                imported=False,
                dry_run=True,
                validation=validation,
                reason=request.reason,
            )
        )
    if not validation.valid:
        raise HTTPException(status_code=400, detail="snapshot validation failed")
    if not request.confirm_replace:
        raise HTTPException(
            status_code=400,
            detail="confirm_replace=true is required when dry_run=false",
        )
    try:
        runtime_repository.replace_snapshot(request.snapshot)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ApiResponse(
        data=RuntimeSnapshotImportResult(
            imported=True,
            dry_run=False,
            validation=validation,
            reason=request.reason,
        )
    )


@router.get("/observability/status", response_model=ApiResponse[ObservabilityStatus])
async def get_observability_status() -> ApiResponse[ObservabilityStatus]:
    settings = get_settings()
    policy = get_trace_policy()
    exporter = trace_exporter_status()
    return ApiResponse(
        data=ObservabilityStatus(
            metrics_enabled=settings.agent_metrics_enabled,
            prometheus_metrics_path="/metrics",
            trace_events_enabled=policy.trace_events_enabled,
            trace_events_buffer_size=policy.trace_events_buffer_size,
            trace_events_retention_seconds=policy.trace_events_retention_seconds,
            trace_sample_rate=policy.trace_sample_rate,
            trace_exporter_configured=exporter.configured,
            trace_exporter_last_success_at=exporter.last_success_at,
            trace_exporter_last_error=exporter.last_error,
            retry_queue_size=exporter.retry_queue_size,
            retry_queue_max_size=exporter.retry_queue_max_size,
            retry_max_attempts=exporter.retry_max_attempts,
            retry_worker_enabled=exporter.retry_worker_enabled,
            retry_worker_running=exporter.retry_worker_running,
            retry_worker_interval_seconds=exporter.retry_worker_interval_seconds,
            langfuse_configured=bool(
                settings.agent_langfuse_host
                and settings.agent_langfuse_public_key
                and settings.agent_langfuse_secret_key
            ),
            opentelemetry_configured=bool(settings.agent_opentelemetry_endpoint),
        )
    )


@router.get("/settings/trace-policy", response_model=ApiResponse[TracePolicySettings])
async def get_trace_policy_settings() -> ApiResponse[TracePolicySettings]:
    return ApiResponse(data=get_trace_policy())


@router.patch("/settings/trace-policy", response_model=ApiResponse[TracePolicySettings])
async def patch_trace_policy_settings(
    patch: TracePolicyPatch,
    _: None = Depends(require_admin),
) -> ApiResponse[TracePolicySettings]:
    try:
        policy = patch_trace_policy(patch)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ApiResponse(data=policy)


@router.get("/observability/events", response_model=ApiResponse[TraceEventsData])
async def get_observability_events(
    event_type: str | None = None,
    run_id: str | None = None,
    tool_name: str | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
    _: None = Depends(require_auditor),
) -> ApiResponse[TraceEventsData]:
    return ApiResponse(
        data=list_trace_events(
            event_type=event_type,
            run_id=run_id,
            tool_name=tool_name,
            limit=limit,
        )
    )


@router.post(
    "/observability/export-retry/flush",
    response_model=ApiResponse[TraceExportRetryData],
)
async def flush_observability_export_retry_queue(
    limit: int = Query(default=100, ge=1, le=1000),
    force: bool = False,
    _: None = Depends(require_admin),
) -> ApiResponse[TraceExportRetryData]:
    return ApiResponse(data=flush_trace_export_retry_queue(limit=limit, force=force))


@router.get("/tools", response_model=ApiResponse[ToolDefinitionsData])
async def list_tool_definitions() -> ApiResponse[ToolDefinitionsData]:
    """登録済みツールの v2 schema 一覧を返す。"""
    return ApiResponse(data=ToolDefinitionsData(tools=tool_registry.definitions()))


@router.get("/skills", response_model=ApiResponse[AgentSkillListOutput])
async def list_agent_skills(
    _: None = Depends(require_viewer),
) -> ApiResponse[AgentSkillListOutput]:
    skills = skill_registry.list()
    return ApiResponse(data=AgentSkillListOutput(skills=skills, metadata={"count": len(skills)}))


@router.post("/skills/plan", response_model=ApiResponse[AgentSkillPlanOutput])
async def plan_agent_skill(
    request: AgentSkillRunInput,
    _: None = Depends(require_viewer),
) -> ApiResponse[AgentSkillPlanOutput]:
    try:
        return ApiResponse(data=skill_registry.plan(request))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="skill not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/agent/tools", response_model=ApiResponse[ToolsData])
async def list_tool_names_compat() -> ApiResponse[ToolsData]:
    """旧 UI/テスト互換の names-only ツール一覧を返す。"""
    return ApiResponse(data=ToolsData(tools=tool_registry.names()))


@router.get("/tools/external-mcp", response_model=ApiResponse[ExternalMcpToolsData])
async def list_external_mcp_tool_definitions(
    server_id: str | None = None,
    trace_id: str | None = None,
    _: None = Depends(require_viewer),
) -> ApiResponse[ExternalMcpToolsData]:
    """外部 MCP gateway の tools/list を標準化して返す。"""
    try:
        return ApiResponse(data=list_external_mcp_tools(server_id=server_id, trace_id=trace_id))
    except ExternalToolError as exc:
        status_code = 400 if exc.code == "external_mcp.not_configured" else 502
        raise HTTPException(
            status_code=status_code,
            detail={"code": exc.code, "message": exc.message, "details": exc.details},
        ) from exc


@router.post("/tools/invoke", response_model=ApiResponse[ToolResult])
@router.post("/agent/tools/invoke", response_model=ApiResponse[ToolResult])
async def invoke_tool(
    call: ToolCall,
    _: None = Depends(require_operator),
) -> ApiResponse[ToolResult]:
    """単発ツール呼び出し。承認が必要な場合は approval_required を返す。"""
    return ApiResponse(data=tool_registry.invoke(call, policy=_configured_tool_policy()))


@router.get("/runs", response_model=ApiResponse[RunsData])
async def list_runs(
    request: Request,
    _: None = Depends(require_viewer),
) -> ApiResponse[RunsData]:
    runs = _filter_runs_for_actor(request, runtime_repository.list_runs())
    return ApiResponse(data=RunsData(runs=runs))


@router.post("/runs", response_model=ApiResponse[RunState])
async def create_run(
    run_request: RunCreateRequest,
    request: Request,
    _: None = Depends(require_operator),
) -> ApiResponse[RunState]:
    try:
        _require_agent_access(request, run_request.agent_id)
        _require_business_view_access(request, _run_create_business_view_id(run_request))
        return ApiResponse(data=runtime_repository.create_run(run_request))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="agent not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/runs/{run_id}", response_model=ApiResponse[RunState])
async def get_run(
    run_id: str,
    request: Request,
    _: None = Depends(require_viewer),
) -> ApiResponse[RunState]:
    try:
        run = runtime_repository.get_run(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="run not found") from exc
    _require_agent_access(request, run.agent_id)
    _require_business_view_access(request, _run_business_view_id(run))
    return ApiResponse(data=run)


@router.get("/runs/{run_id}/audit", response_model=ApiResponse[RunAuditData])
async def get_run_audit(
    run_id: str,
    request: Request,
    _: None = Depends(require_auditor),
) -> ApiResponse[RunAuditData]:
    try:
        run = runtime_repository.get_run(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="run not found") from exc
    _require_agent_access(request, run.agent_id)
    _require_business_view_access(request, _run_business_view_id(run))
    return ApiResponse(data=_run_audit_data(run))


@router.get("/audit/tool-calls", response_model=ApiResponse[ToolCallAuditData])
async def list_tool_call_audit(
    request: Request,
    run_id: str | None = None,
    tool_name: str | None = None,
    status: str | None = None,
    approval_status: str | None = None,
    error_code: str | None = None,
    has_guardrail_warnings: bool | None = None,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=1000),
    _: None = Depends(require_auditor),
) -> ApiResponse[ToolCallAuditData]:
    return ApiResponse(
        data=_tool_call_audit_data(
            request=request,
            run_id=run_id,
            tool_name=tool_name,
            status=status,
            approval_status=approval_status,
            error_code=error_code,
            has_guardrail_warnings=has_guardrail_warnings,
            offset=offset,
            limit=limit,
        )
    )


@router.get("/audit/tool-calls.csv", response_class=Response)
async def export_tool_call_audit_csv(
    request: Request,
    run_id: str | None = None,
    tool_name: str | None = None,
    status: str | None = None,
    approval_status: str | None = None,
    error_code: str | None = None,
    has_guardrail_warnings: bool | None = None,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=1000, ge=1, le=5000),
    _: None = Depends(require_auditor),
) -> Response:
    data = _tool_call_audit_data(
        request=request,
        run_id=run_id,
        tool_name=tool_name,
        status=status,
        approval_status=approval_status,
        error_code=error_code,
        has_guardrail_warnings=has_guardrail_warnings,
        offset=offset,
        limit=limit,
    )
    return Response(
        _tool_call_audit_csv(data.records),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="agent-tool-call-audit.csv"'},
    )


@router.get("/runs/{run_id}/artifacts", response_model=ApiResponse[ArtifactsData])
async def list_run_artifacts(
    run_id: str,
    request: Request,
    _: None = Depends(require_viewer),
) -> ApiResponse[ArtifactsData]:
    try:
        run = runtime_repository.get_run(run_id)
        artifacts = runtime_repository.list_artifacts(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="run not found") from exc
    _require_agent_access(request, run.agent_id)
    _require_business_view_access(request, _run_business_view_id(run))
    return ApiResponse(data=ArtifactsData(artifacts=artifacts))


@router.get("/runs/{run_id}/artifacts/{artifact_id}", response_model=ApiResponse[Artifact])
async def get_run_artifact(
    run_id: str,
    artifact_id: str,
    request: Request,
    _: None = Depends(require_viewer),
) -> ApiResponse[Artifact]:
    try:
        run = runtime_repository.get_run(run_id)
        _require_agent_access(request, run.agent_id)
        _require_business_view_access(request, _run_business_view_id(run))
        return ApiResponse(data=runtime_repository.get_artifact(run_id, artifact_id))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="artifact not found") from exc


@router.get("/runs/{run_id}/events")
async def stream_run_events(
    run_id: str,
    request: Request,
    follow: bool = Query(default=False),
    after_event_id: str | None = Query(default=None),
    _: None = Depends(require_viewer),
) -> Response:
    try:
        run = runtime_repository.get_run(run_id)
        _require_agent_access(request, run.agent_id)
        _require_business_view_access(request, _run_business_view_id(run))
        events = runtime_repository.iter_events(
            run_id,
            after_event_id=after_event_id,
            follow=follow,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="run not found") from exc
    if not follow:
        return Response("".join(_sse_events(events)), media_type="text/event-stream")
    return StreamingResponse(_sse_events(events), media_type="text/event-stream")


@router.websocket("/runs/{run_id}/events/ws")
async def stream_run_events_websocket(
    websocket: WebSocket,
    run_id: str,
    after_event_id: str | None = None,
    heartbeat_interval_seconds: float = 15.0,
    max_events_per_tick: int = 50,
) -> None:
    await websocket.accept()
    if not _websocket_has_roles(websocket, {"viewer", "operator", "approver", "auditor"}):
        await websocket.send_json(
            {
                "type": "error",
                "error_code": "rbac.forbidden",
                "message": "required role: viewer/operator/approver/auditor/admin",
            }
        )
        await websocket.close(code=1008)
        return
    try:
        run = runtime_repository.get_run(run_id)
    except KeyError:
        await websocket.send_json(
            {
                "type": "error",
                "error_code": "run.not_found",
                "message": "run not found",
            }
        )
        await websocket.close(code=1008)
        return
    if not _websocket_has_agent_access(websocket, run.agent_id):
        await websocket.send_json(
            {
                "type": "error",
                "error_code": "rbac.agent_forbidden",
                "message": "agent access denied",
            }
        )
        await websocket.close(code=1008)
        return
    if not _websocket_has_business_view_access(websocket, _run_business_view_id(run)):
        await websocket.send_json(
            {
                "type": "error",
                "error_code": "rbac.business_view_forbidden",
                "message": "business view access denied",
            }
        )
        await websocket.close(code=1008)
        return

    next_index = _event_index_after_ws(run.events, after_event_id)
    last_heartbeat_at = monotonic()
    event_batch_size = max(1, min(max_events_per_tick, 500))
    try:
        while True:
            run = runtime_repository.get_run(run_id)
            events_sent = 0
            while next_index < len(run.events) and events_sent < event_batch_size:
                event = run.events[next_index]
                next_index += 1
                events_sent += 1
                await websocket.send_json(_websocket_event_payload(event))

            has_backlog = next_index < len(run.events)
            if run.status in {"completed", "failed", "cancelled"} and not has_backlog:
                await websocket.close(code=1000)
                return

            now = monotonic()
            if now - last_heartbeat_at >= max(0.0, heartbeat_interval_seconds):
                await websocket.send_json(_websocket_heartbeat_payload(run))
                last_heartbeat_at = now

            await _handle_websocket_command(websocket, run_id)
            await sleep(0.05 if has_backlog else 0.25)
    except WebSocketDisconnect:
        return


@router.post("/runs/{run_id}/cancel", response_model=ApiResponse[RunState])
async def cancel_run(
    run_id: str,
    request: Request,
    _: None = Depends(require_operator),
) -> ApiResponse[RunState]:
    try:
        run = runtime_repository.get_run(run_id)
        _require_agent_access(request, run.agent_id)
        _require_business_view_access(request, _run_business_view_id(run))
        return ApiResponse(data=runtime_repository.cancel_run(run_id))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="run not found") from exc


@router.post("/runs/{run_id}/resume", response_model=ApiResponse[RunState])
async def resume_run(
    run_id: str,
    request: Request,
    _: None = Depends(require_operator),
) -> ApiResponse[RunState]:
    try:
        run = runtime_repository.get_run(run_id)
        _require_agent_access(request, run.agent_id)
        _require_business_view_access(request, _run_business_view_id(run))
        return ApiResponse(data=runtime_repository.resume_run(run_id))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="run not found") from exc


@router.post("/runs/{run_id}/replay", response_model=ApiResponse[RunState])
async def replay_run(
    run_id: str,
    request: Request,
    _: None = Depends(require_operator),
) -> ApiResponse[RunState]:
    try:
        run = runtime_repository.get_run(run_id)
        _require_agent_access(request, run.agent_id)
        _require_business_view_access(request, _run_business_view_id(run))
        return ApiResponse(data=runtime_repository.replay_run(run_id))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="run not found") from exc


@router.post("/approvals/{approval_id}/decision", response_model=ApiResponse[RunState])
async def decide_approval(
    approval_id: str,
    request: ApprovalDecisionRequest,
    http_request: Request,
    _: None = Depends(require_approver),
) -> ApiResponse[RunState]:
    try:
        run = _run_for_approval(approval_id)
        _require_agent_access(http_request, run.agent_id)
        _require_business_view_access(http_request, _run_business_view_id(run))
        return ApiResponse(data=runtime_repository.decide_approval(approval_id, request))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="approval not found") from exc


@router.get("/agents", response_model=ApiResponse[AgentsData])
async def list_agents() -> ApiResponse[AgentsData]:
    return ApiResponse(data=AgentsData(agents=runtime_repository.list_agents()))


@router.post("/agents", response_model=ApiResponse[AgentProfile])
async def create_agent(
    agent: AgentProfile,
    _: None = Depends(require_admin),
) -> ApiResponse[AgentProfile]:
    try:
        return ApiResponse(data=runtime_repository.create_agent(agent))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/agents/{agent_id}", response_model=ApiResponse[AgentProfile])
async def patch_agent(
    agent_id: str,
    patch: AgentProfilePatch,
    _: None = Depends(require_admin),
) -> ApiResponse[AgentProfile]:
    try:
        return ApiResponse(data=runtime_repository.patch_agent(agent_id, patch))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="agent not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/memory/search", response_model=ApiResponse[MemoryData])
async def search_memory_get(
    q: str = Query(default=""),
    limit: int = Query(default=20, ge=1, le=100),
    kind: MemoryKind | None = None,
) -> ApiResponse[MemoryData]:
    entries = runtime_repository.search_memory(MemorySearchRequest(query=q, limit=limit, kind=kind))
    return ApiResponse(data=MemoryData(entries=entries))


@router.post("/memory/search", response_model=ApiResponse[MemoryData])
async def search_memory_post(request: MemorySearchRequest) -> ApiResponse[MemoryData]:
    return ApiResponse(data=MemoryData(entries=runtime_repository.search_memory(request)))


@router.post("/memory", response_model=ApiResponse[MemoryEntry])
async def add_memory(
    entry: MemoryEntry,
    _: None = Depends(require_operator),
) -> ApiResponse[MemoryEntry]:
    return ApiResponse(data=runtime_repository.add_memory(entry))


@router.get("/settings/external-rag", response_model=ApiResponse[ExternalServiceSettings])
async def get_external_rag_settings() -> ApiResponse[ExternalServiceSettings]:
    config = runtime_config_store.get_rag()
    return ApiResponse(
        data=ExternalServiceSettings(
            base_url=config.base_url,
            api_key_configured=bool(config.api_key),
            timeout_seconds=config.timeout_seconds,
            configured=bool(config.base_url),
        )
    )


@router.patch("/settings/external-rag", response_model=ApiResponse[ExternalServiceSettings])
async def patch_external_rag_settings(
    patch: SettingsPatch,
    _: None = Depends(require_admin),
) -> ApiResponse[ExternalServiceSettings]:
    runtime_config_store.patch_rag(
        base_url=patch.base_url,
        timeout_seconds=patch.timeout_seconds,
    )
    return await get_external_rag_settings()


@router.get("/settings/external-nl2sql", response_model=ApiResponse[ExternalServiceSettings])
async def get_external_nl2sql_settings() -> ApiResponse[ExternalServiceSettings]:
    config = runtime_config_store.get_nl2sql()
    return ApiResponse(
        data=ExternalServiceSettings(
            base_url=config.base_url,
            api_key_configured=bool(config.api_key),
            timeout_seconds=config.timeout_seconds,
            default_limit=config.default_limit,
            configured=bool(config.base_url),
        )
    )


@router.patch("/settings/external-nl2sql", response_model=ApiResponse[ExternalServiceSettings])
async def patch_external_nl2sql_settings(
    patch: SettingsPatch,
    _: None = Depends(require_admin),
) -> ApiResponse[ExternalServiceSettings]:
    runtime_config_store.patch_nl2sql(
        base_url=patch.base_url,
        timeout_seconds=patch.timeout_seconds,
        default_limit=patch.default_limit,
    )
    return await get_external_nl2sql_settings()


@router.get("/settings/external-mcp", response_model=ApiResponse[ExternalServiceSettings])
async def get_external_mcp_settings() -> ApiResponse[ExternalServiceSettings]:
    config = runtime_config_store.get_mcp()
    oauth_configured = _mcp_oauth_configured(config)
    return ApiResponse(
        data=ExternalServiceSettings(
            base_url=config.base_url,
            api_key_configured=bool(config.api_key),
            oauth_configured=oauth_configured,
            auth_mode=(
                "oauth_client_credentials"
                if oauth_configured
                else "api_key" if config.api_key else "none"
            ),
            session_configured=bool(config.session_id),
            timeout_seconds=config.timeout_seconds,
            configured=bool(config.base_url),
        )
    )


@router.patch("/settings/external-mcp", response_model=ApiResponse[ExternalServiceSettings])
async def patch_external_mcp_settings(
    patch: SettingsPatch,
    _: None = Depends(require_admin),
) -> ApiResponse[ExternalServiceSettings]:
    runtime_config_store.patch_mcp(
        base_url=patch.base_url,
        timeout_seconds=patch.timeout_seconds,
        session_id=patch.session_id,
    )
    return await get_external_mcp_settings()


@router.get("/settings/tool-policy", response_model=ApiResponse[ToolPolicySettings])
async def get_tool_policy_settings() -> ApiResponse[ToolPolicySettings]:
    return ApiResponse(data=_tool_policy_settings_response())


@router.patch("/settings/tool-policy", response_model=ApiResponse[ToolPolicySettings])
async def patch_tool_policy_settings(
    patch: ToolPolicySettingsPatch,
    _: None = Depends(require_admin),
) -> ApiResponse[ToolPolicySettings]:
    try:
        _validate_tool_policy_patch(patch)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    runtime_config_store.patch_tool_policy(
        default_mode=patch.default_mode,
        allow=patch.allow,
        ask=patch.ask,
        deny=patch.deny,
    )
    return ApiResponse(data=_tool_policy_settings_response())


@router.get("/settings/runtime-safety", response_model=ApiResponse[RuntimeSafetySettings])
async def get_runtime_safety_settings() -> ApiResponse[RuntimeSafetySettings]:
    return ApiResponse(data=_runtime_safety_settings_response())


@router.patch("/settings/runtime-safety", response_model=ApiResponse[RuntimeSafetySettings])
async def patch_runtime_safety_settings(
    patch: RuntimeSafetySettingsPatch,
    _: None = Depends(require_admin),
) -> ApiResponse[RuntimeSafetySettings]:
    try:
        _validate_runtime_safety_patch(patch)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    runtime_config_store.patch_runtime_safety(
        max_tool_calls_per_run=patch.max_tool_calls_per_run,
        max_pending_approvals_per_run=patch.max_pending_approvals_per_run,
    )
    return ApiResponse(data=_runtime_safety_settings_response())


@router.get("/settings/planner", response_model=ApiResponse[PlannerSettings])
async def get_planner_settings() -> ApiResponse[PlannerSettings]:
    return ApiResponse(data=_planner_settings_response())


@router.patch("/settings/planner", response_model=ApiResponse[PlannerSettings])
async def patch_planner_settings(
    patch: PlannerSettingsPatch,
    _: None = Depends(require_admin),
) -> ApiResponse[PlannerSettings]:
    try:
        _validate_planner_patch(patch)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    runtime_config_store.patch_planner(
        provider=patch.provider,
        oci_responses_base_url=patch.oci_responses_base_url,
        oci_responses_model=patch.oci_responses_model,
        oci_responses_project=patch.oci_responses_project,
        oci_agent_endpoint=patch.oci_agent_endpoint,
        enterprise_ai_endpoint=(
            patch.enterprise_ai_endpoint if patch.oci_responses_base_url is None else None
        ),
        timeout_seconds=patch.timeout_seconds,
        max_retries=patch.max_retries,
        fallback_to_heuristic=patch.fallback_to_heuristic,
        allowed_tool_names=patch.allowed_tool_names,
        allow_command_generation=patch.allow_command_generation,
    )
    return ApiResponse(data=_planner_settings_response())


@router.get("/settings/command-policy", response_model=ApiResponse[CommandPolicySettings])
async def get_command_policy_settings() -> ApiResponse[CommandPolicySettings]:
    return ApiResponse(data=_command_policy_settings_response())


@router.patch("/settings/command-policy", response_model=ApiResponse[CommandPolicySettings])
async def patch_command_policy_settings(
    patch: CommandPolicySettingsPatch,
    _: None = Depends(require_admin),
) -> ApiResponse[CommandPolicySettings]:
    try:
        _validate_command_policy_patch(patch)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    runtime_config_store.patch_command_policy(
        enabled=patch.enabled,
        workspace_root=patch.workspace_root,
        allowed_prefixes=patch.allowed_prefixes,
        default_timeout_seconds=patch.default_timeout_seconds,
        max_timeout_seconds=patch.max_timeout_seconds,
        output_limit_bytes=patch.output_limit_bytes,
        artifact_storage_backend=patch.artifact_storage_backend,
        artifact_storage_path=patch.artifact_storage_path,
    )
    return ApiResponse(data=_command_policy_settings_response())


def _sse_events(events: Iterable[RunEvent | None]) -> Iterable[str]:
    for event in events:
        if event is None:
            yield ": keepalive\n\n"
            continue
        payload = event.model_dump(mode="json")
        yield f"event: {event.type}\n"
        yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _event_index_after_ws(events: list[RunEvent], event_id: str | None) -> int:
    if event_id is None:
        return 0
    for index, event in enumerate(events):
        if event.id == event_id:
            return index + 1
    return 0


def _websocket_event_payload(event: RunEvent) -> dict[str, object]:
    return {
        "type": event.type.value,
        "event": event.model_dump(mode="json"),
    }


def _websocket_heartbeat_payload(run: RunState) -> dict[str, object]:
    return {
        "type": "heartbeat",
        "run_id": run.id,
        "run_status": run.status.value,
        "server_time": datetime.now(UTC).isoformat(),
    }


async def _handle_websocket_command(websocket: WebSocket, run_id: str) -> None:
    try:
        message = await wait_for(websocket.receive_json(), timeout=0.01)
    except TimeoutError:
        return
    if not isinstance(message, dict):
        await websocket.send_json(
            _websocket_error_payload(
                "websocket.invalid_message",
                "message must be a JSON object",
            )
        )
        return
    command = message.get("type")
    command_id = message.get("command_id")
    normalized_command_id = command_id if isinstance(command_id, str) and command_id else None
    if command == "ping":
        await websocket.send_json(
            {
                "type": "pong",
                "ok": True,
                "command": "ping",
                "command_id": normalized_command_id,
                "server_time": datetime.now(UTC).isoformat(),
            }
        )
        return
    if command == "cancel":
        if not _websocket_has_roles(websocket, {"operator"}):
            await websocket.send_json(
                _websocket_error_payload(
                    "rbac.forbidden",
                    "cancel requires operator/admin role",
                    command="cancel",
                    command_id=normalized_command_id,
                )
            )
            return
        dedupe = _websocket_command_dedupe_state(run_id, "cancel", normalized_command_id)
        if dedupe == "duplicate":
            await websocket.send_json(
                _websocket_command_accepted("cancel", normalized_command_id, duplicate=True)
            )
            return
        if dedupe == "conflict":
            await websocket.send_json(
                _websocket_error_payload(
                    "websocket.command_id_conflict",
                    "command_id was already used for another command",
                    command="cancel",
                    command_id=normalized_command_id,
                )
            )
            return
        try:
            runtime_repository.cancel_run(run_id)
        except KeyError:
            await websocket.send_json(
                _websocket_error_payload(
                    "run.not_found",
                    "run not found",
                    command="cancel",
                    command_id=normalized_command_id,
                )
            )
            return
        await websocket.send_json(_websocket_command_accepted("cancel", normalized_command_id))
        return
    if command == "resume":
        if not _websocket_has_roles(websocket, {"operator"}):
            await websocket.send_json(
                _websocket_error_payload(
                    "rbac.forbidden",
                    "resume requires operator/admin role",
                    command="resume",
                    command_id=normalized_command_id,
                )
            )
            return
        dedupe = _websocket_command_dedupe_state(run_id, "resume", normalized_command_id)
        if dedupe == "duplicate":
            await websocket.send_json(
                _websocket_command_accepted("resume", normalized_command_id, duplicate=True)
            )
            return
        if dedupe == "conflict":
            await websocket.send_json(
                _websocket_error_payload(
                    "websocket.command_id_conflict",
                    "command_id was already used for another command",
                    command="resume",
                    command_id=normalized_command_id,
                )
            )
            return
        try:
            runtime_repository.resume_run(run_id)
        except KeyError:
            await websocket.send_json(
                _websocket_error_payload(
                    "run.not_found",
                    "run not found",
                    command="resume",
                    command_id=normalized_command_id,
                )
            )
            return
        await websocket.send_json(_websocket_command_accepted("resume", normalized_command_id))
        return
    if command == "approval_decision":
        if not _websocket_has_roles(websocket, {"approver"}):
            await websocket.send_json(
                _websocket_error_payload(
                    "rbac.forbidden",
                    "approval_decision requires approver/admin role",
                    command="approval_decision",
                    command_id=normalized_command_id,
                )
            )
            return
        approval_id = message.get("approval_id")
        approved = message.get("approved")
        if not isinstance(approval_id, str) or not approval_id:
            await websocket.send_json(
                _websocket_error_payload(
                    "websocket.invalid_command",
                    "approval_decision requires approval_id",
                    command="approval_decision",
                    command_id=normalized_command_id,
                )
            )
            return
        if not isinstance(approved, bool):
            await websocket.send_json(
                _websocket_error_payload(
                    "websocket.invalid_command",
                    "approval_decision requires boolean approved",
                    command="approval_decision",
                    command_id=normalized_command_id,
                )
            )
            return
        try:
            approval_run = _run_for_approval(approval_id)
        except KeyError:
            await websocket.send_json(
                _websocket_error_payload(
                    "approval.not_found",
                    "approval not found",
                    command="approval_decision",
                    command_id=normalized_command_id,
                )
            )
            return
        if approval_run.id != run_id:
            await websocket.send_json(
                _websocket_error_payload(
                    "approval.run_mismatch",
                    "approval does not belong to this run",
                    command="approval_decision",
                    command_id=normalized_command_id,
                )
            )
            return
        dedupe = _websocket_command_dedupe_state(
            run_id,
            "approval_decision",
            normalized_command_id,
        )
        if dedupe == "duplicate":
            await websocket.send_json(
                _websocket_command_accepted(
                    "approval_decision",
                    normalized_command_id,
                    duplicate=True,
                )
            )
            return
        if dedupe == "conflict":
            await websocket.send_json(
                _websocket_error_payload(
                    "websocket.command_id_conflict",
                    "command_id was already used for another command",
                    command="approval_decision",
                    command_id=normalized_command_id,
                )
            )
            return
        decided_by = message.get("decided_by")
        comment = message.get("comment")
        runtime_repository.decide_approval(
            approval_id,
            ApprovalDecisionRequest(
                approved=approved,
                decided_by=(
                    decided_by if isinstance(decided_by, str) and decided_by else "websocket"
                ),
                comment=comment if isinstance(comment, str) and comment else None,
            ),
        )
        await websocket.send_json(
            _websocket_command_accepted("approval_decision", normalized_command_id)
        )
        return
    await websocket.send_json(
        _websocket_error_payload(
            "websocket.unknown_command",
            f"unknown command: {command}",
            command=str(command) if command is not None else None,
            command_id=normalized_command_id,
        )
    )


def _websocket_command_dedupe_state(
    run_id: str,
    command: str,
    command_id: str | None,
) -> str:
    if command_id is None:
        return "new"
    now = monotonic()
    expired_keys = [
        key
        for key, (_, recorded_at) in _websocket_command_dedupe.items()
        if now - recorded_at > _WEBSOCKET_COMMAND_DEDUPE_TTL_SECONDS
    ]
    for key in expired_keys:
        _websocket_command_dedupe.pop(key, None)

    key = (run_id, command_id)
    existing = _websocket_command_dedupe.get(key)
    if existing is not None:
        existing_command, _ = existing
        return "duplicate" if existing_command == command else "conflict"

    if len(_websocket_command_dedupe) >= _WEBSOCKET_COMMAND_DEDUPE_MAX_ENTRIES:
        oldest_key = min(
            _websocket_command_dedupe,
            key=lambda item: _websocket_command_dedupe[item][1],
        )
        _websocket_command_dedupe.pop(oldest_key, None)
    _websocket_command_dedupe[key] = (command, now)
    return "new"


def _websocket_command_accepted(
    command: str,
    command_id: str | None,
    *,
    duplicate: bool = False,
) -> dict[str, object]:
    return {
        "type": "command.accepted",
        "ok": True,
        "command": command,
        "command_id": command_id,
        "duplicate": duplicate,
    }


def _websocket_error_payload(
    error_code: str,
    message: str,
    *,
    command: str | None = None,
    command_id: str | None = None,
) -> dict[str, object]:
    return {
        "type": "error",
        "ok": False,
        "error_code": error_code,
        "message": message,
        "command": command,
        "command_id": command_id,
    }


def _configured_tool_policy() -> ToolPolicy:
    config = runtime_config_store.get_tool_policy()
    return ToolPolicy(
        default_mode=config.default_mode,
        allow=config.allow,
        ask=config.ask,
        deny=config.deny,
    )


def _require_actor_roles(request: Request, allowed_roles: set[str]) -> None:
    settings = get_settings()
    if not settings.agent_rbac_enabled:
        return
    roles = _request_roles(request)
    if "admin" in roles or roles.intersection(allowed_roles):
        return
    actor = _actor_name_from_headers(request.headers)
    required = ", ".join(sorted(allowed_roles | {"admin"}))
    raise HTTPException(
        status_code=403,
        detail=f"actor {actor} requires one of roles: {required}",
    )


def _request_roles(request: Request) -> set[str]:
    settings = get_settings()
    actor_policy = _actor_policy_from_headers(request.headers)
    if actor_policy is not None:
        return actor_policy.roles
    if _trusted_policy_required(settings):
        return set()
    return _roles_from_headers(request.headers, roles_header=settings.agent_rbac_roles_header)


def _filter_runs_for_actor(request: Request, runs: list[RunState]) -> list[RunState]:
    if not get_settings().agent_rbac_enabled:
        return runs
    return [
        run
        for run in runs
        if _agent_allowed(request, run.agent_id)
        and _business_view_allowed(request, _run_business_view_id(run))
    ]


def _require_business_view_access(request: Request, business_view_id: str | None) -> None:
    if _business_view_allowed(request, business_view_id):
        return
    actor = _actor_name_from_headers(request.headers)
    raise HTTPException(
        status_code=403,
        detail=f"actor {actor} cannot access business_view_id={business_view_id}",
    )


def _require_agent_access(request: Request, agent_id: str) -> None:
    if _agent_allowed(request, agent_id):
        return
    actor = _actor_name_from_headers(request.headers)
    raise HTTPException(
        status_code=403,
        detail=f"actor {actor} cannot access agent_id={agent_id}",
    )


def _agent_allowed(request: Request, agent_id: str) -> bool:
    settings = get_settings()
    if not settings.agent_rbac_enabled:
        return True
    allowed = _actor_agent_ids(request.headers)
    return allowed is None or "*" in allowed or agent_id in allowed


def _business_view_allowed(request: Request, business_view_id: str | None) -> bool:
    settings = get_settings()
    if not settings.agent_rbac_enabled or business_view_id is None:
        return True
    actor_policy = _actor_policy_from_headers(request.headers)
    if actor_policy is not None:
        allowed_policy_views = actor_policy.business_view_ids
        return (
            allowed_policy_views is None
            or "*" in allowed_policy_views
            or business_view_id in allowed_policy_views
        )
    if _trusted_policy_required(settings):
        return False
    allowed = _business_views_from_headers(
        request.headers,
        business_views_header=settings.agent_rbac_business_views_header,
    )
    return "*" in allowed or business_view_id in allowed


def _websocket_has_roles(websocket: WebSocket, allowed_roles: set[str]) -> bool:
    settings = get_settings()
    if not settings.agent_rbac_enabled:
        return True
    actor_policy = _actor_policy_from_headers(websocket.headers)
    if actor_policy is not None:
        roles = actor_policy.roles
    elif _trusted_policy_required(settings):
        roles = set()
    else:
        roles = _roles_from_headers(
            websocket.headers,
            roles_header=settings.agent_rbac_roles_header,
        )
    return "admin" in roles or bool(roles.intersection(allowed_roles))


def _websocket_has_business_view_access(
    websocket: WebSocket,
    business_view_id: str | None,
) -> bool:
    settings = get_settings()
    if not settings.agent_rbac_enabled or business_view_id is None:
        return True
    actor_policy = _actor_policy_from_headers(websocket.headers)
    if actor_policy is not None:
        allowed_policy_views = actor_policy.business_view_ids
        return (
            allowed_policy_views is None
            or "*" in allowed_policy_views
            or business_view_id in allowed_policy_views
        )
    if _trusted_policy_required(settings):
        return False
    allowed = _business_views_from_headers(
        websocket.headers,
        business_views_header=settings.agent_rbac_business_views_header,
    )
    return "*" in allowed or business_view_id in allowed


def _websocket_has_agent_access(websocket: WebSocket, agent_id: str) -> bool:
    settings = get_settings()
    if not settings.agent_rbac_enabled:
        return True
    actor_policy = _actor_policy_from_headers(websocket.headers)
    if actor_policy is None:
        return not _trusted_policy_required(settings)
    allowed = actor_policy.agent_ids
    return allowed is None or "*" in allowed or agent_id in allowed


def _roles_from_headers(headers: Mapping[str, str], *, roles_header: str) -> set[str]:
    raw_roles = headers.get(roles_header, "")
    return {role.strip().lower() for role in raw_roles.replace(";", ",").split(",") if role.strip()}


def _business_views_from_headers(
    headers: Mapping[str, str],
    *,
    business_views_header: str,
) -> set[str]:
    raw_views = headers.get(business_views_header, "")
    return {view.strip() for view in raw_views.replace(";", ",").split(",") if view.strip()}


def _signed_identity_required(settings: object | None = None) -> bool:
    active_settings = settings if settings is not None else get_settings()
    secret = getattr(active_settings, "agent_rbac_identity_hmac_secret", None)
    return bool(secret)


def _external_rbac_policy_required(settings: object | None = None) -> bool:
    active_settings = settings if settings is not None else get_settings()
    return bool(getattr(active_settings, "agent_rbac_policy_url", None))


def _jwt_identity_required(settings: object | None = None) -> bool:
    active_settings = settings if settings is not None else get_settings()
    return bool(getattr(active_settings, "agent_rbac_jwt_bearer_enabled", False))


def _trusted_policy_required(settings: object | None = None) -> bool:
    active_settings = settings if settings is not None else get_settings()
    return (
        _signed_identity_required(active_settings)
        or _jwt_identity_required(active_settings)
        or _external_rbac_policy_required(active_settings)
    )


def _trusted_identity_claims_from_headers(headers: Mapping[str, str]) -> dict[str, object] | None:
    return _signed_identity_claims_from_headers(headers) or _jwt_claims_from_headers(headers)


def _signed_identity_policy_from_headers(headers: Mapping[str, str]) -> ActorPolicy | None:
    claims = _signed_identity_claims_from_headers(headers)
    if claims is None:
        return None
    return _actor_policy_from_mapping(claims)


def _jwt_policy_from_headers(headers: Mapping[str, str]) -> ActorPolicy | None:
    claims = _jwt_claims_from_headers(headers)
    if claims is None:
        return None
    return _jwt_policy_from_claims(claims)


def _actor_name_from_headers(headers: Mapping[str, str]) -> str:
    settings = get_settings()
    claims = _trusted_identity_claims_from_headers(headers)
    if claims is not None:
        actor = claims.get("actor") or claims.get("sub")
        if isinstance(actor, str) and actor:
            return actor
    return headers.get(settings.agent_rbac_actor_header, "anonymous")


def _signed_identity_claims_from_headers(headers: Mapping[str, str]) -> dict[str, object] | None:
    settings = get_settings()
    secret = settings.agent_rbac_identity_hmac_secret
    if not secret:
        return None
    raw_identity = headers.get(settings.agent_rbac_identity_header, "")
    if "." not in raw_identity:
        return None
    payload_part, signature = raw_identity.rsplit(".", 1)
    expected_signature = hmac.new(
        secret.encode("utf-8"),
        payload_part.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected_signature, signature):
        return None
    try:
        payload = base64.urlsafe_b64decode(_base64url_padded(payload_part)).decode("utf-8")
        loaded = json.loads(payload)
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(loaded, dict):
        return None
    claims = {str(key): value for key, value in loaded.items() if isinstance(key, str)}
    if _signed_identity_time_invalid(claims):
        return None
    return claims


def _jwt_claims_from_headers(headers: Mapping[str, str]) -> dict[str, object] | None:
    settings = get_settings()
    if not settings.agent_rbac_jwt_bearer_enabled:
        return None
    raw_authorization = headers.get("authorization", "") or headers.get("Authorization", "")
    scheme, _, token = raw_authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    parts = token.split(".")
    if len(parts) != 3:
        return None
    header_part, payload_part, signature_part = parts
    try:
        header = json.loads(base64.urlsafe_b64decode(_base64url_padded(header_part)))
        payload = json.loads(base64.urlsafe_b64decode(_base64url_padded(payload_part)))
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(header, dict) or not isinstance(payload, dict):
        return None
    signing_input = f"{header_part}.{payload_part}".encode()
    signature = base64.urlsafe_b64decode(_base64url_padded(signature_part))
    algorithm = header.get("alg")
    if algorithm == "HS256":
        if not settings.agent_rbac_jwt_hs256_secret:
            return None
        if not _jwt_hs256_signature_valid(signing_input, signature_part):
            return None
    elif algorithm == "RS256":
        if not _jwt_rs256_signature_valid(header, signing_input, signature):
            return None
    else:
        return None
    claims = {str(key): value for key, value in payload.items() if isinstance(key, str)}
    if _signed_identity_time_invalid(claims):
        return None
    if settings.agent_rbac_jwt_issuer and claims.get("iss") != settings.agent_rbac_jwt_issuer:
        return None
    if settings.agent_rbac_jwt_audience and not _jwt_audience_matches(
        claims.get("aud"),
        settings.agent_rbac_jwt_audience,
    ):
        return None
    return claims


def _jwt_hs256_signature_valid(signing_input: bytes, signature_part: str) -> bool:
    settings = get_settings()
    if not settings.agent_rbac_jwt_hs256_secret:
        return False
    expected_signature = hmac.new(
        settings.agent_rbac_jwt_hs256_secret.encode("utf-8"),
        signing_input,
        hashlib.sha256,
    ).digest()
    expected_part = base64.urlsafe_b64encode(expected_signature).decode("ascii").rstrip("=")
    return hmac.compare_digest(expected_part, signature_part)


def _jwt_rs256_signature_valid(
    header: Mapping[str, object],
    signing_input: bytes,
    signature: bytes,
) -> bool:
    jwk = _jwt_jwk_for_header(header)
    if jwk is None:
        return False
    try:
        padding_module = cast(
            Any,
            import_module("cryptography.hazmat.primitives.asymmetric.padding"),
        )
        rsa_module = cast(Any, import_module("cryptography.hazmat.primitives.asymmetric.rsa"))
        hashes_module = cast(Any, import_module("cryptography.hazmat.primitives.hashes"))
    except ImportError:
        return False
    modulus = jwk.get("n")
    exponent = jwk.get("e")
    if not isinstance(modulus, str) or not isinstance(exponent, str):
        return False
    try:
        public_numbers = rsa_module.RSAPublicNumbers(
            e=int.from_bytes(base64.urlsafe_b64decode(_base64url_padded(exponent)), "big"),
            n=int.from_bytes(base64.urlsafe_b64decode(_base64url_padded(modulus)), "big"),
        )
        public_key = public_numbers.public_key()
        public_key.verify(
            signature,
            signing_input,
            padding_module.PKCS1v15(),
            hashes_module.SHA256(),
        )
    except Exception:
        return False
    return True


def _jwt_jwk_for_header(header: Mapping[str, object]) -> dict[str, object] | None:
    settings = get_settings()
    if not settings.agent_rbac_jwt_jwks_url:
        return None
    key_id = header.get("kid")
    if not isinstance(key_id, str) or not key_id:
        return None
    jwks = _jwt_jwks(settings.agent_rbac_jwt_jwks_url)
    keys = jwks.get("keys")
    if not isinstance(keys, list):
        return None
    for key in keys:
        if isinstance(key, dict) and key.get("kid") == key_id and key.get("kty") == "RSA":
            return {str(item_key): item_value for item_key, item_value in key.items()}
    return None


def _jwt_jwks(url: str) -> dict[str, object]:
    settings = get_settings()
    cached = _jwt_jwks_cache.get(url)
    now = monotonic()
    if cached is not None:
        payload, expires_at = cached
        if now < expires_at:
            return payload
    try:
        with httpx.Client(timeout=settings.agent_rbac_policy_timeout_seconds) as client:
            response = client.get(url)
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError):
        return {}
    if not isinstance(payload, dict):
        return {}
    ttl = max(0, settings.agent_rbac_jwt_jwks_cache_seconds)
    if ttl > 0:
        _jwt_jwks_cache[url] = (
            {str(key): value for key, value in payload.items() if isinstance(key, str)},
            now + ttl,
        )
    return {str(key): value for key, value in payload.items() if isinstance(key, str)}


def _jwt_audience_matches(value: object, expected: str) -> bool:
    if isinstance(value, str):
        return value == expected
    if isinstance(value, list):
        return any(item == expected for item in value if isinstance(item, str))
    return False


def _base64url_padded(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return f"{value}{padding}".encode("ascii")


def _signed_identity_time_invalid(claims: Mapping[str, object]) -> bool:
    now = datetime.now(UTC).timestamp()
    exp = claims.get("exp")
    if isinstance(exp, int | float) and now > float(exp):
        return True
    nbf = claims.get("nbf")
    return isinstance(nbf, int | float) and now < float(nbf)


def _actor_policy_from_headers(headers: Mapping[str, str]) -> ActorPolicy | None:
    settings = get_settings()
    external_policy = _external_actor_policy_from_headers(headers)
    if external_policy is not None:
        return external_policy
    if _external_rbac_policy_required(settings):
        return None
    signed_policy = _signed_identity_policy_from_headers(headers)
    if signed_policy is not None:
        return signed_policy
    jwt_policy = _jwt_policy_from_headers(headers)
    if jwt_policy is not None:
        return jwt_policy
    if _signed_identity_required(settings) or _jwt_identity_required(settings):
        return None
    if not settings.agent_rbac_actor_policies_json:
        return None
    actor = headers.get(settings.agent_rbac_actor_header, "")
    policies = _actor_policies_from_json(settings.agent_rbac_actor_policies_json)
    return policies.get(actor)


def _external_actor_policy_from_headers(headers: Mapping[str, str]) -> ActorPolicy | None:
    settings = get_settings()
    if not settings.agent_rbac_policy_url:
        return None
    claims = _trusted_identity_claims_from_headers(headers)
    if (_signed_identity_required(settings) or _jwt_identity_required(settings)) and claims is None:
        return None
    actor = _actor_from_claims(claims) or headers.get(settings.agent_rbac_actor_header, "")
    if not actor:
        return None
    cache_key = _external_rbac_policy_cache_key(actor, claims)
    cached_policy = _external_rbac_policy_from_cache(cache_key)
    if cached_policy is not None:
        return cached_policy
    try:
        with httpx.Client(timeout=settings.agent_rbac_policy_timeout_seconds) as client:
            response = client.post(
                settings.agent_rbac_policy_url,
                json={"actor": actor, "claims": claims or {}},
                headers=_external_rbac_policy_headers(settings.agent_rbac_policy_api_key),
            )
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    policy_payload = payload.get("policy", payload)
    if not isinstance(policy_payload, dict):
        return None
    policy = _actor_policy_from_mapping(policy_payload)
    if policy is None:
        return None
    _cache_external_rbac_policy(cache_key, policy)
    return policy


def _actor_from_claims(claims: Mapping[str, object] | None) -> str | None:
    if claims is None:
        return None
    actor = claims.get("actor") or claims.get("sub")
    return actor if isinstance(actor, str) and actor else None


def _external_rbac_policy_headers(api_key: str | None) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}"} if api_key else {}


def _external_rbac_policy_cache_key(
    actor: str,
    claims: Mapping[str, object] | None,
) -> str:
    claims_json = json.dumps(claims or {}, ensure_ascii=False, sort_keys=True, default=str)
    digest = hashlib.sha256(claims_json.encode("utf-8")).hexdigest()
    return f"{actor}:{digest}"


def _external_rbac_policy_from_cache(cache_key: str) -> ActorPolicy | None:
    settings = get_settings()
    if settings.agent_rbac_policy_cache_seconds <= 0:
        return None
    cached = _rbac_policy_cache.get(cache_key)
    if cached is None:
        return None
    policy, expires_at = cached
    if monotonic() >= expires_at:
        _rbac_policy_cache.pop(cache_key, None)
        return None
    return policy


def _cache_external_rbac_policy(cache_key: str, policy: ActorPolicy) -> None:
    settings = get_settings()
    ttl = settings.agent_rbac_policy_cache_seconds
    if ttl <= 0:
        return
    if len(_rbac_policy_cache) > 1000:
        now = monotonic()
        expired = [
            key for key, (_policy, expires_at) in _rbac_policy_cache.items() if now >= expires_at
        ]
        for key in expired:
            _rbac_policy_cache.pop(key, None)
    _rbac_policy_cache[cache_key] = (policy, monotonic() + ttl)


def _actor_policies_from_json(raw: str) -> dict[str, ActorPolicy]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    policies: dict[str, ActorPolicy] = {}
    for actor, policy_value in parsed.items():
        if not isinstance(actor, str) or not isinstance(policy_value, dict):
            continue
        policy = _actor_policy_from_mapping(policy_value)
        if policy is not None:
            policies[actor] = policy
    return policies


def _actor_policy_from_mapping(value: Mapping[str, object]) -> ActorPolicy | None:
    roles = _string_set_from_policy(value.get("roles"))
    if not roles:
        return None
    return ActorPolicy(
        roles={role.lower() for role in roles},
        business_view_ids=_optional_string_set_from_policy(value.get("business_view_ids")),
        agent_ids=_optional_string_set_from_policy(value.get("agent_ids")),
    )


def _jwt_policy_from_claims(claims: Mapping[str, object]) -> ActorPolicy | None:
    settings = get_settings()
    policy_payload = {
        "roles": claims.get(settings.agent_rbac_jwt_roles_claim),
        "business_view_ids": claims.get(settings.agent_rbac_jwt_business_views_claim),
        "agent_ids": claims.get(settings.agent_rbac_jwt_agent_ids_claim),
    }
    return _actor_policy_from_mapping(policy_payload)


def _actor_agent_ids(headers: Mapping[str, str]) -> set[str] | None:
    settings = get_settings()
    actor_policy = _actor_policy_from_headers(headers)
    if actor_policy is None:
        if _trusted_policy_required(settings):
            return set()
        return None
    return actor_policy.agent_ids


def _string_set_from_policy(value: object) -> set[str]:
    if isinstance(value, str):
        return {item.strip() for item in value.replace(";", ",").split(",") if item.strip()}
    if isinstance(value, list):
        return {item.strip() for item in value if isinstance(item, str) and item.strip()}
    return set()


def _optional_string_set_from_policy(value: object) -> set[str] | None:
    if value is None:
        return None
    return _string_set_from_policy(value)


def _run_create_business_view_id(request: RunCreateRequest) -> str | None:
    metadata_view = request.metadata.get("business_view_id")
    if isinstance(metadata_view, str) and metadata_view:
        return metadata_view
    for call in request.tool_calls:
        view = call.arguments.get("business_view_id")
        if isinstance(view, str) and view:
            return view
    return None


def _run_business_view_id(run: RunState) -> str | None:
    metadata_view = run.metadata.get("business_view_id")
    if isinstance(metadata_view, str) and metadata_view:
        return metadata_view
    for call in run.pending_tool_calls:
        view = call.arguments.get("business_view_id")
        if isinstance(view, str) and view:
            return view
    for step in run.steps:
        if step.tool_call is None:
            continue
        view = step.tool_call.arguments.get("business_view_id")
        if isinstance(view, str) and view:
            return view
    return None


def _run_for_approval(approval_id: str) -> RunState:
    for run in runtime_repository.list_runs():
        if any(approval.id == approval_id for approval in run.approvals):
            return run
    raise KeyError(approval_id)


def _tool_policy_settings_response() -> ToolPolicySettings:
    config = runtime_config_store.get_tool_policy()
    return ToolPolicySettings(
        default_mode=config.default_mode,
        allow=sorted(config.allow),
        ask=sorted(config.ask),
        deny=sorted(config.deny),
    )


def _validate_tool_policy_patch(patch: ToolPolicySettingsPatch) -> None:
    if patch.default_mode is not None and patch.default_mode not in {"approval", "deny"}:
        raise ValueError("default_mode must be approval or deny")
    registered_tools = set(tool_registry.names())
    unknown_tools = sorted(
        {
            name
            for names in (patch.allow, patch.ask, patch.deny)
            if names is not None
            for name in names
            if name not in registered_tools
        }
    )
    if unknown_tools:
        raise ValueError(f"unknown tool: {', '.join(unknown_tools)}")


def _runtime_safety_settings_response() -> RuntimeSafetySettings:
    config = runtime_config_store.get_runtime_safety()
    return RuntimeSafetySettings(
        max_tool_calls_per_run=config.max_tool_calls_per_run,
        max_pending_approvals_per_run=config.max_pending_approvals_per_run,
    )


def _validate_runtime_safety_patch(patch: RuntimeSafetySettingsPatch) -> None:
    if patch.max_tool_calls_per_run is not None and patch.max_tool_calls_per_run < 0:
        raise ValueError("max_tool_calls_per_run must be greater than or equal to 0")
    if patch.max_pending_approvals_per_run is not None and patch.max_pending_approvals_per_run < 0:
        raise ValueError("max_pending_approvals_per_run must be greater than or equal to 0")


def _planner_settings_response() -> PlannerSettings:
    config = runtime_config_store.get_planner()
    return PlannerSettings(
        provider=config.provider,
        oci_responses_base_url=config.oci_responses_base_url,
        oci_responses_base_url_configured=bool(config.oci_responses_base_url),
        oci_responses_api_key_configured=bool(config.oci_responses_api_key),
        oci_responses_model=config.oci_responses_model,
        oci_responses_model_configured=bool(config.oci_responses_model),
        oci_responses_project=config.oci_responses_project,
        oci_responses_project_configured=bool(config.oci_responses_project),
        oci_agent_endpoint=config.oci_agent_endpoint,
        oci_agent_endpoint_configured=bool(config.oci_agent_endpoint),
        oci_agent_api_key_configured=bool(config.oci_agent_api_key),
        timeout_seconds=config.timeout_seconds,
        max_retries=config.max_retries,
        fallback_to_heuristic=config.fallback_to_heuristic,
        allowed_tool_names=list(config.allowed_tool_names),
        allow_command_generation=config.allow_command_generation,
    )


def _validate_planner_patch(patch: PlannerSettingsPatch) -> None:
    if patch.provider is not None:
        provider = patch.provider.strip().lower()
        if provider in {"enterprise_ai", "enterprise-ai"}:
            provider = "oci_responses"
        if provider not in {"heuristic", "oci_responses", "oci_agent"}:
            raise ValueError("provider must be heuristic, oci_responses, or oci_agent")
        patch.provider = provider
    if (
        patch.oci_responses_base_url is not None
        and patch.oci_responses_base_url
        and not patch.oci_responses_base_url.startswith(("http://", "https://"))
    ):
        raise ValueError("oci_responses_base_url must be an http or https URL")
    if (
        patch.oci_agent_endpoint is not None
        and patch.oci_agent_endpoint
        and not patch.oci_agent_endpoint.startswith(("http://", "https://"))
    ):
        raise ValueError("oci_agent_endpoint must be an http or https URL")
    if (
        patch.enterprise_ai_endpoint is not None
        and patch.enterprise_ai_endpoint
        and not patch.enterprise_ai_endpoint.startswith(("http://", "https://"))
    ):
        raise ValueError("enterprise_ai_endpoint must be an http or https URL")
    if patch.timeout_seconds is not None and patch.timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be greater than 0")
    if patch.max_retries is not None and patch.max_retries < 0:
        raise ValueError("max_retries must be greater than or equal to 0")
    if patch.allowed_tool_names is not None:
        normalized = _normalized_tool_names(patch.allowed_tool_names)
        registered = set(tool_registry.names())
        unknown = sorted({name for name in normalized if name not in registered})
        if unknown:
            raise ValueError(f"unknown tool: {', '.join(unknown)}")
        patch.allowed_tool_names = normalized


def _command_policy_settings_response() -> CommandPolicySettings:
    config = runtime_config_store.get_command_policy()
    return CommandPolicySettings(
        enabled=config.enabled,
        workspace_root=config.workspace_root,
        allowed_prefixes=list(config.allowed_prefixes),
        default_timeout_seconds=config.default_timeout_seconds,
        max_timeout_seconds=config.max_timeout_seconds,
        output_limit_bytes=config.output_limit_bytes,
        artifact_storage_backend=config.artifact_storage_backend,
        artifact_storage_path=config.artifact_storage_path,
    )


def _mcp_oauth_configured(config: object) -> bool:
    return bool(
        getattr(config, "oauth_token_url", None)
        and getattr(config, "oauth_client_id", None)
        and getattr(config, "oauth_client_secret", None)
    )


def _validate_command_policy_patch(patch: CommandPolicySettingsPatch) -> None:
    if patch.workspace_root is not None and not patch.workspace_root.strip():
        raise ValueError("workspace_root must not be empty")
    if patch.allowed_prefixes is not None:
        patch.allowed_prefixes = _normalized_command_prefixes(patch.allowed_prefixes)
    if patch.default_timeout_seconds is not None and patch.default_timeout_seconds <= 0:
        raise ValueError("default_timeout_seconds must be greater than 0")
    if patch.max_timeout_seconds is not None and patch.max_timeout_seconds <= 0:
        raise ValueError("max_timeout_seconds must be greater than 0")
    current = runtime_config_store.get_command_policy()
    default_timeout = patch.default_timeout_seconds or current.default_timeout_seconds
    max_timeout = patch.max_timeout_seconds or current.max_timeout_seconds
    if default_timeout > max_timeout:
        raise ValueError(
            "default_timeout_seconds must be less than or equal to max_timeout_seconds"
        )
    if patch.output_limit_bytes is not None and patch.output_limit_bytes <= 0:
        raise ValueError("output_limit_bytes must be greater than 0")
    if patch.artifact_storage_backend is not None:
        backend = patch.artifact_storage_backend.strip().lower()
        if backend not in {"inline", "filesystem"}:
            raise ValueError("artifact_storage_backend must be inline or filesystem")
        patch.artifact_storage_backend = backend
    if patch.artifact_storage_path is not None and not patch.artifact_storage_path.strip():
        raise ValueError("artifact_storage_path must not be empty")


def _normalized_command_prefixes(prefixes: list[str]) -> list[str]:
    return sorted({prefix.strip() for prefix in prefixes if prefix.strip()})


def _normalized_tool_names(tool_names: list[str]) -> list[str]:
    return sorted({name.strip() for name in tool_names if name.strip()})


def _run_audit_data(run: RunState) -> RunAuditData:
    approvals = {approval.id: approval for approval in run.approvals}
    artifact_ids_by_step: dict[str, list[str]] = {}
    for event in run.events:
        if event.type != "artifact.created":
            continue
        step_id = event.payload.get("step_id")
        artifact_id = event.payload.get("artifact_id")
        if isinstance(step_id, str) and isinstance(artifact_id, str):
            artifact_ids_by_step.setdefault(step_id, []).append(artifact_id)

    records: list[ToolAuditRecord] = []
    for step in run.steps:
        tool_name = step.tool_call.name if step.tool_call else step.kind
        result = step.tool_result
        approval = approvals.get(step.approval_id or "")
        definition = tool_registry.get(tool_name)
        audit_metadata = result.audit_metadata if result is not None else {}
        records.append(
            ToolAuditRecord(
                step_id=step.id,
                tool_name=tool_name,
                status=step.status.value,
                approval_id=step.approval_id,
                approval_status=approval.status.value if approval else None,
                policy_decision=result.policy_decision.value if result else None,
                permission_level=(
                    definition.permission_level.value
                    if definition is not None
                    else _audit_text(audit_metadata, "permission_level")
                ),
                side_effects=(
                    definition.side_effects
                    if definition is not None
                    else _audit_bool(audit_metadata, "side_effects")
                ),
                started_at=step.started_at.isoformat() if step.started_at else None,
                completed_at=step.completed_at.isoformat() if step.completed_at else None,
                duration_ms=result.duration_ms if result else None,
                success=result.success if result else None,
                error=result.error if result else None,
                error_code=result.error_code if result else None,
                guardrail_warnings=result.guardrail_warnings if result else [],
                trace_id=step.tool_call.trace_id if step.tool_call else None,
                artifact_ids=artifact_ids_by_step.get(step.id, []),
                audit_metadata=audit_metadata,
            )
        )
    return RunAuditData(
        run_id=run.id,
        goal=run.goal,
        status=run.status.value,
        records=records,
    )


def _tool_call_audit_data(
    *,
    request: Request,
    run_id: str | None,
    tool_name: str | None,
    status: str | None,
    approval_status: str | None,
    error_code: str | None,
    has_guardrail_warnings: bool | None,
    offset: int,
    limit: int,
) -> ToolCallAuditData:
    filters = _audit_filters(
        run_id=run_id,
        tool_name=tool_name,
        status=status,
        approval_status=approval_status,
        error_code=error_code,
        has_guardrail_warnings=has_guardrail_warnings,
    )
    projection_reader = getattr(runtime_repository, "list_tool_call_audit_projection", None)
    if callable(projection_reader) and _actor_agent_ids(request.headers) is None:
        try:
            projection = projection_reader(
                run_id=run_id,
                tool_name=tool_name,
                status=status,
                approval_status=approval_status,
                error_code=error_code,
                has_guardrail_warnings=has_guardrail_warnings,
                business_view_ids=_projection_business_view_allowlist(request),
                offset=offset,
                limit=limit,
            )
            if isinstance(projection, RuntimeToolCallAuditData):
                return ToolCallAuditData(
                    total=projection.total,
                    offset=projection.offset,
                    limit=projection.limit,
                    filters=filters,
                    records=[
                        ToolCallAuditRecord.model_validate(record.model_dump())
                        for record in projection.records
                    ],
                )
        except RuntimeError:
            pass

    records: list[ToolCallAuditRecord] = []
    for run in _filter_runs_for_actor(request, runtime_repository.list_runs()):
        if run_id is not None and run.id != run_id:
            continue
        audit = _run_audit_data(run)
        for record in audit.records:
            enriched = ToolCallAuditRecord(
                **record.model_dump(),
                run_id=run.id,
                run_goal=run.goal,
                run_status=run.status.value,
                agent_id=run.agent_id,
                run_created_at=run.created_at.isoformat(),
                run_updated_at=run.updated_at.isoformat(),
            )
            if _audit_record_matches(
                enriched,
                tool_name=tool_name,
                status=status,
                approval_status=approval_status,
                error_code=error_code,
                has_guardrail_warnings=has_guardrail_warnings,
            ):
                records.append(enriched)
    total = len(records)
    return ToolCallAuditData(
        total=total,
        offset=offset,
        limit=limit,
        filters=filters,
        records=records[offset : offset + limit],
    )


def _projection_business_view_allowlist(request: Request) -> set[str] | None:
    settings = get_settings()
    if not settings.agent_rbac_enabled:
        return None
    actor_policy = _actor_policy_from_headers(request.headers)
    if actor_policy is not None:
        allowed_policy_views = actor_policy.business_view_ids
        if allowed_policy_views is None or "*" in allowed_policy_views:
            return None
        return allowed_policy_views
    if _trusted_policy_required(settings):
        return set()
    allowed = _business_views_from_headers(
        request.headers,
        business_views_header=settings.agent_rbac_business_views_header,
    )
    if "*" in allowed:
        return None
    return allowed


def _audit_filters(
    *,
    run_id: str | None,
    tool_name: str | None,
    status: str | None,
    approval_status: str | None,
    error_code: str | None,
    has_guardrail_warnings: bool | None,
) -> dict[str, object]:
    filters: dict[str, object] = {}
    if run_id is not None:
        filters["run_id"] = run_id
    if tool_name is not None:
        filters["tool_name"] = tool_name
    if status is not None:
        filters["status"] = status
    if approval_status is not None:
        filters["approval_status"] = approval_status
    if error_code is not None:
        filters["error_code"] = error_code
    if has_guardrail_warnings is not None:
        filters["has_guardrail_warnings"] = has_guardrail_warnings
    return filters


def _audit_record_matches(
    record: ToolCallAuditRecord,
    *,
    tool_name: str | None,
    status: str | None,
    approval_status: str | None,
    error_code: str | None,
    has_guardrail_warnings: bool | None,
) -> bool:
    if tool_name is not None and record.tool_name != tool_name:
        return False
    if status is not None and record.status != status:
        return False
    if approval_status is not None and record.approval_status != approval_status:
        return False
    if error_code is not None and record.error_code != error_code:
        return False
    return not (
        has_guardrail_warnings is not None
        and bool(record.guardrail_warnings) != has_guardrail_warnings
    )


def _tool_call_audit_csv(records: list[ToolCallAuditRecord]) -> str:
    fieldnames = [
        "run_id",
        "run_goal",
        "run_status",
        "agent_id",
        "step_id",
        "tool_name",
        "status",
        "approval_id",
        "approval_status",
        "policy_decision",
        "permission_level",
        "side_effects",
        "started_at",
        "completed_at",
        "duration_ms",
        "success",
        "error_code",
        "error",
        "guardrail_warnings",
        "trace_id",
        "artifact_ids",
        "run_created_at",
        "run_updated_at",
    ]
    output = StringIO()
    writer = DictWriter(output, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for record in records:
        row = record.model_dump(mode="json")
        row["guardrail_warnings"] = "|".join(record.guardrail_warnings)
        row["artifact_ids"] = "|".join(record.artifact_ids)
        writer.writerow(row)
    return output.getvalue()


def _audit_text(metadata: dict[str, object], key: str) -> str | None:
    value = metadata.get(key)
    if isinstance(value, str):
        return value
    return None


def _audit_bool(metadata: dict[str, object], key: str) -> bool | None:
    value = metadata.get(key)
    if isinstance(value, bool):
        return value
    return None
