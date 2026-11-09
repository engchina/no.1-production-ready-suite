"""引用を根拠の行番号で指させ、原文はシステムが取り出す（#2830）。"""

from rag_engine.generation import grounded
from rag_engine.generation.citation_wire import generation_draft_wire_model
from rag_engine.models.llm import GroundedCitation, GroundedItem

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
    return GroundedItem(
        kind="operation", text="対象を選んで実行する", citations=[GroundedCitation(evidence_id=evidence_id, lines=lines, quote=quote)]
    )


def test_short_lines_are_merged_and_long_lines_split_at_sentences():
    units = grounded.citation_units("あ\nい\n\n" + "う。" * 100, contract_version="explicit_units_v4")
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
    for lines in ("0", "2-99", "あ"):
        cited, error = grounded.cited_quote(item("E1", lines), spans())
        assert cited.quote == ""
        assert error
    # lines が空で、説明が根拠のどの行とも重ならない。
    unrelated = GroundedItem(kind="rule", text="保管期間は十年とする", citations=[GroundedCitation(evidence_id="E1")])
    cited, error = grounded.cited_quote(unrelated, spans())
    assert cited.quote == ""
    assert error


def test_lines_copied_in_the_numbered_display_form_use_the_leading_range():
    """モデルが行の表示の形（`3| 本文`）を lines に写しても、先頭の番号・範囲で引用を取り出す（#3002）。"""
    units = grounded.citation_units(LONG)
    for lines, (first, last) in (("3| 一覧から対象を", (3, 3)), ("2-3| 抽出条件", (2, 3)), ("4 | 入力欄", (4, 4))):
        cited, error = grounded.cited_quote(item("E1", lines), spans())
        assert error == ""
        assert cited.quote == LONG[units[first - 1][0] : units[last - 1][1]]
    for lines in ("99| 範囲外", "| 番号が無い", "あ| 文"):
        cited, error = grounded.cited_quote(item("E1", lines), spans())
        assert cited.quote == ""
        assert error


def test_a_stored_quote_does_not_override_the_id_selected_original():
    copied = item("E2", quote="原文にない文字列")
    cited, error = grounded.cited_quote(copied, spans())
    assert error == "" and cited.quote == SHORT
    assert grounded._citation_extraction(cited)["requested_quote"] == copied.quote
    gap = GroundedItem(kind="gap", text="資料に記載が無い")
    assert grounded.cited_quote(gap, spans()) == (gap, "")


def test_generation_instructions_name_only_the_public_citation_fields():
    schema = GroundedItem.model_json_schema()
    assert "citations" in schema["properties"]
    assert not {"evidence_id", "lines", "quote"} & set(schema["properties"])
    citation = schema["$defs"]["GroundedCitation"]["properties"]
    assert "quote" not in citation
    assert {"evidence_id", "lines"} <= set(citation)
    wire = generation_draft_wire_model().model_json_schema()
    wire_citation = wire["$defs"]["GroundedCitationWire"]
    assert set(wire_citation["properties"]) == {"evidence_id", "line_start", "line_end"}
    assert set(wire_citation["required"]) == {"evidence_id", "line_start", "line_end"}
    # 指示の本文は引用を basis の選択として説明し、schema に無い quote の語を使わない。
    assert "quote" not in grounded.GENERATE_SYSTEM_PROMPT


def test_lines_holding_text_are_not_resolved_by_searching_the_original():
    for lines in ("抽出条件を入力して抽出", "入力欄に 値を直接入力", "実行ボタンを押して登録"):
        cited, error = grounded.cited_quote(item("E1", lines), spans())
        assert error and not cited.quote
        assert grounded._citation_extraction(cited)["reason_code"] == "citation_range_invalid"


def test_lines_text_that_is_short_missing_or_ambiguous_is_still_reported():
    for lines in ("一覧", "別の資料の文です", "説明が続きます。"):
        cited, error = grounded.cited_quote(item("E1", lines), spans())
        assert cited.quote == ""
        assert error


def test_lines_written_as_a_list_use_the_first_to_last_line():
    units = grounded.citation_units(LONG)
    for lines, (first, last) in (("[1,2,3]", (1, 3)), ("2, 4", (2, 4)), ("[3]", (3, 3))):
        cited, error = grounded.cited_quote(item("E1", lines), spans())
        assert error == ""
        assert cited.quote == LONG[units[first - 1][0] : units[last - 1][1]]


def test_missing_lines_are_not_inferred_from_the_claim():
    claim = GroundedItem(kind="operation", text="抽出条件を入力して抽出ボタンを押す", citations=[GroundedCitation(evidence_id="E1")])
    cited, error = grounded.cited_quote(claim, spans())
    assert error and not cited.quote
    assert grounded._citation_extraction(cited)["reason_code"] == "citation_range_missing"
