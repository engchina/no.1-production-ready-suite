"""認可済み正本のspan・入出力欄からだけ全文を解決する共通読取。"""

from collections.abc import Mapping
from typing import Any, Literal

from ..chat_progress import ChatProgressStepDetails
from .trace_bodies import BodyOwner, BodyPage, read_body_page

CallBodySlot = Literal["input", "output", "schema"]


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
    return read_body_page(
        cursor,
        owner,
        content.body_reference,
        first_part=first_part,
        max_parts=max_parts,
        allow_restricted=allow_restricted,
    )
