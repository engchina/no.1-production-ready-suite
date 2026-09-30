"""RAG 評価ランナー。

golden set の各ケースを回答エンジン(根拠付き回答。全体の既定の設定)で回答し、回答の記録
(引用・根拠・実行記録)から指標を求める(#591)。指標は 3 つの観点に整理した 9 つ。

- 検索: context_recall(正解の文書を取れたか)/ mrr(正解の文書が何番目に出たか)
- 根拠: faithfulness(回答の語が根拠に含まれる割合)/ citation_traceability_coverage(引用を
  原文の位置へたどれる割合)/ claim_support_rate(標準回答による評価で、根拠のない主張・根拠と
  矛盾する主張が無いケースの割合)
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
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import partial
from time import perf_counter
from typing import Protocol

from app.clients.oracle import OracleClient
from app.config import OCI_ENTERPRISE_AI_TIMEOUT_MAX_SECONDS, Settings, get_settings
from app.rag.answer_timeout import (
    AnswerTimeoutError,
    answer_stage_label,
    answer_timeout_seconds,
    format_timeout_limit,
    run_answer_with_timeout,
)
from app.rag.audit import record_rag_search_audit
from app.rag.diagnostics import build_search_diagnostics
from app.rag.docrag_answer import DOCRAG_ANSWER_ENGINE, evaluate_answer_record
from app.rag.file_processing_evaluation import citation_traceability_coverage
from app.rag.generation_config import resolve_oracle_generation_settings
from app.rag.guardrails import evaluate_groundedness
from app.rag.observability import (
    elapsed_ms,
    new_trace_id,
    record_evaluation_case,
    record_rag_request,
)
from app.rag.pipeline import RagPipeline, SearchStageProgressCallback
from app.schemas.evaluation import (
    EVALUATION_METRIC_NAMES,
    EvaluationAnswerJudgement,
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
    "この回答には標準回答で評価するための記録がありません(回答エンジンの設定を確認してください)。"
)
ANSWER_JUDGE_TIMEOUT_MESSAGE = "標準回答による評価が時間内に終わりませんでした。"
ANSWER_JUDGE_ERROR_MESSAGE = "標準回答による評価を完了できませんでした。"
# 主張の監査で「根拠で確かめられない」とする判定(rag_poc の run_answer_eval と同じ)。
UNSUPPORTED_CLAIM_STATUSES = frozenset({"unsupported", "contradicted"})
logger = logging.getLogger(__name__)
_DEFAULT_RERANK_TOP_N = int(SearchRequest.model_fields["rerank_top_n"].default)


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
    """標準回答で回答を評価する(rag_poc の 4 軸の評価。LLM を複数回呼ぶ)。

    戻り値は `evaluate_answer_payload` の結果(status / total_score / passed / claim_checks /
    coverage_checks / coverage_cap など)。時間切れは TimeoutError を送出する。
    """

    async def __call__(
        self,
        *,
        trace_id: str,
        evaluation_input: Mapping[str, object],
        standard_answer: str,
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
    timeout_seconds: float,
    settings: Settings | None = None,
) -> Mapping[str, object]:
    """回答を標準回答で評価し、結果を回答の記録にも保存する(保存は best-effort)。

    worker thread の評価は止められないため、時間切れのときは結果を捨てる(#304 と同じ)。
    """
    resolved = settings or get_settings()
    evaluation = await asyncio.wait_for(
        asyncio.to_thread(evaluate_answer_record, evaluation_input, standard_answer, resolved),
        timeout=max(0.001, timeout_seconds),
    )
    saved = {
        **evaluation,
        "standard_answer": standard_answer,
        "evaluated_at": datetime.now(UTC).isoformat(),
    }
    try:
        # 回答の記録の画面から、軸ごとの理由・主張ごとの判定を確かめられるようにする。
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


class EvaluationRunner:
    """小規模な golden set を使って検索・根拠・回答の品質を評価する。"""

    def __init__(
        self,
        pipeline: SearchPipeline | None = None,
        settings: Settings | None = None,
        answer_judge: AnswerJudge | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._pipeline = pipeline
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
        time_budget_seconds: float | None = None,
        progress: EvaluationProgressCallback | None = None,
    ) -> EvaluationMetrics:
        """評価ケースを実行し、集計指標を返す。

        1 ケースは回答生成の上限（`rag_answer_timeout_seconds`）で打ち切り、時間切れになった工程を
        ケースの結果に残して次のケースへ進む（#383）。`time_budget_seconds` を渡すと、評価全体を
        その秒数で打ち切る（同期の HTTP の待ちを超えないため）。上限に達したら実行中のケースを
        打ち切り、残りのケースは実行せずに失敗として記録する。`progress` には、ケースを始める
        前と最後のケースの後に進捗を渡す（評価 job。#390）。
        """
        deadline = _deadline(time_budget_seconds)
        effective_settings = evaluation_settings(self._settings, rag_overrides)
        if self._pipeline is None:
            effective_settings = await resolve_oracle_generation_settings(effective_settings)
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
                # 回答エンジンは rerank の件数を使わない。SearchRequest の制約(top_k 以下)だけ守る。
                rerank_top_n=min(top_k, _DEFAULT_RERANK_TOP_N),
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
                # 工程を記録する tracker は、3 番目の引数（progress_callback）として渡る。
                response = await run_answer_with_timeout(
                    partial(pipeline.run, request, trace_id),
                    effective_settings,
                    timeout_seconds=remaining if limited_by_budget else None,
                )
            except AnswerTimeoutError as exc:
                elapsed = elapsed_ms(case_started_at)
                record_evaluation_case(request.mode.value, "error", elapsed / 1000)
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
        timeout_seconds: float,
    ) -> Mapping[str, object]:
        return await judge_answer_with_standard(
            trace_id=trace_id,
            evaluation_input=evaluation_input,
            standard_answer=standard_answer,
            timeout_seconds=timeout_seconds,
            settings=settings,
        )

    return judge


def summarize_answer_judgement(evaluation: Mapping[str, object]) -> EvaluationAnswerJudgement:
    """rag_poc の評価結果を、指標に使う要約へ変換する。"""
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
    coverage_cap = _optional_float(evaluation.get("coverage_cap"))
    max_score = _optional_float(evaluation.get("max_score")) or 20.0
    goal = evaluation.get("goal_alignment")
    return EvaluationAnswerJudgement(
        status="completed",
        total_score=_optional_float(evaluation.get("total_score")),
        max_score=max_score,
        passed=bool(evaluation.get("passed")),
        claims_supported=not any(
            str(claim.get("status")) in UNSUPPORTED_CLAIM_STATUSES for claim in claims
        ),
        # 網羅性の点(原質問に必要な項目への対応率 × 5)を 0..1 にする。
        requirement_coverage=(
            round(min(1.0, max(0.0, coverage_cap / 5.0)), 4) if coverage_cap is not None else None
        ),
        missing_content=any(str(check.get("status")) == "missing" for check in coverage_checks),
        goal_alignment=str(goal) if goal else None,
        message=str(message) if message else None,
    )


def evaluation_settings(
    settings: Settings,
    overrides: EvaluationRagOverrides | None,
) -> Settings:
    """評価に使う設定。回答エンジンを根拠付き回答にし、experiment の上書きを一時適用する。

    評価は業務ビューを受け取らず、全体の既定で動く(#301)。回答エンジンの全体の既定が
    別のエンジンでも、評価は根拠付き回答(回答の記録を残すエンジン)で行う(#591)。
    """
    update: dict[str, object] = {"rag_answer_engine": DOCRAG_ANSWER_ENGINE}
    if overrides is not None:
        mapping = {
            "query_strategy": "rag_docrag_query_strategy",
            "answer_flow": "rag_docrag_answer_flow",
            "neighbor_child_count": "rag_docrag_neighbor_child_count",
            "rerank_enabled": "rag_docrag_rerank_enabled",
            "rrf_k": "rag_rrf_k",
            "context_group_max_chunks": "rag_context_group_max_chunks",
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
    if relevant:
        hits = [doc_id for doc_id in retrieved_ids if doc_id in relevant]
        context_recall = len(set(hits)) / len(relevant)
        reciprocal_rank = _reciprocal_rank(retrieved_ids, relevant)

    faithfulness: float | None = None
    grounded = True
    overlap_count = 0
    feature_count = 0
    if not abstained and response.answer.strip():
        groundedness = evaluate_groundedness(
            response.answer, "\n".join(chunk.text for chunk in response.citations)
        )
        faithfulness = groundedness.score
        grounded = groundedness.grounded
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
    failure_reasons = _case_failure_reasons(
        answerable=answerable,
        has_relevant=bool(relevant),
        relevant_count=len(relevant),
        hit_count=len(set(hits)),
        abstained=abstained,
        keyword_hit=keyword_hit,
        grounded=grounded,
        guardrail_warnings=response.guardrail_warnings,
        judgement=judgement,
    )
    return EvaluationCaseResult(
        case_id=case.id,
        trace_id=response.trace_id,
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
        answer_evaluation=judgement,
        guardrail_warnings=response.guardrail_warnings,
        failure_reasons=list(failure_reasons),
        diagnostics=response.diagnostics,
        elapsed_ms=response.elapsed_ms,
    )


def is_abstained(response: SearchResponse) -> bool:
    """回答が「資料から答えられない」旨だけか(拒答)を、回答の記録から判定する。

    本文か引用が無い回答は拒答。回答エンジンが不足の理由を返し、モデルが使った根拠が 1 つも
    無い回答も拒答とする(必要な根拠を原文のまま示すだけの回答を含む)。
    """
    if not response.answer.strip() or not response.citations:
        return True
    details = response.diagnostics.docrag or {}
    insufficient = str(details.get("insufficient_reason") or "").strip()
    if not insufficient:
        return False
    return not any(_model_used(chunk) for chunk in response.citations)


def _model_used(chunk: RetrievedChunk) -> bool:
    return bool(chunk.metadata.get("docrag_model_used"))


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
    answerable: bool,
    has_relevant: bool,
    relevant_count: int,
    hit_count: int,
    abstained: bool,
    keyword_hit: bool | None,
    grounded: bool,
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
    if not grounded:
        reasons.append("low_groundedness")
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
