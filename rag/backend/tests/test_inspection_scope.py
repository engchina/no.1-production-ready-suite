"""原本の座標と抽出本文の座標を混同しない同期の回帰。"""

import hashlib
import json
from typing import Any

import pytest

from app.services.inspection_scope import source_inspection


def synthetic_source() -> tuple[bytes, dict[str, Any]]:
    values = ['合成😀\\"' * 300, "長文" * 9000, "末尾"]
    data = json.dumps({"nodes": values}, ensure_ascii=True).encode()
    payload = {
        "elements": [{"element_id": f"e{i}", "text": value} for i, value in enumerate(values)],
        "parser_artifacts": {
            "json_source_map": {
                "version": 1,
                "coordinate_unit": "unicode_codepoint",
                "sha256": hashlib.sha256(data).hexdigest(),
                "elements": {
                    f"e{i}": [
                        {
                            "pointer": f"/nodes/{i}",
                            "start": 0,
                            "end": len(value),
                            "source_start": 0,
                            "source_end": len(value),
                        }
                    ]
                    for i, value in enumerate(values)
                },
            }
        },
    }
    return data, payload


def test_source_windows_cover_raw_json_and_assign_crossing_elements_once() -> None:
    data, payload = synthetic_source()
    windows = []
    offset = 0
    while True:
        window = source_inspection(payload, data, offset=offset, limit=8192)
        windows.append(window)
        if window["next_offset"] is None:
            break
        offset = window["next_offset"]
    assert "".join(w["text"] for w in windows) == data.decode()
    assert all(len(w["text"]) <= 8192 for w in windows)
    assert [e for w in windows for e in w["element_ids"]] == ["e0", "e1", "e2"]
    assert any(not w["element_ids"] for w in windows)
    for i, window in enumerate(windows[1:], 1):
        previous = source_inspection(payload, data, offset=window["previous_offset"], limit=8192)
        assert previous["offset"] == windows[i - 1]["offset"]


def test_focus_is_mapped_to_raw_escaped_unicode_and_canonical_segment() -> None:
    data, payload = synthetic_source()
    window = source_inspection(payload, data, offset=0, limit=8192, element_id="e2")
    assert "e2" in window["element_ids"]
    assert window["offset"] > 0
    unfocused = source_inspection(payload, data, offset=window["offset"], limit=8192)
    assert {**window, "highlights": []} == unfocused
    assert window["highlights"]


def test_version_and_legacy_mapping_fail_closed() -> None:
    data, payload = synthetic_source()
    assert (
        source_inspection(payload, data + b" ", offset=0, limit=8192)["scope_status"]
        == "version_mismatch"
    )
    assert (
        source_inspection({"elements": payload["elements"]}, data, offset=0, limit=8192)[
            "scope_status"
        ]
        == "unrecorded"
    )
    assert (
        source_inspection(payload, data, offset=0, limit=8192, expected_revision="old")[
            "element_ids"
        ]
        == []
    )


def test_malformed_mapping_is_unresolved_without_guessing_positions() -> None:
    data, payload = synthetic_source()
    payload["parser_artifacts"]["json_source_map"]["elements"] = ["invalid"]
    result = source_inspection(payload, data, offset=0, limit=8192)
    assert result["scope_status"] == "unresolved"
    assert result["element_ids"] == []


def test_non_utf8_source_uses_existing_charset_detection() -> None:
    text = "合成の本文です。文字コードを検出して表示します。" * 20
    result = source_inspection({}, text.encode("cp932"), offset=0, limit=8192)
    assert result["text"] == text
    assert result["scope_status"] == "unrecorded"


def test_saved_source_range_highlights_second_identical_text_and_checks_version() -> None:
    from rag_parser_core.source_map import SCHEME_TEXT_CHAR, SourceRange, source_map_artifact

    text = "😀\n同文\n同文"
    data = text.encode()
    payload = {
        "elements": [{"element_id": "a", "text": "同文"}],
        "parser_artifacts": {
            "source_map": source_map_artifact(
                data, text, SCHEME_TEXT_CHAR, {"a": [SourceRange(5, 7, "char=5,7")]}
            )
        },
    }
    result = source_inspection(payload, data, offset=0, limit=8192, element_id="a")
    assert result["highlights"] == [[5, 7]]
    assert text[5:7] == "同文"
    assert (
        source_inspection(payload, data + b" ", offset=0, limit=8192, element_id="a")["highlights"]
        == []
    )


@pytest.mark.parametrize("ascii_only", [False, True])
def test_json_selection_highlights_raw_escaped_text_in_the_selected_duplicate(
    ascii_only: bool,
) -> None:
    text = '合成😀\\"'
    data = json.dumps({"nodes": [text, text]}, ensure_ascii=ascii_only).encode()
    payload: dict[str, Any] = {
        "elements": [{"element_id": "second", "text": text}],
        "parser_artifacts": {
            "json_source_map": {
                "version": 1,
                "coordinate_unit": "unicode_codepoint",
                "sha256": hashlib.sha256(data).hexdigest(),
                "elements": {
                    "second": [
                        {
                            "pointer": "/nodes/1",
                            "start": 0,
                            "end": len(text),
                            "source_start": 0,
                            "source_end": len(text),
                        }
                    ]
                },
            }
        },
    }
    result = source_inspection(payload, data, offset=0, limit=8192, element_id="second")
    assert result["scope_status"] == "exact"
    assert len(result["highlights"]) == 1
    a, b = result["highlights"][0]
    raw = data.decode()
    assert result["text"][a:b] == json.dumps(text, ensure_ascii=ascii_only)[1:-1]
    assert a == raw.rindex(json.dumps(text, ensure_ascii=ascii_only)) + 1
    assert (
        source_inspection(payload, data + b" ", offset=0, limit=8192, element_id="second")[
            "highlights"
        ]
        == []
    )
    payload["parser_artifacts"]["json_source_map"]["elements"]["second"][0]["source_end"] += 1
    invalid = source_inspection(payload, data, offset=0, limit=8192, element_id="second")
    assert invalid["scope_status"] == "unresolved"
    assert invalid["highlights"] == []


def test_json_selection_highlights_only_the_current_long_source_window() -> None:
    data, payload = synthetic_source()
    result = source_inspection(payload, data, offset=0, limit=1000, element_id="e1")
    assert result["scope_status"] == "exact"
    assert result["highlights"]
    assert all(0 <= a < b <= len(result["text"]) for a, b in result["highlights"])
    assert result["next_offset"] is not None
    next_window = source_inspection(payload, data, offset=result["next_offset"], limit=1000)
    assert next_window["highlights"] == []
