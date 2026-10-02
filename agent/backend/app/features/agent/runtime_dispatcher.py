"""組み込み Runtime の Run を claim/lease で実行する専用 worker（本番の別プロセス。#754）。

    python -m app.features.agent.runtime_dispatcher

`AGENT_RUNTIME_DISPATCH_MODE` が `in_process` 以外のとき、API は Run を queued で作るだけで、
この worker が Oracle の checkpoint の row lock で Run を claim して実行する。承認がすべて決まって
queued に戻った Run は、保存した SDK の状態から再開する。
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
from uuid import uuid4

from app.features.agent import builtin_runtime
from app.features.agent.runtime import builtin_resume_pending, runtime_repository
from app.settings import get_settings

logger = logging.getLogger(__name__)


async def dispatch_once(worker_id: str) -> bool:
    """queued の Run を 1 件 claim して実行する（無ければ False）。"""
    settings = get_settings()
    run = runtime_repository.claim_control_plane_run(
        worker_id,
        lease_seconds=settings.agent_runtime_dispatch_lease_seconds,
    )
    if run is None:
        return False
    if builtin_resume_pending(run):
        await builtin_runtime.resume_run(run.id)
    else:
        await builtin_runtime.execute_run(run.id)
    return True


async def run_forever() -> None:
    settings = get_settings()
    worker_id = os.environ.get(
        "AGENT_RUNTIME_DISPATCHER_ID",
        f"{socket.gethostname()}-{uuid4().hex[:8]}",
    )
    poll_seconds = max(0.1, settings.agent_runtime_dispatch_poll_seconds)
    logger.info("runtime-dispatcher started: %s", worker_id)
    while True:
        claimed = await dispatch_once(worker_id)
        if not claimed:
            await asyncio.sleep(poll_seconds)


def main() -> None:
    asyncio.run(run_forever())


if __name__ == "__main__":
    main()
