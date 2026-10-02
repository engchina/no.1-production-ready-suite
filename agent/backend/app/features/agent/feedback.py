"""チャットの回答へのフィードバックの集計と一覧（#774）。

Run に保存した評価（`RunFeedback`）を、期間（評価の更新日時）・業務 Agent で絞って集計し、
評価・理由でさらに絞った一覧を新しい順に返す。対象の Run（利用できる業務 Agent の Run だけ）は
呼び出し側が渡す。集計は RAG の「フィードバック集計」と同じく、前の同じ長さの期間と比べる。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from datetime import datetime, timedelta

from pydantic import BaseModel, Field

from app.features.agent.runtime import (
    FeedbackRating,
    FeedbackReason,
    RunFeedback,
    RunState,
    run_answer_text,
)

# 画面で選べる期間（日）。
FEEDBACK_PERIOD_DAYS: tuple[int, ...] = (7, 30, 90)
# 一覧に返す上限（新しい順）。集計はすべての評価で数える。
FEEDBACK_ITEMS_LIMIT = 500


class ReasonCount(BaseModel):
    reason: FeedbackReason
    count: int


class FeedbackSummary(BaseModel):
    """利用者（会話の本人）の評価の集計と、管理者の評価の件数。"""

    total: int = 0
    helpful: int = 0
    not_helpful: int = 0
    # 評価が 0 件のときは None（画面は「—」）。
    helpful_rate: float | None = None
    reason_counts: list[ReasonCount] = Field(default_factory=list)
    # 管理者の評価（#774）。
    admin_reviewed: int = 0
    admin_not_helpful: int = 0


class FeedbackItem(BaseModel):
    run_id: str
    thread_id: str | None
    agent_id: str
    agent_name: str
    # 会話の本人（Run の作成者）。
    user_uuid: str | None
    display_name: str
    question: str
    answer: str
    # 本人の評価・管理者の評価（どちらかは必ずある）。
    feedback: RunFeedback | None = None
    admin_review: RunFeedback | None = None
    reviewer_display_name: str = ""
    # 新しい方の評価の日時。
    updated_at: datetime


class FeedbackReport(BaseModel):
    days: int
    since: datetime
    until: datetime
    summary: FeedbackSummary
    # 直前の同じ長さの期間（増減の比較に使う）。
    previous: FeedbackSummary
    items: list[FeedbackItem] = Field(default_factory=list)
    # 絞り込みに合う評価の件数（`items` は上限までの新しい順）。
    matched: int = 0


def _rated_at(run: RunState) -> datetime | None:
    times = [item.updated_at for item in (run.feedback, run.admin_review) if item is not None]
    return max(times) if times else None


def _matches(run: RunState, rating: FeedbackRating | None, reason: FeedbackReason | None) -> bool:
    """評価・理由の絞り込み（本人の評価か管理者の評価のどちらかが合えばよい）。"""
    return any(
        item is not None
        and (rating is None or item.rating == rating)
        and (reason is None or item.reason == reason)
        for item in (run.feedback, run.admin_review)
    )


def build_feedback_report(
    runs: Iterable[RunState],
    *,
    days: int,
    now: datetime,
    agent_id: str | None,
    rating: FeedbackRating | None,
    reason: FeedbackReason | None,
    agent_names: Mapping[str, str],
    user_names: Callable[[list[str]], Mapping[str, str]],
) -> FeedbackReport:
    since = now - timedelta(days=days)
    previous_since = since - timedelta(days=days)
    current: list[RunState] = []
    previous: list[RunState] = []
    for run in runs:
        rated_at = _rated_at(run)
        if rated_at is None or (agent_id and run.agent_id != agent_id):
            continue
        if since <= rated_at <= now:
            current.append(run)
        elif previous_since <= rated_at < since:
            previous.append(run)

    matched = [run for run in current if _matches(run, rating, reason)]
    matched.sort(key=lambda run: _rated_at(run) or now, reverse=True)
    listed = matched[:FEEDBACK_ITEMS_LIMIT]
    people = {run.created_by_user_uuid for run in listed if run.created_by_user_uuid} | {
        run.admin_review.user_uuid
        for run in listed
        if run.admin_review is not None and run.admin_review.user_uuid
    }
    names = user_names(sorted(people))
    return FeedbackReport(
        days=days,
        since=since,
        until=now,
        summary=_summarize(current),
        previous=_summarize(previous),
        items=[_item(run, agent_names, names, now) for run in listed],
        matched=len(matched),
    )


def _summarize(runs: list[RunState]) -> FeedbackSummary:
    summary = FeedbackSummary()
    reasons: dict[FeedbackReason, int] = {}
    for run in runs:
        review = run.admin_review
        if review is not None:
            summary.admin_reviewed += 1
            if review.rating == FeedbackRating.NOT_HELPFUL:
                summary.admin_not_helpful += 1
        feedback = run.feedback
        if feedback is None:
            continue
        summary.total += 1
        if feedback.rating == FeedbackRating.HELPFUL:
            summary.helpful += 1
        else:
            summary.not_helpful += 1
            if feedback.reason is not None:
                reasons[feedback.reason] = reasons.get(feedback.reason, 0) + 1
    if summary.total:
        summary.helpful_rate = summary.helpful / summary.total
    # 件数の多い順（同じなら理由の定義順）。
    order = list(FeedbackReason)
    ranked = sorted(reasons.items(), key=lambda pair: (-pair[1], order.index(pair[0])))
    summary.reason_counts = [ReasonCount(reason=item, count=count) for item, count in ranked]
    return summary


def _item(
    run: RunState,
    agent_names: Mapping[str, str],
    names: Mapping[str, str],
    now: datetime,
) -> FeedbackItem:
    owner = run.created_by_user_uuid
    review = run.admin_review
    reviewer = review.user_uuid if review is not None else None
    return FeedbackItem(
        run_id=run.id,
        thread_id=run.thread_id,
        agent_id=run.agent_id,
        agent_name=agent_names.get(run.agent_id, ""),
        user_uuid=owner,
        display_name=names.get(owner, "") if owner else "",
        question=run.goal,
        answer=run_answer_text(run) or "",
        feedback=run.feedback,
        admin_review=review,
        reviewer_display_name=names.get(reviewer, "") if reviewer else "",
        updated_at=_rated_at(run) or now,
    )
