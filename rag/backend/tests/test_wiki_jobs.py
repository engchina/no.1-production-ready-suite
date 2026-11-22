"""背景構築の実行器を、合成の保存先・モデルで確認する。"""

import asyncio
import errno
import json
from dataclasses import replace
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from pr_backend_core.logging import StructuredFormatter

from app.clients import ai_calls
from app.clients.wiki_store import WikiSnapshot
from app.config import Settings
from app.rag import wiki_jobs as module
from app.rag.request_context import AuditRequestContext, current_audit_request_context
from app.rag.wiki_diagnostics import failure_fields
from app.rag.wiki_models import fingerprint
from app.rag.wiki_paragraphs import Paragraph, SentenceTooLarge
from app.rag.wiki_prepare import WikiRequestBuilder
from tests.test_wiki_compiler import Model, units


def test_network_diagnostics_report_the_connection_cause_without_the_address() -> None:
    try:
        raise ConnectionRefusedError(errno.ECONNREFUSED, "private-host")
    except ConnectionRefusedError as cause:
        error = httpx.ConnectError("private-gateway")
        error.__cause__ = cause
    fields = failure_fields(error, "doc-a", "generate_pages", "rev-a")
    assert (fields["error_kind"], fields["network_cause"]) == ("network", "refused")
    assert "private-" not in str(fields)


@pytest.mark.parametrize("recovers", [False, True])
async def test_invalid_single_response_retries_or_logs_field_and_rule(
    setup: Any, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, recovers: bool
) -> None:
    jobs, store, model = setup
    store.source = replace(store.source, units=store.source.units[:1])
    monkeypatch.setattr(ai_calls, "_sleep", AsyncMock())
    row = {
        "unit_id": "U1",
        "context": "private-content",
        "possible_questions": ["private-content" * 20],
        "dependencies": [],
        "unresolved": [],
        "private-key": "private-value",
    }
    invalid = json.dumps({"items": [row]})
    generate = AsyncMock(return_value=invalid)
    if recovers:
        valid = dict(row, possible_questions=[])
        del valid["private-key"]
        generate.side_effect = [invalid, json.dumps({"items": [valid]})]
    monkeypatch.setattr(model, "generate", generate)
    await jobs.build("document-a", "owner-a")
    failures = [r for r in caplog.records if r.getMessage() == "wiki_build_failed"]
    if recovers:
        assert store.state["status"] == "READY" and len(store.pages) == len(store.embedded) == 1
        assert generate.await_count == 2 and not failures
        return
    (record,) = failures
    data = vars(record)
    assert store.state["status"] == "FAILED" and not store.pages
    assert (data["document_id"], data["source_fingerprint"]) == ("document-a", "source-a")
    assert (data["wiki_phase"], data["validation_schema"]) == ("generate_pages", "WikiContexts")
    assert data["unit_id_hash"] == fingerprint(store.source.units[0].unit_id)
    assert data["attempts"] == generate.await_count == 3
    assert data["validation_errors"] == [
        {"type": "string_too_long", "loc": ["items", 0, "possible_questions", 0]},
        {"type": "extra_forbidden", "loc": ["items", 0, "<redacted>"]},
    ]
    assert data["error_kind"] == "invalid_output" and data["http_status"] is None
    formatter = StructuredFormatter(
        service_name="rag",
        service_version="test",
        environment="test",
        component="api",
        max_record_bytes=32768,
    )
    logged = formatter.format(record)
    assert json.loads(logged)["validation_errors"] == data["validation_errors"]
    assert "private-" not in logged


async def test_embedding_http_failure_and_checkpoint_database_failure_have_distinct_logs(
    setup: Any, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    jobs, store, _ = setup
    request = httpx.Request("POST", "https://example.invalid/private-path")
    error = httpx.HTTPStatusError("private-response", request=request, response=httpx.Response(503))
    embedder = AsyncMock()
    embedder.embed.side_effect = error
    jobs.embedder_factory = lambda _: embedder
    await jobs.build("document-a", "owner-a")
    (first,) = (vars(r) for r in caplog.records if r.getMessage() == "wiki_build_failed")
    assert (first["wiki_phase"], first["error_kind"], first["http_status"]) == (
        "embed_pages",
        "unavailable",
        503,
    )
    caplog.clear()
    store.state = None
    embedder.embed.side_effect = None
    embedder.embed.return_value = [[1.0, 0.0]] * len(store.pages)

    async def fail_write(*args: Any, **kwargs: Any) -> None:
        try:
            raise ValueError("ORA-03113 private-message")
        except ValueError as cause:
            raise RuntimeError("private-wrapper") from cause

    monkeypatch.setattr(store, "checkpoint", fail_write)
    await jobs.build("document-a", "owner-a")
    (second,) = (vars(r) for r in caplog.records if r.getMessage() == "wiki_build_failed")
    assert second["wiki_phase"] == "save_embeddings"
    assert second["exception_cause_details"][0]["error_code"] == "ORA-03113"
    assert "private-" not in str([first, second])


async def test_invalid_json_logs_position_of_the_failed_response(
    setup: Any, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    jobs, store, model = setup
    store.source = replace(store.source, units=store.source.units[:1])
    monkeypatch.setattr(ai_calls, "_sleep", AsyncMock())
    monkeypatch.setattr(model, "generate", AsyncMock(return_value='{"items":\n private-content}'))
    await jobs.build("document-a", "owner-a")
    (data,) = (vars(r) for r in caplog.records if r.getMessage() == "wiki_build_failed")
    assert (data["json_line"], data["json_column"], data["error_kind"]) == (2, 2, "invalid_output")
    assert data["attempts"] == 3 and "private-content" not in str(data)


async def test_unsplittable_sentence_logs_hashed_anchor_and_window_offsets(
    setup: Any, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    jobs, store, model = setup
    error = SentenceTooLarge(Paragraph(0, 35, source="anchor-a"), 5, 35)
    monkeypatch.setattr(WikiRequestBuilder, "compile", AsyncMock(side_effect=error))
    await jobs.build("document-a", "owner-a")
    (record,) = (r for r in caplog.records if r.getMessage() == "wiki_build_failed")
    data = vars(record)
    assert data["source_anchor_hash"] == fingerprint("anchor-a")
    assert (data["source_window_start"], data["source_window_end"]) == (5, 35)
    assert store.state["failure_code"] == "build_failed" and not model.calls


class Store:
    def __init__(self) -> None:
        self.enabled = True
        self.prompts: dict[str, str] = {}
        self.revision = "revision-a"
        self.source = WikiSnapshot("document-a", "source-a", tuple(units()))
        self.state: dict[str, Any] | None = None
        self.pages: list[Any] = []
        self.embedded: list[str] = []
        # 実際に構築を始めてからの秒数と、延ばした lease の秒数（#5748）。
        self.elapsed = 0.0
        self.renewed: list[int] = []

    async def build_elapsed_seconds(self, did: str, *, links: bool = False) -> float:
        assert not links
        return self.elapsed

    async def renew_lease(self, did: str, token: str, *, lease_seconds: int, **kwargs: Any) -> bool:
        self.renewed.append(lease_seconds)
        return True

    async def enabled_knowledge_bases(self) -> Any:
        return [("kb-a", "owner-a")] if self.enabled else []

    async def document_ids(self, filters: Any) -> Any:
        return ["document-a"] if self.enabled else []

    async def revision_fingerprint(self, did: str) -> Any:
        return self.revision if self.enabled else None

    async def prompt_overrides(self) -> dict[str, str]:
        return self.prompts

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
    from rag_parser_core.oci_enterprise_ai import OciEnterpriseAiConfig

    monkeypatch.setattr(
        module,
        "config_from_settings",
        lambda settings: OciEnterpriseAiConfig(default_model_id="model-a"),
    )

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
    store.prompts["wiki_build"] = "合成の編集済み指示"
    await jobs.reconcile()
    await asyncio.gather(*list(jobs.tasks.values()))
    assert store.state["status"] == "READY"
    assert len(store.pages) == len(store.embedded) == 4
    assert model.prompts == ["合成の編集済み指示"] * len(model.calls)
    store.prompts["wiki_build"] = "合成の次回用指示"
    store.state["compiler_fingerprint"] = "previous-settings"
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


@pytest.mark.parametrize("change", ["none", "revision", "membership", "actor"])
async def test_document_retry_shares_capacity_and_only_builds_authorized_pages(
    setup: Any, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    jobs, store, model = setup
    store.state = {
        "status": "FAILED",
        "failure_code": "timeout",
        "revision_fingerprint": store.revision,
    }
    scopes: list[frozenset[str] | None] = []
    member = True
    allowed = {"kb-a", "kb-b"}

    async def actor(owner: str, settings: Settings) -> AuditRequestContext:
        return AuditRequestContext(user_uuid=owner, allowed_knowledge_base_ids=frozenset(allowed))

    async def revision(did: str) -> str | None:
        assert did == "document-a"
        scopes.append(current_audit_request_context().allowed_knowledge_base_ids)
        if not member and scopes[-1] == frozenset({"kb-a"}):
            return None
        return str(store.revision)

    monkeypatch.setattr(jobs, "actor", actor)
    monkeypatch.setattr(store, "revision_fingerprint", revision)
    transitions = []

    async def requeue(kb: str, did: str, expected: str) -> bool:
        assert (kb, did, expected) == ("kb-a", "document-a", "revision-a")
        if store.state["status"] != "FAILED":
            return False
        transitions.append(did)
        store.state["status"] = "QUEUED"
        return True

    monkeypatch.setattr(store, "requeue_failed_document_pages", requeue, raising=False)
    links = AsyncMock()
    monkeypatch.setattr(module.WikiJobs, "_build_links", links)
    jobs.capacity = asyncio.Semaphore(0)
    prior_finished = asyncio.Event()
    jobs._schedule("document-a", prior_finished.wait)
    with pytest.raises(KeyError):
        await jobs.retry_document_pages("kb-a", "outside")
    await jobs.retry_document_pages("kb-a", "document-a")
    task = jobs.tasks["document-a"]
    await jobs.retry_document_pages("kb-a", "document-a")
    assert jobs.tasks["document-a"] is task and transitions == ["document-a"]
    prior_finished.set()
    await asyncio.sleep(0)
    assert not task.done() and not model.calls
    if change == "revision":
        store.revision = "new"
    elif change == "membership":
        member = False
    elif change == "actor":
        allowed.remove("kb-a")
    jobs.capacity.release()
    await task
    assert scopes[-1] == frozenset({"kb-a"})
    if change != "none":
        assert not store.pages and not model.calls
    else:
        assert store.state["status"] == "READY" and len(store.pages) == 4
    links.assert_not_awaited()
    await jobs.shutdown()


@pytest.mark.asyncio
async def test_new_source_rebuilds_and_preserves_current_version_bindings(setup: Any) -> None:
    jobs, store, model = setup
    await jobs.build("document-a", "owner-a")
    store.prompts["wiki_build"] = "合成の次回用指示"
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
    assert model.prompts[-1] == "合成の次回用指示"
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
async def test_interrupted_build_resumes_from_saved_pages_on_next_reconcile(
    setup: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """再起動などで止まった構築（interrupted）は、次の照合で保存済みの単位から続ける。"""
    jobs, store, _ = setup
    settings = Settings(rag_wiki_build_batch_units=2)
    monkeypatch.setattr(module, "get_settings", lambda: settings)
    base, blocked, waiting = Model(), asyncio.Event(), asyncio.Event()

    class SlowSecondBatch:
        async def generate(self, *args: Any, **kwargs: Any) -> str:
            if base.calls and not blocked.is_set():
                waiting.set()
                await asyncio.Event().wait()
            return await base.generate(*args, **kwargs)

    jobs.model_factory = lambda settings: SlowSecondBatch()
    task = asyncio.create_task(jobs.build("document-a", "owner-a"))
    await waiting.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
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
async def test_build_past_the_deadline_from_its_actual_start_times_out(setup: Any) -> None:
    """上限は実際に構築を始めた時から数え、超えていればモデルを呼ばずに止める（#5748）。"""
    jobs, store, model = setup
    store.elapsed = Settings().rag_wiki_build_timeout_seconds + 1
    await jobs.build("document-a", "owner-a")
    assert store.state["status"] == "FAILED" and store.state["failure_code"] == "timeout"
    assert not model.calls
    await jobs.shutdown()


@pytest.mark.asyncio
async def test_timeout_after_progress_stops_until_retry(
    setup: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """上限の内側では 1 回で続け、残りの時間を使い切ったら保存済みがあっても止める（#5748）。"""
    jobs, store, _ = setup
    settings = Settings(rag_wiki_build_batch_units=2).model_copy(
        update={"rag_wiki_build_timeout_seconds": 10.0}
    )
    monkeypatch.setattr(module, "get_settings", lambda: settings)
    # 開始から 9.8 秒経っていれば、残りは 0.2 秒。
    store.elapsed = 9.8
    base = Model()

    class SlowSecondBatch:
        async def generate(self, *args: Any, **kwargs: Any) -> str:
            if base.calls:
                await asyncio.Event().wait()
            return await base.generate(*args, **kwargs)

    jobs.model_factory = lambda settings: SlowSecondBatch()
    await jobs.build("document-a", "owner-a")
    assert store.state["status"] == "FAILED" and store.state["failure_code"] == "timeout"
    assert len(store.pages) == 2 and not store.embedded
    await jobs.reconcile()
    await asyncio.gather(*list(jobs.tasks.values()))
    assert len(base.calls) == 1 and store.state["failure_code"] == "timeout"
    await jobs.shutdown()


@pytest.mark.asyncio
async def test_keep_lease_renews_until_the_build_ends() -> None:
    """構築の間は lease を延ばし続け、終わったら延ばさない（#5748）。"""
    renewed: list[tuple[str, str, int, bool]] = []

    class LeaseStore:
        async def renew_lease(
            self, did: str, token: str, *, lease_seconds: int, links: bool = False
        ) -> bool:
            renewed.append((did, token, lease_seconds, links))
            return True

    async with module.keep_lease(LeaseStore(), "document-a", "lease-a", lease_seconds=3):
        await asyncio.sleep(2.2)
    count = len(renewed)
    assert count == 2 and set(renewed) == {("document-a", "lease-a", 3, False)}
    await asyncio.sleep(1.2)
    assert len(renewed) == count


@pytest.mark.asyncio
async def test_timeout_without_progress_stops_until_retry(
    setup: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    jobs, store, _ = setup
    settings = Settings().model_copy(update={"rag_wiki_build_timeout_seconds": 10.0})
    monkeypatch.setattr(module, "get_settings", lambda: settings)
    store.elapsed = 9.8

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
async def test_long_link_builds_do_not_hold_the_page_build_slots(
    setup: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """関連は案内と別の枠で作り、関連が長くてもほかの文書の案内を待たせない（#5751）。"""
    jobs, _, _ = setup
    settings = Settings(rag_wiki_build_workers=1, rag_wiki_link_workers=1)
    monkeypatch.setattr(module, "get_settings", lambda: settings)
    release, link_started = asyncio.Event(), asyncio.Event()
    pages: list[str] = []
    links: list[str] = []
    running_links = 0
    peak_links = 0

    async def build_pages(did: str, owner: Any, settings: Any) -> bool:
        pages.append(did)
        return True

    async def build_links(did: str, owner: Any, settings: Any) -> None:
        nonlocal running_links, peak_links
        running_links += 1
        peak_links = max(peak_links, running_links)
        links.append(did)
        link_started.set()
        await release.wait()
        running_links -= 1

    monkeypatch.setattr(jobs, "_build_pages", build_pages)
    monkeypatch.setattr(jobs, "_build_links", build_links)
    first = asyncio.create_task(jobs.build("document-a", "owner-a"))
    await link_started.wait()
    # 文書 A の関連が枠を持っている間も、文書 B・C の案内は案内の枠（1）で作れる。
    second = asyncio.create_task(jobs.build("document-b", "owner-a"))
    third = asyncio.create_task(jobs.build("document-c", "owner-a"))
    for _ in range(20):
        if len(pages) == 3:
            break
        await asyncio.sleep(0)
    assert pages == ["document-a", "document-b", "document-c"]
    # 関連は関連の枠（1）を超えて同時に作らない。
    assert links == ["document-a"] and peak_links == 1
    release.set()
    await asyncio.gather(first, second, third)
    assert sorted(links) == ["document-a", "document-b", "document-c"] and peak_links == 1
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
