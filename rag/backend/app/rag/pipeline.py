"""検索 RAG パイプライン: 埋め込み -> ベクトル検索 -> リランク -> 生成。"""

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from time import perf_counter

from app.clients.oci_enterprise_ai import OciEnterpriseAiClient
from app.clients.oci_genai import OciGenAiClient
from app.clients.oracle import OracleClient
from app.config import Settings, get_settings
from app.rag.audit import AuditOutcome, record_rag_search_audit
from app.rag.diagnostics import build_search_diagnostics
from app.rag.guardrails import GuardrailPolicy
from app.rag.observability import (
    elapsed_ms,
    new_trace_id,
    record_guardrail_findings,
    record_rag_request,
    record_rag_stage,
    record_trace_span,
)
from app.schemas.search import RetrievedChunk, SearchRequest, SearchResponse

NO_RESULTS_ANSWER = (
    "検索条件に一致する根拠が見つかりませんでした。" "条件やキーワードを変えて検索してください。"
)
NO_RESULTS_WARNING = "検索条件に一致する根拠が見つかりませんでした。"


class RagPipeline:
    """ハイブリッド検索 + リランク + 生成の RAG パイプライン。"""

    def __init__(
        self,
        genai: OciGenAiClient | None = None,
        oracle: OracleClient | None = None,
        llm: OciEnterpriseAiClient | None = None,
        guardrails: GuardrailPolicy | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._genai = genai or OciGenAiClient()
        self._oracle = oracle or OracleClient()
        self._llm = llm or OciEnterpriseAiClient()
        self._guardrails = guardrails or GuardrailPolicy()

    async def run(self, request: SearchRequest, trace_id: str | None = None) -> SearchResponse:
        """RAG 検索を実行する。"""
        started_at = perf_counter()
        trace_id = trace_id or new_trace_id()
        query_guardrail = self._guardrails.validate_query(request.query)
        record_guardrail_findings(
            "query",
            query_guardrail.findings,
            "blocked" if not query_guardrail.allowed else "warning",
        )
        if not query_guardrail.allowed:
            elapsed = elapsed_ms(started_at)
            diagnostics = build_search_diagnostics(request, settings=self._settings)
            record_rag_request(request.mode.value, "blocked", elapsed / 1000, 0)
            record_rag_search_audit(
                trace_id=trace_id,
                outcome="blocked",
                mode=request.mode,
                sanitized_query=query_guardrail.sanitized_text,
                filters=request.filters,
                findings=query_guardrail.findings,
                retrieved_count=0,
                citations=[],
                elapsed_ms=elapsed,
                diagnostics=diagnostics,
            )
            return SearchResponse(
                answer="この検索リクエストは安全ポリシーにより処理できませんでした。",
                citations=[],
                trace_id=trace_id,
                guardrail_warnings=query_guardrail.warnings,
                elapsed_ms=elapsed,
                diagnostics=diagnostics,
            )

        error_stage = "embedding"
        retrieved: list[RetrievedChunk] = []
        ranked: list[RetrievedChunk] = []
        try:
            [vector] = await _observe_stage(
                trace_id,
                request.mode.value,
                "embedding",
                self._genai.embed([query_guardrail.sanitized_text], input_type="SEARCH_QUERY"),
                attributes={
                    "adapter": self._settings.ai_service_adapter,
                    "model": self._settings.oci_genai_embedding_model,
                    "input_type": "SEARCH_QUERY",
                    "input_count": 1,
                },
            )
            error_stage = "retrieval"
            retrieved = await _observe_stage(
                trace_id,
                request.mode.value,
                "retrieval",
                self._oracle.hybrid_search(
                    query=query_guardrail.sanitized_text,
                    embedding=vector,
                    top_k=request.top_k,
                    mode=request.mode,
                    filters=request.filters,
                ),
                attributes={
                    "mode": request.mode.value,
                    "top_k": request.top_k,
                    "filter_key_count": len(request.filters),
                },
                result_attributes=lambda chunks: {"output_count": len(chunks)},
            )
            error_stage = "rerank"
            ranked = await _observe_stage(
                trace_id,
                request.mode.value,
                "rerank",
                self._rerank(
                    query_guardrail.sanitized_text,
                    retrieved,
                    request.rerank_top_n,
                ),
                attributes={
                    "model": self._settings.oci_genai_rerank_model,
                    "input_count": len(retrieved),
                    "top_n": request.rerank_top_n,
                },
                result_attributes=lambda chunks: {"output_count": len(chunks)},
            )
            if not ranked:
                elapsed = elapsed_ms(started_at)
                diagnostics = build_search_diagnostics(
                    request,
                    settings=self._settings,
                    retrieved_count=len(retrieved),
                )
                record_rag_request(request.mode.value, "no_results", elapsed / 1000, len(retrieved))
                record_rag_search_audit(
                    trace_id=trace_id,
                    outcome="no_results",
                    mode=request.mode,
                    sanitized_query=query_guardrail.sanitized_text,
                    filters=request.filters,
                    findings=query_guardrail.findings,
                    retrieved_count=len(retrieved),
                    citations=[],
                    elapsed_ms=elapsed,
                    diagnostics=diagnostics,
                )
                return SearchResponse(
                    answer=NO_RESULTS_ANSWER,
                    citations=[],
                    trace_id=trace_id,
                    guardrail_warnings=[*query_guardrail.warnings, NO_RESULTS_WARNING],
                    elapsed_ms=elapsed,
                    diagnostics=diagnostics,
                )

            context, context_citations = _build_context_with_citations(
                ranked,
                self._settings.rag_context_window_chars,
            )
            diagnostics = build_search_diagnostics(
                request,
                settings=self._settings,
                retrieved_count=len(retrieved),
                reranked_count=len(ranked),
                citation_count=len(context_citations),
                context_chars=len(context),
            )
            error_stage = "generation"
            answer = await _observe_stage(
                trace_id,
                request.mode.value,
                "generation",
                self._llm.generate(query_guardrail.sanitized_text, context),
                attributes={
                    "adapter": self._settings.ai_service_adapter,
                    "model": self._settings.oci_enterprise_ai_llm_model or "local",
                    "context_chars": len(context),
                    "citation_count": len(context_citations),
                },
                result_attributes=lambda generated: {"answer_chars": len(generated)},
            )
            error_stage = "answer_guardrail"
            answer_guardrail = self._guardrails.validate_answer(answer, context=context)
            record_guardrail_findings(
                "answer",
                answer_guardrail.findings,
                "blocked" if not answer_guardrail.allowed else "warning",
            )
            final_answer = answer_guardrail.sanitized_text
            warnings = [*query_guardrail.warnings, *answer_guardrail.warnings]
            outcome: AuditOutcome = "success" if answer_guardrail.allowed else "blocked"
            elapsed = elapsed_ms(started_at)
            record_rag_request(request.mode.value, outcome, elapsed / 1000, len(retrieved))
            record_rag_search_audit(
                trace_id=trace_id,
                outcome=outcome,
                mode=request.mode,
                sanitized_query=query_guardrail.sanitized_text,
                filters=request.filters,
                findings=[*query_guardrail.findings, *answer_guardrail.findings],
                retrieved_count=len(retrieved),
                citations=context_citations,
                elapsed_ms=elapsed,
                diagnostics=diagnostics,
            )
            return SearchResponse(
                answer=final_answer,
                citations=context_citations,
                trace_id=trace_id,
                guardrail_warnings=warnings,
                elapsed_ms=elapsed,
                diagnostics=diagnostics,
            )
        except Exception as exc:
            elapsed = elapsed_ms(started_at)
            diagnostics = build_search_diagnostics(
                request,
                settings=self._settings,
                retrieved_count=len(retrieved),
                reranked_count=len(ranked),
                citation_count=len(ranked),
            )
            record_rag_request(request.mode.value, "error", elapsed / 1000, len(retrieved))
            record_rag_search_audit(
                trace_id=trace_id,
                outcome="error",
                mode=request.mode,
                sanitized_query=query_guardrail.sanitized_text,
                filters=request.filters,
                findings=query_guardrail.findings,
                retrieved_count=len(retrieved),
                citations=ranked,
                elapsed_ms=elapsed,
                diagnostics=diagnostics,
                error=exc,
                error_stage=error_stage,
            )
            raise

    async def _rerank(
        self,
        query: str,
        chunks: list[RetrievedChunk],
        top_n: int,
    ) -> list[RetrievedChunk]:
        """検索候補を rerank し、上位だけ返す。"""
        if not chunks:
            return []
        reranked = await self._genai.rerank(query, [chunk.text for chunk in chunks], top_n)
        by_index = {index: score for index, score in reranked}
        ranked = [
            chunk.model_copy(update={"rerank_score": by_index[index]})
            for index, chunk in enumerate(chunks)
            if index in by_index
        ]
        return sorted(
            ranked,
            key=lambda chunk: chunk.rerank_score if chunk.rerank_score is not None else chunk.score,
            reverse=True,
        )[:top_n]


def _build_context(chunks: list[RetrievedChunk], max_chars: int) -> str:
    """LLM に渡す引用コンテキストを作る。"""
    context, _ = _build_context_with_citations(chunks, max_chars)
    return context


async def _observe_stage[T](
    trace_id: str,
    mode: str,
    stage: str,
    operation: Awaitable[T],
    *,
    attributes: Mapping[str, object] | None = None,
    result_attributes: Callable[[T], Mapping[str, object]] | None = None,
) -> T:
    """非同期 stage の処理時間を outcome 付きで記録する。"""
    started_at = perf_counter()
    base_attributes = dict(attributes or {})
    try:
        result = await operation
    except asyncio.CancelledError as exc:
        elapsed = perf_counter() - started_at
        record_rag_stage(mode, stage, "cancelled", elapsed)
        record_trace_span(
            trace_id=trace_id,
            span_name=stage,
            outcome="cancelled",
            seconds=elapsed,
            attributes=base_attributes,
            error=exc,
        )
        raise
    except Exception as exc:
        elapsed = perf_counter() - started_at
        record_rag_stage(mode, stage, "error", elapsed)
        record_trace_span(
            trace_id=trace_id,
            span_name=stage,
            outcome="error",
            seconds=elapsed,
            attributes=base_attributes,
            error=exc,
        )
        raise
    elapsed = perf_counter() - started_at
    if result_attributes is not None:
        base_attributes.update(result_attributes(result))
    record_rag_stage(mode, stage, "success", elapsed)
    record_trace_span(
        trace_id=trace_id,
        span_name=stage,
        outcome="success",
        seconds=elapsed,
        attributes=base_attributes,
    )
    return result


def _build_context_with_citations(
    chunks: list[RetrievedChunk],
    max_chars: int,
) -> tuple[str, list[RetrievedChunk]]:
    """LLM context と、実際に context へ入った引用だけを返す。"""
    parts: list[str] = []
    citations: list[RetrievedChunk] = []
    total = 0
    separator = "\n\n---\n\n"
    for chunk in chunks:
        source = chunk.file_name or chunk.document_id
        body = f"[{source}#{chunk.chunk_id}]\n{chunk.text}"
        separator_len = len(separator) if parts else 0
        if total + separator_len + len(body) > max_chars:
            remaining = max_chars - total - separator_len
            if remaining > 0 and not parts:
                parts.append(body[:remaining])
                citations.append(chunk)
            break
        parts.append(body)
        citations.append(chunk)
        total += separator_len + len(body)
    return separator.join(parts), citations
