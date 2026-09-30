"""ナレッジベース API のテスト。"""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.api.routes import documents as documents_route
from app.api.routes import knowledge_bases as knowledge_bases_route
from app.clients.oracle import KnowledgeBaseNameConflictError
from app.main import app
from app.rag.kb_adapter_config import KnowledgeBaseQueryConfig, parse_adapter_config
from app.schemas.document import DocumentDetail, DocumentSummary, FileStatus
from app.schemas.knowledge_base import (
    KnowledgeBaseDetail,
    KnowledgeBaseRef,
    KnowledgeBaseStatus,
)
from app.schemas.search import SearchMode
from tests.support import AsgiTestClient

client = AsgiTestClient(app)


class FakeKnowledgeBaseOracle:
    """knowledge base API テスト用のインメモリ fake。"""

    def __init__(self) -> None:
        self.knowledge_bases: dict[str, KnowledgeBaseDetail] = {}
        self.documents: dict[str, DocumentDetail] = {
            "doc-1": DocumentDetail(
                id="doc-1",
                file_name="policy.txt",
                status=FileStatus.INDEXED,
                uploaded_at=datetime(2026, 1, 1, tzinfo=UTC),
                indexed_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        }
        self.memberships: set[tuple[str, str]] = set()
        self.subgraphs: dict[str, tuple[list[dict[str, object]], list[dict[str, object]]]] = {}

    async def create_knowledge_base(
        self,
        *,
        name: str,
        description: str | None = None,
        default_search_mode: SearchMode = SearchMode.HYBRID,
        retrieval_config: dict[str, object] | None = None,
    ) -> KnowledgeBaseDetail:
        if any(item.name.casefold() == name.casefold() for item in self.knowledge_bases.values()):
            raise KnowledgeBaseNameConflictError()
        adapter_config = parse_adapter_config(retrieval_config)
        detail = KnowledgeBaseDetail(
            id=f"kb-{uuid4().hex[:8]}",
            name=name,
            description=description,
            status=KnowledgeBaseStatus.ACTIVE,
            default_search_mode=default_search_mode,
            retrieval_config=retrieval_config or {},
            adapter_config=adapter_config,
            legacy_query_config_ignored=adapter_config.query != KnowledgeBaseQueryConfig(),
            document_count=0,
            indexed_document_count=0,
            error_document_count=0,
            searchable_chunk_count=0,
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
            updated_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        self.knowledge_bases[detail.id] = detail
        return detail

    async def list_knowledge_bases(
        self,
        *,
        status: KnowledgeBaseStatus | None = None,
        query: str | None = None,
        limit: int | None = None,
        offset: int = 0,
        knowledge_base_ids: list[str] | None = None,
    ) -> list[KnowledgeBaseDetail]:
        items = list(self.knowledge_bases.values())
        if knowledge_base_ids is not None:
            items = [item for item in items if item.id in knowledge_base_ids]
        if status is not None:
            items = [item for item in items if item.status == status]
        if query:
            normalized = query.casefold()
            items = [
                item
                for item in items
                if normalized in item.name.casefold()
                or (item.description is not None and normalized in item.description.casefold())
            ]
        return items[offset : offset + limit if limit is not None else None]

    async def count_knowledge_bases(
        self,
        *,
        status: KnowledgeBaseStatus | None = None,
        query: str | None = None,
        knowledge_base_ids: list[str] | None = None,
    ) -> int:
        return len(
            await self.list_knowledge_bases(
                status=status,
                query=query,
                limit=None,
                offset=0,
                knowledge_base_ids=knowledge_base_ids,
            )
        )

    async def get_knowledge_base(self, knowledge_base_id: str) -> KnowledgeBaseDetail | None:
        return self.knowledge_bases.get(knowledge_base_id)

    async def fetch_knowledge_base_subgraph(
        self, knowledge_base_id: str, *, limit: int
    ) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
        nodes, edges = self.subgraphs.get(knowledge_base_id, ([], []))
        return nodes[:limit], edges[:limit]

    async def update_knowledge_base(
        self,
        knowledge_base_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        default_search_mode: SearchMode | None = None,
        retrieval_config: dict[str, object] | None = None,
        update_fields: set[str] | None = None,
    ) -> KnowledgeBaseDetail:
        detail = self.knowledge_bases.get(knowledge_base_id)
        if detail is None:
            raise KeyError(knowledge_base_id)
        fields = update_fields or set()
        adapter_config = (
            parse_adapter_config(retrieval_config)
            if "retrieval_config" in fields
            else detail.adapter_config
        )
        updated = detail.model_copy(
            update={
                "name": name if "name" in fields and name is not None else detail.name,
                "description": description if "description" in fields else detail.description,
                "default_search_mode": (
                    default_search_mode
                    if "default_search_mode" in fields and default_search_mode is not None
                    else detail.default_search_mode
                ),
                "retrieval_config": (
                    retrieval_config
                    if "retrieval_config" in fields and retrieval_config is not None
                    else detail.retrieval_config
                ),
                "adapter_config": adapter_config,
                "legacy_query_config_ignored": (adapter_config.query != KnowledgeBaseQueryConfig()),
                "updated_at": datetime(2026, 1, 2, tzinfo=UTC),
            }
        )
        self.knowledge_bases[knowledge_base_id] = updated
        return updated

    async def archive_knowledge_base(self, knowledge_base_id: str) -> KnowledgeBaseDetail:
        detail = self.knowledge_bases.get(knowledge_base_id)
        if detail is None:
            raise KeyError(knowledge_base_id)
        if detail.name.casefold() == "default":
            raise ValueError("DEFAULT ナレッジベースはアーカイブできません。")
        archived = detail.model_copy(
            update={
                "status": KnowledgeBaseStatus.ARCHIVED,
                "updated_at": datetime(2026, 1, 3, tzinfo=UTC),
                "archived_at": datetime(2026, 1, 3, tzinfo=UTC),
            }
        )
        self.knowledge_bases[knowledge_base_id] = archived
        return archived

    async def list_documents(
        self,
        status: FileStatus | None = None,
        query: str | None = None,
        limit: int | None = None,
        offset: int = 0,
        knowledge_base_id: str | None = None,
    ) -> list[DocumentSummary]:
        documents = [
            document
            for document in self.documents.values()
            if knowledge_base_id is None or (knowledge_base_id, document.id) in self.memberships
        ]
        if status is not None:
            documents = [document for document in documents if document.status == status]
        if query:
            documents = [
                document
                for document in documents
                if query.casefold() in document.file_name.casefold()
            ]
        return [
            DocumentSummary.model_validate(document.model_dump())
            for document in documents[offset : offset + limit if limit is not None else None]
        ]

    async def count_documents(
        self,
        status: FileStatus | None = None,
        query: str | None = None,
        knowledge_base_id: str | None = None,
    ) -> int:
        return len(
            await self.list_documents(
                status=status,
                query=query,
                limit=None,
                offset=0,
                knowledge_base_id=knowledge_base_id,
            )
        )

    async def assign_documents_to_knowledge_base(
        self,
        knowledge_base_id: str,
        document_ids: list[str],
    ) -> KnowledgeBaseDetail:
        detail = self.knowledge_bases.get(knowledge_base_id)
        if detail is None:
            raise KeyError(knowledge_base_id)
        if detail.status == KnowledgeBaseStatus.ARCHIVED:
            raise ValueError("アーカイブ済みナレッジベースは変更できません。")
        for document_id in document_ids:
            if document_id not in self.documents:
                raise KeyError(document_id)
            self.memberships.add((knowledge_base_id, document_id))
        assigned = len([doc_id for kb_id, doc_id in self.memberships if kb_id == knowledge_base_id])
        updated = detail.model_copy(
            update={
                "document_count": assigned,
                "indexed_document_count": assigned,
                "searchable_chunk_count": assigned,
            }
        )
        self.knowledge_bases[knowledge_base_id] = updated
        return updated

    async def remove_document_from_knowledge_base(
        self,
        knowledge_base_id: str,
        document_id: str,
    ) -> KnowledgeBaseDetail:
        detail = self.knowledge_bases.get(knowledge_base_id)
        if detail is None or document_id not in self.documents:
            raise KeyError(knowledge_base_id)
        self.memberships.discard((knowledge_base_id, document_id))
        return detail

    async def get_document(self, document_id: str) -> DocumentDetail | None:
        return self.documents.get(document_id)

    async def document_exists(self, document_id: str) -> bool:
        return document_id in self.documents

    async def get_document_summary(self, document_id: str) -> DocumentDetail | None:
        # 実装は JSON 列を読まない DocumentSummary を返す。fake は詳細(その上位型)で代用する。
        return self.documents.get(document_id)

    async def list_document_knowledge_bases(self, document_id: str) -> list[KnowledgeBaseRef]:
        if document_id not in self.documents:
            return []
        return [
            KnowledgeBaseRef(id=kb_id, name=self.knowledge_bases[kb_id].name)
            for kb_id, doc_id in sorted(self.memberships)
            if doc_id == document_id and kb_id in self.knowledge_bases
        ]

    async def replace_document_knowledge_bases(
        self,
        document_id: str,
        knowledge_base_ids: list[str],
    ) -> list[KnowledgeBaseRef]:
        if document_id not in self.documents:
            raise KeyError(document_id)
        for knowledge_base_id in knowledge_base_ids:
            detail = self.knowledge_bases.get(knowledge_base_id)
            if detail is None:
                raise KeyError(knowledge_base_id)
            if detail.status == KnowledgeBaseStatus.ARCHIVED:
                raise ValueError("アーカイブ済みナレッジベースは変更できません。")
        self.memberships = {
            (kb_id, doc_id) for kb_id, doc_id in self.memberships if doc_id != document_id
        }
        for knowledge_base_id in knowledge_base_ids:
            self.memberships.add((knowledge_base_id, document_id))
        return await self.list_document_knowledge_bases(document_id)


@pytest.fixture
def fake_oracle(monkeypatch: pytest.MonkeyPatch) -> FakeKnowledgeBaseOracle:
    """knowledge base / document router の OracleClient を fake へ差し替える。"""
    fake = FakeKnowledgeBaseOracle()
    monkeypatch.setattr(knowledge_bases_route, "OracleClient", lambda: fake)
    monkeypatch.setattr(documents_route, "OracleClient", lambda: fake)
    return fake


def test_create_and_list_knowledge_bases(fake_oracle: FakeKnowledgeBaseOracle) -> None:
    """ナレッジベースを作成し、一覧で確認できる。"""
    create_resp = client.post(
        "/api/knowledge-bases",
        json={
            "name": " 社内規程 ",
            "description": "就業規則",
            "default_search_mode": "hybrid",
            "retrieval_config": {"top_k": 20},
        },
    )

    assert create_resp.status_code == 200
    created = create_resp.json()["data"]
    assert created["name"] == "社内規程"
    assert created["retrieval_config"] == {"top_k": 20}
    assert created["id"] in fake_oracle.knowledge_bases

    list_resp = client.get("/api/knowledge-bases?q=規程")
    assert list_resp.status_code == 200
    page = list_resp.json()["data"]
    assert page["total"] == 1
    assert page["items"][0]["name"] == "社内規程"


@pytest.mark.parametrize("reserved_name", ["DEFAULT", "default", " DEFAULT "])
def test_default_is_a_reserved_knowledge_base_name(
    fake_oracle: FakeKnowledgeBaseOracle,
    reserved_name: str,
) -> None:
    """DEFAULT は大文字小文字・前後空白にかかわらずユーザー名に使えない。"""
    create_resp = client.post(
        "/api/knowledge-bases", json={"name": reserved_name, "description": "説明"}
    )

    assert create_resp.status_code == 422
    assert "DEFAULT は予約名のため使用できません。" in create_resp.json()["error_messages"][0]

    detail = client.post(
        "/api/knowledge-bases", json={"name": "社内規程", "description": "説明"}
    ).json()["data"]
    update_resp = client.patch(
        f"/api/knowledge-bases/{detail['id']}",
        json={"name": reserved_name},
    )

    assert update_resp.status_code == 422


@pytest.mark.parametrize(
    "payload",
    [
        {"name": "社内規程"},
        {"name": "社内規程", "description": ""},
        {"name": "社内規程", "description": " \u3000\t"},
        {"name": "社内規程", "description": None},
        {"name": " ", "description": "就業規則"},
    ],
    ids=["missing", "empty", "blank", "null", "blank-name"],
)
def test_create_knowledge_base_requires_name_and_description(
    fake_oracle: FakeKnowledgeBaseOracle,
    payload: dict[str, object],
) -> None:
    """ナレッジベースの名前と説明は必須。未指定・空・空白だけ・null は 422 で作成しない（#521）。"""
    response = client.post("/api/knowledge-bases", json=payload)

    assert response.status_code == 422
    assert fake_oracle.knowledge_bases == {}


@pytest.mark.parametrize(
    "description",
    ["", "   ", None],
    ids=["empty", "blank", "null"],
)
def test_update_knowledge_base_rejects_empty_description(
    fake_oracle: FakeKnowledgeBaseOracle,
    description: str | None,
) -> None:
    """更新で説明を空・空白だけ・null にはできない（#521）。"""
    detail = client.post(
        "/api/knowledge-bases", json={"name": "社内規程", "description": "就業規則"}
    ).json()["data"]

    response = client.patch(
        f"/api/knowledge-bases/{detail['id']}", json={"description": description}
    )

    assert response.status_code == 422
    assert "説明を入力してください。" in response.json()["error_messages"][0]
    assert fake_oracle.knowledge_bases[detail["id"]].description == "就業規則"


def test_knowledge_base_without_description_still_loads(
    fake_oracle: FakeKnowledgeBaseOracle,
) -> None:
    """説明が必須になる前の説明なしの KB も一覧・詳細で読め、説明を入れて保存できる（#521）。"""
    detail = client.post(
        "/api/knowledge-bases", json={"name": "旧規程", "description": "仮"}
    ).json()["data"]
    fake_oracle.knowledge_bases[detail["id"]] = fake_oracle.knowledge_bases[
        detail["id"]
    ].model_copy(update={"description": None})

    listed = client.get("/api/knowledge-bases").json()["data"]["items"]
    assert next(item for item in listed if item["id"] == detail["id"])["description"] is None
    assert client.get(f"/api/knowledge-bases/{detail['id']}").json()["data"]["description"] is None

    updated = client.patch(
        f"/api/knowledge-bases/{detail['id']}", json={"description": "  旧の就業規則  "}
    )
    assert updated.status_code == 200
    assert updated.json()["data"]["description"] == "旧の就業規則"


def test_knowledge_base_graph_endpoint(fake_oracle: FakeKnowledgeBaseOracle) -> None:
    """関係情報グラフ endpoint が空/データ/404 を返す。"""
    detail = client.post(
        "/api/knowledge-bases", json={"name": "社内規程", "description": "説明"}
    ).json()["data"]
    kb_id = detail["id"]

    empty = client.get(f"/api/knowledge-bases/{kb_id}/graph")
    assert empty.status_code == 200
    assert empty.json()["data"]["status"] == "empty"
    assert empty.json()["data"]["nodes"] == []

    fake_oracle.subgraphs[kb_id] = (
        [
            {"id": "e1", "name": "就業規則", "type": "concept", "confidence": 0.9},
            {"id": "e2", "name": "有給休暇", "type": "concept", "confidence": 0.8},
        ],
        [{"id": "r1", "source": "e1", "target": "e2", "type": "relates_to", "confidence": 0.7}],
    )
    resp = client.get(f"/api/knowledge-bases/{kb_id}/graph")
    data = resp.json()["data"]
    assert data["status"] == "ok"
    assert [node["name"] for node in data["nodes"]] == ["就業規則", "有給休暇"]
    assert data["edges"][0]["source"] == "e1"
    assert data["edges"][0]["target"] == "e2"

    assert client.get("/api/knowledge-bases/does-not-exist/graph").status_code == 404


def test_update_and_archive_knowledge_base(fake_oracle: FakeKnowledgeBaseOracle) -> None:
    """ナレッジベースの更新とアーカイブができる。"""
    detail = client.post(
        "/api/knowledge-bases", json={"name": "FAQ", "description": "説明"}
    ).json()["data"]

    update_resp = client.patch(
        f"/api/knowledge-bases/{detail['id']}",
        json={"name": "製品 FAQ", "description": "製品の問い合わせ"},
    )

    assert update_resp.status_code == 200
    assert update_resp.json()["data"]["name"] == "製品 FAQ"

    archive_resp = client.post(f"/api/knowledge-bases/{detail['id']}/archive")
    assert archive_resp.status_code == 200
    assert archive_resp.json()["data"]["status"] == "ARCHIVED"


def test_default_knowledge_base_cannot_be_archived(
    fake_oracle: FakeKnowledgeBaseOracle,
) -> None:
    """DEFAULT のアーカイブ要求は競合として拒否する。"""
    detail = client.post(
        "/api/knowledge-bases", json={"name": "一時名", "description": "説明"}
    ).json()["data"]
    kb_id = detail["id"]
    fake_oracle.knowledge_bases[kb_id] = fake_oracle.knowledge_bases[kb_id].model_copy(
        update={"name": "DEFAULT"}
    )

    response = client.post(f"/api/knowledge-bases/{kb_id}/archive")

    assert response.status_code == 409
    assert response.json()["error_messages"] == ["DEFAULT ナレッジベースはアーカイブできません。"]


def test_assign_and_list_knowledge_base_documents(fake_oracle: FakeKnowledgeBaseOracle) -> None:
    """既存文書をナレッジベースへ追加し、KB 文書一覧で確認できる。"""
    detail = client.post(
        "/api/knowledge-bases", json={"name": "社内規程", "description": "説明"}
    ).json()["data"]

    assign_resp = client.post(
        f"/api/knowledge-bases/{detail['id']}/documents",
        json={"document_ids": ["doc-1"]},
    )

    assert assign_resp.status_code == 200
    assert assign_resp.json()["data"]["document_count"] == 1

    # KB 詳細の文書一覧は、文書一覧 API を KB で絞り込んで取得する。
    docs_resp = client.get("/api/documents", params={"knowledge_base_id": detail["id"]})
    assert docs_resp.status_code == 200
    docs_page = docs_resp.json()["data"]
    assert docs_page["total"] == 1
    assert docs_page["items"][0]["id"] == "doc-1"


def test_document_knowledge_base_replace_endpoint(fake_oracle: FakeKnowledgeBaseOracle) -> None:
    """文書側 endpoint から所属ナレッジベースを置換できる。"""
    detail = client.post(
        "/api/knowledge-bases", json={"name": "社内規程", "description": "説明"}
    ).json()["data"]

    replace_resp = client.put(
        "/api/documents/doc-1/knowledge-bases",
        json={"knowledge_base_ids": [detail["id"]]},
    )

    assert replace_resp.status_code == 200
    assert replace_resp.json()["data"] == [{"id": detail["id"], "name": "社内規程"}]

    list_resp = client.get("/api/documents/doc-1/knowledge-bases")
    assert list_resp.status_code == 200
    assert list_resp.json()["data"] == [{"id": detail["id"], "name": "社内規程"}]


def test_archived_knowledge_base_rejects_assignment(
    fake_oracle: FakeKnowledgeBaseOracle,
) -> None:
    """アーカイブ済みナレッジベースには文書を追加できない。"""
    detail = client.post(
        "/api/knowledge-bases", json={"name": "旧規程", "description": "説明"}
    ).json()["data"]
    assert client.post(f"/api/knowledge-bases/{detail['id']}/archive").status_code == 200

    resp = client.post(
        f"/api/knowledge-bases/{detail['id']}/documents",
        json={"document_ids": ["doc-1"]},
    )

    assert resp.status_code == 409
    assert resp.json()["error_messages"] == ["アーカイブ済みナレッジベースは変更できません。"]


def test_create_knowledge_base_rejects_adapter_config(
    fake_oracle: FakeKnowledgeBaseOracle,
) -> None:
    """KB は所属だけを持つ。作成で adapter_config を指定すると 422 で拒否し、保存しない(#302)。"""
    resp = client.post(
        "/api/knowledge-bases",
        json={
            "name": "Markdown FAQ",
            "description": "説明",
            "adapter_config": {
                "ingestion": {"chunking_strategy": "markdown_heading", "chunk_size": 1200},
            },
        },
    )

    assert resp.status_code == 422
    assert resp.json()["error_messages"] == [knowledge_bases_route.ADAPTER_CONFIG_REJECTED_MESSAGE]
    assert fake_oracle.knowledge_bases == {}


@pytest.mark.parametrize(
    "adapter_config",
    [
        {"ingestion": {"chunking_strategy": "page_level"}},
        {"ingestion": {"chunking_strategy": "does_not_exist"}},
        {},
        None,
    ],
)
def test_patch_knowledge_base_rejects_adapter_config(
    fake_oracle: FakeKnowledgeBaseOracle,
    adapter_config: object,
) -> None:
    """PATCH で adapter_config を指定すると(null・空でも)422 にし、保存値を変えない(#302)。"""
    created = client.post(
        "/api/knowledge-bases",
        json={
            "name": "Legacy KB",
            "description": "説明",
            "retrieval_config": {"ingestion": {"chunking_strategy": "structure_aware"}},
        },
    ).json()["data"]

    resp = client.patch(
        f"/api/knowledge-bases/{created['id']}",
        json={"description": "更新", "adapter_config": adapter_config},
    )

    assert resp.status_code == 422
    reloaded = client.get(f"/api/knowledge-bases/{created['id']}").json()["data"]
    assert reloaded["description"] == "説明"
    assert reloaded["adapter_config"]["ingestion"]["chunking_strategy"] == "structure_aware"


def test_knowledge_base_legacy_query_config_is_flagged(
    fake_oracle: FakeKnowledgeBaseOracle,
) -> None:
    """既存 retrieval_config に残る query は読めるが legacy ignored として返る。"""
    created = client.post(
        "/api/knowledge-bases",
        json={
            "name": "Legacy KB",
            "description": "説明",
            "retrieval_config": {"query": {"generation_profile": "detailed_cited"}},
        },
    ).json()["data"]

    assert created["adapter_config"]["query"]["generation_profile"] == "detailed_cited"
    assert created["legacy_query_config_ignored"] is True

    # 名前・説明の更新では legacy の保存値を書き換えない(読み取りのみ)。
    patched = client.patch(
        f"/api/knowledge-bases/{created['id']}",
        json={"description": "更新後"},
    ).json()["data"]

    assert patched["description"] == "更新後"
    assert patched["adapter_config"]["query"]["generation_profile"] == "detailed_cited"


def test_list_knowledge_bases_filters_by_ids_including_archived(
    fake_oracle: FakeKnowledgeBaseOracle,
) -> None:
    """ids を指定すると、その ID の KB だけを(status 省略時はアーカイブ済みも)返す(#302)。"""
    first = client.post(
        "/api/knowledge-bases", json={"name": "社内規程", "description": "説明"}
    ).json()["data"]
    second = client.post(
        "/api/knowledge-bases", json={"name": "製品 FAQ", "description": "説明"}
    ).json()["data"]
    client.post("/api/knowledge-bases", json={"name": "設計資料", "description": "説明"})
    assert client.post(f"/api/knowledge-bases/{second['id']}/archive").status_code == 200

    resp = client.get(
        "/api/knowledge-bases",
        params=[("ids", first["id"]), ("ids", second["id"]), ("ids", "kb-missing")],
    )

    assert resp.status_code == 200
    page = resp.json()["data"]
    assert page["total"] == 2
    assert {(item["id"], item["status"]) for item in page["items"]} == {
        (first["id"], "ACTIVE"),
        (second["id"], "ARCHIVED"),
    }

    active_only = client.get(
        "/api/knowledge-bases",
        params=[("ids", first["id"]), ("ids", second["id"]), ("status", "ACTIVE")],
    ).json()["data"]
    assert [item["id"] for item in active_only["items"]] == [first["id"]]


def test_create_knowledge_base_with_duplicate_name_returns_409(
    fake_oracle: FakeKnowledgeBaseOracle,
) -> None:
    """同じ名前の KB があると 500 ではなく 409 と日本語の理由を返す（#282）。"""
    assert (
        client.post(
            "/api/knowledge-bases", json={"name": "社内規程", "description": "説明"}
        ).status_code
        == 200
    )

    resp = client.post("/api/knowledge-bases", json={"name": "社内規程", "description": "説明"})

    assert resp.status_code == 409
    assert resp.json()["error_messages"] == [
        "同じ名前のナレッジベース（アーカイブ済みを含む）がすでにあります。別の名前を指定してください。"
    ]


def test_rename_to_duplicate_name_returns_409(
    fake_oracle: FakeKnowledgeBaseOracle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """改名先の名前が使われているときも 409 にする。"""
    created = client.post(
        "/api/knowledge-bases", json={"name": "設計資料", "description": "説明"}
    ).json()["data"]

    async def conflicting_update(*_args: object, **_kwargs: object) -> KnowledgeBaseDetail:
        raise KnowledgeBaseNameConflictError()

    monkeypatch.setattr(fake_oracle, "update_knowledge_base", conflicting_update)

    resp = client.patch(f"/api/knowledge-bases/{created['id']}", json={"name": "社内規程"})

    assert resp.status_code == 409


def test_mutation_responses_include_refreshed_counts(
    fake_oracle: FakeKnowledgeBaseOracle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """変更系 API の応答は、集計列を 0 で返す Oracle 操作の結果ではなく取り直した詳細を返す。"""
    created = client.post(
        "/api/knowledge-bases", json={"name": "社内規程", "description": "説明"}
    ).json()["data"]
    stored = fake_oracle.knowledge_bases[created["id"]]
    fake_oracle.knowledge_bases[created["id"]] = stored.model_copy(
        update={"document_count": 5, "indexed_document_count": 4, "error_document_count": 1}
    )

    async def zero_count_update(knowledge_base_id: str, **_kwargs: object) -> KnowledgeBaseDetail:
        # Oracle の変更操作は KB の行だけを読むので集計列は 0 になる。
        return fake_oracle.knowledge_bases[knowledge_base_id].model_copy(
            update={
                "description": "更新後",
                "document_count": 0,
                "indexed_document_count": 0,
                "error_document_count": 0,
            }
        )

    monkeypatch.setattr(fake_oracle, "update_knowledge_base", zero_count_update)

    resp = client.patch(f"/api/knowledge-bases/{created['id']}", json={"description": "更新後"})

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["document_count"] == 5
    assert data["indexed_document_count"] == 4
    assert data["error_document_count"] == 1


def test_remove_default_only_membership_returns_409(
    fake_oracle: FakeKnowledgeBaseOracle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DEFAULT にだけ所属する文書を外す操作は 409 と理由を返す。"""
    created = client.post(
        "/api/knowledge-bases", json={"name": "社内規程", "description": "説明"}
    ).json()["data"]

    async def reject_remove(*_args: object, **_kwargs: object) -> KnowledgeBaseDetail:
        raise ValueError("DEFAULT にだけ所属する文書は外せません。")

    monkeypatch.setattr(fake_oracle, "remove_document_from_knowledge_base", reject_remove)

    resp = client.delete(f"/api/knowledge-bases/{created['id']}/documents/doc-1")

    assert resp.status_code == 409
    assert resp.json()["error_messages"] == ["DEFAULT にだけ所属する文書は外せません。"]


def test_effective_adapter_config_ignores_legacy_knowledge_base_overrides(
    fake_oracle: FakeKnowledgeBaseOracle,
) -> None:
    """KB の legacy 構築上書きは取込で使わないため、effective は global 既定だけを返す。"""
    from app.config import get_settings

    created = client.post(
        "/api/knowledge-bases",
        json={
            "name": "Legacy 構築設定",
            "description": "説明",
            # 旧 API で保存された legacy 構築上書き(retrieval_config カラムの保存値)。
            "retrieval_config": {
                "ingestion": {"chunking_strategy": "page_level", "chunk_size": 1200}
            },
        },
    ).json()["data"]

    # 保存値は読み取り互換として返す。
    assert created["adapter_config"]["ingestion"]["chunking_strategy"] == "page_level"
    effective = client.get(f"/api/knowledge-bases/{created['id']}").json()["data"][
        "effective_adapter_config"
    ]
    settings = get_settings()
    assert effective["ingestion"]["chunking_strategy"] == settings.rag_chunking_strategy
    assert effective["ingestion"]["chunk_size"] == settings.rag_chunk_size
