"""呼出し元が決めた契約の修正入力を、既存の再試行へ渡す。"""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass


@dataclass
class _RetryInput:
    repair: Callable[[Exception, bool], str | None]
    prompt: str | None = None


_retry_input: ContextVar[_RetryInput | None] = ContextVar("structured_output_retry_input", default=None)


@contextmanager
def bind_structured_output_repair(repair: Callable[[Exception, bool], str | None]) -> Iterator[None]:
    """修正はこの呼出しだけに適用し、失敗・並行実行でもほかの呼出しへ持ち越さない。"""
    token = _retry_input.set(_RetryInput(repair))
    try:
        yield
    finally:
        _retry_input.reset(token)


def notify_invalid_output(error: Exception, will_retry: bool) -> None:
    if state := _retry_input.get():
        state.prompt = state.repair(error, will_retry)


def structured_output_prompt(original: str) -> str:
    state = _retry_input.get()
    return state.prompt if state is not None and state.prompt is not None else original
