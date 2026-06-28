"""サービス稼働状態プローブ。

各マイクロサービスの ``GET /health`` を bounded timeout で並列に問い合わせ、稼働状態を
``running / degraded / stopped / unconfigured`` に正規化する。
``parser_adapter_readiness._probe_service_health`` の best-effort パターンを async 化したもの。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Literal

import httpx

from app.config import Settings
from app.services.catalog import (
    SERVICE_CATALOG,
    ServiceCatalogEntry,
    service_health_url,
)

logger = logging.getLogger(__name__)

# - running: /health が status=ok(正常稼働)
# - degraded: 到達したが status!=ok(例: LibreOffice 未導入)
# - stopped: 接続拒否/timeout(コンテナ停止と解釈)
# - unconfigured: URL 未設定
# - in_process: deployable=False のステージ。backend 内処理で動作し、サービス化は将来対応
#   (/health を叩かず固定で返す)。
ServiceRuntimeStatus = Literal[
    "running",
    "degraded",
    "stopped",
    "unconfigured",
    "in_process",
]


async def probe_service_status(
    settings: Settings, entry: ServiceCatalogEntry
) -> ServiceRuntimeStatus:
    """1 サービスの /health を問い合わせて稼働状態を返す(例外は安全に縮退)。"""
    # deployable=False は backend 内処理で動作するため、HTTP を叩かず固定で in_process を返す。
    if not entry.deployable:
        return "in_process"
    url = service_health_url(settings, entry)
    if not url:
        return "unconfigured"
    timeout = float(settings.rag_service_status_probe_timeout_seconds)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(f"{url}/health")
            response.raise_for_status()
            payload = response.json()
    except Exception as exc:  # noqa: BLE001 - 到達不可は stopped へ正規化する境界
        logger.debug(
            "service status probe failed: service=%s url=%s error=%s",
            entry.service_id,
            url,
            exc,
        )
        return "stopped"
    status = str(payload.get("status", "")).strip().lower() if isinstance(payload, dict) else ""
    return "running" if status == "ok" else "degraded"


async def probe_service_statuses(settings: Settings) -> dict[str, ServiceRuntimeStatus]:
    """全カタログサービスの稼働状態を問い合わせて返す。"""
    results = await asyncio.gather(
        *(probe_service_status(settings, entry) for entry in SERVICE_CATALOG)
    )
    return {
        entry.service_id: status for entry, status in zip(SERVICE_CATALOG, results, strict=True)
    }
