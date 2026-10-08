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

業務支援の評価の契約（#1284。handoff §14.1 / §14.2）: ケースの既知の条件（`conditions`）は
検索・回答の request の `conditions` に渡す。確認の質問への返答（`turns`）は、チャットと同じく
返答を次の質問にし、前の往復を会話の履歴として順に実行する。結果は区分（dev / holdout）ごとの
内訳と、必要な根拠ごとの再現率（`required_evidence_recall`。参考の集計）を持つ。

多段の質問の評価（#1335）: 必要な根拠をすべて取れたケースの割合（`evidence_chain_complete_rate`。
参考の集計）と、ケースの種類（`reasoning_type`）別・段の数（`hops`）別の内訳（必要な根拠の再現率・
根拠の連鎖の完全率・期待する語の一致率）を持つ。
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
    asked_condition_ids,
    best_step_order_score,
    condition_coverage,
    contains_normalized,
    forbidden_hits,
    observed_outcome,
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
from app.rag.pipeline import ChatTurn, RagPipeline, SearchStageProgressCallback
from app.schemas.evaluation import (
    EVALUATION_METRIC_NAMES,
    EVALUATION_REASONING_METRIC_NAMES,
    EVALUATION_REPORT_ONLY_METRIC_NAMES,
    EVALUATION_UNASSIGNED_SPLIT,
    EVALUATION_UNCATEGORIZED,
    EVALUATION_UNSPECIFIED_REASONING,
    EvaluationAnswerJudgement,
    EvaluationCase,
    EvaluationCaseResult,
    EvaluationCategorySummary,
    EvaluationCompareResponse,
    EvaluationExperiment,
    EvaluationExperimentResult,
    EvaluationFailureReason,
    EvaluationHandlingExpectation,
    EvaluationMetricName,
    EvaluationMetrics,
    EvaluationRagOverrides,
    EvaluationReasoningSummary,
    EvaluationSplitSummary,
    EvaluationThresholdFailure,
    EvaluationThresholds,
    EvaluationTurnResult,
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
# 返答の往復で、業務ガイドの照合に足す前の質問の数
# （チャットの GUIDE_CONTEXT_TURNS と同じ。#1238 / #1284）。
EVALUATION_GUIDE_CONTEXT_TURNS = 3
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


# 集計する指標（閾値の判定に使う指標と、結果に出すだけの参考の集計。#1284）。
_AGGREGATED_METRIC_NAMES: tuple[str, ...] = (
    *EVALUATION_METRIC_NAMES,
    *EVALUATION_REPORT_ONLY_METRIC_NAMES,
)


@dataclass
class _Aggregate:
    """指標ごとに、測れたケースの値を集める。"""

    values: dict[str, list[float]] = field(default_factory=dict)

    def add(self, metric: str, value: float | None) -> None:
        if value is not None:
            self.values.setdefault(metric, []).append(float(value))

    def means(self) -> dict[str, float | None]:
        return {
            metric: (
                round(sum(self.values[metric]) / len(self.values[metric]), 4)
                if self.values.get(metric)
                else None
            )
            for metric in _AGGREGATED_METRIC_NAMES
        }

    def counts(self) -> dict[str, int]:
        return {metric: len(self.values.get(metric, [])) for metric in _AGGREGATED_METRIC_NAMES}


@dataclass(frozen=True)
class _TurnRun:
    """1 回の回答と、その質問・返答までに渡した条件（#1284）。"""

    response: SearchResponse
    conditions: Mapping[str, str] = field(default_factory=dict)


# (検索・回答の request, Settings, 業務ガイドの照合に足す前の質問) → 解決した request と Settings。
ProfileResolver = Callable[
    [SearchRequest, Settings, Sequence[str]], Awaitable[tuple[SearchRequest, Settings]]
]


async def _resolve_profile_context(
    request: SearchRequest, settings: Settings, guide_context: Sequence[str] = ()
) -> tuple[SearchRequest, Settings]:
    """検索・回答と同じ解決で、プロファイルの KB・回答の設定・業務ガイドを当てる（#1249）。

    返答の往復（#1284）では、チャットと同じく前の質問も業務ガイドの照合に使う。確認の質問は回答として
    測るため、検索の API と同じく ``interactive`` にしない（不明の条件は確認の質問になる）。
    """
    from app.api.routes.search import _resolve_query_context

    resolved, resolved_settings, _kb, _view = await _resolve_query_context(
        request, settings, guide_context=guide_context
    )
    return resolved, resolved_settings


def _case_queries(case: EvaluationCase) -> list[tuple[str, dict[str, str]]]:
    """最初の質問と返答を、送る順に（質問・返答, その発話で分かった条件）で返す（#1284）。"""
    return [
        (case.query, dict(case.conditions)),
        *((turn.reply, dict(turn.conditions)) for turn in case.turns),
    ]


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

        case_results: list[EvaluationCaseResult] = []

        for case_index, case in enumerate(cases):
            if progress is not None:
                await progress(
                    completed_cases=case_index,
                    current_case_id=case.id,
                    current_experiment_id=None,
                )
            trace_id = new_trace_id()
            remaining = _remaining_seconds(deadline)
            if remaining is not None and remaining <= 0:
                # 評価全体の上限に達した。残りのケースは pipeline を呼ばずに失敗として記録する。
                case_results.append(_case_skipped_result(case=case, trace_id=trace_id))
                continue
            case_started_at = perf_counter()
            # 最初の質問と、確認の質問への返答（#1284）を順に送る。返答はチャットと同じく、
            # 前の往復を会話の履歴と業務ガイドの照合の文脈にし、分かった条件を足して渡す。
            turn_runs: list[_TurnRun] = []
            history: list[ChatTurn] = []
            prior_queries: list[str] = []
            conditions: dict[str, str] = {}
            error_result: EvaluationCaseResult | None = None
            for turn_index, (query, turn_conditions) in enumerate(_case_queries(case)):
                conditions = {**conditions, **turn_conditions}
                request = SearchRequest(
                    query=query,
                    top_k=top_k,
                    filters=dict(filters or {}),
                    knowledge_base_ids=list(knowledge_base_ids or []),
                    conditions=dict(conditions),
                )
                turn_trace_id = trace_id if turn_index == 0 else new_trace_id()
                remaining = _remaining_seconds(deadline)
                if remaining is not None and remaining <= 0:
                    # 往復の途中で評価全体の上限に達した。ケースは失敗にする。
                    error_result = _case_skipped_result(
                        case=case, trace_id=turn_trace_id, partially_run=True
                    )
                    break
                case_limit = answer_timeout_seconds(effective_settings)
                limited_by_budget = remaining is not None and remaining < case_limit
                try:
                    run_case: Any = partial(pipeline.run, request, turn_trace_id)
                    if search_answer_profile_id:
                        # プロファイルの解決（KB・回答の設定・業務ガイド）。
                        # 失敗はケースの失敗にする。
                        request, case_settings = await self._profile_resolver(
                            request.model_copy(
                                update={"search_answer_profile_id": search_answer_profile_id}
                            ),
                            effective_settings,
                            prior_queries[-EVALUATION_GUIDE_CONTEXT_TURNS:],
                        )
                        case_pipeline = self._pipeline or RagPipeline(settings=case_settings)
                        run_case = partial(case_pipeline.run, request, turn_trace_id)
                    if history:
                        # 返答はチャットと同じく、会話の履歴を踏まえて単独の質問に
                        # 書き換えてから答える。
                        run_case = partial(run_case, history=list(history))
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
                        trace_id=turn_trace_id,
                        request=request,
                        elapsed=elapsed,
                        # 監査と結果の error_type は、従来どおり元の TimeoutError にする。
                        error=exc.original_error,
                        settings=effective_settings,
                        error_stage="timeout",
                    )
                    error_result = _case_error_result(
                        case=case,
                        trace_id=turn_trace_id,
                        elapsed=elapsed,
                        error=exc.original_error,
                        error_stage=exc.stage,
                        error_message=_case_timeout_message(
                            exc,
                            # 工程の中の時間切れ（LLM 1 回の timeout など）は、全体の上限ではない。
                            limited_by_budget=limited_by_budget and exc.timeout_seconds is not None,
                        ),
                    )
                    break
                except Exception as exc:
                    elapsed = elapsed_ms(case_started_at)
                    record_evaluation_case(SEARCH_METRIC_MODE, "error", elapsed / 1000)
                    _record_case_error_audit(
                        trace_id=turn_trace_id,
                        request=request,
                        elapsed=elapsed,
                        error=exc,
                        settings=effective_settings,
                        error_stage="evaluation",
                    )
                    error_result = _case_error_result(
                        case=case,
                        trace_id=turn_trace_id,
                        elapsed=elapsed,
                        error=exc,
                    )
                    break
                turn_runs.append(_TurnRun(response=response, conditions=dict(conditions)))
                history.extend(
                    [
                        ChatTurn(role="USER", content=query),
                        ChatTurn(role="ASSISTANT", content=response.answer),
                    ]
                )
                prior_queries.append(query)

            if error_result is not None:
                case_results.append(error_result)
                continue

            record_evaluation_case(
                SEARCH_METRIC_MODE,
                "success",
                elapsed_ms(case_started_at) / 1000,
            )
            final_response = turn_runs[-1].response
            judgement = await self._judge(case, final_response, deadline)
            case_results.append(_case_result(case, final_response, judgement, turn_runs=turn_runs))

        if progress is not None:
            await progress(
                completed_cases=len(cases),
                current_case_id=None,
                current_experiment_id=None,
            )
        return summarize_case_results(case_results, thresholds=thresholds)

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


def summarize_case_results(
    case_results: Sequence[EvaluationCaseResult],
    *,
    thresholds: EvaluationThresholds | None = None,
) -> EvaluationMetrics:
    """ケースの結果を集計して評価の結果にする（評価ランナーと、外部の回答の採点で共通。#1289）。

    指標は成功したケースのうち測れたものだけの平均。失敗したケースや、標準回答で評価できなかった
    ケースがあれば合格にしない。
    """
    results = list(case_results)
    aggregate = _Aggregate()
    failure_reason_counts: dict[str, int] = {}
    error_count = 0
    judge_incomplete_count = 0
    for result in results:
        _accumulate_failure_reasons(failure_reason_counts, result.failure_reasons)
        if result.status != "success":
            error_count += 1
            continue
        judgement = result.answer_evaluation
        if judgement is not None and judgement.status != "completed":
            judge_incomplete_count += 1
        _accumulate_case_metrics(aggregate, result)
    aggregate_values = aggregate.means()
    threshold_failures = _threshold_failures(thresholds, aggregate_values)
    return EvaluationMetrics(
        case_count=len(results),
        error_count=error_count,
        category_breakdown=category_breakdown(results),
        split_breakdown=split_breakdown(results),
        reasoning_type_breakdown=reasoning_type_breakdown(results),
        hops_breakdown=hops_breakdown(results),
        passed=not threshold_failures and error_count == 0 and judge_incomplete_count == 0,
        threshold_failures=threshold_failures,
        failure_reason_counts=failure_reason_counts,
        metric_case_counts=aggregate.counts(),
        case_results=results,
        **aggregate_values,
    )


def score_case_answers(
    case: EvaluationCase,
    answers: Sequence[tuple[SearchResponse, Mapping[str, str]]],
    judgement: EvaluationAnswerJudgement | None = None,
) -> EvaluationCaseResult:
    """外部で作った回答（Agent の Run など。#1289）を、評価ランナーと同じ採点にかける。

    `answers` は最初の質問から順の（回答, その発話までに渡した既知の条件）。採点（対応・手順と別解・
    危険な回答・条件・正解の文書・必要な根拠）は `EvaluationRunner.run` のケースと同じ関数で行う。
    """
    if not answers:
        raise ValueError("採点する回答がありません。")
    runs = [
        _TurnRun(response=response, conditions=dict(conditions)) for response, conditions in answers
    ]
    return _case_result(case, runs[-1].response, judgement, turn_runs=runs)


def case_error_result(
    case: EvaluationCase,
    *,
    trace_id: str,
    elapsed_ms: float,
    error_type: str,
    error_message: str,
    error_stage: str | None = None,
) -> EvaluationCaseResult:
    """回答を得られなかったケースの結果（質問の本文は含めない。外部の回答の採点で使う。#1289）。"""
    return EvaluationCaseResult(
        case_id=case.id,
        category=case.category,
        split=case.split,
        reasoning_type=case.reasoning_type,
        hops=case.hops,
        trace_id=trace_id,
        status="error",
        relevant_document_ids=list(case.relevant_document_ids),
        failure_reasons=["case_error"],
        elapsed_ms=elapsed_ms,
        error_type=error_type,
        error_stage=error_stage,
        error_message=error_message,
    )


def _case_result(
    case: EvaluationCase,
    response: SearchResponse,
    judgement: EvaluationAnswerJudgement | None,
    *,
    turn_runs: Sequence[_TurnRun] | None = None,
) -> EvaluationCaseResult:
    """1 ケースの回答から、測れる指標と失敗理由を求める。

    `response` は最後の回答。`turn_runs`（#1284）は最初の質問からの全部の回答で、省略したときは
    `response` だけの 1 往復とみなす。
    """
    runs = list(turn_runs) if turn_runs else [_TurnRun(response, dict(case.conditions))]
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
    evidence_recall, missing_evidence = _evidence_scores(case, response)
    expectations: list[EvaluationHandlingExpectation] = [case, *case.turns]
    turn_results = [
        _turn_result(index, expectation, run)
        for index, (expectation, run) in enumerate(zip(expectations, runs, strict=False))
    ]
    handling = _combine_turn_results(turn_results)
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
    if missing_evidence:
        failure_reasons.append("evidence_miss")
    return EvaluationCaseResult(
        case_id=case.id,
        trace_id=response.trace_id,
        category=case.category,
        split=case.split,
        reasoning_type=case.reasoning_type,
        hops=case.hops,
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
        evidence_recall=_round(evidence_recall),
        missing_evidence=missing_evidence,
        evidence_chain_complete=None if evidence_recall is None else not missing_evidence,
        turn_results=turn_results if case.turns else [],
        answer_evaluation=judgement,
        guardrail_warnings=response.guardrail_warnings,
        failure_reasons=list(failure_reasons),
        diagnostics=response.diagnostics,
        elapsed_ms=round(sum(run.response.elapsed_ms for run in runs), 3),
    )


def _turn_result(
    index: int, expectation: EvaluationHandlingExpectation, run: _TurnRun
) -> EvaluationTurnResult:
    """1 回の回答の業務支援の採点（#1231 / #1284）。

    対応・手順と別解・危険な回答・条件・渡した条件の聞き直しを採点する。
    """
    response = run.response
    observed = observed_outcome(response.diagnostics.answer, abstained=is_abstained(response))
    scores: dict[str, Any] = {
        "turn": index,
        "trace_id": response.trace_id,
        "observed_outcome": observed.outcome,
        "outcome_source": observed.source,
        "elapsed_ms": response.elapsed_ms,
    }
    if expectation.expected_outcomes:
        scores["handling_correct"] = observed.outcome in expectation.expected_outcomes
    if expectation.expected_steps:
        score, missing = best_step_order_score(
            response.answer, [expectation.expected_steps, *expectation.acceptable_alternatives]
        )
        scores["step_order_score"] = _round(score)
        scores["missing_steps"] = missing
    if expectation.forbidden_phrases:
        scores["forbidden_checked"] = True
        scores["forbidden_hits"] = forbidden_hits(response.answer, expectation.forbidden_phrases)
    if expectation.required_conditions:
        coverage, missing = condition_coverage(response.answer, expectation.required_conditions)
        scores["condition_coverage"] = _round(coverage)
        scores["missing_conditions"] = missing
    if run.conditions:
        scores["reasked_conditions"] = [
            condition_id
            for condition_id in asked_condition_ids(response.diagnostics.answer)
            if condition_id in run.conditions
        ]
    return EvaluationTurnResult(**scores)


def _combine_turn_results(turn_results: Sequence[EvaluationTurnResult]) -> dict[str, Any]:
    """往復ごとの採点をケースの採点にまとめる（1 往復ならその往復の採点のまま）。"""
    final = turn_results[-1]
    handling = [item.handling_correct for item in turn_results if item.handling_correct is not None]
    steps = [item.step_order_score for item in turn_results if item.step_order_score is not None]
    coverages = [
        item.condition_coverage for item in turn_results if item.condition_coverage is not None
    ]
    return {
        "observed_outcome": final.observed_outcome,
        "outcome_source": final.outcome_source,
        "handling_correct": all(handling) if handling else None,
        "step_order_score": _mean(steps),
        "missing_steps": _unique_in_order(
            [step for item in turn_results for step in item.missing_steps]
        ),
        "forbidden_checked": any(item.forbidden_checked for item in turn_results),
        "forbidden_hits": _unique_in_order(
            [hit for item in turn_results for hit in item.forbidden_hits]
        ),
        "condition_coverage": _mean(coverages),
        "missing_conditions": _unique_in_order(
            [condition for item in turn_results for condition in item.missing_conditions]
        ),
        "reasked_conditions": _unique_in_order(
            [condition for item in turn_results for condition in item.reasked_conditions]
        ),
    }


def _evidence_scores(
    case: EvaluationCase, response: SearchResponse
) -> tuple[float | None, list[str]]:
    """必要な根拠の再現率と、取れなかった根拠の id（#1284）。

    根拠の無いケースと、検索をしない回答（確認の質問・人への引き継ぎで引用の無い回答）は対象外。
    """
    if not case.required_evidence or _skips_retrieval(response):
        return None, []
    missing = [
        evidence.id
        for evidence in case.required_evidence
        if not any(
            (evidence.document_id is None or chunk.document_id == evidence.document_id)
            and contains_normalized(chunk.text, evidence.text)
            for chunk in response.citations
        )
    ]
    total = len(case.required_evidence)
    return (total - len(missing)) / total, missing


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
    if scores.get("reasked_conditions"):
        reasons.append("known_condition_reasked")
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
    aggregate.add("required_evidence_recall", result.evidence_recall)
    aggregate.add("evidence_chain_complete_rate", _bool_value(result.evidence_chain_complete))


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
        split=case.split,
        reasoning_type=case.reasoning_type,
        hops=case.hops,
        trace_id=trace_id,
        status="error",
        relevant_document_ids=list(case.relevant_document_ids),
        failure_reasons=["case_error"],
        elapsed_ms=elapsed,
        error_type=type(error).__name__,
        error_stage=error_stage,
        error_message=error_message,
    )


def _case_skipped_result(
    *, case: EvaluationCase, trace_id: str, partially_run: bool = False
) -> EvaluationCaseResult:
    """評価全体の上限に達して実行しなかったケースを、失敗として記録する（#383）。

    `partially_run` は、複数往復のケースの途中の返答で上限に達したとき（#1284）。
    """
    skipped = (
        "このケースの残りの返答は実行していません。"
        if partially_run
        else "このケースは実行していません。"
    )
    return EvaluationCaseResult(
        case_id=case.id,
        category=case.category,
        split=case.split,
        reasoning_type=case.reasoning_type,
        hops=case.hops,
        trace_id=trace_id,
        status="error",
        relevant_document_ids=list(case.relevant_document_ids),
        failure_reasons=["case_error"],
        elapsed_ms=0.0,
        error_type=EVALUATION_TIME_BUDGET_ERROR_TYPE,
        error_message=(
            EVALUATION_TIME_BUDGET_MESSAGE_PREFIX + skipped + EVALUATION_TIME_BUDGET_MESSAGE_SUFFIX
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
    """期待する語をすべて含むか（NFKC・大小文字・空白を無視。#1335）。

    全角の「ＨＲＭ」と「HRM」、「5 年」と「5年」を同じ語とみなす（手順・必要な根拠の照合と同じ）。
    """
    return all(contains_normalized(answer, keyword) for keyword in keywords if keyword.strip())


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


def split_breakdown(results: list[EvaluationCaseResult]) -> dict[str, EvaluationSplitSummary]:
    """区分（dev / holdout）ごとの結果（#1284）。区分のあるケースが 1 つも無ければ空にする。

    指標は全体と同じく、成功したケースのうち測れたものだけの平均。区分の無いケースは unassigned。
    """
    if not any(result.split for result in results):
        return {}
    grouped: dict[str, list[EvaluationCaseResult]] = {}
    for result in results:
        grouped.setdefault(result.split or EVALUATION_UNASSIGNED_SPLIT, []).append(result)
    breakdown: dict[str, EvaluationSplitSummary] = {}
    for split, members in grouped.items():
        aggregate = _Aggregate()
        failure_reason_counts: dict[str, int] = {}
        for result in members:
            if result.status == "success":
                _accumulate_case_metrics(aggregate, result)
            _accumulate_failure_reasons(failure_reason_counts, result.failure_reasons)
        breakdown[split] = EvaluationSplitSummary(
            case_count=len(members),
            error_count=sum(1 for result in members if result.status != "success"),
            metrics=aggregate.means(),
            metric_case_counts=aggregate.counts(),
            failure_reason_counts=failure_reason_counts,
            reasoning_type_breakdown=reasoning_type_breakdown(members),
            hops_breakdown=hops_breakdown(members),
        )
    return breakdown


def reasoning_type_breakdown(
    results: Sequence[EvaluationCaseResult],
) -> dict[str, EvaluationReasoningSummary]:
    """多段の質問の種類ごとの結果（#1335）。種類のあるケースが 1 つも無ければ空にする。

    種類の無いケースは unspecified。
    """
    if not any(result.reasoning_type for result in results):
        return {}
    return _reasoning_breakdown(
        results, lambda result: result.reasoning_type or EVALUATION_UNSPECIFIED_REASONING
    )


def hops_breakdown(
    results: Sequence[EvaluationCaseResult],
) -> dict[str, EvaluationReasoningSummary]:
    """段の数ごとの結果（#1335。key は "1"・"2" などで、段の数の順）。

    段の数のあるケースが 1 つも無ければ空にする。段の数の無いケースは unspecified（最後）。
    """
    if not any(result.hops is not None for result in results):
        return {}
    ordered = sorted(results, key=lambda result: (result.hops is None, result.hops or 0))
    return _reasoning_breakdown(
        ordered,
        lambda result: (
            str(result.hops) if result.hops is not None else EVALUATION_UNSPECIFIED_REASONING
        ),
    )


def _reasoning_breakdown(
    results: Sequence[EvaluationCaseResult],
    key: Callable[[EvaluationCaseResult], str],
) -> dict[str, EvaluationReasoningSummary]:
    """ケースを key で分け、根拠と回答の指標を成功したケースのうち測れたものだけで求める。"""
    grouped: dict[str, list[EvaluationCaseResult]] = {}
    for result in results:
        grouped.setdefault(key(result), []).append(result)
    breakdown: dict[str, EvaluationReasoningSummary] = {}
    for name, members in grouped.items():
        aggregate = _Aggregate()
        for result in members:
            if result.status == "success":
                _accumulate_case_metrics(aggregate, result)
        means = aggregate.means()
        counts = aggregate.counts()
        breakdown[name] = EvaluationReasoningSummary(
            case_count=len(members),
            error_count=sum(1 for result in members if result.status != "success"),
            metrics={metric: means[metric] for metric in EVALUATION_REASONING_METRIC_NAMES},
            metric_case_counts={
                metric: counts[metric] for metric in EVALUATION_REASONING_METRIC_NAMES
            },
        )
    return breakdown
