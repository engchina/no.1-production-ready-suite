"""アプリとしての Oracle AI Database 接続（共通 `.env` の `PLATFORM_ORACLE_*`。Thin mode。#215）。

- システム設定 > データベース の接続テスト（`features/agent/router.py`）・共通認証の store
  （`security/store.py`）・システムテーブル（`system_schema.py`）・Run の repository
  （`features/agent/runtime.py`）・定義の保存（`features/agent/control_plane_store.py`）が使う。
  Run の repository の別の接続設定（旧 `AGENT_RUNTIME_ORACLE_*`）は #764 で削除した。
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace
from typing import Any


def oracle_connect_kwargs(settings: Any) -> dict[str, object]:
    """`oracledb.connect` の引数（Thin mode。Wallet があれば mTLS）。"""
    kwargs: dict[str, object] = {
        "user": settings.oracle_user,
        "dsn": settings.oracle_dsn,
        "retry_count": 0,
        "retry_delay": 0,
    }
    tcp_connect_timeout = float(settings.oracle_tcp_connect_timeout_seconds)
    if tcp_connect_timeout > 0:
        kwargs["tcp_connect_timeout"] = tcp_connect_timeout
    if str(settings.oracle_password or "").strip():
        kwargs["password"] = settings.oracle_password
    wallet_dir = str(settings.oracle_wallet_dir or "").strip()
    if wallet_dir:
        kwargs["config_dir"] = str(Path(wallet_dir).expanduser())
        kwargs["wallet_location"] = str(Path(wallet_dir).expanduser())
    if str(settings.oracle_wallet_password or "").strip():
        kwargs["wallet_password"] = settings.oracle_wallet_password
    return kwargs


def platform_oracle_connect_kwargs(settings: Any) -> dict[str, object]:
    """Settings（`PLATFORM_ORACLE_*`）から接続引数を作る。Wallet は解決済みの配置先を使う。"""
    dsn = str(settings.oracle_dsn or "").strip()
    user = str(settings.oracle_user or "").strip()
    if not dsn or not user:
        raise RuntimeError(
            "PLATFORM_ORACLE_DSN と PLATFORM_ORACLE_USER を"
            "共通 .env（platform/.env）に設定してください。"
        )
    return oracle_connect_kwargs(
        SimpleNamespace(
            oracle_user=user,
            oracle_dsn=dsn,
            oracle_password=settings.oracle_password,
            oracle_wallet_dir=settings.resolved_oracle_wallet_dir,
            oracle_wallet_password=settings.oracle_wallet_password,
            oracle_tcp_connect_timeout_seconds=settings.oracle_tcp_connect_timeout_seconds,
        )
    )


@contextmanager
def platform_oracle_connection() -> Iterator[Any]:
    """`PLATFORM_ORACLE_*` の接続（未コミットの変更を持たない。例外時は rollback して閉じる）。"""
    from app.settings import get_settings

    oracledb = import_module("oracledb")
    connection = oracledb.connect(**platform_oracle_connect_kwargs(get_settings()))
    try:
        yield connection
    except BaseException:
        try:
            connection.rollback()
        finally:
            connection.close()
        raise
    else:
        connection.close()


def connect_platform_oracle() -> Any:
    """`PLATFORM_ORACLE_*` の接続を開く（呼び出し側が `with` で閉じ、commit する）。"""
    from pr_backend_core.oracle_session import init_oracle_session

    from app.settings import get_settings

    oracledb = import_module("oracledb")
    connection = oracledb.connect(**platform_oracle_connect_kwargs(get_settings()))
    init_oracle_session(connection)
    return connection
