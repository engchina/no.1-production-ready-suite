"""業務ビュー(Business View)関連スキーマ。

KB が「文書をどう加工して索引するか(加工する側視点)」を司るのに対し、業務ビューは
「どの KB 群を、どんな検索/生成方針・persona で束ねて回答するか(利用する側視点)」を司る。
"""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator

from app.rag.business_view_config import BusinessViewConfig
from app.schemas.knowledge_base import (
    DESCRIPTION_REQUIRED_MESSAGE,
    KnowledgeBaseRef,
    KnowledgeBaseStatus,
)

DEFAULT_BUSINESS_VIEW_NAME = "DEFAULT"
# DEFAULT は改名できないため、説明は既定の文言を補う（作成・読み込み時と migration。#521）。
DEFAULT_BUSINESS_VIEW_DESCRIPTION = (
    "DEFAULT ナレッジベースを検索・回答に使う、既定の業務ビューです。"
)


class BusinessViewStatus(StrEnum):
    """業務ビューの運用状態。"""

    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"


class BusinessViewRef(BaseModel):
    """他スキーマへ埋め込む軽量な業務ビュー参照。"""

    id: str
    name: str


class BusinessViewKnowledgeBaseRef(KnowledgeBaseRef):
    """業務ビューが参照する KB の {id, name, status}(アーカイブ済みを含む)。"""

    status: KnowledgeBaseStatus


class BusinessViewSummary(BusinessViewRef):
    """一覧表示用の業務ビュー要約。"""

    description: str | None = None
    status: BusinessViewStatus
    knowledge_base_count: int = 0
    archived_knowledge_base_count: int = Field(
        default=0,
        description="参照 KB のうちアーカイブ済みの件数(検索対象にならない。#302)。",
    )
    missing_knowledge_base_count: int = Field(
        default=0,
        description="参照 KB のうち tenant に存在しない件数(検索対象にならない。#302)。",
    )
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None = None


class BusinessViewDetail(BusinessViewSummary):
    """詳細表示用の業務ビュー情報。"""

    config: BusinessViewConfig = Field(default_factory=BusinessViewConfig)
    knowledge_bases: list[BusinessViewKnowledgeBaseRef] = Field(
        default_factory=list,
        description=(
            "参照 KB の解決済み一覧(存在する KB のみ。アーカイブ済みを含み、status で見分ける)。"
        ),
    )
    missing_knowledge_base_ids: list[str] = Field(
        default_factory=list,
        description="参照 KB のうち tenant に存在しない ID(#302)。",
    )


class BusinessViewCreateRequest(BaseModel):
    """業務ビュー作成 request。"""

    name: str = Field(..., min_length=1, max_length=256)
    description: str = Field(..., max_length=2000)
    config: BusinessViewConfig = Field(default_factory=BusinessViewConfig)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        return _business_view_name(value)

    @field_validator("description")
    @classmethod
    def normalize_description(cls, value: str) -> str:
        """前後空白を取り、空の説明を拒否する（#521）。"""
        return _required_clean_text(value, DESCRIPTION_REQUIRED_MESSAGE)


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
    def normalize_description(cls, value: str | None) -> str:
        """指定したときは空・空白だけ・null を拒否する。省略すれば変更しない（#521）。"""
        return _required_clean_text(value or "", DESCRIPTION_REQUIRED_MESSAGE)


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
