"""RAG 実行時の非機密診断情報。"""

import hashlib
import json

from rag_pipeline_core.vector_index import resolve_vector_index

from app.config import Settings, get_settings
from app.rag.generation_adapter import generation_contract_mode
from app.schemas.search import (
    SUPPORTED_SCALAR_SEARCH_FILTER_KEYS,
    SearchDiagnostics,
    SearchRequest,
    SearchRetrievalBreakdown,
    SearchRetrievalCandidate,
)


def _effective_vector_target_accuracy(settings: Settings) -> int:
    """選択 profile 解決後の検索時 target accuracy(診断/fingerprint 用)。

    実 SQL は ``resolve_vector_index_adapter`` 経由で同値を使うが、ここでは決定論・
    ネットワーク無しの pure core ロジックで揃える(サービス委譲と同一結果)。
    """
    return resolve_vector_index(
        settings.rag_vector_index_profile,
        settings.oracle_vector_target_accuracy,
    ).target_accuracy


def build_search_diagnostics(
    request: SearchRequest,
    *,
    settings: Settings | None = None,
    retrieval_strategy: str | None = None,
    retrieval_strategy_adapter: str | None = None,
    retrieval_toggles: dict[str, bool] | None = None,
    query_expansion_source: str = "off",
    tree_search_path: list[dict[str, object]] | None = None,
    post_retrieval_pipeline: str | None = None,
    generation_profile: str | None = None,
    generation_attempt_count: int = 0,
    generation_repair_count: int = 0,
    generation_validation_codes: list[str] | None = None,
    guardrail_policy: str | None = None,
    guardrail_degraded: bool = False,
    vector_index_profile: str | None = None,
    graph_profile: str | None = None,
    agentic_profile: str | None = None,
    agentic_subquery_count: int = 0,
    agentic_hops: int = 0,
    route_reason: str | None = None,
    keyword_terms: list[str] | None = None,
    retrieval_breakdown: SearchRetrievalBreakdown | None = None,
    retrieval_candidates: list[SearchRetrievalCandidate] | None = None,
    memory_plan_id: str | None = None,
    graph_hit_count: int = 0,
    fallback_reason: str | None = None,
    gap_stopped: bool = False,
    corrective_retried: bool = False,
    crag_confidence_score: float | None = None,
    crag_fallback_triggered: bool = False,
    crag_hops: int = 0,
    crag_evidence_grade: str = "off",
    hyde_generated: bool = False,
    business_context: dict[str, object] | None = None,
    retrieval_plan: dict[str, object] | None = None,
    retrieved_context_pack: dict[str, object] | None = None,
    context_builder: dict[str, object] | None = None,
    stream_stage_timings: dict[str, float] | None = None,
    retrieved_count: int = 0,
    reranked_count: int = 0,
    deduplicated_count: int = 0,
    context_diversified_count: int = 0,
    context_group_expanded_count: int = 0,
    context_expanded_count: int = 0,
    context_adaptive_expanded_count: int = 0,
    context_dependency_promoted_count: int = 0,
    context_compressed_count: int = 0,
    context_compression_saved_chars: int = 0,
    business_fit_reordered_count: int = 0,
    agent_memory_retrieved_count: int = 0,
    agent_memory_writeback_count: int = 0,
    agent_memory_writeback_status: str = "skipped",
    evidence_count: int = 0,
    support_count: int = 0,
    structure_count: int = 0,
    history_count: int = 0,
    resolver_rejected_count: int = 0,
    insufficient_context_count: int = 0,
    citation_count: int = 0,
    context_chars: int = 0,
    query_variant_count: int = 1,
) -> SearchDiagnostics:
    """検索実行の再現・調査に使う非機密メタデータを作る。"""
    resolved_settings = settings or get_settings()
    resolved_generation_profile = generation_profile or "grounded_concise"
    return SearchDiagnostics(
        mode=request.mode.value,
        retrieval_strategy=retrieval_strategy or request.mode.value,
        retrieval_strategy_adapter=retrieval_strategy_adapter or "hybrid_rrf",
        retrieval_toggles=retrieval_toggles or {},
        query_expansion_source=query_expansion_source,
        tree_search_path=tree_search_path or [],
        post_retrieval_pipeline=post_retrieval_pipeline or "custom",
        generation_profile=resolved_generation_profile,
        generation_config_source=resolved_settings.rag_generation_config_source,
        generation_contract_mode=generation_contract_mode(resolved_generation_profile),
        generation_attempt_count=generation_attempt_count,
        generation_repair_count=generation_repair_count,
        generation_validation_codes=generation_validation_codes or [],
        custom_prompt_version_id=resolved_settings.rag_generation_custom_prompt_version_id,
        guardrail_policy=guardrail_policy or "standard",
        guardrail_backend=resolved_settings.rag_guardrail_backend,
        guardrail_degraded=guardrail_degraded,
        vector_index_profile=vector_index_profile or "balanced",
        graph_profile=graph_profile or "off",
        serving_mode=resolved_settings.rag_serving_mode,
        agentic_profile=agentic_profile or "off",
        agentic_subquery_count=agentic_subquery_count,
        agentic_hops=agentic_hops,
        route_reason=route_reason or "default_hybrid",
        keyword_terms=keyword_terms or [],
        retrieval_breakdown=retrieval_breakdown or SearchRetrievalBreakdown(),
        retrieval_candidates=retrieval_candidates or [],
        memory_plan_id=memory_plan_id,
        graph_hit_count=graph_hit_count,
        fallback_reason=fallback_reason,
        gap_stopped=gap_stopped,
        corrective_retried=corrective_retried,
        crag_confidence_score=crag_confidence_score,
        crag_fallback_triggered=crag_fallback_triggered,
        crag_hops=crag_hops,
        crag_evidence_grade=crag_evidence_grade,
        hyde_generated=hyde_generated,
        business_context=business_context or {},
        retrieval_plan=retrieval_plan or {},
        retrieved_context_pack=retrieved_context_pack or {},
        context_builder=context_builder or {},
        stream_stage_timings=stream_stage_timings or {},
        top_k=request.top_k,
        rerank_top_n=request.rerank_top_n,
        retrieved_count=retrieved_count,
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
        evidence_count=evidence_count,
        support_count=support_count,
        structure_count=structure_count,
        history_count=history_count,
        resolver_rejected_count=resolver_rejected_count,
        insufficient_context_count=insufficient_context_count,
        citation_count=citation_count,
        context_chars=context_chars,
        context_window_chars=resolved_settings.rag_context_window_chars,
        rrf_k=resolved_settings.rag_rrf_k,
        query_variant_count=query_variant_count,
        oracle_vector_target_accuracy=_effective_vector_target_accuracy(resolved_settings),
        filter_keys=sorted(request.filters),
        scalar_filter_keys=sorted(set(request.filters) & SUPPORTED_SCALAR_SEARCH_FILTER_KEYS),
        knowledge_base_count=len(request.knowledge_base_ids),
        config_fingerprint=rag_config_fingerprint(resolved_settings),
    )


def rag_config_fingerprint(settings: Settings | None = None) -> str:
    """RAG 設定の非機密 fingerprint を返す。"""
    resolved_settings = settings or get_settings()
    payload = {
        "embedding_dim": resolved_settings.oci_genai_embedding_dim,
        "embedding_model": resolved_settings.oci_genai_embedding_model,
        "rerank_model": resolved_settings.oci_genai_rerank_model,
        "chunk_size": resolved_settings.rag_chunk_size,
        "chunk_overlap": resolved_settings.rag_chunk_overlap,
        "context_window_chars": resolved_settings.rag_context_window_chars,
        "context_neighbor_window": resolved_settings.rag_context_neighbor_window,
        "context_diversity_lambda": resolved_settings.rag_context_diversity_lambda,
        "context_group_expansion_enabled": (resolved_settings.rag_context_group_expansion_enabled),
        "context_group_max_chunks": resolved_settings.rag_context_group_max_chunks,
        "context_adaptive_expansion_enabled": (
            resolved_settings.rag_context_adaptive_expansion_enabled
        ),
        "context_adaptive_neighbor_window": (
            resolved_settings.rag_context_adaptive_neighbor_window
        ),
        "context_adaptive_min_overlap": resolved_settings.rag_context_adaptive_min_overlap,
        "context_dependency_promotion_enabled": (
            resolved_settings.rag_context_dependency_promotion_enabled
        ),
        "context_dependency_max_chunks": resolved_settings.rag_context_dependency_max_chunks,
        "context_compression_enabled": resolved_settings.rag_context_compression_enabled,
        "context_compression_max_sentences": (
            resolved_settings.rag_context_compression_max_sentences
        ),
        "context_compression_max_chars_per_chunk": (
            resolved_settings.rag_context_compression_max_chars_per_chunk
        ),
        "min_similarity": resolved_settings.rag_min_similarity,
        "rrf_k": resolved_settings.rag_rrf_k,
        "query_expansion_enabled": resolved_settings.rag_query_expansion_enabled,
        "query_expansion_max_variants": resolved_settings.rag_query_expansion_max_variants,
        "vector_index_profile": resolved_settings.rag_vector_index_profile,
        "oracle_vector_target_accuracy": _effective_vector_target_accuracy(resolved_settings),
        "search_timeout_seconds": resolved_settings.rag_search_timeout_seconds,
        "agent_memory_search_enabled": resolved_settings.rag_agent_memory_search_enabled,
        "agent_memory_writeback_enabled": (resolved_settings.rag_agent_memory_writeback_enabled),
        "agent_memory_top_k": resolved_settings.rag_agent_memory_top_k,
        "agent_memory_max_chars": resolved_settings.rag_agent_memory_max_chars,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
