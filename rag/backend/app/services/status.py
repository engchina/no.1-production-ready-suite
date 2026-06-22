"""サービス稼働状態プローブ。

各マイクロサービスの ``GET /health`` を bounded timeout で並列に問い合わせ、稼働状態を
``running / degraded / stopped / dependency_stopped / unconfigured`` に正規化する。
``parser_adapter_readiness._probe_service_health`` の best-effort パターンを async 化したもの。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Literal

import httpx

from app.clients.http_retry import async_request_with_retry, retry_config_from_settings
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
# - dependency_stopped: 本体は到達可能だが依存サービスが未稼働
# - unconfigured: URL 未設定
ServiceRuntimeStatus = Literal[
    "running",
    "degraded",
    "stopped",
    "dependency_stopped",
    "unconfigured",
]


async def probe_service_status(
    settings: Settings, entry: ServiceCatalogEntry
) -> ServiceRuntimeStatus:
    """1 サービスの /health を問い合わせて稼働状態を返す(例外は安全に縮退)。"""
    url = service_health_url(settings, entry)
    if not url:
        return "unconfigured"
    timeout = float(settings.rag_service_status_probe_timeout_seconds)
    retry = retry_config_from_settings(settings)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await async_request_with_retry(
                client,
                "GET",
                f"{url}/health",
                retry=retry,
                logger=logger,
                log_extra={"service_id": entry.service_id, "service_url": url},
            )
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
    """全カタログサービスの稼働状態を問い合わせ、依存未稼働も反映して返す。"""
    results = await asyncio.gather(
        *(probe_service_status(settings, entry) for entry in SERVICE_CATALOG)
    )
    statuses: dict[str, ServiceRuntimeStatus] = {
        entry.service_id: status for entry, status in zip(SERVICE_CATALOG, results, strict=True)
    }
    for entry in SERVICE_CATALOG:
        if not blocked_dependencies(statuses, entry):
            continue
        if statuses[entry.service_id] in {"running", "degraded"}:
            statuses[entry.service_id] = "dependency_stopped"
    return statuses


def blocked_dependencies(
    statuses: dict[str, ServiceRuntimeStatus], entry: ServiceCatalogEntry
) -> tuple[str, ...]:
    """entry の依存サービスのうち running でない service_id を返す。"""
    return tuple(dep for dep in entry.depends_on if statuses.get(dep) != "running")
