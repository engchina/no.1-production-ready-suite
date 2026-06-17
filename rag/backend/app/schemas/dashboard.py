"""ダッシュボード関連スキーマ。"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.document import FileStatus

ActivityType = Literal["UPLOAD", "INDEXING"]
SystemStatus = Literal["online", "degraded", "offline"]


class DashboardStats(BaseModel):
    """ダッシュボード上部の集計値。"""

    total_uploads: int = 0
    uploads_this_month: int = 0
    total_indexed: int = 0
    indexed_this_month: int = 0
    searchable_rows: int = 0


class DashboardIngestionQuality(BaseModel):
    """構造化取込の品質・カバレッジ集計。"""

    document_count: int = 0
    structured_document_count: int = 0
    element_count: int = 0
    table_count: int = 0
    figure_count: int = 0
    formula_count: int = 0
    list_count: int = 0
    page_count: int = 0
    low_confidence_count: int = 0
    fallback_document_count: int = 0
    failed_segment_document_count: int = 0
    segment_artifact_cache_miss_document_count: int = 0
    long_document_count: int = 0
    average_page_coverage: float = Field(default=0.0, ge=0.0, le=1.0)
    risk_counts: dict[str, int] = Field(default_factory=lambda: {"low": 0, "medium": 0, "high": 0})
    parser_profile_counts: dict[str, int] = Field(default_factory=dict)
    parser_backend_counts: dict[str, int] = Field(default_factory=dict)
    warning_counts: dict[str, int] = Field(default_factory=dict)
    chunk_profile_counts: dict[str, int] = Field(default_factory=dict)
    content_kind_counts: dict[str, int] = Field(default_factory=dict)


class DashboardActivity(BaseModel):
    """最近の処理アクティビティ。"""

    id: str
    type: ActivityType
    file_name: str
    timestamp: datetime
    status: FileStatus
    category_name: str | None = None


class DashboardSystemInfo(BaseModel):
    """ダッシュボード用のシステム情報。"""

    status: SystemStatus
    version: str
    searchable_rows: int = 0
    checks: dict[str, str] = Field(default_factory=dict)


class DashboardSummary(BaseModel):
    """ダッシュボード初期表示に必要な情報。"""

    stats: DashboardStats
    ingestion_quality: DashboardIngestionQuality = Field(default_factory=DashboardIngestionQuality)
    recent_activities: list[DashboardActivity] = Field(default_factory=list)
    system: DashboardSystemInfo
