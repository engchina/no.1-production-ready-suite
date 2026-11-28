"""保存済み回答の検索設定を使った、質問単位の資料の再選択。"""

import json
from collections.abc import Mapping
from typing import Any, Literal, cast

from fastapi import HTTPException, Request
from pr_backend_core.file_review import (
    FileReview,
    FileReviewView,
    ReviewAction,
    ReviewSource,
    ReviewVersion,
    StaleReview,
    advance_review,
)
from starlette.concurrency import run_in_threadpool

from app.clients.oracle import OracleClient
from app.clients.oracle_run_history import RunHistoryStore
from app.config import get_settings
from app.rag.chat_attachments import ATTACHMENT_DOCUMENT_PREFIX, chat_attachment_service
from app.rag.pipeline import ANSWER_RECORD_SAVE_STAGE, BASIC_FLOW, EXPANDED_FLOW, storage_failure
from app.rag.request_context import caller_owns
from app.rag.run_history import RunRecorder, search_run_conditions
from app.rag.stored_answer import stored_citations
from app.schemas.search import SearchRequest


def scoped_filters(
    original: dict[str, str], include: list[str], exclude: list[str]
) -> dict[str, str]:
    filters = dict(original)
    if include:
        allowed = set(include)
        if "include_document_ids" in filters:
            allowed.intersection_update(json.loads(filters["include_document_ids"]))
        filters["include_document_ids"] = json.dumps(sorted(allowed))
    blocked = set(exclude) | set(json.loads(filters.get("exclude_document_ids", "[]")))
    filters["exclude_document_ids"] = json.dumps(sorted(blocked))
    return filters


def _json_object(value: object) -> dict[str, Any]:
    if isinstance(value, str):
        value = json.loads(value)
    return dict(value) if isinstance(value, Mapping) else {}


async def review_answer(
    http_request: Request, trace_id: str, command: ReviewAction | None = None
) -> FileReviewView:
    from app.api.routes.search import _answer_record_detail, _run_search_with_timeout

    # 一度に選べる資料の数の上限（#5981）。保存する状態には入れず、応答のたびに今の設定を返す。
    selection_limit = get_settings().rag_file_review_max_files
    oracle = OracleClient()
    row = await oracle.get_file_review(trace_id)
    record = await oracle.get_answer_record(trace_id)
    if row is None or record is None:
        raise HTTPException(404, "回答が見つかりません。")
    detail = _answer_record_detail(record)
    principal = getattr(http_request.state, "principal", None)
    if not principal or not principal.has_permission("menu." + detail.surface):
        raise HTTPException(403, "この回答を利用する権限がありません。")
    if command and not caller_owns(str(row.get("user_id_hash") or "")):
        raise HTTPException(403, "この回答は閲覧のみです。")
    saved = _json_object(row.get("review_state"))
    if saved:
        state = FileReview.model_validate(saved)
    else:
        source = ReviewSource(
            title=detail.question,
            citations=stored_citations(detail.citations),
            trace_id=trace_id,
            answer=detail.answer,
            notice=detail.notice,
        )
        state = FileReview(
            versions=[
                ReviewVersion(
                    id=trace_id,
                    answer=detail.answer,
                    notice=detail.notice,
                    sources={"question": source},
                )
            ]
        )
    config = _json_object(row.get("review_request"))
    ids = state.document_ids()
    attachments = {i for i in ids if i.startswith(ATTACHMENT_DOCUMENT_PREFIX)}
    permitted = await oracle.accessible_document_ids(sorted(ids - attachments))
    conversation_id = config.get("conversation_id")
    if attachments and conversation_id and await oracle.get_conversation(conversation_id):
        records = await run_in_threadpool(
            chat_attachment_service().conversation_records, conversation_id
        )
        permitted.update(
            attachments & {ATTACHMENT_DOCUMENT_PREFIX + r.attachment_id for r in records}
        )
    if not saved:
        state = state.visible(permitted)
        state.seen.update(state.versions[0].sources["question"].documents())
    owner = str(row.get("user_id_hash") or "")

    async def projected(result: FileReview, readable: set[str]) -> FileReviewView:
        view = result.visible(readable)
        ratings = await oracle.file_review_ratings([v.id for v in view.versions], owner)
        for version in view.versions:
            version.rating = cast(Literal["helpful", "not_helpful"] | None, ratings.get(version.id))
        return FileReviewView(**dict(view), selection_limit=selection_limit)

    if command is None:
        return await projected(state, permitted)
    # 旧版では保存のある条件と質問を引き継ぐ。参照先の権限は検索の入口で改めて確かめる。
    previous = config.get("request") or {
        "query": detail.rewritten_question or detail.question,
        "original_question": detail.question,
        "search_answer_profile_id": detail.search_answer_profile_id,
    }
    base = SearchRequest.model_validate(previous)
    flow = (
        EXPANDED_FLOW if config.get("flow", detail.surface) in {"expanded", "chat"} else BASIC_FLOW
    )

    staged: dict[str, object] = {}
    # 実行履歴（#1890）には元の回答と同じ種類で残す。チャットは元の回答の会話とメッセージに
    # 結び付ける（会話の持ち主に見せる条件に使う。#5913）。
    chat_conversation_id = conversation_id if detail.surface == "chat" else None
    chat_message_id = (
        await oracle.get_chat_answer_message_id(chat_conversation_id, trace_id)
        if chat_conversation_id
        else None
    )
    # 回答を作って返した実行（版の保存が確定しなかったときに、保存できなかったことを足す）。
    answer_runs: list[RunRecorder] = []

    async def retrieve(
        task: str, include: list[str], exclude: list[str], generate: bool
    ) -> ReviewSource:
        del task
        req = base.model_copy(
            update={
                "filters": scoped_filters(base.filters, include, exclude),
                "generate_answer": generate,
                "approved_faq_id": None,
                "retrieval_only": False,
            }
        )
        # 再回答と新しい候補の検索も、通常の RAG 検索・チャットと同じく 1 回の実行として残す。
        run_recorder = RunRecorder(
            surface=detail.surface,
            settings=get_settings(),
            store=RunHistoryStore(oracle),
            conversation_id=chat_conversation_id,
            message_id=chat_message_id,
            conditions=search_run_conditions(req) if detail.surface == "search" else None,
        )
        result = await _run_search_with_timeout(
            req,
            flow=flow,
            interactive=True,
            run_recorder=run_recorder,
            attachment_conversation_id=conversation_id,
            staged_answer=staged if generate else None,
        )
        if generate and result.storage_failures:
            raise HTTPException(503, "再回答の記録を保存できませんでした。もう一度お試しください。")
        if generate:
            answer_runs.append(run_recorder)
        return ReviewSource(
            title=detail.question,
            trace_id=result.trace_id,
            answer=result.answer,
            notice=result.notice,
            citations=stored_citations(result.citations),
        )

    async def compose(sources: dict[str, ReviewSource]) -> str:
        return sources["question"].answer

    try:
        try:
            updated = await advance_review(
                state, command, permitted, retrieve, compose, selection_limit=selection_limit
            )
            if command.action == "answer":
                if not staged:
                    raise HTTPException(503, "再回答の根拠を保存できませんでした。")
                staged["surface"] = detail.surface
            await oracle.save_file_review(
                trace_id, command.revision, updated.model_dump(mode="json"), staged
            )
        except Exception as exc:
            # 回答は作ったが版を確定できなかった。成功の実行に、回答の記録が無いことを残す
            # （#2342 と同じ形）。
            for run in answer_runs:
                await run.add_storage_failure(storage_failure(ANSWER_RECORD_SAVE_STAGE, exc))
            raise
    except StaleReview as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    # 新しく検索した資料も、この利用者の現在の検索範囲を通過したものだけ。
    return await projected(updated, permitted | (updated.document_ids() - ids))
