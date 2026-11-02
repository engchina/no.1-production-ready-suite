"""PDF の字の配置と線から分数・上付き下付きを本文に戻す処理のテスト（#4140）。合成の字だけを使う。"""

from __future__ import annotations

from pathlib import Path

import pytest

from rag_engine.adapters.parsers.docling_adapter import _restore_math_layout
from rag_engine.models.layout import LayoutRecord, PageImage
from rag_engine.parsing.math_layout import MathBlock, PdfGlyph, PdfRule, restore_math_text
from rag_engine.parsing.rendering import pdf_math_page_contents

_SIZE = 10.0
_PAGE_BOX = (0.0, 0.0, 600.0, 800.0)


def _run(text: str, left: float, baseline: float, size: float = _SIZE) -> list[PdfGlyph]:
    """等幅の字の並び。空白は字を置かず、位置だけ進める。"""
    glyphs = []
    for index, char in enumerate(text):
        start = left + index * size * 0.6
        if char != " ":
            space_before = index > 0 and text[index - 1] == " "
            glyphs.append(PdfGlyph(char, start, start + size * 0.5, baseline, size, space_before))
    return glyphs


def _block(text: str = "", *, is_formula: bool = True, key: str = "a") -> MathBlock:
    return MathBlock(key=key, box=_PAGE_BOX, text=text, is_formula=is_formula)


def _fraction(numerator: str, denominator: str, left: float, rule_y: float) -> tuple[list[PdfGlyph], PdfRule]:
    """線の幅に中央をそろえた分子と分母（基準線は線の上下）。"""
    width = max(len(numerator), len(denominator)) * _SIZE * 0.6
    glyphs = [
        *_run(numerator, left + (width - len(numerator) * _SIZE * 0.6) / 2, rule_y + 3),
        *_run(denominator, left + (width - len(denominator) * _SIZE * 0.6) / 2, rule_y - 11),
    ]
    return glyphs, PdfRule(rule_y, left, left + width)


def _text_of(blocks: list[MathBlock], glyphs: list[PdfGlyph], *rules: PdfRule, vertical: tuple[PdfRule, ...] = ()) -> list[str]:
    return [item.text for item in restore_math_text(blocks, glyphs, rules, vertical)]


def test_fraction_keeps_numerator_and_denominator_apart() -> None:
    """同じ行に平らになると分母が分からない式で、分母を読み取れる本文になる。"""
    parts, rule = _fraction("1", "1-x", 80.0, 500.0)
    glyphs = [*_run("A*", 50.0, 497.0), *parts, *_run("B", 110.0, 497.0)]

    assert _text_of([_block("A* 1 1-x B")], glyphs, rule) == ["A* 1/(1-x) B"]


@pytest.mark.parametrize(
    ("numerator", "denominator", "expected"),
    [
        ("p", "q", "p/q"),
        ("a+b", "c-d", "(a+b)/(c-d)"),
        ("(a+b)", "c", "(a+b)/c"),
        ("(a)+(b)", "c", "((a)+(b))/c"),
    ],
    ids=["atoms", "terms", "enclosed", "not-enclosed"],
)
def test_operands_are_parenthesized_only_when_needed(numerator: str, denominator: str, expected: str) -> None:
    glyphs, rule = _fraction(numerator, denominator, 100.0, 500.0)

    assert _text_of([_block(f"{numerator} {denominator}")], glyphs, rule) == [expected]


def test_adjacent_and_nested_fractions() -> None:
    first, first_rule = _fraction("a", "b", 100.0, 500.0)
    second, second_rule = _fraction("c", "d", 130.0, 500.0)
    plus = _run("+", 118.0, 497.0)

    assert _text_of([_block("a b + c d")], [*first, *plus, *second], first_rule, second_rule) == ["a/b + c/d"]

    # 内側の分数が外側の分子になる。外側の線は長く、内側を先に戻す。
    inner, inner_rule = _fraction("a", "b", 110.0, 520.0)
    outer_rule = PdfRule(503.0, 100.0, 126.0)
    denominator = _run("c", 110.0, 492.0)

    assert _text_of([_block("a b c")], [*inner, *denominator], inner_rule, outer_rule) == ["(a/b)/c"]


def test_scripts_are_restored_in_formula_blocks() -> None:
    glyphs = [
        *_run("x", 100.0, 500.0),
        *_run("2", 106.0, 504.0, size=6.0),
        *_run("+", 118.0, 500.0),
        *_run("a", 130.0, 500.0),
        *_run("ij", 136.0, 497.0, size=6.0),
    ]

    assert _text_of([_block("x 2 + a ij")], glyphs) == ["x^2 + a_{ij}"]


def test_scripts_inside_fraction_are_restored() -> None:
    glyphs, rule = _fraction("xy", "1-x", 100.0, 500.0)
    glyphs = [*glyphs, *_run("2", 113.0, 507.0, size=6.0)]

    assert _text_of([_block("x y 2 1-x")], glyphs, rule) == ["(xy^2)/(1-x)"]


def test_small_raised_marker_in_body_text_is_left_alone() -> None:
    """数式と判定されていない本文の上付き（脚注の番号など）は変えない。"""
    glyphs = [*_run("note", 100.0, 500.0), *_run("1", 125.0, 504.0, size=6.0)]

    assert _text_of([_block("note 1", is_formula=False)], glyphs) == []


def test_fraction_in_body_text_is_restored() -> None:
    """解析エンジンが本文と判定しても、線と配置で分数と分かるものは戻す。"""
    parts, rule = _fraction("1", "1-x", 80.0, 500.0)

    assert _text_of([_block("A* 1 1-x", is_formula=False)], [*_run("A*", 50.0, 497.0), *parts], rule) == ["A* 1/(1-x)"]


def test_stacked_text_without_a_rule_is_not_guessed() -> None:
    """線の無い積み重ね（2 行の式・行列と区別できない）は分数にしない。"""
    parts, _rule = _fraction("1", "1-x", 80.0, 500.0)

    assert _text_of([_block("A* 1 1-x")], [*_run("A*", 50.0, 497.0), *parts]) == []


def test_lines_that_are_not_fraction_bars_are_ignored() -> None:
    upper = _run("underlined", 100.0, 503.0)
    lower = _run("next", 100.0, 489.0)
    # 下線: 上の行の幅の線の下に、左にそろった短い行がある。
    assert _text_of([_block("underlined next")], [*upper, *lower], PdfRule(500.0, 100.0, 160.0)) == []
    # 下線が行の一部だけ: 線の外へ同じ基準線の字が続く。
    assert _text_of([_block("underlined next")], [*upper, *_run("next", 110.0, 489.0)], PdfRule(500.0, 118.0, 136.0)) == []
    # 取り消し線: 線が字を貫く。
    struck = _run("abc", 100.0, 500.0)
    assert _text_of([_block("abc")], struck, PdfRule(503.0, 100.0, 116.0)) == []
    # 区切りの線: 分子・分母より大きく長い。
    parts, _ = _fraction("1", "2", 300.0, 500.0)
    assert _text_of([_block("1 2")], parts, PdfRule(500.0, 72.0, 540.0)) == []


def test_table_borders_are_not_fraction_bars() -> None:
    parts, rule = _fraction("a", "b", 100.0, 500.0)
    # 縦の罫線につながる線と、同じ高さで続く線。
    corner = PdfRule(rule.start, 480.0, 520.0)
    assert _text_of([_block("a b")], parts, rule, vertical=(corner,)) == []
    next_cell = PdfRule(rule.position, rule.end, rule.end + 40.0)
    assert _text_of([_block("a b")], parts, rule, next_cell) == []


def test_text_layer_that_disagrees_with_the_engine_text_is_not_used() -> None:
    """text layer の符号化が崩れているとき、解析エンジンの本文を残す。"""
    parts, rule = _fraction("1", "1-x", 80.0, 500.0)

    assert _text_of([_block("まったく別の本文です")], parts, rule) == []


def test_fraction_split_across_blocks_is_merged_into_first() -> None:
    """解析エンジンが式を別の塊に分けても、分子と分母を 1 つの本文に戻す。"""
    parts, rule = _fraction("1", "1-x", 80.0, 500.0)
    blocks = [
        MathBlock("lead", (45.0, 494.0, 78.0, 506.0), "A*"),
        MathBlock("num", (78.0, 502.0, 100.0, 514.0), "1"),
        MathBlock("den", (78.0, 487.0, 100.0, 499.0), "1-x"),
        MathBlock("other", (45.0, 400.0, 300.0, 412.0), "unrelated"),
    ]
    glyphs = [*_run("A*", 60.0, 497.0), *parts, *_run("unrelated", 50.0, 402.0)]

    (restored,) = restore_math_text(blocks, glyphs, [rule])

    assert restored.key == "lead"
    assert restored.merged_keys == ("lead", "num", "den")
    assert restored.text == "A* 1/(1-x)"
    assert restored.fraction_count == 1


def test_paragraph_in_next_column_is_not_merged_with_fraction() -> None:
    """同じ高さに隣の段の段落があっても、分数の塊にまとめない。"""
    parts, rule = _fraction("1", "1-x", 80.0, 500.0)
    blocks = [
        MathBlock("formula", (45.0, 487.0, 100.0, 514.0), "1 1-x"),
        MathBlock("column", (108.0, 300.0, 300.0, 600.0), "paragraph"),
    ]
    glyphs = [*parts, *_run("paragraph", 110.0, 497.0)]

    (restored,) = restore_math_text(blocks, glyphs, [rule])

    assert restored.merged_keys == ("formula",)
    assert restored.text == "1/(1-x)"


def test_no_text_layer_returns_nothing() -> None:
    assert restore_math_text([_block("1 1-x")], [], [PdfRule(500.0, 80.0, 100.0)]) == []


def _pdf_with_fraction(path: Path) -> Path:
    """実際の PDF の字と線（線は塗りの細い四角と線の 2 通り）。"""
    stream = b"\n".join(
        (
            b"BT /F1 10 Tf 90 697 Td (A *) Tj ET",
            b"BT /F1 10 Tf 120 703 Td (1) Tj ET",
            b"BT /F1 10 Tf 112 689 Td (1-x) Tj ET",
            b"110 699.7 24 0.6 re f",
            b"BT /F1 10 Tf 80 650 Td (C = d) Tj ET",
            b"BT /F1 6 Tf 104 654 Td (2) Tj ET",
            b"0.5 w 80 600 m 90 600 l S",
        )
    )
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 600 800] /Contents 4 0 R "
        b"/Resources << /Font << /F1 << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    body = b"%PDF-1.4\n"
    offsets = []
    for index, item in enumerate(objects, start=1):
        offsets.append(len(body))
        body += str(index).encode() + b" 0 obj\n" + item + b"\nendobj\n"
    xref = len(body)
    body += b"xref\n0 5\n0000000000 65535 f \n"
    body += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets)
    body += b"trailer\n<< /Size 5 /Root 1 0 R >>\nstartxref\n" + str(xref).encode() + b"\n%%EOF\n"
    path.write_bytes(body)
    return path


def _record(record_id: str, text: str, bbox: list[float], category: str = "Formula", **raw: object) -> LayoutRecord:
    return LayoutRecord(
        id=record_id,
        engine="docling",
        page=1,
        seq_no=int(record_id),
        bbox=bbox,
        coord_system="image_top_left",
        page_width=1200,
        page_height=1600,
        category=category,
        text=text,
        raw_type=category.lower(),
        raw=dict(raw),
    )


_PAGE = PageImage(page=1, width=1200, height=1600, pdf_width=600, pdf_height=800, image_path="page.png")


def test_reads_glyphs_and_rules_from_a_real_pdf(tmp_path: Path) -> None:
    pdf = _pdf_with_fraction(tmp_path / "source.pdf")

    glyphs, horizontal, vertical = pdf_math_page_contents(pdf, [1])[1]

    assert "".join(glyph.text for glyph in glyphs).startswith("A*1")
    assert [round(rule.position) for rule in horizontal] == [700, 600]
    assert vertical == []


def test_pdf_page_without_rules_skips_glyphs_unless_requested(tmp_path: Path) -> None:
    pdf = tmp_path / "no-rules.pdf"
    pdf.write_bytes(_pdf_with_fraction(tmp_path / "source.pdf").read_bytes().replace(b"re f", b"re n").replace(b"l S", b"l n"))

    assert pdf_math_page_contents(pdf, [1])[1] == ([], [], [])
    assert pdf_math_page_contents(pdf, [1], always_read_glyphs=[1])[1][0]


def test_adapter_restores_records_from_a_real_pdf(tmp_path: Path) -> None:
    """record の bbox（画像の座標）で PDF の字を引き、本文を戻してまとめる。"""
    pdf = _pdf_with_fraction(tmp_path / "source.pdf")
    item = {"label": "formula", "self_ref": "#/texts/0"}
    records = [
        _record("1", "A * 1 1-x", [150.0, 180.0, 300.0, 230.0], **item),
        _record("2", "C = d 2", [150.0, 280.0, 300.0, 320.0]),
        _record("3", "body", [150.0, 400.0, 300.0, 430.0], category="Text"),
    ]

    restored = _restore_math_layout(records, pdf, {1: _PAGE})

    assert [record.text for record in restored] == ["A * 1/(1-x)", "C = d^2", "body"]
    assert restored[0].raw["math_layout"] == {"fractions": 1, "scripts": 0, "merged_record_ids": []}
    # raw は同じ item を共有する record があるので、元を書き換えない。
    assert "math_layout" not in item


def test_adapter_merges_split_records_and_keeps_order(tmp_path: Path) -> None:
    pdf = _pdf_with_fraction(tmp_path / "source.pdf")
    records = [
        _record("1", "A *", [150.0, 190.0, 215.0, 215.0], category="Text"),
        _record("2", "1", [220.0, 180.0, 270.0, 199.0], category="Text"),
        _record("3", "1-x", [220.0, 201.0, 270.0, 225.0]),
    ]

    (merged,) = _restore_math_layout(records, pdf, {1: _PAGE})

    assert merged.id == "1"
    assert merged.text == "A * 1/(1-x)"
    assert merged.category == "Formula"
    assert merged.bbox == [150.0, 180.0, 270.0, 225.0]
    assert merged.raw["math_layout"]["merged_record_ids"] == ["2", "3"]


@pytest.mark.parametrize("pdf_path", ["source.png", "missing.pdf"], ids=["not-pdf", "unreadable"])
def test_adapter_keeps_records_when_pdf_cannot_be_read(pdf_path: str) -> None:
    records = [_record("1", "1 1-x", [150.0, 180.0, 300.0, 230.0])]

    assert _restore_math_layout(records, pdf_path, {1: _PAGE})[0].text == "1 1-x"
