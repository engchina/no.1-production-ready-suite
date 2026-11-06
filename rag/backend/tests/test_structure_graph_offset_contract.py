"""構造グラフの総量とページ件数を分け、後続offsetの正式入口を確認する。"""

from collections.abc import AsyncIterator, Iterator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from app.api.routes import knowledge_bases as routes
from app.clients.graph_reference_store import GraphReferenceStore
from app.clients.structure_graph_store import StructureGraphStore
from app.rag.request_context import (
    AuditRequestContext,
    reset_audit_request_context,
    set_audit_request_context,
)

app = FastAPI()
app.include_router(routes.router, prefix="/api/knowledge-bases")


@pytest.fixture(autouse=True)
def graph_request_scope() -> Iterator[None]:
    token = set_audit_request_context(
        AuditRequestContext(
            request_id="synthetic-offset",
            tenant_id_hash="synthetic-tenant",
            user_id_hash="synthetic-user",
            allowed_document_ids=frozenset({"doc"}),
            allowed_knowledge_base_ids=frozenset({"kb"}),
        )
    )
    try:
        yield
    finally:
        reset_audit_request_context(token)


@pytest.fixture
async def graph_pages(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[tuple[httpx.AsyncClient, list[dict[str, Any]]]]:
    calls: list[dict[str, Any]] = []

    class Oracle:
        async def knowledge_base_is_visible(self, kb: str) -> bool:
            return kb == "kb"

    async def children(
        self: StructureGraphStore,
        kb: str,
        doc: str,
        cs: str,
        *,
        parent_id: str | None,
        offset: int,
        limit: int,
    ) -> dict[str, Any]:
        if (kb, doc, cs) != ("kb", "doc", "set") or parent_id is not None:
            raise KeyError("見えない保存版/親")
        calls.append({"kind": "tree", "offset": offset, "limit": limit})
        return {"items": [], "total": 100002, "offset": offset}

    async def search(
        self: StructureGraphStore, kb: str, query: str, *, offset: int, limit: int
    ) -> dict[str, Any]:
        calls.append({"kind": "search", "offset": offset, "limit": limit, "q": query})
        return {"items": [], "total": 100002, "offset": offset}

    async def references(
        self: GraphReferenceStore,
        kb: str,
        node: str,
        *,
        direction: str,
        offset: int,
        limit: int,
        reference_id: str | None = None,
    ) -> dict[str, Any]:
        if node != "a" * 64:
            raise KeyError("見えない節点")
        calls.append(
            {"kind": "references", "offset": offset, "limit": limit, "direction": direction}
        )
        return {"items": [], "total": 100002, "offset": offset}

    monkeypatch.setattr(routes, "OracleClient", Oracle)
    monkeypatch.setattr(StructureGraphStore, "children", children)
    monkeypatch.setattr(StructureGraphStore, "search", search)
    monkeypatch.setattr(GraphReferenceStore, "page", references)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://synthetic"
    ) as client:
        yield client, calls


PAGES = [
    ("tree", "/api/knowledge-bases/kb/graph/documents/doc/tree?chunk_set_id=set", 1000),
    ("search", "/api/knowledge-bases/kb/graph/search?q=synthetic", 100),
    ("references", "/api/knowledge-bases/kb/graph/references?node_id=" + "a" * 64, 100),
]


@pytest.mark.parametrize(("kind", "path", "page_cap"), PAGES)
@pytest.mark.parametrize("offset", [100001, 1000001])
async def test_late_offset_is_forwarded_without_total_cap(
    graph_pages: tuple[httpx.AsyncClient, list[dict[str, Any]]],
    kind: str,
    path: str,
    page_cap: int,
    offset: int,
) -> None:
    client, calls = graph_pages
    response = await client.get(f"{path}&offset={offset}&limit={page_cap}")
    assert response.status_code == 200
    assert response.json()["data"]["offset"] == offset
    assert calls[-1]["kind"] == kind and calls[-1]["offset"] == offset
    assert calls[-1]["limit"] == page_cap


@pytest.mark.parametrize(("_kind", "path", "page_cap"), PAGES)
@pytest.mark.parametrize("fault", ["negative", "zero_limit", "large_limit"])
async def test_page_bounds_still_reject_without_store_read(
    graph_pages: tuple[httpx.AsyncClient, list[dict[str, Any]]],
    _kind: str,
    path: str,
    page_cap: int,
    fault: str,
) -> None:
    client, calls = graph_pages
    suffix = {
        "negative": "offset=-1",
        "zero_limit": "limit=0",
        "large_limit": f"limit={page_cap + 1}",
    }[fault]
    assert (await client.get(f"{path}&{suffix}")).status_code == 422
    assert not calls


@pytest.mark.parametrize(("_kind", "path", "_page_cap"), PAGES)
async def test_late_page_does_not_bypass_kb_visibility(
    graph_pages: tuple[httpx.AsyncClient, list[dict[str, Any]]],
    _kind: str,
    path: str,
    _page_cap: int,
) -> None:
    client, calls = graph_pages
    assert (
        await client.get(path.replace("/kb/", "/foreign/") + "&offset=100001")
    ).status_code == 404
    assert not calls


@pytest.mark.parametrize("selector", ["chunk_set_id=foreign", "parent=" + "b" * 64])
async def test_tree_late_page_still_rejects_unknown_parent_or_version(
    graph_pages: tuple[httpx.AsyncClient, list[dict[str, Any]]], selector: str
) -> None:
    client, calls = graph_pages
    query = selector if selector.startswith("chunk_set_id") else "chunk_set_id=set&" + selector
    assert (
        await client.get(f"/api/knowledge-bases/kb/graph/documents/doc/tree?{query}&offset=100001")
    ).status_code == 404
    assert not calls


async def test_reference_late_page_still_rejects_invisible_node(
    graph_pages: tuple[httpx.AsyncClient, list[dict[str, Any]]],
) -> None:
    client, calls = graph_pages
    assert (
        await client.get(
            "/api/knowledge-bases/kb/graph/references?node_id=" + "b" * 64 + "&offset=100001"
        )
    ).status_code == 404
    assert not calls


async def test_actual_saved_tree_locate_and_late_page_agree(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.rag.graph_index import graph_section_node_id

    count = 100002
    last_name = f"合成章節{count - 1:06d}"
    last_id = graph_section_node_id("set", "kb", last_name)

    class Oracle:
        async def knowledge_base_is_visible(self, kb: str) -> bool:
            return kb == "kb"

        async def _fetch_all(self, sql: str, binds: dict[str, Any]) -> list[dict[str, Any]]:
            if "SELECT d.file_name" in sql:
                assert "cs.is_active=1" in sql
                return (
                    [{"file_name": "synthetic.txt"}]
                    if (binds["kb"], binds["document_id"], binds["chunk_set"])
                    == ("kb", "doc", "set")
                    else []
                )
            if "SELECT c.chunk_index" in sql:
                return [{"chunk_index": i, "path": f"合成章節{i:06d}"} for i in range(count)]
            if "SELECT e.entity_id" in sql:
                return [{"entity_id": last_id}]
            raise AssertionError("想定外の保存tree読取")

    monkeypatch.setattr(routes, "OracleClient", Oracle)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://synthetic"
    ) as client:
        base = "/api/knowledge-bases/kb/graph/documents/doc/"
        locate = await client.get(base + f"locate?chunk_set_id=set&node={last_id}")
        assert locate.status_code == 200
        assert locate.json()["data"]["path"][-1] == {"id": last_id, "index": count - 1}
        page = await client.get(base + f"tree?chunk_set_id=set&offset={count - 1}&limit=1")
        assert page.status_code == 200
        data = page.json()["data"]
        assert data["total"] == count and data["offset"] == count - 1
        assert len(data["items"]) == 1 and data["items"][0]["id"] == last_id
        assert data["items"][0]["name"] == last_name
