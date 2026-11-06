"""独立reviewの保存旧画像と章節の反例を同入力で保持する。"""

import pytest
from rag_parser_core.extraction import DocumentElement, StructuredExtraction
from rag_parser_core.vision_text import VisionTextProjection
from rag_pipeline_core.chunking import CHUNKING_STRATEGIES, chunk_extraction_with_strategy


def mixed_saved_extraction() -> StructuredExtraction:
    old, new = "旧生成の検索補助甲", "新しい画像説明乙"
    projection = VisionTextProjection(new, new + "\n索引専用乙")
    return StructuredExtraction(
        raw_text=old + "\n\n" + new,
        elements=[
            DocumentElement(
                kind="figure",
                element_id="old-image",
                text=old,
                metadata={
                    "figure_text_source": "vision",
                    "vision_text_projection_status": "unrecorded",
                    "vision_status": "succeeded",
                },
            ),
            DocumentElement(
                kind="figure",
                element_id="new-image",
                text=new,
                metadata={"vision_text_projection": projection.dumps()},
            ),
        ],
    )


@pytest.mark.parametrize("strategy", CHUNKING_STRATEGIES)
def test_saved_old_vlm_plus_new_map_requires_reparse_before_chunking(strategy: str) -> None:
    with pytest.raises(ValueError, match="再解析"):
        chunk_extraction_with_strategy(
            mixed_saved_extraction(), strategy=strategy, chunk_size=200, overlap=0, delimiter="\n\n"
        )


@pytest.mark.parametrize("layout", [False, True])
def test_saved_old_vlm_is_rejected_in_both_small_to_big_entries(layout: bool) -> None:
    from app.rag.chunking_small_to_big import (
        build_parent_child_chunks,
        build_structure_parent_child_chunks,
    )

    builder = build_parent_child_chunks if layout else build_structure_parent_child_chunks
    with pytest.raises(ValueError, match="再解析"):
        builder(mixed_saved_extraction())


@pytest.mark.parametrize("strategy", CHUNKING_STRATEGIES)
@pytest.mark.parametrize("size,overlap", [(80, 10), (200, 0)])
def test_role_only_projection_preserves_existing_section_and_chunk_semantics(
    strategy: str,
    size: int,
    overlap: int,
) -> None:
    body = "画面内の保存ボタンです。" * 4
    elements = [
        DocumentElement(kind="title", element_id="h1", text="# 技術 A"),
        DocumentElement(kind="figure", element_id="f1", text=body),
        DocumentElement(kind="title", element_id="h2", text="# 技術 B"),
        DocumentElement(kind="text", element_id="n1", text="原生の設定手順です。" * 4),
    ]
    baseline = StructuredExtraction(
        raw_text="\n\n".join(e.text for e in elements), elements=elements
    )
    projected = baseline.model_copy(
        update={
            "elements": [
                e.model_copy(
                    update={
                        "metadata": {
                            **e.metadata,
                            "vision_text_projection": VisionTextProjection(body, body).dumps(),
                        }
                    }
                )
                if e.element_id == "f1"
                else e
                for e in baseline.elements
            ]
        }
    )

    def summary(extraction: StructuredExtraction) -> list[tuple[object, ...]]:
        chunks = chunk_extraction_with_strategy(
            extraction, strategy=strategy, chunk_size=size, overlap=overlap, delimiter="\n\n"
        )
        groups = {
            str(c.metadata["chunk_group_id"]): i
            for i, c in enumerate(chunks)
            if "chunk_group_id" in c.metadata
        }
        return [
            (
                c.text,
                c.index,
                c.start_offset,
                c.end_offset,
                groups.get(str(c.metadata.get("chunk_group_id"))),
                {
                    k: v
                    for k, v in c.metadata.items()
                    if k != "chunk_group_id"
                    and k.startswith(("section_", "chunk_group_", "chunk_size_"))
                },
            )
            for c in chunks
        ]

    assert summary(projected) == summary(baseline)


@pytest.mark.parametrize("strategy", CHUNKING_STRATEGIES)
@pytest.mark.parametrize("known_vision", [True, False])
def test_null_projection_cannot_override_known_vision_but_native_default_is_preserved(
    strategy: str,
    known_vision: bool,
) -> None:
    extraction = mixed_saved_extraction()
    first = extraction.elements[0]
    metadata: dict[str, str | int | float | bool | None] = {"vision_text_projection": None}
    if known_vision:
        metadata["figure_text_source"] = "vision"
    extraction = extraction.model_copy(
        update={
            "elements": [first.model_copy(update={"metadata": metadata}), *extraction.elements[1:]]
        }
    )
    if known_vision:
        with pytest.raises(ValueError, match="再解析"):
            chunk_extraction_with_strategy(
                extraction, strategy=strategy, chunk_size=200, overlap=0, delimiter="\n\n"
            )
    else:
        chunks = chunk_extraction_with_strategy(
            extraction, strategy=strategy, chunk_size=200, overlap=0, delimiter="\n\n"
        )
        assert chunks and any(first.text in c.text for c in chunks)
