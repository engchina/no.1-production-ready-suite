"""PowerPoint の原本の図形と接続線を、図の読み取りの入力と図の chunk に入れる（#5359）。

Office→PDF で変換した図では、線の端がどの図形につながるかが画像からしか分からない。合成の pptx
（開始 → 中継 → 宛先A / 宛先B、分岐の手前の共通の幹に「条件1 条件2」のラベル）の slide XML から
読んだ構造が、VLM の入力（metadata と読み方の指示）と、図の説明の本文・chunk に、Vision の項目と
別のグループで入ることを確かめる。VLM は決定論スタブ。
"""

from __future__ import annotations

import io
import json
import zipfile
from typing import Any

import pytest
from rag_engine.parsing.slide_shapes import (
    SOURCE_SLIDE_SHAPES_KEY,
    PresentationShapes,
    read_presentation_shapes,
)
from rag_engine.parsing.vision_prompt_rules import VISION_SOURCE_SHAPES_INSTRUCTION

from app.config import Settings
from app.rag.chunking_small_to_big import build_parent_child_chunks
from app.rag.ingestion import IngestionPipeline
from app.rag.source_profile import build_source_profile
from app.rag.vision import read_figures_with_vision
from tests.test_vision import (
    PAGE_PT,
    TEMPLATE,
    FakeDescriber,
    FakeReader,
    PromptOracle,
    _docling_extraction,
    _pdf,
)

EMU = 12700  # 1pt
P = "http://schemas.openxmlformats.org/presentationml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def _xfrm(x: float, y: float, cx: float, cy: float, flip: str = "") -> str:
    return (
        f'<a:xfrm{flip}><a:off x="{int(x * EMU)}" y="{int(y * EMU)}"/>'
        f'<a:ext cx="{int(cx * EMU)}" cy="{int(cy * EMU)}"/></a:xfrm>'
    )


def _sp(shape_id: int, text: str, box: tuple[float, float, float, float]) -> str:
    return (
        f'<p:sp><p:nvSpPr><p:cNvPr id="{shape_id}" name="s{shape_id}"/><p:cNvSpPr/><p:nvPr/>'
        f"</p:nvSpPr><p:spPr>{_xfrm(*box)}</p:spPr><p:txBody><a:bodyPr/><a:p><a:r>"
        f"<a:t>{text}</a:t></a:r></a:p></p:txBody></p:sp>"
    )


def _cxn(
    shape_id: int, source: int, target: int, box: tuple[float, float, float, float], flip: str = ""
) -> str:
    return (
        f'<p:cxnSp><p:nvCxnSpPr><p:cNvPr id="{shape_id}" name="c{shape_id}"/><p:cNvCxnSpPr>'
        f'<a:stCxn id="{source}" idx="2"/><a:endCxn id="{target}" idx="0"/></p:cNvCxnSpPr>'
        f"<p:nvPr/></p:nvCxnSpPr><p:spPr>{_xfrm(*box, flip=flip)}"
        '<a:ln><a:tailEnd type="triangle"/></a:ln></p:spPr></p:cxnSp>'
    )


# 図の領域（test_vision の FIGURE_PT = (100, 100)-(300, 250) pt）の中に置く。
DIAGRAM = "".join(
    [
        _sp(2, "開始", (150, 105, 100, 15)),
        _sp(3, "中継", (150, 135, 100, 15)),
        _sp(4, "宛先A", (110, 220, 60, 15)),
        _sp(5, "宛先B", (230, 220, 60, 15)),
        _sp(6, "条件1 条件2", (175, 155, 50, 10)),
        _cxn(10, 2, 3, (200, 120, 0, 15)),
        # 中継の下端の中央 (200, 150) から左下の宛先A へ（左右の反転）、右下の宛先B へ分かれる。
        _cxn(11, 3, 4, (140, 150, 60, 70), flip=' flipH="1"'),
        _cxn(12, 3, 5, (200, 150, 60, 70)),
    ]
)


def _pptx(slide_count: int = 1) -> bytes:
    ids = "".join(f'<p:sldId id="{256 + n}" r:id="rId{n}"/>' for n in range(1, slide_count + 1))
    rels = "".join(
        f'<Relationship Id="rId{n}" Type="{R}/slide" Target="slides/slide{n}.xml"/>'
        for n in range(1, slide_count + 1)
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "ppt/presentation.xml",
            f'<p:presentation xmlns:p="{P}" xmlns:r="{R}"><p:sldIdLst>{ids}</p:sldIdLst>'
            f'<p:sldSz cx="{int(PAGE_PT[0] * EMU)}" cy="{int(PAGE_PT[1] * EMU)}"/>'
            "</p:presentation>",
        )
        archive.writestr(
            "ppt/_rels/presentation.xml.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f"{rels}</Relationships>",
        )
        for n in range(1, slide_count + 1):
            archive.writestr(
                f"ppt/slides/slide{n}.xml",
                f'<p:sld xmlns:p="{P}" xmlns:a="{A}" xmlns:r="{R}"><p:cSld><p:spTree>'
                '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
                f"<p:grpSpPr/>{DIAGRAM}</p:spTree></p:cSld></p:sld>",
            )
    return buffer.getvalue()


def _read(slide_shapes: PresentationShapes | None) -> tuple[Any, FakeDescriber]:
    describer = FakeDescriber()
    result = read_figures_with_vision(
        _docling_extraction(),
        source_bytes=_pdf(),
        content_type="application/pdf",
        file_name="slides.pdf",
        parser_backend="docling",
        describer=describer,
        prompt_template=TEMPLATE,
        slide_shapes=slide_shapes,
    )
    return result, describer


def test_slide_structure_reaches_vision_input_figure_text_and_chunks() -> None:
    result, describer = _read(read_presentation_shapes(_pptx()))

    assert len(describer.calls) == 1
    structure = describer.calls[0]["metadata"][SOURCE_SLIDE_SHAPES_KEY]
    lines = structure.splitlines()
    # 2 本の枝はどちらも中継から出る。ラベルは位置つきの図形として書き、接続には結び付けない。
    assert any(line.startswith("- #11: #3 ") and " → #4 " in line for line in lines)
    assert any(line.startswith("- #12: #3 ") and " → #5 " in line for line in lines)
    assert any(line.startswith("- #6 (") and line.endswith(": 条件1 条件2") for line in lines)
    prompt = describer.calls[0]["prompt"]
    assert VISION_SOURCE_SHAPES_INSTRUCTION in prompt
    assert json.dumps(structure, ensure_ascii=False) in prompt

    picture = result.parser_artifacts["layout_records"]["records"][1]
    assert picture["raw"][SOURCE_SLIDE_SHAPES_KEY] == structure
    # Vision の説明の後に、原本の構造を別のグループで置く。
    assert "受注画面の登録ボタン1" in picture["text"]
    assert picture["text"].endswith("■ 原ファイルの図形\n" + structure)
    figure = next(element for element in result.elements if element.element_id == "docling-p1-2")
    assert figure.text == picture["text"]
    chunks = build_parent_child_chunks(result, source_name="slides.pptx")
    assert any(structure in chunk.text for chunk in chunks)


def test_slide_structure_is_not_used_when_slide_and_page_counts_differ() -> None:
    result, describer = _read(read_presentation_shapes(_pptx(slide_count=2)))

    assert SOURCE_SLIDE_SHAPES_KEY not in describer.calls[0]["metadata"]
    assert VISION_SOURCE_SHAPES_INSTRUCTION not in describer.calls[0]["prompt"]
    picture = result.parser_artifacts["layout_records"]["records"][1]
    assert SOURCE_SLIDE_SHAPES_KEY not in picture["raw"]
    assert "原ファイルの図形" not in picture["text"]


class _Storage:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.keys: list[str] = []

    async def get(self, key: str) -> bytes:
        self.keys.append(key)
        return self.data


def _pipeline(*, enabled: bool = True, storage: _Storage | None = None) -> Any:
    return IngestionPipeline(
        vlm=FakeReader(),  # type: ignore[arg-type]
        oracle=PromptOracle({}),  # type: ignore[arg-type]
        object_storage=storage,  # type: ignore[arg-type]
        settings=Settings(rag_vision_enabled=enabled),
    )


def _profile(name: str) -> Any:
    return build_source_profile(
        original_file_name=name,
        sanitized_file_name=name,
        content_type=None,
        file_size_bytes=1,
        content_sha256="0" * 64,
    )


async def _shapes(pipeline: Any, name: str, *, source: bytes | None, parsed: str) -> Any:
    return await pipeline._presentation_shapes(
        "trace",
        _profile(name),
        source_bytes=source,
        original_object_storage_path="local://uploaded/doc/original.pptx",
        parse_content_type=parsed,
    )


async def test_ingestion_reads_original_presentation_for_converted_pdf() -> None:
    shapes = await _shapes(_pipeline(), "slides.pptx", source=_pptx(), parsed="application/pdf")

    assert shapes is not None and shapes.page_count == 1 and list(shapes.slides) == [1]


async def test_ingestion_reads_original_from_storage_when_parsing_prepared_file() -> None:
    storage = _Storage(_pptx())

    shapes = await _shapes(
        _pipeline(storage=storage), "slides.pptx", source=None, parsed="application/pdf"
    )

    assert shapes is not None and list(shapes.slides) == [1]
    assert storage.keys == ["local://uploaded/doc/original.pptx"]


@pytest.mark.parametrize(
    ("enabled", "name", "source", "parsed"),
    [
        (False, "slides.pptx", b"", "application/pdf"),
        (True, "manual.pdf", b"", "application/pdf"),
        (True, "slides.pptx", b"", "application/vnd.ms-powerpoint"),
        (True, "slides.pptx", b"not a zip", "application/pdf"),
    ],
    ids=["vision_disabled", "not_presentation", "not_converted", "broken_source"],
)
async def test_ingestion_skips_structure_when_not_applicable(
    enabled: bool, name: str, source: bytes, parsed: str
) -> None:
    storage = _Storage(b"")

    shapes = await _shapes(
        _pipeline(enabled=enabled, storage=storage), name, source=source, parsed=parsed
    )

    assert shapes is None
    assert storage.keys == []


async def test_ingestion_vision_stage_passes_structure_and_saved_reading_instruction() -> None:
    """取込の Vision の段が構造を読み取りへ渡し、画面で編集した読み方の指示を使う。"""
    reader = FakeReader()
    pipeline = _pipeline()
    pipeline._vlm = reader
    pipeline._oracle = PromptOracle({"picture_description.sourceShapes": "### 合成の読み方"})

    result = await pipeline._attach_vision(
        "trace",
        _docling_extraction(),
        source_bytes=_pdf(),
        content_type="application/pdf",
        file_name="slides.pdf",
        parser_backend="docling",
        cancel_checker=None,
        slide_shapes=read_presentation_shapes(_pptx()),
    )

    assert len(reader.calls) == 1
    prompt = reader.calls[0]["prompt"]
    assert "### 合成の読み方" in prompt and VISION_SOURCE_SHAPES_INSTRUCTION not in prompt
    assert SOURCE_SLIDE_SHAPES_KEY in prompt
    figure = next(element for element in result.elements if element.element_id == "docling-p1-2")
    assert "■ 原ファイルの図形" in figure.text and "- #12: #3 " in figure.text
