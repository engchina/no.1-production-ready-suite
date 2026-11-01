"""合成文書の補建で停止・再開・失敗の分離を確認する。"""

import asyncio
from copy import deepcopy
from types import SimpleNamespace
from typing import Any

import pytest

from app.rag import structure_index_jobs as module


@pytest.fixture
def jobs(monkeypatch: pytest.MonkeyPatch) -> tuple[module.StructureIndexJobs, dict[str, Any]]:
    saved: dict[str, Any] = {}

    class Store:
        def __init__(self, oracle: Any) -> None:
            pass

        async def get(self, kb: str) -> Any:
            return deepcopy(saved.get(kb))

        async def mutate(self, kb: str, change: Any, *, start: bool = False) -> Any:
            old = deepcopy(saved.get(kb))
            if start and old and old["status"] in {"RUNNING", "STOPPING"}:
                raise ValueError("構築中")
            saved[kb] = change(old)
            return deepcopy(saved[kb])

    async def plan(oracle: Any, kb: str) -> Any:
        return [
            {
                "document_id": name,
                "name": f"{name}.txt",
                "status": status,
                "chunk_sets": [name],
                "fingerprint": name,
            }
            for name, status in [
                ("a", "MISSING"),
                ("b", "MISSING"),
                ("c", "CURRENT"),
                ("d", "UNAVAILABLE"),
            ]
        ]

    async def actor() -> Any:
        from app.rag.request_context import current_audit_request_context

        return current_audit_request_context()

    monkeypatch.setattr(module, "refresh_actor", actor)
    monkeypatch.setattr(module, "StructureIndexStore", Store)
    monkeypatch.setattr(module, "structure_index_plan", plan)
    return module.StructureIndexJobs(), saved


class Oracle:
    async def get_knowledge_base(self, kb: str) -> Any:
        return SimpleNamespace(retrieval_config={"structure_index_enabled": True})


async def test_partial_failure_and_retry_only_failed(
    jobs: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, saved = jobs
    calls = []

    async def build(oracle: Any, kb: str, doc: str, **kwargs: Any) -> Any:
        calls.append(doc)
        if doc == "b":
            raise ValueError("no_structure")
        return {"quality": "PARTIAL"}

    monkeypatch.setattr(module, "rebuild_document_structure_index", build)
    await service.start(Oracle(), "kb", "missing")
    await asyncio.gather(*service.tasks)
    state = saved["kb"]
    assert calls == ["a", "b"]
    assert state["status"] == "COMPLETED"
    assert [item["status"] for item in state["items"]] == [
        "PARTIAL",
        "FAILED",
        "SKIPPED",
        "UNAVAILABLE",
    ]
    await service.start(Oracle(), "kb", "retry")
    await asyncio.gather(*service.tasks)
    assert calls == ["a", "b", "b"]


async def test_stop_finishes_current_then_resume_and_deduplicates(
    jobs: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, saved = jobs
    started, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def build(oracle: Any, kb: str, doc: str, **kwargs: Any) -> Any:
        calls.append(doc)
        started.set()
        await release.wait()
        return {"quality": "COMPLETE"}

    monkeypatch.setattr(module, "rebuild_document_structure_index", build)
    await service.start(Oracle(), "kb", "missing")
    await started.wait()
    with pytest.raises(ValueError, match="構築中"):
        await service.start(Oracle(), "kb", "missing")
    await service.stop(Oracle(), "kb")
    release.set()
    await asyncio.gather(*service.tasks)
    assert saved["kb"]["status"] == "STOPPED" and calls == ["a"]
    await service.start(Oracle(), "kb", "continue")
    await asyncio.gather(*service.tasks)
    assert calls == ["a", "b"]
    assert saved["kb"]["status"] == "COMPLETED"


async def test_service_shutdown_marks_interruption(
    jobs: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, saved = jobs
    started = asyncio.Event()

    async def build(*args: Any, **kwargs: Any) -> Any:
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(module, "rebuild_document_structure_index", build)
    await service.start(Oracle(), "kb", "missing")
    await started.wait()
    await service.shutdown()
    assert saved["kb"]["status"] == "INTERRUPTED"
    assert saved["kb"]["items"][0]["reason"] == "interrupted"
    assert saved["kb"]["items"][1]["status"] == "QUEUED"


def test_public_response_is_bounded_and_excludes_internal_source() -> None:
    state = {
        "items": [
            {"status": "QUEUED", "chunk_sets": ["cs"], "fingerprint": "fp", "document_id": str(i)}
            for i in range(70)
        ]
    }
    result = module.public_state(state, 25, 25)
    assert result and result["total"] == 70
    assert len(result["items"]) == 25 and result["items"][0]["document_id"] == "25"
    assert "chunk_sets" not in result["items"][0]
    assert result["counts"]["QUEUED"] == 70
