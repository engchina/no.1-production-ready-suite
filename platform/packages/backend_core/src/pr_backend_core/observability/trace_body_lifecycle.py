"""製品の実行全体に全文captureを置く共通の境界。"""

from collections.abc import Awaitable, Callable, Coroutine, Iterator
from contextlib import contextmanager
from functools import wraps
from typing import Any

from ..config.tracing import TracingSettings
from .call_body_capture import call_body_capture, current_body_capture
from .trace_bodies import PreparedBody
from .trace_body_persistence import TraceBodyPersistence
from .trace_body_writer import BodySubmission


def _unavailable(body: PreparedBody) -> BodySubmission:
    return BodySubmission(body.reference, False, "storage_unavailable")


@contextmanager
def body_capture_scope(persistence: TraceBodyPersistence | None) -> Iterator[None]:
    """保存先が無い場合も新規記録を古い切詰めへ戻さず、未保存と明示する。"""
    if persistence is not None:
        with persistence.capture():
            yield
    elif current_body_capture() is not None:
        yield
    else:
        with call_body_capture(
            _unavailable, part_characters=TracingSettings().trace_body_part_characters
        ):
            yield


def captured_body_call[**P, T](
    function: Callable[P, Awaitable[T]],
    *,
    persistence: Callable[[], TraceBodyPersistence | None],
) -> Callable[P, Coroutine[Any, Any, T]]:
    @wraps(function)
    async def wrapped(*args: P.args, **kwargs: P.kwargs) -> T:
        with body_capture_scope(persistence()):
            return await function(*args, **kwargs)

    return wrapped
