"""RAG 診断情報のテスト。"""

from app.config import Settings
from app.rag.diagnostics import build_search_diagnostics, rag_config_fingerprint
from app.schemas.search import SearchMode, SearchRequest


def test_search_diagnostics_exposes_execution_shape_without_secrets() -> None:
    """検索 diagnostics は実行形状を示し、secret 値を含めない。"""
    settings = Settings(
        ai_service_adapter="oci",
        oracle_password="super-secret-password",
        rag_context_window_chars=4096,
    )
    request = SearchRequest(
        query="請求金額",
        top_k=7,
        rerank_top_n=3,
        mode=SearchMode.KEYWORD,
        filters={"status": "analyzed", "file_name": "invoice"},
    )

    diagnostics = build_search_diagnostics(
        request,
        settings=settings,
        retrieved_count=7,
        reranked_count=3,
        citation_count=2,
        context_chars=812,
    )

    assert diagnostics.adapter == "oci"
    assert diagnostics.mode == "keyword"
    assert diagnostics.top_k == 7
    assert diagnostics.rerank_top_n == 3
    assert diagnostics.retrieved_count == 7
    assert diagnostics.reranked_count == 3
    assert diagnostics.citation_count == 2
    assert diagnostics.context_chars == 812
    assert diagnostics.context_window_chars == 4096
    assert diagnostics.filter_keys == ["file_name", "status"]
    assert len(diagnostics.config_fingerprint) == 64
    assert "super-secret-password" not in diagnostics.model_dump_json()


def test_rag_config_fingerprint_changes_when_rag_parameters_change() -> None:
    """fingerprint は RAG の非機密設定変更を反映する。"""
    first = rag_config_fingerprint(Settings(rag_chunk_size=800))
    second = rag_config_fingerprint(Settings(rag_chunk_size=1200))

    assert first != second
