"""所属記録に結び付く全文の保存先を、実行文脈から注入する。"""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from .trace_bodies import PreparedBody
from .trace_body_writer import BodySubmission


@dataclass(frozen=True, slots=True)
class CallBodyCapture:
    submit: Callable[[PreparedBody], BodySubmission]
    part_characters: int


_capture: ContextVar[CallBodyCapture | None] = ContextVar("trace_call_body_capture", default=None)


def current_body_capture() -> CallBodyCapture | None:
    return _capture.get()


@contextmanager
def call_body_capture(
    submit: Callable[[PreparedBody], BodySubmission], *, part_characters: int
) -> Iterator[None]:
    """製品は実際の所属とwriterをbindする。本文ON/OFFの判定は既存の層が行う。"""
    if part_characters < 1:
        raise ValueError("trace body part size must be positive")
    token = _capture.set(CallBodyCapture(submit, part_characters))
    try:
        yield
    finally:
        _capture.reset(token)
