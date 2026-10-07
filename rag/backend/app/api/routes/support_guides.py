"""業務ガイド（SupportGuide。#1237）の API。

検索・回答プロファイルの知識の 1 つ。下書きを保存（楽観ロック）・検証し、公開した版だけを回答に
使う。公開・ロールバックは新しい版を作り、前の版は履歴に残る。取り込みは下書きとして作るだけで、
公開は管理者が検証してから行う。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import ValidationError

from app.clients.oracle import OracleClient
from app.clients.support_guide_store import (
    SupportGuideConflictError,
    SupportGuideNotFoundError,
    SupportGuideStore,
)
from app.rag.support_guide import has_errors, reference_issues, validate_content
from app.schemas.common import ApiResponse
from app.schemas.document import FileStatus
from app.schemas.search_answer_profile import SearchAnswerProfileDetail
from app.schemas.support_guide import (
    SupportGuideContent,
    SupportGuideCreateRequest,
    SupportGuideDetail,
    SupportGuideDraftUpdate,
    SupportGuideExportData,
    SupportGuideImportData,
    SupportGuideImportItem,
    SupportGuideImportPreviewData,
    SupportGuideImportRequest,
    SupportGuideIssue,
    SupportGuidePublishRequest,
    SupportGuideRevision,
    SupportGuideRollbackRequest,
    SupportGuideSummary,
    SupportGuideValidationData,
)
from app.security.dependencies import current_principal

router = APIRouter()
_BASE = "/{search_answer_profile_id}/support-guides"
_GUIDE = _BASE + "/{guide_id}"


def _user(request: Request) -> str | None:
    try:
        return current_principal(request).display_name
    except Exception:  # noqa: BLE001 - 認証の無い開発の構成（local）では記録しない
        return None


async def _profile(
    oracle: OracleClient, search_answer_profile_id: str
) -> SearchAnswerProfileDetail:
    view = await oracle.get_search_answer_profile(search_answer_profile_id)
    if view is None:
        raise HTTPException(status_code=404, detail="検索・回答プロファイルが見つかりません。")
    return view


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="業務ガイドが見つかりません。")


def _conflict(error: SupportGuideConflictError) -> HTTPException:
    return HTTPException(
        status_code=409,
        detail=(
            "読み込んだ後にほかの人が業務ガイドを保存しました。"
            f"最新の内容（版 {error.current_revision}）を読み込み直してから保存してください。"
        ),
    )


async def _publish_issues(
    oracle: OracleClient, profile: SearchAnswerProfileDetail, content: SupportGuideContent
) -> list[SupportGuideIssue]:
    """公開の前の検証（内容の整合と、参照する文書がプロファイルで使えるか）。"""
    knowledge_base_ids = set(profile.config.knowledge_base_ids)

    async def lookup(document_id: str) -> str | None:
        document = await oracle.get_document(document_id)
        if document is None:
            return "文書が見つかりません。"
        bases = {ref.id for ref in await oracle.list_document_knowledge_bases(document_id)}
        if not bases & knowledge_base_ids:
            return "このプロファイルのナレッジベースに含まれていません。"
        if document.status != FileStatus.INDEXED:
            return "索引が済んでいません。"
        return None

    return [*validate_content(content), *await reference_issues(content, lookup)]


async def _detail(
    store: SupportGuideStore, search_answer_profile_id: str, guide_id: str
) -> SupportGuideDetail:
    try:
        summary, draft = await store.get_guide(search_answer_profile_id, guide_id)
    except SupportGuideNotFoundError as error:
        raise _not_found() from error
    published = None
    if summary.published_revision is not None:
        published = (await store.get_revision(guide_id, summary.published_revision)).content
    return SupportGuideDetail(
        **summary.model_dump(),
        draft=draft,
        published=published,
        revisions=await store.list_revisions(guide_id),
        issues=validate_content(draft),
    )


@router.get(_BASE, response_model=ApiResponse[list[SupportGuideSummary]])
async def list_support_guides(
    search_answer_profile_id: str,
    include_archived: bool = Query(default=False),
) -> ApiResponse[list[SupportGuideSummary]]:
    """業務ガイドの一覧（アーカイブしたものは既定で出さない）。"""
    oracle = OracleClient()
    await _profile(oracle, search_answer_profile_id)
    store = SupportGuideStore(oracle)
    return ApiResponse(
        data=await store.list_guides(search_answer_profile_id, include_archived=include_archived)
    )


@router.post(_BASE, response_model=ApiResponse[SupportGuideDetail], status_code=201)
async def create_support_guide(
    request: Request, search_answer_profile_id: str, body: SupportGuideCreateRequest
) -> ApiResponse[SupportGuideDetail]:
    """下書きとして作る（公開は別の操作）。"""
    oracle = OracleClient()
    await _profile(oracle, search_answer_profile_id)
    store = SupportGuideStore(oracle)
    guide_id = await store.create_guide(search_answer_profile_id, body.draft, user=_user(request))
    return ApiResponse(data=await _detail(store, search_answer_profile_id, guide_id))


@router.get(_BASE + "/export", response_model=ApiResponse[SupportGuideExportData])
async def export_support_guides(
    search_answer_profile_id: str,
) -> ApiResponse[SupportGuideExportData]:
    """公開の版を JSON で書き出す（下書きは含めない）。"""
    oracle = OracleClient()
    await _profile(oracle, search_answer_profile_id)
    published = await SupportGuideStore(oracle).published_contents(search_answer_profile_id)
    return ApiResponse(data=SupportGuideExportData(guides=[content for _, _, content in published]))


def _import_items(
    body: SupportGuideImportRequest,
) -> list[tuple[SupportGuideImportItem, SupportGuideContent | None]]:
    items: list[tuple[SupportGuideImportItem, SupportGuideContent | None]] = []
    for index, raw in enumerate(body.guides):
        try:
            content = SupportGuideContent.model_validate(raw)
        except ValidationError as error:
            issues = [
                SupportGuideIssue(
                    severity="error",
                    code="invalid_content",
                    path=".".join(str(part) for part in item["loc"]),
                    message=str(item["msg"]),
                )
                for item in error.errors()[:20]
            ]
            title = raw.get("title") if isinstance(raw.get("title"), str) else None
            items.append(
                (SupportGuideImportItem(index=index, title=title, valid=False, issues=issues), None)
            )
            continue
        issues = validate_content(content)
        items.append(
            (
                SupportGuideImportItem(
                    index=index, title=content.title, valid=not has_errors(issues), issues=issues
                ),
                content,
            )
        )
    return items


@router.post(_BASE + "/import/preview", response_model=ApiResponse[SupportGuideImportPreviewData])
async def preview_support_guide_import(
    search_answer_profile_id: str, body: SupportGuideImportRequest
) -> ApiResponse[SupportGuideImportPreviewData]:
    """取り込む前に、各ガイドの内容を検証して示す。"""
    await _profile(OracleClient(), search_answer_profile_id)
    items = [item for item, _ in _import_items(body)]
    return ApiResponse(
        data=SupportGuideImportPreviewData(
            items=items, importable_count=sum(item.valid for item in items)
        )
    )


@router.post(_BASE + "/import", response_model=ApiResponse[SupportGuideImportData])
async def import_support_guides(
    request: Request, search_answer_profile_id: str, body: SupportGuideImportRequest
) -> ApiResponse[SupportGuideImportData]:
    """検証を通ったガイドを下書きとして作る（公開しない）。1 件でも不正なら何も作らない。"""
    oracle = OracleClient()
    await _profile(oracle, search_answer_profile_id)
    items = _import_items(body)
    invalid = [item.index for item, content in items if content is None or not item.valid]
    if invalid:
        numbers = ", ".join(str(i + 1) for i in invalid)
        raise HTTPException(
            status_code=422, detail=f"取り込めないガイドがあります（{numbers} 件目）。"
        )
    store = SupportGuideStore(oracle)
    user = _user(request)
    created: list[SupportGuideSummary] = []
    for _, content in items:
        assert content is not None
        guide_id = await store.create_guide(search_answer_profile_id, content, user=user)
        created.append((await store.get_guide(search_answer_profile_id, guide_id))[0])
    return ApiResponse(data=SupportGuideImportData(created=created))


@router.get(_GUIDE, response_model=ApiResponse[SupportGuideDetail])
async def get_support_guide(
    search_answer_profile_id: str, guide_id: str
) -> ApiResponse[SupportGuideDetail]:
    """下書き・公開の版・履歴と、下書きの検証の結果。"""
    oracle = OracleClient()
    await _profile(oracle, search_answer_profile_id)
    return ApiResponse(
        data=await _detail(SupportGuideStore(oracle), search_answer_profile_id, guide_id)
    )


@router.put(_GUIDE, response_model=ApiResponse[SupportGuideDetail])
async def save_support_guide_draft(
    request: Request, search_answer_profile_id: str, guide_id: str, body: SupportGuideDraftUpdate
) -> ApiResponse[SupportGuideDetail]:
    """下書きを保存する（`base_revision` が今の版と違えば 409）。公開の版は変わらない。"""
    oracle = OracleClient()
    await _profile(oracle, search_answer_profile_id)
    store = SupportGuideStore(oracle)
    try:
        await store.save_draft(
            search_answer_profile_id,
            guide_id,
            body.draft,
            base_revision=body.base_revision,
            user=_user(request),
        )
    except SupportGuideNotFoundError as error:
        raise _not_found() from error
    except SupportGuideConflictError as error:
        raise _conflict(error) from error
    return ApiResponse(data=await _detail(store, search_answer_profile_id, guide_id))


@router.post(_GUIDE + "/validate", response_model=ApiResponse[SupportGuideValidationData])
async def validate_support_guide(
    search_answer_profile_id: str, guide_id: str
) -> ApiResponse[SupportGuideValidationData]:
    """下書きを公開の前と同じ規則で検証する（参照する文書も確かめる）。"""
    oracle = OracleClient()
    profile = await _profile(oracle, search_answer_profile_id)
    try:
        _, draft = await SupportGuideStore(oracle).get_guide(search_answer_profile_id, guide_id)
    except SupportGuideNotFoundError as error:
        raise _not_found() from error
    issues = await _publish_issues(oracle, profile, draft)
    return ApiResponse(data=SupportGuideValidationData(valid=not has_errors(issues), issues=issues))


@router.post(_GUIDE + "/publish", response_model=ApiResponse[SupportGuideDetail])
async def publish_support_guide(
    request: Request, search_answer_profile_id: str, guide_id: str, body: SupportGuidePublishRequest
) -> ApiResponse[SupportGuideDetail]:
    """下書きを検証して新しい公開の版にする。検証に通らなければ 422（前の公開の版のまま）。"""
    oracle = OracleClient()
    profile = await _profile(oracle, search_answer_profile_id)
    store = SupportGuideStore(oracle)
    try:
        summary, draft = await store.get_guide(search_answer_profile_id, guide_id)
    except SupportGuideNotFoundError as error:
        raise _not_found() from error
    if summary.status != "active":
        raise HTTPException(status_code=409, detail="アーカイブした業務ガイドは公開できません。")
    if summary.draft_revision != body.base_revision:
        raise _conflict(SupportGuideConflictError(summary.draft_revision))
    issues = await _publish_issues(oracle, profile, draft)
    if has_errors(issues):
        raise HTTPException(
            status_code=422,
            detail={
                "message": "検証で問題が見つかったため公開できません。",
                "issues": [issue.model_dump() for issue in issues if issue.severity == "error"],
            },
        )
    try:
        await store.publish(
            search_answer_profile_id,
            guide_id,
            draft,
            base_revision=body.base_revision,
            user=_user(request),
        )
    except SupportGuideConflictError as error:
        raise _conflict(error) from error
    return ApiResponse(data=await _detail(store, search_answer_profile_id, guide_id))


@router.get(_GUIDE + "/revisions/{revision}", response_model=ApiResponse[SupportGuideRevision])
async def get_support_guide_revision(
    search_answer_profile_id: str, guide_id: str, revision: int
) -> ApiResponse[SupportGuideRevision]:
    """公開した版の内容。"""
    oracle = OracleClient()
    await _profile(oracle, search_answer_profile_id)
    store = SupportGuideStore(oracle)
    try:
        await store.get_guide(search_answer_profile_id, guide_id)
        return ApiResponse(data=await store.get_revision(guide_id, revision))
    except SupportGuideNotFoundError as error:
        raise _not_found() from error


@router.post(_GUIDE + "/rollback", response_model=ApiResponse[SupportGuideDetail])
async def rollback_support_guide(
    request: Request,
    search_answer_profile_id: str,
    guide_id: str,
    body: SupportGuideRollbackRequest,
) -> ApiResponse[SupportGuideDetail]:
    """古い公開の版を新しい版として公開し直す（下書きもその内容になる）。参照は今の状態で検証する。"""
    oracle = OracleClient()
    profile = await _profile(oracle, search_answer_profile_id)
    store = SupportGuideStore(oracle)
    try:
        summary, _ = await store.get_guide(search_answer_profile_id, guide_id)
        target = await store.get_revision(guide_id, body.revision)
    except SupportGuideNotFoundError as error:
        raise _not_found() from error
    if summary.status != "active":
        raise HTTPException(status_code=409, detail="アーカイブした業務ガイドは公開できません。")
    issues = await _publish_issues(oracle, profile, target.content)
    if has_errors(issues):
        raise HTTPException(
            status_code=422,
            detail={
                "message": "戻す版は、今の資料の状態では検証に通りません。",
                "issues": [issue.model_dump() for issue in issues if issue.severity == "error"],
            },
        )
    await store.publish(
        search_answer_profile_id,
        guide_id,
        target.content,
        base_revision=None,
        user=_user(request),
        rollback_from=body.revision,
    )
    return ApiResponse(data=await _detail(store, search_answer_profile_id, guide_id))


@router.post(_GUIDE + "/archive", response_model=ApiResponse[SupportGuideDetail])
async def archive_support_guide(
    request: Request, search_answer_profile_id: str, guide_id: str
) -> ApiResponse[SupportGuideDetail]:
    """アーカイブする（回答に使わなくなる。履歴は残る）。"""
    return await _set_status(request, search_answer_profile_id, guide_id, "archived")


@router.post(_GUIDE + "/restore", response_model=ApiResponse[SupportGuideDetail])
async def restore_support_guide(
    request: Request, search_answer_profile_id: str, guide_id: str
) -> ApiResponse[SupportGuideDetail]:
    """アーカイブから戻す（公開の版があれば、また回答に使う）。"""
    return await _set_status(request, search_answer_profile_id, guide_id, "active")


async def _set_status(
    request: Request, search_answer_profile_id: str, guide_id: str, status: str
) -> ApiResponse[SupportGuideDetail]:
    oracle = OracleClient()
    await _profile(oracle, search_answer_profile_id)
    store = SupportGuideStore(oracle)
    try:
        await store.set_status(search_answer_profile_id, guide_id, status, user=_user(request))
    except SupportGuideNotFoundError as error:
        raise _not_found() from error
    return ApiResponse(data=await _detail(store, search_answer_profile_id, guide_id))
