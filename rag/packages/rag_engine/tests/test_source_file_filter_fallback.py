"""明示した資料・ページを緩和せず、自然言語からは SQL 条件を作らない。"""

import inspect
from rag_engine.adapters.oracle.store import _metadata_filter_sql, search_adb_hybrid_chunks
from rag_engine.retrieval.metadata_filters import RetrievalMetadataFilter


def test_only_explicit_conditions_are_accepted():
    assert "inquiry_conditions" not in inspect.signature(search_adb_hybrid_chunks).parameters
    sql, binds = _metadata_filter_sql("c", RetrievalMetadataFilter(source_file_terms=("A.pdf",), page_numbers=(2,)))
    assert "LOWER(c.source_file_name)" in sql
    assert "c.page_start" in sql
    assert binds["metadata_page_0"] == 2
    assert binds["metadata_source_file_0"] == "%a.pdf%"


def test_no_explicit_conditions_means_no_implicit_sql_filter():
    assert _metadata_filter_sql("c", None) == ("", {})
