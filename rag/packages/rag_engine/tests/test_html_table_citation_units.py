"""HTML の表の根拠は行を 1 単位にし、回答の本文にタグを出さない（#4717）。"""

from typing import Any

from rag_engine.chunking.constants import _flatten_table_html
from rag_engine.generation import grounded
from rag_engine.generation.citation_content import table_html_as_rows
from rag_engine.models.llm import GroundedItem

ROW_HEADER = '<tr><th colspan="2">合成の区分</th></tr>'
ROW_A = "<tr><td>項目A</td><td>10</td></tr>"
ROW_B = "<tr>\n<td>項目B</td>\n<td>20</td>\n</tr>"
TABLE = f"<table>\n<tbody>\n{ROW_HEADER}\n{ROW_A}\n{ROW_B}\n</tbody>\n</table>"
TEXT = f"前文の説明です。\n{TABLE}\n後文の説明です。"


def evidence(text: str) -> dict[str, Any]:
    return {
        "evidence_id": "stable-E1",
        "label": "E1",
        "source_id": "parent-E1",
        "source": "synthetic.pdf",
        "function": ("synthetic.pdf", "合成の説明"),
        "text": text,
    }


def pieces(text: str, **options: Any) -> list[str]:
    return [text[start:end] for start, end in grounded.citation_units(text, **options)]


def rendered(text: str) -> str:
    span = evidence(text)
    return grounded.render_answer("", [grounded.CheckedItem(GroundedItem(kind="rule", text=text), span=span)]).text


def test_each_html_table_row_is_one_unit_and_the_text_around_it_is_unchanged() -> None:
    assert pieces(TEXT) == ["前文の説明です。", ROW_HEADER, ROW_A, ROW_B, "後文の説明です。"]


def test_a_table_on_one_line_is_not_cut_inside_a_row() -> None:
    long_row = "<tr><td>" + "長い説明です。" * 30 + "</td><td>30</td></tr>"
    text = f"<table><tbody>{ROW_A}{long_row}</tbody></table>"
    assert pieces(text) == [ROW_A, long_row]


def test_recorded_views_keep_the_units_of_their_version() -> None:
    old = pieces(TEXT, contract_version="explicit_units_v5")
    assert old != pieces(TEXT)
    view = grounded._citation_view(evidence(TEXT), explicit_units=True)
    assert view.contract_version == grounded.CITATION_UNIT_CONTRACT != "explicit_units_v5"


def test_markdown_tables_keep_their_units() -> None:
    text = "| 項目 | 値 |\n| --- | --- |\n| 項目A | 10 |\n| 項目B | 20 |"
    assert pieces(text) == pieces(text, contract_version="explicit_units_v5")
    assert rendered(text).count("| 項目A | 10 |") == 1


def test_answer_shows_table_rows_without_tags() -> None:
    answer = rendered(TABLE)
    assert "<" not in answer and ">" not in answer
    lines = answer.splitlines()
    assert any(line.endswith("| 合成の区分 |  |") for line in lines)
    assert "| 項目A | 10 |" in lines
    assert "| 項目B | 20 |" in lines


def test_cell_text_is_decoded_and_inline_tags_are_removed() -> None:
    answer = rendered("<table><tr><td>A&amp;B<br>続き</td><td>30</td></tr></table>")
    assert "| A&B 続き | 30 |" in answer


def test_a_row_cut_by_a_broken_table_shows_no_tags() -> None:
    answer = rendered("</td></tr><tr><td>項目C</td><td>30</td>")
    assert "<" not in answer and "項目C" in answer and "30" in answer


def test_text_without_table_tags_is_unchanged() -> None:
    assert "1 < 2 かつ <値> を入力します。" in rendered("1 < 2 かつ <値> を入力します。")


def test_long_attributes_and_nested_inline_tags_do_not_leak_into_the_answer() -> None:
    text = f'<table><tr data-note="a > b"><td><span title="{"x" * 100}"><strong>合成の本文</strong></span></td><td>10</td></tr></table>'
    answer = rendered(text)
    assert "<span" not in answer and "<strong" not in answer
    assert "| 合成の本文 | 10 |" in answer
    # 引用単位は表示に変換した本文ではなく、元の HTML の範囲。
    assert pieces(text) == [text[text.index("<tr ") : text.index("</tr>") + len("</tr>")]]


def test_pipe_and_backslash_are_literal_cell_values() -> None:
    text = "<table><tr><td>A|B</td><td>10</td></tr><tr><td>C\\|D\\</td><td>20</td></tr></table>"
    assert table_html_as_rows(text) == "| A\\|B | 10 |\n| C\\\\|D\\ | 20 |"


def test_caption_and_surrounding_text_are_separate_from_table_rows() -> None:
    text = f"前文<table><caption>合成の表題</caption>{ROW_A}{ROW_B}</table>後文"
    assert table_html_as_rows(text).splitlines() == ["前文", "合成の表題", "| 項目A | 10 |", "| 項目B | 20 |", "後文"]


def test_plain_fallback_and_quote_only_remove_long_tags_without_cutting_values() -> None:
    text = f'</td></tr><tr><td><span title="{"x" * 100}">合成の本文</span></td><td>30</td>'
    for actual in (table_html_as_rows(text), _flatten_table_html(text)):
        assert "<span" not in actual and "合成の本文" in actual and "30" in actual


def test_html_entities_are_text_and_are_not_parsed_as_new_tags() -> None:
    text = "<table><tr><td>&lt;span&gt; &amp; A&#124;B</td><td>10</td></tr></table>"
    assert table_html_as_rows(text) == "| <span> & A\\|B | 10 |"


def test_nested_tables_fall_back_to_plain_text_without_losing_values() -> None:
    text = "<table><tr><td>外側<table><tr><td>内側</td></tr></table>続き</td><td>20</td></tr></table>"
    actual = table_html_as_rows(text)
    assert "<" not in actual
    assert all(value in actual for value in ("外側", "内側", "続き", "20"))


def test_extreme_colspan_has_bounded_output() -> None:
    text = f'<table><tr><td colspan="{"9" * 5000}">合成の本文</td></tr></table>'
    assert table_html_as_rows(text).count("|") == 21


def test_summary_uses_the_same_html_display_contract_as_the_body() -> None:
    text = f'<table><caption>合成の表題</caption><tr><td><span title="{"x" * 100}">A|B</span></td><td>10</td></tr></table>'
    answer = grounded.render_answer(text, []).text
    assert "<span" not in answer and "<table" not in answer
    assert answer.startswith("合成の表題\n| A\\|B | 10 |")
