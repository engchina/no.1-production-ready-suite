"""原文で裏付けられる結合下線の復元と、章節・分割への反映。"""

from __future__ import annotations

from typing import Any

import fitz as pymupdf  # type: ignore[import-untyped]
import pytest

from app.rag.chunking import chunk_extraction
from app.rag.chunking_small_to_big import build_parent_child_chunks
from app.rag.navigation import build_navigation_tree
from app.rag.pdf_segment_merge import ContinuationGeometry, consolidate_document_extraction
from app.rag.pdf_text_correction import _restore_underscores, correct_docling_pdf_text
from app.schemas.extraction import DocumentElement, StructuredExtraction


@pytest.mark.parametrize(
    ("parsed", "original", "expected"),
    [
        ("Sample\u033201", "Sample_01", "Sample_01"),
        ("Sample\u033201", "Sample\u033201", "Sample\u033201"),
        ("A\u0332B\u0332", "A_B\u0332", "A_B\u0332"),
        ("A\u0332B", "A-XB", "A\u0332B"),
        ("A\u0332B", "Other_B", "A\u0332B"),
        ("A\u0332B", "", "A\u0332B"),
        ("A\u0332 B", "A_B", "A_ B"),
    ],
)
def test_restore_requires_original_text(parsed: str, original: str, expected: str) -> None:
    assert _restore_underscores(parsed, original) == expected


def _fixture() -> tuple[bytes, dict[str, Any]]:
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_text((50, 70), "Synthetic_01_Title")
        page.insert_text((50, 150), "Other_02_Title")
        rect = page.search_for("Synthetic_01_Title")[0]
        record = {
            "id": "title-1",
            "engine": "docling",
            "category": "Section-header",
            "page": 1,
            "seq_no": 1,
            "text": "Synthetic\u033201\u0332Title",
            "bbox": list(rect),
            "page_width": page.rect.width,
            "page_height": page.rect.height,
            "coord_system": "image_top_left",
            "raw": {"text": "Synthetic\u033201\u0332Title"},
        }
        return bytes(document.tobytes()), record


def test_corrects_only_same_position_and_is_idempotent() -> None:
    pdf, record = _fixture()
    assert correct_docling_pdf_text([record], pdf) == {"title-1"}
    assert record["text"] == "Synthetic_01_Title"
    assert correct_docling_pdf_text([record], pdf) == set()
    # 原始の parser 出力は診断のため保持する。
    assert "\u0332" in record["raw"]["text"]


@pytest.mark.parametrize("kind", ["Table", "Picture"])
def test_does_not_flatten_table_or_image_text(kind: str) -> None:
    pdf, record = _fixture()
    record["category"] = kind
    assert correct_docling_pdf_text([record], pdf) == set()


def test_does_not_use_matching_text_from_another_position() -> None:
    pdf, record = _fixture()
    record["bbox"] = [50.0, 135.0, 200.0, 155.0]
    assert correct_docling_pdf_text([record], pdf) == set()
    assert "\u0332" in record["text"]


@pytest.mark.parametrize("changed", ["page", "bounds", "scale", "rotation", "invalid_pdf"])
def test_unreadable_sources_are_preserved(changed: str) -> None:
    pdf, record = _fixture()
    if changed == "page":
        record["page"] = 2
    elif changed == "bounds":
        record["bbox"][0] = float("nan")
    elif changed == "scale":
        record["page_width"] = 0
    elif changed == "rotation":
        with pymupdf.open(stream=pdf, filetype="pdf") as document:
            document[0].set_rotation(90)
            pdf = bytes(document.tobytes())
    else:
        pdf = b"not a pdf"
    assert correct_docling_pdf_text([record], pdf) == set()
    assert "\u0332" in record["text"]


def test_consolidation_corrects_text_paths_navigation_and_chunks() -> None:
    pdf, record = _fixture()
    extraction = StructuredExtraction(
        raw_text=record["text"] + "\n\nSynthetic paragraph.\n\nAdditional image description.",
        elements=[
            DocumentElement(
                kind="title",
                text=record["text"],
                element_id="title-1",
                page_number=1,
                section_path=[record["text"]],
            ),
            DocumentElement(
                kind="text",
                text="Synthetic paragraph.",
                element_id="body-1",
                page_number=1,
                section_path=[record["text"]],
            ),
        ],
        parser_artifacts={
            "layout_records": {
                "records": [
                    record,
                    {
                        **record,
                        "id": "body-1",
                        "category": "Text",
                        "seq_no": 2,
                        "text": "Synthetic paragraph.",
                        "raw": {},
                    },
                ]
            }
        },
    )
    corrected = consolidate_document_extraction(
        extraction, source_bytes=pdf, geometry=ContinuationGeometry(enabled=False)
    )
    assert extraction.elements[0].text == record["text"]
    assert corrected.elements[0].text == "Synthetic_01_Title"
    assert corrected.raw_text.endswith("\n\nAdditional image description.")
    assert "\u0332" not in corrected.raw_text
    assert all(element.section_path == ["Synthetic_01_Title"] for element in corrected.elements)
    assert build_navigation_tree(corrected)[0].title == "Synthetic_01_Title"
    chunks = chunk_extraction(corrected, chunk_size=500, overlap=0)
    assert chunks
    assert all("\u0332" not in str(chunk.metadata) + chunk.text for chunk in chunks)
    parent_child_chunks = build_parent_child_chunks(corrected)
    assert parent_child_chunks
    assert all("\u0332" not in str(chunk.metadata) + chunk.text for chunk in parent_child_chunks)
    again = consolidate_document_extraction(
        corrected, source_bytes=pdf, geometry=ContinuationGeometry(enabled=False)
    )
    assert again == corrected


def test_no_text_layer_and_other_parser_are_not_corrected() -> None:
    pdf, record = _fixture()
    record["engine"] = "mineru"
    assert correct_docling_pdf_text([record], pdf) == set()
    record["engine"] = "docling"
    with pymupdf.open() as document:
        document.new_page()
        empty = bytes(document.tobytes())
    assert correct_docling_pdf_text([record], empty) == set()


def test_normal_text_does_not_open_pdf(monkeypatch: pytest.MonkeyPatch) -> None:
    pdf, record = _fixture()
    record["text"] = "Synthetic_01_Title"

    def unexpected_open(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("正常な文字では PDF を開かない")

    monkeypatch.setattr(pymupdf, "open", unexpected_open)
    assert correct_docling_pdf_text([record], pdf) == set()
