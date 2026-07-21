"""認証済み application actor を request / worker 境界で伝播する。"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token

_ACTOR_USER_ID: ContextVar[str] = ContextVar("nl2sql_actor_user_id", default="")


def current_actor_user_id() -> str:
    return _ACTOR_USER_ID.get()


def set_actor_user_id(user_id: str) -> Token[str]:
    return _ACTOR_USER_ID.set(user_id)


def reset_actor_user_id(token: Token[str]) -> None:
    _ACTOR_USER_ID.reset(token)


@contextmanager
def actor_scope(user_id: str) -> Iterator[None]:
    token = set_actor_user_id(user_id)
    try:
        yield
    finally:
        reset_actor_user_id(token)
