"""RAG 実行時の非機密診断情報。"""

import hashlib
import json
from collections.abc import Mapping
from typing import Literal

from rag_pipeline_core.vector_index import resolve_vector_index

from app.config import Settings, get_settings
from app.schemas.common import JsonValue
from app.schemas.search import SearchDiagnostics, SearchRequest

# 回答の経路。grounded は根拠付き回答、retrieval_only は検索だけ
# (KB の検索テスト・レシピの検索比較。#593)、blocked は質問の安全チェックで止めたもの。
RetrievalPath = Literal["grounded", "retrieval_only", "blocked"]


def _effective_vector_target_accuracy(settings: Settings) -> int:
    """選択 profile 解決後の検索時 target accuracy(fingerprint 用)。

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
    retrieval_strategy_adapter: RetrievalPath,
    guardrail_degraded: bool = False,
    answer: Mapping[str, JsonValue] | None = None,
) -> SearchDiagnostics:
    """検索実行の再現・調査に使う非機密メタデータを作る。"""
    resolved_settings = settings or get_settings()
    return SearchDiagnostics(
        retrieval_strategy="hybrid",
        retrieval_strategy_adapter=retrieval_strategy_adapter,
        guardrail_policy=resolved_settings.rag_guardrail_policy,
        guardrail_backend=resolved_settings.rag_guardrail_backend,
        guardrail_degraded=guardrail_degraded,
        filter_keys=sorted(request.filters),
        knowledge_base_count=len(request.knowledge_base_ids),
        config_fingerprint=rag_config_fingerprint(resolved_settings),
        answer=dict(answer) if answer is not None else None,
    )


def rag_config_fingerprint(settings: Settings | None = None) -> str:
    """検索・回答の結果に効く RAG 設定の非機密 fingerprint を返す。"""
    resolved_settings = settings or get_settings()
    payload = {
        "embedding_dim": resolved_settings.oci_genai_embedding_dim,
        "embedding_model": resolved_settings.oci_genai_embedding_model,
        "rerank_model": resolved_settings.oci_genai_rerank_model,
        "chunk_size": resolved_settings.rag_chunk_size,
        "chunk_overlap": resolved_settings.rag_chunk_overlap,
        "context_group_max_chunks": resolved_settings.rag_context_group_max_chunks,
        "min_similarity": resolved_settings.rag_min_similarity,
        "rrf_k": resolved_settings.rag_rrf_k,
        "vector_index_profile": resolved_settings.rag_vector_index_profile,
        "oracle_vector_target_accuracy": _effective_vector_target_accuracy(resolved_settings),
        "query_strategy": resolved_settings.rag_query_strategy,
        "answer_flow": resolved_settings.rag_answer_flow,
        "neighbor_child_count": resolved_settings.rag_neighbor_child_count,
        "rerank_enabled": resolved_settings.rag_rerank_enabled,
        "screen_linking_enabled": resolved_settings.rag_screen_linking_enabled,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
