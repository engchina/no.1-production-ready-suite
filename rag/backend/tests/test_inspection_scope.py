"""原本の座標と抽出本文の座標を混同しない同期の回帰。"""

import hashlib
import json

from app.services.inspection_scope import source_inspection


def synthetic_source():
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


def test_source_windows_cover_raw_json_and_assign_crossing_elements_once():
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


def test_focus_is_mapped_to_raw_escaped_unicode_and_canonical_segment():
    data, payload = synthetic_source()
    window = source_inspection(payload, data, offset=0, limit=8192, element_id="e2")
    assert "e2" in window["element_ids"]
    assert window["offset"] > 0
    assert window == source_inspection(payload, data, offset=window["offset"], limit=8192)


def test_version_and_legacy_mapping_fail_closed():
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


def test_malformed_mapping_is_unresolved_without_guessing_positions():
    data, payload = synthetic_source()
    payload["parser_artifacts"]["json_source_map"]["elements"] = ["invalid"]
    result = source_inspection(payload, data, offset=0, limit=8192)
    assert result["scope_status"] == "unresolved"
    assert result["element_ids"] == []


def test_non_utf8_source_uses_existing_charset_detection():
    text = "合成の本文です。文字コードを検出して表示します。" * 20
    result = source_inspection({}, text.encode("cp932"), offset=0, limit=8192)
    assert result["text"] == text
    assert result["scope_status"] == "unrecorded"
