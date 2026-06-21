"""NL2SQL feature router."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException
from pr_backend_core import ApiResponse

from .models import (
    AnalyzeData,
    AnalyzeRequest,
    AssetRefreshData,
    CommentApplyData,
    CommentApplyRequest,
    CommentSuggestionData,
    CompareData,
    CompareHistoryData,
    CompareRequest,
    DemoLearningData,
    DiagnosticsData,
    EvaluateData,
    EvaluateRequest,
    EvaluationRunsData,
    EvaluationSet,
    EvaluationSetsData,
    EvaluationSetUpsertRequest,
    ExecuteRequest,
    FeedbackData,
    FeedbackIndexData,
    FeedbackIndexRequest,
    FeedbackRequest,
    HistoryData,
    JobCreateData,
    JobCreateRequest,
    JobData,
    Nl2SqlProfile,
    PreviewData,
    PreviewRequest,
    ProfileRecommendationData,
    ProfileRecommendationRequest,
    ProfileUpsertRequest,
    QueryResults,
    RepairData,
    RepairRequest,
    ReverseSqlData,
    ReverseSqlRequest,
    SimilarHistoryData,
    SimilarHistoryRequest,
    SyntheticCasesData,
)
from .service import enforce_row_limit, nl2sql_service
from .service import is_select_only as _is_select_only

router = APIRouter(prefix="/nl2sql", tags=["nl2sql"])


def is_select_only(sql: str) -> bool:
    """Backward-compatible safety guard export for tests and callers."""
    return _is_select_only(sql)


@router.post("/preview", response_model=ApiResponse[PreviewData])
async def preview(req: PreviewRequest) -> ApiResponse[PreviewData]:
    """自然言語から SQL を生成して実行せずに safety / engine meta を返す。"""
    return ApiResponse(data=nl2sql_service.preview(req))


@router.post("/execute", response_model=ApiResponse[QueryResults])
async def execute(req: ExecuteRequest) -> ApiResponse[QueryResults]:
    """SELECT/WITH のみを安全に実行する。

    local skeleton は deterministic mock result を返す。
    実運用では Oracle 実行 adapter へ差し替える。
    """
    row_limit = req.row_limit or nl2sql_service.get_profile(req.profile_id).default_row_limit
    safety, _executable, results = nl2sql_service.execute_sql(
        sql=enforce_row_limit(req.sql, row_limit),
        allowed=req.allowed_objects,
        row_limit=row_limit,
    )
    if not safety.is_safe:
        raise HTTPException(status_code=400, detail=safety.blocked_reason)
    return ApiResponse(data=results)


@router.post("/jobs", response_model=ApiResponse[JobCreateData])
async def create_job(req: JobCreateRequest) -> ApiResponse[JobCreateData]:
    """NL2SQL 検索 job を開始する。"""
    return ApiResponse(data=nl2sql_service.start_job(req))


@router.get("/jobs/{job_id}", response_model=ApiResponse[JobData])
async def get_job(job_id: str) -> ApiResponse[JobData]:
    """NL2SQL 検索 job の状態・結果を返す。"""
    job = nl2sql_service.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="指定されたジョブが見つかりません。")
    return ApiResponse(data=job)


@router.get("/profiles", response_model=ApiResponse[list[Nl2SqlProfile]])
async def list_profiles() -> ApiResponse[list[Nl2SqlProfile]]:
    """NL2SQL profile 一覧。"""
    return ApiResponse(data=nl2sql_service.list_profiles())


@router.post("/profiles", response_model=ApiResponse[Nl2SqlProfile])
async def create_profile(req: ProfileUpsertRequest) -> ApiResponse[Nl2SqlProfile]:
    """NL2SQL profile を作成する。"""
    profile = Nl2SqlProfile(id=str(uuid.uuid4()), **req.model_dump())
    return ApiResponse(data=nl2sql_service.create_profile(profile))


@router.patch("/profiles/{profile_id}", response_model=ApiResponse[Nl2SqlProfile])
async def update_profile(profile_id: str, req: ProfileUpsertRequest) -> ApiResponse[Nl2SqlProfile]:
    """NL2SQL profile を更新する。"""
    try:
        updated = nl2sql_service.update_profile(
            profile_id, lambda current: current.model_copy(update=req.model_dump())
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=404, detail="指定された profile が見つかりません。"
        ) from exc
    return ApiResponse(data=updated)


@router.post("/profiles/{profile_id}/archive", response_model=ApiResponse[Nl2SqlProfile])
async def archive_profile(profile_id: str) -> ApiResponse[Nl2SqlProfile]:
    """NL2SQL profile を archive する。"""
    try:
        return ApiResponse(data=nl2sql_service.archive_profile(profile_id))
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


@router.get("/history", response_model=ApiResponse[HistoryData])
async def history() -> ApiResponse[HistoryData]:
    """NL2SQL 検索履歴。"""
    return ApiResponse(data=nl2sql_service.list_history())


@router.post("/feedback", response_model=ApiResponse[FeedbackData])
async def feedback(req: FeedbackRequest) -> ApiResponse[FeedbackData]:
    """検索結果 feedback を保存する。"""
    return ApiResponse(data=nl2sql_service.save_feedback(req.history_id, req.rating, req.comment))


@router.post("/demo/learning", response_model=ApiResponse[DemoLearningData])
async def seed_demo_learning() -> ApiResponse[DemoLearningData]:
    """Learning / feedback 画面の検証用 demo データを投入する。"""
    return ApiResponse(data=nl2sql_service.seed_demo_learning_data())


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


@router.post("/analyze", response_model=ApiResponse[AnalyzeData])
async def analyze(req: AnalyzeRequest) -> ApiResponse[AnalyzeData]:
    """SQL の安全性・参照表・推奨修正を返す。"""
    return ApiResponse(
        data=nl2sql_service.analyze_sql(
            req.sql,
            req.allowed_objects,
            req.row_limit or nl2sql_service.get_profile(None).default_row_limit,
        )
    )


@router.post("/repair", response_model=ApiResponse[RepairData])
async def repair(req: RepairRequest) -> ApiResponse[RepairData]:
    """Oracle error message に基づいて SELECT SQL の修復候補を返す。"""
    return ApiResponse(
        data=nl2sql_service.repair_oracle_error(
            req,
            req.row_limit or nl2sql_service.get_profile(None).default_row_limit,
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


@router.post("/comments/suggest", response_model=ApiResponse[CommentSuggestionData])
async def suggest_comments() -> ApiResponse[CommentSuggestionData]:
    """表/列コメント候補を決定論で生成する。"""
    return ApiResponse(data=nl2sql_service.suggest_comments())


@router.post("/comments/apply", response_model=ApiResponse[CommentApplyData])
async def apply_comments(req: CommentApplyRequest) -> ApiResponse[CommentApplyData]:
    """COMMENT ON TABLE/COLUMN の dry-run / restricted execution。"""
    return ApiResponse(data=nl2sql_service.apply_comments(req))


@router.post("/synthetic-cases", response_model=ApiResponse[SyntheticCasesData])
async def synthetic_cases(
    profile_id: str | None = None, limit: int = 6
) -> ApiResponse[SyntheticCasesData]:
    """Synthetic NL2SQL 評価ケースを生成する。"""
    return ApiResponse(data=nl2sql_service.synthetic_cases(profile_id=profile_id, limit=limit))


@router.get("/diagnostics", response_model=ApiResponse[DiagnosticsData])
async def diagnostics() -> ApiResponse[DiagnosticsData]:
    """OCI / Oracle / NL2SQL エンジン設定の非 secret 診断を返す。"""
    return ApiResponse(data=nl2sql_service.diagnostics())
