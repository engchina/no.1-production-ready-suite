"""RAG 評価ランナーのテスト。"""

import asyncio
import logging
from typing import Any, cast

from pytest import LogCaptureFixture, MonkeyPatch

from app.config import get_settings
from app.main import app
from app.rag.evaluation import EVALUATION_CASE_ERROR_MESSAGE, EvaluationRunner
from app.schemas.evaluation import EvaluationCase, EvaluationThresholds
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
            answer="請求金額は 120000 円です。",
            citations=[
                RetrievedChunk(
                    document_id="doc-1",
                    chunk_id="doc-1:0",
                    text="請求金額: 120,000",
                    score=1.0,
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


async def test_evaluation_runner_computes_metrics() -> None:
    pipeline = StubPipeline()
    runner = EvaluationRunner(pipeline=pipeline)
    metrics = await runner.run(
        cases=[
            EvaluationCase(
                id="case-1",
                query="請求金額",
                relevant_document_ids=["doc-1"],
                expected_answer_keywords=["120000"],
            )
        ],
        top_k=5,
        rerank_top_n=3,
        mode=SearchMode.KEYWORD,
        filters={"status": "analyzed"},
    )
    assert metrics.evaluated_k == 3
    assert metrics.precision_at_k == 0.3333
    assert metrics.recall_at_k == 1.0
    assert metrics.mrr == 1.0
    assert metrics.answer_keyword_hit_rate == 1.0
    assert metrics.passed is True
    assert metrics.threshold_failures == []
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
    assert result.guardrail_warnings == []
    assert result.diagnostics.top_k == 5
    assert result.diagnostics.rerank_top_n == 3
    assert result.diagnostics.retrieved_count == 1
    assert result.diagnostics.citation_count == 1
    assert pipeline.requests[0].mode == SearchMode.KEYWORD
    assert pipeline.requests[0].filters == {"status": "ANALYZED"}


class DuplicateChunkPipeline:
    """同じ document の複数 chunk を返す pipeline。"""

    async def run(
        self,
        request: SearchRequest,
        trace_id: str | None = None,
    ) -> SearchResponse:
        return SearchResponse(
            answer="A 文書が関連します。",
            citations=[
                RetrievedChunk(document_id="doc-a", chunk_id="doc-a:0", text="A", score=1.0),
                RetrievedChunk(document_id="doc-a", chunk_id="doc-a:1", text="A2", score=0.9),
                RetrievedChunk(document_id="doc-b", chunk_id="doc-b:0", text="B", score=0.8),
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
    assert metrics.case_results[0].retrieved_document_ids == ["doc-a", "doc-b"]
    assert metrics.case_results[0].hit_document_ids == ["doc-a"]


async def test_evaluation_runner_marks_threshold_gate_passed() -> None:
    """aggregate 指標が閾値以上なら CI gate を passed にする。"""
    runner = EvaluationRunner(pipeline=StubPipeline())

    metrics = await runner.run(
        cases=[
            EvaluationCase(
                id="case-pass",
                query="請求金額",
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
                query="請求金額",
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
                RetrievedChunk(document_id="doc-x", chunk_id="doc-x:0", text="X", score=0.8),
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
                query="請求金額",
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
    assert result.guardrail_warnings == ["検索条件に一致する根拠が見つかりませんでした。"]
    assert result.elapsed_ms == 12.5


async def test_evaluation_runner_isolates_case_errors(caplog: LogCaptureFixture) -> None:
    """1 case の検索失敗は batch 全体を中断せず、失敗 case として返す。"""
    runner = EvaluationRunner(pipeline=PartiallyFailingPipeline())

    with caplog.at_level(logging.INFO, logger="app.audit"):
        metrics = await runner.run(
            cases=[
                EvaluationCase(
                    id="case-ok",
                    query="請求金額",
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
                query="請求金額",
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
            answer="請求金額は 120000 円です。",
            citations=[
                RetrievedChunk(
                    document_id="doc-1",
                    chunk_id="doc-1:0",
                    text="請求金額: 120000",
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
                    "query": "請求金額",
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
                    "query": "請求金額",
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


def test_evaluation_api_runs_against_local_pipeline() -> None:
    """API 経由でも golden set 評価メトリクスを返す。"""
    response = client.post(
        "/api/evaluation/run",
        json={
            "cases": [
                {
                    "id": "empty-store",
                    "query": "存在しない請求書",
                    "relevant_document_ids": [],
                    "expected_answer_keywords": [],
                }
            ],
            "top_k": 5,
            "rerank_top_n": 3,
            "mode": "hybrid",
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
    assert data["passed"] is True
    assert data["threshold_failures"] == []
    assert data["case_results"][0]["case_id"] == "empty-store"
    assert data["case_results"][0]["retrieved_document_ids"] == []
    assert data["case_results"][0]["answer_keyword_hit"] is True
