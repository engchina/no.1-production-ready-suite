"""RAG / guardrail の監査イベント。"""

import asyncio
import hashlib
import logging
from typing import Literal

from pydantic import BaseModel, Field

from app.rag.guardrails import GuardrailFinding
from app.rag.request_context import current_audit_request_context
from app.schemas.search import (
    RetrievedChunk,
    SearchDiagnostics,
    SearchMode,
    parse_search_id_filter,
)

logger = logging.getLogger("app.audit")
persist_logger = logging.getLogger("app.audit.persistence")

AuditOutcome = Literal["success", "blocked", "no_results", "error"]
IngestionAuditOutcome = Literal["success", "error"]


class RagSearchAuditEvent(BaseModel):
    """検索実行ごとの監査ログ payload。"""

    event_type: Literal["rag.search"] = "rag.search"
    trace_id: str
    request_id: str | None = None
    tenant_id_hash: str | None = None
    user_id_hash: str | None = None
    outcome: AuditOutcome
    mode: SearchMode
    query_hash: str
    query_chars: int
    filter_keys: list[str] = Field(default_factory=list)
    top_k: int | None = None
    rerank_top_n: int | None = None
    query_variant_count: int = 1
    guardrail_codes: list[str] = Field(default_factory=list)
    guardrail_severities: list[str] = Field(default_factory=list)
    retrieved_count: int = 0
    reranked_count: int = 0
    deduplicated_count: int = 0
    context_diversified_count: int = 0
    context_group_expanded_count: int = 0
    context_expanded_count: int = 0
    context_compressed_count: int = 0
    context_compression_saved_chars: int = 0
    citation_count: int = 0
    context_chars: int = 0
    context_window_chars: int | None = None
    document_ids: list[str] = Field(default_factory=list)
    knowledge_base_ids: list[str] = Field(default_factory=list)
    config_fingerprint: str | None = None
    elapsed_ms: float
    error_stage: str | None = None
    error_type: str | None = None


class RagIngestionAuditEvent(BaseModel):
    """取込実行ごとの監査ログ payload。"""

    event_type: Literal["rag.ingestion"] = "rag.ingestion"
    trace_id: str
    request_id: str | None = None
    tenant_id_hash: str | None = None
    user_id_hash: str | None = None
    document_id: str
    outcome: IngestionAuditOutcome
    source_sha256: str
    source_bytes: int
    document_type: str | None = None
    extraction_confidence: float | None = None
    chunk_count: int = 0
    vector_count: int = 0
    elapsed_ms: float
    error_type: str | None = None
    error_message: str | None = None


def record_rag_search_audit(
    *,
    trace_id: str,
    outcome: AuditOutcome,
    mode: SearchMode,
    sanitized_query: str,
    filters: dict[str, str],
    findings: list[GuardrailFinding],
    retrieved_count: int,
    citations: list[RetrievedChunk],
    elapsed_ms: float,
    diagnostics: SearchDiagnostics | None = None,
    error: Exception | None = None,
    error_stage: str | None = None,
) -> RagSearchAuditEvent:
    """RAG 検索の監査イベントを構造化ログへ出す。"""
    request_context = current_audit_request_context()
    event = RagSearchAuditEvent(
        trace_id=trace_id,
        request_id=request_context.request_id,
        tenant_id_hash=request_context.tenant_id_hash,
        user_id_hash=request_context.user_id_hash,
        outcome=outcome,
        mode=mode,
        query_hash=_query_hash(sanitized_query),
        query_chars=len(sanitized_query),
        filter_keys=sorted(filters),
        top_k=diagnostics.top_k if diagnostics is not None else None,
        rerank_top_n=diagnostics.rerank_top_n if diagnostics is not None else None,
        query_variant_count=diagnostics.query_variant_count if diagnostics is not None else 1,
        guardrail_codes=[finding.code for finding in findings],
        guardrail_severities=[finding.severity for finding in findings],
        retrieved_count=retrieved_count,
        reranked_count=diagnostics.reranked_count if diagnostics is not None else 0,
        deduplicated_count=diagnostics.deduplicated_count if diagnostics is not None else 0,
        context_diversified_count=(
            diagnostics.context_diversified_count if diagnostics is not None else 0
        ),
        context_group_expanded_count=(
            diagnostics.context_group_expanded_count if diagnostics is not None else 0
        ),
        context_expanded_count=(
            diagnostics.context_expanded_count if diagnostics is not None else 0
        ),
        context_compressed_count=(
            diagnostics.context_compressed_count if diagnostics is not None else 0
        ),
        context_compression_saved_chars=(
            diagnostics.context_compression_saved_chars if diagnostics is not None else 0
        ),
        citation_count=len(citations),
        context_chars=diagnostics.context_chars if diagnostics is not None else 0,
        context_window_chars=(
            diagnostics.context_window_chars if diagnostics is not None else None
        ),
        document_ids=_unique_document_ids(citations),
        knowledge_base_ids=parse_search_id_filter(filters.get("knowledge_base_id")),
        config_fingerprint=(diagnostics.config_fingerprint if diagnostics is not None else None),
        elapsed_ms=elapsed_ms,
        error_stage=error_stage,
        error_type=type(error).__name__ if error is not None else None,
    )
    logger.info(
        "rag_search_audit",
        extra={"audit_event": event.model_dump(mode="json")},
    )
    _schedule_audit_persistence("search", event.model_dump(mode="json"))
    return event


def record_rag_ingestion_audit(
    *,
    trace_id: str,
    document_id: str,
    outcome: IngestionAuditOutcome,
    source_bytes: bytes,
    elapsed_ms: float,
    document_type: str | None = None,
    extraction_confidence: float | None = None,
    chunk_count: int = 0,
    vector_count: int = 0,
    error: Exception | None = None,
) -> RagIngestionAuditEvent:
    """RAG 取込の監査イベントを構造化ログへ出す。"""
    request_context = current_audit_request_context()
    event = RagIngestionAuditEvent(
        trace_id=trace_id,
        request_id=request_context.request_id,
        tenant_id_hash=request_context.tenant_id_hash,
        user_id_hash=request_context.user_id_hash,
        document_id=document_id,
        outcome=outcome,
        source_sha256=_query_hash_bytes(source_bytes),
        source_bytes=len(source_bytes),
        document_type=document_type,
        extraction_confidence=extraction_confidence,
        chunk_count=chunk_count,
        vector_count=vector_count,
        elapsed_ms=elapsed_ms,
        error_type=type(error).__name__ if error is not None else None,
        error_message=_safe_error_message(error),
    )
    logger.info(
        "rag_ingestion_audit",
        extra={"audit_event": event.model_dump(mode="json")},
    )
    _schedule_audit_persistence("ingestion", event.model_dump(mode="json"))
    return event


def _schedule_audit_persistence(
    kind: Literal["search", "ingestion"],
    event: dict[str, object],
) -> None:
    """設定されている場合だけ Oracle 監査 table へ非同期保存する。"""
    from app.config import get_settings

    settings = get_settings()
    if settings.audit_persistence not in {"oracle", "both"}:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        persist_logger.warning(
            "rag_audit_persistence_skipped",
            extra={"audit_kind": kind, "reason": "no_running_event_loop"},
        )
        return
    task = loop.create_task(_persist_audit_event(kind, event))
    task.add_done_callback(_log_audit_persistence_failure)


async def _persist_audit_event(
    kind: Literal["search", "ingestion"], event: dict[str, object]
) -> None:
    """OracleClient への import を遅延し、audit/logging 経路の循環 import を避ける。"""
    from app.clients.oracle import OracleClient

    client = OracleClient()
    if kind == "search":
        await client.save_search_audit_event(event)
    else:
        await client.save_ingestion_audit_event(event)


def _log_audit_persistence_failure(task: asyncio.Task[None]) -> None:
    """監査 table 保存に失敗しても本処理は失敗させず、型だけを記録する。"""
    try:
        task.result()
    except Exception as exc:  # pragma: no cover - task callback の防御的ログ
        persist_logger.warning(
            "rag_audit_persistence_failed",
            extra={"error_type": type(exc).__name__},
        )


def _query_hash(query: str) -> str:
    """問い合わせ本文をログに残さず相関できる stable hash を返す。"""
    return hashlib.sha256(query.encode("utf-8")).hexdigest()


def _query_hash_bytes(data: bytes) -> str:
    """原本 bytes をログで相関するための stable hash を返す。"""
    return hashlib.sha256(data).hexdigest()


def _safe_error_message(error: Exception | None) -> str | None:
    """監査ログ向けにエラーメッセージを短く抑える。"""
    if error is None:
        return None
    if not getattr(error, "safe_for_user", False):
        return "内部エラーの詳細は保存しません。"
    message = str(error).replace("\n", " ").strip()
    return message[:200] if message else None


def _unique_document_ids(citations: list[RetrievedChunk]) -> list[str]:
    """引用に含まれる document_id を初出順で重複排除する。"""
    document_ids: list[str] = []
    seen: set[str] = set()
    for citation in citations:
        if citation.document_id in seen:
            continue
        seen.add(citation.document_id)
        document_ids.append(citation.document_id)
    return document_ids
