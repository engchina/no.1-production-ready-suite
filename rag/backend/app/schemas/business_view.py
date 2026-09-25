"""業務ビュー(Business View)関連スキーマ。

KB が「文書をどう加工して索引するか(加工する側視点)」を司るのに対し、業務ビューは
「どの KB 群を、どんな検索/生成方針・persona で束ねて回答するか(利用する側視点)」を司る。
"""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator

from app.rag.business_view_config import BusinessViewConfig
from app.schemas.knowledge_base import KnowledgeBaseRef

DEFAULT_BUSINESS_VIEW_NAME = "DEFAULT"


class BusinessViewStatus(StrEnum):
    """業務ビューの運用状態。"""

    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"


class BusinessViewRef(BaseModel):
    """他スキーマへ埋め込む軽量な業務ビュー参照。"""

    id: str
    name: str


class BusinessViewSummary(BusinessViewRef):
    """一覧表示用の業務ビュー要約。"""

    description: str | None = None
    status: BusinessViewStatus
    knowledge_base_count: int = 0
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None = None


class BusinessViewDetail(BusinessViewSummary):
    """詳細表示用の業務ビュー情報。"""

    config: BusinessViewConfig = Field(default_factory=BusinessViewConfig)
    knowledge_bases: list[KnowledgeBaseRef] = Field(
        default_factory=list,
        description="参照 KB の解決済み一覧(存在する KB のみ。名前表示用)。",
    )


class BusinessViewCreateRequest(BaseModel):
    """業務ビュー作成 request。"""

    name: str = Field(..., min_length=1, max_length=256)
    description: str | None = Field(default=None, max_length=2000)
    config: BusinessViewConfig = Field(default_factory=BusinessViewConfig)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        return _business_view_name(value)

    @field_validator("description")
    @classmethod
    def normalize_description(cls, value: str | None) -> str | None:
        return _optional_clean_text(value)


class BusinessViewUpdateRequest(BaseModel):
    """業務ビュー更新 request。指定フィールドのみ更新する。"""

    name: str | None = Field(default=None, min_length=1, max_length=256)
    description: str | None = Field(default=None, max_length=2000)
    config: BusinessViewConfig | None = Field(
        default=None,
        description="指定時は設定一式を置換する。",
    )

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _business_view_name(value)

    @field_validator("description")
    @classmethod
    def normalize_description(cls, value: str | None) -> str | None:
        return _optional_clean_text(value)


def _required_clean_text(value: str, message: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(message)
    return cleaned


def _business_view_name(value: str) -> str:
    cleaned = _required_clean_text(value, "名前を入力してください。")
    if cleaned.casefold() == DEFAULT_BUSINESS_VIEW_NAME.casefold():
        raise ValueError("DEFAULT は予約名のため使用できません。")
    return cleaned


def _optional_clean_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None
