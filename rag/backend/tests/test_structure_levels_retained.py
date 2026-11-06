"""記録済みの共通階層を保ち、正式readerで仮想親から実節へ到達する。"""

from collections.abc import Iterator
from typing import Any

import pytest

from app.clients.structure_graph_store import StructureGraphStore
from app.rag.graph_index import graph_section_node_id
from app.rag.request_context import (
    AuditRequestContext,
    reset_audit_request_context,
    set_audit_request_context,
)
from app.rag.structure_tree import StructureChunkRow, build_structure_tree


@pytest.fixture(autouse=True)
def synthetic_scope() -> Iterator[None]:
    token = set_audit_request_context(
        AuditRequestContext(
            tenant_id_hash="synthetic-tenant",
            allowed_document_ids=frozenset({"doc-doc"}),
            allowed_knowledge_base_ids=frozenset({"kb-1"}),
        )
    )
    try:
        yield
    finally:
        reset_audit_request_context(token)


def node_id(path: str) -> str:
    return graph_section_node_id("set-doc", "kb-1", path)


@pytest.mark.parametrize(
    "paths",
    [
        ["第1章 > 接続 > 形式"],
        ["第1章 > 接続", "第1章 > 保存"],
        ["題名 > 第1章", "題名 > 第2章"],
    ],
)
def test_recorded_common_levels_are_not_inferred_as_document_titles(paths: list[str]) -> None:
    tree = build_structure_tree(
        [StructureChunkRow(path) for path in paths],
        chunk_set_id="set-doc",
        knowledge_base_id="kb-1",
    )
    root = paths[0].split(" > ")[0]
    assert tree.roots == [node_id(root)]
    for path in paths:
        parts = path.split(" > ")
        expected = [node_id(" > ".join(parts[:i])) for i in range(1, len(parts) + 1)]
        assert [row["id"] for row in tree.locate(node_id(path))] == expected
        for parent, child in zip(expected, expected[1:], strict=False):
            assert tree.nodes[child].parent_id == parent
            assert child in tree.children_of(parent)


class SavedGraph:
    """正式readerのSQLに保存済み行だけを返す。中間階層のentityは作らない。"""

    def __init__(self, count: int = 2) -> None:
        self.rows = [
            {
                "chunk_index": i,
                "path": f"第1章 > 接続 > 形式{i}",
                "path_json": None,
                "title": f"形式{i}",
                "kind": "text",
                "path_overflow": 0,
            }
            for i in range(count)
        ]
        self.entities = {node_id(str(row["path"])) for row in self.rows}
        self.calls: list[str] = []

    async def _fetch_all(self, sql: str, binds: dict[str, Any]) -> list[dict[str, Any]]:
        self.calls.append(sql)
        assert "1=0" not in sql.replace(" ", "")
        if "SELECT c.chunk_index" in sql:
            assert binds == {"chunk_set": "set-doc"}
            return self.rows
        if "SELECT e.entity_id" in sql:
            assert binds["kb"] == "kb-1" and binds["chunk_set"] == "set-doc"
            return [{"entity_id": item} for item in binds.values() if item in self.entities]
        assert "rag_chunk_sets" in sql
        assert "doc-doc" in binds.values() and "set-doc" in binds.values()
        return [{"file_name": "合成文書.pdf"}]


async def test_formal_reader_expands_virtual_ancestors_and_keeps_persisted_leaf_identity() -> None:
    oracle = SavedGraph()
    store = StructureGraphStore(oracle)
    root = await store.children("kb-1", "doc-doc", "set-doc", parent_id=None, offset=0, limit=100)
    assert root["total"] == 1
    assert root["items"][0]["id"] == node_id("第1章")
    assert root["items"][0]["virtual"] is True
    middle = await store.children(
        "kb-1", "doc-doc", "set-doc", parent_id=node_id("第1章"), offset=0, limit=100
    )
    assert middle["items"][0]["id"] == node_id("第1章 > 接続")
    assert middle["items"][0]["virtual"] is True
    leaf = await store.children(
        "kb-1", "doc-doc", "set-doc", parent_id=node_id("第1章 > 接続"), offset=1, limit=1
    )
    assert leaf["total"] == 2 and leaf["offset"] == 1
    assert leaf["items"][0]["id"] == node_id("第1章 > 接続 > 形式1")
    assert leaf["items"][0]["virtual"] is False
    assert leaf["items"][0]["parent_id"] == node_id("第1章 > 接続")
    located = await store.locate("kb-1", "doc-doc", "set-doc", leaf["items"][0]["id"])
    assert located["path"] == [
        {"id": node_id("第1章"), "index": 0},
        {"id": node_id("第1章 > 接続"), "index": 0},
        {"id": leaf["items"][0]["id"], "index": 1},
    ]
    assert all("INSERT " not in sql and "UPDATE " not in sql for sql in oracle.calls)


async def test_formal_reader_keeps_all_late_siblings_and_full_locate_chain() -> None:
    store = StructureGraphStore(SavedGraph(1002))
    page = await store.children(
        "kb-1", "doc-doc", "set-doc", parent_id=node_id("第1章 > 接続"), offset=1000, limit=100
    )
    assert page["total"] == 1002
    assert [row["id"] for row in page["items"]] == [
        node_id(f"第1章 > 接続 > 形式{i}") for i in [1000, 1001]
    ]
    located = await store.locate("kb-1", "doc-doc", "set-doc", page["items"][-1]["id"])
    assert [row["index"] for row in located["path"]] == [0, 0, 1001]


async def test_browser_payload_is_actual_reader_and_leaf_matches_formal_graph_links() -> None:
    import json
    from pathlib import Path

    from app.rag.chunking import Chunk
    from app.rag.graph_index import build_graph_index
    from app.schemas.extraction import StructuredExtraction

    fixture = json.loads(
        (
            Path(__file__).resolve().parents[2]
            / "frontend/e2e/fixtures/structure-levels-reader.json"
        ).read_text()
    )
    oracle = SavedGraph()
    graph = build_graph_index(
        document_id="doc-doc",
        knowledge_base_ids=["kb-1"],
        chunk_set_id="set-doc",
        document_name="合成文書.pdf",
        extraction=StructuredExtraction(),
        chunks=[
            Chunk(
                text="合成の原文",
                index=i,
                start_offset=i * 6,
                end_offset=i * 6 + 5,
                metadata={"section_path": path},
            )
            for i, path in enumerate(fixture["sourcePaths"])
        ],
    )
    persisted = {entity.entity_id for entity in graph.entities}
    assert oracle.entities <= persisted
    assert node_id("第1章") not in persisted
    assert node_id("第1章 > 接続") not in persisted
    for i, path in enumerate(fixture["sourcePaths"]):
        assert any(
            link.entity_id == node_id(path) and link.chunk_id == f"doc-doc:set-doc:{i}"
            for link in graph.entity_chunk_links
        )
    store = StructureGraphStore(oracle)
    for parent, expected in fixture["after"]["trees"].items():
        page = await store.children(
            "kb-1",
            "doc-doc",
            "set-doc",
            parent_id=None if parent.startswith("root:") else parent,
            offset=0,
            limit=100,
        )
        assert page["items"] == expected
    assert (
        await store.locate("kb-1", "doc-doc", "set-doc", fixture["after"]["leafID"])
        == fixture["after"]["locate"]
    )
