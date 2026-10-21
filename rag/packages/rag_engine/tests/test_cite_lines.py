"""引用を根拠の行番号で指させ、原文はシステムが取り出す（#2830）。"""

from rag_engine.generation import grounded
from rag_engine.models.llm import GroundedDraft, GroundedItem

# 各行は 1 行で引用の単位になる長さ（2 行を続けると単位の上限を超える）。
LONG = "\n".join(
    sentence + "説明が続きます。" * 12
    for sentence in (
        "一覧画面を開きます。",
        "抽出条件を入力して抽出ボタンを押します。",
        "一覧から対象を選択します。",
        "入力欄に値を直接入力します。",
        "実行ボタンを押して登録を完了します。",
    )
)
SHORT = "一覧から対象を選択します。\n実行ボタンを押します。"


def spans():
    return [
        {"evidence_id": "Ea", "label": "E1", "text": LONG},
        {"evidence_id": "Eb", "label": "E2", "text": SHORT},
    ]


def item(evidence_id, lines="", quote=""):
    return GroundedItem(kind="operation", text="対象を選んで実行する", evidence_id=evidence_id, lines=lines, quote=quote)


def test_short_lines_are_merged_and_long_lines_split_at_sentences():
    units = grounded.citation_units("あ\nい\n\n" + "う。" * 100)
    texts = ["あ\nい\n\n" + "う。" * 100][0]
    assert all(end - start <= grounded.CITE_UNIT_MAX_CHARS for start, end in units)
    assert texts[units[0][0] : units[0][1]].startswith("あ\nい")
    assert "".join(texts[start:end] for start, end in units).replace("\n", "") == texts.replace("\n", "")


def test_only_long_evidence_is_numbered():
    assert grounded.numbered_text(SHORT) == SHORT
    numbered = grounded.numbered_text(LONG)
    assert numbered.startswith("1| 一覧画面を開きます。")
    assert len(grounded.citation_units(LONG)) == numbered.count("| ")


def test_line_range_becomes_the_exact_original_quote():
    cited, error = grounded.cited_quote(item("E1", "2-3"), spans())
    units = grounded.citation_units(LONG)
    assert error == ""
    assert cited.quote == LONG[units[1][0] : units[2][1]]
    assert cited.quote in LONG


def test_short_evidence_without_lines_quotes_the_whole_text():
    cited, error = grounded.cited_quote(item("E2"), spans())
    assert (cited.quote, error) == (SHORT, "")


def test_invalid_or_missing_range_on_long_evidence_is_reported():
    for lines in ("", "0", "2-99", "あ"):
        cited, error = grounded.cited_quote(item("E1", lines), spans())
        assert cited.quote == ""
        assert error


def test_copied_quote_and_gap_are_left_to_the_existing_matching():
    copied = item("E2", quote="実行ボタンを押します。")
    assert grounded.cited_quote(copied, spans()) == (copied, "")
    gap = GroundedItem(kind="gap", text="資料に記載が無い")
    assert grounded.cited_quote(gap, spans()) == (gap, "")


def test_verify_publishes_the_item_cited_by_lines():
    draft = GroundedDraft.model_validate(
        {"summary": "s", "confidence": "high", "items": [item("E1", "3").model_dump(), item("E1", "9").model_dump()]}
    )
    checked, dropped = grounded.verify("対象を選ぶ方法", draft, spans())
    first = next(entry for entry in checked if entry.draft_index == 0)
    assert first.span is not None and first.span["evidence_id"] == "Ea"
    assert "quote_unmatched" not in [mark.get("code") for mark in first.marks]
    assert [entry["index"] for entry in dropped] == [1]
    assert "範囲" in dropped[0]["reason"]
