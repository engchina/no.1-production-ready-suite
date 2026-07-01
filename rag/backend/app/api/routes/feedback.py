"""回答・引用フィードバック API。"""

from decimal import Decimal

from fastapi import APIRouter, HTTPException, Query, Request

from app.clients.oracle import OracleClient
from app.rag.rate_limit import enforce_rate_limit
from app.schemas.common import ApiResponse, Page
from app.schemas.feedback import (
    CurrentFeedbackItem,
    FeedbackDashboard,
    FeedbackItem,
    FeedbackRating,
    FeedbackReason,
    FeedbackReasonCount,
    FeedbackRequest,
    FeedbackSubmissionResponse,
    FeedbackSummary,
    FeedbackTargetType,
)

router = APIRouter()
FEEDBACK_ADMIN_ROLES = {"ADMIN", "LOCAL"}


@router.post("", response_model=ApiResponse[FeedbackSubmissionResponse])
async def submit_feedback(
    http_request: Request,
    request: FeedbackRequest,
) -> ApiResponse[FeedbackSubmissionResponse]:
    """回答または引用 feedback を追記する。"""
    enforce_rate_limit("search", http_request)
    oracle = OracleClient()
    if await oracle.get_business_view(request.business_view_id) is None:
        raise HTTPException(status_code=404, detail="業務ビューが見つかりません。")
    feedback_id = await oracle.save_feedback(request.model_dump(mode="json"))
    return ApiResponse(
        data=FeedbackSubmissionResponse(
            feedback_id=feedback_id,
            **request.model_dump(),
        )
    )


@router.get("/current", response_model=ApiResponse[list[CurrentFeedbackItem]])
async def current_feedback(
    trace_id: str = Query(..., min_length=1, max_length=64),
) -> ApiResponse[list[CurrentFeedbackItem]]:
    """現在の利用者・trace の最新評価を返す。"""
    rows = await OracleClient().list_current_feedback(trace_id.strip())
    return ApiResponse(data=[CurrentFeedbackItem.model_validate(row) for row in rows])


@router.get("", response_model=ApiResponse[FeedbackDashboard])
async def list_feedback(
    http_request: Request,
    business_view_id: str | None = Query(default=None, min_length=1, max_length=64),
    target_type: FeedbackTargetType | None = None,
    rating: FeedbackRating | None = None,
    reason: FeedbackReason | None = None,
    period_days: int | None = Query(default=30, ge=1, le=3650),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> ApiResponse[FeedbackDashboard]:
    """管理者向けに有効な最新票を集計・一覧表示する。"""
    _require_feedback_admin(http_request)
    rows, total, groups = await OracleClient().list_feedback_dashboard_rows(
        business_view_id=business_view_id,
        target_type=target_type.value if target_type else None,
        rating=rating.value if rating else None,
        reason=reason.value if reason else None,
        period_days=period_days,
        limit=limit,
        offset=offset,
    )
    items = [FeedbackItem.model_validate(row) for row in rows]
    return ApiResponse(
        data=FeedbackDashboard(
            summary=_feedback_summary(groups),
            items=Page(
                items=items,
                total=total,
                limit=limit,
                offset=offset,
                has_next=offset + limit < total,
            ),
        )
    )


def _require_feedback_admin(request: Request) -> None:
    session = getattr(request.state, "auth_session", None)
    role = str(getattr(session, "role", "")).upper()
    if role not in FEEDBACK_ADMIN_ROLES:
        raise HTTPException(
            status_code=403,
            detail="フィードバック一覧を表示する権限がありません。",
        )


def _feedback_summary(groups: list[dict[str, object]]) -> FeedbackSummary:
    total = helpful = answer_total = answer_helpful = citation_total = citation_helpful = 0
    reasons: dict[FeedbackReason, int] = {}
    for group in groups:
        raw_count = group.get("item_count")
        count = int(raw_count) if isinstance(raw_count, int | float | str | Decimal) else 0
        target = str(group.get("target_type") or "")
        rating = str(group.get("rating") or "")
        total += count
        if rating == FeedbackRating.HELPFUL:
            helpful += count
        if target == FeedbackTargetType.ANSWER:
            answer_total += count
            if rating == FeedbackRating.HELPFUL:
                answer_helpful += count
        elif target == FeedbackTargetType.CITATION:
            citation_total += count
            if rating == FeedbackRating.HELPFUL:
                citation_helpful += count
        raw_reason = group.get("reason")
        if raw_reason:
            reason_key = FeedbackReason(str(raw_reason))
            reasons[reason_key] = reasons.get(reason_key, 0) + count

    return FeedbackSummary(
        total=total,
        helpful_count=helpful,
        not_helpful_count=total - helpful,
        helpful_rate=_rate(helpful, total),
        answer_total=answer_total,
        answer_helpful_rate=_rate(answer_helpful, answer_total),
        citation_total=citation_total,
        citation_helpful_rate=_rate(citation_helpful, citation_total),
        reason_counts=[
            FeedbackReasonCount(reason=reason, count=count)
            for reason, count in sorted(reasons.items(), key=lambda item: (-item[1], item[0].value))
        ],
    )


def _rate(helpful: int, total: int) -> float:
    return round(helpful / total, 4) if total else 0.0
