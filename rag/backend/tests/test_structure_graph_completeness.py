"""旧総数上限を超えても、構造の生成・保存・ページ取得で欠落しない。"""

from typing import Any, cast

import app.clients.graph_reference_store as reference_module
import app.clients.oracle as oracle_module
from app.clients.structure_graph_store import StructureGraphStore
from app.rag.chunking import Chunk
from app.rag.graph_index import build_graph_index
from app.schemas.extraction import StructuredExtraction


async def test_all_processed_sections_are_written_and_reachable(monkeypatch: Any) -> None:
    count = 2501
    chunks = [
        Chunk(
            text="合成本文",
            index=i,
            start_offset=0,
            end_offset=4,
            metadata={"section_path": f"合成章節{i:05d}"},
        )
        for i in range(count)
    ]
    graph = build_graph_index(
        document_id="synthetic-doc",
        knowledge_base_ids=["synthetic-kb"],
        chunk_set_id="synthetic-cs",
        extraction=StructuredExtraction(),
        chunks=chunks,
        document_name="synthetic.txt",
    )
    written: dict[str, list[dict[str, Any]]] = {}
    batch_sizes: list[int] = []

    def capture(_connection: Any, sql: str, rows: Any, **_kwargs: Any) -> None:
        batch_sizes.append(len(rows))
        written.setdefault(sql.split("INSERT INTO ")[1].split()[0], []).extend(rows)

    monkeypatch.setattr(oracle_module, "_select_document_state", lambda *_: {})
    monkeypatch.setattr(oracle_module, "_fetch_all", lambda *_: [])
    monkeypatch.setattr(oracle_module, "_delete_graph_rows_for_chunk_set", lambda *_, **__: None)
    monkeypatch.setattr(oracle_module, "_executemany", capture)
    monkeypatch.setattr(reference_module, "write_graph_references", lambda *_, **__: None)
    oracle_module._write_document_graph_index(
        cast(Any, object()), "synthetic-doc", graph, chunk_set_id="synthetic-cs"
    )
    assert len(written["rag_graph_entities"]) == count + 1
    assert len(written["rag_graph_relationships"]) == count
    assert len(written["rag_graph_entity_chunks"]) == count
    assert max(batch_sizes) <= 250

    class Oracle:
        async def _fetch_all(self, sql: str, _binds: Any) -> list[dict[str, Any]]:
            if "SELECT d.file_name" in sql:
                return [{"file_name": "synthetic.txt"}]
            if "SELECT c.chunk_index" in sql:
                return [
                    {"chunk_index": c.index, "path": c.metadata["section_path"]} for c in chunks
                ]
            if "SELECT e.entity_id" in sql:
                return [{"entity_id": e.entity_id} for e in graph.entities]
            raise AssertionError(sql)

    store = StructureGraphStore(Oracle())
    received = []
    for offset in range(0, count, 100):
        page = await store.children(
            "synthetic-kb",
            "synthetic-doc",
            "synthetic-cs",
            parent_id=None,
            offset=offset,
            limit=100,
        )
        assert page["total"] == count
        received.extend(page["items"])
    assert len(received) == len({row["id"] for row in received}) == count
    assert received[-1]["name"] == "合成章節02500"
