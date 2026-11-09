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
        guardrails_input_enabled=resolved_settings.rag_guardrails_input_enabled,
        guardrails_output_enabled=resolved_settings.rag_guardrails_output_enabled,
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
        # 条の目録から根拠の条を選ぶ段(#2254)。
        "article_catalog_enabled": resolved_settings.rag_article_catalog_enabled,
        "article_catalog_max_documents": resolved_settings.rag_article_catalog_max_documents,
        "article_catalog_context_records": resolved_settings.rag_article_catalog_context_records,
        "article_catalog_scope_documents": resolved_settings.rag_article_catalog_scope_documents,
        # 業務ガイドの手順ごとの検索と追加の検索の枠(#2395)。
        "guide_step_retrieval_enabled": resolved_settings.rag_guide_step_retrieval_enabled,
        "guide_step_max_queries": resolved_settings.rag_guide_step_max_queries,
        "extra_retrieval_max_queries": resolved_settings.rag_extra_retrieval_max_queries,
        "reference_expansion_enabled": resolved_settings.rag_reference_expansion_enabled,
        "reference_expansion_max_chunks": resolved_settings.rag_reference_expansion_max_chunks,
        "reference_expansion_max_targets": resolved_settings.rag_reference_expansion_max_targets,
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
        # 生成に渡す根拠の原文の文字予算(#3776)。
        "evidence_budget_chars": resolved_settings.rag_evidence_budget_chars,
        # rerank の後、起点に採る同じ文書の chunk の上限(#2167)。
        "max_anchors_per_document": resolved_settings.rag_max_anchors_per_document,
        "auto_field_filter_enabled": resolved_settings.rag_auto_field_filter_enabled,
        # 回答の LLM の時間の短縮(#1499)。
        "strategy_reasoning_effort": resolved_settings.rag_strategy_reasoning_effort,
        "judge_reasoning_effort": resolved_settings.rag_judge_reasoning_effort,
        "generation_reasoning_effort": resolved_settings.rag_generation_reasoning_effort,
        "crag_compact_input": resolved_settings.rag_crag_compact_input,
        # CRAG の評価の候補の短い ID(#1864)。
        "crag_short_candidate_ids": resolved_settings.rag_crag_short_candidate_ids,
        "section_skip_regrade_at_scope": resolved_settings.rag_section_skip_regrade_at_scope,
        "crag_skip_on_strong_signals": resolved_settings.rag_crag_skip_on_strong_signals,
        "crag_skip_min_rerank_score": resolved_settings.rag_crag_skip_min_rerank_score,
        "query_expansion_max_queries": resolved_settings.rag_query_expansion_max_queries,
        "history_rewrite_enabled": resolved_settings.rag_history_rewrite_enabled,
        "search_top_k_default": resolved_settings.rag_search_top_k_default,
        "guardrails_input_enabled": resolved_settings.rag_guardrails_input_enabled,
        "guardrails_output_enabled": resolved_settings.rag_guardrails_output_enabled,
        # 検索の経路と候補の予算(#4141。rerank に進む候補の数が変わる)。
        "hybrid_search_backend": resolved_settings.rag_hybrid_search_backend,
        "native_vector_exact_max_chunks": resolved_settings.rag_native_vector_exact_max_chunks,
        "native_vector_shortage_oversample_factor": (
            resolved_settings.rag_native_vector_shortage_oversample_factor
        ),
        "rrf_method_ratio": resolved_settings.rag_rrf_method_ratio,
        "rrf_source_ratio": resolved_settings.rag_rrf_source_ratio,
        "rerank_slot_ratio": resolved_settings.rag_rerank_slot_ratio,
        "retrieval_query_limit": resolved_settings.rag_retrieval_query_limit,
        "retrieval_candidate_limit": resolved_settings.rag_retrieval_candidate_limit,
        "rerank_candidate_limit": resolved_settings.rag_rerank_candidate_limit,
        "rerank_top_n": resolved_settings.rag_rerank_top_n,
        "rerank_min_relevance_score": resolved_settings.rag_rerank_min_relevance_score,
        "candidate_diversity_depth": resolved_settings.rag_candidate_diversity_depth,
        "candidate_diversity_max_extra": resolved_settings.rag_candidate_diversity_max_extra,
        "candidate_diversity_new_document": resolved_settings.rag_candidate_diversity_new_document,
        "candidate_diversity_present_document": (
            resolved_settings.rag_candidate_diversity_present_document
        ),
        # 段ごとの追加の検索の予算(#4141)。
        "section_expansion_followup_reserve_percent": (
            resolved_settings.rag_section_expansion_followup_reserve_percent
        ),
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
        "guide_reference_max_chunks": resolved_settings.rag_guide_reference_max_chunks,
        "article_catalog_max_articles": resolved_settings.rag_article_catalog_max_articles,
        "article_catalog_original_question_search": (
            resolved_settings.rag_article_catalog_original_question_search
        ),
        "article_catalog_refine_articles": resolved_settings.rag_article_catalog_refine_articles,
        "article_catalog_refine_candidates": (
            resolved_settings.rag_article_catalog_refine_candidates
        ),
        "article_catalog_output_token_cap": resolved_settings.rag_article_catalog_output_token_cap,
        # 用語・回答ルール・業務ガイドの使い方(#4141。内容の版は manifest の profiles に残す)。
        "runtime_rules_confirmation_enabled": (
            resolved_settings.rag_runtime_rules_confirmation_enabled
        ),
        "runtime_rules_answer_enabled": resolved_settings.rag_runtime_rules_answer_enabled,
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
        # 最終照合の入力の分け方(#4141)。
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
