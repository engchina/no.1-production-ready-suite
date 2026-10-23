"""実行環境が任意に注入する観測。本文や認証情報は observer に渡さない。"""

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from contextvars import ContextVar
from typing import Protocol


class CallObservation(Protocol):
    def usage(self, value: object) -> None: ...


class _NoObservation:
    def usage(self, value: object) -> None:
        pass


Observer = Callable[[str, str], AbstractContextManager[CallObservation]]
_OBSERVER: ContextVar[Observer | None] = ContextVar("engine_trace_observer", default=None)


@contextmanager
def bind_trace_observer(observer: Observer) -> Iterator[None]:
    token = _OBSERVER.set(observer)
    try:
        yield
    finally:
        _OBSERVER.reset(token)


@contextmanager
def observe_call(operation: str, model: str) -> Iterator[CallObservation]:
    observer = _OBSERVER.get()
    if observer is None:
        yield _NoObservation()
    else:
        with observer(operation, model) as observation:
            yield observation
