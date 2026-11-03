"""PDF の字の配置と線から分数・上付き下付きを本文に戻す処理のテスト（#4140・#4154）。合成の字だけを使う。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from rag_engine.adapters.parsers.docling_adapter import _restore_math_layout
from rag_engine.models.layout import LayoutRecord, PageImage
from rag_engine.parsing.math_layout import MathBlock, PdfGlyph, PdfMathPage, PdfRule, restore_math_text
from rag_engine.parsing.picture_text import PICTURE_OCR_TEXT_RAW_TYPE, picture_ocr_text_label
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


def test_stacked_text_without_an_operator_is_not_guessed() -> None:
    """線が無く、同じ行の隣が演算子でない積み重ねは分数にしない。"""
    parts, _rule = _fraction("1", "1-x", 80.0, 500.0)

    assert _text_of([_block("A 1 1-x")], [*_run("A", 62.0, 497.0), *parts]) == []


def _stack(numerator: str, denominator: str, left: float, axis: float = 503.5) -> list[PdfGlyph]:
    """線の無い分子と分母。同じ行の中心（基準線 500）の高さを挟んで上下に同じ距離に置く。"""
    width = max(len(numerator), len(denominator)) * _SIZE * 0.6
    center_offset = _SIZE * 0.35
    return [
        *_run(numerator, left + (width - len(numerator) * _SIZE * 0.6) / 2, axis + 8.0 - center_offset),
        *_run(denominator, left + (width - len(denominator) * _SIZE * 0.6) / 2, axis - 8.0 - center_offset),
    ]


def _restored_text(*glyphs: list[PdfGlyph], text: str = "x") -> list[str]:
    flat = [glyph for group in glyphs for glyph in group]
    return [item.text for item in restore_math_text([_block(text)], flat, [])]


def test_stack_between_operators_is_a_fraction() -> None:
    """同じ行の字が上下の真ん中の高さで左右に続き、演算子で隣り合うなら分数と言える。"""
    assert _restored_text(
        _run("G =", 50.0, 500.0), _stack("p", "q", 72.0), _run("+", 82.0, 500.0), _stack("r", "s", 92.0), text="G = p q + r s"
    ) == ["G = p/q + r/s"]


def test_stack_with_different_widths_centered_is_a_fraction() -> None:
    """右に続きが無くても、幅が違うのに中央がそろう並びは左そろえの 2 行ではない。"""
    assert _restored_text(_run("A *", 50.0, 500.0), _stack("1", "1-x", 72.0), text="A * 1 1-x") == ["A * 1/(1-x)"]


@pytest.mark.parametrize(
    "glyphs",
    [
        # 幅が同じで右に続きが無い（左そろえの 2 行と区別できない）。
        [*_run("K =", 50.0, 500.0), *_stack("u", "v", 72.0)],
        # 幅が違い、左にそろう。
        [*_run("K =", 50.0, 500.0), *_run("a", 72.0, 511.0), *_run("abc", 72.0, 495.0)],
        # 2 行の式: 同じ行の字が上の並びの高さにある。
        [*_run("m = a + b", 50.0, 500.0), *_run("+ c + d", 72.0, 486.0)],
        # 同じ行の隣が演算子でない（行列・語の並び）。
        [*_run("M", 62.0, 500.0), *_stack("g", "i", 72.0), *_run("N", 82.0, 500.0)],
        # 隣が開き括弧と閉じ括弧（縦のベクトル・組み合わせの記号）。
        [*_run("=(", 60.0, 500.0), *_stack("e", "f", 72.0), *_run(")", 82.0, 500.0)],
        # 3 行の並び。
        [*_run("=", 62.0, 500.0), *_stack("a", "b", 72.0), *_run("c", 72.0, 480.0), *_run("+", 82.0, 500.0)],
        # 上付きを含む（字の並びが分かれる）。
        [*_run("=", 62.0, 500.0), *_stack("a", "b", 72.0), *_run("2", 72.0, 516.0, size=6.0), *_run("+", 82.0, 500.0)],
        # 2 x 2 の行列（横に離れた列）。
        [*_run("=", 60.0, 500.0), *_stack("a    b", "c    d", 72.0)],
        # 同じ行が無い（上下だけの 2 行）。
        _stack("1", "1-x", 72.0),
    ],
    ids=["same-width", "left-aligned", "two-line", "no-operator", "brackets", "three-rows", "script", "matrix", "no-main-line"],
)
def test_ambiguous_stacks_are_not_guessed(glyphs: list[PdfGlyph]) -> None:
    assert restore_math_text([_block("x")], glyphs, []) == []


def test_stack_next_to_curved_bracket_or_bar_or_rule_is_not_a_fraction() -> None:
    """括弧の形の曲線・縦の線・上下の間を通る線があれば、分数にしない。"""
    glyphs = [*_run("=", 62.0, 500.0), *_stack("e", "f1", 72.0), *_run("+", 90.0, 500.0)]
    block = [_block("= e f1 +")]

    assert restore_math_text(block, glyphs, []) != []
    assert restore_math_text(block, glyphs, [], (), [(68.0, 490.0, 71.0, 518.0)]) == []
    assert restore_math_text(block, glyphs, [], (PdfRule(70.0, 490.0, 518.0),)) == []
    # 分数の線にならない長い線（表の罫線など）が上下の間を通る。
    assert restore_math_text(block, glyphs, [PdfRule(503.0, 20.0, 400.0)]) == []


def test_stack_reports_how_it_was_restored() -> None:
    glyphs = [*_run("A *", 50.0, 500.0), *_stack("1", "1-x", 72.0)]

    (restored,) = restore_math_text([_block("A * 1 1-x")], glyphs, [])

    assert restored.fraction_count == 1
    assert restored.stacked_fraction_count == 1
    assert restored.line_count == 1


def _body(*glyphs: list[PdfGlyph], text: str, extra: tuple[MathBlock, ...] = ()) -> list[str]:
    flat = [glyph for group in glyphs for glyph in group]
    return [item.text for item in restore_math_text([_block(text, is_formula=False), *extra], flat, [])]


def test_body_exponents_change_only_those_places_and_subscripts_stay() -> None:
    """数式と判定されていない本文でも、基が短い記号の上付きはその箇所だけ直す。下付きは直さない。"""
    glyphs = [
        *_run("E=mc", 100.0, 500.0),
        *_run("2", 124.0, 504.0, size=6.0),
        *_run("H", 140.0, 500.0),
        *_run("2", 146.0, 498.0, size=6.0),
        *_run("O", 150.0, 500.0),
    ]

    assert _body(glyphs, text="E=mc 2 と H2O  の   本文") == ["E=mc^2 と H2O  の   本文"]


def test_body_negative_exponent_after_a_number_is_restored() -> None:
    glyphs = [*_run("10", 100.0, 500.0), *_run("-3", 112.0, 504.0, size=6.0)]

    assert _body(glyphs, text="誤差 10 -3 以下") == ["誤差 10^{-3} 以下"]


def test_body_exponent_after_a_group_is_restored_and_a_subscript_is_not() -> None:
    group = [*_run("(a+b)", 100.0, 500.0), *_run("2", 130.0, 504.0, size=6.0)]
    assert _body(group, text="(a+b) 2 です") == ["(a+b)^2 です"]

    chemical = [
        *_run("Fe", 100.0, 500.0),
        *_run("2", 112.0, 498.0, size=6.0),
        *_run("O", 116.0, 500.0),
        *_run("3", 122.0, 498.0, size=6.0),
    ]
    assert _body(chemical, text="Fe2O3 です") == []


@pytest.mark.parametrize(
    ("glyphs", "text", "extra"),
    [
        # 語の後の上付き（脚注の番号）。
        ([*_run("reference", 100.0, 500.0), *_run("2", 155.0, 504.0, size=6.0)], "reference 2 です", ()),
        # 日本語の後の上付き。
        ([*_run("本文", 100.0, 500.0), *_run("2", 113.0, 504.0, size=6.0)], "本文 2 です", ()),
        # 上付きの 1 は指数としてほぼ使わない。
        ([*_run("ab", 100.0, 500.0), *_run("1", 112.0, 504.0, size=6.0)], "ab 1 です", ()),
        # 記号の印。
        ([*_run("ab", 100.0, 500.0), *_run("*", 112.0, 504.0, size=6.0)], "ab * です", ()),
        # 同じページに同じ番号の脚注の本文がある。
        (
            [*_run("ab", 100.0, 500.0), *_run("2", 112.0, 504.0, size=6.0)],
            "ab 2 です",
            (MathBlock("note", (0.0, 0.0, 600.0, 10.0), "2 脚注の本文"),),
        ),
        # 同じ字の並びが普通の大きさでもあり、どちらか決められない。
        ([*_run("x", 100.0, 500.0), *_run("2", 106.0, 504.0, size=6.0)], "x 2 と x2", ()),
    ],
    ids=["word", "japanese", "one", "symbol", "footnote-on-page", "same-plain-text"],
)
def test_body_scripts_that_may_be_footnotes_are_left_alone(glyphs: list[PdfGlyph], text: str, extra: tuple[MathBlock, ...]) -> None:
    assert _body(glyphs, text=text, extra=extra) == []


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

    content = pdf_math_page_contents(pdf, [1])[1]

    assert "".join(glyph.text for glyph in content.glyphs).startswith("A*1")
    assert [round(rule.position) for rule in content.horizontal_rules] == [700, 600]
    assert content.vertical_rules == []


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
    assert restored[0].raw["math_layout"] == {"fractions": 1, "scripts": 0, "stacked_fractions": 0, "merged_record_ids": []}
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


def _pdf_with_form(path: Path, page: bytes, form: bytes, form_dict: bytes = b"") -> Path:
    """フォーム XObject を 1 つ持つ PDF。"""
    fonts = b"<< /Font << /F1 << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> >> >>"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 600 800] /Contents 4 0 R /Resources << /Font "
        + fonts[9:-3]
        + b" /XObject << /Eq 5 0 R >> >> >>",
        b"<< /Length " + str(len(page)).encode() + b" >>\nstream\n" + page + b"\nendstream",
        b"<< /Type /XObject /Subtype /Form /BBox [0 0 400 400] "
        + form_dict
        + b" /Resources "
        + fonts
        + b" /Length "
        + str(len(form)).encode()
        + b" >>\nstream\n"
        + form
        + b"\nendstream",
    ]
    body = b"%PDF-1.4\n"
    offsets = []
    for index, item in enumerate(objects, start=1):
        offsets.append(len(body))
        body += str(index).encode() + b" 0 obj\n" + item + b"\nendobj\n"
    xref = len(body)
    body += b"xref\n0 6\n0000000000 65535 f \n"
    body += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets)
    body += b"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n" + str(xref).encode() + b"\n%%EOF\n"
    path.write_bytes(body)
    return path


_FORM_FRACTION = b"\n".join(
    (
        b"BT /F1 10 Tf 8 8 Td (1) Tj ET",
        b"BT /F1 10 Tf 0 -6 Td (1-x) Tj ET",
        b"0 5 18 0.6 re f",
    )
)


@pytest.mark.parametrize(
    ("page_stream", "form_dict", "expected_y", "expected_size"),
    [
        (b"q 1 0 0 1 110 695 cm /Eq Do Q", b"", 700, 10),
        (b"q 2 0 0 2 110 690 cm /Eq Do Q", b"", 700, 20),
        (b"q 1 0 0 1 110 690 cm /Eq Do Q", b"/Matrix [1 0 0 1 0 5]", 700, 10),
    ],
    ids=["translated", "scaled", "form-matrix"],
)
def test_rules_and_glyph_sizes_inside_a_form_use_page_coordinates(
    tmp_path: Path, page_stream: bytes, form_dict: bytes, expected_y: float, expected_size: float
) -> None:
    """フォーム XObject の中の線をページの座標で読み、字の大きさは行列の拡大を含める。"""
    pdf = _pdf_with_form(tmp_path / "source.pdf", page_stream, _FORM_FRACTION, form_dict)

    content = pdf_math_page_contents(pdf, [1])[1]

    (rule,) = content.horizontal_rules
    assert rule.position == pytest.approx(expected_y, abs=1.0)
    assert rule.start == pytest.approx(110, abs=0.5)
    assert {round(glyph.size) for glyph in content.glyphs} == {expected_size}


def test_fraction_with_its_rule_inside_a_form_is_restored(tmp_path: Path) -> None:
    pdf = _pdf_with_form(tmp_path / "source.pdf", b"q 1 0 0 1 110 695 cm /Eq Do Q", _FORM_FRACTION)
    records = [_record("1", "1 1-x", [200.0, 160.0, 300.0, 240.0])]

    (restored,) = _restore_math_layout(records, pdf, {1: _PAGE})

    assert restored.text == "1/(1-x)"


def test_glyph_size_includes_the_text_matrix(tmp_path: Path) -> None:
    """``/F1 1 Tf`` と拡大の行列で字の大きさを決める PDF でも、実際の大きさで判定する。"""
    pdf = _pdf_with_form(tmp_path / "source.pdf", b"BT /F1 1 Tf 10 0 0 10 100 500 Tm (a) Tj ET", b"")

    (glyph,) = pdf_math_page_contents(pdf, [1])[1].glyphs

    assert glyph.size == pytest.approx(10.0)


def test_fraction_with_font_size_set_by_the_text_matrix_is_restored(tmp_path: Path) -> None:
    """字の大きさを行列で決める PDF でも、線との隙間を実際の大きさで判定して戻す。"""
    stream = b"\n".join(
        (
            b"BT /F1 1 Tf 10 0 0 10 118 703 Tm (1) Tj ET",
            b"BT /F1 1 Tf 10 0 0 10 110 689 Tm (1-x) Tj ET",
            b"110 699.7 24 0.6 re f",
        )
    )
    pdf = _pdf_with_form(tmp_path / "source.pdf", stream, b"")
    records = [_record("1", "1 1-x", [200.0, 160.0, 300.0, 240.0])]

    (restored,) = _restore_math_layout(records, pdf, {1: _PAGE})

    assert restored.text == "1/(1-x)"


def _picture_records(
    text: str, *, category: str = "Picture", bbox: tuple[float, float, float, float] = (150.0, 180.0, 300.0, 230.0)
) -> list[LayoutRecord]:
    """解析エンジンが図と判定した塊（中身の無い図と、同じ範囲の文字の record）。"""
    return [
        _record("1", "", list(bbox), category=category, self_ref="#/pictures/0"),
        LayoutRecord(
            id="2",
            engine="docling",
            page=1,
            seq_no=2,
            bbox=list(bbox),
            coord_system="image_top_left",
            page_width=1200,
            page_height=1600,
            category=category,
            text=f"{picture_ocr_text_label()}:\n{text}",
            raw_type=PICTURE_OCR_TEXT_RAW_TYPE,
            raw={"aggregation": "docling_picture_children", "source_picture_ref": "#/pictures/0"},
        ),
    ]


def test_picture_that_is_a_one_line_formula_becomes_a_formula(tmp_path: Path) -> None:
    """字と線だけの 1 行の分数は図でなく式の record にし、Vision の対象にしない。"""
    pdf = _pdf_with_fraction(tmp_path / "source.pdf")

    (restored,) = _restore_math_layout(_picture_records("A * 1 1-x"), pdf, {1: _PAGE})

    assert restored.id == "2"
    assert restored.text == "A * 1/(1-x)"
    assert restored.category == "Formula"
    assert restored.raw_type == "formula"


def test_picture_with_an_image_stays_a_picture(tmp_path: Path) -> None:
    """画像と重なる塊は図のままにし、文字だけを直す。"""
    source = _pdf_with_fraction(tmp_path / "source.pdf").read_bytes()
    pdf = tmp_path / "with-image.pdf"
    pdf.write_bytes(source)
    records = _picture_records("A * 1 1-x")
    page = _math_page_with_image()

    from rag_engine.adapters.parsers import docling_adapter

    with patch.object(docling_adapter, "pdf_math_page_contents", return_value={1: page}):
        restored = _restore_math_layout(records, pdf, {1: _PAGE})

    assert [record.id for record in restored] == ["1", "2"]
    assert restored[1].category == "Picture"
    assert restored[1].text == f"{picture_ocr_text_label()}:\nA * 1/(1-x)"


def _math_page_with_image() -> PdfMathPage:
    parts, rule = _fraction("1", "1-x", 112.0, 700.0)
    return PdfMathPage([*_run("A *", 85.0, 697.0), *parts], [rule], [], [], [(70.0, 680.0, 160.0, 720.0)])


@pytest.mark.parametrize("pdf_path", ["source.png", "missing.pdf"], ids=["not-pdf", "unreadable"])
def test_adapter_keeps_records_when_pdf_cannot_be_read(pdf_path: str) -> None:
    records = [_record("1", "1 1-x", [150.0, 180.0, 300.0, 230.0])]

    assert _restore_math_layout(records, pdf_path, {1: _PAGE})[0].text == "1 1-x"


# 以下は #4224。折り返した項目名・幅の広い線・2 行の分母が並ぶ式（文字はすべて合成）。
_NARROW_PITCH = 12.0  # 字の大きさの 1.2 倍。折り返しの行間で、上下の間に線を引く余地が無い。
_LONG_DENOMINATOR = "1-x abcdefghijklmnopqrstuvwxyzabcdefghijkl"


def _wide_fraction(left: float, rule_y: float = 500.0) -> tuple[list[PdfGlyph], PdfRule]:
    """分母より字の大きさの 4 倍を超えて長い線と、小さな字の分母（線との隙間が字の大きさより広い）。"""
    rule = PdfRule(rule_y, left, left + 300.0)
    width = len(_LONG_DENOMINATOR) * 9.0 * 0.6
    glyphs = [
        *_run("1", left + 148.0, rule_y + 5.0),
        *_run(_LONG_DENOMINATOR, left + (300.0 - width) / 2, rule_y - 18.0, size=9.0),
    ]
    return glyphs, rule


def _wrapped_label(text: tuple[str, ...], left: float, axis: float, pitch: float) -> list[PdfGlyph]:
    """折り返した項目名。行の真ん中が axis の高さになるよう左にそろえて置く。"""
    top = axis + pitch * (len(text) - 1) / 2 - _SIZE * 0.35
    return [glyph for index, line in enumerate(text) for glyph in _run(line, left, top - index * pitch)]


def _axis_run(text: str, left: float, axis: float = 500.0) -> list[PdfGlyph]:
    return _run(text, left, axis - _SIZE * 0.35)


def test_wide_rule_next_to_an_operator_with_a_wrapped_label_is_restored() -> None:
    """線が分母より長く、分母の字が小さくても、軸に演算子が隣るなら分数。折り返した項目名は 1 つの項にする。"""
    parts, rule = _wide_fraction(200.0)
    glyphs = [*_wrapped_label(("PPP", "QQ"), 160.0, 500.0, 21.0), *_axis_run("*", 185.0), *parts]
    blocks = [
        MathBlock("upper", (155.0, 505.0, 180.0, 520.0), "PPP"),
        MathBlock("op", (182.0, 495.0, 195.0, 505.0), "*"),
        MathBlock("lower", (155.0, 480.0, 180.0, 495.0), "QQ"),
        MathBlock("num", (340.0, 503.0, 360.0, 516.0), "1"),
        MathBlock("den", (200.0, 478.0, 500.0, 492.0), _LONG_DENOMINATOR),
    ]

    (restored,) = restore_math_text(blocks, glyphs, [rule])

    assert restored.text == f"PPP QQ * 1/({_LONG_DENOMINATOR})"
    assert restored.merged_keys == ("upper", "op", "lower", "num", "den")
    assert restored.line_count == 1


def test_wide_rule_without_an_operator_is_still_not_a_fraction() -> None:
    """軸に演算子が無ければ、長い線は区切りの線と区別できないので戻さない。"""
    parts, rule = _wide_fraction(200.0)

    assert _text_of([_block(f"1 {_LONG_DENOMINATOR}")], parts, rule) == []


def test_wrapped_denominator_stays_inside_the_fraction() -> None:
    """分母の 2 行目もかっこの中に入れ、左そろえの折り返しでも順序を保つ。"""
    rule = PdfRule(500.0, 100.0, 220.0)
    glyphs = [
        *_axis_run("A =", 70.0),
        *_run("1", 158.0, 503.0),
        *_run("1-x abcdefghijklmn", 106.0, 487.0),
        *_run("opqrst", 106.0, 475.0),
        *_axis_run("+ B", 226.0),
    ]

    assert _text_of([_block("A = 1 1-x abcdefghijklmn + B opqrst")], glyphs, rule) == ["A = 1/(1-x abcdefghijklmn opqrst) + B"]


def test_line_below_the_denominator_that_leaves_the_rule_is_not_part_of_it() -> None:
    """線の外へ続く次の行は本文なので、分母に入れない。"""
    rule = PdfRule(500.0, 120.0, 160.0)
    blocks = [
        MathBlock("formula", (60.0, 484.0, 250.0, 516.0), "A = 1 1-x"),
        MathBlock("body", (60.0, 460.0, 400.0, 482.0), "next line of the body continues past the rule"),
    ]
    glyphs = [
        *_axis_run("A =", 95.0),
        *_run("1", 138.0, 503.0),
        *_run("1-x", 130.0, 487.0),
        *_run("next line of the body continues past the rule", 62.0, 475.0),
    ]

    (restored,) = restore_math_text(blocks, glyphs, [rule])

    assert restored.text == "A = 1/(1-x)"
    assert restored.merged_keys == ("formula",)


def test_wrapped_label_beside_a_ruled_fraction_is_not_a_fraction() -> None:
    """線のある分数の隣の項目名は、中央にそろって演算子に挟まれても分数にせず、項の順序を保つ。"""
    rule = PdfRule(500.0, 230.0, 270.0)
    blocks = [
        MathBlock("head", (235.0, 503.0, 265.0, 516.0), "SSS"),
        MathBlock("rest", (165.0, 470.0, 400.0, 507.0), "= RRRR TTTT + - U 1-x"),
    ]
    glyphs = [
        *_axis_run("=", 170.0),
        *_wrapped_label(("RRRR", "TTTT"), 190.0, 500.0, _NARROW_PITCH),
        *_axis_run("+", 219.0),
        *_run("SSS", 240.0, 505.0),
        *_run("1-x", 240.0, 487.0),
        *_axis_run("- U", 276.0),
    ]

    (restored,) = restore_math_text(blocks, glyphs, [rule])

    assert restored.text == "= RRRR TTTT + SSS/(1-x) - U"
    assert restored.stacked_fraction_count == 0
    assert restored.line_count == 1


def test_numerator_in_a_heading_block_is_used_only_when_the_rule_has_an_operator() -> None:
    """解析エンジンが見出しと判定した分子は、軸に演算子が隣る線のときだけ字を使う。"""
    rule = PdfRule(500.0, 230.0, 270.0)
    heading = MathBlock("head", (235.0, 503.0, 265.0, 516.0), "SSS", expression_only=True)
    rest = MathBlock("rest", (100.0, 470.0, 400.0, 502.0), "= 1-x")
    numerator, denominator = _run("SSS", 240.0, 505.0), _run("1-x", 240.0, 487.0)

    (restored,) = restore_math_text([heading, rest], [*_axis_run("=", 215.0), *numerator, *denominator], [rule])
    assert restored.text == "= SSS/(1-x)"
    assert restored.merged_keys == ("head", "rest")

    # 見出しの下の区切りの線: 軸に演算子が無いので、見出しと次の行をつなげない。
    plain_body = MathBlock("rest", (100.0, 470.0, 400.0, 502.0), "1-x")
    assert restore_math_text([heading, plain_body], [*numerator, *denominator], [rule]) == []


def test_heading_block_text_is_never_changed_on_its_own() -> None:
    """見出しの塊は、式の一部と確かめられないかぎり上付きも直さない。"""
    heading = MathBlock("head", _PAGE_BOX, "mc 2", expression_only=True)
    glyphs = [*_run("mc", 100.0, 500.0), *_run("2", 112.0, 504.0, size=6.0)]

    assert restore_math_text([heading], glyphs, []) == []


def test_stack_on_a_page_with_a_ruled_fraction_is_not_guessed() -> None:
    """線を描く文書の線の無い積み重ねは、分数でなく項目名の折り返しとみる。"""
    parts, rule = _fraction("1", "1-x", 80.0, 700.0)
    stack = [*_run("G =", 50.0, 500.0), *_stack("p", "q", 72.0), *_run("+", 82.0, 500.0), *_stack("r", "s", 92.0)]
    blocks = [
        MathBlock("ruled", (70.0, 680.0, 110.0, 715.0), "1 1-x"),
        MathBlock("stack", (40.0, 480.0, 120.0, 520.0), "G = p q + r s"),
    ]

    (restored,) = restore_math_text(blocks, [*parts, *stack], [rule])

    assert restored.key == "ruled"
    assert restored.text == "1/(1-x)"


def test_stack_with_a_line_pitch_too_narrow_for_a_rule_is_not_a_fraction() -> None:
    """上下の行間に線を引く余地が無い積み重ねは、演算子に挟まれても折り返しの行とみる。"""
    label = _wrapped_label(("RRRR", "TTTT"), 72.0, 503.5, _NARROW_PITCH)

    assert _restored_text(_run("G =", 50.0, 500.0), label, _run("+ U", 102.0, 500.0), text="G = RRRR TTTT + U") == []


def test_wrapped_label_without_an_operator_towards_the_fraction_is_left_alone() -> None:
    """分数との間に演算子が無い 2 行は、式の項と言えないのでまとめない。"""
    parts, rule = _fraction("1", "1-x", 200.0, 500.0)
    blocks = [
        MathBlock("upper", (165.0, 505.0, 198.0, 520.0), "AAAA"),
        MathBlock("lower", (165.0, 480.0, 198.0, 495.0), "BBBB"),
        MathBlock("formula", (198.0, 480.0, 225.0, 520.0), "1 1-x"),
    ]

    (restored,) = restore_math_text(blocks, [*_wrapped_label(("AAAA", "BBBB"), 170.0, 500.0, 21.0), *parts], [rule])

    assert restored.merged_keys == ("formula",)
    assert restored.text == "1/(1-x)"


def test_curved_brackets_around_a_ruled_expression_are_kept() -> None:
    """曲線の括弧が左右で対になり、式を囲むときは括弧を本文に戻す（項の結びつきが変わるので）。"""
    parts, rule = _fraction("1", "1-x", 200.0, 500.0)
    glyphs = [*_axis_run("U -", 135.0), *_axis_run("A *", 170.0), *parts, *_axis_run("- V", 234.0)]
    paths = [(160.0, 476.0, 166.0, 524.0), (222.0, 476.0, 228.0, 524.0)]

    (restored,) = restore_math_text([_block("U - A * 1 1-x - V")], glyphs, [rule], (), paths)
    assert restored.text == "U - (A * 1/(1-x)) - V"

    # 片側だけの曲線（場合分けの括弧など）は、対にならないので足さない。
    (one_sided,) = restore_math_text([_block("U - A * 1 1-x - V")], glyphs, [rule], (), paths[:1])
    assert one_sided.text == "U - A * 1/(1-x) - V"


def test_radical_bar_is_not_a_fraction_bar_and_keeps_the_root() -> None:
    """分母の根号の線は分数の線にせず、根号を本文に残す（落とすと別の式になる）。"""
    bar = PdfRule(500.0, 110.0, 150.0)
    radical_bar = PdfRule(497.0, 113.0, 150.0)
    stroke = (106.0, 472.0, 114.5, 498.0)
    glyphs = [*_axis_run("A =", 85.0), *_run("num", 121.0, 505.0), *_run("a+b", 121.0, 485.0)]

    assert _text_of([_block("A = num a+b")], glyphs, bar) == ["A = num/(a+b)"]
    (restored,) = restore_math_text([_block("A = num a+b")], glyphs, [bar, radical_bar], (), [stroke])
    assert restored.text == "A = num/√(a+b)"

    # 分母の根号で線が式の行より上がっても、項の順序を保つ。
    raised = [
        *_axis_run("A =", 85.0, axis=494.0),
        *_run("num", 121.0, 505.0),
        *_run("a+b", 121.0, 485.0),
        *_axis_run("* B", 157.0, axis=494.0),
    ]
    (kept_order,) = restore_math_text([_block("A = num * B a+b")], raised, [bar, radical_bar], (), [stroke])
    assert kept_order.text == "A = num/√(a+b) * B"
    assert kept_order.line_count == 1


def test_picture_with_curved_brackets_becomes_a_formula_in_the_body(tmp_path: Path) -> None:
    """図と判定された括弧つきの式も、字と線だけの 1 行になれば本文の式にする。"""
    pdf = _pdf_with_form(
        tmp_path / "source.pdf",
        b"\n".join(
            (
                b"BT /F1 10 Tf 92 707 Td (PP) Tj ET",
                b"BT /F1 10 Tf 92 693 Td (QQ) Tj ET",
                b"BT /F1 10 Tf 116 697 Td (*) Tj ET",
                b"BT /F1 10 Tf 148 703 Td (1) Tj ET",
                b"BT /F1 10 Tf 143 689 Td (1-x) Tj ET",
                b"130 699.7 40 0.6 re f",
                b"0.5 w 88 680 m 80 690 80 710 88 722 c S",
                b"0.5 w 174 680 m 182 690 182 710 174 722 c S",
                b"BT /F1 10 Tf 189 697 Td (- U) Tj ET",
            )
        ),
        b"",
    )

    (restored,) = _restore_math_layout(_picture_records("PP QQ * 1 1-x - U", bbox=(160.0, 156.0, 420.0, 240.0)), pdf, {1: _PAGE})

    assert restored.category == "Formula"
    assert restored.text == "(PP QQ * 1/(1-x)) - U"


def test_adapter_uses_a_heading_only_as_part_of_a_ruled_expression(tmp_path: Path) -> None:
    """見出しと判定された分子は、線のある式の分子のときだけ式の record にまとめ、それ以外の見出しは変えない。"""
    pdf = _pdf_with_form(
        tmp_path / "source.pdf",
        b"\n".join(
            (
                b"BT /F1 10 Tf 108 697 Td (A =) Tj ET",
                b"BT /F1 10 Tf 140 703 Td (num) Tj ET",
                b"BT /F1 10 Tf 143 689 Td (1-x) Tj ET",
                b"130 699.7 40 0.6 re f",
                b"BT /F1 10 Tf 100 600 Td (Plain heading) Tj ET",
            )
        ),
        b"",
    )
    records = [
        _record("1", "num", [268.0, 170.0, 340.0, 190.0], category="Section-header"),
        _record("2", "A = 1-x", [190.0, 190.0, 340.0, 225.0], category="Text"),
        _record("3", "Plain heading", [190.0, 380.0, 340.0, 405.0], category="Section-header"),
    ]

    restored = _restore_math_layout(records, pdf, {1: _PAGE})

    assert [(record.id, record.category, record.text) for record in restored] == [
        ("1", "Formula", "A = num/(1-x)"),
        ("3", "Section-header", "Plain heading"),
    ]
