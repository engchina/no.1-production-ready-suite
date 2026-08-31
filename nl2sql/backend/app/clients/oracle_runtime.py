"""Oracle driver 初期化と DeepSec 用 Thin-only control/data pool。"""

from __future__ import annotations

import importlib
import logging
import re
import threading
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from functools import lru_cache
from typing import Any

from app.features.nl2sql.oracle_adapter import (
    OracleAdapterError,
    ensure_deepsec_thin_mode,
    oracle_connect_kwargs,
)
from app.settings import Settings, get_settings

logger = logging.getLogger(__name__)
_ORACLE_INVALID_CREDENTIAL_RE = re.compile(r"\bORA-01017\b", re.IGNORECASE)
DEEPSEC_DATA_USER_INVALID_CREDENTIAL_MESSAGE = (
    "DeepSec DATA USER の Oracle ログインに失敗しました。Deep Data Security 画面で "
    "DATA USER パスワードを保存し直し、Oracle END USER へ同期してください。"
    "解消しない場合は DATA USER 認証の「Oracle へ同期」を再実行してください。"
)


class OraclePoolManager:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._oracledb: Any | None = None
        self._control_pool: Any | None = None
        self._data_pool: Any | None = None
        self._lock = threading.RLock()

    def validate_deepsec_configuration(self) -> None:
        """共有 DATA USER の direct logon に必要な DeepSec 設定を検証する。"""
        ensure_deepsec_thin_mode(self.settings)
        if (
            self.settings.oracle_deepsec_enabled
            and not self.settings.oracle_deepsec_data_user_password
        ):
            raise OracleAdapterError("ORACLE_DEEPSEC_DATA_USER_PASSWORD を設定してください。")

    def validate_deepsec_control_configuration(self) -> None:
        """DeepSec 管理 DDL 用の control-plane 設定を検証する。"""
        ensure_deepsec_thin_mode(self.settings)

    def validate_data_user_login(self) -> None:
        """保存済み DATA USER 認証情報で direct logon できることだけを検証する。"""
        ensure_deepsec_thin_mode(self.settings)
        if not self.settings.oracle_deepsec_data_user_password:
            raise OracleAdapterError("ORACLE_DEEPSEC_DATA_USER_PASSWORD を設定してください。")
        connection: Any | None = None
        pool: Any | None = None
        try:
            with self._lock:
                oracledb = self._load_oracledb()
                self._initialize_driver(oracledb)
                kwargs = oracle_connect_kwargs(
                    self.settings,
                    user=self.settings.oracle_deepsec_data_user,
                    password=self.settings.oracle_deepsec_data_user_password,
                )
                kwargs.update(min=1, max=1, increment=1)
                pool = oracledb.create_pool(**kwargs)
            connection = pool.acquire()
        except Exception as exc:
            if _is_invalid_data_user_credential_error(exc):
                raise OracleAdapterError(DEEPSEC_DATA_USER_INVALID_CREDENTIAL_MESSAGE) from exc
            raise
        finally:
            if connection is not None:
                with suppress(Exception):
                    connection.close()
            if pool is not None:
                with suppress(Exception):
                    pool.close(force=True)

    @contextmanager
    def control_connection(self) -> Iterator[Any]:
        pool = self._get_pool(data_plane=False)
        connection = pool.acquire()
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def data_connection(self, actor_user_uuid: str) -> Iterator[Any]:
        if not actor_user_uuid:
            raise OracleAdapterError("データ接続には認証済み actor_user_uuid が必要です。")
        with self._data_connection_raw() as connection:
            try:
                with connection.cursor() as cursor:
                    cursor.callproc("NL2SQL_DEEPSEC_CTX_PKG.SET_APP_USER_UUID", [actor_user_uuid])
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
        self.validate_deepsec_configuration()
        if not self.settings.oracle_deepsec_enabled:
            raise OracleAdapterError("Deep Data Security が有効ではありません。")
        pool = self._get_pool(data_plane=True)
        connection = pool.acquire()
        try:
            yield connection
        finally:
            try:
                connection.close()
            except Exception:
                logger.warning("oracle_data_connection_close_failed", exc_info=True)
                self._drop_connection_once(pool, connection)

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
            ensure_deepsec_thin_mode(self.settings)
            oracledb = self._load_oracledb()
            self._initialize_driver(oracledb)
            if data_plane:
                kwargs = oracle_connect_kwargs(
                    self.settings,
                    user=self.settings.oracle_deepsec_data_user,
                    password=self.settings.oracle_deepsec_data_user_password,
                )
            else:
                kwargs = oracle_connect_kwargs(self.settings)
            kwargs.update(min=1, max=4, increment=1)
            try:
                pool = oracledb.create_pool(**kwargs)
            except Exception as exc:
                if data_plane and _is_invalid_data_user_credential_error(exc):
                    raise OracleAdapterError(DEEPSEC_DATA_USER_INVALID_CREDENTIAL_MESSAGE) from exc
                raise
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
        ensure_deepsec_thin_mode(self.settings)
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
            logger.warning("oracle_deepsec_context_clear_failed", exc_info=True)
            pool = self._data_pool
            if pool is not None:
                self._drop_connection_once(pool, connection)
            raise OracleAdapterError(
                "DeepSec context を消去できないため接続を破棄しました。"
            ) from exc

    def _drop_connection_once(self, pool: Any, connection: Any) -> None:
        marker = "_nl2sql_oracle_pool_dropped"
        if getattr(connection, marker, False):
            return
        with suppress(Exception):
            setattr(connection, marker, True)
        with suppress(Exception):
            pool.drop(connection)


def _is_invalid_data_user_credential_error(exc: Exception) -> bool:
    return bool(_ORACLE_INVALID_CREDENTIAL_RE.search(str(exc)))


@lru_cache
def get_oracle_pool_manager() -> OraclePoolManager:
    return OraclePoolManager(get_settings())


def close_oracle_pools() -> None:
    if get_oracle_pool_manager.cache_info().currsize:
        get_oracle_pool_manager().close()
    get_oracle_pool_manager.cache_clear()
