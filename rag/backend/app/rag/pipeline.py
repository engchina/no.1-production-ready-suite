"""検索 RAG パイプライン: 埋め込み -> ベクトル検索 -> リランク -> 生成。"""

import asyncio
import hashlib
import json
import math
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from time import perf_counter

from app.clients.oci_enterprise_ai import OciEnterpriseAiClient
from app.clients.oci_genai import OciGenAiClient
from app.clients.oracle import OracleClient, oracle_text_terms
from app.config import Settings, enterprise_ai_default_model_id, get_settings
from app.rag.agentic_adapter import resolve_agentic_adapter
from app.rag.audit import AuditOutcome, record_rag_search_audit
from app.rag.diagnostics import build_search_diagnostics
from app.rag.generation_adapter import resolve_generation_adapter, validate_structured_answer
from app.rag.graph_adapter import resolve_graph_adapter
from app.rag.grounding_adapter import GroundingAdapterParams, resolve_grounding_adapter
from app.rag.guardrails import GuardrailPolicy
from app.rag.memory_engineering import (
    BusinessContextPack,
    ContextPack,
    RetrievalPlan,
    build_business_context_pack,
    build_context_with_memory_roles,
    build_retrieval_plan,
    resolve_context_pack,
)
from app.rag.observability import (
    elapsed_ms,
    new_trace_id,
    record_guardrail_findings,
    record_rag_request,
    record_rag_stage,
    record_trace_span,
)
from app.rag.query_transform import expand_retrieval_queries
from app.rag.request_context import current_audit_request_context
from app.rag.retrieval_adapter import RetrievalAdapterParams, resolve_retrieval_adapter
from app.rag.retrieval_strategy import ResolvedRetrievalStrategy, resolve_retrieval_strategy
from app.schemas.common import JsonValue
from app.schemas.search import (
    RetrievedChunk,
    SearchMode,
    SearchRequest,
    SearchResponse,
    SearchRetrievalBreakdown,
    SearchRetrievalCandidate,
    SearchStrategy,
)

NO_RESULTS_ANSWER = (
    "検索条件に一致する根拠が見つかりませんでした。" "条件やキーワードを変えて検索してください。"
)
NO_RESULTS_WARNING = "検索条件に一致する根拠が見つかりませんでした。"
UNVERIFIED_RESULTS_WARNING = "取得候補は検証で除外されたため、回答に使える根拠がありませんでした。"
GAP_STOP_ANSWER = (
    "この検索に必要な業務スコープ(テナント / データセット / ACL / バージョン)が"
    "確定していないため、根拠検索を実行しませんでした。スコープを指定して再検索してください。"
)
GAP_STOP_WARNING = "業務スコープが未確定のため検索を停止しました(gap-stop)。"
WHITESPACE_RE = re.compile(r"\s+")
CONTEXT_SEGMENT_RE = re.compile(r"[^。！？!?\n]+[。！？!?]?")
QUERY_FEATURE_RE = re.compile(r"[a-z0-9_]{2,}|[ぁ-んァ-ン一-龯々ー]{2,}", re.IGNORECASE)
CONTEXT_DIVERSITY_NGRAM_SIZE = 3
RETRIEVAL_CANDIDATE_LIMIT = 20


@dataclass(frozen=True)
class RetrievalExecutionResult:
    """retrieval stage の実行結果と runtime routing 診断。"""

    chunks: list[RetrievedChunk]
    strategy: SearchStrategy
    graph_hit_count: int = 0
    agent_memory_hit_count: int = 0
    fallback_reason: str | None = None


@dataclass(frozen=True)
class SearchStageProgress:
    """SSE / diagnostics 用の低機密 stage progress event。"""

    trace_id: str
    stage: str
    outcome: str
    elapsed_ms: float
    attributes: Mapping[str, object]


type SearchStageProgressCallback = Callable[[SearchStageProgress], Awaitable[None]]


@dataclass(frozen=True)
class SearchTokenDelta:
    """SSE 用の回答 token/chunk delta。"""

    trace_id: str
    text: str


type SearchTokenCallback = Callable[[SearchTokenDelta], Awaitable[None]]


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
        self._genai = genai or OciGenAiClient(settings=self._settings)
        self._oracle = oracle or OracleClient(settings=self._settings)
        self._llm = llm or OciEnterpriseAiClient(settings=self._settings)
        self._guardrails = guardrails or GuardrailPolicy()

    async def run(
        self,
        request: SearchRequest,
        trace_id: str | None = None,
        progress_callback: SearchStageProgressCallback | None = None,
        token_callback: SearchTokenCallback | None = None,
    ) -> SearchResponse:
        """RAG 検索を実行する。"""
        started_at = perf_counter()
        trace_id = trace_id or new_trace_id()
        stream_stage_timings: dict[str, float] = {}
        # 配信モードを retrieval where へ伝播する(filters 経由)。fused は chunk_set 制限を外し、
        # 複数 chunk_set を横断検索する。重複は後段の source-span dedup で除去する。
        request.filters["serving_mode"] = self._settings.rag_serving_mode
        collapse_spans = self._settings.rag_serving_mode == "fused"
        query_guardrail = self._guardrails.validate_query(request.query)
        record_guardrail_findings(
            "query",
            query_guardrail.findings,
            "blocked" if not query_guardrail.allowed else "warning",
        )
        if not query_guardrail.allowed:
            elapsed = elapsed_ms(started_at)
            blocked_business_context = build_business_context_pack(request)
            diagnostics = build_search_diagnostics(
                request,
                settings=self._settings,
                business_context=blocked_business_context.diagnostics(),
            )
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
        reranked_count = 0
        deduplicated_count = 0
        context_diversified_count = 0
        context_group_expanded_count = 0
        context_expanded_count = 0
        context_adaptive_expanded_count = 0
        context_dependency_promoted_count = 0
        context_compressed_count = 0
        context_compression_saved_chars = 0
        agent_memory_retrieved_count = 0
        agent_memory_writeback_count = 0
        agent_memory_writeback_status = "skipped"
        query_variant_count = 1
        keyword_terms: list[str] = []
        business_context: BusinessContextPack = build_business_context_pack(request)
        retrieval_plan: RetrievalPlan | None = None
        context_pack: ContextPack | None = None
        context_builder_diagnostics: dict[str, object] = {}
        runtime_retrieval_strategy = "hybrid"
        runtime_fallback_reason: str | None = None
        runtime_graph_hit_count = 0
        business_fit_reordered_count = 0
        corrective_retried = False
        crag_confidence_score: float | None = None
        crag_fallback_triggered = False
        hyde_generated = False
        agentic_subquery_count = 0
        agentic_hops = 0
        graph_profile = resolve_graph_adapter(self._settings).profile
        retrieval_params: RetrievalAdapterParams = resolve_retrieval_adapter(self._settings)
        grounding_params: GroundingAdapterParams = resolve_grounding_adapter(self._settings)
        agentic_params = resolve_agentic_adapter(self._settings)
        effective_request = _apply_retrieval_adapter_request(request, retrieval_params)
        resolved_strategy = resolve_retrieval_strategy(
            effective_request,
            settings=self._settings,
            query=query_guardrail.sanitized_text,
        )
        keyword_terms = oracle_text_terms(query_guardrail.sanitized_text)
        runtime_retrieval_strategy = resolved_strategy.strategy.value
        runtime_fallback_reason = resolved_strategy.fallback_reason
        runtime_graph_hit_count = resolved_strategy.graph_hit_count
        if retrieval_params.gap_stop and not _business_context_scope_pinned(business_context):
            elapsed = elapsed_ms(started_at)
            diagnostics = build_search_diagnostics(
                effective_request,
                settings=self._settings,
                retrieval_strategy=runtime_retrieval_strategy,
                retrieval_strategy_adapter=retrieval_params.strategy,
                post_retrieval_pipeline=grounding_params.pipeline,
                generation_profile=self._settings.rag_generation_profile,
                guardrail_policy=self._settings.rag_guardrail_policy,
                vector_index_profile=self._settings.rag_vector_index_profile,
                graph_profile=graph_profile,
                agentic_profile=agentic_params.profile,
                agentic_subquery_count=agentic_subquery_count,
                agentic_hops=agentic_hops,
                corrective_retried=corrective_retried,
                crag_confidence_score=crag_confidence_score,
                crag_fallback_triggered=crag_fallback_triggered,
                hyde_generated=hyde_generated,
                route_reason=resolved_strategy.route_reason,
                keyword_terms=keyword_terms,
                fallback_reason="gap_stop_scope_unresolved",
                business_context=business_context.diagnostics(),
                gap_stopped=True,
            )
            record_rag_request(resolved_strategy.mode.value, "no_results", elapsed / 1000, 0)
            record_rag_search_audit(
                trace_id=trace_id,
                outcome="no_results",
                mode=resolved_strategy.mode,
                sanitized_query=query_guardrail.sanitized_text,
                filters=request.filters,
                findings=query_guardrail.findings,
                retrieved_count=0,
                citations=[],
                elapsed_ms=elapsed,
                diagnostics=diagnostics,
            )
            return SearchResponse(
                answer=GAP_STOP_ANSWER,
                citations=[],
                trace_id=trace_id,
                guardrail_warnings=[*query_guardrail.warnings, GAP_STOP_WARNING],
                elapsed_ms=elapsed,
                diagnostics=diagnostics,
            )
        try:
            query_variants = expand_retrieval_queries(
                query_guardrail.sanitized_text,
                enabled=retrieval_params.query_expansion,
                max_variants=self._settings.rag_query_expansion_max_variants,
            )
            if not query_variants:
                query_variants = [query_guardrail.sanitized_text]
            if agentic_params.enabled:
                error_stage = "agentic_planning"
                planned = await self._llm.plan_query(
                    query_guardrail.sanitized_text,
                    mode=agentic_params.profile,
                    max_subqueries=agentic_params.max_subqueries,
                )
                if planned:
                    agentic_subquery_count = len(planned)
                    agentic_hops = 1
                    if agentic_params.hyde:
                        # HyDE: 仮説文書を主検索クエリにし、元クエリも残す。
                        hyde_generated = True
                    if agentic_params.rewrite:
                        query_variants = _dedupe_strings([planned[0], *query_variants])
                    else:
                        query_variants = _dedupe_strings([*query_variants, *planned])
            query_variant_count = len(query_variants)
            retrieval_plan = build_retrieval_plan(
                trace_id=trace_id,
                request=request,
                business_context=business_context,
                resolved_strategy=resolved_strategy,
                query_variant_count=query_variant_count,
            )
            vectors: list[list[float]] = []
            if resolved_strategy.mode != SearchMode.KEYWORD:
                vectors = await _observe_stage(
                    trace_id,
                    request.mode.value,
                    "embedding",
                    self._genai.embed(query_variants, input_type="SEARCH_QUERY"),
                    attributes={
                        "model": self._settings.oci_genai_embedding_model,
                        "input_type": "SEARCH_QUERY",
                        "input_count": query_variant_count,
                        "query_variant_count": query_variant_count,
                    },
                    result_attributes=lambda vectors: {"output_count": len(vectors)},
                    progress_callback=progress_callback,
                    stage_timings=stream_stage_timings,
                )
            error_stage = "retrieval"
            retrieval_result = await _observe_stage(
                trace_id,
                resolved_strategy.mode.value,
                "retrieval",
                self._retrieve_with_strategy(
                    query_variants=query_variants,
                    vectors=vectors,
                    request=effective_request,
                    resolved_strategy=resolved_strategy,
                ),
                attributes={
                    "mode": resolved_strategy.mode.value,
                    "strategy": resolved_strategy.strategy.value,
                    "top_k": request.top_k,
                    "filter_key_count": len(request.filters),
                    "query_variant_count": query_variant_count,
                    "memory_plan_id": retrieval_plan.plan_id,
                    "memory_type_count": len(retrieval_plan.memory_sequence),
                },
                result_attributes=lambda result: {
                    "output_count": len(result.chunks),
                    "runtime_strategy": result.strategy.value,
                    "graph_hit_count": result.graph_hit_count,
                    "agent_memory_hit_count": result.agent_memory_hit_count,
                    "fallback_reason": result.fallback_reason or "",
                },
                progress_callback=progress_callback,
                stage_timings=stream_stage_timings,
            )
            retrieved = retrieval_result.chunks
            runtime_retrieval_strategy = retrieval_result.strategy.value
            runtime_fallback_reason = retrieval_result.fallback_reason
            runtime_graph_hit_count = retrieval_result.graph_hit_count
            agent_memory_retrieved_count = retrieval_result.agent_memory_hit_count
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
                progress_callback=progress_callback,
                stage_timings=stream_stage_timings,
            )
            reranked_count = len(ranked)
            if not ranked:
                elapsed = elapsed_ms(started_at)
                retrieval_breakdown, retrieval_candidates = _build_retrieval_diagnostics(
                    request_mode=resolved_strategy.mode,
                    retrieved=retrieved,
                    ranked=ranked,
                    citations=[],
                )
                diagnostics = build_search_diagnostics(
                    request,
                    settings=self._settings,
                    retrieval_strategy=runtime_retrieval_strategy,
                    route_reason=resolved_strategy.route_reason,
                    keyword_terms=keyword_terms,
                    retrieval_breakdown=retrieval_breakdown,
                    retrieval_candidates=retrieval_candidates,
                    retrieval_strategy_adapter=retrieval_params.strategy,
                    post_retrieval_pipeline=grounding_params.pipeline,
                    generation_profile=self._settings.rag_generation_profile,
                    guardrail_policy=self._settings.rag_guardrail_policy,
                    vector_index_profile=self._settings.rag_vector_index_profile,
                    graph_profile=graph_profile,
                    agentic_profile=agentic_params.profile,
                    agentic_subquery_count=agentic_subquery_count,
                    agentic_hops=agentic_hops,
                    corrective_retried=corrective_retried,
                    crag_confidence_score=crag_confidence_score,
                    crag_fallback_triggered=crag_fallback_triggered,
                    hyde_generated=hyde_generated,
                    memory_plan_id=retrieval_plan.plan_id,
                    graph_hit_count=runtime_graph_hit_count,
                    fallback_reason=runtime_fallback_reason,
                    business_context=business_context.diagnostics(),
                    retrieval_plan=retrieval_plan.diagnostics(),
                    stream_stage_timings=stream_stage_timings,
                    retrieved_count=len(retrieved),
                    agent_memory_retrieved_count=agent_memory_retrieved_count,
                    query_variant_count=query_variant_count,
                )
                record_rag_request(
                    resolved_strategy.mode.value,
                    "no_results",
                    elapsed / 1000,
                    len(retrieved),
                )
                record_rag_search_audit(
                    trace_id=trace_id,
                    outcome="no_results",
                    mode=resolved_strategy.mode,
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

            # CRAG: grounding preset が corrective(verified_context/full_governed)で、rerank の
            # 最高スコアが閾値未満なら query を書き換えて 1 回だけ corrective 再検索する。
            crag_confidence_score = _crag_confidence(ranked)
            crag_threshold = float(
                getattr(self._settings, "rag_grounding_crag_confidence_threshold", 0.0)
            )
            if (
                grounding_params.corrective_enabled
                and not corrective_retried
                and crag_threshold > 0.0
                and crag_confidence_score < crag_threshold
            ):
                corrective_retried = True
                crag_fallback_triggered = True
                error_stage = "crag_corrective"
                rewritten = await self._llm.plan_query(
                    query_guardrail.sanitized_text,
                    mode="query_rewrite",
                    max_subqueries=agentic_params.max_subqueries,
                )
                crag_variants = (
                    _dedupe_strings([*rewritten, *query_variants]) if rewritten else query_variants
                )
                crag_vectors = await self._genai.embed(crag_variants, input_type="SEARCH_QUERY")
                crag_result = await self._retrieve_with_strategy(
                    query_variants=crag_variants,
                    vectors=crag_vectors,
                    request=effective_request,
                    resolved_strategy=resolved_strategy,
                )
                crag_ranked = await self._rerank(
                    query_guardrail.sanitized_text,
                    crag_result.chunks,
                    request.rerank_top_n,
                )
                crag_new_confidence = _crag_confidence(crag_ranked)
                if crag_ranked and crag_new_confidence > crag_confidence_score:
                    # 書き換え後の方が信頼度が高ければ採用する。
                    ranked = crag_ranked
                    retrieved = crag_result.chunks
                    crag_confidence_score = crag_new_confidence

            if retrieval_params.business_fit_weighting:
                error_stage = "business_fit_weighting"
                ranked, business_fit_reordered_count = _apply_business_fit_weighting(ranked)
            if grounding_params.dependency_promotion_enabled:
                error_stage = "context_dependency_promotion"
                ranked, context_dependency_promoted_count = await _observe_stage(
                    trace_id,
                    request.mode.value,
                    "context_dependency_promotion",
                    self._promote_dependency_linked_context(ranked, retrieved),
                    attributes={
                        "anchor_count": len(ranked),
                        "candidate_count": len(retrieved),
                        "max_chunks_per_anchor": (self._settings.rag_context_dependency_max_chunks),
                    },
                    result_attributes=lambda item: {
                        "promoted_count": item[1],
                        "output_count": len(item[0]),
                    },
                    progress_callback=progress_callback,
                    stage_timings=stream_stage_timings,
                )
            packed_chunks, deduplicated_count = _dedupe_ranked_chunks(
                ranked, collapse_overlapping_spans=collapse_spans
            )
            if grounding_params.diversity_enabled:
                error_stage = "context_diversity"
                packed_chunks, context_diversified_count = await _observe_stage(
                    trace_id,
                    request.mode.value,
                    "context_diversity",
                    self._diversify_context_anchors(
                        packed_chunks,
                        grounding_params.diversity_lambda,
                    ),
                    attributes={
                        "lambda": grounding_params.diversity_lambda,
                        "input_count": len(packed_chunks),
                    },
                    result_attributes=lambda item: {
                        "reordered_count": item[1],
                        "output_count": len(item[0]),
                    },
                    progress_callback=progress_callback,
                    stage_timings=stream_stage_timings,
                )
            if grounding_params.expansion_mode == "adaptive":
                error_stage = "context_adaptive_expansion"
                packed_chunks, context_adaptive_expanded_count = await _observe_stage(
                    trace_id,
                    request.mode.value,
                    "context_adaptive_expansion",
                    self._expand_context_adaptively(
                        packed_chunks,
                        query_guardrail.sanitized_text,
                    ),
                    attributes={
                        "input_count": len(packed_chunks),
                        "neighbor_window": (self._settings.rag_context_adaptive_neighbor_window),
                        "max_chunks_per_group": (self._settings.rag_context_group_max_chunks),
                        "min_overlap": self._settings.rag_context_adaptive_min_overlap,
                    },
                    result_attributes=lambda item: {
                        "expanded_count": item[1],
                        "output_count": len(item[0]),
                    },
                    progress_callback=progress_callback,
                    stage_timings=stream_stage_timings,
                )
            elif grounding_params.expansion_mode == "group":
                error_stage = "context_group_expansion"
                packed_chunks, context_group_expanded_count = await _observe_stage(
                    trace_id,
                    request.mode.value,
                    "context_group_expansion",
                    self._expand_context_group_siblings(packed_chunks),
                    attributes={
                        "input_count": len(packed_chunks),
                        "max_chunks_per_group": (self._settings.rag_context_group_max_chunks),
                    },
                    result_attributes=lambda item: {
                        "expanded_count": item[1],
                        "output_count": len(item[0]),
                    },
                    progress_callback=progress_callback,
                    stage_timings=stream_stage_timings,
                )
            if grounding_params.neighbor_expansion_enabled:
                error_stage = "context_expansion"
                packed_chunks, context_expanded_count = await _observe_stage(
                    trace_id,
                    request.mode.value,
                    "context_expansion",
                    self._expand_context_neighbors(packed_chunks),
                    attributes={
                        "neighbor_window": self._settings.rag_context_neighbor_window,
                        "anchor_count": len(packed_chunks),
                    },
                    result_attributes=lambda item: {
                        "expanded_count": item[1],
                        "output_count": len(item[0]),
                    },
                    progress_callback=progress_callback,
                    stage_timings=stream_stage_timings,
                )
            if grounding_params.compression_enabled:
                error_stage = "context_compression"
                (
                    packed_chunks,
                    context_compressed_count,
                    context_compression_saved_chars,
                ) = await _observe_stage(
                    trace_id,
                    request.mode.value,
                    "context_compression",
                    self._compress_context_chunks(
                        packed_chunks,
                        query_guardrail.sanitized_text,
                    ),
                    attributes={
                        "input_count": len(packed_chunks),
                        "max_sentences": (self._settings.rag_context_compression_max_sentences),
                        "max_chars_per_chunk": (
                            self._settings.rag_context_compression_max_chars_per_chunk
                        ),
                    },
                    result_attributes=lambda item: {
                        "compressed_count": item[1],
                        "saved_chars": item[2],
                        "output_count": len(item[0]),
                    },
                    progress_callback=progress_callback,
                    stage_timings=stream_stage_timings,
                )
            if retrieval_plan is None:
                raise RuntimeError("retrieval plan が初期化されていません。")
            context_pack = resolve_context_pack(packed_chunks, plan=retrieval_plan)
            if (
                retrieval_params.corrective_retrieval
                and context_pack.evidence_count == 0
                and not corrective_retried
            ):
                corrective_retried = True
                error_stage = "corrective_retrieval"
                relaxed_request = _relaxed_corrective_request(request)
                corrective_result = await self._retrieve_with_strategy(
                    query_variants=query_variants,
                    vectors=vectors,
                    request=relaxed_request,
                    resolved_strategy=resolved_strategy,
                )
                corrective_ranked = await self._rerank(
                    query_guardrail.sanitized_text,
                    corrective_result.chunks,
                    request.rerank_top_n,
                )
                if corrective_ranked:
                    corrective_packed, _ = _dedupe_ranked_chunks(
                        corrective_ranked, collapse_overlapping_spans=collapse_spans
                    )
                    corrective_pack = resolve_context_pack(
                        corrective_packed,
                        plan=retrieval_plan,
                    )
                    if corrective_pack.evidence_count > 0:
                        retrieved = corrective_result.chunks
                        packed_chunks = corrective_packed
                        context_pack = corrective_pack
            if (
                agentic_params.multi_hop
                and context_pack.evidence_count == 0
                and not corrective_retried
            ):
                corrective_retried = True
                error_stage = "agentic_multi_hop"
                hop_queries = await self._llm.plan_query(
                    query_guardrail.sanitized_text,
                    mode="decompose",
                    max_subqueries=agentic_params.max_subqueries,
                )
                if hop_queries:
                    agentic_hops += 1
                    hop_variants = _dedupe_strings([*query_variants, *hop_queries])
                    hop_vectors = await self._genai.embed(
                        hop_variants,
                        input_type="SEARCH_QUERY",
                    )
                    hop_result = await self._retrieve_with_strategy(
                        query_variants=hop_variants,
                        vectors=hop_vectors,
                        request=effective_request,
                        resolved_strategy=resolved_strategy,
                    )
                    hop_ranked = await self._rerank(
                        query_guardrail.sanitized_text,
                        hop_result.chunks,
                        request.rerank_top_n,
                    )
                    if hop_ranked:
                        hop_packed, _ = _dedupe_ranked_chunks(
                            hop_ranked, collapse_overlapping_spans=collapse_spans
                        )
                        hop_pack = resolve_context_pack(hop_packed, plan=retrieval_plan)
                        if hop_pack.evidence_count > 0:
                            retrieved = hop_result.chunks
                            packed_chunks = hop_packed
                            context_pack = hop_pack
            if not context_pack.chunks:
                elapsed = elapsed_ms(started_at)
                retrieval_breakdown, retrieval_candidates = _build_retrieval_diagnostics(
                    request_mode=resolved_strategy.mode,
                    retrieved=retrieved,
                    ranked=ranked,
                    citations=[],
                )
                diagnostics = build_search_diagnostics(
                    request,
                    settings=self._settings,
                    retrieval_strategy=runtime_retrieval_strategy,
                    route_reason=resolved_strategy.route_reason,
                    keyword_terms=keyword_terms,
                    retrieval_breakdown=retrieval_breakdown,
                    retrieval_candidates=retrieval_candidates,
                    retrieval_strategy_adapter=retrieval_params.strategy,
                    post_retrieval_pipeline=grounding_params.pipeline,
                    generation_profile=self._settings.rag_generation_profile,
                    guardrail_policy=self._settings.rag_guardrail_policy,
                    vector_index_profile=self._settings.rag_vector_index_profile,
                    graph_profile=graph_profile,
                    agentic_profile=agentic_params.profile,
                    agentic_subquery_count=agentic_subquery_count,
                    agentic_hops=agentic_hops,
                    corrective_retried=corrective_retried,
                    crag_confidence_score=crag_confidence_score,
                    crag_fallback_triggered=crag_fallback_triggered,
                    hyde_generated=hyde_generated,
                    memory_plan_id=retrieval_plan.plan_id,
                    graph_hit_count=runtime_graph_hit_count,
                    fallback_reason=runtime_fallback_reason,
                    business_context=business_context.diagnostics(),
                    retrieval_plan=retrieval_plan.diagnostics(),
                    retrieved_context_pack=context_pack.diagnostics(),
                    stream_stage_timings=stream_stage_timings,
                    retrieved_count=len(retrieved),
                    reranked_count=reranked_count,
                    deduplicated_count=deduplicated_count,
                    context_diversified_count=context_diversified_count,
                    context_group_expanded_count=context_group_expanded_count,
                    context_expanded_count=context_expanded_count,
                    context_adaptive_expanded_count=context_adaptive_expanded_count,
                    context_dependency_promoted_count=context_dependency_promoted_count,
                    context_compressed_count=context_compressed_count,
                    context_compression_saved_chars=context_compression_saved_chars,
                    business_fit_reordered_count=business_fit_reordered_count,
                    agent_memory_retrieved_count=agent_memory_retrieved_count,
                    resolver_rejected_count=context_pack.rejected_count,
                    insufficient_context_count=context_pack.insufficient_count,
                    query_variant_count=query_variant_count,
                )
                record_rag_request(
                    resolved_strategy.mode.value,
                    "no_results",
                    elapsed / 1000,
                    len(retrieved),
                )
                record_rag_search_audit(
                    trace_id=trace_id,
                    outcome="no_results",
                    mode=resolved_strategy.mode,
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
                    guardrail_warnings=[
                        *query_guardrail.warnings,
                        NO_RESULTS_WARNING,
                        UNVERIFIED_RESULTS_WARNING,
                    ],
                    elapsed_ms=elapsed,
                    diagnostics=diagnostics,
                )
            built_context = build_context_with_memory_roles(
                context_pack.chunks,
                self._settings.rag_context_window_chars,
            )
            context = built_context.context
            context_citations = built_context.citations
            context_builder_diagnostics = built_context.diagnostics()
            retrieval_breakdown, retrieval_candidates = _build_retrieval_diagnostics(
                request_mode=resolved_strategy.mode,
                retrieved=retrieved,
                ranked=ranked,
                citations=context_citations,
                evidence_count=built_context.evidence_count,
            )
            diagnostics = build_search_diagnostics(
                request,
                settings=self._settings,
                retrieval_strategy=runtime_retrieval_strategy,
                route_reason=resolved_strategy.route_reason,
                keyword_terms=keyword_terms,
                retrieval_breakdown=retrieval_breakdown,
                retrieval_candidates=retrieval_candidates,
                retrieval_strategy_adapter=retrieval_params.strategy,
                post_retrieval_pipeline=grounding_params.pipeline,
                generation_profile=self._settings.rag_generation_profile,
                guardrail_policy=self._settings.rag_guardrail_policy,
                vector_index_profile=self._settings.rag_vector_index_profile,
                graph_profile=graph_profile,
                agentic_profile=agentic_params.profile,
                agentic_subquery_count=agentic_subquery_count,
                agentic_hops=agentic_hops,
                corrective_retried=corrective_retried,
                crag_confidence_score=crag_confidence_score,
                crag_fallback_triggered=crag_fallback_triggered,
                hyde_generated=hyde_generated,
                memory_plan_id=retrieval_plan.plan_id,
                graph_hit_count=runtime_graph_hit_count,
                fallback_reason=runtime_fallback_reason,
                business_context=business_context.diagnostics(),
                retrieval_plan=retrieval_plan.diagnostics(),
                retrieved_context_pack=context_pack.diagnostics(),
                context_builder=context_builder_diagnostics,
                stream_stage_timings=stream_stage_timings,
                retrieved_count=len(retrieved),
                reranked_count=reranked_count,
                deduplicated_count=deduplicated_count,
                context_diversified_count=context_diversified_count,
                context_group_expanded_count=context_group_expanded_count,
                context_expanded_count=context_expanded_count,
                context_adaptive_expanded_count=context_adaptive_expanded_count,
                context_dependency_promoted_count=context_dependency_promoted_count,
                context_compressed_count=context_compressed_count,
                context_compression_saved_chars=context_compression_saved_chars,
                business_fit_reordered_count=business_fit_reordered_count,
                agent_memory_retrieved_count=agent_memory_retrieved_count,
                evidence_count=built_context.evidence_count,
                support_count=built_context.support_count,
                structure_count=built_context.structure_count,
                history_count=built_context.history_count,
                resolver_rejected_count=context_pack.rejected_count,
                insufficient_context_count=context_pack.insufficient_count,
                citation_count=len(context_citations),
                context_chars=len(context),
                query_variant_count=query_variant_count,
            )
            error_stage = "generation"
            stream_generation = self._settings.rag_stream_realtime_enabled and token_callback
            answer = await _observe_stage(
                trace_id,
                request.mode.value,
                "generation",
                self._generate_answer(
                    query_guardrail.sanitized_text,
                    context,
                    trace_id=trace_id,
                    token_callback=token_callback if stream_generation else None,
                ),
                attributes={
                    "model": enterprise_ai_default_model_id(self._settings),
                    "context_chars": len(context),
                    "citation_count": len(context_citations),
                    "streaming_enabled": bool(stream_generation),
                },
                result_attributes=lambda generated: {"answer_chars": len(generated)},
                progress_callback=progress_callback,
                stage_timings=stream_stage_timings,
            )
            diagnostics = diagnostics.model_copy(
                update={"stream_stage_timings": dict(stream_stage_timings)}
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
            if answer_guardrail.allowed:
                agent_memory_writeback_count, agent_memory_writeback_status = (
                    await self._write_agent_memory(
                        trace_id=trace_id,
                        answer=final_answer,
                        citations=context_citations,
                        retrieval_plan=retrieval_plan,
                    )
                )
                diagnostics = diagnostics.model_copy(
                    update={
                        "agent_memory_writeback_count": agent_memory_writeback_count,
                        "agent_memory_writeback_status": agent_memory_writeback_status,
                    }
                )
            elapsed = elapsed_ms(started_at)
            record_rag_request(
                resolved_strategy.mode.value,
                outcome,
                elapsed / 1000,
                len(retrieved),
            )
            record_rag_search_audit(
                trace_id=trace_id,
                outcome=outcome,
                mode=resolved_strategy.mode,
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
                # マスク/ブロックで本文が変化したか。realtime stream 済みのとき
                # マスク済み本文を置換イベントで再送する判定に使う。
                answer_replaced=final_answer != answer,
            )
        except Exception as exc:
            elapsed = elapsed_ms(started_at)
            retrieval_breakdown, retrieval_candidates = _build_retrieval_diagnostics(
                request_mode=resolved_strategy.mode,
                retrieved=retrieved,
                ranked=ranked,
                citations=ranked,
            )
            diagnostics = build_search_diagnostics(
                request,
                settings=self._settings,
                retrieval_strategy=runtime_retrieval_strategy,
                route_reason=resolved_strategy.route_reason,
                keyword_terms=keyword_terms,
                retrieval_breakdown=retrieval_breakdown,
                retrieval_candidates=retrieval_candidates,
                retrieval_strategy_adapter=retrieval_params.strategy,
                post_retrieval_pipeline=grounding_params.pipeline,
                generation_profile=self._settings.rag_generation_profile,
                guardrail_policy=self._settings.rag_guardrail_policy,
                vector_index_profile=self._settings.rag_vector_index_profile,
                graph_profile=graph_profile,
                agentic_profile=agentic_params.profile,
                agentic_subquery_count=agentic_subquery_count,
                agentic_hops=agentic_hops,
                corrective_retried=corrective_retried,
                crag_confidence_score=crag_confidence_score,
                crag_fallback_triggered=crag_fallback_triggered,
                hyde_generated=hyde_generated,
                memory_plan_id=retrieval_plan.plan_id if retrieval_plan is not None else None,
                graph_hit_count=runtime_graph_hit_count,
                fallback_reason=runtime_fallback_reason,
                business_context=business_context.diagnostics(),
                retrieval_plan=(
                    retrieval_plan.diagnostics() if retrieval_plan is not None else None
                ),
                retrieved_context_pack=(
                    context_pack.diagnostics() if context_pack is not None else None
                ),
                context_builder=context_builder_diagnostics,
                stream_stage_timings=stream_stage_timings,
                retrieved_count=len(retrieved),
                reranked_count=reranked_count,
                deduplicated_count=deduplicated_count,
                context_diversified_count=context_diversified_count,
                context_group_expanded_count=context_group_expanded_count,
                context_expanded_count=context_expanded_count,
                context_adaptive_expanded_count=context_adaptive_expanded_count,
                context_dependency_promoted_count=context_dependency_promoted_count,
                context_compressed_count=context_compressed_count,
                context_compression_saved_chars=context_compression_saved_chars,
                business_fit_reordered_count=business_fit_reordered_count,
                agent_memory_retrieved_count=agent_memory_retrieved_count,
                agent_memory_writeback_count=agent_memory_writeback_count,
                agent_memory_writeback_status=agent_memory_writeback_status,
                resolver_rejected_count=(
                    context_pack.rejected_count if context_pack is not None else 0
                ),
                insufficient_context_count=(
                    context_pack.insufficient_count if context_pack is not None else 0
                ),
                citation_count=len(ranked),
                query_variant_count=query_variant_count,
            )
            record_rag_request(
                resolved_strategy.mode.value,
                "error",
                elapsed / 1000,
                len(retrieved),
            )
            record_rag_search_audit(
                trace_id=trace_id,
                outcome="error",
                mode=resolved_strategy.mode,
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
        history_chunks = [chunk for chunk in chunks if _is_agent_memory_chunk(chunk)]
        rerank_candidates = [chunk for chunk in chunks if not _is_agent_memory_chunk(chunk)]
        if not rerank_candidates:
            return history_chunks[:top_n]
        # OCI Rerank は top_n <= documents 数を要求するため候補数でクランプする。
        top_n = min(top_n, len(rerank_candidates))
        reranked = await self._genai.rerank(
            query,
            [chunk.text for chunk in rerank_candidates],
            top_n,
        )
        by_index = {index: score for index, score in reranked}
        ranked = [
            chunk.model_copy(update={"rerank_score": by_index[index]})
            for index, chunk in enumerate(rerank_candidates)
            if index in by_index
        ]
        ranked_context = sorted(
            ranked,
            key=lambda chunk: chunk.rerank_score if chunk.rerank_score is not None else chunk.score,
            reverse=True,
        )[:top_n]
        ranked_context = [
            chunk.model_copy(update={"metadata": {**chunk.metadata, "rerank_rank": rank}})
            for rank, chunk in enumerate(ranked_context, start=1)
        ]
        return [*ranked_context, *history_chunks]

    async def _generate_answer(
        self,
        query: str,
        context: str,
        *,
        trace_id: str,
        token_callback: SearchTokenCallback | None,
    ) -> str:
        """回答生成を通常呼び出しまたは Enterprise AI stream で実行する。"""
        generation_params = resolve_generation_adapter(self._settings)
        system_prompt = generation_params.system_prompt
        if token_callback is None:
            if system_prompt is None:
                answer = await self._llm.generate(query, context)
            else:
                answer = await self._llm.generate(query, context, system_prompt=system_prompt)
        else:
            stream = (
                self._llm.generate_stream(query, context)
                if system_prompt is None
                else self._llm.generate_stream(query, context, system_prompt=system_prompt)
            )
            chunks: list[str] = []
            async for chunk in stream:
                if not chunk:
                    continue
                chunks.append(chunk)
                await token_callback(SearchTokenDelta(trace_id=trace_id, text=chunk))
            if not chunks:
                raise ValueError("OCI Enterprise AI stream に回答 text がありません。")
            answer = "".join(chunks)
        # structured_json プロファイルは生成結果を JSON スキーマで検証する(AGENTS ルール 4)。
        # ストリーム時はトークンが検証前にクライアントへ届くため、失敗はストリーム後に表面化する。
        if generation_params.structured_output:
            answer = validate_structured_answer(answer)
        return answer

    async def _write_agent_memory(
        self,
        *,
        trace_id: str,
        answer: str,
        citations: list[RetrievedChunk],
        retrieval_plan: RetrievalPlan,
    ) -> tuple[int, str]:
        """根拠付き回答を scoped Agent Memory として Oracle 26ai へ writeback する。"""
        if (
            not self._settings.rag_agent_memory_writeback_enabled
            or not _agent_memory_scope_available()
            or not citations
            or _oracle_method_is_inherited(self._oracle, "save_agent_memory")
        ):
            return 0, "skipped"
        memory_text = _build_agent_memory_text(
            answer,
            citations,
            max_chars=self._settings.rag_agent_memory_max_chars,
        )
        if not memory_text:
            return 0, "skipped"
        try:
            vectors = await self._genai.embed([memory_text], input_type="SEARCH_DOCUMENT")
            if not vectors:
                return 0, "failed"
            saved_id = await self._oracle.save_agent_memory(
                {
                    "trace_id": trace_id,
                    "memory_text": memory_text,
                    "metadata": {
                        "memory_plan_id": retrieval_plan.plan_id,
                        "citation_count": len(citations),
                        "citation_ids": _citation_ids(citations),
                        "citation_document_ids": _citation_document_ids(citations),
                        "knowledge_base_ids": _citation_knowledge_base_ids(citations),
                        "source": "rag_answer_writeback",
                    },
                    "usefulness_score": 0.5,
                },
                vectors[0],
            )
        except Exception:
            return 0, "failed"
        return (1, "saved") if saved_id else (0, "skipped")

    async def _promote_dependency_linked_context(
        self,
        anchors: list[RetrievedChunk],
        candidates: list[RetrievedChunk],
    ) -> tuple[list[RetrievedChunk], int]:
        """retrieved pool から parent/child lineage で結びつく chunk を昇格する。"""
        dependency_candidates = list(candidates)
        if not _oracle_method_is_inherited(self._oracle, "context_dependency_chunks"):
            dependency_candidates.extend(
                await self._oracle.context_dependency_chunks(
                    anchors,
                    max_chunks_per_anchor=self._settings.rag_context_dependency_max_chunks,
                )
            )
        return _promote_dependency_linked_context(
            anchors,
            dependency_candidates,
            max_chunks_per_anchor=self._settings.rag_context_dependency_max_chunks,
        )

    async def _expand_context_neighbors(
        self,
        chunks: list[RetrievedChunk],
    ) -> tuple[list[RetrievedChunk], int]:
        """rerank anchor の隣接 chunk を context 候補に加える。"""
        window = self._settings.rag_context_neighbor_window
        if window <= 0 or not chunks:
            return chunks, 0
        neighbors = await self._oracle.context_neighbors(chunks, window=window)
        return _interleave_context_neighbors(chunks, neighbors)

    async def _expand_context_group_siblings(
        self,
        chunks: list[RetrievedChunk],
    ) -> tuple[list[RetrievedChunk], int]:
        """同じ親 chunk group の sibling を context 候補に加える。"""
        max_chunks = self._settings.rag_context_group_max_chunks
        if max_chunks <= 0 or not chunks:
            return chunks, 0
        siblings = await self._oracle.context_group_siblings(
            chunks,
            max_chunks_per_group=max_chunks,
        )
        return _interleave_context_group_siblings(chunks, siblings)

    async def _expand_context_adaptively(
        self,
        chunks: list[RetrievedChunk],
        query: str,
    ) -> tuple[list[RetrievedChunk], int]:
        """query relevance と構造連続性で必要な context chunk だけを追加する。"""
        if not chunks:
            return chunks, 0
        candidates: list[RetrievedChunk] = []
        if not _oracle_method_is_inherited(self._oracle, "context_group_siblings"):
            candidates.extend(
                await self._oracle.context_group_siblings(
                    chunks,
                    max_chunks_per_group=self._settings.rag_context_group_max_chunks,
                )
            )
        window = self._settings.rag_context_adaptive_neighbor_window
        if window > 0 and not _oracle_method_is_inherited(self._oracle, "context_neighbors"):
            candidates.extend(await self._oracle.context_neighbors(chunks, window=window))
        return _interleave_adaptive_context(
            chunks,
            candidates,
            query=query,
            min_overlap=self._settings.rag_context_adaptive_min_overlap,
        )

    async def _diversify_context_anchors(
        self,
        chunks: list[RetrievedChunk],
        diversity_lambda: float | None = None,
    ) -> tuple[list[RetrievedChunk], int]:
        """MMR 風に rerank anchor を並べ替え、context window の冗長化を抑える。"""
        effective_lambda = (
            diversity_lambda
            if diversity_lambda is not None
            else self._settings.rag_context_diversity_lambda
        )
        return _diversify_context_anchors(
            chunks,
            diversity_lambda=effective_lambda,
        )

    async def _compress_context_chunks(
        self,
        chunks: list[RetrievedChunk],
        query: str,
    ) -> tuple[list[RetrievedChunk], int, int]:
        """query に関連する sentence/line を残して LLM context 用 chunk を圧縮する。"""
        return _compress_context_chunks(
            chunks,
            query=query,
            max_sentences=self._settings.rag_context_compression_max_sentences,
            max_chars_per_chunk=(self._settings.rag_context_compression_max_chars_per_chunk),
        )

    async def _retrieve_with_strategy(
        self,
        *,
        query_variants: list[str],
        vectors: list[list[float]],
        request: SearchRequest,
        resolved_strategy: ResolvedRetrievalStrategy,
    ) -> RetrievalExecutionResult:
        """resolved strategy に応じて GraphRAG-lite または baseline retrieval を実行する。"""
        if resolved_strategy.strategy not in (
            SearchStrategy.GRAPH_LOCAL,
            SearchStrategy.GRAPH_GLOBAL,
        ):
            chunks = await self._retrieve_with_query_variants(
                query_variants=query_variants,
                vectors=vectors,
                request=request,
                mode=resolved_strategy.mode,
            )
            agent_memory_hits = await self._retrieve_agent_memory(
                query_variants=query_variants,
                vectors=vectors,
                request=request,
            )
            return RetrievalExecutionResult(
                chunks=[*chunks, *agent_memory_hits],
                strategy=resolved_strategy.strategy,
                graph_hit_count=resolved_strategy.graph_hit_count,
                agent_memory_hit_count=len(agent_memory_hits),
                fallback_reason=resolved_strategy.fallback_reason,
            )

        graph_query = query_variants[0] if query_variants else request.query
        graph_hits, graph_fallback_reason = await self._graph_search(
            strategy=resolved_strategy.strategy,
            query=graph_query,
            top_k=request.top_k,
            filters=request.filters,
        )
        if graph_hits:
            agent_memory_hits = await self._retrieve_agent_memory(
                query_variants=query_variants,
                vectors=vectors,
                request=request,
            )
            return RetrievalExecutionResult(
                chunks=[*graph_hits, *agent_memory_hits],
                strategy=resolved_strategy.strategy,
                graph_hit_count=len(graph_hits),
                agent_memory_hit_count=len(agent_memory_hits),
            )

        chunks = await self._retrieve_with_query_variants(
            query_variants=query_variants,
            vectors=vectors,
            request=request,
            mode=resolved_strategy.mode,
        )
        agent_memory_hits = await self._retrieve_agent_memory(
            query_variants=query_variants,
            vectors=vectors,
            request=request,
        )
        return RetrievalExecutionResult(
            chunks=[*chunks, *agent_memory_hits],
            strategy=SearchStrategy.HYBRID,
            graph_hit_count=0,
            agent_memory_hit_count=len(agent_memory_hits),
            fallback_reason=graph_fallback_reason or "graph_no_hits",
        )

    async def _retrieve_agent_memory(
        self,
        *,
        query_variants: list[str],
        vectors: list[list[float]],
        request: SearchRequest,
    ) -> list[RetrievedChunk]:
        """Agent Memory Search を別 backend として実行し、失敗時は retrieval を継続する。"""
        if (
            not self._settings.rag_agent_memory_search_enabled
            or self._settings.rag_agent_memory_top_k <= 0
            or not query_variants
            or not vectors
            or not _agent_memory_scope_available()
            or _oracle_method_is_inherited(self._oracle, "agent_memory_search")
        ):
            return []
        try:
            hits = await self._oracle.agent_memory_search(
                query=query_variants[0],
                embedding=vectors[0],
                top_k=self._settings.rag_agent_memory_top_k,
                filters=request.filters,
            )
            return [chunk for chunk in hits if _agent_memory_chunk_matches_request(chunk, request)]
        except Exception:
            return []

    async def _graph_search(
        self,
        *,
        strategy: SearchStrategy,
        query: str,
        top_k: int,
        filters: dict[str, str],
    ) -> tuple[list[RetrievedChunk], str | None]:
        """GraphRAG-lite 経路を実行し、KG 未適用環境では空として扱う。"""
        try:
            if strategy == SearchStrategy.GRAPH_GLOBAL:
                return await self._oracle.graph_global_search(query, top_k, filters), None
            return await self._oracle.graph_local_search(query, top_k, filters), None
        except Exception:
            return [], "graph_query_error"

    async def _retrieve_with_query_variants(
        self,
        *,
        query_variants: list[str],
        vectors: list[list[float]],
        request: SearchRequest,
        mode: SearchMode,
    ) -> list[RetrievedChunk]:
        """query expansion variants で検索し、chunk 単位で融合する。"""
        if not query_variants:
            return []
        if mode == SearchMode.KEYWORD:
            if len(query_variants) == 1:
                return await self._oracle.keyword_search(
                    query_variants[0], request.top_k, request.filters
                )
            variant_hits = await asyncio.gather(
                *[
                    self._oracle.keyword_search(query, request.top_k, request.filters)
                    for query in query_variants
                ]
            )
            return _fuse_query_variant_hits(
                variant_hits,
                top_k=request.top_k,
                rrf_k=self._settings.rag_rrf_k,
            )
        if len(query_variants) != len(vectors):
            raise ValueError("query variants と query embeddings の件数が一致しません。")
        if len(query_variants) == 1:
            return await self._oracle.hybrid_search(
                query=query_variants[0],
                embedding=vectors[0],
                top_k=request.top_k,
                mode=mode,
                filters=request.filters,
            )
        variant_hits = await asyncio.gather(
            *[
                self._oracle.hybrid_search(
                    query=query,
                    embedding=vector,
                    top_k=request.top_k,
                    mode=mode,
                    filters=request.filters,
                )
                for query, vector in zip(query_variants, vectors, strict=True)
            ]
        )
        return _fuse_query_variant_hits(
            variant_hits,
            top_k=request.top_k,
            rrf_k=self._settings.rag_rrf_k,
        )


def _build_context(chunks: list[RetrievedChunk], max_chars: int) -> str:
    """LLM に渡す引用コンテキストを作る。"""
    context, _ = _build_context_with_citations(chunks, max_chars)
    return context


def _agent_memory_scope_available() -> bool:
    context = current_audit_request_context()
    return any(
        (
            context.user_id_hash,
            context.role_id_hash,
            context.agent_id_hash,
            context.thread_id_hash,
        )
    )


def _build_agent_memory_text(
    answer: str,
    citations: list[RetrievedChunk],
    *,
    max_chars: int,
) -> str:
    """Agent Memory に保存する短い回答要約を作る。query 原文は含めない。"""
    cleaned_answer = WHITESPACE_RE.sub(" ", answer).strip()
    if not cleaned_answer:
        return ""
    citation_ids = ", ".join(_citation_ids(citations)[:8])
    text = f"回答要約: {cleaned_answer}"
    if citation_ids:
        text = f"{text}\n根拠ID: {citation_ids}"
    return text[:max_chars].rstrip()


def _citation_ids(citations: list[RetrievedChunk]) -> list[str]:
    ids: list[str] = []
    for citation in citations:
        if citation.document_id == "agent-memory":
            continue
        ids.append(f"{citation.document_id}#{citation.chunk_id}")
    return ids


def _citation_document_ids(citations: list[RetrievedChunk]) -> list[str]:
    return sorted({citation.document_id for citation in citations if citation.document_id})


def _citation_knowledge_base_ids(citations: list[RetrievedChunk]) -> list[str]:
    knowledge_base_ids: set[str] = set()
    for citation in citations:
        knowledge_base_ids.update(_metadata_id_set(citation.metadata, "knowledge_base_id"))
        knowledge_base_ids.update(_metadata_id_set(citation.metadata, "knowledge_base_ids"))
    return sorted(knowledge_base_ids)


def _is_agent_memory_chunk(chunk: RetrievedChunk) -> bool:
    return str(chunk.metadata.get("retrieval_mode") or "").casefold() in {
        "agent_memory",
        "memory",
        "history",
    }


def _build_retrieval_diagnostics(
    *,
    request_mode: SearchMode,
    retrieved: list[RetrievedChunk],
    ranked: list[RetrievedChunk],
    citations: list[RetrievedChunk],
    evidence_count: int = 0,
) -> tuple[SearchRetrievalBreakdown, list[SearchRetrievalCandidate]]:
    """本文を含めず、検索候補の流れだけを diagnostics 化する。"""
    document_candidates = [chunk for chunk in retrieved if not _is_agent_memory_chunk(chunk)]
    vector_count = _metadata_max_int(document_candidates, "retrieval_vector_count")
    keyword_count = _metadata_max_int(document_candidates, "retrieval_keyword_count")
    overlap_count = _metadata_max_int(document_candidates, "retrieval_overlap_count")
    fused_count = _metadata_max_int(document_candidates, "retrieval_fused_count")
    fusion_dropped_count = _metadata_max_int(
        document_candidates,
        "retrieval_fusion_dropped_count",
    )

    if vector_count is None:
        vector_count = (
            len(document_candidates)
            if request_mode == SearchMode.VECTOR
            else sum(1 for chunk in document_candidates if _candidate_has_source(chunk, "vector"))
        )
    if keyword_count is None:
        keyword_count = (
            len(document_candidates)
            if request_mode == SearchMode.KEYWORD
            else sum(1 for chunk in document_candidates if _candidate_has_source(chunk, "keyword"))
        )
    if overlap_count is None:
        overlap_count = sum(
            1
            for chunk in document_candidates
            if _candidate_has_source(chunk, "vector") and _candidate_has_source(chunk, "keyword")
        )
    if fused_count is None:
        fused_count = len(document_candidates)
    if fusion_dropped_count is None:
        fusion_dropped_count = max(0, vector_count + keyword_count - overlap_count - fused_count)

    ranked_ids = {chunk.chunk_id for chunk in ranked if not _is_agent_memory_chunk(chunk)}
    citation_ids = {chunk.chunk_id for chunk in citations}
    rerank_rank_by_id = {
        chunk.chunk_id: rank
        for rank, chunk in enumerate(
            [item for item in ranked if not _is_agent_memory_chunk(item)],
            start=1,
        )
    }
    rerank_input_count = len(document_candidates)
    rerank_kept_count = len(ranked_ids)
    citation_count = len(citations)
    breakdown = SearchRetrievalBreakdown(
        vector_count=vector_count,
        keyword_count=keyword_count,
        overlap_count=overlap_count,
        fused_count=fused_count,
        fusion_dropped_count=fusion_dropped_count,
        rerank_input_count=rerank_input_count,
        rerank_kept_count=rerank_kept_count,
        rerank_dropped_count=max(0, rerank_input_count - rerank_kept_count),
        evidence_count=evidence_count,
        citation_count=citation_count,
        dropped_count=max(0, rerank_input_count - citation_count),
    )
    candidates = [
        _retrieval_candidate(
            chunk,
            request_mode=request_mode,
            ranked_ids=ranked_ids,
            citation_ids=citation_ids,
            rerank_rank_by_id=rerank_rank_by_id,
        )
        for chunk in document_candidates[:RETRIEVAL_CANDIDATE_LIMIT]
    ]
    return breakdown, candidates


def _retrieval_candidate(
    chunk: RetrievedChunk,
    *,
    request_mode: SearchMode,
    ranked_ids: set[str],
    citation_ids: set[str],
    rerank_rank_by_id: dict[str, int],
) -> SearchRetrievalCandidate:
    sources = _candidate_sources(chunk, request_mode)
    if chunk.chunk_id in citation_ids:
        status = "citation"
        drop_reason = None
    elif chunk.chunk_id in ranked_ids:
        status = "reranked"
        drop_reason = "not_cited"
    else:
        status = "dropped"
        drop_reason = "rerank_out"
    return SearchRetrievalCandidate(
        chunk_id=chunk.chunk_id,
        document_id=chunk.document_id,
        file_name=chunk.file_name,
        sources=sources,
        vector_rank=_metadata_optional_int(chunk.metadata.get("vector_rank")),
        vector_score=_metadata_optional_float(chunk.metadata.get("vector_score")),
        keyword_rank=_metadata_optional_int(chunk.metadata.get("keyword_rank")),
        keyword_score=_metadata_optional_float(chunk.metadata.get("keyword_score")),
        rrf_score=_metadata_optional_float(chunk.metadata.get("rrf_score")),
        rerank_rank=rerank_rank_by_id.get(chunk.chunk_id),
        rerank_score=chunk.rerank_score,
        status=status,
        drop_reason=drop_reason,
    )


def _candidate_sources(chunk: RetrievedChunk, request_mode: SearchMode) -> list[str]:
    sources: list[str] = []
    if _candidate_has_source(chunk, "vector"):
        sources.append("vector")
    if _candidate_has_source(chunk, "keyword"):
        sources.append("keyword")
    retrieval_mode = str(chunk.metadata.get("retrieval_mode") or "").casefold()
    if retrieval_mode.startswith("graph"):
        sources.append("graph")
    if retrieval_mode == "agent_memory":
        sources.append("agent_memory")
    if not sources and request_mode in {SearchMode.VECTOR, SearchMode.KEYWORD}:
        sources.append(request_mode.value)
    if not sources:
        sources.append(retrieval_mode or request_mode.value)
    return _dedupe_strings(sources)


def _candidate_has_source(chunk: RetrievedChunk, source: str) -> bool:
    if source == "vector" and "vector_rank" in chunk.metadata:
        return True
    if source == "keyword" and "keyword_rank" in chunk.metadata:
        return True
    retrieval_mode = str(chunk.metadata.get("retrieval_mode") or "").casefold()
    return retrieval_mode == source or retrieval_mode == "hybrid"


def _metadata_max_int(chunks: list[RetrievedChunk], key: str) -> int | None:
    values = [
        value
        for chunk in chunks
        if (value := _metadata_optional_int(chunk.metadata.get(key))) is not None
    ]
    return max(values) if values else None


def _metadata_optional_int(value: object) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        cleaned = value.strip()
        if cleaned and cleaned.lstrip("-").isdigit():
            return int(cleaned)
    return None


def _metadata_optional_float(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float):
        number = float(value)
        return number if math.isfinite(number) else None
    if isinstance(value, str):
        try:
            number = float(value.strip())
        except ValueError:
            return None
        return number if math.isfinite(number) else None
    return None


def _agent_memory_chunk_matches_request(chunk: RetrievedChunk, request: SearchRequest) -> bool:
    """Agent Memory hit が明示 filter / access scope を破らないことを確認する。"""
    if _is_agent_memory_chunk(chunk):
        return _agent_memory_history_matches_request(chunk, request)
    return _document_chunk_matches_request(chunk, request)


def _document_chunk_matches_request(chunk: RetrievedChunk, request: SearchRequest) -> bool:
    """通常 document chunk が明示 filter / access scope を破らないことを確認する。"""
    filters = request.filters
    context = current_audit_request_context()
    if (document_id := filters.get("document_id")) and chunk.document_id != document_id:
        return False
    if (status := filters.get("status")) and not _metadata_value_equals(
        chunk.metadata,
        "status",
        status,
    ):
        return False
    if (file_name := filters.get("file_name")) and (
        file_name.casefold() not in (chunk.file_name or "").casefold()
    ):
        return False
    if (category_name := filters.get("category_name")) and not (
        _metadata_value_contains(chunk.metadata, "category_name", category_name)
        or (
            chunk.category_name is not None
            and category_name.casefold() in chunk.category_name.casefold()
        )
    ):
        return False
    for key in ("content_kind", "source_acl", "document_version"):
        expected = filters.get(key)
        if expected and not _metadata_value_equals(chunk.metadata, key, expected):
            return False
    for key in ("section_title", "section_path"):
        expected = filters.get(key)
        if expected and not _metadata_value_contains(chunk.metadata, key, expected):
            return False

    if context.allowed_document_ids is not None and chunk.document_id not in (
        context.allowed_document_ids
    ):
        return False
    if context.allowed_category_names is not None:
        category = (
            chunk.category_name
            or _metadata_string(chunk.metadata, "category_name")
            or _metadata_string(chunk.metadata, "category")
            or ""
        )
        if category.casefold() not in context.allowed_category_names:
            return False
    return True


def _agent_memory_history_matches_request(
    chunk: RetrievedChunk,
    request: SearchRequest,
) -> bool:
    """History memory が明示 filter / dataset scope を破らないことを確認する。"""
    filters = request.filters
    context = current_audit_request_context()
    has_document_scope = (
        bool(filters.get("document_id")) or context.allowed_document_ids is not None
    )
    knowledge_base_ids = _expected_knowledge_base_ids(request, context)
    if context.tenant_id_hash is not None and not has_document_scope and not knowledge_base_ids:
        return False
    if (document_id := filters.get("document_id")) and not _memory_references_document(
        chunk,
        document_id,
    ):
        return False
    if context.allowed_document_ids is not None:
        if not context.allowed_document_ids:
            return False
        if not any(
            _memory_references_document(chunk, item) for item in context.allowed_document_ids
        ):
            return False
    if (
        knowledge_base_ids
        and not (
            _metadata_id_set(chunk.metadata, "knowledge_base_id")
            | _metadata_id_set(chunk.metadata, "knowledge_base_ids")
        )
        & knowledge_base_ids
    ):
        return False
    if (status := filters.get("status")) and not _metadata_value_equals(
        chunk.metadata,
        "status",
        status,
    ):
        return False
    if (file_name := filters.get("file_name")) and not _metadata_value_contains(
        chunk.metadata,
        "file_name",
        file_name,
    ):
        return False
    if (category_name := filters.get("category_name")) and not _metadata_value_contains(
        chunk.metadata,
        "category_name",
        category_name,
    ):
        return False
    if context.allowed_category_names is not None:
        category = _metadata_string(chunk.metadata, "category_name") or ""
        if category.casefold() not in context.allowed_category_names:
            return False
    for key in ("content_kind", "source_acl", "document_version"):
        expected = filters.get(key)
        if expected and not _metadata_value_equals(chunk.metadata, key, expected):
            return False
    for key in ("section_title", "section_path"):
        expected = filters.get(key)
        if expected and not _metadata_value_contains(chunk.metadata, key, expected):
            return False
    return True


def _expected_knowledge_base_ids(request: SearchRequest, context: object) -> set[str]:
    expected = set(request.knowledge_base_ids)
    if filter_ids := request.filters.get("knowledge_base_id"):
        expected.update(item.strip() for item in filter_ids.split(",") if item.strip())
    allowed = getattr(context, "allowed_knowledge_base_ids", None)
    if allowed is not None:
        expected.update(str(item) for item in allowed)
    return expected


def _memory_references_document(chunk: RetrievedChunk, document_id: str) -> bool:
    document_ids = (
        _metadata_id_set(chunk.metadata, "citation_document_ids")
        | _metadata_id_set(chunk.metadata, "source_document_ids")
        | _document_ids_from_citation_ids(chunk.metadata)
    )
    return document_id in document_ids


def _metadata_id_set(
    metadata: Mapping[str, JsonValue],
    key: str,
) -> set[str]:
    return _metadata_id_tokens(metadata.get(key))


def _metadata_id_tokens(value: object) -> set[str]:
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("["):
            try:
                decoded = json.loads(stripped)
            except json.JSONDecodeError:
                decoded = None
            if decoded is not None:
                return _metadata_id_tokens(decoded)
        cleaned = stripped.replace("[", "").replace("]", "").replace("'", "").replace('"', "")
        return {item.strip() for item in cleaned.split(",") if item.strip()}
    if isinstance(value, bool) or value is None:
        return set()
    if isinstance(value, int | float):
        return {str(value)}
    if isinstance(value, list):
        tokens: set[str] = set()
        for item in value:
            tokens.update(_metadata_id_tokens(item))
        return tokens
    return set()


def _document_ids_from_citation_ids(
    metadata: Mapping[str, JsonValue],
) -> set[str]:
    citation_ids = _metadata_id_set(metadata, "citation_ids")
    return {item.split("#", 1)[0] for item in citation_ids if "#" in item}


def _metadata_value_equals(
    metadata: Mapping[str, JsonValue],
    key: str,
    expected: str,
) -> bool:
    value = _metadata_text(metadata.get(key))
    return bool(value and value.casefold() == expected.casefold())


def _metadata_value_contains(
    metadata: Mapping[str, JsonValue],
    key: str,
    expected: str,
) -> bool:
    value = _metadata_text(metadata.get(key))
    return bool(value and expected.casefold() in value.casefold())


def _metadata_string(
    metadata: Mapping[str, JsonValue],
    key: str,
) -> str | None:
    return _metadata_text(metadata.get(key))


def _metadata_text(value: object) -> str | None:
    if isinstance(value, str):
        cleaned = value.strip()
        return cleaned or None
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float):
        return str(value)
    if isinstance(value, list):
        parts = [part for item in value if (part := _metadata_text(item))]
        joined = " > ".join(parts)
        return joined or None
    return None


def _apply_retrieval_adapter_request(
    request: SearchRequest,
    params: RetrievalAdapterParams,
) -> SearchRequest:
    """Retrieval アダプターの mode/strategy bias を request 既定値にだけ適用する。

    per-request で明示された mode(HYBRID 以外)は上書きしない。
    """
    updates: dict[str, object] = {}
    if params.mode_override is not None and request.mode == SearchMode.HYBRID:
        updates["mode"] = params.mode_override
    if params.strategy_bias is not None and request.strategy == SearchStrategy.HYBRID:
        updates["strategy"] = params.strategy_bias
    return request.model_copy(update=updates) if updates else request


def _business_context_scope_pinned(business_context: BusinessContextPack) -> bool:
    """gap-stop 判定: 業務スコープ(テナント/データセット/ACL/版)が 1 つでも固定済みか。"""
    return any(
        (
            business_context.tenant_scoped,
            business_context.user_scoped,
            business_context.role_scoped,
            business_context.document_acl_scoped,
            business_context.category_acl_scoped,
            business_context.knowledge_base_scoped,
            business_context.source_acl_filter_present,
            business_context.version_filter_present,
        )
    )


def _crag_confidence(ranked: list[RetrievedChunk]) -> float:
    """CRAG の信頼度シグナル: rerank 最高スコア(無ければ vector score)を [0,1] で返す。"""
    if not ranked:
        return 0.0
    best = max(
        (chunk.rerank_score if chunk.rerank_score is not None else chunk.score) for chunk in ranked
    )
    return max(0.0, min(1.0, float(best)))


def _relaxed_corrective_request(request: SearchRequest) -> SearchRequest:
    """corrective retrieval 用に top_k を広げ、絞り込み filter を緩めた request を作る。"""
    relaxed_filters = {
        key: value
        for key, value in request.filters.items()
        if key not in {"content_kind", "section_title", "section_path"}
    }
    return request.model_copy(
        update={
            "top_k": min(100, max(request.top_k + 1, request.top_k * 2)),
            "filters": relaxed_filters,
            "mode": SearchMode.HYBRID,
            "strategy": SearchStrategy.HYBRID,
        }
    )


def _business_fit(chunk: RetrievedChunk) -> float:
    """chunk metadata から業務適合度を決定論的に算出する(version/ACL/鮮度)。"""
    fit = 1.0
    version_status = (_metadata_string(chunk.metadata, "version_status") or "").casefold()
    if version_status in {"active", "current", "approved", "effective", "published"}:
        fit *= 1.15
    elif version_status in {"draft", "provisional", "pending", "review"}:
        fit *= 0.85
    if _metadata_string(chunk.metadata, "source_acl"):
        fit *= 1.05
    if _metadata_string(chunk.metadata, "document_version"):
        fit *= 1.02
    return max(0.5, min(1.3, fit))


def _apply_business_fit_weighting(
    chunks: list[RetrievedChunk],
) -> tuple[list[RetrievedChunk], int]:
    """rerank 後に final = semantic × business_fit で並べ替える(PDF AIDB Proof)。"""
    if len(chunks) <= 1:
        return chunks, 0
    indexed = list(enumerate(chunks))

    def sort_key(item: tuple[int, RetrievedChunk]) -> tuple[float, int]:
        index, chunk = item
        semantic = chunk.rerank_score if chunk.rerank_score is not None else chunk.score
        return (-(semantic * _business_fit(chunk)), index)

    reordered = sorted(indexed, key=sort_key)
    changed = sum(1 for new_index, (old_index, _) in enumerate(reordered) if new_index != old_index)
    return [chunk for _, chunk in reordered], changed


def _oracle_method_is_inherited(oracle: OracleClient, method_name: str) -> bool:
    """テスト用 subclass が実 DB 用 base method を継承しているだけなら true。"""
    oracle_type = type(oracle)
    if oracle_type is OracleClient:
        return False
    return getattr(oracle_type, method_name, None) is getattr(OracleClient, method_name, None)


def _dedupe_ranked_chunks(
    chunks: list[RetrievedChunk], *, collapse_overlapping_spans: bool = False
) -> tuple[list[RetrievedChunk], int]:
    """同一本文の chunk を rerank 後に除外し、context 枠を節約する。

    ``collapse_overlapping_spans=True``(fused 配信)では、text-hash 完全一致に加え、
    異なる chunk_set 由来で **source span(start_offset..end_offset)が重なる** chunk も
    冗長として除外する(ランク順に走査し高ランクを優先)。offset 欠落 chunk は除外しない。
    """
    seen: set[str] = set()
    unique: list[RetrievedChunk] = []
    kept_spans: dict[str, list[tuple[int, int]]] = {}
    for chunk in chunks:
        key = _chunk_dedupe_key(chunk)
        if key in seen:
            continue
        if collapse_overlapping_spans:
            span = _chunk_source_span(chunk)
            if span is not None:
                spans = kept_spans.setdefault(chunk.document_id, [])
                if any(_source_spans_overlap(span, kept) for kept in spans):
                    continue
                spans.append(span)
        seen.add(key)
        unique.append(chunk)
    return unique, len(chunks) - len(unique)


def _chunk_source_span(chunk: RetrievedChunk) -> tuple[int, int] | None:
    """chunk の元文書 source span(start_offset, end_offset)を返す。欠落なら None。"""
    start = chunk.metadata.get("start_offset")
    end = chunk.metadata.get("end_offset")
    if isinstance(start, int) and isinstance(end, int) and end > start:
        return (start, end)
    return None


def _source_spans_overlap(a: tuple[int, int], b: tuple[int, int]) -> bool:
    """半開区間 [start, end) 同士が重なるか(隣接は重ならない)。"""
    return a[0] < b[1] and b[0] < a[1]


def _chunk_dedupe_key(chunk: RetrievedChunk) -> str:
    """text_sha256 があれば使い、なければ正規化本文 hash を使う。"""
    text_sha256 = chunk.metadata.get("text_sha256")
    if isinstance(text_sha256, str) and text_sha256.strip():
        return f"text_sha256:{text_sha256.strip().casefold()}"
    normalized_text = WHITESPACE_RE.sub(" ", chunk.text).strip().casefold()
    digest = hashlib.sha256(normalized_text.encode("utf-8")).hexdigest()
    return f"text:{digest}"


def _compress_context_chunks(
    chunks: list[RetrievedChunk],
    *,
    query: str,
    max_sentences: int,
    max_chars_per_chunk: int,
) -> tuple[list[RetrievedChunk], int, int]:
    """長い chunk から query 関連 segment を抽出し、context 枠を節約する。"""
    if not chunks:
        return chunks, 0, 0
    query_features = _query_match_features(query)
    compressed_chunks: list[RetrievedChunk] = []
    compressed_count = 0
    saved_chars = 0
    for chunk in chunks:
        excerpt = _extract_relevant_excerpt(
            chunk.text,
            query_features=query_features,
            max_sentences=max_sentences,
            max_chars=max_chars_per_chunk,
        )
        if len(excerpt) < len(chunk.text.strip()):
            original_chars = len(chunk.text)
            excerpt_chars = len(excerpt)
            compressed_count += 1
            saved_chars += max(0, original_chars - excerpt_chars)
            compressed_chunks.append(
                chunk.model_copy(
                    update={
                        "text": excerpt,
                        "metadata": {
                            **chunk.metadata,
                            "context_compressed": True,
                            "context_original_chars": original_chars,
                            "context_compressed_chars": excerpt_chars,
                            "context_compression_saved_chars": max(
                                0,
                                original_chars - excerpt_chars,
                            ),
                        },
                    }
                )
            )
        else:
            compressed_chunks.append(chunk)
    return compressed_chunks, compressed_count, saved_chars


def _extract_relevant_excerpt(
    text: str,
    *,
    query_features: set[str],
    max_sentences: int,
    max_chars: int,
) -> str:
    """query feature と重なる sentence/line を元の順序で抜き出す。"""
    normalized_text = text.strip()
    if len(normalized_text) <= max_chars:
        return normalized_text
    segments = _split_context_segments(normalized_text)
    if not segments:
        return normalized_text[:max_chars].rstrip()
    scored_indices = [
        (index, _segment_match_score(segment, query_features))
        for index, segment in enumerate(segments)
    ]
    best_score = max(score for _, score in scored_indices)
    if best_score <= 0:
        selected_indices = _leading_segment_indices(segments, max_sentences, max_chars)
    else:
        selected_indices = sorted(
            index
            for index, _ in sorted(
                scored_indices,
                key=lambda item: (
                    item[1],
                    -item[0],
                ),
                reverse=True,
            )
            if _segment_match_score(segments[index], query_features) > 0
        )[:max_sentences]
    excerpt = "\n".join(segments[index] for index in selected_indices).strip()
    if not excerpt:
        excerpt = normalized_text[:max_chars].rstrip()
    if len(excerpt) > max_chars:
        excerpt = excerpt[:max_chars].rstrip()
    return excerpt


def _split_context_segments(text: str) -> list[str]:
    """句点・改行を尊重し、表/箇条書きの行も segment として扱う。"""
    segments: list[str] = []
    for raw_line in text.splitlines():
        line = WHITESPACE_RE.sub(" ", raw_line).strip()
        if not line:
            continue
        line_segments = [
            match.group(0).strip()
            for match in CONTEXT_SEGMENT_RE.finditer(line)
            if match.group(0).strip()
        ]
        segments.extend(line_segments or [line])
    if segments:
        return segments
    normalized = WHITESPACE_RE.sub(" ", text).strip()
    return [normalized] if normalized else []


def _query_match_features(query: str) -> set[str]:
    """日本語・英数字混在 query から excerpt 抽出用 feature を作る。"""
    normalized = WHITESPACE_RE.sub(" ", query.casefold()).strip()
    compact = WHITESPACE_RE.sub("", normalized)
    features: list[str] = [
        token for token in QUERY_FEATURE_RE.findall(normalized) if len(token) >= 2
    ]
    for ngram_size in (4, 3, 2):
        if len(compact) < ngram_size:
            continue
        features.extend(
            compact[index : index + ngram_size] for index in range(len(compact) - ngram_size + 1)
        )
    return set(_dedupe_strings(features)[:80])


def _segment_match_score(segment: str, query_features: set[str]) -> float:
    """segment が query feature を含むほど高くする軽量スコア。"""
    if not query_features:
        return 0.0
    spaced = WHITESPACE_RE.sub(" ", segment.casefold())
    compact = WHITESPACE_RE.sub("", spaced)
    score = 0.0
    for feature in query_features:
        if feature in spaced or feature in compact:
            score += 2.0 if len(feature) >= 3 else 1.0
    return score


def _leading_segment_indices(
    segments: list[str],
    max_sentences: int,
    max_chars: int,
) -> list[int]:
    """query feature がない場合の安全な先頭 fallback。"""
    selected: list[int] = []
    total = 0
    for index, segment in enumerate(segments):
        separator_len = 1 if selected else 0
        if selected and total + separator_len + len(segment) > max_chars:
            break
        selected.append(index)
        total += separator_len + len(segment)
        if len(selected) >= max_sentences:
            break
    return selected or [0]


def _dedupe_strings(values: list[str]) -> list[str]:
    """順序安定で文字列を重複排除する。"""
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        unique.append(value)
    return unique


def _interleave_context_neighbors(
    anchors: list[RetrievedChunk],
    neighbors: list[RetrievedChunk],
) -> tuple[list[RetrievedChunk], int]:
    """各 anchor の直後に隣接 context を低優先度で差し込む。"""
    if not anchors or not neighbors:
        return anchors, 0
    anchor_ids = {chunk.chunk_id for chunk in anchors}
    neighbors_by_anchor: dict[str, list[RetrievedChunk]] = {}
    for neighbor in neighbors:
        anchor_id = neighbor.metadata.get("context_anchor_chunk_id")
        if not isinstance(anchor_id, str) or neighbor.chunk_id in anchor_ids:
            continue
        neighbors_by_anchor.setdefault(anchor_id, []).append(neighbor)

    packed: list[RetrievedChunk] = []
    seen: set[str] = set()
    for anchor in anchors:
        if anchor.chunk_id not in seen:
            packed.append(anchor)
            seen.add(anchor.chunk_id)
        for neighbor in sorted(
            neighbors_by_anchor.get(anchor.chunk_id, []),
            key=_context_neighbor_sort_key,
        ):
            if neighbor.chunk_id in seen:
                continue
            packed.append(neighbor)
            seen.add(neighbor.chunk_id)
    return packed, len(packed) - len(anchors)


def _interleave_context_group_siblings(
    anchors: list[RetrievedChunk],
    siblings: list[RetrievedChunk],
) -> tuple[list[RetrievedChunk], int]:
    """各 anchor の直後に同一 group の sibling context を差し込む。"""
    if not anchors or not siblings:
        return anchors, 0
    anchor_ids = {chunk.chunk_id for chunk in anchors}
    siblings_by_anchor: dict[str, list[RetrievedChunk]] = {}
    for sibling in siblings:
        anchor_id = sibling.metadata.get("context_anchor_chunk_id")
        if not isinstance(anchor_id, str) or sibling.chunk_id in anchor_ids:
            continue
        siblings_by_anchor.setdefault(anchor_id, []).append(sibling)

    packed: list[RetrievedChunk] = []
    seen: set[str] = set()
    for anchor in anchors:
        if anchor.chunk_id not in seen:
            packed.append(anchor)
            seen.add(anchor.chunk_id)
        for sibling in sorted(
            siblings_by_anchor.get(anchor.chunk_id, []),
            key=_context_group_sort_key,
        ):
            if sibling.chunk_id in seen:
                continue
            packed.append(sibling)
            seen.add(sibling.chunk_id)
    return packed, len(packed) - len(anchors)


def _promote_dependency_linked_context(
    anchors: list[RetrievedChunk],
    candidates: list[RetrievedChunk],
    *,
    max_chunks_per_anchor: int,
) -> tuple[list[RetrievedChunk], int]:
    """rerank で落ちた parent/child chunk を retrieved pool から context へ戻す。"""
    if not anchors or not candidates or max_chunks_per_anchor <= 0:
        return anchors, 0
    anchor_ids = {chunk.chunk_id for chunk in anchors}
    promoted_by_anchor: dict[str, list[RetrievedChunk]] = {}
    for anchor in anchors:
        if _is_agent_memory_chunk(anchor):
            continue
        promoted: list[RetrievedChunk] = []
        for candidate in candidates:
            if (
                candidate.chunk_id in anchor_ids
                or candidate.document_id != anchor.document_id
                or _is_agent_memory_chunk(candidate)
            ):
                continue
            reason, shared_ids = _dependency_promotion_reason(anchor, candidate)
            if reason is None:
                continue
            promoted.append(
                _with_context_dependency_metadata(
                    candidate,
                    anchor=anchor,
                    reason=reason,
                    shared_ids=shared_ids,
                )
            )
        promoted_by_anchor[anchor.chunk_id] = sorted(
            promoted,
            key=_context_dependency_sort_key,
        )[:max_chunks_per_anchor]

    packed: list[RetrievedChunk] = []
    seen: set[str] = set()
    for anchor in anchors:
        if anchor.chunk_id not in seen:
            packed.append(anchor)
            seen.add(anchor.chunk_id)
        for candidate in promoted_by_anchor.get(anchor.chunk_id, []):
            if candidate.chunk_id in seen:
                continue
            packed.append(candidate)
            seen.add(candidate.chunk_id)
    return packed, len(packed) - len(anchors)


def _dependency_promotion_reason(
    anchor: RetrievedChunk,
    candidate: RetrievedChunk,
) -> tuple[str | None, set[str]]:
    """anchor と candidate の dependency 関係を返す。"""
    anchor_elements = _metadata_id_set(anchor.metadata, "element_ids")
    candidate_elements = _metadata_id_set(candidate.metadata, "element_ids")
    anchor_parents = _metadata_id_set(anchor.metadata, "parent_element_ids")
    candidate_parents = _metadata_id_set(candidate.metadata, "parent_element_ids")
    if shared := anchor_elements & candidate_parents:
        return "child_of_anchor", shared
    if shared := candidate_elements & anchor_parents:
        return "parent_of_anchor", shared

    candidate_edges = _dependency_edge_pairs(candidate.metadata)
    if shared := _dependency_edge_shared_ids(candidate_edges, anchor_elements):
        return "candidate_dependency_edge", shared
    anchor_edges = _dependency_edge_pairs(anchor.metadata)
    if shared := _dependency_edge_shared_ids(anchor_edges, candidate_elements):
        return "anchor_dependency_edge", shared
    return None, set()


def _dependency_edge_pairs(
    metadata: Mapping[str, JsonValue],
) -> set[tuple[str, str]]:
    """metadata.dependency_edges を (parent_id, child_id) の集合へ変換する。"""
    value = metadata.get("dependency_edges")
    if isinstance(value, str):
        if not value.strip():
            return set()
        try:
            payload = json.loads(value)
        except json.JSONDecodeError:
            return set()
    else:
        payload = value
    if not isinstance(payload, list):
        return set()
    edges: set[tuple[str, str]] = set()
    for item in payload:
        if not isinstance(item, Mapping):
            continue
        parent_id = _mapping_text(item, "parent_id") or _mapping_text(item, "parent")
        child_id = _mapping_text(item, "child_id") or _mapping_text(item, "child")
        if parent_id and child_id:
            edges.add((parent_id, child_id))
    return edges


def _dependency_edge_shared_ids(
    edges: set[tuple[str, str]],
    element_ids: set[str],
) -> set[str]:
    """dependency edge と element_ids の交差を返す。"""
    if not edges or not element_ids:
        return set()
    edge_ids = {item for edge in edges for item in edge}
    return edge_ids & element_ids


def _mapping_text(mapping: Mapping[object, object], key: str) -> str | None:
    value = mapping.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else None


def _with_context_dependency_metadata(
    chunk: RetrievedChunk,
    *,
    anchor: RetrievedChunk,
    reason: str,
    shared_ids: set[str],
) -> RetrievedChunk:
    """dependency promotion の判断理由を citation metadata に残す。"""
    metadata = {
        **chunk.metadata,
        "context_dependency_promoted": True,
        "context_dependency_reason": reason,
        "context_anchor_chunk_id": anchor.chunk_id,
    }
    if shared_ids:
        metadata["context_dependency_shared_element_ids"] = ",".join(sorted(shared_ids)[:8])
    return chunk.model_copy(update={"metadata": metadata})


def _context_dependency_sort_key(chunk: RetrievedChunk) -> tuple[int, int, str]:
    """parent/child 関係の強さと chunk_index で安定化する。"""
    reason = _metadata_string(chunk.metadata, "context_dependency_reason") or ""
    priority = {
        "child_of_anchor": 0,
        "parent_of_anchor": 1,
        "candidate_dependency_edge": 2,
        "anchor_dependency_edge": 3,
    }.get(reason, 9)
    return (priority, _metadata_int(chunk.metadata.get("chunk_index")), chunk.chunk_id)


def _interleave_adaptive_context(
    anchors: list[RetrievedChunk],
    candidates: list[RetrievedChunk],
    *,
    query: str,
    min_overlap: float,
) -> tuple[list[RetrievedChunk], int]:
    """SCAR 風に query/構造連続性で選んだ context だけを anchor 後へ差し込む。"""
    if not anchors or not candidates:
        return anchors, 0
    anchors_by_id = {chunk.chunk_id: chunk for chunk in anchors}
    anchor_ids = set(anchors_by_id)
    query_features = _query_match_features(query)
    candidates_by_anchor: dict[str, list[RetrievedChunk]] = {}
    seen_candidates: set[tuple[str, str]] = set()
    for candidate in candidates:
        anchor_id = candidate.metadata.get("context_anchor_chunk_id")
        if not isinstance(anchor_id, str) or candidate.chunk_id in anchor_ids:
            continue
        anchor = anchors_by_id.get(anchor_id)
        if anchor is None:
            continue
        key = (anchor_id, candidate.chunk_id)
        if key in seen_candidates:
            continue
        seen_candidates.add(key)
        reason, overlap = _adaptive_context_reason(
            anchor,
            candidate,
            query_features=query_features,
            min_overlap=min_overlap,
        )
        if reason is None:
            continue
        candidates_by_anchor.setdefault(anchor_id, []).append(
            _with_context_adaptive_metadata(candidate, reason=reason, overlap=overlap)
        )

    packed: list[RetrievedChunk] = []
    seen: set[str] = set()
    for anchor in anchors:
        if anchor.chunk_id not in seen:
            packed.append(anchor)
            seen.add(anchor.chunk_id)
        for candidate in sorted(
            candidates_by_anchor.get(anchor.chunk_id, []),
            key=_context_adaptive_sort_key,
        ):
            if candidate.chunk_id in seen:
                continue
            packed.append(candidate)
            seen.add(candidate.chunk_id)
    return packed, len(packed) - len(anchors)


def _adaptive_context_reason(
    anchor: RetrievedChunk,
    candidate: RetrievedChunk,
    *,
    query_features: set[str],
    min_overlap: float,
) -> tuple[str | None, float]:
    """候補 context を追加する理由と query overlap を返す。"""
    overlap = _query_feature_overlap(candidate.text, query_features)
    if _same_structural_group(anchor, candidate):
        return "same_structural_group", overlap
    if _has_dependency_lineage(anchor, candidate):
        return "dependency_lineage", overlap
    if query_features and overlap >= min_overlap:
        return "query_overlap", overlap
    if _same_section(anchor, candidate) and overlap > 0:
        return "same_section_overlap", overlap
    return None, overlap


def _query_feature_overlap(text: str, query_features: set[str]) -> float:
    """query feature のうち候補 chunk に含まれる割合を返す。"""
    if not query_features:
        return 0.0
    spaced = WHITESPACE_RE.sub(" ", text.casefold())
    compact = WHITESPACE_RE.sub("", spaced)
    matched = sum(1 for feature in query_features if feature in spaced or feature in compact)
    return matched / len(query_features)


def _same_structural_group(anchor: RetrievedChunk, candidate: RetrievedChunk) -> bool:
    """表・図・式などの同一 chunk group は query overlap がなくても連続性を重視する。"""
    group_id = _metadata_string(anchor.metadata, "chunk_group_id")
    if group_id is None or group_id != _metadata_string(candidate.metadata, "chunk_group_id"):
        return False
    structural_kinds = {"table", "figure", "equation", "code", "list", "slide", "sheet"}
    anchor_kind = _metadata_string(anchor.metadata, "content_kind")
    candidate_kind = _metadata_string(candidate.metadata, "content_kind")
    group_kind = _metadata_string(anchor.metadata, "chunk_group_kind") or _metadata_string(
        candidate.metadata,
        "chunk_group_kind",
    )
    return any(kind in structural_kinds for kind in (anchor_kind, candidate_kind, group_kind))


def _has_dependency_lineage(anchor: RetrievedChunk, candidate: RetrievedChunk) -> bool:
    """figure-caption 等の parent/child lineage がある候補を拾う。"""
    if _metadata_string(candidate.metadata, "dependency_edges"):
        return True
    anchor_elements = _metadata_id_set(anchor.metadata, "element_ids")
    candidate_parents = _metadata_id_set(candidate.metadata, "parent_element_ids")
    if anchor_elements and candidate_parents and anchor_elements & candidate_parents:
        return True
    candidate_elements = _metadata_id_set(candidate.metadata, "element_ids")
    anchor_parents = _metadata_id_set(anchor.metadata, "parent_element_ids")
    return bool(candidate_elements and anchor_parents and candidate_elements & anchor_parents)


def _same_section(anchor: RetrievedChunk, candidate: RetrievedChunk) -> bool:
    """section_path / section_title が一致するかを見る。"""
    for key in ("section_path", "section_title"):
        anchor_value = _metadata_string(anchor.metadata, key)
        candidate_value = _metadata_string(candidate.metadata, key)
        if (
            anchor_value
            and candidate_value
            and anchor_value.casefold() == candidate_value.casefold()
        ):
            return True
    return False


def _with_context_adaptive_metadata(
    chunk: RetrievedChunk,
    *,
    reason: str,
    overlap: float,
) -> RetrievedChunk:
    """adaptive expansion の判断理由を citation metadata に残す。"""
    return chunk.model_copy(
        update={
            "metadata": {
                **chunk.metadata,
                "context_adaptive_expanded": True,
                "context_adaptive_reason": reason,
                "context_adaptive_query_overlap": round(overlap, 4),
            }
        }
    )


def _context_adaptive_sort_key(chunk: RetrievedChunk) -> tuple[int, int, int, int, str]:
    """group sibling を優先し、近さと chunk_index で安定化する。"""
    reason = _metadata_string(chunk.metadata, "context_adaptive_reason") or ""
    priority = {
        "same_structural_group": 0,
        "dependency_lineage": 1,
        "query_overlap": 2,
        "same_section_overlap": 3,
    }.get(reason, 9)
    group_distance = _metadata_int(chunk.metadata.get("context_group_distance"))
    neighbor_distance = _metadata_int(chunk.metadata.get("context_neighbor_distance"))
    distance = group_distance if group_distance else neighbor_distance
    chunk_index = _metadata_int(chunk.metadata.get("chunk_index"))
    return (priority, abs(distance), distance, chunk_index, chunk.chunk_id)


def _context_group_sort_key(chunk: RetrievedChunk) -> tuple[int, int, int, str]:
    """同一 group 内の anchor への近さと chunk_index で安定化する。"""
    distance = _metadata_int(chunk.metadata.get("context_group_distance"))
    chunk_index = _metadata_int(chunk.metadata.get("chunk_index"))
    return (abs(distance), distance, chunk_index, chunk.chunk_id)


def _context_neighbor_sort_key(chunk: RetrievedChunk) -> tuple[int, int, int, str]:
    """anchor への近さ、前後順、chunk_index の順で安定化する。"""
    distance = _metadata_int(chunk.metadata.get("context_neighbor_distance"))
    chunk_index = _metadata_int(chunk.metadata.get("chunk_index"))
    return (abs(distance), distance, chunk_index, chunk.chunk_id)


def _diversify_context_anchors(
    chunks: list[RetrievedChunk],
    *,
    diversity_lambda: float,
) -> tuple[list[RetrievedChunk], int]:
    """rerank score と本文 novelty を使い、MMR 風に context anchor を重排する。"""
    if len(chunks) < 3 or diversity_lambda >= 1.0:
        return chunks, 0
    lambda_weight = max(0.0, min(diversity_lambda, 1.0))
    relevance_scores = _normalized_relevance_scores(chunks)
    features = [_context_diversity_features(chunk.text) for chunk in chunks]
    selected_indices = [0]
    remaining_indices = list(range(1, len(chunks)))
    while remaining_indices:
        best_index = max(
            remaining_indices,
            key=lambda index: (
                _mmr_score(
                    index=index,
                    selected_indices=selected_indices,
                    relevance_scores=relevance_scores,
                    features=features,
                    lambda_weight=lambda_weight,
                ),
                relevance_scores[index],
                -index,
            ),
        )
        selected_indices.append(best_index)
        remaining_indices.remove(best_index)
    original_positions = {
        chunk.chunk_id: position for position, chunk in enumerate(chunks, start=1)
    }
    diversified = [
        _with_context_diversity_metadata(
            chunks[index],
            original_rank=original_positions[chunks[index].chunk_id],
            diversified_rank=position,
        )
        for position, index in enumerate(selected_indices, start=1)
    ]
    changed_count = sum(
        1
        for original, selected in zip(chunks, diversified, strict=True)
        if original.chunk_id != selected.chunk_id
    )
    return diversified, changed_count


def _with_context_diversity_metadata(
    chunk: RetrievedChunk,
    *,
    original_rank: int,
    diversified_rank: int,
) -> RetrievedChunk:
    """context diversity で順位が変わった chunk だけ metadata に残す。"""
    if original_rank == diversified_rank:
        return chunk
    return chunk.model_copy(
        update={
            "metadata": {
                **chunk.metadata,
                "context_diversified": True,
                "context_original_rank": original_rank,
                "context_diversified_rank": diversified_rank,
            }
        }
    )


def _normalized_relevance_scores(chunks: list[RetrievedChunk]) -> list[float]:
    """rerank/retrieval score を 0.0-1.0 へ正規化する。"""
    raw_scores = [
        chunk.rerank_score if chunk.rerank_score is not None else chunk.score for chunk in chunks
    ]
    minimum = min(raw_scores)
    maximum = max(raw_scores)
    if maximum == minimum:
        return [1.0 for _ in raw_scores]
    return [(score - minimum) / (maximum - minimum) for score in raw_scores]


def _mmr_score(
    *,
    index: int,
    selected_indices: list[int],
    relevance_scores: list[float],
    features: list[set[str]],
    lambda_weight: float,
) -> float:
    """Maximal Marginal Relevance 風の選択スコア。"""
    novelty_penalty = 0.0
    if selected_indices:
        novelty_penalty = max(
            _jaccard_similarity(features[index], features[selected_index])
            for selected_index in selected_indices
        )
    return (lambda_weight * relevance_scores[index]) - ((1.0 - lambda_weight) * novelty_penalty)


def _context_diversity_features(text: str) -> set[str]:
    """日本語でも効きやすい軽量 character n-gram 特徴を作る。"""
    normalized = WHITESPACE_RE.sub("", text.casefold())
    if not normalized:
        return set()
    if len(normalized) <= CONTEXT_DIVERSITY_NGRAM_SIZE:
        return {normalized}
    return {
        normalized[index : index + CONTEXT_DIVERSITY_NGRAM_SIZE]
        for index in range(len(normalized) - CONTEXT_DIVERSITY_NGRAM_SIZE + 1)
    }


def _jaccard_similarity(first: set[str], second: set[str]) -> float:
    """空集合を安全に扱う Jaccard 類似度。"""
    if not first or not second:
        return 0.0
    return len(first & second) / len(first | second)


def _metadata_int(value: object) -> int:
    """metadata の数値風値を sort 用に整数化する。"""
    if isinstance(value, bool) or value is None:
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        cleaned = value.strip()
        if cleaned and cleaned.lstrip("-").isdigit():
            return int(cleaned)
    return 0


def _fuse_query_variant_hits(
    variant_hits: list[list[RetrievedChunk]],
    *,
    top_k: int,
    rrf_k: int,
) -> list[RetrievedChunk]:
    """複数 query variant の検索結果を RRF で融合する。"""
    if not variant_hits:
        return []
    if len(variant_hits) == 1:
        return variant_hits[0][:top_k]

    fused: dict[str, RetrievedChunk] = {}
    scores: dict[str, float] = {}
    matched_variant_counts: dict[str, int] = {}
    for hits in variant_hits:
        seen_in_variant: set[str] = set()
        for rank, hit in enumerate(hits, start=1):
            scores[hit.chunk_id] = scores.get(hit.chunk_id, 0.0) + _rrf(rank, rrf_k)
            seen_in_variant.add(hit.chunk_id)
            existing = fused.get(hit.chunk_id)
            if existing is None or hit.score > existing.score:
                fused[hit.chunk_id] = hit
        for chunk_id in seen_in_variant:
            matched_variant_counts[chunk_id] = matched_variant_counts.get(chunk_id, 0) + 1

    ranked_ids = sorted(
        scores,
        key=lambda chunk_id: _retrieved_chunk_sort_key(
            fused[chunk_id],
            scores[chunk_id],
        ),
    )[:top_k]
    return [
        fused[chunk_id].model_copy(
            update={
                "score": round(scores[chunk_id], 6),
                "metadata": {
                    **fused[chunk_id].metadata,
                    "query_fusion_score": round(scores[chunk_id], 6),
                    "query_variant_count": len(variant_hits),
                    "matched_query_variant_count": matched_variant_counts[chunk_id],
                },
            }
        )
        for chunk_id in ranked_ids
    ]


def _rrf(rank: int, k: int) -> float:
    """Reciprocal Rank Fusion の 1 hit 分スコア。"""
    return 1.0 / (k + rank)


def _retrieved_chunk_sort_key(
    chunk: RetrievedChunk,
    score: float,
) -> tuple[float, str, int, str]:
    """fusion score 降順、document/chunk 昇順で安定化する。"""
    chunk_index = chunk.metadata.get("chunk_index")
    stable_index = chunk_index if isinstance(chunk_index, int) else 0
    return (-score, chunk.document_id, stable_index, chunk.chunk_id)


async def _observe_stage[T](
    trace_id: str,
    mode: str,
    stage: str,
    operation: Awaitable[T],
    *,
    attributes: Mapping[str, object] | None = None,
    result_attributes: Callable[[T], Mapping[str, object]] | None = None,
    progress_callback: SearchStageProgressCallback | None = None,
    stage_timings: dict[str, float] | None = None,
) -> T:
    """非同期 stage の処理時間を outcome 付きで記録する。"""
    started_at = perf_counter()
    base_attributes = dict(attributes or {})
    await _emit_stage_progress(
        progress_callback,
        trace_id=trace_id,
        stage=stage,
        outcome="started",
        elapsed=0.0,
        attributes=base_attributes,
    )
    try:
        result = await operation
    except asyncio.CancelledError as exc:
        elapsed = perf_counter() - started_at
        _record_stage_timing(stage_timings, stage, elapsed)
        record_rag_stage(mode, stage, "cancelled", elapsed)
        record_trace_span(
            trace_id=trace_id,
            span_name=stage,
            outcome="cancelled",
            seconds=elapsed,
            attributes=base_attributes,
            error=exc,
        )
        await _emit_stage_progress(
            progress_callback,
            trace_id=trace_id,
            stage=stage,
            outcome="cancelled",
            elapsed=elapsed,
            attributes=base_attributes,
        )
        raise
    except Exception as exc:
        elapsed = perf_counter() - started_at
        _record_stage_timing(stage_timings, stage, elapsed)
        record_rag_stage(mode, stage, "error", elapsed)
        record_trace_span(
            trace_id=trace_id,
            span_name=stage,
            outcome="error",
            seconds=elapsed,
            attributes=base_attributes,
            error=exc,
        )
        await _emit_stage_progress(
            progress_callback,
            trace_id=trace_id,
            stage=stage,
            outcome="error",
            elapsed=elapsed,
            attributes={
                **base_attributes,
                "error_type": type(exc).__name__,
            },
        )
        raise
    elapsed = perf_counter() - started_at
    if result_attributes is not None:
        base_attributes.update(result_attributes(result))
    _record_stage_timing(stage_timings, stage, elapsed)
    record_rag_stage(mode, stage, "success", elapsed)
    record_trace_span(
        trace_id=trace_id,
        span_name=stage,
        outcome="success",
        seconds=elapsed,
        attributes=base_attributes,
    )
    await _emit_stage_progress(
        progress_callback,
        trace_id=trace_id,
        stage=stage,
        outcome="success",
        elapsed=elapsed,
        attributes=base_attributes,
    )
    return result


def _record_stage_timing(
    stage_timings: dict[str, float] | None,
    stage: str,
    elapsed: float,
) -> None:
    if stage_timings is not None:
        stage_timings[stage] = round(elapsed * 1000, 3)


async def _emit_stage_progress(
    progress_callback: SearchStageProgressCallback | None,
    *,
    trace_id: str,
    stage: str,
    outcome: str,
    elapsed: float,
    attributes: Mapping[str, object],
) -> None:
    if progress_callback is None:
        return
    await progress_callback(
        SearchStageProgress(
            trace_id=trace_id,
            stage=stage,
            outcome=outcome,
            elapsed_ms=round(elapsed * 1000, 3),
            attributes=dict(attributes),
        )
    )


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
