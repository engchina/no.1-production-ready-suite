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
    citation_count: int = 0,
    context_chars: int = 0,
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
        citation_count=citation_count,
        context_chars=context_chars,
        context_window_chars=resolved_settings.rag_context_window_chars,
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
        "max_chunks_per_document": resolved_settings.rag_max_chunks_per_document,
        "min_similarity": resolved_settings.rag_min_similarity,
        "search_timeout_seconds": resolved_settings.rag_search_timeout_seconds,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
