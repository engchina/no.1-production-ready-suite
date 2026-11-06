"""解析サービスの crop 保存 → backend の Vision の書き戻し → 分割を、初回の取込で通す（#4569）。

Docling の解析サービスは VLM の前に ``persist_semantic_visual_crops`` を実行する。本文が空で
画像の中に意味のある文字がある Picture に、そこで空の画像本文の投影を書くと、backend の Vision の
書き戻しの前の検査と分割の検査が不正として止まる。合成の PDF と決定論のスタブで確かめる。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
from PIL import Image
from rag_engine.chunking.records import _vision_projection_status
from rag_engine.models.layout import LayoutRecord, PageImage
from rag_engine.parsing.layout_metadata import layout_record_element_metadata
from rag_engine.parsing.visual_artifacts import persist_semantic_visual_crops
from rag_parser_core.extraction import DocumentElement
from rag_parser_core.result import ParserRegistryResult
from rag_parser_core.vision_text import VISION_TEXT_PROJECTION_KEY, VisionTextProjection

from app.schemas.extraction import StructuredExtraction
from tests.test_ingestion_pdf_segments import _docling_like_extraction, _pdf
from tests.test_vision_pipeline import (
    PromptOracle,
    Storage,
    _ingest,
    _install,
    _pipeline,
    _saved_extraction,
)

PICTURE_BBOX = [100.0, 250.0, 500.0, 650.0]


class Reader:
    """Vision のスタブ（画面の項目を読み取った説明を返す）。"""

    def __init__(self) -> None:
        self.calls = 0

    async def generate_from_images(self, images: Any, prompt: str, **_: Any) -> str:
        self.calls += 1
        return json.dumps(
            {"retrieval_text": "画面Aの登録の操作です。", "visible_buttons": ["登録"]},
            ensure_ascii=False,
        )

    async def generate(self, prompt: str, context: str, **_: Any) -> str:
        return "[]"


def _picture(record_id: str, seq_no: int, text: str, raw_type: str) -> LayoutRecord:
    return LayoutRecord(
        id=record_id,
        engine="docling",
        page=1,
        seq_no=seq_no,
        bbox=list(PICTURE_BBOX),
        coord_system="image_top_left",
        page_width=600.0,
        page_height=800.0,
        category="Picture",
        text=text,
        raw_type=raw_type,
    )


def _service_extraction(source_bytes: bytes, work: Path) -> StructuredExtraction:
    """解析サービスと同じ順（解析 → crop の保存 → 抽出の形への変換）で結果を作る。"""
    base = _docling_like_extraction(source_bytes)
    layout = dict(cast(dict[str, Any], base.parser_artifacts["layout_records"]))
    records = [LayoutRecord(**record) for record in layout["records"]]
    # 本文が空の Picture と、同じ bbox の画像内 OCR（操作の語を含む）。
    picture = _picture("docling-p1-8", 8, "", "picture")
    ocr = _picture("docling-p1-9", 9, "登録ボタン", "picture_ocr_text")
    records.extend([picture, ocr])
    image = work / "page-1.png"
    Image.new("RGB", (600, 800), "white").save(image)
    persist_semantic_visual_crops(
        records,
        [PageImage(1, 600, 800, 612.0, 792.0, str(image))],
        run_dir=work / "run",
    )
    elements = list(base.elements) + [
        DocumentElement(
            kind="figure",
            text=record.text,
            element_id=record.id,
            source_parser="docling_layout",
            page_number=record.page,
            bbox=list(record.bbox),
            metadata=dict(layout_record_element_metadata(record)),
        )
        for record in (picture, ocr)
    ]
    layout["records"] = [record.to_dict() for record in records]
    layout["pages"] = [
        {**page, "pdf_width": 612.0, "pdf_height": 792.0} for page in layout["pages"]
    ]
    return base.model_copy(
        update={
            "elements": elements,
            "raw_text": "\n\n".join(element.text for element in elements if element.text.strip()),
            "parser_artifacts": {**base.parser_artifacts, "layout_records": layout},
        }
    )


def _install_service(monkeypatch: pytest.MonkeyPatch, work: Path) -> None:
    def run(backend: str, source_bytes: bytes, *_: Any, **__: Any) -> ParserRegistryResult:
        return ParserRegistryResult(
            extraction=_service_extraction(source_bytes, work),
            parser_backend=backend,
            parser_version="2.132.0",
            template="docling_layout",
        )

    _install(monkeypatch, cast(Any, run))


async def test_first_ingest_keeps_vision_of_picture_with_visual_text(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """初回の取込でも、画像の中に文字がある図の VLM の説明が書き戻され、分割まで通る。"""
    _install_service(monkeypatch, tmp_path)
    reader = Reader()
    oracle = PromptOracle()

    await _ingest(_pipeline(cast(Any, reader), oracle, Storage()), _pdf(1))

    assert reader.calls == 1
    assert oracle.saved_chunk_count > 0
    extraction = _saved_extraction(oracle)
    layout = cast(dict[str, Any], extraction.parser_artifacts["layout_records"])
    picture = next(record for record in layout["records"] if record["id"] == "docling-p1-8")
    assert picture["raw"]["visual_role_reason"] == "meaningful_text_in_visual"
    assert picture["raw"]["vision_status"] == "succeeded"
    assert "登録" in picture["text"]
    assert _vision_projection_status(picture) == "verified"
    assert "vision_failed" not in extraction.warnings


def test_service_crop_step_leaves_no_projection_before_vision(tmp_path: Path) -> None:
    """解析サービスの出力（VLM の前）には、図の画像本文の投影が無い。"""
    extraction = _service_extraction(_pdf(1), tmp_path)
    layout = cast(dict[str, Any], extraction.parser_artifacts["layout_records"])
    picture = next(record for record in layout["records"] if record["id"] == "docling-p1-8")
    assert picture["raw"]["visual_role_reason"] == "meaningful_text_in_visual"
    assert VISION_TEXT_PROJECTION_KEY not in picture["raw"]
    assert _vision_projection_status(picture) is None


def test_invalid_projection_failure_reaches_the_job_as_a_safe_message() -> None:
    """不正な投影で分割が止まったとき、job には汎用の文ではなく直し方を示す文を残す。"""
    from app.api.routes import documents

    record = {
        "id": "picture-a",
        "category": "Picture",
        "text": "",
        "raw": {VISION_TEXT_PROJECTION_KEY: VisionTextProjection("", "").dumps()},
    }
    with pytest.raises(ValueError) as caught:
        _vision_projection_status(record)
    message = documents._safe_ingestion_job_error_message(caught.value)
    assert message != "取込処理に失敗しました。"
    assert "画像本文の投影" in message and "再解析" in message
