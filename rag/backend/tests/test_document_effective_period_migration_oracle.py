"""文書の分類（#2127 で削除）の名残を消す migration のテスト。

SQL の形は ``test_oracle_schema_cli.py`` が確かめる。ここでは実 Oracle で、旧版の形
（rag_documents.classification に分類と有効期間・rag_query_history.classification_filter）に
戻した表へ migration を 2 回当て、有効期間だけが effective_period に残ること・分類が消えること・
冪等なことを確かめる（未到達なら oracle_db fixture が skip する）。
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any
from uuid import uuid4

import pytest

from app.rag.oracle_schema import oracle_schema_migration_sections, split_sql_statements
from tests import _oracle_test_db

_MIGRATION = "20261016_002_document_effective_period"


def _migration_statements() -> list[str]:
    section = next(item for item in oracle_schema_migration_sections() if item.name == _MIGRATION)
    return split_sql_statements(section.sql)


def _column_exists(cursor: Any, table: str, column: str) -> bool:
    cursor.execute(
        "SELECT COUNT(*) FROM user_tab_columns WHERE table_name = :t AND column_name = :c",
        {"t": table, "c": column},
    )
    return int(cursor.fetchone()[0]) > 0


def _apply_migration(cursor: Any) -> None:
    for statement in _migration_statements():
        cursor.execute(statement)


@pytest.fixture
def legacy_classification_schema(oracle_db: None) -> Iterator[tuple[Any, str, str]]:
    """文書の表を分類の残っていた旧版の形に戻し、後で migration で今の形にそろえ直す。

    実 Oracle のテスト（oracle_db）は xdist でも 1 つの worker で直列に動く（conftest の
    ``xdist_group("oracle")``）ので、表の形を一時的に変えても他のテストと重ならない。
    """
    dated_id = f"pytest-dated-{uuid4().hex[:16]}"
    category_only_id = f"pytest-category-{uuid4().hex[:16]}"
    with _oracle_test_db._schema_connection() as connection:
        cursor = connection.cursor()
        try:
            cursor.execute(
                "ALTER TABLE rag_documents RENAME COLUMN effective_period TO classification"
            )
            cursor.execute("ALTER TABLE rag_query_history ADD (classification_filter JSON)")
            for document_id, classification in (
                (
                    dated_id,
                    {
                        "large_category": "10_業務A",
                        "middle_category": "操作説明書",
                        "small_category": "登録",
                        "source": "manual",
                        "effective_from": "2026-04-01",
                        "effective_to": "2027-04-01",
                    },
                ),
                (category_only_id, {"large_category": "業務B"}),
            ):
                cursor.execute(
                    "INSERT INTO rag_documents (document_id, file_name, status, classification) "
                    "VALUES (:document_id, :file_name, 'UPLOADED', :classification)",
                    {
                        "document_id": document_id,
                        "file_name": f"{document_id}.pdf",
                        "classification": json.dumps(classification, ensure_ascii=False),
                    },
                )
            connection.commit()
            yield cursor, dated_id, category_only_id
        finally:
            # 失敗しても今の形に戻す（migration は冪等）。テストの行を消す。
            connection.rollback()
            _apply_migration(cursor)
            cursor.execute(
                "DELETE FROM rag_documents WHERE document_id IN (:dated_id, :category_only_id)",
                {"dated_id": dated_id, "category_only_id": category_only_id},
            )
            connection.commit()


def _effective_period(cursor: Any, document_id: str) -> dict[str, Any] | None:
    cursor.execute(
        "SELECT JSON_SERIALIZE(effective_period RETURNING VARCHAR2(4000)) "
        "FROM rag_documents WHERE document_id = :document_id",
        {"document_id": document_id},
    )
    value = cursor.fetchone()[0]
    return None if value is None else json.loads(value)


def test_document_effective_period_migration_on_real_oracle(
    legacy_classification_schema: tuple[Any, str, str],
) -> None:
    cursor, dated_id, category_only_id = legacy_classification_schema

    for _ in range(2):  # 2 回目は何もしない（冪等）。
        _apply_migration(cursor)

        assert not _column_exists(cursor, "RAG_DOCUMENTS", "CLASSIFICATION")
        assert _column_exists(cursor, "RAG_DOCUMENTS", "EFFECTIVE_PERIOD")
        assert not _column_exists(cursor, "RAG_QUERY_HISTORY", "CLASSIFICATION_FILTER")
        # 有効期間だけを残し、分類の key は消す。有効期間の無い文書は NULL。
        assert _effective_period(cursor, dated_id) == {
            "effective_from": "2026-04-01",
            "effective_to": "2027-04-01",
        }
        assert _effective_period(cursor, category_only_id) is None
