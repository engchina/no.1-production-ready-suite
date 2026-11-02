"""保存済みの原本の位置の検証（#4100）。版や範囲が合わないときは同期の結果を出さない。"""

import hashlib
from typing import Any

from app.services.source_map_resolve import resolve_source_map

DATA = "原本の本文".encode()


def _map(**overrides: Any) -> dict[str, Any]:
    return {
        "version": 2,
        "coordinate_unit": "unicode_codepoint",
        "sha256": hashlib.sha256(DATA).hexdigest(),
        "source_length": 5,
        "scheme": "text_char",
        "elements": {"e": [{"source_start": 1, "source_end": 3, "locator": "char=1,3"}]},
        **overrides,
    }


def test_valid_map_returns_element_start() -> None:
    result = resolve_source_map(_map(), data=DATA, text_length=5, element_texts={"e": "本文"})
    assert (result.status, result.starts, result.units) == ("exact", {"e": 1}, ())


def test_different_source_digest_is_version_mismatch() -> None:
    result = resolve_source_map(
        _map(sha256="0" * 64), data=DATA, text_length=5, element_texts={"e": ""}
    )
    assert (result.status, result.starts) == ("version_mismatch", {})


def test_broken_maps_are_unresolved_not_guessed() -> None:
    for broken in (
        _map(source_length=4),
        _map(version=1),
        _map(coordinate_unit="byte"),
        _map(elements=[]),
        _map(elements={"e": [{"source_start": 3, "source_end": 1}]}),
        _map(elements={"e": [{"source_start": 0, "source_end": 6}]}),
        _map(elements={"e": [{"source_start": "1", "source_end": 3}]}),
        _map(elements={"e": [{"source_start": True, "source_end": 3}]}),
        _map(elements={"e": [{}]}),
        _map(elements={"other": [{"source_start": 0, "source_end": 1}]}),
    ):
        result = resolve_source_map(broken, data=DATA, text_length=5, element_texts={"e": "本文"})
        assert (result.status, result.starts) == ("unresolved", {}), broken


def test_fragment_scheme_returns_units_and_element_scheme_does_not() -> None:
    text = "| a |\n| b |"
    spans = [
        {"source_start": 0, "source_end": 2, "start": 0, "end": 5, "row_start": 1, "row_end": 1},
        {"source_start": 2, "source_end": 5, "start": 6, "end": 11, "row_start": 2, "row_end": 2},
    ]
    fragment = resolve_source_map(
        _map(scheme="delimited_row", elements={"t": spans}),
        data=DATA,
        text_length=5,
        element_texts={"t": text},
    )
    assert fragment.starts == {"t": 0}
    assert [(u.source_start, u.text_start, u.text_end, u.row_start) for u in fragment.units] == [
        (0, 0, 5, 1),
        (2, 6, 11, 2),
    ]
    element_level = resolve_source_map(
        _map(scheme="html_source", elements={"t": spans}),
        data=DATA,
        text_length=5,
        element_texts={"t": text},
    )
    assert element_level.units == ()


def test_edited_element_text_keeps_element_start_without_units() -> None:
    spans = [
        {"source_start": 0, "source_end": 2, "start": 0, "end": 5},
        {"source_start": 2, "source_end": 5, "start": 6, "end": 11},
    ]
    result = resolve_source_map(
        _map(scheme="json_pointer", elements={"t": spans}),
        data=DATA,
        text_length=5,
        element_texts={"t": "確認で修正した本文"},
    )
    assert (result.status, result.starts, result.units) == ("exact", {"t": 0}, ())
