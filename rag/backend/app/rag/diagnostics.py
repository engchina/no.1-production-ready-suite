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
        guardrails_input_enabled=resolved_settings.guardrails_input_enabled,
        guardrails_output_enabled=resolved_settings.guardrails_output_enabled,
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
        "neighbor_child_count": resolved_settings.rag_neighbor_child_count,
        "rerank_enabled": resolved_settings.rag_rerank_enabled,
        # 業務ガイドの手順ごとの検索と追加の検索の枠(#2395)。
        "guide_step_retrieval_enabled": resolved_settings.rag_guide_step_retrieval_enabled,
        "guide_step_max_queries": resolved_settings.rag_guide_step_max_queries,
        "extra_retrieval_max_queries": resolved_settings.rag_extra_retrieval_max_queries,
        "reference_expansion_enabled": resolved_settings.rag_reference_expansion_enabled,
        "reference_expansion_max_targets": resolved_settings.rag_reference_expansion_max_targets,
        "reference_section_read_max_chunks": (
            resolved_settings.rag_reference_section_read_max_chunks
        ),
        # 文書全体への参照の参照先を、目次からモデルが選んだ章節で読む(#5263)。
        "reference_outline_selection_enabled": (
            resolved_settings.rag_reference_outline_selection_enabled
        ),
        "reference_outline_selection_catalog_max_chars": (
            resolved_settings.rag_reference_outline_selection_catalog_max_chars
        ),
        "reference_outline_selection_max_sections": (
            resolved_settings.rag_reference_outline_selection_max_sections
        ),
        "reference_outline_selection_read_max_chunks": (
            resolved_settings.rag_reference_outline_selection_read_max_chunks
        ),
        # 起点の文書の目次から選んだ章節を rerank の枠の外で根拠に足す(#5324)。
        "outline_selection_enabled": resolved_settings.rag_outline_selection_enabled,
        "outline_selection_max_documents": resolved_settings.rag_outline_selection_max_documents,
        "outline_selection_catalog_max_chars": (
            resolved_settings.rag_outline_selection_catalog_max_chars
        ),
        "outline_selection_read_max_chars": (
            resolved_settings.rag_outline_selection_read_max_chars
        ),
        # 2 つの目次の選択の目次の行に添える本文の書き出しの字数(#5345)。
        "outline_lead_chars": resolved_settings.rag_outline_lead_chars,
        # 実体の 1 段の拡張(#1362)。検索方法の全体設定で選ぶ(#1388・#3053)。
        "entity_expansion_enabled": resolved_settings.rag_entity_expansion_enabled,
        "entity_expansion_max_chunks": resolved_settings.rag_entity_expansion_max_chunks,
        # 関係情報で検索の候補を足す(#2055)。
        "graph_expansion_enabled": resolved_settings.rag_graph_expansion_enabled,
        "graph_expansion_max_chunks": resolved_settings.rag_graph_expansion_max_chunks,
        # 章節の拡張(#1436)。検索方法の全体設定で選ぶ。
        "section_expansion_enabled": resolved_settings.rag_section_expansion_enabled,
        "section_expansion_max_levels": resolved_settings.rag_section_expansion_max_levels,
        "section_expansion_input_token_percent": (
            resolved_settings.rag_section_expansion_input_token_percent
        ),
        # 章節の拡張が次の段を読む前に生成に残す、生成の 1 回の見積もり(#5184。読む段の数が変わる)。
        "answer_generation_round_estimate_seconds": (
            resolved_settings.rag_answer_generation_round_estimate_seconds
        ),
        # 回答文の生成の段の出力の上限(#5368。上限に届いた生成は失敗になる)。
        "answer_generation_max_output_tokens": (
            resolved_settings.rag_answer_generation_max_output_tokens
        ),
        # 生成に渡す根拠の原文の文字予算(#3776)。
        "evidence_budget_chars": resolved_settings.rag_evidence_budget_chars,
        # 分割読取の並行数(#4997)。
        "batch_read_workers": resolved_settings.rag_batch_read_workers,
        # rerank の後、起点に採る同じ文書の chunk の上限(#2167)。
        "max_anchors_per_document": resolved_settings.rag_max_anchors_per_document,
        # 回答の LLM の時間の短縮(#1499)。
        "generation_reasoning_effort": resolved_settings.rag_generation_reasoning_effort,
        "history_rewrite_enabled": resolved_settings.rag_history_rewrite_enabled,
        "search_top_k_default": resolved_settings.rag_search_top_k_default,
        "guardrails_input_enabled": resolved_settings.guardrails_input_enabled,
        "guardrails_output_enabled": resolved_settings.guardrails_output_enabled,
        # 検索の経路と候補の予算(#4141。rerank に進む候補の数が変わる)。
        "hybrid_search_backend": resolved_settings.rag_hybrid_search_backend,
        # 全文検索の語と式の文字数の上限(#5157)。
        "text_search_max_tokens": resolved_settings.rag_text_search_max_tokens,
        "text_search_max_query_chars": resolved_settings.rag_text_search_max_query_chars,
        "native_vector_exact_max_chunks": resolved_settings.rag_native_vector_exact_max_chunks,
        "native_vector_shortage_oversample_factor": (
            resolved_settings.rag_native_vector_shortage_oversample_factor
        ),
        "rrf_method_ratio": resolved_settings.rag_rrf_method_ratio,
        "retrieval_query_limit": resolved_settings.rag_retrieval_query_limit,
        "retrieval_candidate_limit": resolved_settings.rag_retrieval_candidate_limit,
        "rerank_candidate_limit": resolved_settings.rag_rerank_candidate_limit,
        "rerank_min_relevance_score": resolved_settings.rag_rerank_min_relevance_score,
        "candidate_diversity_depth": resolved_settings.rag_candidate_diversity_depth,
        "candidate_diversity_max_extra": resolved_settings.rag_candidate_diversity_max_extra,
        "candidate_diversity_new_document": resolved_settings.rag_candidate_diversity_new_document,
        "candidate_diversity_present_document": (
            resolved_settings.rag_candidate_diversity_present_document
        ),
        # 段ごとの追加の検索の予算(#4141)。
        "request_coverage_chunks_per_subquestion": (
            resolved_settings.rag_request_coverage_chunks_per_subquestion
        ),
        "request_coverage_deadline_seconds": (
            resolved_settings.rag_request_coverage_deadline_seconds
        ),
        "request_coverage_max_chunks": resolved_settings.rag_request_coverage_max_chunks,
        "request_coverage_max_queries": resolved_settings.rag_request_coverage_max_queries,
        "request_coverage_max_subquestions": (
            resolved_settings.rag_request_coverage_max_subquestions
        ),
        "guide_step_chunks_per_step": resolved_settings.rag_guide_step_chunks_per_step,
        "guide_step_deadline_seconds": resolved_settings.rag_guide_step_deadline_seconds,
        # 用語・業務ガイドの使い方(#4141。内容の版は manifest の profiles に残す)。
        "runtime_terms_search_enabled": resolved_settings.rag_runtime_terms_search_enabled,
        "runtime_terms_answer_enabled": resolved_settings.rag_runtime_terms_answer_enabled,
        "support_guide_enabled": resolved_settings.rag_support_guide_enabled,
        "support_guide_semantic_match_enabled": (
            resolved_settings.rag_support_guide_semantic_match_enabled
        ),
        "support_guide_semantic_threshold": resolved_settings.rag_support_guide_semantic_threshold,
        "support_guide_close_match_clarification_enabled": (
            resolved_settings.rag_support_guide_close_match_clarification_enabled
        ),
        "support_guide_close_match_margin": resolved_settings.rag_support_guide_close_match_margin,
        # Wiki の検索の経路(#4941)。使用・候補と選択の上限・時間の上限。
        "wiki_search": {
            key.removeprefix("rag_wiki_"): getattr(resolved_settings, key)
            for key in (
                "rag_wiki_search_enabled",
                "rag_wiki_candidates",
                "rag_wiki_selected_documents",
                "rag_wiki_selected_units",
                "rag_wiki_selected_sections",
                "rag_wiki_section_catalog_limit",
                "rag_wiki_query_timeout_seconds",
            )
        },
        # 最終照合の入力の分け方(#4141)。
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
