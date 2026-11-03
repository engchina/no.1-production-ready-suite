"""拡張の読み取りと採用を同じ工程に結び付ける、回答内だけの観測。"""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ContextReadReceipt:
    reads: list[dict[str, Any]] = field(default_factory=list)
    selection: list[dict[str, Any]] = field(default_factory=list)


_CONTEXT_READ: ContextVar[ContextReadReceipt | None] = ContextVar("context_read_receipt", default=None)


def context_read_receipt() -> ContextReadReceipt | None:
    return _CONTEXT_READ.get()


@contextmanager
def bind_context_read_receipt() -> Iterator[ContextReadReceipt]:
    receipt = ContextReadReceipt()
    token = _CONTEXT_READ.set(receipt)
    try:
        yield receipt
    finally:
        _CONTEXT_READ.reset(token)
