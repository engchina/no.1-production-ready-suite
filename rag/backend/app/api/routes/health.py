"""ヘルスチェックエンドポイント。"""

import asyncio
import logging

from fastapi import APIRouter, Response, status

from app.clients.oracle import test_oracle_connection
from app.config import get_settings
from app.rag.system_schema import oracle_error_code, system_schema_manager
from app.readiness import (
    READINESS_OK,
    oracle_readiness_check,
    readiness_checks,
    readiness_checks_are_ok,
)
from app.schemas.common import ApiResponse, DatabaseStatusData, HealthData

router = APIRouter()
logger = logging.getLogger(__name__)


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


@router.get("/ready/database", response_model=ApiResponse[DatabaseStatusData])
async def database_status() -> ApiResponse[DatabaseStatusData]:
    """データベースの利用可否を返す(設定の有無 + 実接続プローブ)。

    フロントの DB ゲートが「設定ページ以外」を開く前に参照する。常に 200 で返し、
    status で ok / not_configured / unreachable / setup_required を区別する。
    """
    settings = get_settings()
    check = oracle_readiness_check(settings)

    # 接続情報が未設定/不足: 実接続を試さず即座に「未設定」を返す。
    if check != READINESS_OK:
        return ApiResponse(
            data=DatabaseStatusData(status="not_configured", check=check),
        )

    # 設定済み: 起動しているかを bounded な実接続プローブで確認する。
    # test_oracle_connection 側で Oracle 専用 timeout を持つ。閲覧 API 用 timeout で
    # 先に切ると、起動済み ADB の初回接続を unreachable と誤判定する。
    try:
        await test_oracle_connection(settings)
    except Exception as exc:  # noqa: BLE001 - DB 不通を status へ正規化する境界
        logger.warning(
            "database_status_unreachable",
            extra={"exception_type": type(exc).__name__},
        )
        return ApiResponse(
            data=DatabaseStatusData(
                status="unreachable",
                check=check,
                detail=str(exc) or None,
            ),
        )

    try:
        schema = await asyncio.to_thread(system_schema_manager.status)
    except Exception as exc:  # noqa: BLE001 - schema 準備案内へ正規化する境界
        code = oracle_error_code(exc)
        logger.warning(
            "database_schema_status_unavailable",
            extra={"error_code": code, "exception_type": type(exc).__name__},
        )
        return ApiResponse(
            data=DatabaseStatusData(
                status="setup_required",
                check=check,
                detail=f"システムテーブルの状態を確認できませんでした ({code})。",
            )
        )

    schema_status = schema["status"]
    if (
        schema_status != "ready"
        or schema["operation_state"]["status"] == "running"
    ):
        return ApiResponse(
            data=DatabaseStatusData(
                status="setup_required",
                check=check,
                schema_status=schema_status,
            )
        )

    return ApiResponse(
        data=DatabaseStatusData(
            status="ok",
            check=check,
            schema_status="ready",
        )
    )
