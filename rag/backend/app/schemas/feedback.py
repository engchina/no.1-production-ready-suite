"""利用者フィードバックの低機密スキーマ。"""

import hashlib
from datetime import datetime
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, Field, field_validator, model_validator

from app.schemas.common import Page


class FeedbackRating(StrEnum):
    """フィードバック評価。"""

    HELPFUL = "helpful"
    NOT_HELPFUL = "not_helpful"


class FeedbackReason(StrEnum):
    """改善箇所を特定する低機密理由カテゴリ。"""

    INCORRECT = "incorrect"
    INCOMPLETE = "incomplete"
    MISSING_EVIDENCE = "missing_evidence"
    NOT_RELEVANT = "not_relevant"
    ANSWER_UNTRUSTED = "answer_untrusted"


class FeedbackTargetType(StrEnum):
    """評価対象。"""

    ANSWER = "answer"
    CITATION = "citation"


class FeedbackSourceSurface(StrEnum):
    """フィードバックを送信した画面。"""

    SEARCH = "search"
    CHAT = "chat"


ANSWER_REASONS = {
    FeedbackReason.INCORRECT,
    FeedbackReason.INCOMPLETE,
    FeedbackReason.NOT_RELEVANT,
    FeedbackReason.ANSWER_UNTRUSTED,
}
CITATION_REASONS = {
    FeedbackReason.MISSING_EVIDENCE,
    FeedbackReason.NOT_RELEVANT,
    FeedbackReason.ANSWER_UNTRUSTED,
}


def _clean_identifier(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    if any(ord(character) < 32 or ord(character) == 127 for character in cleaned):
        raise ValueError("ID に制御文字は使用できません。")
    return cleaned


class FeedbackRequest(BaseModel):
    """回答または引用に対するフィードバック登録。"""

    trace_id: str = Field(..., min_length=1, max_length=64)
    business_view_id: str = Field(..., min_length=1, max_length=64)
    target_type: FeedbackTargetType
    source_surface: FeedbackSourceSurface
    document_id: str | None = Field(default=None, max_length=64)
    chunk_id: str | None = Field(default=None, max_length=128)
    rating: FeedbackRating
    reason: FeedbackReason | None = None

    @field_validator("trace_id", "business_view_id", "document_id", "chunk_id")
    @classmethod
    def validate_identifier(cls, value: str | None) -> str | None:
        return _clean_identifier(value)

    @model_validator(mode="after")
    def validate_target_and_reason(self) -> Self:
        if self.target_type == FeedbackTargetType.CITATION:
            if not self.document_id or not self.chunk_id:
                raise ValueError("引用フィードバックには文書 ID とチャンク ID が必要です。")
            allowed_reasons = CITATION_REASONS
        else:
            if self.document_id is not None or self.chunk_id is not None:
                raise ValueError("回答フィードバックには文書 ID とチャンク ID を指定できません。")
            allowed_reasons = ANSWER_REASONS

        if self.rating == FeedbackRating.HELPFUL:
            self.reason = None
        elif self.reason is None:
            raise ValueError("役に立たなかった理由を選択してください。")
        elif self.reason not in allowed_reasons:
            raise ValueError("評価対象に対応していない理由です。")
        return self


class FeedbackSubmissionResponse(BaseModel):
    """フィードバック登録結果。"""

    feedback_id: str
    trace_id: str
    business_view_id: str | None = None
    target_type: FeedbackTargetType
    source_surface: FeedbackSourceSurface | None = None
    document_id: str | None = None
    chunk_id: str | None = None
    rating: FeedbackRating
    reason: FeedbackReason | None = None


class CurrentFeedbackItem(FeedbackSubmissionResponse):
    """現在の利用者について有効な最新フィードバック。"""

    created_at: datetime


class FeedbackReasonCount(BaseModel):
    """低評価理由別の件数。"""

    reason: FeedbackReason
    count: int = Field(ge=0)


class FeedbackSummary(BaseModel):
    """有効票の集計。"""

    total: int = 0
    helpful_count: int = 0
    not_helpful_count: int = 0
    helpful_rate: float = 0.0
    answer_total: int = 0
    answer_helpful_rate: float = 0.0
    citation_total: int = 0
    citation_helpful_rate: float = 0.0
    reason_counts: list[FeedbackReasonCount] = Field(default_factory=list)


class FeedbackItem(CurrentFeedbackItem):
    """管理画面に表示するフィードバック明細。"""

    business_view_name: str | None = None
    conversation_id: str | None = None
    conversation_title: str | None = None
    message_id: str | None = None
    model: str | None = None
    file_name: str | None = None


class FeedbackDashboard(BaseModel):
    """専用画面向けの集計とページング済み明細。"""

    summary: FeedbackSummary
    items: Page[FeedbackItem]


# 旧 API の互換型。新 UI は FeedbackRequest を使い、自由記述を送信しない。
CitationFeedbackRating = FeedbackRating
CitationFeedbackReason = FeedbackReason


class CitationFeedbackRequest(BaseModel):
    """旧引用フィードバック API の互換リクエスト。"""

    trace_id: str = Field(..., min_length=1, max_length=64)
    document_id: str = Field(..., min_length=1, max_length=64)
    chunk_id: str = Field(..., min_length=1, max_length=128)
    rating: FeedbackRating
    reason: FeedbackReason | None = None
    comment: str | None = Field(default=None, max_length=1000)

    @field_validator("trace_id", "document_id", "chunk_id")
    @classmethod
    def validate_identifier(cls, value: str) -> str:
        return _clean_identifier(value) or ""

    @field_validator("comment")
    @classmethod
    def normalize_comment(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = " ".join(value.split())
        return cleaned or None

    @model_validator(mode="after")
    def validate_reason(self) -> Self:
        if self.rating == FeedbackRating.HELPFUL:
            self.reason = None
        elif self.reason not in CITATION_REASONS:
            raise ValueError("引用に対応した理由を選択してください。")
        return self

    @property
    def comment_hash(self) -> str | None:
        if self.comment is None:
            return None
        return hashlib.sha256(self.comment.encode("utf-8")).hexdigest()

    @property
    def comment_chars(self) -> int:
        return len(self.comment or "")


class CitationFeedbackResponse(BaseModel):
    """旧引用フィードバック API の互換レスポンス。"""

    feedback_id: str
    trace_id: str
    document_id: str
    chunk_id: str
    rating: FeedbackRating
