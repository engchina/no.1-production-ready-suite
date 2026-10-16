"""文書の有効期間と、検索の基準日（as_of）の絞り込みのテスト。"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from app.api.routes import documents as documents_route
from app.clients.oracle import OracleClient, _effective_period_where, _oracle_retrieval_where
from app.main import app
from app.schemas.document import DocumentDetail, DocumentEffectivePeriod, FileStatus
from app.schemas.search import normalize_search_filters
from tests.support import AsgiTestClient

client = AsgiTestClient(app)


def test_effective_period_rejects_inverted_period() -> None:
    with pytest.raises(ValidationError, match="終了日"):
        DocumentEffectivePeriod(effective_from=date(2026, 4, 1), effective_to=date(2026, 4, 1))


def test_effective_period_rejects_removed_category_keys() -> None:
    """大分類・中分類・小分類（#2127 で削除）は受け付けず、検索の filters にも無い。"""
    with pytest.raises(ValueError, match="未対応"):
        normalize_search_filters({"large_category": "経理"})
    assert DocumentEffectivePeriod.model_validate({"large_category": "経理"}).is_empty()


def test_normalize_accepts_as_of() -> None:
    assert normalize_search_filters({"as_of": "2026-04-01"}) == {"as_of": "2026-04-01"}


def test_normalize_rejects_bad_as_of() -> None:
    with pytest.raises(ValueError, match="基準日"):
        normalize_search_filters({"as_of": "2026/04/01"})


def test_retrieval_where_applies_effective_period() -> None:
    sql, binds = _oracle_retrieval_where({"as_of": "2026-04-01"})

    assert "JSON_VALUE(d.effective_period, '$.effective_from'), :filter_as_of)" in sql
    assert "JSON_VALUE(d.effective_period, '$.effective_to'), '9999-12-31')" in sql
    assert "classification" not in sql
    assert binds["filter_as_of"] == "2026-04-01"


def test_retrieval_where_always_applies_effective_period_with_today() -> None:
    """基準日を指定しなくても、期間外の文書は今日を基準に除外する。"""
    sql, binds = _oracle_retrieval_where({})

    assert "'$.effective_to'), '9999-12-31') > :filter_as_of" in sql
    assert binds["filter_as_of"] == date.today().isoformat()


class FakeDocumentOracle:
    def __init__(self) -> None:
        self.saved: DocumentEffectivePeriod | None = None

    async def save_document_effective_period(
        self, document_id: str, effective_period: DocumentEffectivePeriod
    ) -> DocumentDetail:
        if document_id != "doc-1":
            raise KeyError(document_id)
        self.saved = effective_period
        return DocumentDetail(
            id="doc-1",
            file_name="manual.pdf",
            status=FileStatus.INDEXED,
            uploaded_at=datetime(2026, 1, 1, tzinfo=UTC),
            effective_period=None if effective_period.is_empty() else effective_period,
        )


def test_put_effective_period_saves_and_returns_detail(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeDocumentOracle()
    monkeypatch.setattr(documents_route, "OracleClient", lambda: fake)

    response = client.put(
        "/api/documents/doc-1/effective-period", json={"effective_from": "2026-04-01"}
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["effective_period"] == {"effective_from": "2026-04-01", "effective_to": None}
    assert "classification" not in data
    assert fake.saved is not None and fake.saved.effective_from == date(2026, 4, 1)


def test_put_effective_period_validates_and_returns_404(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(documents_route, "OracleClient", FakeDocumentOracle)

    invalid = client.put(
        "/api/documents/doc-1/effective-period",
        json={"effective_from": "2026-05-01", "effective_to": "2026-04-01"},
    )
    missing = client.put(
        "/api/documents/doc-x/effective-period", json={"effective_from": "2026-04-01"}
    )

    assert invalid.status_code == 422
    assert missing.status_code == 404


def test_removed_classification_endpoint_is_gone() -> None:
    assert client.put("/api/documents/doc-1/classification", json={}).status_code in {404, 405}


@pytest.mark.usefixtures("oracle_db")
async def test_effective_period_is_saved_and_filters_documents_on_real_oracle() -> None:
    """実 Oracle AI Database で、保存した有効期間が述語どおりに絞り込まれることを確かめる。"""
    oracle = OracleClient()
    document = await oracle.create_document(
        file_name="effective-period.pdf",
        object_storage_path="local://effective-period.pdf",
        content_type="application/pdf",
    )
    saved = await oracle.save_document_effective_period(
        document.id,
        DocumentEffectivePeriod(effective_from=date(2026, 4, 1), effective_to=date(2027, 4, 1)),
    )
    assert saved.effective_period is not None
    assert saved.effective_period.effective_from == date(2026, 4, 1)

    async def matches(filters: dict[str, str]) -> bool:
        clauses, binds = _effective_period_where(filters)
        row = await oracle._fetch_one(  # noqa: SLF001 - 述語を実 DB で評価する
            "SELECT COUNT(*) AS count_value FROM rag_documents d "
            "WHERE d.document_id = :document_id AND " + " AND ".join(clauses),
            {**binds, "document_id": document.id},
        )
        return bool(row and int(str(row["count_value"])))

    assert await matches({"as_of": "2026-04-01"})
    assert not await matches({"as_of": "2026-03-31"})
    # 終了日は排他的。
    assert not await matches({"as_of": "2027-04-01"})

    cleared = await oracle.save_document_effective_period(document.id, DocumentEffectivePeriod())
    assert cleared.effective_period is None
    assert await matches({"as_of": "2000-01-01"})
