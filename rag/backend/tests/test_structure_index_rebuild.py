"""KB の画面からの文書構造索引の手動の構築(保存済み成果からの補建)を合成データで確認する。"""

from datetime import UTC, datetime
from typing import Any

import pytest

from app.rag.structure_index import rebuild_document_structure_index
from app.schemas.document import DocumentDetail, FileStatus


class SavedOracle:
    def __init__(self) -> None:
        self.memberships = ["a", "b"]
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

    async def knowledge_base_is_visible(self, kb_id: str) -> bool:
        return True

    async def get_document_summary(self, document_id: str) -> DocumentDetail:
        return DocumentDetail(
            id=document_id,
            file_name="synthetic.txt",
            status=FileStatus.INDEXED,
            uploaded_at=datetime.now(UTC),
        )

    async def document_structure_index_knowledge_base_ids(self, document_id: str) -> list[str]:
        return list(self.memberships)

    async def _fetch_all(self, sql: str, binds: Any) -> list[dict[str, Any]]:
        assert "embedding" not in sql
        if "SELECT DISTINCT c.document_id" in sql:
            return [
                {
                    "document_id": "doc",
                    "chunk_set_id": "cs",
                    "file_name": "synthetic.txt",
                    "path": "合成章 > 合成節",
                }
            ]
        assert "reference_source_text" in sql
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
    assert any(e.canonical_name == "合成章 > 合成節" for e in oracle.saved[0].entities)
    oracle.memberships = ["b"]
    with pytest.raises(ValueError, match="membership_changed"):
        await rebuild_document_structure_index(oracle, "a", "doc")
    oracle.memberships = ["a", "b"]
    oracle.rows = []
    with pytest.raises(ValueError, match="no_chunks"):
        await rebuild_document_structure_index(oracle, "a", "doc")


async def test_missing_headings_builds_body_without_fabricating_sections(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.clients.structure_index_store import StructureIndexStore

    oracle = SavedOracle()
    oracle.rows[0].update(section_title=None, section_path_json=None)

    async def publish(store: Any, *args: Any, **kwargs: Any) -> None:
        store.oracle.saved.extend(args[4].values())

    monkeypatch.setattr(StructureIndexStore, "publish", publish)
    result = await rebuild_document_structure_index(oracle, "a", "doc")
    assert result["quality"] == "COMPLETE"
    assert any(entity.entity_type == "body" for entity in oracle.saved[0].entities)


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
    monkeypatch.setattr(
        structure_index_store, "_fetch_one", lambda *args: {"knowledge_base_id": "kb"}
    )
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


async def test_old_job_cannot_publish_after_replacement(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.clients import structure_index_store as module
    from app.rag.structure_index import source_fingerprint

    class Oracle:
        async def _run_transaction(self, operation: Any) -> Any:
            return operation("connection")

    monkeypatch.setattr(module, "_require_active_knowledge_base", lambda *args: None)

    def fetch(connection: Any, sql: str, binds: Any) -> Any:
        if "rag_structure_index_jobs" in sql:
            return {"job_id": "new", "status": "RUNNING"}
        return {"knowledge_base_id": "kb"}

    monkeypatch.setattr(module, "_fetch_one", fetch)
    store: Any = module.StructureIndexStore(Oracle())  # type: ignore[arg-type]  # 合成のtransaction adapter。
    with pytest.raises(ValueError, match="interrupted"):
        await store.publish(
            "kb",
            "doc",
            source_fingerprint(["cs"]),
            ["cs"],
            {},
            "COMPLETE",
            {},
            expected_job_id="old",
        )


async def test_rebuild_revalidates_saved_references_without_embedding_or_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import json

    from app.clients.structure_index_store import StructureIndexStore
    from app.rag.cross_references import ReferenceTarget

    oracle = SavedOracle()
    oracle.rows = [
        {
            "chunk_id": "doc:cs:0",
            "chunk_set_id": "cs",
            "chunk_index": 0,
            "section_path_text": "第1条",
            "content_kind": "text",
            "stored_references": json.dumps(
                [
                    json.dumps(
                        [ReferenceTarget("第2条", "article", "2", None, "第2条").to_dict()],
                        ensure_ascii=False,
                    )
                ],
                ensure_ascii=False,
            ),
        },
        {
            "chunk_id": "doc:cs:1",
            "chunk_set_id": "cs",
            "chunk_index": 1,
            "section_path_text": "第2条",
            "content_kind": "text",
        },
    ]

    async def publish(
        store: Any,
        _kb: str,
        _doc: str,
        _fp: str,
        _source: list[str],
        graphs: dict[str, Any],
        _quality: str,
        _counts: Any,
    ) -> None:
        store.oracle.saved.extend(graphs.values())

    monkeypatch.setattr(StructureIndexStore, "publish", publish)
    await rebuild_document_structure_index(oracle, "a", "doc")
    references = [
        edge for edge in oracle.saved[0].relationships if edge.relationship_type == "references"
    ]
    assert len(references) == 1


async def test_rebuild_reuses_catalog_only_for_the_same_authorization_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dataclasses import replace

    from app.clients.graph_reference_store import GraphReferenceStore
    from app.clients.structure_index_store import StructureIndexStore
    from app.rag.request_context import (
        current_audit_request_context,
        reset_audit_request_context,
        set_audit_request_context,
    )

    calls: list[str] = []

    async def documents(store: Any, kb_id: str) -> tuple[Any, ...]:
        calls.append(kb_id)
        return ()

    async def publish(*args: Any, **kwargs: Any) -> None:
        pass

    monkeypatch.setattr(GraphReferenceStore, "documents", documents)
    monkeypatch.setattr(StructureIndexStore, "publish", publish)
    cache: dict[Any, Any] = {}
    oracle = SavedOracle()
    await rebuild_document_structure_index(oracle, "a", "doc", reference_catalog_cache=cache)
    await rebuild_document_structure_index(oracle, "a", "doc", reference_catalog_cache=cache)
    assert calls == ["a"]
    context = replace(current_audit_request_context(), allowed_document_ids=frozenset({"doc"}))
    token = set_audit_request_context(context)
    try:
        await rebuild_document_structure_index(oracle, "a", "doc", reference_catalog_cache=cache)
    finally:
        reset_audit_request_context(token)
    assert calls == ["a", "a"]
