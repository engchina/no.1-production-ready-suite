"""実行履歴 API（チャット・RAG 検索の実行の一覧と詳細。#1890）。

見え方は #1889・#1954 の規則（system_admin は全員の実行。それ以外は、自分が実行した実行と、自分の
会話で他の人〔system_admin〕が実行した実行）。一覧・件数・詳細は Oracle の SQL の条件で同じ範囲に
絞る（`RunHistoryStore`）。範囲の外の実行の詳細は 404。一覧は「実行者」（すべて / 自分が実行 /
他の人が実行）で絞れる。実行した人の表示名（「実行者」）は見る人を問わず返す。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pr_backend_core.api import DEFAULT_PAGE_LIMIT, OffsetParams, offset_params, paginate
from pr_system_settings.auth.visibility import DEFAULT_RUN_EXECUTOR_FILTER, RunExecutorFilter
from starlette.concurrency import run_in_threadpool

from app.api.routes.search import _stored_citation_rows
from app.clients.oracle import OracleClient
from app.clients.oracle_run_history import RunHistoryStore, owner_user_uuids
from app.rag.request_context import current_audit_request_context
from app.schemas.common import ApiResponse, Page
from app.schemas.history import RunDetail, RunStatus, RunSummary, RunSurface
from app.schemas.search import RetrievedChunk
from app.security.user_names import display_names_by_user_hash, user_display_names

router = APIRouter()

RUN_NOT_FOUND_MESSAGE = (
    "実行が見つかりません。保持期間を過ぎて削除されたか、表示する権限がありません。"
)


_INTERNAL_FIELDS = {"user_id_hash", "user_uuid", "conversation_owner_hash"}


def _summary_fields(row: dict[str, Any], names: dict[str, str]) -> dict[str, Any]:
    """Oracle の行を一覧の項目にする（利用者の hash・UUID は返さない）。"""
    context = current_audit_request_context()
    user_uuid = row.get("user_uuid")
    fields = {key: value for key, value in row.items() if key not in _INTERNAL_FIELDS}
    fields["answer_generated"] = bool(row.get("answer_generated"))
    review = row.get("needs_human_review")
    fields["needs_human_review"] = None if review is None else bool(review)
    fields["executed_by_me"] = (
        context.user_id_hash is not None and row.get("user_id_hash") == context.user_id_hash
    )
    # 実行した人（実行者）の表示名は、見る人を問わず返す（#1954。持ち主の会話で system_admin が
    # 実行した行を、持ち主が「〈名前〉が実行」と読む）。引けない利用者は空文字。
    fields["executor_display_name"] = names.get(str(user_uuid), "") if user_uuid else ""
    return fields


async def _executor_names(rows: list[dict[str, Any]]) -> dict[str, str]:
    """実行した利用者の UUID → 表示名（一覧の「実行者」。#1954）。"""
    uuids = owner_user_uuids(rows)
    if not uuids:
        return {}
    return await run_in_threadpool(user_display_names, uuids)


@router.get("/runs", response_model=ApiResponse[Page[RunSummary]])
async def list_runs(
    paging: Annotated[
        OffsetParams, Depends(offset_params(default=DEFAULT_PAGE_LIMIT, max_limit=100))
    ],
    q: str | None = Query(default=None, max_length=200),
    surface: RunSurface | None = None,
    status: RunStatus | None = None,
    executor: RunExecutorFilter = DEFAULT_RUN_EXECUTOR_FILTER,
    # 開始日時がこの時刻以後の実行だけ（ISO 8601。期間の絞り込み。#1955）。
    since: datetime | None = None,
) -> ApiResponse[Page[RunSummary]]:
    """チャットと RAG 検索の実行を新しい順に返す。

    質問・実行 ID の検索・種類・状態・実行者・期間で絞り込み、同じ条件の総件数を付ける（#1955）。
    """
    rows, total = await RunHistoryStore(OracleClient()).list_runs(
        query=q.strip() if q and q.strip() else None,
        surface=surface,
        status=status,
        executor=executor,
        # タイムゾーンの無い時刻は UTC とみなす（画面は `toISOString()` の UTC を送る）。
        since=since if since is None or since.tzinfo else since.replace(tzinfo=UTC),
        limit=paging.limit,
        offset=paging.offset,
    )
    names = await _executor_names(rows)
    items = [RunSummary.model_validate(_summary_fields(row, names)) for row in rows]
    return ApiResponse(data=paginate(items, total=total, limit=paging.limit, offset=paging.offset))


@router.get("/runs/{run_id}", response_model=ApiResponse[RunDetail])
async def get_run(run_id: str) -> ApiResponse[RunDetail]:
    """実行 1 件（質問の全文・回答・根拠・回答の検証・処理の段と時間・フィードバック）。"""
    oracle = OracleClient()
    store = RunHistoryStore(oracle)
    row = await store.get_run(run_id)
    if row is None:
        raise HTTPException(status_code=404, detail=RUN_NOT_FOUND_MESSAGE)
    fields = _summary_fields(row, await _executor_names([row]))
    fields.update(await _conversation_owner_fields(row))
    raw_stages = row.get("stages_json")
    stages: dict[str, Any] = raw_stages if isinstance(raw_stages, dict) else {}
    detail: dict[str, Any] = {
        **{key: value for key, value in fields.items() if not key.endswith("_json")},
        "stages": stages.get("stages") or [],
        "steps": stages.get("steps") or [],
        "citations": _citations(row.get("citations_json")),
    }
    if detail["answer_generated"]:
        # 回答・引用・診断は回答の記録が正本（保持期間で消えていれば出さない）。
        record = await oracle.get_answer_record(run_id)
        if record is not None:
            detail["answer"] = record.get("answer")
            detail["answer_available"] = True
            detail["citations"] = _citations(_stored_citation_rows(record.get("citations_json")))
            diagnostics = record.get("diagnostics_json")
            detail["answer_diagnostics"] = diagnostics if isinstance(diagnostics, dict) else None
    detail["feedback"] = await store.list_run_feedback(run_id)
    return ApiResponse(data=RunDetail.model_validate(detail))


async def _conversation_owner_fields(row: dict[str, Any]) -> dict[str, Any]:
    """詳細の「会話の持ち主」と、見ている利用者が持ち主か（#1954）。

    会話の無い実行（RAG 検索）と、会話が削除された・持ち主の分からない実行は None。
    """
    owner_hash = row.get("conversation_owner_hash")
    if not row.get("conversation_id") or not owner_hash:
        return {"conversation_owner_display_name": None, "owned_by_me": None}
    names = await run_in_threadpool(display_names_by_user_hash, {str(owner_hash)})
    caller = current_audit_request_context().user_id_hash
    return {
        "conversation_owner_display_name": names.get(str(owner_hash), ""),
        "owned_by_me": caller is not None and caller == owner_hash,
    }


def _citations(value: object) -> list[RetrievedChunk]:
    if not isinstance(value, list):
        return []
    return [RetrievedChunk.model_validate(item) for item in value if isinstance(item, dict)]
