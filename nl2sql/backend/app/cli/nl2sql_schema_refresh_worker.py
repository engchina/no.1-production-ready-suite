"""Lease 付き永続 schema refresh job worker。"""

from __future__ import annotations

import argparse
import time

from app.features.nl2sql.service import nl2sql_service


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="1 件だけ確認して終了する")
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    args = parser.parse_args()

    while True:
        processed = nl2sql_service.run_next_schema_refresh_job()
        if args.once:
            return 0
        if not processed:
            time.sleep(max(0.2, args.poll_seconds))


if __name__ == "__main__":  # pragma: no cover - CLI boundary
    raise SystemExit(main())
