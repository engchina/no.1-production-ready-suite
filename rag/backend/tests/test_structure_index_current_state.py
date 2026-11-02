"""現在版の索引状態と過去の作業結果を分離する合成回帰。"""

from copy import deepcopy
from typing import Any

import pytest

from app.rag import structure_index_jobs as jobs


@pytest.mark.parametrize("operation", ["SKIPPED", "FAILED", "RUNNING", "QUEUED", "SUCCEEDED"])
async def test_current_index_does_not_inherit_operation_result(
    monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    state = {
        "job_id": "synthetic-job",
        "status": "COMPLETED",
        "items": [
            {
                "document_id": "synthetic-doc",
                "name": "synthetic.txt",
                "status": operation,
                "reason": "current" if operation == "SKIPPED" else None,
                "fingerprint": "old-snapshot",
                "chunk_sets": ["old-set"],
            },
        ],
    }
    original = deepcopy(state)
    current = "CURRENT"
    calls: list[list[str] | None] = []

    async def plan(oracle: Any, kb: str, *, document_ids: list[str] | None = None) -> list[Any]:
        calls.append(document_ids)
        return [
            {
                "document_id": "synthetic-doc",
                "status": current,
                "quality": "COMPLETE",
                "reason": None,
            }
        ]

    monkeypatch.setattr(jobs, "structure_index_plan", plan)
    view = await jobs.current_public_state(object(), "synthetic-kb", state)
    assert view is not None
    assert view["items"][0]["index_status"] == "CURRENT"
    assert view["items"][0]["status"] == operation
    current = "STALE"
    changed = await jobs.current_public_state(object(), "synthetic-kb", state)
    assert changed is not None and changed["items"][0]["index_status"] == "STALE"
    assert state == original
    assert calls == [["synthetic-doc"], ["synthetic-doc"]]


async def test_only_visible_page_is_refreshed_and_missing_is_not_inferred(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[list[str] | None] = []

    async def plan(oracle: Any, kb: str, *, document_ids: list[str] | None = None) -> list[Any]:
        seen.append(document_ids)
        return []

    monkeypatch.setattr(jobs, "structure_index_plan", plan)
    assert await jobs.current_public_state(object(), "kb", None) is None
    state = {
        "items": [
            {"document_id": f"doc-{i}", "name": f"synthetic-{i}.txt", "status": "SUCCEEDED"}
            for i in range(30)
        ]
    }
    view = await jobs.current_public_state(object(), "kb", state, offset=25, limit=25)
    assert view is not None
    assert seen == [[f"doc-{i}" for i in range(25, 30)]]
    assert all(item["index_status"] == "UNKNOWN" for item in view["items"])
    assert view["counts"]["SUCCEEDED"] == 30


async def test_current_snapshot_is_derived_from_live_chunk_sets_and_saved_build(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from app.rag.structure_index import source_fingerprint, structure_index_plan

    class Oracle:
        source = "synthetic-set-a"
        enabled = True
        sqls: list[tuple[str, Any]] = []

        async def get_knowledge_base(self, _kb: str) -> Any:
            return SimpleNamespace(retrieval_config={"structure_index_enabled": self.enabled})

        async def _fetch_all(self, sql: str, binds: Any) -> list[dict[str, Any]]:
            self.sqls.append((sql, binds))
            if "rag_structure_index_builds" in sql:
                return [
                    {
                        "document_id": "synthetic-doc",
                        "source_fingerprint": source_fingerprint(["synthetic-set-a"]),
                        "quality": "COMPLETE",
                    }
                ]
            assert "d.document_id IN" in sql
            assert "embedding" not in sql and "chunk_text" not in sql
            return [
                {
                    "document_id": "synthetic-doc",
                    "file_name": "synthetic.txt",
                    "chunk_set_id": self.source,
                    "chunks": 1,
                    "headings": 1,
                }
            ]

    oracle = Oracle()
    before = await structure_index_plan(oracle, "kb", document_ids=["synthetic-doc"])
    assert before[0]["status"] == "CURRENT"
    oracle.enabled = False
    disabled = await structure_index_plan(oracle, "kb", document_ids=["synthetic-doc"])
    assert disabled[0]["status"] == "UNAVAILABLE"
    assert disabled[0]["index_status"] == "CURRENT"
    oracle.enabled = True
    oracle.source = "synthetic-set-b"
    after = await structure_index_plan(oracle, "kb", document_ids=["synthetic-doc"])
    assert after[0]["status"] == "STALE"
    assert await structure_index_plan(oracle, "kb", document_ids=[]) == []


async def test_job_endpoint_enriches_only_currently_visible_items(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api.routes import knowledge_bases as routes
    from app.clients import structure_index_store

    state = {
        "items": [
            {
                "document_id": "visible",
                "name": "synthetic-a.txt",
                "status": "SKIPPED",
                "reason": "current",
            },
            {"document_id": "hidden", "name": "synthetic-b.txt", "status": "SUCCEEDED"},
        ]
    }

    class Oracle:
        async def get_knowledge_base(self, _kb: str) -> object:
            return object()

    class Store:
        def __init__(self, _oracle: Any) -> None:
            pass

        async def get(self, _kb: str) -> dict[str, Any]:
            return state

        async def visible_state(self, _kb: str, value: Any) -> dict[str, Any]:
            return {**value, "items": value["items"][:1], "partial_visibility": True}

    async def plan(_oracle: Any, _kb: str, *, document_ids: list[str] | None = None) -> list[Any]:
        assert document_ids == ["visible"]
        return [{"document_id": "visible", "status": "CURRENT", "quality": "COMPLETE"}]

    monkeypatch.setattr(routes, "OracleClient", Oracle)
    monkeypatch.setattr(structure_index_store, "StructureIndexStore", Store)
    monkeypatch.setattr(jobs, "structure_index_plan", plan)
    response = await routes.get_structure_index_job("kb", offset=0, limit=25)
    assert response.data is not None
    assert response.data["total"] == 1 and response.data["partial_visibility"]
    assert response.data["items"][0]["index_status"] == "CURRENT"
    assert state["items"][0]["status"] == "SKIPPED"
