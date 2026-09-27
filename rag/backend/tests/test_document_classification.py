"""文書の分類・有効期間と、検索の分類フィルタ(rag_poc の ClassificationFilter)のテスト。"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from app.api.routes import documents as documents_route
from app.clients.oracle import OracleClient, _classification_where, _oracle_retrieval_where
from app.main import app
from app.schemas.document import DocumentClassification, DocumentDetail, FileStatus
from app.schemas.search import normalize_search_filters
from tests.support import AsgiTestClient

client = AsgiTestClient(app)


def test_classification_blank_values_become_none() -> None:
    classification = DocumentClassification(large_category=" 経理 ", middle_category="")

    assert classification.large_category == "経理"
    assert classification.middle_category is None
    assert not classification.is_empty()
    assert DocumentClassification(small_category=" ").is_empty()


def test_classification_rejects_inverted_effective_period() -> None:
    with pytest.raises(ValidationError, match="終了日"):
        DocumentClassification(effective_from=date(2026, 4, 1), effective_to=date(2026, 4, 1))


def test_normalize_accepts_classification_filters_and_as_of() -> None:
    normalized = normalize_search_filters(
        {"large_category": " 経理 ", "small_category": "", "as_of": "2026-04-01"}
    )

    assert normalized == {"large_category": "経理", "as_of": "2026-04-01"}


def test_normalize_rejects_bad_as_of() -> None:
    with pytest.raises(ValueError, match="基準日"):
        normalize_search_filters({"as_of": "2026/04/01"})


def test_retrieval_where_filters_classification_exactly_and_effective_period() -> None:
    sql, binds = _oracle_retrieval_where(
        {"large_category": "経理", "middle_category": "精算", "as_of": "2026-04-01"}
    )

    assert "JSON_VALUE(d.classification, '$.large_category') = :filter_large_category" in sql
    assert "JSON_VALUE(d.classification, '$.middle_category') = :filter_middle_category" in sql
    assert "small_category" not in sql
    assert "'$.effective_from'), :filter_as_of) <= :filter_as_of" in sql
    assert "'$.effective_to'), '9999-12-31') > :filter_as_of" in sql
    assert binds["filter_large_category"] == "経理"
    assert binds["filter_as_of"] == "2026-04-01"


def test_retrieval_where_always_applies_effective_period_with_today() -> None:
    """基準日を指定しなくても、期間外の文書は今日を基準に除外する(rag_poc と同じ)。"""
    sql, binds = _oracle_retrieval_where({})

    assert "'$.effective_to'), '9999-12-31') > :filter_as_of" in sql
    assert binds["filter_as_of"] == date.today().isoformat()


class FakeDocumentOracle:
    def __init__(self) -> None:
        self.saved: DocumentClassification | None = None

    async def save_document_classification(
        self, document_id: str, classification: DocumentClassification
    ) -> DocumentDetail:
        if document_id != "doc-1":
            raise KeyError(document_id)
        self.saved = classification
        return DocumentDetail(
            id="doc-1",
            file_name="manual.pdf",
            status=FileStatus.INDEXED,
            uploaded_at=datetime(2026, 1, 1, tzinfo=UTC),
            classification=None if classification.is_empty() else classification,
        )


def test_put_classification_saves_and_returns_detail(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeDocumentOracle()
    monkeypatch.setattr(documents_route, "OracleClient", lambda: fake)

    response = client.put(
        "/api/documents/doc-1/classification",
        json={"large_category": "経理", "effective_from": "2026-04-01"},
    )

    assert response.status_code == 200
    assert response.json()["data"]["classification"] == {
        "large_category": "経理",
        "middle_category": None,
        "small_category": None,
        "effective_from": "2026-04-01",
        "effective_to": None,
    }
    assert fake.saved is not None and fake.saved.effective_from == date(2026, 4, 1)


def test_put_classification_validates_and_returns_404(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(documents_route, "OracleClient", FakeDocumentOracle)

    invalid = client.put(
        "/api/documents/doc-1/classification",
        json={"effective_from": "2026-05-01", "effective_to": "2026-04-01"},
    )
    missing = client.put("/api/documents/doc-x/classification", json={"large_category": "経理"})

    assert invalid.status_code == 422
    assert missing.status_code == 404


@pytest.mark.usefixtures("oracle_db")
async def test_classification_is_saved_and_filters_documents_on_real_oracle() -> None:
    """実 Oracle 26ai で、保存した分類と有効期間が述語どおりに絞り込まれることを確かめる。"""
    oracle = OracleClient()
    document = await oracle.create_document(
        file_name="classification.pdf",
        object_storage_path="local://classification.pdf",
        content_type="application/pdf",
    )
    saved = await oracle.save_document_classification(
        document.id,
        DocumentClassification(
            large_category="経理",
            effective_from=date(2026, 4, 1),
            effective_to=date(2027, 4, 1),
        ),
    )
    assert saved.classification is not None
    assert saved.classification.large_category == "経理"

    async def matches(filters: dict[str, str]) -> bool:
        clauses, binds = _classification_where(filters)
        row = await oracle._fetch_one(  # noqa: SLF001 - 述語を実 DB で評価する
            "SELECT COUNT(*) AS count_value FROM rag_documents d "
            "WHERE d.document_id = :document_id AND " + " AND ".join(clauses),
            {**binds, "document_id": document.id},
        )
        return bool(row and int(str(row["count_value"])))

    assert await matches({"large_category": "経理", "as_of": "2026-04-01"})
    assert not await matches({"large_category": "人事", "as_of": "2026-04-01"})
    assert not await matches({"as_of": "2026-03-31"})
    # 終了日は排他的。
    assert not await matches({"as_of": "2027-04-01"})

    cleared = await oracle.save_document_classification(document.id, DocumentClassification())
    assert cleared.classification is None
    assert await matches({"as_of": "2000-01-01"})
