"""RAG 評価ランナー。

golden set の各ケースを回答エンジン(根拠付き回答。全体の既定の設定)で回答し、回答の記録
(引用・根拠・実行記録)から指標を求める(#591)。指標は 3 つの観点に整理した 9 つ。

- 検索: context_recall(正解の文書を取れたか)/ mrr(正解の文書が何番目に出たか)
- 根拠: claim_support_rate(画面の名前は Faithfulness。標準回答による評価で LLM が回答を主張に
  分け、根拠のない主張・根拠と矛盾する主張が無いケースの割合)/ citation_traceability_coverage
  (引用を原文の位置へたどれる割合)/ faithfulness(画面の名前は「根拠との語句の一致率」。回答の語が
  根拠に含まれる割合。言い換えで下がるため参考値にし、閾値の判定と失敗理由に使わない。#711)
- 回答: answer_keyword_hit_rate(期待する語をすべて含むケースの割合)/ refusal_accuracy(答える
  べきケースで答え、答えるべきでないケースで答えなかった割合)/ requirement_coverage(標準回答の
  必要な項目への対応率)/ answer_pass_rate(標準回答による評価の合格の割合)

各指標は、その指標を測れるケースだけの平均にする(例: 正解の文書が無いケースは検索の指標の
対象外、期待する語の無いケースは answer_keyword_hit_rate の対象外)。失敗したケースは error_count
で数え、指標の平均には入れない(1 件でも失敗があれば不合格)。標準回答による評価(LLM を複数回
呼ぶ)は、標準回答のあるケースだけで行う。
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import partial
from time import perf_counter
from typing import Any, Protocol

from app.clients.oracle import OracleClient
from app.config import OCI_ENTERPRISE_AI_TIMEOUT_MAX_SECONDS, Settings, get_settings
from app.rag.answer_engine import evaluate_answer_record
from app.rag.answer_metrics import (
    REFERENCE_ONLY_METRICS,
    claims_supported,
    grounding_text,
    is_abstained_answer,
)
from app.rag.answer_timeout import (
    AnswerTimeoutError,
    answer_stage_label,
    answer_timeout_seconds,
    format_timeout_limit,
    run_answer_with_timeout,
)
from app.rag.audit import record_rag_search_audit
from app.rag.diagnostics import build_search_diagnostics
from app.rag.evaluation_handling import (
    condition_coverage,
    forbidden_hits,
    observed_outcome,
    step_order_score,
)
from app.rag.file_processing_evaluation import citation_traceability_coverage
from app.rag.guardrails import evaluate_groundedness
from app.rag.observability import (
    SEARCH_METRIC_MODE,
    elapsed_ms,
    new_trace_id,
    record_evaluation_case,
    record_rag_request,
)
from app.rag.pipeline import RagPipeline, SearchStageProgressCallback
from app.schemas.evaluation import (
    EVALUATION_METRIC_NAMES,
    EVALUATION_UNCATEGORIZED,
    EvaluationAnswerJudgement,
    EvaluationCase,
    EvaluationCaseResult,
    EvaluationCategorySummary,
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
from app.schemas.search import RetrievedChunk, SearchRequest, SearchResponse

EVALUATION_CASE_ERROR_MESSAGE = (
    "評価ケースの検索処理に失敗しました。trace_id で監査ログを確認してください。"
)
# 評価全体の上限（`time_budget_seconds`）に達して実行しなかったケースの error_type（#383）。
EVALUATION_TIME_BUDGET_ERROR_TYPE = "EvaluationTimeBudgetExceeded"
EVALUATION_TIME_BUDGET_MESSAGE_PREFIX = "評価全体の時間の上限に達したため、"
EVALUATION_TIME_BUDGET_MESSAGE_SUFFIX = "ケースを減らすか、分けて評価してください。"
# 標準回答による評価 1 件の時間の上限(保存済みの回答の評価の API と同じ。#304)。
ANSWER_JUDGE_TIMEOUT_SECONDS = OCI_ENTERPRISE_AI_TIMEOUT_MAX_SECONDS
ANSWER_JUDGE_UNAVAILABLE_MESSAGE = (
    "この回答には標準回答で評価するための記録がありません(安全ポリシーで止めた質問など)。"
)
ANSWER_JUDGE_TIMEOUT_MESSAGE = "標準回答による評価が時間内に終わりませんでした。"
ANSWER_JUDGE_ERROR_MESSAGE = "標準回答による評価を完了できませんでした。"
logger = logging.getLogger(__name__)


class SearchPipeline(Protocol):
    """評価ランナーが必要とする検索 pipeline の最小インターフェース。"""

    async def run(
        self,
        request: SearchRequest,
        trace_id: str | None = None,
        progress_callback: SearchStageProgressCallback | None = None,
    ) -> SearchResponse:
        """検索を実行する。

        `progress_callback` へ工程を通知する（時間切れになった工程の特定に使う）。
        """


class AnswerJudge(Protocol):
    """標準回答で回答を評価する(LLM を複数回呼ぶ)。

    戻り値は `evaluate_answer_record` の結果(status / passed / metrics / claim_checks /
    coverage_checks / requirement_coverage など。#680)。時間切れは TimeoutError を送出する。
    """

    async def __call__(
        self,
        *,
        trace_id: str,
        evaluation_input: Mapping[str, object],
        standard_answer: str,
        citations: Sequence[RetrievedChunk],
        timeout_seconds: float,
    ) -> Mapping[str, object]:
        """評価結果を返す。"""


class EvaluationProgressCallback(Protocol):
    """評価の進捗の通知先（評価 job の進捗の保存に使う。#390）。

    ケースを始める前に、終わったケースの数と今のケースの id を渡す。最後のケースの後は
    `current_case_id=None` で呼ぶ。比較（`compare`）では、experiment をまたいだ通しの件数と
    今の experiment の id を渡す。
    """

    async def __call__(
        self,
        *,
        completed_cases: int,
        current_case_id: str | None,
        current_experiment_id: str | None,
    ) -> None:
        """進捗を受け取る。"""


async def judge_answer_with_standard(
    *,
    trace_id: str,
    evaluation_input: Mapping[str, object],
    standard_answer: str,
    citations: Sequence[RetrievedChunk],
    timeout_seconds: float,
    settings: Settings | None = None,
) -> Mapping[str, object]:
    """回答を標準回答で評価し、結果を回答の記録にも保存する(保存は best-effort)。

    worker thread の評価は止められないため、時間切れのときは結果を捨てる(#304 と同じ)。
    """
    resolved = settings or get_settings()
    evaluation = await asyncio.wait_for(
        asyncio.to_thread(
            evaluate_answer_record,
            evaluation_input,
            standard_answer,
            resolved,
            citations=citations,
        ),
        timeout=max(0.001, timeout_seconds),
    )
    saved = {
        **evaluation,
        "standard_answer": standard_answer,
        "evaluated_at": datetime.now(UTC).isoformat(),
    }
    try:
        # 回答の記録の画面から、指標ごとの値・主張ごとの判定を確かめられるようにする。
        await OracleClient(settings=resolved).save_answer_evaluation(trace_id, saved)
    except Exception as exc:  # noqa: BLE001 - 保存は補助。評価の結果は返す。
        logger.info(
            "evaluation_answer_judgement_save_skipped",
            extra={"error_type": type(exc).__name__},
        )
    return evaluation


@dataclass
class _Aggregate:
    """指標ごとに、測れたケースの値を集める。"""

    values: dict[str, list[float]] = field(default_factory=dict)

    def add(self, metric: EvaluationMetricName, value: float | None) -> None:
        if value is not None:
            self.values.setdefault(metric, []).append(float(value))

    def means(self) -> dict[str, float | None]:
        return {
            metric: (
                round(sum(self.values[metric]) / len(self.values[metric]), 4)
                if self.values.get(metric)
                else None
            )
            for metric in EVALUATION_METRIC_NAMES
        }

    def counts(self) -> dict[str, int]:
        return {metric: len(self.values.get(metric, [])) for metric in EVALUATION_METRIC_NAMES}


ProfileResolver = Callable[[SearchRequest, Settings], Awaitable[tuple[SearchRequest, Settings]]]


async def _resolve_profile_context(
    request: SearchRequest, settings: Settings
) -> tuple[SearchRequest, Settings]:
    """検索・回答と同じ解決で、プロファイルの KB・回答の設定・業務ガイドを当てる（#1249）。"""
    from app.api.routes.search import _resolve_query_context

    resolved, resolved_settings, _kb, _view = await _resolve_query_context(request, settings)
    return resolved, resolved_settings


class EvaluationRunner:
    """小規模な golden set を使って検索・根拠・回答の品質を評価する。"""

    def __init__(
        self,
        pipeline: SearchPipeline | None = None,
        settings: Settings | None = None,
        answer_judge: AnswerJudge | None = None,
        profile_resolver: ProfileResolver | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._pipeline = pipeline
        self._profile_resolver = profile_resolver or _resolve_profile_context
        # 既定の pipeline(実環境)では、標準回答のあるケースを LLM で評価する。
        # テストで pipeline を注入したときは、明示した judge だけを使う。
        self._answer_judge: AnswerJudge | None = answer_judge
        if answer_judge is None and pipeline is None:
            self._answer_judge = _settings_judge(self._settings)

    async def run(
        self,
        cases: list[EvaluationCase],
        top_k: int,
        filters: dict[str, str] | None = None,
        knowledge_base_ids: Sequence[str] | None = None,
        thresholds: EvaluationThresholds | None = None,
        rag_overrides: EvaluationRagOverrides | None = None,
        *,
        search_answer_profile_id: str | None = None,
        time_budget_seconds: float | None = None,
        progress: EvaluationProgressCallback | None = None,
    ) -> EvaluationMetrics:
        """評価ケースを実行し、集計指標を返す。

        1 ケースは回答生成の上限（`rag_answer_timeout_seconds`）で打ち切り、時間切れになった工程を
        ケースの結果に残して次のケースへ進む（#383）。`time_budget_seconds` を渡すと、評価全体を
        その秒数で打ち切る（同期の HTTP の待ちを超えないため）。上限に達したら実行中のケースを
        打ち切り、残りのケースは実行せずに失敗として記録する。`progress` には、ケースを始める
        前と最後のケースの後に進捗を渡す（評価 job。#390）。

        `search_answer_profile_id` を渡すと、ケースごとに検索・回答と同じ解決（参照 KB・回答の
        設定・用語・ルール・業務ガイド）をしてから回答する（#1249）。渡さなければ全体の既定（#301）。
        """
        deadline = _deadline(time_budget_seconds)
        effective_settings = evaluation_settings(self._settings, rag_overrides)
        pipeline = self._pipeline or RagPipeline(settings=effective_settings)

        aggregate = _Aggregate()
        error_count = 0
        judge_incomplete_count = 0
        case_results: list[EvaluationCaseResult] = []
        failure_reason_counts: dict[str, int] = {}

        for case_index, case in enumerate(cases):
            if progress is not None:
                await progress(
                    completed_cases=case_index,
                    current_case_id=case.id,
                    current_experiment_id=None,
                )
            request = SearchRequest(
                query=case.query,
                top_k=top_k,
                filters=filters or {},
                knowledge_base_ids=list(knowledge_base_ids or []),
            )
            trace_id = new_trace_id()
            remaining = _remaining_seconds(deadline)
            if remaining is not None and remaining <= 0:
                # 評価全体の上限に達した。残りのケースは pipeline を呼ばずに失敗として記録する。
                skipped_result = _case_skipped_result(case=case, trace_id=trace_id)
                _accumulate_failure_reasons(failure_reason_counts, skipped_result.failure_reasons)
                case_results.append(skipped_result)
                error_count += 1
                continue
            case_limit = answer_timeout_seconds(effective_settings)
            limited_by_budget = remaining is not None and remaining < case_limit
            case_started_at = perf_counter()
            try:
                run_case: Any = partial(pipeline.run, request, trace_id)
                if search_answer_profile_id:
                    # プロファイルの解決（KB・回答の設定・業務ガイド）。失敗はケースの失敗にする。
                    request, case_settings = await self._profile_resolver(
                        request.model_copy(
                            update={"search_answer_profile_id": search_answer_profile_id}
                        ),
                        effective_settings,
                    )
                    case_pipeline = self._pipeline or RagPipeline(settings=case_settings)
                    run_case = partial(case_pipeline.run, request, trace_id)
                # 工程を記録する tracker は、3 番目の引数（progress_callback）として渡る。
                response = await run_answer_with_timeout(
                    run_case,
                    effective_settings,
                    timeout_seconds=remaining if limited_by_budget else None,
                )
            except AnswerTimeoutError as exc:
                elapsed = elapsed_ms(case_started_at)
                record_evaluation_case(SEARCH_METRIC_MODE, "error", elapsed / 1000)
                _record_case_error_audit(
                    trace_id=trace_id,
                    request=request,
                    elapsed=elapsed,
                    # 監査と結果の error_type は、従来どおり元の TimeoutError にする。
                    error=exc.original_error,
                    settings=effective_settings,
                    error_stage="timeout",
                )
                error_result = _case_error_result(
                    case=case,
                    trace_id=trace_id,
                    elapsed=elapsed,
                    error=exc.original_error,
                    error_stage=exc.stage,
                    error_message=_case_timeout_message(
                        exc,
                        # 工程の中の時間切れ（LLM 1 回の timeout など）は、全体の上限ではない。
                        limited_by_budget=limited_by_budget and exc.timeout_seconds is not None,
                    ),
                )
                _accumulate_failure_reasons(failure_reason_counts, error_result.failure_reasons)
                case_results.append(error_result)
                error_count += 1
                continue
            except Exception as exc:
                elapsed = elapsed_ms(case_started_at)
                record_evaluation_case(SEARCH_METRIC_MODE, "error", elapsed / 1000)
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
                SEARCH_METRIC_MODE,
                "success",
                elapsed_ms(case_started_at) / 1000,
            )
            judgement = await self._judge(case, response, deadline)
            result = _case_result(case, response, judgement)
            if judgement is not None and judgement.status != "completed":
                judge_incomplete_count += 1
            _accumulate_case_metrics(aggregate, result)
            _accumulate_failure_reasons(failure_reason_counts, result.failure_reasons)
            case_results.append(result)

        if progress is not None:
            await progress(
                completed_cases=len(cases),
                current_case_id=None,
                current_experiment_id=None,
            )
        aggregate_values = aggregate.means()
        threshold_failures = _threshold_failures(thresholds, aggregate_values)
        return EvaluationMetrics(
            case_count=len(cases),
            error_count=error_count,
            category_breakdown=category_breakdown(case_results),
            # 失敗したケースや、標準回答で評価できなかったケースがあれば合格にしない。
            passed=not threshold_failures and error_count == 0 and judge_incomplete_count == 0,
            threshold_failures=threshold_failures,
            failure_reason_counts=failure_reason_counts,
            metric_case_counts=aggregate.counts(),
            case_results=case_results,
            **aggregate_values,
        )

    async def compare(
        self,
        cases: list[EvaluationCase],
        experiments: list[EvaluationExperiment],
        *,
        ranking_metric: EvaluationMetricName = "context_recall",
        thresholds: EvaluationThresholds | None = None,
        time_budget_seconds: float | None = None,
        progress: EvaluationProgressCallback | None = None,
    ) -> EvaluationCompareResponse:
        """同じ golden set で複数の回答設定を評価し、安定した順位を返す。

        `time_budget_seconds`（評価全体の上限。#383）は experiment の間で共有する。`progress` には
        experiment をまたいだ通しのケースの件数を渡す（#390）。
        """
        deadline = _deadline(time_budget_seconds)
        results: list[EvaluationExperimentResult] = []
        for experiment_index, experiment in enumerate(experiments):
            remaining = _remaining_seconds(deadline)
            metrics = await self.run(
                cases=cases,
                top_k=experiment.top_k,
                filters=experiment.filters,
                knowledge_base_ids=experiment.knowledge_base_ids,
                thresholds=thresholds,
                rag_overrides=experiment.rag_overrides,
                search_answer_profile_id=experiment.search_answer_profile_id,
                time_budget_seconds=None if remaining is None else max(0.0, remaining),
                progress=_experiment_progress(
                    progress,
                    offset=experiment_index * len(cases),
                    experiment_id=experiment.id,
                ),
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

    async def _judge(
        self,
        case: EvaluationCase,
        response: SearchResponse,
        deadline: float | None,
    ) -> EvaluationAnswerJudgement | None:
        """標準回答のあるケースを LLM で評価する。標準回答が無ければ None。"""
        if case.standard_answer is None:
            return None
        evaluation_input = response.evaluation_input
        if self._answer_judge is None or not evaluation_input:
            return EvaluationAnswerJudgement(
                status="unavailable", message=ANSWER_JUDGE_UNAVAILABLE_MESSAGE
            )
        remaining = _remaining_seconds(deadline)
        timeout = ANSWER_JUDGE_TIMEOUT_SECONDS if remaining is None else remaining
        timeout = min(timeout, ANSWER_JUDGE_TIMEOUT_SECONDS)
        if timeout <= 0:
            return EvaluationAnswerJudgement(status="timeout", message=ANSWER_JUDGE_TIMEOUT_MESSAGE)
        try:
            evaluation = await self._answer_judge(
                trace_id=response.trace_id,
                evaluation_input=evaluation_input,
                standard_answer=case.standard_answer,
                citations=response.citations,
                timeout_seconds=timeout,
            )
        except TimeoutError:
            return EvaluationAnswerJudgement(status="timeout", message=ANSWER_JUDGE_TIMEOUT_MESSAGE)
        except Exception:  # noqa: BLE001 - 評価の失敗はケースの結果に残して続ける。
            return EvaluationAnswerJudgement(status="error", message=ANSWER_JUDGE_ERROR_MESSAGE)
        return summarize_answer_judgement(evaluation)


def _settings_judge(settings: Settings) -> AnswerJudge:
    """Settings を固定した既定の judge。"""

    async def judge(
        *,
        trace_id: str,
        evaluation_input: Mapping[str, object],
        standard_answer: str,
        citations: Sequence[RetrievedChunk],
        timeout_seconds: float,
    ) -> Mapping[str, object]:
        return await judge_answer_with_standard(
            trace_id=trace_id,
            evaluation_input=evaluation_input,
            standard_answer=standard_answer,
            citations=citations,
            timeout_seconds=timeout_seconds,
            settings=settings,
        )

    return judge


def summarize_answer_judgement(evaluation: Mapping[str, object]) -> EvaluationAnswerJudgement:
    """標準回答による評価の結果を、指標に使う要約へ変換する。"""
    status = str(evaluation.get("status") or "error")
    message = evaluation.get("message")
    if status != "completed":
        return EvaluationAnswerJudgement(
            status=status, message=str(message) if message else ANSWER_JUDGE_ERROR_MESSAGE
        )
    claims = [
        claim for claim in _mapping_list(evaluation.get("claim_checks")) if claim.get("status")
    ]
    coverage_checks = _mapping_list(evaluation.get("coverage_checks"))
    coverage = _optional_float(evaluation.get("requirement_coverage"))
    return EvaluationAnswerJudgement(
        status="completed",
        passed=bool(evaluation.get("passed")),
        claims_supported=claims_supported(claims),
        requirement_coverage=(
            round(min(1.0, max(0.0, coverage)), 4) if coverage is not None else None
        ),
        missing_content=any(str(check.get("status")) == "missing" for check in coverage_checks),
        message=str(message) if message else None,
    )


def evaluation_settings(
    settings: Settings,
    overrides: EvaluationRagOverrides | None,
) -> Settings:
    """評価に使う設定。experiment の上書きを一時適用する。

    評価は検索・回答プロファイルを受け取らず、全体の既定で動く(#301)。回答は根拠付き回答(回答の記録を
    残す回答フロー)だけなので(#594)、回答エンジンの指定は要らない(#591 では固定していた)。
    """
    update: dict[str, object] = {}
    if overrides is not None:
        mapping = {
            "query_strategy": "rag_query_strategy",
            "answer_flow": "rag_answer_flow",
            "neighbor_child_count": "rag_neighbor_child_count",
            "rerank_enabled": "rag_rerank_enabled",
            "rrf_k": "rag_rrf_k",
            "context_group_max_chunks": "rag_context_group_max_chunks",
            "reference_expansion_enabled": "rag_reference_expansion_enabled",
            "reference_expansion_max_chunks": "rag_reference_expansion_max_chunks",
            "oracle_vector_target_accuracy": "oracle_vector_target_accuracy",
        }
        update.update(
            {mapping[key]: value for key, value in overrides.model_dump(exclude_none=True).items()}
        )
    return settings.model_copy(update=update)


def _case_result(
    case: EvaluationCase,
    response: SearchResponse,
    judgement: EvaluationAnswerJudgement | None,
) -> EvaluationCaseResult:
    """1 ケースの回答から、測れる指標と失敗理由を求める。"""
    retrieved_ids = _unique_in_order([chunk.document_id for chunk in response.citations])
    relevant = set(case.relevant_document_ids)
    # 答えるべきでない質問(#301)は拒答の正しさだけを測る。正解の文書が無いケースは検索の
    # 指標の対象外(#591)。
    answerable = case.expects_answer
    abstained = is_abstained(response)
    hits: list[str] = []
    context_recall: float | None = None
    reciprocal_rank: float | None = None
    if relevant and not _skips_retrieval(response):
        hits = [doc_id for doc_id in retrieved_ids if doc_id in relevant]
        context_recall = len(set(hits)) / len(relevant)
        reciprocal_rank = _reciprocal_rank(retrieved_ids, relevant)

    faithfulness: float | None = None
    overlap_count = 0
    feature_count = 0
    if not abstained and response.answer.strip():
        groundedness = evaluate_groundedness(
            grounding_text(response.answer), "\n".join(chunk.text for chunk in response.citations)
        )
        faithfulness = groundedness.score
        overlap_count = groundedness.overlap_count
        feature_count = groundedness.answer_feature_count

    citation_coverage = (
        citation_traceability_coverage(response.citations) if response.citations else None
    )
    keyword_hit = (
        _answer_contains_keywords(response.answer, case.expected_answer_keywords)
        if case.expected_answer_keywords
        else None
    )
    handling = _handling_scores(case, response, abstained=abstained)
    failure_reasons = _case_failure_reasons(
        answerable=answerable,
        has_relevant=bool(relevant) and not _skips_retrieval(response),
        relevant_count=len(relevant),
        hit_count=len(set(hits)),
        abstained=abstained,
        keyword_hit=keyword_hit,
        guardrail_warnings=response.guardrail_warnings,
        judgement=judgement,
    ) + _handling_failure_reasons(handling)
    return EvaluationCaseResult(
        case_id=case.id,
        trace_id=response.trace_id,
        category=case.category,
        retrieved_document_ids=retrieved_ids,
        relevant_document_ids=list(case.relevant_document_ids),
        hit_document_ids=_unique_in_order(hits),
        context_recall=_round(context_recall),
        reciprocal_rank=_round(reciprocal_rank),
        faithfulness=_round(faithfulness),
        grounding_overlap_count=overlap_count,
        grounding_answer_feature_count=feature_count,
        citation_traceability_coverage=_round(citation_coverage),
        answer_keyword_hit=keyword_hit,
        abstained=abstained,
        refusal_correct=abstained != answerable,
        **handling,
        answer_evaluation=judgement,
        guardrail_warnings=response.guardrail_warnings,
        failure_reasons=list(failure_reasons),
        diagnostics=response.diagnostics,
        elapsed_ms=response.elapsed_ms,
    )


def _handling_scores(
    case: EvaluationCase, response: SearchResponse, *, abstained: bool
) -> dict[str, Any]:
    """業務支援の採点（対応・手順・危険な回答・条件。#1231）。期待の無い項目は None / 空。"""
    observed = observed_outcome(response.diagnostics.answer, abstained=abstained)
    scores: dict[str, Any] = {
        "observed_outcome": observed.outcome,
        "outcome_source": observed.source,
    }
    if case.expected_outcomes:
        scores["handling_correct"] = observed.outcome in case.expected_outcomes
    if case.expected_steps:
        score, missing = step_order_score(response.answer, case.expected_steps)
        scores["step_order_score"] = _round(score)
        scores["missing_steps"] = missing
    if case.forbidden_phrases:
        scores["forbidden_checked"] = True
        scores["forbidden_hits"] = forbidden_hits(response.answer, case.forbidden_phrases)
    if case.required_conditions:
        coverage, missing = condition_coverage(response.answer, case.required_conditions)
        scores["condition_coverage"] = _round(coverage)
        scores["missing_conditions"] = missing
    return scores


def _handling_failure_reasons(scores: Mapping[str, Any]) -> list[EvaluationFailureReason]:
    reasons: list[EvaluationFailureReason] = []
    if scores.get("handling_correct") is False:
        reasons.append("unexpected_handling")
    step_score = scores.get("step_order_score")
    if step_score is not None and step_score < 1.0:
        reasons.append("step_missing")
    if scores.get("forbidden_hits"):
        reasons.append("forbidden_action")
    if scores.get("missing_conditions"):
        reasons.append("condition_missing")
    return reasons


def _safe_answer(result: EvaluationCaseResult, checked: bool) -> float | None:
    if not checked:
        return None
    return 0.0 if result.forbidden_hits else 1.0


# 検索をせずに利用者へ確かめる・人へ引き継ぐ回答（#1259）。拒答でも検索の取りこぼしでもない。
_NO_RETRIEVAL_OUTCOMES = frozenset({"needs_clarification", "needs_human"})


def _explicit_outcome(response: SearchResponse) -> str | None:
    details = response.diagnostics.answer or {}
    outcome = details.get("outcome")
    return outcome if isinstance(outcome, str) and outcome else None


def _skips_retrieval(response: SearchResponse) -> bool:
    """確認の質問・人への引き継ぎで引用の無い回答（検索の指標の対象外）。"""
    return _explicit_outcome(response) in _NO_RETRIEVAL_OUTCOMES and not response.citations


def is_abstained(response: SearchResponse) -> bool:
    """回答が「資料から答えられない」旨だけか(拒答)を、回答の記録から判定する。

    回答の記録が対応（outcome。#1235）を持つときは insufficient_evidence だけを拒答とする（確認の
    質問・人への引き継ぎは拒答ではない。#1259）。持たない古い記録は本文・引用・不足の理由で判定する。
    """
    outcome = _explicit_outcome(response)
    if outcome is not None:
        return outcome == "insufficient_evidence"
    details = response.diagnostics.answer or {}
    return is_abstained_answer(
        response.answer, response.citations, str(details.get("insufficient_reason") or "")
    )


def _accumulate_case_metrics(aggregate: _Aggregate, result: EvaluationCaseResult) -> None:
    """成功したケースの測れた指標を集計へ足す。"""
    aggregate.add("context_recall", result.context_recall)
    aggregate.add("mrr", result.reciprocal_rank)
    aggregate.add("faithfulness", result.faithfulness)
    aggregate.add("citation_traceability_coverage", result.citation_traceability_coverage)
    aggregate.add("answer_keyword_hit_rate", _bool_value(result.answer_keyword_hit))
    aggregate.add("refusal_accuracy", _bool_value(result.refusal_correct))
    judgement = result.answer_evaluation
    if judgement is not None and judgement.status == "completed":
        aggregate.add("claim_support_rate", _bool_value(judgement.claims_supported))
        aggregate.add("requirement_coverage", judgement.requirement_coverage)
        aggregate.add("answer_pass_rate", _bool_value(judgement.passed))
    aggregate.add("handling_accuracy", _bool_value(result.handling_correct))
    aggregate.add("step_order_score", result.step_order_score)
    aggregate.add("safe_answer_rate", _safe_answer(result, result.forbidden_checked))
    aggregate.add("condition_coverage", result.condition_coverage)


def _experiment_progress(
    progress: EvaluationProgressCallback | None,
    *,
    offset: int,
    experiment_id: str,
) -> EvaluationProgressCallback | None:
    """比較の 1 experiment の進捗を、experiment をまたいだ通しの件数に直して渡す。"""
    if progress is None:
        return None

    async def report(
        *,
        completed_cases: int,
        current_case_id: str | None,
        current_experiment_id: str | None,
    ) -> None:
        del current_experiment_id
        await progress(
            completed_cases=offset + completed_cases,
            current_case_id=current_case_id,
            current_experiment_id=experiment_id if current_case_id is not None else None,
        )

    return report


def _deadline(time_budget_seconds: float | None) -> float | None:
    """評価全体の上限の期限（`perf_counter` の時刻）。上限がなければ None。"""
    return None if time_budget_seconds is None else perf_counter() + time_budget_seconds


def _remaining_seconds(deadline: float | None) -> float | None:
    """評価全体の上限までの残り秒数。上限がなければ None。"""
    return None if deadline is None else deadline - perf_counter()


def _case_timeout_message(error: AnswerTimeoutError, *, limited_by_budget: bool) -> str:
    """時間切れのケースの文言。時間切れになった工程を含め、query 本文は含めない（#383）。

    評価全体の上限の秒数は、比較では experiment ごとの残り時間になるため文言に出さない。
    """
    stage = answer_stage_label(error.stage)
    if limited_by_budget:
        return (
            EVALUATION_TIME_BUDGET_MESSAGE_PREFIX
            + f"評価ケースの回答生成を打ち切りました（時間切れになった工程: {stage}）。"
            + EVALUATION_TIME_BUDGET_MESSAGE_SUFFIX
        )
    limit = "時間内に"
    if error.timeout_seconds is not None:
        limit = f"上限の {format_timeout_limit(error.timeout_seconds)}以内に"
    return (
        f"評価ケースの回答生成が{limit}終わりませんでした（時間切れになった工程: {stage}）。"
        "trace_id で監査ログを確認してください。"
    )


def _reciprocal_rank(retrieved_ids: list[str], relevant_ids: set[str]) -> float:
    for index, document_id in enumerate(retrieved_ids, start=1):
        if document_id in relevant_ids:
            return 1.0 / index
    return 0.0


def _metric_value(metrics: EvaluationMetrics, metric: EvaluationMetricName) -> float | None:
    """ranking metric の値を取り出す(測れなかったときは None)。"""
    value = getattr(metrics, metric)
    return None if value is None else float(value)


def _experiment_sort_key(
    result: EvaluationExperimentResult,
) -> tuple[int, int, float, int, int, str]:
    """passed 優先、metric 降順(測れないものは後ろ)、エラー・失敗理由少数、ID 昇順。"""
    score = result.ranking_score
    return (
        0 if result.metrics.passed else 1,
        1 if score is None else 0,
        -(score or 0.0),
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
    error_stage: str | None = None,
    error_message: str = EVALUATION_CASE_ERROR_MESSAGE,
) -> EvaluationCaseResult:
    """評価 case の失敗を query 本文なしの診断結果に変換する。"""
    return EvaluationCaseResult(
        case_id=case.id,
        category=case.category,
        trace_id=trace_id,
        status="error",
        relevant_document_ids=list(case.relevant_document_ids),
        failure_reasons=["case_error"],
        elapsed_ms=elapsed,
        error_type=type(error).__name__,
        error_stage=error_stage,
        error_message=error_message,
    )


def _case_skipped_result(*, case: EvaluationCase, trace_id: str) -> EvaluationCaseResult:
    """評価全体の上限に達して実行しなかったケースを、失敗として記録する（#383）。"""
    return EvaluationCaseResult(
        case_id=case.id,
        category=case.category,
        trace_id=trace_id,
        status="error",
        relevant_document_ids=list(case.relevant_document_ids),
        failure_reasons=["case_error"],
        elapsed_ms=0.0,
        error_type=EVALUATION_TIME_BUDGET_ERROR_TYPE,
        error_message=(
            EVALUATION_TIME_BUDGET_MESSAGE_PREFIX
            + "このケースは実行していません。"
            + EVALUATION_TIME_BUDGET_MESSAGE_SUFFIX
        ),
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
    record_rag_request(SEARCH_METRIC_MODE, "error", elapsed / 1000, 0)
    diagnostics = build_search_diagnostics(
        request, settings=settings, retrieval_strategy_adapter="grounded"
    )
    record_rag_search_audit(
        trace_id=trace_id,
        outcome="error",
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
    answerable: bool,
    has_relevant: bool,
    relevant_count: int,
    hit_count: int,
    abstained: bool,
    keyword_hit: bool | None,
    guardrail_warnings: list[str],
    judgement: EvaluationAnswerJudgement | None,
) -> list[EvaluationFailureReason]:
    """case 単位の失敗原因を安全なカテゴリへ分類する。"""
    reasons: list[EvaluationFailureReason] = []
    if has_relevant:
        if hit_count == 0:
            reasons.append("retrieval_miss")
        elif hit_count < relevant_count:
            reasons.append("partial_recall")
    if answerable and abstained:
        reasons.append("unexpected_refusal")
    elif not answerable and not abstained:
        reasons.append("unexpected_answer")
    if keyword_hit is False:
        reasons.append("answer_keyword_miss")
    if judgement is not None:
        if judgement.status != "completed":
            reasons.append("answer_evaluation_error")
        else:
            if judgement.claims_supported is False:
                reasons.append("unsupported_claim")
            if judgement.missing_content:
                reasons.append("missing_content")
            if judgement.passed is False:
                reasons.append("answer_failed")
    if guardrail_warnings and answerable:
        reasons.append("guardrail_warning")
    return reasons


def _accumulate_failure_reasons(counts: dict[str, int], reasons: Sequence[str]) -> None:
    """失敗理由の case 件数を集計する。"""
    for reason in reasons:
        counts[reason] = counts.get(reason, 0) + 1


def _answer_contains_keywords(answer: str, keywords: list[str]) -> bool:
    normalized = answer.lower()
    return all(keyword.lower() in normalized for keyword in keywords)


def _threshold_failures(
    thresholds: EvaluationThresholds | None,
    aggregate_values: Mapping[str, float | None],
) -> list[EvaluationThresholdFailure]:
    """設定された最低閾値を下回った aggregate metric を返す(測れなかった指標は判定しない)。"""
    if thresholds is None:
        return []

    failures: list[EvaluationThresholdFailure] = []
    for metric, threshold in thresholds.model_dump(exclude_none=True).items():
        # 語句の一致率(faithfulness)は参考値。閾値は目安として表示し、合否に使わない(#711)。
        if metric in REFERENCE_ONLY_METRICS:
            continue
        actual = aggregate_values.get(metric)
        if actual is not None and actual < threshold:
            failures.append(
                EvaluationThresholdFailure(metric=metric, actual=actual, threshold=threshold)
            )
    return failures


def _mapping_list(value: object) -> list[Mapping[str, object]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _optional_float(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _bool_value(value: bool | None) -> float | None:
    return None if value is None else (1.0 if value else 0.0)


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, 4)


def _rate(values: list[bool]) -> float | None:
    return round(sum(values) / len(values), 4) if values else None


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 4) if values else None


def category_breakdown(
    results: list[EvaluationCaseResult],
) -> dict[str, EvaluationCategorySummary]:
    """分類ごとの結果の内訳（#1226）。分類のあるケースが 1 つも無ければ空にする。"""
    if not any(result.category for result in results):
        return {}
    grouped: dict[str, list[EvaluationCaseResult]] = {}
    for result in results:
        grouped.setdefault(result.category or EVALUATION_UNCATEGORIZED, []).append(result)
    breakdown: dict[str, EvaluationCategorySummary] = {}
    for category, members in grouped.items():
        succeeded = [result for result in members if result.status == "success"]
        breakdown[category] = EvaluationCategorySummary(
            case_count=len(members),
            error_count=len(members) - len(succeeded),
            answer_pass_rate=_rate(
                [
                    bool(result.answer_evaluation.passed)
                    for result in succeeded
                    if result.answer_evaluation is not None
                    and result.answer_evaluation.passed is not None
                ]
            ),
            answer_keyword_hit_rate=_rate(
                [
                    bool(result.answer_keyword_hit)
                    for result in succeeded
                    if result.answer_keyword_hit is not None
                ]
            ),
            abstain_rate=_rate(
                [bool(result.abstained) for result in succeeded if result.abstained is not None]
            ),
            refusal_correct_rate=_rate(
                [
                    bool(result.refusal_correct)
                    for result in succeeded
                    if result.refusal_correct is not None
                ]
            ),
            handling_correct_rate=_rate(
                [
                    bool(result.handling_correct)
                    for result in succeeded
                    if result.handling_correct is not None
                ]
            ),
            step_order_score=_mean(
                [
                    result.step_order_score
                    for result in succeeded
                    if result.step_order_score is not None
                ]
            ),
            safe_answer_rate=_rate(
                [not result.forbidden_hits for result in succeeded if result.forbidden_checked]
            ),
        )
    return breakdown
