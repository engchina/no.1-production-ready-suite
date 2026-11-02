"""位置情報の無い既存文書の原本対応を、推測せずに作り直す回帰（#4094）。"""

import hashlib
import json
from typing import Any

import pytest
from rag_parser_core.xml_tree_json import xml_tree_json_extraction

from app.services import json_source_rebuild
from app.services.inspection_scope import source_inspection


def _node(tag: str, *children: Any, **attrs: str) -> dict[str, Any]:
    return {"tag": tag, "attr": attrs, "children": list(children)}


def _tree_source(count: int = 40, *, sentence: str = "合成の本文です。" * 80) -> bytes:
    articles = (
        _node(
            "Article",
            _node("ArticleTitle", str(i)),
            _node("Paragraph", _node("ParagraphSentence", _node("Sentence", f"{i}{sentence}"))),
            Num=str(i),
        )
        for i in range(1, count + 1)
    )
    tree = {
        "law_info": {},
        "revision_info": {"law_title": "合成文書"},
        "law_full_text": _node("Law", _node("LawBody", _node("MainProvision", *articles))),
    }
    return json.dumps(tree, ensure_ascii=False).encode()


def _legacy_payload(data: bytes) -> dict[str, Any]:
    """位置情報を記録する前に解析した抽出結果（`json_source_map` が無い）。"""
    extraction = xml_tree_json_extraction(data)
    assert extraction is not None
    artifacts = dict(extraction.parser_artifacts)
    artifacts.pop("json_source_map")
    return {
        "elements": [
            {"element_id": element.element_id, "text": str(element.text)}
            for element in extraction.elements
        ],
        "parser_artifacts": artifacts,
    }


@pytest.fixture(autouse=True)
def _empty_cache() -> None:
    json_source_rebuild._cache.clear()


def test_legacy_tree_follows_the_source_window_like_a_recorded_document() -> None:
    data = _tree_source()
    extraction = xml_tree_json_extraction(data)
    assert extraction is not None
    recorded = {
        "elements": _legacy_payload(data)["elements"],
        "parser_artifacts": dict(extraction.parser_artifacts),
    }
    legacy = _legacy_payload(data)

    windows = []
    offset = 0
    while True:
        window = source_inspection(legacy, data, offset=offset, limit=8192)
        assert window["scope_status"] == "exact"
        assert window == source_inspection(recorded, data, offset=offset, limit=8192)
        windows.append(window)
        if window["next_offset"] is None:
            break
        offset = window["next_offset"]

    assert len(windows) > 1
    assigned = [element_id for window in windows for element_id in window["element_ids"]]
    assert sorted(assigned) == sorted(element["element_id"] for element in legacy["elements"])
    assert "json_source_map" not in legacy["parser_artifacts"]


def test_only_elements_with_identical_id_and_text_receive_a_position() -> None:
    data = _tree_source()
    payload = _legacy_payload(data)
    payload["elements"][0]["text"] += "（保存後に変わった本文）"
    payload["elements"].append({"element_id": "added-after-parsing", "text": "追加の要素"})

    window = source_inspection(payload, data, offset=0, limit=8192)

    assert window["scope_status"] == "exact"
    assert payload["elements"][0]["element_id"] not in window["element_starts"]
    assert "added-after-parsing" not in window["element_starts"]
    assert payload["elements"][1]["element_id"] in window["element_starts"]


def test_no_matching_element_stays_unrecorded() -> None:
    data = _tree_source()
    payload = _legacy_payload(data)
    for element in payload["elements"]:
        element["text"] += "（別の版）"

    window = source_inspection(payload, data, offset=0, limit=8192)

    assert window["scope_status"] == "unrecorded"
    assert window["element_ids"] == []


def test_other_revision_is_not_rebuilt() -> None:
    data = _tree_source()

    window = source_inspection(
        _legacy_payload(data), data, offset=0, limit=8192, expected_revision="0" * 64
    )

    assert window["scope_status"] == "version_mismatch"
    assert window["element_ids"] == []


def test_recorded_map_is_never_replaced() -> None:
    data = _tree_source()
    payload = _legacy_payload(data)
    recorded = {"version": 1, "coordinate_unit": "unicode_codepoint", "sha256": "0" * 64}
    payload["parser_artifacts"]["json_source_map"] = recorded

    assert source_inspection(payload, data, offset=0, limit=8192)["scope_status"] == (
        "version_mismatch"
    )
    assert payload["parser_artifacts"]["json_source_map"] is recorded


def test_other_source_format_is_not_rebuilt() -> None:
    data = _tree_source()
    payload = _legacy_payload(data)
    payload["parser_artifacts"]["source_format"] = "text"

    assert source_inspection(payload, data, offset=0, limit=8192)["scope_status"] == "unrecorded"


def test_broken_source_stays_unrecorded() -> None:
    data = "{壊れた JSON".encode() * 2000
    payload = {
        "elements": [{"element_id": "e0", "text": "本文"}],
        "parser_artifacts": {"source_format": "xml_tree_json"},
    }

    assert source_inspection(payload, data, offset=0, limit=8192)["scope_status"] == "unrecorded"


def test_rebuild_is_cached_per_source_and_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    real = xml_tree_json_extraction

    def counted(data: bytes) -> Any:
        calls.append(hashlib.sha256(data).hexdigest())
        return real(data)

    monkeypatch.setattr(json_source_rebuild, "xml_tree_json_extraction", counted)
    data = _tree_source()
    payload = _legacy_payload(data)

    source_inspection(payload, data, offset=0, limit=8192)
    source_inspection(payload, data, offset=8192, limit=8192)
    assert len(calls) == 1

    for index in range(json_source_rebuild._CACHE_MAX_SOURCES + 1):
        other = _tree_source(1, sentence=f"合成{index}")
        json_source_rebuild.payload_with_source_map(_legacy_payload(other), other)
    assert len(json_source_rebuild._cache) == json_source_rebuild._CACHE_MAX_SOURCES


def test_source_within_one_window_assigns_every_element_without_a_map() -> None:
    data = "短い本文\n".encode()
    payload = {
        "elements": [
            {"element_id": "e0", "text": "短い"},
            {"element_id": "e1", "text": "本文"},
        ],
        "parser_artifacts": {},
    }

    window = source_inspection(payload, data, offset=0, limit=8192)

    assert window["scope_status"] == "exact"
    assert window["element_ids"] == ["e0", "e1"]
    assert window["next_offset"] is None


def test_long_source_without_a_map_stays_unrecorded() -> None:
    data = ("本文\n" * 5000).encode()
    payload = {"elements": [{"element_id": "e0", "text": "本文"}], "parser_artifacts": {}}

    window = source_inspection(payload, data, offset=0, limit=8192)

    assert window["scope_status"] == "unrecorded"
    assert window["element_ids"] == []
