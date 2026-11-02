"""原本範囲と未定位の Chunk を、混ぜずに有界取得する。"""

from collections.abc import Mapping
from typing import Any

import pytest
from fastapi import HTTPException

from app.api.routes import documents as routes
from app.clients.oracle import OracleClient
from app.schemas.document import DocumentChunkView


@pytest.mark.parametrize("status", ["exact", "unrecorded", "version_mismatch", "unresolved"])
async def test_raw_scope_has_independent_unlocated_page(
    monkeypatch: pytest.MonkeyPatch, status: str
) -> None:
    calls: list[dict[str, Any]] = []
    chunk = DocumentChunkView(document_id="doc", chunk_id="chunk", chunk_index=1, text="本文")

    class Store:
        async def get_document_recipe(self, *args: Any) -> dict[str, str]:
            return {"active_chunk_set_id": "set", "active_extraction_recipe_id": "extraction"}

        async def get_document_extraction_artifact(self, **kwargs: Any) -> dict[str, Any]:
            return {"extraction_json": {}}

        async def list_chunk_set_chunk_window(
            self, chunk_set_id: str, **kwargs: Any
        ) -> tuple[list[DocumentChunkView], int]:
            assert chunk_set_id == "set"
            calls.append(kwargs)
            return ([chunk], 30) if kwargs.get("unlocated_only") else ([], 0)

        async def count_chunk_set_inspection_locations(
            self, chunk_set_id: str, **kwargs: Any
        ) -> tuple[int, int]:
            assert chunk_set_id == "set"
            assert kwargs == {
                "extraction_recipe_id": "extraction",
                "location_unavailable": status != "exact",
            }
            return 32, 30

    async def scope(*args: Any, **kwargs: Any) -> dict[str, Any]:
        # 旧記録の有効 ID があっても、原本上の位置は保証できない。
        return {"scope_status": status, "element_ids": ["known"]}

    monkeypatch.setattr(routes, "OracleClient", Store)
    monkeypatch.setattr(routes, "_inspector_source_scope", scope)
    response = await routes.document_recipe_chunk_window(
        "doc", "recipe", source_offset=0, unlocated_offset=25, limit=5, chunk_id="chunk"
    )
    assert response.data is not None
    data = response.data
    assert data["items"] == [] and data["total"] == 0
    assert data["unlocated_items"] == [chunk]
    assert (data["unlocated_total"], data["document_total"], data["unlocated_offset"]) == (
        30,
        32,
        25,
    )
    assert calls[0]["scope_element_ids"] == (["known"] if status == "exact" else [])
    assert calls[1] == {
        "offset": 25,
        "limit": 5,
        "chunk_id": "chunk",
        "unlocated_only": True,
        "location_unavailable": status != "exact",
        "extraction_recipe_id": "extraction",
    }


@pytest.mark.parametrize("page", [None, 2])
async def test_unscoped_and_page_views_do_not_duplicate_unlocated(
    monkeypatch: pytest.MonkeyPatch, page: int | None
) -> None:
    class Store:
        async def get_document_recipe(self, *args: Any) -> dict[str, str]:
            return {"active_chunk_set_id": "set", "active_extraction_recipe_id": "extraction"}

        async def list_chunk_set_chunk_window(
            self, chunk_set_id: str, **kwargs: Any
        ) -> tuple[list[DocumentChunkView], int]:
            assert kwargs["scope_element_ids"] is None
            assert kwargs["page"] == page
            return [], 2

        async def count_chunk_set_inspection_locations(
            self, *args: Any, **kwargs: Any
        ) -> tuple[int, int]:
            return 10, 5

    monkeypatch.setattr(routes, "OracleClient", Store)
    response = await routes.document_recipe_chunk_window(
        "doc", "recipe", page=page, source_offset=0 if page else None
    )
    assert response.data is not None
    assert response.data["scope_status"] == ("page" if page else "unscoped")
    assert response.data["document_total"] == 10
    assert response.data["unlocated_items"] == [] and response.data["unlocated_total"] == 0


async def test_missing_recipe_stops_before_chunk_read(monkeypatch: pytest.MonkeyPatch) -> None:
    class Store:
        async def get_document_recipe(self, *args: Any) -> None:
            return None

    monkeypatch.setattr(routes, "OracleClient", Store)
    with pytest.raises(HTTPException) as error:
        await routes.document_recipe_chunk_window("other-doc", "recipe", source_offset=0)
    assert error.value.status_code == 404


async def test_partition_queries_share_version_permission_and_owner_sql(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    statements: list[str] = []
    captured: list[dict[str, object]] = []
    oracle = OracleClient()

    async def fetch(
        statement: str, binds: Mapping[str, object] | None = None
    ) -> list[dict[str, object]]:
        statements.append(statement)
        captured.append(dict(binds or {}))
        return []

    monkeypatch.setattr(oracle, "_fetch_all", fetch)
    await oracle.list_chunk_set_chunk_window(
        "set",
        extraction_recipe_id="extraction",
        unlocated_only=True,
        location_unavailable=True,
        offset=25,
        limit=25,
        chunk_id="selected",
    )
    assert await oracle.count_chunk_set_inspection_locations(
        "set", extraction_recipe_id="extraction", location_unavailable=True
    ) == (0, 0)
    common = statements[0].split(", scope_ids AS")[0].strip()
    assert statements[1].strip().startswith(common)
    assert "cs.extraction_recipe_id=e.extraction_recipe_id" in common
    assert "rag_documents d" in common
    assert "WHERE c.chunk_set_id=:chunk_set_id" in common
    assert "ORDER BY eo.ordinal FETCH FIRST 1 ROW ONLY" in common
    assert "start_offset" not in common
    assert "c.first_element_id IS NULL" in statements[0]
    assert "OFFSET :offset ROWS FETCH NEXT :limit ROWS ONLY" in statements[0]
    assert captured[0]["unlocated_only"] == 1 and captured[0]["chunk_id"] == "selected"
    assert captured[0]["offset"] == 25 and captured[0]["limit"] == 25
    assert all(binds["location_unavailable"] == 1 for binds in captured)


async def test_counts_and_unlocated_rows_keep_document_permissions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.rag.request_context import (
        AuditRequestContext,
        reset_audit_request_context,
        set_audit_request_context,
    )

    captured: list[tuple[str, dict[str, object]]] = []
    oracle = OracleClient()

    async def fetch(
        statement: str, binds: Mapping[str, object] | None = None
    ) -> list[dict[str, object]]:
        captured.append((statement, dict(binds or {})))
        return []

    monkeypatch.setattr(oracle, "_fetch_all", fetch)
    token = set_audit_request_context(
        AuditRequestContext(
            tenant_id_hash="synthetic-tenant", allowed_document_ids=frozenset({"permitted-doc"})
        )
    )
    try:
        await oracle.list_chunk_set_chunk_window(
            "set", extraction_recipe_id="extraction", unlocated_only=True, chunk_id="selected"
        )
        await oracle.count_chunk_set_inspection_locations("set", extraction_recipe_id="extraction")
    finally:
        reset_audit_request_context(token)
    assert len(captured) == 2
    for sql, binds in captured:
        assert "d.tenant_id_hash = :tenant_id_hash" in sql
        assert "d.document_id IN (:access_document_id_0)" in sql
        assert binds["tenant_id_hash"] == "synthetic-tenant"
        assert binds["access_document_id_0"] == "permitted-doc"


@pytest.mark.asyncio
async def test_left_range_returns_every_chunk_without_a_continuation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """左の表示範囲の Chunk は既定の件数で切らず、全部を返す（#4094）。"""
    chunks = [f"chunk-{index}" for index in range(30)]
    calls: list[dict[str, Any]] = []

    class Oracle:
        async def get_document_recipe(self, document_id: str, recipe_id: str) -> dict[str, str]:
            return {"active_chunk_set_id": "set", "active_extraction_recipe_id": "extraction"}

        async def get_document_extraction_artifact(self, **_: Any) -> dict[str, Any]:
            return {"extraction_json": {}}

        async def list_chunk_set_chunk_window(self, chunk_set: str, **options: Any) -> Any:
            calls.append(options)
            return chunks[options["offset"] : options["offset"] + options["limit"]], len(chunks)

        async def count_chunk_set_inspection_locations(self, *_: Any, **__: Any) -> tuple[int, int]:
            return 30, 0

    async def scope(*_: Any, **__: Any) -> dict[str, Any]:
        return {"scope_status": "exact", "element_ids": ["known"]}

    monkeypatch.setattr(routes, "OracleClient", Oracle)
    monkeypatch.setattr(routes, "_inspector_source_scope", scope)

    response = await routes.document_recipe_chunk_window(
        "doc", "recipe", source_offset=0, offset=25, limit=25
    )

    assert response.data is not None
    assert response.data["items"] == chunks
    assert response.data["complete"] is True
    assert (response.data["offset"], response.data["limit"]) == (0, 30)
    assert [(call["offset"], call["limit"]) for call in calls] == [(0, 25), (0, 30)]
    assert all(call["full_text"] is True for call in calls)
