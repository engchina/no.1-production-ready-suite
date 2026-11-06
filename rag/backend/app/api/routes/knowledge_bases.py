"""ナレッジベース API。作成・一覧・詳細・membership 管理。"""

from collections.abc import Awaitable, Callable
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pr_backend_core.api import OffsetParams, offset_params, paginate

from app.clients.oracle import OracleClient
from app.clients.structure_graph_store import StructureGraphStore
from app.config import get_settings
from app.db_read_failures import read_or_raise
from app.rag.extraction_field_adapter import (
    FieldDefinition,
    load_field_schema,
    validate_field_schema,
)
from app.schemas.common import ApiResponse, Page
from app.schemas.knowledge_base import (
    KnowledgeBaseCreateRequest,
    KnowledgeBaseDetail,
    KnowledgeBaseDocumentAssignmentRequest,
    KnowledgeBaseStatus,
    KnowledgeBaseStructureIndexData,
    KnowledgeBaseStructureIndexUpdate,
    KnowledgeBaseSummary,
    KnowledgeBaseUpdateRequest,
    StructureGraphBodyChunkPage,
    StructureGraphLocation,
    StructureGraphOverview,
    StructureGraphReferences,
    StructureGraphSearchPage,
    StructureGraphTreePage,
    StructureIndexStartRequest,
)
from app.schemas.settings import (
    FieldDefinitionData,
    KnowledgeBaseExtractionFieldsData,
    KnowledgeBaseExtractionFieldsUpdate,
)

router = APIRouter()

# KB は所属(スコープ)だけを持つ。構築設定の書き込みは受け付けない(rag/AGENTS.md「RAG 設定責務」)。
ADAPTER_CONFIG_REJECTED_MESSAGE = (
    "adapter_config は指定できません。ナレッジベースは文書の所属だけを持ちます。"
    "文書の処理は文書のレシピ、検索・回答の設定は検索・回答プロファイルで指定してください。"
)
# ID で絞るときの上限(検索・回答プロファイル・評価の参照 KB の上限と同じ)。
MAX_KNOWLEDGE_BASE_ID_FILTER = 200


def _reject_adapter_config(fields_set: set[str]) -> None:
    """`adapter_config` を含む作成・更新を 422 で拒否する(#302。旧 API との互換を変更)。"""
    if "adapter_config" in fields_set:
        raise HTTPException(status_code=422, detail=ADAPTER_CONFIG_REJECTED_MESSAGE)


async def _refreshed_detail_response(
    oracle: OracleClient,
    detail: KnowledgeBaseDetail,
) -> ApiResponse[KnowledgeBaseDetail]:
    """変更後の詳細を集計列(文書数・索引済み数など)込みで取り直して返す。

    変更系の Oracle 操作は KB の行だけを読むため、集計列が 0 のまま返る。取り直せない
    (範囲外になった等)ときは変更結果をそのまま返す(#282)。
    """
    refreshed = await oracle.get_knowledge_base(detail.id)
    return ApiResponse(data=refreshed or detail)


@router.get("", response_model=ApiResponse[Page[KnowledgeBaseSummary]])
async def list_knowledge_bases(
    paging: Annotated[OffsetParams, Depends(offset_params(default=50, max_limit=200))],
    status: KnowledgeBaseStatus | None = None,
    q: str | None = Query(default=None, min_length=1, max_length=200),
    ids: Annotated[
        list[str] | None,
        Query(
            max_length=MAX_KNOWLEDGE_BASE_ID_FILTER,
            description=(
                "指定した ID の KB だけを返す(選択済みの名前・状態の解決用。#302)。"
                "status を省くとアーカイブ済みも返す。"
            ),
        ),
    ] = None,
) -> ApiResponse[Page[KnowledgeBaseSummary]]:
    """ナレッジベース一覧を返す。読み取りの失敗は API の失敗にする（#4217）。"""
    oracle = OracleClient()
    settings = get_settings()

    async def _load() -> Page[KnowledgeBaseSummary]:
        items = await oracle.list_knowledge_bases(
            status=status,
            query=q,
            limit=paging.limit,
            offset=paging.offset,
            knowledge_base_ids=ids,
        )
        total = await oracle.count_knowledge_bases(status=status, query=q, knowledge_base_ids=ids)
        return paginate(items, total=total, limit=paging.limit, offset=paging.offset)

    page = await read_or_raise(
        _load,
        timeout_seconds=settings.db_read_timeout_seconds,
        log_label="knowledge_bases_list",
    )
    return ApiResponse(
        data=page,
    )


@router.get("/count", response_model=ApiResponse[int])
async def count_knowledge_bases(
    status: KnowledgeBaseStatus | None = None,
    q: str | None = Query(default=None, min_length=1, max_length=200),
    ids: Annotated[list[str] | None, Query(max_length=MAX_KNOWLEDGE_BASE_ID_FILTER)] = None,
) -> ApiResponse[int]:
    """一覧と同じ閲覧範囲でKBの総数だけを返し、文書/Chunk統計を取得しない。"""
    oracle = OracleClient()
    total = await read_or_raise(
        lambda: oracle.count_knowledge_bases(status=status, query=q, knowledge_base_ids=ids),
        timeout_seconds=get_settings().db_read_timeout_seconds,
        log_label="knowledge_bases_count",
    )
    return ApiResponse(data=total)


@router.post("", response_model=ApiResponse[KnowledgeBaseDetail])
async def create_knowledge_base(
    request: KnowledgeBaseCreateRequest,
) -> ApiResponse[KnowledgeBaseDetail]:
    """ナレッジベースを作成する。`adapter_config` は 422 で拒否する。"""
    _reject_adapter_config(request.model_fields_set)
    try:
        detail = await OracleClient().create_knowledge_base(
            name=request.name,
            description=request.description,
            default_search_mode=request.default_search_mode,
            retrieval_config=request.retrieval_config,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return ApiResponse(data=detail)


@router.get("/{knowledge_base_id}", response_model=ApiResponse[KnowledgeBaseDetail])
async def get_knowledge_base(
    knowledge_base_id: str,
) -> ApiResponse[KnowledgeBaseDetail]:
    """ナレッジベース詳細を返す。"""
    detail = await OracleClient().get_knowledge_base(knowledge_base_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="ナレッジベースが見つかりません。")
    return ApiResponse(data=detail)


async def _read_structure_graph(
    knowledge_base_id: str,
    log_label: str,
    read: Callable[[StructureGraphStore], Awaitable[dict[str, Any]]],
) -> dict[str, Any]:
    """KB の確認と読み取り。読み取りの失敗は API の失敗にし、見えない対象は 404。"""
    oracle = OracleClient()

    async def _load() -> dict[str, Any]:
        if not await oracle.knowledge_base_is_visible(knowledge_base_id):
            raise HTTPException(status_code=404, detail="ナレッジベースが見つかりません。")
        try:
            return await read(StructureGraphStore(oracle))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="対象が見つかりません。") from exc

    return await read_or_raise(
        _load, timeout_seconds=get_settings().db_read_timeout_seconds, log_label=log_label
    )


@router.get(
    "/{knowledge_base_id}/graph/overview",
    response_model=ApiResponse[StructureGraphOverview],
)
async def get_structure_graph_overview(
    knowledge_base_id: str,
) -> ApiResponse[StructureGraphOverview]:
    """KB の全文書と、文書の組ごとの解決済みの参照の件数を返す。"""
    data = await _read_structure_graph(
        knowledge_base_id,
        "structure_graph_overview",
        lambda store: store.overview(knowledge_base_id),
    )
    return ApiResponse(data=StructureGraphOverview(**data))


@router.get(
    "/{knowledge_base_id}/graph/documents/{document_id}/tree",
    response_model=ApiResponse[StructureGraphTreePage],
)
async def get_structure_graph_tree(
    knowledge_base_id: str,
    document_id: str,
    chunk_set_id: str = Query(min_length=1, max_length=64),
    parent: str | None = Query(default=None, min_length=64, max_length=64),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=1000),
) -> ApiResponse[StructureGraphTreePage]:
    """章節の木の 1 段を返す（文書の順）。``parent`` が無ければ文書の直下。"""
    data = await _read_structure_graph(
        knowledge_base_id,
        "structure_graph_tree",
        lambda store: store.children(
            knowledge_base_id,
            document_id,
            chunk_set_id,
            parent_id=parent,
            offset=offset,
            limit=limit,
        ),
    )
    return ApiResponse(data=StructureGraphTreePage(**data))


@router.get(
    "/{knowledge_base_id}/graph/documents/{document_id}/locate",
    response_model=ApiResponse[StructureGraphLocation],
)
async def locate_structure_graph_node(
    knowledge_base_id: str,
    document_id: str,
    chunk_set_id: str = Query(min_length=1, max_length=64),
    node: str = Query(min_length=64, max_length=64),
) -> ApiResponse[StructureGraphLocation]:
    """節点までの段と各段の位置を返す（検索・参照から図の位置へ開く）。"""
    data = await _read_structure_graph(
        knowledge_base_id,
        "structure_graph_locate",
        lambda store: store.locate(knowledge_base_id, document_id, chunk_set_id, node),
    )
    return ApiResponse(data=StructureGraphLocation(**data))


@router.get(
    "/{knowledge_base_id}/graph/documents/{document_id}/references",
    response_model=ApiResponse[StructureGraphReferences],
)
async def get_structure_graph_document_references(
    knowledge_base_id: str,
    document_id: str,
    chunk_set_id: str = Query(min_length=1, max_length=64),
) -> ApiResponse[StructureGraphReferences]:
    """開いた文書が端にある、章節の間の解決済みの参照を返す。"""

    async def read(store: StructureGraphStore) -> dict[str, Any]:
        await store.assert_document(knowledge_base_id, document_id, chunk_set_id)
        return await store.references(knowledge_base_id, chunk_set_id)

    data = await _read_structure_graph(knowledge_base_id, "structure_graph_references", read)
    return ApiResponse(data=StructureGraphReferences(**data))


@router.get(
    "/{knowledge_base_id}/graph/search",
    response_model=ApiResponse[StructureGraphSearchPage],
)
async def search_structure_graph(
    knowledge_base_id: str,
    q: str = Query(default="", max_length=200),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=25, ge=1, le=100),
) -> ApiResponse[StructureGraphSearchPage]:
    """KB の全体から文書名と章節名を検索する。"""
    data = await _read_structure_graph(
        knowledge_base_id,
        "structure_graph_search",
        lambda store: store.search(knowledge_base_id, q, offset=offset, limit=limit),
    )
    return ApiResponse(data=StructureGraphSearchPage(**data))


@router.get("/{knowledge_base_id}/graph/references")
async def get_knowledge_base_graph_references(
    knowledge_base_id: str,
    node_id: str = Query(min_length=64, max_length=64, pattern="^[a-f0-9]+$"),
    reference_id: str | None = Query(
        default=None, min_length=64, max_length=64, pattern="^[a-f0-9]+$"
    ),
    direction: str = Query(default="outgoing", pattern="^(outgoing|incoming)$"),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=25, ge=1, le=100),
) -> ApiResponse[dict[str, Any]]:
    """同じ版の参照先・参照元を、読み取り時点の権限で返す。"""
    from app.clients.graph_reference_store import GraphReferenceStore

    oracle = OracleClient()
    if await oracle.get_knowledge_base(knowledge_base_id) is None:
        raise HTTPException(status_code=404, detail="ナレッジベースが見つかりません。")
    try:
        data = await GraphReferenceStore(oracle).page(
            knowledge_base_id,
            node_id,
            direction=direction,
            offset=offset,
            limit=limit,
            reference_id=reference_id,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="対象が見つかりません。") from exc
    return ApiResponse(data=data)


@router.patch("/{knowledge_base_id}", response_model=ApiResponse[KnowledgeBaseDetail])
async def update_knowledge_base(
    knowledge_base_id: str,
    request: KnowledgeBaseUpdateRequest,
) -> ApiResponse[KnowledgeBaseDetail]:
    """ナレッジベースを更新する。`adapter_config` は 422 で拒否する。"""
    _reject_adapter_config(request.model_fields_set)
    update_fields = set(request.model_fields_set)
    oracle = OracleClient()
    try:
        detail = await oracle.update_knowledge_base(
            knowledge_base_id,
            name=request.name,
            description=request.description,
            default_search_mode=request.default_search_mode,
            retrieval_config=request.retrieval_config,
            update_fields=update_fields,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="ナレッジベースが見つかりません。") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return await _refreshed_detail_response(oracle, detail)


def _extraction_fields_data(
    fields: list[FieldDefinition] | None,
) -> ApiResponse[KnowledgeBaseExtractionFieldsData]:
    """KB の定義(None なら全体の既定)を API 形にする。"""
    effective = load_field_schema().fields if fields is None else fields
    return ApiResponse(
        data=KnowledgeBaseExtractionFieldsData(
            inherits_default=fields is None,
            fields=[FieldDefinitionData.model_validate(field.model_dump()) for field in effective],
        )
    )


@router.get(
    "/{knowledge_base_id}/extraction-fields",
    response_model=ApiResponse[KnowledgeBaseExtractionFieldsData],
)
async def get_knowledge_base_extraction_fields(
    knowledge_base_id: str,
) -> ApiResponse[KnowledgeBaseExtractionFieldsData]:
    """ナレッジベースの項目抽出の定義を返す。KB に定義が無ければ全体の既定を返す(#548)。"""
    oracle = OracleClient()
    if await oracle.get_knowledge_base(knowledge_base_id) is None:
        raise HTTPException(status_code=404, detail="ナレッジベースが見つかりません。")
    return _extraction_fields_data(
        await oracle.get_knowledge_base_extraction_fields(knowledge_base_id)
    )


@router.put(
    "/{knowledge_base_id}/extraction-fields",
    response_model=ApiResponse[KnowledgeBaseExtractionFieldsData],
)
async def update_knowledge_base_extraction_fields(
    knowledge_base_id: str,
    request: KnowledgeBaseExtractionFieldsUpdate,
) -> ApiResponse[KnowledgeBaseExtractionFieldsData]:
    """ナレッジベースの項目抽出の定義を保存する。`fields` が null なら全体の既定に戻す(#548)。

    name の重複(大文字小文字を区別しない)は 422、アーカイブ済みの KB は 409。保存した定義は
    次の取込から効く(既存の文書の抽出値は変えない)。
    """
    oracle = OracleClient()
    detail = await oracle.get_knowledge_base(knowledge_base_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="ナレッジベースが見つかりません。")
    if detail.status != KnowledgeBaseStatus.ACTIVE:
        raise HTTPException(
            status_code=409, detail="アーカイブ済みのナレッジベースは変更できません。"
        )
    fields: list[FieldDefinition] | None = None
    if request.fields is not None:
        try:
            fields = validate_field_schema(
                [FieldDefinition.model_validate(field.model_dump()) for field in request.fields]
            ).fields
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    await oracle.update_knowledge_base_extraction_fields(knowledge_base_id, fields)
    return _extraction_fields_data(fields)


@router.post("/{knowledge_base_id}/archive", response_model=ApiResponse[KnowledgeBaseDetail])
async def archive_knowledge_base(
    knowledge_base_id: str,
) -> ApiResponse[KnowledgeBaseDetail]:
    """ナレッジベースをアーカイブする。"""
    oracle = OracleClient()
    try:
        detail = await oracle.archive_knowledge_base(knowledge_base_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="ナレッジベースが見つかりません。") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return await _refreshed_detail_response(oracle, detail)


@router.post("/{knowledge_base_id}/documents", response_model=ApiResponse[KnowledgeBaseDetail])
async def assign_documents_to_knowledge_base(
    knowledge_base_id: str,
    request: KnowledgeBaseDocumentAssignmentRequest,
) -> ApiResponse[KnowledgeBaseDetail]:
    """既存文書をナレッジベースへ追加する。"""
    oracle = OracleClient()
    try:
        detail = await oracle.assign_documents_to_knowledge_base(
            knowledge_base_id,
            request.document_ids,
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail="ナレッジベースまたは文書が見つかりません。",
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return await _refreshed_detail_response(oracle, detail)


@router.delete(
    "/{knowledge_base_id}/documents/{document_id}",
    response_model=ApiResponse[KnowledgeBaseDetail],
)
async def remove_document_from_knowledge_base(
    knowledge_base_id: str,
    document_id: str,
) -> ApiResponse[KnowledgeBaseDetail]:
    """文書をナレッジベースから外す。文書自体は削除しない。

    最後の所属を外すときは DEFAULT へ移す(未所属の文書を作らない)。DEFAULT にだけ
    所属する文書は外せない(409)。
    """
    oracle = OracleClient()
    try:
        detail = await oracle.remove_document_from_knowledge_base(
            knowledge_base_id,
            document_id,
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail="ナレッジベースまたは文書が見つかりません。",
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return await _refreshed_detail_response(oracle, detail)


@router.get(
    "/{knowledge_base_id}/structure-index",
    response_model=ApiResponse[KnowledgeBaseStructureIndexData],
)
async def get_structure_index_settings(
    knowledge_base_id: str,
) -> ApiResponse[KnowledgeBaseStructureIndexData]:
    from app.rag.structure_index import PREFERENCE_KEY

    oracle = OracleClient()
    kb = await oracle.get_knowledge_base(knowledge_base_id)
    if kb is None:
        raise HTTPException(status_code=404, detail="ナレッジベースが見つかりません。")
    value = kb.retrieval_config.get(PREFERENCE_KEY)
    return ApiResponse(
        data=KnowledgeBaseStructureIndexData(
            enabled=value if isinstance(value, bool) else None,
            global_enabled=get_settings().rag_graph_profile == "entities",
        )
    )


@router.put(
    "/{knowledge_base_id}/structure-index",
    response_model=ApiResponse[KnowledgeBaseStructureIndexData],
)
async def update_structure_index_settings(
    knowledge_base_id: str, request: KnowledgeBaseStructureIndexUpdate
) -> ApiResponse[KnowledgeBaseStructureIndexData]:
    oracle = OracleClient()
    try:
        await oracle.set_knowledge_base_structure_index(knowledge_base_id, request.enabled)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="ナレッジベースが見つかりません。") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return await get_structure_index_settings(knowledge_base_id)


@router.get("/{knowledge_base_id}/structure-index/plan")
async def get_structure_index_plan(
    knowledge_base_id: str, offset: int = Query(0, ge=0), limit: int = Query(25, ge=1, le=100)
) -> ApiResponse[dict[str, Any]]:
    from app.rag.structure_index import structure_index_plan
    from app.rag.structure_index_jobs import public_state

    try:
        items = await structure_index_plan(OracleClient(), knowledge_base_id)
    except KeyError as exc:
        raise HTTPException(404, "ナレッジベースが見つかりません。") from exc
    counts = {
        key: sum(item["status"] == key for item in items)
        for key in ("MISSING", "STALE", "CURRENT", "UNAVAILABLE")
    }
    return ApiResponse(
        data={
            "counts": counts,
            "total": len(items),
            "offset": offset,
            "limit": limit,
            "items": (public_state({"items": items}, offset, limit) or {})["items"],
        }
    )


@router.get("/{knowledge_base_id}/structure-index/job")
async def get_structure_index_job(
    knowledge_base_id: str, offset: int = Query(0, ge=0), limit: int = Query(25, ge=1, le=100)
) -> ApiResponse[dict[str, Any] | None]:
    from app.clients.structure_index_store import StructureIndexStore
    from app.rag.structure_index_jobs import current_public_state

    oracle = OracleClient()
    if await oracle.get_knowledge_base(knowledge_base_id) is None:
        raise HTTPException(404, "ナレッジベースが見つかりません。")
    store = StructureIndexStore(oracle)
    state = await store.get(knowledge_base_id)
    return ApiResponse(
        data=await current_public_state(
            oracle,
            knowledge_base_id,
            await store.visible_state(knowledge_base_id, state),
            offset,
            limit,
        )
    )


@router.post("/{knowledge_base_id}/structure-index/job")
async def start_structure_index_job(
    knowledge_base_id: str, request: StructureIndexStartRequest
) -> ApiResponse[dict[str, Any]]:
    from app.rag.structure_index_jobs import (
        StructureIndexScopeError,
        current_public_state,
        structure_index_jobs,
    )

    oracle = OracleClient()
    try:
        state = await structure_index_jobs.start(
            oracle, knowledge_base_id, request.mode, request.document_ids
        )
    except StructureIndexScopeError as exc:
        raise HTTPException(404, str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(404, "ナレッジベースが見つかりません。") from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    from app.clients.structure_index_store import StructureIndexStore

    return ApiResponse(
        data=await current_public_state(
            oracle,
            knowledge_base_id,
            await StructureIndexStore(oracle).visible_state(knowledge_base_id, state),
        )
        or {}
    )


@router.post("/{knowledge_base_id}/structure-index/job/stop")
async def stop_structure_index_job(knowledge_base_id: str) -> ApiResponse[dict[str, Any]]:
    from app.rag.structure_index_jobs import current_public_state, structure_index_jobs

    oracle = OracleClient()
    if await oracle.get_knowledge_base(knowledge_base_id) is None:
        raise HTTPException(404, "ナレッジベースが見つかりません。")
    try:
        state = await structure_index_jobs.stop(oracle, knowledge_base_id)
    except KeyError as exc:
        raise HTTPException(404, "構築の記録が見つかりません。") from exc
    from app.clients.structure_index_store import StructureIndexStore

    return ApiResponse(
        data=await current_public_state(
            oracle,
            knowledge_base_id,
            await StructureIndexStore(oracle).visible_state(knowledge_base_id, state),
        )
        or {}
    )


@router.get(
    "/{knowledge_base_id}/graph/documents/{document_id}/body-chunks",
    response_model=ApiResponse[StructureGraphBodyChunkPage],
)
async def get_structure_graph_body_chunks(
    knowledge_base_id: str,
    document_id: str,
    chunk_set_id: str = Query(..., min_length=1, max_length=64),
    offset: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
) -> ApiResponse[StructureGraphBodyChunkPage]:
    """図の本文を、同じ閲覧範囲/保存版の原文表示へ結ぶ。"""
    data = await _read_structure_graph(
        knowledge_base_id,
        "structure_graph_body_chunks",
        lambda store: store.body_chunks(
            knowledge_base_id, document_id, chunk_set_id, offset=offset, limit=limit
        ),
    )
    return ApiResponse(data=StructureGraphBodyChunkPage(**data))
