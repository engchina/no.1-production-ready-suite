"""取込 job を 1 件だけ実行する subprocess 用 entrypoint。"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence

from app.clients.oracle import close_oracle_pool
from app.config import get_settings
from app.logging_config import configure_logging


async def _run(job_id: str) -> None:
    # FastAPI app/lifespan は起動せず、job 実行関数だけを遅延 import する。
    from app.api.routes.documents import _run_ingestion_job

    await _run_ingestion_job(job_id)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a queued ingestion job once.")
    parser.add_argument("job_id", help="rag_ingestion_jobs.id")
    args = parser.parse_args(argv)

    settings = get_settings()
    configure_logging(settings.log_level)
    try:
        asyncio.run(_run(args.job_id))
    finally:
        close_oracle_pool()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
