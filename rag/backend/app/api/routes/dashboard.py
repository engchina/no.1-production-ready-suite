"""ダッシュボード API。"""

import asyncio
import logging
from datetime import UTC, datetime

from fastapi import APIRouter
from pydantic import ValidationError

from app.clients.oracle import OracleClient
from app.config import get_settings
from app.readiness import readiness_checks, readiness_checks_are_ok
from app.schemas.common import ApiResponse
from app.schemas.dashboard import (
    DashboardActivity,
    DashboardIngestionQuality,
    DashboardStats,
    DashboardSummary,
    DashboardSystemInfo,
)
from app.schemas.document import DocumentSummary, FileStatus
from app.schemas.extraction import DocumentElement, StructuredExtraction

router = APIRouter()
logger = logging.getLogger(__name__)

DASHBOARD_DB_TIMEOUT_MESSAGE = (
    "ダッシュボードのデータ取得が {timeout:g} 秒以内に完了しませんでした。"
    "データベースの起動状態を確認して再試行してください。"
)
DASHBOARD_DB_ERROR_MESSAGE = (
    "ダッシュボードのデータ取得に失敗しました。"
    "データベースの起動状態を確認して再試行してください。"
)

type DashboardData = tuple[
    list[DocumentSummary],
    int,
    list[dict[str, object]],
    list[dict[str, str | int | float | bool | None]],
]


class DashboardDataUnavailable(RuntimeError):
    """DB 応答不良時に dashboard を縮退表示するための内部例外。"""

    def __init__(self, message: str, check_status: str) -> None:
        super().__init__(message)
        self.message = message
        self.check_status = check_status


@router.get("/summary", response_model=ApiResponse[DashboardSummary])
async def dashboard_summary() -> ApiResponse[DashboardSummary]:
    """ダッシュボード初期表示用の集計を返す。"""
    settings = get_settings()
    oracle = OracleClient(settings)
    warning_messages: list[str] = []
    dashboard_data_status: str | None = None
    try:
        documents, searchable_rows, extractions, chunk_metadata = await _load_dashboard_data(
            oracle,
            timeout_seconds=settings.dashboard_query_timeout_seconds,
        )
    except DashboardDataUnavailable as exc:
        documents = []
        searchable_rows = 0
        extractions = []
        chunk_metadata = []
        warning_messages.append(exc.message)
        dashboard_data_status = exc.check_status

    ingestion_quality = _ingestion_quality(
        documents=documents,
        extractions=extractions,
        chunk_metadata=chunk_metadata,
    )
    checks = readiness_checks(settings)
    if dashboard_data_status is not None:
        checks["dashboard_data"] = dashboard_data_status
        if checks.get("oracle") == "ok":
            checks["oracle"] = dashboard_data_status

    return ApiResponse(
        data=DashboardSummary(
            stats=_dashboard_stats(
                documents=documents,
                searchable_rows=searchable_rows,
            ),
            ingestion_quality=ingestion_quality,
            recent_activities=_recent_activities(documents),
            system=DashboardSystemInfo(
                status="online" if readiness_checks_are_ok(checks) else "degraded",
                version=settings.app_version,
                searchable_rows=searchable_rows,
                checks=checks,
            ),
        ),
        warning_messages=warning_messages,
    )


async def _load_dashboard_data(
    oracle: OracleClient,
    *,
    timeout_seconds: float,
) -> DashboardData:
    """DB 停止時に dashboard 初期表示を長時間 pending にしない。"""
    try:
        return await asyncio.wait_for(
            _load_dashboard_data_unbounded(oracle),
            timeout=timeout_seconds,
        )
    except TimeoutError as exc:
        logger.warning(
            "dashboard_data_timeout",
            extra={"timeout_seconds": timeout_seconds},
        )
        raise DashboardDataUnavailable(
            DASHBOARD_DB_TIMEOUT_MESSAGE.format(timeout=timeout_seconds),
            "timeout",
        ) from exc
    except Exception as exc:
        logger.exception(
            "dashboard_data_load_failed",
            extra={"exception_type": type(exc).__name__},
        )
        raise DashboardDataUnavailable(DASHBOARD_DB_ERROR_MESSAGE, "error") from exc


async def _load_dashboard_data_unbounded(oracle: OracleClient) -> DashboardData:
    """dashboard 集計に必要な DB データを取得する。"""
    documents = await oracle.list_documents(limit=None)
    searchable_rows = await oracle.count_chunks()
    extractions = await oracle.list_document_extractions()
    chunk_metadata = await oracle.list_chunk_metadata()
    return documents, searchable_rows, extractions, chunk_metadata


def _ingestion_quality(
    *,
    documents: list[DocumentSummary],
    extractions: list[dict[str, object]],
    chunk_metadata: list[dict[str, str | int | float | bool | None]],
) -> DashboardIngestionQuality:
    """extraction と chunk metadata から構造化取込の集計を作る。"""
    document_count = len(documents)
    structured_document_count = 0
    element_count = 0
    table_count = 0
    list_count = 0
    page_keys: set[tuple[int, int]] = set()

    for document_index, extraction in enumerate(extractions):
        elements = _normalized_elements(extraction)
        if elements:
            structured_document_count += 1
        element_count += len(elements)
        for element in elements:
            if element.kind == "table":
                table_count += 1
            elif element.kind == "list":
                list_count += 1
            if element.page_number is not None:
                page_keys.add((document_index, element.page_number))

    return DashboardIngestionQuality(
        document_count=document_count,
        structured_document_count=structured_document_count,
        element_count=element_count,
        table_count=table_count,
        list_count=list_count,
        page_count=len(page_keys),
        chunk_profile_counts=_metadata_counts(chunk_metadata, "chunk_profile"),
        content_kind_counts=_metadata_counts(chunk_metadata, "content_kind"),
    )


def _normalized_elements(extraction: dict[str, object]) -> list[DocumentElement]:
    """旧 raw_text-only データも StructureExtraction と同じ規則で elements 化する。"""
    if not extraction:
        return []
    try:
        return StructuredExtraction.model_validate(extraction).elements
    except (TypeError, ValueError, ValidationError):
        return []


def _metadata_counts(
    rows: list[dict[str, str | int | float | bool | None]],
    key: str,
) -> dict[str, int]:
    """chunk metadata の低 cardinality 値を件数化する。"""
    counts: dict[str, int] = {}
    for metadata in rows:
        value = metadata.get(key)
        label = str(value).strip() if isinstance(value, str) and value.strip() else "unknown"
        counts[label] = counts.get(label, 0) + 1
    return dict(sorted(counts.items()))


def _dashboard_stats(
    documents: list[DocumentSummary],
    searchable_rows: int,
) -> DashboardStats:
    """DocumentSummary 群からダッシュボード集計を作る。"""
    now = datetime.now(UTC)
    return DashboardStats(
        total_uploads=len(documents),
        uploads_this_month=sum(
            1 for document in documents if _same_month(document.uploaded_at, now)
        ),
        total_indexed=sum(1 for document in documents if document.status == FileStatus.INDEXED),
        indexed_this_month=sum(
            1
            for document in documents
            if document.indexed_at is not None and _same_month(document.indexed_at, now)
        ),
        searchable_rows=searchable_rows,
    )


def _recent_activities(documents: list[DocumentSummary], limit: int = 5) -> list[DashboardActivity]:
    """索引日時またはアップロード日時の新しい順で最近の活動を返す。"""
    activities = [
        DashboardActivity(
            id=document.id,
            type="INDEXING" if document.status == FileStatus.INDEXED else "UPLOAD",
            file_name=document.file_name,
            timestamp=document.indexed_at or document.uploaded_at,
            status=document.status,
            category_name=document.category_name,
        )
        for document in documents
    ]
    return sorted(activities, key=lambda activity: activity.timestamp, reverse=True)[:limit]


def _same_month(left: datetime, right: datetime) -> bool:
    """timezone aware/naive の差を吸収して年月を比較する。"""
    left_utc = left.astimezone(UTC) if left.tzinfo else left.replace(tzinfo=UTC)
    right_utc = right.astimezone(UTC) if right.tzinfo else right.replace(tzinfo=UTC)
    return left_utc.year == right_utc.year and left_utc.month == right_utc.month
