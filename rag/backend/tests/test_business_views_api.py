"""業務ビュー(Business View)API のテスト。"""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.api.routes import business_views as business_views_route
from app.main import app
from app.rag.business_view_config import BusinessViewConfig, parse_business_view_config
from app.schemas.business_view import BusinessViewDetail, BusinessViewStatus
from app.schemas.knowledge_base import KnowledgeBaseRef
from tests.support import AsgiTestClient

client = AsgiTestClient(app)


class FakeBusinessViewOracle:
    """business view API テスト用のインメモリ fake。"""

    def __init__(self) -> None:
        self.views: dict[str, BusinessViewDetail] = {}
        self.knowledge_bases: dict[str, str] = {"kb-1": "社内規程", "kb-2": "製品 FAQ"}

    async def create_business_view(
        self,
        *,
        name: str,
        description: str | None = None,
        config: BusinessViewConfig | None = None,
    ) -> BusinessViewDetail:
        resolved = config or BusinessViewConfig()
        detail = BusinessViewDetail(
            id=f"bv-{uuid4().hex[:8]}",
            name=name,
            description=description,
            status=BusinessViewStatus.ACTIVE,
            knowledge_base_count=len(resolved.normalized_knowledge_base_ids()),
            config=resolved,
            knowledge_bases=self._refs(resolved),
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
            updated_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        self.views[detail.id] = detail
        return detail

    async def list_business_views(
        self,
        *,
        status: BusinessViewStatus | None = None,
        query: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[BusinessViewDetail]:
        items = [
            view
            for view in self.views.values()
            if (status is None or view.status == status)
            and (query is None or query.casefold() in view.name.casefold())
        ]
        return items[offset : (offset + limit) if limit is not None else None]

    async def count_business_views(
        self,
        *,
        status: BusinessViewStatus | None = None,
        query: str | None = None,
    ) -> int:
        return len(await self.list_business_views(status=status, query=query))

    async def get_business_view(self, business_view_id: str) -> BusinessViewDetail | None:
        return self.views.get(business_view_id)

    async def update_business_view(
        self,
        business_view_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        config: BusinessViewConfig | None = None,
        update_fields: set[str] | None = None,
    ) -> BusinessViewDetail:
        existing = self.views.get(business_view_id)
        if existing is None:
            raise KeyError(business_view_id)
        fields = update_fields or set()
        updates: dict[str, object] = {}
        if "name" in fields and name is not None:
            updates["name"] = name
        if "description" in fields:
            updates["description"] = description
        if "config" in fields and config is not None:
            updates["config"] = config
            updates["knowledge_base_count"] = len(config.normalized_knowledge_base_ids())
            updates["knowledge_bases"] = self._refs(config)
        updated = existing.model_copy(update=updates)
        self.views[business_view_id] = updated
        return updated

    async def archive_business_view(self, business_view_id: str) -> BusinessViewDetail:
        existing = self.views.get(business_view_id)
        if existing is None:
            raise KeyError(business_view_id)
        archived = existing.model_copy(update={"status": BusinessViewStatus.ARCHIVED})
        self.views[business_view_id] = archived
        return archived

    def _refs(self, config: BusinessViewConfig) -> list[KnowledgeBaseRef]:
        return [
            KnowledgeBaseRef(id=kb_id, name=self.knowledge_bases[kb_id])
            for kb_id in config.normalized_knowledge_base_ids()
            if kb_id in self.knowledge_bases
        ]


@pytest.fixture
def fake_oracle(monkeypatch: pytest.MonkeyPatch) -> FakeBusinessViewOracle:
    """business view router の OracleClient を fake へ差し替える。"""
    fake = FakeBusinessViewOracle()
    monkeypatch.setattr(business_views_route, "OracleClient", lambda: fake)
    return fake


def test_create_and_get_business_view(fake_oracle: FakeBusinessViewOracle) -> None:
    """複数 KB と query 設定・persona を束ねて作成し、参照 KB 名が解決される。"""
    resp = client.post(
        "/api/business-views",
        json={
            "name": " 経理アシスタント ",
            "description": "経理規程の問い合わせ窓口",
            "config": {
                "knowledge_base_ids": ["kb-1", "kb-2"],
                "query": {"generation_profile": "detailed_cited"},
                "system_prompt": "あなたは経理規程アシスタントです。",
                "default_language": "日本語",
            },
        },
    )

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["name"] == "経理アシスタント"
    assert data["knowledge_base_count"] == 2
    assert data["config"]["query"]["generation_profile"] == "detailed_cited"
    assert [ref["name"] for ref in data["knowledge_bases"]] == ["社内規程", "製品 FAQ"]

    get_resp = client.get(f"/api/business-views/{data['id']}")
    assert get_resp.status_code == 200
    assert get_resp.json()["data"]["id"] == data["id"]


def test_list_business_views(fake_oracle: FakeBusinessViewOracle) -> None:
    """作成した業務ビューを一覧・検索できる。"""
    client.post("/api/business-views", json={"name": "経理アシスタント"})
    client.post("/api/business-views", json={"name": "営業アシスタント"})

    page = client.get("/api/business-views?q=経理").json()["data"]
    assert page["total"] == 1
    assert page["items"][0]["name"] == "経理アシスタント"


def test_update_and_archive_business_view(fake_oracle: FakeBusinessViewOracle) -> None:
    """業務ビューの更新とアーカイブができる。"""
    detail = client.post("/api/business-views", json={"name": "FAQ ビュー"}).json()["data"]

    update_resp = client.patch(
        f"/api/business-views/{detail['id']}",
        json={"config": {"knowledge_base_ids": ["kb-1"]}},
    )
    assert update_resp.status_code == 200
    assert update_resp.json()["data"]["knowledge_base_count"] == 1

    archive_resp = client.post(f"/api/business-views/{detail['id']}/archive")
    assert archive_resp.status_code == 200
    assert archive_resp.json()["data"]["status"] == "ARCHIVED"


def test_get_missing_business_view_returns_404(fake_oracle: FakeBusinessViewOracle) -> None:
    """存在しない ID は 404 を返す。"""
    resp = client.get("/api/business-views/does-not-exist")
    assert resp.status_code == 404


def test_detail_config_roundtrips_through_schema() -> None:
    """detail の config は BusinessViewConfig として解釈できる。"""
    config = BusinessViewConfig(knowledge_base_ids=["kb-1"], system_prompt="x")
    restored = parse_business_view_config(config.model_dump(mode="json"))
    assert restored.system_prompt == "x"
