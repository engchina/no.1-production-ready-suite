"""本文IDを直接指定せず、実保存のspan欄と所属・可視性を二重に確認する。"""

from dataclasses import asdict
from typing import Any

from test_trace_bodies import OwnerCursor

from pr_backend_core.observability.trace_bodies import (
    BodyOwner,
    attach_body_owner,
    prepare_body,
    write_body,
)
from pr_backend_core.observability.trace_body_reader import read_call_body_page


def test_reader_resolves_only_saved_slot_and_returns_all_pages() -> None:
    cursor = OwnerCursor()
    owner = BodyOwner("rag", "history", "record-a")
    text = "合成の全文。" * 10000 + "最終条件。"
    body = prepare_body(text, part_characters=101)
    attach_body_owner(cursor, owner, [body.reference])
    write_body(cursor, body, clob_type=object())
    details: dict[str, Any] = {
        "span-a": {
            "call": {
                "input": {
                    "status": "recorded",
                    "text": text[:64000],
                    "preview_only": True,
                    "body_reference": asdict(body.reference),
                }
            }
        }
    }
    assert read_call_body_page(cursor, owner, details, span_id="missing", slot="input") is None
    assert read_call_body_page(cursor, owner, details, span_id="span-a", slot="output") is None
    assert (
        read_call_body_page(
            cursor,
            BodyOwner("rag", "history", "unrelated-record"),
            details,
            span_id="span-a",
            slot="input",
        )
        is None
    )
    position = 0
    restored = ""
    while True:
        page = read_call_body_page(
            cursor, owner, details, span_id="span-a", slot="input", first_part=position
        )
        assert page is not None and page.status == "complete"
        restored += "".join(part.text for part in page.parts)
        if page.next_part is None:
            break
        position = page.next_part
    assert restored == text
    details["span-a"]["call"]["input"]["status"] = "withheld"
    assert read_call_body_page(cursor, owner, details, span_id="span-a", slot="input") is None


def test_restricted_call_is_denied_even_when_reference_visibility_is_owner() -> None:
    cursor = OwnerCursor()
    owner = BodyOwner("nl2sql", "turn", "record-a")
    body = prepare_body("合成本文", part_characters=10)
    attach_body_owner(cursor, owner, [body.reference])
    write_body(cursor, body, clob_type=object())
    details = {
        "span-a": {
            "call": {
                "visibility": "system_admin",
                "input": {"status": "recorded", "body_reference": asdict(body.reference)},
            }
        }
    }
    assert read_call_body_page(cursor, owner, details, span_id="span-a", slot="input") is None
    page = read_call_body_page(
        cursor, owner, details, span_id="span-a", slot="input", allow_restricted=True
    )
    assert page is not None and page.parts[0].text == body.text
