"""Thin/Thick driver 初期化と DeepSec 用 control/data pool。"""

from __future__ import annotations

import importlib
import threading
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from functools import lru_cache
from typing import Any

from app.features.nl2sql.oracle_adapter import OracleAdapterError, oracle_connect_kwargs
from app.settings import Settings, get_settings


class OraclePoolManager:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._oracledb: Any | None = None
        self._control_pool: Any | None = None
        self._data_pool: Any | None = None
        self._lock = threading.RLock()

    def validate_deepsec_mode(self) -> None:
        if self.settings.oracle_deepsec_enabled and self.settings.oracle_driver_mode != "thin":
            raise OracleAdapterError(
                "Deep Data Security を有効にする場合は ORACLE_DRIVER_MODE=thin が必要です。"
            )
        if (
            self.settings.oracle_deepsec_enabled
            and not self.settings.oracle_deepsec_end_user_password
        ):
            raise OracleAdapterError("ORACLE_DEEPSEC_END_USER_PASSWORD を設定してください。")

    @contextmanager
    def control_connection(self) -> Iterator[Any]:
        pool = self._get_pool(data_plane=False)
        connection = pool.acquire()
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def data_connection(self, actor_user_id: str) -> Iterator[Any]:
        if not actor_user_id:
            raise OracleAdapterError("データ接続には認証済み actor_user_id が必要です。")
        with self._data_connection_raw() as connection:
            try:
                with connection.cursor() as cursor:
                    cursor.callproc("NL2SQL_DEEPSEC_CTX_PKG.SET_APP_USER", [actor_user_id])
                yield connection
            finally:
                self._clear_context_or_drop(connection)

    @contextmanager
    def unscoped_data_connection(self) -> Iterator[Any]:
        """DeepSec verification の no-context probe 専用。"""
        with self._data_connection_raw() as connection:
            self._clear_context_or_drop(connection)
            yield connection

    @contextmanager
    def _data_connection_raw(self) -> Iterator[Any]:
        self.validate_deepsec_mode()
        if not self.settings.oracle_deepsec_enabled:
            raise OracleAdapterError("Deep Data Security が有効ではありません。")
        pool = self._get_pool(data_plane=True)
        connection = pool.acquire()
        try:
            yield connection
        finally:
            connection.close()

    def close(self) -> None:
        with self._lock:
            for pool in (self._data_pool, self._control_pool):
                if pool is not None:
                    with suppress(Exception):
                        pool.close(force=True)
            self._data_pool = None
            self._control_pool = None

    def _get_pool(self, *, data_plane: bool) -> Any:
        with self._lock:
            current = self._data_pool if data_plane else self._control_pool
            if current is not None:
                return current
            oracledb = self._load_oracledb()
            self._initialize_driver(oracledb)
            if data_plane:
                kwargs = oracle_connect_kwargs(
                    self.settings,
                    user=self.settings.oracle_deepsec_end_user,
                    password=self.settings.oracle_deepsec_end_user_password,
                )
            else:
                kwargs = oracle_connect_kwargs(self.settings)
            kwargs.update(min=1, max=4, increment=1)
            pool = oracledb.create_pool(**kwargs)
            if data_plane:
                self._data_pool = pool
            else:
                self._control_pool = pool
            return pool

    def _load_oracledb(self) -> Any:
        if self._oracledb is None:
            self._oracledb = importlib.import_module("oracledb")
        return self._oracledb

    def _initialize_driver(self, oracledb: Any) -> None:
        if self.settings.oracle_driver_mode == "thin":
            return
        if not self.settings.oracle_client_lib_dir:
            return
        if getattr(oracledb, "is_thin_mode", lambda: True)():
            oracledb.init_oracle_client(lib_dir=self.settings.oracle_client_lib_dir)

    def _clear_context_or_drop(self, connection: Any) -> None:
        try:
            with connection.cursor() as cursor:
                cursor.callproc("NL2SQL_DEEPSEC_CTX_PKG.CLEAR_APP_USER")
        except Exception as exc:
            pool = self._data_pool
            if pool is not None:
                with suppress(Exception):
                    pool.drop(connection)
            raise OracleAdapterError(
                "DeepSec context を消去できないため接続を破棄しました。"
            ) from exc


@lru_cache
def get_oracle_pool_manager() -> OraclePoolManager:
    return OraclePoolManager(get_settings())


def close_oracle_pools() -> None:
    if get_oracle_pool_manager.cache_info().currsize:
        get_oracle_pool_manager().close()
    get_oracle_pool_manager.cache_clear()
