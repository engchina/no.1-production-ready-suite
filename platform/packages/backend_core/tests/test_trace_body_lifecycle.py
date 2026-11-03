"""実行境界が保存先不在を隠さず、既存の明示captureや例外を保持する。"""

import json

import pytest

from pr_backend_core.observability.call_body_capture import call_body_capture, current_body_capture
from pr_backend_core.observability.call_details import call_content
from pr_backend_core.observability.trace_bodies import PreparedBody
from pr_backend_core.observability.trace_body_lifecycle import (
    body_capture_scope,
    captured_body_call,
    captured_sync_body_call,
)
from pr_backend_core.observability.trace_body_writer import BodySubmission


def test_missing_storage_is_explicit_incomplete_but_small_full_content_stays_inline() -> None:
    with body_capture_scope(None):
        large = call_content("synthetic input " * 10000)
        small = call_content(list(range(1301)))
    assert large.status == "incomplete" and large.failure_code == "storage_unavailable"
    assert large.body_reference is not None and large.preview_only
    assert small.status == "recorded" and not small.preview_only
    assert json.loads(small.text) == list(range(1301))
    assert current_body_capture() is None


def test_existing_explicit_capture_is_not_replaced_by_missing_product_store() -> None:
    saved: list[PreparedBody] = []

    def submit(body: PreparedBody) -> BodySubmission:
        saved.append(body)
        return BodySubmission(body.reference, True)

    with call_body_capture(submit, part_characters=100):
        before = current_body_capture()
        with body_capture_scope(None):
            content = call_content("synthetic input " * 10000)
            assert current_body_capture() is before
    assert content.status == "recorded" and len(saved) == 1
    assert current_body_capture() is None


async def test_async_boundary_keeps_result_exception_and_restores_capture() -> None:
    async def operation(fail: bool) -> str:
        assert current_body_capture() is not None
        if fail:
            raise RuntimeError("synthetic failure")
        return "synthetic result"

    wrapped = captured_body_call(operation, persistence=lambda: None)
    assert await wrapped(False) == "synthetic result"
    assert current_body_capture() is None
    with pytest.raises(RuntimeError, match="synthetic failure"):
        await wrapped(True)
    assert current_body_capture() is None


def test_sync_boundary_keeps_result_exception_and_nested_capture() -> None:
    def operation(*, fail: bool) -> str:
        with body_capture_scope(None):
            assert current_body_capture() is not None
        if fail:
            raise RuntimeError("synthetic sync failure")
        return "synthetic result"

    wrapped = captured_sync_body_call(operation, persistence=lambda: None)
    assert wrapped(fail=False) == "synthetic result"
    assert current_body_capture() is None
    with pytest.raises(RuntimeError, match="synthetic sync failure"):
        wrapped(fail=True)
    assert current_body_capture() is None
