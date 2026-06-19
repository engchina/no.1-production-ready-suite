"""サービス管理エンドポイント。

前処理 / Parser マイクロサービスの稼働状態の可視化(GET)と、ローカル開発(Docker Compose)
での起動/停止(POST)を提供する。制御はカタログ allowlist + feature flag で二重に保護する。
"""

import logging

from fastapi import APIRouter, HTTPException, status

from app.config import Settings, get_settings
from app.schemas.common import ApiResponse
from app.schemas.service_management import (
    ServiceControlResultData,
    ServiceListData,
    ServiceStatusData,
)
from app.services.catalog import (
    SERVICE_CATALOG,
    get_catalog_entry,
    is_dev_mode,
    service_health_url,
)
from app.services.control import (
    ServiceAction,
    ServiceControlClient,
    ServiceControlError,
)
from app.services.status import probe_service_status, probe_service_statuses

router = APIRouter()
logger = logging.getLogger(__name__)

_control_client = ServiceControlClient()


def _control_enabled(settings: Settings) -> bool:
    """制御の実効可否。dev(uv)は自動有効化し、prod は明示フラグを要する。"""
    return is_dev_mode(settings) or bool(settings.rag_service_control_enabled)


@router.get("", response_model=ApiResponse[ServiceListData])
async def list_services() -> ApiResponse[ServiceListData]:
    """全マイクロサービスの稼働状態と制御可否・配備モードを返す。"""
    settings = get_settings()
    statuses = await probe_service_statuses(settings)
    services = [
        ServiceStatusData(
            service_id=entry.service_id,
            category=entry.category,
            profile=entry.profile,
            label_key=entry.label_key,
            status=statuses[entry.service_id],
            configured=bool(service_health_url(settings, entry)),
        )
        for entry in SERVICE_CATALOG
    ]
    return ApiResponse(
        data=ServiceListData(
            control_enabled=_control_enabled(settings),
            deployment_mode="dev" if is_dev_mode(settings) else "prod",
            services=services,
        )
    )


async def _control(service_id: str, action: ServiceAction) -> ApiResponse[ServiceControlResultData]:
    """共通の起動/停止ハンドラ(flag 確認 → allowlist 照合 → 実行 → 再プローブ)。"""
    settings = get_settings()
    if not _control_enabled(settings):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="サービスの起動/停止は無効化されています(rag_service_control_enabled)。",
        )
    entry = get_catalog_entry(service_id)
    if entry is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="指定したサービスが見つかりません。",
        )
    try:
        await _control_client.control(settings, entry, action)
    except ServiceControlError as exc:
        logger.warning(
            "service_control_failed",
            extra={
                "service_id": service_id,
                "action": action,
                "exit_code": exc.result.exit_code,
            },
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=exc.result.detail or "サービスの操作に失敗しました。",
        ) from exc
    # 操作対象 1 件のみ再プローブする(全件叩く必要はない)。
    new_status = await probe_service_status(settings, entry)
    return ApiResponse(
        data=ServiceControlResultData(
            service_id=service_id,
            action=action,
            status=new_status,
        )
    )


@router.post("/{service_id}/start", response_model=ApiResponse[ServiceControlResultData])
async def start_service(service_id: str) -> ApiResponse[ServiceControlResultData]:
    """サービスを起動する(docker compose up -d)。"""
    return await _control(service_id, "start")


@router.post("/{service_id}/stop", response_model=ApiResponse[ServiceControlResultData])
async def stop_service(service_id: str) -> ApiResponse[ServiceControlResultData]:
    """サービスを停止する(docker compose stop)。"""
    return await _control(service_id, "stop")


@router.post("/{service_id}/restart", response_model=ApiResponse[ServiceControlResultData])
async def restart_service(service_id: str) -> ApiResponse[ServiceControlResultData]:
    """サービスを再起動する(docker compose restart)。"""
    return await _control(service_id, "restart")
