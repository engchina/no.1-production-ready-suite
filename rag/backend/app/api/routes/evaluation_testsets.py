"""本人の評価結果から作る、名前付きテストセットの下書き API。"""

import asyncio
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import ValidationError

from app.rag import evaluation_testsets as service
from app.rag.evaluation_excel import XLSX_MEDIA_TYPE
from app.rag.request_context import current_audit_request_context
from app.schemas.common import ApiResponse
from app.schemas.evaluation_testsets import (
    TestSetCreateRequest,
    TestSetDraft,
    TestSetItem,
    TestSetSaveRequest,
    TestSetSourcesRequest,
    TestSetSummary,
)

router = APIRouter()


def _owner() -> None:
    if current_audit_request_context().user_id_hash is None:
        raise HTTPException(status_code=403, detail="本人を確認できないため操作できません。")


async def _draft(draft_id: str) -> TestSetDraft:
    _owner()
    draft = await service.get_store().get(draft_id)
    if draft is None:
        raise HTTPException(status_code=404, detail="下書きが見つかりません。")
    return draft


@router.get("/sources")
async def sources() -> ApiResponse[list[dict[str, object]]]:
    _owner()
    return ApiResponse(data=await service.get_reader().list_jobs())


@router.post("/sources/items", response_model=ApiResponse[list[TestSetItem]])
async def candidates(body: TestSetSourcesRequest) -> ApiResponse[list[TestSetItem]]:
    _owner()
    try:
        return ApiResponse(data=await service.source_items(service.get_reader(), body.job_ids))
    except ValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail="入力が保存できる範囲を超えています。項目を分けるか内容を確認してください。",
        ) from exc
    except service.TestSetError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("", response_model=ApiResponse[list[TestSetSummary]])
async def drafts() -> ApiResponse[list[TestSetSummary]]:
    _owner()
    return ApiResponse(data=await service.get_store().list())


@router.post("", response_model=ApiResponse[TestSetDraft], status_code=201)
async def create(body: TestSetCreateRequest) -> ApiResponse[TestSetDraft]:
    _owner()
    try:
        items = await service.source_items(service.get_reader(), body.job_ids)
    except ValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail="入力が保存できる範囲を超えています。項目を分けるか内容を確認してください。",
        ) from exc
    except service.TestSetError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    by_id = {item.item_id: item for item in items}
    selected = list(dict.fromkeys(body.item_ids))
    if any(item_id not in by_id for item_id in selected):
        raise HTTPException(
            status_code=422, detail="選んだ項目が見つかりません。選び直してください。"
        )
    try:
        draft = service.new_draft(body.name, [by_id[item_id] for item_id in selected])
    except ValidationError as exc:
        raise HTTPException(
            status_code=422, detail="下書きが大きすぎます。項目を分けてください。"
        ) from exc
    await service.get_store().create(draft)
    return ApiResponse(data=draft)


@router.get("/{draft_id}", response_model=ApiResponse[TestSetDraft])
async def get(draft_id: str) -> ApiResponse[TestSetDraft]:
    return ApiResponse(data=await _draft(draft_id))


@router.put("/{draft_id}", response_model=ApiResponse[TestSetDraft])
async def save(draft_id: str, body: TestSetSaveRequest) -> ApiResponse[TestSetDraft]:
    previous = await _draft(draft_id)
    if previous.revision != body.revision:
        raise HTTPException(
            status_code=409, detail="別の画面で更新されました。最新の下書きを開き直してください。"
        )
    try:
        updated = await asyncio.to_thread(service.edited_draft, previous, body)
    except ValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail="入力が保存できる範囲を超えています。項目を分けるか内容を確認してください。",
        ) from exc
    except service.TestSetError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not await service.get_store().save(updated, body.revision):
        raise HTTPException(
            status_code=409, detail="別の画面で更新されました。最新の下書きを開き直してください。"
        )
    return ApiResponse(data=updated)


@router.get("/{draft_id}/export.xlsx")
async def export(
    draft_id: str,
    revision: Annotated[int, Query(ge=1)],
) -> Response:
    draft = await _draft(draft_id)
    if draft.revision != revision:
        raise HTTPException(
            status_code=409, detail="下書きが更新されました。最新の内容を確認してください。"
        )
    try:
        content = await asyncio.to_thread(service.export_draft, draft)
    except ValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail="入力が保存できる範囲を超えています。項目を分けるか内容を確認してください。",
        ) from exc
    except service.TestSetError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return Response(
        content,
        media_type=XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": 'attachment; filename="evaluation_testset.xlsx"'},
    )
