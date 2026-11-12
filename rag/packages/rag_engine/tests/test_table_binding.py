"""根拠の本文の表の形の判定。"""

from rag_engine.generation.table_binding import has_table_structure

TABLE = "<table><tr><th>区分</th><th>値</th></tr><tr><td>区分A</td><td>01</td></tr></table>"


def test_html_table_is_detected() -> None:
    assert has_table_structure(TABLE)


def test_plain_text_has_no_table() -> None:
    assert not has_table_structure("合成の本文です。")
