"""合成データ生成 worker。受理済み操作のみ実行し不明な実行は対照する。"""

import logging
import signal
import threading

from app.features.nl2sql.synthetic_service import worker_loop


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    worker_loop(stop)


if __name__ == "__main__":
    main()
