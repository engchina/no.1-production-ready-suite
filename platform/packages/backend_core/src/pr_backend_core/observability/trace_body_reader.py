"""認可済み正本のspan・入出力欄からだけ全文を解決する共通読取。"""

import contextlib
import logging
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from typing import Any, Literal

from ..chat_progress import CallContent, ChatProgressStepDetails
from .trace_bodies import BodyOwner, BodyPage, read_body_page

CallBodySlot = Literal["input", "output", "schema"]
logger = logging.getLogger(__name__)


def saved_call_body_content(
    span_details: Mapping[str, Any],
    *,
    span_id: str,
    slot: CallBodySlot,
    allow_restricted: bool = False,
) -> CallContent | None:
    """認可済み正本の正式な欄を解決する。非公開欄の参照自体を渡さない。"""
    value = span_details.get(span_id)
    if value is None or slot not in {"input", "output", "schema"}:
        return None
    details = (
        value
        if isinstance(value, ChatProgressStepDetails)
        else ChatProgressStepDetails.model_validate(value)
    )
    if details.call is None:
        return None
    if details.call.visibility == "system_admin" and not allow_restricted:
        return None
    content = {
        "input": details.call.input,
        "output": details.call.output,
        "schema": details.call.schema_,
    }[slot]
    if content.status in {"withheld", "not_recorded"} or content.body_reference is None:
        return None
    if content.body_reference.visibility == "system_admin" and not allow_restricted:
        return None
    return content


def read_call_body_page(
    cursor: Any,
    owner: BodyOwner,
    span_details: Mapping[str, Any],
    *,
    span_id: str,
    slot: CallBodySlot,
    first_part: int = 0,
    max_parts: int = 8,
    allow_restricted: bool = False,
) -> BodyPage | None:
    """製品側で正本へのアクセスを確認した後に呼ぶ。任意のbody IDは受け取らない。"""
    content = saved_call_body_content(
        span_details, span_id=span_id, slot=slot, allow_restricted=allow_restricted
    )
    if content is None or content.body_reference is None:
        return None
    return read_body_page(
        cursor,
        owner,
        content.body_reference,
        first_part=first_part,
        max_parts=max_parts,
        allow_restricted=allow_restricted,
    )


def fetch_saved_call_body_page(
    connection_factory: Callable[[], AbstractContextManager[Any]] | None,
    owner: BodyOwner | None,
    span_details: Mapping[str, Any],
    *,
    span_id: str,
    slot: CallBodySlot,
    first_part: int,
    allow_restricted: bool,
) -> BodyPage | None:
    """製品が正本と根拠を認可した後の共通読取。未保存を空の完了本文にしない。"""
    content = saved_call_body_content(
        span_details, span_id=span_id, slot=slot, allow_restricted=allow_restricted
    )
    if content is None or content.body_reference is None:
        return None
    if connection_factory is None or owner is None:
        return BodyPage(
            content.body_reference,
            "incomplete",
            content.failure_code or "storage_unavailable",
            (),
            None,
        )
    try:
        with connection_factory() as connection, contextlib.closing(connection.cursor()) as cursor:
            page = read_body_page(
                cursor,
                owner,
                content.body_reference,
                first_part=first_part,
                allow_restricted=allow_restricted,
            )
        if page is not None:
            return page
    except Exception:
        logger.warning("trace_body_read_failed")
        return BodyPage(content.body_reference, "incomplete", "storage_unavailable", (), None)
    return BodyPage(
        content.body_reference, "incomplete", content.failure_code or "body_unavailable", (), None
    )
