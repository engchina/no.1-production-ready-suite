"""検索の結果と観測の本文の大きさの規則（#3321）。内容はすべて合成。"""

import random
import re

import pytest

from pr_backend_core.source_text import (
    SOURCE_TEXT_ITEM_MAX_TOKENS,
    SOURCE_TEXT_PAGE_BUDGET_TOKENS,
    SOURCE_TEXT_RESPONSE_BUDGET_TOKENS,
    SourceTextItem,
    TextRange,
    excerpt_source_text,
    fit_source_texts,
    page_source_texts,
    query_terms,
)
from pr_backend_core.token_estimate import estimate_input_tokens


def _sentences(count: int, marker: int | None = None) -> tuple[str, list[str]]:
    parts = [
        f"段落{index}の合成の本文は手順{index}の確認を順に説明します。"
        if index != marker
        else f"段落{index}では目印の値が三十日であると定めます。"
        for index in range(count)
    ]
    return "".join(parts), parts


def test_text_within_the_ceiling_is_returned_whole() -> None:
    text, _ = _sentences(10)
    piece = excerpt_source_text(text, max_tokens=SOURCE_TEXT_ITEM_MAX_TOKENS)
    assert (piece.text, piece.truncated, piece.text_omitted) == (text, False, False)
    assert piece.excerpt_range == TextRange(0, len(text))
    assert piece.next_offset is None
    assert piece.tokens == estimate_input_tokens(text)


def test_text_is_cut_at_sentence_boundaries_around_the_match() -> None:
    text, parts = _sentences(200, marker=120)
    piece = excerpt_source_text(text, max_tokens=300, queries=["目印の値は何日か"])
    assert piece.truncated and piece.tokens <= 300
    assert "目印の値が三十日" in piece.text
    # 文の途中で始まらず、文の途中で終わらない。
    assert piece.text.startswith("段落") and piece.text.endswith("。")
    assert all(part in parts for part in re.findall(r"段落[^。]*。", piece.text))
    # 当たった文を中心にし、前後の文を含む。
    assert piece.excerpt_range is not None
    marker_at = text.index("目印の値")
    assert piece.excerpt_range.start < marker_at < piece.excerpt_range.end
    before = marker_at - piece.excerpt_range.start
    after = piece.excerpt_range.end - marker_at
    assert before > 50 and after > 50
    assert text[piece.excerpt_range.start : piece.excerpt_range.end] == piece.text
    assert piece.next_offset == piece.excerpt_range.end
    assert piece.text_length == len(text)


def test_text_without_a_match_is_cut_from_the_head() -> None:
    text, _ = _sentences(200)
    piece = excerpt_source_text(text, max_tokens=300, queries=["該当しない語"])
    assert piece.excerpt_range is not None and piece.excerpt_range.start == 0
    assert piece.text.endswith("。")
    assert piece.next_offset == piece.excerpt_range.end


def test_paragraph_and_line_breaks_are_boundaries() -> None:
    lines = [f"項目{index}: 合成の説明の行" for index in range(400)]
    text = "\n".join(lines)
    piece = excerpt_source_text(text, max_tokens=200, queries=["項目250"])
    assert piece.truncated and piece.tokens <= 200
    assert "項目250:" in piece.text
    kept = piece.text.rstrip("\n").split("\n")
    assert all(line in lines for line in kept)


def test_a_single_long_sentence_falls_back_to_clauses_and_characters() -> None:
    text = "、".join(f"値{index}" for index in range(2000)) + "。"
    piece = excerpt_source_text(text, max_tokens=100, queries=["値1500"])
    assert piece.truncated and 0 < piece.tokens <= 100
    assert "値1500" in piece.text
    unbroken = "合成" * 3000
    piece = excerpt_source_text(unbroken, max_tokens=50)
    assert piece.tokens <= 50 and piece.text == "合成" * 25


def _html_table(rows: int, marker_row: int) -> str:
    header = "<tr><th>列A</th><th>列B</th><th>列C</th></tr>"
    body = "".join(
        f"<tr><td>行{index}</td><td>{'目印の値' if index == marker_row else '合成の値'}</td>"
        f"<td>{index * 7}</td></tr>"
        for index in range(rows)
    )
    return f"<table>{header}{body}</table>"


def test_html_table_keeps_the_header_row_and_cuts_between_rows() -> None:
    text = _html_table(400, marker_row=300)
    piece = excerpt_source_text(text, max_tokens=600, kind="table", queries=["目印の値"])
    assert piece.truncated and piece.tokens <= 600
    assert piece.text.startswith("<table><tr><th>列A</th><th>列B</th><th>列C</th></tr>")
    assert piece.header_range == TextRange(0, text.index("<tr><td>"))
    body = piece.text[piece.header_range.end :]
    # 行の途中で始まらず、行の途中で終わらない。
    assert body.startswith("<tr>") and body.endswith("</tr>")
    assert re.fullmatch(r"(<tr>.*?</tr>)+", body)
    assert "目印の値" in body
    assert piece.excerpt_range is not None
    assert text[piece.excerpt_range.start : piece.excerpt_range.end] == body
    assert piece.next_offset == piece.excerpt_range.end


def test_table_without_a_match_starts_after_the_header() -> None:
    text = _html_table(400, marker_row=-1)
    piece = excerpt_source_text(text, max_tokens=600, kind="table", queries=["該当しない"])
    # 見出しの直後から続くので、範囲は先頭からの 1 つ（見出しの範囲は別に持たない）。
    assert piece.header_range is None
    assert piece.excerpt_range is not None and piece.excerpt_range.start == 0
    assert piece.text.endswith("</tr>")


def test_line_table_keeps_the_column_headers_and_cuts_between_rows() -> None:
    header = "関連の見出し:\n合成の章\n表の題: 合成の表\n列見出し: 列A | 列B | 列C\n"
    rows = [
        f"行 {index}: 値{index} | {'目印' if index == 250 else '合成'} | {index}"
        for index in range(400)
    ]
    text = header + "\n".join(rows)
    piece = excerpt_source_text(text, max_tokens=500, kind="table", queries=["目印"])
    assert piece.truncated and piece.tokens <= 500
    assert piece.text.startswith(header)
    kept = piece.text[len(header) :].rstrip("\n").split("\n")
    assert all(line in rows for line in kept)
    assert any("目印" in line for line in kept)


def test_markdown_table_keeps_the_header_and_rule_lines() -> None:
    header = "| 列A | 列B |\n|---|---|\n"
    rows = [f"| 値{index} | {'目印' if index == 10 else '合成'} |" for index in range(600)]
    text = header + "\n".join(rows)
    piece = excerpt_source_text(text, max_tokens=300, kind="table", queries=["目印"])
    assert piece.text.startswith(header)
    assert all(line in rows for line in piece.text[len(header) :].rstrip("\n").split("\n"))


def test_table_with_a_huge_header_is_cut_as_text() -> None:
    header = "<tr>" + "".join(f"<th>見出し{index}</th>" for index in range(300)) + "</tr>"
    text = "<table>" + header + "<tr><td>合成</td></tr>" * 50 + "</table>"
    piece = excerpt_source_text(text, max_tokens=200, kind="table")
    assert piece.truncated and piece.tokens <= 200 and piece.header_range is None


def test_excerpt_never_exceeds_the_ceiling() -> None:
    rng = random.Random(3321)
    alphabet = ["合", "成", "の", "値", "a", "b", " ", "。", "、", "\n", "|", "1"]
    for _ in range(50):
        text = "".join(rng.choice(alphabet) for _ in range(rng.randint(1, 3000)))
        for kind in ("text", "table"):
            piece = excerpt_source_text(text, max_tokens=120, kind=kind, queries=["合成の値"])
            assert piece.tokens <= 120
            assert piece.tokens == estimate_input_tokens(piece.text)
            if piece.truncated:
                assert piece.excerpt_range is not None


def test_query_terms_use_cjk_pairs_and_words() -> None:
    terms = query_terms(["目印の値は API v2 で 30 日"])
    assert {"目印", "印の", "の値", "値は", "api", "v2", "30", "日"} == terms
    # ひらがなだけの組（助詞など）は数えない。
    assert "のは" not in query_terms(["目印のはなし"])
    assert query_terms([None, ""]) == frozenset()


def test_fit_fills_the_budget_in_rank_order() -> None:
    items = [
        SourceTextItem("一" * 400),
        SourceTextItem("二" * 500),
        SourceTextItem("三" * 300),
        SourceTextItem("四" * 100),
    ]
    fitted = fit_source_texts(items, budget_tokens=1000, item_max_tokens=600)
    assert [piece.text_omitted for piece in fitted] == [False, False, True, False]
    assert fitted[0].text == "一" * 400 and fitted[3].text == "四" * 100
    omitted = fitted[2]
    assert (omitted.text, omitted.text_length, omitted.excerpt_range) == ("", 300, None)
    assert sum(piece.tokens for piece in fitted) <= 1000


def test_fit_counts_the_fixed_part_of_every_item() -> None:
    items = [SourceTextItem("一" * 400, fixed_tokens=300) for _ in range(3)]
    fitted = fit_source_texts(items, budget_tokens=1600, item_max_tokens=600)
    # 固定の 900 を引いた 700 に本文 400 が 1 件だけ入る。
    assert [piece.text_omitted for piece in fitted] == [False, True, True]


def test_fit_truncates_only_items_over_the_ceiling() -> None:
    long_text, _ = _sentences(300, marker=200)
    items = [SourceTextItem("短い合成の本文。"), SourceTextItem(long_text)]
    fitted = fit_source_texts(items, item_max_tokens=500, queries=["目印の値"])
    assert fitted[0].truncated is False and fitted[0].text == "短い合成の本文。"
    assert fitted[1].truncated is True and "目印の値" in fitted[1].text
    assert fitted[1].tokens <= 500


def test_default_budget_holds_the_default_result_sizes() -> None:
    # 既定の分割の大きさ（本文 1000 字・表 3000 字）を、すべての字を CJK として見積もる。
    assert SOURCE_TEXT_ITEM_MAX_TOKENS >= 3000 + 200
    assert SOURCE_TEXT_RESPONSE_BUDGET_TOKENS >= 12 * 1000
    assert SOURCE_TEXT_PAGE_BUDGET_TOKENS <= 25000


def test_page_returns_full_texts_until_the_page_budget() -> None:
    items = [SourceTextItem("合" * 300) for _ in range(5)]
    first = page_source_texts(items, limit=10, budget_tokens=1000)
    assert [index for index, _ in first.items] == [0, 1, 2]
    assert all(piece.text == "合" * 300 and not piece.truncated for _, piece in first.items)
    assert first.next_offset == 3
    second = page_source_texts(items, offset=3, limit=10, budget_tokens=1000)
    assert [index for index, _ in second.items] == [3, 4]
    assert second.next_offset is None


def test_page_omits_a_single_item_over_the_page_budget() -> None:
    items = [SourceTextItem("合" * 100), SourceTextItem("大" * 5000), SourceTextItem("合" * 100)]
    page = page_source_texts(items, limit=10, budget_tokens=1000)
    assert [(index, piece.text_omitted) for index, piece in page.items] == [
        (0, False),
        (1, True),
        (2, False),
    ]
    assert page.items[1][1].text_length == 5000 and page.next_offset is None


def test_page_respects_the_limit() -> None:
    items = [SourceTextItem("合") for _ in range(30)]
    page = page_source_texts(items, limit=20)
    assert len(page.items) == 20 and page.next_offset == 20


@pytest.mark.parametrize(("offset", "limit"), [(-1, 1), (0, 0)], ids=["offset", "limit"])
def test_page_rejects_invalid_ranges(offset: int, limit: int) -> None:
    with pytest.raises(ValueError):
        page_source_texts([], offset=offset, limit=limit)
