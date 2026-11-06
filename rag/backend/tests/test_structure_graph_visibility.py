"""図の可視性確認で、集計せず正式KBの同じ範囲を判定する。"""

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

from app.api.routes import knowledge_bases as route
from app.clients.oracle import OracleClient, _oracle_knowledge_base_access_predicate_sql
from app.rag.request_context import (
    AuditRequestContext,
    reset_audit_request_context,
    set_audit_request_context,
)


class VisibilityDb:
    def __init__(
        self, *, stored_id: str = "kb-a", status: str = "ACTIVE", visible: bool = True
    ) -> None:
        self.stored_id = stored_id
        self.status = status
        self.visible = visible
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def matches(self, binds: dict[str, Any]) -> bool:
        return (
            self.visible
            and bool(binds.get("knowledge_base_id"))
            and binds["knowledge_base_id"] == self.stored_id
        )

    async def fetch_all(self, sql: str, binds: dict[str, Any]) -> list[dict[str, Any]]:
        self.calls.append((" ".join(sql.split()), binds))
        if not self.matches(binds):
            return []
        return [
            {
                "knowledge_base_id": self.stored_id,
                "name": "合成KB A",
                "status": self.status,
                "default_search_mode": "hybrid",
                "created_at": datetime(2026, 1, 1, tzinfo=UTC),
                "updated_at": datetime(2026, 1, 1, tzinfo=UTC),
                "document_count": 128,
                "indexed_document_count": 128,
                "error_document_count": 0,
                "searchable_chunk_count": 524288,
            }
        ]

    async def fetch_one(self, sql: str, binds: dict[str, Any]) -> dict[str, Any] | None:
        rows = await self.fetch_all(sql, binds)
        return rows[0] if rows else None

    def client(self, monkeypatch: pytest.MonkeyPatch) -> OracleClient:
        oracle = OracleClient.__new__(OracleClient)
        monkeypatch.setattr(oracle, "_fetch_all", self.fetch_all)
        monkeypatch.setattr(oracle, "_fetch_one", self.fetch_one)
        return oracle


@pytest.mark.parametrize("status", ["ACTIVE", "ARCHIVED"])
@pytest.mark.parametrize(
    "allowed,visible",
    [
        (None, True),
        (frozenset({"kb-a"}), True),
        (frozenset({"other"}), False),
        (frozenset(), False),
    ],
    ids=["all", "allowed", "denied", "empty-scope"],
)
async def test_graph_visibility_avoids_statistics_and_preserves_scope(
    monkeypatch: pytest.MonkeyPatch, status: str, allowed: frozenset[str] | None, visible: bool
) -> None:
    db = VisibilityDb(status=status, visible=visible)
    monkeypatch.setattr(route, "OracleClient", lambda: db.client(monkeypatch))
    monkeypatch.setattr(route, "get_settings", lambda: SimpleNamespace(db_read_timeout_seconds=8))
    calls = []

    async def read(store: Any) -> dict[str, Any]:
        calls.append(store)
        return {"status": "synthetic"}

    token = set_audit_request_context(
        AuditRequestContext(
            request_id="synthetic", tenant_id_hash="tenant-a", allowed_knowledge_base_ids=allowed
        )
    )
    try:
        predicate = _oracle_knowledge_base_access_predicate_sql(alias="kb")
        if visible:
            assert await route._read_structure_graph("kb-a", "synthetic-graph", read) == {
                "status": "synthetic"
            }
        else:
            with pytest.raises(HTTPException) as error:
                await route._read_structure_graph("kb-a", "synthetic-graph", read)
            assert error.value.status_code == 404
        assert len(calls) == int(visible)
        assert len(db.calls) == 1
        sql, binds = db.calls[0]
        assert "rag_chunks" not in sql
        assert "rag_document_knowledge_bases" not in sql
        assert "COUNT(" not in sql
        assert "GROUP BY" not in sql
        assert predicate in sql
        assert "kb.status" not in sql
        assert binds["knowledge_base_id"] == "kb-a"
        assert binds["tenant_id_hash"] == "tenant-a"
    finally:
        reset_audit_request_context(token)


@pytest.mark.parametrize(
    "requested,stored,expected",
    [
        ("missing", "kb-a", False),
        ("", "kb-a", False),
        ("   ", "kb-a", False),
        (" kb-a ", "kb-a", False),
        (" opaque ", " opaque ", True),
    ],
)
async def test_graph_visibility_matches_opaque_id_exactly(
    monkeypatch: pytest.MonkeyPatch, requested: str, stored: str, expected: bool
) -> None:
    db = VisibilityDb(stored_id=stored)
    monkeypatch.setattr(route, "OracleClient", lambda: db.client(monkeypatch))
    monkeypatch.setattr(route, "get_settings", lambda: SimpleNamespace(db_read_timeout_seconds=8))

    async def read(_store: Any) -> dict[str, Any]:
        assert expected
        return {"ok": True}

    if expected:
        assert await route._read_structure_graph(requested, "synthetic-graph", read) == {"ok": True}
    else:
        with pytest.raises(HTTPException) as error:
            await route._read_structure_graph(requested, "synthetic-graph", read)
        assert error.value.status_code == 404
    assert db.calls[0][1]["knowledge_base_id"] == requested
    assert "rag_chunks" not in db.calls[0][0]


@pytest.mark.parametrize(
    "failure,error_name",
    [(TimeoutError, "DatabaseReadTimeoutError"), (RuntimeError, "DatabaseReadFailedError")],
)
async def test_visibility_read_failure_does_not_become_empty_graph(
    monkeypatch: pytest.MonkeyPatch, failure: type[Exception], error_name: str
) -> None:
    from app.api import errors

    db = VisibilityDb()

    async def fail(_sql: str, _binds: dict[str, Any]) -> dict[str, Any] | None:
        raise failure("synthetic read failure")

    monkeypatch.setattr(db, "fetch_one", fail)
    monkeypatch.setattr(route, "OracleClient", lambda: db.client(monkeypatch))
    monkeypatch.setattr(route, "get_settings", lambda: SimpleNamespace(db_read_timeout_seconds=8))

    async def read(_store: Any) -> dict[str, Any]:
        pytest.fail("読み取り失敗後に図の本体を取得してはいけない")

    with pytest.raises(getattr(errors, error_name)):
        await route._read_structure_graph("kb-a", "synthetic-graph", read)
