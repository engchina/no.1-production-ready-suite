"""RAG 評価ランナーのテスト(#591 の 9 指標)。"""

import asyncio
import logging
from collections.abc import Mapping, Sequence
from typing import Any, cast

import pytest
from pydantic import ValidationError
from pytest import LogCaptureFixture, MonkeyPatch

from app.config import OCI_ENTERPRISE_AI_TIMEOUT_MAX_SECONDS, Settings, get_settings
from app.main import app
from app.rag.evaluation import (
    ANSWER_JUDGE_UNAVAILABLE_MESSAGE,
    EVALUATION_CASE_ERROR_MESSAGE,
    EvaluationRunner,
    evaluation_settings,
    is_abstained,
    summarize_answer_judgement,
)
from app.rag.evaluation_adapter import resolve_evaluation_suite
from app.rag.pipeline import (
    SearchStageProgress,
    SearchStageProgressCallback,
)
from app.schemas.evaluation import (
    EvaluationCase,
    EvaluationCompareResponse,
    EvaluationExperiment,
    EvaluationMetrics,
    EvaluationRagOverrides,
    EvaluationThresholds,
)
from app.schemas.search import (
    RetrievedChunk,
    SearchDiagnostics,
    SearchRequest,
    SearchResponse,
)
from tests.support import AsgiTestClient

client = AsgiTestClient(app)

EVALUATION_INPUT = {"question": "承認条件", "answer_text": "承認条件は 120000 円です。"}


def _approval_response(
    request: SearchRequest,
    trace_id: str,
    *,
    evaluation_input: Mapping[str, object] | None = None,
) -> SearchResponse:
    return SearchResponse(
        answer="承認条件は 120000 円です。",
        citations=[
            RetrievedChunk(
                document_id="doc-1",
                chunk_id="doc-1:0",
                text="承認条件: 120000",
                score=1.0,
                metadata={
                    "page_start": 2,
                    "page_end": 2,
                    "element_ids": "el-approval",
                    "evidence_model_used": True,
                },
            )
        ],
        trace_id=trace_id,
        guardrail_warnings=[],
        elapsed_ms=1.0,
        diagnostics=SearchDiagnostics(knowledge_base_count=len(request.knowledge_base_ids)),
    ).with_evaluation_input(evaluation_input)


class StubPipeline:
    """評価ランナー用の固定レスポンス pipeline(回答の記録の評価入力つき)。"""

    def __init__(self) -> None:
        self.requests: list[SearchRequest] = []
        self.trace_ids: list[str] = []

    async def run(
        self,
        request: SearchRequest,
        trace_id: str | None = None,
        progress_callback: SearchStageProgressCallback | None = None,
    ) -> SearchResponse:
        assert trace_id
        self.requests.append(request)
        self.trace_ids.append(trace_id)
        return _approval_response(request, trace_id, evaluation_input=EVALUATION_INPUT)


class FakeJudge:
    """標準回答による評価(evaluate_answer_record)の決定論スタブ。"""

    def __init__(self, result: Mapping[str, object] | Exception) -> None:
        self.result = result
        self.calls: list[dict[str, object]] = []

    async def __call__(
        self,
        *,
        trace_id: str,
        evaluation_input: Mapping[str, object],
        standard_answer: str,
        citations: Sequence[RetrievedChunk],
        timeout_seconds: float,
    ) -> Mapping[str, object]:
        self.calls.append(
            {
                "trace_id": trace_id,
                "evaluation_input": dict(evaluation_input),
                "standard_answer": standard_answer,
                "citations": list(citations),
                "timeout_seconds": timeout_seconds,
            }
        )
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def _completed_judgement(
    *,
    passed: bool = True,
    claim_status: str = "supported",
    requirement_coverage: float = 1.0,
    coverage_statuses: tuple[str, ...] = ("addressed",),
) -> dict[str, object]:
    return {
        "status": "completed",
        "passed": passed,
        "claim_checks": [{"status": claim_status}],
        "coverage_checks": [{"status": status} for status in coverage_statuses],
        "requirement_coverage": requirement_coverage,
        "message": "",
    }


async def test_evaluation_runner_computes_metrics_by_perspective() -> None:
    """検索・根拠・回答の指標を、測れるケースだけで求める。"""
    pipeline = StubPipeline()
    runner = EvaluationRunner(pipeline=pipeline)
    metrics = await runner.run(
        cases=[
            EvaluationCase(
                id="case-1",
                query="承認条件",
                relevant_document_ids=["doc-1"],
                expected_answer_keywords=["120000"],
            )
        ],
        top_k=5,
        filters={"status": "indexed"},
    )
    assert metrics.context_recall == 1.0
    assert metrics.mrr == 1.0
    # 数字 1 件の一致だけで 1.0 に短絡せず、全回答 feature に対する比率を使う。
    assert metrics.faithfulness == 0.5
    assert metrics.citation_traceability_coverage == 1.0
    assert metrics.answer_keyword_hit_rate == 1.0
    assert metrics.refusal_accuracy == 1.0
    # 標準回答の無いケースだけなので、LLM による評価の指標は測らない。
    assert metrics.claim_support_rate is None
    assert metrics.requirement_coverage is None
    assert metrics.answer_pass_rate is None
    assert metrics.metric_case_counts == {
        "context_recall": 1,
        "mrr": 1,
        "faithfulness": 1,
        "citation_traceability_coverage": 1,
        "claim_support_rate": 0,
        "answer_keyword_hit_rate": 1,
        "refusal_accuracy": 1,
        "requirement_coverage": 0,
        "answer_pass_rate": 0,
        "handling_accuracy": 0,
        "step_order_score": 0,
        "safe_answer_rate": 0,
        "condition_coverage": 0,
        # 参考の集計（#1284 / #1335）。必要な根拠の無いケースだけなので測らない。
        "required_evidence_recall": 0,
        "evidence_chain_complete_rate": 0,
    }
    assert metrics.required_evidence_recall is None
    assert metrics.evidence_chain_complete_rate is None
    assert metrics.reasoning_type_breakdown == {}
    assert metrics.hops_breakdown == {}
    assert metrics.split_breakdown == {}
    assert metrics.passed is True
    assert metrics.threshold_failures == []
    assert metrics.failure_reason_counts == {}
    result = metrics.case_results[0]
    assert result.case_id == "case-1"
    assert result.trace_id == pipeline.trace_ids[0]
    assert result.status == "success"
    assert result.retrieved_document_ids == ["doc-1"]
    assert result.hit_document_ids == ["doc-1"]
    assert result.context_recall == 1.0
    assert result.reciprocal_rank == 1.0
    assert result.faithfulness == 0.5
    assert result.grounding_overlap_count >= 1
    assert result.answer_keyword_hit is True
    assert result.abstained is False
    assert result.refusal_correct is True
    assert result.answer_evaluation is None
    assert result.failure_reasons == []
    assert pipeline.requests[0].top_k == 5
    assert pipeline.requests[0].filters == {"status": "INDEXED"}


async def test_evaluation_runner_passes_knowledge_base_scope_to_search_request() -> None:
    """評価 runner は KB スコープを SearchRequest へ伝播する。"""
    pipeline = StubPipeline()
    runner = EvaluationRunner(pipeline=pipeline)

    await runner.run(
        cases=[EvaluationCase(id="case-1", query="承認条件")],
        top_k=5,
        knowledge_base_ids=["kb-1", "kb-2"],
    )

    request = pipeline.requests[0]
    assert request.knowledge_base_ids == ["kb-1", "kb-2"]
    assert request.filters["knowledge_base_id"] == "kb-1,kb-2"


async def test_evaluation_runner_includes_superseded_documents_only_for_marked_cases() -> None:
    """旧版も検索するケース（版の比較。#1366）だけ include_superseded=true を足す。"""
    pipeline = StubPipeline()
    runner = EvaluationRunner(pipeline=pipeline)

    await runner.run(
        cases=[
            EvaluationCase(id="current", query="今の承認の期限"),
            EvaluationCase(id="versions", query="旧版との違い", include_superseded=True),
        ],
        top_k=5,
        filters={"status": "indexed"},
    )

    assert [request.filters for request in pipeline.requests] == [
        {"status": "INDEXED"},
        {"status": "INDEXED", "include_superseded": "true"},
    ]


async def test_metrics_skip_cases_without_expectations() -> None:
    """期待値を持たないケースは、その指標の分母に入れない(rag_poc と同じ)。"""
    runner = EvaluationRunner(pipeline=StubPipeline())

    metrics = await runner.run(
        cases=[
            EvaluationCase(
                id="with-keywords",
                query="承認条件",
                relevant_document_ids=["doc-1"],
                expected_answer_keywords=["120000"],
            ),
            EvaluationCase(id="doc-only", query="承認条件", relevant_document_ids=["doc-2"]),
        ],
        top_k=5,
    )

    assert metrics.metric_case_counts["answer_keyword_hit_rate"] == 1
    assert metrics.answer_keyword_hit_rate == 1.0
    assert metrics.metric_case_counts["context_recall"] == 2
    assert metrics.context_recall == 0.5
    assert metrics.case_results[1].answer_keyword_hit is None
    assert metrics.case_results[1].failure_reasons == ["retrieval_miss"]


class DuplicateChunkPipeline:
    """同じ document の複数 chunk を返す pipeline。"""

    async def run(
        self,
        request: SearchRequest,
        trace_id: str | None = None,
        progress_callback: SearchStageProgressCallback | None = None,
    ) -> SearchResponse:
        return SearchResponse(
            answer="A 文書の承認条件が関連します。",
            citations=[
                RetrievedChunk(
                    document_id=document_id,
                    chunk_id=chunk_id,
                    text=text,
                    score=score,
                )
                for document_id, chunk_id, text, score in (
                    ("doc-b", "doc-b:0", "B 文書の検索候補です。", 0.9),
                    ("doc-a", "doc-a:0", "A 文書には承認条件が記載されています。", 0.8),
                    ("doc-a", "doc-a:1", "A 文書の補足説明です。", 0.7),
                )
            ],
            trace_id=trace_id or "trace",
            guardrail_warnings=[],
            elapsed_ms=1.0,
        )


async def test_retrieval_metrics_are_document_level_not_chunk_level() -> None:
    runner = EvaluationRunner(pipeline=DuplicateChunkPipeline())

    metrics = await runner.run(
        cases=[EvaluationCase(id="case-duplicate", query="A", relevant_document_ids=["doc-a"])],
        top_k=3,
    )

    assert metrics.context_recall == 1.0
    # 正解の文書は 2 番目の文書(chunk の数では数えない)。
    assert metrics.mrr == 0.5
    assert metrics.case_results[0].retrieved_document_ids == ["doc-b", "doc-a"]
    assert metrics.case_results[0].hit_document_ids == ["doc-a"]


class MissPipeline:
    """関連 document を返さず、guardrail warning 付きの応答を返す pipeline。"""

    async def run(
        self,
        request: SearchRequest,
        trace_id: str | None = None,
        progress_callback: SearchStageProgressCallback | None = None,
    ) -> SearchResponse:
        return SearchResponse(
            answer="関連しない回答です。",
            citations=[
                RetrievedChunk(
                    document_id="doc-x",
                    chunk_id="doc-x:0",
                    text="支払条件は月末締め翌月末払いです。",
                    score=0.8,
                ),
            ],
            trace_id=trace_id or "trace-miss",
            guardrail_warnings=["検索条件に一致する根拠が見つかりませんでした。"],
            elapsed_ms=12.5,
        )


async def test_evaluation_case_result_exposes_miss_diagnostics() -> None:
    """失敗ケースでも trace と取得 document を返し、原因追跡できる。"""
    runner = EvaluationRunner(pipeline=MissPipeline())

    metrics = await runner.run(
        cases=[
            EvaluationCase(
                id="case-miss",
                query="承認条件",
                relevant_document_ids=["doc-a"],
                expected_answer_keywords=["120000"],
            )
        ],
        top_k=5,
    )

    assert metrics.context_recall == 0.0
    assert metrics.mrr == 0.0
    assert metrics.answer_keyword_hit_rate == 0.0
    assert metrics.faithfulness == 0.0
    # 答えるべきケースで答えたので、拒答の判定は正しい。
    assert metrics.refusal_accuracy == 1.0
    result = metrics.case_results[0]
    assert result.retrieved_document_ids == ["doc-x"]
    assert result.hit_document_ids == []
    assert result.grounding_answer_feature_count > 0
    assert result.guardrail_warnings == ["検索条件に一致する根拠が見つかりませんでした。"]
    assert result.failure_reasons == [
        "retrieval_miss",
        "answer_keyword_miss",
        "guardrail_warning",
    ]
    assert metrics.failure_reason_counts == {
        "retrieval_miss": 1,
        "answer_keyword_miss": 1,
        "guardrail_warning": 1,
    }
    assert result.elapsed_ms == 12.5


async def test_evaluation_runner_reports_threshold_failures() -> None:
    """aggregate 指標が閾値を下回る場合は metric ごとの失敗を返す。"""
    runner = EvaluationRunner(pipeline=MissPipeline())

    metrics = await runner.run(
        cases=[
            EvaluationCase(
                id="case-fail",
                query="承認条件",
                relevant_document_ids=["doc-a"],
                expected_answer_keywords=["120000"],
            )
        ],
        top_k=5,
        thresholds=resolve_evaluation_suite("standard"),
    )

    assert metrics.passed is False
    assert [
        (failure.metric, failure.actual, failure.threshold)
        for failure in metrics.threshold_failures
    ] == [
        ("context_recall", 0.0, 0.8),
        ("mrr", 0.0, 0.6),
        ("citation_traceability_coverage", 0.0, 0.9),
        ("answer_keyword_hit_rate", 0.0, 0.8),
    ]
    # 語句の一致率(faithfulness)は参考値。閾値を下回っても合否に使わない(#711)。
    assert metrics.faithfulness == 0.0


NO_RESULTS_ANSWER = "資料の中に、この質問に答えられる根拠が見つかりませんでした。"
NO_RESULTS_WARNING = "検索条件に一致する根拠が見つかりませんでした。"


class NoResultsPipeline:
    """根拠が見つからず、引用なしで「答えられない」と返す pipeline。"""

    async def run(
        self,
        request: SearchRequest,
        trace_id: str | None = None,
        progress_callback: SearchStageProgressCallback | None = None,
    ) -> SearchResponse:
        return SearchResponse(
            answer=NO_RESULTS_ANSWER,
            citations=[],
            trace_id=trace_id or "trace-no-results",
            guardrail_warnings=[NO_RESULTS_WARNING],
            elapsed_ms=3.0,
        )


@pytest.mark.parametrize("suite", ["standard", "strict"])
async def test_expected_refusal_case_passes_every_suite(suite: str) -> None:
    """答えるべきでない質問に答えなければ合格(#301)。検索・根拠の指標は測らない。"""
    runner = EvaluationRunner(pipeline=NoResultsPipeline())

    metrics = await runner.run(
        cases=[EvaluationCase(id="case-negative", query="存在しない社内規程の承認者は？")],
        top_k=5,
        thresholds=resolve_evaluation_suite(suite),
    )

    assert metrics.threshold_failures == []
    assert metrics.passed is True
    assert metrics.refusal_accuracy == 1.0
    assert metrics.context_recall is None
    assert metrics.mrr is None
    assert metrics.faithfulness is None
    assert metrics.failure_reason_counts == {}
    result = metrics.case_results[0]
    assert result.abstained is True
    assert result.refusal_correct is True
    assert result.failure_reasons == []


async def test_answerable_case_with_no_results_counts_unexpected_refusal() -> None:
    """正解の文書があるケースで答えなかったら、検索の失敗と想定外の拒答として数える。"""
    runner = EvaluationRunner(pipeline=NoResultsPipeline())

    metrics = await runner.run(
        cases=[
            EvaluationCase(id="case-positive", query="承認者は？", relevant_document_ids=["doc-a"])
        ],
        top_k=5,
    )

    assert metrics.mrr == 0.0
    assert metrics.refusal_accuracy == 0.0
    assert metrics.case_results[0].failure_reasons == [
        "retrieval_miss",
        "unexpected_refusal",
        "guardrail_warning",
    ]


async def test_unanswerable_case_answered_counts_unexpected_answer() -> None:
    """answerable=false のケースで答えたら、想定外の回答として数える。"""
    runner = EvaluationRunner(pipeline=StubPipeline())

    metrics = await runner.run(
        cases=[EvaluationCase(id="case-negative", query="承認条件", answerable=False)],
        top_k=5,
    )

    assert metrics.refusal_accuracy == 0.0
    assert metrics.case_results[0].failure_reasons == ["unexpected_answer"]


async def test_category_breakdown_summarizes_results_per_category() -> None:
    """ケースの分類ごとに件数・語の一致・拒答の内訳を出す（#1226）。

    分類の無いケースは uncategorized。
    """
    runner = EvaluationRunner(pipeline=StubPipeline())

    metrics = await runner.run(
        cases=[
            EvaluationCase(
                id="doc-1",
                query="承認条件",
                expected_answer_keywords=["120000"],
                category="document_answerable",
            ),
            EvaluationCase(
                id="missing-1", query="承認条件", answerable=False, category="knowledge_missing"
            ),
            EvaluationCase(id="plain-1", query="承認条件", expected_answer_keywords=["無い語"]),
        ],
        top_k=5,
    )

    assert [result.category for result in metrics.case_results] == [
        "document_answerable",
        "knowledge_missing",
        None,
    ]
    breakdown = {key: value.model_dump() for key, value in metrics.category_breakdown.items()}
    assert breakdown["document_answerable"] == {
        "case_count": 1,
        "error_count": 0,
        "answer_pass_rate": None,
        "answer_keyword_hit_rate": 1.0,
        "abstain_rate": 0.0,
        "refusal_correct_rate": 1.0,
        "handling_correct_rate": None,
        "step_order_score": None,
        "safe_answer_rate": None,
    }
    # 資料に無い質問に答えたので、拒答の判断が期待と違う。
    assert breakdown["knowledge_missing"]["refusal_correct_rate"] == 0.0
    assert breakdown["uncategorized"]["answer_keyword_hit_rate"] == 0.0


async def test_category_breakdown_is_empty_without_categories() -> None:
    runner = EvaluationRunner(pipeline=StubPipeline())
    metrics = await runner.run(cases=[EvaluationCase(id="c", query="承認条件")], top_k=5)
    assert metrics.category_breakdown == {}
    with pytest.raises(ValidationError):
        EvaluationCase.model_validate({"id": "c", "query": "q", "category": "unknown"})


def test_is_abstained_uses_answer_record_insufficient_reason() -> None:
    """不足の理由を返し、モデルが使った根拠が無い回答は拒答とみなす。"""
    request = SearchRequest(query="承認条件")
    answered = _approval_response(request, "trace-1")
    refused = answered.model_copy(
        update={
            "citations": [
                chunk.model_copy(update={"metadata": {"evidence_model_used": False}})
                for chunk in answered.citations
            ],
            "diagnostics": SearchDiagnostics(answer={"insufficient_reason": "資料に記載がない"}),
        }
    )
    partial = answered.model_copy(
        update={"diagnostics": SearchDiagnostics(answer={"insufficient_reason": "一部不足"})}
    )

    assert is_abstained(answered) is False
    assert is_abstained(refused) is True
    # 一部だけ不足でも、モデルが根拠を使って答えた回答は拒答ではない。
    assert is_abstained(partial) is False


def test_expects_answer_is_inferred_from_expectations() -> None:
    assert EvaluationCase(id="a", query="q").expects_answer is False
    assert EvaluationCase(id="b", query="q", expected_answer_keywords=["x"]).expects_answer
    assert EvaluationCase(id="c", query="q", standard_answer="答え").expects_answer
    assert not EvaluationCase(id="d", query="q", standard_answer="   ").expects_answer
    assert not EvaluationCase(
        id="e", query="q", relevant_document_ids=["doc"], answerable=False
    ).expects_answer


async def test_standard_answer_case_is_judged_with_answer_record() -> None:
    """標準回答のあるケースは、回答の記録の評価入力を使って LLM で比較する。"""
    judge = FakeJudge(_completed_judgement())
    pipeline = StubPipeline()
    runner = EvaluationRunner(pipeline=pipeline, answer_judge=judge)

    metrics = await runner.run(
        cases=[
            EvaluationCase(
                id="case-judged",
                query="承認条件",
                relevant_document_ids=["doc-1"],
                standard_answer="承認条件は 120000 円です。",
            ),
            EvaluationCase(id="case-plain", query="承認条件", relevant_document_ids=["doc-1"]),
        ],
        top_k=5,
        thresholds=resolve_evaluation_suite("strict"),
    )

    assert len(judge.calls) == 1
    call = judge.calls[0]
    assert call["trace_id"] == pipeline.trace_ids[0]
    assert call["evaluation_input"] == EVALUATION_INPUT
    assert call["standard_answer"] == "承認条件は 120000 円です。"
    assert call["timeout_seconds"] == OCI_ENTERPRISE_AI_TIMEOUT_MAX_SECONDS
    # 決定的な指標(忠実さ・引用の追跡)に使う回答の引用も渡す(#680)。
    assert call["citations"]
    assert metrics.claim_support_rate == 1.0
    assert metrics.requirement_coverage == 1.0
    assert metrics.answer_pass_rate == 1.0
    assert metrics.metric_case_counts["answer_pass_rate"] == 1
    judgement = metrics.case_results[0].answer_evaluation
    assert judgement is not None
    assert judgement.status == "completed"
    assert judgement.passed is True
    assert judgement.claims_supported is True
    assert judgement.missing_content is False
    assert metrics.case_results[1].answer_evaluation is None


async def test_judgement_failures_are_reported_per_axis() -> None:
    """根拠のない主張・必要な項目の欠落・不合格を、それぞれの失敗理由として残す。"""
    judge = FakeJudge(
        _completed_judgement(
            passed=False,
            claim_status="contradicted",
            requirement_coverage=0.5,
            coverage_statuses=("addressed", "missing"),
        )
    )
    runner = EvaluationRunner(pipeline=StubPipeline(), answer_judge=judge)

    metrics = await runner.run(
        cases=[
            EvaluationCase(
                id="case-judged",
                query="承認条件",
                relevant_document_ids=["doc-1"],
                standard_answer="承認者は部長です。",
            )
        ],
        top_k=5,
        thresholds=resolve_evaluation_suite("standard"),
    )

    assert metrics.claim_support_rate == 0.0
    assert metrics.requirement_coverage == 0.5
    assert metrics.answer_pass_rate == 0.0
    assert metrics.case_results[0].failure_reasons == [
        "unsupported_claim",
        "missing_content",
        "answer_failed",
    ]
    assert {failure.metric for failure in metrics.threshold_failures} >= {
        "claim_support_rate",
        "requirement_coverage",
        "answer_pass_rate",
    }
    assert metrics.passed is False


@pytest.mark.parametrize(
    ("judge_result", "status"),
    [
        (TimeoutError(), "timeout"),
        (RuntimeError("secret detail"), "error"),
        ({"status": "input_too_large", "message": "入力上限を超えています。"}, "input_too_large"),
    ],
    ids=["timeout", "exception", "input_too_large"],
)
async def test_incomplete_judgement_is_not_counted_and_fails_the_gate(
    judge_result: Mapping[str, object] | Exception, status: str
) -> None:
    """標準回答で評価できなかったケースは指標に入れず、評価を合格にしない。"""
    runner = EvaluationRunner(pipeline=StubPipeline(), answer_judge=FakeJudge(judge_result))

    metrics = await runner.run(
        cases=[
            EvaluationCase(
                id="case-judged",
                query="承認条件",
                relevant_document_ids=["doc-1"],
                standard_answer="承認者は部長です。",
            )
        ],
        top_k=5,
    )

    judgement = metrics.case_results[0].answer_evaluation
    assert judgement is not None
    assert judgement.status == status
    assert "secret detail" not in str(judgement.model_dump())
    assert metrics.answer_pass_rate is None
    assert metrics.case_results[0].failure_reasons == ["answer_evaluation_error"]
    assert metrics.error_count == 0
    assert metrics.passed is False


async def test_judgement_is_unavailable_without_answer_record_input() -> None:
    """回答の記録の評価入力が無い回答(別の回答エンジン)は、評価できない理由を残す。"""
    judge = FakeJudge(_completed_judgement())
    runner = EvaluationRunner(pipeline=MissPipeline(), answer_judge=judge)

    metrics = await runner.run(
        cases=[EvaluationCase(id="case", query="承認条件", standard_answer="部長です。")],
        top_k=5,
    )

    judgement = metrics.case_results[0].answer_evaluation
    assert judgement is not None
    assert judgement.status == "unavailable"
    assert judgement.message == ANSWER_JUDGE_UNAVAILABLE_MESSAGE
    assert judge.calls == []


def test_summarize_answer_judgement_normalizes_coverage() -> None:
    judgement = summarize_answer_judgement(
        _completed_judgement(requirement_coverage=0.75, coverage_statuses=("partial",))
    )
    assert judgement.requirement_coverage == 0.75
    assert judgement.missing_content is False


def test_saved_legacy_metrics_still_validate() -> None:
    """削除した指標・失敗理由を含む保存済みの結果も読める(表示を壊さない)。"""
    legacy = {
        "case_count": 1,
        "error_count": 0,
        "evaluation_suite": "balanced",
        "evaluated_k": 3,
        "precision_at_k": 0.3333,
        "recall_at_k": 1.0,
        "mrr": 1.0,
        "answer_keyword_hit_rate": 1.0,
        "groundedness_pass_rate": 1.0,
        "faithfulness": 0.5,
        "context_recall": 1.0,
        "bbox_citation_coverage": 1.0,
        "passed": False,
        "threshold_failures": [{"metric": "section_coverage", "actual": 0.5, "threshold": 0.8}],
        "failure_reason_counts": {"section_miss": 1},
        "case_results": [
            {
                "case_id": "c1",
                "trace_id": "t1",
                "precision_at_k": 0.3333,
                "recall_at_k": 1.0,
                "reciprocal_rank": 1.0,
                "answer_keyword_hit": True,
                "groundedness_passed": True,
                "groundedness_score": 0.5,
                "failure_reasons": ["section_miss"],
                "elapsed_ms": 1.0,
            }
        ],
        "ingestion_quality": {"document_count": 3},
    }

    metrics = EvaluationMetrics.model_validate(legacy)

    assert metrics.mrr == 1.0
    assert metrics.context_recall == 1.0
    assert metrics.refusal_accuracy is None
    assert metrics.threshold_failures[0].metric == "section_coverage"
    assert metrics.failure_reason_counts == {"section_miss": 1}
    assert metrics.case_results[0].reciprocal_rank == 1.0
    assert "precision_at_k" not in metrics.model_dump()

    comparison = EvaluationCompareResponse.model_validate(
        {
            "ranking_metric": "precision_at_k",
            "best_experiment_id": "hybrid",
            "results": [
                {
                    "rank": 1,
                    "ranking_score": 0.5,
                    "experiment": {
                        "id": "hybrid",
                        "mode": "hybrid",
                        "rerank_top_n": 5,
                        "rag_overrides": {"context_window_chars": 4096, "rrf_k": 30},
                    },
                    "metrics": legacy,
                }
            ],
        }
    )
    assert comparison.ranking_metric == "precision_at_k"
    overrides = comparison.results[0].experiment.rag_overrides
    assert overrides is not None
    assert overrides.rrf_k == 30


class PartiallyFailingPipeline:
    """一部 case だけ失敗する評価 runner 用 pipeline。"""

    async def run(
        self,
        request: SearchRequest,
        trace_id: str | None = None,
        progress_callback: SearchStageProgressCallback | None = None,
    ) -> SearchResponse:
        if "失敗" in request.query:
            raise RuntimeError("raw secret detail: INV-SECRET")
        return _approval_response(request, trace_id or "trace-ok")


async def test_evaluation_runner_isolates_case_errors(caplog: LogCaptureFixture) -> None:
    """1 case の検索失敗は batch 全体を中断せず、失敗 case として返す。"""
    runner = EvaluationRunner(pipeline=PartiallyFailingPipeline())

    with caplog.at_level(logging.INFO, logger="app.audit"):
        metrics = await runner.run(
            cases=[
                EvaluationCase(
                    id="case-ok",
                    query="承認条件",
                    relevant_document_ids=["doc-1"],
                    expected_answer_keywords=["120000"],
                ),
                EvaluationCase(
                    id="case-error",
                    query="INV-SECRET の失敗ケース",
                    relevant_document_ids=["doc-2"],
                    expected_answer_keywords=["999"],
                ),
            ],
            top_k=5,
        )

    assert metrics.case_count == 2
    assert metrics.error_count == 1
    assert metrics.passed is False
    # 失敗したケースは指標の平均に入れない(error_count で数える)。
    assert metrics.context_recall == 1.0
    assert metrics.metric_case_counts["context_recall"] == 1

    ok_result, error_result = metrics.case_results
    assert ok_result.status == "success"
    assert error_result.case_id == "case-error"
    assert error_result.status == "error"
    assert error_result.error_type == "RuntimeError"
    assert error_result.error_message == EVALUATION_CASE_ERROR_MESSAGE
    assert error_result.relevant_document_ids == ["doc-2"]
    assert error_result.context_recall is None
    assert error_result.failure_reasons == ["case_error"]
    assert metrics.failure_reason_counts == {"case_error": 1}
    assert "INV-SECRET" not in str(error_result.model_dump(mode="json"))
    assert "raw secret detail" not in str(error_result.model_dump(mode="json"))

    audit_record = next(record for record in caplog.records if record.message == "rag_search_audit")
    audit_event = cast(Any, audit_record).audit_event
    assert audit_event["trace_id"] == error_result.trace_id
    assert audit_event["outcome"] == "error"
    assert audit_event["error_stage"] == "evaluation"
    assert audit_event["error_type"] == "RuntimeError"
    assert "INV-SECRET" not in str(audit_event)


async def test_evaluation_runner_records_case_metrics(monkeypatch: MonkeyPatch) -> None:
    """評価 case ごとの成功/失敗を低 cardinality metrics に残す。"""
    observed: list[tuple[str, str, float]] = []
    monkeypatch.setattr(
        "app.rag.evaluation.record_evaluation_case",
        lambda mode, status, seconds: observed.append((mode, status, seconds)),
    )
    runner = EvaluationRunner(pipeline=PartiallyFailingPipeline())

    await runner.run(
        cases=[
            EvaluationCase(id="case-ok", query="承認条件", relevant_document_ids=["doc-1"]),
            EvaluationCase(id="case-error", query="失敗ケース", relevant_document_ids=["doc-2"]),
        ],
        top_k=5,
    )

    assert [(mode, status) for mode, status, _ in observed] == [
        ("hybrid", "success"),
        ("hybrid", "error"),
    ]
    assert all(seconds >= 0 for _, _, seconds in observed)


class SlowPipeline:
    """評価 case timeout を再現する pipeline。"""

    async def run(
        self,
        request: SearchRequest,
        trace_id: str | None = None,
        progress_callback: SearchStageProgressCallback | None = None,
    ) -> SearchResponse:
        assert trace_id
        await asyncio.sleep(1)
        raise AssertionError("timeout 前に完了しない")


class StagedSlowPipeline:
    """「遅い」を含む質問だけ、回答フロー（answer）の工程で止まる pipeline。"""

    def __init__(self, *, sleep_seconds: float) -> None:
        self._sleep_seconds = sleep_seconds
        self.queries: list[str] = []

    async def run(
        self,
        request: SearchRequest,
        trace_id: str | None = None,
        progress_callback: SearchStageProgressCallback | None = None,
    ) -> SearchResponse:
        self.queries.append(request.query)
        if "遅い" in request.query:
            if progress_callback is not None:
                await progress_callback(
                    SearchStageProgress(
                        trace_id=trace_id or "trace",
                        stage="answer",
                        outcome="started",
                        elapsed_ms=0.0,
                        attributes={},
                    )
                )
            await asyncio.sleep(self._sleep_seconds)
        return _approval_response(request, trace_id or "trace-ok")


async def test_evaluation_runner_records_timeout_audit(
    monkeypatch: MonkeyPatch,
    caplog: LogCaptureFixture,
) -> None:
    """評価 case timeout は error result と脱敏済み RAG 監査ログに残す。"""
    settings = get_settings()
    monkeypatch.setattr(settings, "rag_answer_timeout_seconds", 0.001)
    runner = EvaluationRunner(pipeline=SlowPipeline(), settings=settings)

    with caplog.at_level(logging.INFO, logger="app.audit"):
        metrics = await runner.run(
            cases=[
                EvaluationCase(
                    id="case-timeout",
                    query="INV-SECRET の timeout ケース",
                    relevant_document_ids=["doc-timeout"],
                )
            ],
            top_k=5,
        )

    assert metrics.error_count == 1
    assert metrics.passed is False
    result = metrics.case_results[0]
    assert result.status == "error"
    assert result.error_type == "TimeoutError"
    assert result.error_message is not None
    assert "時間切れになった工程: 検索の準備" in result.error_message
    assert "INV-SECRET" not in result.error_message

    audit_record = next(record for record in caplog.records if record.message == "rag_search_audit")
    audit_event = cast(Any, audit_record).audit_event
    assert audit_event["trace_id"] == result.trace_id
    assert audit_event["error_stage"] == "timeout"
    assert "INV-SECRET" not in str(audit_event)


def _timeout_cases() -> list[EvaluationCase]:
    return [
        EvaluationCase(id="case-slow", query="遅い: 承認条件は？", relevant_document_ids=["doc-1"]),
        EvaluationCase(id="case-fast", query="承認条件は？", relevant_document_ids=["doc-1"]),
    ]


async def test_evaluation_case_is_limited_by_answer_timeout() -> None:
    """評価の 1 ケースは、回答生成の上限（rag_answer_timeout_seconds）で打ち切る（#383）。"""
    settings = Settings(rag_answer_timeout_seconds=0.05)
    pipeline = StagedSlowPipeline(sleep_seconds=1.0)
    runner = EvaluationRunner(pipeline=pipeline, settings=settings)

    metrics = await runner.run(cases=_timeout_cases(), top_k=5)

    slow, fast = metrics.case_results
    assert slow.status == "error"
    assert slow.error_type == "TimeoutError"
    assert slow.failure_reasons == ["case_error"]
    assert slow.error_stage == "answer"
    assert slow.error_message is not None
    assert "上限の 1 秒以内に終わりませんでした" in slow.error_message
    assert "時間切れになった工程: 根拠の検索と回答の生成" in slow.error_message
    assert "遅い" not in slow.error_message
    assert fast.status == "success"
    assert pipeline.queries == ["遅い: 承認条件は？", "承認条件は？"]
    assert metrics.error_count == 1
    assert metrics.passed is False


async def test_evaluation_time_budget_stops_remaining_cases() -> None:
    """評価全体の上限に達したら、実行中のケースを打ち切り、残りのケースは実行せずに記録する。"""
    settings = Settings(rag_answer_timeout_seconds=5.0)
    pipeline = StagedSlowPipeline(sleep_seconds=1.0)
    runner = EvaluationRunner(pipeline=pipeline, settings=settings)

    metrics = await runner.run(cases=_timeout_cases(), top_k=5, time_budget_seconds=0.05)

    slow, skipped = metrics.case_results
    assert slow.status == "error"
    assert slow.error_message is not None
    assert "評価全体の時間の上限に達したため" in slow.error_message
    assert skipped.error_type == "EvaluationTimeBudgetExceeded"
    assert skipped.failure_reasons == ["case_error"]
    assert pipeline.queries == ["遅い: 承認条件は？"]
    assert metrics.error_count == 2


async def test_evaluation_compare_shares_time_budget_across_experiments() -> None:
    """比較は、評価全体の上限を experiment の間で共有する（HTTP の待ちを超えない）。"""
    settings = Settings(rag_answer_timeout_seconds=5.0)
    pipeline = StagedSlowPipeline(sleep_seconds=1.0)
    runner = EvaluationRunner(pipeline=pipeline, settings=settings)

    comparison = await runner.compare(
        cases=_timeout_cases(),
        experiments=[
            EvaluationExperiment(id="first", top_k=5),
            EvaluationExperiment(id="second", top_k=5),
        ],
        time_budget_seconds=0.05,
    )

    by_id = {result.experiment.id: result for result in comparison.results}
    assert by_id["first"].metrics.error_count == 2
    assert [result.error_type for result in by_id["second"].metrics.case_results] == [
        "EvaluationTimeBudgetExceeded",
        "EvaluationTimeBudgetExceeded",
    ]
    assert pipeline.queries == ["遅い: 承認条件は？"]


class ComparePipeline:
    """top_k が大きい設定だけ正解の文書を返す pipeline。"""

    async def run(
        self,
        request: SearchRequest,
        trace_id: str | None = None,
        progress_callback: SearchStageProgressCallback | None = None,
    ) -> SearchResponse:
        if request.top_k >= 3:
            return _approval_response(request, trace_id or "trace-wide")
        return SearchResponse(
            answer="関連しない回答です。",
            citations=[
                RetrievedChunk(
                    document_id="doc-x",
                    chunk_id="doc-x:0",
                    text="支払条件は月末締め翌月末払いです。",
                    score=0.8,
                )
            ],
            trace_id=trace_id or "trace-narrow",
            guardrail_warnings=[],
            elapsed_ms=2.0,
        )


async def test_evaluation_runner_compares_experiments_and_ranks_best() -> None:
    """同じ golden set で複数の回答設定を比較し、metric と失敗数で順位付けする。"""
    runner = EvaluationRunner(pipeline=ComparePipeline())

    comparison = await runner.compare(
        cases=[
            EvaluationCase(
                id="case-compare",
                query="承認条件",
                relevant_document_ids=["doc-1"],
                expected_answer_keywords=["120000"],
            )
        ],
        experiments=[
            EvaluationExperiment(id="narrow", top_k=1),
            EvaluationExperiment(id="wide", top_k=3),
        ],
    )

    assert comparison.ranking_metric == "context_recall"
    assert comparison.best_experiment_id == "wide"
    assert [result.experiment.id for result in comparison.results] == ["wide", "narrow"]
    assert comparison.results[0].ranking_score == 1.0
    assert comparison.results[1].ranking_score == 0.0
    assert comparison.results[1].metrics.failure_reason_counts["retrieval_miss"] == 1


async def test_compare_ranks_unmeasured_metric_last() -> None:
    """順位の指標を測れなかった experiment は、測れたものの後ろに並べる。"""
    runner = EvaluationRunner(pipeline=StubPipeline())

    comparison = await runner.compare(
        cases=[EvaluationCase(id="case", query="承認条件", relevant_document_ids=["doc-1"])],
        experiments=[EvaluationExperiment(id="a"), EvaluationExperiment(id="b")],
        ranking_metric="answer_pass_rate",
    )

    assert [result.ranking_score for result in comparison.results] == [None, None]
    assert comparison.best_experiment_id == "a"


def test_evaluation_settings_map_overrides() -> None:
    """評価は全体の既定のまま動かし、experiment の上書きだけを一時適用する。"""
    base = Settings(
        rag_query_strategy="auto_routing",
        rag_answer_flow="crag",
        rag_neighbor_child_count=3,
        rag_rerank_enabled=True,
        rag_rrf_k=60,
        rag_context_group_max_chunks=4,
        oracle_vector_target_accuracy=95,
    )

    default = evaluation_settings(base, None)
    overridden = evaluation_settings(
        base,
        EvaluationRagOverrides(
            query_strategy="rag_fusion",
            answer_flow="standard_rag",
            neighbor_child_count=1,
            rerank_enabled=False,
            rrf_k=30,
            context_group_max_chunks=2,
            oracle_vector_target_accuracy=90,
        ),
    )

    assert default.rag_query_strategy == "auto_routing"
    assert overridden.rag_query_strategy == "rag_fusion"
    assert overridden.rag_answer_flow == "standard_rag"
    assert overridden.rag_neighbor_child_count == 1
    assert overridden.rag_rerank_enabled is False
    assert overridden.rag_rrf_k == 30
    assert overridden.rag_context_group_max_chunks == 2
    assert overridden.oracle_vector_target_accuracy == 90
    # 元の Settings は変えない。
    assert base.rag_query_strategy == "auto_routing"


async def test_evaluation_compare_applies_experiment_rag_overrides(
    monkeypatch: MonkeyPatch,
) -> None:
    """compare experiment ごとの上書きを一時 Settings として pipeline へ渡す。"""
    observed_settings: list[Settings] = []

    class CapturingRagPipeline:
        def __init__(self, settings: Settings) -> None:
            observed_settings.append(settings)

        async def run(
            self,
            request: SearchRequest,
            trace_id: str | None = None,
            progress_callback: SearchStageProgressCallback | None = None,
        ) -> SearchResponse:
            return _approval_response(request, trace_id or "trace")

    monkeypatch.setattr("app.rag.evaluation.RagPipeline", CapturingRagPipeline)
    runner = EvaluationRunner(
        settings=Settings(rag_answer_timeout_seconds=30.0, rag_rrf_k=60),
        answer_judge=FakeJudge(_completed_judgement()),
    )

    await runner.compare(
        cases=[EvaluationCase(id="case", query="承認条件", relevant_document_ids=["doc-1"])],
        experiments=[
            EvaluationExperiment(id="baseline", top_k=3),
            EvaluationExperiment(
                id="fusion",
                top_k=3,
                rag_overrides=EvaluationRagOverrides(query_strategy="rag_fusion", rrf_k=10),
            ),
        ],
    )

    assert [settings.rag_rrf_k for settings in observed_settings] == [60, 10]
    assert observed_settings[1].rag_query_strategy == "rag_fusion"


def test_evaluation_api_rejects_empty_cases() -> None:
    response = client.post("/api/evaluation/run", json={"cases": [], "top_k": 5})

    assert response.status_code == 422
    body = response.json()
    assert body["data"] is None
    assert body["error_messages"]


@pytest.mark.parametrize(
    "thresholds",
    [{"context_recall": 1.1}, {"precision_at_k": 0.5}, {"groundedness_pass_rate": 0.9}],
    ids=["out-of-range", "removed-precision", "removed-groundedness"],
)
def test_evaluation_api_rejects_invalid_thresholds(thresholds: dict[str, float]) -> None:
    """範囲外の値と、削除した指標の閾値は受け付けない(gate が効かないまま通さない)。"""
    response = client.post(
        "/api/evaluation/run",
        json={"cases": [{"id": "c", "query": "承認条件"}], "thresholds": thresholds},
    )

    assert response.status_code == 422
    assert response.json()["error_messages"]


def test_evaluation_api_ignores_removed_run_fields(monkeypatch: MonkeyPatch) -> None:
    """以前の golden set の mode / rerank_top_n / 削除した期待値の欄は無視して評価する。"""
    captured: dict[str, Any] = {}

    async def fake_run(self: EvaluationRunner, **kwargs: Any) -> EvaluationMetrics:
        captured.update(kwargs)
        return EvaluationMetrics(case_count=1)

    class NoopOracleClient:
        async def save_evaluation_artifact(self, artifact: dict[str, Any]) -> str:
            return "eval-1"

    monkeypatch.setattr(EvaluationRunner, "run", fake_run)
    monkeypatch.setattr("app.api.routes.evaluation.OracleClient", NoopOracleClient)

    response = client.post(
        "/api/evaluation/run",
        json={
            "cases": [
                {
                    "id": "c",
                    "query": "承認条件",
                    "expected_content_kind": "table",
                    "expected_section_paths": ["経費 > 承認"],
                }
            ],
            "mode": "keyword",
            "rerank_top_n": 3,
        },
    )

    assert response.status_code == 200
    assert "mode" not in captured
    assert captured["top_k"] == 20


def test_evaluation_compare_api_rejects_duplicate_experiment_ids() -> None:
    response = client.post(
        "/api/evaluation/compare",
        json={
            "cases": [{"id": "case-1", "query": "承認条件"}],
            "experiments": [{"id": "same", "top_k": 5}, {"id": "same", "top_k": 10}],
        },
    )

    assert response.status_code == 422
    body = response.json()
    assert any("experiment id が重複" in message for message in body["error_messages"])


@pytest.mark.parametrize(
    "path",
    ["/api/evaluation/jobs/run", "/api/evaluation/jobs/compare", "/api/evaluation/run"],
    ids=["jobs-run", "jobs-compare", "run"],
)
@pytest.mark.parametrize(
    ("cases", "expected"),
    [
        (
            [{"id": "same", "query": "A"}, {"id": " same ", "query": "B"}],
            "評価ケースの id が重複しています: same",
        ),
        ([{"id": "  ", "query": "A"}], "評価ケースの id を入力してください。"),
        ([{"id": "x" * 201, "query": "A"}], "cases[0].id: 200 文字以内で入力してください。"),
    ],
    ids=["duplicate", "blank", "too-long"],
)
def test_evaluation_api_rejects_invalid_case_ids(
    path: str, cases: list[dict[str, str]], expected: str
) -> None:
    """結果のケースを id で区別できるよう、重複・空・長すぎる id を投入前に拒否する（#977）。"""
    body: dict[str, Any] = {"cases": cases}
    if path.endswith("compare"):
        body["experiments"] = [{"id": "default"}]
    response = client.post(path, json=body)

    assert response.status_code == 422
    assert any(expected in message for message in response.json()["error_messages"])


@pytest.mark.parametrize(
    "experiment",
    [
        {"id": "removed-mode", "mode": "keyword"},
        {"id": "removed-override", "rag_overrides": {"context_window_chars": 4096}},
        {"id": "bad-strategy", "rag_overrides": {"query_strategy": "unknown"}},
        {"id": "bad-neighbors", "rag_overrides": {"neighbor_child_count": 21}},
    ],
    ids=["removed-mode", "removed-override", "bad-strategy", "bad-neighbors"],
)
def test_evaluation_compare_api_rejects_invalid_experiments(experiment: dict[str, Any]) -> None:
    """回答エンジンが使わない設定と、削除した上書きのキーは受け付けない。"""
    response = client.post(
        "/api/evaluation/compare",
        json={"cases": [{"id": "case-1", "query": "承認条件"}], "experiments": [experiment]},
    )

    assert response.status_code == 422
    assert response.json()["error_messages"]


def test_evaluation_api_rejects_blank_case_query() -> None:
    response = client.post(
        "/api/evaluation/run",
        json={"cases": [{"id": "blank-query", "query": "   "}], "top_k": 5},
    )

    assert response.status_code == 422
    assert response.json()["error_messages"]


def test_evaluation_api_persists_redacted_artifact(monkeypatch: MonkeyPatch) -> None:
    """評価 API は query・期待語・標準回答の原文を除いた artifact summary を保存する。"""
    artifacts: list[dict[str, Any]] = []

    class FakeEvaluationRunner:
        async def run(self, **kwargs: object) -> EvaluationMetrics:
            del kwargs
            return EvaluationMetrics(case_count=1, passed=True)

    class FakeOracleClient:
        async def save_evaluation_artifact(self, artifact: dict[str, Any]) -> str:
            artifacts.append(artifact)
            return "eval-1"

    monkeypatch.setattr("app.api.routes.evaluation.EvaluationRunner", FakeEvaluationRunner)
    monkeypatch.setattr("app.api.routes.evaluation.OracleClient", FakeOracleClient)

    response = client.post(
        "/api/evaluation/run",
        json={
            "cases": [
                {
                    "id": "secret-case",
                    "query": "社外秘キーワード ABC-123 の承認条件",
                    "relevant_document_ids": ["doc-1"],
                    "expected_answer_keywords": ["ABC-123"],
                    "standard_answer": "承認者は XYZ-999 部長です。",
                }
            ],
            "top_k": 1,
            "knowledge_base_ids": ["kb-1"],
        },
    )

    assert response.status_code == 200
    assert len(artifacts) == 1
    artifact_text = str(artifacts[0])
    assert "社外秘キーワード" not in artifact_text
    assert "ABC-123" not in artifact_text
    assert "XYZ-999" not in artifact_text
    case_summary = artifacts[0]["request_summary"]["cases"][0]
    assert case_summary["query_hash"]
    assert case_summary["standard_answer_hash"]
    assert artifacts[0]["knowledge_base_ids"] == ["kb-1"]


def test_evaluation_api_limits_whole_run_by_time_budget(monkeypatch: MonkeyPatch) -> None:
    """評価 API は、評価全体を画面・Nginx の待ちより短い上限（600 秒）で打ち切らせる（#383）。"""
    from app.api.routes import evaluation as evaluation_route

    observed: dict[str, object] = {}

    class CapturingEvaluationRunner:
        async def run(self, **kwargs: object) -> EvaluationMetrics:
            observed["run"] = kwargs.get("time_budget_seconds")
            return EvaluationMetrics(case_count=0)

        async def compare(self, **kwargs: object) -> EvaluationCompareResponse:
            observed["compare"] = kwargs.get("time_budget_seconds")
            return EvaluationCompareResponse(
                ranking_metric="mrr", best_experiment_id=None, results=[]
            )

    class NoopOracleClient:
        async def save_evaluation_artifact(self, artifact: dict[str, Any]) -> str:
            del artifact
            return "eval-1"

    monkeypatch.setattr("app.api.routes.evaluation.EvaluationRunner", CapturingEvaluationRunner)
    monkeypatch.setattr("app.api.routes.evaluation.OracleClient", NoopOracleClient)
    case = {"id": "case-1", "query": "承認条件は？", "relevant_document_ids": ["doc-1"]}

    run_response = client.post("/api/evaluation/run", json={"cases": [case]})
    compare_response = client.post(
        "/api/evaluation/compare",
        json={"cases": [case], "experiments": [{"id": "hybrid"}]},
    )

    assert run_response.status_code == 200
    assert compare_response.status_code == 200
    assert evaluation_route.EVALUATION_RUN_TIMEOUT_SECONDS == OCI_ENTERPRISE_AI_TIMEOUT_MAX_SECONDS
    assert observed == {
        "run": evaluation_route.EVALUATION_RUN_TIMEOUT_SECONDS,
        "compare": evaluation_route.EVALUATION_RUN_TIMEOUT_SECONDS,
    }


@pytest.mark.usefixtures("oracle_db")
def test_evaluation_api_runs_against_local_pipeline(monkeypatch: MonkeyPatch) -> None:
    """API 経由でも、回答エンジンの回答の記録から指標を返す(実 Oracle。LLM はスタブ)。"""
    from app.rag.answer_engine import AnswerEngine, AnswerOutcome

    async def refuse(
        self: AnswerEngine, request: SearchRequest, *, step_callback: object = None
    ) -> AnswerOutcome:
        del self, request, step_callback
        return AnswerOutcome(
            answer="資料からは確認できませんでした。",
            citations=[],
            diagnostics={"insufficient_reason": "該当する資料がありません。"},
            context_text="",
            evaluation_input=None,
        )

    monkeypatch.setattr(AnswerEngine, "run", refuse)
    response = client.post(
        "/api/evaluation/run",
        json={
            "cases": [{"id": "empty-store", "query": "存在しない社内規程"}],
            "top_k": 5,
            "knowledge_base_ids": ["kb-eval-empty-isolated"],
            "suite": "strict",
        },
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["case_count"] == 1
    assert data["evaluation_suite"] == "strict"
    # 答えるべきでない質問に答えなかったので合格(#301)。
    assert data["refusal_accuracy"] == 1.0
    assert data["context_recall"] is None
    assert data["passed"] is True
    assert data["threshold_failures"] == []
    result = data["case_results"][0]
    assert result["abstained"] is True
    assert result["failure_reasons"] == []


def _eval_run_body() -> dict[str, Any]:
    return {
        "cases": [
            {
                "id": "c1",
                "query": "承認条件は?",
                "relevant_document_ids": ["doc-1"],
                "expected_answer_keywords": ["承認"],
            }
        ]
    }


def test_run_evaluation_applies_suite_thresholds_when_request_omits(
    monkeypatch: MonkeyPatch,
) -> None:
    """request に thresholds 未指定なら設定の基準の閾値を適用し、基準を stamp する。"""
    captured: dict[str, Any] = {}

    async def fake_run(self: EvaluationRunner, **kwargs: Any) -> EvaluationMetrics:
        captured["thresholds"] = kwargs.get("thresholds")
        return EvaluationMetrics(case_count=1)

    monkeypatch.setattr(EvaluationRunner, "run", fake_run)
    monkeypatch.setattr(get_settings(), "rag_evaluation_suite", "strict")

    resp = client.post("/api/evaluation/run", json=_eval_run_body())

    assert resp.status_code == 200
    assert resp.json()["data"]["evaluation_suite"] == "strict"
    assert captured["thresholds"].refusal_accuracy == 1.0


def test_run_evaluation_request_thresholds_take_precedence_over_suite(
    monkeypatch: MonkeyPatch,
) -> None:
    """request の thresholds は基準より優先される(空の thresholds で判定を行わない)。"""
    captured: dict[str, Any] = {}

    async def fake_run(self: EvaluationRunner, **kwargs: Any) -> EvaluationMetrics:
        captured["thresholds"] = kwargs.get("thresholds")
        return EvaluationMetrics(case_count=1)

    monkeypatch.setattr(EvaluationRunner, "run", fake_run)

    body = {**_eval_run_body(), "thresholds": {"mrr": 0.42}, "suite": "strict"}
    resp = client.post("/api/evaluation/run", json=body)

    assert resp.status_code == 200
    assert resp.json()["data"]["evaluation_suite"] == "strict"
    assert captured["thresholds"].mrr == 0.42
    assert captured["thresholds"].refusal_accuracy is None

    resp = client.post("/api/evaluation/run", json={**_eval_run_body(), "thresholds": {}})
    assert resp.status_code == 200
    assert captured["thresholds"].model_dump(exclude_none=True) == {}


def test_run_evaluation_rejects_removed_suite_name() -> None:
    """削除した基準の名前は request では受け付けない(設定値は後継へ寄せる)。"""
    resp = client.post("/api/evaluation/run", json={**_eval_run_body(), "suite": "strict_ci"})

    assert resp.status_code == 422


def test_run_evaluation_ignores_kb_legacy_evaluation_suite_when_request_omits(
    monkeypatch: MonkeyPatch,
) -> None:
    """単一 KB 指定時も KB legacy evaluation_suite は使わずグローバル既定が効く。"""
    from app.api.routes import evaluation as evaluation_route

    async def fake_run(self: EvaluationRunner, **kwargs: Any) -> EvaluationMetrics:
        return EvaluationMetrics(case_count=1)

    class FakeOracleClient:
        async def get_knowledge_base(self, knowledge_base_id: str) -> None:
            raise AssertionError("KB legacy query は評価の基準の解決で参照しない")

        async def save_evaluation_artifact(self, artifact: dict[str, Any]) -> str:
            return "eval-1"

    monkeypatch.setattr(EvaluationRunner, "run", fake_run)
    monkeypatch.setattr(evaluation_route, "OracleClient", FakeOracleClient)
    monkeypatch.setattr(get_settings(), "rag_evaluation_suite", "standard")

    body = {**_eval_run_body(), "knowledge_base_ids": ["kb-1"]}
    resp = client.post("/api/evaluation/run", json=body)

    assert resp.status_code == 200
    assert resp.json()["data"]["evaluation_suite"] == "standard"


def test_compare_evaluation_stamps_resolved_suite_on_each_experiment(
    monkeypatch: MonkeyPatch,
) -> None:
    """compare でも確定した評価の基準を各 experiment の metrics に残す(#277)。"""
    from app.api.routes import evaluation as evaluation_route

    captured: dict[str, Any] = {}

    async def fake_run(self: EvaluationRunner, **kwargs: Any) -> EvaluationMetrics:
        captured["thresholds"] = kwargs.get("thresholds")
        return EvaluationMetrics(case_count=1)

    class FakeOracleClient:
        async def save_evaluation_artifact(self, artifact: dict[str, Any]) -> str:
            return "eval-1"

    monkeypatch.setattr(EvaluationRunner, "run", fake_run)
    monkeypatch.setattr(evaluation_route, "OracleClient", FakeOracleClient)
    monkeypatch.setattr(get_settings(), "rag_evaluation_suite", "strict")

    body = {
        "cases": _eval_run_body()["cases"],
        "experiments": [{"id": "exp-a"}, {"id": "exp-b", "top_k": 5}],
    }
    resp = client.post("/api/evaluation/compare", json=body)

    assert resp.status_code == 200
    results = resp.json()["data"]["results"]
    assert [result["metrics"]["evaluation_suite"] for result in results] == ["strict", "strict"]
    assert captured["thresholds"].claim_support_rate == 1.0


def test_threshold_skips_unmeasured_metrics() -> None:
    """測れなかった指標(None)の閾値は判定しない。"""
    from app.rag.evaluation import _threshold_failures

    failures = _threshold_failures(
        EvaluationThresholds(answer_pass_rate=0.8, mrr=0.5),
        {"answer_pass_rate": None, "mrr": 0.4},
    )

    assert [(failure.metric, failure.actual) for failure in failures] == [("mrr", 0.4)]


# ---- 業務支援の採点（#1231） ------------------------------------------------------------


def test_handling_helpers_score_outcome_steps_forbidden_and_conditions() -> None:
    from app.rag.evaluation_handling import (
        condition_coverage,
        forbidden_hits,
        observed_outcome,
        step_order_score,
    )

    assert observed_outcome({"outcome": "needs_human"}, abstained=True).source == "explicit"
    assert observed_outcome({}, abstained=True).outcome == "insufficient_evidence"
    assert observed_outcome({"external_data_required": True}, abstained=False).outcome == (
        "needs_environment_data"
    )
    assert observed_outcome({"needs_human_review": True}, abstained=False).outcome == "conditional"
    assert observed_outcome(None, abstained=False).outcome == "answered"
    # 未知の対応の値は推定に戻す。
    assert observed_outcome({"outcome": "other"}, abstained=False).source == "inferred"

    answer = "1. アカウントを登録する。2. 権限を付与する。3. 通知を設定する。"
    assert step_order_score(answer, ["登録", "権限", "通知"]) == (1.0, [])
    score, missing = step_order_score(answer, ["通知", "登録", "承認"])
    assert (round(score, 4), missing) == (0.3333, ["承認"])
    assert step_order_score(answer, []) == (1.0, [])
    assert forbidden_hits("グループ　に付与してください", ["グループに付与", "削除"]) == [
        "グループに付与"
    ]
    assert condition_coverage(answer, ["権限", "対象の利用者"]) == (0.5, ["対象の利用者"])


async def test_business_support_cases_score_handling_metrics_and_reasons() -> None:
    cases = [
        EvaluationCase(
            id="steps",
            query="承認条件",
            expected_answer_keywords=["承認条件"],
            category="document_answerable",
            expected_outcomes=["answered"],
            expected_steps=["承認条件", "120000"],
            forbidden_phrases=["グループに付与"],
            required_conditions=["承認条件", "対象の部署"],
        ),
        EvaluationCase(
            id="clarify",
            query="承認条件",
            category="clarification_required",
            expected_outcomes=["needs_clarification", "conditional"],
        ),
    ]
    metrics = await EvaluationRunner(pipeline=StubPipeline()).run(cases=cases, top_k=5)

    first, second = metrics.case_results
    assert (first.observed_outcome, first.outcome_source) == ("answered", "inferred")
    assert first.handling_correct is True
    assert first.step_order_score == 1.0
    assert first.forbidden_checked is True and first.forbidden_hits == []
    assert (first.condition_coverage, first.missing_conditions) == (0.5, ["対象の部署"])
    assert "condition_missing" in first.failure_reasons
    assert second.handling_correct is False
    assert "unexpected_handling" in second.failure_reasons
    assert second.step_order_score is None and second.forbidden_checked is False
    assert metrics.handling_accuracy == 0.5
    assert metrics.safe_answer_rate == 1.0
    assert metrics.metric_case_counts["safe_answer_rate"] == 1
    assert metrics.category_breakdown["clarification_required"].handling_correct_rate == 0.0


# ---- 検索・回答プロファイルを指定した評価（#1249） ------------------------------------------


async def test_profile_evaluation_resolves_each_case_through_the_profile() -> None:
    seen: list[tuple[str | None, str]] = []

    async def resolver(request: Any, settings: Any, guide_context: Any) -> tuple[Any, Any]:
        seen.append((request.search_answer_profile_id, request.query))
        return request.model_copy(update={"knowledge_base_ids": ["kb-profile"]}), settings

    pipeline = StubPipeline()
    runner = EvaluationRunner(pipeline=pipeline, profile_resolver=resolver)
    cases = [EvaluationCase(id="a", query="承認条件"), EvaluationCase(id="b", query="期限")]
    metrics = await runner.run(cases=cases, top_k=5, search_answer_profile_id="bv-1")
    assert seen == [("bv-1", "承認条件"), ("bv-1", "期限")]
    assert metrics.error_count == 0

    # 指定しなければ解決しない（全体の既定。#301）。
    seen.clear()
    await runner.run(cases=cases, top_k=5)
    assert seen == []


async def test_profile_resolution_failure_fails_only_that_case() -> None:
    from fastapi import HTTPException

    async def resolver(request: Any, settings: Any, guide_context: Any) -> tuple[Any, Any]:
        raise HTTPException(status_code=409, detail="アーカイブ済み")

    runner = EvaluationRunner(pipeline=StubPipeline(), profile_resolver=resolver)
    metrics = await runner.run(
        cases=[EvaluationCase(id="a", query="承認条件")], top_k=5, search_answer_profile_id="bv-x"
    )
    assert metrics.error_count == 1
    assert metrics.case_results[0].status == "error"


# ---- 確認の質問・引き継ぎは拒答・検索の取りこぼしではない（#1259） ---------------------------


def _response_with_outcome(outcome: str | None, *, citations: bool) -> Any:
    from app.schemas.search import SearchDiagnostics, SearchResponse

    answer: dict[str, Any] = {"insufficient_reason": ""}
    if outcome is not None:
        answer["outcome"] = outcome
    return SearchResponse(
        answer="権限は個別の利用者とグループのどちらに付けますか？",
        citations=[RetrievedChunk(document_id="doc-1", chunk_id="doc-1:0", text="本文", score=1.0)]
        if citations
        else [],
        trace_id="t",
        elapsed_ms=1.0,
        diagnostics=SearchDiagnostics(answer=answer),
    )


def test_clarification_is_not_a_refusal_or_retrieval_miss() -> None:
    from app.rag.evaluation import _case_result, is_abstained

    case = EvaluationCase(id="c", query="権限を付与したい", relevant_document_ids=["doc-1"])
    clarify = _response_with_outcome("needs_clarification", citations=False)
    assert is_abstained(clarify) is False
    result = _case_result(case, clarify, None)
    assert (result.context_recall, result.refusal_correct) == (None, True)
    assert "retrieval_miss" not in result.failure_reasons
    assert "unexpected_refusal" not in result.failure_reasons

    assert is_abstained(_response_with_outcome("insufficient_evidence", citations=False)) is True
    assert is_abstained(_response_with_outcome("answered", citations=True)) is False
    # 対応を持たない古い記録は、引用が無ければ拒答（今までどおり）。
    assert is_abstained(_response_with_outcome(None, citations=False)) is True


# ---- 既知の条件・往復・別解・区分・必要な根拠（#1284） --------------------------------------


def test_case_schema_accepts_conditions_turns_alternatives_split_and_evidence() -> None:
    case = EvaluationCase.model_validate(
        {
            "id": "mt",
            "query": "アクセス権限を付与したい",
            "split": "holdout",
            "conditions": {" target ": " 個別 ", "empty": " "},
            "expected_steps": ["詳細画面", "付与"],
            "acceptable_alternatives": [["権限タブ", "付与"], [" ", ""]],
            "required_evidence": [
                {"id": "e1", "document_id": "file:manual.pdf", "text": " 個別の利用者 "},
                {"id": "e2", "document_id": " ", "text": "付与"},
            ],
            "turns": [
                {
                    "reply": "  個別の利用者です ",
                    "conditions": {"approved": "はい"},
                    "expected_outcomes": ["answered"],
                }
            ],
        }
    )
    assert case.split == "holdout"
    assert case.conditions == {"target": "個別"}
    assert case.acceptable_alternatives == [["権限タブ", "付与"]]
    assert case.turns[0].reply == "個別の利用者です"
    assert case.required_evidence[0].text == "個別の利用者"
    assert case.required_evidence[1].document_id is None
    assert case.all_condition_ids() == {"target", "approved"}
    # 根拠だけのケースも答えるべき質問とみなす。
    assert EvaluationCase(
        id="e", query="q", required_evidence=[{"id": "e1", "text": "x"}]
    ).expects_answer
    # 既存の評価セット（新しい欄なし）はそのまま読める。
    legacy = EvaluationCase(id="old", query="q")
    assert (legacy.split, legacy.conditions, legacy.turns, legacy.required_evidence) == (
        None,
        {},
        [],
        [],
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"split": "train"},
        {"acceptable_alternatives": [["付与"]]},
        {"turns": [{"reply": "はい", "unknown": 1}]},
        {"turns": [{"reply": " "}]},
        {"turns": [{"reply": "はい"}] * 6},
        {"required_evidence": [{"id": "e", "text": "a"}, {"id": "e", "text": "b"}]},
        {
            "conditions": {f"c{i}": "v" for i in range(20)},
            "turns": [{"reply": "r", "conditions": {f"t{i}": "v" for i in range(11)}}],
        },
    ],
    ids=[
        "unknown-split",
        "alternatives-without-steps",
        "turn-extra-field",
        "blank-reply",
        "too-many-turns",
        "duplicate-evidence",
        "too-many-conditions",
    ],
)
def test_case_schema_rejects_invalid_contract(payload: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        EvaluationCase.model_validate({"id": "x", "query": "q", **payload})


def test_nfkc_matching_ignores_width_case_and_whitespace() -> None:
    from app.rag.evaluation_handling import (
        best_step_order_score,
        contains_normalized,
        forbidden_hits,
    )

    answer = "ＡＵＴＨ．ＬＯＧ を確認し、\n すぐに　削除して ください。"
    assert forbidden_hits(answer, ["すぐに削除してください", "auth.log", "承認は不要"]) == [
        "すぐに削除してください",
        "auth.log",
    ]
    assert contains_normalized("最長 ３０ 日です", "最長30日")
    assert not contains_normalized("本文", " ")
    # 別解のうち、いちばん点の高い列で採点する（同点なら期待する手順）。
    text = "再ログインしてください。"
    assert best_step_order_score(text, [["ログアウト", "ログインし直"], ["再ログイン"]]) == (
        1.0,
        [],
    )
    assert best_step_order_score(text, [["ログアウト"], ["サインアウト"]]) == (0.0, ["ログアウト"])
    assert best_step_order_score(text, []) == (1.0, [])


def _turn_response(
    request: SearchRequest,
    trace_id: str,
    *,
    answer: str,
    outcome: str,
    citations: Sequence[RetrievedChunk] = (),
    clarifications: Sequence[str] = (),
) -> SearchResponse:
    details: dict[str, Any] = {"outcome": outcome, "insufficient_reason": ""}
    if clarifications:
        details["envelope"] = {
            "clarifications": [{"condition_id": item} for item in clarifications]
        }
    return SearchResponse(
        answer=answer,
        citations=list(citations),
        trace_id=trace_id,
        elapsed_ms=10.0,
        diagnostics=SearchDiagnostics(answer=details),
    )


class ConversationPipeline:
    """最初の質問には確認の質問、返答には根拠付きの回答を返す（会話の履歴と条件を記録する）。"""

    def __init__(self, *, reask: bool = False, fail_on_turn: int | None = None) -> None:
        self.reask = reask
        self.fail_on_turn = fail_on_turn
        self.calls: list[dict[str, Any]] = []

    async def run(
        self,
        request: SearchRequest,
        trace_id: str | None = None,
        progress_callback: SearchStageProgressCallback | None = None,
        *,
        history: Sequence[Any] | None = None,
    ) -> SearchResponse:
        assert trace_id
        turn = len(history or []) // 2
        self.calls.append(
            {
                "query": request.query,
                "conditions": dict(request.conditions),
                "history": [(item.role, item.content) for item in history or []],
                "trace_id": trace_id,
            }
        )
        if turn == self.fail_on_turn:
            raise RuntimeError("boom")
        if turn == 0:
            return _turn_response(
                request,
                trace_id,
                answer="権限は個別の利用者とグループのどちらに付けますか？",
                outcome="needs_clarification",
                clarifications=["target"],
            )
        return _turn_response(
            request,
            trace_id,
            answer=(
                "どちらに付けますか？"
                if self.reask
                else "利用者の詳細画面の「権限」タブで付与します。"
            ),
            outcome="needs_clarification" if self.reask else "answered",
            clarifications=["target"] if self.reask else (),
            citations=[
                RetrievedChunk(
                    document_id="doc-manual",
                    chunk_id="doc-manual:3",
                    text="個別の利用者に付与する場合: 利用者の詳細画面の「権限」タブ",
                    score=1.0,
                )
            ],
        )


def _multi_turn_case(**overrides: Any) -> EvaluationCase:
    payload: dict[str, Any] = {
        "id": "mt-grant",
        "query": "アクセス権限を付与したい",
        "category": "clarification_required",
        "split": "dev",
        "relevant_document_ids": ["doc-manual"],
        "conditions": {"scope": "本番"},
        "expected_outcomes": ["needs_clarification"],
        "required_conditions": ["個別", "グループ"],
        "required_evidence": [
            {"id": "grant-individual", "document_id": "doc-manual", "text": "個別の利用者に付与"},
            {"id": "other-doc", "document_id": "doc-other", "text": "個別の利用者"},
        ],
        "turns": [
            {
                "reply": "個別の利用者に付けます",
                "conditions": {"target": "個別"},
                "expected_outcomes": ["answered", "conditional"],
                "expected_steps": ["権限タブ", "付与"],
                "acceptable_alternatives": [["詳細画面", "付与"]],
                "forbidden_phrases": ["グループに付与してください"],
            }
        ],
    }
    payload.update(overrides)
    return EvaluationCase.model_validate(payload)


async def test_runner_sends_conditions_and_runs_turns_in_order_with_history() -> None:
    pipeline = ConversationPipeline()
    metrics = await EvaluationRunner(pipeline=pipeline).run(cases=[_multi_turn_case()], top_k=5)

    first, second = pipeline.calls
    # 最初の質問は既知の条件だけ、返答は前の往復の履歴と、足した条件で送る。
    assert (first["query"], first["conditions"], first["history"]) == (
        "アクセス権限を付与したい",
        {"scope": "本番"},
        [],
    )
    assert second["query"] == "個別の利用者に付けます"
    assert second["conditions"] == {"scope": "本番", "target": "個別"}
    assert second["history"] == [
        ("USER", "アクセス権限を付与したい"),
        ("ASSISTANT", "権限は個別の利用者とグループのどちらに付けますか？"),
    ]
    assert first["trace_id"] != second["trace_id"]

    result = metrics.case_results[0]
    assert result.status == "success"
    assert [turn.turn for turn in result.turn_results] == [0, 1]
    clarify, answer = result.turn_results
    assert (clarify.observed_outcome, clarify.handling_correct) == ("needs_clarification", True)
    assert clarify.condition_coverage == 1.0
    # 最初の質問で聞いた target は、まだ渡していない条件なので聞き直しではない。
    assert clarify.reasked_conditions == []
    # 「権限タブ」は「「権限」タブ」と一致しないが、別解（詳細画面 → 付与）で満点になる。
    assert (answer.handling_correct, answer.step_order_score) == (True, 1.0)
    assert answer.forbidden_checked and answer.forbidden_hits == []
    # ケースの採点: 対応はすべての往復で正しい、検索・根拠は最後の回答で測る。
    assert result.observed_outcome == "answered"
    assert result.handling_correct is True
    assert result.trace_id == second["trace_id"]
    assert result.context_recall == 1.0
    assert result.elapsed_ms == 20.0
    assert (result.evidence_recall, result.missing_evidence) == (0.5, ["other-doc"])
    assert result.failure_reasons == ["evidence_miss"]
    assert metrics.required_evidence_recall == 0.5
    assert metrics.metric_case_counts["required_evidence_recall"] == 1
    assert metrics.handling_accuracy == 1.0


async def test_runner_flags_reasked_known_condition() -> None:
    metrics = await EvaluationRunner(pipeline=ConversationPipeline(reask=True)).run(
        cases=[_multi_turn_case()], top_k=5
    )
    result = metrics.case_results[0]
    assert result.turn_results[1].reasked_conditions == ["target"]
    assert result.reasked_conditions == ["target"]
    assert result.handling_correct is False
    assert {"known_condition_reasked", "unexpected_handling"} <= set(result.failure_reasons)


async def test_runner_turn_failure_fails_the_case() -> None:
    metrics = await EvaluationRunner(pipeline=ConversationPipeline(fail_on_turn=1)).run(
        cases=[_multi_turn_case()], top_k=5
    )
    assert metrics.error_count == 1
    result = metrics.case_results[0]
    assert (result.status, result.split, result.failure_reasons) == ("error", "dev", ["case_error"])


async def test_profile_evaluation_passes_prior_questions_as_guide_context() -> None:
    seen: list[tuple[str, list[str], dict[str, str]]] = []

    async def resolver(request: Any, settings: Any, guide_context: Any) -> tuple[Any, Any]:
        seen.append((request.query, list(guide_context), dict(request.conditions)))
        return request, settings

    runner = EvaluationRunner(pipeline=ConversationPipeline(), profile_resolver=resolver)
    await runner.run(cases=[_multi_turn_case()], top_k=5, search_answer_profile_id="bv-1")
    assert seen == [
        ("アクセス権限を付与したい", [], {"scope": "本番"}),
        (
            "個別の利用者に付けます",
            ["アクセス権限を付与したい"],
            {"scope": "本番", "target": "個別"},
        ),
    ]


async def test_split_breakdown_reports_metrics_per_split() -> None:
    cases = [
        EvaluationCase(id="d1", query="承認条件", split="dev", expected_answer_keywords=["120000"]),
        EvaluationCase(id="d2", query="承認条件", split="dev", expected_answer_keywords=["999"]),
        EvaluationCase(
            id="h1", query="承認条件", split="holdout", expected_answer_keywords=["120000"]
        ),
        EvaluationCase(id="u1", query="承認条件", expected_answer_keywords=["999"]),
    ]
    metrics = await EvaluationRunner(pipeline=StubPipeline()).run(cases=cases, top_k=5)

    assert set(metrics.split_breakdown) == {"dev", "holdout", "unassigned"}
    dev = metrics.split_breakdown["dev"]
    assert (dev.case_count, dev.error_count) == (2, 0)
    assert dev.metrics["answer_keyword_hit_rate"] == 0.5
    assert dev.metric_case_counts["answer_keyword_hit_rate"] == 2
    assert dev.failure_reason_counts == {"answer_keyword_miss": 1}
    assert metrics.split_breakdown["holdout"].metrics["answer_keyword_hit_rate"] == 1.0
    assert metrics.split_breakdown["unassigned"].metrics["answer_keyword_hit_rate"] == 0.0
    assert [result.split for result in metrics.case_results] == ["dev", "dev", "holdout", None]
    # 区分の無い評価セットでは内訳を出さない。
    plain = await EvaluationRunner(pipeline=StubPipeline()).run(
        cases=[EvaluationCase(id="p", query="承認条件")], top_k=5
    )
    assert plain.split_breakdown == {}


def test_trend_includes_split_breakdown_and_evidence_recall() -> None:
    from app.rag.evaluation_cli import _metrics_trend
    from app.schemas.evaluation import EvaluationSplitSummary

    trend = _metrics_trend(
        EvaluationMetrics(
            case_count=1,
            required_evidence_recall=0.5,
            split_breakdown={"holdout": EvaluationSplitSummary(case_count=1, metrics={"mrr": 1.0})},
        )
    )
    assert trend["required_evidence_recall"] == 0.5
    assert trend["split_breakdown"]["holdout"]["metrics"] == {"mrr": 1.0}
