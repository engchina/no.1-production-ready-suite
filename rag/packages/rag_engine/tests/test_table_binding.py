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


def test_grounded_item_with_a_value_from_another_row_is_downgraded() -> None:
    question = "区分Ⅱで選ぶ選択肢の値は？"
    spans = grounded.tag_spans(
        question, [{"evidence_id": "E1", "source_id": "c1", "source": "m.pdf", "page": 1, "text": TABLE, "section_path": []}]
    )
    wrong = GroundedItem(kind="rule", text="区分Ⅱの選択肢の値は 01 です。", evidence_id="E1", quote="区分Ⅱ 02")
    right = GroundedItem(kind="rule", text="区分Ⅱの選択肢の値は 02 です。", evidence_id="E1", quote="区分Ⅱ 02")

    def checked(item: GroundedItem) -> grounded.CheckedItem:
        entries, _ = grounded.verify(question, GroundedDraft(summary="", confidence="high", items=[item]), spans)
        return next(entry for entry in entries if entry.item.text == item.text)

    assert checked(wrong).quote_only and "表の別の行の値" in checked(wrong).reasons[0]
    assert not checked(right).quote_only and checked(right).reasons == []


def test_audit_prompt_judges_the_relation_not_the_shared_words() -> None:
    assert RELATION_SUPPORT_RULES in grounded.AUDIT_SYSTEM_PROMPT
    section = grounded.AUDIT_SYSTEM_PROMPT.split("3.1 support", 1)[1].split("3.2 applicability", 1)[0]
    assert RELATION_SUPPORT_RULES in section  # support の判定の規則
    # function_context・evidence の語で支持済みにするのは、同じ対象の記載のときだけ。
    rules = [line for line in grounded.AUDIT_SYSTEM_PROMPT.splitlines() if line.startswith("- function_context は")]
    assert len(rules) == 1 and "同じ対象について述べられていれば" in rules[0]
