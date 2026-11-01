"""索引整理の永続要求を消費する。業務のcommitと結果を混同しない。"""

from __future__ import annotations

import asyncio
import logging
import math
from contextlib import suppress
from dataclasses import dataclass
from typing import Literal, Protocol

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class MaintenanceLease:
    owner: str
    token: str
    event_ids: tuple[str, ...]


class MaintenanceStore(Protocol):
    async def claim(self, owner: str) -> MaintenanceLease | None: ...

    async def optimize(self) -> None: ...

    async def heartbeat(self, lease: MaintenanceLease) -> bool: ...

    async def complete(self, lease: MaintenanceLease) -> bool: ...

    async def fail(self, lease: MaintenanceLease, error_type: str) -> None: ...


MaintenanceOutcome = Literal["idle", "complete", "failed", "lease_lost"]


async def run_maintenance_once(
    store: MaintenanceStore,
    owner: str,
    *,
    heartbeat_seconds: float = 30,
) -> MaintenanceOutcome:
    """取得時にcommit済みだった要求だけを完了する。後着は次回へ残す。

    単調増加IDの最大値以下を一括削除すると、後からcommitされた小さいIDも
    失うため、取得したIDのsnapshotとlease tokenを完了時にも照合する。
    """
    if not math.isfinite(heartbeat_seconds) or heartbeat_seconds <= 0:
        raise ValueError("索引整理のheartbeat間隔は有限の正数で指定してください。")
    lease = await store.claim(owner)
    if lease is None:
        return "idle"
    stop_heartbeat = asyncio.Event()
    lease_lost = False

    async def heartbeat() -> None:
        nonlocal lease_lost
        while True:
            try:
                await asyncio.wait_for(stop_heartbeat.wait(), heartbeat_seconds)
                return
            except TimeoutError:
                try:
                    if await store.heartbeat(lease):
                        continue
                except Exception as exc:
                    logger.warning(
                        "native_index_maintenance_heartbeat_failed",
                        extra={"error_type": type(exc).__name__},
                    )
                lease_lost = True
                return

    heartbeat_task = asyncio.create_task(heartbeat())
    try:
        try:
            await store.optimize()
        except Exception as exc:
            # SQLや接続先を含み得る例外本文はログや状態へ保存しない。
            await store.fail(lease, type(exc).__name__)
            logger.warning(
                "native_index_maintenance_failed", extra={"error_type": type(exc).__name__}
            )
            return "failed"
        if lease_lost or not await store.complete(lease):
            return "lease_lost"
        return "complete"
    finally:
        # 取消時も要求を完了しない。leaseの失効後に別workerが再取得できる。
        stop_heartbeat.set()
        heartbeat_task.cancel()
        with suppress(asyncio.CancelledError):
            await heartbeat_task
