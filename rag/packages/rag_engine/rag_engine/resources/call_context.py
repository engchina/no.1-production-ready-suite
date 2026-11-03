"""モデル呼出しの用途・バッチと実送信の番号。本文から用途を推定しない。"""

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field


@dataclass
class _Context:
    values: dict[str, object] = field(default_factory=dict)
    sends: int = 0


_CURRENT: ContextVar[_Context | None] = ContextVar("engine_call_context", default=None)


@contextmanager
def call_context(values: Mapping[str, object] | None = None) -> Iterator[None]:
    parent = _CURRENT.get()
    own = _Context({**(parent.values if parent else {}), **(values or {})})
    token = _CURRENT.set(own)
    try:
        yield
    finally:
        _CURRENT.reset(token)


def update_call_context(values: Mapping[str, object]) -> None:
    if current := _CURRENT.get():
        current.values.update(values)


def next_send_attributes() -> dict[str, object]:
    current = _CURRENT.get()
    if current is None:
        return {}
    current.sends += 1
    return {**current.values, "pr.attempt": current.sends}


def observation_attributes(observation: object, values: Mapping[str, object]) -> None:
    """観測のない環境と旧い注入の代役でも、主処理の動作を変えない。"""
    callback = getattr(observation, "attributes", None)
    if callable(callback):
        callback(values)
