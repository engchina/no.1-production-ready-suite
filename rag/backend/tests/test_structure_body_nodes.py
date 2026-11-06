"""見出しなしの保存済み本文を、推測した章節から分離して表示する合成回帰。"""

from typing import Any

import pytest
from rag_parser_core.language import current_pack

from app.rag.chunking import Chunk
from app.rag.graph_index import _graph_id, build_graph_index, graph_section_node_id
from app.rag.structure_tree import StructureChunkRow, build_structure_tree
from app.schemas.extraction import StructuredExtraction


@pytest.mark.parametrize("paths", [[None, None], ["合成見出し", None], ["本文", None]])
def test_body_tree_has_dedicated_identity_in_saved_order(paths: list[str | None]) -> None:
    tree = build_structure_tree(
        [StructureChunkRow(path) for path in paths], chunk_set_id="set", knowledge_base_id="kb"
    )
    body = _graph_id("body", "set", "kb")
    assert body in tree.nodes
    assert tree.nodes[body].label == current_pack().text("search_labels", "body")
    assert tree.nodes[body].type == "body"
    assert tree.nodes[body].parent_id is None
    assert body != graph_section_node_id("set", "kb", "本文")
    assert tree.locate(body)[-1]["id"] == body
    if paths[0]:
        assert tree.roots == [graph_section_node_id("set", "kb", paths[0]), body]


@pytest.mark.parametrize("kind", [None, "text", "table", "figure"])
def test_graph_links_only_actual_unheaded_chunks_without_changing_positions(
    kind: str | None,
) -> None:
    chunks = [
        Chunk(
            text=f"合成本文{i}",
            index=i,
            start_offset=i * 20,
            end_offset=i * 20 + 10,
            metadata={
                "content_kind": kind,
                **({"section_path": "本文"} if i == 1 else {}),
                "page_number": i + 1,
            },
        )
        for i in range(3)
    ]
    before = [(c.text, c.start_offset, c.end_offset, dict(c.metadata)) for c in chunks]
    graph = build_graph_index(
        document_id="doc",
        knowledge_base_ids=["kb"],
        extraction=StructuredExtraction(),
        chunks=chunks,
        chunk_set_id="set",
        document_name="synthetic.pdf",
    )
    body = _graph_id("body", "set", "kb")
    assert [link.chunk_id for link in graph.entity_chunk_links if link.entity_id == body] == [
        "doc:set:0",
        "doc:set:2",
    ]
    assert next(e for e in graph.entities if e.entity_id == body).entity_type == "body"
    assert any(
        link.entity_id == graph_section_node_id("set", "kb", "本文")
        for link in graph.entity_chunk_links
    )
    assert before == [(c.text, c.start_offset, c.end_offset, dict(c.metadata)) for c in chunks]


def test_empty_is_not_fabricated_body() -> None:
    assert not build_structure_tree([], chunk_set_id="set", knowledge_base_id="kb").nodes


@pytest.mark.parametrize("headed", [False, True])
async def test_saved_body_build_is_complete_and_disabled_is_preserved(
    monkeypatch: pytest.MonkeyPatch, headed: bool
) -> None:
    from app.clients.structure_index_store import StructureIndexStore
    from app.rag.structure_index import rebuild_document_structure_index
    from tests.test_structure_index_settings import SavedOracle

    oracle = SavedOracle()
    if not headed:
        oracle.rows[0].update(section_title=None, section_path_json=None)
    else:
        oracle.rows.append(
            {
                **oracle.rows[0],
                "chunk_id": "doc:cs:1",
                "chunk_index": 1,
                "section_title": None,
                "section_path_json": None,
            }
        )

    async def publish(
        store: Any,
        kb: str,
        doc: str,
        fp: str,
        source: list[str],
        graphs: dict[str, Any],
        quality: str,
        counts: Any,
    ) -> None:
        assert source == ["cs"] and quality == "COMPLETE"
        store.oracle.saved.extend(graphs.values())

    monkeypatch.setattr(StructureIndexStore, "publish", publish)
    result = await rebuild_document_structure_index(oracle, "a", "doc")
    assert result["quality"] == "COMPLETE"
    body = _graph_id("body", "cs", "a")
    assert any(link.entity_id == body for link in oracle.saved[0].entity_chunk_links)
    oracle.enabled = False
    with pytest.raises(ValueError, match="disabled"):
        await rebuild_document_structure_index(oracle, "a", "doc")


async def test_body_chunk_reader_scopes_actual_links_and_keeps_order() -> None:
    from app.clients.structure_graph_store import StructureGraphStore

    calls: list[tuple[str, dict[str, Any]]] = []

    class Oracle:
        async def _fetch_all(self, sql: str, binds: dict[str, Any]) -> list[dict[str, Any]]:
            calls.append((sql, binds))
            if "SELECT d.file_name" in sql:
                return [{"file_name": "synthetic.pdf"}]
            return [
                {"chunk_id": "doc:set:2", "chunk_index": 2, "page_number": 3, "total_count": 26}
            ]

    result = await StructureGraphStore(Oracle()).body_chunks(
        "kb", "doc", "set", offset=25, limit=25
    )
    assert result == {
        "items": [{"chunk_id": "doc:set:2", "chunk_index": 2, "page_number": 3}],
        "total": 26,
        "offset": 25,
    }
    sql, binds = calls[1]
    assert "ORDER BY c.chunk_index,c.chunk_id" in sql
    assert "c.chunk_set_id=e.chunk_set_id" in sql and "c.document_id=:document_id" in sql
    assert "e.knowledge_base_id=:kb" in sql and "l.document_id=:document_id" in sql
    assert binds["body"] == _graph_id("body", "set", "kb")
    assert "cs.is_active=1" in calls[0][0]


async def test_body_chunk_reader_does_not_read_links_for_invisible_or_expired_document() -> None:
    from app.clients.structure_graph_store import StructureGraphStore

    calls: list[str] = []

    class Oracle:
        async def _fetch_all(self, sql: str, binds: dict[str, Any]) -> list[dict[str, Any]]:
            calls.append(sql)
            return []

    with pytest.raises(KeyError):
        await StructureGraphStore(Oracle()).body_chunks("kb", "doc", "set", offset=0, limit=25)
    assert len(calls) == 1


def test_body_api_validates_page_and_uses_visible_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.api.routes import knowledge_bases as routes
    from app.clients.structure_graph_store import StructureGraphStore
    from app.main import app
    from tests.support import AsgiTestClient

    calls: list[tuple[str, str, str, int, int]] = []

    class Oracle:
        async def knowledge_base_is_visible(self, kb: str) -> bool:
            return kb == "kb"

    async def read(
        store: Any, kb: str, document: str, chunk_set: str, *, offset: int, limit: int
    ) -> dict[str, Any]:
        calls.append((kb, document, chunk_set, offset, limit))
        if chunk_set != "set":
            raise KeyError(chunk_set)
        return {
            "items": [{"chunk_id": "doc:set:0", "chunk_index": 0, "page_number": None}]
            if offset == 0
            else [],
            "total": 1,
            "offset": offset,
        }

    monkeypatch.setattr(routes, "OracleClient", Oracle)
    monkeypatch.setattr(StructureGraphStore, "body_chunks", read)
    client = AsgiTestClient(app)
    path = "/api/knowledge-bases/kb/graph/documents/doc/body-chunks"
    assert client.get(path + "?chunk_set_id=set").status_code == 200
    assert calls == [("kb", "doc", "set", 0, 25)]
    beyond = client.get(path + "?chunk_set_id=set&offset=1000001")
    assert beyond.status_code == 200
    assert beyond.json()["data"] == {"items": [], "total": 1, "offset": 1000001}
    assert client.get(path + "?chunk_set_id=set&limit=101").status_code == 422
    assert client.get(path + "?chunk_set_id=set&offset=-1").status_code == 422
    assert client.get(path + "?chunk_set_id=old").status_code == 404
    assert client.get(path.replace("/kb/", "/missing/") + "?chunk_set_id=set").status_code == 404
    assert len(calls) == 3


@pytest.mark.parametrize("total", [0, 27])
@pytest.mark.parametrize("offset", [100, 100001, 1000001])
async def test_body_empty_page_keeps_actual_total_with_same_scope(total: int, offset: int) -> None:
    from app.clients.structure_graph_store import StructureGraphStore

    calls: list[tuple[str, dict[str, Any]]] = []

    class Oracle:
        async def _fetch_all(self, sql: str, binds: dict[str, Any]) -> list[dict[str, Any]]:
            calls.append((sql, binds))
            if "SELECT d.file_name" in sql:
                return [{"file_name": "synthetic.pdf"}]
            if "COUNT(*) AS total" in sql:
                return [{"total": total}]
            return []

    result = await StructureGraphStore(Oracle()).body_chunks(
        "kb", "doc", "set", offset=offset, limit=25
    )
    assert result == {"items": [], "total": total, "offset": offset}
    page, count = calls[1:]
    assert (
        page[0][page[0].index("FROM rag_graph_entity_chunks") :].split("ORDER BY")[0].strip()
        == count[0][count[0].index("FROM rag_graph_entity_chunks") :].strip()
    )
    assert count[1] == {k: v for k, v in page[1].items() if k not in {"skip", "take"}}


@pytest.mark.parametrize(
    "node_type,direction,expected",
    [("body", "incoming", 0), ("body", "outgoing", 1), ("section", "incoming", 1)],
)
async def test_body_reference_identity_does_not_borrow_named_section(
    node_type: str, direction: str, expected: int
) -> None:
    from app.clients.graph_reference_store import GraphReferenceStore

    calls: list[tuple[str, dict[str, Any]]] = []

    class Oracle:
        async def _fetch_all(self, sql: str, binds: dict[str, Any]) -> list[dict[str, Any]]:
            calls.append((sql, binds))
            if "SELECT e.entity_id" in sql:
                return [
                    {
                        "entity_id": "body-node",
                        "canonical_name": "本文",
                        "entity_type": node_type,
                        "chunk_set_id": "set",
                        "document_id": "doc",
                        "file_name": "synthetic.txt",
                    }
                ]
            if binds.get("node_path") == "本文" or binds.get("node_id") == "body-node":
                return [
                    {
                        "reference_id": "ref",
                        "resolution_status": "resolved",
                        "label": "合成参照",
                        "source_document_id": "source",
                        "source_chunk_set_id": "source-set",
                        "source_section_path": None,
                        "source_name": "source.txt",
                        "target_document_id": "doc",
                        "target_chunk_set_id": "set",
                        "target_section_path": "本文",
                        "target_name": "synthetic.txt",
                        "active_target_set": "set",
                    }
                ]
            return []

    result = await GraphReferenceStore(Oracle()).page(
        "kb", "body-node", direction=direction, offset=0, limit=25
    )
    assert len(result["items"]) == expected
    if node_type == "body" and direction == "incoming":
        assert len(calls) == 1
    elif direction == "outgoing":
        assert "nc.chunk_id=r.source_chunk_id" in calls[1][0]


async def test_body_search_is_not_a_short_alias_of_a_named_section() -> None:
    from app.clients.structure_graph_store import StructureGraphStore

    calls: list[str] = []

    class Oracle:
        async def _fetch_all(self, sql: str, binds: dict[str, Any]) -> list[dict[str, Any]]:
            calls.append(sql)
            return []

    await StructureGraphStore(Oracle()).search("kb", "本文", offset=0, limit=25)
    assert "e.entity_type NOT IN ('document','body')" in calls[0]
