"""Wiki の参照範囲・順位融合・原文の引き継ぎを合成の索引で確認する。"""

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.clients.oci_genai import OciGenAiClient
from app.clients.wiki_graph_store import WikiGraphStore
from app.clients.wiki_store import WikiStore
from app.config import Settings
from app.rag.request_context import (
    AuditRequestContext,
    reset_audit_request_context,
    set_audit_request_context,
)
from app.rag.search_answer_profile_config import SearchAnswerProfileConfig
from app.rag.wiki_explorer import Method, WikiExplorer, WikiHandoff
from app.schemas.knowledge_base import KnowledgeBaseRef
from app.schemas.search import RetrievedChunk
from app.schemas.wiki_graph import WikiSourceRef
from app.security.permissions import MENU_WIKI_SEARCH, ROUTE_PERMISSIONS
from tests.test_wiki_graph_api import PAGES, U


def kb(id: str) -> KnowledgeBaseRef:
    return KnowledgeBaseRef(id=id, name=id)


async def test_profile_access_and_manual_search_ignore_automatic_switches() -> None:
    cfg = SearchAnswerProfileConfig(knowledge_base_ids=["a", "b", "c"])
    profile = SimpleNamespace(
        status="ACTIVE",
        config=cfg,
        knowledge_bases=[
            SimpleNamespace(id=id, name=id, status=status)
            for id, status in [("a", "ACTIVE"), ("b", "ACTIVE"), ("c", "ARCHIVED")]
        ],
    )
    oracle: Any = SimpleNamespace(get_search_answer_profile=AsyncMock(return_value=profile))
    token = set_audit_request_context(
        AuditRequestContext(
            allowed_search_answer_profile_ids=frozenset({"p"}),
            allowed_knowledge_base_ids=frozenset({"a", "c"}),
        )
    )
    try:
        scope = await WikiExplorer.for_profile(oracle, "p")
        assert scope.ids == "a" and not scope.settings.rag_wiki_search_enabled
        for profile_id, kb_id in [("p", "b"), ("p", "c"), ("other", None)]:
            with pytest.raises(HTTPException) as exc:
                await WikiExplorer.for_profile(oracle, profile_id, kb_id)
            assert exc.value.status_code == 404
    finally:
        reset_audit_request_context(token)
    assert ROUTE_PERMISSIONS["GET", "/wiki-search/profiles/{scope_id}/search"] == {MENU_WIKI_SEARCH}


@pytest.mark.parametrize("method", ["hybrid", "vector", "keyword"])
async def test_search_uses_selected_channels_and_scoped_memberships(
    monkeypatch: pytest.MonkeyPatch, method: Method
) -> None:
    pages = [PAGES["a1"], PAGES["b1"]]
    items = [
        {
            "id": p.unit.unit_id,
            "label": p.unit.title,
            "document_id": p.unit.document_id,
            "document_name": p.unit.document_id,
        }
        for p in reversed(pages)
    ]
    keyword = AsyncMock(return_value={"items": items, "total": 2})
    vector = AsyncMock(return_value=pages)
    embed = AsyncMock(return_value=[[0.5]])
    resolve = AsyncMock(
        return_value=SimpleNamespace(
            sources=[1],
            catalog=[{"document_id": p.unit.document_id, "title": p.unit.title} for p in pages],
        )
    )
    for obj, name, mock in [
        (WikiGraphStore, "search", keyword),
        (WikiStore, "resolve", resolve),
        (WikiStore, "pages", vector),
        (OciGenAiClient, "embed", embed),
    ]:
        monkeypatch.setattr(obj, name, mock)
    oracle: Any = SimpleNamespace(
        knowledge_base_ids_by_retrieved_document=AsyncMock(
            return_value={"doc-a": {"a"}, "doc-b": {"b"}}
        )
    )
    explorer = WikiExplorer(oracle, [kb("a"), kb("b")], Settings(rag_rrf_method_ratio=(7, 3)))
    result = await explorer.search("質問", method, 0, 5)
    assert [u.id for u in result.items] == (
        [U["b1"], U["a1"]] if method == "keyword" else [U["a1"], U["b1"]]
    )
    assert [u.knowledge_base_ids[0] for u in result.items] == (
        ["b", "a"] if method == "keyword" else ["a", "b"]
    )
    if method == "keyword":
        embed.assert_not_awaited()
    else:
        embed.assert_awaited_once_with(["質問"], input_type="SEARCH_QUERY")
        resolve.assert_awaited_once_with({"knowledge_base_id": "a,b"}, require_build_enabled=False)
    assert keyword.await_count == int(method != "vector")


async def test_handoff_rechecks_hash_bytes_and_access_after_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = PAGES["a1"]
    ref = WikiSourceRef(unit_id=page.unit.unit_id, source_hash=page.unit.source_hash)
    chunk = RetrievedChunk(
        document_id=page.unit.document_id,
        file_name="合成 A.md",
        chunk_id=page.unit.source_chunk_ids[0],
        text=page.unit.text,
        score=1,
        metadata={},
    )
    rows = AsyncMock(return_value={ref.unit_id: {"page": page, "document_name": "合成 A.md"}})
    originals = AsyncMock(return_value=[chunk])
    monkeypatch.setattr(WikiGraphStore, "pages_by_unit", rows)
    monkeypatch.setattr(WikiStore, "read_sources", originals)
    scope = WikiExplorer(cast(Any, object()), [kb("a")], Settings())
    first = await scope.original(ref.unit_id)
    assert first.chunks[0].text == page.unit.text and first.context != page.unit.text
    assert await scope.selected([ref], {"knowledge_base_id": "a,b"}) == [chunk]
    originals.assert_awaited_with([page], {"knowledge_base_id": "a"}, require_build_enabled=False)
    handoff = WikiHandoff("p", (ref,), (chunk,))
    monkeypatch.setattr(WikiExplorer, "for_profile", AsyncMock(return_value=scope))
    await handoff.recheck(scope.oracle, {"knowledge_base_id": "a"})
    for stale, filters, source_chunks in [
        (ref.model_copy(update={"source_hash": "f" * 64}), {}, [chunk]),
        (ref, {"knowledge_base_id": "b"}, [chunk]),
        (ref, {}, []),
    ]:
        originals.return_value = source_chunks
        with pytest.raises(HTTPException) as exc:
            await scope.selected([stale], filters)
        assert exc.value.status_code == 409
    rows.return_value = {}
    with pytest.raises(HTTPException):
        await handoff.recheck(scope.oracle, {})
