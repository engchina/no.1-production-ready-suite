"""RAG 診断情報のテスト。"""

from app.config import Settings
from app.rag.diagnostics import build_search_diagnostics, rag_config_fingerprint
from app.schemas.search import SearchMode, SearchRequest


def test_search_diagnostics_exposes_execution_shape_without_secrets() -> None:
    """検索 diagnostics は実行形状を示し、secret 値を含めない。"""
    settings = Settings(
        oracle_password="super-secret-password",
        rag_context_window_chars=4096,
        rag_rrf_k=30,
        oracle_vector_target_accuracy=90,
    )
    request = SearchRequest(
        query="承認条件",
        top_k=7,
        rerank_top_n=3,
        mode=SearchMode.KEYWORD,
        filters={"status": "indexed", "file_name": "policy"},
    )

    diagnostics = build_search_diagnostics(
        request,
        settings=settings,
        memory_plan_id="mp-12345678",
        business_context={"tenant_scoped": True},
        retrieval_plan={"memory_sequence": ["evidence", "history"]},
        retrieved_context_pack={"rejection_reasons": ["access_denied"]},
        context_builder={"included_count": 2},
        retrieved_count=7,
        reranked_count=3,
        deduplicated_count=1,
        context_diversified_count=1,
        context_group_expanded_count=2,
        context_expanded_count=1,
        context_compressed_count=1,
        context_compression_saved_chars=1200,
        agent_memory_retrieved_count=1,
        agent_memory_writeback_count=1,
        agent_memory_writeback_status="saved",
        evidence_count=1,
        support_count=1,
        history_count=1,
        resolver_rejected_count=1,
        insufficient_context_count=1,
        citation_count=2,
        context_chars=812,
    )

    assert diagnostics.mode == "keyword"
    assert diagnostics.top_k == 7
    assert diagnostics.rerank_top_n == 3
    assert diagnostics.memory_plan_id == "mp-12345678"
    assert diagnostics.business_context == {"tenant_scoped": True}
    assert diagnostics.retrieval_plan == {"memory_sequence": ["evidence", "history"]}
    assert diagnostics.retrieved_context_pack == {"rejection_reasons": ["access_denied"]}
    assert diagnostics.context_builder == {"included_count": 2}
    assert diagnostics.retrieved_count == 7
    assert diagnostics.reranked_count == 3
    assert diagnostics.deduplicated_count == 1
    assert diagnostics.context_diversified_count == 1
    assert diagnostics.context_group_expanded_count == 2
    assert diagnostics.context_expanded_count == 1
    assert diagnostics.context_compressed_count == 1
    assert diagnostics.context_compression_saved_chars == 1200
    assert diagnostics.agent_memory_retrieved_count == 1
    assert diagnostics.agent_memory_writeback_count == 1
    assert diagnostics.agent_memory_writeback_status == "saved"
    assert diagnostics.evidence_count == 1
    assert diagnostics.support_count == 1
    assert diagnostics.history_count == 1
    assert diagnostics.resolver_rejected_count == 1
    assert diagnostics.insufficient_context_count == 1
    assert diagnostics.citation_count == 2
    assert diagnostics.context_chars == 812
    assert diagnostics.context_window_chars == 4096
    assert diagnostics.rrf_k == 30
    assert diagnostics.query_variant_count == 1
    assert diagnostics.oracle_vector_target_accuracy == 90
    assert diagnostics.filter_keys == ["file_name", "status"]
    assert len(diagnostics.config_fingerprint) == 64
    assert "super-secret-password" not in diagnostics.model_dump_json()


def test_rag_config_fingerprint_changes_when_rag_parameters_change() -> None:
    """fingerprint は RAG の非機密設定変更を反映する。"""
    first = rag_config_fingerprint(Settings(rag_chunk_size=800))
    second = rag_config_fingerprint(Settings(rag_chunk_size=1200))

    assert first != second


def test_rag_config_fingerprint_changes_when_oracle_vector_accuracy_changes() -> None:
    """fingerprint は Oracle approximate search 精度の変更も反映する。"""
    first = rag_config_fingerprint(Settings(oracle_vector_target_accuracy=95))
    second = rag_config_fingerprint(Settings(oracle_vector_target_accuracy=90))

    assert first != second


def test_rag_config_fingerprint_changes_when_rrf_k_changes() -> None:
    """fingerprint は hybrid RRF 定数の変更も反映する。"""
    first = rag_config_fingerprint(Settings(rag_rrf_k=60))
    second = rag_config_fingerprint(Settings(rag_rrf_k=10))

    assert first != second


def test_rag_config_fingerprint_changes_when_query_expansion_changes() -> None:
    """fingerprint は query expansion 設定の変更も反映する。"""
    first = rag_config_fingerprint(Settings(rag_query_expansion_enabled=True))
    second = rag_config_fingerprint(Settings(rag_query_expansion_enabled=False))
    third = rag_config_fingerprint(Settings(rag_query_expansion_max_variants=2))

    assert first != second
    assert first != third


def test_rag_config_fingerprint_changes_when_context_neighbor_window_changes() -> None:
    """fingerprint は隣接 context expansion 設定の変更も反映する。"""
    first = rag_config_fingerprint(Settings(rag_context_neighbor_window=0))
    second = rag_config_fingerprint(Settings(rag_context_neighbor_window=1))

    assert first != second


def test_rag_config_fingerprint_changes_when_context_diversity_changes() -> None:
    """fingerprint は context diversity 設定の変更も反映する。"""
    first = rag_config_fingerprint(Settings(rag_context_diversity_lambda=1.0))
    second = rag_config_fingerprint(Settings(rag_context_diversity_lambda=0.35))

    assert first != second


def test_rag_config_fingerprint_changes_when_context_group_expansion_changes() -> None:
    """fingerprint は同一 group context expansion 設定の変更も反映する。"""
    first = rag_config_fingerprint(Settings(rag_context_group_expansion_enabled=False))
    second = rag_config_fingerprint(Settings(rag_context_group_expansion_enabled=True))
    third = rag_config_fingerprint(Settings(rag_context_group_max_chunks=2))

    assert first != second
    assert first != third


def test_rag_config_fingerprint_changes_when_context_compression_changes() -> None:
    """fingerprint は context compression 設定の変更も反映する。"""
    first = rag_config_fingerprint(Settings(rag_context_compression_enabled=False))
    second = rag_config_fingerprint(Settings(rag_context_compression_enabled=True))
    third = rag_config_fingerprint(Settings(rag_context_compression_max_sentences=2))

    assert first != second
    assert first != third


def test_rag_config_fingerprint_changes_when_agent_memory_changes() -> None:
    """fingerprint は Agent Memory 検索・保存設定の変更も反映する。"""
    first = rag_config_fingerprint(Settings(rag_agent_memory_search_enabled=True))
    second = rag_config_fingerprint(Settings(rag_agent_memory_search_enabled=False))
    third = rag_config_fingerprint(Settings(rag_agent_memory_top_k=5))
    fourth = rag_config_fingerprint(Settings(rag_agent_memory_writeback_enabled=False))

    assert first != second
    assert first != third
    assert first != fourth


def test_search_diagnostics_target_accuracy_follows_vector_index_profile() -> None:
    """検索診断の target accuracy は選択 profile 解決後の値を返す(95/98/85)。"""
    request = SearchRequest(query="承認条件", mode=SearchMode.VECTOR)
    balanced = build_search_diagnostics(
        request,
        settings=Settings(oracle_vector_target_accuracy=95, rag_vector_index_profile="balanced"),
    )
    accurate = build_search_diagnostics(
        request,
        settings=Settings(oracle_vector_target_accuracy=95, rag_vector_index_profile="accurate"),
    )
    fast = build_search_diagnostics(
        request,
        settings=Settings(oracle_vector_target_accuracy=95, rag_vector_index_profile="fast"),
    )

    assert balanced.oracle_vector_target_accuracy == 95
    assert accurate.oracle_vector_target_accuracy == 98
    assert fast.oracle_vector_target_accuracy == 85


def test_rag_config_fingerprint_changes_with_vector_index_profile() -> None:
    """fingerprint は検索インデックス profile の違い(検索時 accuracy)を反映する。"""
    fingerprints = {
        rag_config_fingerprint(Settings(rag_vector_index_profile=profile))
        for profile in ("balanced", "accurate", "fast")
    }

    assert len(fingerprints) == 3
