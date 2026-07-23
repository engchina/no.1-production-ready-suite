"""Oracle 永続 queue から NL2SQL 品質評価 job を実行する worker。"""

from __future__ import annotations

import logging
import signal
import socket
import time

from app.features.nl2sql.quality_evaluation_service import quality_evaluation_service
from app.settings import get_settings

logger = logging.getLogger(__name__)
_running = True


def _stop(_signum: int, _frame: object) -> None:
    global _running  # noqa: PLW0603
    _running = False


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = get_settings()
    worker_id = f"{socket.gethostname()}:quality-evaluation"
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    logger.info("quality_evaluation_worker_started", extra={"worker_id": worker_id})
    while _running:
        processed = quality_evaluation_service.run_next_job(worker_id=worker_id)
        if not processed:
            time.sleep(max(0.1, settings.nl2sql_quality_evaluation_worker_poll_seconds))
    logger.info("quality_evaluation_worker_stopped", extra={"worker_id": worker_id})


if __name__ == "__main__":
    main()
