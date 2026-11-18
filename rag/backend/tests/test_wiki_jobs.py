"""背景構築の実行器を、合成の保存先・モデルで確認する。"""

import asyncio
from dataclasses import replace
from typing import Any

import httpx
import pytest

from app.clients import ai_calls
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
            "source_fingerprint": snapshot.source_fingerprint,
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

    async def link_knowledge_bases(self, did: str) -> list[str]:
        # 文書をまたぐ関連（#5492）は test_wiki_links.py で確かめる。ここでは候補の範囲を空にする。
        return []


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


@pytest.mark.asyncio
async def test_timeout_after_progress_resumes_on_next_reconcile(
    setup: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    jobs, store, _ = setup
    settings = Settings(rag_wiki_build_batch_units=2).model_copy(
        update={"rag_wiki_build_timeout_seconds": 0.2}
    )
    monkeypatch.setattr(module, "get_settings", lambda: settings)
    base, blocked = Model(), asyncio.Event()

    class SlowSecondBatch:
        async def generate(self, *args: Any, **kwargs: Any) -> str:
            if base.calls and not blocked.is_set():
                await asyncio.Event().wait()
            return await base.generate(*args, **kwargs)

    jobs.model_factory = lambda settings: SlowSecondBatch()
    await jobs.build("document-a", "owner-a")
    assert store.state["status"] == "FAILED" and store.state["failure_code"] == "interrupted"
    assert len(store.pages) == 2 and not store.embedded

    # 次の照合（再試行の指定なし）で保存済みの 2 単位を再利用し、残りだけを作る。
    blocked.set()
    await jobs.reconcile()
    await asyncio.gather(*list(jobs.tasks.values()))
    assert store.state["status"] == "READY"
    assert len(base.calls) == 2 and len(store.embedded) == 4
    await jobs.shutdown()


@pytest.mark.asyncio
async def test_timeout_without_progress_stops_until_retry(
    setup: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    jobs, store, _ = setup
    settings = Settings().model_copy(update={"rag_wiki_build_timeout_seconds": 0.2})
    monkeypatch.setattr(module, "get_settings", lambda: settings)

    calls: list[int] = []

    class Hanging:
        async def generate(self, *args: Any, **kwargs: Any) -> str:
            calls.append(1)
            await asyncio.Event().wait()
            return ""

    jobs.model_factory = lambda settings: Hanging()
    await jobs.build("document-a", "owner-a")
    assert store.state["failure_code"] == "timeout" and not store.pages
    await jobs.reconcile()
    await asyncio.gather(*list(jobs.tasks.values()))
    assert len(calls) == 1 and store.state["failure_code"] == "timeout"
    await jobs.shutdown()


@pytest.mark.asyncio
async def test_transient_model_failure_is_retried_by_shared_caller(
    setup: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    jobs, store, _ = setup
    base, failures = Model(), [503]

    async def no_wait(seconds: float) -> None:
        return None

    monkeypatch.setattr(ai_calls, "_sleep", no_wait)

    class FlakyModel:
        async def generate(self, *args: Any, **kwargs: Any) -> str:
            if failures:
                request = httpx.Request("POST", "https://example.invalid/v1/responses")
                raise httpx.HTTPStatusError(
                    "合成の一時的な失敗",
                    request=request,
                    response=httpx.Response(failures.pop(), request=request),
                )
            return await base.generate(*args, **kwargs)

    jobs.model_factory = lambda settings: FlakyModel()
    await jobs.build("document-a", "owner-a")
    assert store.state["status"] == "READY" and not failures
    assert len(base.calls) == 1 and len(store.embedded) == 4
    await jobs.shutdown()


@pytest.mark.asyncio
async def test_links_are_built_only_after_the_document_is_ready(
    setup: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """文書をまたぐ関連（#5492）は案内を公開した後だけ作る。案内の失敗では作らない。"""
    jobs, store, _ = setup
    linked: list[str] = []

    async def build_links(did: str, owner: Any, settings: Any) -> None:
        linked.append(did)

    monkeypatch.setattr(jobs, "_build_links", build_links)
    store.enabled = False
    await jobs.build("document-a", "owner-a")
    assert linked == []
    store.enabled = True
    await jobs.build("document-a", "owner-a")
    assert store.state["status"] == "READY" and linked == ["document-a"]
    # 公開済みの文書も、照合のたびに関連の状態を確かめる（作るかは関連の状態で決める）。
    await jobs.build("document-a", "owner-a")
    assert linked == ["document-a", "document-a"]
    await jobs.shutdown()
