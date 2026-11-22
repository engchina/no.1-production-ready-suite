"""KB ごとの Wiki 構築の指定と、原文保存版に結び付く構築状態。"""

from datetime import datetime
from typing import Literal

from pr_backend_core.schemas import Page
from pydantic import BaseModel, ConfigDict, Field, StrictBool


class KnowledgeBaseWikiUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: StrictBool


class KnowledgeBaseWikiDocument(BaseModel):
    document_id: str
    file_name: str
    status: str
    # 再試行の要る失敗の理由（`timeout` / `build_failed` / `authorization_changed`）。
    failure_code: str | None = None
    updated_at: datetime | None = None
    link_status: str = "NOT_BUILT"
    judged_count: int | None = None
    planned_count: int | None = None
    link_count: int = 0
    link_failure_code: str | None = None
    failure_phase: str | None = None


class KnowledgeBaseWikiData(BaseModel):
    enabled: bool
    # 状態ごとの件数は KB 全体、documents は名前の順の 1 ページ分（#5466）。
    counts: dict[str, int] = Field(default_factory=dict)
    documents: Page[KnowledgeBaseWikiDocument]
    link_counts: dict[str, int] = Field(default_factory=dict)
    link_count: int = 0
    judged_count: int = 0
    planned_count: int | None = None
    # 構築が進まない理由。`actor_unavailable` は構築を指定した利用者が権限を失った（#5466）。
    blocked_reason: Literal["actor_unavailable"] | None = None
