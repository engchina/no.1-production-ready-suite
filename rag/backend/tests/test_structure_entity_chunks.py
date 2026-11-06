"""文書構造グラフの節点（章節・本文）に結んだ chunk の一覧（#4544）の合成回帰。

実 Oracle の同じ範囲は ``test_graph_references_oracle.py`` が確かめる。
"""

from dataclasses import replace
from typing import Any

import pytest

from app.clients.structure_graph_store import StructureGraphStore
from app.rag.graph_index import graph_body_node_id, graph_section_node_id
from app.rag.request_context import (
    current_audit_request_context,
    reset_audit_request_context,
    set_audit_request_context,
)

SECTION = graph_section_node_id("set", "kb", "合成章 > 合成節")
BODY = graph_body_node_id("set", "kb")


class _Oracle:
    """呼ばれた SQL を記録し、文書の確認・節点の確認・ページ・件数に合成の行を返す。"""

    def __init__(
        self,
        *,
        visible: bool = True,
        entity: bool = True,
        rows: list[dict[str, Any]] | None = None,
        total: int = 0,
    ) -> None:
        self.visible = visible
        self.entity = entity
        self.rows = rows or []
        self.total = total
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def _fetch_all(self, sql: str, binds: dict[str, Any]) -> list[dict[str, Any]]:
        self.calls.append((sql, binds))
        if "SELECT d.file_name" in sql:
            return [{"file_name": "synthetic.pdf"}] if self.visible else []
        if "FROM rag_graph_entities e WHERE" in sql:
            return [{"entity_id": binds["node"]}] if self.entity else []
        if "COUNT(*) AS total" in sql:
            return [{"total": self.total}]
        return self.rows


@pytest.mark.parametrize("node", [SECTION, BODY], ids=["section", "body"])
async def test_entity_chunks_return_saved_order_page_with_scope_total(node: str) -> None:
    oracle = _Oracle(
        rows=[{"chunk_id": "doc:set:30", "chunk_index": 30, "page_number": 4, "total_count": 26}]
    )
    result = await StructureGraphStore(oracle).entity_chunks(
        "kb", "doc", "set", node_id=node, offset=25, limit=25
    )
    assert result == {
        "items": [{"chunk_id": "doc:set:30", "chunk_index": 30, "page_number": 4}],
        "total": 26,
        "offset": 25,
    }
    document, entity, page = oracle.calls
    assert "cs.is_active=1" in document[0] and "m.knowledge_base_id=:kb" in document[0]
    # 節点は同じ版・同じ KB の実体だけ（文書の節点は対象外）。
    assert "e.chunk_set_id=:chunk_set" in entity[0] and "e.knowledge_base_id=:kb" in entity[0]
    assert "e.entity_type<>'document'" in entity[0]
    assert entity[1] == {"node": node, "chunk_set": "set", "kb": "kb"}
    sql, binds = page
    assert "ORDER BY c.chunk_index,c.chunk_id" in sql
    assert "e.chunk_set_id=:chunk_set AND e.knowledge_base_id=:kb" in sql
    assert "c.chunk_set_id=e.chunk_set_id" in sql and "c.document_id=:document_id" in sql
    assert "l.entity_id=:node AND l.document_id=:document_id" in sql
    assert binds["node"] == node and (binds["skip"], binds["take"]) == (25, 25)


@pytest.mark.parametrize("total", [0, 27])
@pytest.mark.parametrize("offset", [100, 1000001])
async def test_entity_chunks_empty_page_keeps_total_with_same_scope(
    total: int, offset: int
) -> None:
    oracle = _Oracle(total=total)
    result = await StructureGraphStore(oracle).entity_chunks(
        "kb", "doc", "set", node_id=SECTION, offset=offset, limit=25
    )
    assert result == {"items": [], "total": total, "offset": offset}
    page, count = oracle.calls[2:]
    marker = "FROM rag_graph_entity_chunks"
    assert page[0][page[0].index(marker) :].split("ORDER BY")[0].strip() == (
        count[0][count[0].index(marker) :].strip()
    )
    assert count[1] == {k: v for k, v in page[1].items() if k not in {"skip", "take"}}


async def test_virtual_or_foreign_node_is_not_found_without_reading_links() -> None:
    """木だけにある仮想の節点・別の版や KB の ID は、chunk を作らず 404 にする。"""
    oracle = _Oracle(entity=False)
    with pytest.raises(KeyError):
        await StructureGraphStore(oracle).entity_chunks(
            "kb", "doc", "set", node_id=SECTION, offset=0, limit=25
        )
    assert len(oracle.calls) == 2
    assert not any("rag_graph_entity_chunks" in sql for sql, _ in oracle.calls)


async def test_document_outside_scope_stops_at_first_gate_without_counts() -> None:
    """権限の外の文書・別の KB・別の tenant・失効した版は、最初の確認で止めて件数を数えない。"""
    oracle = _Oracle(visible=False, total=99)
    context = replace(current_audit_request_context(), allowed_document_ids=frozenset({"other"}))
    token = set_audit_request_context(context)
    try:
        with pytest.raises(KeyError):
            await StructureGraphStore(oracle).entity_chunks(
                "kb", "doc", "set", node_id=SECTION, offset=0, limit=25
            )
    finally:
        reset_audit_request_context(token)
    [(sql, binds)] = oracle.calls
    assert "d.document_id IN (:access_document_id_0)" in sql
    assert binds["access_document_id_0"] == "other"


def test_entity_chunks_api_validates_and_uses_visible_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api.routes import knowledge_bases as routes
    from app.main import app
    from tests.support import AsgiTestClient

    calls: list[tuple[str, str, str, str, int, int]] = []

    class Oracle:
        async def knowledge_base_is_visible(self, kb: str) -> bool:
            return kb == "kb"

    async def read(
        store: Any,
        kb: str,
        document: str,
        chunk_set: str,
        *,
        node_id: str,
        offset: int,
        limit: int,
    ) -> dict[str, Any]:
        calls.append((kb, document, chunk_set, node_id, offset, limit))
        if chunk_set != "set" or node_id != SECTION:
            raise KeyError(chunk_set)
        return {
            "items": [{"chunk_id": "doc:set:0", "chunk_index": 0, "page_number": None}]
            if offset == 0
            else [],
            "total": 1,
            "offset": offset,
        }

    monkeypatch.setattr(routes, "OracleClient", Oracle)
    monkeypatch.setattr(StructureGraphStore, "entity_chunks", read)
    client = AsgiTestClient(app)
    path = "/api/knowledge-bases/kb/graph/documents/doc/entity-chunks"
    query = f"?chunk_set_id=set&node_id={SECTION}"
    assert client.get(path + query).status_code == 200
    assert calls == [("kb", "doc", "set", SECTION, 0, 25)]
    beyond = client.get(path + query + "&offset=1000001")
    assert beyond.status_code == 200
    assert beyond.json()["data"] == {"items": [], "total": 1, "offset": 1000001}
    assert client.get(path + query + "&limit=101").status_code == 422
    assert client.get(path + query + "&offset=-1").status_code == 422
    assert client.get(path + "?chunk_set_id=set").status_code == 422
    assert client.get(path + "?chunk_set_id=set&node_id=short").status_code == 422
    assert client.get(path + f"?chunk_set_id=old&node_id={SECTION}").status_code == 404
    assert client.get(path + f"?chunk_set_id=set&node_id={BODY}").status_code == 404
    assert client.get(path.replace("/kb/", "/missing/") + query).status_code == 404
    assert len(calls) == 4
    # 本文だけの読み取りは同じ API に統合した（互換の経路は持たない）。
    old = "/api/knowledge-bases/kb/graph/documents/doc/body-chunks?chunk_set_id=set"
    assert client.get(old).status_code == 404
