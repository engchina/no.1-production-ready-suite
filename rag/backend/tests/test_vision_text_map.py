"""全文の組み立てと空白正規化を形式的に検証する。"""

import pytest
from rag_parser_core.vision_text import VisionTextProjection
from rag_parser_core.vision_text_map import (
    ProjectedVisionText,
    VisionTextRange,
    VisionTextSource,
    prove_complete_assembly,
)


def image(text: str, element_id: str, occurrence: int) -> ProjectedVisionText:
    source = VisionTextSource(
        element_id, occurrence, VisionTextProjection(text, text + "\n索引専用")
    )
    return ProjectedVisionText(text, (VisionTextRange(0, len(text), source),))


def test_duplicate_image_text_is_bound_by_order_not_first_match() -> None:
    parts = [
        image("同じ表示", "image-a", 0),
        ProjectedVisionText("原生の同じ表示"),
        image("同じ表示", "image-b", 2),
    ]
    raw = "\n\n".join(part.text for part in parts)
    value = prove_complete_assembly(raw, parts, separator="\n\n")
    assert [(r.start, r.end, r.source.element_id) for r in value.ranges] == [
        (0, 4, "image-a"),
        (15, 19, "image-b"),
    ]
    assert value.slice(15, 19).ranges[0].source.element_id == "image-b"


@pytest.mark.parametrize(
    "raw", ["前に別の本文\n同じ表示", "同じ表示\n後ろに別の本文", "同じ 表示", ""]
)
def test_partial_or_different_assembly_is_rejected(raw: str) -> None:
    with pytest.raises(ValueError, match="組み立て"):
        prove_complete_assembly(raw, [image("同じ表示", "image-a", 0)], separator="\n\n")


def test_normalization_preserves_fullwidth_characters_and_mixed_native_sources() -> None:
    parts = [
        ProjectedVisionText("原生\t"),
        image("　Ａ１２\r\n　保存　", "image-a", 1),
        ProjectedVisionText("\n原生末尾"),
    ]
    whole = ProjectedVisionText.join(parts, "\n")
    normalized = whole.whitespace_normalized()
    assert normalized.text == "原生 Ａ１２ 保存 原生末尾"
    assert "Ａ１２ 保存" in "".join(normalized.text[r.start : r.end] for r in normalized.ranges)
    assert all("原生" not in normalized.text[r.start : r.end] for r in normalized.ranges)
    assert all(r.source.element_id == "image-a" for r in normalized.ranges)


def test_empty_and_native_text_have_no_image_source() -> None:
    assert ProjectedVisionText("\n　\t").stripped() == ProjectedVisionText("")
    assert ProjectedVisionText(" 原生の検索語 ").whitespace_normalized() == ProjectedVisionText(
        "原生の検索語"
    )


def test_slice_and_join_preserve_each_image_occurrence() -> None:
    original = image("同じ表示", "image-a", 0)
    repeated = image("同じ表示", "image-a", 1)
    joined = ProjectedVisionText.join([original.slice(1, 3), repeated.slice(1, 3)], " / ")
    assert joined.text == "じ表 / じ表"
    assert [(r.start, r.end, r.source.occurrence) for r in joined.ranges] == [(0, 2, 0), (5, 7, 1)]


@pytest.mark.parametrize(
    "ranges",
    [
        (
            VisionTextRange(
                -1, 2, VisionTextSource("image-a", 0, VisionTextProjection("本文", "本文"))
            ),
        ),
        (
            VisionTextRange(
                0, 2, VisionTextSource("image-a", 0, VisionTextProjection("本文", "本文"))
            ),
            VisionTextRange(
                1, 2, VisionTextSource("image-b", 1, VisionTextProjection("本文", "本文"))
            ),
        ),
    ],
)
def test_invalid_ranges_are_not_guessed(ranges: tuple[VisionTextRange, ...]) -> None:
    with pytest.raises(ValueError, match="投影位置"):
        ProjectedVisionText("本文", ranges)


def test_compact_map_roundtrip_stores_each_image_projection_once() -> None:
    import json

    from rag_parser_core.vision_text_map import (
        VISION_CHUNK_MAP_KEY,
        chunk_projected_text,
        chunk_projection_metadata,
    )

    original = image("同じ表示です。" * 20, "image-a", 0)
    value = ProjectedVisionText.join(
        [original.slice(start, start + 7) for start in range(0, 70, 7)], " "
    )
    metadata = chunk_projection_metadata(value)
    payload = json.loads(metadata[VISION_CHUNK_MAP_KEY])
    assert len(payload["sources"]) == 1
    assert len(payload["ranges"]) == 10
    assert chunk_projected_text(value.text, metadata) == value
    payload["ranges"][0]["source_index"] = -1
    with pytest.raises(ValueError, match="再分割"):
        chunk_projected_text(value.text, {VISION_CHUNK_MAP_KEY: json.dumps(payload)})


@pytest.mark.parametrize("field", ["record_id", "element_body_sha256"])
@pytest.mark.parametrize("bad", [None, [], {}, 0, False])
def test_present_invalid_source_identity_never_uses_the_default(field: str, bad: object) -> None:
    import json

    from rag_parser_core.vision_text_map import (
        VISION_CHUNK_MAP_KEY,
        chunk_projected_text,
        chunk_projection_metadata,
    )

    value = image("画像本文", "image-a", 0)
    payload = json.loads(chunk_projection_metadata(value)[VISION_CHUNK_MAP_KEY])
    payload["sources"][0][field] = bad
    with pytest.raises(ValueError, match="再分割"):
        chunk_projected_text(value.text, {VISION_CHUNK_MAP_KEY: json.dumps(payload)})
