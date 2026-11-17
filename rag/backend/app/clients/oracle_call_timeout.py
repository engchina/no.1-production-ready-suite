"""DB の呼出し 1 回の上限（python-oracledb の ``call_timeout``。#5381）。

``db_call_timeout(seconds)`` の中で ``OracleClient`` が共有 pool から借りた接続の SQL の実行
（``_run_with_connection`` / ``_run_transaction_sync``）は、1 回の呼出しが ``seconds`` 秒を超えると
driver が DB へ中断を送り、``DPY-4024`` で失敗する。DB の側でも SQL が止まり、接続は pool へ返る
（待つ側の ``asyncio`` の打ち切りだけでは thread・SQL・接続が SQL の終わりまで残る）。

- 範囲は ``contextvars`` で渡す（``asyncio.to_thread`` と回答の専用の thread の中の SQL にも効く）。
- 借りた接続の元の値は、pool へ返す前に戻す（範囲の外の取込・保守などの長い呼出しに残さない）。
- 接続にすでにより短い上限があれば、短いほうを使う。
"""

from __future__ import annotations

import math
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from contextvars import ContextVar

_DB_CALL_TIMEOUT_MS: ContextVar[int | None] = ContextVar(
    "rag_oracle_db_call_timeout_ms", default=None
)


def current_db_call_timeout_ms() -> int | None:
    """いまの範囲の DB の呼出し 1 回の上限（ミリ秒。上限が無ければ None）。"""
    return _DB_CALL_TIMEOUT_MS.get()


@contextmanager
def db_call_timeout(seconds: float | None) -> Iterator[None]:
    """この中の DB の呼出し 1 回を ``seconds`` 秒までにする（None・0 以下は上限なし）。"""
    milliseconds = max(1, math.ceil(seconds * 1000)) if seconds and seconds > 0 else None
    token = _DB_CALL_TIMEOUT_MS.set(milliseconds)
    try:
        yield
    finally:
        _DB_CALL_TIMEOUT_MS.reset(token)


@contextmanager
def applied_call_timeout(connection: object) -> Iterator[None]:
    """いまの範囲の上限を接続の ``call_timeout`` に当て、抜けるときに元の値へ戻す。"""
    milliseconds = current_db_call_timeout_ms()
    previous = getattr(connection, "call_timeout", None)
    # 上限の無い範囲と、call_timeout を持たない接続（テストの替え玉）はそのまま使う。
    if milliseconds is None or not isinstance(previous, int) or isinstance(previous, bool):
        yield
        return
    applied = min(previous, milliseconds) if previous > 0 else milliseconds
    connection.call_timeout = applied  # type: ignore[attr-defined]
    try:
        yield
    finally:
        # 切れた接続でも元の値へ戻す（失敗しても SQL の例外を隠さない）。
        with suppress(Exception):
            connection.call_timeout = previous  # type: ignore[attr-defined]


__all__ = ["applied_call_timeout", "current_db_call_timeout_ms", "db_call_timeout"]
