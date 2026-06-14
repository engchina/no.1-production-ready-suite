"""ヘルスチェックエンドポイント。"""

from fastapi import APIRouter, Response, status

from app.config import get_settings
from app.readiness import readiness_checks, readiness_checks_are_ok
from app.schemas.common import ApiResponse, HealthData

router = APIRouter()


@router.get("/health", response_model=ApiResponse[HealthData])
async def health() -> ApiResponse[HealthData]:
    """サービス稼働状態を返す。"""
    settings = get_settings()
    return ApiResponse(
        data=HealthData(
            status="ok",
            version=settings.app_version,
            message="oci",
        )
    )


@router.get("/ready", response_model=ApiResponse[HealthData])
async def readiness(response: Response) -> ApiResponse[HealthData]:
    """依存設定を含めた readiness を返す。"""
    settings = get_settings()
    checks = readiness_checks(settings)
    ready = readiness_checks_are_ok(checks)
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ApiResponse(
        data=HealthData(
            status="ok" if ready else "degraded",
            version=settings.app_version,
            message="oci",
            checks=checks,
        )
    )
