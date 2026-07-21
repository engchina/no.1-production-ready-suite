"""NL2SQL feature router."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Request,
    Response,
    UploadFile,
)
from pr_backend_core import ApiResponse

from .incremental_store import IncrementalVersionConflict
from .models import (
    AgentConversationCreateData,
    AgentConversationCreateRequest,
    AgentConversationsData,
    AgentPrivilegeCheckData,
    AgentTeamRunData,
    AgentTeamRunRequest,
    AgentToolRunRequest,
    AnalyzeData,
    AnalyzeRequest,
    AnnotationApplyData,
    AnnotationApplyRequest,
    AnnotationSuggestionData,
    AssetCleanupData,
    AssetCleanupRequest,
    AssetRefreshData,
    ClassifierFeedbackImportData,
    ClassifierFeedbackImportRequest,
    ClassifierImportData,
    ClassifierModelImportData,
    ClassifierPredictionData,
    ClassifierPredictRequest,
    ClassifierStatusData,
    ClassifierTrainingCandidatesData,
    ClassifierTrainingDataData,
    ClassifierTrainingExample,
    ClassifierTrainingExampleUpdateRequest,
    ClassifierTrainRequest,
    CommentApplyData,
    CommentApplyRequest,
    CommentSuggestionData,
    CommentSuggestionRequest,
    CompareData,
    CompareHistoryData,
    CompareRequest,
    DbAdminAiAnalysisData,
    DbAdminAiAnalysisRequest,
    DbAdminCsvUploadData,
    DbAdminCsvUploadRequest,
    DbAdminDataPreviewData,
    DbAdminDataPreviewRequest,
    DbAdminDropTableRequest,
    DbAdminDropViewRequest,
    DbAdminExecuteData,
    DbAdminExecuteRequest,
    DbAdminImportTabularData,
    DbAdminImportTabularRequest,
    DbAdminJoinWhereData,
    DbAdminJoinWhereRequest,
    DbAdminObjectDetail,
    DbAdminObjectsData,
    DbAdminStatementsRequest,
    DemoLearningData,
    DiagnosticsData,
    EvaluateData,
    EvaluateRequest,
    EvaluationRunsData,
    EvaluationSet,
    EvaluationSetsData,
    EvaluationSetUpsertRequest,
    ExecuteRequest,
    FeedbackClearData,
    FeedbackData,
    FeedbackEntriesData,
    FeedbackEntriesDeleteRequest,
    FeedbackIndexData,
    FeedbackIndexRequest,
    FeedbackListData,
    FeedbackRequest,
    FeedbackSearchConfigData,
    FeedbackSearchConfigRequest,
    HistoryData,
    JobCreateData,
    JobCreateRequest,
    JobData,
    LegacyLearningMaterialData,
    MetadataSqlGenerateData,
    MetadataSqlGenerateRequest,
    MetadataSqlSampleData,
    MetadataSqlSampleRequest,
    Nl2SqlProfile,
    PersistenceStatusData,
    PreviewData,
    PreviewRequest,
    ProfileLearningMaterialImportData,
    ProfileRecommendationData,
    ProfileRecommendationRequest,
    ProfileSelectAiProfileRequest,
    ProfileSummaryPage,
    ProfileUpsertRequest,
    QueryResults,
    RepairData,
    RepairRequest,
    ReverseSqlData,
    ReverseSqlRequest,
    RewriteData,
    RewriteRequest,
    SampleDataInfo,
    SampleDataMutationData,
    SampleDataMutationRequest,
    SelectAiAgentAssetsData,
    SelectAiDbProfileDetailData,
    SelectAiDbProfileDropRequest,
    SelectAiDbProfileMutationData,
    SelectAiDbProfilesData,
    SelectAiDbProfileUpsertRequest,
    SelectAiFeedbackAddData,
    SelectAiFeedbackAddRequest,
    SelectAiFeedbackDeleteRequest,
    SelectAiFeedbackEntriesData,
    SelectAiFeedbackMutationData,
    SelectAiFeedbackVectorIndexRequest,
    SelectAiProfilesExportData,
    SelectAiProfilesImportRequest,
    SimilarHistoryData,
    SimilarHistoryRequest,
    SyntheticCasesData,
    SyntheticDataGenerateRequest,
    SyntheticDataOperationData,
    SyntheticDataOperationStatusData,
    SyntheticDataResultsData,
)
from .service import is_select_only as _is_select_only
from .service import nl2sql_service


async def _require_persistence() -> None:
    nl2sql_service.ensure_persistence_available()


persistence_router = APIRouter(prefix="/nl2sql", tags=["nl2sql"])
router = APIRouter(
    prefix="/nl2sql",
    tags=["nl2sql"],
    dependencies=[Depends(_require_persistence)],
)


@persistence_router.get(
    "/persistence",
    response_model=ApiResponse[PersistenceStatusData],
)
async def persistence_status() -> ApiResponse[PersistenceStatusData]:
    """NL2SQL incremental store の可用性を返す。"""
    return ApiResponse(data=nl2sql_service.persistence_status())


@persistence_router.post(
    "/persistence/recover",
    response_model=ApiResponse[PersistenceStatusData],
)
async def recover_persistence() -> ApiResponse[PersistenceStatusData]:
    """DB 復旧後に接続/migration を再確認する（業務 state は再読込しない）。"""
    return ApiResponse(data=nl2sql_service.recover_persistence())


def is_select_only(sql: str) -> bool:
    """Backward-compatible safety guard export for tests and callers."""
    return _is_select_only(sql)


@router.post("/preview", response_model=ApiResponse[PreviewData])
async def preview(req: PreviewRequest) -> ApiResponse[PreviewData]:
    """自然言語から SQL を生成して実行せずに safety / engine meta を返す。"""
    try:
        return ApiResponse(data=nl2sql_service.preview(req))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/execute", response_model=ApiResponse[QueryResults])
async def execute(req: ExecuteRequest) -> ApiResponse[QueryResults]:
    """SELECT/WITH のみを安全に実行する。

    local skeleton は deterministic mock result を返す。
    実運用では Oracle 実行 adapter へ差し替える。
    """
    try:
        allowed = nl2sql_service.resolve_allowed_objects(req.profile_id, req.allowed_objects)
        safety, _executable, results = nl2sql_service.execute_sql(
            sql=req.sql,
            allowed=allowed,
            row_limit=req.row_limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not safety.is_safe:
        raise HTTPException(status_code=400, detail=safety.blocked_reason)
    return ApiResponse(data=results)


@router.post("/jobs", response_model=ApiResponse[JobCreateData])
async def create_job(req: JobCreateRequest, request: Request) -> ApiResponse[JobCreateData]:
    """NL2SQL 検索 job を開始する。"""
    try:
        principal = getattr(request.state, "principal", None)
        actor_user_id = str(getattr(principal, "user_id", ""))
        return ApiResponse(
            data=nl2sql_service.start_job(
                req,
                actor_user_id=actor_user_id,
            )
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/jobs/{job_id}", response_model=ApiResponse[JobData])
async def get_job(job_id: str) -> ApiResponse[JobData]:
    """NL2SQL 検索 job の状態・結果を返す。"""
    job = nl2sql_service.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="指定されたジョブが見つかりません。")
    return ApiResponse(data=job)


@router.get("/profiles", response_model=ApiResponse[list[Nl2SqlProfile]])
async def list_profiles(
    response: Response, include_archived: bool = False
) -> ApiResponse[list[Nl2SqlProfile]]:
    """NL2SQL profile 一覧。"""
    response.headers["Deprecation"] = "true"
    response.headers["Sunset"] = "Wed, 30 Sep 2026 00:00:00 GMT"
    response.headers["Link"] = '</api/nl2sql/profiles/search>; rel="successor-version"'
    return ApiResponse(data=nl2sql_service.list_profiles(include_archived=include_archived))


@router.get("/profiles/search", response_model=ApiResponse[ProfileSummaryPage])
async def search_profiles(
    response: Response,
    cursor: str | None = None,
    limit: int = 50,
    q: str = "",
    include_archived: bool = False,
    if_none_match: Annotated[str | None, Header(alias="If-None-Match")] = None,
) -> ApiResponse[ProfileSummaryPage] | Response:
    """Full payload を返さない業務 profile keyset page。"""
    if limit < 1 or limit > 100:
        raise HTTPException(status_code=422, detail="limit は 1 から 100 で指定してください。")
    page = nl2sql_service.search_profiles(
        cursor=cursor,
        limit=limit,
        query=q,
        include_archived=include_archived,
    )
    quoted_etag = f'"profiles-{page.change_token}"'
    if if_none_match == quoted_etag:
        return Response(status_code=304, headers={"ETag": quoted_etag})
    response.headers["ETag"] = quoted_etag
    return ApiResponse(data=page)


@router.get("/profiles/{profile_id}", response_model=ApiResponse[Nl2SqlProfile])
async def get_profile_detail(
    profile_id: str,
    response: Response,
    if_none_match: Annotated[str | None, Header(alias="If-None-Match")] = None,
) -> ApiResponse[Nl2SqlProfile] | Response:
    """選択された profile だけを遅延取得する。"""
    try:
        profile = nl2sql_service.get_profile(profile_id, include_archived=True)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    quoted_etag = f'"{profile.etag}"'
    if profile.etag and if_none_match == quoted_etag:
        return Response(status_code=304, headers={"ETag": quoted_etag})
    if profile.etag:
        response.headers["ETag"] = quoted_etag
    return ApiResponse(data=profile)


@router.post("/profiles", response_model=ApiResponse[Nl2SqlProfile])
async def create_profile(
    req: ProfileUpsertRequest, response: Response
) -> ApiResponse[Nl2SqlProfile]:
    """NL2SQL profile を作成する。"""
    profile = Nl2SqlProfile(id=str(uuid.uuid4()), **req.model_dump())
    try:
        stored = nl2sql_service.create_profile(profile)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _materialize_incremental_profile_view(stored.id)
    if stored.etag:
        response.headers["ETag"] = f'"{stored.etag}"'
    return ApiResponse(data=stored)


@router.patch("/profiles/{profile_id}", response_model=ApiResponse[Nl2SqlProfile])
async def update_profile(
    profile_id: str,
    req: ProfileUpsertRequest,
    response: Response,
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> ApiResponse[Nl2SqlProfile]:
    """NL2SQL profile を更新する。"""
    try:
        if nl2sql_service.uses_incremental_store and not if_match:
            raise HTTPException(status_code=428, detail="If-Match header が必要です。")
        updated = nl2sql_service.update_profile(
            profile_id,
            lambda current: current.model_copy(update=req.model_dump()),
            expected_etag=if_match.strip('"') if if_match else None,
        )
    except IncrementalVersionConflict as exc:
        raise HTTPException(
            status_code=409,
            detail="業務 profile が更新されています。再読込してください。",
            headers={"ETag": f'"{exc.current_etag}"'},
        ) from exc
    except KeyError as exc:
        raise HTTPException(
            status_code=404, detail="指定された profile が見つかりません。"
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if updated.etag:
        response.headers["ETag"] = f'"{updated.etag}"'
    _materialize_incremental_profile_view(updated.id)
    return ApiResponse(data=updated)


def _materialize_incremental_profile_view(profile_id: str) -> None:
    if not nl2sql_service.uses_incremental_store:
        return
    # Local import avoids router construction cycles while keeping mutation-time materialization.
    from .ontology_router import ontology_runtime

    ontology_runtime.materialize_profile_view(profile_id)


@router.delete("/profiles/{profile_id}", response_model=ApiResponse[Nl2SqlProfile])
async def delete_profile(
    profile_id: str,
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> ApiResponse[Nl2SqlProfile]:
    """NL2SQL profile を物理削除する。"""
    try:
        if nl2sql_service.uses_incremental_store and not if_match:
            raise HTTPException(status_code=428, detail="If-Match header が必要です。")
        return ApiResponse(
            data=nl2sql_service.delete_profile(
                profile_id,
                expected_etag=if_match.strip('"') if if_match else None,
            )
        )
    except IncrementalVersionConflict as exc:
        raise HTTPException(
            status_code=409,
            detail="業務 profile が更新されています。再読込してください。",
            headers={"ETag": f'"{exc.current_etag}"'},
        ) from exc
    except KeyError as exc:
        raise HTTPException(
            status_code=404, detail="指定された profile が見つかりません。"
        ) from exc


@router.post(
    "/profiles/{profile_id}/learning-material/import",
    response_model=ApiResponse[ProfileLearningMaterialImportData],
)
async def import_profile_learning_material(
    profile_id: str,
    file: Annotated[UploadFile, File()],
    mode: Annotated[str, Form()] = "merge",
) -> ApiResponse[ProfileLearningMaterialImportData]:
    """旧版 terms/rules/few-shot CSV/XLSX を取り込む。rules は追加指示へ吸収する。"""
    content = await file.read()
    try:
        return ApiResponse(
            data=nl2sql_service.import_profile_learning_material(
                profile_id=profile_id,
                filename=file.filename or "learning_material.csv",
                content=content,
                mode=mode,
            )
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=404, detail="指定された profile が見つかりません。"
        ) from exc


@router.get("/profiles/{profile_id}/learning-material/export.xlsx")
async def export_profile_learning_material(profile_id: str) -> Response:
    """Profile learning material を Excel workbook として出力する。"""
    try:
        filename, content = nl2sql_service.export_profile_learning_material_xlsx(profile_id)
    except KeyError as exc:
        raise HTTPException(
            status_code=404, detail="指定された profile が見つかりません。"
        ) from exc
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get(
    "/legacy-learning-material",
    response_model=ApiResponse[LegacyLearningMaterialData],
)
async def get_legacy_learning_material() -> ApiResponse[LegacyLearningMaterialData]:
    """旧版 terms.xlsx / rules.xlsx 互換データ（グローバル用語集・グローバルルール）を返す。"""
    return ApiResponse(data=nl2sql_service.get_legacy_learning_material())


@router.post(
    "/legacy-learning-material/terms/import",
    response_model=ApiResponse[LegacyLearningMaterialData],
)
async def import_legacy_terms(
    file: Annotated[UploadFile, File()],
) -> ApiResponse[LegacyLearningMaterialData]:
    """旧版 terms.xlsx 互換の用語集を取り込む。"""
    return ApiResponse(
        data=nl2sql_service.import_legacy_terms(
            filename=file.filename or "terms.xlsx",
            content=await file.read(),
        )
    )


@router.post(
    "/legacy-learning-material/rules/import",
    response_model=ApiResponse[LegacyLearningMaterialData],
)
async def import_legacy_rules(
    file: Annotated[UploadFile, File()],
) -> ApiResponse[LegacyLearningMaterialData]:
    """旧版 rules.xlsx 互換のグローバルルールを取り込む。"""
    return ApiResponse(
        data=nl2sql_service.import_legacy_rules(
            filename=file.filename or "rules.xlsx",
            content=await file.read(),
        )
    )


@router.get("/legacy-learning-material/terms/export.xlsx")
async def export_legacy_terms() -> Response:
    """旧版 terms.xlsx 互換の用語集を Excel workbook として出力する。"""
    filename, content = nl2sql_service.export_legacy_terms_xlsx()
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/legacy-learning-material/rules/export.xlsx")
async def export_legacy_rules() -> Response:
    """旧版 rules.xlsx 互換のグローバルルールを Excel workbook として出力する。"""
    filename, content = nl2sql_service.export_legacy_rules_xlsx()
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/profiles/{profile_id}/archive", response_model=ApiResponse[Nl2SqlProfile])
async def archive_profile(profile_id: str) -> ApiResponse[Nl2SqlProfile]:
    """NL2SQL profile を archive する。"""
    try:
        return ApiResponse(data=nl2sql_service.archive_profile(profile_id))
    except KeyError as exc:
        raise HTTPException(
            status_code=404, detail="指定された profile が見つかりません。"
        ) from exc


@router.post("/profiles/{profile_id}/restore", response_model=ApiResponse[Nl2SqlProfile])
async def restore_profile(profile_id: str) -> ApiResponse[Nl2SqlProfile]:
    """archive 済みの NL2SQL profile を復元する。"""
    try:
        profile = nl2sql_service.restore_profile(profile_id)
        _materialize_incremental_profile_view(profile.id)
        return ApiResponse(data=profile)
    except KeyError as exc:
        raise HTTPException(
            status_code=404, detail="指定された profile が見つかりません。"
        ) from exc


@router.post(
    "/profiles/{profile_id}/select-ai-profile",
    response_model=ApiResponse[SelectAiDbProfileMutationData],
)
async def upsert_profile_select_ai_profile(
    profile_id: str,
    req: ProfileSelectAiProfileRequest,
) -> ApiResponse[SelectAiDbProfileMutationData]:
    """業務 profile から Oracle DBMS_CLOUD_AI profile を作成する。"""
    try:
        return ApiResponse(data=nl2sql_service.upsert_profile_select_ai_profile(profile_id, req))
    except KeyError as exc:
        raise HTTPException(
            status_code=404, detail="指定された profile が見つかりません。"
        ) from exc


@router.post("/select-ai/profiles/refresh", response_model=ApiResponse[AssetRefreshData])
async def refresh_select_ai_profile(profile_id: str | None = None) -> ApiResponse[AssetRefreshData]:
    """Oracle Select AI profile を作成/更新する adapter boundary。"""
    return ApiResponse(data=nl2sql_service.refresh_select_ai_profile(profile_id))


@router.post("/select-ai-agent/assets/refresh", response_model=ApiResponse[AssetRefreshData])
async def refresh_select_ai_agent_assets(
    profile_id: str | None = None,
) -> ApiResponse[AssetRefreshData]:
    """Oracle Select AI Agent assets を作成/更新する adapter boundary。"""
    return ApiResponse(data=nl2sql_service.refresh_select_ai_agent_assets(profile_id))


@router.post("/select-ai/assets/cleanup", response_model=ApiResponse[list[AssetCleanupData]])
async def cleanup_select_ai_assets(
    req: AssetCleanupRequest,
) -> ApiResponse[list[AssetCleanupData]]:
    """Oracle Select AI / Agent assets の cleanup を実行する。"""
    return ApiResponse(
        data=nl2sql_service.cleanup_select_ai_assets(
            profile_id=req.profile_id,
            engines=req.engines,
            confirmation=req.confirmation,
            reason=req.reason,
        )
    )


@router.get("/select-ai/db-profiles", response_model=ApiResponse[SelectAiDbProfilesData])
async def select_ai_db_profiles(
    include_detail: bool = False,
    business_profiles_only: bool = False,
    include_archived_business_profiles: bool = True,
) -> ApiResponse[SelectAiDbProfilesData]:
    """Oracle DBMS_CLOUD_AI profile 一覧を返す。"""
    return ApiResponse(
        data=nl2sql_service.list_select_ai_db_profiles(
            include_detail=include_detail,
            business_profiles_only=business_profiles_only,
            include_archived_business_profiles=include_archived_business_profiles,
        )
    )


@router.get(
    "/select-ai/db-profiles/{profile_name}",
    response_model=ApiResponse[SelectAiDbProfileDetailData],
)
async def select_ai_db_profile_detail(
    profile_name: str,
) -> ApiResponse[SelectAiDbProfileDetailData]:
    """Oracle DBMS_CLOUD_AI profile 詳細を返す。"""
    return ApiResponse(data=nl2sql_service.get_select_ai_db_profile(profile_name))


@router.get("/select-ai/feedback", response_model=ApiResponse[SelectAiFeedbackEntriesData])
async def select_ai_feedback(
    profile_name: str,
    limit: int = 50,
) -> ApiResponse[SelectAiFeedbackEntriesData]:
    """Oracle DBMS_CLOUD_AI profile feedback vector entries を返す。"""
    return ApiResponse(data=nl2sql_service.list_select_ai_feedback_entries(profile_name, limit))


@router.post(
    "/select-ai/feedback/add",
    response_model=ApiResponse[SelectAiFeedbackAddData],
)
async def add_select_ai_feedback(
    req: SelectAiFeedbackAddRequest,
) -> ApiResponse[SelectAiFeedbackAddData]:
    """Oracle DBMS_CLOUD_AI profile feedback entry を追加する。"""
    return ApiResponse(data=nl2sql_service.add_select_ai_feedback(req))


@router.post(
    "/select-ai/feedback/delete",
    response_model=ApiResponse[SelectAiFeedbackMutationData],
)
async def delete_select_ai_feedback(
    req: SelectAiFeedbackDeleteRequest,
) -> ApiResponse[SelectAiFeedbackMutationData]:
    """Oracle DBMS_CLOUD_AI profile feedback entry を削除する。"""
    return ApiResponse(data=nl2sql_service.delete_select_ai_feedback(req))


@router.post(
    "/select-ai/feedback/vector-index",
    response_model=ApiResponse[SelectAiFeedbackMutationData],
)
async def update_select_ai_feedback_vector_index(
    req: SelectAiFeedbackVectorIndexRequest,
) -> ApiResponse[SelectAiFeedbackMutationData]:
    """Oracle DBMS_CLOUD_AI feedback vector index attributes を更新する。"""
    return ApiResponse(data=nl2sql_service.update_select_ai_feedback_vector_index(req))


@router.post(
    "/select-ai/db-profiles",
    response_model=ApiResponse[SelectAiDbProfileMutationData],
)
async def upsert_select_ai_db_profile(
    req: SelectAiDbProfileUpsertRequest,
) -> ApiResponse[SelectAiDbProfileMutationData]:
    """Oracle DBMS_CLOUD_AI profile を low-level JSON から作成/更新する。"""
    return ApiResponse(data=nl2sql_service.upsert_select_ai_db_profile(req))


@router.patch(
    "/select-ai/db-profiles/{profile_name}",
    response_model=ApiResponse[SelectAiDbProfileMutationData],
)
async def patch_select_ai_db_profile(
    profile_name: str,
    req: SelectAiDbProfileUpsertRequest,
) -> ApiResponse[SelectAiDbProfileMutationData]:
    """Oracle DBMS_CLOUD_AI profile を名前指定で更新する。"""
    return ApiResponse(
        data=nl2sql_service.upsert_select_ai_db_profile(
            req.model_copy(
                update={
                    "profile_name": req.profile_name or profile_name,
                    "original_name": req.original_name or profile_name,
                }
            )
        )
    )


@router.get(
    "/select-ai/profiles/export.json",
    response_model=ApiResponse[SelectAiProfilesExportData],
)
async def export_select_ai_profiles_json(
    business_profiles_only: bool = False,
    include_archived_business_profiles: bool = True,
) -> ApiResponse[SelectAiProfilesExportData]:
    """Oracle DBMS_CLOUD_AI profile definitions を JSON として返す。"""
    return ApiResponse(
        data=nl2sql_service.export_select_ai_profiles_json(
            business_profiles_only=business_profiles_only,
            include_archived_business_profiles=include_archived_business_profiles,
        )
    )


@router.post(
    "/select-ai/profiles/import-json",
    response_model=ApiResponse[list[SelectAiDbProfileMutationData]],
)
async def import_select_ai_profiles_json(
    req: SelectAiProfilesImportRequest,
) -> ApiResponse[list[SelectAiDbProfileMutationData]]:
    """Oracle DBMS_CLOUD_AI profile definitions JSON を import する。"""
    return ApiResponse(data=nl2sql_service.import_select_ai_profiles_json(req))


@router.post(
    "/select-ai/db-profiles/{profile_name}/drop",
    response_model=ApiResponse[AssetCleanupData],
)
async def drop_select_ai_db_profile(
    profile_name: str,
    req: SelectAiDbProfileDropRequest,
) -> ApiResponse[AssetCleanupData]:
    """Oracle DBMS_CLOUD_AI profile を名前指定で drop する。"""
    return ApiResponse(
        data=nl2sql_service.drop_select_ai_db_profile(
            profile_name,
            confirmation=req.confirmation,
            reason=req.reason,
        )
    )


@router.get("/select-ai-agent/assets", response_model=ApiResponse[SelectAiAgentAssetsData])
async def select_ai_agent_assets() -> ApiResponse[SelectAiAgentAssetsData]:
    """Oracle Select AI Agent low-level asset names を返す。"""
    return ApiResponse(data=nl2sql_service.list_select_ai_agent_assets())


@router.post("/select-ai-agent/run-team", response_model=ApiResponse[AgentTeamRunData])
async def run_select_ai_agent_team(
    req: AgentTeamRunRequest,
) -> ApiResponse[AgentTeamRunData]:
    """Oracle Select AI Agent team を実行する。"""
    return ApiResponse(data=nl2sql_service.run_select_ai_agent_team(req))


@router.post("/select-ai-agent/run-tool", response_model=ApiResponse[AgentTeamRunData])
async def run_select_ai_agent_tool(
    req: AgentToolRunRequest,
) -> ApiResponse[AgentTeamRunData]:
    """Oracle Select AI Agent tool を明示名で実行する。"""
    return ApiResponse(data=nl2sql_service.run_select_ai_agent_tool(req))


@router.post(
    "/select-ai-agent/conversations/create",
    response_model=ApiResponse[AgentConversationCreateData],
)
async def create_select_ai_agent_conversation(
    req: AgentConversationCreateRequest,
) -> ApiResponse[AgentConversationCreateData]:
    """Oracle Select AI Agent conversation を作成する。"""
    return ApiResponse(data=nl2sql_service.create_select_ai_agent_conversation(req))


@router.post("/select-ai-agent/assets/cleanup", response_model=ApiResponse[list[AssetCleanupData]])
async def cleanup_select_ai_agent_assets(
    req: AssetCleanupRequest,
) -> ApiResponse[list[AssetCleanupData]]:
    """Oracle Select AI Agent low-level assets の cleanup を実行する。"""
    return ApiResponse(data=nl2sql_service.cleanup_select_ai_agent_assets_low_level(req))


@router.get(
    "/select-ai-agent/conversations",
    response_model=ApiResponse[AgentConversationsData],
)
async def select_ai_agent_conversations(
    team_name: str | None = None,
    limit: int = 20,
) -> ApiResponse[AgentConversationsData]:
    """Oracle Select AI Agent conversation 履歴を返す。"""
    return ApiResponse(
        data=nl2sql_service.list_select_ai_agent_conversations(
            team_name=team_name,
            limit=max(1, min(limit, 100)),
        )
    )


@router.get(
    "/select-ai-agent/privileges/check",
    response_model=ApiResponse[AgentPrivilegeCheckData],
)
async def check_select_ai_agent_privileges() -> ApiResponse[AgentPrivilegeCheckData]:
    """Oracle Select AI Agent 実行に必要な package/view 可視性を確認する。"""
    return ApiResponse(data=nl2sql_service.check_select_ai_agent_privileges())


@router.get("/history", response_model=ApiResponse[HistoryData])
async def history() -> ApiResponse[HistoryData]:
    """NL2SQL 検索履歴。"""
    return ApiResponse(data=nl2sql_service.list_history())


@router.post("/feedback", response_model=ApiResponse[FeedbackData])
async def feedback(req: FeedbackRequest) -> ApiResponse[FeedbackData]:
    """検索結果 feedback を保存する。"""
    try:
        data = nl2sql_service.save_feedback(req.history_id, req.rating, req.comment)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="対象の SQL 履歴が見つかりません。") from exc
    return ApiResponse(data=data)


@router.get("/feedback", response_model=ApiResponse[FeedbackListData])
async def list_feedback(
    cursor: str | None = None,
    limit: int = 20,
    rating: str = "all",
    profile_id: str = "",
    q: str = "",
) -> ApiResponse[FeedbackListData]:
    """アプリ内 SQL feedback を Profile/評価/キーワードで一覧する。"""
    if rating not in {"all", "good", "bad", "unrated"}:
        raise HTTPException(status_code=422, detail="rating が不正です。")
    try:
        data = nl2sql_service.list_feedback(
            cursor=cursor,
            limit=max(1, min(limit, 100)),
            rating=rating,
            profile_id=profile_id.strip(),
            query=q.strip(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return ApiResponse(data=data)


@router.delete("/feedback/{history_id}", response_model=ApiResponse[FeedbackClearData])
async def clear_feedback(history_id: str) -> ApiResponse[FeedbackClearData]:
    """SQL 履歴を残したままアプリ内 feedback だけを解除する。"""
    try:
        data = nl2sql_service.clear_feedback(history_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="対象の SQL 履歴が見つかりません。") from exc
    return ApiResponse(data=data)


@router.post("/demo/learning", response_model=ApiResponse[DemoLearningData])
async def seed_demo_learning() -> ApiResponse[DemoLearningData]:
    """Learning / feedback 画面の検証用 demo データを投入する。"""
    return ApiResponse(data=nl2sql_service.seed_demo_learning_data())


@router.get("/sample-data", response_model=ApiResponse[SampleDataInfo])
async def sample_data_info() -> ApiResponse[SampleDataInfo]:
    """Optional SQL Assist sample package status / SQL preview."""
    return ApiResponse(data=nl2sql_service.sample_data_info())


@router.post("/sample-data/import", response_model=ApiResponse[SampleDataMutationData])
async def import_sample_data(
    req: SampleDataMutationRequest,
) -> ApiResponse[SampleDataMutationData]:
    """Optional SQL Assist sample data import execution."""
    return ApiResponse(data=nl2sql_service.import_sample_data(req))


@router.post("/sample-data/delete", response_model=ApiResponse[SampleDataMutationData])
async def delete_sample_data(
    req: SampleDataMutationRequest,
) -> ApiResponse[SampleDataMutationData]:
    """Optional SQL Assist sample data delete execution."""
    return ApiResponse(data=nl2sql_service.delete_sample_data(req))


@router.get("/feedback-index", response_model=ApiResponse[FeedbackIndexData])
async def feedback_index_status() -> ApiResponse[FeedbackIndexData]:
    """Feedback learning vector index の状態を返す。"""
    return ApiResponse(data=nl2sql_service.feedback_index_status())


@router.post("/feedback-index/rebuild", response_model=ApiResponse[FeedbackIndexData])
async def rebuild_feedback_index(req: FeedbackIndexRequest) -> ApiResponse[FeedbackIndexData]:
    """Feedback learning vector index の再構築 plan / 実行。"""
    return ApiResponse(data=nl2sql_service.rebuild_feedback_index(req))


@router.post("/feedback-index/clear", response_model=ApiResponse[FeedbackIndexData])
async def clear_feedback_index(req: FeedbackIndexRequest) -> ApiResponse[FeedbackIndexData]:
    """Feedback learning vector index の clear plan / 実行。"""
    return ApiResponse(data=nl2sql_service.clear_feedback_index(req))


@router.get("/feedback-entries", response_model=ApiResponse[FeedbackEntriesData])
async def feedback_entries() -> ApiResponse[FeedbackEntriesData]:
    """Feedback learning entries を一覧する。"""
    return ApiResponse(data=nl2sql_service.list_feedback_entries())


@router.post("/feedback-entries/delete", response_model=ApiResponse[FeedbackEntriesData])
async def delete_feedback_entries(
    req: FeedbackEntriesDeleteRequest,
) -> ApiResponse[FeedbackEntriesData]:
    """Feedback learning entries を削除する。"""
    return ApiResponse(data=nl2sql_service.delete_feedback_entries(req.history_ids))


@router.get("/feedback-config", response_model=ApiResponse[FeedbackSearchConfigData])
async def feedback_config() -> ApiResponse[FeedbackSearchConfigData]:
    """Feedback similar-history default config を返す。"""
    return ApiResponse(data=nl2sql_service.feedback_search_config())


@router.patch("/feedback-config", response_model=ApiResponse[FeedbackSearchConfigData])
async def update_feedback_config(
    req: FeedbackSearchConfigRequest,
) -> ApiResponse[FeedbackSearchConfigData]:
    """Feedback similar-history default config を更新する。"""
    return ApiResponse(data=nl2sql_service.update_feedback_search_config(req))


@router.get("/classifier", response_model=ApiResponse[ClassifierStatusData])
async def classifier_status() -> ApiResponse[ClassifierStatusData]:
    """Embedding + LogisticRegression classifier の状態を返す。"""
    return ApiResponse(data=nl2sql_service.classifier_status())


@router.get("/classifier/training-data", response_model=ApiResponse[ClassifierTrainingDataData])
async def classifier_training_data() -> ApiResponse[ClassifierTrainingDataData]:
    """Classifier training data 一覧を返す。"""
    return ApiResponse(data=nl2sql_service.classifier_training_data())


@router.get(
    "/classifier/training-candidates",
    response_model=ApiResponse[ClassifierTrainingCandidatesData],
)
async def classifier_training_candidates(
    cursor: str | None = None,
    limit: int = 20,
    status: str = "all",
    profile_id: str = "",
    q: str = "",
    history_id: str = "",
) -> ApiResponse[ClassifierTrainingCandidatesData]:
    """good feedback から質問/Profile training 候補を導出する。"""
    allowed_statuses = {
        "all",
        "pending",
        "added",
        "already_covered",
        "conflict",
        "profile_missing",
        "source_changed",
    }
    if status not in allowed_statuses:
        raise HTTPException(status_code=422, detail="status が不正です。")
    try:
        data = nl2sql_service.classifier_training_candidates(
            cursor=cursor,
            limit=max(1, min(limit, 100)),
            status=status,
            profile_id=profile_id.strip(),
            query=q.strip(),
            history_id=history_id.strip(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return ApiResponse(data=data)


@router.post(
    "/classifier/training-data/from-feedback",
    response_model=ApiResponse[ClassifierFeedbackImportData],
)
async def import_classifier_training_data_from_feedback(
    req: ClassifierFeedbackImportRequest,
) -> ApiResponse[ClassifierFeedbackImportData]:
    """確認済み feedback の質問/Profile 対応を training data に追加する。"""
    return ApiResponse(data=nl2sql_service.import_classifier_feedback_examples(req))


@router.patch(
    "/classifier/training-data/{example_id}",
    response_model=ApiResponse[ClassifierTrainingExample],
)
async def update_classifier_training_example(
    example_id: str,
    req: ClassifierTrainingExampleUpdateRequest,
) -> ApiResponse[ClassifierTrainingExample]:
    try:
        data = nl2sql_service.update_classifier_training_example(example_id, req)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Training data が見つかりません。") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return ApiResponse(data=data)


@router.delete(
    "/classifier/training-data/{example_id}",
    response_model=ApiResponse[ClassifierTrainingDataData],
)
async def delete_classifier_training_example(
    example_id: str,
) -> ApiResponse[ClassifierTrainingDataData]:
    try:
        data = nl2sql_service.delete_classifier_training_example(example_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Training data が見つかりません。") from exc
    return ApiResponse(data=data)


@router.post("/classifier/training-data/import", response_model=ApiResponse[ClassifierImportData])
async def import_classifier_training_data(
    file: Annotated[UploadFile, File()],
    replace: Annotated[bool, Form()] = False,
    profile_id: Annotated[str | None, Form()] = None,
) -> ApiResponse[ClassifierImportData]:
    """CATEGORY/TEXT の CSV/XLSX training data を取り込む。"""
    content = await file.read()
    return ApiResponse(
        data=nl2sql_service.import_classifier_training_data(
            filename=file.filename or "training_data.csv",
            content=content,
            replace=replace,
            profile_id=profile_id,
        )
    )


@router.get("/classifier/training-data/export.xlsx")
async def export_classifier_training_data_xlsx() -> Response:
    """Classifier training data を Excel workbook として出力する。"""
    filename, content = nl2sql_service.export_classifier_training_data_xlsx()
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/classifier/train", response_model=ApiResponse[ClassifierStatusData])
async def train_classifier(req: ClassifierTrainRequest) -> ApiResponse[ClassifierStatusData]:
    """Imported training data から LogisticRegression classifier を学習する。"""
    return ApiResponse(data=nl2sql_service.train_classifier(req))


@router.post("/classifier/model/import", response_model=ApiResponse[ClassifierModelImportData])
async def import_classifier_model(
    file: Annotated[UploadFile, File()],
) -> ApiResponse[ClassifierModelImportData]:
    """唯一の classifier model を joblib / JSON artifact で置き換える。"""
    content = await file.read()
    return ApiResponse(
        data=nl2sql_service.import_classifier_model_artifact(
            filename=file.filename or "classifier.joblib",
            content=content,
        )
    )


@router.post("/classifier/models/import", response_model=ApiResponse[ClassifierModelImportData])
async def import_classifier_model_legacy(
    file: Annotated[UploadFile, File()],
    activate: Annotated[bool, Form()] = True,
) -> ApiResponse[ClassifierModelImportData]:
    """旧複数形 URL。単一モデルとして置き換える場合のみ受理する。"""
    if not activate:
        raise HTTPException(
            status_code=422,
            detail="単一モデル管理では activate=false を指定できません。",
        )
    return await import_classifier_model(file)


@router.post("/classifier/predict", response_model=ApiResponse[ClassifierPredictionData])
async def predict_classifier(
    req: ClassifierPredictRequest,
) -> ApiResponse[ClassifierPredictionData]:
    """質問を classifier category/profile 候補へ分類する。"""
    return ApiResponse(data=nl2sql_service.predict_classifier(req))


@router.post("/similar-history", response_model=ApiResponse[SimilarHistoryData])
async def similar_history(req: SimilarHistoryRequest) -> ApiResponse[SimilarHistoryData]:
    """質問に近い履歴を few-shot / feedback 学習候補として返す。"""
    return ApiResponse(data=nl2sql_service.similar_history(req))


@router.post("/recommend-profile", response_model=ApiResponse[ProfileRecommendationData])
async def recommend_profile(
    req: ProfileRecommendationRequest,
) -> ApiResponse[ProfileRecommendationData]:
    """質問から profile / schema 範囲と query rewrite を推薦する。"""
    return ApiResponse(data=nl2sql_service.recommend_profile(req))


@router.post("/rewrite", response_model=ApiResponse[RewriteData])
async def rewrite(req: RewriteRequest) -> ApiResponse[RewriteData]:
    """用語・schema・追加指示を使って質問を書き換える。"""
    return ApiResponse(data=nl2sql_service.rewrite(req))


@router.post("/analyze", response_model=ApiResponse[AnalyzeData])
async def analyze(req: AnalyzeRequest) -> ApiResponse[AnalyzeData]:
    """SQL の安全性・参照表・推奨修正を返す。"""
    return ApiResponse(
        data=nl2sql_service.analyze_sql(
            req.sql,
            req.allowed_objects,
            req.row_limit,
            use_llm=req.use_llm,
        )
    )


@router.post("/repair", response_model=ApiResponse[RepairData])
async def repair(req: RepairRequest) -> ApiResponse[RepairData]:
    """Oracle error message に基づいて SELECT SQL の修復候補を返す。"""
    return ApiResponse(
        data=nl2sql_service.repair_oracle_error(
            req,
            req.row_limit,
        )
    )


@router.post("/evaluate", response_model=ApiResponse[EvaluateData])
async def evaluate(req: EvaluateRequest) -> ApiResponse[EvaluateData]:
    """Deterministic NL2SQL 評価。外部 LLM-as-judge は使わない。"""
    return ApiResponse(data=nl2sql_service.evaluate(req))


@router.get("/evaluation-runs", response_model=ApiResponse[EvaluationRunsData])
async def evaluation_runs(limit: int = 20) -> ApiResponse[EvaluationRunsData]:
    """保存済み deterministic NL2SQL 評価実行 record の最近分を返す。"""
    return ApiResponse(data=nl2sql_service.list_evaluation_runs(limit=max(1, min(limit, 100))))


@router.get("/evaluation-sets", response_model=ApiResponse[EvaluationSetsData])
async def evaluation_sets(include_archived: bool = False) -> ApiResponse[EvaluationSetsData]:
    """保存済み NL2SQL 評価セットを返す。"""
    return ApiResponse(data=nl2sql_service.list_evaluation_sets(include_archived=include_archived))


@router.post("/evaluation-sets", response_model=ApiResponse[EvaluationSet])
async def create_evaluation_set(req: EvaluationSetUpsertRequest) -> ApiResponse[EvaluationSet]:
    """NL2SQL 評価セットを作成する。"""
    return ApiResponse(data=nl2sql_service.create_evaluation_set(req))


@router.patch("/evaluation-sets/{evaluation_set_id}", response_model=ApiResponse[EvaluationSet])
async def update_evaluation_set(
    evaluation_set_id: str, req: EvaluationSetUpsertRequest
) -> ApiResponse[EvaluationSet]:
    """NL2SQL 評価セットを更新する。"""
    try:
        return ApiResponse(data=nl2sql_service.update_evaluation_set(evaluation_set_id, req))
    except KeyError as exc:
        raise HTTPException(
            status_code=404, detail="指定された評価セットが見つかりません。"
        ) from exc


@router.post(
    "/evaluation-sets/{evaluation_set_id}/archive",
    response_model=ApiResponse[EvaluationSet],
)
async def archive_evaluation_set(evaluation_set_id: str) -> ApiResponse[EvaluationSet]:
    """NL2SQL 評価セットを archive する。"""
    try:
        return ApiResponse(data=nl2sql_service.archive_evaluation_set(evaluation_set_id))
    except KeyError as exc:
        raise HTTPException(
            status_code=404, detail="指定された評価セットが見つかりません。"
        ) from exc


@router.post("/compare", response_model=ApiResponse[CompareData])
async def compare(req: CompareRequest) -> ApiResponse[CompareData]:
    """同一質問を複数 engine で preview し、SQL・安全性・時間を比較する。"""
    return ApiResponse(data=nl2sql_service.compare_engines(req))


@router.get("/compare-history", response_model=ApiResponse[CompareHistoryData])
async def compare_history(limit: int = 20) -> ApiResponse[CompareHistoryData]:
    """保存済み engine 比較 record の最近分を返す。"""
    return ApiResponse(data=nl2sql_service.list_compare_records(limit=max(1, min(limit, 50))))


@router.post("/reverse", response_model=ApiResponse[ReverseSqlData])
async def reverse(req: ReverseSqlRequest) -> ApiResponse[ReverseSqlData]:
    """SQL から自然言語説明を生成する。"""
    return ApiResponse(data=nl2sql_service.reverse_sql(req))


@router.post("/reverse/deep", response_model=ApiResponse[ReverseSqlData])
async def reverse_deep(req: ReverseSqlRequest) -> ApiResponse[ReverseSqlData]:
    """SQL から Enterprise AI backed の自然言語説明を生成する。"""
    return ApiResponse(data=nl2sql_service.reverse_sql_deep(req))


@router.post("/comments/suggest", response_model=ApiResponse[CommentSuggestionData])
async def suggest_comments(
    req: CommentSuggestionRequest | None = None,
) -> ApiResponse[CommentSuggestionData]:
    """表/列コメント候補を deterministic / Enterprise AI で生成する。"""
    return ApiResponse(data=nl2sql_service.suggest_comments(req))


@router.post("/comments/generate-sql", response_model=ApiResponse[MetadataSqlGenerateData])
async def generate_comment_sql(
    req: MetadataSqlGenerateRequest,
) -> ApiResponse[MetadataSqlGenerateData]:
    """SQL Assist コメント管理互換の COMMENT ON SQL を生成する。"""
    return ApiResponse(data=nl2sql_service.generate_comment_sql(req))


@router.post("/metadata-samples", response_model=ApiResponse[MetadataSqlSampleData])
async def metadata_samples(
    req: MetadataSqlSampleRequest,
) -> ApiResponse[MetadataSqlSampleData]:
    """コメント/アノテーション SQL 生成向けの列代表値を再取得する。"""
    return ApiResponse(data=nl2sql_service.get_metadata_samples(req))


@router.post("/comments/apply", response_model=ApiResponse[CommentApplyData])
async def apply_comments(req: CommentApplyRequest) -> ApiResponse[CommentApplyData]:
    """COMMENT ON TABLE/COLUMN の restricted execution。"""
    return ApiResponse(data=nl2sql_service.apply_comments(req))


@router.post("/annotations/generate", response_model=ApiResponse[AnnotationSuggestionData])
async def generate_annotations() -> ApiResponse[AnnotationSuggestionData]:
    """Oracle annotation 候補を生成する。"""
    return ApiResponse(data=nl2sql_service.suggest_annotations())


@router.post("/annotations/generate-sql", response_model=ApiResponse[MetadataSqlGenerateData])
async def generate_annotation_sql(
    req: MetadataSqlGenerateRequest,
) -> ApiResponse[MetadataSqlGenerateData]:
    """SQL Assist アノテーション管理互換の ALTER ... ANNOTATIONS SQL を生成する。"""
    return ApiResponse(data=nl2sql_service.generate_annotation_sql(req))


@router.post("/annotations/apply", response_model=ApiResponse[AnnotationApplyData])
async def apply_annotations(req: AnnotationApplyRequest) -> ApiResponse[AnnotationApplyData]:
    """Oracle annotation の restricted execution。"""
    return ApiResponse(data=nl2sql_service.apply_annotations(req))


@router.get("/db-admin/tables", response_model=ApiResponse[DbAdminObjectsData])
async def db_admin_tables() -> ApiResponse[DbAdminObjectsData]:
    """DB admin table 一覧を返す。"""
    return ApiResponse(data=nl2sql_service.list_db_admin_tables())


@router.get("/db-admin/tables/{table_name}", response_model=ApiResponse[DbAdminObjectDetail])
async def db_admin_table_detail(
    table_name: str, include_ddl: bool = True, exact_count: bool = False
) -> ApiResponse[DbAdminObjectDetail]:
    """DB admin table 詳細/DDL を返す。

    include_ddl=false で重い GET_DDL を省略(列一覧の初期表示を高速化)。
    exact_count=false は num_rows 統計、true のみ COUNT(*) で正確件数を取得。
    """
    return ApiResponse(
        data=nl2sql_service.get_db_admin_object(
            table_name, "table", include_ddl=include_ddl, exact_count=exact_count
        )
    )


@router.get("/db-admin/views", response_model=ApiResponse[DbAdminObjectsData])
async def db_admin_views() -> ApiResponse[DbAdminObjectsData]:
    """DB admin view 一覧を返す。"""
    return ApiResponse(data=nl2sql_service.list_db_admin_views())


@router.get("/db-admin/views/{view_name}", response_model=ApiResponse[DbAdminObjectDetail])
async def db_admin_view_detail(
    view_name: str, include_ddl: bool = True, exact_count: bool = False
) -> ApiResponse[DbAdminObjectDetail]:
    """DB admin view 詳細/DDL を返す。

    include_ddl=false で重い GET_DDL を省略(列一覧の初期表示を高速化)。
    exact_count=false は num_rows 統計、true のみ COUNT(*) で正確件数を取得(view は通常 None)。
    """
    return ApiResponse(
        data=nl2sql_service.get_db_admin_object(
            view_name, "view", include_ddl=include_ddl, exact_count=exact_count
        )
    )


@router.post("/db-admin/drop-table", response_model=ApiResponse[DbAdminExecuteData])
async def db_admin_drop_table(req: DbAdminDropTableRequest) -> ApiResponse[DbAdminExecuteData]:
    """DB admin DROP TABLE execution。"""
    return ApiResponse(data=nl2sql_service.drop_db_admin_table(req))


@router.post("/db-admin/execute", response_model=ApiResponse[DbAdminExecuteData])
async def db_admin_execute(req: DbAdminExecuteRequest) -> ApiResponse[DbAdminExecuteData]:
    """DB admin SQL executor。通常 NL2SQL 実行 path とは分離する。"""
    return ApiResponse(data=nl2sql_service.execute_db_admin_sql(req))


@router.post("/db-admin/statements", response_model=ApiResponse[DbAdminExecuteData])
async def db_admin_statements(req: DbAdminStatementsRequest) -> ApiResponse[DbAdminExecuteData]:
    """文種 whitelist 付き複数 statement 実行(テーブル/ビュー作成・データ SQL)。"""
    return ApiResponse(data=nl2sql_service.execute_db_admin_statements(req))


@router.post("/db-admin/drop-view", response_model=ApiResponse[DbAdminExecuteData])
async def db_admin_drop_view(req: DbAdminDropViewRequest) -> ApiResponse[DbAdminExecuteData]:
    """DB admin DROP VIEW execution。"""
    return ApiResponse(data=nl2sql_service.drop_db_admin_view(req))


@router.post("/db-admin/preview-data", response_model=ApiResponse[DbAdminDataPreviewData])
async def db_admin_preview_data(
    req: DbAdminDataPreviewRequest,
) -> ApiResponse[DbAdminDataPreviewData]:
    """テーブル/ビューのデータ表示(件数上限+任意 WHERE)。"""
    try:
        return ApiResponse(data=nl2sql_service.preview_db_admin_data(req))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/db-admin/preview-data/export.xlsx")
async def db_admin_export_preview_xlsx(req: DbAdminDataPreviewRequest) -> Response:
    """テーブル/ビューの表示結果を Excel workbook として出力する。"""
    try:
        filename, content = nl2sql_service.export_db_admin_preview_xlsx(req)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/db-admin/upload-csv", response_model=ApiResponse[DbAdminCsvUploadData])
async def db_admin_upload_csv(
    req: DbAdminCsvUploadRequest,
) -> ApiResponse[DbAdminCsvUploadData]:
    """既存テーブルへの CSV アップロード(INSERT / TRUNCATE&INSERT)。"""
    try:
        return ApiResponse(data=nl2sql_service.upload_db_admin_csv(req))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/db-admin/analyze-error", response_model=ApiResponse[DbAdminAiAnalysisData])
async def db_admin_analyze_error(
    req: DbAdminAiAnalysisRequest,
) -> ApiResponse[DbAdminAiAnalysisData]:
    """Admin SQL 実行結果の AI 分析(OCI Enterprise AI、未設定時は deterministic)。"""
    return ApiResponse(data=nl2sql_service.analyze_db_admin_failure(req))


@router.post("/db-admin/extract-join-where", response_model=ApiResponse[DbAdminJoinWhereData])
async def db_admin_extract_join_where(
    req: DbAdminJoinWhereRequest,
) -> ApiResponse[DbAdminJoinWhereData]:
    """ビュー DDL から JOIN/WHERE 条件を抽出する(OCI Enterprise AI、未設定時は deterministic)。"""
    return ApiResponse(data=nl2sql_service.extract_db_admin_join_where(req))


@router.post("/db-admin/import-tabular", response_model=ApiResponse[DbAdminImportTabularData])
async def db_admin_import_tabular(
    req: DbAdminImportTabularRequest,
) -> ApiResponse[DbAdminImportTabularData]:
    """CSV/XLSX tabular data を DB admin tool から import する。"""
    return ApiResponse(data=nl2sql_service.import_db_admin_tabular(req))


@router.get("/db-admin/tables/{table_name}/export.xlsx")
async def db_admin_export_table_xlsx(table_name: str, limit: int = 1000) -> Response:
    """DB admin table の列情報を Excel workbook として出力する。"""
    filename, content = nl2sql_service.export_db_admin_table_xlsx(
        table_name,
        limit=max(1, min(limit, 50000)),
    )
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/synthetic-cases", response_model=ApiResponse[SyntheticCasesData])
async def synthetic_cases(
    profile_id: str | None = None, limit: int = 6
) -> ApiResponse[SyntheticCasesData]:
    """Synthetic NL2SQL 評価ケースを生成する。"""
    return ApiResponse(data=nl2sql_service.synthetic_cases(profile_id=profile_id, limit=limit))


@router.post("/synthetic-data/generate", response_model=ApiResponse[SyntheticDataOperationData])
async def generate_synthetic_data(
    req: SyntheticDataGenerateRequest,
) -> ApiResponse[SyntheticDataOperationData]:
    """DBMS_CLOUD_AI synthetic table data generation execution。"""
    return ApiResponse(data=nl2sql_service.generate_synthetic_data(req))


@router.get(
    "/synthetic-data/operations/{operation_id}",
    response_model=ApiResponse[SyntheticDataOperationStatusData],
)
async def synthetic_data_operation_status(
    operation_id: str,
) -> ApiResponse[SyntheticDataOperationStatusData]:
    """DBMS_CLOUD_AI synthetic data operation status を返す。"""
    return ApiResponse(data=nl2sql_service.synthetic_data_operation_status(operation_id))


@router.get("/synthetic-data/results", response_model=ApiResponse[SyntheticDataResultsData])
async def synthetic_data_results(
    table_name: str,
    limit: int = 100,
) -> ApiResponse[SyntheticDataResultsData]:
    """Synthetic DB data generation 後の table preview を返す。"""
    return ApiResponse(
        data=nl2sql_service.synthetic_data_results(
            table_name=table_name,
            limit=max(1, min(limit, 1000)),
        )
    )


@router.get("/diagnostics", response_model=ApiResponse[DiagnosticsData])
async def diagnostics() -> ApiResponse[DiagnosticsData]:
    """OCI / Oracle / NL2SQL エンジン設定の非 secret 診断を返す。"""
    return ApiResponse(data=nl2sql_service.diagnostics())
