"""業務ビュー単位のドメインキーワード API と全文検索クエリへの反映。"""

from datetime import UTC, datetime

import pytest

from app.api.routes import business_view_knowledge as knowledge_route
from app.clients.oracle import _oracle_text_query
from app.config import Settings
from app.main import app
from app.rag.business_view_config import BusinessViewConfig
from app.schemas.business_view import BusinessViewDetail, BusinessViewStatus
from tests.support import AsgiTestClient

client = AsgiTestClient(app)


class FakeKnowledgeOracle:
    def __init__(self) -> None:
        self.payloads: dict[tuple[str, str], dict[str, object]] = {}
        self.chunk_texts = [
            ("c1", "d1", "伝票区分を変更するには伝票区分マスタを開きます。"),
            ("c2", "d1", "伝票区分の初期値は通常伝票です。受注番号は必須です。"),
            ("c3", "d2", "受注番号を入力して伝票区分を選択します。"),
        ]
        self.requested_kbs: list[str] = []

    async def get_business_view(self, business_view_id: str) -> BusinessViewDetail | None:
        if business_view_id != "bv-1":
            return None
        return BusinessViewDetail(
            id="bv-1",
            name="受注サポート",
            status=BusinessViewStatus.ACTIVE,
            knowledge_base_count=1,
            config=BusinessViewConfig(knowledge_base_ids=["kb-1"]),
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
            updated_at=datetime(2026, 1, 1, tzinfo=UTC),
        )

    async def get_business_view_knowledge(
        self, business_view_id: str, kind: str
    ) -> dict[str, object] | None:
        return self.payloads.get((business_view_id, kind))

    async def save_business_view_knowledge(
        self, business_view_id: str, kind: str, payload: dict[str, object]
    ) -> None:
        self.payloads[(business_view_id, kind)] = payload

    async def list_business_view_chunk_texts(
        self, knowledge_base_ids: list[str], *, limit: int
    ) -> list[tuple[str, str, str]]:
        self.requested_kbs = list(knowledge_base_ids)
        return self.chunk_texts[:limit]


@pytest.fixture
def fake_oracle(monkeypatch: pytest.MonkeyPatch) -> FakeKnowledgeOracle:
    fake = FakeKnowledgeOracle()
    monkeypatch.setattr(knowledge_route, "OracleClient", lambda: fake)
    return fake


def test_domain_keywords_are_normalized_and_saved_per_business_view(
    fake_oracle: FakeKnowledgeOracle,
) -> None:
    resp = client.put(
        "/api/business-views/bv-1/domain-keywords",
        json={"keywords": [" 伝票区分 ", "伝票区分", "", "ORA-01555"]},
    )

    assert resp.status_code == 200
    assert resp.json()["data"]["keywords"] == ["伝票区分", "ORA-01555"]
    got = client.get("/api/business-views/bv-1/domain-keywords")
    assert got.json()["data"]["keywords"] == ["伝票区分", "ORA-01555"]
    assert client.get("/api/business-views/missing/domain-keywords").status_code == 404


def test_domain_keyword_suggestions_use_business_view_knowledge_bases(
    fake_oracle: FakeKnowledgeOracle,
) -> None:
    resp = client.post("/api/business-views/bv-1/domain-keywords/suggest")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert fake_oracle.requested_kbs == ["kb-1"]
    assert data["processed_chunk_count"] == 3
    keywords = [item["keyword"] for item in data["candidates"]]
    assert {"伝票", "受注"} <= set(keywords)


def test_oracle_text_query_keeps_domain_keyword_as_single_term() -> None:
    settings = Settings(rag_domain_keywords=["伝票区分"])

    query = _oracle_text_query("伝票区分を変更する方法", settings=settings)

    assert query is not None
    assert "{伝票区分}" in query
    # キーワードも sudachi も無ければ既存の builtin 分割のまま。
    assert _oracle_text_query("伝票区分を変更する方法", settings=Settings()) == _oracle_text_query(
        "伝票区分を変更する方法"
    )
