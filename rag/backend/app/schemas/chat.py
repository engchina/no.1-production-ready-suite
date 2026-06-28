"""チャット(会話 / マルチモデル比較)関連スキーマ。

会話は業務ビュー(Business View)配下に置く。検索・回答は既存 RAG パイプラインを
再利用し、ASSISTANT メッセージは生成モデル・引用・trace を保持する。
"""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator

from app.schemas.search import RetrievedChunk, SearchMode


class ConversationStatus(StrEnum):
    """会話の状態。"""

    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"


class MessageRole(StrEnum):
    """メッセージの役割。"""

    USER = "USER"
    ASSISTANT = "ASSISTANT"
    SYSTEM = "SYSTEM"


class MessageStatus(StrEnum):
    """メッセージの確定状態。"""

    STREAMING = "STREAMING"
    COMPLETE = "COMPLETE"
    ERROR = "ERROR"


class ChatMessage(BaseModel):
    """会話中の 1 メッセージ。ASSISTANT は引用・モデル・trace を含む。"""

    message_id: str
    conversation_id: str
    role: MessageRole
    content: str
    model: str | None = None
    citations: list[RetrievedChunk] = Field(default_factory=list)
    guardrail_warnings: list[str] = Field(default_factory=list)
    trace_id: str | None = None
    status: MessageStatus = MessageStatus.COMPLETE
    reply_to_message_id: str | None = None
    created_at: datetime


class ConversationSummary(BaseModel):
    """会話一覧用の要約。"""

    id: str
    business_view_id: str
    title: str | None = None
    status: ConversationStatus = ConversationStatus.ACTIVE
    message_count: int = 0
    created_at: datetime
    updated_at: datetime


class ConversationDetail(ConversationSummary):
    """会話詳細。メッセージ列を同梱する。"""

    messages: list[ChatMessage] = Field(default_factory=list)


class ConversationCreateRequest(BaseModel):
    """会話作成リクエスト。"""

    business_view_id: str = Field(..., min_length=1, max_length=128)
    title: str | None = Field(default=None, max_length=400)

    @field_validator("business_view_id")
    @classmethod
    def strip_business_view_id(cls, value: str) -> str:
        """業務ビュー ID の前後空白を除去する。"""
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("業務ビューを指定してください。")
        return cleaned

    @field_validator("title")
    @classmethod
    def strip_title(cls, value: str | None) -> str | None:
        """空文字のタイトルは未指定として扱う。"""
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None


class ChatMessageRequest(BaseModel):
    """会話へのメッセージ送信リクエスト。"""

    content: str = Field(..., min_length=1, max_length=8000)
    # 複数指定でマルチモデル比較(設定済み OCI モデル間)。空なら既定モデル 1 系統。
    model_ids: list[str] = Field(default_factory=list, max_length=5)
    mode: SearchMode = SearchMode.HYBRID
    top_k: int = Field(default=20, ge=1, le=100)

    @field_validator("content")
    @classmethod
    def strip_content(cls, value: str) -> str:
        """本文の前後空白を除去し、空入力を拒否する。"""
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("メッセージを入力してください。")
        return cleaned

    @field_validator("model_ids")
    @classmethod
    def dedupe_model_ids(cls, values: list[str]) -> list[str]:
        """モデル ID の前後空白と重複を取り除く。"""
        seen: set[str] = set()
        normalized: list[str] = []
        for value in values:
            cleaned = value.strip()
            if not cleaned or cleaned in seen:
                continue
            seen.add(cleaned)
            normalized.append(cleaned)
        return normalized
