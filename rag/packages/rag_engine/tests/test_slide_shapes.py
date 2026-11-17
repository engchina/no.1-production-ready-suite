"""PowerPoint の原本の図形と接続線を形式どおりに読み、図の読み取りの入力と説明に入れる（#5359）。

合成の slide XML で、分岐の手前の共通の幹にラベルが 1 つだけある図（開始 → 中継 → 宛先A / 宛先B、
中継の下に「条件1 条件2」）を作る。ラベルがどの接続に付くかはコードで決めず、接続の端点と図形の位置
だけを確定した事実として書くことを確かめる。
"""

from __future__ import annotations

import io
import zipfile

import pytest

from rag_engine.parsing.picture_descriptions import vision_description_text, vision_text_projection
from rag_engine.parsing.slide_shapes import read_presentation_shapes, slide_shapes_text
from rag_engine.parsing.vision_prompt_rules import (
    SOURCE_SLIDE_SHAPES_KEY,
    VISION_SOURCE_SHAPES_INSTRUCTION,
    vision_context_instructions,
)

EMU = 12700  # 1pt
SLIDE = (720 * EMU, 540 * EMU)
P = "http://schemas.openxmlformats.org/presentationml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
MC = "http://schemas.openxmlformats.org/markup-compatibility/2006"


def _xfrm(x: float, y: float, cx: float, cy: float, *, rot: int = 0, flip_h: bool = False, tag: str = "a:xfrm") -> str:
    attrs = (f' rot="{rot}"' if rot else "") + (' flipH="1"' if flip_h else "")
    return f'<{tag}{attrs}><a:off x="{int(x * EMU)}" y="{int(y * EMU)}"/><a:ext cx="{int(cx * EMU)}" cy="{int(cy * EMU)}"/></{tag}>'


def _sp(shape_id: int, text: str, box: tuple[float, float, float, float]) -> str:
    body = f"<p:txBody><a:bodyPr/><a:p><a:r><a:t>{text}</a:t></a:r></a:p></p:txBody>" if text else ""
    return (
        f'<p:sp><p:nvSpPr><p:cNvPr id="{shape_id}" name="s{shape_id}"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr>'
        f"<p:spPr>{_xfrm(*box)}</p:spPr>{body}</p:sp>"
    )


def _cxn(
    shape_id: int,
    source: int | None,
    target: int | None,
    box: tuple[float, float, float, float],
    *,
    rot: int = 0,
    flip_h: bool = False,
    tail: str = "triangle",
    adjust: int | None = None,
) -> str:
    st = f'<a:stCxn id="{source}" idx="2"/>' if source else ""
    end = f'<a:endCxn id="{target}" idx="0"/>' if target else ""
    gd = f'<a:gd name="adj1" fmla="val {adjust}"/>' if adjust is not None else ""
    return (
        f'<p:cxnSp><p:nvCxnSpPr><p:cNvPr id="{shape_id}" name="c{shape_id}"/><p:cNvCxnSpPr>{st}{end}</p:cNvCxnSpPr><p:nvPr/></p:nvCxnSpPr>'
        f'<p:spPr>{_xfrm(*box, rot=rot, flip_h=flip_h)}<a:prstGeom prst="bentConnector3"><a:avLst>{gd}</a:avLst></a:prstGeom>'
        f'<a:ln><a:tailEnd type="{tail}"/></a:ln></p:spPr></p:cxnSp>'
    )


def _slide(shapes: str, *, hidden: bool = False) -> str:
    show = ' show="0"' if hidden else ""
    return (
        f'<p:sld xmlns:p="{P}" xmlns:a="{A}" xmlns:r="{R}" xmlns:mc="{MC}"{show}><p:cSld><p:spTree>'
        '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr/>'
        f"{shapes}</p:spTree></p:cSld></p:sld>"
    )


def _pptx(slides: list[str]) -> bytes:
    ids = "".join(f'<p:sldId id="{256 + index}" r:id="rId{index + 1}"/>' for index in range(len(slides)))
    rels = "".join(
        f'<Relationship Id="rId{index + 1}" Type="{R}/slide" Target="slides/slide{index + 1}.xml"/>' for index in range(len(slides))
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "ppt/presentation.xml",
            f'<p:presentation xmlns:p="{P}" xmlns:r="{R}"><p:sldIdLst>{ids}</p:sldIdLst>'
            f'<p:sldSz cx="{SLIDE[0]}" cy="{SLIDE[1]}"/></p:presentation>',
        )
        archive.writestr(
            "ppt/_rels/presentation.xml.rels",
            f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{rels}</Relationships>',
        )
        for index, slide in enumerate(slides, start=1):
            archive.writestr(f"ppt/slides/slide{index}.xml", slide)
    return buffer.getvalue()


# 開始(2) → 中継(3)。中継の下端の中央 (300, 160) から、宛先A(4) へ右下、宛先B(5) へ左下に分かれる。
# 「条件1 条件2」(6) は分岐の手前、中継の真下の共通の幹にある。宛先B はグループの中（子の座標は半分の縮尺）。
DIAGRAM = "".join(
    [
        _sp(9, "合成の見出し", (40, 10, 300, 20)),
        _sp(2, "開始", (250, 60, 100, 40)),
        _sp(3, "中継", (250, 120, 100, 40)),
        _sp(4, "宛先A", (400, 300, 100, 40)),
        '<p:grpSp><p:nvGrpSpPr><p:cNvPr id="20" name="g"/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
        '<p:grpSpPr><a:xfrm><a:off x="1270000" y="3810000"/><a:ext cx="1270000" cy="508000"/>'
        '<a:chOff x="0" y="0"/><a:chExt cx="2540000" cy="1016000"/></a:xfrm></p:grpSpPr>'
        f"{_sp(5, '宛先B', (0, 0, 200, 80))}</p:grpSp>",
        _sp(6, "条件1 条件2", (260, 170, 80, 20)),
        _cxn(10, 2, 3, (300, 100, 0, 20)),
        # 中継の下端の中央 (300, 160) → 宛先A の上端の中央 (450, 300)。折れる位置は調整値（幅の 40%）。
        _cxn(11, 3, 4, (300, 160, 150, 140), adjust=40000),
        # 左右の反転: 枠の右上 (300, 160) が始点、左下の宛先B の上端の中央 (150, 300) が終点。
        _cxn(12, 3, 5, (150, 160, 150, 140), flip_h=True),
        # 互換の選択肢の中の図形（Fallback を読む）。
        f'<mc:AlternateContent><mc:Choice Requires="x">{_sp(30, "選択肢", (0, 0, 1, 1))}</mc:Choice>'
        f"<mc:Fallback>{_sp(31, '代替', (600, 500, 50, 20))}</mc:Fallback></mc:AlternateContent>",
    ]
)


def _flat(points) -> list[float]:
    return [value for point in points for value in point]


def _shapes():
    hidden = _slide(_sp(2, "非表示", (0, 0, 10, 10)) + _cxn(3, 2, None, (0, 0, 5, 5)), hidden=True)
    return read_presentation_shapes(_pptx([hidden, _slide(DIAGRAM), _slide(_sp(2, "本文", (0, 0, 10, 10)))]))


def test_hidden_slides_are_not_pages_and_slides_without_connectors_are_skipped():
    shapes = _shapes()

    # 非表示のスライドは Office→PDF の頁にならない。接続線の無いスライドは構造を持たない。
    assert shapes.page_count == 2
    assert list(shapes.slides) == [1]


def test_connections_and_positions_are_read_from_the_slide_xml():
    slide = _shapes().slides[1]
    by_id = {connector.connector_id: connector for connector in slide.connectors}
    assert {key: (item.source, item.target) for key, item in by_id.items()} == {
        "10": ("2", "3"),
        "11": ("3", "4"),
        "12": ("3", "5"),
    }
    assert all(item.arrow_at_end and not item.arrow_at_start for item in by_id.values())
    # 2 本の枝は同じ点（中継の下端の中央）から出る。反転した線は右上が始点になる。
    assert by_id["11"].start == pytest.approx((300 * EMU, 160 * EMU))
    assert by_id["11"].end == pytest.approx((450 * EMU, 300 * EMU))
    assert by_id["12"].start == pytest.approx((300 * EMU, 160 * EMU))
    assert by_id["12"].end == pytest.approx((150 * EMU, 300 * EMU))
    # カギ線の折れ点（横 → 縦 → 横）。幅 0 の線は重なる点を 1 つにする。
    assert _flat(by_id["11"].path) == pytest.approx([value * EMU for value in (300, 160, 360, 160, 360, 300, 450, 300)])
    assert len(by_id["10"].path) == 2
    shapes = {shape.shape_id: shape for shape in slide.shapes}
    assert shapes["6"].text == "条件1 条件2"
    assert shapes["6"].box == pytest.approx((260 * EMU, 170 * EMU, 340 * EMU, 190 * EMU))
    # グループの子は、グループの縮尺と位置でスライドの座標へ写す。
    assert shapes["5"].box == pytest.approx((100 * EMU, 300 * EMU, 200 * EMU, 340 * EMU))
    assert "31" in shapes and "30" not in shapes


def test_rotated_connector_ends_follow_flip_then_rotation():
    slide = read_presentation_shapes(
        _pptx([_slide(_sp(2, "上", (0, 0, 10, 10)) + _sp(3, "下", (0, 100, 10, 10)) + _cxn(4, 2, 3, (-40, 50, 100, 20), rot=5400000))])
    ).slides[1]
    connector = slide.connectors[0]
    # 横 100・縦 20 の枠を中心 (10, 60) で 90 度回すと、縦 → 横 → 縦のカギ線になり、左上の始点は (20, 10)、
    # 右下の終点は (0, 110) になる。
    assert _flat(connector.path) == pytest.approx([value * EMU for value in (20, 10, 20, 60, 0, 60, 0, 110)], abs=1e-3)


def test_text_lists_region_shapes_and_every_branch_without_assigning_labels():
    slide = _shapes().slides[1]
    # 頁の画像は 1pt = 2px。図の領域は (200, 100)-(1100, 700) px（スライドの (100, 50)-(550, 350) pt）。
    text = slide_shapes_text(slide, page_size=(1440, 1080), region=(200, 100, 1100, 700))

    lines = text.splitlines()
    assert lines[0] == "図形:"
    assert "- #6 (520,340)-(680,380): 条件1 条件2" in lines
    assert "- #5 (200,600)-(400,680): 宛先B" in lines
    assert "接続線:" in lines
    # 2 本の枝は同じ点 (600,320) から始まる。線の点は始点から終点の順。
    assert "- #11: #3 → #4 (600,320)-(720,320)-(720,600)-(900,600)" in lines
    assert "- #12: #3 → #5 (600,320)-(450,320)-(450,600)-(300,600)" in lines
    assert "- #10: #2 → #3 (600,200)-(600,240)" in lines
    # 領域の外の文字の図形は書かない。ラベルを接続に結び付けた行は作らない。
    assert "合成の見出し" not in text and "代替" not in text
    assert not any("条件1" in line for line in lines if line.startswith("- #1"))


def test_region_without_connectors_has_no_text():
    slide = _shapes().slides[1]

    assert slide_shapes_text(slide, page_size=(1440, 1080), region=(1150, 950, 1400, 1070)) == ""


def test_projection_keeps_source_structure_apart_from_vision_fields():
    description = {"retrieval_text": "合成の図の説明", "search_keywords": ["合成の語"]}
    structure = "図形:\n- #6 (1,2)-(3,4): 条件1 条件2"

    answer = vision_description_text(description, for_answer=True, source_shapes=structure)
    index = vision_description_text(description, source_shapes=structure)

    assert answer.endswith("■ 原ファイルの図形\n" + structure)
    assert index.index("■ 原ファイルの図形") < index.index("■ 検索")
    projection = vision_text_projection(description, source_shapes=structure)
    assert (projection.answer_description, projection.index_text) == (answer, index)
    assert "原ファイルの図形" not in vision_description_text(description)


def test_reading_instruction_is_added_only_with_source_structure():
    assert VISION_SOURCE_SHAPES_INSTRUCTION in vision_context_instructions({SOURCE_SLIDE_SHAPES_KEY: "図形:"})
    assert VISION_SOURCE_SHAPES_INSTRUCTION not in vision_context_instructions({SOURCE_SLIDE_SHAPES_KEY: ""})
