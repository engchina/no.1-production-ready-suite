"""背景構築の実行器を、合成の保存先・モデルで確認する。"""

import asyncio
from dataclasses import replace
from typing import Any

import pytest

from app.clients.wiki_store import WikiSnapshot
from app.config import Settings
from app.rag import wiki_jobs as module
from app.rag.request_context import AuditRequestContext
from app.rag.wiki_models import fingerprint
from tests.test_wiki_compiler import Model, units


class Store:
    def __init__(self) -> None:
        self.enabled = True
        self.revision = "revision-a"
        self.source = WikiSnapshot("document-a", "source-a", tuple(units()))
        self.state: dict[str, Any] | None = None
        self.pages: list[Any] = []
        self.embedded: list[str] = []

    async def enabled_knowledge_bases(self) -> Any:
        return [("kb-a", "owner-a")] if self.enabled else []

    async def document_ids(self, filters: Any) -> Any:
        return ["document-a"] if self.enabled else []

    async def revision_fingerprint(self, did: str) -> Any:
        return self.revision if self.enabled else None

    async def document_state(self, did: str) -> Any:
        return self.state

    async def source_units(self, did: str) -> Any:
        return self.source if self.enabled else None

    async def enqueue(self, snapshot: Any) -> bool:
        self.state = {
            "status": "QUEUED",
            "compiler_fingerprint": snapshot.compiler_fingerprint,
            "revision_fingerprint": snapshot.revision_fingerprint,
            "failure_code": None,
        }
        return True

    async def claim(self, snapshot: Any, **kwargs: Any) -> str:
        assert self.state is not None
        self.state["status"], self.state["leased"] = "RUNNING", 1
        return "lease-a"

    async def saved_pages(self, snapshot: Any) -> Any:
        return self.pages

    async def checkpoint(self, snapshot: Any, token: str, pages: Any, **kwargs: Any) -> None:
        assert self.enabled and snapshot.revision_fingerprint == self.revision
        self.pages = list({page.unit.unit_id: page for page in [*self.pages, *pages]}.values())
        if kwargs.get("vectors"):
            self.embedded.extend(page.unit.unit_id for page in pages)

    async def finish(self, snapshot: Any, token: str, *, failure_code: str | None = None) -> None:
        assert self.state is not None
        self.state.update(status="FAILED" if failure_code else "READY", failure_code=failure_code)


@pytest.fixture
def setup(monkeypatch: pytest.MonkeyPatch) -> Any:
    store, model = Store(), Model()
    settings = Settings(rag_wiki_build_batch_units=4)
    monkeypatch.setattr(module, "get_settings", lambda: settings)
    monkeypatch.setattr(module, "compiler_fingerprint", lambda settings: "compiler-a")
    monkeypatch.setattr(module, "enterprise_ai_default_model_id", lambda settings: "model-a")

    async def actor(owner: Any, settings: Any) -> Any:
        return AuditRequestContext(user_uuid=owner)

    class Embedder:
        async def embed(self, texts: Any) -> Any:
            return [[1.0, 0.0] for _ in texts]

    jobs = module.WikiJobs(
        store_factory=lambda: store,
        actor=actor,
        model_factory=lambda settings: model,
        embedder_factory=lambda settings: Embedder(),
    )
    return jobs, store, model


@pytest.mark.asyncio
async def test_background_build_without_any_profile_and_cache_reuse(setup: Any) -> None:
    jobs, store, model = setup
    await jobs.reconcile()
    await asyncio.gather(*list(jobs.tasks.values()))
    assert store.state["status"] == "READY"
    assert len(store.pages) == len(store.embedded) == 4
    assert len(model.calls) == 1
    await jobs.build("document-a", "owner-a")
    assert len(model.calls) == 1
    await jobs.shutdown()


@pytest.mark.asyncio
async def test_disabled_build_does_not_instantiate_a_model(setup: Any) -> None:
    jobs, store, model = setup
    store.enabled = False
    await jobs.reconcile()
    assert not jobs.tasks and not model.calls
    await jobs.shutdown()


@pytest.mark.asyncio
async def test_new_source_rebuilds_and_preserves_current_version_bindings(setup: Any) -> None:
    jobs, store, model = setup
    await jobs.build("document-a", "owner-a")
    store.revision = "revision-b"
    updated = [
        unit.model_copy(
            update={
                "chunk_set_id": "version-b",
                "text": unit.text + " 更新。",
                "source_hash": fingerprint(unit.text + " 更新。"),
            }
        )
        for unit in store.source.units
    ]
    store.source = replace(store.source, source_fingerprint="source-b", units=tuple(updated))
    await jobs.build("document-a", "owner-a")
    assert len(model.calls) == 2 and store.state["status"] == "READY"
    assert all(page.unit.chunk_set_id == "version-b" for page in store.pages)
    await jobs.shutdown()


@pytest.mark.asyncio
async def test_policy_change_during_generation_blocks_publication(setup: Any) -> None:
    jobs, store, _ = setup
    entered, release = asyncio.Event(), asyncio.Event()
    base = Model()

    class WaitingModel:
        async def generate(self, *args: Any, **kwargs: Any) -> str:
            entered.set()
            await release.wait()
            return await base.generate(*args, **kwargs)

    jobs.model_factory = lambda settings: WaitingModel()
    task = asyncio.create_task(jobs.build("document-a", "owner-a"))
    await asyncio.wait_for(entered.wait(), 1)
    store.enabled = False
    release.set()
    await task
    assert not store.pages and store.state["status"] == "FAILED"
    assert store.state["failure_code"] == "authorization_changed"
    await jobs.shutdown()
