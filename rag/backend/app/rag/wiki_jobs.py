"""KB の指定を現在の権限で読み、検索を待たせず Wiki を増分構築する。"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine, Sequence
from contextlib import asynccontextmanager, suppress
from dataclasses import replace
from functools import partial
from typing import Any

from pr_system_settings.auth.errors import SecurityApiError
from pr_system_settings.auth.visibility import can_view_all_users_data

from app.clients.ai_calls import make_ai_caller
from app.clients.oci_enterprise_ai import OciEnterpriseAiClient, config_from_settings
from app.clients.oci_genai import OciGenAiClient
from app.clients.oracle import OracleClient
from app.clients.wiki_store import WikiStore
from app.config import (
    Settings,
    enterprise_ai_default_model_id,
    enterprise_ai_output_token_budget,
    get_settings,
)
from app.rag.request_context import (
    AuditRequestContext,
    audit_request_context_for_principal,
    audit_request_context_from_headers,
    reset_audit_request_context,
    set_audit_request_context,
    system_access_context,
)
from app.rag.wiki_compiler import WIKI_CONTEXT_PROMPT
from app.rag.wiki_diagnostics import failure_fields
from app.rag.wiki_models import WIKI_SCHEMA_VERSION, WikiPage, fingerprint
from app.rag.wiki_paragraphs import SentenceTooLarge
from app.rag.wiki_prepare import WikiRequestBuilder
from app.rag.wiki_source_inputs import source_inputs

logger = logging.getLogger(__name__)


def compiler_fingerprint(settings: Settings) -> str:
    model_id = enterprise_ai_default_model_id(settings)
    return fingerprint(
        [
            WIKI_SCHEMA_VERSION,
            WIKI_CONTEXT_PROMPT,
            model_id,
            enterprise_ai_output_token_budget(settings, model_id),
            settings.rag_wiki_build_input_chars,
            settings.rag_wiki_build_batch_units,
            settings.rag_wiki_build_timeout_seconds,
            settings.rag_wiki_embedding_source_chars,
            settings.oci_genai_embedding_model,
        ]
    )


async def current_actor(user_uuid: str | None, settings: Settings) -> AuditRequestContext:
    if settings.local_debug_enabled:
        return audit_request_context_from_headers(
            {},
            request_id="wiki-worker",
            settings=settings,
            authenticated_user_id=user_uuid,
            trust_scope_headers=False,
        )
    if not user_uuid:
        raise PermissionError("Wiki の構築を指定した利用者を確認できません。")
    from app.security.domain import as_principal
    from app.security.permissions import MENU_KNOWLEDGE_BASES
    from app.security.service import get_security_service

    principal = as_principal(
        await asyncio.to_thread(get_security_service().principal_for_worker, user_uuid)
    )
    if MENU_KNOWLEDGE_BASES not in principal.permissions:
        raise PermissionError("Wiki の構築権限が変更されました。")
    return audit_request_context_for_principal(
        {},
        request_id="wiki-worker",
        user_uuid=user_uuid,
        allowed_search_answer_profile_ids=principal.allowed_search_answer_profile_ids,
        allowed_knowledge_base_ids=principal.allowed_knowledge_base_ids,
        all_users_visible=can_view_all_users_data(principal),
        settings=settings,
    )


@asynccontextmanager
async def keep_lease(
    store: Any, document_id: str, token: str, *, lease_seconds: int
) -> AsyncIterator[None]:
    """構築の間、lease を ``lease_seconds`` の 1/3 ごとに延ばす（#5748）。

    lease は短く保ち、process が止まれば lease の時間の後に次の照合が続きを拾う。所有権を
    失ったら延ばすのをやめる（保存の時に所有権を確かめて止まる）。
    """

    async def renew() -> None:
        while True:
            await asyncio.sleep(max(1.0, lease_seconds / 3))
            try:
                if not await store.renew_lease(document_id, token, lease_seconds=lease_seconds):
                    return
            except Exception as error:
                logger.warning(
                    "wiki_lease_renew_failed", extra={"error_type": type(error).__name__}
                )

    task = asyncio.create_task(renew())
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


async def remaining_seconds(store: Any, document_id: str, settings: Settings) -> float:
    """実際に構築を始めてからの上限（``RAG_WIKI_BUILD_TIMEOUT_SECONDS``）の残りの秒数（#5748）。"""
    elapsed = float(await store.build_elapsed_seconds(document_id))
    return settings.rag_wiki_build_timeout_seconds - elapsed


class WikiDefaultTextModel:
    """Wiki の各モデル要求に、その時点の既定テキストモデルと接続を使う。"""

    def __init__(self, factory: Callable[[Settings], Any]) -> None:
        self.factory = factory

    async def generate(self, prompt: str, context: str, **kwargs: Any) -> str:
        return str(await self.factory(get_settings()).generate(prompt, context, **kwargs))


class WikiJobs:
    def __init__(
        self,
        *,
        store_factory: Callable[[], Any] | None = None,
        actor: Callable[[str | None, Settings], Awaitable[AuditRequestContext]] = current_actor,
        model_factory: Callable[[Settings], Any] | None = None,
        embedder_factory: Callable[[Settings], Any] | None = None,
    ) -> None:
        self.default_store = store_factory is None
        self.store_factory = store_factory or (lambda: WikiStore(OracleClient()))
        self.actor, self.model_factory, self.embedder_factory = (
            actor,
            model_factory or (lambda settings: OciEnterpriseAiClient(settings=settings)),
            embedder_factory or (lambda settings: OciGenAiClient(settings=settings)),
        )
        self.tasks: dict[str, asyncio.Task[None]] = {}
        self.reconciliations: set[asyncio.Task[None]] = set()
        self.capacity: asyncio.Semaphore | None = None

    def notify(self, kb_id: str | None = None) -> None:
        task = asyncio.create_task(self.reconcile(kb_id))
        self.reconciliations.add(task)
        task.add_done_callback(self.reconciliations.discard)

    def _schedule(
        self,
        document_id: str,
        run: Callable[[], Coroutine[Any, Any, None]],
        *,
        after_running: bool = False,
    ) -> None:
        prior = self.tasks.get(document_id)
        if prior is not None and not prior.done() and not after_running:
            return

        async def after_prior() -> None:
            if prior is not None and not prior.done():
                await asyncio.gather(prior, return_exceptions=True)
            await run()

        task = asyncio.create_task(after_prior())
        self.tasks[document_id] = task

        def finished(completed: asyncio.Task[None]) -> None:
            if self.tasks.get(document_id) is completed:
                self.tasks.pop(document_id, None)

        task.add_done_callback(finished)

    def _worker_capacity(self, settings: Settings) -> asyncio.Semaphore:
        if self.capacity is None:
            self.capacity = asyncio.Semaphore(settings.rag_wiki_build_workers)
        return self.capacity

    async def retry_document_pages(self, kb_id: str, document_id: str) -> None:
        """操作した利用者と既存の構築担当の両方から見える一文書の案内を再試行する。"""
        store = self.store_factory()
        filters = {"knowledge_base_id": kb_id, "document_id": document_id}
        if document_id not in await store.document_ids(filters):
            raise KeyError(document_id)
        owners = dict(await store.enabled_knowledge_bases())
        if kb_id not in owners:
            raise ValueError("Wiki の構築を有効にしてください。")
        owner = owners[kb_id]
        revision = await store.revision_fingerprint(document_id)
        if not revision:
            raise KeyError(document_id)

        async def scoped_actor(user: str | None, settings: Settings) -> AuditRequestContext:
            try:
                context = await self.actor(user, settings)
            except (SecurityApiError, PermissionError) as error:
                raise PermissionError("Wiki の構築権限が変更されました。") from error
            if (
                context.allowed_knowledge_base_ids is not None
                and kb_id not in context.allowed_knowledge_base_ids
            ):
                raise PermissionError("Wiki の構築権限が変更されました。")
            # 別の KB で同じ資料が見えていても、指定した KB の所属・版を再確認する。
            context = replace(context, allowed_knowledge_base_ids=frozenset({kb_id}))
            token = set_audit_request_context(context)
            try:
                if await store.revision_fingerprint(document_id) != revision:
                    raise PermissionError("Wiki の原文・所属が変更されました。")
            finally:
                reset_audit_request_context(token)
            return context

        await scoped_actor(owner, get_settings())
        if not await store.requeue_failed_document_pages(kb_id, document_id, revision):
            return

        async def run() -> None:
            settings = get_settings()
            worker = WikiJobs(
                store_factory=self.store_factory,
                actor=scoped_actor,
                model_factory=self.model_factory,
                embedder_factory=self.embedder_factory,
            )
            async with self._worker_capacity(settings):
                await worker._build_pages(document_id, owner, settings)

        # 先行する照合が FAILED を見て終了する途中でも、今回受理した再試行を失わない。
        self._schedule(document_id, run, after_running=True)

    async def reconcile(self, kb_id: str | None = None) -> None:
        if self.default_store and not get_settings().oracle_dsn.strip():
            return
        try:
            store = self.store_factory()
            # 起動後の復旧は ID と構築を指定した利用者だけを読み、本文はその利用者で読む。
            with system_access_context():
                policies = await store.enabled_knowledge_bases()
            settings = get_settings()
            for kb, owner in policies:
                if kb_id is not None and kb != kb_id:
                    continue
                try:
                    context = await self.actor(owner, settings)
                except Exception as error:
                    logger.warning(
                        "wiki_actor_unavailable", extra={"error_type": type(error).__name__}
                    )
                    continue
                token = set_audit_request_context(context)
                try:
                    for did in await store.document_ids(
                        {"knowledge_base_id": kb, "include_superseded": "true"}
                    ):
                        self._schedule(did, partial(self.build, did, owner))
                finally:
                    reset_audit_request_context(token)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            # schema 未更新や接続失敗も、通常の検索・起動を止めない。
            logger.warning("wiki_reconcile_failed", extra={"error_type": type(error).__name__})

    async def build(self, document_id: str, owner: str | None) -> None:
        """文書の案内を作る。別の文書への参照は案内と同じ処理で取る（文書ごとの 1 段。#5784）。"""
        settings = get_settings()
        async with self._worker_capacity(settings):
            await self._build_pages(document_id, owner, settings)

    async def _build_pages(self, document_id: str, owner: str | None, settings: Settings) -> bool:
        """文書の案内を作る。今の原文で公開済み（``READY``）なら True。"""
        store = self.store_factory()
        snapshot, lease, context_token = None, None, None
        phase = "prepare_document"
        try:
            context_token = set_audit_request_context(await self.actor(owner, settings))
            revision = await store.revision_fingerprint(document_id)
            if revision is None:
                return False
            state = await store.document_state(document_id)
            if state and state["revision_fingerprint"] == revision:
                if state["status"] == "READY":
                    return True
                if state["status"] == "RUNNING" and state["leased"]:
                    return False
                # 失敗は再試行の API（`requeue_failed`）が待機へ戻すまで作り直さない。
                if (
                    state["status"] == "FAILED"
                    and state.get("failure_code") != "interrupted"
                    and not (
                        state.get("failure_code") == "authorization_changed"
                        and state.get("owner_user_uuid") != owner
                    )
                ):
                    return False
            raw = await store.source_units(document_id)
            if raw is None:
                return False
            snapshot = replace(
                raw,
                compiler_fingerprint=compiler_fingerprint(settings),
                revision_fingerprint=revision,
            )
            await store.enqueue(snapshot)
            lease = await store.claim(snapshot, lease_seconds=settings.rag_wiki_lease_seconds)
            if lease is None:
                return False
            # 上限は実際に構築を始めた時から数え、中断からの続きでも同じ開始を使う（#5748）。
            remaining = await remaining_seconds(store, document_id, settings)
            if remaining <= 0:
                raise TimeoutError
            phase = "load_saved_pages"
            saved = await store.saved_pages(snapshot)

            async def authorize() -> None:
                nonlocal phase
                prior = phase
                phase = "authorize"
                current = get_settings()
                set_audit_request_context(await self.actor(owner, current))
                if await store.revision_fingerprint(document_id) != revision:
                    raise PermissionError("Wiki の原文・所属が変更されました。")
                phase = prior

            async def checkpoint(pages: Sequence[WikiPage]) -> None:
                nonlocal phase
                await authorize()
                phase = "save_pages"
                await store.checkpoint(
                    snapshot,
                    lease,
                    pages,
                    original_chars=settings.rag_wiki_embedding_source_chars,
                )
                phase = "generate_pages"

            async with (
                keep_lease(
                    store, document_id, lease, lease_seconds=settings.rag_wiki_lease_seconds
                ),
                asyncio.timeout(remaining),
            ):
                phase = "prepare_input"
                sources, related = (
                    await source_inputs(
                        store.oracle, snapshot.units, snapshot.source_metadata, authorize
                    )
                    if snapshot.source_metadata
                    else ({}, {})
                )
                builder = WikiRequestBuilder(
                    snapshot.units,
                    config=config_from_settings(settings),
                    current_config=lambda: config_from_settings(get_settings()),
                    system_prompt=(await store.prompt_overrides()).get(
                        "wiki_build", WIKI_CONTEXT_PROMPT
                    ),
                    sources=sources,
                    related=related,
                    max_source_chars=settings.rag_wiki_build_input_chars,
                    batch_size=settings.rag_wiki_build_batch_units,
                )
                phase = "generate_pages"
                pages = await builder.compile(
                    snapshot.units,
                    model=WikiDefaultTextModel(self.model_factory),
                    saved=saved,
                    authorize=authorize,
                    checkpoint=checkpoint,
                    caller=make_ai_caller(settings, "wiki_build"),
                )
                phase = "prepare_embedding"
                embedder = self.embedder_factory(settings)
                for offset in range(0, len(pages), settings.rag_wiki_build_batch_units):
                    phase = "embed_pages"
                    batch = pages[offset : offset + settings.rag_wiki_build_batch_units]
                    await authorize()
                    vectors = await embedder.embed(
                        [
                            page.search_text(
                                original_chars=settings.rag_wiki_embedding_source_chars
                            )
                            for page in batch
                        ]
                    )
                    await authorize()
                    phase = "save_embeddings"
                    await store.checkpoint(
                        snapshot,
                        lease,
                        batch,
                        vectors=vectors,
                        original_chars=settings.rag_wiki_embedding_source_chars,
                    )
                await authorize()
                phase = "publish_pages"
                await store.finish(snapshot, lease)
            return True
        except asyncio.CancelledError:
            if snapshot is not None and lease:
                with suppress(Exception):
                    await store.finish(snapshot, lease, failure_code="interrupted")
            raise
        except Exception as error:
            if snapshot is not None and lease:
                with suppress(Exception):
                    await store.finish(
                        snapshot,
                        lease,
                        # 実際の開始からの上限を超えたら止め、再試行を待つ（#5748）。
                        failure_code="timeout"
                        if isinstance(error, TimeoutError)
                        else "authorization_changed"
                        if isinstance(error, PermissionError)
                        else "build_failed",
                    )
            logger.warning(
                "wiki_build_failed",
                extra={
                    **failure_fields(
                        error, document_id, phase, snapshot.source_fingerprint if snapshot else None
                    ),
                    **(
                        {
                            "source_anchor_hash": fingerprint(error.source),
                            "source_window_start": error.start,
                            "source_window_end": error.end,
                        }
                        if isinstance(error, SentenceTooLarge)
                        else {}
                    ),
                },
            )
        finally:
            if context_token is not None:
                reset_audit_request_context(context_token)
        return False

    async def run_forever(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            await self.reconcile()
            with suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=get_settings().rag_wiki_poll_seconds)

    async def shutdown(self) -> None:
        tasks = [*self.reconciliations, *self.tasks.values()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.tasks.clear()
        self.reconciliations.clear()
        self.capacity = None


wiki_jobs = WikiJobs()
