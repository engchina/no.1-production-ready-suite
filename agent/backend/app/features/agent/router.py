"""Agent Runtime API。

業務 RAG / NL2SQL は外部ツールとして扱い、このサービスは実行管理・
権限・監査・表示用の標準契約を提供する。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from asyncio import sleep, wait_for
from collections.abc import Iterable, Mapping
from csv import DictWriter
from datetime import UTC, datetime
from importlib import import_module
from io import StringIO
from time import monotonic
from typing import Any, cast

import httpx
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
from pydantic import BaseModel, Field

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
    model_id: str
    display_name: str
    vision_enabled: bool = False


class EnterpriseAiModelSettings(BaseModel):
    endpoint: str = ""
    project_ocid: str = ""
    api_key: str = ""
    has_api_key: bool = False
    clear_api_key: bool = False
    models: list[EnterpriseAiConfiguredModel] = Field(default_factory=list)
    default_model_id: str = ""
    api_path: str = "/responses"
    vlm_input_mode: str = "auto"
    text_payload_template: str = ""
    vision_payload_template: str = ""
    text_response_path: str = "output_text"
    vision_response_path: str = "output_text"
    timeout_seconds: int = 60
    max_retries: int = 3


class GenerativeAiModelSettings(BaseModel):
    embedding_model: str = "cohere.embed-v4.0"
    embedding_dim: int = 1536
    rerank_model: str = "cohere.rerank-v4.0-fast"


class ModelSettingsPayload(BaseModel):
    enterprise_ai: EnterpriseAiModelSettings
    generative_ai: GenerativeAiModelSettings


class ModelSettingsData(BaseModel):
    settings: ModelSettingsPayload
    checks: dict[str, str]
    model_settings_file: str = "runtime"
    source: str = "runtime"


class ModelSettingsTestRequest(BaseModel):
    settings: ModelSettingsPayload
    target_type: str
    model_id: str
    vision_enabled: bool = False


class ModelSettingsTestResult(BaseModel):
    status: str
    target_type: str
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
    user: str = ""
    dsn: str = ""
    wallet_dir: str = ""
    password: str | None = None
    wallet_password: str | None = None
    clear_password: bool = False
    clear_wallet_password: bool = False


class DatabaseConnectionTestResult(BaseModel):
    status: str
    readiness: str
    message: str
    elapsed_ms: int = 0
    troubleshooting: list[str] = Field(default_factory=list)
    details: dict[str, str | int | bool | None] = Field(default_factory=dict)
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
    adb_ocid: str = ""
    region: str = ""


class UploadStorageSettingsData(BaseModel):
    backend: str = "local"
    local_storage_dir: str = "/u01/production-ready-rag"
    object_storage_region: str = "ap-osaka-1"
    object_storage_namespace: str = ""
    object_storage_bucket: str = ""
    readiness: str = "missing"
    max_upload_bytes: int = 100 * 1024 * 1024
    config_source: str = "runtime"


class UploadStorageSettingsUpdate(BaseModel):
    backend: str = "local"
    local_storage_dir: str = ""
    object_storage_namespace: str | None = None
    object_storage_bucket: str = ""


class OciConfigReadRequest(BaseModel):
    config_file: str = "~/.oci/config"
    profile: str = "DEFAULT"


class OciConfigReadData(BaseModel):
    profile: str = "DEFAULT"
    user: str = ""
    fingerprint: str = ""
    tenancy: str = ""
    region: str = ""
    key_file: str = "~/.oci/oci_api_key.pem"
    applied_fields: list[str] = Field(default_factory=list)


class OciSettingsUpdate(BaseModel):
    user: str = ""
    fingerprint: str = ""
    tenancy: str = ""
    region: str = ""


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
    object_storage_region: str = "ap-osaka-1"
    object_storage_namespace: str = ""


class OciConfigTestResult(BaseModel):
    status: str
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
    config_file: str = "~/.oci/config"
    profile: str = "DEFAULT"
    region: str = "ap-osaka-1"


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


def _settings_int(name: str, default: int) -> int:
    value = _settings_attr(name, default)
    if isinstance(value, int):
        return value
    if isinstance(value, float | str):
        try:
            return int(value)
        except ValueError:
            return default
    return default


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _get_model_settings_state() -> ModelSettingsPayload:
    global _model_settings_state
    if _model_settings_state is None:
        model_id = str(
            _settings_attr("enterprise_ai_default_model_id", "")
            or _settings_attr("agent_planner_oci_responses_model", "")
            or "enterprise-llm"
        )
        api_key = str(
            _settings_attr("enterprise_ai_api_key", "")
            or _settings_attr("agent_planner_oci_responses_api_key", "")
            or ""
        )
        _model_settings_state = ModelSettingsPayload(
            enterprise_ai=EnterpriseAiModelSettings(
                endpoint=str(
                    _settings_attr("enterprise_ai_endpoint", "")
                    or _settings_attr("agent_planner_oci_responses_base_url", "")
                    or ""
                ),
                project_ocid=str(
                    _settings_attr("enterprise_ai_project_ocid", "")
                    or _settings_attr("agent_planner_oci_responses_project", "")
                    or ""
                ),
                api_key="",
                has_api_key=bool(api_key),
                models=[
                    EnterpriseAiConfiguredModel(
                        model_id=model_id,
                        display_name="業務 RAG 標準",
                        vision_enabled=True,
                    )
                ],
                default_model_id=model_id,
                api_path=str(_settings_attr("enterprise_ai_api_path", "/responses")),
                vlm_input_mode=str(_settings_attr("enterprise_ai_vlm_input_mode", "auto")),
                text_response_path=str(
                    _settings_attr("enterprise_ai_text_response_path", "output_text")
                ),
                vision_response_path=str(
                    _settings_attr("enterprise_ai_vision_response_path", "output_text")
                ),
                timeout_seconds=_settings_int("enterprise_ai_timeout_seconds", 60),
                max_retries=_settings_int("enterprise_ai_max_retries", 3),
            ),
            generative_ai=GenerativeAiModelSettings(
                embedding_model=str(_settings_attr("embedding_model", "cohere.embed-v4.0")),
                embedding_dim=_settings_int("embedding_dim", 1536),
                rerank_model=str(_settings_attr("rerank_model", "cohere.rerank-v4.0-fast")),
            ),
        )
    return _model_settings_state.model_copy(deep=True)


def _set_model_settings_state(payload: ModelSettingsPayload) -> ModelSettingsPayload:
    global _model_settings_state
    current = _get_model_settings_state()
    stored = payload.model_copy(deep=True)
    if stored.enterprise_ai.clear_api_key:
        stored.enterprise_ai.has_api_key = False
    elif stored.enterprise_ai.api_key:
        stored.enterprise_ai.has_api_key = True
    else:
        stored.enterprise_ai.has_api_key = current.enterprise_ai.has_api_key
    stored.enterprise_ai.api_key = ""
    stored.enterprise_ai.clear_api_key = False
    _model_settings_state = stored
    return stored.model_copy(deep=True)


def _model_settings_checks(payload: ModelSettingsPayload) -> dict[str, str]:
    enterprise = payload.enterprise_ai
    genai = payload.generative_ai
    has_default_model = bool(
        enterprise.default_model_id
        and any(model.model_id == enterprise.default_model_id for model in enterprise.models)
    )
    enterprise_ok = bool(
        enterprise.endpoint
        and enterprise.project_ocid
        and enterprise.has_api_key
        and enterprise.models
        and has_default_model
    )
    genai_ok = bool(genai.embedding_model and genai.rerank_model)
    return {
        "enterprise_ai": "ok" if enterprise_ok else "missing",
        "generative_ai": "ok" if genai_ok else "missing",
        "embedding_dim": "ok" if genai.embedding_dim == 1536 else "invalid",
    }


def _model_settings_data(payload: ModelSettingsPayload | None = None) -> ModelSettingsData:
    settings_payload = payload or _get_model_settings_state()
    public_payload = settings_payload.model_copy(deep=True)
    public_payload.enterprise_ai.api_key = ""
    public_payload.enterprise_ai.clear_api_key = False
    return ModelSettingsData(
        settings=public_payload,
        checks=_model_settings_checks(public_payload),
    )


def _get_database_settings_state() -> DatabaseSettingsData:
    global _database_settings_state
    if _database_settings_state is None:
        settings = get_settings()
        dsn = str(_settings_attr("oracle_dsn", "") or settings.agent_runtime_oracle_dsn or "")
        user = str(_settings_attr("oracle_user", "") or settings.agent_runtime_oracle_user or "")
        wallet_dir = str(_settings_attr("oracle_wallet_dir", "") or "")
        has_password = bool(
            _settings_attr("oracle_password", "") or settings.agent_runtime_oracle_password
        )
        _database_settings_state = DatabaseSettingsData(
            user=user,
            dsn=dsn,
            wallet_dir=wallet_dir,
            wallet_uploaded=bool(_settings_attr("oracle_wallet_uploaded", False)),
            available_services=[dsn] if dsn else [],
            has_password=has_password,
            has_wallet_password=bool(_settings_attr("oracle_wallet_password", "")),
            adb_ocid=str(_settings_attr("adb_ocid", "")),
            region=str(_settings_attr("oci_region", "") or _settings_attr("oracle_region", "")),
        )
        _database_settings_state.readiness = _database_readiness(_database_settings_state)
    return _database_settings_state.model_copy(deep=True)


def _set_database_settings_state(patch: DatabaseSettingsUpdate) -> DatabaseSettingsData:
    global _database_settings_state
    current = _get_database_settings_state()
    current.user = patch.user.strip()
    current.dsn = patch.dsn.strip()
    current.wallet_dir = patch.wallet_dir.strip()
    current.available_services = [current.dsn] if current.dsn else []
    if patch.clear_password:
        current.has_password = False
    elif patch.password:
        current.has_password = True
    if patch.clear_wallet_password:
        current.has_wallet_password = False
    elif patch.wallet_password:
        current.has_wallet_password = True
    current.readiness = _database_readiness(current)
    _database_settings_state = current
    return current.model_copy(deep=True)


def _database_readiness(data: DatabaseSettingsData) -> str:
    if not data.user or not data.dsn:
        return "missing"
    if not (data.has_password or data.wallet_uploaded or data.has_wallet_password):
        return "missing_credentials"
    return "ok"


def _get_upload_storage_settings_state() -> UploadStorageSettingsData:
    global _upload_storage_settings_state
    if _upload_storage_settings_state is None:
        _upload_storage_settings_state = UploadStorageSettingsData(
            backend=str(_settings_attr("upload_storage_backend", "local")),
            local_storage_dir=str(_settings_attr("local_storage_dir", "/u01/production-ready-rag")),
            object_storage_region=str(_settings_attr("object_storage_region", "ap-osaka-1")),
            object_storage_namespace=str(_settings_attr("object_storage_namespace", "")),
            object_storage_bucket=str(_settings_attr("object_storage_bucket", "")),
            max_upload_bytes=_settings_int("max_upload_bytes", 100 * 1024 * 1024),
        )
        _upload_storage_settings_state.readiness = _upload_storage_readiness(
            _upload_storage_settings_state
        )
    return _upload_storage_settings_state.model_copy(deep=True)


def _set_upload_storage_settings_state(
    patch: UploadStorageSettingsUpdate,
) -> UploadStorageSettingsData:
    global _upload_storage_settings_state
    current = _get_upload_storage_settings_state()
    current.backend = patch.backend
    current.local_storage_dir = patch.local_storage_dir.strip()
    if patch.object_storage_namespace is not None:
        current.object_storage_namespace = patch.object_storage_namespace.strip()
    current.object_storage_bucket = patch.object_storage_bucket.strip()
    current.readiness = _upload_storage_readiness(current)
    _upload_storage_settings_state = current
    return current.model_copy(deep=True)


def _upload_storage_readiness(data: UploadStorageSettingsData) -> str:
    if data.backend == "oci":
        if not data.object_storage_namespace or not data.object_storage_bucket:
            return "missing"
        return "ok"
    return "ok" if data.local_storage_dir else "missing"


def _get_oci_settings_state() -> OciSettingsData:
    global _oci_settings_state
    if _oci_settings_state is None:
        _oci_settings_state = OciSettingsData(
            config_file=str(_settings_attr("oci_config_file", "~/.oci/config")),
            profile=str(_settings_attr("oci_config_profile", "DEFAULT")),
            user=str(_settings_attr("oci_user_ocid", "")),
            fingerprint=str(_settings_attr("oci_fingerprint", "")),
            tenancy=str(_settings_attr("oci_tenancy_ocid", "")),
            region=str(_settings_attr("oci_region", "us-chicago-1")),
            key_file=str(_settings_attr("oci_key_file", "~/.oci/oci_api_key.pem")),
            key_file_exists=bool(_settings_attr("oci_key_file_exists", False)),
            config_file_exists=bool(_settings_attr("oci_config_file_exists", False)),
        )
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


@router.get("/settings/model", response_model=ApiResponse[ModelSettingsData])
async def get_model_settings() -> ApiResponse[ModelSettingsData]:
    return ApiResponse(data=_model_settings_data())


@router.patch("/settings/model", response_model=ApiResponse[ModelSettingsData])
async def patch_model_settings(
    patch: ModelSettingsPayload,
    _: None = Depends(require_admin),
) -> ApiResponse[ModelSettingsData]:
    return ApiResponse(data=_model_settings_data(_set_model_settings_state(patch)))


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
    return ApiResponse(data=_set_database_settings_state(patch))


@router.post("/settings/database/wallet", response_model=ApiResponse[DatabaseSettingsData])
async def upload_database_wallet(
    request: Request,
    _: None = Depends(require_admin),
) -> ApiResponse[DatabaseSettingsData]:
    global _database_settings_state
    filename = await _uploaded_filename(request)
    if filename and not filename.lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="Wallet ZIP を選択してください")
    data = _get_database_settings_state()
    data.wallet_uploaded = True
    data.readiness = _database_readiness(data)
    _database_settings_state = data
    return ApiResponse(data=data)


@router.post("/settings/database/test", response_model=ApiResponse[DatabaseConnectionTestResult])
async def test_database_settings(
    patch: DatabaseSettingsUpdate,
) -> ApiResponse[DatabaseConnectionTestResult]:
    data = _get_database_settings_state()
    data.user = patch.user.strip()
    data.dsn = patch.dsn.strip()
    data.wallet_dir = patch.wallet_dir.strip()
    if patch.password:
        data.has_password = True
    if patch.wallet_password:
        data.has_wallet_password = True
    readiness = _database_readiness(data)
    ok = readiness == "ok"
    return ApiResponse(
        data=DatabaseConnectionTestResult(
            status="success" if ok else "failed",
            readiness=readiness,
            message=(
                "接続設定の形式を確認しました。"
                if ok
                else "接続に必要な設定が不足しています。"
            ),
            troubleshooting=(
                []
                if ok
                else ["ユーザー名、DSN、パスワードまたは Wallet を確認してください。"]
            ),
            checked_at=_now_iso(),
            error_type=None if ok else readiness,
            details={"dry_run": True, "dsn": data.dsn or None},
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
    return ApiResponse(data=_set_upload_storage_settings_state(patch))


@router.get("/settings/oci", response_model=ApiResponse[OciSettingsData])
async def get_oci_settings() -> ApiResponse[OciSettingsData]:
    return ApiResponse(data=_get_oci_settings_state())


@router.patch("/settings/oci", response_model=ApiResponse[OciSettingsData])
async def patch_oci_settings(
    patch: OciSettingsUpdate,
    _: None = Depends(require_admin),
) -> ApiResponse[OciSettingsData]:
    return ApiResponse(data=_set_oci_settings_state(patch))


@router.patch("/settings/oci/object-storage", response_model=ApiResponse[UploadStorageSettingsData])
async def patch_oci_object_storage_settings(
    patch: OciObjectStorageSettingsUpdate,
    _: None = Depends(require_admin),
) -> ApiResponse[UploadStorageSettingsData]:
    global _upload_storage_settings_state
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
    return ApiResponse(data=updated)


@router.post("/settings/oci/config/read", response_model=ApiResponse[OciConfigReadData])
async def read_oci_config(_: OciConfigReadRequest) -> ApiResponse[OciConfigReadData]:
    data = _get_oci_settings_state()
    applied = [
        field
        for field, value in {
            "user": data.user,
            "fingerprint": data.fingerprint,
            "tenancy": data.tenancy,
            "region": data.region,
            "key_file": data.key_file if data.key_file_exists else "",
        }.items()
        if value
    ]
    return ApiResponse(
        data=OciConfigReadData(
            profile=data.profile,
            user=data.user,
            fingerprint=data.fingerprint,
            tenancy=data.tenancy,
            region=data.region,
            key_file=data.key_file,
            applied_fields=applied,
        )
    )


@router.post("/settings/oci/config/test", response_model=ApiResponse[OciConfigTestResult])
async def test_oci_config() -> ApiResponse[OciConfigTestResult]:
    data = _get_oci_settings_state()
    missing = _oci_missing_fields(data)
    return ApiResponse(
        data=OciConfigTestResult(
            status="success" if not missing else "failed",
            profile=data.profile,
            config_file=data.config_file,
            key_file=data.key_file,
            config_file_exists=data.config_file_exists,
            key_file_exists=data.key_file_exists,
            missing_fields=missing,
            message=(
                "OCI 設定の形式を確認しました。"
                if not missing
                else "OCI 設定に不足があります。"
            ),
            checked_at=_now_iso(),
            error_type=None if not missing else "missing_fields",
        )
    )


@router.post(
    "/settings/oci/object-storage/namespace",
    response_model=ApiResponse[OciObjectStorageNamespaceData],
)
async def read_oci_object_storage_namespace(
    _: OciObjectStorageNamespaceRequest,
) -> ApiResponse[OciObjectStorageNamespaceData]:
    storage = _get_upload_storage_settings_state()
    return ApiResponse(
        data=OciObjectStorageNamespaceData(namespace=storage.object_storage_namespace)
    )


@router.post("/settings/oci/key-file", response_model=ApiResponse[OciPrivateKeyUploadData])
async def upload_oci_private_key(
    request: Request,
    _: None = Depends(require_admin),
) -> ApiResponse[OciPrivateKeyUploadData]:
    global _oci_settings_state
    filename = await _uploaded_filename(request)
    if filename and not filename.lower().endswith((".pem", ".key")):
        raise HTTPException(status_code=400, detail=".pem または .key ファイルを選択してください")
    data = _get_oci_settings_state()
    data.key_file_exists = True
    _oci_settings_state = data
    return ApiResponse(data=OciPrivateKeyUploadData(key_file=data.key_file, saved=True))


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
