"""データベース可用性 API。"""

from __future__ import annotations

import logging
import re

from fastapi import APIRouter
from pr_backend_core import ApiResponse

from app.api.models import DatabaseStatusData
from app.clients.oracle import OracleConnectionTimeoutError, test_oracle_connection
from app.features.nl2sql.incremental_observability import record_ready_once
from app.features.nl2sql.service import nl2sql_service
from app.features.settings.system_schema_runtime import observe_system_schema_epoch
from app.readiness import READINESS_OK, oracle_readiness_check
from app.settings import get_settings

router = APIRouter(tags=["health"])
logger = logging.getLogger(__name__)


def _safe_database_error_detail(exc: Exception) -> str:
    """接続文字列や Wallet path を返さず、分類に必要な情報だけを返す。"""
    if isinstance(exc, OracleConnectionTimeoutError):
        return "Oracle connection probe timed out."
    match = re.search(r"ORA-\d{5}", str(exc), flags=re.IGNORECASE)
    if match:
        return f"Oracle connection probe failed ({match.group(0).upper()})."
    return "Oracle connection probe failed."


@router.get("/ready/database", response_model=ApiResponse[DatabaseStatusData])
async def database_status() -> ApiResponse[DatabaseStatusData]:
    """DB gate が使用する設定確認と bounded connection probe。常に HTTP 200。"""
    settings = get_settings()
    runtime = settings.nl2sql_runtime_mode.strip().lower()
    persistence = settings.nl2sql_persistence_mode.strip().lower()
    if runtime == "deterministic" and persistence == "memory":
        record_ready_once()
        return ApiResponse(
            data=DatabaseStatusData(status="ok", check=READINESS_OK, detail="memory"),
        )

    check = oracle_readiness_check(settings)
    if check != READINESS_OK:
        return ApiResponse(data=DatabaseStatusData(status="not_configured", check=check))

    try:
        await test_oracle_connection(settings)
    except Exception as exc:  # noqa: BLE001 - DB failure is normalized at this API boundary
        logger.exception(
            "database_status_unreachable",
            extra={"exception_type": type(exc).__name__},
        )
        return ApiResponse(
            data=DatabaseStatusData(
                status="unreachable",
                check=check,
                detail=_safe_database_error_detail(exc),
            )
        )

    if nl2sql_service.uses_incremental_store:
        try:
            migrated, migration_detail = nl2sql_service.check_incremental_store()
        except Exception as exc:  # noqa: BLE001 - normalized readiness boundary
            logger.exception(
                "incremental_store_check_failed",
                extra={"exception_type": type(exc).__name__},
            )
            return ApiResponse(
                data=DatabaseStatusData(
                    status="unreachable",
                    check="migration_check_failed",
                    detail=_safe_database_error_detail(exc),
                )
            )
        if not migrated:
            return ApiResponse(
                data=DatabaseStatusData(
                    # DB 接続設定は有効で probe も成功している。migration 未適用を
                    # 接続情報の未設定として扱うと、設定画面の接続成功表示と矛盾する。
                    status="setup_required",
                    check="migration_required",
                    detail=migration_detail,
                )
            )

        try:
            observe_system_schema_epoch()
        except Exception as exc:  # noqa: BLE001 - readiness boundary で安全な状態へ正規化
            logger.warning(
                "system_schema_epoch_check_failed",
                extra={"exception_type": type(exc).__name__},
            )

    record_ready_once()
    return ApiResponse(data=DatabaseStatusData(status="ok", check=check))
