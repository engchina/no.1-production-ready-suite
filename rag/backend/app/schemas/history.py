"""実行履歴（チャット・RAG 検索の実行。#1890）の API スキーマ。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.common import JsonValue
from app.schemas.search import RetrievedChunk

RunSurface = Literal["search", "chat"]
RunStatus = Literal["success", "blocked", "error"]
RunVerification = Literal["verified", "partial", "unverified"]


class RunSummary(BaseModel):
    """実行履歴の一覧の行（質問は先頭の一部だけ）。"""

    run_id: str
    surface: RunSurface
    search_answer_profile_id: str | None = None
    # 検索・回答プロファイルの名前（削除・読めないときは None。画面は ID を出さない）。
    search_answer_profile_name: str | None = None
    # 安全チェックの後の質問。安全チェックで止めた質問は保存しないので None。
    question: str | None = None
    # 回答を作った実行か（RAG 検索の既定は回答を作らない。#649）。
    answer_generated: bool
    status: RunStatus
    # 回答の対応（answered / conditional / insufficient_evidence など。#1235）。
    answer_outcome: str | None = None
    # 回答の検証（回答の監査で要求ごとに根拠で答えられたか）。回答を作らない実行は None。
    verification: RunVerification | None = None
    confidence: str | None = None
    needs_human_review: bool | None = None
    citation_count: int = 0
    elapsed_ms: float | None = None
    error_stage: str | None = None
    model_id: str | None = None
    conversation_id: str | None = None
    message_id: str | None = None
    created_at: datetime
    # 見ている利用者自身が実行したか（#1954。持ち主の会話で system_admin が実行した行は、持ち主には
    # false）。
    executed_by_me: bool = False
    # 実行した利用者（実行者）の表示名。見る人を問わず返す（#1954）。引けない利用者（削除済み
    # など）は空文字（画面は「削除済みのユーザー」と出す）。
    executor_display_name: str = ""


class RunStage(BaseModel):
    """処理の段（質問の書き換え・検索・回答・回答側の安全チェック）と時間。"""

    stage: str
    outcome: str
    elapsed_ms: float | None = None


class RunStep(BaseModel):
    """回答フローの中の工程（質問の理解・文書検索・回答の生成など）と時間。"""

    name: str
    status: str = ""
    elapsed_seconds: float | None = None
    llm_calls: int | None = None


class RunFeedback(BaseModel):
    """実行への利用者フィードバック（送った利用者・対象ごとの最新の票）。"""

    feedback_id: str
    target_type: Literal["answer", "citation"]
    rating: Literal["helpful", "not_helpful"]
    reason: str | None = None
    document_id: str | None = None
    created_at: datetime


class RunDetail(RunSummary):
    """実行の詳細（質問の全文・回答・根拠・回答の診断・処理の段・フィードバック）。"""

    error_type: str | None = None
    # 会話の持ち主の表示名（#1954）。会話の無い実行（RAG 検索）・会話が削除された実行は None、
    # 引けない利用者は空文字。
    conversation_owner_display_name: str | None = None
    # 見ている利用者が会話の持ち主か（会話を開く導線は持ち主と system_admin だけ）。会話の無い
    # 実行は None。
    owned_by_me: bool | None = None
    answer: str | None = None
    # 回答の記録が保持期間で消えた・読めないとき False（回答を作った実行だけ意味がある）。
    answer_available: bool = False
    citations: list[RetrievedChunk] = Field(default_factory=list)
    answer_diagnostics: dict[str, JsonValue] | None = None
    stages: list[RunStage] = Field(default_factory=list)
    steps: list[RunStep] = Field(default_factory=list)
    feedback: list[RunFeedback] = Field(default_factory=list)
