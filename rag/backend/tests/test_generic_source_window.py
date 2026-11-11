"""汎用の保存位置を引用原本の有界窓へ渡す。"""

import hashlib
from typing import Any
from unittest.mock import AsyncMock

import pytest
from rag_parser_core.source_map import SourceRange, source_map_artifact

from app.services.json_source_window import load_source_map_window, source_map_window


def fixture(kind: str) -> tuple[bytes, dict[str, Any], int, str]:
    value = '合成 "A" 😀'
    if kind == "html":
        source = f"<main><p>{value}</p><p>{value}</p></main>"
        text = value
        start = source.rindex("<p>")
        end = source.index("</p>", start) + len("</p>")
        locator = "p[2]"
        scheme = "html_source"
    else:
        source = '{"a":"synthetic", "b":"synthetic"}'
        start = source.index('"b"')
        end = len(source) - 1
        text = "synthetic"
        locator = "/b"
        scheme = "json_pointer"
    data = source.encode()
    mapping = source_map_artifact(
        data, source, scheme, {"e": [SourceRange(start, end, locator, 0, len(text))]}
    )
    return (
        data,
        {
            "elements": [{"element_id": "e", "text": text}],
            "parser_artifacts": {"source_map": mapping},
        },
        start,
        source[start:end],
    )


@pytest.mark.parametrize("kind", ["html", "json"])
def test_generic_reader_uses_second_saved_fragment(kind: str) -> None:
    raw, payload, start, expected = fixture(kind)
    result = source_map_window(payload, raw, element_id="e")
    assert result["status"] == "exact"
    assert result["anchor_status"] == "element"
    assert result["text"] == expected
    assert result["offset"] == start
    assert result["highlights"] == [[0, len(expected)]]


@pytest.mark.parametrize("kind", ["html", "json"])
def test_generic_reader_refuses_modified_source_and_invalid_mapping(kind: str) -> None:
    raw, payload, _, _ = fixture(kind)
    assert source_map_window(payload, raw + b" ", element_id="e")["status"] == "version_mismatch"
    payload["parser_artifacts"]["source_map"]["elements"]["e"][0]["source_end"] = len(raw) * 3
    assert source_map_window(payload, raw, element_id="e")["status"] == "unresolved"


@pytest.mark.asyncio
async def test_loader_uses_matching_prepared_version_for_generic_mapping() -> None:
    raw, payload, _, _ = fixture("html")
    load = AsyncMock(side_effect=[b"different original", raw])
    result = await load_source_map_window(payload, element_id="e", revision="ex1", load=load)
    assert result["status"] == "exact"
    assert result["revision"] == "ex1"
    assert result["variant"] == "prepared"
    assert [call.args[0] for call in load.await_args_list] == ["original", "prepared"]


def test_large_generic_fragment_stays_bounded_and_continues() -> None:
    raw, payload, start, _ = fixture("html")
    source = raw.decode()
    fragment = "<p>" + "合成" * 10000 + "</p>"
    source = source[:start] + fragment + "</main>"
    raw = source.encode()
    mapping = payload["parser_artifacts"]["source_map"]
    mapping.update(sha256=hashlib.sha256(raw).hexdigest(), source_length=len(source))
    mapping["elements"]["e"][0]["source_end"] = start + len(fragment)
    first = source_map_window(payload, raw, element_id="e")
    assert len(first["text"]) == 8192
    second = source_map_window(payload, raw, element_id="e", offset=first["next_offset"])
    assert second["offset"] == start + 8192
    assert second["text"] == source[start + 8192 : start + 16384]


@pytest.mark.parametrize(
    "options", [{"element_id": "missing"}, {"source_index": -1}, {"source_index": 1}, {"offset": 0}]
)
def test_generic_reader_rejects_unknown_or_outside_scope(options: dict[str, Any]) -> None:
    raw, payload, _, _ = fixture("html")
    result = source_map_window(payload, raw, **{"element_id": "e", **options})
    assert result["status"] == "unresolved"
    assert not result["text"]


@pytest.mark.asyncio
async def test_generic_reader_missing_mapping_does_not_load_files() -> None:
    load = AsyncMock()
    result = await load_source_map_window({}, element_id="e", revision="ex1", load=load)
    assert result["status"] == "unrecorded"
    load.assert_not_awaited()


def test_json_reader_selects_saved_unit_intersecting_unique_citation() -> None:
    source = '{"a":"first", "b":"second"}'
    raw = source.encode()
    text = "first\nsecond"
    first = source.index('"a"')
    second = source.index('"b"')
    mapping = source_map_artifact(
        raw,
        source,
        "json_pointer",
        {
            "e": [
                SourceRange(first, second - 2, "/a", 0, 5),
                SourceRange(second, len(source) - 1, "/b", 6, 12),
            ]
        },
    )
    payload = {
        "elements": [{"element_id": "e", "text": text}],
        "parser_artifacts": {"source_map": mapping},
    }
    result = source_map_window(payload, raw, element_id="e", quote="second")
    assert result["pointer"] == "/b"
    assert result["text"] == '"b":"second"'
    assert result["source_count"] == 1
