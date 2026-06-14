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
        retrieved_count=7,
        reranked_count=3,
        context_diversified_count=1,
        context_group_expanded_count=2,
        context_expanded_count=1,
        context_compressed_count=1,
        context_compression_saved_chars=1200,
        citation_count=2,
        context_chars=812,
    )

    assert diagnostics.mode == "keyword"
    assert diagnostics.top_k == 7
    assert diagnostics.rerank_top_n == 3
    assert diagnostics.retrieved_count == 7
    assert diagnostics.reranked_count == 3
    assert diagnostics.deduplicated_count == 0
    assert diagnostics.context_diversified_count == 1
    assert diagnostics.context_group_expanded_count == 2
    assert diagnostics.context_expanded_count == 1
    assert diagnostics.context_compressed_count == 1
    assert diagnostics.context_compression_saved_chars == 1200
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
