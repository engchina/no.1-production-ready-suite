"""KBだけの構築設定・scope分離・保存済み成果からの補建を合成データで確認する。"""

from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from app.rag.structure_index import (
    rebuild_document_structure_index,
    structure_index_enabled,
    structure_index_targets,
)
from app.schemas.document import DocumentDetail, FileStatus
from app.schemas.knowledge_base import (
    KnowledgeBaseDetail,
    KnowledgeBaseStatus,
    KnowledgeBaseStructureIndexUpdate,
)


@pytest.mark.parametrize("default", [False, True])
@pytest.mark.parametrize("override", [None, False, True])
def test_override_is_independent_of_default(default: bool, override: bool | None) -> None:
    assert structure_index_enabled(override, default) == (default if override is None else override)
    assert structure_index_enabled("true", default) == default


def test_api_rejects_implicit_enable_and_unknown_fields() -> None:
    for data in ({"enabled": "true"}, {"enabled": 1}, {"enabled": False, "embedding": True}):
        with pytest.raises(ValidationError):
            KnowledgeBaseStructureIndexUpdate.model_validate(data)


async def test_current_memberships_have_independent_preferences() -> None:
    class Oracle:
        async def document_structure_index_preferences(
            self, document_id: str
        ) -> dict[str, bool | None]:
            return {"a": True, "b": False, "c": None}

    oracle = Oracle()
    assert await structure_index_targets(oracle, "doc", default=False) == ["a"]
    assert await structure_index_targets(oracle, "doc", default=True) == ["a", "c"]


class SavedOracle:
    def __init__(self) -> None:
        self.enabled: bool | None = True
        self.rows = [
            {
                "chunk_id": "doc:cs:0",
                "chunk_set_id": "cs",
                "chunk_index": 0,
                "section_title": "合成見出し",
                "section_path_json": '["合成章","合成節"]',
                "content_kind": "text",
                "page_number": 1,
            }
        ]
        self.saved: list[Any] = []

    async def get_knowledge_base(self, kb_id: str) -> KnowledgeBaseDetail:
        return KnowledgeBaseDetail(
            id=kb_id,
            name="合成KB",
            status=KnowledgeBaseStatus.ACTIVE,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )

    async def get_document(self, document_id: str) -> DocumentDetail:
        return DocumentDetail(
            id=document_id,
            file_name="synthetic.txt",
            status=FileStatus.INDEXED,
            uploaded_at=datetime.now(UTC),
        )

    async def document_structure_index_preferences(
        self, document_id: str
    ) -> dict[str, bool | None]:
        return {"a": self.enabled, "b": False}

    async def _fetch_all(self, sql: str, binds: Any) -> list[dict[str, Any]]:
        assert "chunk_text" not in sql and "embedding" not in sql
        assert "is_active = 1" in sql
        return self.rows

    async def replace_document_graph_index(self, doc: str, graph: Any, **kwargs: Any) -> None:
        assert kwargs["knowledge_base_ids"] == ["a"]
        assert all(e.knowledge_base_id == "a" for e in graph.entities)
        self.saved.append(graph)

    async def get_chunk_set(self, chunk_set_id: str) -> dict[str, object]:
        return {"is_active": 1}


async def test_rebuild_reads_only_structure_and_preserves_other_kb() -> None:
    oracle = SavedOracle()
    first = await rebuild_document_structure_index(oracle, "a", "doc")
    second = await rebuild_document_structure_index(oracle, "a", "doc")
    assert first == second and first["entities"] > 1
    assert [e.entity_id for e in oracle.saved[0].entities] == [
        e.entity_id for e in oracle.saved[1].entities
    ]
    assert any(e.canonical_name == "合成章 / 合成節" for e in oracle.saved[0].entities)
    oracle.enabled = False
    with pytest.raises(ValueError, match="無効"):
        await rebuild_document_structure_index(oracle, "a", "doc")
    oracle.enabled = True
    oracle.rows = []
    with pytest.raises(ValueError, match="active"):
        await rebuild_document_structure_index(oracle, "a", "doc")


def test_api_persists_nullable_override_and_rejects_other_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api.routes import knowledge_bases as routes
    from app.config import Settings
    from app.main import app
    from tests.support import AsgiTestClient

    class Oracle:
        def __init__(self) -> None:
            self.config: dict[str, object] = {"unrelated": "preserved"}

        async def get_knowledge_base(self, kb_id: str) -> KnowledgeBaseDetail | None:
            if kb_id != "a":
                return None
            return KnowledgeBaseDetail(
                id=kb_id,
                name="合成KB",
                status=KnowledgeBaseStatus.ACTIVE,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
                retrieval_config=self.config,
            )

        async def set_knowledge_base_structure_index(
            self, kb_id: str, enabled: bool | None
        ) -> None:
            if kb_id != "a":
                raise KeyError(kb_id)
            if enabled is None:
                self.config.pop("structure_index_enabled", None)
            else:
                self.config["structure_index_enabled"] = enabled

    oracle = Oracle()
    monkeypatch.setattr(routes, "OracleClient", lambda: oracle)
    monkeypatch.setattr(routes, "get_settings", lambda: Settings(rag_graph_profile="off"))
    client = AsgiTestClient(app)
    path = "/api/knowledge-bases/a/structure-index"
    for value in (True, False, None):
        response = client.put(path, json={"enabled": value})
        assert response.status_code == 200
        assert response.json()["data"] == {"enabled": value, "global_enabled": False}
        assert client.get(path).json()["data"]["enabled"] == value
        assert oracle.config["unrelated"] == "preserved"
    assert client.put(path, json={"enabled": "true"}).status_code == 422
    assert client.get("/api/knowledge-bases/missing/structure-index").status_code == 404
