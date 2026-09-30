"""業務ビュー(Business View)API のテスト。"""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.api.routes import business_views as business_views_route
from app.main import app
from app.rag.business_view_config import BusinessViewConfig, parse_business_view_config
from app.schemas.business_view import (
    DEFAULT_BUSINESS_VIEW_DESCRIPTION,
    DEFAULT_BUSINESS_VIEW_NAME,
    BusinessViewDetail,
    BusinessViewKnowledgeBaseRef,
    BusinessViewStatus,
)
from app.schemas.knowledge_base import KnowledgeBaseStatus
from tests.support import AsgiTestClient

client = AsgiTestClient(app)


class FakeBusinessViewOracle:
    """business view API テスト用のインメモリ fake。"""

    def __init__(self) -> None:
        self.views: dict[str, BusinessViewDetail] = {}
        self.knowledge_bases: dict[str, str] = {
            "kb-default": "DEFAULT",
            "kb-1": "社内規程",
            "kb-2": "製品 FAQ",
        }

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

    async def ensure_default_business_view(self) -> BusinessViewDetail:
        existing = next(
            (
                view
                for view in self.views.values()
                if view.name.casefold() == DEFAULT_BUSINESS_VIEW_NAME.casefold()
            ),
            None,
        )
        if existing is None:
            return await self.create_business_view(
                name=DEFAULT_BUSINESS_VIEW_NAME,
                description=DEFAULT_BUSINESS_VIEW_DESCRIPTION,
                config=BusinessViewConfig(knowledge_base_ids=["kb-default"]),
            )
        config = existing.config.model_copy(update={"knowledge_base_ids": ["kb-default"]})
        normalized = existing.model_copy(
            update={
                "description": existing.description or DEFAULT_BUSINESS_VIEW_DESCRIPTION,
                "status": BusinessViewStatus.ACTIVE,
                "archived_at": None,
                "config": config,
                "knowledge_base_count": 1,
                "knowledge_bases": self._refs(config),
            }
        )
        self.views[existing.id] = normalized
        return normalized

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
        items.sort(key=lambda view: view.name.casefold() != DEFAULT_BUSINESS_VIEW_NAME.casefold())
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
        if existing.name.casefold() == DEFAULT_BUSINESS_VIEW_NAME.casefold():
            if "name" in fields:
                raise ValueError("DEFAULT 業務ビューの名前は変更できません。")
            if (
                "config" in fields
                and config is not None
                and config.knowledge_base_ids != ["kb-default"]
            ):
                raise ValueError("DEFAULT 業務ビューの参照 KB は変更できません。")
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
        if existing.name.casefold() == DEFAULT_BUSINESS_VIEW_NAME.casefold():
            raise ValueError("DEFAULT 業務ビューはアーカイブできません。")
        archived = existing.model_copy(update={"status": BusinessViewStatus.ARCHIVED})
        self.views[business_view_id] = archived
        return archived

    def _refs(self, config: BusinessViewConfig) -> list[BusinessViewKnowledgeBaseRef]:
        return [
            BusinessViewKnowledgeBaseRef(
                id=kb_id, name=self.knowledge_bases[kb_id], status=KnowledgeBaseStatus.ACTIVE
            )
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
    """複数 KB と回答の設定を束ねて作成し、参照 KB 名が解決される。"""
    resp = client.post(
        "/api/business-views",
        json={
            "name": " 経理アシスタント ",
            "description": "経理規程の問い合わせ窓口",
            "config": {
                "knowledge_base_ids": ["kb-1", "kb-2"],
                "query": {
                    "docrag_query_strategy": "rag_fusion",
                    "vector_index_profile": "accurate",
                    # 業務ビューは品質評価を上書きしない。送られても保存しない(#301)。
                    "evaluation_suite": "strict_ci",
                    # 旧 standard の回答スタイル(#595 で削除)。送られても保存しない。
                    "generation_profile": "detailed_cited",
                },
                # 旧 standard の persona(#595 で削除)。送られても保存しない。
                "system_prompt": "あなたは経理規程アシスタントです。",
                "default_language": "日本語",
            },
        },
    )

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["name"] == "経理アシスタント"
    assert data["knowledge_base_count"] == 2
    assert data["config"]["query"]["docrag_query_strategy"] == "rag_fusion"
    assert "vector_index_profile" not in data["config"]["query"]
    assert "evaluation_suite" not in data["config"]["query"]
    assert "generation_profile" not in data["config"]["query"]
    assert "system_prompt" not in data["config"]
    assert "default_language" not in data["config"]
    assert [ref["name"] for ref in data["knowledge_bases"]] == ["社内規程", "製品 FAQ"]

    get_resp = client.get(f"/api/business-views/{data['id']}")
    assert get_resp.status_code == 200
    assert get_resp.json()["data"]["id"] == data["id"]


def test_list_business_views(fake_oracle: FakeBusinessViewOracle) -> None:
    """作成した業務ビューを一覧・検索できる。"""
    client.post(
        "/api/business-views",
        json={"name": "経理アシスタント", "description": "経理アシスタントの説明"},
    )
    client.post(
        "/api/business-views",
        json={"name": "営業アシスタント", "description": "営業アシスタントの説明"},
    )

    page = client.get("/api/business-views?q=経理").json()["data"]
    assert page["total"] == 1
    assert page["items"][0]["name"] == "経理アシスタント"


def test_list_ensures_default_business_view(fake_oracle: FakeBusinessViewOracle) -> None:
    """初回一覧で DEFAULT KB だけを参照する DEFAULT 業務ビューを冪等に保証する。"""
    client.post(
        "/api/business-views",
        json={"name": "経理アシスタント", "description": "経理アシスタントの説明"},
    )
    first = client.get("/api/business-views").json()["data"]
    second = client.get("/api/business-views").json()["data"]

    assert first["total"] == second["total"] == 2
    assert first["items"][0]["name"] == "DEFAULT"
    default = next(view for view in fake_oracle.views.values() if view.name == "DEFAULT")
    assert default.status == BusinessViewStatus.ACTIVE
    assert default.config.knowledge_base_ids == ["kb-default"]
    assert [kb.name for kb in default.knowledge_bases] == ["DEFAULT"]
    # DEFAULT は改名できないため、既定の説明を持つ（#521）。
    assert default.description == DEFAULT_BUSINESS_VIEW_DESCRIPTION


@pytest.mark.parametrize("reserved_name", ["DEFAULT", "default", " DEFAULT "])
def test_default_is_a_reserved_business_view_name(
    fake_oracle: FakeBusinessViewOracle,
    reserved_name: str,
) -> None:
    """DEFAULT は大文字小文字・前後空白にかかわらずユーザー名に使えない。"""
    response = client.post(
        "/api/business-views", json={"name": reserved_name, "description": "予約名の確認"}
    )

    assert response.status_code == 422
    assert "DEFAULT は予約名のため使用できません。" in response.json()["error_messages"][0]


@pytest.mark.parametrize(
    "payload",
    [
        {"name": "経理ビュー"},
        {"name": "経理ビュー", "description": ""},
        {"name": "経理ビュー", "description": " \u3000\n"},
        {"name": "経理ビュー", "description": None},
    ],
    ids=["missing", "empty", "blank", "null"],
)
def test_create_business_view_requires_description(
    fake_oracle: FakeBusinessViewOracle,
    payload: dict[str, object],
) -> None:
    """業務ビューの説明は必須。未指定・空・空白だけ・null は 422 で作成しない（#521）。"""
    response = client.post("/api/business-views", json=payload)

    assert response.status_code == 422
    assert any("description" in message for message in response.json()["error_messages"])
    assert fake_oracle.views == {}


@pytest.mark.parametrize(
    "payload",
    [{"name": ""}, {"name": "   "}],
    ids=["empty", "blank"],
)
def test_create_business_view_requires_name(
    fake_oracle: FakeBusinessViewOracle,
    payload: dict[str, object],
) -> None:
    """業務ビューの名前は必須。空・空白だけは 422 で作成しない。"""
    response = client.post("/api/business-views", json={**payload, "description": "説明"})

    assert response.status_code == 422
    assert fake_oracle.views == {}


def test_create_business_view_trims_description(fake_oracle: FakeBusinessViewOracle) -> None:
    """説明の前後の空白は取り除いて保存する。"""
    response = client.post(
        "/api/business-views", json={"name": "経理ビュー", "description": "  経理の問い合わせ  "}
    )

    assert response.status_code == 200
    assert response.json()["data"]["description"] == "経理の問い合わせ"


@pytest.mark.parametrize(
    "description",
    ["", "   ", None],
    ids=["empty", "blank", "null"],
)
def test_update_business_view_rejects_empty_description(
    fake_oracle: FakeBusinessViewOracle,
    description: str | None,
) -> None:
    """更新で説明を空・空白だけ・null にはできない（#521）。"""
    detail = client.post(
        "/api/business-views", json={"name": "経理ビュー", "description": "経理の問い合わせ"}
    ).json()["data"]

    response = client.patch(
        f"/api/business-views/{detail['id']}", json={"description": description}
    )

    assert response.status_code == 422
    assert "説明を入力してください。" in response.json()["error_messages"][0]
    assert fake_oracle.views[detail["id"]].description == "経理の問い合わせ"


def test_business_view_without_description_still_loads_and_updates_settings(
    fake_oracle: FakeBusinessViewOracle,
) -> None:
    """説明が必須になる前の説明なしの業務ビューも読み込め、説明を送らない更新（設定だけ）もできる。"""
    legacy = BusinessViewDetail(
        id="bv-legacy",
        name="旧ビュー",
        description=None,
        status=BusinessViewStatus.ACTIVE,
        knowledge_base_count=1,
        config=BusinessViewConfig(knowledge_base_ids=["kb-1"]),
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        updated_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    fake_oracle.views[legacy.id] = legacy

    listed = client.get("/api/business-views").json()["data"]["items"]
    assert next(item for item in listed if item["id"] == "bv-legacy")["description"] is None
    assert client.get("/api/business-views/bv-legacy").json()["data"]["description"] is None

    update = client.patch(
        "/api/business-views/bv-legacy",
        json={"config": {"knowledge_base_ids": ["kb-2"]}},
    )
    assert update.status_code == 200
    assert update.json()["data"]["knowledge_base_count"] == 1

    described = client.patch(
        "/api/business-views/bv-legacy", json={"name": "旧ビュー", "description": "旧の用途"}
    )
    assert described.status_code == 200
    assert described.json()["data"]["description"] == "旧の用途"


def test_update_and_archive_business_view(fake_oracle: FakeBusinessViewOracle) -> None:
    """業務ビューの更新とアーカイブができる。"""
    detail = client.post(
        "/api/business-views", json={"name": "FAQ ビュー", "description": "FAQ ビューの説明"}
    ).json()["data"]

    update_resp = client.patch(
        f"/api/business-views/{detail['id']}",
        json={"config": {"knowledge_base_ids": ["kb-1"]}},
    )
    assert update_resp.status_code == 200
    assert update_resp.json()["data"]["knowledge_base_count"] == 1

    archive_resp = client.post(f"/api/business-views/{detail['id']}/archive")
    assert archive_resp.status_code == 200
    assert archive_resp.json()["data"]["status"] == "ARCHIVED"


def test_default_business_view_allows_settings_but_protects_identity_and_scope(
    fake_oracle: FakeBusinessViewOracle,
) -> None:
    """DEFAULT は設定だけ更新でき、名前・参照 KB・アーカイブは変更できない。"""
    default = client.get("/api/business-views").json()["data"]["items"][0]
    business_view_id = default["id"]

    update = client.patch(
        f"/api/business-views/{business_view_id}",
        json={
            "description": "全社共通の検索設定",
            "config": {
                "knowledge_base_ids": ["kb-default"],
                "query": {"docrag_answer_flow": "standard_rag"},
            },
        },
    )
    assert update.status_code == 200
    assert update.json()["data"]["description"] == "全社共通の検索設定"
    assert update.json()["data"]["config"]["knowledge_base_ids"] == ["kb-default"]
    assert update.json()["data"]["config"]["query"]["docrag_answer_flow"] == "standard_rag"

    # 画面の保存と同じ形（名前を送らず説明と設定を送る）で DEFAULT を保存できる（#521）。
    assert default["description"] == DEFAULT_BUSINESS_VIEW_DESCRIPTION
    empty_description = client.patch(
        f"/api/business-views/{business_view_id}",
        json={"description": " ", "config": {"knowledge_base_ids": ["kb-default"]}},
    )
    assert empty_description.status_code == 422

    rename = client.patch(
        f"/api/business-views/{business_view_id}",
        json={"name": "全社ビュー"},
    )
    assert rename.status_code == 409
    assert rename.json()["error_messages"] == ["DEFAULT 業務ビューの名前は変更できません。"]

    replace_scope = client.patch(
        f"/api/business-views/{business_view_id}",
        json={"config": {"knowledge_base_ids": ["kb-1"]}},
    )
    assert replace_scope.status_code == 409
    assert replace_scope.json()["error_messages"] == [
        "DEFAULT 業務ビューの参照 KB は変更できません。"
    ]

    archive = client.post(f"/api/business-views/{business_view_id}/archive")
    assert archive.status_code == 409
    assert archive.json()["error_messages"] == ["DEFAULT 業務ビューはアーカイブできません。"]


def test_get_missing_business_view_returns_404(fake_oracle: FakeBusinessViewOracle) -> None:
    """存在しない ID は 404 を返す。"""
    resp = client.get("/api/business-views/does-not-exist")
    assert resp.status_code == 404


def test_detail_config_roundtrips_through_schema() -> None:
    """detail の config は BusinessViewConfig として解釈できる。"""
    config = BusinessViewConfig(knowledge_base_ids=["kb-1"])
    restored = parse_business_view_config(config.model_dump(mode="json"))
    assert restored.normalized_knowledge_base_ids() == ["kb-1"]
