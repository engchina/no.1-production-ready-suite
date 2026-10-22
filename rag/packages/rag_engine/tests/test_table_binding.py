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
    # 時刻のコロンは区切りにしない（時刻は値として照合する）。
    times = "区分Ⅰ：9:00\n区分Ⅱ：10:00"
    assert unbound_table_values("区分Ⅱは 10:00 に始まる。", [times]) == []
    assert unbound_table_values("区分Ⅱは 9:00 に始まる。", [times]) == [("9:00", "区分Ⅱ")]


def test_markdown_tables_and_other_tables_binding_the_value() -> None:
    markdown = "| 区分 | 値 |\n|---|---|\n| 区分Ⅰ | 01 |\n| 区分Ⅱ | 02 |"
    assert unbound_table_values("区分Ⅱは 01 です。", [markdown]) == [("01", "区分Ⅱ")]
    # 同じ値を別の表が同じ行の区分と結び付けていれば、表をまたいで疑わない。
    other = "<table><tr><td>区分Ⅱ</td><td>01</td></tr><tr><td>区分Ⅳ</td><td>04</td></tr></table>"
    assert unbound_table_values("区分Ⅱは 01 です。", [markdown, other]) == []
    assert len(evidence_tables(TABLE)) == 1 and len(evidence_tables(TABLE)[0].rows) == 3  # 見出しの行は除く
    assert unbound_table_values("区分Ⅱは 01 です。", ["区分Ⅰは 01 です。区分Ⅱは 02 です。"]) == []  # 表でない本文は見ない


@pytest.mark.parametrize(
    "number",
    ["5.4", "6.2.1", "step-3", "step‑3", "step−3", "５．４", "-3", "+3", "−3"],
    ids=["section", "nested-section", "step", "nonbreaking-hyphen", "minus", "full-width", "negative", "positive-sign", "unicode-negative"],
)
def test_number_parts_are_not_independent_table_values(number: str) -> None:
    """節・小数・コードの内部や符号付きの数を、別の行の正の値として扱わない。"""
    table = "| 番号 | 名称 |\n|---|---|\n| 2 | 項目甲 |\n| 3 | 項目乙 |\n| 4 | 項目丙 |\n| 5 | 項目丁 |\n| 6 | 項目戊 |"
    assert unbound_table_values(f"項目甲については「{number}」を参照する。", [table]) == []


@pytest.mark.parametrize(
    ("claim", "expected"),
    [
        ("「5.4 区分A・登録保留との順序」を参照する。", []),
        ("**3．登録期限（step-3）**", []),
        ("登録保留の期間は「4」です。", []),
        ("仮登録の番号は「4」です。", []),
        ("登録データは「4」です。", []),
        ("登録では「2」を指定する。", []),
        ("登録では「4」を指定する。", [("4", "登録、変更、取消等")]),
        ("「登録、変更、取消等」では「4」を指定する。", [("4", "登録、変更、取消等")]),
    ],
    ids=["section-and-compound", "heading", "suffix", "prefix", "katakana-suffix", "correct-row", "wrong-row", "full-cell-wrong-row"],
)
def test_shortened_cell_label_does_not_match_inside_a_compound(claim: str, expected: list[tuple[str, str]]) -> None:
    """読点で切ったセルの頭は、複合語の一部分だけでは行の手がかりにしない（#2752）。"""
    table = "| 章 | 操作 |\n|---|---|\n| 2 | 登録、変更、取消等 |\n| 3 | 参照、出力 |\n| 4 | 照会、抽出 |\n| 5 | 相談、案内 |"
    assert unbound_table_values(claim, [table]) == expected


@pytest.mark.parametrize("value", ["1", "A", "B2", "3.2", "step-3"])
def test_standalone_values_still_detect_wrong_rows(value: str) -> None:
    table = f"| 名称 | 値 |\n|---|---|\n| 項目甲 | {value} |\n| 項目乙 | 9 |"
    assert unbound_table_values(f"項目乙では「{value}」を指定する。", [table]) == [(value, "項目乙")]


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


# ---- 行と列の構造での照合（#2659。値の形に依らない） ----------------------------------------------------

WORD_TABLE = (
    "<table><tr><th>区分</th><th>処理の周期</th><th>保存期間</th></tr>"
    "<tr><td>区分A</td><td>月次処理</td><td>30日以内</td></tr>"
    "<tr><td>区分B</td><td>日次処理</td><td>60日以内</td></tr></table>"
)


@pytest.mark.parametrize(
    ("claim", "expected"),
    [
        ("区分Aは月次処理で、保存期間は30日以内です。", []),
        # 日本語の値・単位付きの値も、別の行の値なら検出する。
        ("区分Bは月次処理です。", [("月次処理", "区分B")]),
        ("区分Bの保存期間は30日以内です。", [("30日以内", "区分B")]),
        # 同じ列の値の列挙（区分の名前を述べない）は疑わない。
        ("処理の周期は月次処理・日次処理のどちらかです。", []),
        ("区分Aと区分Bは、それぞれ月次処理と日次処理です。", []),
    ],
    ids=["correct", "word-other-row", "unit-other-row", "word-list", "both-rows"],
)
def test_word_and_unit_values_are_checked_by_the_row(claim: str, expected: list[tuple[str, str]]) -> None:
    assert unbound_table_values(claim, [WORD_TABLE]) == expected


def test_code_inside_a_cell_with_a_unit_is_the_value_of_its_row() -> None:
    """「コード／数量 単位」のセルは、セル全体とセルの中のコードを、その行の値として照合する。"""
    table = (
        "<table><tr><td>項目</td><td>設定値</td><td>確認方法</td></tr>"
        "<tr><td>項目X1</td><td>K-01-1／140 分</td><td>記録1と画面を照合</td></tr>"
        "<tr><td>項目X2</td><td>K-01-2／141 分</td><td>記録2と画面を照合</td></tr>"
        "<tr><td>項目X3</td><td>K-01-3／142 分</td><td>記録3と画面を照合</td></tr></table>"
    )
    assert unbound_table_values("項目X2 の設定値は K-01-2／141 分です。", [table]) == []
    assert unbound_table_values("項目X2 の設定値は K-01-3／142 分です。", [table]) == [("K-01-3／142 分", "項目X2")]
    assert unbound_table_values("項目X2 は K-01-1 を設定します。", [table]) == [("k-01-1", "項目X2")]
    assert unbound_table_values("項目X3 は記録2と画面を照合します。", [table]) == [("記録2と画面を照合", "項目X3")]
    # 数字だけの並び（数量）はセルの中から取り出さない（ほかの意味の数と見分けられない）。
    assert unbound_table_values("項目X2 は 140 分ごとに確認します。", [table]) == []


def test_value_of_another_column_is_found_by_the_header() -> None:
    """行は合っていても、見出しが別の列（改定前と改定後）の値を述べた文を検出する。"""
    table = "| 項目 | 改定前 | 改定後 |\n|---|---|---|\n| 申請の期限 | 30日以内 | 60日以内 |\n| 保存期間 | 3年 | 5年 |"
    assert unbound_table_values("改定後の申請の期限は60日以内です。", [table]) == []
    assert unbound_table_values("改定前の申請の期限は30日以内です。", [table]) == []
    assert unbound_table_values("改定後の申請の期限は30日以内です。", [table]) == [("30日以内", "改定後")]
    # 見出しを述べない文・両方の列を述べる文は疑わない。
    assert unbound_table_values("申請の期限は30日以内です。", [table]) == []
    assert unbound_table_values("申請の期限は改定前の30日以内から改定後の60日以内に変わります。", [table]) == []


@pytest.mark.parametrize(
    ("evidence", "claim", "expected"),
    [
        # 1 文の中で並べた対応（表でない本文）。
        ("区分Aは値x01、区分Bは値y02とする。", "区分Bは値x01です。", [("値x01", "区分B")]),
        ("区分Aは値x01、区分Bは値y02とする。", "区分Bは値y02です。", []),
        # 別々の文の記載は並べた対応とみなさない。
        ("区分Aは値x01です。区分Bは値y02です。", "区分Bは値x01です。", []),
        # 箇条書きの「名前：値」・等号の区切り。
        ("- 区分A：x01\n- 区分B：y02", "区分Bは x01 です。", [("x01", "区分B")]),
        ("区分A = x01\n区分B = y02", "区分Bは x01 です。", [("x01", "区分B")]),
    ],
    ids=["prose-swapped", "prose-correct", "separate-sentences", "bullet-pairs", "equals-pairs"],
)
def test_enumerated_pairs_outside_tables(evidence: str, claim: str, expected: list[tuple[str, str]]) -> None:
    assert unbound_table_values(claim, [evidence]) == expected


def test_single_roman_numeral_rows_are_names_not_values() -> None:
    """ローマ数字だけの行の名前（Ⅰ・Ⅱ）は区分の名前として照合し、似た表記（l・1）とは区別する。"""
    table = "<table><tr><th>区分</th><th>期限</th></tr><tr><td>Ⅰ</td><td>30日</td></tr><tr><td>Ⅱ</td><td>60日</td></tr></table>"
    assert unbound_table_values("区分Ⅰは30日です。", [table]) == []
    assert unbound_table_values("区分Ⅰは60日です。", [table]) == [("60日", "Ⅰ")]
    assert unbound_table_values("区分Ⅱは30日です。", [table]) == [("30日", "Ⅱ")]
    assert unbound_table_values("区分IIは30日です。", [table]) == [("30日", "Ⅱ")]
    assert unbound_table_values("区分lは60日です。", [table]) == []


def test_short_words_and_note_rows_are_not_values() -> None:
    """他の語の一部に現れやすい短い語（あり・なし）と、値の列に別の種類の文しか無い注記の行では疑わない。"""
    flags = "<table><tr><th>区分</th><th>承認</th></tr><tr><td>区分A</td><td>あり</td></tr><tr><td>区分B</td><td>なし</td></tr></table>"
    assert unbound_table_values("区分Bの申請は承認がありません。", [flags]) == []
    notes = "区分A：x01\n区分B：y02\n備考：入力は必須"
    assert unbound_table_values("備考のとおり x01 を入力します。", [notes]) == []
