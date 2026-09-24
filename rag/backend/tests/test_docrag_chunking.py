"""DocRAG Small-to-Big 分割(docrag_small_to_big)の backend Chunk 変換。"""

import json

import pytest

from app.clients.oracle import _chunk_search_text
from app.rag.chunking_strategy import normalize_chunking_strategy
from app.rag.docrag_chunking import (
    DOCRAG_SEARCH_TEXT_KEY,
    DocragLayoutMissingError,
    build_docrag_chunks,
    has_docrag_layout,
)
from app.schemas.extraction import StructuredExtraction


def _record(seq: int, category: str, text: str, bbox: list[float]) -> dict[str, object]:
    return {
        "id": f"docling-p1-{seq}",
        "engine": "docling",
        "page": 1,
        "seq_no": seq,
        "bbox": bbox,
        "coord_system": "image_top_left",
        "page_width": 1000,
        "page_height": 1400,
        "category": category,
        "text": text,
        "confidence": None,
        "raw_type": category.lower(),
        "raw": {},
    }


def _extraction() -> StructuredExtraction:
    records = [
        _record(1, "Title", "受注登録マニュアル", [50, 40, 900, 90]),
        _record(2, "Section-header", "受注入力画面", [50, 120, 600, 160]),
        _record(3, "Text", "受注番号を入力し、登録ボタンを押します。", [50, 180, 900, 240]),
        _record(
            4,
            "Table",
            "<table><tr><th>項目</th><th>説明</th></tr><tr><td>受注番号</td><td>必須</td></tr></table>",
            [50, 260, 900, 420],
        ),
    ]
    return StructuredExtraction(
        raw_text="受注登録マニュアル",
        parser_artifacts={
            "docrag_layout": {
                "version": 1,
                "pages": [
                    {"page": 1, "width": 1000, "height": 1400, "pdf_width": 600, "pdf_height": 840}
                ],
                "records": records,
            }
        },
    )


def test_docrag_chunks_keep_parent_search_text_and_element_refs() -> None:
    chunks = build_docrag_chunks(_extraction(), source_name="manual.pdf")

    assert chunks
    body = next(chunk for chunk in chunks if "登録ボタン" in chunk.text)
    metadata = body.metadata
    search_text = metadata[DOCRAG_SEARCH_TEXT_KEY]
    assert isinstance(search_text, str) and "登録ボタン" in search_text
    assert "manual.pdf" in search_text
    assert metadata["chunk_group_id"] == metadata["parent_chunk_id"]
    assert "登録ボタン" in str(metadata["docrag_parent_text"])
    assert "docling-p1-3" in str(metadata["element_ids"]).split(",")
    assert metadata["page_start"] == 1
    assert json.loads(str(metadata["bbox"]))[0] == 50.0
    assert json.loads(str(metadata["docrag_metadata_json"]))["schema_version"] == 4
    table = next(chunk for chunk in chunks if chunk.metadata["content_kind"] == "table")
    assert "受注番号" in table.text
    # Oracle Text へは rag_poc の search_text を索引する。
    assert _chunk_search_text(body) == search_text


def test_docrag_strategy_requires_docling_layout() -> None:
    assert normalize_chunking_strategy("docrag_small_to_big") == "docrag_small_to_big"
    plain = StructuredExtraction(raw_text="本文")
    assert not has_docrag_layout(plain)
    with pytest.raises(DocragLayoutMissingError):
        build_docrag_chunks(plain)
