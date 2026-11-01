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

    async def get_document_summary(self, document_id: str) -> DocumentDetail:
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


async def test_rebuild_reads_only_structure_and_preserves_other_kb(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.clients.structure_index_store import StructureIndexStore

    async def publish(
        store: Any,
        kb: str,
        doc: str,
        fp: str,
        source: list[str],
        graphs: dict[str, Any],
        quality: str,
        result: Any,
    ) -> None:
        assert kb == "a" and doc == "doc" and source == ["cs"]
        store.oracle.saved.extend(graphs.values())

    monkeypatch.setattr(StructureIndexStore, "publish", publish)
    oracle = SavedOracle()
    first = await rebuild_document_structure_index(oracle, "a", "doc")
    second = await rebuild_document_structure_index(oracle, "a", "doc")
    assert first == second and first["entities"] > 1
    assert [e.entity_id for e in oracle.saved[0].entities] == [
        e.entity_id for e in oracle.saved[1].entities
    ]
    assert any(e.canonical_name == "合成章 / 合成節" for e in oracle.saved[0].entities)
    oracle.enabled = False
    with pytest.raises(ValueError, match="disabled"):
        await rebuild_document_structure_index(oracle, "a", "doc")
    oracle.enabled = True
    oracle.rows = []
    with pytest.raises(ValueError, match="no_chunks"):
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


async def test_missing_structure_is_not_a_success(monkeypatch: pytest.MonkeyPatch) -> None:
    oracle = SavedOracle()
    oracle.rows[0].update(section_title=None, section_path_json=None)
    with pytest.raises(ValueError, match="no_structure"):
        await rebuild_document_structure_index(oracle, "a", "doc")
    assert oracle.saved == []


async def test_changed_source_is_rejected_before_publication() -> None:
    oracle = SavedOracle()
    with pytest.raises(ValueError, match="source_changed"):
        await rebuild_document_structure_index(oracle, "a", "doc", expected_source=["old"])
    assert oracle.saved == []


async def test_publish_checks_source_under_lock_before_any_graph_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.clients import oracle as db
    from app.clients.structure_index_store import StructureIndexStore
    from app.rag.structure_index import source_fingerprint

    writes: list[Any] = []

    class Oracle:
        async def _run_transaction(self, operation: Any) -> Any:
            return operation("connection")

    from app.clients import structure_index_store

    monkeypatch.setattr(structure_index_store, "_require_active_knowledge_base", lambda *args: None)
    monkeypatch.setattr(db, "_select_document_for_update", lambda *args: {"document_id": "doc"})

    def fetch(connection: Any, sql: str, binds: Any) -> Any:
        assert "FOR UPDATE" in sql
        return [{"chunk_set_id": "new"}]

    monkeypatch.setattr(db, "_fetch_all", fetch)
    monkeypatch.setattr(
        db, "_write_document_graph_index", lambda *args, **kwargs: writes.append(args)
    )
    with pytest.raises(ValueError, match="source_changed"):
        store: Any = StructureIndexStore(Oracle())  # type: ignore[arg-type]  # 決定論のtransaction adapter。
        await store.publish(
            "kb", "doc", source_fingerprint(["old"]), ["old"], {"old": object()}, "COMPLETE", {}
        )
    assert writes == []


async def test_saved_job_does_not_expose_removed_or_inaccessible_documents() -> None:
    from app.clients.structure_index_store import StructureIndexStore

    class Oracle:
        async def _fetch_all(self, sql: str, binds: Any) -> Any:
            assert "rag_document_knowledge_bases" in sql and "knowledge_base_id=:kb" in sql
            return [{"document_id": "visible", "file_name": "current.txt"}]

    store: Any = StructureIndexStore(Oracle())  # type: ignore[arg-type]  # 合成のDB adapter。
    state = {
        "status": "COMPLETED",
        "items": [
            {"document_id": "visible", "name": "old.txt", "status": "SUCCEEDED"},
            {"document_id": "removed", "name": "hidden.txt", "status": "FAILED"},
        ],
    }
    filtered = await store.visible_state("kb", state)
    assert filtered["partial_visibility"] is True
    assert filtered["items"] == [
        {"document_id": "visible", "name": "current.txt", "status": "SUCCEEDED"}
    ]
    assert len(state["items"]) == 2
