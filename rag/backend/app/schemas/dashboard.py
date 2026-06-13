"""ダッシュボード関連スキーマ。"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.document import FileStatus

ActivityType = Literal["UPLOAD", "REGISTRATION"]
SystemStatus = Literal["online", "degraded", "offline"]


class DashboardStats(BaseModel):
    """ダッシュボード上部の集計値。"""

    total_uploads: int = 0
    uploads_this_month: int = 0
    total_registrations: int = 0
    registrations_this_month: int = 0
    total_categories: int = 0
    active_categories: int = 0
    searchable_rows: int = 0


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
    adapter: str
    searchable_rows: int = 0
    checks: dict[str, str] = Field(default_factory=dict)


class DashboardSummary(BaseModel):
    """ダッシュボード初期表示に必要な情報。"""

    stats: DashboardStats
    recent_activities: list[DashboardActivity] = Field(default_factory=list)
    system: DashboardSystemInfo
