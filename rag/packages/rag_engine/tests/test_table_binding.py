"""根拠の本文から表を読む処理（MCP の evidence_tables と引用の表の判定）。"""

from rag_engine.generation.table_binding import evidence_tables, has_table_structure

TABLE = "<table><tr><th>区分</th><th>値</th></tr><tr><td>区分A</td><td>01</td></tr></table>"


def test_html_table_is_read_as_rows_and_detected() -> None:
    tables = evidence_tables(TABLE)
    assert len(tables) == 1
    assert has_table_structure(TABLE)


def test_plain_text_has_no_table() -> None:
    assert not evidence_tables("合成の本文です。")
    assert not has_table_structure("合成の本文です。")
