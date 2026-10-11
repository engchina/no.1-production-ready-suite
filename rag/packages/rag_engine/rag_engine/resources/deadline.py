"""回答の期限（協調的な取り消し。#1422）。

回答フローは worker thread で動く。呼び出し側（backend）が回答を上限で打ち切っても、thread は
外から止められないため、LLM 1 回の timeout・再試行のまま LLM を呼び続けてしまう。

呼び出し側は回答の期限（`Deadline`）を `bind_deadline` で文脈（`ContextVar`）に置き、打ち切る
ときに `Deadline.cancel()` を呼ぶ。worker thread は文脈を引き継ぐ（`asyncio.to_thread` /
`contextvars.copy_context`）ので、thread の中の LLM の呼び出しは同じ期限を見て、

- 呼ぶ前に `check_deadline()` で期限を確かめ（過ぎていれば `DeadlineExceeded`）、
- 1 回の timeout を `remaining_seconds()` に縮め、
- 期限を過ぎたら再試行しない。

期限が無い文脈（取込・単体の CLI など）では何もしない（従来どおりの timeout と再試行）。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar


class DeadlineExceeded(TimeoutError):
    """回答の期限を過ぎた・取り消された（それ以上 LLM を呼ばない）。"""


class Deadline:
    """回答の期限。`timeout_seconds` が None のときは時刻の期限を持たず、取り消しだけを伝える。

    `parent` を渡すと、親の期限と取り消しも引き継ぐ（期限は早いほう）。thread をまたいで
    共有するので、取り消しは `threading.Event` で持つ。
    """

    def __init__(self, timeout_seconds: float | None = None, *, parent: Deadline | None = None) -> None:
        self._expires_at = (
            None if timeout_seconds is None else time.monotonic() + max(0.0, float(timeout_seconds))
        )
        self._parent = parent
        self._cancelled = threading.Event()

    def cancel(self) -> None:
        """取り消す（以後の LLM の呼び出しは `DeadlineExceeded` になる）。"""
        self._cancelled.set()

    @property
    def cancelled(self) -> bool:
        if self._cancelled.is_set():
            return True
        return self._parent.cancelled if self._parent is not None else False

    def remaining(self) -> float | None:
        """残りの秒数。期限が無ければ None、取り消し済み・期限切れは 0。"""
        if self.cancelled:
            return 0.0
        remaining: float | None = None
        if self._expires_at is not None:
            remaining = max(0.0, self._expires_at - time.monotonic())
        if self._parent is not None:
            parent_remaining = self._parent.remaining()
            if parent_remaining is not None:
                remaining = parent_remaining if remaining is None else min(remaining, parent_remaining)
        return remaining

    @property
    def expired(self) -> bool:
        remaining = self.remaining()
        return remaining is not None and remaining <= 0.0

    def check(self) -> None:
        """期限を過ぎた・取り消されたら `DeadlineExceeded` を送出する。"""
        if self.cancelled:
            raise DeadlineExceeded("回答の処理が取り消されました。")
        if self.expired:
            raise DeadlineExceeded("回答の期限を過ぎました。")


_current: ContextVar[Deadline | None] = ContextVar("rag_engine_answer_deadline", default=None)


@contextmanager
def bind_deadline(deadline: Deadline | None) -> Iterator[Deadline | None]:
    """`deadline` をこの文脈の回答の期限にする（None は期限なし）。"""
    token = _current.set(deadline)
    try:
        yield deadline
    finally:
        _current.reset(token)


def current_deadline() -> Deadline | None:
    """この文脈の回答の期限（無ければ None）。"""
    return _current.get()


def remaining_seconds() -> float | None:
    """この文脈の回答の期限までの残りの秒数（期限が無ければ None）。"""
    deadline = _current.get()
    return None if deadline is None else deadline.remaining()


def check_deadline() -> None:
    """この文脈の回答の期限を過ぎていたら `DeadlineExceeded` を送出する（期限が無ければ何もしない）。"""
    deadline = _current.get()
    if deadline is not None:
        deadline.check()
