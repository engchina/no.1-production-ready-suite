"""引用位置の窓は検索順位に依存せず、版と原要素の範囲を守る。"""

from collections.abc import Mapping
from typing import Any, cast

import pytest
from fastapi import HTTPException

from app.clients.oracle import OracleClient
from app.services.source_reader import reading_window


def fixture(count: int = 40) -> dict[str, Any]:
    return {
        "elements": [
            {
                "element_id": f"e{i}",
                "text": f"段落{i}:" + "文" * 300,
                "metadata": {"section_path": ["章A", "節A" if i < 20 else "節B"]},
            }
            for i in range(count)
        ]
    }


def read(payload: Mapping[str, Any], **kwargs: Any) -> dict[str, Any]:
    return reading_window(payload, revision="v1", anchor_ids=["e35"], quote="段落35:", **kwargs)


def test_anchor_at_end_and_bounded_original_context() -> None:
    result = read(fixture())
    assert any(b["id"] == "e35" and b["cited"] and b["highlights"] for b in result["blocks"])
    assert len(result["blocks"]) <= 9
    assert sum(len(b["text"]) for b in result["blocks"]) <= 8192
    assert result["previous_cursor"]
    previous = read(fixture(), cursor=result["previous_cursor"], direction="before")
    assert previous["blocks"][-1]["id"] == "e33"
    following = read(fixture(), cursor=previous["next_cursor"])
    assert following["blocks"][0]["id"] == result["blocks"][0]["id"]


def test_long_paragraph_centers_quote_and_both_directions_have_no_gap() -> None:
    payload = {
        "elements": [{"element_id": "e35", "text": "前" * 18000 + "引用本文" + "後" * 12000}]
    }
    result = reading_window(payload, revision="v1", anchor_ids=["e35"], quote="引用本文")
    assert result["blocks"][0]["highlights"]
    start = result["blocks"][0]["offset"]
    previous = read(payload, cursor=result["previous_cursor"], direction="before")
    assert previous["blocks"][0]["offset"] + len(previous["blocks"][0]["text"]) == start
    following = read(payload, cursor=result["next_cursor"])
    assert following["blocks"][0]["offset"] == start + len(result["blocks"][0]["text"])


def test_sections_parent_and_outline() -> None:
    payload = fixture()
    result = read(payload, scope="section")
    assert all(int(b["id"][1:]) >= 20 for b in result["blocks"])
    assert result["section_path"] == ["章A", "節B"]
    sibling = next(row for row in result["outline"] if row["title"] == "節A")
    result = read(payload, scope="section", section_id=sibling["id"])
    assert all(int(b["id"][1:]) < 20 for b in result["blocks"])
    assert read(payload, scope="parent")["outline"]


def test_legacy_missing_locator_does_not_search_full_text() -> None:
    result = read({"elements": [{"element_id": "other", "text": "段落35:"}]})
    assert result["anchor_status"] == "unresolved"
    assert result["previous_cursor"] is None
    assert result["blocks"][0]["text"] == "段落35:"


def test_version_and_invalid_cursor() -> None:
    result = read(fixture())
    with pytest.raises(HTTPException) as exc:
        reading_window(
            fixture(), revision="v2", anchor_ids=["e35"], quote="", cursor=result["previous_cursor"]
        )
    assert exc.value.status_code == 409
    with pytest.raises(HTTPException) as exc:
        read(fixture(), cursor="invalid")
    assert exc.value.status_code == 422


def test_ambiguous_quote_is_not_highlighted() -> None:
    result = reading_window(
        {"elements": [{"element_id": "e35", "text": "同文。同文。"}]},
        revision="v1",
        anchor_ids=["e35"],
        quote="同文。",
    )
    assert result["anchor_status"] == "element"
    assert not result["blocks"][0]["highlights"]


def test_top_level_section_path_from_saved_extraction() -> None:
    payload = {
        "elements": [{"element_id": "e35", "text": "段落35:", "section_path": ["章 A", "節 A"]}]
    }
    assert read(payload)["section_path"] == ["章 A", "節 A"]


def test_saved_string_element_ids_and_version_pin() -> None:
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.services.source_reader import read_source_context

    oracle = SimpleNamespace(
        retrievable_chunk=AsyncMock(
            return_value=SimpleNamespace(
                metadata={"chunk_set_id": "v1", "element_ids": "e35,e36"}, text="段落35:"
            )
        ),
        chunk_set_extraction_recipe_ids=AsyncMock(return_value={"v1": "extraction1"}),
        get_document_extraction_artifact=AsyncMock(return_value={"extraction_json": fixture()}),
    )
    result = asyncio.run(read_source_context(cast(OracleClient, oracle), "d1", "c1"))
    assert result["anchor_status"] == "exact"
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            read_source_context(cast(OracleClient, oracle), "d1", "c1", expected_revision="v0")
        )
    assert exc.value.status_code == 409
