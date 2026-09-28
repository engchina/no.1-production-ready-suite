"""取込 job を 1 件だけ実行する subprocess 用 entrypoint。"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import signal
from collections.abc import Sequence

from app.clients.oracle import close_oracle_pool
from app.config import get_settings
from app.logging_config import configure_logging

logger = logging.getLogger(__name__)

# SIGTERM で止めたときの終了コード(シェルの慣例 128 + SIGTERM)。
TERMINATED_EXIT_CODE = 128 + int(signal.SIGTERM)


async def _run(job_id: str, *, lease_owner: str | None = None) -> None:
    # SIGTERM / SIGINT で job の実行(coroutine)を取り消し、finally の後始末(DB pool の close)を
    # 通してから終了する。既定の SIGTERM は後始末なしで即座に終了する。job の状態は書かない:
    # 親の worker が、停止のときは自分の lease の job を QUEUED に戻し、それ以外は失敗にする(#357)。
    task = asyncio.current_task()
    if task is not None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            with contextlib.suppress(NotImplementedError):  # pragma: no cover - Windows 等
                loop.add_signal_handler(sig, task.cancel)

    # FastAPI app/lifespan は起動せず、job 実行関数だけを遅延 import する。
    from app.api.routes.documents import _run_ingestion_job

    await _run_ingestion_job(job_id, lease_owner=lease_owner)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a queued ingestion job once.")
    parser.add_argument("job_id", help="rag_ingestion_jobs.id")
    parser.add_argument(
        "--lease-owner",
        default=None,
        help="job を実行する取込 worker の識別子(claim で lease を取る。#357)",
    )
    args = parser.parse_args(argv)

    settings = get_settings()
    configure_logging(settings.log_level)
    try:
        asyncio.run(_run(args.job_id, lease_owner=args.lease_owner))
    except asyncio.CancelledError:
        logger.warning("ingestion_job_runner_terminated", extra={"job_id": args.job_id})
        return TERMINATED_EXIT_CODE
    finally:
        close_oracle_pool()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
