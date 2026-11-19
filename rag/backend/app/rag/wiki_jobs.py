"""KB の指定を現在の権限で読み、検索を待たせず Wiki を増分構築する。"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from contextlib import suppress
from dataclasses import replace
from typing import Any

from pr_system_settings.auth.visibility import can_view_all_users_data

from app.clients.ai_calls import make_ai_caller
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
from app.rag.wiki_links import (
    WIKI_LINK_PROMPT,
    WIKI_LINK_SCHEMA_VERSION,
    WikiLinkDecision,
    WikiLinkItem,
    judge_links,
    link_query_text,
)
from app.rag.wiki_models import WIKI_SCHEMA_VERSION, WikiPage, fingerprint
from app.rag.wiki_navigation import interleave

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


def link_fingerprint(settings: Settings, compiler: str, knowledge_bases: Sequence[str]) -> str:
    """文書をまたぐ関連の構築の条件（#5492）。

    所属する KB が変われば候補の範囲が変わるので作り直す。
    """
    model_id = enterprise_ai_default_model_id(settings)
    return fingerprint(
        [
            WIKI_LINK_SCHEMA_VERSION,
            WIKI_LINK_PROMPT,
            compiler,
            model_id,
            settings.rag_wiki_link_candidates,
            settings.rag_wiki_link_batch_units,
            settings.rag_wiki_link_source_chars,
            sorted(knowledge_bases),
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

    def notify(self, kb_id: str | None = None) -> None:
        task = asyncio.create_task(self.reconcile(kb_id))
        self.reconciliations.add(task)
        task.add_done_callback(self.reconciliations.discard)

    async def reconcile(self, kb_id: str | None = None) -> None:
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
                        task = asyncio.create_task(self.build(did, owner))
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

    async def build(self, document_id: str, owner: str | None) -> None:
        """文書の案内を作り、公開できていれば文書をまたぐ関連を作る（関連の失敗は案内を止めない）。"""
        settings = get_settings()
        if self.capacity is None:
            self.capacity = asyncio.Semaphore(settings.rag_wiki_build_workers)
        async with self.capacity:
            if await self._build_pages(document_id, owner, settings):
                await self._build_links(document_id, owner, get_settings())

    async def _build_pages(self, document_id: str, owner: str | None, settings: Settings) -> bool:
        """文書の案内を作る。今の原文・構築条件で公開済み（``READY``）なら True。"""
        store = self.store_factory()
        snapshot, lease, context_token = None, None, None
        # この回で案内を保存したか。時間切れでも進んでいれば、次の照合で続きから作る。
        progressed = False
        try:
            context_token = set_audit_request_context(await self.actor(owner, settings))
            compiler = compiler_fingerprint(settings)
            revision = await store.revision_fingerprint(document_id)
            if revision is None:
                return False
            state = await store.document_state(document_id)
            if (
                state
                and state["compiler_fingerprint"] == compiler
                and state["revision_fingerprint"] == revision
            ):
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
            snapshot = replace(raw, compiler_fingerprint=compiler, revision_fingerprint=revision)
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
                return False
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
                nonlocal progressed
                await authorize()
                await store.checkpoint(
                    snapshot,
                    lease,
                    pages,
                    original_chars=settings.rag_wiki_embedding_source_chars,
                )
                progressed = True

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
                    caller=make_ai_caller(settings, "wiki_build"),
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
                        # 案内を 1 件も足せない時間切れだけ止める（毎回進むので終わる）。
                        failure_code=("interrupted" if progressed else "timeout")
                        if isinstance(error, TimeoutError)
                        else "authorization_changed"
                        if isinstance(error, PermissionError)
                        else "build_failed",
                    )
            logger.warning(
                "wiki_build_failed",
                extra={
                    "error_type": type(error).__name__,
                    # 共通の実行器の失敗は、最後の失敗の種類と HTTP の状態コードを持つ。
                    "error_kind": str(getattr(error, "kind", "") or ""),
                    "http_status": getattr(error, "status", None),
                },
            )
        finally:
            if context_token is not None:
                reset_audit_request_context(context_token)
        return False

    async def _build_links(self, document_id: str, owner: str | None, settings: Settings) -> None:
        """文書をまたぐ関連を作る（#5492）。状態・lease・checkpoint は案内と別に持つ。

        この文書から出る関連と、この文書の案内が候補に入るほかの文書の単位からこの文書への関連だけを
        作り、KB 全体は作り直さない。失敗しても文書の案内は ``READY`` のまま使える。
        """
        if settings.rag_wiki_link_candidates <= 0:
            return
        store = self.store_factory()
        token, context_token, progressed = None, None, False
        try:
            context_token = set_audit_request_context(await self.actor(owner, settings))
            compiler = compiler_fingerprint(settings)
            state = await store.document_state(document_id)
            if not state or state["status"] != "READY" or state["compiler_fingerprint"] != compiler:
                return
            source = str(state["source_fingerprint"])
            knowledge_bases = await store.link_knowledge_bases(document_id)
            if not knowledge_bases:
                return
            links = link_fingerprint(settings, compiler, knowledge_bases)
            current = await store.link_state(document_id)
            if (
                current
                and current["source_fingerprint"] == source
                and current["link_fingerprint"] == links
                and (
                    current["status"] == "READY"
                    or (current["status"] == "RUNNING" and current["leased"])
                    # 失敗は再試行の API（`requeue_failed`）が待機へ戻すまで作り直さない。
                    or (
                        current["status"] == "FAILED"
                        and current.get("failure_code") != "interrupted"
                    )
                )
            ):
                return
            claimed = await store.claim_links(
                document_id,
                source,
                links,
                lease_seconds=int(
                    max(
                        settings.rag_wiki_lease_seconds,
                        settings.rag_wiki_build_timeout_seconds + 60,
                    )
                ),
            )
            if claimed is None:
                return
            lease, done = claimed
            token = lease

            async def authorize() -> None:
                latest = get_settings()
                set_audit_request_context(await self.actor(owner, latest))
                now = await store.document_state(document_id)
                if (
                    compiler_fingerprint(latest) != compiler
                    or not now
                    or now["status"] != "READY"
                    or now["source_fingerprint"] != source
                    or await store.link_knowledge_bases(document_id) != knowledge_bases
                ):
                    raise PermissionError("Wiki の案内・構築設定・所属が変更されました。")

            async def checkpoint(
                batch: Sequence[WikiLinkItem],
                candidates: Sequence[str],
                decisions: Sequence[WikiLinkDecision],
            ) -> None:
                nonlocal progressed
                await authorize()
                await store.save_links(
                    document_id,
                    lease,
                    sources=[item.source.unit.unit_id for item in batch],
                    candidates=candidates,
                    links=[
                        (d.source_unit_id, d.target_unit_id, d.relation, d.reason)
                        for d in decisions
                    ],
                    done=[item.key for item in batch],
                )
                progressed = True

            async with asyncio.timeout(settings.rag_wiki_build_timeout_seconds):
                items = await self._link_items(store, document_id, knowledge_bases, settings)
                skip = set(done)
                model, caller = self.model_factory(settings), make_ai_caller(settings, "wiki_build")
                # 前向き（この文書 → ほか）と逆向き（ほか → この文書）は起点と候補が入れ替わるので、
                # 同じ判定に混ぜない。
                for group in items:
                    await judge_links(
                        [item for item in group if item.key not in skip],
                        model=model,
                        batch_size=settings.rag_wiki_link_batch_units,
                        original_chars=settings.rag_wiki_link_source_chars,
                        max_input_chars=settings.rag_wiki_build_input_chars,
                        authorize=authorize,
                        checkpoint=checkpoint,
                        caller=caller,
                    )
                await authorize()
                await store.finish_links(document_id, lease)
        except asyncio.CancelledError:
            if token:
                with suppress(Exception):
                    await store.finish_links(document_id, token, failure_code="interrupted")
            raise
        except Exception as error:
            if token:
                with suppress(Exception):
                    await store.finish_links(
                        document_id,
                        token,
                        failure_code=("interrupted" if progressed else "timeout")
                        if isinstance(error, TimeoutError)
                        else "authorization_changed"
                        if isinstance(error, PermissionError)
                        else "build_failed",
                    )
            logger.warning(
                "wiki_link_failed",
                extra={
                    "error_type": type(error).__name__,
                    "error_kind": str(getattr(error, "kind", "") or ""),
                    "http_status": getattr(error, "status", None),
                },
            )
        finally:
            if context_token is not None:
                reset_audit_request_context(context_token)

    async def _link_items(
        self, store: Any, document_id: str, knowledge_bases: Sequence[str], settings: Settings
    ) -> tuple[list[WikiLinkItem], list[WikiLinkItem]]:
        """前向きと逆向きの候補の組を DB だけで作る（モデルは呼ばない）。

        起点の単位ごとに、属する KB ごとのほかの文書の案内をベクトル・全文で探し、上位を候補にする。
        逆向きは、候補になったほかの文書の単位を起点に、その単位を見つけたこの文書の単位を候補にする。
        """
        compiler, limit = compiler_fingerprint(settings), settings.rag_wiki_link_candidates
        own = await store.pages(
            await store.resolve(
                {
                    "knowledge_base_id": ",".join(knowledge_bases),
                    "document_id": document_id,
                    "include_superseded": "true",
                },
                compiler,
            )
        )
        own = [page for page in own if link_query_text(page)]
        # KB ごとの範囲は 1 回だけ決め、起点の単位ごとの候補の検索で使い回す（#5499）。
        scopes = [
            await store.resolve({"knowledge_base_id": kb, "include_superseded": "true"}, compiler)
            for kb in (knowledge_bases if own else ())
        ]
        texts = [link_query_text(page) for page in own]
        embedder = self.embedder_factory(settings)
        vectors: list[Sequence[float]] = []
        step = settings.rag_wiki_build_batch_units
        for offset in range(0, len(texts), step):
            vectors.extend(
                await embedder.embed(texts[offset : offset + step], input_type="SEARCH_QUERY")
            )
        forward: list[WikiLinkItem] = []
        found_by: dict[str, tuple[WikiPage, list[tuple[int, WikiPage]]]] = {}
        for page, text, vector in zip(own, texts, vectors, strict=True):
            groups: list[list[WikiPage]] = []
            for scope in scopes:
                near, ranking = await asyncio.gather(
                    store.pages(
                        scope,
                        query_vector=vector,
                        limit=limit,
                        exclude_document_ids=[document_id],
                    ),
                    store.text_rankings(
                        text, scope, limit=limit, exclude_document_ids=[document_id]
                    ),
                )
                texts_found = await store.pages(scope, unit_ids=[uid for _, uid in ranking])
                by_id = {found.unit.unit_id: found for found in texts_found}
                groups.extend([near, [by_id[uid] for _, uid in ranking if uid in by_id]])
            candidates = [
                found
                for found in interleave(groups, limit)
                if found.unit.document_id != document_id
            ]
            if not candidates:
                continue
            forward.append(WikiLinkItem(f"forward:{page.unit.unit_id}", page, tuple(candidates)))
            for rank, found in enumerate(candidates):
                found_by.setdefault(found.unit.unit_id, (found, []))[1].append((rank, page))
        reverse = [
            WikiLinkItem(
                f"reverse:{uid}",
                found,
                tuple(page for _, page in sorted(sources, key=lambda entry: entry[0])[:limit]),
            )
            for uid, (found, sources) in found_by.items()
        ]
        return forward, reverse

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
