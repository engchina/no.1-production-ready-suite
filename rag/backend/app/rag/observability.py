"""RAG/HTTP の観測性ヘルパー。"""

import logging
from collections.abc import Iterable
from time import perf_counter
from typing import Literal
from uuid import uuid4

from prometheus_client import Counter, Histogram
from pydantic import BaseModel, Field

from app.rag.guardrails import GuardrailFinding

trace_logger = logging.getLogger("app.trace")
TraceOutcome = Literal["success", "error", "cancelled"]
TraceAttribute = str | int | float | bool | None


class TraceSpanEvent(BaseModel):
    """OpenTelemetry / Langfuse へ橋渡ししやすい span event。"""

    event_type: Literal["rag.trace_span"] = "rag.trace_span"
    trace_id: str
    span_name: str
    outcome: TraceOutcome
    duration_ms: float
    attributes: dict[str, TraceAttribute] = Field(default_factory=dict)
    error_type: str | None = None


HTTP_REQUESTS = Counter(
    "rag_http_requests_total",
    "HTTP リクエスト数",
    ["method", "path", "status"],
)
HTTP_REQUEST_DURATION = Histogram(
    "rag_http_request_duration_seconds",
    "HTTP リクエスト処理時間",
    ["method", "path"],
)
RAG_REQUESTS = Counter(
    "rag_search_requests_total",
    "RAG 検索リクエスト数",
    ["mode", "outcome"],
)
RAG_LATENCY = Histogram(
    "rag_search_duration_seconds",
    "RAG 検索処理時間",
    ["mode"],
)
RAG_STAGE_LATENCY = Histogram(
    "rag_search_stage_duration_seconds",
    "RAG 検索の stage 別処理時間",
    ["mode", "stage", "outcome"],
)
INGESTION_DOCUMENTS = Counter(
    "rag_ingestion_documents_total",
    "取込ドキュメント数",
    ["outcome"],
)
INGESTION_CHUNKS = Histogram(
    "rag_ingestion_chunks",
    "1 ドキュメントあたりのチャンク数",
)
INGESTION_STAGE_LATENCY = Histogram(
    "rag_ingestion_stage_duration_seconds",
    "RAG 取込の stage 別処理時間",
    ["stage", "outcome"],
)
EVALUATION_CASES = Counter(
    "rag_evaluation_cases_total",
    "RAG 評価ケース数",
    ["mode", "status"],
)
EVALUATION_CASE_DURATION = Histogram(
    "rag_evaluation_case_duration_seconds",
    "RAG 評価ケース処理時間",
    ["mode", "status"],
)
RETRIEVAL_HITS = Histogram(
    "rag_retrieval_hits",
    "検索で取得した候補チャンク数",
    ["mode"],
)
GUARDRAIL_FINDINGS = Counter(
    "rag_guardrail_findings_total",
    "RAG guardrail の検出件数",
    ["surface", "code", "severity", "action"],
)
RATE_LIMIT_DECISIONS = Counter(
    "rag_rate_limit_decisions_total",
    "高コスト API の rate limit 判定数",
    ["scope", "outcome"],
)


def new_trace_id() -> str:
    """検索・取込の追跡に使う軽量 trace ID を発行する。"""
    return uuid4().hex


def now() -> float:
    """monotonic clock の現在値を返す。"""
    return perf_counter()


def elapsed_ms(started_at: float) -> float:
    """開始時刻からの経過ミリ秒。"""
    return round((perf_counter() - started_at) * 1000, 2)


def record_http_request(method: str, path: str, status: int, seconds: float) -> None:
    """HTTP メトリクスを記録する。"""
    status_label = str(status)
    HTTP_REQUESTS.labels(method=method, path=path, status=status_label).inc()
    HTTP_REQUEST_DURATION.labels(method=method, path=path).observe(seconds)


def record_rag_request(mode: str, outcome: str, seconds: float, hits: int) -> None:
    """RAG 検索メトリクスを記録する。"""
    RAG_REQUESTS.labels(mode=mode, outcome=outcome).inc()
    RAG_LATENCY.labels(mode=mode).observe(seconds)
    RETRIEVAL_HITS.labels(mode=mode).observe(hits)


def record_rag_stage(mode: str, stage: str, outcome: str, seconds: float) -> None:
    """RAG 検索の stage 別メトリクスを記録する。"""
    RAG_STAGE_LATENCY.labels(mode=mode, stage=stage, outcome=outcome).observe(seconds)


def record_ingestion(outcome: str, chunk_count: int) -> None:
    """取込メトリクスを記録する。"""
    INGESTION_DOCUMENTS.labels(outcome=outcome).inc()
    INGESTION_CHUNKS.observe(chunk_count)


def record_ingestion_stage(stage: str, outcome: str, seconds: float) -> None:
    """RAG 取込の stage 別メトリクスを記録する。"""
    INGESTION_STAGE_LATENCY.labels(stage=stage, outcome=outcome).observe(seconds)


def record_evaluation_case(mode: str, status: str, seconds: float) -> None:
    """golden set 評価 case のメトリクスを記録する。"""
    EVALUATION_CASES.labels(mode=mode, status=status).inc()
    EVALUATION_CASE_DURATION.labels(mode=mode, status=status).observe(seconds)


def record_guardrail_findings(
    surface: str,
    findings: Iterable[GuardrailFinding],
    action: str,
) -> None:
    """guardrail finding を低 cardinality label で記録する。"""
    for finding in findings:
        GUARDRAIL_FINDINGS.labels(
            surface=surface,
            code=finding.code,
            severity=finding.severity,
            action=action,
        ).inc()


def record_rate_limit_decision(scope: str, outcome: str) -> None:
    """rate limit 判定を低 cardinality label で記録する。"""
    RATE_LIMIT_DECISIONS.labels(scope=scope, outcome=outcome).inc()


def record_trace_span(
    *,
    trace_id: str,
    span_name: str,
    outcome: TraceOutcome,
    seconds: float,
    attributes: dict[str, object] | None = None,
    error: BaseException | None = None,
) -> TraceSpanEvent:
    """単一 RAG stage の trace span event を構造化ログへ出す。"""
    event = TraceSpanEvent(
        trace_id=trace_id,
        span_name=span_name,
        outcome=outcome,
        duration_ms=round(seconds * 1000, 2),
        attributes=_safe_trace_attributes(attributes or {}),
        error_type=type(error).__name__ if error is not None else None,
    )
    trace_logger.info(
        "rag_trace_span",
        extra={"trace_event": event.model_dump(mode="json")},
    )
    return event


def _safe_trace_attributes(attributes: dict[str, object]) -> dict[str, TraceAttribute]:
    """trace attribute を低機密・低 cardinality な scalar に絞る。"""
    safe: dict[str, TraceAttribute] = {}
    for key, value in attributes.items():
        if value is None or isinstance(value, bool | int | float):
            safe[key] = value
        elif isinstance(value, str):
            safe[key] = value[:200]
    return safe
