"""回答・引用フィードバック API。"""

from collections.abc import Mapping, Sequence
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Query, Request

from app.clients.oracle import OracleClient
from app.rag.rate_limit import enforce_rate_limit
from app.schemas.common import ApiResponse, Page
from app.schemas.feedback import (
    CurrentFeedbackItem,
    FeedbackCitationSnapshot,
    FeedbackContentSource,
    FeedbackDashboard,
    FeedbackDetail,
    FeedbackItem,
    FeedbackRating,
    FeedbackReason,
    FeedbackReasonCount,
    FeedbackRequest,
    FeedbackSortOrder,
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
    details = await _resolve_feedback_details(oracle, request)
    payload = request.model_dump(
        mode="json",
        exclude={"message_id", "content_snapshot", "comment"},
    )
    payload["comment_hash"] = request.comment_hash
    payload["comment_chars"] = request.comment_chars
    feedback_id = await oracle.save_feedback(payload, details=details)
    return ApiResponse(
        data=FeedbackSubmissionResponse(
            feedback_id=feedback_id,
            trace_id=request.trace_id,
            business_view_id=request.business_view_id,
            target_type=request.target_type,
            source_surface=request.source_surface,
            document_id=request.document_id,
            chunk_id=request.chunk_id,
            message_id=request.message_id,
            rating=request.rating,
            reason=request.reason,
            comment=request.comment,
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
    q: str | None = Query(default=None, max_length=200),
    sort_order: FeedbackSortOrder = FeedbackSortOrder.NEWEST,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> ApiResponse[FeedbackDashboard]:
    """管理者向けに有効な最新票を集計・一覧表示する。"""
    _require_feedback_admin(http_request)
    rows, total, groups, previous_groups = await OracleClient().list_feedback_dashboard_rows(
        business_view_id=business_view_id,
        target_type=target_type.value if target_type else None,
        rating=rating.value if rating else None,
        reason=reason.value if reason else None,
        period_days=period_days,
        search_query=q.strip() if q and q.strip() else None,
        sort_order=sort_order.value,
        limit=limit,
        offset=offset,
    )
    items = [FeedbackItem.model_validate(row) for row in rows]
    return ApiResponse(
        data=FeedbackDashboard(
            summary=_feedback_summary(groups),
            previous_summary=(
                _feedback_summary(previous_groups) if period_days is not None else None
            ),
            items=Page(
                items=items,
                total=total,
                limit=limit,
                offset=offset,
                has_next=offset + limit < total,
            ),
        )
    )


@router.get("/{feedback_id}", response_model=ApiResponse[FeedbackDetail])
async def get_feedback_detail(
    http_request: Request,
    feedback_id: str,
) -> ApiResponse[FeedbackDetail]:
    """管理者向けに feedback の本文・根拠・実行診断を返す。"""
    _require_feedback_admin(http_request)
    cleaned_id = feedback_id.strip()
    if not cleaned_id or len(cleaned_id) > 64:
        raise HTTPException(status_code=404, detail="フィードバックが見つかりません。")
    row = await OracleClient().get_feedback_detail(cleaned_id)
    if row is None:
        raise HTTPException(status_code=404, detail="フィードバックが見つかりません。")
    return ApiResponse(data=FeedbackDetail.model_validate(row))


async def _resolve_feedback_details(
    oracle: OracleClient,
    request: FeedbackRequest,
) -> dict[str, object] | None:
    """chat は server record、検索は検証済み trace の画面 snapshot を保存する。"""
    details: dict[str, object] | None = None
    if request.message_id:
        details = await oracle.get_feedback_message_context(request.message_id, request.trace_id)
        if details is None:
            raise HTTPException(status_code=404, detail="評価対象のメッセージが見つかりません。")
        details["citations"] = _feedback_citations(details.get("citations"))
    elif request.content_snapshot is not None:
        if not await oracle.feedback_trace_exists(request.trace_id):
            raise HTTPException(status_code=404, detail="評価対象の検索結果が見つかりません。")
        details = {
            "message_id": None,
            "content_source": FeedbackContentSource.SEARCH_SNAPSHOT.value,
            "question_text": request.content_snapshot.question,
            "answer_text": request.content_snapshot.answer,
            "citations": [
                citation.model_dump(mode="json") for citation in request.content_snapshot.citations
            ],
        }
    elif request.comment is not None:
        details = {
            "message_id": None,
            "content_source": (
                FeedbackContentSource.CHAT_MESSAGE.value
                if request.source_surface.value == "chat"
                else FeedbackContentSource.SEARCH_SNAPSHOT.value
            ),
            "question_text": None,
            "answer_text": None,
            "citations": [],
        }

    if details is not None:
        details["comment_text"] = request.comment
    return details


def _feedback_citations(value: object) -> list[dict[str, object]]:
    """StoredMessage の RetrievedChunk JSON を小さな feedback snapshot へ落とす。"""
    if not isinstance(value, Sequence) or isinstance(value, str | bytes | bytearray):
        return []
    snapshots: list[dict[str, object]] = []
    for item in value[:50]:
        if not isinstance(item, Mapping):
            continue
        metadata = item.get("metadata")
        metadata_map = metadata if isinstance(metadata, Mapping) else {}
        raw = {
            "document_id": item.get("document_id"),
            "chunk_id": item.get("chunk_id"),
            "file_name": item.get("file_name") or metadata_map.get("file_name"),
            "section_title": metadata_map.get("section_title"),
            "page_number": metadata_map.get("page_number") or metadata_map.get("page"),
            "content_preview": str(item.get("text") or item.get("content") or "")[:2000] or None,
            "rerank_score": item.get("rerank_score"),
        }
        try:
            snapshots.append(FeedbackCitationSnapshot.model_validate(raw).model_dump(mode="json"))
        except ValueError:
            continue
    return snapshots


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
