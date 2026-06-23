"""RAG 由来のシステム設定 API。

OCI 認証、アップロード保存先、モデル、データベース設定画面が期待する
契約を NL2SQL プロジェクトにも提供する。値は現在の Settings インスタンスへ
反映し、secret 本文はレスポンスに返さない。
"""

import configparser
import importlib
import io
import json
import re
import shutil
import stat
import time
from base64 import b64decode
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal
from uuid import uuid4
from zipfile import BadZipFile, ZipFile

from fastapi import APIRouter, File, HTTPException, UploadFile
from pr_backend_core import ApiResponse

from app.clients.oci_auth import (
    load_oci_config_without_prompt,
    pem_file_is_encrypted,
    resolve_oci_key_file,
)
from app.clients.oci_database import AutonomousDatabaseInfo, OciDatabaseClient
from app.clients.oci_enterprise_ai import OciEnterpriseAiClient
from app.clients.oci_genai import OciGenAiClient
from app.clients.oracle import close_oracle_pool, test_oracle_connection
from app.schemas.settings import (
    AdbInfoData,
    AdbOperationStatus,
    AdbSettingsUpdate,
    DatabaseConnectionTestResult,
    DatabaseSettingsData,
    DatabaseSettingsUpdate,
    EnterpriseAiModelEntrySettings,
    EnterpriseAiModelSettings,
    GenerativeAiModelSettings,
    ModelSettingsCheckStatus,
    ModelSettingsData,
    ModelSettingsPayload,
    ModelSettingsTestRequest,
    ModelSettingsTestResult,
    ModelSettingsTestTargetType,
    OciConfigField,
    OciConfigReadData,
    OciConfigReadRequest,
    OciConfigTestResult,
    OciConfigTestStatus,
    OciObjectStorageNamespaceData,
    OciObjectStorageNamespaceRequest,
    OciObjectStorageSettingsUpdate,
    OciPrivateKeyUploadData,
    OciSettingsData,
    OciSettingsUpdate,
    UploadStorageSettingsData,
    UploadStorageSettingsUpdate,
)
from app.settings import (
    EnterpriseAiConfiguredModel as SettingsEnterpriseAiConfiguredModel,
)
from app.settings import (
    Settings,
    enterprise_ai_default_model_id,
    enterprise_ai_model_catalog,
    get_settings,
    resolve_model_settings_file,
)

router = APIRouter(prefix="/settings", tags=["settings"])
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
ORACLE_ERROR_CODE_RE = re.compile(r"\b(?:ORA|DPY|DPI)-\d{4,5}\b", re.IGNORECASE)
OCI_CONFIG_KEYS: tuple[OciConfigField, ...] = (
    "user",
    "fingerprint",
    "tenancy",
    "region",
    "key_file",
)
MODEL_TEST_IMAGE_BYTES = b64decode(
    "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"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAD//2Q=="
)


@router.get("/model", response_model=ApiResponse[ModelSettingsData])
async def get_model_settings() -> ApiResponse[ModelSettingsData]:
    settings = get_settings()
    payload = _model_payload(settings)
    return ApiResponse(data=_model_settings_data(payload, settings))


@router.patch("/model", response_model=ApiResponse[ModelSettingsData])
async def update_model_settings(payload: ModelSettingsPayload) -> ApiResponse[ModelSettingsData]:
    settings = get_settings()
    resolved_payload = _model_settings_with_resolved_secret(settings, payload)
    _persist_model_settings(settings, resolved_payload)
    _apply_model_settings(settings, payload)
    return ApiResponse(data=_model_settings_data(_model_payload(settings), settings))


@router.post("/model/check", response_model=ApiResponse[ModelSettingsData])
async def check_model_settings(payload: ModelSettingsPayload) -> ApiResponse[ModelSettingsData]:
    return ApiResponse(data=_model_settings_data(payload, get_settings()))


@router.post("/model/test", response_model=ApiResponse[ModelSettingsTestResult])
async def test_model_settings(
    request: ModelSettingsTestRequest,
) -> ApiResponse[ModelSettingsTestResult]:
    started = time.perf_counter()
    settings = get_settings()
    candidate = _model_test_candidate_settings(settings, request)
    try:
        details = await _run_model_settings_test(candidate, request)
    except Exception as exc:
        return ApiResponse(
            data=_failed_model_test_result(
                request,
                exc,
                elapsed_ms=_elapsed_ms(started),
                secrets=[candidate.oci_enterprise_ai_api_key],
            )
        )
    return ApiResponse(
        data=_successful_model_test_result(
            request,
            details=details,
            elapsed_ms=_elapsed_ms(started),
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
    close_oracle_pool()
    return ApiResponse(data=_database_settings_data(settings))


@router.post("/database/wallet", response_model=ApiResponse[DatabaseSettingsData])
async def upload_database_wallet(
    file: Annotated[UploadFile, File(...)],
) -> ApiResponse[DatabaseSettingsData]:
    settings = get_settings()
    data = await _read_upload_file(file, ORACLE_WALLET_MAX_BYTES)
    wallet_dir = _install_database_wallet(settings, data, file.filename)
    settings.oracle_wallet_dir = str(wallet_dir)
    close_oracle_pool()
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
                troubleshooting=_database_connection_troubleshooting(readiness=readiness),
            )
        )

    try:
        await test_oracle_connection(candidate)
    except Exception as exc:
        oracle_error_codes = _oracle_error_codes(str(exc))
        message = _database_connection_error_message(exc, oracle_error_codes)
        return ApiResponse(
            data=DatabaseConnectionTestResult(
                status="failed",
                readiness=readiness,
                message=message,
                elapsed_ms=_elapsed_ms(started),
                troubleshooting=_database_connection_troubleshooting(
                    readiness=readiness,
                    error_text=str(exc),
                    error_type=type(exc).__name__,
                ),
                details={
                    "timeout_seconds": candidate.oracle_db_test_timeout_seconds,
                    "tcp_connect_timeout_seconds": candidate.oracle_tcp_connect_timeout_seconds,
                    "oracle_error_codes": ", ".join(oracle_error_codes) or None,
                },
                error_type=type(exc).__name__,
            )
        )

    return ApiResponse(
        data=DatabaseConnectionTestResult(
            status="success",
            readiness=readiness,
            message="Oracle 26ai への接続に成功しました。",
            elapsed_ms=_elapsed_ms(started),
            details={
                "timeout_seconds": candidate.oracle_db_test_timeout_seconds,
                "tcp_connect_timeout_seconds": candidate.oracle_tcp_connect_timeout_seconds,
            },
        )
    )


@router.get("/database/adb", response_model=ApiResponse[AdbInfoData])
async def get_adb_info() -> ApiResponse[AdbInfoData]:
    return ApiResponse(data=await _load_adb_info(get_settings()))


@router.post("/database/adb/settings", response_model=ApiResponse[AdbInfoData])
async def update_adb_settings(payload: AdbSettingsUpdate) -> ApiResponse[AdbInfoData]:
    settings = get_settings()
    _apply_adb_settings(settings, payload)
    _persist_adb_settings(settings)
    return ApiResponse(data=await _load_adb_info(settings))


@router.post("/database/adb/start", response_model=ApiResponse[AdbInfoData])
async def start_adb() -> ApiResponse[AdbInfoData]:
    return ApiResponse(data=await _control_adb(get_settings(), action="start"))


@router.post("/database/adb/stop", response_model=ApiResponse[AdbInfoData])
async def stop_adb() -> ApiResponse[AdbInfoData]:
    return ApiResponse(data=await _control_adb(get_settings(), action="stop"))


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
    _write_oci_config(settings, payload)
    _persist_oci_settings(settings, payload)
    settings.oci_region = payload.region
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
    configured_models = enterprise_ai_model_catalog(settings)
    models = [
        EnterpriseAiModelEntrySettings(
            model_id=model.model_id,
            display_name=model.display_name,
            vision_enabled=model.vision_enabled,
        )
        for model in configured_models
    ]
    if not models:
        models.append(
            EnterpriseAiModelEntrySettings(model_id="", display_name="", vision_enabled=False)
        )
    api_path = settings.oci_enterprise_ai_llm_path or settings.oci_enterprise_ai_vlm_path
    return ModelSettingsPayload(
        enterprise_ai=EnterpriseAiModelSettings(
            endpoint=getattr(settings, "oci_enterprise_ai_endpoint", ""),
            project_ocid=getattr(settings, "oci_enterprise_ai_project_ocid", ""),
            api_key="",
            has_api_key=bool(getattr(settings, "oci_enterprise_ai_api_key", "")),
            models=models,
            default_model_id=enterprise_ai_default_model_id(settings),
            api_path=api_path or "/responses",
            vlm_input_mode=getattr(settings, "oci_enterprise_ai_vlm_input_mode", "auto"),
            text_payload_template=getattr(settings, "oci_enterprise_ai_llm_payload_template", ""),
            vision_payload_template=getattr(settings, "oci_enterprise_ai_vlm_payload_template", ""),
            text_response_path=getattr(settings, "oci_enterprise_ai_llm_response_path", ""),
            vision_response_path=getattr(settings, "oci_enterprise_ai_vlm_response_path", ""),
            timeout_seconds=getattr(settings, "oci_enterprise_ai_timeout_seconds", 600.0),
            max_retries=getattr(settings, "oci_enterprise_ai_max_retries", 3),
            llm_max_output_tokens=getattr(
                settings, "oci_enterprise_ai_llm_max_output_tokens", 1200
            ),
            vlm_max_output_tokens=getattr(
                settings, "oci_enterprise_ai_vlm_max_output_tokens", 65536
            ),
        ),
        generative_ai=GenerativeAiModelSettings(
            embedding_model=getattr(settings, "oci_genai_embedding_model", "")
            or getattr(settings, "oci_genai_embed_model_id", "cohere.embed-v4.0"),
            embedding_dim=getattr(settings, "oci_genai_embedding_dim", 1536),
            rerank_model=getattr(settings, "oci_genai_rerank_model", "")
            or getattr(settings, "oci_genai_rerank_model_id", "cohere.rerank-v4.0-fast"),
        ),
    )


def _apply_model_settings(settings: Settings, payload: ModelSettingsPayload) -> None:
    enterprise = payload.enterprise_ai
    generative = payload.generative_ai
    settings.oci_enterprise_ai_endpoint = enterprise.endpoint
    settings.oci_enterprise_ai_project_ocid = enterprise.project_ocid
    settings.oci_enterprise_ai_api_key = _secret_value(
        current=settings.oci_enterprise_ai_api_key,
        update=enterprise.api_key,
        clear=enterprise.clear_api_key,
    )
    settings.oci_enterprise_ai_models = [
        SettingsEnterpriseAiConfiguredModel(
            model_id=model.model_id,
            display_name=model.display_name,
            vision_enabled=model.vision_enabled,
        )
        for model in enterprise.models
        if model.model_id
    ]
    settings.oci_enterprise_ai_default_model = enterprise.default_model_id
    default_model = enterprise.default_model_id
    vision_model = next(
        (
            model.model_id
            for model in enterprise.models
            if model.model_id == default_model and model.vision_enabled
        ),
        "",
    ) or next(
        (model.model_id for model in enterprise.models if model.model_id and model.vision_enabled),
        default_model,
    )
    settings.oci_enterprise_ai_llm_model = default_model
    settings.oci_enterprise_ai_vlm_model = vision_model
    settings.oci_enterprise_ai_llm_path = enterprise.api_path
    settings.oci_enterprise_ai_vlm_path = enterprise.api_path
    settings.oci_enterprise_ai_vlm_input_mode = enterprise.vlm_input_mode
    settings.oci_enterprise_ai_llm_payload_template = enterprise.text_payload_template
    settings.oci_enterprise_ai_vlm_payload_template = enterprise.vision_payload_template
    settings.oci_enterprise_ai_llm_response_path = enterprise.text_response_path
    settings.oci_enterprise_ai_vlm_response_path = enterprise.vision_response_path
    settings.oci_enterprise_ai_timeout_seconds = enterprise.timeout_seconds
    settings.oci_enterprise_ai_max_retries = enterprise.max_retries
    settings.oci_enterprise_ai_llm_max_output_tokens = enterprise.llm_max_output_tokens
    settings.oci_enterprise_ai_vlm_max_output_tokens = enterprise.vlm_max_output_tokens
    settings.oci_genai_embedding_model = generative.embedding_model
    settings.oci_genai_embedding_dim = generative.embedding_dim
    settings.oci_genai_rerank_model = generative.rerank_model
    settings.oci_genai_embed_model_id = generative.embedding_model
    settings.oci_genai_rerank_model_id = generative.rerank_model


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
            "llm_max_output_tokens": enterprise.llm_max_output_tokens,
            "vlm_max_output_tokens": enterprise.vlm_max_output_tokens,
        },
        "generative_ai": {
            "embedding_model": generative.embedding_model,
            "embedding_dim": generative.embedding_dim,
            "rerank_model": generative.rerank_model,
        },
    }


def _resolve_model_settings_file(path_value: str) -> Path:
    return resolve_model_settings_file(path_value)


def _ensure_model_settings_directory(path: Path) -> None:
    existed = path.exists()
    path.mkdir(mode=OCI_DIRECTORY_MODE, parents=True, exist_ok=True)
    if not existed:
        path.chmod(OCI_DIRECTORY_MODE)


def _model_settings_data(payload: ModelSettingsPayload, settings: Settings) -> ModelSettingsData:
    """payload と静的チェック結果を API data へ変換する。"""
    return ModelSettingsData(
        settings=_public_model_settings_payload(payload),
        checks={
            "enterprise_ai": _enterprise_ai_status(payload.enterprise_ai),
            "generative_ai": _generative_ai_status(payload.generative_ai),
            "embedding_dim": _embedding_dim_status(payload.generative_ai),
        },
        model_settings_file=settings.model_settings_file,
        source="runtime",
    )


def _public_model_settings_payload(payload: ModelSettingsPayload) -> ModelSettingsPayload:
    """secret を除いたモデル設定 payload を返す。"""
    enterprise_ai = payload.enterprise_ai.model_copy(
        update={
            "api_key": "",
            "has_api_key": (
                not payload.enterprise_ai.clear_api_key
                and (
                    payload.enterprise_ai.has_api_key or _is_present(payload.enterprise_ai.api_key)
                )
            ),
            "clear_api_key": False,
        }
    )
    return ModelSettingsPayload(
        enterprise_ai=enterprise_ai,
        generative_ai=payload.generative_ai,
    )


def _enterprise_ai_status(
    settings: EnterpriseAiModelSettings,
) -> ModelSettingsCheckStatus:
    """Enterprise AI の必須設定が揃っているか確認する。"""
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


def _generative_ai_status(
    settings: GenerativeAiModelSettings,
) -> ModelSettingsCheckStatus:
    """Generative AI の必須設定が揃っているか確認する。"""
    if _embedding_dim_status(settings) == "invalid":
        return "invalid"
    required = (settings.embedding_model, settings.rerank_model)
    return "ok" if all(_is_present(value) for value in required) else "missing"


def _embedding_dim_status(
    settings: GenerativeAiModelSettings,
) -> ModelSettingsCheckStatus:
    """Oracle 26ai VECTOR 列と embedding 次元の互換性を確認する。"""
    return "ok" if settings.embedding_dim == 1536 else "invalid"


def _is_present(value: str) -> bool:
    """空白のみの値を未設定として扱う。"""
    return bool(value.strip())


def _secret_is_available(settings: EnterpriseAiModelSettings) -> bool:
    """新規入力または保存済み Enterprise AI API key があるか確認する。"""
    if settings.clear_api_key:
        return False
    return _is_present(settings.api_key) or settings.has_api_key


def _model_test_candidate_settings(
    base: Settings,
    request: ModelSettingsTestRequest,
) -> Settings:
    """保存前 payload を実テスト用の一時 Settings へ変換する。"""
    resolved_payload = _model_settings_with_resolved_secret(base, request.settings)
    candidate = base.model_copy(deep=True)
    _apply_model_settings(candidate, resolved_payload)
    _apply_model_test_target(candidate, request)
    return candidate


def _apply_model_test_target(settings: Settings, request: ModelSettingsTestRequest) -> None:
    """対象モデルだけをテスト呼び出しに使うよう Settings を調整する。"""
    model_id = request.model_id.strip()
    if request.target_type == "enterprise_text":
        settings.oci_enterprise_ai_default_model = model_id
        settings.oci_enterprise_ai_llm_model = model_id
    elif request.target_type == "enterprise_vision":
        settings.oci_enterprise_ai_default_model = model_id
        settings.oci_enterprise_ai_vlm_model = model_id
        settings.oci_enterprise_ai_models = [
            SettingsEnterpriseAiConfiguredModel(
                model_id=model.model_id,
                display_name=model.display_name,
                vision_enabled=(model.model_id == model_id or model.vision_enabled),
            )
            for model in enterprise_ai_model_catalog(settings)
        ]
    elif request.target_type == "embedding":
        settings.oci_genai_embedding_model = model_id
        settings.oci_genai_embed_model_id = model_id
    elif request.target_type == "rerank":
        settings.oci_genai_rerank_model = model_id
        settings.oci_genai_rerank_model_id = model_id


async def _run_model_settings_test(
    settings: Settings,
    request: ModelSettingsTestRequest,
) -> dict[str, str | int | float | bool | None]:
    """対象モデルの実 API 呼び出しを行い、表示用 details を返す。"""
    _require_model_test_id(request)
    if request.target_type == "enterprise_text":
        text = await OciEnterpriseAiClient(settings=settings).generate(
            "モデル接続テストです。短く応答してください。",
            "これは Production Ready RAG のモデル接続テスト用コンテキストです。",
        )
        return {"response_chars": len(text), "surface": "llm"}
    if request.target_type == "enterprise_vision":
        text = await OciEnterpriseAiClient(settings=settings).generate_from_image(
            MODEL_TEST_IMAGE_BYTES,
            "白い背景にある大きな図形の色を日本語で1語だけ返してください。",
            mime_type="image/jpeg",
        )
        return {
            "surface": "vision",
            "response_chars": len(text),
        }
    if request.target_type == "embedding":
        vectors = await OciGenAiClient(settings=settings).embed(
            ["モデル接続テスト"],
            input_type="SEARCH_QUERY",
        )
        vector = vectors[0] if vectors else []
        return {"vector_dim": len(vector), "input_count": len(vectors)}
    ranks = await OciGenAiClient(settings=settings).rerank(
        "モデル接続テスト",
        [
            "これはモデル接続テストに関する候補文書です。",
            "別の業務文書に関する候補文書です。",
        ],
        top_n=1,
    )
    top_score = ranks[0][1] if ranks else None
    return {"ranked_count": len(ranks), "top_score": top_score}


def _require_model_test_id(request: ModelSettingsTestRequest) -> None:
    """空の model_id は実 API 呼び出し前に分かりやすく失敗させる。"""
    if not request.model_id.strip():
        raise ValueError("テストするモデル ID を入力してください。")


def _successful_model_test_result(
    request: ModelSettingsTestRequest,
    *,
    details: dict[str, str | int | float | bool | None],
    elapsed_ms: int,
) -> ModelSettingsTestResult:
    return ModelSettingsTestResult(
        status="success",
        target_type=request.target_type,
        model_id=request.model_id,
        message=_model_test_success_message(request.target_type, request.model_id),
        troubleshooting=[],
        elapsed_ms=elapsed_ms,
        details=details,
    )


def _failed_model_test_result(
    request: ModelSettingsTestRequest,
    exc: Exception,
    *,
    elapsed_ms: int,
    secrets: list[str],
) -> ModelSettingsTestResult:
    raw_error = _sanitize_model_test_error(str(exc), secrets)
    return ModelSettingsTestResult(
        status="failed",
        target_type=request.target_type,
        model_id=request.model_id,
        message=_model_test_failure_message(request.target_type, request.model_id),
        troubleshooting=_model_test_troubleshooting(
            request.target_type,
            raw_error,
            type(exc).__name__,
        ),
        raw_error=raw_error,
        error_type=type(exc).__name__,
        elapsed_ms=elapsed_ms,
        details={},
    )


def _model_test_success_message(target_type: ModelSettingsTestTargetType, model_id: str) -> str:
    """モデル種別別の成功メッセージ。"""
    if target_type == "enterprise_text":
        return f"Enterprise AI の回答生成モデル「{model_id}」から応答を取得しました。"
    if target_type == "enterprise_vision":
        return (
            f"Enterprise AI の Vision モデル「{model_id}」から"
            "構造化抽出レスポンスを取得しました。"
        )
    if target_type == "embedding":
        return f"Embedding モデル「{model_id}」で 1536 次元ベクトルを取得しました。"
    return f"Rerank モデル「{model_id}」から順位スコアを取得しました。"


def _model_test_failure_message(target_type: ModelSettingsTestTargetType, model_id: str) -> str:
    """モデル種別別の失敗メッセージ。"""
    if target_type in {"enterprise_text", "enterprise_vision"}:
        return f"Enterprise AI モデル「{model_id or '未入力'}」のテストに失敗しました。"
    if target_type == "embedding":
        return f"Embedding モデル「{model_id or '未入力'}」のテストに失敗しました。"
    return f"Rerank モデル「{model_id or '未入力'}」のテストに失敗しました。"


def _model_test_troubleshooting(
    target_type: ModelSettingsTestTargetType,
    raw_error: str,
    error_type: str,
) -> list[str]:
    """実エラーからユーザーが次に確認しやすい項目を返す。"""
    lowered = f"{raw_error} {error_type}".lower()
    tips: list[str] = []
    if target_type in {"enterprise_text", "enterprise_vision"}:
        tips.extend(
            [
                "Endpoint URL、API パス、Project OCID、API key が Enterprise AI の"
                " OpenAI-compatible gateway と一致しているか確認してください。",
                "モデル ID が Enterprise AI 側の model deployment / gateway で"
                "利用可能か確認してください。",
            ]
        )
        if "response path" in lowered or "回答 text" in raw_error or "構造化抽出" in raw_error:
            tips.append(
                "独自 gateway の場合は payload template と response path が"
                "実レスポンスの JSON 構造に合っているか確認してください。"
            )
    else:
        tips.extend(
            [
                "OCI config file、profile、region、compartment OCID が"
                "バックエンド実行環境から参照できるか確認してください。",
                "モデル ID と IAM policy が OCI Generative AI Inference の"
                " embedding/rerank 呼び出しを許可しているか確認してください。",
            ]
        )
    if any(token in lowered for token in ("401", "unauthorized", "authentication")):
        tips.append(
            "認証エラーです。API key / OCI config の資格情報を" "再発行または再保存してください。"
        )
    if any(token in lowered for token in ("403", "notauthorized", "not authorized", "forbidden")):
        tips.append(
            "権限エラーです。Project / compartment / IAM policy の対象が"
            "このモデル呼び出しを許可しているか確認してください。"
        )
    if any(token in lowered for token in ("404", "not found")):
        tips.append(
            "Endpoint、API パス、model ID のいずれかが見つかっていません。"
            "リージョンと model deployment 名も確認してください。"
        )
    if any(token in lowered for token in ("timeout", "timed out")):
        tips.append(
            "タイムアウトです。ネットワーク経路を確認し、"
            "必要ならタイムアウト秒数を一時的に長くしてください。"
        )
    if any(token in lowered for token in ("429", "quota", "rate")):
        tips.append(
            "レート制限または quota の可能性があります。"
            "しばらく待つか service limit を確認してください。"
        )
    if any(token in lowered for token in ("500", "502", "503", "504")):
        tips.append(
            "サービス側または gateway 側の一時障害の可能性があります。"
            "少し待って再試行し、OCI 側の稼働状況を確認してください。"
        )
    return list(dict.fromkeys(tips))


def _sanitize_model_test_error(raw_error: str, secrets: list[str]) -> str:
    """実エラーは残しつつ、既知の secret だけを伏せる。"""
    sanitized = raw_error.strip() or "詳細メッセージは返されませんでした。"
    for secret in secrets:
        cleaned = secret.strip()
        if cleaned:
            sanitized = sanitized.replace(cleaned, "<secret>")
    return sanitized[:2000]


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
        embedding_dimension=settings.oci_genai_embedding_dim,
        vector_column=f"VECTOR({settings.oci_genai_embedding_dim}, FLOAT32)",
        adb_ocid=getattr(settings, "oracle_adb_ocid", ""),
        region=settings.resolved_oracle_adb_region,
        config_source="runtime",
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
    region = settings.resolved_oracle_adb_region
    resolved_status: AdbOperationStatus = status or ("success" if adb_ocid else "not_configured")
    return AdbInfoData(
        status=resolved_status,
        message=message
        or ("ADB OCID が設定されています。" if adb_ocid else "ADB OCID が設定されていません。"),
        id=adb_ocid or None,
        region=region or None,
    )


def _apply_adb_settings(settings: Settings, payload: AdbSettingsUpdate) -> None:
    """ADB 操作対象 OCID と region を現在プロセスへ反映する。"""
    settings.oracle_adb_ocid = payload.adb_ocid.strip()
    settings.oracle_adb_region = payload.region.strip()


def _adb_info_data(
    status: AdbOperationStatus,
    message: str,
    info: AutonomousDatabaseInfo,
    region: str | None,
    *,
    lifecycle_override: str | None = None,
) -> AdbInfoData:
    """ADB 情報スナップショットを表示用データへ変換する。"""
    return AdbInfoData(
        status=status,
        message=message,
        id=info.id,
        display_name=info.display_name,
        lifecycle_state=lifecycle_override or info.lifecycle_state,
        db_name=info.db_name,
        cpu_core_count=info.cpu_core_count,
        data_storage_size_in_tbs=info.data_storage_size_in_tbs,
        region=region,
    )


async def _load_adb_info(settings: Settings) -> AdbInfoData:
    """ADB の情報を取得する。設定不足や OCI エラーは status へ載せて返す。"""
    region = settings.resolved_oracle_adb_region or None
    adb_ocid = settings.oracle_adb_ocid.strip()
    if not adb_ocid:
        return _adb_info(settings, status="not_configured")
    try:
        info = await OciDatabaseClient(settings=settings).get_autonomous_database(adb_ocid)
    except Exception as exc:
        return AdbInfoData(
            status="error",
            message=f"データベース情報の取得に失敗しました: {exc}",
            id=adb_ocid,
            region=region,
        )
    return _adb_info_data(
        "success",
        "データベース情報を取得しました。",
        info,
        region,
    )


async def _control_adb(settings: Settings, *, action: Literal["start", "stop"]) -> AdbInfoData:
    """ADB の起動/停止を OCI Database API 経由で行う。"""
    region = settings.resolved_oracle_adb_region or None
    adb_ocid = settings.oracle_adb_ocid.strip()
    if not adb_ocid:
        return _adb_info(settings, status="not_configured")

    client = OciDatabaseClient(settings=settings)
    try:
        info = await client.get_autonomous_database(adb_ocid)
    except Exception as exc:
        return AdbInfoData(
            status="error",
            message=f"データベース情報の取得に失敗しました: {exc}",
            id=adb_ocid,
            region=region,
        )

    state = info.lifecycle_state
    if action == "start":
        if state == "AVAILABLE":
            return _adb_info_data(
                "already_available",
                "データベースは既に起動しています。",
                info,
                region,
            )
        if state not in ("STOPPED", "UNAVAILABLE"):
            return _adb_info_data(
                "cannot_start",
                f"データベースの現在の状態 ({state}) では起動できません。",
                info,
                region,
            )
        try:
            await client.start_autonomous_database(adb_ocid)
        except Exception as exc:
            return _adb_info_data("error", f"データベースの起動に失敗しました: {exc}", info, region)
        return _adb_info_data(
            "accepted",
            f"データベース '{info.display_name}' の起動を開始しました。",
            info,
            region,
            lifecycle_override="STARTING",
        )

    if state == "STOPPED":
        return _adb_info_data("already_stopped", "データベースは既に停止しています。", info, region)
    if state != "AVAILABLE":
        return _adb_info_data(
            "cannot_stop",
            f"データベースの現在の状態 ({state}) では停止できません。",
            info,
            region,
        )
    try:
        await client.stop_autonomous_database(adb_ocid)
    except Exception as exc:
        return _adb_info_data("error", f"データベースの停止に失敗しました: {exc}", info, region)
    return _adb_info_data(
        "accepted",
        f"データベース '{info.display_name}' の停止を開始しました。",
        info,
        region,
        lifecycle_override="STOPPING",
    )


def _persist_adb_settings(settings: Settings) -> None:
    _write_env_values(
        BACKEND_ENV_FILE,
        {
            "ORACLE_ADB_OCID": settings.oracle_adb_ocid,
            "ORACLE_ADB_REGION": settings.oracle_adb_region,
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
        if (backend == "local" and local_dir) or (backend == "oci" and namespace and bucket)
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
        config_source="runtime",
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
        config_source="runtime",
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
    """perf_counter の開始時刻から経過 ms を返す。"""
    return max(0, round((time.perf_counter() - started) * 1000))


def _oracle_error_codes(error_text: str) -> list[str]:
    """Oracle / python-oracledb の公開してよいエラーコードだけを抽出する。"""
    return list(dict.fromkeys(match.upper() for match in ORACLE_ERROR_CODE_RE.findall(error_text)))


def _database_connection_error_message(exc: Exception, oracle_error_codes: list[str]) -> str:
    """secret を含めず、Oracle 接続エラーの原因カテゴリをユーザーへ返す。"""
    if getattr(exc, "safe_for_user", False):
        return str(exc)

    code_label = f"（{', '.join(oracle_error_codes)}）" if oracle_error_codes else ""
    code_set = set(oracle_error_codes)
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
            "Oracle Instant Client の配置と ORACLE_CLIENT_LIB_DIR を確認してください。"
        )
    if oracle_error_codes:
        return (
            f"Oracle 26ai へ接続できませんでした{code_label}。"
            "下の確認ポイントと backend ログを確認してください。"
        )
    return (
        "Oracle 26ai へ接続できませんでした。"
        "下の確認ポイントと backend ログの Oracle エラーコードを確認してください。"
    )


def _database_connection_troubleshooting(
    *,
    readiness: str,
    error_text: str = "",
    error_type: str = "",
) -> list[str]:
    """Oracle 接続テストの結果から次に確認するポイントを返す。"""
    tips: list[str] = []
    if readiness == "missing":
        tips.append(
            "ユーザー名、Wallet サービス名、Wallet ZIP が"
            "入力・アップロード済みか確認してください。"
        )
    if readiness == "missing_credentials":
        tips.append("DB パスワードまたは Wallet パスワードが保存済みか確認してください。")
    if readiness == "wallet_not_found":
        tips.append("ADB からダウンロードした Wallet ZIP をアップロードし直してください。")
    if readiness == "invalid":
        tips.append("Wallet の tnsnames.ora / sqlnet.ora とサービス名の形式を確認してください。")

    combined = f"{error_text} {error_type}".lower()
    if any(token in combined for token in ("timeout", "timed out", "oracleconnectiontimeouterror")):
        tips.append(
            "接続テストがタイムアウトしました。ADB が起動中か、VCN/VPN/プロキシ経路から "
            "TCPS 1522 に到達できるか確認してください。"
        )
    if "ora-01017" in combined:
        tips.append("ユーザー名または DB パスワードが正しいか確認してください。")
    if "ora-12154" in combined or "tns" in combined:
        tips.append("Wallet サービス名が tnsnames.ora に存在するか確認してください。")
    if "ora-12506" in combined:
        tips.append(
            "ADB の Network Access / ACL で、この実行ホストの public IP または VCN 経路を"
            "許可してください。"
        )
    if "ora-12514" in combined or "ora-12505" in combined:
        tips.append("Wallet サービス名が ADB の接続文字列として有効か確認してください。")
    if "ora-12541" in combined or "dpy-6005" in combined or "dpy-6000" in combined:
        tips.append(
            "データベースが停止していないか、ADB の listener に" "到達できるか確認してください。"
        )
    if "wallet" in combined or "dpy-4011" in combined:
        tips.append(
            "Wallet ZIP の内容、Wallet パスワード、" "ORACLE_CLIENT_LIB_DIR を確認してください。"
        )
    if "dpi-1047" in combined or "dpi-1072" in combined:
        tips.append(
            "Oracle Instant Client が ORACLE_CLIENT_LIB_DIR に存在し、"
            "実行環境から読み込めるか確認してください。"
        )
    if "operationalerror" in combined and not tips:
        tips.append(
            "backend ログに出ている ORA/DPY/DPI エラーコードを確認し、"
            "Wallet、サービス名、認証情報、ネットワーク経路を切り分けてください。"
        )

    if not tips:
        tips.append(
            "バックエンドログの Oracle エラーコードと Wallet / DSN /"
            "ネットワーク設定を確認してください。"
        )
    return list(dict.fromkeys(tips))


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
