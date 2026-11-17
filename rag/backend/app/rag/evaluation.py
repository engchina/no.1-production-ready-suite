"""RAG の評価。検索二・回答一の指標を独立して集計する。

必要な根拠は本文・位置、MRR は重複を除いた引用文書の順位、回答は標準回答に照らした意味上の
正確性を測る（回答・確認・保留の期待も標準回答の文で比べる）。前提不足は None とし平均に入れない。
会話の履歴・条件・区分・種類別の内訳を保ち、実行エラーと評価の未完了を合格にしない。"""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import partial
from time import perf_counter
from typing import Any, Protocol

from pr_backend_core.bounded_calls import (
    Attempt,
    BoundedCallAborted,
    BoundedCaller,
    BoundedCallFailed,
)
from pr_evaluation_scoring import (
    MetricAggregate,
    ThresholdFailure,
    average_precision,
    bleu,
    count_reasons,
    evidence_matches,
    ndcg_at_k,
    overall_passed,
    precision_at_k,
    reciprocal_rank,
    rouge_l,
    rouge_n,
    skips_retrieval,
    threshold_failures,
    unique_in_order,
)
from pydantic import BaseModel, ValidationError
from rag_engine.evaluation.answer_eval import scored_answer_text
from rag_engine.retrieval.text_search_tokenizer import surface_tokens

from app.clients.ai_calls import make_ai_caller
from app.clients.oracle import OracleClient
from app.config import (
    OCI_ENTERPRISE_AI_TIMEOUT_MAX_SECONDS,
    Settings,
    enterprise_ai_default_model_id,
    enterprise_ai_judge_model_id,
    get_settings,
)
from app.rag.answer_engine import answer_model_of, evaluate_answer_record
from app.rag.answer_failure import describe_answer_failure
from app.rag.answer_metrics import is_abstained_answer
from app.rag.answer_threads import run_answer_in_thread
from app.rag.answer_timeout import (
    AnswerTimeoutError,
    answer_stage_label,
    answer_timeout_seconds,
    format_timeout_limit,
    run_answer_with_timeout,
)
from app.rag.audit import record_rag_search_audit
from app.rag.diagnostics import build_search_diagnostics
from app.rag.evaluation_handling import observed_outcome
from app.rag.observability import (
    SEARCH_METRIC_MODE,
    elapsed_ms,
    new_trace_id,
    record_evaluation_case,
    record_rag_request,
)
from app.rag.pipeline import (
    BASIC_FLOW,
    EXPANDED_FLOW,
    AnswerFlow,
    ChatTurn,
    RagPipeline,
    SearchStageProgressCallback,
)
from app.schemas.evaluation import (
    ANSWER_GENERATION_FALLBACK,
    DEFAULT_EVALUATION_ANSWER_MODE,
    EVALUATION_METRIC_NAMES,
    EVALUATION_METRICS_VERSION,
    EVALUATION_REASONING_METRIC_NAMES,
    EVALUATION_REPORT_ONLY_METRIC_NAMES,
    EVALUATION_UNASSIGNED_SPLIT,
    EVALUATION_UNCATEGORIZED,
    EVALUATION_UNSPECIFIED_REASONING,
    EvaluationAnswerJudgement,
    EvaluationAnswerMode,
    EvaluationCase,
    EvaluationCaseResult,
    EvaluationCategorySummary,
    EvaluationCoveredPoint,
    EvaluationEvidence,
    EvaluationJudgedClaim,
    EvaluationJudgeDiagnostics,
    EvaluationMetrics,
    EvaluationRagOverrides,
    EvaluationReasoningSummary,
    EvaluationSplitSummary,
    EvaluationThresholdFailure,
    EvaluationThresholds,
    EvaluationTurnResult,
)
from app.schemas.search import (
    INCLUDE_SUPERSEDED_FILTER_KEY,
    RetrievedChunk,
    SearchRequest,
    SearchResponse,
)

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
    """標準回答で回答を評価する(正確性の判定の LLM を 1 回呼ぶ)。

    戻り値は `evaluate_answer_record` の結果(status / passed / metrics / answer_correctness /
    correctness_reason など。#680)。時間切れは TimeoutError を送出する。
    ``judge_model_id`` は判定に使うモデル(#3574。runner が回答を作ったモデルから解決した ID)。
    """

    async def __call__(
        self,
        *,
        trace_id: str,
        evaluation_input: Mapping[str, object],
        standard_answer: str,
        timeout_seconds: float,
        judge_model_id: str,
    ) -> Mapping[str, object]:
        """評価結果を返す。"""


class EvaluationProgressCallback(Protocol):
    """評価の進捗の通知先（評価 job の進捗の保存に使う。#390）。

    ケースを始める前に、終わったケースの数と今のケースの id を渡す。最後のケースの後は
    `current_case_id=None` で呼ぶ。
    """

    async def __call__(
        self,
        *,
        completed_cases: int,
        current_case_id: str | None,
    ) -> None:
        """進捗を受け取る。"""


class EvaluationCaseResultCallback(Protocol):
    """1 ケースの結果の通知先（評価 job のケースごとの保存に使う。#4208）。

    `case_index` は評価のケースの位置（並行で実行すると、終わる順はケースの順と違う）。
    """

    async def __call__(self, *, case_index: int, result: EvaluationCaseResult) -> None: ...


async def judge_answer_with_standard(
    *,
    trace_id: str,
    evaluation_input: Mapping[str, object],
    standard_answer: str,
    timeout_seconds: float,
    settings: Settings | None = None,
    judge_model_id: str | None = None,
) -> Mapping[str, object]:
    """回答を標準回答で評価し、結果を回答の記録にも保存する(保存は best-effort)。

    ``judge_model_id`` は判定のモデル(#3574。省略時は `evaluate_answer_record` が解決する)。

    時間切れのときは結果を捨てる(#304 と同じ)。評価の thread には同じ期限を渡し、時間切れの後は
    LLM を呼ばない(#1422)。
    """
    resolved = settings or get_settings()
    limit = max(0.001, timeout_seconds)
    evaluation = await asyncio.wait_for(
        run_answer_in_thread(
            evaluate_answer_record,
            evaluation_input,
            standard_answer,
            resolved,
            judge_model_id=judge_model_id,
            timeout_seconds=limit,
        ),
        timeout=limit,
    )
    saved = {
        **evaluation,
        "standard_answer": standard_answer,
        "evaluated_at": datetime.now(UTC).isoformat(),
    }
    try:
        # 回答の記録の画面から、正確性の値と判定の理由を確かめられるようにする。
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


def _aggregate() -> MetricAggregate:
    """指標ごとに、測れたケースの値を集める（platform の採点の部品。#3240）。"""
    return MetricAggregate(names=_AGGREGATED_METRIC_NAMES)


@dataclass(frozen=True)
class _TurnRun:
    """1 回の回答と、その質問・返答までに渡した条件（#1284）。"""

    response: SearchResponse
    conditions: Mapping[str, str] = field(default_factory=dict)
    # 送った質問（RAG 検索の方法で往復をつないだ 1 問。#5340）。None はケースの質問・返答のまま。
    question: str | None = None


# (検索・回答の request, Settings, 業務ガイドの照合に足す前の質問) → 解決した request と Settings。
ProfileResolver = Callable[
    [SearchRequest, Settings, Sequence[str]], Awaitable[tuple[SearchRequest, Settings]]
]


async def _resolve_profile_context(
    request: SearchRequest,
    settings: Settings,
    guide_context: Sequence[str] = (),
    *,
    flow: AnswerFlow = EXPANDED_FLOW,
) -> tuple[SearchRequest, Settings]:
    """検索・回答と同じ解決で、プロファイルの KB・回答の設定・業務ガイドを当てる（#1249）。

    ``flow`` は回答の方法の経路（#5340）。返答の往復（#1284）では、チャットと同じく前の質問も業務
    ガイドの照合に使う。確認の質問は回答として測るため、検索の API と同じく ``interactive`` に
    しない（不明の条件は確認の質問になる）。
    """
    from app.api.routes.search import _resolve_query_context

    resolved, resolved_settings, _kb, _view = await _resolve_query_context(
        request, settings, guide_context=guide_context, flow=flow
    )
    return resolved, resolved_settings


@dataclass(frozen=True)
class EvaluationAnswerRoute:
    """品質評価の回答の方法（#5340）の経路。入口の解決と pipeline に同じ ``flow`` を渡す。

    ``conversational`` は往復のケース（#1284）を会話として順に送るか。RAG 検索（基本の RAG）は
    会話の履歴・確認の往復を持たないので、最初の質問と返答をつないで 1 問にする。
    """

    mode: EvaluationAnswerMode
    flow: AnswerFlow
    conversational: bool

    async def resolve(
        self, request: SearchRequest, settings: Settings, guide_context: Sequence[str]
    ) -> tuple[SearchRequest, Settings]:
        """検索・回答プロファイルの解決（KB・回答の設定・業務ガイド）。"""
        return await _resolve_profile_context(request, settings, guide_context, flow=self.flow)

    def pipeline(self, settings: Settings) -> RagPipeline:
        """この経路の検索・回答の pipeline。"""
        return RagPipeline(settings=settings, flow=self.flow)


# 回答の方法 → 経路。chat は RAG チャットと同じ経路（業務ガイドの照合・承認済み FAQ・
# 根拠の拡張あり）、search は画面の RAG 検索と同じ基本の RAG（#5332）。
EVALUATION_ANSWER_ROUTES: Mapping[EvaluationAnswerMode, EvaluationAnswerRoute] = {
    "chat": EvaluationAnswerRoute(mode="chat", flow=EXPANDED_FLOW, conversational=True),
    "search": EvaluationAnswerRoute(mode="search", flow=BASIC_FLOW, conversational=False),
}


def evaluation_answer_route(mode: EvaluationAnswerMode) -> EvaluationAnswerRoute:
    """回答の方法の経路（評価の入口が 1 か所で選ぶ）。"""
    return EVALUATION_ANSWER_ROUTES[mode]


def _case_queries(
    case: EvaluationCase, *, conversational: bool = True
) -> list[tuple[str, dict[str, str]]]:
    """最初の質問と返答を、送る順に（質問・返答, その発話で分かった条件）で返す（#1284）。

    ``conversational`` でなければ（RAG 検索。#5340）、最初の質問と返答を改行でつないだ 1 問と、
    すべての発話で分かった条件にする。
    """
    queries = [
        (case.query, dict(case.conditions)),
        *((turn.reply, dict(turn.conditions)) for turn in case.turns),
    ]
    if conversational or len(queries) == 1:
        return queries
    conditions: dict[str, str] = {}
    for _query, turn_conditions in queries:
        conditions.update(turn_conditions)
    return [("\n".join(query for query, _ in queries), conditions)]


def _case_filters(case: EvaluationCase, filters: Mapping[str, str] | None) -> dict[str, str]:
    """ケースの検索条件。旧版も検索するケース（#1366）は include_superseded=true を足す。"""
    case_filters = dict(filters or {})
    if case.include_superseded:
        case_filters[INCLUDE_SUPERSEDED_FILTER_KEY] = "true"
    return case_filters


# 文書 ID の列から、解析後の本文が同じ別の文書の ID を引く（#5325。文書 ID → 別の文書の ID の列）。
SameTextDocuments = Callable[[Sequence[str]], Awaitable[Mapping[str, Sequence[str]]]]


async def _oracle_same_text_documents(document_ids: Sequence[str]) -> Mapping[str, Sequence[str]]:
    return await OracleClient().same_extracted_text_document_ids(document_ids)


def _expected_document_ids(case: EvaluationCase) -> list[str]:
    """ケースが期待する文書（正解の文書と、必要な根拠の文書）。"""
    return unique_in_order(
        [
            *case.relevant_document_ids,
            *(item.document_id for item in case.required_evidence if item.document_id),
        ]
    )


def with_same_text_equivalents(
    case: EvaluationCase, same_text: Mapping[str, Sequence[str]]
) -> EvaluationCase:
    """解析後の本文が同じ文書（#5325）を、期待する文書と同じ文書とみなす文書に足す。

    今の同じ文書とみなす文書（同じファイル名。#2086）に加える。`same_text` は文書 ID → 解析後の
    本文の hash が同じ別の文書の ID で、hash の無い文書は持たない（今の判定のまま）。期待する文書
    同士は互いにそろえない（それぞれの根拠をその文書で照合する）。
    """
    expected = _expected_document_ids(case)
    taken = set(expected)
    merged = {key: list(values) for key, values in case.equivalent_document_ids.items()}
    changed = False
    for canonical in expected:
        others = merged.setdefault(canonical, [])
        for source in [canonical, *others]:
            for other in same_text.get(source, ()):
                if other not in taken and other not in others:
                    others.append(other)
                    changed = True
    if not changed:
        return case
    return case.model_copy(
        update={"equivalent_document_ids": {key: ids for key, ids in merged.items() if ids}}
    )


class EvaluationRunner:
    """小規模な golden set を使って検索・根拠・回答の品質を評価する。"""

    def __init__(
        self,
        pipeline: SearchPipeline | None = None,
        settings: Settings | None = None,
        answer_judge: AnswerJudge | None = None,
        profile_resolver: ProfileResolver | None = None,
        same_text_documents: SameTextDocuments | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._pipeline = pipeline
        # 省略時は回答の方法の経路の解決（#5340）。テストでは注入した解決だけを使う。
        self._profile_resolver = profile_resolver
        # 解析後の本文が同じ文書の引き方（#5325）。既定の pipeline(実環境)では DB から引く。
        # テストで pipeline を注入したときは、明示したものだけを使う。
        self._same_text_documents = same_text_documents
        if same_text_documents is None and pipeline is None:
            self._same_text_documents = _oracle_same_text_documents
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
        on_case_result: EvaluationCaseResultCallback | None = None,
        answer_model_id: str | None = None,
        answer_mode: EvaluationAnswerMode = DEFAULT_EVALUATION_ANSWER_MODE,
    ) -> EvaluationMetrics:
        """評価ケースを実行し、集計指標を返す。

        1 ケースは回答生成の上限（`rag_answer_timeout_seconds`）で打ち切り、時間切れになった工程を
        ケースの結果に残して次のケースへ進む（#383）。`time_budget_seconds` を渡すと、評価全体を
        その秒数で打ち切る（同期の HTTP の待ちを超えないため）。上限に達したら実行中のケースを
        打ち切り、残りのケースは実行せずに失敗として記録する。`progress` には、ケースを始める
        前と最後のケースの後に進捗を渡す（評価 job。#390）。`on_case_result` には、ケースが終わる
        たびに（失敗のケースも）結果を渡す（途中で止まっても終わったケースを残すため。#4208）。

        `search_answer_profile_id` を渡すと、ケースごとに検索・回答と同じ解決（参照 KB・回答の
        設定・用語・同義語・業務ガイド）をしてから回答する（#1249）。渡さなければ全体の既定（#301）。
        `answer_mode` は回答の方法（#5340。chat は RAG チャットと同じ経路、search は基本の RAG）。
        """
        return await self._run(
            cases,
            top_k,
            filters,
            knowledge_base_ids,
            thresholds,
            rag_overrides,
            search_answer_profile_id=search_answer_profile_id,
            time_budget_seconds=time_budget_seconds,
            progress=progress,
            on_case_result=on_case_result,
            answer_model_id=answer_model_id,
            route=evaluation_answer_route(answer_mode),
        )

    async def _run(
        self,
        cases: list[EvaluationCase],
        top_k: int,
        filters: dict[str, str] | None,
        knowledge_base_ids: Sequence[str] | None,
        thresholds: EvaluationThresholds | None,
        rag_overrides: EvaluationRagOverrides | None,
        *,
        search_answer_profile_id: str | None,
        time_budget_seconds: float | None,
        progress: EvaluationProgressCallback | None,
        on_case_result: EvaluationCaseResultCallback | None,
        answer_model_id: str | None,
        route: EvaluationAnswerRoute,
    ) -> EvaluationMetrics:
        deadline = _deadline(time_budget_seconds)
        cases = await self._with_same_text_equivalents(cases)
        effective_settings = evaluation_settings(self._settings, rag_overrides)
        pipeline = self._pipeline or route.pipeline(effective_settings)

        concurrency = evaluation_concurrency(self._settings)
        caller = make_ai_caller(self._settings, "evaluation", max_concurrency=concurrency)
        results: list[EvaluationCaseResult | None] = [None] * len(cases)
        pending = iter(enumerate(cases))
        finished = 0

        async def worker() -> None:
            nonlocal finished
            for case_index, case in pending:
                if progress is not None:
                    try:
                        await progress(
                            completed_cases=finished,
                            current_case_id=case.id,
                        )
                    except asyncio.CancelledError:
                        # 取り消し・中断（job の行が RUNNING でなくなった）。ほかのケースも止める。
                        raise _EvaluationStopped from None
                case_result = await self._run_case(
                    case,
                    caller=caller,
                    pipeline=pipeline,
                    effective_settings=effective_settings,
                    deadline=deadline,
                    top_k=top_k,
                    filters=filters,
                    knowledge_base_ids=knowledge_base_ids,
                    search_answer_profile_id=search_answer_profile_id,
                    answer_model_id=answer_model_id,
                    route=route,
                )
                results[case_index] = case_result
                if on_case_result is not None:
                    await on_case_result(case_index=case_index, result=case_result)
                finished += 1

        # ケースは上限つきの並行（`RAG_EVALUATION_CONCURRENCY`。#2004）で、評価ケースの順に
        # 始める。結果はケースの位置に書くため、並行でも順番は変わらない。1 ケースの中の往復と
        # 判定は順に行う（前の往復が次の質問の履歴になる）。
        workers = max(1, min(concurrency, len(cases)))
        try:
            async with asyncio.TaskGroup() as group:
                for _ in range(workers):
                    group.create_task(worker())
        except BaseExceptionGroup as group_error:
            if group_error.subgroup(_EvaluationStopped) is not None:
                raise asyncio.CancelledError from None
            raise _first_error(group_error) from None

        if progress is not None:
            await progress(
                completed_cases=len(cases),
                current_case_id=None,
            )
        case_results = [result for result in results if result is not None]
        return summarize_case_results(case_results, thresholds=thresholds)

    async def _with_same_text_equivalents(
        self, cases: list[EvaluationCase]
    ) -> list[EvaluationCase]:
        """解析後の本文が同じ文書を、各ケースの同じ文書とみなす文書に足す（#5325）。"""
        if self._same_text_documents is None:
            return cases
        document_ids = unique_in_order(
            [
                document_id
                for case in cases
                for canonical in _expected_document_ids(case)
                for document_id in [canonical, *case.equivalent_document_ids.get(canonical, [])]
            ]
        )
        if not document_ids:
            return cases
        same_text = await self._same_text_documents(document_ids)
        if not same_text:
            return cases
        return [with_same_text_equivalents(case, same_text) for case in cases]

    async def _run_case(
        self,
        case: EvaluationCase,
        *,
        caller: BoundedCaller,
        pipeline: Any,
        effective_settings: Settings,
        deadline: float | None,
        top_k: int,
        filters: dict[str, str] | None,
        knowledge_base_ids: Sequence[str] | None,
        search_answer_profile_id: str | None,
        answer_model_id: str | None = None,
        route: EvaluationAnswerRoute = EVALUATION_ANSWER_ROUTES[DEFAULT_EVALUATION_ANSWER_MODE],
    ) -> EvaluationCaseResult:
        """1 ケース（最初の質問と確認の質問への返答の往復、判定）を実行して結果を返す。

        回答は ``route``（回答の方法の経路。#5340）で作る。プロファイルの解決と pipeline に同じ
        経路を渡し、RAG 検索の経路は往復を 1 問にまとめて送る。

        回答が流量の制限（429）で失敗したら、共通の実行器が全体で待って送り直す（#2004）。
        ほかの失敗はケースの失敗にする（今までと同じ）。権限・設定の誤り・割り当ての使い切りは
        ``BoundedCallAborted`` で評価の全体を止める。
        """
        trace_id = new_trace_id()
        remaining = _remaining_seconds(deadline)
        if remaining is not None and remaining <= 0:
            # 評価全体の上限に達した。残りのケースは pipeline を呼ばずに失敗として記録する。
            return _case_skipped_result(case=case, trace_id=trace_id)
        case_started_at = perf_counter()
        # 最初の質問と、確認の質問への返答（#1284）を順に送る。返答はチャットと同じく、
        # 前の往復を会話の履歴と業務ガイドの照合の文脈にし、分かった条件を足して渡す。
        turn_runs: list[_TurnRun] = []
        history: list[ChatTurn] = []
        prior_queries: list[str] = []
        conditions: dict[str, str] = {}
        error_result: EvaluationCaseResult | None = None
        # 回答に使った設定（プロファイルの pass はプロファイルの回答のモデルを当てた設定。#3574）。
        answer_settings = effective_settings
        combined = not route.conversational and bool(case.turns)
        resolve_profile = self._profile_resolver or route.resolve
        for turn_index, (query, turn_conditions) in enumerate(
            _case_queries(case, conversational=route.conversational)
        ):
            conditions = {**conditions, **turn_conditions}
            request = SearchRequest(
                query=query,
                model_id=answer_model_id,
                top_k=top_k,
                filters=_case_filters(case, filters),
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
                case_pipeline: Any = pipeline
                if search_answer_profile_id:
                    # プロファイルの解決（KB・回答の設定・業務ガイド）。
                    # 失敗はケースの失敗にする。
                    request, case_settings = await resolve_profile(
                        request.model_copy(
                            update={"search_answer_profile_id": search_answer_profile_id}
                        ),
                        effective_settings,
                        prior_queries[-EVALUATION_GUIDE_CONTEXT_TURNS:],
                    )
                    case_pipeline = self._pipeline or route.pipeline(case_settings)
                    answer_settings = case_settings
                attempt_trace_ids = [turn_trace_id]

                async def answer_once(
                    attempt: Attempt,
                    *,
                    case_pipeline: Any = case_pipeline,
                    request: SearchRequest = request,
                    trace_ids: list[str] = attempt_trace_ids,
                    turn_history: tuple[ChatTurn, ...] = tuple(history),
                    limited: bool = limited_by_budget,
                ) -> SearchResponse:
                    # 流量の制限の後の送り直しは、新しい trace id で記録する（#2004）。
                    if attempt.number > 1:
                        trace_ids.append(new_trace_id())
                    run_case: Any = partial(case_pipeline.run, request, trace_ids[-1])
                    if turn_history:
                        # 返答はチャットと同じく、会話の履歴を踏まえて単独の質問に
                        # 書き換えてから答える。
                        run_case = partial(run_case, history=list(turn_history))
                    # 工程を記録する tracker は、3 番目の引数（progress_callback）として渡る。
                    return await run_answer_with_timeout(
                        run_case,
                        effective_settings,
                        timeout_seconds=_remaining_seconds(deadline) if limited else None,
                    )

                try:
                    response = await _call_unwrapped(caller, answer_once, label=case.id)
                finally:
                    turn_trace_id = attempt_trace_ids[-1]
            except BoundedCallAborted:
                raise
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
                # LLM の応答が送り直しても未完了なら、理由と直し方の文と工程を残す（#2023）。
                failure = describe_answer_failure(exc, EVALUATION_CASE_ERROR_MESSAGE)
                error_result = _case_error_result(
                    case=case,
                    trace_id=turn_trace_id,
                    elapsed=elapsed,
                    error=exc,
                    error_stage=failure.stage,
                    error_message=failure.message,
                    error_type=failure.error_type,
                )
                break
            turn_runs.append(
                _TurnRun(
                    response=response,
                    conditions=dict(conditions),
                    question=query if combined else None,
                )
            )
            history.extend(
                [
                    ChatTurn(role="USER", content=query),
                    ChatTurn(role="ASSISTANT", content=response.answer),
                ]
            )
            prior_queries.append(query)

        if error_result is not None:
            return error_result

        record_evaluation_case(
            SEARCH_METRIC_MODE,
            "success",
            elapsed_ms(case_started_at) / 1000,
        )
        final_response = turn_runs[-1].response
        judgement = await self._judge(
            case,
            final_response,
            deadline,
            caller,
            judge_model_id=_judge_model_id(self._settings, final_response, answer_settings),
            turn_runs=turn_runs,
        )
        return _case_result(case, final_response, judgement, turn_runs=turn_runs)

    async def _judge(
        self,
        case: EvaluationCase,
        response: SearchResponse,
        deadline: float | None,
        caller: BoundedCaller | None = None,
        *,
        judge_model_id: str,
        turn_runs: Sequence[_TurnRun] | None = None,
    ) -> EvaluationAnswerJudgement | None:
        """標準回答のあるケースの回答を、主張ごとの正確性と要点の網羅で評価する（#5262）。

        正確性は標準回答だけと比べる。必須の回答要点と確かめる条件は標準回答に結合せず、網羅の要点
        （``answer_points``）として渡す（無ければ判定が標準回答の文を要点にする）。標準回答の無いケースは
        判定を呼ばず、正確性・網羅は未評価にする。

        ``judge_model_id`` は判定のモデル(#3574)。判定を完了できなかったケースにも残す。
        """
        standard_answer = (case.standard_answer or "").strip()
        answer_points = [*case.required_answer_points, *case.required_conditions]
        evaluation_input = response.evaluation_input or {
            "question": case.query,
            "answer_text": response.answer,
        }
        runs = list(turn_runs or [_TurnRun(response=response, conditions={})])
        actual_responses = [
            {
                "question": run.question
                if run.question is not None
                else case.query
                if index == 0
                else case.turns[index - 1].reply,
                "answer_text": run.response.answer,
            }
            for index, run in enumerate(runs)
        ]
        if not standard_answer:
            return None
        evaluation_input = {
            **evaluation_input,
            "actual_responses": actual_responses,
            "answer_points": answer_points,
        }
        if self._answer_judge is None:
            return None
        remaining = _remaining_seconds(deadline)
        timeout = ANSWER_JUDGE_TIMEOUT_SECONDS if remaining is None else remaining
        timeout = min(timeout, ANSWER_JUDGE_TIMEOUT_SECONDS)
        if timeout <= 0:
            return EvaluationAnswerJudgement(
                status="timeout",
                message=ANSWER_JUDGE_TIMEOUT_MESSAGE,
                judge_model_id=judge_model_id,
            )
        judge = self._answer_judge

        async def judge_once(_attempt: Attempt) -> Mapping[str, object]:
            return await judge(
                trace_id=response.trace_id,
                evaluation_input=evaluation_input,
                standard_answer=standard_answer,
                timeout_seconds=timeout,
                judge_model_id=judge_model_id,
            )

        try:
            # 流量の制限（429）は、ケースの回答と同じ実行器で全体で待って送り直す（#2004）。
            evaluation = await _call_unwrapped(
                caller or make_ai_caller(self._settings, "evaluation"), judge_once, label=case.id
            )
        except BoundedCallAborted:
            raise
        except TimeoutError:
            return EvaluationAnswerJudgement(
                status="timeout",
                message=ANSWER_JUDGE_TIMEOUT_MESSAGE,
                judge_model_id=judge_model_id,
            )
        except Exception:  # noqa: BLE001 - 評価の失敗はケースの結果に残して続ける。
            return EvaluationAnswerJudgement(
                status="error",
                message=ANSWER_JUDGE_ERROR_MESSAGE,
                judge_model_id=judge_model_id,
            )
        return summarize_answer_judgement(evaluation).model_copy(
            update={
                "judge_model_id": judge_model_id,
                **({"answer_correctness": None} if not standard_answer else {}),
            }
        )


def _judge_model_id(settings: Settings, response: SearchResponse, answer_settings: Settings) -> str:
    """ケースの回答を判定するモデル(#3574)。

    回答を作ったモデルは回答の診断の ``models.llm.model_id``、無ければ回答に使った設定の既定の
    テキストモデル(プロファイルの pass はプロファイルの回答のモデル)。共通のモデル設定の既定の
    判定モデルで、判定のモデルに解決する(既定は既定のテキストモデル)。
    """
    answer_model = answer_model_of(response.diagnostics.answer) or enterprise_ai_default_model_id(
        answer_settings
    )
    return enterprise_ai_judge_model_id(settings, answer_model)


class _EvaluationStopped(Exception):
    """進捗の保存で、評価の取り消し・中断が分かった（並行のケースをすべて止める）。"""


def evaluation_concurrency(settings: Settings) -> int:
    """同時に実行するケースの数の上限（``RAG_EVALUATION_CONCURRENCY``。#2004）。"""
    return max(1, int(getattr(settings, "rag_evaluation_concurrency", 1) or 1))


async def _call_unwrapped[T](
    caller: BoundedCaller, fn: Callable[[Attempt], Awaitable[T]], *, label: str
) -> T:
    """実行器で呼ぶ。件の失敗は元の例外で出す（ケースの失敗の記録・監査を変えない）。"""
    try:
        return await caller.call(fn, label=label)
    except BoundedCallFailed as exc:
        cause = exc.__cause__
        if isinstance(cause, Exception):
            raise cause  # noqa: B904 - 元の例外の原因の連鎖を変えずに出し直す
        raise


def _first_error(group: BaseExceptionGroup) -> BaseException:
    """並行のケースの失敗のうち、評価を止めた失敗（中断を優先し、無ければ最初の失敗）。"""
    leaves: list[BaseException] = []
    stack: list[BaseException] = [group]
    while stack:
        current = stack.pop(0)
        if isinstance(current, BaseExceptionGroup):
            stack[:0] = list(current.exceptions)
        else:
            leaves.append(current)
    aborted = [error for error in leaves if isinstance(error, BoundedCallAborted)]
    return aborted[0] if aborted else leaves[0]


def _settings_judge(settings: Settings) -> AnswerJudge:
    """Settings を固定した既定の judge。"""

    async def judge(
        *,
        trace_id: str,
        evaluation_input: Mapping[str, object],
        standard_answer: str,
        timeout_seconds: float,
        judge_model_id: str,
    ) -> Mapping[str, object]:
        return await judge_answer_with_standard(
            trace_id=trace_id,
            evaluation_input=evaluation_input,
            standard_answer=standard_answer,
            timeout_seconds=timeout_seconds,
            settings=settings,
            judge_model_id=judge_model_id,
        )

    return judge


def _judge_diagnostics(evaluation: Mapping[str, object]) -> EvaluationJudgeDiagnostics | None:
    """判定の診断（呼び出しと再試行の回数）。記録が無い・形が違えば None。"""
    value = evaluation.get("judge_diagnostics")
    if not isinstance(value, Mapping):
        return None
    try:
        return EvaluationJudgeDiagnostics.model_validate(value)
    except ValidationError:
        return None


def summarize_answer_judgement(evaluation: Mapping[str, object]) -> EvaluationAnswerJudgement:
    """標準回答による評価の結果を、指標に使う要約へ変換する。"""
    status = str(evaluation.get("status") or "error")
    message = evaluation.get("message")
    diagnostics = _judge_diagnostics(evaluation)
    rubric = evaluation.get("rubric_version")
    rubric_version = rubric if isinstance(rubric, int) and not isinstance(rubric, bool) else None
    if status != "completed":
        return EvaluationAnswerJudgement(
            status=status,
            message=str(message) if message else ANSWER_JUDGE_ERROR_MESSAGE,
            judge_diagnostics=diagnostics,
            rubric_version=rubric_version,
        )
    passed = evaluation.get("passed")
    return EvaluationAnswerJudgement(
        status="completed",
        passed=passed if isinstance(passed, bool) else None,
        answer_correctness=_optional_float(evaluation.get("answer_correctness")),
        answer_coverage=_optional_float(evaluation.get("answer_coverage")),
        reason=str(evaluation["correctness_reason"])
        if evaluation.get("correctness_reason")
        else None,
        message=str(message) if message else None,
        judge_diagnostics=diagnostics,
        rubric_version=rubric_version,
        claims=_judged_rows(evaluation.get("correctness_claims"), EvaluationJudgedClaim),
        points=_judged_rows(evaluation.get("coverage_points"), EvaluationCoveredPoint),
    )


def _judged_rows[RowT: BaseModel](value: object, model: type[RowT]) -> list[RowT]:
    """判定の記録の主張・要点の一覧。形の違う行は読み捨てる。"""
    if not isinstance(value, list):
        return []
    rows: list[RowT] = []
    for item in value:
        try:
            rows.append(model.model_validate(item))
        except ValidationError:
            continue
    return rows


def evaluation_settings(
    settings: Settings,
    overrides: EvaluationRagOverrides | None,
) -> Settings:
    """評価に使う設定。評価の上書き（rag_overrides）を一時適用する。

    評価は検索・回答プロファイルを受け取らず、全体の既定で動く(#301)。回答は根拠付き回答(回答の記録を
    残す回答フロー)だけなので(#594)、回答エンジンの指定は要らない(#591 では固定していた)。
    """
    update: dict[str, object] = {}
    if overrides is not None:
        mapping = {
            "neighbor_child_count": "rag_neighbor_child_count",
            "rerank_enabled": "rag_rerank_enabled",
            "rrf_k": "rag_rrf_k",
            "context_group_max_chunks": "rag_context_group_max_chunks",
            "reference_expansion_enabled": "rag_reference_expansion_enabled",
            "entity_expansion_enabled": "rag_entity_expansion_enabled",
            "entity_expansion_max_chunks": "rag_entity_expansion_max_chunks",
            "graph_expansion_enabled": "rag_graph_expansion_enabled",
            "graph_expansion_max_chunks": "rag_graph_expansion_max_chunks",
            "section_expansion_enabled": "rag_section_expansion_enabled",
            "section_expansion_max_levels": "rag_section_expansion_max_levels",
            "section_expansion_input_token_percent": (  # nosec B105 - 設定の名前
                "rag_section_expansion_input_token_percent"
            ),
            "max_anchors_per_document": "rag_max_anchors_per_document",
            "oracle_vector_target_accuracy": "oracle_vector_target_accuracy",
            "generation_reasoning_effort": "rag_generation_reasoning_effort",
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
    """ケースの結果を集計して評価の結果にする（評価ランナーと `score_case_answers` で共通）。

    指標は成功したケースのうち測れたものだけの平均。失敗したケースや、標準回答で評価できなかった
    ケース、回答文を生成できず参考情報へ退避したケースがあれば合格にしない。
    """
    results = list(case_results)
    aggregate = _aggregate()
    failure_reason_counts: dict[str, int] = {}
    error_count = 0
    judge_incomplete_count = 0
    generation_fallback_count = 0
    for result in results:
        count_reasons(failure_reason_counts, result.failure_reasons)
        if result.status != "success":
            error_count += 1
            continue
        judgement = result.answer_evaluation
        if judgement is not None and judgement.status != "completed":
            judge_incomplete_count += 1
        if ANSWER_GENERATION_FALLBACK in result.failure_reasons:
            generation_fallback_count += 1
        _accumulate_case_metrics(aggregate, result)
    aggregate_values = aggregate.means()
    failures = _threshold_failures(thresholds, aggregate_values)
    return EvaluationMetrics(
        metrics_version=EVALUATION_METRICS_VERSION,
        case_count=len(results),
        error_count=error_count,
        category_breakdown=category_breakdown(results),
        split_breakdown=split_breakdown(results),
        reasoning_type_breakdown=reasoning_type_breakdown(results),
        hops_breakdown=hops_breakdown(results),
        # 回答文を生成できず参考情報へ退避したケース（この問は不合格。#5320）があれば、
        # 全体も合格にしない。
        passed=overall_passed(
            failures=failures,
            error_count=error_count,
            incomplete_count=judge_incomplete_count + generation_fallback_count,
            measured_counts=aggregate.counts(),
            thresholds=_threshold_values(thresholds),
            values=aggregate_values,
        ),
        applied_thresholds=_threshold_values(thresholds),
        threshold_failures=[
            EvaluationThresholdFailure(
                metric=item.metric, actual=item.actual, threshold=item.threshold
            )
            for item in failures
        ],
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
    """得た RAG の回答を、評価ランナーと同じ採点にかける（ケースの採点の入口）。

    `answers` は最初の質問から順の（回答, その発話までに渡した既知の条件）。採点（回答の正確性・
    正解の文書・必要な根拠）は `EvaluationRunner.run` のケースと同じ関数で行う。
    製品横断の評価（業務 Agent の Run の採点）は suite（`suite/evaluation`。#3240）が持ち、ここは
    RAG の回答（`SearchResponse`）だけを扱う。
    """
    if not answers:
        raise ValueError("採点する回答がありません。")
    runs = [
        _TurnRun(response=response, conditions=dict(conditions)) for response, conditions in answers
    ]
    return _case_result(case, runs[-1].response, judgement, turn_runs=runs)


def _ranking_metrics(retrieved_ids: list[str], relevant: set[str]) -> dict[str, float]:
    """引用の文書列の順位の参考値（外れの文書も順位に残す）。"""
    return {
        "precision_at_10": precision_at_k(retrieved_ids, relevant, 10),
        "ndcg_at_3": ndcg_at_k(retrieved_ids, relevant, 3),
        "ndcg_at_10": ndcg_at_k(retrieved_ids, relevant, 10),
        "map": average_precision(retrieved_ids, relevant),
    }


def _overlap_metrics(case: EvaluationCase, response: SearchResponse) -> dict[str, float]:
    """回答と標準回答の語の重なりの参考値。標準回答か分割の結果が無ければ測らない。"""
    reference = surface_tokens(case.standard_answer or "")
    candidate = surface_tokens(scored_answer_text(response.answer))
    if not reference or not candidate:
        return {}
    return {
        "bleu_1": bleu(candidate, reference, order=1),
        "bleu_2": bleu(candidate, reference, order=2),
        "bleu_4": bleu(candidate, reference, order=4),
        "rouge_1": rouge_n(candidate, reference, 1),
        "rouge_2": rouge_n(candidate, reference, 2),
        "rouge_l": rouge_l(candidate, reference),
    }


def _case_result(
    case: EvaluationCase,
    response: SearchResponse,
    judgement: EvaluationAnswerJudgement | None,
    *,
    turn_runs: Sequence[_TurnRun] | None = None,
) -> EvaluationCaseResult:
    """1 ケースの回答から、測れる指標と失敗理由を求める。

    `response` は最後の回答。`turn_runs`（#1284）は最初の質問からの全部の回答で、省略したときは
    `response` だけの 1 往復とみなす。`screen_score`（#2402）は判定器も使った案内した画面の採点で、
    省略したときは決定的な判定だけで採点する（決められなければ未判定）。
    """
    runs = list(turn_runs) if turn_runs else [_TurnRun(response, dict(case.conditions))]
    # 同じ文書とみなす文書（同じ名前の文書・解析後の本文が同じ文書。#2086・#5325）は、正解の
    # 文書の ID にそろえて数える。そろえた引用の文書は結果に残す。
    retrieved_ids = unique_in_order(
        [case.canonical_document_id(chunk.document_id) for chunk in response.citations]
    )
    equated: dict[str, list[str]] = {}
    for chunk in response.citations:
        canonical = case.canonical_document_id(chunk.document_id)
        if canonical == chunk.document_id:
            continue
        cited = equated.setdefault(canonical, [])
        if chunk.document_id not in cited:
            cited.append(chunk.document_id)
    relevant = set(case.relevant_document_ids)
    # 答えるべきでない質問(#301)は拒答の正しさだけを測る。正解の文書が無いケースは検索の
    # 指標の対象外(#591)。
    abstained = is_abstained(response)
    hits: list[str] = []
    rank: float | None = None
    reference_metrics: dict[str, float] = _overlap_metrics(case, response)
    if relevant and not _skips_retrieval(response):
        hits = [doc_id for doc_id in retrieved_ids if doc_id in relevant]
        rank = reciprocal_rank(retrieved_ids, relevant)
        reference_metrics |= _ranking_metrics(retrieved_ids, relevant)

    evidence_recall, missing_evidence = _evidence_scores(case, response)
    observed = observed_outcome(response.diagnostics.answer, abstained=abstained)

    turn_results = []
    for index, run in enumerate(runs):
        actual = observed_outcome(
            run.response.diagnostics.answer, abstained=is_abstained(run.response)
        )
        # 自己申告の応答ラベルは記録にだけ残す。本文の採点は独立した全体評価が持つ。
        turn_results.append(
            EvaluationTurnResult(
                turn=index,
                trace_id=run.response.trace_id,
                observed_outcome=actual.outcome,
                outcome_source=actual.source,
                elapsed_ms=run.response.elapsed_ms,
            )
        )
    # 退避した回答（#4104）は、回答の記録の構造の印で決める（本文の語では判定しない）。
    generation_fallback = any(
        (run.response.diagnostics.answer or {}).get("generation_failure") for run in runs
    )
    if generation_fallback and judgement is not None and judgement.status == "completed":
        # 参考情報の原文は標準回答と合う主張になり得るので、この問は正確性に関わらず
        # 不合格（#5320）。正確性・網羅の値は判定のまま残す。
        judgement = judgement.model_copy(update={"passed": False})
    failure_reasons: list[str] = []
    if missing_evidence:
        failure_reasons.append("evidence_miss")
    if relevant and not hits and not _skips_retrieval(response):
        failure_reasons.append("retrieval_miss")
    if judgement is not None:
        if judgement.status != "completed":
            failure_reasons.append("answer_evaluation_error")
        if judgement.answer_correctness is not None and judgement.answer_correctness < 1:
            failure_reasons.append("answer_failed")
    # 退避した回答は成功の生成に数えない（#4104）。status は評価の処理の完了のまま。
    if generation_fallback:
        failure_reasons.append(ANSWER_GENERATION_FALLBACK)
    return EvaluationCaseResult(
        case_id=case.id,
        trace_id=response.trace_id,
        category=case.category,
        split=case.split,
        reasoning_type=case.reasoning_type,
        hops=case.hops,
        retrieved_document_ids=retrieved_ids,
        relevant_document_ids=list(case.relevant_document_ids),
        hit_document_ids=unique_in_order(hits),
        equated_document_ids=equated,
        mrr=rank,
        answer_correctness=judgement.answer_correctness if judgement else None,
        answer_coverage=judgement.answer_coverage if judgement else None,
        observed_outcome=observed.outcome,
        outcome_source=observed.source,
        required_evidence_recall=evidence_recall,
        missing_evidence=missing_evidence,
        turn_results=turn_results if case.turns else [],
        answer_evaluation=judgement,
        guardrail_warnings=response.guardrail_warnings,
        failure_reasons=list(failure_reasons),
        diagnostics=response.diagnostics,
        elapsed_ms=round(sum(run.response.elapsed_ms for run in runs), 3),
        **reference_metrics,
    )


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
            evidence_matches_chunk(
                evidence, chunk, document_id=case.canonical_document_id(chunk.document_id)
            )
            for chunk in response.citations
        )
    ]
    total = len(case.required_evidence)
    return (total - len(missing)) / total, missing


def evidence_matches_chunk(
    evidence: EvaluationEvidence, chunk: RetrievedChunk, *, document_id: str | None = None
) -> bool:
    """引用の 1 つの chunk が根拠の条件（文書・ページ・シート・行・章節・本文）をすべて満たすか。

    #2065・#2086。`document_id` は chunk の文書の ID の代わりに比べる ID（同じ文書とみなす文書を
    正解の文書の ID にそろえたもの）。照合は platform の採点の部品（#3240）。行の条件は MCP の根拠の
    場所と同じ行の番号の基準と行（`row_basis`・`row_start`・`row_end`）で照合する（#3547。suite の
    評価と同じ）。
    """
    # MCP の層は検索・回答の多くを読むので、使うときに読む（循環の import を避ける）。
    from app.mcp.tools import evidence_row_location

    return evidence_matches(
        evidence,
        document_id=document_id if document_id is not None else chunk.document_id,
        text=chunk.text,
        locator={**chunk.metadata, **evidence_row_location(dict(chunk.metadata))},
    )


def _explicit_outcome(response: SearchResponse) -> str | None:
    details = response.diagnostics.answer or {}
    outcome = details.get("outcome")
    return outcome if isinstance(outcome, str) and outcome else None


def _skips_retrieval(response: SearchResponse) -> bool:
    """確認の質問・人への引き継ぎで引用の無い回答（検索の指標の対象外）。"""
    return skips_retrieval(_explicit_outcome(response), has_citations=bool(response.citations))


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


def _accumulate_case_metrics(aggregate: MetricAggregate, result: EvaluationCaseResult) -> None:
    """成功したケースの測れた指標を集計へ足す。"""
    aggregate.add("required_evidence_recall", result.required_evidence_recall)
    aggregate.add("mrr", result.mrr)
    aggregate.add("answer_correctness", result.answer_correctness)
    for name in EVALUATION_REPORT_ONLY_METRIC_NAMES:
        aggregate.add(name, getattr(result, name))


def _deadline(time_budget_seconds: float | None) -> float | None:
    """評価全体の上限の期限（`perf_counter` の時刻）。上限がなければ None。"""
    return None if time_budget_seconds is None else perf_counter() + time_budget_seconds


def _remaining_seconds(deadline: float | None) -> float | None:
    """評価全体の上限までの残り秒数。上限がなければ None。"""
    return None if deadline is None else deadline - perf_counter()


def _case_timeout_message(error: AnswerTimeoutError, *, limited_by_budget: bool) -> str:
    """時間切れのケースの文言。時間切れになった工程を含め、query 本文は含めない（#383）。"""
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


def _case_error_result(
    *,
    case: EvaluationCase,
    trace_id: str,
    elapsed: float,
    error: Exception,
    error_stage: str | None = None,
    error_message: str = EVALUATION_CASE_ERROR_MESSAGE,
    error_type: str | None = None,
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
        error_type=error_type or type(error).__name__,
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


def _threshold_values(thresholds: EvaluationThresholds | None) -> dict[str, float]:
    """設定された最低閾値（指標 → 値）。"""
    return thresholds.model_dump(exclude_none=True) if thresholds is not None else {}


def _threshold_failures(
    thresholds: EvaluationThresholds | None,
    aggregate_values: Mapping[str, float | None],
) -> list[ThresholdFailure]:
    """設定された最低閾値を下回った aggregate metric（測れなかった指標は判定しない）。"""
    return threshold_failures(_threshold_values(thresholds), aggregate_values)


def _optional_float(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        return None
    try:
        return float(value)
    except ValueError:
        return None


def category_breakdown(results: list[EvaluationCaseResult]) -> dict[str, EvaluationCategorySummary]:
    """ケース分類別の件数。指標は全体の指標の集計に統一する。"""
    if not any(item.category for item in results):
        return {}
    grouped: dict[str, list[EvaluationCaseResult]] = {}
    for item in results:
        grouped.setdefault(item.category or EVALUATION_UNCATEGORIZED, []).append(item)
    summaries = {}
    for name, members in grouped.items():
        aggregate = _aggregate()
        for item in members:
            if item.status == "success":
                _accumulate_case_metrics(aggregate, item)
        summaries[name] = EvaluationCategorySummary(
            case_count=len(members),
            error_count=sum(item.status == "error" for item in members),
            metrics=aggregate.means(),
            metric_case_counts=aggregate.counts(),
        )
    return summaries


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
        aggregate = _aggregate()
        failure_reason_counts: dict[str, int] = {}
        for result in members:
            if result.status == "success":
                _accumulate_case_metrics(aggregate, result)
            count_reasons(failure_reason_counts, result.failure_reasons)
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
        aggregate = _aggregate()
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
