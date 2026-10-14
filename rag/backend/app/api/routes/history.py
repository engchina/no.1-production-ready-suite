"""実行履歴 API（チャット・RAG 検索の実行の一覧と詳細。#1890）。

見え方は #1889 の規則（system_admin は全員の実行、それ以外は自分の実行だけ）。一覧・件数・詳細は
Oracle の SQL の条件で同じ範囲に絞る（`RunHistoryStore`）。範囲の外の実行の詳細は 404。
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pr_backend_core.api import OffsetParams, offset_params, paginate

from app.api.routes.search import _stored_citation_rows
from app.clients.oracle import OracleClient
from app.clients.oracle_run_history import RunHistoryStore, owner_user_uuids
from app.rag.request_context import current_audit_request_context
from app.schemas.common import ApiResponse, Page
from app.schemas.history import RunDetail, RunStatus, RunSummary, RunSurface
from app.schemas.search import RetrievedChunk
from app.security.user_names import user_display_names

router = APIRouter()

RUN_NOT_FOUND_MESSAGE = (
    "実行が見つかりません。保持期間を過ぎて削除されたか、表示する権限がありません。"
)


def _summary_fields(row: dict[str, Any], names: dict[str, str] | None) -> dict[str, Any]:
    """Oracle の行を一覧の項目にする（持ち主の hash は返さない）。"""
    context = current_audit_request_context()
    user_uuid = row.get("user_uuid")
    fields = {key: value for key, value in row.items() if key not in {"user_id_hash", "user_uuid"}}
    fields["answer_generated"] = bool(row.get("answer_generated"))
    review = row.get("needs_human_review")
    fields["needs_human_review"] = None if review is None else bool(review)
    fields["own"] = (
        context.user_id_hash is not None and row.get("user_id_hash") == context.user_id_hash
    )
    # 利用者の名前は全員の実行を見られる利用者（system_admin）にだけ返す（#1889）。
    fields["user_display_name"] = (
        names.get(str(user_uuid), "") if names is not None and user_uuid else None
    )
    return fields


def _names_for(rows: list[dict[str, Any]]) -> dict[str, str] | None:
    if not current_audit_request_context().all_users_visible:
        return None
    return user_display_names(owner_user_uuids(rows))


@router.get("/runs", response_model=ApiResponse[Page[RunSummary]])
async def list_runs(
    paging: Annotated[OffsetParams, Depends(offset_params(default=10, max_limit=100))],
    q: str | None = Query(default=None, max_length=200),
    surface: RunSurface | None = None,
    status: RunStatus | None = None,
) -> ApiResponse[Page[RunSummary]]:
    """チャットと RAG 検索の実行を新しい順に返す。

    質問の検索・種類・状態で絞り込み、総件数を付ける。
    """
    rows, total = await RunHistoryStore(OracleClient()).list_runs(
        query=q.strip() if q and q.strip() else None,
        surface=surface,
        status=status,
        limit=paging.limit,
        offset=paging.offset,
    )
    names = _names_for(rows)
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
    fields = _summary_fields(row, _names_for([row]))
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


def _citations(value: object) -> list[RetrievedChunk]:
    if not isinstance(value, list):
        return []
    return [RetrievedChunk.model_validate(item) for item in value if isinstance(item, dict)]
