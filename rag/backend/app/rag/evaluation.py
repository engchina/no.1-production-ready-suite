"""RAG 評価ランナー。"""

import asyncio
from time import perf_counter
from typing import Protocol

from app.config import Settings, get_settings
from app.rag.audit import record_rag_search_audit
from app.rag.diagnostics import build_search_diagnostics
from app.rag.observability import (
    elapsed_ms,
    new_trace_id,
    record_evaluation_case,
    record_rag_request,
)
from app.rag.pipeline import RagPipeline
from app.schemas.evaluation import (
    EvaluationCase,
    EvaluationCaseResult,
    EvaluationMetrics,
    EvaluationThresholdFailure,
    EvaluationThresholds,
)
from app.schemas.search import SearchMode, SearchRequest, SearchResponse

EVALUATION_CASE_ERROR_MESSAGE = (
    "評価ケースの検索処理に失敗しました。trace_id で監査ログを確認してください。"
)


class SearchPipeline(Protocol):
    """評価ランナーが必要とする検索 pipeline の最小インターフェース。"""

    async def run(
        self,
        request: SearchRequest,
        trace_id: str | None = None,
    ) -> SearchResponse:
        """検索を実行する。"""


class EvaluationRunner:
    """小規模な golden set を使って検索・回答品質を評価する。"""

    def __init__(
        self,
        pipeline: SearchPipeline | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._pipeline = pipeline or RagPipeline()

    async def run(
        self,
        cases: list[EvaluationCase],
        top_k: int,
        rerank_top_n: int,
        mode: SearchMode = SearchMode.HYBRID,
        filters: dict[str, str] | None = None,
        thresholds: EvaluationThresholds | None = None,
    ) -> EvaluationMetrics:
        """評価ケースを実行し、集計指標を返す。"""
        if not cases:
            aggregate_values = {
                "precision_at_k": 0.0,
                "recall_at_k": 0.0,
                "mrr": 0.0,
                "answer_keyword_hit_rate": 0.0,
            }
            threshold_failures = _threshold_failures(thresholds, aggregate_values)
            return EvaluationMetrics(
                case_count=0,
                evaluated_k=0,
                error_count=0,
                passed=not threshold_failures,
                threshold_failures=threshold_failures,
                case_results=[],
                **aggregate_values,
            )

        precision_total = 0.0
        recall_total = 0.0
        mrr_total = 0.0
        keyword_hits = 0
        error_count = 0
        evaluated_k = max(1, min(top_k, rerank_top_n))
        case_results: list[EvaluationCaseResult] = []

        for case in cases:
            request = SearchRequest(
                query=case.query,
                top_k=top_k,
                rerank_top_n=rerank_top_n,
                mode=mode,
                filters=filters or {},
            )
            trace_id = new_trace_id()
            case_started_at = perf_counter()
            try:
                response = await asyncio.wait_for(
                    self._pipeline.run(request, trace_id=trace_id),
                    timeout=self._settings.rag_search_timeout_seconds,
                )
            except TimeoutError as exc:
                elapsed = elapsed_ms(case_started_at)
                record_evaluation_case(request.mode.value, "error", elapsed / 1000)
                _record_case_error_audit(
                    trace_id=trace_id,
                    request=request,
                    elapsed=elapsed,
                    error=exc,
                    settings=self._settings,
                    error_stage="timeout",
                )
                case_results.append(
                    _case_error_result(
                        case=case,
                        trace_id=trace_id,
                        elapsed=elapsed,
                        error=exc,
                    )
                )
                error_count += 1
                continue
            except Exception as exc:
                elapsed = elapsed_ms(case_started_at)
                record_evaluation_case(request.mode.value, "error", elapsed / 1000)
                _record_case_error_audit(
                    trace_id=trace_id,
                    request=request,
                    elapsed=elapsed,
                    error=exc,
                    settings=self._settings,
                    error_stage="evaluation",
                )
                case_results.append(
                    _case_error_result(
                        case=case,
                        trace_id=trace_id,
                        elapsed=elapsed,
                        error=exc,
                    )
                )
                error_count += 1
                continue

            record_evaluation_case(
                request.mode.value,
                "success",
                elapsed_ms(case_started_at) / 1000,
            )
            retrieved_ids = _unique_in_order([chunk.document_id for chunk in response.citations])
            relevant = set(case.relevant_document_ids)
            relevant_ids = list(case.relevant_document_ids)
            hits: list[str] = []
            precision = 0.0
            recall = 0.0
            reciprocal_rank = 0.0
            if relevant:
                hits = [doc_id for doc_id in retrieved_ids if doc_id in relevant]
                precision = len(hits) / evaluated_k
                recall = len(set(hits)) / len(relevant)
                reciprocal_rank = _reciprocal_rank(retrieved_ids, relevant)
            else:
                precision = 1.0 if not retrieved_ids else 0.0
                recall = 1.0 if not retrieved_ids else 0.0
            precision_total += precision
            recall_total += recall
            mrr_total += reciprocal_rank

            answer_keyword_hit = _answer_contains_keywords(
                response.answer, case.expected_answer_keywords
            )
            if answer_keyword_hit:
                keyword_hits += 1
            case_results.append(
                EvaluationCaseResult(
                    case_id=case.id,
                    trace_id=response.trace_id,
                    retrieved_document_ids=retrieved_ids,
                    relevant_document_ids=relevant_ids,
                    hit_document_ids=_unique_in_order(hits),
                    precision_at_k=round(precision, 4),
                    recall_at_k=round(recall, 4),
                    reciprocal_rank=round(reciprocal_rank, 4),
                    answer_keyword_hit=answer_keyword_hit,
                    guardrail_warnings=response.guardrail_warnings,
                    diagnostics=response.diagnostics,
                    elapsed_ms=response.elapsed_ms,
                )
            )

        case_count = len(cases)
        aggregate_values = {
            "precision_at_k": round(precision_total / case_count, 4),
            "recall_at_k": round(recall_total / case_count, 4),
            "mrr": round(mrr_total / case_count, 4),
            "answer_keyword_hit_rate": round(keyword_hits / case_count, 4),
        }
        threshold_failures = _threshold_failures(thresholds, aggregate_values)
        return EvaluationMetrics(
            case_count=case_count,
            error_count=error_count,
            evaluated_k=evaluated_k,
            passed=not threshold_failures and error_count == 0,
            threshold_failures=threshold_failures,
            case_results=case_results,
            **aggregate_values,
        )


def _reciprocal_rank(retrieved_ids: list[str], relevant_ids: set[str]) -> float:
    for index, document_id in enumerate(retrieved_ids, start=1):
        if document_id in relevant_ids:
            return 1.0 / index
    return 0.0


def _case_error_result(
    *,
    case: EvaluationCase,
    trace_id: str,
    elapsed: float,
    error: Exception,
) -> EvaluationCaseResult:
    """評価 case の失敗を query 本文なしの診断結果に変換する。"""
    return EvaluationCaseResult(
        case_id=case.id,
        trace_id=trace_id,
        status="error",
        retrieved_document_ids=[],
        relevant_document_ids=list(case.relevant_document_ids),
        hit_document_ids=[],
        precision_at_k=0.0,
        recall_at_k=0.0,
        reciprocal_rank=0.0,
        answer_keyword_hit=False,
        guardrail_warnings=[],
        elapsed_ms=elapsed,
        error_type=type(error).__name__,
        error_message=EVALUATION_CASE_ERROR_MESSAGE,
    )


def _record_case_error_audit(
    *,
    trace_id: str,
    request: SearchRequest,
    elapsed: float,
    error: Exception,
    settings: Settings,
    error_stage: str,
) -> None:
    """評価 runner 側で捕捉した case 失敗を RAG 監査へ残す。"""
    record_rag_request(request.mode.value, "error", elapsed / 1000, 0)
    diagnostics = build_search_diagnostics(request, settings=settings)
    record_rag_search_audit(
        trace_id=trace_id,
        outcome="error",
        mode=request.mode,
        sanitized_query=request.query,
        filters=request.filters,
        findings=[],
        retrieved_count=0,
        citations=[],
        elapsed_ms=elapsed,
        diagnostics=diagnostics,
        error=error,
        error_stage=error_stage,
    )


def _unique_in_order(values: list[str]) -> list[str]:
    """重複を除き、初出順を維持する。"""
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        unique.append(value)
    return unique


def _answer_contains_keywords(answer: str, keywords: list[str]) -> bool:
    if not keywords:
        return True
    normalized = answer.lower()
    return all(keyword.lower() in normalized for keyword in keywords)


def _threshold_failures(
    thresholds: EvaluationThresholds | None,
    aggregate_values: dict[str, float],
) -> list[EvaluationThresholdFailure]:
    """設定された最低閾値を下回った aggregate metric を返す。"""
    if thresholds is None:
        return []

    failures: list[EvaluationThresholdFailure] = []
    for metric, threshold in thresholds.model_dump(exclude_none=True).items():
        actual = aggregate_values[metric]
        if actual < threshold:
            failures.append(
                EvaluationThresholdFailure(
                    metric=metric,
                    actual=actual,
                    threshold=threshold,
                )
            )
    return failures
