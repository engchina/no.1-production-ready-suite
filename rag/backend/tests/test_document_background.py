"""最終選択後の背景取得の範囲・版・再利用を合成データで確認する。"""

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.rag.document_background import selected_document_backgrounds
from app.schemas.search import PAGE_RANGES_FILTER_KEY, RetrievedChunk


def _chunk(document: str = "doc-a", version: str = "version-a", page: int = 1) -> RetrievedChunk:
    return RetrievedChunk(
        document_id=document,
        chunk_id=f"{document}-{version}-{page}",
        text="合成の本文",
        score=0.0,
        metadata={"chunk_set_id": version, "page_start": page, "page_end": page},
    )


def _span(document: str = "doc-a", version: str = "version-a", **extra: Any) -> dict[str, Any]:
    return {"document_scope": [document, "", "run", "native", version, document], **extra}


def _state(*chunks: RetrievedChunk) -> SimpleNamespace:
    return SimpleNamespace(
        chunks={chunk.chunk_id: chunk for chunk in chunks}, document_backgrounds={}
    )


def _oracle(
    first: RetrievedChunk | None = None, page: dict[str, Any] | None = None
) -> SimpleNamespace:
    return SimpleNamespace(
        retrieval_document_first_chunk=AsyncMock(return_value=first),
        chunk_set_first_page_contexts=AsyncMock(return_value={"version-a": page} if page else {}),
    )


async def test_background_reads_selected_versions_and_reuses_identical_scope() -> None:
    first = _chunk()
    selected = _chunk(page=3)
    state = _state(selected)
    page = {"status": "available", "text": "合成の先頭ページ", "truncated": False}
    oracle = _oracle(first, page)
    filters = {"category_name": "category-a"}
    result = await selected_document_backgrounds(oracle, state, filters, [_span()])
    assert result[0]["first_chunk_status"] == "available"
    assert result[0]["first_page_status"] == "available"
    assert state.chunks[first.chunk_id] is first
    oracle.retrieval_document_first_chunk.assert_awaited_once_with(
        filters, document_id="doc-a", chunk_set_id="version-a"
    )
    oracle.chunk_set_first_page_contexts.assert_awaited_once_with(["version-a"], filters=filters)
    again = await selected_document_backgrounds(oracle, state, filters, [_span()])
    assert again[0]["reused"] is True
    assert oracle.retrieval_document_first_chunk.await_count == 1
    assert (
        await selected_document_backgrounds(oracle, state, filters, [_span(version="other")]) == []
    )


@pytest.mark.parametrize("first", [_chunk(document="other"), _chunk(version="other")])
async def test_wrong_document_or_version_is_never_admitted(first: RetrievedChunk) -> None:
    selected = _chunk(page=3)
    state = _state(selected)
    result = await selected_document_backgrounds(_oracle(first), state, {}, [_span()])
    assert result[0]["first_chunk_status"] == "version_mismatch"
    assert list(state.chunks) == [selected.chunk_id]


async def test_page_scope_excludes_literal_first_without_substituting_later_chunk() -> None:
    import json

    scope = json.dumps([{"document_id": "doc-a", "page_start": 2, "page_end": 4}])
    oracle = _oracle(_chunk())
    state = _state(_chunk(page=3))
    result = await selected_document_backgrounds(oracle, state, {}, [_span(page_scope=scope)])
    assert result[0]["first_chunk"] is None
    assert result[0]["first_page_status"] == "outside_page_scope"
    oracle.chunk_set_first_page_contexts.assert_not_awaited()
    assert oracle.retrieval_document_first_chunk.await_args.args[0][PAGE_RANGES_FILTER_KEY] == scope


@pytest.mark.parametrize(
    "filters,status",
    [
        ({"page_number_min": "2"}, "outside_page_scope"),
        ({"section_title": "section-a"}, "content_scope_not_verifiable"),
    ],
)
async def test_full_page_is_not_read_when_its_scope_cannot_be_authorized(
    filters: dict[str, str], status: str
) -> None:
    oracle = _oracle()
    result = await selected_document_backgrounds(oracle, _state(_chunk(page=3)), filters, [_span()])
    assert result[0]["first_page_status"] == status
    oracle.chunk_set_first_page_contexts.assert_not_awaited()


@pytest.mark.parametrize(
    "page,status",
    [
        ({"status": "available", "text": "prefix", "truncated": True}, "legacy_incomplete"),
        ({"status": "available", "text": "old"}, "completeness_not_recorded"),
    ],
)
async def test_old_prefix_is_not_described_as_complete(page: dict[str, Any], status: str) -> None:
    result = await selected_document_backgrounds(
        _oracle(page=page), _state(_chunk()), {}, [_span()]
    )
    assert result[0]["first_page_status"] == status


async def test_legacy_page_recovery_uses_exact_immutable_revision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.clients.oracle import OracleClient
    from app.rag.document_background import first_page_for_index
    from app.schemas.extraction import DocumentElement, ExtractionPage, StructuredExtraction

    full = "合成ページ" * 2200
    extraction = StructuredExtraction(
        raw_text=full,
        pages=[ExtractionPage(page_number=1, width=100, height=200)],
        elements=[DocumentElement(element_id="unit-a", text=full, page_number=1)],
    )
    client = OracleClient()
    fetch = AsyncMock(
        side_effect=[
            [
                {
                    "chunk_set_id": "version-a",
                    "first_page_context": {
                        "status": "available",
                        "text": "prefix",
                        "truncated": True,
                    },
                }
            ],
            [{"chunk_set_id": "version-a", "extraction_json": extraction.model_dump(mode="json")}],
        ]
    )
    monkeypatch.setattr(client, "_fetch_all", fetch)
    result = await client.chunk_set_first_page_contexts(
        ["version-a"], filters={"category_name": "category-a"}
    )
    assert result["version-a"]["text"] == full
    assert result["version-a"]["truncated"] is False
    assert result["version-a"]["reconstructed_from"] == "immutable_extraction_revision"
    from rag_engine.knowledge.document_metadata import normalize_first_page_context

    assert normalize_first_page_context(result["version-a"]) == result["version-a"]
    assert fetch.await_args is not None
    sql, binds = fetch.await_args.args
    assert "revision.extraction_recipe_id = cs.extraction_recipe_id" in sql
    assert "revision.recipe_id = cs.recipe_id" in sql
    assert "rag_document_extractions " not in sql
    assert not any(key.startswith("first_page_chunk_set") for key in binds)
    assert first_page_for_index(StructuredExtraction(raw_text=full))["status"] != "available"
