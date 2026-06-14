"""RAG 評価ランナー。"""

import asyncio
from time import perf_counter
from typing import Protocol

from app.config import Settings, get_settings
from app.rag.audit import record_rag_search_audit
from app.rag.diagnostics import build_search_diagnostics
from app.rag.guardrails import evaluate_groundedness
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
    EvaluationCompareResponse,
    EvaluationExperiment,
    EvaluationExperimentResult,
    EvaluationFailureReason,
    EvaluationMetricName,
    EvaluationMetrics,
    EvaluationRagOverrides,
    EvaluationThresholdFailure,
    EvaluationThresholds,
)
from app.schemas.search import SearchMode, SearchRequest, SearchResponse

EVALUATION_CASE_ERROR_MESSAGE = (
    "評価ケースの検索処理に失敗しました。trace_id で監査ログを確認してください。"
)
ZERO_METRIC = 0.0


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
        self._pipeline = pipeline

    async def run(
        self,
        cases: list[EvaluationCase],
        top_k: int,
        rerank_top_n: int,
        mode: SearchMode = SearchMode.HYBRID,
        filters: dict[str, str] | None = None,
        thresholds: EvaluationThresholds | None = None,
        rag_overrides: EvaluationRagOverrides | None = None,
    ) -> EvaluationMetrics:
        """評価ケースを実行し、集計指標を返す。"""
        effective_settings = _settings_with_rag_overrides(self._settings, rag_overrides)
        pipeline = self._pipeline or RagPipeline(settings=effective_settings)
        if not cases:
            aggregate_values = {
                "precision_at_k": ZERO_METRIC,
                "recall_at_k": ZERO_METRIC,
                "mrr": ZERO_METRIC,
                "answer_keyword_hit_rate": ZERO_METRIC,
                "groundedness_pass_rate": ZERO_METRIC,
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
        groundedness_passes = 0
        error_count = 0
        evaluated_k = max(1, min(top_k, rerank_top_n))
        case_results: list[EvaluationCaseResult] = []
        failure_reason_counts: dict[EvaluationFailureReason, int] = {}

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
                    pipeline.run(request, trace_id=trace_id),
                    timeout=effective_settings.rag_search_timeout_seconds,
                )
            except TimeoutError as exc:
                elapsed = elapsed_ms(case_started_at)
                record_evaluation_case(request.mode.value, "error", elapsed / 1000)
                _record_case_error_audit(
                    trace_id=trace_id,
                    request=request,
                    elapsed=elapsed,
                    error=exc,
                    settings=effective_settings,
                    error_stage="timeout",
                )
                error_result = _case_error_result(
                    case=case,
                    trace_id=trace_id,
                    elapsed=elapsed,
                    error=exc,
                )
                _accumulate_failure_reasons(failure_reason_counts, error_result.failure_reasons)
                case_results.append(error_result)
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
                    settings=effective_settings,
                    error_stage="evaluation",
                )
                error_result = _case_error_result(
                    case=case,
                    trace_id=trace_id,
                    elapsed=elapsed,
                    error=exc,
                )
                _accumulate_failure_reasons(failure_reason_counts, error_result.failure_reasons)
                case_results.append(error_result)
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
            grounding_context = "\n".join(chunk.text for chunk in response.citations)
            groundedness = evaluate_groundedness(response.answer, grounding_context)
            if groundedness.grounded:
                groundedness_passes += 1
            failure_reasons = _case_failure_reasons(
                relevant=relevant,
                retrieved_ids=retrieved_ids,
                hit_document_ids=hits,
                answer_keyword_hit=answer_keyword_hit,
                groundedness_passed=groundedness.grounded,
                guardrail_warnings=response.guardrail_warnings,
            )
            _accumulate_failure_reasons(failure_reason_counts, failure_reasons)
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
                    groundedness_passed=groundedness.grounded,
                    groundedness_score=groundedness.score,
                    grounding_overlap_count=groundedness.overlap_count,
                    grounding_answer_feature_count=groundedness.answer_feature_count,
                    guardrail_warnings=response.guardrail_warnings,
                    failure_reasons=failure_reasons,
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
            "groundedness_pass_rate": round(groundedness_passes / case_count, 4),
        }
        threshold_failures = _threshold_failures(thresholds, aggregate_values)
        return EvaluationMetrics(
            case_count=case_count,
            error_count=error_count,
            evaluated_k=evaluated_k,
            passed=not threshold_failures and error_count == 0,
            threshold_failures=threshold_failures,
            failure_reason_counts=failure_reason_counts,
            case_results=case_results,
            **aggregate_values,
        )

    async def compare(
        self,
        cases: list[EvaluationCase],
        experiments: list[EvaluationExperiment],
        *,
        ranking_metric: EvaluationMetricName = "mrr",
        thresholds: EvaluationThresholds | None = None,
    ) -> EvaluationCompareResponse:
        """同じ golden set で複数 RAG 設定を評価し、安定した順位を返す。"""
        results: list[EvaluationExperimentResult] = []
        for experiment in experiments:
            metrics = await self.run(
                cases=cases,
                top_k=experiment.top_k,
                rerank_top_n=experiment.rerank_top_n,
                mode=experiment.mode,
                filters=experiment.filters,
                thresholds=thresholds,
                rag_overrides=experiment.rag_overrides,
            )
            results.append(
                EvaluationExperimentResult(
                    rank=0,
                    ranking_score=_metric_value(metrics, ranking_metric),
                    experiment=experiment,
                    metrics=metrics,
                )
            )

        ranked_results = [
            result.model_copy(update={"rank": rank})
            for rank, result in enumerate(sorted(results, key=_experiment_sort_key), start=1)
        ]
        return EvaluationCompareResponse(
            ranking_metric=ranking_metric,
            best_experiment_id=ranked_results[0].experiment.id if ranked_results else None,
            results=ranked_results,
        )


def _settings_with_rag_overrides(
    settings: Settings,
    overrides: EvaluationRagOverrides | None,
) -> Settings:
    """評価 experiment の非 secret RAG 設定だけ一時的に上書きする。"""
    if overrides is None:
        return settings
    override_values = overrides.model_dump(exclude_none=True)
    if not override_values:
        return settings
    mapping = {
        "rrf_k": "rag_rrf_k",
        "query_expansion_enabled": "rag_query_expansion_enabled",
        "query_expansion_max_variants": "rag_query_expansion_max_variants",
        "context_window_chars": "rag_context_window_chars",
        "context_neighbor_window": "rag_context_neighbor_window",
        "context_diversity_lambda": "rag_context_diversity_lambda",
        "context_group_expansion_enabled": "rag_context_group_expansion_enabled",
        "context_group_max_chunks": "rag_context_group_max_chunks",
        "context_compression_enabled": "rag_context_compression_enabled",
        "context_compression_max_sentences": ("rag_context_compression_max_sentences"),
        "context_compression_max_chars_per_chunk": ("rag_context_compression_max_chars_per_chunk"),
        "oracle_vector_target_accuracy": "oracle_vector_target_accuracy",
    }
    return settings.model_copy(
        update={mapping[key]: value for key, value in override_values.items()}
    )


def _reciprocal_rank(retrieved_ids: list[str], relevant_ids: set[str]) -> float:
    for index, document_id in enumerate(retrieved_ids, start=1):
        if document_id in relevant_ids:
            return 1.0 / index
    return 0.0


def _metric_value(metrics: EvaluationMetrics, metric: EvaluationMetricName) -> float:
    """ranking metric の値を取り出す。"""
    return float(getattr(metrics, metric))


def _experiment_sort_key(result: EvaluationExperimentResult) -> tuple[int, float, int, int, str]:
    """passed 優先、metric 降順、エラー・失敗理由少数、ID 昇順で安定順位にする。"""
    return (
        0 if result.metrics.passed else 1,
        -result.ranking_score,
        result.metrics.error_count,
        sum(result.metrics.failure_reason_counts.values()),
        result.experiment.id,
    )


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
        groundedness_passed=False,
        groundedness_score=0.0,
        grounding_overlap_count=0,
        grounding_answer_feature_count=0,
        guardrail_warnings=[],
        failure_reasons=["case_error"],
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


def _case_failure_reasons(
    *,
    relevant: set[str],
    retrieved_ids: list[str],
    hit_document_ids: list[str],
    answer_keyword_hit: bool,
    groundedness_passed: bool,
    guardrail_warnings: list[str],
) -> list[EvaluationFailureReason]:
    """case 単位の失敗原因を安全なカテゴリへ分類する。"""
    reasons: list[EvaluationFailureReason] = []
    if relevant:
        hit_count = len(set(hit_document_ids))
        if hit_count == 0:
            reasons.append("retrieval_miss")
        elif hit_count < len(relevant):
            reasons.append("partial_recall")
    elif retrieved_ids:
        reasons.append("unexpected_retrieval")
    if not answer_keyword_hit:
        reasons.append("answer_keyword_miss")
    if not groundedness_passed:
        reasons.append("low_groundedness")
    expected_no_results = not relevant and not retrieved_ids
    if guardrail_warnings and not expected_no_results:
        reasons.append("guardrail_warning")
    return reasons


def _accumulate_failure_reasons(
    counts: dict[EvaluationFailureReason, int],
    reasons: list[EvaluationFailureReason],
) -> None:
    """失敗理由の case 件数を集計する。"""
    for reason in reasons:
        counts[reason] = counts.get(reason, 0) + 1


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
