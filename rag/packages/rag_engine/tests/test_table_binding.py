"""表・一覧の行の対応に反して、別の行の値を述べた文の検出（#2659）。根拠・文は合成。"""

import pytest

from rag_engine.generation import grounded
from rag_engine.generation.answer_policy import RELATION_SUPPORT_RULES
from rag_engine.generation.table_binding import evidence_tables, unbound_table_values
from rag_engine.models.llm import GroundedDraft, GroundedItem

# 区分と選択肢の値の表（見出しは <th>）。区分の名前はローマ数字だけが違う。
TABLE = (
    "<table><tr><th>区分</th><th>選択肢の値</th></tr>"
    "<tr><td>区分Ⅰ</td><td>01</td></tr>"
    "<tr><td>区分Ⅱ</td><td>02</td></tr>"
    "<tr><td>区分Ⅲ</td><td>03</td></tr></table>"
)


@pytest.mark.parametrize(
    ("claim", "expected"),
    [
        # 正しい対応は残す。
        ("区分Ⅱでは選択肢の値 02 を選びます。", []),
        ("区分Ⅰは 01、区分Ⅱは 02 を選びます。", []),
        # 隣の行の値を述べた文。
        ("区分Ⅱでは選択肢の値 01 を選びます。", [("01", "区分Ⅱ")]),
        ("区分Ⅱでは選択肢の値 03 を選びます。", [("03", "区分Ⅱ")]),
        # 1 文の中で入れ替えた対応は、読点で区切った区間ごとに見る。
        ("区分Ⅰは 02、区分Ⅱは 01 を選びます。", [("01", "区分Ⅱ"), ("02", "区分Ⅰ")]),
        # 値の無い区間（「区分Ⅱの場合、」）は続く区間と合わせて見る。
        ("区分Ⅱの場合、01 を選びます。", [("01", "区分Ⅱ")]),
        # 似た表記（全角・半角のローマ数字）も同じ区分として照合する。
        ("区分IIでは 01 を選びます。", [("01", "区分Ⅱ")]),
        # 見出しの行の語だけを述べた文は疑わない。
        ("選択肢の値は 01 です。", []),
        # 根拠の ID・頁の参照の数字を値として照合しない。
        ("区分Ⅱでは 02 を選びます（p.1）【証拠 ID 0123abcd:1:1】", []),
    ],
    ids=["correct", "both-correct", "adjacent-row", "other-row", "swapped-pair", "condition-clause", "roman-width", "header", "citation"],
)
def test_values_from_another_row_are_found(claim: str, expected: list[tuple[str, str]]) -> None:
    assert unbound_table_values(claim, [TABLE]) == expected


# コード・名称・開始・終了・単価の表（見出しの印が無く、値の無い最初の行を見出しとみなす。終了は空欄）。
PRICE_TABLE = (
    "<table><tr><td>コード</td><td>名称</td><td>開始</td><td>終了</td><td>単価</td></tr>"
    "<tr><td>A101</td><td>項目甲</td><td>202504</td><td></td><td>254</td></tr>"
    "<tr><td>A102</td><td>項目乙</td><td>202504</td><td></td><td>253</td></tr>"
    "<tr><td>A103</td><td>項目丙</td><td>202504</td><td></td><td>300</td></tr></table>"
)


@pytest.mark.parametrize(
    ("claim", "expected"),
    [
        # 複数の行の値の列挙・範囲は、区分と値の結び付けではない（#2734）。
        ("E 列が 202504、F 列が空欄、Q 列に 254・253・300 などの単価が表示されている。", []),
        ("対象はコード A101〜A103 で、単価は 254 から 300 です。", []),
        # 同じ行のコードと値。同じ行のコードがあれば、別の行の名前を述べても疑わない。
        ("A101 の単価は 254 です。", []),
        ("A101 の単価は 254 で項目乙より高い。", []),
        # 別の行の名前と値の結び付けは検出する。
        ("項目甲の単価は 253 です。", [("253", "項目甲")]),
    ],
    ids=["value-list", "value-range", "same-row-code", "same-row-code-with-other-name", "other-row-name"],
)
def test_values_listed_from_several_rows_are_not_suspected(claim: str, expected: list[tuple[str, str]]) -> None:
    assert unbound_table_values(claim, [PRICE_TABLE]) == expected


def test_shared_values_spanned_cells_and_notes_are_not_suspected() -> None:
    shared = "<table><tr><td>区分Ⅰ</td><td>01</td></tr><tr><td>区分Ⅱ</td><td>01</td></tr></table>"
    assert unbound_table_values("区分Ⅰと区分Ⅱはどちらも 01 です。", [shared]) == []
    # rowspan の区分は覆う行の値にも対応する。
    spanned = "<table><tr><td rowspan=2>区分Ⅰ</td><td>01</td></tr><tr><td>02</td></tr><tr><td>区分Ⅱ</td><td>03</td></tr></table>"
    assert unbound_table_values("区分Ⅰでは 02 も選べます。", [spanned]) == []
    assert unbound_table_values("区分Ⅱでは 02 を選びます。", [spanned]) == [("02", "区分Ⅱ")]
    # 「名前：値」の行の並び。値の無い注記の行の語は、行を見分ける手がかりにしない。
    pairs = "一般：01\n特別：02\n注意：入力は必須"
    assert unbound_table_values("特別の場合は 02 を指定する。", [pairs]) == []
    assert unbound_table_values("一般の場合は 02 を指定する。", [pairs]) == [("02", "一般")]
    assert unbound_table_values("注意して 01 を指定する。", [pairs]) == []
    # 手順の番号・数量の 1 文字の数字を、表の 1 文字の値として照合しない。
    digits = "<table><tr><td>区分Ⅰ</td><td>1</td></tr><tr><td>区分Ⅱ</td><td>2</td></tr></table>"
    assert unbound_table_values("1. 区分Ⅱの値を選びます。", [digits]) == []
    assert unbound_table_values("手順1で区分Ⅱを選びます。", [digits]) == []
    assert unbound_table_values("区分Ⅱでは「1」を選びます。", [digits]) == [("1", "区分Ⅱ")]
    # 時刻のコロンは区切りにしない。
    assert unbound_table_values("区分Ⅱは 9:00 に始まる。", ["区分Ⅰ：9:00\n区分Ⅱ：10:00"]) == []


def test_markdown_tables_and_other_tables_binding_the_value() -> None:
    markdown = "| 区分 | 値 |\n|---|---|\n| 区分Ⅰ | 01 |\n| 区分Ⅱ | 02 |"
    assert unbound_table_values("区分Ⅱは 01 です。", [markdown]) == [("01", "区分Ⅱ")]
    # 同じ値を別の表が同じ行の区分と結び付けていれば、表をまたいで疑わない。
    other = "<table><tr><td>区分Ⅱ</td><td>01</td></tr><tr><td>区分Ⅳ</td><td>04</td></tr></table>"
    assert unbound_table_values("区分Ⅱは 01 です。", [markdown, other]) == []
    assert len(evidence_tables(TABLE)) == 1 and len(evidence_tables(TABLE)[0].rows) == 3  # 見出しの行は除く
    assert unbound_table_values("区分Ⅱは 01 です。", ["区分Ⅰは 01 です。区分Ⅱは 02 です。"]) == []  # 表でない本文は見ない


def test_grounded_item_with_a_value_from_another_row_gets_a_warning() -> None:
    """表の別の行の値を結び付けた説明は、言い換えを残して警告（value_unbound）を付ける（#2738。以前は降格）。"""
    question = "区分Ⅱで選ぶ選択肢の値は？"
    spans = grounded.tag_spans(
        question, [{"evidence_id": "E1", "source_id": "c1", "source": "m.pdf", "page": 1, "text": TABLE, "section_path": []}]
    )
    wrong = GroundedItem(kind="rule", text="区分Ⅱの選択肢の値は 01 です。", evidence_id="E1", quote="区分Ⅱ 02")
    right = GroundedItem(kind="rule", text="区分Ⅱの選択肢の値は 02 です。", evidence_id="E1", quote="区分Ⅱ 02")

    def checked(item: GroundedItem) -> grounded.CheckedItem:
        entries, _ = grounded.verify(question, GroundedDraft(summary="", confidence="high", items=[item]), spans)
        return next(entry for entry in entries if entry.item.text == item.text)

    entry = checked(wrong)
    assert not entry.quote_only and entry.state == "warning" and "表の別の行の値" in entry.reasons[0]
    assert [mark["code"] for mark in entry.marks] == ["value_unbound"]
    assert "原文「区分Ⅱ | 02」" in entry.marks[0]["detail"]
    assert not checked(right).quote_only and checked(right).reasons == [] and checked(right).state == ""


def test_grounded_item_listing_values_of_several_rows_is_kept() -> None:
    """複数の行の値を列挙した説明は、表の別の行の値として落とさない（#2734）。"""
    question = "単価の列に表示される値は？"
    spans = grounded.tag_spans(
        question, [{"evidence_id": "E1", "source_id": "c1", "source": "m.pdf", "page": 1, "text": PRICE_TABLE, "section_path": []}]
    )
    listed = GroundedItem(kind="rule", text="単価の列に 254・253・300 が表示されます。", evidence_id="E1", quote="254")
    entries, _ = grounded.verify(question, GroundedDraft(summary="", confidence="high", items=[listed]), spans)
    entry = next(entry for entry in entries if entry.item.text == listed.text)
    assert not any("表の別の行の値" in reason for reason in entry.reasons)


def test_audit_prompt_judges_the_relation_not_the_shared_words() -> None:
    assert RELATION_SUPPORT_RULES in grounded.AUDIT_SYSTEM_PROMPT
    section = grounded.AUDIT_SYSTEM_PROMPT.split("3.1 support", 1)[1].split("3.2 applicability", 1)[0]
    assert RELATION_SUPPORT_RULES in section  # support の判定の規則
    # function_context・evidence の語で支持済みにするのは、同じ対象の記載のときだけ。
    rules = [line for line in grounded.AUDIT_SYSTEM_PROMPT.splitlines() if line.startswith("- function_context は")]
    assert len(rules) == 1 and "同じ対象について述べられていれば" in rules[0]
