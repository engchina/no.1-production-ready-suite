"""scalar / 日付 / カテゴリ pre-filter（PoweRAG 由来）の単体テスト。

`normalize_search_filters`（schema 検証）と `_oracle_retrieval_where`（Oracle 26ai 述語生成）は
いずれも純粋関数なので、実 Oracle なしで pre-filter のロジックを検証できる。
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.clients.oracle import _oracle_retrieval_where
from app.schemas.search import normalize_search_filters


def test_normalize_numeric_range_filters_parse_and_canonicalize() -> None:
    normalized = normalize_search_filters({"page_number_min": " 2 ", "page_number_max": "10"})
    assert normalized == {"page_number_min": "2", "page_number_max": "10"}


def test_normalize_rejects_non_integer_numeric_filter() -> None:
    with pytest.raises(ValueError, match="数値フィルター"):
        normalize_search_filters({"page_number_min": "abc"})


def test_normalize_rejects_negative_numeric_filter() -> None:
    with pytest.raises(ValueError, match="0 以上"):
        normalize_search_filters({"page_number_min": "-1"})


def test_normalize_rejects_inverted_numeric_range() -> None:
    with pytest.raises(ValueError, match="page_number_min"):
        normalize_search_filters({"page_number_min": "9", "page_number_max": "3"})


def test_normalize_accepts_date_only_and_datetime_filters() -> None:
    normalized = normalize_search_filters(
        {"uploaded_from": "2026-01-01", "uploaded_to": "2026-01-31T12:00:00Z"}
    )
    assert normalized == {
        "uploaded_from": "2026-01-01",
        "uploaded_to": "2026-01-31T12:00:00Z",
    }


def test_normalize_rejects_bad_date_filter() -> None:
    with pytest.raises(ValueError, match="日付フィルター"):
        normalize_search_filters({"indexed_from": "2026/01/01"})


def test_normalize_rejects_inverted_date_range() -> None:
    with pytest.raises(ValueError, match="以前"):
        normalize_search_filters({"uploaded_from": "2026-02-01", "uploaded_to": "2026-01-01"})


def test_normalize_content_kinds_dedupes_and_validates() -> None:
    normalized = normalize_search_filters({"content_kinds": "Table, figure ,table"})
    assert normalized == {"content_kinds": "table,figure"}


def test_normalize_rejects_unknown_content_kind_in_list() -> None:
    with pytest.raises(ValueError, match="内容種別"):
        normalize_search_filters({"content_kinds": "table,bogus"})


def test_retrieval_where_builds_numeric_range_predicates() -> None:
    sql, binds = _oracle_retrieval_where({"page_number_min": "2", "page_number_max": "5"})
    assert "$.page_number' RETURNING NUMBER) >= :filter_page_number_min" in sql
    assert "$.page_number' RETURNING NUMBER) <= :filter_page_number_max" in sql
    assert binds["filter_page_number_min"] == 2
    assert binds["filter_page_number_max"] == 5


def test_retrieval_where_builds_date_range_with_end_of_day_boundary() -> None:
    sql, binds = _oracle_retrieval_where(
        {"uploaded_from": "2026-01-01", "uploaded_to": "2026-01-31"}
    )
    assert "d.uploaded_at >= :filter_uploaded_from" in sql
    assert "d.uploaded_at <= :filter_uploaded_to" in sql
    assert binds["filter_uploaded_from"] == datetime(2026, 1, 1, tzinfo=UTC)
    # date-only の `_to` は当日全体を含むよう終端へ寄せる。
    assert binds["filter_uploaded_to"] == datetime(2026, 1, 31, 23, 59, 59, 999999, tzinfo=UTC)


def test_retrieval_where_parses_zulu_datetime_as_utc() -> None:
    _, binds = _oracle_retrieval_where({"indexed_from": "2026-03-04T05:06:07Z"})
    assert binds["filter_indexed_from"] == datetime(2026, 3, 4, 5, 6, 7, tzinfo=UTC)


def test_retrieval_where_builds_content_kind_in_predicate() -> None:
    sql, binds = _oracle_retrieval_where({"content_kinds": "table,figure"})
    assert "LOWER(JSON_VALUE(c.metadata_json, '$.content_kind')) IN (" in sql
    assert binds["filter_content_kind_in_0"] == "table"
    assert binds["filter_content_kind_in_1"] == "figure"


def test_retrieval_where_rejects_unknown_filter_key() -> None:
    with pytest.raises(ValueError, match="未対応の検索フィルター"):
        _oracle_retrieval_where({"totally_unknown": "x"})
