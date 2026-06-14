"""RAG 実行時の非機密診断情報。"""

import hashlib
import json

from app.config import Settings, get_settings
from app.schemas.search import SearchDiagnostics, SearchRequest


def build_search_diagnostics(
    request: SearchRequest,
    *,
    settings: Settings | None = None,
    retrieved_count: int = 0,
    reranked_count: int = 0,
    deduplicated_count: int = 0,
    context_diversified_count: int = 0,
    context_group_expanded_count: int = 0,
    context_expanded_count: int = 0,
    context_compressed_count: int = 0,
    context_compression_saved_chars: int = 0,
    citation_count: int = 0,
    context_chars: int = 0,
    query_variant_count: int = 1,
) -> SearchDiagnostics:
    """検索実行の再現・調査に使う非機密メタデータを作る。"""
    resolved_settings = settings or get_settings()
    return SearchDiagnostics(
        adapter=resolved_settings.ai_service_adapter,
        mode=request.mode.value,
        top_k=request.top_k,
        rerank_top_n=request.rerank_top_n,
        retrieved_count=retrieved_count,
        reranked_count=reranked_count,
        deduplicated_count=deduplicated_count,
        context_diversified_count=context_diversified_count,
        context_group_expanded_count=context_group_expanded_count,
        context_expanded_count=context_expanded_count,
        context_compressed_count=context_compressed_count,
        context_compression_saved_chars=context_compression_saved_chars,
        citation_count=citation_count,
        context_chars=context_chars,
        context_window_chars=resolved_settings.rag_context_window_chars,
        rrf_k=resolved_settings.rag_rrf_k,
        query_variant_count=query_variant_count,
        oracle_vector_target_accuracy=resolved_settings.oracle_vector_target_accuracy,
        filter_keys=sorted(request.filters),
        config_fingerprint=rag_config_fingerprint(resolved_settings),
    )


def rag_config_fingerprint(settings: Settings | None = None) -> str:
    """RAG 設定の非機密 fingerprint を返す。"""
    resolved_settings = settings or get_settings()
    payload = {
        "adapter": resolved_settings.ai_service_adapter,
        "embedding_dim": resolved_settings.oci_genai_embedding_dim,
        "embedding_model": resolved_settings.oci_genai_embedding_model,
        "rerank_model": resolved_settings.oci_genai_rerank_model,
        "chunk_size": resolved_settings.rag_chunk_size,
        "chunk_overlap": resolved_settings.rag_chunk_overlap,
        "context_window_chars": resolved_settings.rag_context_window_chars,
        "context_neighbor_window": resolved_settings.rag_context_neighbor_window,
        "context_diversity_lambda": resolved_settings.rag_context_diversity_lambda,
        "context_group_expansion_enabled": (
            resolved_settings.rag_context_group_expansion_enabled
        ),
        "context_group_max_chunks": resolved_settings.rag_context_group_max_chunks,
        "context_compression_enabled": resolved_settings.rag_context_compression_enabled,
        "context_compression_max_sentences": (
            resolved_settings.rag_context_compression_max_sentences
        ),
        "context_compression_max_chars_per_chunk": (
            resolved_settings.rag_context_compression_max_chars_per_chunk
        ),
        "max_chunks_per_document": resolved_settings.rag_max_chunks_per_document,
        "min_similarity": resolved_settings.rag_min_similarity,
        "rrf_k": resolved_settings.rag_rrf_k,
        "query_expansion_enabled": resolved_settings.rag_query_expansion_enabled,
        "query_expansion_max_variants": resolved_settings.rag_query_expansion_max_variants,
        "oracle_vector_target_accuracy": resolved_settings.oracle_vector_target_accuracy,
        "search_timeout_seconds": resolved_settings.rag_search_timeout_seconds,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
