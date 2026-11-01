"""原 JSON の窓は hash・原ノード・抽出範囲を照合してから返す。"""

import hashlib
import json
from typing import Any

import pytest

from app.services.json_source_window import source_map_window


def fixture() -> tuple[bytes, dict[str, Any]]:
    raw = json.dumps({"a~/b": ["Same 😀 text", "Same 😀 text"]}, ensure_ascii=True).encode()
    return raw, {
        "elements": [{"element_id": "e1", "text": "Heading\nSame 😀 text"}],
        "parser_artifacts": {
            "json_source_map": {
                "version": 1,
                "coordinate_unit": "unicode_codepoint",
                "sha256": hashlib.sha256(raw).hexdigest(),
                "elements": {
                    "e1": [
                        {
                            "start": 8,
                            "end": 19,
                            "pointer": "/a~0~1b/1",
                            "source_start": 0,
                            "source_end": 11,
                        }
                    ]
                },
            }
        },
    }


def test_raw_node_window_uses_pointer_instead_of_first_duplicate() -> None:
    raw, payload = fixture()
    result = source_map_window(payload, raw, element_id="e1")
    assert result["status"] == "exact"
    assert result["pointer"] == "/a~0~1b/1"
    assert result["offset"] > raw.decode().index(",")
    assert result["text"] == "Same \\ud83d\\ude00 text"
    assert result["highlights"] == [[0, len(result["text"])]]
    assert result["coordinate_unit"] == "unicode_codepoint"


def test_legacy_missing_mapping_and_hash_mismatch_never_claim_exact() -> None:
    raw, payload = fixture()
    assert (
        source_map_window({"elements": payload["elements"]}, raw, element_id="e1")["status"]
        == "unrecorded"
    )
    result = source_map_window(payload, raw + b" ", element_id="e1")
    assert result["status"] == "version_mismatch"
    assert not result["text"]


@pytest.mark.parametrize("field,value", [("source_end", 12), ("start", 0), ("pointer", "/missing")])
def test_invalid_mapping_is_not_fuzzily_resolved(field: str, value: Any) -> None:
    raw, payload = fixture()
    payload["parser_artifacts"]["json_source_map"]["elements"]["e1"][0][field] = value
    assert source_map_window(payload, raw, element_id="e1")["status"] == "unresolved"


def test_bounded_window_and_continuation_for_long_source_node() -> None:
    value = "前" * 20000 + "Target" + "後" * 20000
    raw = json.dumps([value], ensure_ascii=False).encode()
    payload = {
        "elements": [{"element_id": "e", "text": "Target"}],
        "parser_artifacts": {
            "json_source_map": {
                "version": 1,
                "coordinate_unit": "unicode_codepoint",
                "sha256": hashlib.sha256(raw).hexdigest(),
                "elements": {
                    "e": [
                        {
                            "start": 0,
                            "end": 6,
                            "pointer": "/0",
                            "source_start": 20000,
                            "source_end": 20006,
                        }
                    ]
                },
            }
        },
    }
    result = source_map_window(payload, raw, element_id="e")
    assert len(result["text"]) <= 8192
    assert result["highlights"]
    assert result["previous_offset"] is not None and result["next_offset"] is not None
    next_result = source_map_window(payload, raw, element_id="e", offset=result["next_offset"])
    assert next_result["offset"] == result["offset"] + len(result["text"])


def test_prepared_variant_requires_exact_hash_and_legacy_does_not_load() -> None:
    import asyncio
    from unittest.mock import AsyncMock

    from app.services.json_source_window import load_source_map_window

    raw, payload = fixture()
    load = AsyncMock(side_effect=[b"different original", raw])
    result = asyncio.run(load_source_map_window(payload, element_id="e1", revision="r1", load=load))
    assert result["status"] == "exact"
    assert result["variant"] == "prepared" and result["revision"] == "r1"
    assert load.await_args_list[0].args == ("original",)
    assert load.await_args_list[1].args == ("prepared",)
    load.reset_mock()
    result = asyncio.run(load_source_map_window({}, element_id="e1", revision="r1", load=load))
    assert result["status"] == "unrecorded"
    load.assert_not_awaited()


def test_both_changed_variants_do_not_return_a_different_version() -> None:
    import asyncio
    from unittest.mock import AsyncMock

    from app.services.json_source_window import load_source_map_window

    _, payload = fixture()
    result = asyncio.run(
        load_source_map_window(
            payload, element_id="e1", revision="r1", load=AsyncMock(return_value=b"new")
        )
    )
    assert result["status"] == "version_mismatch" and not result["text"]


def test_missing_files_are_retryable_errors_instead_of_version_mismatch() -> None:
    import asyncio
    from unittest.mock import AsyncMock

    from fastapi import HTTPException

    from app.services.json_source_window import load_source_map_window

    _, payload = fixture()
    with pytest.raises(HTTPException) as error:
        asyncio.run(
            load_source_map_window(
                payload,
                element_id="e1",
                revision="r1",
                load=AsyncMock(side_effect=HTTPException(404, "missing")),
            )
        )
    assert error.value.status_code == 404


def test_citation_subset_highlights_only_its_mapped_range() -> None:
    raw, payload = fixture()
    result = source_map_window(payload, raw, element_id="e1", quote="text")
    assert result["status"] == "exact" and result["anchor_status"] == "exact"
    assert "".join(result["text"][a:b] for a, b in result["highlights"]) == "text"
    assert (
        source_map_window(payload, raw, element_id="e1", quote="Heading")["status"] == "unresolved"
    )


def test_quote_at_last_source_node_does_not_open_first_node() -> None:
    raw = json.dumps(["Alpha", "Target"]).encode()
    payload = {
        "elements": [{"element_id": "e", "text": "AlphaTarget"}],
        "parser_artifacts": {
            "json_source_map": {
                "version": 1,
                "coordinate_unit": "unicode_codepoint",
                "sha256": hashlib.sha256(raw).hexdigest(),
                "elements": {
                    "e": [
                        {"start": 0, "end": 5, "pointer": "/0", "source_start": 0, "source_end": 5},
                        {
                            "start": 5,
                            "end": 11,
                            "pointer": "/1",
                            "source_start": 0,
                            "source_end": 6,
                        },
                    ]
                },
            }
        },
    }
    result = source_map_window(payload, raw, element_id="e", quote="Target")
    assert result["status"] == "exact" and result["anchor_status"] == "exact"
    assert result["pointer"] == "/1" and result["source_count"] == 1
