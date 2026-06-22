"""ナレッジベース API のテスト。"""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.api.routes import documents as documents_route
from app.api.routes import knowledge_bases as knowledge_bases_route
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

    async def create_knowledge_base(
        self,
        *,
        name: str,
        description: str | None = None,
        default_search_mode: SearchMode = SearchMode.HYBRID,
        retrieval_config: dict[str, object] | None = None,
    ) -> KnowledgeBaseDetail:
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
    ) -> list[KnowledgeBaseDetail]:
        items = list(self.knowledge_bases.values())
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
    ) -> int:
        return len(
            await self.list_knowledge_bases(status=status, query=query, limit=None, offset=0)
        )

    async def get_knowledge_base(self, knowledge_base_id: str) -> KnowledgeBaseDetail | None:
        return self.knowledge_bases.get(knowledge_base_id)

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
                "legacy_query_config_ignored": (
                    adapter_config.query != KnowledgeBaseQueryConfig()
                ),
                "updated_at": datetime(2026, 1, 2, tzinfo=UTC),
            }
        )
        self.knowledge_bases[knowledge_base_id] = updated
        return updated

    async def archive_knowledge_base(self, knowledge_base_id: str) -> KnowledgeBaseDetail:
        detail = self.knowledge_bases.get(knowledge_base_id)
        if detail is None:
            raise KeyError(knowledge_base_id)
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


def test_update_and_archive_knowledge_base(fake_oracle: FakeKnowledgeBaseOracle) -> None:
    """ナレッジベースの更新とアーカイブができる。"""
    detail = client.post("/api/knowledge-bases", json={"name": "FAQ"}).json()["data"]

    update_resp = client.patch(
        f"/api/knowledge-bases/{detail['id']}",
        json={"name": "製品 FAQ", "description": None},
    )

    assert update_resp.status_code == 200
    assert update_resp.json()["data"]["name"] == "製品 FAQ"

    archive_resp = client.post(f"/api/knowledge-bases/{detail['id']}/archive")
    assert archive_resp.status_code == 200
    assert archive_resp.json()["data"]["status"] == "ARCHIVED"


def test_assign_and_list_knowledge_base_documents(fake_oracle: FakeKnowledgeBaseOracle) -> None:
    """既存文書をナレッジベースへ追加し、KB 文書一覧で確認できる。"""
    detail = client.post("/api/knowledge-bases", json={"name": "社内規程"}).json()["data"]

    assign_resp = client.post(
        f"/api/knowledge-bases/{detail['id']}/documents",
        json={"document_ids": ["doc-1"]},
    )

    assert assign_resp.status_code == 200
    assert assign_resp.json()["data"]["document_count"] == 1

    docs_resp = client.get(f"/api/knowledge-bases/{detail['id']}/documents")
    assert docs_resp.status_code == 200
    docs_page = docs_resp.json()["data"]
    assert docs_page["total"] == 1
    assert docs_page["items"][0]["id"] == "doc-1"


def test_document_knowledge_base_replace_endpoint(fake_oracle: FakeKnowledgeBaseOracle) -> None:
    """文書側 endpoint から所属ナレッジベースを置換できる。"""
    detail = client.post("/api/knowledge-bases", json={"name": "社内規程"}).json()["data"]

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
    detail = client.post("/api/knowledge-bases", json={"name": "旧規程"}).json()["data"]
    assert client.post(f"/api/knowledge-bases/{detail['id']}/archive").status_code == 200

    resp = client.post(
        f"/api/knowledge-bases/{detail['id']}/documents",
        json={"document_ids": ["doc-1"]},
    )

    assert resp.status_code == 409
    assert resp.json()["error_messages"] == ["アーカイブ済みナレッジベースは変更できません。"]


def test_create_knowledge_base_with_adapter_config(
    fake_oracle: FakeKnowledgeBaseOracle,
) -> None:
    """adapter_config を指定して作成すると構築設定だけが detail に型付きで戻る。"""
    resp = client.post(
        "/api/knowledge-bases",
        json={
            "name": "Markdown FAQ",
            "adapter_config": {
                "ingestion": {"chunking_strategy": "markdown_heading", "chunk_size": 1200},
                "query": {"generation_profile": "detailed_cited"},
            },
        },
    )

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["adapter_config"]["ingestion"]["chunking_strategy"] == "markdown_heading"
    assert data["adapter_config"]["ingestion"]["chunk_size"] == 1200
    assert data["adapter_config"]["query"]["generation_profile"] is None
    assert data["legacy_query_config_ignored"] is False
    # 未指定フィールドはグローバル継承を表す None で戻る。
    assert data["adapter_config"]["ingestion"]["parser_adapter_backend"] is None
    assert data["adapter_config"]["query"]["retrieval_strategy"] is None


def test_patch_knowledge_base_replaces_adapter_config(
    fake_oracle: FakeKnowledgeBaseOracle,
) -> None:
    """adapter_config を PATCH すると置換され、detail へ反映される。"""
    created = client.post("/api/knowledge-bases", json={"name": "Scanned PDF"}).json()["data"]
    assert created["adapter_config"]["ingestion"]["chunking_strategy"] is None

    patch_resp = client.patch(
        f"/api/knowledge-bases/{created['id']}",
        json={
            "adapter_config": {
                "ingestion": {
                    "parser_adapter_backend": "docling",
                    "parser_docling_enabled": True,
                    "chunking_strategy": "page_level",
                }
            }
        },
    )

    assert patch_resp.status_code == 200
    data = patch_resp.json()["data"]
    assert data["adapter_config"]["ingestion"]["parser_adapter_backend"] == "docling"
    assert data["adapter_config"]["ingestion"]["parser_docling_enabled"] is True
    assert data["adapter_config"]["ingestion"]["chunking_strategy"] == "page_level"

    # 取得し直しても保持される。
    get_resp = client.get(f"/api/knowledge-bases/{created['id']}")
    reloaded = get_resp.json()["data"]["adapter_config"]
    assert reloaded["ingestion"]["chunking_strategy"] == "page_level"


def test_knowledge_base_legacy_query_config_is_flagged(
    fake_oracle: FakeKnowledgeBaseOracle,
) -> None:
    """既存 retrieval_config に残る query は読めるが legacy ignored として返る。"""
    created = client.post(
        "/api/knowledge-bases",
        json={
            "name": "Legacy KB",
            "retrieval_config": {"query": {"generation_profile": "detailed_cited"}},
        },
    ).json()["data"]

    assert created["adapter_config"]["query"]["generation_profile"] == "detailed_cited"
    assert created["legacy_query_config_ignored"] is True

    patched = client.patch(
        f"/api/knowledge-bases/{created['id']}",
        json={"adapter_config": {"ingestion": {"chunking_strategy": "page_level"}}},
    ).json()["data"]

    assert patched["adapter_config"]["ingestion"]["chunking_strategy"] == "page_level"
    assert patched["adapter_config"]["query"]["generation_profile"] is None
    assert patched["legacy_query_config_ignored"] is False


def test_create_knowledge_base_rejects_invalid_adapter_config(
    fake_oracle: FakeKnowledgeBaseOracle,
) -> None:
    """allowlist 外の戦略値は 422 で拒否する。"""
    resp = client.post(
        "/api/knowledge-bases",
        json={
            "name": "壊れた設定",
            "adapter_config": {"ingestion": {"chunking_strategy": "does_not_exist"}},
        },
    )

    assert resp.status_code == 422
