"""ダッシュボード API。"""

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


@router.get("/summary", response_model=ApiResponse[DashboardSummary])
async def dashboard_summary() -> ApiResponse[DashboardSummary]:
    """ダッシュボード初期表示用の集計を返す。"""
    settings = get_settings()
    oracle = OracleClient(settings)
    documents = await oracle.list_documents(limit=None)
    searchable_rows = await oracle.count_chunks()
    ingestion_quality = _ingestion_quality(
        documents=documents,
        extractions=await oracle.list_document_extractions(),
        chunk_metadata=await oracle.list_chunk_metadata(),
    )
    checks = readiness_checks(settings)

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
                adapter=settings.ai_service_adapter,
                searchable_rows=searchable_rows,
                checks=checks,
            ),
        )
    )


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
        total_indexed=sum(
            1 for document in documents if document.status == FileStatus.INDEXED
        ),
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
