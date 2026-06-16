"""ユーザー feedback スキーマ。"""

import hashlib
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, Field, field_validator, model_validator


class CitationFeedbackRating(StrEnum):
    """引用 feedback の評価。"""

    HELPFUL = "helpful"
    NOT_HELPFUL = "not_helpful"


class CitationFeedbackReason(StrEnum):
    """引用 feedback の低機密理由カテゴリ。"""

    MISSING_EVIDENCE = "missing_evidence"
    NOT_RELEVANT = "not_relevant"
    ANSWER_UNTRUSTED = "answer_untrusted"


class CitationFeedbackRequest(BaseModel):
    """引用単位の feedback 登録リクエスト。"""

    trace_id: str = Field(..., min_length=1, max_length=64)
    document_id: str = Field(..., min_length=1, max_length=128)
    chunk_id: str = Field(..., min_length=1, max_length=128)
    rating: CitationFeedbackRating
    reason: CitationFeedbackReason | None = None
    comment: str | None = Field(default=None, max_length=1000)

    @field_validator("trace_id", "document_id", "chunk_id")
    @classmethod
    def validate_identifier(cls, value: str) -> str:
        """前後空白や制御文字を拒否し、監査 ID として扱いやすくする。"""
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("ID を入力してください。")
        if any(ord(character) < 32 or ord(character) == 127 for character in cleaned):
            raise ValueError("ID に制御文字は使用できません。")
        return cleaned

    @field_validator("comment")
    @classmethod
    def normalize_comment(cls, value: str | None) -> str | None:
        """自由記述は明文保存せず、hash/文字数算出用に最小正規化する。"""
        if value is None:
            return None
        cleaned = " ".join(value.split())
        return cleaned or None

    @model_validator(mode="after")
    def validate_reason(self) -> Self:
        """有用 feedback は理由なし、問題 feedback はカテゴリ付きに寄せる。"""
        if self.rating == CitationFeedbackRating.HELPFUL:
            self.reason = None
        return self

    @property
    def comment_hash(self) -> str | None:
        """comment 明文を保存せず相関するための SHA-256 hash。"""
        if self.comment is None:
            return None
        return hashlib.sha256(self.comment.encode("utf-8")).hexdigest()

    @property
    def comment_chars(self) -> int:
        """comment の文字数だけ保存する。"""
        return len(self.comment or "")


class CitationFeedbackResponse(BaseModel):
    """引用 feedback 登録結果。"""

    feedback_id: str
    trace_id: str
    document_id: str
    chunk_id: str
    rating: CitationFeedbackRating
