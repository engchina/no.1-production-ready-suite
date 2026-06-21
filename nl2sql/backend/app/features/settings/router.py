"""RAG 由来のシステム設定 API。

OCI 認証、アップロード保存先、モデル、データベース設定画面が期待する
契約を NL2SQL プロジェクトにも提供する。値は現在の Settings インスタンスへ
反映し、secret 本文はレスポンスに返さない。
"""

from __future__ import annotations

import configparser
import importlib
import io
import json
import re
import shutil
import stat
import time
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal
from uuid import uuid4
from zipfile import BadZipFile, ZipFile

from fastapi import APIRouter, File, HTTPException, UploadFile
from pr_backend_core import ApiResponse
from pydantic import BaseModel, Field

from app.clients.oci_auth import (
    load_oci_config_without_prompt,
    pem_file_is_encrypted,
    resolve_oci_key_file,
)
from app.features.nl2sql.oracle_adapter import OracleNl2SqlAdapter
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
BACKEND_ENV_FILE = Path(__file__).resolve().parents[3] / ".env"
ENV_FILE_MODE = 0o600
ENV_ASSIGNMENT_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")
OCI_DIRECTORY_MODE = 0o700
OCI_CONFIG_MAX_BYTES = 64 * 1024
OCI_CONFIG_FILE_MODE = 0o600
OCI_PRIVATE_KEY_FILE = "~/.oci/oci_api_key.pem"
OCI_PRIVATE_KEY_FILE_MODE = 0o600
OCI_PRIVATE_KEY_MAX_BYTES = 64 * 1024
ORACLE_WALLET_MAX_BYTES = 20 * 1024 * 1024
ORACLE_WALLET_MAX_EXTRACTED_BYTES = 100 * 1024 * 1024
ORACLE_WALLET_REQUIRED_FILES = frozenset(
    {"tnsnames.ora", "sqlnet.ora", "cwallet.sso", "ewallet.pem"}
)
ORACLE_WALLET_SKIPPED_FILES = frozenset(
    {"readme", "keystore.jks", "truststore.jks", "ojdbc.properties", "ewallet.p12"}
)
MODEL_SETTINGS_FILE_MODE = 0o600
OCI_CONFIG_KEYS: tuple[OciConfigField, ...] = (
    "user",
    "fingerprint",
    "tenancy",
    "region",
    "key_file",
)
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
    resolved_payload = _model_settings_with_resolved_secret(settings, payload)
    _persist_model_settings(settings, resolved_payload)
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
    candidate = _database_settings_candidate(settings, payload)
    _persist_database_settings(candidate)
    _apply_database_settings(settings, candidate)
    return ApiResponse(data=_database_settings_data(settings))


@router.post("/database/wallet", response_model=ApiResponse[DatabaseSettingsData])
async def upload_database_wallet(
    file: Annotated[UploadFile, File(...)],
) -> ApiResponse[DatabaseSettingsData]:
    settings = get_settings()
    data = await _read_upload_file(file, ORACLE_WALLET_MAX_BYTES)
    wallet_dir = _install_database_wallet(settings, data, file.filename)
    settings.oracle_wallet_dir = str(wallet_dir)
    return ApiResponse(data=_database_settings_data(settings))


@router.post("/database/test", response_model=ApiResponse[DatabaseConnectionTestResult])
async def test_database_settings(
    payload: DatabaseSettingsUpdate | None = None,
) -> ApiResponse[DatabaseConnectionTestResult]:
    started = time.perf_counter()
    base = get_settings()
    candidate = _database_settings_candidate(base, payload) if payload is not None else base
    readiness = _database_readiness(candidate)
    if readiness != "ok":
        return ApiResponse(
            data=DatabaseConnectionTestResult(
                status="failed",
                readiness=readiness,
                message="Oracle 26ai 接続に必要な設定が不足しています。",
                elapsed_ms=_elapsed_ms(started),
                troubleshooting=["ユーザー、DSN、パスワードまたは Wallet を確認してください。"],
                checked_at=_now(),
                error_type="missing_required_fields",
            )
        )

    ok, message = OracleNl2SqlAdapter(candidate).test_connection()
    return ApiResponse(
        data=DatabaseConnectionTestResult(
            status="success" if ok else "failed",
            readiness=readiness,
            message=message,
            elapsed_ms=_elapsed_ms(started),
            troubleshooting=(
                [] if ok else ["Oracle 接続情報、Wallet、ネットワーク疎通を確認してください。"]
            ),
            details={"network_call": True, "dsn": candidate.oracle_dsn.strip()},
            checked_at=_now(),
            error_type=None if ok else "OracleConnectionError",
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
    _persist_adb_settings(settings)
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
    candidate = _upload_storage_settings_candidate(settings, payload)
    _persist_upload_storage_settings(candidate)
    _apply_upload_storage_settings(settings, candidate)
    return ApiResponse(data=_upload_storage_settings_data(settings))


@router.get("/oci", response_model=ApiResponse[OciSettingsData])
async def get_oci_settings() -> ApiResponse[OciSettingsData]:
    return ApiResponse(data=_oci_settings_data(get_settings()))


@router.patch("/oci", response_model=ApiResponse[OciSettingsData])
async def update_oci_settings(payload: OciSettingsUpdate) -> ApiResponse[OciSettingsData]:
    settings = get_settings()
    settings.oci_config_file = _oci_config_file(settings)
    settings.oci_config_profile = _oci_profile(settings)
    _write_oci_config(settings, payload)
    _persist_oci_settings(settings, payload)
    settings.oci_region = payload.region.strip()
    settings.oci_user_ocid = payload.user.strip()
    settings.oci_fingerprint = payload.fingerprint.strip()
    settings.oci_tenancy_ocid = payload.tenancy.strip()
    return ApiResponse(data=_oci_settings_data(settings))


@router.patch("/oci/object-storage", response_model=ApiResponse[UploadStorageSettingsData])
async def update_oci_object_storage_settings(
    payload: OciObjectStorageSettingsUpdate,
) -> ApiResponse[UploadStorageSettingsData]:
    settings = get_settings()
    settings.object_storage_region = payload.object_storage_region.strip()
    settings.object_storage_namespace = payload.object_storage_namespace.strip()
    _persist_oci_object_storage_settings(settings)
    return ApiResponse(data=_upload_storage_settings_data(settings))


@router.post("/oci/config/read", response_model=ApiResponse[OciConfigReadData])
async def read_oci_config(payload: OciConfigReadRequest) -> ApiResponse[OciConfigReadData]:
    content = _read_oci_config_text(payload.config_file)
    return ApiResponse(data=_parse_oci_config(content, payload.profile))


@router.post("/oci/config/test", response_model=ApiResponse[OciConfigTestResult])
async def test_oci_config() -> ApiResponse[OciConfigTestResult]:
    return ApiResponse(data=_test_oci_config(get_settings()))


@router.post(
    "/oci/object-storage/namespace",
    response_model=ApiResponse[OciObjectStorageNamespaceData],
)
async def read_oci_object_storage_namespace(
    payload: OciObjectStorageNamespaceRequest,
) -> ApiResponse[OciObjectStorageNamespaceData]:
    return ApiResponse(
        data=OciObjectStorageNamespaceData(namespace=_read_object_storage_namespace(payload))
    )


@router.post("/oci/key-file", response_model=ApiResponse[OciPrivateKeyUploadData])
async def upload_oci_private_key(
    file: Annotated[UploadFile, File(...)],
) -> ApiResponse[OciPrivateKeyUploadData]:
    data = await _read_upload_file(
        file,
        OCI_PRIVATE_KEY_MAX_BYTES,
        "秘密鍵 PEM ファイルのサイズが上限を超えています。",
    )
    _install_oci_private_key(data, file.filename)
    return ApiResponse(data=OciPrivateKeyUploadData(key_file=OCI_PRIVATE_KEY_FILE, saved=True))


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


def _model_settings_with_resolved_secret(
    settings: Settings,
    payload: ModelSettingsPayload,
) -> ModelSettingsPayload:
    enterprise = payload.enterprise_ai
    resolved_api_key = _secret_value(
        current=settings.oci_enterprise_ai_api_key,
        update=enterprise.api_key,
        clear=enterprise.clear_api_key,
    )
    resolved_enterprise = enterprise.model_copy(
        update={
            "api_key": resolved_api_key,
            "has_api_key": bool(resolved_api_key.strip()),
            "clear_api_key": False,
        }
    )
    return payload.model_copy(update={"enterprise_ai": resolved_enterprise})


def _persist_model_settings(settings: Settings, payload: ModelSettingsPayload) -> None:
    """モデル設定を RAG と同じ JSON document へ atomic に保存する。"""
    path = _resolve_model_settings_file(settings.model_settings_file)
    document = _model_settings_document(payload)
    tmp_path = path.with_name(f".{path.name}.tmp-{uuid4().hex}")
    try:
        _ensure_model_settings_directory(path.parent)
        tmp_path.write_text(
            json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        tmp_path.chmod(MODEL_SETTINGS_FILE_MODE)
        tmp_path.replace(path)
        path.chmod(MODEL_SETTINGS_FILE_MODE)
    except OSError as exc:
        raise HTTPException(
            status_code=500,
            detail="モデル設定を永続化ファイルへ保存できませんでした。",
        ) from exc
    finally:
        tmp_path.unlink(missing_ok=True)


def _model_settings_document(payload: ModelSettingsPayload) -> dict[str, object]:
    enterprise = payload.enterprise_ai
    generative = payload.generative_ai
    return {
        "version": 1,
        "enterprise_ai": {
            "endpoint": enterprise.endpoint,
            "project_ocid": enterprise.project_ocid,
            "api_key": enterprise.api_key,
            "models": [
                {
                    "model_id": model.model_id,
                    "display_name": model.display_name,
                    "vision_enabled": model.vision_enabled,
                }
                for model in enterprise.models
                if model.model_id
            ],
            "default_model_id": enterprise.default_model_id,
            "api_path": enterprise.api_path,
            "vlm_input_mode": enterprise.vlm_input_mode,
            "text_payload_template": enterprise.text_payload_template,
            "vision_payload_template": enterprise.vision_payload_template,
            "text_response_path": enterprise.text_response_path,
            "vision_response_path": enterprise.vision_response_path,
            "timeout_seconds": enterprise.timeout_seconds,
            "max_retries": enterprise.max_retries,
        },
        "generative_ai": {
            "embedding_model": generative.embedding_model,
            "embedding_dim": generative.embedding_dim,
            "rerank_model": generative.rerank_model,
        },
    }


def _resolve_model_settings_file(path_value: str) -> Path:
    raw_path = path_value.strip() or "model-settings.json"
    path = Path(raw_path).expanduser()
    if path.is_absolute():
        return path
    return BACKEND_ENV_FILE.parent / path


def _ensure_model_settings_directory(path: Path) -> None:
    existed = path.exists()
    path.mkdir(mode=OCI_DIRECTORY_MODE, parents=True, exist_ok=True)
    if not existed:
        path.chmod(OCI_DIRECTORY_MODE)


def _model_settings_data(payload: ModelSettingsPayload) -> ModelSettingsData:
    return ModelSettingsData(
        settings=_public_model_payload(payload),
        checks={
            "enterprise_ai": _enterprise_check(payload.enterprise_ai),
            "generative_ai": _generative_check(payload.generative_ai),
            "embedding_dim": "ok" if payload.generative_ai.embedding_dim == 1536 else "invalid",
        },
        model_settings_file=getattr(get_settings(), "model_settings_file", "model-settings.json"),
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
    wallet_dir = settings.resolved_oracle_wallet_dir
    wallet_path = _expand(wallet_dir) if wallet_dir else None
    if wallet_path is not None:
        _sanitize_database_wallet_dir(wallet_path)
    wallet_exists = bool(wallet_path and wallet_path.is_dir())
    has_password = bool(getattr(settings, "oracle_password", ""))
    return DatabaseSettingsData(
        user=getattr(settings, "oracle_user", ""),
        dsn=getattr(settings, "oracle_dsn", ""),
        wallet_dir=wallet_dir,
        wallet_uploaded=wallet_exists if wallet_uploaded is None else wallet_uploaded,
        available_services=(
            _extract_wallet_services(wallet_path) if wallet_path and wallet_exists else []
        ),
        has_password=has_password,
        has_wallet_password=bool(getattr(settings, "oracle_wallet_password", "")),
        readiness=_database_readiness(settings),
        adb_ocid=getattr(settings, "oracle_adb_ocid", ""),
        region=getattr(settings, "oci_region", ""),
    )


def _sanitize_database_wallet_dir(wallet_path: Path) -> None:
    if not wallet_path.is_dir():
        return
    for file_name in ORACLE_WALLET_SKIPPED_FILES:
        try:
            path = wallet_path / file_name
            if path.is_file():
                path.unlink()
        except OSError:
            continue


def _database_settings_candidate(base: Settings, payload: DatabaseSettingsUpdate) -> Settings:
    updates = {
        "oracle_user": payload.user.strip(),
        "oracle_dsn": payload.dsn.strip(),
        "oracle_wallet_dir": base.resolved_oracle_wallet_dir,
        "oracle_password": _secret_value(
            current=base.oracle_password,
            update=payload.password,
            clear=payload.clear_password,
        ),
        "oracle_wallet_password": _secret_value(
            current=base.oracle_wallet_password,
            update=payload.wallet_password,
            clear=payload.clear_wallet_password,
        ),
    }
    return base.model_copy(update=updates)


def _apply_database_settings(target: Settings, source: Settings) -> None:
    target.oracle_user = source.oracle_user
    target.oracle_password = source.oracle_password
    target.oracle_dsn = source.oracle_dsn
    target.oracle_client_lib_dir = source.oracle_client_lib_dir
    target.oracle_wallet_dir = source.oracle_wallet_dir
    target.oracle_wallet_password = source.oracle_wallet_password


def _persist_database_settings(settings: Settings) -> None:
    values = {
        "ORACLE_USER": settings.oracle_user,
        "ORACLE_PASSWORD": settings.oracle_password,
        "ORACLE_DSN": settings.oracle_dsn,
        "ORACLE_CLIENT_LIB_DIR": settings.oracle_client_lib_dir,
        "ORACLE_WALLET_PASSWORD": settings.oracle_wallet_password,
    }
    if not settings.oracle_client_lib_dir.strip() and settings.oracle_wallet_dir.strip():
        values["ORACLE_WALLET_DIR"] = settings.oracle_wallet_dir
    _write_env_values(
        BACKEND_ENV_FILE,
        values,
        section_comment="# Oracle 26ai",
        error_detail="Oracle 26ai 接続設定を backend/.env へ保存できませんでした。",
    )


def _database_readiness(settings: Settings) -> str:
    if not settings.oracle_user.strip() or not settings.oracle_dsn.strip():
        return "missing"
    if settings.oracle_password.strip():
        return "ok"
    wallet_dir = settings.resolved_oracle_wallet_dir.strip()
    if not wallet_dir:
        return "missing_credentials"
    if not Path(wallet_dir).expanduser().is_dir():
        return "wallet_not_found"
    return "ok"


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


def _persist_adb_settings(settings: Settings) -> None:
    _write_env_values(
        BACKEND_ENV_FILE,
        {
            "ORACLE_ADB_OCID": settings.oracle_adb_ocid,
            "OCI_REGION": settings.oci_region,
        },
        section_comment="# Oracle Autonomous Database 管理",
        error_detail="ADB 設定を backend/.env へ保存できませんでした。",
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


def _upload_storage_settings_candidate(
    base: Settings,
    payload: UploadStorageSettingsUpdate,
) -> Settings:
    updates = {
        "upload_storage_backend": payload.backend,
        "local_storage_dir": payload.local_storage_dir.strip(),
        "object_storage_namespace": (
            payload.object_storage_namespace.strip()
            if payload.object_storage_namespace is not None
            else base.object_storage_namespace
        ),
        "object_storage_bucket": payload.object_storage_bucket.strip(),
    }
    return base.model_copy(update=updates)


def _persist_upload_storage_settings(settings: Settings) -> None:
    values = {
        "UPLOAD_STORAGE_BACKEND": settings.upload_storage_backend,
        "LOCAL_STORAGE_DIR": settings.local_storage_dir,
    }
    if settings.upload_storage_backend == "oci":
        values["OBJECT_STORAGE_REGION"] = settings.object_storage_region
        values["OBJECT_STORAGE_NAMESPACE"] = settings.object_storage_namespace
        values["OBJECT_STORAGE_BUCKET"] = settings.object_storage_bucket
    _write_env_values(
        BACKEND_ENV_FILE,
        values,
        section_comment="# アップロード保存先",
        error_detail="アップロード保存先設定を backend/.env へ保存できませんでした。",
    )


def _apply_upload_storage_settings(target: Settings, source: Settings) -> None:
    target.upload_storage_backend = source.upload_storage_backend
    target.local_storage_dir = source.local_storage_dir
    target.object_storage_region = source.object_storage_region
    target.object_storage_namespace = source.object_storage_namespace
    target.object_storage_bucket = source.object_storage_bucket


def _oci_settings_data(settings: Settings) -> OciSettingsData:
    config_file = _oci_config_file(settings)
    profile = _oci_profile(settings)
    parsed = _read_runtime_oci_config(config_file, profile)
    key_file = OCI_PRIVATE_KEY_FILE
    return OciSettingsData(
        config_file=config_file,
        profile=profile,
        user=parsed.user if parsed is not None else "",
        fingerprint=parsed.fingerprint if parsed is not None else "",
        tenancy=parsed.tenancy if parsed is not None else "",
        region=settings.oci_region.strip() or (parsed.region if parsed is not None else ""),
        key_file=key_file,
        key_file_exists=_expand(key_file).exists(),
        config_file_exists=_expand(config_file).exists(),
    )


def _oci_config_file(settings: Settings) -> str:
    return settings.oci_config_file.strip() or "~/.oci/config"


def _oci_profile(settings: Settings) -> str:
    return (
        getattr(settings, "oci_config_profile", "").strip()
        or getattr(settings, "oci_profile", "").strip()
        or "DEFAULT"
    )


def _read_runtime_oci_config(config_file: str, profile: str) -> OciConfigReadData | None:
    """runtime の OCI config を表示用に読む。読めない場合は画面表示を継続する。"""
    try:
        content = _read_oci_config_text(config_file)
        return _parse_oci_config(content, profile)
    except HTTPException:
        return None


def _expand(path: str) -> Path:
    return Path(path).expanduser()


async def _read_upload_file(
    file: UploadFile,
    max_bytes: int,
    too_large_detail: str = "Wallet ZIP のサイズが上限を超えています。",
) -> bytes:
    """アップロードファイルを上限付きで読み込む。"""
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(status_code=413, detail=too_large_detail)
        chunks.append(chunk)
    return b"".join(chunks)


def _install_oci_private_key(data: bytes, file_name: str | None) -> Path:
    """OCI API 秘密鍵 PEM を固定 path へ上書き保存する。"""
    safe_name = PurePosixPath((file_name or "oci_api_key.pem").replace("\\", "/")).name
    if Path(safe_name).suffix.lower() not in {".pem", ".key"}:
        raise HTTPException(
            status_code=415,
            detail="秘密鍵は .pem または .key ファイルを選択してください。",
        )
    if not data:
        raise HTTPException(status_code=400, detail="空の秘密鍵ファイルはアップロードできません。")
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
    """秘密鍵らしい PEM テキストだけを受け付ける。"""
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


def _install_database_wallet(settings: Settings, data: bytes, file_name: str | None) -> Path:
    """Wallet ZIP を ORACLE_CLIENT_LIB_DIR/network/admin へ展開する。"""
    safe_name = _safe_wallet_filename(file_name)
    if not safe_name.lower().endswith(".zip"):
        raise HTTPException(
            status_code=415,
            detail="Oracle Wallet は ZIP ファイルを選択してください。",
        )
    if not data:
        raise HTTPException(status_code=400, detail="空の Wallet ZIP はアップロードできません。")

    target = _wallet_storage_root(settings)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_dir = target.parent / f".{target.name}.tmp-{uuid4().hex}"
    try:
        wallet_dir = _extract_wallet_zip(data, tmp_dir)
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
        _remove_tmp_wallet_dir(tmp_dir)


def _extract_wallet_zip(data: bytes, target_dir: Path) -> Path:
    """ZIP を検証しながら展開し、config_dir として使うディレクトリを返す。"""
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
                        detail="Wallet ZIP の展開後サイズが上限を超えています。",
                    )
                destination = _wallet_member_destination(target_dir, member.filename)
                if destination.name.lower() in ORACLE_WALLET_SKIPPED_FILES:
                    continue
                if _zip_member_is_symlink(member.external_attr):
                    raise HTTPException(
                        status_code=400,
                        detail="Wallet ZIP にシンボリックリンクは含められません。",
                    )
                destination.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as src, destination.open("wb") as dst:
                    shutil.copyfileobj(src, dst)
                extracted_files.append(destination)
    except BadZipFile as exc:
        raise HTTPException(
            status_code=400,
            detail="Wallet ZIP の形式を確認してください。",
        ) from exc

    wallet_dir = _find_wallet_config_dir(extracted_files)
    if wallet_dir is None:
        required = ", ".join(sorted(ORACLE_WALLET_REQUIRED_FILES))
        raise HTTPException(
            status_code=400,
            detail=f"Wallet ZIP に {required} が含まれているか確認してください。",
        )
    return wallet_dir


def _wallet_member_destination(root: Path, member_name: str) -> Path:
    """Zip Slip を防ぎながら member の展開先を決める。"""
    path = PurePosixPath(member_name.replace("\\", "/"))
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise HTTPException(
            status_code=400,
            detail="Wallet ZIP に安全でないファイルパスが含まれています。",
        )
    destination = (root.joinpath(*path.parts)).resolve()
    resolved_root = root.resolve()
    if resolved_root != destination and resolved_root not in destination.parents:
        raise HTTPException(
            status_code=400,
            detail="Wallet ZIP に安全でないファイルパスが含まれています。",
        )
    return destination


def _find_wallet_config_dir(extracted_files: list[Path]) -> Path | None:
    """tnsnames.ora/sqlnet.ora と認証ファイルが揃うディレクトリを探す。"""
    candidates = {path.parent for path in extracted_files}
    for candidate in sorted(candidates, key=lambda path: len(path.parts)):
        names = {path.name.lower() for path in extracted_files if path.parent == candidate}
        if ORACLE_WALLET_REQUIRED_FILES.issubset(names):
            return candidate
    return None


def _extract_wallet_services(wallet_dir: Path) -> list[str]:
    """tnsnames.ora からトップレベルの TNS alias を抽出する。"""
    tnsnames = wallet_dir / "tnsnames.ora"
    if not tnsnames.is_file():
        return []

    try:
        content = tnsnames.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    reserved_names = {
        "ADDRESS",
        "ADDRESS_LIST",
        "CONNECT_DATA",
        "DESCRIPTION",
        "DESCRIPTION_LIST",
        "HOST",
        "PORT",
        "PROTOCOL",
        "SECURITY",
        "SERVICE_NAME",
        "SSL_SERVER_CERT_DN",
    }
    services: list[str] = []
    seen: set[str] = set()
    for match in re.finditer(r"(?m)^([A-Za-z0-9_.-]+)\s*=", content):
        service = match.group(1)
        normalized = service.upper()
        if normalized in reserved_names or normalized in seen:
            continue
        seen.add(normalized)
        services.append(service)
    return services


def _zip_member_is_symlink(external_attr: int) -> bool:
    """ZIP metadata 上の symlink を拒否する。"""
    mode = external_attr >> 16
    return bool(mode and stat.S_ISLNK(mode))


def _wallet_storage_root(settings: Settings) -> Path:
    """アップロード Wallet の固定保存先。"""
    wallet_dir = settings.resolved_oracle_wallet_dir.strip()
    if not wallet_dir:
        raise HTTPException(
            status_code=422,
            detail="ORACLE_CLIENT_LIB_DIR が未設定のため Wallet 保存先を決定できません。",
        )
    return Path(wallet_dir).expanduser().resolve()


def _safe_wallet_filename(file_name: str | None) -> str:
    """表示名由来の ZIP ファイル名を basename に丸める。"""
    name = PurePosixPath((file_name or "wallet.zip").replace("\\", "/")).name.strip()
    name = re.sub(r"[\x00-\x1f\x7f]+", "_", name).strip(" .")
    return name[:255] if name else "wallet.zip"


def _remove_tmp_wallet_dir(path: Path) -> None:
    """失敗時に今回作成した一時展開先だけを片付ける。"""
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)


def _persist_oci_settings(settings: Settings, payload: OciSettingsUpdate) -> None:
    """OCI 共通設定を backend/.env へ永続化する。"""
    _write_env_values(
        BACKEND_ENV_FILE,
        {
            "OCI_CONFIG_FILE": _oci_config_file(settings),
            "OCI_CONFIG_PROFILE": _oci_profile(settings),
            "OCI_REGION": payload.region.strip(),
        },
        section_comment="# OCI 共通",
        error_detail="OCI 認証設定を backend/.env へ保存できませんでした。",
    )


def _persist_oci_object_storage_settings(settings: Settings) -> None:
    """OCI Object Storage 共通設定を backend/.env へ永続化する。"""
    _write_env_values(
        BACKEND_ENV_FILE,
        {
            "OBJECT_STORAGE_REGION": settings.object_storage_region,
            "OBJECT_STORAGE_NAMESPACE": settings.object_storage_namespace,
        },
        section_comment="# OCI Object Storage",
        error_detail="OCI Object Storage 設定を backend/.env へ保存できませんでした。",
    )


def _write_env_values(
    path: Path,
    values: dict[str, str],
    *,
    section_comment: str,
    error_detail: str,
) -> None:
    """既存 .env のコメントや無関係な値を保ったまま指定 key だけ更新する。"""
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

        content = "\n".join(next_lines).rstrip() + "\n"
        _replace_env_file(path, content)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=error_detail) from exc


def _env_assignment_key(line: str) -> str | None:
    """通常の .env 代入行から key を取り出す。コメント行は対象外。"""
    if line.lstrip().startswith("#"):
        return None
    match = ENV_ASSIGNMENT_RE.match(line)
    return match.group(1) if match else None


def _format_env_value(value: str) -> str:
    """python-dotenv と shell の両方で読みやすい .env value へ整形する。"""
    normalized = value.strip()
    if not normalized:
        return ""
    if re.search(r"[\s#\"']", normalized):
        return '"' + normalized.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return normalized


def _replace_env_file(path: Path, content: str) -> None:
    """同一ディレクトリ内の一時ファイルから atomic replace する。"""
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


def _write_oci_config(settings: Settings, payload: OciSettingsUpdate) -> Path:
    """OCI SDK config を安全な権限で作成または更新する。"""
    target = Path(_oci_config_file(settings)).expanduser()
    profile = _safe_oci_profile_name(_oci_profile(settings))
    parser = _load_oci_config_for_write(target)
    values = {
        "user": payload.user.strip(),
        "fingerprint": payload.fingerprint.strip(),
        "tenancy": payload.tenancy.strip(),
        "region": payload.region.strip(),
        "key_file": OCI_PRIVATE_KEY_FILE,
    }
    _set_oci_config_profile(parser, profile, values)
    _atomic_write_oci_config(target, parser)
    return target


def _safe_oci_profile_name(profile: str) -> str:
    """OCI profile 名を INI section として安全な文字列へ制限する。"""
    selected = profile.strip() or "DEFAULT"
    if any(char in selected for char in "[]\r\n"):
        raise HTTPException(status_code=422, detail="プロファイル名に [ ] や改行は使用できません。")
    return selected


def _load_oci_config_for_write(path: Path) -> configparser.ConfigParser:
    """既存 config があれば読み、なければ空の parser を返す。"""
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
    """DEFAULT または指定 profile に OCI SDK 必須値を設定する。"""
    if profile.upper() == "DEFAULT":
        for key, value in values.items():
            parser["DEFAULT"][key] = value
        return
    if not parser.has_section(profile):
        parser.add_section(profile)
    for key, value in values.items():
        parser[profile][key] = value


def _atomic_write_oci_config(path: Path, parser: configparser.ConfigParser) -> None:
    """config を一時ファイル経由で保存し、ディレクトリ/ファイル権限を補正する。"""
    tmp_path = path.with_name(f".{path.name}.tmp-{uuid4().hex}")
    try:
        _ensure_private_directory(path.parent)
        buffer = io.StringIO()
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
    """OCI credential directory を作成し、所有者だけが入れる権限に補正する。"""
    path.mkdir(mode=OCI_DIRECTORY_MODE, parents=True, exist_ok=True)
    path.chmod(OCI_DIRECTORY_MODE)


def _test_oci_config(settings: Settings) -> OciConfigTestResult:
    """保存済み OCI config の構造、秘密鍵の存在、権限を確認する。"""
    config_file = _oci_config_file(settings)
    config_path = Path(config_file).expanduser()
    profile = _safe_oci_profile_name(_oci_profile(settings))
    key_path = Path(OCI_PRIVATE_KEY_FILE).expanduser()
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
            key_file_exists=key_path.is_file(),
            message=str(exc.detail),
            checked_at=_now(),
            error_type="HTTPException",
            oci_directory_mode=_mode_string(config_path.parent),
            config_file_mode=_mode_string(config_path),
            key_file_mode=_mode_string(key_path),
        )

    parsed_values = {
        "user": parsed.user,
        "fingerprint": parsed.fingerprint,
        "tenancy": parsed.tenancy,
        "region": parsed.region,
        "key_file": parsed.key_file,
    }
    missing_fields: list[OciConfigField] = [
        field for field in OCI_CONFIG_KEYS if not parsed_values[field].strip()
    ]
    key_path = resolve_oci_key_file(parsed.key_file or OCI_PRIVATE_KEY_FILE, config_path)
    key_file_exists = key_path.is_file()
    permission_issues = _oci_permission_issues(config_path, key_path)
    pass_phrase_required = (
        key_file_exists
        and pem_file_is_encrypted(key_path)
        and not _oci_config_has_private_key_pass_phrase(content, profile)
    )
    can_use_config = (
        not missing_fields
        and key_file_exists
        and not permission_issues
        and not pass_phrase_required
    )
    status: OciConfigTestStatus = "success" if can_use_config else "failed"

    if missing_fields:
        message = "OCI config の必須項目が不足しています。"
    elif not key_file_exists:
        message = "OCI config の key_file が指す秘密鍵ファイルが見つかりません。"
    elif pass_phrase_required:
        message = (
            "OCI API 秘密鍵 PEM が暗号化されています。"
            "pass_phrase を OCI config に設定するか、"
            "パスフレーズなしの秘密鍵 PEM を使用してください。"
        )
    elif permission_issues:
        message = "OCI 認証ファイルの権限を確認してください。"
    else:
        message = "OCI config と秘密鍵ファイルを確認できました。"

    return OciConfigTestResult(
        status=status,
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
        checked_at=_now(),
        error_type="OciPrivateKeyPassPhraseRequiredError" if pass_phrase_required else None,
    )


def _oci_permission_issues(config_path: Path, key_path: Path) -> list[str]:
    """OCI credential path の group/other 権限露出を検出する。"""
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
    """path の permission mode を 4 桁 8 進数で返す。"""
    mode = _path_mode(path)
    return f"{mode:04o}" if mode is not None else None


def _oci_config_has_private_key_pass_phrase(content: str, profile: str) -> bool:
    """OCI config profile に private key pass phrase があるか確認する。"""
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
    return any(
        str(entries.get(key, "")).strip() for key in ("pass_phrase", "passphrase", "key_password")
    )


def _path_mode(path: Path) -> int | None:
    """存在しない path の mode 取得失敗を通常値として扱う。"""
    try:
        return stat.S_IMODE(path.stat().st_mode)
    except OSError:
        return None


def _secret_value(*, current: str, update: str | None, clear: bool) -> str:
    """secret の保持・更新・削除を判定する。"""
    if clear:
        return ""
    if update is not None and update != "":
        return update
    return current


def _read_oci_config_text(config_file: str) -> str:
    """OCI config file を安全な上限付きで読み込む。"""
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
    """OCI config の profile から UI に反映する値だけを抽出する。"""
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


def _elapsed_ms(started: float) -> int:
    return max(1, int((time.perf_counter() - started) * 1000))


def _read_object_storage_namespace(payload: OciObjectStorageNamespaceRequest) -> str:
    """OCI SDK で Object Storage namespace を取得する。"""
    try:
        oci_config = importlib.import_module("oci.config")
        object_storage = importlib.import_module("oci.object_storage")
        config = load_oci_config_without_prompt(
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


def _now() -> str:
    return datetime.now(UTC).isoformat()
