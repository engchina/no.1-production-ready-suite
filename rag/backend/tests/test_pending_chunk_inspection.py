"""分割確認は候補と対応抽出を使い、検索の配信版を切り替えない。"""

from typing import Any

import pytest
from fastapi import HTTPException

from app.api.routes import documents as routes
from app.schemas.document import DocumentChunkView
from app.services.recipe_inspection import inspection_chunk_set_id


@pytest.mark.parametrize(
    ("status", "failed_phase", "expected"),
    [
        ("CHUNKED", None, "pending"),
        ("INDEXING", None, "pending"),
        ("ERROR", "INDEX", "pending"),
        ("ERROR", "CHUNK", "active"),
        ("CHUNKING", None, "active"),
        ("REVIEW", None, "active"),
        ("INDEXED", None, "active"),
    ],
)
def test_inspection_version_state_matrix(status: str, failed_phase: str | None, expected: str):
    assert (
        inspection_chunk_set_id(
            dict(
                status=status,
                failed_phase=failed_phase,
                pending_chunk_set_id="pending",
                active_chunk_set_id="active",
            )
        )
        == expected
    )


class Store:
    def __init__(
        self,
        *,
        active: str | None = "active",
        status: str = "CHUNKED",
        failed_phase: str | None = None,
    ):
        self.row = dict(
            status=status,
            failed_phase=failed_phase,
            pending_chunk_set_id="pending",
            active_chunk_set_id=active,
            active_extraction_recipe_id="latest-extraction",
        )
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.metadata: dict[str, Any] | None = dict(
            document_id="doc", recipe_id="recipe", extraction_recipe_id="bound-extraction"
        )
        self.artifact: dict[str, Any] | None = {
            "extraction_json": {"elements": [{"element_id": "new", "text": "新方式の本文"}]}
        }

    async def get_document_recipe(self, *args: Any):
        return self.row

    async def get_chunk_set(self, value: str):
        self.calls.append(("version", {"id": value}))
        return self.metadata

    async def get_document_extraction_artifact(self, **kwargs: Any):
        assert kwargs == {"document_id": "doc", "extraction_recipe_id": "bound-extraction"}
        self.calls.append(("extraction", kwargs))
        return self.artifact

    async def list_chunk_set_chunk_window(self, value: str, **kwargs: Any):
        assert value == "pending"
        assert kwargs["extraction_recipe_id"] == "bound-extraction"
        self.calls.append(("window", kwargs))
        return [
            DocumentChunkView(
                document_id="doc",
                chunk_id="new-chunk",
                chunk_index=0,
                text="新方式の本文",
                metadata={"chunk_strategy": "structure_aware", "page_start": 2},
                element_ids=["new"],
            )
        ], 1

    async def count_chunk_set_inspection_locations(self, value: str, **kwargs: Any):
        assert value == "pending" and kwargs["extraction_recipe_id"] == "bound-extraction"
        return 1, 0

    async def list_chunk_set_chunks(self, value: str):
        assert value == "pending"
        return [
            DocumentChunkView(
                document_id="doc", chunk_id="new-chunk", chunk_index=0, text="全文" * 2000
            )
        ]


@pytest.mark.parametrize("active", [None, "active"])
@pytest.mark.parametrize("scope", ["unscoped", "page", "source", "focus"])
async def test_first_and_repeated_chunked_inspection_uses_pending_and_bound_extraction(
    monkeypatch, active, scope
):
    store = Store(active=active)
    monkeypatch.setattr(routes, "OracleClient", lambda: store)

    async def source(*args: Any, **kwargs: Any):
        assert args[2] == store.artifact["extraction_json"]
        return {"scope_status": "exact", "element_ids": ["new"]}

    monkeypatch.setattr(routes, "_inspector_source_scope", source)
    options = (
        {"page": 2}
        if scope == "page"
        else {"source_offset": 0, "source_revision": "source"}
        if scope == "source"
        else {"chunk_id": "new-chunk"}
        if scope == "focus"
        else {}
    )
    response = await routes.document_recipe_chunk_window("doc", "recipe", **options)
    assert response.data["revision"] == "pending"
    assert response.data["extraction_revision"] == "bound-extraction"
    assert response.data["inspection_mode"] == "pending"
    assert response.data["items"][0].metadata["page_start"] == 2
    assert store.row["active_chunk_set_id"] == active
    assert [entry[0] for entry in store.calls].count("extraction") == 1


async def test_recipe_chunk_list_selects_same_pending_revision(monkeypatch):
    store = Store()
    monkeypatch.setattr(routes, "OracleClient", lambda: store)
    response = await routes.list_document_recipe_chunks("doc", "recipe")
    assert response.data[0].chunk_id == "new-chunk"
    assert store.row["active_chunk_set_id"] == "active"


@pytest.mark.parametrize(
    "missing",
    [
        "pending",
        "metadata",
        "extraction_id",
        "artifact",
        "payload",
        "wrong_document",
        "wrong_recipe",
    ],
)
async def test_missing_pending_binding_never_falls_back_to_active_or_latest(monkeypatch, missing):
    store = Store()
    if missing == "pending":
        store.row["pending_chunk_set_id"] = None
    elif missing == "metadata":
        store.metadata = None
    elif missing == "extraction_id":
        store.metadata["extraction_recipe_id"] = None
    elif missing == "artifact":
        store.artifact = None
    elif missing == "payload":
        store.artifact = {"extraction_json": None}
    elif missing == "wrong_document":
        store.metadata["document_id"] = "other"
    else:
        store.metadata["recipe_id"] = "other"
    monkeypatch.setattr(routes, "OracleClient", lambda: store)
    with pytest.raises(HTTPException) as error:
        await routes.document_recipe_chunk_window("doc", "recipe")
    assert error.value.status_code == 409
    assert all(call[0] != "window" for call in store.calls)
    assert store.row["active_chunk_set_id"] == "active"


@pytest.mark.parametrize(
    ("strategy", "size", "overlap"),
    [
        ("fixed_size", 500, 0),
        ("structure_aware", 500, 0),
        ("fixed_size", 300, 0),
        ("fixed_size", 500, 80),
        ("structure_aware", 800, 80),
    ],
)
async def test_saved_candidates_for_changed_strategy_size_overlap_are_the_inspected_results(
    monkeypatch, strategy, size, overlap
):
    from app.rag.chunking import chunk_extraction_with_strategy
    from app.schemas.extraction import DocumentElement, StructuredExtraction

    extraction = StructuredExtraction(
        elements=[
            DocumentElement(
                element_id=f"e-{page}",
                kind="text",
                text=(f"合成の第{page}ページの本文です。" * 40),
                page_number=page,
            )
            for page in range(1, 4)
        ]
    )
    built = chunk_extraction_with_strategy(
        extraction, strategy=strategy, chunk_size=size, overlap=overlap
    )
    expected = [
        DocumentChunkView(
            document_id="doc",
            chunk_id=f"pending:{chunk.index}",
            chunk_index=chunk.index,
            text=chunk.text,
            metadata=dict(chunk.metadata),
            element_ids=list(chunk.metadata.get("element_ids") or []),
        )
        for chunk in built
    ]

    class GeneratedStore(Store):
        async def list_chunk_set_chunk_window(self, value, **kwargs):
            assert value == "pending"
            assert kwargs["extraction_recipe_id"] == "bound-extraction"
            return expected[kwargs["offset"] : kwargs["offset"] + kwargs["limit"]], len(expected)

        async def count_chunk_set_inspection_locations(self, value, **kwargs):
            return len(expected), 0

    store = GeneratedStore()
    store.row["processing_config"] = {
        "chunking_strategy": strategy,
        "chunk_size": size,
        "chunk_overlap": overlap,
    }
    store.artifact = {"extraction_json": extraction.to_document_payload()}
    monkeypatch.setattr(routes, "OracleClient", lambda: store)
    response = await routes.document_recipe_chunk_window("doc", "recipe")
    assert response.data["revision"] == "pending"
    assert response.data["total"] == len(built)
    assert response.data["items"] == expected[:25]
    assert all(item.metadata["chunk_strategy"] == strategy for item in response.data["items"])
    assert store.row["active_chunk_set_id"] == "active"


async def test_index_failure_keeps_pending_inspection_separate_from_search_active(monkeypatch):
    store = Store(status="ERROR", failed_phase="INDEX")
    monkeypatch.setattr(routes, "OracleClient", lambda: store)
    response = await routes.document_recipe_chunk_window("doc", "recipe", page=2)
    assert response.data["revision"] == "pending"
    assert response.data["inspection_mode"] == "pending"
    assert store.row["active_chunk_set_id"] == "active"


@pytest.mark.parametrize("endpoint", ["window", "chunks"])
async def test_inspection_revision_changed_during_request_is_not_cached_under_an_old_version(
    monkeypatch, endpoint
):
    store = Store()
    monkeypatch.setattr(routes, "OracleClient", lambda: store)
    call = (
        routes.document_recipe_chunk_window
        if endpoint == "window"
        else routes.list_document_recipe_chunks
    )
    with pytest.raises(HTTPException) as error:
        await call("doc", "recipe", expected_revision="old-pending")
    assert error.value.status_code == 409
    assert store.calls == []
