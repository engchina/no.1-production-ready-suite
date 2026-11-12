"""KB の指定を現在の権限で読み、検索を待たせず Wiki を増分構築する。"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from contextlib import suppress
from dataclasses import replace
from typing import Any

from pr_system_settings.auth.visibility import can_view_all_users_data

from app.clients.oci_enterprise_ai import OciEnterpriseAiClient
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
from app.rag.wiki_compiler import WIKI_CONTEXT_PROMPT, compile_pages
from app.rag.wiki_models import WIKI_SCHEMA_VERSION, WikiPage, fingerprint

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

    def notify(self, kb_id: str | None = None, *, retry: bool = False) -> None:
        task = asyncio.create_task(self.reconcile(kb_id, retry=retry))
        self.reconciliations.add(task)
        task.add_done_callback(self.reconciliations.discard)

    async def reconcile(self, kb_id: str | None = None, *, retry: bool = False) -> None:
        if self.default_store and not get_settings().oracle_dsn.strip():
            return
        try:
            store = self.store_factory()
            # 起動後の復旧は ID と構築を指定した利用者だけを読み、本文はその利用者で読む。
            with system_access_context():
                policies = await store.enabled_knowledge_bases()
            settings = get_settings()
            if self.capacity is None:
                self.capacity = asyncio.Semaphore(settings.rag_wiki_build_workers)
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
                        if did in self.tasks and not self.tasks[did].done():
                            continue
                        task = asyncio.create_task(self.build(did, owner, retry=retry))
                        self.tasks[did] = task

                        def finished(completed: asyncio.Task[None], did: str = did) -> None:
                            if self.tasks.get(did) is completed:
                                self.tasks.pop(did, None)

                        task.add_done_callback(finished)
                finally:
                    reset_audit_request_context(token)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            # schema 未更新や接続失敗も、通常の検索・起動を止めない。
            logger.warning("wiki_reconcile_failed", extra={"error_type": type(error).__name__})

    async def build(self, document_id: str, owner: str | None, *, retry: bool = False) -> None:
        settings = get_settings()
        if self.capacity is None:
            self.capacity = asyncio.Semaphore(settings.rag_wiki_build_workers)
        async with self.capacity:
            store = self.store_factory()
            snapshot, lease, context_token = None, None, None
            try:
                context_token = set_audit_request_context(await self.actor(owner, settings))
                compiler = compiler_fingerprint(settings)
                revision = await store.revision_fingerprint(document_id)
                if revision is None:
                    return
                state = await store.document_state(document_id)
                if (
                    state
                    and state["compiler_fingerprint"] == compiler
                    and state["revision_fingerprint"] == revision
                ):
                    if state["status"] == "READY" or (
                        state["status"] == "RUNNING" and state["leased"]
                    ):
                        return
                    if (
                        state["status"] == "FAILED"
                        and state.get("failure_code") != "interrupted"
                        and not retry
                        and not (
                            state.get("failure_code") == "authorization_changed"
                            and state.get("owner_user_uuid") != owner
                        )
                    ):
                        return
                raw = await store.source_units(document_id)
                if raw is None:
                    return
                snapshot = replace(
                    raw, compiler_fingerprint=compiler, revision_fingerprint=revision
                )
                await store.enqueue(snapshot)
                lease = await store.claim(
                    snapshot,
                    lease_seconds=int(
                        max(
                            settings.rag_wiki_lease_seconds,
                            settings.rag_wiki_build_timeout_seconds + 60,
                        )
                    ),
                )
                if lease is None:
                    return
                saved = await store.saved_pages(snapshot)
                expected_revision, expected_compiler = revision, compiler

                async def authorize() -> None:
                    current = get_settings()
                    set_audit_request_context(await self.actor(owner, current))
                    if (
                        compiler_fingerprint(current) != expected_compiler
                        or await store.revision_fingerprint(document_id) != expected_revision
                    ):
                        raise PermissionError("Wiki の構築設定・原文・所属が変更されました。")

                async def checkpoint(pages: Sequence[WikiPage]) -> None:
                    await authorize()
                    await store.checkpoint(
                        snapshot,
                        lease,
                        pages,
                        original_chars=settings.rag_wiki_embedding_source_chars,
                    )

                async with asyncio.timeout(settings.rag_wiki_build_timeout_seconds):
                    pages = await compile_pages(
                        snapshot.units,
                        model=self.model_factory(settings),
                        model_id=enterprise_ai_default_model_id(settings),
                        max_input_chars=settings.rag_wiki_build_input_chars,
                        batch_size=settings.rag_wiki_build_batch_units,
                        saved=saved,
                        authorize=authorize,
                        checkpoint=checkpoint,
                    )
                    embedder = self.embedder_factory(settings)
                    for offset in range(0, len(pages), settings.rag_wiki_build_batch_units):
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
                        await store.checkpoint(
                            snapshot,
                            lease,
                            batch,
                            vectors=vectors,
                            original_chars=settings.rag_wiki_embedding_source_chars,
                        )
                    await authorize()
                    await store.finish(snapshot, lease)
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
                            failure_code="timeout"
                            if isinstance(error, TimeoutError)
                            else "authorization_changed"
                            if isinstance(error, PermissionError)
                            else "build_failed",
                        )
                logger.warning("wiki_build_failed", extra={"error_type": type(error).__name__})
            finally:
                if context_token is not None:
                    reset_audit_request_context(context_token)

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
