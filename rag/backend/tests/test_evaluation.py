"""RAG 評価ランナーのテスト。"""

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any, cast

import pytest
from pytest import LogCaptureFixture, MonkeyPatch

from app.config import Settings, get_settings
from app.main import app
from app.rag.evaluation import EVALUATION_CASE_ERROR_MESSAGE, EvaluationRunner
from app.schemas.evaluation import (
    EvaluationCase,
    EvaluationExperiment,
    EvaluationMetrics,
    EvaluationRagOverrides,
    EvaluationThresholds,
)
from app.schemas.search import (
    RetrievedChunk,
    SearchDiagnostics,
    SearchMode,
    SearchRequest,
    SearchResponse,
)
from tests.support import AsgiTestClient

client = AsgiTestClient(app)


class StubPipeline:
    """評価ランナー用の固定レスポンス pipeline。"""

    def __init__(self) -> None:
        self.requests: list[SearchRequest] = []
        self.trace_ids: list[str] = []

    async def run(
        self,
        request: SearchRequest,
        trace_id: str | None = None,
    ) -> SearchResponse:
        assert trace_id
        self.requests.append(request)
        self.trace_ids.append(trace_id)
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
                        "bbox": "[0.1, 0.2, 0.4, 0.3]",
                        "content_kind": "text",
                        "section_title": "承認",
                        "section_path": "経費申請 > 承認",
                    },
                )
            ],
            trace_id=trace_id,
            guardrail_warnings=[],
            elapsed_ms=1.0,
            diagnostics=SearchDiagnostics(
                mode=request.mode.value,
                top_k=request.top_k,
                rerank_top_n=request.rerank_top_n,
                retrieved_count=1,
                reranked_count=1,
                citation_count=1,
            ),
        )


class EmptyQualitySource:
    """評価 runner の補助的な取込品質サマリを空で返す。"""

    async def list_document_extractions(self) -> list[dict[str, object]]:
        return []


async def test_evaluation_runner_computes_metrics() -> None:
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
        rerank_top_n=3,
        mode=SearchMode.KEYWORD,
        filters={"status": "indexed"},
    )
    assert metrics.evaluated_k == 3
    assert metrics.precision_at_k == 0.3333
    assert metrics.recall_at_k == 1.0
    assert metrics.mrr == 1.0
    assert metrics.answer_keyword_hit_rate == 1.0
    assert metrics.groundedness_pass_rate == 1.0
    assert metrics.faithfulness == 1.0
    assert metrics.context_precision == 1.0
    assert metrics.context_recall == 1.0
    assert metrics.response_relevancy >= 0.8
    assert metrics.noise_sensitivity == 1.0
    assert metrics.citation_traceability_coverage == 1.0
    assert metrics.bbox_citation_coverage == 1.0
    assert metrics.element_lineage_coverage == 1.0
    assert metrics.content_kind_hit_rate == 1.0
    assert metrics.section_coverage == 1.0
    assert metrics.passed is True
    assert metrics.threshold_failures == []
    assert metrics.failure_reason_counts == {}
    assert len(metrics.case_results) == 1
    result = metrics.case_results[0]
    assert result.case_id == "case-1"
    assert result.trace_id == pipeline.trace_ids[0]
    assert result.status == "success"
    assert result.retrieved_document_ids == ["doc-1"]
    assert result.relevant_document_ids == ["doc-1"]
    assert result.hit_document_ids == ["doc-1"]
    assert result.precision_at_k == 0.3333
    assert result.recall_at_k == 1.0
    assert result.reciprocal_rank == 1.0
    assert result.answer_keyword_hit is True
    assert result.groundedness_passed is True
    assert result.groundedness_score == 1.0
    assert result.grounding_overlap_count >= 1
    assert result.grounding_answer_feature_count >= 1
    assert result.faithfulness == 1.0
    assert result.context_precision == 1.0
    assert result.context_recall == 1.0
    assert result.response_relevancy >= 0.8
    assert result.noise_sensitivity == 1.0
    assert result.citation_traceability_coverage == 1.0
    assert result.bbox_citation_coverage == 1.0
    assert result.element_lineage_coverage == 1.0
    assert result.content_kind_hit_rate == 1.0
    assert result.section_coverage == 1.0
    assert result.guardrail_warnings == []
    assert result.failure_reasons == []
    assert result.diagnostics.top_k == 5
    assert result.diagnostics.rerank_top_n == 3
    assert result.diagnostics.retrieved_count == 1
    assert result.diagnostics.citation_count == 1
    assert pipeline.requests[0].mode == SearchMode.KEYWORD
    assert pipeline.requests[0].filters == {"status": "INDEXED"}


async def test_evaluation_runner_scores_content_kind_and_section_expectations() -> None:
    """document-level hit でも content kind / section が違えば評価で検知する。"""
    pipeline = StubPipeline()
    runner = EvaluationRunner(pipeline=pipeline)
    metrics = await runner.run(
        cases=[
            EvaluationCase(
                id="case-section-hit",
                query="承認条件",
                relevant_document_ids=["doc-1"],
                expected_answer_keywords=["120000"],
                expected_content_kind="text",
                expected_section_paths=["経費申請 > 承認"],
            ),
            EvaluationCase(
                id="case-section-miss",
                query="料金表",
                relevant_document_ids=["doc-1"],
                expected_answer_keywords=["120000"],
                expected_content_kind="table",
                expected_section_paths=["経費申請 > 料金表"],
            ),
        ],
        top_k=5,
        rerank_top_n=3,
        thresholds=EvaluationThresholds(
            content_kind_hit_rate=0.8,
            section_coverage=0.8,
        ),
    )

    assert metrics.content_kind_hit_rate == 0.5
    assert metrics.section_coverage == 0.5
    assert metrics.passed is False
    assert metrics.failure_reason_counts["content_kind_miss"] == 1
    assert metrics.failure_reason_counts["section_miss"] == 1
    threshold_failures = {failure.metric for failure in metrics.threshold_failures}
    assert threshold_failures == {"content_kind_hit_rate", "section_coverage"}
    assert metrics.case_results[0].content_kind_hit_rate == 1.0
    assert metrics.case_results[0].section_coverage == 1.0
    assert metrics.case_results[1].content_kind_hit_rate == 0.0
    assert metrics.case_results[1].section_coverage == 0.0
    assert "content_kind_miss" in metrics.case_results[1].failure_reasons
    assert "section_miss" in metrics.case_results[1].failure_reasons


async def test_evaluation_runner_passes_knowledge_base_scope_to_search_request() -> None:
    """評価 runner は KB スコープを SearchRequest へ伝播する。"""
    pipeline = StubPipeline()
    runner = EvaluationRunner(pipeline=pipeline)

    await runner.run(
        cases=[EvaluationCase(id="case-1", query="承認条件")],
        top_k=5,
        rerank_top_n=3,
        knowledge_base_ids=["kb-1", "kb-2"],
    )

    request = pipeline.requests[0]
    assert request.knowledge_base_ids == ["kb-1", "kb-2"]
    assert request.filters["knowledge_base_id"] == "kb-1,kb-2"


class QualitySource:
    """評価 runner に保存済み取込品質レポートを渡す fake。"""

    async def list_document_extractions(self) -> list[dict[str, object]]:
        return [
            {
                "quality_report": {
                    "parser_profile": "enterprise_ai_pdf_layout",
                    "parser_backend": "enterprise_ai",
                    "fallback_used": False,
                    "risk_level": "medium",
                    "page_count": 4,
                    "page_coverage": 0.75,
                    "table_count": 2,
                    "figure_count": 0,
                    "formula_count": 1,
                    "element_count": 12,
                    "low_confidence_count": 0,
                    "failed_segment_count": 0,
                    "long_document": False,
                    "quality_warnings": ["table_structure_review", "formula_review"],
                }
            },
            {
                "quality_report": {
                    "parser_profile": "enterprise_ai_image_ocr",
                    "parser_backend": "enterprise_ai",
                    "fallback_used": True,
                    "risk_level": "high",
                    "page_count": 35,
                    "page_coverage": 0.5,
                    "table_count": 0,
                    "figure_count": 3,
                    "formula_count": 0,
                    "element_count": 40,
                    "low_confidence_count": 2,
                    "failed_segment_count": 1,
                    "long_document": True,
                    "quality_warnings": [
                        "figure_ocr_review",
                        "long_document",
                        "low_confidence_elements",
                        "failed_segments",
                        "parser_fallback_used",
                        "segment_extraction_artifact_cache_miss",
                    ],
                }
            },
        ]


class SlowQualitySource:
    """補助的な取込品質サマリが遅いケースを再現する fake。"""

    async def list_document_extractions(self) -> list[dict[str, object]]:
        await asyncio.sleep(1)
        return [{"quality_report": {"risk_level": "high"}}]


async def test_evaluation_runner_includes_ingestion_quality_summary() -> None:
    """評価結果に表・画像・長文書の取込品質サマリを含める。"""
    runner = EvaluationRunner(pipeline=StubPipeline(), quality_source=QualitySource())

    metrics = await runner.run(
        cases=[EvaluationCase(id="case-quality", query="承認条件")],
        top_k=5,
        rerank_top_n=3,
    )

    quality = metrics.ingestion_quality
    assert quality.document_count == 2
    assert quality.table_document_count == 1
    assert quality.figure_document_count == 1
    assert quality.formula_document_count == 1
    assert quality.low_confidence_document_count == 1
    assert quality.fallback_document_count == 1
    assert quality.failed_segment_document_count == 1
    assert quality.segment_artifact_cache_miss_document_count == 1
    assert quality.long_document_count == 1
    assert quality.average_page_coverage == 0.625
    assert quality.warning_counts == {
        "table_structure_review": 1,
        "formula_review": 1,
        "figure_ocr_review": 1,
        "long_document": 1,
        "low_confidence_elements": 1,
        "failed_segments": 1,
        "parser_fallback_used": 1,
        "segment_extraction_artifact_cache_miss": 1,
    }
    assert quality.risk_counts == {"low": 0, "medium": 1, "high": 1}
    assert quality.parser_profile_counts == {
        "enterprise_ai_pdf_layout": 1,
        "enterprise_ai_image_ocr": 1,
    }


async def test_evaluation_runner_times_out_ingestion_quality_summary(
    monkeypatch: MonkeyPatch,
) -> None:
    """取込品質サマリが遅くても evaluation 本体は成功として返す。"""
    settings = get_settings()
    monkeypatch.setattr(settings, "db_read_timeout_seconds", 0.001)
    runner = EvaluationRunner(
        pipeline=StubPipeline(),
        quality_source=SlowQualitySource(),
        settings=settings,
    )

    metrics = await runner.run(
        cases=[EvaluationCase(id="case-quality-timeout", query="承認条件")],
        top_k=5,
        rerank_top_n=3,
    )

    assert metrics.error_count == 0
    assert metrics.ingestion_quality.document_count == 0


class DuplicateChunkPipeline:
    """同じ document の複数 chunk を返す pipeline。"""

    async def run(
        self,
        request: SearchRequest,
        trace_id: str | None = None,
    ) -> SearchResponse:
        return SearchResponse(
            answer="A 文書の承認条件が関連します。",
            citations=[
                RetrievedChunk(
                    document_id="doc-a",
                    chunk_id="doc-a:0",
                    text="A 文書には承認条件が記載されています。",
                    score=1.0,
                ),
                RetrievedChunk(
                    document_id="doc-a",
                    chunk_id="doc-a:1",
                    text="A 文書の補足説明です。",
                    score=0.9,
                ),
                RetrievedChunk(
                    document_id="doc-b",
                    chunk_id="doc-b:0",
                    text="B 文書の検索候補です。",
                    score=0.8,
                ),
            ],
            trace_id=trace_id or "trace",
            guardrail_warnings=[],
            elapsed_ms=1.0,
        )


async def test_evaluation_metrics_are_document_level_not_chunk_level() -> None:
    runner = EvaluationRunner(pipeline=DuplicateChunkPipeline())

    metrics = await runner.run(
        cases=[
            EvaluationCase(
                id="case-duplicate",
                query="A",
                relevant_document_ids=["doc-a"],
                expected_answer_keywords=[],
            )
        ],
        top_k=3,
        rerank_top_n=3,
    )

    assert metrics.precision_at_k == 0.3333
    assert metrics.evaluated_k == 3
    assert metrics.recall_at_k == 1.0
    assert metrics.mrr == 1.0
    assert metrics.groundedness_pass_rate == 1.0
    assert metrics.case_results[0].retrieved_document_ids == ["doc-a", "doc-b"]
    assert metrics.case_results[0].hit_document_ids == ["doc-a"]


async def test_evaluation_runner_marks_threshold_gate_passed() -> None:
    """aggregate 指標が閾値以上なら CI gate を passed にする。"""
    runner = EvaluationRunner(pipeline=StubPipeline())

    metrics = await runner.run(
        cases=[
            EvaluationCase(
                id="case-pass",
                query="承認条件",
                relevant_document_ids=["doc-1"],
                expected_answer_keywords=["120000"],
            )
        ],
        top_k=5,
        rerank_top_n=3,
        thresholds=EvaluationThresholds(
            precision_at_k=0.3,
            recall_at_k=1.0,
            mrr=1.0,
            answer_keyword_hit_rate=1.0,
            groundedness_pass_rate=1.0,
            faithfulness=1.0,
            context_precision=1.0,
            context_recall=1.0,
            noise_sensitivity=1.0,
            citation_traceability_coverage=1.0,
            bbox_citation_coverage=1.0,
            element_lineage_coverage=1.0,
        ),
    )

    assert metrics.passed is True
    assert metrics.threshold_failures == []


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
        rerank_top_n=3,
        thresholds=EvaluationThresholds(
            precision_at_k=0.1,
            recall_at_k=0.9,
            mrr=0.5,
            answer_keyword_hit_rate=0.9,
            groundedness_pass_rate=0.9,
            faithfulness=0.9,
            context_precision=0.9,
            context_recall=0.9,
            response_relevancy=0.9,
            noise_sensitivity=0.9,
            citation_traceability_coverage=0.9,
            bbox_citation_coverage=0.9,
            element_lineage_coverage=0.9,
        ),
    )

    assert metrics.passed is False
    assert [
        (failure.metric, failure.actual, failure.threshold)
        for failure in metrics.threshold_failures
    ] == [
        ("precision_at_k", 0.0, 0.1),
        ("recall_at_k", 0.0, 0.9),
        ("mrr", 0.0, 0.5),
        ("answer_keyword_hit_rate", 0.0, 0.9),
        ("groundedness_pass_rate", 0.0, 0.9),
        ("faithfulness", 0.0, 0.9),
        ("context_precision", 0.0, 0.9),
        ("context_recall", 0.0, 0.9),
        ("response_relevancy", 0.0, 0.9),
        ("noise_sensitivity", 0.32, 0.9),
        ("citation_traceability_coverage", 0.0, 0.9),
        ("bbox_citation_coverage", 0.0, 0.9),
        ("element_lineage_coverage", 0.0, 0.9),
    ]


class MissPipeline:
    """関連 document を返さず、guardrail warning 付きの応答を返す pipeline。"""

    async def run(
        self,
        request: SearchRequest,
        trace_id: str | None = None,
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
        rerank_top_n=3,
    )

    assert metrics.precision_at_k == 0.0
    assert metrics.recall_at_k == 0.0
    assert metrics.mrr == 0.0
    assert metrics.answer_keyword_hit_rate == 0.0
    assert metrics.groundedness_pass_rate == 0.0
    assert metrics.faithfulness == 0.0
    assert metrics.context_precision == 0.0
    assert metrics.context_recall == 0.0
    assert metrics.response_relevancy == 0.0
    assert metrics.noise_sensitivity == 0.32
    result = metrics.case_results[0]
    assert result.case_id == "case-miss"
    assert result.trace_id
    assert result.status == "success"
    assert result.retrieved_document_ids == ["doc-x"]
    assert result.relevant_document_ids == ["doc-a"]
    assert result.hit_document_ids == []
    assert result.precision_at_k == 0.0
    assert result.recall_at_k == 0.0
    assert result.reciprocal_rank == 0.0
    assert result.answer_keyword_hit is False
    assert result.groundedness_passed is False
    assert result.groundedness_score == 0.0
    assert result.grounding_answer_feature_count > 0
    assert result.faithfulness == 0.0
    assert result.context_precision == 0.0
    assert result.context_recall == 0.0
    assert result.response_relevancy == 0.0
    assert result.noise_sensitivity == 0.32
    assert result.guardrail_warnings == ["検索条件に一致する根拠が見つかりませんでした。"]
    assert result.failure_reasons == [
        "retrieval_miss",
        "answer_keyword_miss",
        "low_groundedness",
        "guardrail_warning",
    ]
    assert metrics.failure_reason_counts == {
        "retrieval_miss": 1,
        "answer_keyword_miss": 1,
        "low_groundedness": 1,
        "guardrail_warning": 1,
    }
    assert result.elapsed_ms == 12.5


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
            rerank_top_n=3,
        )

    assert metrics.case_count == 2
    assert metrics.error_count == 1
    assert metrics.passed is False
    assert metrics.precision_at_k == 0.1667
    assert metrics.recall_at_k == 0.5
    assert metrics.mrr == 0.5
    assert metrics.answer_keyword_hit_rate == 0.5
    assert metrics.groundedness_pass_rate == 0.5

    ok_result, error_result = metrics.case_results
    assert ok_result.status == "success"
    assert error_result.case_id == "case-error"
    assert error_result.status == "error"
    assert error_result.error_type == "RuntimeError"
    assert error_result.error_message == EVALUATION_CASE_ERROR_MESSAGE
    assert error_result.retrieved_document_ids == []
    assert error_result.relevant_document_ids == ["doc-2"]
    assert error_result.precision_at_k == 0.0
    assert error_result.answer_keyword_hit is False
    assert error_result.groundedness_passed is False
    assert error_result.groundedness_score == 0.0
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
    assert audit_event["retrieved_count"] == 0
    assert "INV-SECRET" not in str(audit_event)
    assert "raw secret detail" not in str(audit_event)


async def test_evaluation_runner_records_case_metrics(monkeypatch: MonkeyPatch) -> None:
    """評価 case ごとの成功/失敗を低 cardinality metrics に残す。"""
    observed: list[tuple[str, str, float]] = []
    monkeypatch.setattr(
        "app.rag.evaluation.record_evaluation_case",
        lambda mode, status, seconds: observed.append((mode, status, seconds)),
    )
    runner = EvaluationRunner(pipeline=PartiallyFailingPipeline())

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
        rerank_top_n=3,
        mode=SearchMode.HYBRID,
    )

    assert metrics.error_count == 1
    assert [(mode, status) for mode, status, _ in observed] == [
        ("hybrid", "success"),
        ("hybrid", "error"),
    ]
    assert all(seconds >= 0 for _, _, seconds in observed)


async def test_evaluation_runner_records_timeout_audit(
    monkeypatch: MonkeyPatch,
    caplog: LogCaptureFixture,
) -> None:
    """評価 case timeout は error result と脱敏済み RAG 監査ログに残す。"""
    settings = get_settings()
    monkeypatch.setattr(settings, "rag_search_timeout_seconds", 0.001)
    runner = EvaluationRunner(pipeline=SlowPipeline(), settings=settings)

    with caplog.at_level(logging.INFO, logger="app.audit"):
        metrics = await runner.run(
            cases=[
                EvaluationCase(
                    id="case-timeout",
                    query="INV-SECRET の timeout ケース",
                    relevant_document_ids=["doc-timeout"],
                    expected_answer_keywords=["timeout"],
                )
            ],
            top_k=5,
            rerank_top_n=3,
        )

    assert metrics.error_count == 1
    assert metrics.passed is False
    result = metrics.case_results[0]
    assert result.status == "error"
    assert result.error_type == "TimeoutError"
    assert result.error_message == EVALUATION_CASE_ERROR_MESSAGE
    assert result.trace_id

    audit_record = next(record for record in caplog.records if record.message == "rag_search_audit")
    audit_event = cast(Any, audit_record).audit_event
    assert audit_event["trace_id"] == result.trace_id
    assert audit_event["outcome"] == "error"
    assert audit_event["error_stage"] == "timeout"
    assert audit_event["error_type"] == "TimeoutError"
    assert "INV-SECRET" not in str(audit_event)


async def test_evaluation_runner_compares_experiments_and_ranks_best() -> None:
    """同じ golden set で複数 RAG 設定を比較し、metric と失敗数で順位付けする。"""
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
            EvaluationExperiment(
                id="vector-small",
                mode=SearchMode.VECTOR,
                top_k=1,
                rerank_top_n=1,
            ),
            EvaluationExperiment(
                id="hybrid-wide",
                mode=SearchMode.HYBRID,
                top_k=3,
                rerank_top_n=3,
            ),
        ],
        ranking_metric="recall_at_k",
    )

    assert comparison.ranking_metric == "recall_at_k"
    assert comparison.best_experiment_id == "hybrid-wide"
    assert [result.rank for result in comparison.results] == [1, 2]
    assert [result.experiment.id for result in comparison.results] == [
        "hybrid-wide",
        "vector-small",
    ]
    assert comparison.results[0].ranking_score == 1.0
    assert comparison.results[0].metrics.failure_reason_counts == {}
    assert comparison.results[1].ranking_score == 0.0
    assert comparison.results[1].metrics.failure_reason_counts["retrieval_miss"] == 1


async def test_evaluation_compare_applies_experiment_rag_overrides(
    monkeypatch: MonkeyPatch,
) -> None:
    """compare experiment ごとの RAG override を一時 Settings として pipeline へ渡す。"""
    observed_settings: list[Settings] = []

    class CapturingRagPipeline:
        def __init__(self, settings: Settings) -> None:
            self.settings = settings
            observed_settings.append(settings)

        async def run(
            self,
            request: SearchRequest,
            trace_id: str | None = None,
        ) -> SearchResponse:
            return SearchResponse(
                answer="承認条件は 120000 円です。",
                citations=[
                    RetrievedChunk(
                        document_id="doc-1",
                        chunk_id="doc-1:0",
                        text="承認条件: 120000",
                        score=1.0,
                    )
                ],
                trace_id=trace_id or "trace",
                guardrail_warnings=[],
                elapsed_ms=1.0,
                diagnostics=SearchDiagnostics(
                    mode=request.mode.value,
                    top_k=request.top_k,
                    rerank_top_n=request.rerank_top_n,
                    rrf_k=self.settings.rag_rrf_k,
                    context_window_chars=self.settings.rag_context_window_chars,
                    oracle_vector_target_accuracy=(self.settings.oracle_vector_target_accuracy),
                    query_variant_count=(
                        self.settings.rag_query_expansion_max_variants
                        if self.settings.rag_query_expansion_enabled
                        else 1
                    ),
                ),
            )

    monkeypatch.setattr("app.rag.evaluation.RagPipeline", CapturingRagPipeline)
    runner = EvaluationRunner(
        quality_source=EmptyQualitySource(),
        settings=Settings.model_construct(
            rag_search_timeout_seconds=30.0,
            rag_rrf_k=60,
            rag_context_window_chars=12000,
            rag_context_neighbor_window=0,
            rag_context_diversity_lambda=1.0,
            rag_context_adaptive_expansion_enabled=False,
            rag_context_adaptive_neighbor_window=1,
            rag_context_adaptive_min_overlap=0.08,
            rag_context_group_expansion_enabled=False,
            rag_context_group_max_chunks=4,
            rag_context_dependency_promotion_enabled=False,
            rag_context_dependency_max_chunks=4,
            rag_context_compression_enabled=False,
            rag_context_compression_max_sentences=3,
            rag_context_compression_max_chars_per_chunk=1200,
            rag_query_expansion_enabled=True,
            rag_query_expansion_max_variants=3,
            oracle_vector_target_accuracy=95,
        ),
    )

    comparison = await runner.compare(
        cases=[
            EvaluationCase(
                id="case-overrides",
                query="承認条件",
                relevant_document_ids=["doc-1"],
                expected_answer_keywords=["120000"],
            )
        ],
        experiments=[
            EvaluationExperiment(id="baseline", top_k=3, rerank_top_n=3),
            EvaluationExperiment(
                id="diverse-context",
                top_k=3,
                rerank_top_n=3,
                rag_overrides=EvaluationRagOverrides(
                    rrf_k=10,
                    query_expansion_enabled=False,
                    query_expansion_max_variants=2,
                    context_window_chars=4096,
                    context_neighbor_window=1,
                    context_diversity_lambda=0.35,
                    context_adaptive_expansion_enabled=True,
                    context_adaptive_neighbor_window=2,
                    context_adaptive_min_overlap=0.2,
                    context_group_expansion_enabled=True,
                    context_group_max_chunks=2,
                    context_dependency_promotion_enabled=True,
                    context_dependency_max_chunks=3,
                    context_compression_enabled=True,
                    context_compression_max_sentences=2,
                    context_compression_max_chars_per_chunk=800,
                    oracle_vector_target_accuracy=90,
                ),
            ),
        ],
        ranking_metric="mrr",
    )

    assert [settings.rag_rrf_k for settings in observed_settings] == [60, 10]
    assert observed_settings[0].rag_context_diversity_lambda == 1.0
    assert observed_settings[1].rag_context_diversity_lambda == 0.35
    assert observed_settings[1].rag_context_neighbor_window == 1
    assert observed_settings[1].rag_context_adaptive_expansion_enabled is True
    assert observed_settings[1].rag_context_adaptive_neighbor_window == 2
    assert observed_settings[1].rag_context_adaptive_min_overlap == 0.2
    assert observed_settings[1].rag_context_group_expansion_enabled is True
    assert observed_settings[1].rag_context_group_max_chunks == 2
    assert observed_settings[1].rag_context_dependency_promotion_enabled is True
    assert observed_settings[1].rag_context_dependency_max_chunks == 3
    assert observed_settings[1].rag_context_compression_enabled is True
    assert observed_settings[1].rag_context_compression_max_sentences == 2
    assert observed_settings[1].rag_context_compression_max_chars_per_chunk == 800
    assert observed_settings[1].rag_query_expansion_enabled is False
    assert observed_settings[1].rag_context_window_chars == 4096
    assert observed_settings[1].oracle_vector_target_accuracy == 90
    assert comparison.results[0].metrics.case_results[0].diagnostics.rrf_k in {60, 10}


class PartiallyFailingPipeline:
    """一部 case だけ失敗する評価 runner 用 pipeline。"""

    async def run(
        self,
        request: SearchRequest,
        trace_id: str | None = None,
    ) -> SearchResponse:
        if "失敗" in request.query:
            raise RuntimeError("raw secret detail: INV-SECRET")
        return SearchResponse(
            answer="承認条件は 120000 円です。",
            citations=[
                RetrievedChunk(
                    document_id="doc-1",
                    chunk_id="doc-1:0",
                    text="承認条件: 120000",
                    score=1.0,
                )
            ],
            trace_id=trace_id or "trace-ok",
            guardrail_warnings=[],
            elapsed_ms=2.0,
        )


class SlowPipeline:
    """評価 case timeout を再現する pipeline。"""

    async def run(
        self,
        request: SearchRequest,
        trace_id: str | None = None,
    ) -> SearchResponse:
        assert trace_id
        await asyncio.sleep(1)
        raise AssertionError("timeout 前に完了しない")


class ComparePipeline:
    """evaluation compare の順位付けを確認する pipeline。"""

    async def run(
        self,
        request: SearchRequest,
        trace_id: str | None = None,
    ) -> SearchResponse:
        if request.mode == SearchMode.HYBRID:
            return SearchResponse(
                answer="承認条件は 120000 円です。",
                citations=[
                    RetrievedChunk(
                        document_id="doc-1",
                        chunk_id="doc-1:0",
                        text="承認条件: 120000",
                        score=1.0,
                    )
                ],
                trace_id=trace_id or "trace-hybrid",
                guardrail_warnings=[],
                elapsed_ms=2.0,
            )
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
            trace_id=trace_id or "trace-vector",
            guardrail_warnings=[],
            elapsed_ms=2.0,
        )


def test_evaluation_api_rejects_empty_cases() -> None:
    response = client.post(
        "/api/evaluation/run",
        json={"cases": [], "top_k": 5, "rerank_top_n": 3},
    )

    assert response.status_code == 422
    body = response.json()
    assert body["data"] is None
    assert body["error_messages"]


def test_evaluation_api_rejects_threshold_out_of_range() -> None:
    response = client.post(
        "/api/evaluation/run",
        json={
            "cases": [
                {
                    "id": "bad-threshold",
                    "query": "承認条件",
                    "relevant_document_ids": [],
                    "expected_answer_keywords": [],
                }
            ],
            "thresholds": {"recall_at_k": 1.1},
        },
    )

    assert response.status_code == 422
    body = response.json()
    assert body["data"] is None
    assert body["error_messages"]


def test_evaluation_api_rejects_rerank_top_n_larger_than_top_k() -> None:
    response = client.post(
        "/api/evaluation/run",
        json={
            "cases": [
                {
                    "id": "bad-depth",
                    "query": "承認条件",
                    "relevant_document_ids": [],
                    "expected_answer_keywords": [],
                }
            ],
            "top_k": 2,
            "rerank_top_n": 3,
        },
    )

    assert response.status_code == 422
    body = response.json()
    assert body["data"] is None
    assert any("rerank_top_n は top_k 以下" in message for message in body["error_messages"])


def test_evaluation_compare_api_rejects_duplicate_experiment_ids() -> None:
    response = client.post(
        "/api/evaluation/compare",
        json={
            "cases": [
                {
                    "id": "case-1",
                    "query": "承認条件",
                    "relevant_document_ids": [],
                    "expected_answer_keywords": [],
                }
            ],
            "experiments": [
                {"id": "same", "top_k": 5, "rerank_top_n": 3},
                {"id": "same", "top_k": 10, "rerank_top_n": 5},
            ],
        },
    )

    assert response.status_code == 422
    body = response.json()
    assert body["data"] is None
    assert any("experiment id が重複" in message for message in body["error_messages"])


def test_evaluation_compare_api_rejects_invalid_rag_overrides() -> None:
    response = client.post(
        "/api/evaluation/compare",
        json={
            "cases": [
                {
                    "id": "case-1",
                    "query": "承認条件",
                    "relevant_document_ids": [],
                    "expected_answer_keywords": [],
                }
            ],
            "experiments": [
                {
                    "id": "bad-overrides",
                    "top_k": 5,
                    "rerank_top_n": 3,
                    "rag_overrides": {
                        "context_diversity_lambda": 1.2,
                        "context_adaptive_neighbor_window": 6,
                        "context_adaptive_min_overlap": -0.01,
                        "context_group_max_chunks": 21,
                        "context_dependency_max_chunks": 21,
                        "context_neighbor_window": 6,
                        "context_compression_max_sentences": 11,
                        "context_compression_max_chars_per_chunk": 199,
                    },
                }
            ],
        },
    )

    assert response.status_code == 422
    body = response.json()
    assert body["data"] is None
    assert body["error_messages"]


def test_evaluation_api_rejects_blank_case_query() -> None:
    response = client.post(
        "/api/evaluation/run",
        json={
            "cases": [
                {
                    "id": "blank-query",
                    "query": "   ",
                    "relevant_document_ids": [],
                    "expected_answer_keywords": [],
                }
            ],
            "top_k": 5,
            "rerank_top_n": 3,
        },
    )

    assert response.status_code == 422
    body = response.json()
    assert body["data"] is None
    assert body["error_messages"]


def test_evaluation_api_persists_redacted_artifact(monkeypatch: MonkeyPatch) -> None:
    """評価 API は query 原文を除いた artifact summary を best-effort 保存する。"""
    artifacts: list[dict[str, Any]] = []

    class FakeEvaluationRunner:
        async def run(self, **kwargs: object) -> EvaluationMetrics:
            del kwargs
            return EvaluationMetrics(
                case_count=1,
                evaluated_k=1,
                precision_at_k=1.0,
                recall_at_k=1.0,
                mrr=1.0,
                answer_keyword_hit_rate=1.0,
                groundedness_pass_rate=1.0,
                passed=True,
            )

    class FakeOracleClient:
        async def save_evaluation_artifact(self, artifact: dict[str, Any]) -> str:
            artifacts.append(artifact)
            return "eval-1"

        async def get_knowledge_base(self, knowledge_base_id: str) -> None:
            # この fake は KB 別の評価スイート上書きを使わない。
            return None

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
                }
            ],
            "top_k": 1,
            "rerank_top_n": 1,
            "knowledge_base_ids": ["kb-1"],
        },
    )

    assert response.status_code == 200
    assert len(artifacts) == 1
    artifact_text = str(artifacts[0])
    assert "社外秘キーワード" not in artifact_text
    assert "ABC-123" not in artifact_text
    assert artifacts[0]["request_summary"]["cases"][0]["query_hash"]
    assert artifacts[0]["knowledge_base_ids"] == ["kb-1"]


@pytest.mark.usefixtures("oracle_db")
def test_evaluation_api_runs_against_local_pipeline() -> None:
    """API 経由でも golden set 評価メトリクスを返す。"""
    response = client.post(
        "/api/evaluation/run",
        json={
            "cases": [
                {
                    "id": "empty-store",
                    "query": "存在しない社内規程",
                    "relevant_document_ids": [],
                    "expected_answer_keywords": [],
                }
            ],
            "top_k": 5,
            "rerank_top_n": 3,
            "mode": "hybrid",
            "knowledge_base_ids": ["kb-eval-empty-isolated"],
            "thresholds": {
                "precision_at_k": 1.0,
                "recall_at_k": 1.0,
                "answer_keyword_hit_rate": 1.0,
            },
        },
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["case_count"] == 1
    assert data["evaluated_k"] == 3
    assert data["precision_at_k"] == 1.0
    assert data["recall_at_k"] == 1.0
    assert data["answer_keyword_hit_rate"] == 1.0
    assert data["groundedness_pass_rate"] == 1.0
    assert data["passed"] is True
    assert data["threshold_failures"] == []
    assert data["failure_reason_counts"] == {}
    assert data["case_results"][0]["case_id"] == "empty-store"
    assert data["case_results"][0]["retrieved_document_ids"] == []
    assert data["case_results"][0]["answer_keyword_hit"] is True
    assert data["case_results"][0]["groundedness_passed"] is True
    assert data["case_results"][0]["failure_reasons"] == []


def _minimal_eval_metrics() -> EvaluationMetrics:
    return EvaluationMetrics(
        case_count=1,
        evaluated_k=1,
        precision_at_k=1.0,
        recall_at_k=1.0,
        mrr=1.0,
        answer_keyword_hit_rate=1.0,
        groundedness_pass_rate=1.0,
    )


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
    """request に thresholds 未指定なら設定 suite の閾値を適用し suite を stamp する。"""
    captured: dict[str, Any] = {}

    async def fake_run(self: EvaluationRunner, **kwargs: Any) -> EvaluationMetrics:
        captured["thresholds"] = kwargs.get("thresholds")
        return _minimal_eval_metrics()

    monkeypatch.setattr(EvaluationRunner, "run", fake_run)
    monkeypatch.setattr(get_settings(), "rag_evaluation_suite", "balanced")

    resp = client.post("/api/evaluation/run", json=_eval_run_body())

    assert resp.status_code == 200
    assert resp.json()["data"]["evaluation_suite"] == "balanced"
    thresholds = captured["thresholds"]
    assert thresholds is not None
    assert thresholds.groundedness_pass_rate == 0.9


def test_run_evaluation_request_thresholds_take_precedence_over_suite(
    monkeypatch: MonkeyPatch,
) -> None:
    """request の thresholds は suite より優先される。"""
    captured: dict[str, Any] = {}

    async def fake_run(self: EvaluationRunner, **kwargs: Any) -> EvaluationMetrics:
        captured["thresholds"] = kwargs.get("thresholds")
        return _minimal_eval_metrics()

    monkeypatch.setattr(EvaluationRunner, "run", fake_run)
    monkeypatch.setattr(get_settings(), "rag_evaluation_suite", "balanced")

    body = {**_eval_run_body(), "thresholds": {"mrr": 0.42}, "suite": "strict_ci"}
    resp = client.post("/api/evaluation/run", json=body)

    assert resp.status_code == 200
    # suite は stamp されるが、閾値は request 明示が優先。
    assert resp.json()["data"]["evaluation_suite"] == "strict_ci"
    thresholds = captured["thresholds"]
    assert thresholds is not None
    assert thresholds.mrr == 0.42
    assert thresholds.groundedness_pass_rate is None


def test_run_evaluation_request_only_keeps_no_preset_thresholds(
    monkeypatch: MonkeyPatch,
) -> None:
    """既定 request_only は閾値なしで現行どおり(thresholds=None)。"""
    captured: dict[str, Any] = {}

    async def fake_run(self: EvaluationRunner, **kwargs: Any) -> EvaluationMetrics:
        captured["thresholds"] = kwargs.get("thresholds")
        return _minimal_eval_metrics()

    monkeypatch.setattr(EvaluationRunner, "run", fake_run)
    monkeypatch.setattr(get_settings(), "rag_evaluation_suite", "request_only")

    resp = client.post("/api/evaluation/run", json=_eval_run_body())

    assert resp.status_code == 200
    assert resp.json()["data"]["evaluation_suite"] == "request_only"
    assert captured["thresholds"] is None


def test_run_evaluation_uses_kb_evaluation_suite_when_request_omits(
    monkeypatch: MonkeyPatch,
) -> None:
    """単一 KB 指定時、request 未指定なら KB の evaluation_suite が解決順 2 番目で効く。"""
    from app.api.routes import evaluation as evaluation_route
    from app.rag.kb_adapter_config import KnowledgeBaseAdapterConfig
    from app.schemas.knowledge_base import KnowledgeBaseDetail, KnowledgeBaseStatus

    captured: dict[str, Any] = {}

    async def fake_run(self: EvaluationRunner, **kwargs: Any) -> EvaluationMetrics:
        captured["thresholds"] = kwargs.get("thresholds")
        return _minimal_eval_metrics()

    class FakeOracleClient:
        async def get_knowledge_base(self, knowledge_base_id: str) -> KnowledgeBaseDetail:
            return KnowledgeBaseDetail(
                id=knowledge_base_id,
                name="KB",
                status=KnowledgeBaseStatus.ACTIVE,
                adapter_config=KnowledgeBaseAdapterConfig.model_validate(
                    {"query": {"evaluation_suite": "strict_ci"}}
                ),
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
                updated_at=datetime(2026, 1, 1, tzinfo=UTC),
            )

        async def save_evaluation_artifact(self, artifact: dict[str, Any]) -> str:
            return "eval-1"

    monkeypatch.setattr(EvaluationRunner, "run", fake_run)
    monkeypatch.setattr(evaluation_route, "OracleClient", FakeOracleClient)
    monkeypatch.setattr(get_settings(), "rag_evaluation_suite", "balanced")

    body = {**_eval_run_body(), "knowledge_base_ids": ["kb-1"]}
    resp = client.post("/api/evaluation/run", json=body)

    assert resp.status_code == 200
    # グローバル balanced ではなく KB の strict_ci が選ばれる。
    assert resp.json()["data"]["evaluation_suite"] == "strict_ci"
    assert captured["thresholds"] is not None


def test_run_evaluation_request_suite_beats_kb_suite(
    monkeypatch: MonkeyPatch,
) -> None:
    """request の suite 明示は KB の evaluation_suite より優先される。"""
    from app.api.routes import evaluation as evaluation_route

    async def fake_run(self: EvaluationRunner, **kwargs: Any) -> EvaluationMetrics:
        return _minimal_eval_metrics()

    class FakeOracleClient:
        async def get_knowledge_base(self, knowledge_base_id: str) -> None:
            raise AssertionError("request suite 指定時は KB を参照しない")

        async def save_evaluation_artifact(self, artifact: dict[str, Any]) -> str:
            return "eval-1"

    monkeypatch.setattr(EvaluationRunner, "run", fake_run)
    monkeypatch.setattr(evaluation_route, "OracleClient", FakeOracleClient)

    body = {**_eval_run_body(), "knowledge_base_ids": ["kb-1"], "suite": "retrieval_focused"}
    resp = client.post("/api/evaluation/run", json=body)

    assert resp.status_code == 200
    assert resp.json()["data"]["evaluation_suite"] == "retrieval_focused"
