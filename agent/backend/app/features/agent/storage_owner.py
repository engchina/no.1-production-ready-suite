"""同じ保存先を使う API backend の単一所有者（#4216）。"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Any

from app.features.agent import storage_backend
from app.settings import get_settings

logger = logging.getLogger(__name__)
OWNER_CHECK_SECONDS = 1.0

OWNER_KEY = "__api_backend_owner__"
OWNER_MESSAGE = (
    "同じ保存先を使用する Agent の backend が既に起動しています。"
    "先の backend を停止するか、独立した保存先を指定してください。"
)


class StorageAlreadyOwnedError(RuntimeError):
    """別の backend が保存先を使用している。"""


class StorageNotInitializedError(RuntimeError):
    """排他の制御表がまだ作成されていない。"""


class StorageOwner:
    """API の全寿命で保持し、起動時の復元とスケジューラをまとめて排他にする。"""

    def __init__(self) -> None:
        self._connection: Any = None
        self._file: Any = None
        self._lock = threading.Lock()
        self.enabled = False
        self._stop = threading.Event()
        self._monitor: threading.Thread | None = None

    def acquire(self) -> None:
        with self._lock:
            if not self.enabled or self._connection is not None or self._file is not None:
                return
            backend = storage_backend.resolved_backend()
            if backend in storage_backend.ORACLE_BACKENDS:
                self._acquire_oracle()
            elif backend in storage_backend.FILE_BACKENDS:
                import fcntl

                configured = get_settings().agent_runtime_snapshot_path
                if not configured:
                    raise RuntimeError("file の保存先には AGENT_RUNTIME_SNAPSHOT_PATH が必要です。")
                path = Path(configured).expanduser().resolve()
                path.parent.mkdir(parents=True, exist_ok=True)
                handle = path.with_name(path.name + ".owner.lock").open("a")
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as exc:
                    handle.close()
                    raise StorageAlreadyOwnedError(OWNER_MESSAGE) from exc
                self._file = handle

    def _acquire_oracle(self) -> None:
        import oracledb
        from pr_backend_core.oracle_session import init_oracle_session

        from app.oracle_connection import platform_oracle_connect_kwargs

        # pool へ返すと行の lock が解けるため、API 専用の接続を保持する。
        connection = oracledb.connect(**platform_oracle_connect_kwargs(get_settings()))
        try:
            init_oracle_session(connection, None)
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT 1 FROM AGENT_SCHEMA_OPERATIONS WHERE operation_key = :key",
                    key=OWNER_KEY,
                )
                if cursor.fetchone() is None:
                    try:
                        cursor.execute(
                            "INSERT INTO AGENT_SCHEMA_OPERATIONS "
                            "(operation_key, status, schema_epoch) VALUES (:key, 'IDLE', 0)",
                            key=OWNER_KEY,
                        )
                        connection.commit()
                    except oracledb.IntegrityError:
                        connection.rollback()
                cursor.execute(
                    "SELECT operation_key FROM AGENT_SCHEMA_OPERATIONS "
                    "WHERE operation_key = :key FOR UPDATE NOWAIT",
                    key=OWNER_KEY,
                )
                if cursor.fetchone() is None:
                    raise RuntimeError("Agent の保存先の排他行がありません。")
            connection.call_timeout = 5000
            self._connection = connection
            self._stop.clear()
            self._monitor = threading.Thread(target=self._watch_connection, daemon=True)
            self._monitor.start()
        except Exception as exc:
            connection.close()
            if "ORA-00942" in str(exc):
                raise StorageNotInitializedError(
                    "Agent のシステムテーブルを作成してください。"
                ) from exc
            if "ORA-00054" in str(exc):
                raise StorageAlreadyOwnedError(OWNER_MESSAGE) from exc
            raise

    def _watch_connection(self) -> None:
        # 専用 session が切れれば Oracle は lock を解放する。旧 backend の実行を残さない。
        while not self._stop.wait(OWNER_CHECK_SECONDS):
            try:
                self._connection.ping()
            except Exception:
                logger.critical("agent_storage_owner_connection_lost")
                self._terminate()
                return

    @staticmethod
    def _terminate() -> None:
        os._exit(1)

    def close(self) -> None:
        self._stop.set()
        if self._monitor is not None:
            self._monitor.join(timeout=6)
            self._monitor = None
        with self._lock:
            self.enabled = False
            if self._connection is not None:
                self._connection.close()
                self._connection = None
            if self._file is not None:
                self._file.close()
                self._file = None


storage_owner = StorageOwner()
