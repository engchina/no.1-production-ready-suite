"""RAG 由来のシステム設定 API。

OCI 認証、アップロード保存先、モデル、データベース設定画面が期待する
契約を NL2SQL プロジェクトにも提供する。値は現在の Settings インスタンスへ
反映し、secret 本文はレスポンスに返さない。
"""

from __future__ import annotations

import configparser
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Request
from pr_backend_core import ApiResponse
from pydantic import BaseModel, Field

from app.settings import Settings, get_settings

router = APIRouter(prefix="/settings", tags=["settings"])

ModelSettingsCheckStatus = Literal["ok", "missing", "invalid"]
ModelSettingsTestStatus = Literal["success", "failed"]
ModelSettingsTestTargetType = Literal[
    "enterprise_text",
    "enterprise_vision",
    "embedding",
    "rerank",
]
UploadStorageBackend = Literal["local", "oci"]
DatabaseConnectionTestStatus = Literal["success", "failed", "skipped"]
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


class EnterpriseAiConfiguredModel(BaseModel):
    model_id: str = ""
    display_name: str = ""
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
    vlm_input_mode: Literal["auto", "files_api", "inline_image"] = "auto"
    text_payload_template: str = ""
    vision_payload_template: str = ""
    text_response_path: str = ""
    vision_response_path: str = ""
    timeout_seconds: float = 600.0
    max_retries: int = 2


class GenerativeAiModelSettings(BaseModel):
    embedding_model: str = "cohere.embed-v4.0"
    embedding_dim: int = 1536
    rerank_model: str = "cohere.rerank-v4.0-fast"


class ModelSettingsPayload(BaseModel):
    enterprise_ai: EnterpriseAiModelSettings
    generative_ai: GenerativeAiModelSettings


class ModelSettingsData(BaseModel):
    settings: ModelSettingsPayload
    checks: dict[str, ModelSettingsCheckStatus]
    model_settings_file: str = "runtime-settings"
    source: Literal["runtime"] = "runtime"


class ModelSettingsTestRequest(BaseModel):
    settings: ModelSettingsPayload
    target_type: ModelSettingsTestTargetType
    model_id: str = ""
    vision_enabled: bool = False


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
    details: dict[str, str | int | float | bool | None] = Field(default_factory=dict)


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
    config_source: Literal["runtime"] = "runtime"


class DatabaseSettingsUpdate(BaseModel):
    user: str = ""
    dsn: str = ""
    wallet_dir: str = ""
    password: str | None = None
    wallet_password: str | None = None
    clear_password: bool = False
    clear_wallet_password: bool = False


class DatabaseConnectionTestResult(BaseModel):
    status: DatabaseConnectionTestStatus
    readiness: str
    message: str
    elapsed_ms: int
    troubleshooting: list[str] = Field(default_factory=list)
    details: dict[str, str | int | float | bool | None] = Field(default_factory=dict)
    checked_at: str
    error_type: str | None = None


class AdbInfoData(BaseModel):
    status: AdbOperationStatus
    message: str
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
    backend: UploadStorageBackend
    local_storage_dir: str = ""
    object_storage_region: str = ""
    object_storage_namespace: str = ""
    object_storage_bucket: str = ""
    readiness: str = "missing"
    max_upload_bytes: int = 104857600
    config_source: Literal["runtime"] = "runtime"


class UploadStorageSettingsUpdate(BaseModel):
    backend: UploadStorageBackend = "local"
    local_storage_dir: str = ""
    object_storage_namespace: str | None = None
    object_storage_bucket: str = ""


class OciConfigReadRequest(BaseModel):
    config_file: str = "~/.oci/config"
    profile: str = "DEFAULT"


class OciConfigReadData(BaseModel):
    profile: str
    user: str = ""
    fingerprint: str = ""
    tenancy: str = ""
    region: str = ""
    key_file: str = ""
    applied_fields: list[OciConfigField] = Field(default_factory=list)


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
    config_source: Literal["runtime"] = "runtime"


class OciObjectStorageSettingsUpdate(BaseModel):
    object_storage_region: str = ""
    object_storage_namespace: str = ""


class OciConfigTestResult(BaseModel):
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
    checked_at: str
    error_type: str | None = None


class OciObjectStorageNamespaceRequest(BaseModel):
    config_file: str = "~/.oci/config"
    profile: str = "DEFAULT"
    region: str = ""


class OciObjectStorageNamespaceData(BaseModel):
    namespace: str = ""


class OciPrivateKeyUploadData(BaseModel):
    key_file: str = "~/.oci/oci_api_key.pem"
    saved: bool = False


@router.get("/model", response_model=ApiResponse[ModelSettingsData])
async def get_model_settings() -> ApiResponse[ModelSettingsData]:
    settings = get_settings()
    payload = _model_payload(settings)
    return ApiResponse(data=_model_settings_data(payload))


@router.patch("/model", response_model=ApiResponse[ModelSettingsData])
async def update_model_settings(payload: ModelSettingsPayload) -> ApiResponse[ModelSettingsData]:
    settings = get_settings()
    _apply_model_settings(settings, payload)
    return ApiResponse(data=_model_settings_data(_model_payload(settings)))


@router.post("/model/check", response_model=ApiResponse[ModelSettingsData])
async def check_model_settings(payload: ModelSettingsPayload) -> ApiResponse[ModelSettingsData]:
    return ApiResponse(data=_model_settings_data(payload))


@router.post("/model/test", response_model=ApiResponse[ModelSettingsTestResult])
async def test_model_settings(
    request: ModelSettingsTestRequest,
) -> ApiResponse[ModelSettingsTestResult]:
    started = time.perf_counter()
    configured, message, troubleshooting = _model_target_configured(request)
    return ApiResponse(
        data=ModelSettingsTestResult(
            status="success" if configured else "failed",
            target_type=request.target_type,
            model_id=request.model_id,
            message=message,
            troubleshooting=troubleshooting,
            error_type=None if configured else "not_configured",
            elapsed_ms=max(1, int((time.perf_counter() - started) * 1000)),
            checked_at=_now(),
            details={
                "network_call": False,
                "reason": "configuration_check_only",
            },
        )
    )


@router.get("/database", response_model=ApiResponse[DatabaseSettingsData])
async def get_database_settings() -> ApiResponse[DatabaseSettingsData]:
    return ApiResponse(data=_database_settings_data(get_settings()))


@router.patch("/database", response_model=ApiResponse[DatabaseSettingsData])
async def update_database_settings(
    payload: DatabaseSettingsUpdate,
) -> ApiResponse[DatabaseSettingsData]:
    settings = get_settings()
    settings.oracle_user = payload.user.strip()
    settings.oracle_dsn = payload.dsn.strip()
    settings.oracle_client_lib_dir = payload.wallet_dir.strip()
    if payload.clear_password:
        settings.oracle_password = ""
    elif payload.password is not None and payload.password.strip():
        settings.oracle_password = payload.password
    if payload.clear_wallet_password:
        settings.oracle_wallet_password = ""
    elif payload.wallet_password is not None and payload.wallet_password.strip():
        settings.oracle_wallet_password = payload.wallet_password
    return ApiResponse(data=_database_settings_data(settings))


@router.post("/database/wallet", response_model=ApiResponse[DatabaseSettingsData])
async def upload_database_wallet(request: Request) -> ApiResponse[DatabaseSettingsData]:
    await request.body()
    return ApiResponse(data=_database_settings_data(get_settings(), wallet_uploaded=True))


@router.post("/database/test", response_model=ApiResponse[DatabaseConnectionTestResult])
async def test_database_settings(
    payload: DatabaseSettingsUpdate,
) -> ApiResponse[DatabaseConnectionTestResult]:
    started = time.perf_counter()
    missing = []
    if not payload.user.strip():
        missing.append("ORACLE_USER")
    if not payload.dsn.strip():
        missing.append("ORACLE_DSN")
    status: DatabaseConnectionTestStatus = "failed" if missing else "skipped"
    message = (
        f"未設定項目があります: {', '.join(missing)}"
        if missing
        else "この lightweight settings API は実 DB 接続を行わず、入力値の形式のみ確認します。"
    )
    return ApiResponse(
        data=DatabaseConnectionTestResult(
            status=status,
            readiness="missing" if missing else "ok",
            message=message,
            elapsed_ms=max(1, int((time.perf_counter() - started) * 1000)),
            troubleshooting=(
                [] if not missing else ["データベースユーザーと DSN を入力してください。"]
            ),
            details={"network_call": False, "dsn": payload.dsn.strip()},
            checked_at=_now(),
            error_type="missing_required_fields" if missing else None,
        )
    )


@router.get("/database/adb", response_model=ApiResponse[AdbInfoData])
async def get_adb_info() -> ApiResponse[AdbInfoData]:
    return ApiResponse(data=_adb_info(get_settings()))


@router.post("/database/adb/settings", response_model=ApiResponse[AdbInfoData])
async def update_adb_settings(payload: AdbSettingsUpdate) -> ApiResponse[AdbInfoData]:
    settings = get_settings()
    settings.oracle_adb_ocid = payload.adb_ocid.strip()
    settings.oci_region = payload.region.strip() or settings.oci_region
    return ApiResponse(data=_adb_info(settings))


@router.post("/database/adb/start", response_model=ApiResponse[AdbInfoData])
async def start_adb() -> ApiResponse[AdbInfoData]:
    settings = get_settings()
    if not settings.oracle_adb_ocid.strip():
        return ApiResponse(data=_adb_info(settings, status="not_configured"))
    return ApiResponse(
        data=_adb_info(settings, status="accepted", message="起動リクエストを受け付けました。")
    )


@router.post("/database/adb/stop", response_model=ApiResponse[AdbInfoData])
async def stop_adb() -> ApiResponse[AdbInfoData]:
    settings = get_settings()
    if not settings.oracle_adb_ocid.strip():
        return ApiResponse(data=_adb_info(settings, status="not_configured"))
    return ApiResponse(
        data=_adb_info(settings, status="accepted", message="停止リクエストを受け付けました。")
    )


@router.get("/upload-storage", response_model=ApiResponse[UploadStorageSettingsData])
async def get_upload_storage_settings() -> ApiResponse[UploadStorageSettingsData]:
    return ApiResponse(data=_upload_storage_settings_data(get_settings()))


@router.patch("/upload-storage", response_model=ApiResponse[UploadStorageSettingsData])
async def update_upload_storage_settings(
    payload: UploadStorageSettingsUpdate,
) -> ApiResponse[UploadStorageSettingsData]:
    settings = get_settings()
    settings.upload_storage_backend = payload.backend
    settings.local_storage_dir = payload.local_storage_dir.strip()
    settings.object_storage_bucket = payload.object_storage_bucket.strip()
    if payload.object_storage_namespace is not None:
        settings.object_storage_namespace = payload.object_storage_namespace.strip()
    return ApiResponse(data=_upload_storage_settings_data(settings))


@router.get("/oci", response_model=ApiResponse[OciSettingsData])
async def get_oci_settings() -> ApiResponse[OciSettingsData]:
    return ApiResponse(data=_oci_settings_data(get_settings()))


@router.patch("/oci", response_model=ApiResponse[OciSettingsData])
async def update_oci_settings(payload: OciSettingsUpdate) -> ApiResponse[OciSettingsData]:
    settings = get_settings()
    settings.oci_user_ocid = payload.user.strip()
    settings.oci_fingerprint = payload.fingerprint.strip()
    settings.oci_tenancy_ocid = payload.tenancy.strip()
    settings.oci_region = payload.region.strip()
    return ApiResponse(data=_oci_settings_data(settings))


@router.patch("/oci/object-storage", response_model=ApiResponse[UploadStorageSettingsData])
async def update_oci_object_storage_settings(
    payload: OciObjectStorageSettingsUpdate,
) -> ApiResponse[UploadStorageSettingsData]:
    settings = get_settings()
    settings.object_storage_region = payload.object_storage_region.strip()
    settings.object_storage_namespace = payload.object_storage_namespace.strip()
    return ApiResponse(data=_upload_storage_settings_data(settings))


@router.post("/oci/config/read", response_model=ApiResponse[OciConfigReadData])
async def read_oci_config(payload: OciConfigReadRequest) -> ApiResponse[OciConfigReadData]:
    return ApiResponse(data=_read_oci_config(payload.config_file, payload.profile))


@router.post("/oci/config/test", response_model=ApiResponse[OciConfigTestResult])
async def test_oci_config() -> ApiResponse[OciConfigTestResult]:
    settings = get_settings()
    data = _oci_settings_data(settings)
    missing: list[OciConfigField] = []
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
                "OCI config を確認しました。"
                if not missing
                else "OCI config に不足項目があります。"
            ),
            checked_at=_now(),
            error_type=None if not missing else "missing_required_fields",
        )
    )


@router.post(
    "/oci/object-storage/namespace",
    response_model=ApiResponse[OciObjectStorageNamespaceData],
)
async def read_oci_object_storage_namespace(
    _payload: OciObjectStorageNamespaceRequest,
) -> ApiResponse[OciObjectStorageNamespaceData]:
    return ApiResponse(
        data=OciObjectStorageNamespaceData(
            namespace=get_settings().object_storage_namespace
        )
    )


@router.post("/oci/key-file", response_model=ApiResponse[OciPrivateKeyUploadData])
async def upload_oci_private_key(request: Request) -> ApiResponse[OciPrivateKeyUploadData]:
    content = await request.body()
    file_content = _extract_uploaded_file_body(
        content,
        request.headers.get("content-type", ""),
    )
    key_file = _expand(get_settings().oci_key_file or "~/.oci/oci_api_key.pem")
    saved = False
    if file_content:
        key_file.parent.mkdir(parents=True, exist_ok=True)
        key_file.write_bytes(file_content)
        saved = True
    return ApiResponse(data=OciPrivateKeyUploadData(key_file="~/.oci/oci_api_key.pem", saved=saved))


def _model_payload(settings: Settings) -> ModelSettingsPayload:
    llm_model = getattr(settings, "oci_enterprise_ai_llm_model", "") or getattr(
        settings, "oci_enterprise_ai_default_model", ""
    )
    vision_model = getattr(settings, "oci_enterprise_ai_vlm_model", "")
    models = []
    if llm_model:
        models.append(
            EnterpriseAiConfiguredModel(
                model_id=llm_model,
                display_name="回答生成",
                vision_enabled=False,
            )
        )
    if vision_model and vision_model != llm_model:
        models.append(
            EnterpriseAiConfiguredModel(
                model_id=vision_model,
                display_name="Vision / OCR",
                vision_enabled=True,
            )
        )
    if not models:
        models.append(
            EnterpriseAiConfiguredModel(model_id="", display_name="", vision_enabled=False)
        )
    return ModelSettingsPayload(
        enterprise_ai=EnterpriseAiModelSettings(
            endpoint=getattr(settings, "oci_enterprise_ai_endpoint", ""),
            project_ocid=getattr(settings, "oci_enterprise_ai_project_ocid", ""),
            api_key="",
            has_api_key=bool(getattr(settings, "oci_enterprise_ai_api_key", "")),
            models=models,
            default_model_id=getattr(settings, "oci_enterprise_ai_default_model", "") or llm_model,
            api_path=getattr(settings, "oci_enterprise_ai_llm_path", "") or "/responses",
            vlm_input_mode=getattr(settings, "oci_enterprise_ai_vlm_input_mode", "auto"),
            text_payload_template=getattr(settings, "oci_enterprise_ai_llm_payload_template", ""),
            vision_payload_template=getattr(settings, "oci_enterprise_ai_vlm_payload_template", ""),
            text_response_path=getattr(settings, "oci_enterprise_ai_llm_response_path", ""),
            vision_response_path=getattr(settings, "oci_enterprise_ai_vlm_response_path", ""),
            timeout_seconds=getattr(settings, "oci_enterprise_ai_timeout_seconds", 600.0),
            max_retries=getattr(settings, "oci_enterprise_ai_max_retries", 2),
        ),
        generative_ai=GenerativeAiModelSettings(
            embedding_model=getattr(settings, "oci_genai_embed_model_id", "cohere.embed-v4.0"),
            embedding_dim=1536,
            rerank_model=getattr(settings, "oci_genai_rerank_model_id", "cohere.rerank-v4.0-fast"),
        ),
    )


def _apply_model_settings(settings: Settings, payload: ModelSettingsPayload) -> None:
    enterprise = payload.enterprise_ai
    generative = payload.generative_ai
    settings.oci_enterprise_ai_endpoint = enterprise.endpoint.strip()
    settings.oci_enterprise_ai_project_ocid = enterprise.project_ocid.strip()
    if enterprise.clear_api_key:
        settings.oci_enterprise_ai_api_key = ""
    elif enterprise.api_key.strip():
        settings.oci_enterprise_ai_api_key = enterprise.api_key
    settings.oci_enterprise_ai_default_model = enterprise.default_model_id.strip()
    settings.oci_enterprise_ai_llm_model = (
        next(
            (
                model.model_id.strip()
                for model in enterprise.models
                if not model.vision_enabled and model.model_id.strip()
            ),
            "",
        )
        or enterprise.default_model_id.strip()
    )
    settings.oci_enterprise_ai_vlm_model = next(
        (
            model.model_id.strip()
            for model in enterprise.models
            if model.vision_enabled and model.model_id.strip()
        ),
        "",
    )
    settings.oci_enterprise_ai_llm_path = enterprise.api_path.strip() or "/responses"
    settings.oci_enterprise_ai_vlm_input_mode = enterprise.vlm_input_mode
    settings.oci_enterprise_ai_llm_payload_template = enterprise.text_payload_template
    settings.oci_enterprise_ai_vlm_payload_template = enterprise.vision_payload_template
    settings.oci_enterprise_ai_llm_response_path = enterprise.text_response_path
    settings.oci_enterprise_ai_vlm_response_path = enterprise.vision_response_path
    settings.oci_enterprise_ai_timeout_seconds = enterprise.timeout_seconds
    settings.oci_enterprise_ai_max_retries = enterprise.max_retries
    settings.oci_genai_embed_model_id = generative.embedding_model.strip()
    settings.oci_genai_rerank_model_id = generative.rerank_model.strip()


def _model_settings_data(payload: ModelSettingsPayload) -> ModelSettingsData:
    return ModelSettingsData(
        settings=_public_model_payload(payload),
        checks={
            "enterprise_ai": _enterprise_check(payload.enterprise_ai),
            "generative_ai": _generative_check(payload.generative_ai),
            "embedding_dim": "ok" if payload.generative_ai.embedding_dim == 1536 else "invalid",
        },
    )


def _public_model_payload(payload: ModelSettingsPayload) -> ModelSettingsPayload:
    public_payload = payload.model_copy(deep=True)
    public_payload.enterprise_ai.api_key = ""
    return public_payload


def _enterprise_check(settings: EnterpriseAiModelSettings) -> ModelSettingsCheckStatus:
    has_model = any(model.model_id.strip() for model in settings.models)
    if (
        settings.endpoint.strip()
        and has_model
        and (settings.has_api_key or settings.api_key.strip())
    ):
        return "ok"
    return "missing"


def _generative_check(settings: GenerativeAiModelSettings) -> ModelSettingsCheckStatus:
    return "ok" if settings.embedding_model.strip() and settings.rerank_model.strip() else "missing"


def _model_target_configured(
    request: ModelSettingsTestRequest,
) -> tuple[bool, str, list[str]]:
    if request.target_type in {"embedding", "rerank"}:
        model_id = request.model_id.strip()
        return (
            bool(model_id),
            f"{model_id} の設定を確認しました。" if model_id else "モデル ID が未設定です。",
            [] if model_id else ["OCI Generative AI のモデル ID を設定してください。"],
        )
    enterprise = request.settings.enterprise_ai
    configured = bool(
        enterprise.endpoint.strip()
        and request.model_id.strip()
        and (enterprise.has_api_key or enterprise.api_key.strip())
    )
    return (
        configured,
        (
            f"{request.model_id} の設定を確認しました。"
            if configured
            else "Enterprise AI の接続情報が不足しています。"
        ),
        [] if configured else ["Endpoint、API key、モデル ID を設定してください。"],
    )


def _database_settings_data(
    settings: Settings,
    wallet_uploaded: bool | None = None,
) -> DatabaseSettingsData:
    wallet_dir = getattr(settings, "oracle_client_lib_dir", "")
    wallet_path = _expand(wallet_dir) if wallet_dir else None
    wallet_exists = bool(wallet_path and wallet_path.exists())
    has_password = bool(getattr(settings, "oracle_password", ""))
    readiness = (
        "ok"
        if getattr(settings, "oracle_user", "") and getattr(settings, "oracle_dsn", "")
        else "missing"
    )
    if not has_password and not wallet_exists and readiness == "ok":
        readiness = "missing_credentials"
    return DatabaseSettingsData(
        user=getattr(settings, "oracle_user", ""),
        dsn=getattr(settings, "oracle_dsn", ""),
        wallet_dir=wallet_dir,
        wallet_uploaded=wallet_exists if wallet_uploaded is None else wallet_uploaded,
        available_services=[],
        has_password=has_password,
        has_wallet_password=bool(getattr(settings, "oracle_wallet_password", "")),
        readiness=readiness,
        adb_ocid=getattr(settings, "oracle_adb_ocid", ""),
        region=getattr(settings, "oci_region", ""),
    )


def _adb_info(
    settings: Settings,
    status: AdbOperationStatus | None = None,
    message: str | None = None,
) -> AdbInfoData:
    adb_ocid = getattr(settings, "oracle_adb_ocid", "").strip()
    resolved_status: AdbOperationStatus = status or ("success" if adb_ocid else "not_configured")
    return AdbInfoData(
        status=resolved_status,
        message=message
        or ("ADB OCID が設定されています。" if adb_ocid else "ADB OCID が設定されていません。"),
        id=adb_ocid or None,
        region=getattr(settings, "oci_region", "") or None,
    )


def _upload_storage_settings_data(settings: Settings) -> UploadStorageSettingsData:
    backend = getattr(settings, "upload_storage_backend", "local")
    if backend not in {"local", "oci"}:
        backend = "local"
    namespace = getattr(settings, "object_storage_namespace", "")
    bucket = getattr(settings, "object_storage_bucket", "")
    local_dir = getattr(settings, "local_storage_dir", "")
    readiness = (
        "ok"
        if (backend == "local" and local_dir)
        or (backend == "oci" and namespace and bucket)
        else "missing"
    )
    return UploadStorageSettingsData(
        backend=backend,
        local_storage_dir=local_dir,
        object_storage_region=getattr(settings, "object_storage_region", "")
        or getattr(settings, "oci_region", ""),
        object_storage_namespace=namespace,
        object_storage_bucket=bucket,
        readiness=readiness,
        max_upload_bytes=getattr(settings, "max_upload_bytes", 104857600),
    )


def _oci_settings_data(settings: Settings) -> OciSettingsData:
    config_file = getattr(settings, "oci_config_file", "") or "~/.oci/config"
    key_file = getattr(settings, "oci_key_file", "") or "~/.oci/oci_api_key.pem"
    return OciSettingsData(
        config_file="~/.oci/config",
        profile=getattr(settings, "oci_profile", "") or "DEFAULT",
        user=getattr(settings, "oci_user_ocid", ""),
        fingerprint=getattr(settings, "oci_fingerprint", ""),
        tenancy=getattr(settings, "oci_tenancy_ocid", ""),
        region=getattr(settings, "oci_region", ""),
        key_file="~/.oci/oci_api_key.pem",
        key_file_exists=_expand(key_file).exists(),
        config_file_exists=_expand(config_file).exists(),
    )


def _read_oci_config(config_file: str, profile: str) -> OciConfigReadData:
    path = _expand(config_file or "~/.oci/config")
    parser = configparser.ConfigParser()
    if path.exists():
        parser.read(path)
    section = profile or "DEFAULT"
    values: Mapping[str, str] = (
        parser[section] if parser.has_section(section) or section == "DEFAULT" else {}
    )
    if section == "DEFAULT" and parser.defaults():
        values = parser.defaults()
    fields: list[OciConfigField] = []
    result = OciConfigReadData(profile=section)
    for config_key, field_name in [
        ("user", "user"),
        ("fingerprint", "fingerprint"),
        ("tenancy", "tenancy"),
        ("region", "region"),
        ("key_file", "key_file"),
    ]:
        value = values.get(config_key, "") if hasattr(values, "get") else ""
        if value:
            setattr(result, field_name, value)
            fields.append(config_key)  # type: ignore[arg-type]
    result.applied_fields = fields
    return result


def _expand(path: str) -> Path:
    return Path(path).expanduser()


def _extract_uploaded_file_body(content: bytes, content_type: str) -> bytes:
    if not content:
        return b""
    marker = "boundary="
    if "multipart/form-data" not in content_type or marker not in content_type:
        return content

    boundary = content_type.split(marker, 1)[1].split(";", 1)[0].strip().strip('"')
    if not boundary:
        return b""
    delimiter = f"--{boundary}".encode()
    for part in content.split(delimiter):
        if b'name="file"' not in part:
            continue
        header, separator, body = part.partition(b"\r\n\r\n")
        if not separator or b"Content-Disposition:" not in header:
            continue
        return body.removesuffix(b"\r\n").removesuffix(b"--").removesuffix(b"\r\n")
    return b""


def _now() -> str:
    return datetime.now(UTC).isoformat()
