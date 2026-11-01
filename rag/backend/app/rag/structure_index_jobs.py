"""構造索引の補建。評価jobと同じく要求の権限を引き継ぎ、状態はOracleへ保存する。"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import UTC, datetime
from functools import partial
from typing import Any
from uuid import uuid4

from app.clients.oracle import OracleClient
from app.clients.structure_index_store import ACTIVE, StructureIndexStore
from app.rag.structure_index import rebuild_document_structure_index, structure_index_plan

REASONS = {
    "no_chunks",
    "no_structure",
    "source_changed",
    "disabled",
    "membership_changed",
    "invalid_chunk_id",
    "authorization_changed",
    "interrupted",
}


async def refresh_actor() -> Any:
    """文書ごとに現在の利用者/ロールを読み直し、失効した権限で続行しない。"""
    from app.config import get_settings
    from app.rag.request_context import (
        audit_request_context_for_principal,
        current_audit_request_context,
    )
    from app.security.domain import as_principal
    from app.security.permissions import MENU_KNOWLEDGE_BASES
    from app.security.service import get_security_service

    context = current_audit_request_context()
    if get_settings().local_debug_enabled:
        return context
    if not context.user_uuid:
        raise ValueError("authorization_changed")
    principal = as_principal(
        await asyncio.to_thread(get_security_service().principal_for_worker, context.user_uuid)
    )
    if MENU_KNOWLEDGE_BASES not in principal.permissions:
        raise ValueError("authorization_changed")
    return audit_request_context_for_principal(
        {},
        request_id=context.request_id or "",
        user_uuid=context.user_uuid,
        allowed_search_answer_profile_ids=principal.allowed_search_answer_profile_ids,
        allowed_knowledge_base_ids=principal.allowed_knowledge_base_ids,
        all_users_visible=context.all_users_visible,
    )


def now() -> str:
    return datetime.now(UTC).isoformat()


def public_state(
    state: dict[str, Any] | None, offset: int = 0, limit: int = 25
) -> dict[str, Any] | None:
    if state is None:
        return None
    result = {key: value for key, value in state.items() if key != "items"}
    items = state["items"]
    result["counts"] = {
        status: sum(item["status"] == status for item in items)
        for status in (
            "QUEUED",
            "RUNNING",
            "SUCCEEDED",
            "PARTIAL",
            "FAILED",
            "SKIPPED",
            "UNAVAILABLE",
        )
    }
    result.update(
        total=len(items),
        offset=offset,
        limit=limit,
        items=[
            {
                k: v
                for k, v in item.items()
                if k not in {"chunk_sets", "build_chunk_sets", "fingerprint"}
            }
            for item in items[offset : offset + limit]
        ],
    )
    return result


class StructureIndexJobs:
    def __init__(self) -> None:
        self.tasks: set[asyncio.Task[None]] = set()

    async def start(self, oracle: OracleClient, kb_id: str, mode: str) -> dict[str, Any]:
        store = StructureIndexStore(oracle)
        kb = await oracle.get_knowledge_base(kb_id)
        if kb is None:
            raise KeyError("ナレッジベースが見つかりません。")
        if kb.retrieval_config.get("structure_index_enabled") is False:
            raise ValueError("文書構造索引の構築設定がオフです。")
        previous = await store.get(kb_id)
        if mode in {"retry", "continue"} and previous is None:
            raise ValueError("再開する構築の記録がありません。")
        plan = await structure_index_plan(oracle, kb_id)
        if mode in {"retry", "continue"}:
            assert previous is not None
            wanted = {
                item["document_id"]
                for item in previous["items"]
                if item["status"]
                in ({"FAILED"} if mode == "retry" else {"QUEUED", "RUNNING", "FAILED"})
            }
            plan = [item for item in plan if item["document_id"] in wanted]
        for item in plan:
            if item["status"] == "UNAVAILABLE":
                continue
            if item["status"] == "CURRENT" and mode != "rebuild":
                item.update(status="SKIPPED", reason="current")
            else:
                item.update(status="QUEUED", reason=None)
        state: dict[str, Any] = {
            "job_id": uuid4().hex,
            "status": "RUNNING",
            "started_at": now(),
            "finished_at": None,
            "items": plan,
        }
        state = await store.mutate(kb_id, lambda _: state, start=True)
        task = asyncio.create_task(self.run(oracle, kb_id, state["job_id"]))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return state

    async def run(self, oracle: OracleClient, kb_id: str, job_id: str) -> None:
        store = StructureIndexStore(oracle)

        def current(state: dict[str, Any] | None) -> dict[str, Any]:
            if state is None or state["job_id"] != job_id:
                raise LookupError("構築の実行が変更されました。")
            return state

        async def heartbeat() -> None:
            while True:
                await asyncio.sleep(10)
                await store.mutate(kb_id, current)

        heartbeat_task = asyncio.create_task(heartbeat())
        try:
            while True:
                state = await store.mutate(kb_id, current)
                if state["status"] != "RUNNING":
                    break
                item = next((item for item in state["items"] if item["status"] == "QUEUED"), None)
                if item is None:
                    break
                did = item["document_id"]

                def update(
                    state: dict[str, Any] | None,
                    status: str,
                    reason: str | None = None,
                    result: Any = None,
                    did: str = did,
                ) -> dict[str, Any]:
                    state = current(state)
                    selected = next(i for i in state["items"] if i["document_id"] == did)
                    selected.update(status=status, reason=reason, result=result)
                    return state

                # 停止が先にcommitされていたら、次の文書を開始しない。
                state = await store.mutate(
                    kb_id,
                    lambda s: (
                        update(s, "RUNNING") if current(s)["status"] == "RUNNING" else current(s)
                    ),
                )
                if state["status"] != "RUNNING":
                    break
                try:
                    from app.rag.request_context import (
                        reset_audit_request_context,
                        set_audit_request_context,
                    )

                    actor = await refresh_actor()
                    token = set_audit_request_context(actor)
                    try:
                        result = await rebuild_document_structure_index(
                            oracle,
                            kb_id,
                            did,
                            expected_source=item["chunk_sets"],
                            expected_job_id=job_id,
                            build_chunk_sets=item.get("build_chunk_sets"),
                        )
                    finally:
                        reset_audit_request_context(token)
                    status = "PARTIAL" if result["quality"] == "PARTIAL" else "SUCCEEDED"
                    await store.mutate(
                        kb_id,
                        partial(update, status=status, result=result),
                    )
                except asyncio.CancelledError:
                    await store.mutate(kb_id, lambda s: update(s, "FAILED", "interrupted"))
                    raise
                except Exception as exc:  # noqa: BLE001 - 文書単位の失敗は残し、残りを続ける。
                    reason = (
                        str(exc)
                        if isinstance(exc, ValueError) and str(exc) in REASONS
                        else "processing_error"
                    )
                    await store.mutate(kb_id, partial(update, status="FAILED", reason=reason))

            def finish(state: dict[str, Any] | None) -> dict[str, Any]:
                state = current(state)
                if state["status"] in ACTIVE:
                    state.update(
                        status="STOPPED" if state["status"] == "STOPPING" else "COMPLETED",
                        finished_at=now(),
                    )
                return state

            await store.mutate(kb_id, finish)
        except asyncio.CancelledError:

            def interrupted(state: dict[str, Any] | None) -> dict[str, Any]:
                state = current(state)
                state.update(status="INTERRUPTED", finished_at=now())
                return state

            await store.mutate(kb_id, interrupted)
            raise
        except Exception:  # noqa: BLE001 - DB障害などはheartbeatの期限切れで再開可能になる。
            return
        finally:
            heartbeat_task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await heartbeat_task

    async def stop(self, oracle: OracleClient, kb_id: str) -> dict[str, Any]:
        def change(state: dict[str, Any] | None) -> dict[str, Any]:
            if state is None:
                raise KeyError("構築の記録が見つかりません。")
            if state["status"] in ACTIVE:
                state["status"] = "STOPPING"
            return state

        return await StructureIndexStore(oracle).mutate(kb_id, change)

    async def shutdown(self) -> None:
        tasks = list(self.tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


structure_index_jobs = StructureIndexJobs()
