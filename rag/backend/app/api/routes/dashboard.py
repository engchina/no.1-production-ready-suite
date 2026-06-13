"""ダッシュボード API。"""

from datetime import UTC, datetime

from fastapi import APIRouter

from app.api.routes.health import _readiness_checks, readiness_checks_are_ok
from app.clients.oracle import OracleClient
from app.config import get_settings
from app.schemas.category import default_categories
from app.schemas.common import ApiResponse
from app.schemas.dashboard import (
    DashboardActivity,
    DashboardStats,
    DashboardSummary,
    DashboardSystemInfo,
)
from app.schemas.document import DocumentSummary, FileStatus

router = APIRouter()


@router.get("/summary", response_model=ApiResponse[DashboardSummary])
async def dashboard_summary() -> ApiResponse[DashboardSummary]:
    """ダッシュボード初期表示用の集計を返す。"""
    settings = get_settings()
    oracle = OracleClient(settings)
    documents = await oracle.list_documents(limit=None)
    searchable_rows = await oracle.count_chunks()
    checks = _readiness_checks(settings)
    categories = default_categories()

    return ApiResponse(
        data=DashboardSummary(
            stats=_dashboard_stats(
                documents=documents,
                searchable_rows=searchable_rows,
                total_categories=len(categories),
                active_categories=sum(1 for category in categories if category.enabled),
            ),
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


def _dashboard_stats(
    documents: list[DocumentSummary],
    searchable_rows: int,
    total_categories: int,
    active_categories: int,
) -> DashboardStats:
    """DocumentSummary 群からダッシュボード集計を作る。"""
    now = datetime.now(UTC)
    return DashboardStats(
        total_uploads=len(documents),
        uploads_this_month=sum(
            1 for document in documents if _same_month(document.uploaded_at, now)
        ),
        total_registrations=sum(
            1 for document in documents if document.status == FileStatus.REGISTERED
        ),
        registrations_this_month=sum(
            1
            for document in documents
            if document.registered_at is not None and _same_month(document.registered_at, now)
        ),
        total_categories=total_categories,
        active_categories=active_categories,
        searchable_rows=searchable_rows,
    )


def _recent_activities(documents: list[DocumentSummary], limit: int = 5) -> list[DashboardActivity]:
    """登録日時またはアップロード日時の新しい順で最近の活動を返す。"""
    activities = [
        DashboardActivity(
            id=document.id,
            type="REGISTRATION" if document.status == FileStatus.REGISTERED else "UPLOAD",
            file_name=document.file_name,
            timestamp=document.registered_at or document.uploaded_at,
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
