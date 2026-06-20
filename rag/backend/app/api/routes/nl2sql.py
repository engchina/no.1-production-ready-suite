"""NL2SQL クエリ API(生成 → 人手プレビュー確認 → 実行 の 2 段ゲート)。

``POST /api/nl2sql/generate``: NL から SQL を生成し Guardrail 検査して返す(**未実行**)。
``POST /api/nl2sql/execute`` : 人手承認済み SQL を再検査し read-only のみ実行する。

実 Select AI 呼び出しは ``SelectAiClient`` をモジュール属性として参照するため、テストは
``monkeypatch.setattr(nl2sql_route, "SelectAiClient", lambda *a, **k: fake)`` で差し替えできる。
"""

from fastapi import APIRouter, HTTPException, Request

from app.clients.oracle import SelectAiUnavailableError
from app.clients.select_ai import SelectAiClient
from app.config import Settings, get_settings
from app.nl2sql.guardrail import GuardrailVerdict
from app.nl2sql.pipeline import (
    Nl2SqlExecutionOutcome,
    Nl2SqlGenerationOutcome,
    Nl2SqlPipeline,
)
from app.rag.rate_limit import enforce_rate_limit
from app.schemas.common import ApiResponse
from app.schemas.nl2sql import (
    GuardrailVerdictData,
    Nl2SqlExecuteRequest,
    Nl2SqlExecuteResponseData,
    Nl2SqlGenerateRequest,
    Nl2SqlGenerateResponseData,
    RouterSummaryData,
    SqlResultData,
)

router = APIRouter()


def _build_pipeline(settings: Settings) -> Nl2SqlPipeline:
    """実 Select AI クライアントで pipeline を組む(テストは SelectAiClient を差し替え)。"""
    return Nl2SqlPipeline(settings, select_ai_client=SelectAiClient(settings))


def _verdict_data(verdict: GuardrailVerdict) -> GuardrailVerdictData:
    return GuardrailVerdictData(
        allowed=verdict.allowed,
        policy=verdict.policy,
        statement_type=verdict.statement_type,
        violations=list(verdict.violations),
        semantic_verify_required=verdict.semantic_verify_required,
        max_rows=verdict.max_rows,
        run_role=verdict.run_role,
    )


def _generate_data(outcome: Nl2SqlGenerationOutcome) -> Nl2SqlGenerateResponseData:
    return Nl2SqlGenerateResponseData(
        question=outcome.question,
        profile_name=outcome.profile_name,
        generation_backend=outcome.generation_backend,
        router=RouterSummaryData(
            profile_selected=outcome.router.profile_selected,
            generation_backend=outcome.router.generation_backend,
            complexity_score=outcome.router.complexity_score,
            matched_signals=list(outcome.router.matched_signals),
            reason=outcome.router.reason,
        ),
        generated_sql=outcome.generated_sql,
        narration=outcome.narration,
        guardrail=_verdict_data(outcome.guardrail),
    )


def _execute_data(outcome: Nl2SqlExecutionOutcome) -> Nl2SqlExecuteResponseData:
    result = outcome.result
    return Nl2SqlExecuteResponseData(
        sql=outcome.sql,
        executed=outcome.executed,
        blocked_reason=outcome.blocked_reason,
        guardrail=_verdict_data(outcome.guardrail),
        result=(
            SqlResultData(
                columns=list(result.columns),
                rows=[list(row) for row in result.rows],
                row_count=result.row_count,
                truncated=result.truncated,
            )
            if result is not None
            else None
        ),
    )


@router.post("/generate", response_model=ApiResponse[Nl2SqlGenerateResponseData])
async def generate_sql(
    http_request: Request,
    request: Nl2SqlGenerateRequest,
) -> ApiResponse[Nl2SqlGenerateResponseData]:
    """NL から SQL を生成し Guardrail 検査して返す(未実行・確認待ち)。"""
    enforce_rate_limit("search", http_request)
    pipeline = _build_pipeline(get_settings())
    try:
        outcome = await pipeline.generate(
            request.question,
            profile_name=request.profile_name,
            team_name=request.team_name,
            allowed_objects=tuple(request.allowed_objects),
        )
    except SelectAiUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return ApiResponse(data=_generate_data(outcome))


@router.post("/execute", response_model=ApiResponse[Nl2SqlExecuteResponseData])
async def execute_sql(
    http_request: Request,
    request: Nl2SqlExecuteRequest,
) -> ApiResponse[Nl2SqlExecuteResponseData]:
    """人手承認済み SQL を再検査し read-only のみ実行する。"""
    enforce_rate_limit("search", http_request)
    pipeline = _build_pipeline(get_settings())
    try:
        outcome = await pipeline.execute(
            request.sql,
            allowed_objects=tuple(request.allowed_objects),
        )
    except SelectAiUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return ApiResponse(data=_execute_data(outcome))
