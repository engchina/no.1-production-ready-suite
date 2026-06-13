"""ヘルスチェックエンドポイント。"""

from collections.abc import Mapping
from pathlib import Path

from fastapi import APIRouter, Response, status

from app.config import Settings, get_settings
from app.schemas.common import ApiResponse, HealthData

router = APIRouter()

READINESS_OK = "ok"
READINESS_MISSING = "missing"
READINESS_INVALID = "invalid"
READINESS_MISSING_CREDENTIALS = "missing_credentials"
READINESS_WALLET_NOT_FOUND = "wallet_not_found"


@router.get("/health", response_model=ApiResponse[HealthData])
async def health() -> ApiResponse[HealthData]:
    """サービス稼働状態を返す。"""
    settings = get_settings()
    return ApiResponse(
        data=HealthData(
            status="ok",
            version=settings.app_version,
            message=f"adapter={settings.ai_service_adapter}",
        )
    )


@router.get("/ready", response_model=ApiResponse[HealthData])
async def readiness(response: Response) -> ApiResponse[HealthData]:
    """依存設定を含めた readiness を返す。"""
    settings = get_settings()
    checks = _readiness_checks(settings)
    ready = readiness_checks_are_ok(checks)
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ApiResponse(
        data=HealthData(
            status="ok" if ready else "degraded",
            version=settings.app_version,
            message=f"adapter={settings.ai_service_adapter}",
            checks=checks,
        )
    )


def readiness_checks_are_ok(checks: Mapping[str, str]) -> bool:
    """readiness checks がすべて成功しているか判定する。"""
    return all(value == READINESS_OK for value in checks.values())


def _readiness_checks(settings: Settings) -> dict[str, str]:
    """adapter mode ごとの readiness check を実行する。"""
    if settings.ai_service_adapter == "local":
        checks = {"local_storage": _local_storage_check(settings)}
        checks.update(_production_safety_checks(settings))
        return checks
    checks = {
        "oci_common": _required_values_check(
            settings.oci_region,
            settings.oci_compartment_id,
        ),
        "enterprise_ai": _required_values_check(
            settings.oci_enterprise_ai_endpoint,
            settings.oci_enterprise_ai_llm_model,
            settings.oci_enterprise_ai_vlm_model,
        ),
        "genai": _genai_check(settings),
        "oracle": _oracle_check(settings),
        "object_storage": _required_values_check(
            settings.object_storage_namespace,
            settings.object_storage_bucket,
        ),
    }
    checks.update(_production_safety_checks(settings))
    return checks


def _production_safety_checks(settings: Settings) -> dict[str, str]:
    """production 環境で必須にする安全設定を確認する。"""
    if not _is_production(settings):
        return {}
    return {
        "deployment_adapter": (
            READINESS_OK if settings.ai_service_adapter == "oci" else READINESS_INVALID
        ),
        "audit_context_salt": (
            READINESS_OK if _is_present(settings.audit_context_hash_salt) else READINESS_MISSING
        ),
    }


def _local_storage_check(settings: Settings) -> str:
    """local adapter の保存先が作成・書き込み可能か確認する。"""
    try:
        root = Path(settings.local_storage_dir).expanduser()
        root.mkdir(parents=True, exist_ok=True)
        probe = root / ".readiness"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
    except OSError:
        return "error"
    return READINESS_OK


def _genai_check(settings: Settings) -> str:
    """OCI Generative AI の embedding/rerank 設定を確認する。"""
    required_status = _required_values_check(
        settings.oci_genai_embedding_model,
        settings.oci_genai_rerank_model,
    )
    if required_status != READINESS_OK:
        return required_status
    if settings.oci_genai_embedding_dim != 1536:
        return READINESS_INVALID
    return READINESS_OK


def _oracle_check(settings: Settings) -> str:
    """Oracle 26ai の接続設定を確認する。"""
    required_status = _required_values_check(settings.oracle_user, settings.oracle_dsn)
    if required_status != READINESS_OK:
        return required_status

    has_password = _is_present(settings.oracle_password)
    wallet_dir = settings.oracle_wallet_dir.strip()
    has_wallet = _is_present(wallet_dir)
    if has_wallet and not Path(wallet_dir).expanduser().is_dir():
        return READINESS_WALLET_NOT_FOUND
    if not has_password and not has_wallet:
        return READINESS_MISSING_CREDENTIALS
    return READINESS_OK


def _required_values_check(*values: str) -> str:
    """必須文字列がすべて設定済みか確認する。"""
    if all(_is_present(value) for value in values):
        return READINESS_OK
    return READINESS_MISSING


def _is_present(value: str) -> bool:
    """空白のみの値を未設定として扱う。"""
    return bool(value.strip())


def _is_production(settings: Settings) -> bool:
    """ENVIRONMENT=production を production 判定に使う。"""
    return settings.environment.strip().lower() == "production"
