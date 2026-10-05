"""チャット(会話 / マルチモデル比較)関連スキーマ。

会話は検索・回答プロファイル(Search Answer Profile)配下に置く。検索・回答は既存 RAG パイプラインを
再利用し、ASSISTANT メッセージは生成モデル・引用・trace を保持する。
"""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator

from app.schemas.search import RetrievedChunk
from app.schemas.search_answer_profile_knowledge import ClarificationAnswer


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
    """メッセージの確定状態。

    回答は作成を始めた時点で ``STREAMING`` で保存し、終わったら ``COMPLETE``・``ERROR``（失敗・
    中断）・``CANCELLED``（利用者の停止）にする（#1175）。
    """

    STREAMING = "STREAMING"
    COMPLETE = "COMPLETE"
    ERROR = "ERROR"
    CANCELLED = "CANCELLED"


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
    # 回答の処理の段階（3 製品共通の ChatProgressStep。#1146 / #1175）。
    # 作成中（STREAMING）は今の段階、終わった後は処理の経過。段階を保存していないメッセージは None。
    progress: list[dict[str, object]] | None = None


class ChatAnswerCancelResult(BaseModel):
    """回答の作成の取消の結果(#1175)。作成中の回答を止めた・止める予定なら True。"""

    cancelled: bool


class ConversationSummary(BaseModel):
    """会話一覧用の要約。"""

    id: str
    search_answer_profile_id: str
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

    search_answer_profile_id: str = Field(..., min_length=1, max_length=128)
    title: str | None = Field(default=None, max_length=400)

    @field_validator("search_answer_profile_id")
    @classmethod
    def strip_search_answer_profile_id(cls, value: str) -> str:
        """検索・回答プロファイル ID の前後空白を除去する。"""
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("検索・回答プロファイルを指定してください。")
        return cleaned

    @field_validator("title")
    @classmethod
    def strip_title(cls, value: str | None) -> str | None:
        """空文字のタイトルは未指定として扱う。"""
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None


class ConversationUpdateRequest(BaseModel):
    """会話タイトル更新リクエスト。"""

    title: str = Field(..., min_length=1, max_length=80)

    @field_validator("title", mode="before")
    @classmethod
    def strip_title(cls, value: object) -> object:
        """空白だけのタイトルを拒否し、前後空白を除去する。"""
        if not isinstance(value, str):
            return value
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("会話名を入力してください。")
        return cleaned


class ChatMessageRequest(BaseModel):
    """会話へのメッセージ送信リクエスト。"""

    content: str = Field(..., min_length=1, max_length=8000)
    # 複数指定でマルチモデル比較(設定済み OCI モデル間)。空なら既定モデル 1 系統。
    model_ids: list[str] = Field(default_factory=list, max_length=5)
    # 旧 standard の検索モード(``mode``)は #595 で削除した。送られても読み捨てる。
    top_k: int = Field(default=20, ge=1, le=100)
    # 利用者が選んだ類似の承認済み FAQ の id(#684)。b
    # ackend が検索・回答プロファイルの FAQ から引き直し、
    #
    # 質問と一緒に回答の LLM へ渡す。利用者の送った文を FAQ として扱わない。
    approved_faq_id: str | None = Field(default=None, min_length=1, max_length=200)
    # 利用者が確認の質問に答えた内容(#717)。backen
    # d が検索・回答プロファイルのルールから引き直し、選んだ
    #
    # 章節のページに絞って検索し、選んだ条件を回答の前提にする。
    clarification: ClarificationAnswer | None = None
    # 画面が決めた質問（USER のメッセージ）の id（#1175）。送信の直後（`start` の前）の停止でも、
    # この id で回答の作成を取り消せる（NL2SQL の `client_job_id`（#900）と同じ考え方）。
    client_message_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")

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
