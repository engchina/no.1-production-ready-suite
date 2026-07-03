"""RAG 評価ランナー。"""

import asyncio
import re
from collections.abc import Sequence
from time import perf_counter
from typing import Protocol

from app.clients.oracle import OracleClient
from app.config import Settings, get_settings
from app.rag.audit import record_rag_search_audit
from app.rag.diagnostics import build_search_diagnostics
from app.rag.file_processing_evaluation import (
    bbox_citation_coverage,
    citation_traceability_coverage,
    element_lineage_coverage,
)
from app.rag.generation_config import resolve_oracle_generation_settings
from app.rag.guardrails import evaluate_groundedness
from app.rag.ingestion_quality import summarize_ingestion_quality
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
    EvaluationIngestionQualitySummary,
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
TEXT_FEATURE_PATTERN = re.compile(r"[0-9a-zA-Z_]+|[\u3040-\u30ff\u3400-\u9fff]+")
EVALUATION_STOP_FEATURES = {
    "the",
    "and",
    "for",
    "with",
    "について",
    "ください",
    "です",
    "ます",
}
NOISE_FAILURE_REASONS: set[EvaluationFailureReason] = {
    "unexpected_retrieval",
    "low_groundedness",
    "guardrail_warning",
}


class SearchPipeline(Protocol):
    """評価ランナーが必要とする検索 pipeline の最小インターフェース。"""

    async def run(
        self,
        request: SearchRequest,
        trace_id: str | None = None,
    ) -> SearchResponse:
        """検索を実行する。"""


class IngestionQualitySource(Protocol):
    """評価 runner が corpus の取込品質を読むための最小インターフェース。"""

    async def list_document_extractions(self) -> list[dict[str, object]]:
        """保存済み extraction JSON 一覧を返す。"""


class EvaluationRunner:
    """小規模な golden set を使って検索・回答品質を評価する。"""

    def __init__(
        self,
        pipeline: SearchPipeline | None = None,
        quality_source: IngestionQualitySource | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._pipeline = pipeline
        self._quality_source = (
            quality_source
            if quality_source is not None
            else (OracleClient(settings=self._settings) if pipeline is None else None)
        )

    async def run(
        self,
        cases: list[EvaluationCase],
        top_k: int,
        rerank_top_n: int,
        mode: SearchMode = SearchMode.HYBRID,
        filters: dict[str, str] | None = None,
        knowledge_base_ids: Sequence[str] | None = None,
        thresholds: EvaluationThresholds | None = None,
        rag_overrides: EvaluationRagOverrides | None = None,
    ) -> EvaluationMetrics:
        """評価ケースを実行し、集計指標を返す。"""
        effective_settings = _settings_with_rag_overrides(self._settings, rag_overrides)
        if self._pipeline is None:
            effective_settings = await resolve_oracle_generation_settings(effective_settings)
        pipeline = self._pipeline or RagPipeline(settings=effective_settings)
        if not cases:
            aggregate_values = {
                "precision_at_k": ZERO_METRIC,
                "recall_at_k": ZERO_METRIC,
                "mrr": ZERO_METRIC,
                "answer_keyword_hit_rate": ZERO_METRIC,
                "groundedness_pass_rate": ZERO_METRIC,
                "faithfulness": ZERO_METRIC,
                "context_precision": ZERO_METRIC,
                "context_recall": ZERO_METRIC,
                "response_relevancy": ZERO_METRIC,
                "noise_sensitivity": ZERO_METRIC,
                "citation_traceability_coverage": ZERO_METRIC,
                "bbox_citation_coverage": ZERO_METRIC,
                "element_lineage_coverage": ZERO_METRIC,
                "content_kind_hit_rate": ZERO_METRIC,
                "section_coverage": ZERO_METRIC,
            }
            threshold_failures = _threshold_failures(thresholds, aggregate_values)
            return EvaluationMetrics(
                case_count=0,
                evaluated_k=0,
                error_count=0,
                passed=not threshold_failures,
                threshold_failures=threshold_failures,
                case_results=[],
                ingestion_quality=await _ingestion_quality_summary(
                    self._quality_source,
                    timeout_seconds=_ingestion_quality_timeout_seconds(effective_settings),
                ),
                **aggregate_values,
            )

        precision_total = 0.0
        recall_total = 0.0
        mrr_total = 0.0
        keyword_hits = 0
        groundedness_passes = 0
        faithfulness_total = 0.0
        context_precision_total = 0.0
        context_recall_total = 0.0
        response_relevancy_total = 0.0
        noise_sensitivity_total = 0.0
        citation_traceability_total = 0.0
        bbox_citation_total = 0.0
        element_lineage_total = 0.0
        content_kind_hit_total = 0.0
        section_coverage_total = 0.0
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
                knowledge_base_ids=list(knowledge_base_ids or []),
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
            faithfulness = groundedness.score
            context_precision = _context_precision(response.citations, relevant)
            context_recall = recall
            response_relevancy = _response_relevancy(case.query, response.answer)
            citation_traceability = _case_citation_traceability_coverage(
                response,
                relevant,
            )
            bbox_citation = _case_bbox_citation_coverage(response, relevant)
            element_lineage = _case_element_lineage_coverage(response, relevant)
            content_kind_hit = _case_content_kind_hit_rate(response, relevant, case)
            section_coverage = _case_section_coverage(response, relevant, case)
            failure_reasons = _case_failure_reasons(
                relevant=relevant,
                retrieved_ids=retrieved_ids,
                hit_document_ids=hits,
                answer_keyword_hit=answer_keyword_hit,
                groundedness_passed=groundedness.grounded,
                guardrail_warnings=response.guardrail_warnings,
                content_kind_hit=content_kind_hit,
                content_kind_required=case.expected_content_kind is not None,
                section_coverage=section_coverage,
                section_required=bool(case.expected_section_paths),
            )
            noise_sensitivity = _noise_sensitivity(failure_reasons)
            faithfulness_total += faithfulness
            context_precision_total += context_precision
            context_recall_total += context_recall
            response_relevancy_total += response_relevancy
            noise_sensitivity_total += noise_sensitivity
            citation_traceability_total += citation_traceability
            bbox_citation_total += bbox_citation
            element_lineage_total += element_lineage
            content_kind_hit_total += content_kind_hit
            section_coverage_total += section_coverage
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
                    faithfulness=round(faithfulness, 4),
                    context_precision=round(context_precision, 4),
                    context_recall=round(context_recall, 4),
                    response_relevancy=round(response_relevancy, 4),
                    noise_sensitivity=round(noise_sensitivity, 4),
                    citation_traceability_coverage=round(citation_traceability, 4),
                    bbox_citation_coverage=round(bbox_citation, 4),
                    element_lineage_coverage=round(element_lineage, 4),
                    content_kind_hit_rate=round(content_kind_hit, 4),
                    section_coverage=round(section_coverage, 4),
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
            "faithfulness": round(faithfulness_total / case_count, 4),
            "context_precision": round(context_precision_total / case_count, 4),
            "context_recall": round(context_recall_total / case_count, 4),
            "response_relevancy": round(response_relevancy_total / case_count, 4),
            "noise_sensitivity": round(noise_sensitivity_total / case_count, 4),
            "citation_traceability_coverage": round(
                citation_traceability_total / case_count,
                4,
            ),
            "bbox_citation_coverage": round(bbox_citation_total / case_count, 4),
            "element_lineage_coverage": round(element_lineage_total / case_count, 4),
            "content_kind_hit_rate": round(content_kind_hit_total / case_count, 4),
            "section_coverage": round(section_coverage_total / case_count, 4),
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
            ingestion_quality=await _ingestion_quality_summary(
                self._quality_source,
                timeout_seconds=_ingestion_quality_timeout_seconds(effective_settings),
            ),
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
                knowledge_base_ids=experiment.knowledge_base_ids,
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
        "context_adaptive_expansion_enabled": "rag_context_adaptive_expansion_enabled",
        "context_adaptive_neighbor_window": "rag_context_adaptive_neighbor_window",
        "context_adaptive_min_overlap": "rag_context_adaptive_min_overlap",
        "context_group_expansion_enabled": "rag_context_group_expansion_enabled",
        "context_group_max_chunks": "rag_context_group_max_chunks",
        "context_dependency_promotion_enabled": "rag_context_dependency_promotion_enabled",
        "context_dependency_max_chunks": "rag_context_dependency_max_chunks",
        "context_compression_enabled": "rag_context_compression_enabled",
        "context_compression_max_sentences": ("rag_context_compression_max_sentences"),
        "context_compression_max_chars_per_chunk": ("rag_context_compression_max_chars_per_chunk"),
        "oracle_vector_target_accuracy": "oracle_vector_target_accuracy",
    }
    return settings.model_copy(
        update={mapping[key]: value for key, value in override_values.items()}
    )


async def _ingestion_quality_summary(
    source: IngestionQualitySource | None,
    *,
    timeout_seconds: float,
) -> EvaluationIngestionQualitySummary:
    """取込品質 source がある場合だけ評価結果へ集計を添付する。"""
    if source is None:
        return EvaluationIngestionQualitySummary()
    # 評価本体の gate は検索・生成品質であり、品質サマリは補助情報。
    # Oracle Wallet や監査用 schema の一時不整合で評価全体を落とさない。
    try:
        extractions = await asyncio.wait_for(
            source.list_document_extractions(),
            timeout=timeout_seconds,
        )
    except Exception:
        return EvaluationIngestionQualitySummary()
    return EvaluationIngestionQualitySummary.model_validate(
        summarize_ingestion_quality(extractions)
    )


def _ingestion_quality_timeout_seconds(settings: Settings) -> float:
    """評価の補助サマリが golden set 実行を長時間ブロックしない上限を返す。"""
    timeout = float(getattr(settings, "db_read_timeout_seconds", 8.0))
    return max(0.001, min(timeout, 2.0))


def _reciprocal_rank(retrieved_ids: list[str], relevant_ids: set[str]) -> float:
    for index, document_id in enumerate(retrieved_ids, start=1):
        if document_id in relevant_ids:
            return 1.0 / index
    return 0.0


def _context_precision(citations: Sequence[object], relevant_ids: set[str]) -> float:
    """引用 context のうち golden relevant document に由来する比率。"""
    if not citations:
        return 1.0 if not relevant_ids else 0.0
    if not relevant_ids:
        return 0.0
    hit_count = sum(
        1 for citation in citations if getattr(citation, "document_id", None) in relevant_ids
    )
    return hit_count / len(citations)


def _case_citation_traceability_coverage(
    response: SearchResponse,
    relevant_ids: set[str],
) -> float:
    """no-results 正解を除き、citation traceability coverage を返す。"""
    if not response.citations:
        return 1.0 if not relevant_ids else 0.0
    return citation_traceability_coverage(response.citations)


def _case_bbox_citation_coverage(response: SearchResponse, relevant_ids: set[str]) -> float:
    """no-results 正解を除き、bbox citation coverage を返す。"""
    if not response.citations:
        return 1.0 if not relevant_ids else 0.0
    return bbox_citation_coverage(response.citations)


def _case_element_lineage_coverage(response: SearchResponse, relevant_ids: set[str]) -> float:
    """no-results 正解を除き、element lineage coverage を返す。"""
    if not response.citations:
        return 1.0 if not relevant_ids else 0.0
    return element_lineage_coverage(response.citations)


def _case_content_kind_hit_rate(
    response: SearchResponse,
    relevant_ids: set[str],
    case: EvaluationCase,
) -> float:
    """期待 content_kind を citation metadata で満たしたかを case 単位で返す。"""
    if case.expected_content_kind is None:
        return 1.0
    citations = _scoped_citations(response, relevant_ids)
    if not citations:
        return 0.0
    expected = case.expected_content_kind
    if any(_citation_content_kind(citation) == expected for citation in citations):
        return 1.0
    return 0.0


def _case_section_coverage(
    response: SearchResponse,
    relevant_ids: set[str],
    case: EvaluationCase,
) -> float:
    """期待 section_path が citation metadata にどれだけ含まれるかを返す。"""
    if not case.expected_section_paths:
        return 1.0
    citations = _scoped_citations(response, relevant_ids)
    if not citations:
        return 0.0
    observed_sections = _citation_sections(citations)
    if not observed_sections:
        return 0.0
    expected_sections = [_normalize_section_label(value) for value in case.expected_section_paths]
    hit_count = sum(
        1
        for expected in expected_sections
        if any(_section_matches(expected, observed) for observed in observed_sections)
    )
    return hit_count / len(expected_sections)


def _scoped_citations(
    response: SearchResponse,
    relevant_ids: set[str],
) -> list[object]:
    """relevant document が指定されている場合は該当 citation だけを評価対象にする。"""
    if not relevant_ids:
        return list(response.citations)
    return [
        citation
        for citation in response.citations
        if getattr(citation, "document_id", None) in relevant_ids
    ]


def _citation_content_kind(citation: object) -> str:
    metadata = getattr(citation, "metadata", {})
    if not isinstance(metadata, dict):
        return ""
    value = metadata.get("content_kind")
    return value.strip().casefold() if isinstance(value, str) else ""


def _citation_sections(citations: Sequence[object]) -> set[str]:
    sections: set[str] = set()
    for citation in citations:
        metadata = getattr(citation, "metadata", {})
        if not isinstance(metadata, dict):
            continue
        for key in ("section_path", "section_title"):
            value = metadata.get(key)
            if isinstance(value, str) and value.strip():
                sections.add(_normalize_section_label(value))
    return sections


def _normalize_section_label(value: str) -> str:
    return " > ".join(part.strip().casefold() for part in value.split(">") if part.strip())


def _section_matches(expected: str, observed: str) -> bool:
    if expected == observed:
        return True
    return observed.endswith(f" > {expected}") or expected.endswith(f" > {observed}")


def _response_relevancy(query: str, answer: str) -> float:
    """query feature が回答にどれだけ反映されたかを低コストに近似する。"""
    query_features = _text_features(query)
    if not query_features:
        return 1.0 if answer.strip() else 0.0
    answer_features = _text_features(answer)
    return len(query_features & answer_features) / len(query_features)


def _noise_sensitivity(reasons: list[EvaluationFailureReason]) -> float:
    """irrelevant context / guardrail noise に対する頑健性を 0..1 で返す。"""
    noise_failure_count = len(NOISE_FAILURE_REASONS.intersection(reasons))
    return max(0.0, 1.0 - (noise_failure_count * 0.34))


def _text_features(text: str) -> set[str]:
    """日本語と英数字を混在させた評価用 feature 集合を作る。"""
    features: set[str] = set()
    for raw_token in TEXT_FEATURE_PATTERN.findall(text.casefold()):
        token = raw_token.strip()
        if len(token) <= 1 or token in EVALUATION_STOP_FEATURES:
            continue
        features.add(token)
        if _contains_cjk(token):
            features.update(token[index : index + 2] for index in range(0, max(len(token) - 1, 0)))
            features.update(token[index : index + 3] for index in range(0, max(len(token) - 2, 0)))
    return features


def _contains_cjk(text: str) -> bool:
    return any("\u3040" <= char <= "\u30ff" or "\u3400" <= char <= "\u9fff" for char in text)


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
    content_kind_hit: float,
    content_kind_required: bool,
    section_coverage: float,
    section_required: bool,
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
    if content_kind_required and content_kind_hit < 1.0:
        reasons.append("content_kind_miss")
    if section_required and section_coverage < 1.0:
        reasons.append("section_miss")
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
